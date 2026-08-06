#!/usr/bin/env bash

cd "$(dirname "$0")" || exit 1

repo_root="$(cd ../../.. && pwd)"
mkdir -p wf
python3 make-act-workflow.py \
	"$repo_root/.github/workflows/rk3576-bsp-release.yml" \
	wf/act-new.yml || exit 1

if [[ ! -f secrets.env ]]; then
	{
		printf 'PAMIR_GITHUB_TOKEN=%s\n' "$(gh auth token 2>/dev/null)"
		for s in PAMIR_ARTIFACT_DOWNLOAD_TOKEN PAMIR_AVB_VBMETA_KEY_B64 \
			PAMIR_BOOT_KEYS_TGZ_B64 PAMIR_LAPIS_USER_PASSWORD \
			PAMIR_LAPIS_CF_ACCESS_SIT; do
			printf '%s=act-placeholder\n' "$s"
		done
	} >secrets.env
	chmod 600 secrets.env
fi
if [[ ! -f vars.env ]]; then
	printf 'PAMIR_OTP_PUBKEY_SHA256=act-placeholder\n' >vars.env
fi

failures=0
checks=0

note() { printf '%s\n' "$1"; }
ok() { checks=$((checks + 1)); }
bad() { printf '  FAIL: %s\n' "$1"; failures=$((failures + 1)); }

run_act() {
	local name="$1"
	shift
	timeout 900 act "$@" -W wf/act-new.yml \
		-P ubuntu-latest=catthehacker/ubuntu:act-latest \
		--secret-file secrets.env --var-file vars.env \
		--pull=false >"out-$name.log" 2>&1
	printf '%s' "$?"
}

extract() {
	sed 's/\x1b\[[0-9;]*m//g' "out-$1.log" |
		grep -oE '(STEP_RAN|RESOLVED)::[^ ]*.*' | sed 's/[[:space:]]*$//'
}

val() {
	extract "$1" | awk -v k="RESOLVED::$2=" 'index($0,k)==1{print substr($0,length(k)+1); exit}'
}

ran() {
	extract "$1" | grep -qF "STEP_RAN::$2"
}

SECURE_STEPS=(
	"Bootstrap secure workspace BSP tools"
	"Stage signing keys into the secure workspace"
	"Build and upload secure release"
	"Attach secure artifacts to the GitHub release"
)

check_case() {
	local name="$1" rc="$2" want_channel="$3" want_secure="$4"
	note ""
	note "=== $name (act exit $rc) ==="

	if [[ "$want_channel" == "REJECT" ]]; then
		if [[ "$rc" == "0" ]]; then
			bad "$name: job succeeded, expected the resolve step to reject the ref"
		else
			ok
			note "  job failed as expected"
		fi
		return
	fi

	if [[ "$rc" != "0" ]]; then
		bad "$name: act exited $rc"
		return
	fi

	local channel sec_prefix sdk sec_sdk rauc
	channel="$(val "$name" CHANNEL)"
	sec_prefix="$(val "$name" SEC_S3_PREFIX)"
	sdk="$(val "$name" SDK_DIR)"
	sec_sdk="$(val "$name" SEC_SDK_DIR)"
	rauc="$(val "$name" RAUC_BUNDLE)"

	note "  channel=$channel rauc=$rauc"
	note "  dev workspace=$sdk"
	note "  sec workspace=$sec_sdk"
	note "  sec s3=${sec_prefix:-<empty>}"

	if [[ "$channel" == "$want_channel" ]]; then ok; else
		bad "$name: channel=$channel want=$want_channel"
	fi

	if [[ "$sdk" != "$sec_sdk" ]]; then ok; else
		bad "$name: both legs share workspace $sdk"
	fi

	local step
	for step in "${SECURE_STEPS[@]}"; do
		if ran "$name" "$step"; then
			if [[ "$want_secure" == "yes" ]]; then ok; else
				bad "$name: secure step ran but should not: $step"
			fi
		else
			if [[ "$want_secure" == "no" ]]; then ok; else
				bad "$name: secure step did not run: $step"
			fi
		fi
	done

	if [[ "$want_secure" == "yes" ]]; then
		if [[ -n "$sec_prefix" ]]; then ok; else
			bad "$name: secure leg runs but SEC_S3_PREFIX is empty; S3_PREFIX would fall through to the dev prefix and overwrite it"
		fi
		if [[ "$sec_prefix" == *-sec* ]]; then ok; else
			bad "$name: secure prefix lacks the -sec discriminator: $sec_prefix"
		fi
	else
		if [[ -z "$sec_prefix" ]]; then ok; else
			bad "$name: secure leg skipped but SEC_S3_PREFIX=$sec_prefix"
		fi
		if [[ "$rauc" == "1" ]]; then
			bad "$name: RAUC_BUNDLE=1 on untagged channel $channel"
		else
			ok
		fi
	fi
}

rc=$(run_act push-stable push --eventpath events/push-stable.json)
check_case push-stable "$rc" stable yes

rc=$(run_act push-rc push --eventpath events/push-rc.json)
check_case push-rc "$rc" candidate yes

rc=$(run_act push-bogus push --eventpath events/push-bogus-tag.json)
check_case push-bogus "$rc" REJECT no

rc=$(run_act dispatch-dev workflow_dispatch --input manifest_ref=main --input channel=dev --input version=)
check_case dispatch-dev "$rc" dev no

rc=$(run_act dispatch-scratch workflow_dispatch --input manifest_ref=main --input channel=scratch --input version=)
check_case dispatch-scratch "$rc" scratch no

rc=$(run_act dispatch-stable workflow_dispatch --input manifest_ref=refs/tags/rk3576-v0.0.8 --input channel=stable --input version=rk3576-v0.0.8)
check_case dispatch-stable "$rc" stable yes

rc=$(run_act dispatch-bad-stable workflow_dispatch --input manifest_ref=main --input channel=stable --input version=rk3576-v0.0.8)
check_case dispatch-bad-stable "$rc" REJECT no

note ""
note "$checks checks, $failures failures"
[[ "$failures" -eq 0 ]]
