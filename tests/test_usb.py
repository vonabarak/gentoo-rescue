"""Check USB updates and cancellation without writing a block device."""

import hashlib
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

import update_usb
import usb_media
import write_usb
from export import FILES, INVENTORY, verify_payloads


@pytest.mark.parametrize("previous_inventory", [None, "old", "current"])
def test_update_only_changed_files(tmp_path, previous_inventory):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    for name in FILES:
        (source / name).write_text(name)
        (target / name).write_text(name)
    (target / "vmlinuz").write_text("previous")
    (source / INVENTORY).write_text("current")
    if previous_inventory is not None:
        (target / INVENTORY).write_text(previous_inventory)
    inventory_inode = (target / INVENTORY).stat().st_ino if previous_inventory else None
    (target / ".profile").write_text("persistent home")
    unchanged = (target / "root.squashfs").stat().st_ino
    checksums = {name: update_usb.digest(source / name)
                 for name in (*FILES[:-1], INVENTORY, FILES[-1])}
    update_usb.update_files(source, target, checksums)
    assert (target / "vmlinuz").read_text() == "vmlinuz"
    assert (target / "root.squashfs").stat().st_ino == unchanged
    assert (target / ".profile").read_text() == "persistent home"
    assert (target / INVENTORY).read_text() == "current"
    if previous_inventory == "current":
        assert (target / INVENTORY).stat().st_ino == inventory_inode
    before = {name: (target / name).stat().st_ino for name in (*FILES, INVENTORY)}
    update_usb.update_files(source, target, checksums)
    assert before == {name: (target / name).stat().st_ino for name in (*FILES, INVENTORY)}


def test_bad_source_checksum(tmp_path):
    (tmp_path / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(b'expected').hexdigest()}  {name}\n"
            for name in FILES[:-1]
        )
    )
    for name in FILES[:-1]:
        (tmp_path / name).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Checksum mismatch"):
        verify_payloads(tmp_path)


def test_countdown_can_cancel_before_device_open(tmp_path, monkeypatch):
    image = tmp_path / "image"
    image.write_bytes(bytes(512))
    info = {"size": 1024, "log-sec": 512, "model": "Test USB"}

    def cancel(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(write_usb.time, "sleep", cancel)
    with image.open("rb") as source, pytest.raises(KeyboardInterrupt):
        write_usb.write_usb(image, source, Path("/nonexistent"), 0, info)


def test_busy_device(monkeypatch):
    monkeypatch.setattr(
        usb_media,
        "disk_info",
        lambda _: {
            "type": "disk",
            "tran": "usb",
            "ro": False,
            "mountpoints": [None],
            "children": [{"type": "part", "mountpoints": ["/mnt/rescue"]}],
        },
    )
    with pytest.raises(ValueError, match="busy"):
        usb_media.usb_info(Path("/dev/test"))


def test_root_required(monkeypatch, capsys):
    monkeypatch.setattr(usb_media.os, "geteuid", lambda: 1000)
    monkeypatch.setattr("sys.argv", ["write_usb.py", "/nonexistent"])
    with pytest.raises(SystemExit) as exc:
        usb_media.main(None, "write")
    assert exc.value.code == 1
    assert "Root privileges" in capsys.readouterr().err


@pytest.mark.parametrize("failure", [None, "placement", "path"])
def test_source_uses_first_node(tmp_path, monkeypatch, failure):
    image_path = tmp_path / "rescue.raw"
    if failure != "path":
        image_path.write_bytes(b"image")
    first, second = [SimpleNamespace(id=i, name=str(i)) for i in (1, 2)]
    placements = [SimpleNamespace(node=second, file_path="/elsewhere")]
    if failure != "placement":
        placements.append(SimpleNamespace(node=first, file_path=str(image_path)))
    image = SimpleNamespace(ephemeral=False, format="raw", placements=placements)
    client = SimpleNamespace(
        nodes=SimpleNamespace(list=lambda: [first, second]),
        disks=SimpleNamespace(get=lambda _: SimpleNamespace(show=lambda: image)),
    )
    monkeypatch.setattr(usb_media, "connect", lambda: nullcontext(client))
    if failure:
        with (
            pytest.raises(ValueError, match="first node|unavailable"),
            usb_media.source_image(),
        ):
            pytest.fail("Invalid placement was opened")
    else:
        with usb_media.source_image() as (path, stream):
            assert path == image_path
            assert stream.read() == b"image"


def test_staging_failure_does_not_replace_files(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    for name in FILES:
        (source / name).write_text("new")
        (target / name).write_text("old")

    def corrupt_copy(_source, destination):
        destination.write_text("corrupt")

    checksums = {name: update_usb.digest(source / name) for name in FILES}
    monkeypatch.setattr(update_usb.shutil, "copyfile", corrupt_copy)
    with pytest.raises(ValueError, match="Staged checksum mismatch"):
        update_usb.update_files(source, target, checksums)
    assert {p.name: p.read_text() for p in target.iterdir()} == dict.fromkeys(
        FILES, "old"
    )


@pytest.mark.parametrize("scenario", ["default", "override", "missing", "corrupt", "changed"])
def test_offline_update_cli(tmp_path, monkeypatch, capsys, scenario):
    monkeypatch.chdir(tmp_path)
    override = scenario == "override"
    source = Path("custom export" if override else "build/gentoo-rescue")
    source.mkdir(parents=True)
    for name in FILES[:-1]:
        (source / name).write_text(name)
    (source / FILES[-1]).write_text(
        "".join(f"{hashlib.sha256(name.encode()).hexdigest()}  {name}\n"
                for name in FILES[:-1])
    )
    if scenario == "missing":
        (source / "vmlinuz").unlink()
    elif scenario == "corrupt":
        (source / "vmlinuz").write_text("corrupt")

    device = tmp_path / "device"
    device.touch()
    target = tmp_path / "target"
    target.mkdir()
    (target / INVENTORY).write_text("legacy inventory")
    mounts = []

    @contextmanager
    def mounted(partition, options):
        mounts.append((partition, options))
        if scenario == "changed":
            (source / "vmlinuz").write_text("changed after validation")
        yield target

    def reject_corvus():
        pytest.fail("Offline update must not connect to Corvus")

    monkeypatch.setattr(usb_media, "connect", reject_corvus)
    monkeypatch.setattr(update_usb.os, "geteuid", lambda: 0)
    monkeypatch.setattr(update_usb, "target_disk", lambda _: nullcontext((device, 0, {})))
    monkeypatch.setattr(update_usb, "carrier", lambda _: "/dev/partition")
    monkeypatch.setattr(update_usb, "usb_info", lambda _: {})
    monkeypatch.setattr(update_usb, "mounted", mounted)
    args = ["update_usb.py", str(device)]
    if override:
        args.extend(("--source", str(source)))
    monkeypatch.setattr("sys.argv", args)

    errors = {
        "missing": "Run make export",
        "corrupt": "Checksum mismatch",
        "changed": "Staged checksum mismatch",
    }
    if scenario in errors:
        with pytest.raises(SystemExit) as exc:
            update_usb.main()
        assert exc.value.code == 1
        assert errors[scenario] in capsys.readouterr().err
        assert set(p.name for p in target.iterdir()) == {INVENTORY}
    else:
        update_usb.main()
        assert all((target / name).read_bytes() == (source / name).read_bytes()
                   for name in FILES)
    assert (target / INVENTORY).read_text() == "legacy inventory"
    assert mounts == ([] if scenario in {"missing", "corrupt"} else [
        ("/dev/partition", "rw,nodev,nosuid,noexec")
    ])
