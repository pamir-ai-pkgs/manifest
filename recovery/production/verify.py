"""Read-only audit of the built DVT production artifacts."""
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

SDK = Path(os.environ['SDK_DIR'])
RELEASE = SDK / 'output/releases/ci' / os.environ['BUILD_ID']
BASE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
ROOTFS = SDK / 'mkosi/lapis-rootfs-verity.img'
RAW_ROOTFS = SDK / 'mkosi/lapis-rootfs.img'

def run(*args):
    return subprocess.check_output(args, stderr=subprocess.PIPE)

def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(8*1024*1024), b''):
            h.update(b)
    return h.hexdigest()

def read(p):
    return run('debugfs', '-R', 'cat '+p, str(ROOTFS))

def save(n, obj):
    (OUT / n).write_text(json.dumps(obj, indent=2)+'\n')

expected = json.loads((BASE/'expected-components.json').read_text())
assert ROOTFS.stat().st_size > RAW_ROOTFS.stat().st_size
# Read product contents from the actual deployed payload. Also bind the raw
# ext4 used to construct it to the exact prefix preceding the verity tree.
with RAW_ROOTFS.open('rb') as raw, ROOTFS.open('rb') as protected:
    while block := raw.read(8*1024*1024):
        assert protected.read(len(block)) == block, 'Raw ext4 differs from verity payload'
actual = {}
for path, pin in expected.items():
    actual[path] = run('git', '-c', f'safe.directory={SDK/path}', '-C', str(SDK/path), 'rev-parse', 'HEAD').decode().strip()
    assert actual[path] == pin, path
save('components.json', actual)
osrel = read('/usr/lib/os-release').decode()
assert 'IMAGE_VERSION="v0.2.0"' in osrel or 'IMAGE_VERSION=v0.2.0\n' in osrel
seed = json.loads(read('/usr/share/lapis-update/bootstrap/manifest.json'))
assert seed['version']=='2026.9.15' and seed['channel']=='sit'
assert seed['min_os_version']=='0.2.0-rc.1'
assert seed['toplevel_store_path']=='/nix/store/fss8vha6n2nb357mddyvqvdpkwx2gsg0-lapis-env'
assert read('/etc/lapis-update/channel-default').decode().strip()=='sit'
assert read('/usr/share/lapis/versions/update').decode().strip()=='1.1.11'
ca = OUT/'rauc-ca.pem'; ca.write_bytes(read('/etc/rauc/ca.cert.pem'))
assert ca.read_bytes()==(SDK/'mkosi/mkosi.extra/etc/rauc/ca.cert.pem').read_bytes()
bundle = RELEASE/'IMAGES/lapis-dvt-sec-v0.2.0.raucb'
uboot = RELEASE/'IMAGES/uboot.img'
info = json.loads(run('rauc','info','--keyring='+str(ca),'--output-format=json',str(bundle)))
assert info['version']=='v0.2.0'
assert info['compatible']==read('/etc/rauc/system.conf').decode().split('compatible=',1)[1].splitlines()[0].strip()
assert not info['compatible'].endswith('-dev')
save('rauc-info.json', info)
firmware = SDK/'output/firmware'
payloads = {'rootfs': firmware/'rootfs.img'}
for slot in ('boot', 'vbmeta'):
    slotted = firmware/(slot+'_a.img')
    payloads[slot] = slotted if slotted.is_file() else firmware/(slot+'.img')
for image in info['images']:
    for slot, meta in image.items():
        # RAUC uses generic names inside the bundle; production firmware has
        # per-slot vbmeta/boot names. Match the bundle builder's exact inputs.
        file = payloads[slot]
        assert file.stat().st_size == meta['size'] and sha(file) == meta['checksum'], slot
