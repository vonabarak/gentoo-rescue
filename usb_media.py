"""Host-side access to the published rescue image and destination USB disk."""

import contextlib
import fcntl
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path

from export import IMAGE, connect, resolve_image

PARTITION_TYPES = {
    1: "21686148-6449-6e6f-744e-656564454649",
    2: "c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
    3: "0fc63daf-8483-4772-8e79-3d69d8477de4",
}


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def disk_info(device, *options):
    disks = json.loads(
        run(
            "lsblk",
            "--json",
            "--tree",
            *options,
            "--bytes",
            "--output",
            "PATH,TYPE,TRAN,MODEL,SIZE,LOG-SEC,RO,MOUNTPOINTS,PARTN,PARTTYPE,PTTYPE",
            "--",
            str(device),
        )
    )["blockdevices"]
    if len(disks) != 1:
        raise ValueError(f"Cannot identify disk {device}")
    return disks[0]


def usb_info(device):
    info = disk_info(device)
    if info["type"] != "disk" or info["tran"] != "usb" or info["ro"]:
        raise ValueError(f"{device} must be a writable whole USB disk")
    for entry in (info, *info.get("children", [])):
        if any(entry["mountpoints"] or []) or (
            entry is not info and (entry["type"] != "part" or entry.get("children"))
        ):
            raise ValueError(f"{device} is busy. Unmount and deactivate it first.")
    return info


@contextlib.contextmanager
def target_disk(device):
    device = device.resolve(strict=True)
    identity = device.stat()
    if not stat.S_ISBLK(identity.st_mode):
        raise ValueError(f"{device} is not a block device")
    with device.open("rb", buffering=0) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.fstat(lock.fileno()).st_rdev != identity.st_rdev:
            raise ValueError(f"{device} changed while opening it")
        yield device, identity.st_rdev, usb_info(device)


@contextlib.contextmanager
def source_image():
    with connect() as client:
        node, image, placement = resolve_image(client)
        with open_raw_image(node, image, placement) as source:
            yield source


@contextlib.contextmanager
def open_raw_image(node, image, placement):
    if image.format != "raw":
        raise ValueError(f"{IMAGE} must be a persistent raw image")
    path = Path(placement.file_path)
    if not path.is_absolute() or not path.is_file():
        raise ValueError(
            f"Run on {node.name}: registered image {path} is unavailable"
        )
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as image_file:
        info = os.fstat(image_file.fileno())
        if not stat.S_ISREG(info.st_mode) or not info.st_size:
            raise ValueError(f"{path} must be a nonempty regular raw image")
        yield path, image_file


def carrier(device):
    info = disk_info(device, "--properties-by", "blkid")
    parts = info.get("children", [])
    if (
        info["pttype"] != "gpt"
        or {p["partn"]: p["parttype"] for p in parts} != PARTITION_TYPES
    ):
        raise ValueError(f"{device} does not have the rescue GPT layout")
    partition = next(p["path"] for p in parts if p["partn"] == 3)
    for field, expected in (("TYPE", "ext4"), ("LABEL", "rescue-data")):
        if run("blkid", "-p", "-s", field, "-o", "value", partition) != expected:
            raise ValueError(f"{partition} must be the ext4 rescue-data partition")
    return partition


@contextlib.contextmanager
def mounted(partition, options):
    directory = Path(tempfile.mkdtemp(prefix="gentoo-rescue-"))
    try:
        run("mount", "-t", "ext4", "-o", options, "--", partition, str(directory))
        try:
            yield directory
        finally:
            run("umount", "--", str(directory))
    finally:
        if not directory.is_mount():
            directory.rmdir()


def main(operation, description):
    import argparse

    import capnp
    from corvus_client import CorvusError

    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("device", type=Path, help="whole USB block device")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.exit(1, "Root privileges are required. Run with sudo.\n")
    try:
        with source_image() as (image_path, image), target_disk(args.device) as target:
            operation(image_path, image, *target)
    except KeyboardInterrupt:
        parser.exit(130, "Interrupted.\n")
    except (
        OSError,
        ValueError,
        subprocess.CalledProcessError,
        CorvusError,
        capnp.KjException,
    ) as exc:
        parser.exit(1, f"USB operation failed: {exc}\n")
