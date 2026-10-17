"""Inventory old generated image files; never traverse source or build caches."""
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

root = Path('/srv/bsp/workspaces')
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
report = {'at': datetime.now(timezone.utc).isoformat(), 'df': subprocess.check_output(['df', '-B1', str(root)], text=True)}
report['workspace_usage'] = subprocess.check_output(['du', '-x', '-B1', '--max-depth=2', str(root)], text=True)
running = subprocess.check_output(['docker', 'ps', '-q'], text=True).split()
report['containers'] = []
if running:
    for c in json.loads(subprocess.check_output(['docker', 'inspect', *running])):
        report['containers'].append({'id': c['Id'], 'name': c['Name'], 'mounts': c['Mounts'], 'state': c['State']['Status']})
report['process_cwds'] = []
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit():
        continue
    try:
        cwd = os.readlink(proc / 'cwd')
        if cwd.startswith(str(root)):
            report['process_cwds'].append({'pid': int(proc.name), 'comm': (proc / 'comm').read_text().strip(), 'cwd': cwd})
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        pass
report['images'] = []
for workspace in sorted(root.iterdir()):
    if not workspace.is_dir() or workspace.is_symlink():
        continue
    for sub in ('output', 'rockdev', 'mkosi'):
        start = workspace / sub
        if not start.is_dir() or start.is_symlink():
            continue
        for directory, dirs, files in os.walk(start, followlinks=False):
            # Restrict mkosi traversal to generated root-level images/output.
            if sub == 'mkosi':
                dirs[:] = [d for d in dirs if d == 'output' and Path(directory) == start]
            for name in files:
                p = Path(directory) / name
                if p.is_symlink() or not name.endswith(('.img', '.img.xz', '.raucb', '.zip')):
                    continue
                stat = p.stat()
                if stat.st_size < 128 * 1024**2:
                    continue
                report['images'].append({'path': str(p), 'bytes': stat.st_size, 'allocated': stat.st_blocks * 512, 'mtime': stat.st_mtime, 'nlink': stat.st_nlink, 'inode': stat.st_ino, 'device': stat.st_dev})
(out / 'disk-inventory.json').write_text(json.dumps(report, indent=2) + '\n')
print(report['df'])
print(report['workspace_usage'])
print(json.dumps({'containers': report['containers'], 'process_cwds': report['process_cwds'], 'images': report['images']}, indent=2))
