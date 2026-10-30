"""Copy narrowly selected build evidence; never modify the BSP workspaces."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

out = Path(sys.argv[1])
out.mkdir(exist_ok=False)
pins = {p.get('path'): p.get('revision') for p in ET.parse('rk3576-debian-ab.xml').getroot().findall('project')}
assert pins['external/lapis'] == 'bc6f7288bc6d48720c08f9c72e5217554aa85318'
pool = '200fa1bfcc9f963d03c15ba8f1b5668b6c1e4e9b3e0afcad4367e21733647163'
required = {'lapis-hwserviced', 'lapis-terminal-app', 'lapis-display-session', 'lapis-bootanim', 'lapis-update', 'lapis-healthd', 'lapis-ota', 'lapis-telemetryd'}

def command(*args):
    return subprocess.check_output(args, text=True).strip()

def copy(source, dest):
    assert source.is_file() and not source.is_symlink(), str(source)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    with dest.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'bytes': dest.stat().st_size, 'sha256': digest}

for variant, sdk in [('dev', Path('/srv/bsp/workspaces/pamir-rk3576-dvt-candidate')),
                     ('sit', Path('/srv/bsp/workspaces/pamir-rk3576-dvt-secure'))]:
    report = {'sources': {}, 'packages': {}, 'kernel': {}, 'mutation': 'none; evidence copied read-only'}
    for rel, pin in pins.items():
        source = sdk / rel
        assert command('git', '-C', str(source), 'rev-parse', 'HEAD') == pin, rel
        subprocess.run(['git', '-C', str(source), 'diff', '--quiet', 'HEAD', '--'], check=True)
        report['sources'][rel] = pin
    assert (sdk / 'mkosi/packages/.pool-sha256').read_text().strip() == pool
    report['pool_sha256'] = pool
    for deb in sorted((sdk / 'mkosi/packages/sdk-built').glob('*.deb')):
        package = command('dpkg-deb', '-f', str(deb), 'Package')
        if package.endswith('-dbgsym'):
            continue
        version = command('dpkg-deb', '-f', str(deb), 'Version')
        info = copy(deb, out / variant / 'sdk-debs' / deb.name)
        info.update(package=package, version=version)
        report['packages'][deb.name] = info
    assert required.issubset({p['package'] for p in report['packages'].values()})
    for rel in ('.config', 'include/config/kernel.release', 'include/generated/compile.h',
                'arch/arm64/boot/dts/rockchip/rk3576-lapis-dvt.dtb', 'arch/arm64/boot/Image', 'resource.img'):
        report['kernel'][rel] = copy(sdk / 'kernel-6.1' / rel, out / variant / 'kernel' / rel)
    (out / variant / 'evidence.json').write_text(json.dumps(report, indent=2) + '\n')
    print(variant, 'copied', len(report['packages']), 'package files and pinned kernel evidence; source trees unchanged')
