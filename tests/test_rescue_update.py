import hashlib
import os
from pathlib import Path
import pty
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "assets/root/usr/local/sbin/rescue-update"
PAYLOADS = ("vmlinuz", "initramfs.img", "root.squashfs")

# Substitute the network and mount operations. File validation and installation
# still run against the fixture through the production entry point.
HARNESS = r'''
source "$1"
boot_uuid() { echo 00000000-0000-0000-0000-000000000001; }
identify_carrier() { device="$FIXTURE/device"; }
mount() { rmdir "${@: -1}"; ln -s "$FIXTURE/usb" "${@: -1}"; }
umount() { rm "$2"; mkdir "$2"; }
curl() {
    local name=${@: -1} output head=0
    name=${name##*/}
    while (( $# )); do
        case "$1" in
            --head) head=1 ;;
            --output) shift; output=$1 ;;
        esac
        shift
    done
    printf '%s %s\n' "$name" "$head" >> "$FIXTURE/requests"
    if (( head )); then
        printf 'Content-Length: %s\r\n' "$(stat -c %s "$FIXTURE/server/$name")" > "$output"
    else
        cp "$FIXTURE/server/$name" "$output"
        if [[ $name == vmlinuz && ${SCENARIO-} == moving ]]; then
            printf 'changed\n' >> "$FIXTURE/server/SHA256SUMS"
        fi
    fi
    printf 200
}
df() {
    case "${SCENARIO-}" in
        full) printf '0\n' ;;
        *) command df "$@" ;;
    esac
}
shift
main "$FIXTURE/run" "$@"
'''


def manifest(values):
    return "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in values.items())


@pytest.fixture
def release(tmp_path):
    usb, server = (tmp_path / name for name in ("usb", "server"))
    for directory in (usb, server, tmp_path / "run"):
        directory.mkdir()
    (tmp_path / "device").touch()
    original = {name: name.encode() for name in PAYLOADS}
    updated = dict(original, vmlinuz=b"new kernel")
    for directory, values, version in ((usb, original, "old"), (server, updated, "new")):
        for name, data in values.items():
            (directory / name).write_bytes(data)
        (directory / "SHA256SUMS").write_text(manifest(values))
        (directory / "packages.md").write_text(f"# Installed packages\n\n{version}\n")
    (usb / "persistent-home-file").write_text("keep")
    return usb, server


def run_update(root, scenario, *args, stdin=subprocess.DEVNULL):
    return subprocess.run(
        ["bash", "-c", HARNESS, "rescue-update-test", str(SCRIPT), *args],
        env=dict(os.environ, FIXTURE=str(root), SCENARIO=scenario),
        stdin=stdin,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_updates_only_changed_payload_and_preserves_home(release, tmp_path):
    usb, server = release
    inode = (usb / "root.squashfs").stat().st_ino
    result = run_update(tmp_path, "", "--yes")
    assert result.returncode == 0, result.stderr
    assert "-old\n+new" in result.stdout
    assert (tmp_path / "requests").read_text().splitlines() == [
        "SHA256SUMS 0", "packages.md 0", "vmlinuz 1", "vmlinuz 0",
        "SHA256SUMS 0", "packages.md 0",
    ]
    for name in (*PAYLOADS, "packages.md", "SHA256SUMS"):
        assert (usb / name).read_bytes() == (server / name).read_bytes()
    assert (usb / "root.squashfs").stat().st_ino == inode
    assert (usb / "persistent-home-file").read_text() == "keep"
    result = run_update(tmp_path, "", "-y")
    assert result.returncode == 0, result.stderr
    assert "already up to date" in result.stdout
    assert not list((tmp_path / "run").glob("rescue-update.*"))


@pytest.mark.parametrize(
    "scenario, message",
    [
        ("full", "Not enough free space"),
        ("bad-hash", "Checksum mismatch"),
        ("moving", "Published release changed"),
        ("unsafe-path", "Invalid published SHA256SUMS"),
        ("no-tty", "Use --yes"),
    ],
)
def test_failure_preserves_usb(release, tmp_path, scenario, message):
    usb, server = release
    original = {path.name: path.read_bytes() for path in usb.iterdir()}
    if scenario == "bad-hash":
        (server / "vmlinuz").write_bytes(b"bad kernel")
    if scenario == "unsafe-path":
        path = server / "SHA256SUMS"
        path.write_text(path.read_text().replace("vmlinuz", "../vmlinuz"))
    args = () if scenario == "no-tty" else ("--yes",)
    result = run_update(tmp_path, scenario, *args)
    assert result.returncode == 1
    assert message in result.stderr
    assert {path.name: path.read_bytes() for path in usb.iterdir()} == original
    assert not list((tmp_path / "run").glob("rescue-update.*"))
    if scenario == "no-tty":
        assert "-old\n+new" in result.stdout
        assert (tmp_path / "requests").read_text() == "SHA256SUMS 0\npackages.md 0\n"


@pytest.mark.parametrize("answer, install", [(b"n\n", False), (b"yes\n", True)])
def test_interactive_confirmation(release, tmp_path, answer, install):
    usb, server = release
    master, terminal = pty.openpty()
    try:
        os.write(master, answer)
        result = run_update(tmp_path, "", stdin=terminal)
    finally:
        os.close(terminal)
        os.close(master)
    assert result.returncode == 0, result.stderr
    assert "Download and install the update?" in result.stderr
    assert "-old\n+new" in result.stdout
    assert ((usb / "vmlinuz").read_bytes() == (server / "vmlinuz").read_bytes()) == install
    if not install:
        assert (tmp_path / "requests").read_text() == "SHA256SUMS 0\npackages.md 0\n"
