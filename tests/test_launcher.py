import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock

import run


class LauncherTests(unittest.TestCase):
    def test_msys_interpreter_is_recognized(self):
        self.assertTrue(run.is_msys_python('mingw_x86_64_ucrt','C:/Python314'))
        self.assertTrue(run.is_msys_python('win-amd64','C:\\msys64\\ucrt64'))
        self.assertFalse(run.is_msys_python('win-amd64','C:/Python313'))
        self.assertFalse(run.is_msys_python('linux-x86_64','/usr'))

    def test_active_environment_is_reused_even_with_bin_layout_on_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            environment=Path(directory)/'.venv'
            executable=environment/'bin/python.exe'
            with patch.object(run.sys,'prefix',str(environment)),patch.object(run.sys,'base_prefix','C:/Python313'),patch.object(run.sys,'executable',str(executable)),patch.object(run.venv,'EnvBuilder') as builder:
                self.assertEqual(run.environment_python(environment,windows=True),executable)
                builder.assert_not_called()

    def test_wrong_layout_is_preserved_and_not_recreated(self):
        with tempfile.TemporaryDirectory() as directory:
            environment=Path(directory)/'.venv'
            (environment/'bin').mkdir(parents=True)
            existing=environment/'bin/python.exe'
            existing.write_bytes(b'old interpreter')
            with patch.object(run.venv,'EnvBuilder') as builder:
                with self.assertRaisesRegex(SystemExit,'переименуйте'):
                    run.environment_python(environment,windows=True)
                builder.assert_not_called()
            self.assertEqual(existing.read_bytes(),b'old interpreter')

    def test_msys_fails_before_attempting_to_create_or_install(self):
        with patch.object(run,'is_msys_python',return_value=True),patch.object(run.venv,'EnvBuilder') as builder,patch.object(run.subprocess,'run') as command:
            with self.assertRaisesRegex(SystemExit,'MSYS2'):
                run.main()
            builder.assert_not_called()
            command.assert_not_called()

    def test_missing_venv_launcher_is_reported_before_pip(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(run.venv,'EnvBuilder') as builder:
                with self.assertRaisesRegex(SystemExit,'неподходящей структурой'):
                    run.environment_python(Path(directory)/'.venv',windows=True)
                builder.return_value.create.assert_called_once()

    def test_existing_environment_is_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            environment=Path(directory)/'.venv'
            output=json.dumps([[3,13],str(environment),'C:/Python313','win-amd64'])
            with patch.object(run.subprocess,'run',return_value=Mock(stdout=output)):
                run.validate_environment(Path('python.exe'),environment)
            output=json.dumps([[3,14],str(environment),'C:/msys64/ucrt64','mingw_x86_64'])
            with patch.object(run.subprocess,'run',return_value=Mock(stdout=output)):
                with self.assertRaisesRegex(SystemExit,'повреждено'):
                    run.validate_environment(Path('python.exe'),environment)
