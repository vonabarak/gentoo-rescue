# Gentoo rescue agent guide

This repository builds a bootable Gentoo rescue USB image using installed Corvus.

## Project map

- `rescue.yml` defines the Corvus build pipeline and VM templates.
- `build.py` composes the image recipe. `rescue_stages.py` implements the
  rescue-specific stages on top of `corvus_image_builder`.
- `assets/grub/` holds the menu template. `assets/root/` mirrors paths in
  the target filesystem, including dracut hooks and systemd configuration.
- `export.py` exports verified payloads. `write_usb.py` and `update_usb.py`
  manage USB media, with shared host-side logic in `usb_media.py`.

## Build conventions

- Use the installed Corvus Python client and image builder. The Makefile
  accepts `PYTHON` and `CORVUS_SHARE_DIR` overrides.
- Reuse the registered `gentoo-glibc-systemd-builder-cloudinit:latest`.
  Invoke the installed Corvus YAML recipe only when that dependency is missing.
  Keep dependency preparation separate from rescue image publication.

## Boot and USB invariants

- GRUB locates the ext4 carrier by UUID. Keep `vmlinuz`, `initramfs.img`,
  and `root.squashfs` consistent with the carrier's `SHA256SUMS`.
- Normal boot uses a read-only SquashFS with a volatile root overlay.
  Writable carrier storage provides persistent `/root`. RAM boot copies
  the carrier contents into tmpfs, skipping only its top-level `lost+found`.
  See [Disk and runtime](README.md#disk-and-runtime) and
  [Boot from RAM](README.md#boot-from-ram).
- The root-only USB tools use the first registered Corvus node's local
  `gentoo-rescue:latest` placement. Preserve the flash warning and cancellable
  10-second countdown. The offline updater verifies checksums, replaces only
  missing or changed payloads and manifests, and installs the manifest last
  when it changes. Preserve persistent home, the carrier UUID, and the ESP.
  Use disposable media for destructive tests. See
  [Initial USB installation](README.md#initial-usb-installation) and
  [Offline payload updates](README.md#offline-payload-updates).

## Verification

- Run `make test` for local regressions and `make plan` for build-stage changes.
- For boot changes, run `make test-integration` with a published image that
  contains the change. The serial-console tests cover normal boot, checksum
  verification, and copy to RAM. See [Validation](README.md#validation).
- `make check` checks for published Corvus outputs.
