"""Publish a release page and original dev downloads; never rebuild or retag."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

BASE=Path(__file__).resolve().parent
SDK=Path('/srv/bsp/workspaces/pamir-rk3576-dvt-stable')
ORIGINAL=SDK/'output/releases/ci/38851595535-1'
WORK=Path(os.environ['RUNNER_TEMP'])/('v020-release-'+os.environ['GITHUB_RUN_ID'])
WORK.mkdir(exist_ok=False)
RELEASE=WORK/'release'; RELEASE.mkdir()
TAG='rk3576-v0.2.0'
REPO='pamir-ai-pkgs/manifest'
PREFIX='pamir-rk3576/releases/rk3576-v0.2.0-dvt'
BUCKET='lapis-os-artifacts'

def output(*args):
    return subprocess.check_output(args,text=True)

def run(*args):
    subprocess.run(args,check=True)

os.environ.pop('AWS_PROFILE',None)
os.environ.update(AWS_REGION='us-west-2',AWS_DEFAULT_REGION='us-west-2',RELEASE_IMAGE_LABEL='Dev')
identity=json.loads(output('aws','sts','get-caller-identity','--output','json'))
assert identity['Account']=='322551983552' and identity['Arn'].startswith('arn:aws:sts::322551983552:assumed-role/lapis-ota-build/')
tag=json.loads(output('gh','api','repos/'+REPO+'/git/ref/tags/'+TAG))
assert tag['object']['sha']=='04008b8b8b181323bdc9da3792bd029c0c013a34'
expected=json.loads((BASE/'expected-components.json').read_text())
manifest=ET.fromstring((ORIGINAL/'manifest.xml').read_bytes())
assert {p.get('path'):p.get('revision') for p in manifest.findall('project')}==expected
info=json.loads((ORIGINAL/'build-info.v1.json').read_text())
assert info['build_id']=='38851595535-1' and info['version']==TAG and info['channel']=='stable'
# Retain original build provenance. New metadata is isolated from original files.
for path in ORIGINAL.rglob('*'):
    if not path.is_file() or path.name in {'artifact-inventory.v1.json','SHA256SUMS','github-release.v1.json','github-release.v1.md','github-release-rows.v1.md'}:
        continue
    dest=RELEASE/path.relative_to(ORIGINAL)
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.symlink_to(path.resolve())
(RELEASE/'CHANGELOG-v0.2.0.md').write_bytes((BASE/'CHANGELOG.md').read_bytes())
provenance={'original_run':38851595535,'original_attempt':1,'recovery_run':os.environ['GITHUB_RUN_ID'],'recovery_revision':os.environ['GITHUB_SHA'],'tag':TAG,'tag_unchanged':True,'rebuilt':False,'available':'DVT dev','production_built':False,'changelog':'Release-specific notes prepared from frozen integration inventory; tagged source changelog remains unchanged.'}
(RELEASE/'recovery-provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
sys.path.insert(0,str(SDK/'bsp-tools/src'))
from bsp_release_tools import collect_release_metadata as c,github_release as g
payload=g.write_github_release_files(release_dir=RELEASE,build_info=info,inventory=c.collect_artifact_inventory(RELEASE,'s3://'+BUCKET+'/'+PREFIX),aws_region='eu-north-1',github_repository=REPO,github_run_url='https://github.com/'+REPO+'/actions/runs/38851595535',download_base_url='https://d16xxnueu5edhn.cloudfront.net',download_token=os.environ['ARTIFACT_DOWNLOAD_TOKEN'],changelog_path=BASE/'CHANGELOG.md')
changelog_url='https://github.com/'+REPO+'/releases/download/'+TAG+'/CHANGELOG-v0.2.0.md'
payload['changelog_url']=changelog_url
g.write_json(RELEASE/g.GITHUB_RELEASE_DATA_NAME,payload)
body=(RELEASE/g.GITHUB_RELEASE_BODY_NAME).read_text()
body=body.replace('## Images\n','**DVT dev is available and the dev OTA is live. Production/secure artifacts are pending; that build leg did not run.**\n\n## Images\n',1)
body=body.replace('Full checksums are attached as the `SHA256SUMS` (dev) and `SHA256SUMS-secure` (secure) release assets.','Full development checksums are attached as `SHA256SUMS-dvt`. No production artifacts are attached.')
body=body.replace('_Source: release/CHANGELOG.md @ v0.2.0_',f'_Source: [release-specific changelog]({changelog_url}), compiled from the frozen integration inventory. The tagged canonical changelog remains unchanged._')
body='\n'.join(line for line in body.splitlines() if not line.startswith('- Channel pointer:'))+'\n'
(RELEASE/g.GITHUB_RELEASE_BODY_NAME).write_text(body)
c.write_sha256sums(RELEASE)
c.write_json(RELEASE/c.INVENTORY_NAME,c.collect_artifact_inventory(RELEASE,'s3://'+BUCKET+'/'+PREFIX))
# Never overwrite any prior release objects, including a partial upload.
listing=json.loads(output('aws','s3api','list-objects-v2','--bucket',BUCKET,'--prefix',PREFIX+'/','--max-keys','1','--output','json'))
assert not listing.get('Contents'), 'Release prefix already contains objects; inspect before retry'
run('aws','s3','sync',str(RELEASE)+'/', 's3://'+BUCKET+'/'+PREFIX+'/', '--only-show-errors','--sse','AES256')
verified=[]
for artifact in payload['primary_downloads']:
    relative=artifact['path']
    head=json.loads(output('aws','s3api','head-object','--bucket',BUCKET,'--key',PREFIX+'/'+relative,'--output','json'))
    assert head['ContentLength']==(RELEASE/relative).stat().st_size,relative
    verified.append({'path':relative,'bytes':head['ContentLength']})
# The standard publisher creates a draft, attaches metadata, then makes it public.
env=os.environ.copy();env.update(CHANNEL='stable',VERSION=TAG,BUILD_ID='38851595535-1',SDK_DIR=str(SDK),RELEASE_DIR=str(RELEASE),GITHUB_RELEASE_REPOSITORY=REPO,ASSET_NAME_SUFFIX='-dvt',RELEASE_NOTES_MODE='replace')
subprocess.run(['bash',str(SDK/'bsp-tools/ci/publish-github-release.sh')],env=env,check=True)
run('gh','release','upload',TAG,'--repo',REPO,str(RELEASE/'manifest.xml'),str(RELEASE/'CHANGELOG-v0.2.0.md'),str(RELEASE/'recovery-provenance.json'),str(RELEASE/'artifact-inventory.v1.json'))
receipt={'release_url':'https://github.com/'+REPO+'/releases/tag/'+TAG,'tag_unchanged':True,'rebuilt':False,'available':'DVT dev','production_available':False,'recovered_downloads':verified,'s3_prefix':'s3://'+BUCKET+'/'+PREFIX,'recovery_run':os.environ['GITHUB_RUN_ID']}
(WORK/'release-verification.json').write_text(json.dumps(receipt,indent=2)+'\n')
print('V020_RELEASE_PUBLISHED '+json.dumps(receipt),flush=True)
