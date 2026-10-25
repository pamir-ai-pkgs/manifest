#!/usr/bin/env python3
"""Stage a trusted Lapis build for dev/SIT images without publishing a channel."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

REPOSITORY = 'pamir-ai-pkgs/lapis'


def github(*args):
    return subprocess.check_output(['gh', *args], text=True)


def trusted_run(run, workflow):
    if (run.get('repository', {}).get('full_name') != REPOSITORY
            or run.get('head_repository', {}).get('full_name') != REPOSITORY
            or run.get('path') != '.github/workflows/' + workflow
            or run.get('head_branch') != 'main' or run.get('event') != 'workflow_dispatch'
            or run.get('status') != 'completed' or run.get('conclusion') != 'success'):
        raise ValueError('Seed must come from a successful manual main-branch workflow in ' + REPOSITORY)


def os_version(value):
    match = re.fullmatch(r'v?([0-9]{1,5})\.([0-9]{1,5})\.([0-9]{1,5})(?:-(nightly|rc)\.([0-9]{1,5}))?', value)
    if not match:
        raise ValueError('Candidate images require a publishable RC/final OS version')
    major, minor, patch, kind, counter = match.groups()
    return (int(major), int(minor), int(patch), {'nightly': 0, 'rc': 1, None: 2}[kind], int(counter or 0))


def validate_seed(root, source, image_version):
    # Reject symlinks/special files before any copying. The existing image
    # bootstrap validator then verifies signatures, hashes and closure topology.
    for path in root.rglob('*'):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError('Seed contains a symlink or special file')
    c = json.loads((root / 'candidate.json').read_text())
    if c.get('source') != source:
        raise ValueError('Candidate Lapis source differs from the image manifest pin')
    if (not re.fullmatch(r'[1-9][0-9]*', c.get('run', ''))
            or not re.fullmatch(r'[0-9]{1,9}(\.[0-9]{1,9})*', c.get('version', ''))
            or not re.fullmatch(r'/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-[A-Za-z0-9+._?=-]+', c.get('toplevel', ''))
            or not re.fullmatch(r'sha256-[A-Za-z0-9+/]{43}=', c.get('nar_hash', ''))
            or type(c.get('closure_size')) is not int or not 0 < c['closure_size'] <= 4096 * 1024 * 1024
            or c.get('schema') != 1 or c.get('channel') != 'dev'
            or c.get('board') != 'rk3576' or c.get('profile') != 'lapis-vendor'):
        raise ValueError('Invalid candidate build receipt')
    if os_version(image_version) < os_version(c['min_os_version']):
        raise ValueError('Image version is below the candidate OS floor')
    if not (root / 'cache/nix-cache-info').is_file():
        raise ValueError('Seed has no Nix cache')
    for channel in ('dev', 'sit'):
        m = json.loads((root / channel / 'manifest.json').read_text())
        expected = dict(schema_version=1, version=c['version'], channel=channel,
                        board=c['board'], profile=c['profile'], toplevel_store_path=c['toplevel'],
                        nar_hash=c['nar_hash'], closure_size=c['closure_size'],
                        min_os_version=c['min_os_version'], rollout=100)
        if any(m.get(k) != value for k, value in expected.items()):
            raise ValueError('Seed manifest differs from candidate receipt or channel')
        if not re.fullmatch('manifest-' + channel + r'-[1-9][0-9]*', m.get('signature', {}).get('key_id', '')):
            raise ValueError('Seed manifest has the wrong channel signing key')
    return c


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', default='')
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--manifest-file', required=True)
    parser.add_argument('--image-version', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifests = args.sdk / '.repo/manifests'
    pin_file = manifests / 'ci/nix-image-seed.json'
    pin = json.loads(pin_file.read_text()) if pin_file.exists() else {}
    if pin_file.exists() and (not isinstance(pin, dict) or set(pin) != {'seed_run_id'}
                             or not isinstance(pin['seed_run_id'], str)
                             or not re.fullmatch(r'[1-9][0-9]*', pin['seed_run_id'])):
        raise ValueError('Committed seed pin must contain one positive seed_run_id string')
    pinned_run = pin.get('seed_run_id', '')
    if args.run_id and pinned_run and args.run_id != pinned_run:
        raise ValueError('Dispatch seed run differs from the committed seed pin')
    run_id = args.run_id or pinned_run
    if not run_id:
        print('No candidate seed selected; image uses its existing published bootstrap pins')
        return
    if not isinstance(run_id, str) or not re.fullmatch(r'[1-9][0-9]*', run_id):
        raise ValueError('Seed run ID must be a positive integer string')
    projects = ET.parse(manifests / args.manifest_file).getroot().findall('project')
    lapis = [p for p in projects if p.get('name') == 'lapis' and p.get('path') == 'external/lapis']
    if len(lapis) != 1 or not re.fullmatch(r'[0-9a-f]{40}', lapis[0].get('revision', '')):
        raise ValueError('Candidate seed requires one exact Lapis source commit in the image manifest')
    source = lapis[0].get('revision')
    run = json.loads(github('api', f'repos/{REPOSITORY}/actions/runs/{run_id}'))
    trusted_run(run, 'lapis-nix-image-seed.yml')
    args.output.mkdir(parents=True, exist_ok=False)
    bundle = args.output / 'download'
    subprocess.run(['gh', 'run', 'download', run_id, '--repo', REPOSITORY,
                    '--name', 'nix-image-seed', '--dir', str(bundle)], check=True)
    candidate = validate_seed(bundle, source, args.image_version)
    build = json.loads(github('api', f'repos/{REPOSITORY}/actions/runs/{candidate["run"]}'))
    trusted_run(build, 'lapis-nix-release.yml')
    if build.get('head_sha') != source:
        raise ValueError('Candidate source does not match the successful build')
    for channel in ('dev', 'sit'):
        target = args.output / channel
        target.mkdir()
        shutil.copyfile(bundle / channel / 'manifest.json', target / 'manifest.json')
        shutil.copytree(bundle / 'cache', target / 'cache', copy_function=os.link)
        (target / '.fetch-complete').touch()
    receipt = dict(seed_run_id=run_id, seed_workflow_source=run['head_sha'],
                   image_version=args.image_version, candidate=candidate)
    (args.output / 'selection.json').write_text(json.dumps(receipt, indent=2) + '\n')
    with open(os.environ['GITHUB_ENV'], 'a') as env:
        env.write(f'DEV_NIX_SEED_INPUT={args.output / "dev"}\n')
        env.write(f'SIT_NIX_SEED_INPUT={args.output / "sit"}\n')
        env.write(f'NIX_SEED_RECEIPT={args.output / "selection.json"}\n')
    print(f'Staged candidate {candidate["version"]} from build {candidate["run"]}; source {source}')


if __name__ == '__main__':
    main()
