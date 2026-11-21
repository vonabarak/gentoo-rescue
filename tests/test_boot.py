"""Exercise the real GRUB menu and guest shell through Corvus."""

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager

import capnp
import pytest
import yaml
from corvus_client import AsyncClient

MEDIA = "/run/initramfs/rescue-media"
LOWER = "/run/initramfs/rescue-lower"
MENU = b"Gentoo rescue (copy to RAM)"


class Console:
    def __init__(self, stream):
        self.stream = stream
        self.output = bytearray()

    async def wait_for(self, needle, start=0, timeout=120):
        deadline = time.monotonic() + timeout
        while needle not in self.output[start:]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"Waiting for {needle!r}: {self.output[-4096:]!r}")
            try:
                chunk = await asyncio.wait_for(self.stream.read(), remaining)
            except asyncio.TimeoutError as exc:
                raise AssertionError(
                    f"Waiting for {needle!r}: {self.output[-4096:]!r}"
                ) from exc
            if chunk is None:
                raise AssertionError(f"Console closed: {self.output[-4096:]!r}")
            self.output.extend(chunk)

    async def command(self, command, timeout=30):
        token = uuid.uuid4().hex
        # Split the marker in the command so terminal echo cannot match it.
        offset = len(self.output)
        await self.stream.write(
            f"({command}); printf '\\n%s%s:%s\\n' {token[:16]} {token[16:]} $?\r".encode()
        )
        await self.wait_for(f"{token}:0".encode(), offset, timeout=timeout)


@asynccontextmanager
async def boot(vm, downs):
    await vm.start(wait=True)
    try:
        stream = await vm.serial_console()
        try:
            console = Console(stream)
            await console.wait_for(MENU)
            await asyncio.sleep(0.4)
            for _ in range(downs):
                await stream.write(b"\x1b[B")
                await asyncio.sleep(0.35)
            await stream.write(b"\r")
            if downs == 1:
                await console.wait_for(b"Checking boot files against SHA256SUMS...")
            await console.wait_for(b"~ # ")
            yield console
        finally:
            await stream.close()
    finally:
        await vm.stop(wait=True, timeout_sec=45)


@asynccontextmanager
async def rescue_template():
    socket = os.environ.get("CORVUS_SOCKET")
    settings = (
        {"unix_socket": socket}
        if socket
        else {"host": os.environ.get("CORVUS_HOST", "127.0.0.1")}
    )
    async with AsyncClient(
        **settings, port=int(os.environ.get("CORVUS_PORT", "9876"))
    ) as client:
        disk = await client.disks.get(
            os.environ.get("RESCUE_IMAGE", "gentoo-rescue:latest")
        )
        image = await disk.show()
        name = f"rescue-it-{uuid.uuid4().hex}"
        template = await client.templates.create(
            yaml.safe_dump(
                {
                    "name": name,
                    "headless": True,
                    "cpuCount": 2,
                    "ram": "4G",
                    "drives": [
                        {
                            "diskImage": image.id,
                            "interface": "scsi",
                            "strategy": "overlay",
                        },
                        {
                            "diskImage": "ovmf-code",
                            "strategy": "direct",
                            "interface": "pflash",
                            "readOnly": True,
                        },
                        {
                            "diskImage": "ovmf-vars",
                            "interface": "pflash",
                            "strategy": "clone",
                        },
                    ],
                }
            )
        )
        try:
            yield template, name
        finally:
            await template.delete()


@pytest.mark.integration
@pytest.mark.parametrize("downs", [0, 1, 2], ids=["normal", "checksum", "copy-to-ram"])
def test_rescue_boot(downs):
    asyncio.run(capnp.run(check_boot(downs)))


async def check_boot(downs):
    async with rescue_template() as (template, name):
        vm = await template.instantiate(f"{name}-{downs}")
        try:
            await check_vm(vm, downs)
        finally:
            await vm.delete(force=True)


