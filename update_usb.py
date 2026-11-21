"""Update the rescue payloads on an offline USB drive."""

import contextlib
import hashlib
import os
import shutil
import stat
import tempfile
from pathlib import Path

from export import FILES, verify_payloads
from usb_media import carrier, main, mounted, run, usb_info


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").digest()


def update_files(source, target):
    changed = {}
    for name in FILES:
        destination = target / name
        expected = digest(source / name)
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


def update_usb(_path, image, device, identity, info):
    if device.stat().st_rdev != identity:
        raise ValueError("USB device changed")
    destination = carrier(device)
    with contextlib.ExitStack() as cleanup:
        loop = run(
            "losetup",
            "--find",
            "--show",
            "--read-only",
            "--partscan",
            f"/proc/{os.getpid()}/fd/{image.fileno()}",
        )
        cleanup.callback(run, "losetup", "--detach", loop)
        run("udevadm", "settle", "--timeout=30")
        source = cleanup.enter_context(
            mounted(carrier(loop), "ro,noload,nodev,nosuid,noexec")
        )
        verify_payloads(source)
        if device.stat().st_rdev != identity or usb_info(device) != info:
            raise ValueError("USB device changed")
        target = cleanup.enter_context(mounted(destination, "rw,nodev,nosuid,noexec"))
        update_files(source, target)


if __name__ == "__main__":
    main(update_usb, __doc__)
