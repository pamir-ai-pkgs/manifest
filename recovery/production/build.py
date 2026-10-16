"""Run the frozen release driver with isolated orchestration-only repairs."""
import difflib
import hashlib
import json
import os
from pathlib import Path
import subprocess

sdk = Path(os.environ['SDK_DIR'])
base = Path(__file__).resolve().parent
work = Path(os.environ['RUNNER_TEMP']) / ('v020-prod-' + os.environ['GITHUB_RUN_ID'])
work.mkdir(exist_ok=False)
original = sdk / 'bsp-tools/ci/build-and-upload-release.sh'
pin = subprocess.check_output(['git', '-C', str(sdk / 'bsp-tools'), 'rev-parse', 'HEAD'], text=True).strip()
assert pin == '1c8b65545e867eecfb3da317a673e37c8198ff53'
source = original.read_text()
frozen = subprocess.check_output(['git', '-C', str(sdk / 'bsp-tools'), 'show', pin + ':ci/build-and-upload-release.sh'], text=True)
assert source == frozen

def replace_once(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new, 1)

driver = replace_once(source,
    'bsp_tools_dir="$(cd "${script_dir}/.." && pwd)" || \\\n\tfail "failed to resolve bsp-tools directory"',
    'bsp_tools_dir="${SDK_DIR:?}/bsp-tools"')
driver = replace_once(driver,
    'run uv --project "$bsp_tools_dir" run --locked pamir-bsp-collect-release-metadata \\\n\t"${metadata_args[@]}"',
    'run uv --project "$bsp_tools_dir" run --locked python "${PRODUCTION_ORCHESTRATION:?}/metadata.py" \\\n\t"${metadata_args[@]}"')
# A unique run prefix is empty before writing. Never delete prior artifacts.
driver = replace_once(driver, '\ts3_sync_args+=(--delete)', '\t: # isolated run prefix: no delete')
# Build/download artifacts only: do not advance mutable release pointers.
boundary = '\nlatest_json="$(mktemp)"'
assert driver.count(boundary) == 1
driver = driver.split(boundary)[0] + '\nprintf "Production artifacts uploaded: %s\\n" "$s3_prefix"\n'
prepared = work / 'build-and-upload-production.sh'
prepared.write_text(driver)
(work / 'driver.diff').write_text(''.join(difflib.unified_diff(source.splitlines(True), driver.splitlines(True), fromfile=str(original), tofile=str(prepared))))
(work / 'orchestration.json').write_text(json.dumps({
    'tag': 'rk3576-v0.2.0', 'manifest': 'd823b38bd2b21b26fc35715ea644dce3c02491da',
    'bsp_tools': pin, 'original_driver_sha256': hashlib.sha256(source.encode()).hexdigest(),
    'executed_driver_sha256': hashlib.sha256(driver.encode()).hexdigest(),
    'workflow_revision': os.environ['GITHUB_SHA'], 'source_edits': False,
    'dev_rebuilt': False, 'ota_publication': False, 'pointer_updates': False,
    'sdk': str(sdk), 's3_prefix': os.environ['S3_PREFIX'],
}, indent=2) + '\n')
prefix = os.environ['S3_PREFIX']
bucket, key = prefix.removeprefix('s3://').split('/', 1)
listing = json.loads(subprocess.check_output(['aws', 's3api', 'list-objects-v2', '--bucket', bucket, '--prefix', key + '/', '--max-keys', '1', '--output', 'json']))
assert not listing.get('Contents'), 'Run prefix already contains artifacts; inspect rather than overwrite'
subprocess.run(['bash', '-n', str(prepared)], check=True)
subprocess.run(['bash', str(prepared)], cwd=sdk, check=True)