assert hashlib.sha256(read('/usr/lib/lapis-display/gst_capture.py')).hexdigest()=='6c38d4bae93f223f54d2f51d7d2fdbecfe22900a7685d1ef86912443970a3b6e'
assert b"GST_MPP_NO_RGA" in read('/usr/lib/lapis-display/gst_capture.py')
compat=json.loads(read('/usr/share/lapis-terminal-app/compatibility-v1.json'))
assert compat['terminal_input_protocol_version']==3 and compat['icon_catalog_version']==2
save('terminal-compatibility.json',compat)
for package in json.loads((BASE/'previous-pool-binaries.json').read_text())['packages']:
    for file in package['installed_files']:
        assert hashlib.sha256(read('/'+file['path'])).hexdigest()==file['sha256'], file['path']

sidecar=json.loads((RELEASE/'IMAGES/rootfs.img.system-image.json').read_text())
root_sha=sha(ROOTFS)
checksums={line.split('  ',1)[1].strip():line.split('  ',1)[0] for line in (RELEASE/'SHA256SUMS').read_text().splitlines()}
artifacts=[]
for p in [RELEASE/'IMAGES/update.img.xz',bundle,uboot]:
    digest=sha(p)
    relative=str(p.relative_to(RELEASE))
    assert checksums[relative] == digest, relative
    artifacts.append({'path':str(p),'bytes':p.stat().st_size,'sha256':digest})
save('artifacts.json', artifacts)
save('rootfs.json', {'os_release':osrel,'seed':seed,'sha256':root_sha,'sidecar':sidecar,'raw_ext4_prefix_matches':True,'verified_payload':str(ROOTFS)})
status=read('/var/lib/dpkg/status').decode()
installed={}
for para in status.split('\n\n'):
    fields=dict(l.split(': ',1) for l in para.splitlines() if ': ' in l and not l.startswith(' '))
    if fields.get('Status')=='install ok installed': installed[fields['Package']]=fields['Version']
audit=json.loads((BASE/'prebuilt-audit.json').read_text())
assert (SDK/'mkosi/packages/.pool-sha256').read_text().strip()==audit['pool_sha256']
image_config=(SDK/'output/.config').read_text().splitlines()
assert '# RK_USB_ADBD is not set' in image_config
assert 'android-adbd' not in installed
for pkg in audit['packages']:
    for rel in pkg['paths']:
        assert sha(SDK/'mkosi/packages'/rel) in pkg['sha256'], rel
    if pkg['package']=='android-adbd':
        # The audit's installed set came from dev. The secure defconfig disables
        # this optional package; its DEB checksum above still has to match.
        continue
    if pkg['installed_version'] is not None:
        assert installed.get(pkg['package'])==pkg['installed_version'], pkg['package']
save('packages.json', {'installed':installed,'verified_pool_debs':audit['unique_pool_deb_count'],'pool_sha256':audit['pool_sha256'],'android_adbd_absent_as_configured':True})
binaries=[]
for old in json.loads((BASE/'previous-binaries.json').read_text()):
    pkg,path=old['package'],old['path']
    debs=list((SDK/'mkosi/packages/sdk-built').glob(pkg+'_*.deb'))
    assert len(debs)==1, (pkg,[str(p) for p in debs])
    data=run('dpkg-deb','--fsys-tarfile',str(debs[0]))
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        fresh=t.extractfile('./'+path).read()
    present=read('/'+path)
    assert fresh==present, path
    binaries.append({'package':pkg,'path':path,'sha256':hashlib.sha256(present).hexdigest()})
save('binaries.json',binaries)
# Inventory remaining non-secret paths to resolve content checks without changing artifacts.
listing={p:run('debugfs','-R','ls -p '+p,str(ROOTFS)).decode() for p in ['/usr/share/lapis','usr/share/lapis-terminal-app','usr/share/lapis-hwserviced','/usr/share/lapis-update/bootstrap']}
save('listing.json',listing)
save('verified.json',{'verified':True,'source_run':os.environ['GITHUB_RUN_ID'],'build_id':os.environ['BUILD_ID'],'version':'v0.2.0','channel':'sit','rootfs_sha256':root_sha,'artifacts':artifacts,'publisher_not_run':True})
print('V020_PRODUCTION_VERIFIED '+json.dumps(artifacts),flush=True)
