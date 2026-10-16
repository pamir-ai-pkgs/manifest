"""Read-only audit of the retained failed-run DVT dev artifacts."""
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

SDK = Path('/srv/bsp/workspaces/pamir-rk3576-dvt-stable')
RELEASE = SDK / 'output/releases/ci/38851595535-1'
BASE = Path(__file__).resolve().parent
OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
ROOTFS = SDK / 'mkosi/lapis-rootfs.img'

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
actual = {}
for path, pin in expected.items():
    actual[path] = run('git', '-c', f'safe.directory={SDK/path}', '-C', str(SDK/path), 'rev-parse', 'HEAD').decode().strip()
    assert actual[path] == pin, path
save('components.json', actual)
osrel = read('/usr/lib/os-release').decode()
assert 'IMAGE_VERSION="v0.2.0"' in osrel or 'IMAGE_VERSION=v0.2.0\n' in osrel
seed = json.loads(read('/usr/share/lapis-update/bootstrap/manifest.json'))
assert seed['version']=='2026.10.16' and seed['channel']=='dev'
assert seed['min_os_version']=='0.2.0-rc.2'
assert seed['toplevel_store_path']=='/nix/store/hp71rayqbapsqvmkz2i7p50qvb95iiq9-lapis-env'
assert read('/etc/lapis-update/channel-default').decode().strip()=='dev'
assert read('/usr/share/lapis/versions/update').decode().strip()=='1.1.11'
ca = OUT/'rauc-ca.pem'; ca.write_bytes(read('/etc/rauc/ca.cert.pem'))
assert ca.read_bytes()==(SDK/'mkosi/mkosi.extra/etc/rauc/ca.cert.pem').read_bytes()
bundle = RELEASE/'IMAGES/lapis-dvt-dev-v0.2.0.raucb'
uboot = RELEASE/'IMAGES/uboot.img'
info = json.loads(run('rauc','info','--keyring='+str(ca),'--output-format=json',str(bundle)))
assert info['version']=='v0.2.0' and info['compatible']=='pamir-lapis-rk3576-dev'
save('rauc-info.json', info)
for image in info['images']:
    for slot, meta in image.items():
        file = ROOTFS if slot == 'rootfs' else RELEASE/'IMAGES'/meta['filename']
        assert file.stat().st_size == meta['size'] and sha(file) == meta['checksum'], slot
for wanted in json.loads((BASE/'expected-artifacts.json').read_text()):
    file=Path(wanted['path'])
    assert file.stat().st_size==wanted['bytes'] and sha(file)==wanted['sha256'], str(file)
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
assert root_sha=='3cfc810b289c7acf4c3377c4a08d51d848eb790a25c8148b04ab7b304e189224'
artifacts=[{'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)} for p in [bundle,uboot]]
save('artifacts.json', artifacts)
save('rootfs.json', {'os_release':osrel,'seed':seed,'sha256':root_sha,'sidecar':sidecar})
status=read('/var/lib/dpkg/status').decode()
installed={}
for para in status.split('\n\n'):
    fields=dict(l.split(': ',1) for l in para.splitlines() if ': ' in l and not l.startswith(' '))
    if fields.get('Status')=='install ok installed': installed[fields['Package']]=fields['Version']
audit=json.loads((BASE/'prebuilt-audit.json').read_text())
assert (SDK/'mkosi/packages/.pool-sha256').read_text().strip()==audit['pool_sha256']
for pkg in audit['packages']:
    for rel in pkg['paths']:
        assert sha(SDK/'mkosi/packages'/rel) in pkg['sha256'], rel
    if pkg['installed_version'] is not None:
        assert installed.get(pkg['package'])==pkg['installed_version'], pkg['package']
save('packages.json', {'installed':installed,'verified_pool_debs':audit['unique_pool_deb_count'],'pool_sha256':audit['pool_sha256']})
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
save('verified.json',{'verified':True,'source_run':38851595535,'attempt':1,'version':'v0.2.0','channel':'dev','rootfs_sha256':root_sha,'artifacts':artifacts,'publisher_not_run':True})
print('V020_DEV_RECOVERED '+json.dumps(artifacts),flush=True)
