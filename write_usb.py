"""Write the published Gentoo rescue image to a USB drive."""

import os
import time

from usb_media import main, run, usb_info

CHUNK = 4 * 1024 * 1024
MIB = 1024 * 1024


def report_progress(phase, size, remaining, last_report):
    now = time.monotonic()
    if remaining and now - last_report < 1:
        return last_report
    completed = size - remaining
    print(
        f"{phase}: {completed * 100 // size}% "
        f"({completed / MIB:.1f} / {size / MIB:.1f} MiB)",
        flush=True,
    )
    return now


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
    hr = "*" * 77
    print(
        f"\n{hr}\n* WARNING: All files on the drive will be replaced. Press Ctrl+C to cancel. *\n{hr}\n",
        flush=True,
    )
    deadline = time.monotonic() + 10
    for seconds in range(10, 0, -1):
        print(f"Writing in {seconds} seconds…", flush=True)
        time.sleep(max(0, deadline - seconds + 1 - time.monotonic()))

    if usb_info(device) != info:
        raise ValueError("USB device changed during the countdown")
    # Report writes as they reach the device instead of the kernel page cache.
    fd = os.open(device, os.O_RDWR | os.O_EXCL | os.O_DSYNC)
    with os.fdopen(fd, "r+b") as target:
        if os.fstat(fd).st_rdev != identity or run(
            "blockdev", "--getsize64", f"/proc/{os.getpid()}/fd/{fd}"
        ) != str(info["size"]):
            raise ValueError("USB device changed during the countdown")
        print("Writing image…", flush=True)
        last_report = time.monotonic()
        remaining = size
        while remaining:
            chunk = image.read(min(remaining, CHUNK))
            if not chunk:
                raise ValueError("Source image was truncated")
            target.write(chunk)
            remaining -= len(chunk)
            if remaining:
                last_report = report_progress("Writing", size, remaining, last_report)
        print("Flushing writes to USB…", flush=True)
        target.flush()
        os.fsync(fd)
        run("blockdev", "--flushbufs", f"/proc/{os.getpid()}/fd/{fd}")
        if size:
            report_progress("Writing", size, 0, last_report)
        print("Verifying written image…", flush=True)
        image.seek(0)
        target.seek(0)
        last_report = time.monotonic()
        remaining = size
        while remaining:
            chunk = image.read(min(remaining, CHUNK))
            if not chunk or target.read(len(chunk)) != chunk:
                raise ValueError("USB readback verification failed")
            remaining -= len(chunk)
            last_report = report_progress("Verifying", size, remaining, last_report)
        if info["size"] > size:
            print("Moving backup GPT header…", flush=True)
            print(run("sgdisk", "--move-second-header", f"/proc/{os.getpid()}/fd/{fd}"))
            os.fsync(fd)
    run("blockdev", "--rereadpt", str(device))
    print(f"Verified rescue image written to {device}.")


if __name__ == "__main__":
    main(write_usb, __doc__)
