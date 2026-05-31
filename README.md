# Pamir AI Manifest Repository

This repository contains the manifest file for managing all Pamir AI Distiller projects using Google's `repo` tool.

## Prerequisites

### Install Google Repo Tool

```bash
mkdir -p ~/.local/bin
curl https://storage.googleapis.com/git-repo-downloads/repo > ~/.local/bin/repo
chmod a+x ~/.local/bin/repo
export PATH="$HOME/.local/bin:$PATH"
```

### Verify Installation

```bash
repo version
```

## RK3576 Linux BSP (`rk3576-debian-ab.xml`)

The `rk3576-debian-ab.xml` manifest builds the Pamir RK3576 BSP: a Debian
Bookworm arm64 server image with seamless A/B slots, runtime OTA, and a
Nix-primary userland, produced by the `mkosi` rootfs builder.

### Build-host prerequisites

**Host & environment**

- **Linux x86_64 host** (Debian 12+ / Ubuntu 22.04+ recommended).
- **The build user must own the tree** — the SDK enforces this; never build
  under `sudo`. Fix ownership with `sudo chown -h -R $(id -un):$(id -un) <tree>`.
  The build invokes `sudo` internally (ext4 image packing, the Nix chroot), so
  the user needs working `sudo`.
- **Build tree on an ext4 / xfs / btrfs / f2fs / zfs filesystem** — the SDK
  refuses overlayfs, NTFS, and network mounts.
- **Network access at build time** — `debootstrap` pulls Debian Bookworm,
  `fetch-nix-tarball.sh` pulls the Nix release, and `gh` pulls the toolchain and
  package pool.
- **`qemu-user-static` binfmt registered** — the rootfs postinst runs in an
  arm64 chroot under qemu on an x86_64 host.

**Tools fetched at sync (no manual install)**

- **AArch64 cross-toolchain** and the **mkosi deb package pool** — both pulled by
  the `repo sync --verify` post-sync hook from GitHub Releases.
- **`repo`** — Google's repo tool (see top of this README).
- **`gh` (GitHub CLI), authenticated** — most BSP repos are private and the
  Release assets need auth; run `gh auth login` (or export `GH_TOKEN`) first.

**Host packages (Debian/Ubuntu)**

```bash
sudo apt-get install -y \
    build-essential gcc g++ make bc bison flex libssl-dev \
    git curl ca-certificates rsync fakeroot scons cpio gzip zstd sudo \
    python3 python-is-python3 device-tree-compiler \
    mkosi systemd-container debootstrap dpkg-dev \
    qemu-user-static binfmt-support
```

- `mkosi` must be **v25 or newer** (v26 is what the tree is tested against); on
  older distros install it from PyPI (`pipx install mkosi`) or backports.
- The SDK's `check-package.sh` reports any remaining missing tool on first run
  with the exact `apt-get install` line to fix it.

### Sync

```bash
mkdir -p ~/pamir-rk3576 && cd ~/pamir-rk3576
repo init -u https://github.com/pamir-ai-pkgs/manifest -b main -m rk3576-debian-ab.xml
repo sync -j$(nproc) --verify
```

`--verify` is **required, not optional**: the `linux-rockchip-bsp-tools`
post-sync hook only runs non-interactively under `--verify`, and that hook
fetches the AArch64 cross-toolchain and the mkosi deb package pool from their
GitHub Releases. Without it `repo sync` still exits 0, but the toolchain and
`mkosi/packages/` are absent and the build fails. If you already synced without
it, fetch by hand:

```bash
bsp-tools/fetch-prebuilts.sh      # cross-toolchain
mkosi/scripts/fetch-packages.sh   # mkosi deb pool (always latest)
```

### Build `update.img`

```bash
./build.sh rockchip_rk3576_lapis_defconfig   # select the Lapis config
./build.sh                                    # U-Boot + kernel + rootfs + firmware
```

The flashable image lands at `rockdev/update.img`, with the per-partition images
(`MiniLoaderAll.bin`, `uboot.img`, `boot.img`, `rootfs.img`, `userdata.img`, …)
alongside it. To repack just the image after a component rebuild:

```bash
./build.sh updateimg
```

### Flash

