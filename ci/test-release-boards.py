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

if __name__ == '__main__':
    unittest.main()
