# Pamir AI Manifest Repository

This repository contains the manifest file for managing all Pamir AI Distiller projects using Google's `repo` tool.

## Prerequisites

### Install Google Repo Tool

```bash
# Create bin directory if it doesn't exist
mkdir -p ~/.local/bin

# Download repo tool
curl https://storage.googleapis.com/git-repo-downloads/repo > ~/.local/bin/repo
chmod a+x ~/.local/bin/repo

# Add to PATH (add to ~/.bashrc for persistence)
export PATH="$HOME/.local/bin:$PATH"
```

### Verify Installation

```bash
repo version
```

## Quick Start

### Initial Setup

```bash
# Create workspace directory
mkdir -p ~/pamir-ai
cd ~/pamir-ai

# Initialize repo with manifest
repo init -u https://github.com/pamir-ai-pkgs/manifest -b main

# Sync all projects
repo sync -j$(nproc)
```

### Sync to Remote Server

On your remote server:

```bash
# SSH into remote server
ssh user@remote-server

# Create workspace
mkdir -p ~/pamir-ai
cd ~/pamir-ai

# Initialize and sync
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

```bash
# Example: Check git status in all repos
repo forall -c 'git status'

# Example: Pull latest changes
repo forall -c 'git pull'

# Example: Check current branch
repo forall -c 'echo "$REPO_PROJECT: $(git rev-parse --abbrev-ref HEAD)"'
```

### Update to Specific Branch

```bash
# Check out specific branch in all projects
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
  <!-- Override specific project branches -->
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

```bash
# Clean and retry
repo sync -j1 --force-sync

# Or manually fix specific project
cd problematic-project
git fetch --all
git reset --hard origin/branch-name
```

### Detached HEAD State

```bash
# Check out proper branches
repo forall -c 'git checkout $(git rev-parse --abbrev-ref HEAD)'
```

### Authentication Issues

Ensure SSH keys are configured or use HTTPS with credentials:

```bash
# Configure Git credentials
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