Put the board into **Maskrom** (or **Loader**/rockusb) mode, connect USB-C, then
flash the full image:

```bash
./rkflash.sh update
```

or with Rockchip's `upgrade_tool` directly:

```bash
upgrade_tool uf rockdev/update.img
```

A full `update.img` flash provisions both A/B slots and the userdata partition.
Run `./rkflash.sh` with no arguments for the partition list and targeted
single-partition reflashes.

### What you get

- **Seamless A/B** — two rootfs slots (`system_a` / `system_b`) selected by
  U-Boot via the Android-style `slot_suffix`; OTA writes the inactive slot and
  flips on a healthy boot.
- **Runtime OTA** — `lapis-ota` drives `updateEngine` against the A/B slots;
  mark-success is automatic on a good boot.
- **Persistence** — `/var`, `/home`, `/root`, NetworkManager state, SSH host
  keys, machine-id, and the Nix store live in the `userdata` partition and
  survive slot swaps; the rootfs slots stay reproducible.
- **Nix-primary userland** — the Nix store sits in `userdata` (outside the A/B
  pair), bind-mounted at `/nix`; user software is managed through Nix.

### Updating the package pool

The mkosi deb pool is a Release asset on `linux-rockchip-mkosi`, always fetched
at its latest release — no tag or checksum is pinned anywhere. To ship a new
pool, rebuild the tarball plus its `.sha256` and upload both to a new Release
under the same asset names; the next `repo sync --verify` picks it up.

## Quick Start

### Initial Setup

```bash
mkdir -p ~/pamir-ai
cd ~/pamir-ai
repo init -u https://github.com/pamir-ai-pkgs/manifest -b main
repo sync -j$(nproc)
```

### Sync to Remote Server

On your remote server:

```bash
ssh user@remote-server
mkdir -p ~/pamir-ai
cd ~/pamir-ai
repo init -u https://github.com/pamir-ai/manifest -b main
repo sync -j$(nproc)
```

## Common Operations

### Update All Projects

```bash
cd ~/pamir-ai
repo sync -j$(nproc)
```

### Check Status of All Projects

```bash
repo status
```

### Execute Command Across All Projects

Check git status in all repos:
```bash
repo forall -c 'git status'
```

Pull latest changes:
```bash
repo forall -c 'git pull'
```

Check current branch:
```bash
repo forall -c 'echo "$REPO_PROJECT: $(git rev-parse --abbrev-ref HEAD)"'
```

### Update to Specific Branch

```bash
repo forall -c 'git checkout main'
```

### Show Project List

```bash
repo list
```

## Advanced Usage

### Create Local Manifest

You can create a local manifest for custom configurations:

```bash
mkdir -p .repo/local_manifests
cat > .repo/local_manifests/custom.xml << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<manifest>
  <remove-project name="distiller-cc" />
  <project name="distiller-cc"
           path="distiller-cc"
           revision="main" />
</manifest>
EOF
repo sync
```

### Working with Forks

To use your own forks:

```bash
cat > .repo/local_manifests/forks.xml << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<manifest>
  <remote name="myfork" fetch="https://github.com/YOUR-USERNAME/" />
  <remove-project name="distiller-cc" />
  <project name="distiller-cc"
           path="distiller-cc"
           remote="myfork"
           revision="my-feature-branch" />
</manifest>
EOF
repo sync
```

## Troubleshooting

### Sync Failures

Clean and retry:
```bash
repo sync -j1 --force-sync
```

Or manually fix specific project:
```bash
cd problematic-project
git fetch --all
git reset --hard origin/branch-name
```

### Detached HEAD State

```bash
repo forall -c 'git checkout $(git rev-parse --abbrev-ref HEAD)'
```

### Authentication Issues

Ensure SSH keys are configured or use HTTPS with credentials:

```bash
git config --global credential.helper cache
```

## Documentation

- [Google Repo Tool Documentation](https://gerrit.googlesource.com/git-repo/+/refs/heads/main/docs/)
- [Pamir AI Main Documentation](https://github.com/pamir-ai-pkgs)

## Support

For issues with:
- **Manifest repository**: Create issue at https://github.com/pamir-ai-pkgs/manifest/issues
- **Individual projects**: Create issue in respective project repository

## License

See individual project repositories for license information.
