# Pamir RK3576 Linux BSP

Manifest for the Pamir RK3576 Linux 6.1 BSP: a Debian Bookworm arm64 server
image with seamless A/B slots, runtime OTA, and a Nix-primary userland, produced
by the mkosi rootfs builder and assembled with Google's `repo` tool.

This repository hosts a single manifest. `repo init` selects `default.xml`
(which includes `rk3576-debian-ab.xml`) automatically; no `-m` flag is required.

## Contents

1. [Prerequisites](#1-prerequisites)
2. [Sync the tree](#2-sync-the-tree)
3. [Build update.img](#3-build-updateimg)
4. [Flash](#4-flash)
5. [What the image provides](#5-what-the-image-provides)
6. [Updating the package pool](#6-updating-the-package-pool)

## 1. Prerequisites

### 1.1 repo tool

```bash
mkdir -p ~/.local/bin
curl https://storage.googleapis.com/git-repo-downloads/repo > ~/.local/bin/repo
chmod a+x ~/.local/bin/repo
export PATH="$HOME/.local/bin:$PATH"
```

### 1.2 GitHub CLI

Most BSP component repositories are private, and the cross-toolchain and mkosi
package pool are delivered as GitHub Release assets. Authenticate before syncing:

```bash
gh auth login          # or: export GH_TOKEN=<token>
```

### 1.3 Host environment

- Linux x86_64 host (Debian 12+ or Ubuntu 22.04+ recommended).
- The build tree must reside on an `ext4`, `xfs`, `btrfs`, `f2fs`, or `zfs`
  filesystem. `overlayfs`, NTFS, and network mounts are rejected.
- The build user must own the tree and have working `sudo`; image packing and
  the Nix bootstrap require it. Do not run the build itself under `sudo`.
- Network access is required at build time (debootstrap, the Nix release, and
  the Release-asset fetches).
- `qemu-user-static` binfmt must be registered: the rootfs is assembled in an
  arm64 chroot under qemu on the x86_64 host.

### 1.4 Host packages

```bash
sudo apt-get install -y \
    build-essential gcc g++ make bc bison flex libssl-dev \
    git curl ca-certificates rsync fakeroot scons cpio gzip zstd sudo \
    python3 python-is-python3 device-tree-compiler \
    mkosi systemd-container debootstrap dpkg-dev \
    qemu-user-static binfmt-support
```

`mkosi` is the rootfs builder and must be version 25 or newer; the tree is
tested against v26. On older distributions, install it from PyPI
(`pipx install mkosi`) or from backports.

## 2. Sync the tree

```bash
mkdir -p ~/pamir-rk3576 && cd ~/pamir-rk3576
repo init -u https://github.com/pamir-ai-pkgs/manifest -b main
repo sync -j"$(nproc)" --verify
```

`--verify` is required. The post-sync hook in `linux-rockchip-bsp-tools` runs
only under `--verify`, and it fetches the AArch64 cross-toolchain and the mkosi
package pool from their GitHub Releases. Without it, `repo sync` still exits 0,
but the toolchain and `mkosi/packages/` are absent and the build fails.

To fetch those assets by hand:

```bash
bsp-tools/fetch-prebuilts.sh        # cross-toolchain
mkosi/scripts/fetch-packages.sh     # mkosi package pool (latest release)
```

## 3. Build update.img

```bash
./build.sh rockchip_rk3576_lapis_defconfig    # select the Lapis configuration
./build.sh                                     # U-Boot + kernel + rootfs + firmware
```

The flashable image is written to `rockdev/update.img`, alongside the
per-partition images (`MiniLoaderAll.bin`, `uboot.img`, `boot.img`,
`rootfs.img`, `userdata.img`). To repack only the image after a component
rebuild:

```bash
./build.sh updateimg
```

## 4. Flash

Place the board in Maskrom mode (or Loader/rockusb mode), connect USB-C, then
flash the full image:

```bash
./rkflash.sh update                 # full update.img
```

Or use Rockchip's tool directly:

```bash
upgrade_tool uf rockdev/update.img
```

A full `update.img` flash provisions both A/B slots and the userdata partition.
Run `./rkflash.sh` with no arguments for the partition list and single-partition
reflashes.

## 5. What the image provides

- **Seamless A/B** — two rootfs slots (`system_a` / `system_b`) selected by
  U-Boot via the Android-style `slot_suffix`; OTA writes the inactive slot and
  switches on a healthy boot.
- **Runtime OTA** — `lapis-ota` drives `updateEngine` against the A/B slots;
  mark-success is automatic after a good boot.
- **Persistence** — `/var`, `/home`, `/root`, NetworkManager state, SSH host
  keys, machine-id, and the Nix store live in the `userdata` partition and
  survive slot switches; the rootfs slots remain reproducible.
- **Nix-primary userland** — the Nix store resides in `userdata`, outside the
  A/B pair, bind-mounted at `/nix`; user software is managed through Nix.

## 6. Updating the package pool

The mkosi package pool is published as a Release asset on `linux-rockchip-mkosi`
and is always fetched at its latest release; no tag or checksum is pinned. To
publish a new pool, rebuild the tarball and its `.sha256` and upload both to a
new Release under the same asset names. The next `repo sync --verify` picks it
up.
