"""Remove only inventoried, explicitly selected obsolete image files."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

base = Path(__file__).resolve().parent
plan = json.loads((base / 'cleanup-plan.json').read_text())
root = Path('/srv/bsp/workspaces')
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
targets = [Path(item['path']) for item in plan['files']]
workspaces = {root / p.relative_to(root).parts[0] for p in targets}
protected = root / 'pamir-rk3576-dvt-stable'
for path in targets:
    assert path.is_relative_to(root) and not path.is_relative_to(protected)
    assert path.resolve() == path, 'Do not follow symlinks'
    rel = path.relative_to(root)
    assert not rel.parts[0].startswith('v020-')
    assert 'IMAGES' in rel.parts or rel.parts[1] in ('rockdev', 'mkosi') or rel.parts[1:3] == ('output', 'firmware') or rel.parts[1:4] == ('output', 'update-ab', 'Image')
    assert path.name.endswith(('.img', '.img.xz', '.raucb', '.zip'))

ids = subprocess.check_output(['docker', 'ps', '-q'], text=True).split()
if ids:
    for c in json.loads(subprocess.check_output(['docker', 'inspect', *ids])):
        for mount in c['Mounts']:
            source = Path(mount['Source'])
            assert not any(source.is_relative_to(w) or w.is_relative_to(source) for w in workspaces), 'An active container uses a selected workspace'
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit():
        continue
    try:
        cwd = Path(os.readlink(proc / 'cwd'))
        assert not any(cwd.is_relative_to(w) for w in workspaces), 'An active process uses a selected workspace'
        for fd in (proc / 'fd').iterdir():
            try:
                opened = Path(os.readlink(fd))
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            assert opened not in targets, 'Selected image is open'
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        pass

for item, path in zip(plan['files'], targets):
    stat = path.stat()
    assert stat.st_size == item['bytes'] and stat.st_ino == item['inode'] and stat.st_dev == item['device']
    assert stat.st_mtime == item['mtime'] and stat.st_nlink == item['nlink'] == 1

receipt = {'at': datetime.now(timezone.utc).isoformat(), 'before': shutil.disk_usage(root)._asdict(), 'deleted': []}
try:
    for item, path in zip(plan['files'], targets):
        if shutil.disk_usage(root).free >= plan['target_free_bytes']:
            break
        path.unlink()
        receipt['deleted'].append(item)
finally:
    receipt['after'] = shutil.disk_usage(root)._asdict()
    (out / 'cleanup-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt, indent=2))
assert receipt['after']['free'] >= 75 * 1024**3, 'Still below build preflight floor'
