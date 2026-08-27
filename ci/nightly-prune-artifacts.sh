#!/usr/bin/env bash
#
# Drop the build artifacts of expired nightlies. The stager's prune step
# decides which nightly tags expired (their nightly/DATE branches are past the
# retention window and they are not the newest nightly); this removes what the
# build published for each of them:
#
#   s3://$S3_BUCKET/pamir-rk3576/nightly/<tag>/       dev-leg release dirs
#   s3://$S3_BUCKET/pamir-rk3576/nightly/<tag>-sec/   prod-leg release dirs
#   GitHub prerelease <tag>                           the tag itself stays
#
# Every nightly is independent, so a failure on one is reported and the rest
# are still pruned; the exit status reflects any failure.
#
# Usage: nightly-prune-artifacts.sh <rk3576-vX.Y.Z-nightly.N>...
# Env:   S3_BUCKET, GH_TOKEN, GITHUB_RELEASE_REPOSITORY

set -euo pipefail

bucket="${S3_BUCKET:?S3_BUCKET is required}"
release_repo="${GITHUB_RELEASE_REPOSITORY:-${GITHUB_REPOSITORY:-pamir-ai-pkgs/manifest}}"
nightly_tag_regex='^rk3576-v([0-9]+\.[0-9]+\.[0-9]+-nightly\.[0-9]+)$'

if [[ $# -eq 0 ]]; then
	echo "nothing to prune"
	exit 0
fi

for tool in aws gh; do
	if ! command -v "$tool" >/dev/null 2>&1; then
		echo "::error::$tool is not installed on the runner" >&2
		exit 1
	fi
done

failures=0
for tag in "$@"; do
	if [[ ! "$tag" =~ $nightly_tag_regex ]]; then
		echo "::error::not a nightly tag, refusing to prune: $tag" >&2
		failures=$((failures + 1))
		continue
	fi
	for prefix in "pamir-rk3576/nightly/${tag}/" "pamir-rk3576/nightly/${tag}-sec/"; do
		if ! aws s3 rm "s3://${bucket}/${prefix}" --recursive --only-show-errors; then
			echo "::warning::failed to remove s3://${bucket}/${prefix}" >&2
			failures=$((failures + 1))
		fi
	done
	# The GitHub prerelease is the human-facing download page; it goes with
	# the artifacts it links to. No --cleanup-tag: the tag is the counter.
	if gh release view "$tag" --repo "$release_repo" --json tagName >/dev/null 2>&1; then
		if ! gh release delete "$tag" --repo "$release_repo" --yes; then
			echo "::warning::failed to delete GitHub release $tag" >&2
			failures=$((failures + 1))
		fi
	fi
	echo "pruned $tag"
done

[[ "$failures" -eq 0 ]]
