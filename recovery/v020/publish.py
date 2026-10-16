"""Publish only the independently verified retained final DVT development bundle."""
import base64
import importlib.util
import json
import os
import subprocess
from pathlib import Path

base=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('publisher',base.parents[1]/'ci/ota-publish.py')
p=importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
work=Path(os.environ['RUNNER_TEMP'])/'v020-publish'
work.mkdir(exist_ok=False)
os.environ.pop('AWS_PROFILE',None)
os.environ.update(AWS_REGION=p.REGION,AWS_DEFAULT_REGION=p.REGION)
identity=json.loads(p.output('aws','sts','get-caller-identity','--output','json'))
if identity['Account']!=p.ACCOUNT or not identity['Arn'].startswith('arn:aws:sts::322551983552:assumed-role/lapis-ota-build/'):
    raise ValueError('Unexpected native OTA identity')
tag=json.loads(p.output('gh','api','repos/pamir-ai-pkgs/manifest/git/ref/tags/rk3576-v0.2.0'))
if tag['object']['sha']!='04008b8b8b181323bdc9da3792bd029c0c013a34':
    raise ValueError('Frozen tag changed')
expected=json.loads((base/'expected-artifacts.json').read_text())
for item in expected:
    path=Path(item['path'])
    if path.stat().st_size!=item['bytes'] or p.verify_checksum(path,item['sha256']+'  '+path.name,path.name)!=item['sha256']:
        raise ValueError('Retained artifact changed')
bundle,uboot=map(lambda i:Path(i['path']),expected)
ca=Path(os.environ['RUNNER_TEMP'])/'v020-recovery/rauc-ca.pem'
info=json.loads(p.output('rauc','info','--keyring='+str(ca),'--output-format=json',str(bundle)))
p.validate_bundle(info,{'version':'0.2.0','compatible':'pamir-lapis-rk3576-dev'})
exe=p.build_publisher(work,'1c8b65545e867eecfb3da317a673e37c8198ff53')
secret=p.output('aws','secretsmanager','get-secret-value','--secret-id','lapis-ota/fleet-tokens','--query','SecretString','--output','text')
token=next(line.strip() for line in reversed(secret.splitlines()) if line.strip())
key=work/'manifest-key.der'
key.write_bytes(base64.b64decode(p.github_file('pamir-ai-pkgs/linux-rockchip-mkosi','a8f7bd6467606141f58f47e04ef2ec91f33645d0','mkosi.extra/etc/lapis-ota/manifest.p256.pub').strip()))
def check(version):
    return p.check_offer(work,token,key,'dev',version)
def validate(offer):
    p.validate_offer(offer,'0.2.0',expected[0]['sha256'],expected[0]['bytes'],expected[1]['sha256'],expected[1]['bytes'])
before=check('0.0.0')
if before.get('update_available') and before['update']['version']=='0.2.0':
    validate(before)
    print('Identical final dev OTA is already live; no duplicate writes.',flush=True)
else:
    if check('0.2.0').get('update_available'):
        raise ValueError('Newer dev release already offered')
    subprocess.run([str(exe),'publish','--bundle',str(bundle),'--uboot',str(uboot),'--board','lapis','--channel','dev','--version','0.2.0','--region','us-west-2','--bucket','pamir-ota-bundles','--table','lapis-ota-releases'],check=True)
for current in ['0.0.0','0.2.0-rc.2','0.2.0-rc.3']:
    validate(check(current))
if check('0.2.0').get('update_available'):
    raise ValueError('Installed 0.2.0 offered a reinstall')
receipt={'board':'lapis','channel':'dev','version':'0.2.0','source_run':38851595535,'attempt':1,'bundle':expected[0],'uboot':expected[1],'manifest_signature_verified':True,'offer_from_rc2_verified':True,'offer_from_rc3_verified':True,'no_reinstall_on_020_verified':True,'tag_unchanged':True,'physical_ota_acceptance':'pending'}
(work/'publication-verification.json').write_text(json.dumps(receipt,indent=2)+'\n')
print('OTA_PUBLISH_VERIFIED '+json.dumps(receipt),flush=True)
