#!/usr/bin/env bash
#
# Initialize a BSP workspace and sync only bsp-tools into it, so the release
# driver (bsp-tools/ci/build-and-upload-release.sh) can take over. Both the
# dev and secure release legs bootstrap their workspaces through this script.
#
# It lives in the manifest repo rather than bsp-tools because it runs before
# any bsp-tools checkout exists in the workspace; the workflow invokes it from
# its own manifest checkout.
#
# Usage: bootstrap-workspace.sh <workspace-dir>
# Env:   MANIFEST_URL, MANIFEST_FILE, MANIFEST_REF, REPO_MIRROR

set -euo pipefail

workspace="${1:?usage: bootstrap-workspace.sh <workspace-dir>}"
manifest_url="${MANIFEST_URL:?MANIFEST_URL is required}"
manifest_file="${MANIFEST_FILE:?MANIFEST_FILE is required}"
manifest_ref="${MANIFEST_REF:?MANIFEST_REF is required}"
repo_mirror="${REPO_MIRROR:?REPO_MIRROR is required}"

mkdir -p "$workspace"
cd "$workspace"

repo_init_args=()
if [[ -d "$repo_mirror/.repo" && "$manifest_ref" != refs/tags/* ]]; then
	repo_init_args+=(--reference="$repo_mirror")
fi
if [[ "$manifest_ref" == refs/tags/* && -d .repo/manifests ]]; then
	git -C .repo/manifests fetch --force "$manifest_url" \
		"$manifest_ref:$manifest_ref"
fi
repo init -u "$manifest_url" -b "$manifest_ref" -m "$manifest_file" \
	"${repo_init_args[@]}"

# For tag builds, repo resolves component refs from the project checkout's
# local refs; refresh the bsp-tools component tag so a stale or moved tag
# cached in the persistent workspace cannot win over the remote.
if [[ "$manifest_ref" == refs/tags/* ]] &&
	git -C bsp-tools rev-parse --git-dir >/dev/null 2>&1; then
	# The component pin lives in the manifest XML, not in the manifest tag
	# name: rc candidates are tagged rk3576-vX.Y.Z-rc.N while components
	# stay at vX.Y.Z, so deriving the ref from the tag string breaks on
	# every rc build. Read the bsp-tools revision from the pinned manifest.
	component_tag_ref="$(python3 - ".repo/manifests/$manifest_file" <<-'PY'
		import sys
		import xml.etree.ElementTree as ET

		root = ET.parse(sys.argv[1]).getroot()
		for project in root.iter("project"):
		    if project.get("path") == "bsp-tools":
		        print(project.get("revision", ""))
		        break
		PY
	)"
	if [[ "$component_tag_ref" != refs/tags/* ]]; then
		echo "bsp-tools revision in $manifest_file is not a tag ref: '$component_tag_ref'" >&2
		exit 1
	fi
	mapfile -t bsp_tools_remotes < <(git -C bsp-tools remote)
	bsp_tools_remote="${bsp_tools_remotes[0]:-}"
	if [[ -z "$bsp_tools_remote" ]]; then
		echo "bsp-tools checkout has no git remote" >&2
		exit 1
	fi
	git -C bsp-tools fetch --force "$bsp_tools_remote" \
		"$component_tag_ref:$component_tag_ref"
	component_tag_commit="$(git -C bsp-tools rev-parse "${component_tag_ref}^{}")"
	git -C bsp-tools update-ref "$component_tag_ref" "$component_tag_commit"
fi
if git -C bsp-tools rev-parse --git-dir >/dev/null 2>&1; then
	git -C bsp-tools reset --hard
	git -C bsp-tools clean -fdx
fi
repo sync -j"$(nproc)" --current-branch --optimized-fetch \
	--prune --force-remove-dirty --fail-fast --verify bsp-tools
uv --project bsp-tools sync --python /usr/bin/python3 --group dev --locked
