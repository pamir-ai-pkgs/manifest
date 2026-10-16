"""Use the existing changelog override API without editing frozen sources."""
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

sdk = Path(os.environ['SDK_DIR'])
base = Path(__file__).resolve().parent
expected = json.loads((base / 'expected-components.json').read_text())
for path, pin in expected.items():
    actual = subprocess.check_output(['git', '-C', str(sdk / path), 'rev-parse', 'HEAD'], text=True).strip()
    assert actual == pin, path
manifest_pin = subprocess.check_output(['git', '-C', str(sdk / '.repo/manifests'), 'rev-parse', 'HEAD'], text=True).strip()
assert manifest_pin == 'd823b38bd2b21b26fc35715ea644dce3c02491da'
assert (sdk / 'mkosi/packages/.pool-sha256').read_text().strip() == '5ea84800fb7afded7b679c60a0faa3f62081e01ee735b6515594bcb5fd8be7c9'
release = sdk / 'output/releases/ci' / os.environ['BUILD_ID']
actual_manifest = ET.fromstring((release / 'manifest.xml').read_bytes())
assert {p.get('path'): p.get('revision') for p in actual_manifest.findall('project')} == expected
sys.path.insert(0, str(sdk / 'bsp-tools/src'))
from bsp_release_tools.collect_release_metadata import parse_args, write_release_metadata

args = parse_args(sys.argv[1:])
write_release_metadata(**vars(args), changelog_path=base / 'CHANGELOG.md')
