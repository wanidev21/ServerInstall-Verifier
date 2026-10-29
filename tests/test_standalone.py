import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen
from urllib.error import URLError
ROOT = Path(__file__).resolve().parents[1]
from demo_fixture import write_fixture

class StandaloneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='standalone tools ')
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.log = ROOT
        self.install = ROOT

    def run_python(self, script, *args):
        return subprocess.run([sys.executable, '-E', str(script)]+list(args), cwd=str(self.work),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', timeout=20)


    def test_install_bundle_never_imports_log_analyzer(self):
        code = 'import sys;sys.path.insert(0,sys.argv[1]);import serverinstall.checks;assert "serverlog" not in sys.modules'
        p = self.run_python('-c', code, str(self.install))
        self.assertEqual(p.returncode, 0, p.stderr)


    def test_install_bundle_mock_to_report(self):
        sos, mock = write_fixture(self.work/'sample data')
        out = self.work/'standalone reports'
        p = self.run_python(self.install/'serverinstall.py', '--sos', str(sos), '--redfish-mock', str(mock), '-o', str(out))
        self.assertEqual(p.returncode, 0, p.stderr+p.stdout)
        self.assertIn('Result : PASS', p.stdout)
        self.assertEqual(len(list(out.glob('*.html'))), 1)


    def test_install_shell_forwards_spaced_arguments(self):
        shell = os.environ.get('SERVERTOOLS_TEST_SHELL') or shutil.which('sh')
        if not shell and Path('C:/Program Files/Git/bin/bash.exe').is_file():
            shell = 'C:/Program Files/Git/bin/bash.exe'
        if not shell:
            self.skipTest('POSIX shell unavailable')
        sos, mock = write_fixture(self.work/'shell sample')
        out = self.work/'shell reports'
        # Git Bash에는 현재 Python 실행 파일 폴더를 명시적으로 전달한다.
        env = dict(os.environ)
        env['PATH'] = str(Path(sys.executable).parent)+os.pathsep+env.get('PATH','')
        p = subprocess.run([shell, str(self.install/'run_install_check.sh'), '--sos', str(sos),
                            '--redfish-mock', str(mock), '-o', str(out)], cwd=str(self.work), env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr+p.stdout)
        self.assertEqual(len(list(out.glob('*.html'))), 1)
