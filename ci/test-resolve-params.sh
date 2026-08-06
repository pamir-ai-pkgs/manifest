#!/usr/bin/env bash

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workflow="$repo_root/.github/workflows/rk3576-bsp-release.yml"
release_driver="${RELEASE_DRIVER:-}"

failures=0
checks=0

fail() {
	printf 'FAIL: %s\n' "$1" >&2
	failures=$((failures + 1))
}

ok() {
	checks=$((checks + 1))
}

extract_step() {
	python3 - "$workflow" "$1" <<'PY'
import sys, yaml
workflow, step_name = sys.argv[1], sys.argv[2]
with open(workflow) as fh:
	doc = yaml.safe_load(fh)
for step in doc["jobs"]["build"]["steps"]:
	if step.get("name") == step_name:
		sys.stdout.write(step["run"])
		break
else:
	sys.exit(f"step not found: {step_name}")
PY
}

dev_prefix() {
	local channel="$1" version="$2" build_id="$3" manifest_ref="$4" bucket="$5"
	local safe_ref="${manifest_ref//\//-}"
	case "$channel" in
	stable) printf 's3://%s/pamir-rk3576/releases/%s\n' "$bucket" "$version" ;;
	candidate) printf 's3://%s/pamir-rk3576/candidates/%s/%s\n' "$bucket" "$version" "$build_id" ;;
	scratch) printf 's3://%s/pamir-rk3576/scratch/%s/%s\n' "$bucket" "$safe_ref" "$build_id" ;;
	*) printf 's3://%s/pamir-rk3576/dev/%s/%s\n' "$bucket" "$safe_ref" "$build_id" ;;
	esac
}

resolve() {
	local outfile
	outfile="$(mktemp)"
	(
		set -e
		export GITHUB_ENV="$outfile"
		export S3_BUCKET="${S3_BUCKET:-distiller-os-release-artifacts}"
		export WORK_ROOT="${WORK_ROOT:-/srv/bsp/workspaces}"
		export GITHUB_RUN_ID="${GITHUB_RUN_ID:-12345}"
		export GITHUB_RUN_ATTEMPT="${GITHUB_RUN_ATTEMPT:-1}"
		eval "$RESOLVE_BODY"
	) >/dev/null 2>&1
	local rc=$?
	cat "$outfile"
	rm -f "$outfile"
	return $rc
}

scenario() {
	local desc="$1" expect_channel="$2" expect_secure="$3"
	local env_out rc
	env_out="$(resolve)"
	rc=$?

	if [[ "$expect_channel" == "REJECT" ]]; then
		if ((rc == 0)); then
			fail "$desc: expected the step to reject this ref, it succeeded"
		else
			ok
		fi
		return
	fi

	if ((rc != 0)); then
		fail "$desc: resolve step exited $rc"
		return
	fi

	local channel version build_id manifest_ref sec_prefix sdk_dir sec_sdk_dir rauc
	channel="$(printf '%s\n' "$env_out" | awk -F= '/^CHANNEL=/{print substr($0,9)}')"
	version="$(printf '%s\n' "$env_out" | awk -F= '/^VERSION=/{print substr($0,9)}')"
	build_id="$(printf '%s\n' "$env_out" | awk '/^BUILD_ID=/{print substr($0,10)}')"
	manifest_ref="$(printf '%s\n' "$env_out" | awk '/^MANIFEST_REF=/{print substr($0,14)}')"
	sec_prefix="$(printf '%s\n' "$env_out" | awk '/^SEC_S3_PREFIX=/{print substr($0,15)}')"
	sdk_dir="$(printf '%s\n' "$env_out" | awk '/^SDK_DIR=/{print substr($0,9)}')"
	sec_sdk_dir="$(printf '%s\n' "$env_out" | awk '/^SEC_SDK_DIR=/{print substr($0,13)}')"
	rauc="$(printf '%s\n' "$env_out" | awk '/^RAUC_BUNDLE=/{print substr($0,13)}')"

	[[ "$channel" == "$expect_channel" ]] ||
		fail "$desc: channel is '$channel', expected '$expect_channel'"
	[[ "$channel" == "$expect_channel" ]] && ok

	local devp
	devp="$(dev_prefix "$channel" "$version" "$build_id" "$manifest_ref" \
		"${S3_BUCKET:-distiller-os-release-artifacts}")"

	if [[ "$expect_secure" == "yes" ]]; then
		if [[ -z "$sec_prefix" ]]; then
			fail "$desc: secure leg runs on this channel but SEC_S3_PREFIX is empty, so S3_PREFIX falls through to the dev leg's default and the secure build overwrites the dev objects"
		else
			ok
		fi
		if [[ "$sec_prefix" == "$devp" ]]; then
			fail "$desc: secure and dev legs share the S3 prefix $devp"
		else
			ok
		fi
		if [[ "$sdk_dir" == "$sec_sdk_dir" ]]; then
			fail "$desc: secure and dev legs share the workspace $sdk_dir"
		else
			ok
		fi
	else
		if [[ -n "$sec_prefix" ]]; then
			fail "$desc: secure leg does not run here but SEC_S3_PREFIX is set to $sec_prefix"
		else
			ok
		fi
	fi

	if [[ "$rauc" == "1" && "$channel" != "stable" && "$channel" != "candidate" ]]; then
		fail "$desc: RAUC_BUNDLE=1 on channel '$channel', but only tagged builds bake a canonical IMAGE_VERSION"
	else
		ok
	fi

	printf '  %-52s channel=%-9s dev=%s\n' "$desc" "$channel" "$devp"
	[[ -n "$sec_prefix" ]] && printf '  %-52s %-17s sec=%s\n' "" "" "$sec_prefix"
	return 0
}

