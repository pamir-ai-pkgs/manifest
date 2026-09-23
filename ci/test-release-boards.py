"""Check board isolation by executing the workflow's parameter resolver."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

WORKFLOW = Path(__file__).parents[1] / '.github/workflows/rk3576-bsp-release.yml'

class BoardReleaseTests(unittest.TestCase):
    def test_both_boards_have_serial_independent_builds(self):
        job = yaml.safe_load(WORKFLOW.read_text())['jobs']['build']
        self.assertEqual(job['strategy']['max-parallel'], 1)
        self.assertFalse(job['strategy']['fail-fast'])
        self.assertEqual(job['strategy']['matrix']['board'], ['evt3', 'dvt'])

    def test_candidate_board_paths_and_configurations_are_distinct(self):
        job = yaml.safe_load(WORKFLOW.read_text())['jobs']['build']
        body = next(s['run'] for s in job['steps'] if s.get('name') == 'Resolve build parameters')
        results = []
        for board in ['evt3', 'dvt']:
            with tempfile.NamedTemporaryFile() as f:
                env = dict(os.environ, GITHUB_ENV=f.name, GITHUB_EVENT_NAME='push',
                           GITHUB_REF_TYPE='tag', GITHUB_REF_NAME='rk3576-v0.2.0-rc.1',
                           GITHUB_RUN_ID='12345', GITHUB_RUN_ATTEMPT='1',
                           S3_BUCKET='test-bucket', WORK_ROOT='/tmp/work', BOARD_VARIANT=board)
                subprocess.run(['bash', '-e', '-c', body], env=env, check=True)
                values = dict(line.split('=', 1) for line in Path(f.name).read_text().splitlines())
                self.assertEqual(values['DEFCONFIG'], f'rockchip_rk3576_lapis_{board}_defconfig')
                self.assertEqual(values['SECURE_DEFCONFIG'], f'rockchip_rk3576_lapis_{board}_secure_defconfig')
                self.assertEqual(values['RK_IMAGE_VERSION'], 'v0.2.0-rc.1')
                results.append(values)
        for key in ['SDK_DIR', 'SEC_SDK_DIR', 'SEC_S3_PREFIX', 'DEV_ASSET_SUFFIX', 'SEC_ASSET_SUFFIX', 'DEV_IMAGE_LABEL', 'SEC_IMAGE_LABEL']:
            self.assertNotEqual(results[0][key], results[1][key], key)
        self.assertTrue(results[1]['DEV_S3_PREFIX'].endswith('/rk3576-v0.2.0-rc.1-dvt/12345-1'))
        self.assertTrue(results[1]['SEC_S3_PREFIX'].endswith('/rk3576-v0.2.0-rc.1-dvt-sec/12345-1'))

    def test_cleanup_leaves_other_board_outputs_intact(self):
        job = yaml.safe_load(WORKFLOW.read_text())['jobs']['build']
        body = next(s['run'] for s in job['steps'] if s.get('name') == 'Clean stale BSP outputs')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            names = ['pamir-rk3576-candidate', 'pamir-rk3576-secure',
                     'pamir-rk3576-dvt-candidate', 'pamir-rk3576-dvt-secure']
            for name in names:
                (root / name / 'output').mkdir(parents=True)
            env = dict(os.environ, WORK_ROOT=d, SDK_DIR=str(root / names[0]),
                       SEC_SDK_DIR=str(root / names[1]), XDG_CACHE_HOME=str(root / 'cache'))
            subprocess.run(['bash', '-e', '-c', body], env=env, check=True)
            self.assertFalse((root / names[0] / 'output').exists())
            self.assertFalse((root / names[1] / 'output').exists())
            self.assertTrue((root / names[2] / 'output').exists())
            self.assertTrue((root / names[3] / 'output').exists())

    def test_nightly_builds_the_dvt_dev_leg_only(self):
        job = yaml.safe_load(WORKFLOW.read_text())['jobs']['build']
        body = next(s['run'] for s in job['steps'] if s.get('name') == 'Resolve build parameters')
        with tempfile.NamedTemporaryFile() as f:
            env = dict(os.environ, GITHUB_ENV=f.name, GITHUB_EVENT_NAME='push',
                       GITHUB_REF_TYPE='tag', GITHUB_REF_NAME='rk3576-v0.2.0-nightly.3',
                       GITHUB_RUN_ID='12345', GITHUB_RUN_ATTEMPT='1',
                       S3_BUCKET='test-bucket', WORK_ROOT='/tmp/work', BOARD_VARIANT='dvt')
            subprocess.run(['bash', '-e', '-c', body], env=env, check=True)
            values = dict(line.split('=', 1) for line in Path(f.name).read_text().splitlines())
        self.assertEqual(values['DEV_S3_PREFIX'],
                         's3://test-bucket/pamir-rk3576/nightly/rk3576-v0.2.0-nightly.3-dvt/12345-1')
        self.assertEqual(values['DEV_ASSET_SUFFIX'], '-dvt')
        secure = [s for s in job['steps'] if s['name'] in (
            'Bootstrap secure workspace BSP tools', 'Stage signing keys into the secure workspace',
            'Build and upload secure release', 'Attach secure artifacts to the GitHub release')]
        self.assertEqual(len(secure), 4)
        for step in secure:
            self.assertIn("(env.CHANNEL == 'nightly' && env.BOARD_VARIANT == 'evt3')", step['if'])

if __name__ == '__main__':
    unittest.main()
