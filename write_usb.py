"""Write the published Gentoo rescue image to a USB drive."""

import os
import time

from usb_media import main, run, usb_info

CHUNK = 4 * 1024 * 1024


def write_usb(path, image, device, identity, info):
    size = os.fstat(image.fileno()).st_size
    if info["size"] < size or info["log-sec"] != 512 or size % 512:
        raise ValueError(
            "USB capacity or logical sector size is incompatible with the image"
        )
    print(
        f"Image: {path}\nTarget: {device} ({info['model'] or 'unknown model'}, "
        f"{info['size']} bytes)"
    )
    print(
        "WARNING: All files on the drive will be replaced. Press Ctrl+C to cancel.",
        flush=True,
    )
    deadline = time.monotonic() + 10
    for seconds in range(10, 0, -1):
        print(f"Writing in {seconds} seconds…", flush=True)
        time.sleep(max(0, deadline - seconds + 1 - time.monotonic()))

    if usb_info(device) != info:
        raise ValueError("USB device changed during the countdown")
    fd = os.open(device, os.O_RDWR | os.O_EXCL)
    with os.fdopen(fd, "r+b") as target:
        if os.fstat(fd).st_rdev != identity or run(
            "blockdev", "--getsize64", f"/proc/{os.getpid()}/fd/{fd}"
        ) != str(info["size"]):
            raise ValueError("USB device changed during the countdown")
        print("Writing image…", flush=True)
        remaining = size
        while remaining:
            chunk = image.read(min(remaining, CHUNK))
            if not chunk:
                raise ValueError("Source image was truncated")
            target.write(chunk)
            remaining -= len(chunk)
        target.flush()
        os.fsync(fd)
        run("blockdev", "--flushbufs", f"/proc/{os.getpid()}/fd/{fd}")
        print("Verifying written image…", flush=True)
        image.seek(0)
        target.seek(0)
        remaining = size
        while remaining:
            chunk = image.read(min(remaining, CHUNK))
            if not chunk or target.read(len(chunk)) != chunk:
                raise ValueError("USB readback verification failed")
            remaining -= len(chunk)
        if info["size"] > size:
            print(run("sgdisk", "--move-second-header", f"/proc/{os.getpid()}/fd/{fd}"))
            os.fsync(fd)
    run("blockdev", "--rereadpt", str(device))
    print(f"Verified rescue image written to {device}.")


if __name__ == "__main__":
    main(write_usb, __doc__)
