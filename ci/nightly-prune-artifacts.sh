#!/usr/bin/env bash
#
# Drop the build artifacts and OTA registration of expired nightlies. The
# stager's prune step decides which nightly tags expired (their nightly/DATE
# branches are past the retention window and they are not the newest
# nightly); this removes what the build published for each of them:
#
#   s3://$S3_BUCKET/pamir-rk3576/nightly/<tag>/       dev-leg release dirs
#   s3://$S3_BUCKET/pamir-rk3576/nightly/<tag>-sec/   prod-leg release dirs
#   lapis/dev OTA release <X.Y.Z-nightly.N>           bundle + uboot, purged
#   GitHub prerelease <tag>                           the tag itself stays
#
# Every nightly is independent, so a failure on one is reported and the rest
# are still pruned; the exit status reflects any failure.
#
# Usage: nightly-prune-artifacts.sh <rk3576-vX.Y.Z-nightly.N>...
# Env:   S3_BUCKET, OTA_PUBLISH_REGION, OTA_PUBLISH_ROLE_ARN (optional, as in
#        ota-publish-release.sh), GH_TOKEN, GITHUB_RELEASE_REPOSITORY

set -euo pipefail

bucket="${S3_BUCKET:?S3_BUCKET is required}"
region="${OTA_PUBLISH_REGION:?OTA_PUBLISH_REGION is required}"
release_repo="${GITHUB_RELEASE_REPOSITORY:-${GITHUB_REPOSITORY:-pamir-ai-pkgs/manifest}}"
nightly_tag_regex='^rk3576-v([0-9]+\.[0-9]+\.[0-9]+-nightly\.[0-9]+)$'

if [[ $# -eq 0 ]]; then
	echo "nothing to prune"
	exit 0
fi

for tool in aws gh ota-publish; do
	if ! command -v "$tool" >/dev/null 2>&1; then
		echo "::error::$tool is not installed on the runner" >&2
		exit 1
	fi
done

if [[ -n "${OTA_PUBLISH_ROLE_ARN:-}" ]]; then
	# Same cross-account hop as ota-publish-release.sh; the S3 artifact
	# bucket is reachable from the runner's own role, so take the hop only
	# for the ota-publish calls.
	unset AWS_PROFILE
	if ! creds="$(aws sts assume-role \
		--role-arn "$OTA_PUBLISH_ROLE_ARN" \
		--role-session-name "ota-prune-${GITHUB_RUN_ID:-local}" \
		--query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' \
		--output text)"; then
		echo "::error::cannot assume OTA publish role $OTA_PUBLISH_ROLE_ARN" >&2
		exit 1
	fi
	read -r ota_key ota_secret ota_token <<<"$creds"
	ota_env=(AWS_ACCESS_KEY_ID="$ota_key" AWS_SECRET_ACCESS_KEY="$ota_secret"
		AWS_SESSION_TOKEN="$ota_token")
else
	ota_env=()
fi

failures=0
for tag in "$@"; do
	if [[ ! "$tag" =~ $nightly_tag_regex ]]; then
		echo "::error::not a nightly tag, refusing to prune: $tag" >&2
		failures=$((failures + 1))
		continue
	fi
	version="${BASH_REMATCH[1]}"
	for prefix in "pamir-rk3576/nightly/${tag}/" "pamir-rk3576/nightly/${tag}-sec/"; do
		if ! aws s3 rm "s3://${bucket}/${prefix}" --recursive --only-show-errors; then
			echo "::warning::failed to remove s3://${bucket}/${prefix}" >&2
			failures=$((failures + 1))
		fi
	done
	# Nightlies are registered only on the dev channel. A registration that
	# is already gone is not a failure.
	if ! env "${ota_env[@]}" ota-publish delete \
		--board lapis --channel dev --version "$version" \
		--purge --region "$region"; then
		echo "::warning::ota-publish delete failed for lapis/dev $version" >&2
		failures=$((failures + 1))
	fi
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