async def check_vm(vm, downs):
    if downs == 2:
        async with boot(vm, 0) as console:
            await console.command(
                "test -d /root/lost+found && echo rescue-marker > /root/.boot-test && sync"
            )
    async with boot(vm, downs) as console:
        await console.command(
            'test "$(id -u)" = 0 && '
            'test "$(findmnt -rn -o FSTYPE,VFS-OPTIONS -M / | cut -d, -f1)" = "overlay rw" && '
            f'test "$(findmnt -rn -o FSTYPE,VFS-OPTIONS -M {LOWER} | cut -d, -f1)" = "squashfs ro"'
        )
        await console.command(
            "test -s /root/packages.md && grep -q '^# Installed packages$' /root/packages.md"
        )
        if downs < 2:
            assert b"Boot file verification failed." not in console.output
            await console.command(
                'test "$(findmnt -n -o FSTYPE -M /root)" = ext4 && '
                "touch /root/.boot-write-test"
            )
            await console.command(
                'test -x /usr/local/sbin/rescue-update && '
                '! rescue-update --yes > /tmp/update-error 2>&1 && '
                "grep -Fq 'Boot using Gentoo rescue (copy to RAM)' /tmp/update-error"
            )
        else:
            await console.command(
                "grep -qw 'corvus.rescue.ram=1' /proc/cmdline && "
                f'test "$(findmnt -n -o FSTYPE -M {MEDIA})" = tmpfs && '
                'test "$(findmnt -n -o FSTYPE -M /root)" = tmpfs && '
                'test "$(cat /root/.boot-test)" = rescue-marker && '
                f"test ! -e /root/lost+found && test ! -e {MEDIA}/lost+found && "
                'carrier=$(blkid -L rescue-data) && test -b "$carrier" && '
                '! findmnt -n -S "$carrier" && '
                f"test -s {MEDIA}/vmlinuz && test -s {MEDIA}/initramfs.img && "
                f"test -s {MEDIA}/root.squashfs && test -s {MEDIA}/SHA256SUMS && "
                "touch /root/.boot-write-test"
            )
            await check_ram_update(console)


async def check_ram_update(console):
    await console.command(
        'carrier=$(blkid -L rescue-data) && mkdir -p /tmp/update-carrier && '
        'mount "$carrier" /tmp/update-carrier && '
        'rm /tmp/update-carrier/vmlinuz /tmp/update-carrier/SHA256SUMS && '
        'umount /tmp/update-carrier'
    )
    # Serve the RAM-boot files as a fixture while exercising actual disk mounts.
    await console.command(
        f'export UPDATE_FIXTURE={MEDIA}; '
        'curl() { local name=${!#} out head=0; name=${name##*/}; '
        'while (( $# )); do case "$1" in '
        '--output) shift; out=$1 ;; --head) head=1 ;; esac; shift; done; '
        'if (( head )); then printf "Content-Length: %s\\r\\n" '
        '"$(stat -c %s "$UPDATE_FIXTURE/$name")" > "$out"; '
        'else cp "$UPDATE_FIXTURE/$name" "$out"; fi && printf 200; }; '
        'export -f curl; rescue-update --yes',
        timeout=120,
    )
    await console.command(
        'carrier=$(blkid -L rescue-data) && ! findmnt -n -S "$carrier" && '
        'mount -o ro "$carrier" /tmp/update-carrier && '
        f'cmp {MEDIA}/vmlinuz /tmp/update-carrier/vmlinuz && '
        f'cmp {MEDIA}/SHA256SUMS /tmp/update-carrier/SHA256SUMS && '
        f'cmp {MEDIA}/packages.md /tmp/update-carrier/packages.md && '
        'test "$(cat /tmp/update-carrier/.boot-test)" = rescue-marker && '
        'test ! -e /tmp/update-carrier/.boot-write-test && '
        '(cd /tmp/update-carrier && sha256sum --check SHA256SUMS) && '
        'umount /tmp/update-carrier',
        timeout=120,
    )
