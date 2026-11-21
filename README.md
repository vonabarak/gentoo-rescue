# Gentoo rescue USB image

## Downloads

- Full USB image ([gentoo-rescue.raw](https://gentoo-rescue.v7k.me/gentoo-rescue.raw)) and its [SHA-256 checksum](https://gentoo-rescue.v7k.me/gentoo-rescue.raw.sha256)
- Offline updates ([vmlinuz](https://gentoo-rescue.v7k.me/vmlinuz), [initramfs.img](https://gentoo-rescue.v7k.me/initramfs.img), and [root.squashfs](https://gentoo-rescue.v7k.me/root.squashfs)) and their [SHA256SUMS manifest](https://gentoo-rescue.v7k.me/SHA256SUMS)

Browse all files in the [public download directory](https://gentoo-rescue.v7k.me/).

## Build

Build and publish the 2 GiB raw disk and its runtime template:

```sh
make build
make check
```

Install [Corvus](https://github.com/vonabarak/corvus) with the Python client
and image builder, and use a Python environment with that distribution installed. The Gentoo ebuild enables
these modules with the `python` USE flag. For a binary release, install the
bundled wheel, put `crv` on PATH, and set `CORVUS_SHARE_DIR` to the extracted
release root. The default is `/usr/share/corvus`. The dependency recipes also
use Bash, jq, and curl. The Corvus host needs the `corvus` network, registered
`ovmf-code` and `ovmf-vars` images, and a writable `GENTOO_CACHE_DIR` for
virtiofs. Use `PYTHON=/absolute/path/to/python` to select the interpreter.

The build reports the compressed root filesystem size and available ext4 space.
First writable boot expands storage to the USB stick's capacity.

Rescue builds reuse the registered `gentoo-glibc-systemd-builder-cloudinit:latest`
image. If it is missing, the Makefile runs Corvus’s installed YAML recipes to
create it. Rescue builds default to `IMAGE_POLICY=update`, skipping publication
when the rescue recipe is unchanged. When a build runs, it synchronizes the Gentoo Portage tree and
selects current unmasked **stable amd64** packages with the stable systemd profile.
Publication creates a new version and moves `latest`, preserving existing versions
and users. Remote Portage changes alone do not trigger a build; use
`IMAGE_POLICY=overwrite` to force a refresh. Explicit policy overrides still work.
`make ensure` fills missing rescue outputs. `make rebuild` forces publication
of the rescue image using the registered builder, creating the builder only
if it is missing. Compatible package caches speed up installation; packages
may be compiled from source when the selected version is unavailable as a binary.

Rescue uses the shared Gentoo Portage verification library for signed repository
snapshots, repository metadata, and downloaded binary packages. The common
`GENTOO_KEYSERVER` option selects the HKPS repository key refresh endpoint;
see the Portage verification section in the Corvus image guide. Unsigned locally built
packages remain usable in the trusted shared cache.

The registered glibc/systemd builder provides native tools and caches. Rescue
uses the Gentoo distribution binary kernel, its complete modules, linux-firmware,
and a generic, non-hostonly dracut initramfs. It does not use the VM-only kernel
used by other Corvus Gentoo images. The recipe checks required hardware options,
boot drivers, and squashfs/OverlayFS support before publication.

`build.py` composes the stages in `rescue_stages.py` with the shared builder
stages. The bundle includes the recipe-local module. The YAML uploads
`assets/` as a directory. Files under `assets/root/` mirror their destinations
in the target root filesystem. The other asset directories hold shared
systemd drop-ins, Portage policy, and the GRUB template. The build fills in
the carrier UUID in that template and generates the payload checksum manifest.

## Rescue utilities

The image includes BIOS and UEFI GRUB repair tools, FSArchiver, Borg, rclone,
XFS dump and restore, and the QEMU disk utilities `qemu-img` and `qemu-nbd`.
Network tools include `socat`, `iperf3`, `nmap`, `dig`, `smbclient`, and
`mount.cifs`. Hardware diagnostics include `dmidecode`, `lsscsi`, `sg_inq`,
`hdparm`, `lshw`, `hwinfo`, `ipmitool`, `memtester`, and `picocom`.

To enable multipath for the current session, configure `/etc/multipath.conf`
for the intended disks, then run:

```sh
rm /etc/udev/rules.d/56-multipath.rules
udevadm control --reload
systemctl unmask multipathd.service multipathd.socket
systemctl start multipathd.service
multipath -ll
```

## Disk and runtime

| GPT partition | Size | Format | Use |
| --- | --- | --- | --- |
| 1 | 1 MiB | BIOS boot | BIOS GRUB |
| 2 | 64 MiB | FAT32 | Removable UEFI GRUB; normally unmounted |
| 3 | Remaining space | ext4, `rescue-data` | Kernel, initramfs, compressed root and persistent home |

The ESP holds `/EFI/BOOT/BOOTX64.EFI`, `/grub/grub.cfg` and both GRUB
module sets. Its menu waits 300 seconds, reports kernel/initramfs loading and
searches the ext4 UUID. Select **Gentoo rescue (verify boot files)**
to check every file listed in `SHA256SUMS` before GRUB loads Linux. The list
covers the kernel, initramfs and SquashFS. A failed check cancels that boot entry. The ext4 partition contains:

```text
vmlinuz
initramfs.img
root.squashfs
packages.md
SHA256SUMS
```

The build records all installed Gentoo package versions in `packages.md`.
It appears at `/root/packages.md` on writable and copy-to-RAM boots.

A custom generic dracut module mounts this partition under
`/run/initramfs/rescue-media`, mounts the squashfs file read-only through a loop
device, and supplies a tmpfs OverlayFS for `/`. These mounts survive switch-root.
A small, versioned shutdown initramfs inside the squashfs lets dracut release
them in dependency order without unpacking the boot archive's hardware firmware
into RAM again. The carrier is mounted
read-write when possible, otherwise read-only with journal replay disabled.
`/root` binds the ext4 partition root only if writable,
with free blocks/inodes and a successful synchronized write probe. Otherwise it
is an empty tmpfs; fallback never unmounts the carrier backing the running root.
Changes elsewhere, including installed packages and configuration, vanish on reboot.
Carrier corruption that prevents reading the kernel or squashfs can prevent boot.

On writable boot, `rescue-grow` validates the boot UUID, ext4 label, parent disk
and exact three-partition GPT layout before extending partition 3 and its ext4
filesystem. It retries filesystem growth on later boots, including after partial
growth. Failures do not prevent home fallback or console login. Other disks are
never selected for expansion. Read-only media skip expansion and persistent home.

The bake template has **32 GiB RAM and 8 CPUs**. Installation happens entirely in
a **16 GiB tmpfs** at `/mnt/sysroot`, followed by XZ SquashFS compression
(1 MiB blocks, 2 GiB compressor memory limit). No staging system is written
to the target ext4 partition. The runtime image needs much less memory.

Local consoles log in automatically as root; the root password is locked. Use
only where unrestricted local root access is appropriate. SSH starts automatically;
install a public key in `/root/.ssh/authorized_keys` for root login. SSH host keys
persist under `/root/.ssh/host-keys` and are generated on first boot.
The default locale is `C.UTF-8`, and `en_US.UTF-8` is available for SSH clients
that forward their locale settings.

Local Linux virtual consoles use the Terminus font. Logins open a plain shell.
Run `neofetch` to display system information or `tmux` to start a tmux session.
The system-wide tmux configuration starts CMatrix after 10 minutes of
inactivity in an attached session. Press any key to return to the session.

GPM starts at boot. With a supported mouse, drag with the left button to
select text and middle-click to paste on a Linux virtual console. The image
also includes `tree`, `lnav`, `rg`, and `fzf` for browsing directories, reading
logs, searching text, and fuzzy selection.

NetworkManager provides wired DHCP and automatically reconnects saved Wi-Fi
profiles. To connect and save a Wi-Fi network without a GUI:

```sh
nmcli --ask device wifi connect "YOUR_SSID"
```

Profiles and their passwords are stored in plain text in root-only files under
`/root/.config/NetworkManager/system-connections`. When persistent home is
unavailable, profiles, authorized keys, and host keys are temporary or absent;
the SSH host identity changes on each such boot.

QEMU guest-agent is enabled for VM testing.
Recovery disks are not mounted automatically; swap, RAID assembly and LVM
activation require explicit commands. Filesystem repair, partitioning, data
recovery, encryption, RAID/LVM, device diagnostics, networking and archive tools
are installed, including `mc`, `htop`, `strace`, `jq`, `yq`, `tmux`, `rar`,
`ncdu`, `lsof`, `exfatprogs`, and `mtr`. The `yq` command is the jq-based
YAML processor. No graphical desktop or ZFS is included.

Requires x86-64, BIOS or UEFI with Secure Boot disabled. Start with 4 GiB RAM;
2 GiB is the minimum target and available RAM limits volatile writes. There is
no persistent system overlay. Initial installation replaces the stick; subsequent
updates replace the three payload files and their checksums offline.

## Boot from RAM

Select **Gentoo rescue (copy to RAM)** to copy the `rescue-data` files into
tmpfs, including the kernel, initramfs, compressed root, and home dotfiles.
The copy skips the top-level `lost+found` directory. The USB is safe to remove
once the login prompt or shell appears. Home starts with the USB's SSH keys
and network profiles. Session changes disappear at reboot.

The copy must fit within half of physical RAM and leave at least 1 GiB of
boot-time available memory. It can take several minutes on a slow drive.
If RAM preparation fails, check `journalctl -u rescue-root.service` from
the emergency shell, or reboot and select **Gentoo rescue**.

## Export rescue payloads

Run this command with the Corvus Python client installed in the selected Python
environment and with access to the Corvus daemon.

```sh
make export
```

Use `PYTHON=/absolute/path/to/python` to select an environment.
Set `CORVUS_SOCKET` for a Unix socket or `CORVUS_HOST` and `CORVUS_PORT` for
TCP. TCP uses the Python client's default TLS configuration.

The first Corvus node must have the published `gentoo-rescue:latest` image
and be able to instantiate the `gentoo-musl-openrc-headless` template.
The exporter attaches the rescue image read-only to a temporary VM, downloads
the boot files and the package inventory when present, verifies the boot
payload checksums, and deletes the VM. It writes the files to
`build/gentoo-rescue/`. Set `OUTPUT_DIR=/absolute/path` to override the
destination. Use a fresh output directory when exporting an older image
to avoid retaining a report from a previous export.

## Publish public downloads

Run `make upload` on the first Corvus node with read access to the local
`gentoo-rescue:latest` raw image and access to the exporter VM template.
The command writes the update files and image checksum to `OUTPUT_DIR`
(default `build/gentoo-rescue`). The local `script` command from util-linux
keeps SCP progress visible in captured logs.

The configured SSH account on `gentoo-rescue.v7k.me` needs write access to
`/var/www/public`. SCP directly replaces `gentoo-rescue.raw`, `vmlinuz`,
`initramfs.img`, `root.squashfs`, `packages.md`, `gentoo-rescue.raw.sha256`,
and `SHA256SUMS`, with the checksum files copied last. An interrupted transfer can leave a
partial public file. Rerun `make upload` to replace the complete set.
The command sets the public files to mode `0644` and verifies the package
inventory and both checksum files over HTTPS after copying. Rebuild older
images to generate the inventory before uploading.

## Initial USB installation

Run on the first registered Corvus node with the local placement of
`gentoo-rescue:latest`. Both USB commands require root and use the same
Corvus connection settings as the exporter. Pass those settings to the
process running under sudo if your installation uses a custom connection.
A separate export is unnecessary.

Use the whole unmounted USB device. Check its path before running:

```sh
sudo make usb-write DEVICE=/dev/disk/by-id/usb-…
```

All files on the drive will be replaced. Press Ctrl+C during the 10-second
countdown to cancel. The command reads the written image back to verify it.
The first writable boot expands storage to use the remaining device capacity.

## Offline payload updates

Boot another OS and unmount all partitions on the rescue USB. Then run:

```sh
sudo make usb-update DEVICE=/dev/disk/by-id/usb-…
```

The updater verifies the published image's payload checksums, stages changed
files and installs the checksum manifest last. It refreshes `packages.md`
when the source image includes it. With a legacy source, an existing report
remains a snapshot of the release that supplied it. The updater preserves
other persistent home files, the carrier UUID, and the existing ESP and GRUB menu. Leave enough free space
for the staged files. If the update is interrupted, rerun the command before
booting. To change the GRUB menu or migrate an older disk layout, back up home,
flash the current image, and restore home to the ext4 partition root.

## Validation

```sh
make test
make test-integration
```

Install pytest and jsonschema in the selected Python environment. The integration
tests use the exporter connection settings and boot disposable UEFI VMs from
`gentoo-rescue:latest`. Set `RESCUE_IMAGE` to test a different registered image
or version.

The runtime regression checks simulate healthy, absent, read-only, full,
inode-exhausted and I/O-failing storage, plus failed binds/remounts/unmounts.
They check empty home fallback without unmounting the carrier, and validate GRUB
and initramfs generator output. They do not substitute for boot tests.

The squashfs layout has passed QEMU boots with BIOS/virtio, BIOS/SATA,
UEFI/NVMe and UEFI/USB. Checks covered expansion of a compact image to 16 GiB,
idempotent reboots, persistent home, volatile `/etc`, and clean shutdown through
dracut's exit environment. A read-only USB boot with 2 GiB RAM used an empty
temporary home and left the complete disk hash unchanged.
An offline update from a separately built image preserved the destination's
different filesystem UUID, ESP hash and existing home files; payload checksums
and an offline ext4 check passed afterward. The updated disk booted through BIOS
and read-only UEFI/USB with 2 GiB RAM.

For each release, build through the real recipe and check tmpfs capacity and bake
memory. Boot BIOS and UEFI with virtio, SATA, NVMe and USB storage. Verify squashfs
is read-only, `/` is writable OverlayFS, `/root` is the ext4 partition root,
the ESP stays unmounted, `/etc` changes vanish and home persists. Boot whole-media
read-only with 2 GiB RAM and compare the complete disk hash before/after.

Flash a compact export onto a larger disposable disk; verify GPT/partition and
filesystem expansion, persistent home, idempotent reboots, and offline payload
updates preserving UUID, ESP and home. Check full/inode-exhausted/read-only/I/O
fallback and interrupted growth/update recovery. Check clean shutdown unmounts
before hashing writable media. Confirm unrelated swap, RAID and LVM disks stay
inactive. Physical USB, keyboard, Ethernet and Wi-Fi tests must be recorded
separately from QEMU checks; the earlier layout was tested on physical hardware.
