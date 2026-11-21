"""Publish the latest rescue disk and offline update payloads."""

import argparse
import hashlib
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

import capnp
from corvus_client import CorvusError

from export import INVENTORY, MANIFEST, PAYLOADS, connect, download_payloads, resolve_image
from usb_media import open_raw_image

HOST = "gentoo-rescue.v7k.me"
PUBLIC = "/var/www/public"
RAW = "gentoo-rescue.raw"
RAW_MANIFEST = f"{RAW}.sha256"
MANIFESTS = (RAW_MANIFEST, MANIFEST)
RELEASE = (RAW, *PAYLOADS, INVENTORY, *MANIFESTS)


def report(message):
    print(message, flush=True)


def prepare_with_progress(work):
    done = threading.Event()
    started = time.monotonic()

    def heartbeat():
        while not done.wait(10):
            report(f"Preparing release ({int(time.monotonic() - started)}s elapsed)")

    worker = threading.Thread(target=heartbeat)
    worker.start()
    try:
        return prepare(work)
    finally:
        done.set()
        worker.join()


def prepare(work):
    work.mkdir(parents=True, exist_ok=True)
    with connect() as client:
        report("Resolving gentoo-rescue:latest")
        node, image, placement = resolve_image(client)
        with open_raw_image(node, image, placement) as (path, source):
            report(f"Calculating SHA-256 for {path}")
            digest = hashlib.file_digest(source, "sha256")
        (work / RAW_MANIFEST).write_text(
            f"{digest.hexdigest()}  {RAW}\n", encoding="ascii"
        )
        download_payloads(client, work, node, image, progress=report, require_inventory=True)
    return path


def transfer(work, raw_path):
    for name in RELEASE:
        source = raw_path if name == RAW else work / name
        destination = f"{HOST}:{PUBLIC}/{name}"
        print(f"Uploading {name} to {destination}", flush=True)
        scp = ["scp", str(source), destination]
        if sys.stdout.isatty():
            subprocess.run(scp, check=True)
            report(f"Uploaded {name}")
            continue

        command = ["script", "-qef", "-c", shlex.join(scp), "/dev/null"]
        with subprocess.Popen(command, stdout=subprocess.PIPE) as process:
            # SCP redraws its terminal meter with carriage returns.
            for chunk in iter(lambda: process.stdout.read1(4096), b""):
                sys.stdout.buffer.write(chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n"))
                sys.stdout.buffer.flush()
            status = process.wait()
        if status:
            raise subprocess.CalledProcessError(status, command)
        report(f"Uploaded {name}")


def set_public_permissions():
    paths = [f"{PUBLIC}/{name}" for name in RELEASE]
    subprocess.run(["ssh", HOST, shlex.join(["chmod", "0644", "--", *paths])], check=True)


def verify_public(work):
    for name in (INVENTORY, *MANIFESTS):
        url = f"https://{HOST}/{name}"
        report(f"Checking {url}")
        response = subprocess.check_output([
            "curl", "--fail", "--silent", "--show-error", "--max-time", "30",
            "--header", "Cache-Control: no-cache", "--write-out", "%{http_code}", url,
        ])
        body, status = response[:-3], response[-3:]
        if status != b"200" or body != (work / name).read_bytes():
            raise ValueError(f"Unexpected response from {url} (HTTP {status.decode()})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    work = parser.parse_args().output.resolve()
    phase = "local preparation"
    try:
        raw_path = prepare_with_progress(work)
        phase = "SCP transfer"
        transfer(work, raw_path)
        phase = "setting public file permissions"
        set_public_permissions()
        phase = "public file verification"
        verify_public(work)
    except (OSError, ValueError, CorvusError, capnp.KjException, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None)
        sys.exit(f"Rescue upload failed during {phase}: {exc}" + (f"\n{detail.strip()}" if detail else ""))
    print(f"Published and verified inventory and checksums at https://{HOST}/")


if __name__ == "__main__":
    main()
