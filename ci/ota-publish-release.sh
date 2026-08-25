#!/usr/bin/env bash
#
# Register a release leg's RAUC bundle with the lapis-ota server via
# bsp-tools/ota-publish. Both release legs share this: the dev leg publishes
# lapis-dev-<ver>.raucb to the dev channel dev images poll, the secure leg
# lapis-sec-<ver>.raucb to the sit channel secure images poll.
# The leg's uboot.img rides along as the release's standalone U-Boot image.
#
# Only run this script for final release tags (rk3576-vX.Y.Z) and, on the
# dev channel only, nightly tags (rk3576-vX.Y.Z-nightly.N); candidates keep
# their rc name and stay on the artifact bucket. The release is live for
# devices the moment the DynamoDB item lands.
#
# Usage: ota-publish-release.sh <sdk-dir> <channel>
# Env:   VERSION              release tag, rk3576-vX.Y.Z
#        OTA_PUBLISH_REGION   region of the OTA control plane
#        OTA_PUBLISH_ROLE_ARN optional cross-account publish role; when unset
#                             the runner's own identity must carry the
#                             server's publish policy

set -euo pipefail

sdk_dir="${1:?usage: ota-publish-release.sh <sdk-dir> <channel>}"
channel="${2:?usage: ota-publish-release.sh <sdk-dir> <channel>}"
release_tag="${VERSION:?VERSION is required}"
region="${OTA_PUBLISH_REGION:?OTA_PUBLISH_REGION is required}"

# ota-publish (bsp-tools/ota-publish) is installed on the runner; it is not
# built per run.
for tool in ota-publish rauc ${OTA_PUBLISH_ROLE_ARN:+aws}; do
	if ! command -v "$tool" >/dev/null 2>&1; then
		echo "::error::$tool is not installed on the runner" >&2
		exit 1
	fi
done

# rk3576-v0.1.0 -> 0.1.0: the server keys releases on a bare
# MAJOR.MINOR.PATCH; devices normalize their v-prefixed IMAGE_VERSION to the
# same form on the wire. Nightlies keep their prerelease suffix
# (0.2.0-nightly.3) and are only ever registered on the dev channel: sit and
# prod receive on-demand final releases, never a nightly.
version="${release_tag#rk3576-v}"
release_regex='^[0-9]+\.[0-9]+\.[0-9]+$'
nightly_regex='^[0-9]+\.[0-9]+\.[0-9]+-nightly\.[0-9]+$'
if [[ "$version" =~ $release_regex ]]; then
	:
elif [[ "$version" =~ $nightly_regex ]]; then
	if [[ "$channel" != "dev" ]]; then
		echo "::error::refusing to publish nightly $release_tag to channel $channel;" \
			"nightlies go to dev only" >&2
		exit 1
	fi
else
	echo "::error::refusing to publish non-release version $release_tag" >&2
	exit 1
fi

shopt -s nullglob
bundles=("${sdk_dir%/}"/output/ota/*.raucb)
if [[ ${#bundles[@]} -ne 1 ]]; then
	echo "::error::expected exactly one RAUC bundle under $sdk_dir/output/ota," \
		"found ${#bundles[@]}: ${bundles[*]:-none}" >&2
	exit 1
fi
bundle="${bundles[0]}"

uboot="${sdk_dir%/}/output/firmware/uboot.img"
if [[ ! -f "$uboot" ]]; then
	echo "::error::U-Boot image missing: $uboot" >&2
	exit 1
fi

# The bundle version must be the release being registered, or devices
# would install one version and report another.
if ! info="$(rauc info --no-verify --output-format=shell "$bundle" 2>&1)"; then
	echo "::error::rauc info failed for $bundle: $info" >&2
	exit 1
fi
bundle_version="$(sed -nE "s/^RAUC_MF_VERSION='?(v?[0-9A-Za-z.-]+)'?$/\\1/p" <<<"$info")"
if [[ "${bundle_version#v}" != "$version" ]]; then
	echo "::error::bundle $bundle carries version '${bundle_version:-unknown}'," \
		"not release $version" >&2
	exit 1
fi

if [[ -n "${OTA_PUBLISH_ROLE_ARN:-}" ]]; then
	# Cross-account hop from runner's instance role; nothing persists.
	unset AWS_PROFILE
	if ! creds="$(aws sts assume-role \
		--role-arn "$OTA_PUBLISH_ROLE_ARN" \
		--role-session-name "ota-publish-${GITHUB_RUN_ID:-local}" \
		--query 'Credentials.[AccessKeyId,SecretAccessKey,SessionToken]' \
		--output text)"; then
		echo "::error::cannot assume OTA publish role $OTA_PUBLISH_ROLE_ARN" >&2
		exit 1
	fi
	read -r AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN <<<"$creds"
	export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
fi

ota-publish publish \
	--bundle "$bundle" \
	--uboot "$uboot" \
	--board lapis \
	--channel "$channel" \
	--version "$version" \
	--region "$region"
