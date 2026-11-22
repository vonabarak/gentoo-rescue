"""Update the rescue payloads on an offline USB drive."""

import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from export import INVENTORY, MANIFEST, PAYLOADS, verify_payloads
from usb_media import carrier, mounted, target_disk, usb_info


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").digest()


def source_checksums(source):
    for name in (*PAYLOADS, MANIFEST):
        path = source / name
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError as exc:
            raise ValueError(f"Missing {path}. Run make export to prepare the source.") from exc
        if not stat.S_ISREG(mode):
            raise ValueError(f"Expected a regular file at {path}")

    checksums = verify_payloads(source)
    inventory = source / INVENTORY
    try:
        info = inventory.lstat()
    except FileNotFoundError:
        return checksums
    if not stat.S_ISREG(info.st_mode) or not info.st_size:
        raise ValueError(f"Expected a nonempty regular file at {inventory}")
    manifest = checksums.pop(MANIFEST)
    checksums[INVENTORY] = digest(inventory)
    checksums[MANIFEST] = manifest
    return checksums


def update_files(source, target, checksums):
    changed = {}
    for name, expected in checksums.items():
        destination = target / name
        try:
            mode = destination.lstat().st_mode
        except FileNotFoundError:
            changed[name] = expected
            continue
        if not stat.S_ISREG(mode):
            raise ValueError(f"Expected a regular file at {destination}")
        if digest(destination) != expected:
            changed[name] = expected

    if not changed:
        print("USB payloads are already up to date.")
        return
    with tempfile.TemporaryDirectory(prefix=".rescue-update-", dir=target) as directory:
        staged = Path(directory)
        for name, expected in changed.items():
            path = staged / name
            shutil.copyfile(source / name, path)
            os.chmod(path, 0o644)
            if digest(path) != expected:
                raise ValueError(f"Staged checksum mismatch for {name}")
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
        directory_fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for name in changed:
                (staged / name).replace(target / name)
                os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    print("Updated " + ", ".join(changed))


def update_usb(source, device, identity, info):
    checksums = source_checksums(source)
    if device.stat().st_rdev != identity:
        raise ValueError("USB device changed")
    destination = carrier(device)
    if device.stat().st_rdev != identity or usb_info(device) != info:
        raise ValueError("USB device changed")
    with mounted(destination, "rw,nodev,nosuid,noexec") as target:
        update_files(source, target, checksums)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("device", type=Path, help="whole USB block device")
    parser.add_argument(
        "--source", type=Path, default=Path("build/gentoo-rescue"),
        help="exported payload directory (default: build/gentoo-rescue)",
    )
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.exit(1, "Root privileges are required. Run with sudo.\n")
    try:
        with target_disk(args.device) as target:
            update_usb(args.source, *target)
    except KeyboardInterrupt:
        parser.exit(130, "Interrupted.\n")
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"USB operation failed: {exc}\n")


if __name__ == "__main__":
    main()
