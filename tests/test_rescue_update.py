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
    local url=${@: -1} output head=0 silent=0
    local name=${url##*/}
    printf '%s\n' "$url" >> "$FIXTURE/urls"
    while (( $# )); do
        case "$1" in
            --head) head=1 ;;
            --silent) silent=1 ;;
            --output) shift; output=$1 ;;
        esac
        shift
    done
    printf '%s %s\n' "$name" "$head" >> "$FIXTURE/requests"
    printf '%s %s %s\n' "$name" "$head" "$silent" >> "$FIXTURE/meters"
    if [[ $name == vmlinuz && $head == 0 && ${SCENARIO-} == interrupted ]]; then
        return 18
    fi
    if (( head )); then
        printf 'Content-Length: %s\r\n' "$(stat -c %s "$FIXTURE/server/$name")" > "$output"
    else
        cp "$FIXTURE/server/$name" "$output"
    fi
    printf '200 %s 0.01' "$(stat -c %s "$output")"
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
        ("interrupted", "Could not fetch vmlinuz"),
        ("full", "Not enough free space"),
        ("bad-hash", "Checksum mismatch"),
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
    if scenario == "interrupted":
        assert "Downloaded vmlinuz" not in result.stderr
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


@pytest.mark.parametrize(
    "args, base",
    [
        (("--yes",), "https://gentoo-rescue.v7k.me"),
        (("--source", "https://mirror.example.org/rescue/", "--yes"),
         "https://mirror.example.org/rescue"),
    ],
)
def test_update_source_urls(release, tmp_path, args, base):
    result = run_update(tmp_path, "", *args)
    assert result.returncode == 0, result.stderr
    assert "Boot payloads to download: 1 (10 bytes total)." in result.stderr
    assert "Downloaded vmlinuz (10 bytes in 0.01 s)." in result.stderr
    assert "Verifying checksum for vmlinuz" in result.stderr
    assert all(line.endswith(" 1") for line in (tmp_path / "meters").read_text().splitlines())
    names = ["SHA256SUMS", "packages.md", "vmlinuz", "vmlinuz"]
    assert (tmp_path / "urls").read_text().splitlines() == [f"{base}/{name}" for name in names]


@pytest.mark.parametrize("args", [("--source",), ("--source", "http://mirror.example.org")])
def test_invalid_source_fails_before_network_access(release, tmp_path, args):
    result = run_update(tmp_path, "", *args)
    assert result.returncode == 1
    assert "HTTPS" in result.stderr
    assert not (tmp_path / "urls").exists()


def test_live_meter_only_for_terminal_downloads(release, tmp_path):
    master, terminal = pty.openpty()
    try:
        result = subprocess.run(
            ["bash", "-c", HARNESS, "rescue-update-test", str(SCRIPT), "--yes"],
            env=dict(os.environ, FIXTURE=str(tmp_path)),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=terminal,
            timeout=15,
        )
    finally:
        os.close(terminal)
        os.close(master)
    assert result.returncode == 0
    meters = (tmp_path / "meters").read_text().splitlines()
    assert "vmlinuz 1 1" in meters
    assert "vmlinuz 0 0" in meters
    assert "SHA256SUMS 0 0" in meters
