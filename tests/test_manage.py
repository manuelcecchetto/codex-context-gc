import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import manage


class ManageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_profile_preserves_existing_instructions_and_roundtrips(self):
        path = self.root / 'AGENTS.md'
        original = '# My instructions\nPreserve my work.\n'
        path.write_text(original)
        manage.configure(path, 'install')
        self.assertTrue(path.read_text().startswith(original))
        manage.configure(path, 'install')
        self.assertEqual(path.read_text().count(manage.START), 1)
        self.assertIn('before materially different work', path.read_text())
        before = path.read_text()
        backups = list(self.root.glob('AGENTS.md.gc-backup-*'))
        manage.configure(path, 'install')
        self.assertEqual(path.read_text(), before)
        self.assertEqual(list(self.root.glob('AGENTS.md.gc-backup-*')), backups)
        manage.configure(path, 'off')
        self.assertEqual(path.read_text().strip(), original.strip())
        self.assertTrue(any(b.read_text() == original for b in backups))

    def test_conflicting_or_broken_guidance_is_not_overwritten(self):
        for text in ['Use compact_context already.', manage.START, manage.END + manage.START]:
            path = self.root / 'AGENTS.md'
            path.write_text(text)
            with self.assertRaises(RuntimeError):
                manage.configure(path, 'install')
            self.assertEqual(path.read_text(), text)

    def test_version_mismatch_stops_before_creating_source(self):
        with patch('manage.platform.system', return_value='Darwin'), patch('manage.shutil.which', return_value='/tool'), patch('manage.output', return_value='codex-cli different'):
            with self.assertRaisesRegex(RuntimeError, 'requires'):
                manage.install(self.root / 'install', self.root / 'App.app')
        self.assertFalse((self.root / 'install').exists())

    def test_launch_refuses_running_app(self):
        app = self.root / 'App.app'
        binary = self.root / 'codex'
        (self.root / 'installation.json').write_text(json.dumps({'app': str(app), 'binary': str(binary)}))
        with patch('manage.check_version'), patch('manage.os.access', return_value=True), patch('manage.output', return_value=str(app / 'Contents/MacOS/ChatGPT')), patch('manage.os.execve') as execute:
            with self.assertRaisesRegex(RuntimeError, 'Quit'):
                manage.launch(self.root)
            execute.assert_not_called()

    def test_launch_sets_override_only_for_new_process(self):
        app = self.root / 'App.app'
        binary = self.root / 'codex'
        (self.root / 'installation.json').write_text(json.dumps({'app': str(app), 'binary': str(binary)}))
        with patch('manage.check_version') as version, patch('manage.os.access', return_value=True), patch('manage.output', return_value='other app'), patch('manage.os.execve') as execute:
            manage.launch(self.root)
            self.assertEqual(version.call_count, 2)
            self.assertEqual(execute.call_args.args[2]['CODEX_CLI_PATH'], str(binary))

    def test_install_pins_source_and_validates_before_receipt(self):
        prefix = self.root / 'install'
        app = self.root / 'App.app'
        calls = []

        def fake_run(*args, cwd=None):
            calls.append(tuple(str(a) for a in args))
            if args[:2] == ('git', 'init'):
                Path(args[2]).mkdir()
            if args[:2] == ('cargo', 'build'):
                binary = prefix / 'source/codex-rs/target/debug/codex'
                binary.parent.mkdir(parents=True)
                binary.touch()

        def fake_output(*args, cwd=None):
            return manage.MANIFEST['commit'] if args[:2] == ('git', 'rev-parse') else 'nextest'

        with patch('manage.platform.system', return_value='Darwin'), patch('manage.shutil.which', return_value='/tool'), patch('manage.check_version'), patch('manage.run', side_effect=fake_run), patch('manage.output', side_effect=fake_output), patch('manage.smoke') as smoke, patch('manage.os.access', return_value=True):
            manage.install(prefix, app)
            smoke.assert_called_once()
        self.assertTrue((prefix / 'installation.json').exists())
        self.assertTrue(any(c[:3] == ('git', 'apply', '--check') for c in calls))
        self.assertTrue(any(c[:2] == ('just', 'test') for c in calls))
        self.assertTrue(any(c[:3] == ('git', 'fetch', '--depth=1') for c in calls))


if __name__ == '__main__':
    unittest.main()