RESOLVE_BODY="$(extract_step 'Resolve build parameters')" ||
	{ printf 'could not extract the resolve step\n' >&2; exit 1; }

printf 'Resolve-build-parameters matrix\n\n'

GITHUB_EVENT_NAME=push GITHUB_REF_TYPE=tag GITHUB_REF_NAME=rk3576-v0.0.8 \
	DISPATCH_CHANNEL= DISPATCH_MANIFEST_REF= DISPATCH_VERSION= \
	scenario 'push stable tag' stable yes

GITHUB_EVENT_NAME=push GITHUB_REF_TYPE=tag GITHUB_REF_NAME=rk3576-v0.0.8-rc.1 \
	DISPATCH_CHANNEL= DISPATCH_MANIFEST_REF= DISPATCH_VERSION= \
	scenario 'push candidate tag' candidate yes

GITHUB_EVENT_NAME=push GITHUB_REF_TYPE=tag GITHUB_REF_NAME=rk3576-vBOGUS \
	DISPATCH_CHANNEL= DISPATCH_MANIFEST_REF= DISPATCH_VERSION= \
	scenario 'push malformed release tag' REJECT no

GITHUB_EVENT_NAME=workflow_dispatch GITHUB_REF_TYPE=branch GITHUB_REF_NAME=main \
	DISPATCH_CHANNEL=stable DISPATCH_MANIFEST_REF=refs/tags/rk3576-v0.0.8 \
	DISPATCH_VERSION=rk3576-v0.0.8 \
	scenario 'dispatch stable' stable yes

GITHUB_EVENT_NAME=workflow_dispatch GITHUB_REF_TYPE=branch GITHUB_REF_NAME=main \
	DISPATCH_CHANNEL=candidate DISPATCH_MANIFEST_REF=refs/tags/rk3576-v0.0.8-rc.1 \
	DISPATCH_VERSION=rk3576-v0.0.8-rc.1 \
	scenario 'dispatch candidate' candidate yes

GITHUB_EVENT_NAME=workflow_dispatch GITHUB_REF_TYPE=branch GITHUB_REF_NAME=main \
	DISPATCH_CHANNEL=dev DISPATCH_MANIFEST_REF=main DISPATCH_VERSION= \
	scenario 'dispatch dev' dev no

GITHUB_EVENT_NAME=workflow_dispatch GITHUB_REF_TYPE=branch GITHUB_REF_NAME=main \
	DISPATCH_CHANNEL=scratch DISPATCH_MANIFEST_REF=main DISPATCH_VERSION= \
	scenario 'dispatch scratch' scratch no

GITHUB_EVENT_NAME=workflow_dispatch GITHUB_REF_TYPE=branch GITHUB_REF_NAME=main \
	DISPATCH_CHANNEL=stable DISPATCH_MANIFEST_REF=main DISPATCH_VERSION=rk3576-v0.0.8 \
	scenario 'dispatch stable with mismatched ref' REJECT no

GITHUB_EVENT_NAME=workflow_dispatch GITHUB_REF_TYPE=branch GITHUB_REF_NAME=main \
	DISPATCH_CHANNEL=stable DISPATCH_MANIFEST_REF=refs/tags/rk3576-v0.0.8-rc.1 \
	DISPATCH_VERSION=rk3576-v0.0.8-rc.1 \
	scenario 'dispatch stable given an rc version' REJECT no

GITHUB_EVENT_NAME=workflow_dispatch GITHUB_REF_TYPE=branch GITHUB_REF_NAME=main \
	DISPATCH_CHANNEL=candidate DISPATCH_MANIFEST_REF=refs/tags/rk3576-v0.0.8 \
	DISPATCH_VERSION=rk3576-v0.0.8 \
	scenario 'dispatch candidate given a stable version' REJECT no

if [[ -n "$release_driver" ]]; then
	printf '\nCross-checking the dev prefix expectations against %s\n' "$release_driver"
	for want in \
		'pamir-rk3576/releases/${version}' \
		'pamir-rk3576/candidates/${version}/${build_id}' \
		'pamir-rk3576/scratch/${safe_ref}/${build_id}' \
		'pamir-rk3576/dev/${safe_ref}/${build_id}'; do
		if grep -qF "$want" "$release_driver"; then
			ok
		else
			fail "release driver no longer contains the prefix form $want; this test's dev_prefix() has drifted"
		fi
	done
fi

printf '\n%d checks, %d failures\n' "$checks" "$failures"
[[ "$failures" -eq 0 ]]
