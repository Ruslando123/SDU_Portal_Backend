"""Safety boundary tests need neither a production connection nor Docker."""
import json
import subprocess
import unittest
from unittest.mock import patch
import common


class ResetSafetyTest(unittest.TestCase):
    def run_guard(self, database='portal_benchmark', user='benchmark_migrator', marker='SDU-PHASE6-ISOLATED:token', project=common.PROJECT, running=False):
        def compose(*args,**kwargs):
            value='running-backend' if '--status' in args and running else '' if '--status' in args else 'isolated-postgres'
            return subprocess.CompletedProcess(args,0,stdout=value)
        with patch('common.compose',side_effect=compose),patch('common.subprocess.check_output',return_value=project),\
             patch('common.sql',return_value=json.dumps({'database':database,'user':user,'marker':marker})),\
             patch('common.settings',return_value={'BENCH_SAFETY_TOKEN':'token'}):
            common.guard(require_stopped=True)

    def test_wrong_compose_project_is_rejected(self):
        with self.assertRaises(RuntimeError):self.run_guard(project='working-portal')
    def test_wrong_database_is_rejected(self):
        with self.assertRaises(RuntimeError):self.run_guard(database='sdu_registration')
    def test_wrong_role_is_rejected(self):
        with self.assertRaises(RuntimeError):self.run_guard(user='portal_migrator')
    def test_unmarked_database_is_rejected(self):
        with self.assertRaises(RuntimeError):self.run_guard(marker=None)
        populated=json.dumps({'database':'portal_benchmark','user':'benchmark_migrator','marker':None})
        with patch('common.compose',return_value=subprocess.CompletedProcess([],0,stdout='postgres')),\
             patch('common.subprocess.check_output',return_value=common.PROJECT),\
             patch('common.settings',return_value={'BENCH_SAFETY_TOKEN':'token'}),\
             patch('common.sql',side_effect=[populated,'t','t']):
            with self.assertRaisesRegex(RuntimeError,'unrecognized populated'):
                common.guard(allow_unmarked=True)

    def test_wrong_marker_is_rejected(self):
        with self.assertRaises(RuntimeError):self.run_guard(marker='other')
    def test_running_backend_blocks_reset(self):
        with self.assertRaises(RuntimeError):self.run_guard(running=True)
    def test_only_stopped_marked_isolated_target_is_accepted(self):self.run_guard()

if __name__=='__main__':unittest.main()
