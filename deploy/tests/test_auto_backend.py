import hashlib
import io
import json
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auto_backend as deploy
import package_backend as pack


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sha = 'a' * 40

    def tearDown(self):
        self.temp.cleanup()

    def database(self, directory):
        directory.mkdir()
        with sqlite3.connect(directory / 'db.sqlite') as db:
            db.execute('CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)')
            db.execute("INSERT INTO sample VALUES (1, 'synthetic')")
        return directory

    def test_backup_and_restore_preserve_data_files_and_backup_directory(self):
        data = self.database(self.root / 'data')
        (data / 'exports').mkdir()
        (data / 'exports' / 'example.txt').write_text('synthetic file')
        (data / 'backups').mkdir()
        (data / 'backups' / 'keep').write_text('old backup')
        backup = self.root / 'snapshot'
        deploy.snapshot(data, backup)
        with sqlite3.connect(data / 'db.sqlite') as db:
            db.execute("UPDATE sample SET value='changed'")
            db.execute('CREATE TABLE migrated (id INTEGER)')
        (data / 'exports' / 'example.txt').unlink()
        deploy.restore(data, backup)
        with sqlite3.connect(data / 'db.sqlite') as db:
            self.assertEqual(db.execute('SELECT value FROM sample').fetchone()[0], 'synthetic')
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='migrated'").fetchone())
        self.assertEqual((data / 'exports' / 'example.txt').read_text(), 'synthetic file')
        self.assertTrue((data / 'backups' / 'keep').exists())
        self.assertFalse((backup / 'backups').exists())

    def test_incomplete_backup_never_clears_live_data(self):
        data = self.database(self.root / 'data')
        with self.assertRaises(RuntimeError):
            deploy.restore(data, self.root / 'missing')
        self.assertTrue((data / 'db.sqlite').exists())

    def test_wal_snapshot_contains_committed_rows(self):
        data = self.database(self.root / 'data')
        db = sqlite3.connect(data / 'db.sqlite')
        db.execute('PRAGMA journal_mode=WAL')
        db.execute("INSERT INTO sample VALUES(2,'in WAL')")
        db.commit()
        backup = self.root / 'snapshot'
        deploy.snapshot(data, backup)
        with sqlite3.connect(backup / 'db.sqlite') as copied:
            self.assertEqual(copied.execute('SELECT count(*) FROM sample').fetchone()[0], 2)
        db.close()

    def package(self):
        backend = self.root / 'backend'
        backend.mkdir()
        for name in pack.BUILD_PATHS:
            if name in ('app', 'alembic'):
                (backend / name).mkdir()
                (backend / name / 'example.py').write_text('# synthetic source\n')
            else:
                (backend / name).write_text('# synthetic configuration\n')
        # Runtime secrets and databases are deliberately outside the allowlist.
        (backend / '.env').write_text('APP_PASSWORD=synthetic-secret')
        (backend / 'db.sqlite').write_bytes(b'not a release asset')
        output = self.root / 'release'
        pack.package(self.root, output, self.sha, 'main', 'owner/repo')
        return output

    def test_source_package_roundtrip_excludes_credentials_and_data(self):
        output = self.package()
        target = self.root / 'extract'
        manifest = deploy.extract_release(output, target, self.sha, 'main', 'owner/repo')
        self.assertEqual(pack.fingerprint(target / 'backend'), manifest['backend_fingerprint'])
        self.assertFalse((target / 'backend/.env').exists())
        self.assertFalse((target / 'backend/db.sqlite').exists())

    def test_changed_archive_or_identity_is_rejected(self):
        output = self.package()
        with self.assertRaises(ValueError):
            deploy.extract_release(output, self.root / 'bad', 'b' * 40, 'main', 'owner/repo')
        with (output / 'backend.tar.gz').open('ab') as f:
            f.write(b'tampered')
        with self.assertRaises(ValueError):
            deploy.extract_release(output, self.root / 'bad', self.sha, 'main', 'owner/repo')

    def test_archive_traversal_and_symlinks_are_rejected(self):
        for name, kind in [('../escape', tarfile.REGTYPE), ('backend/app/link', tarfile.SYMTYPE)]:
            with self.subTest(name=name):
                output = self.root / ('archive' + str(len(name)))
                output.mkdir()
                archive = output / 'backend.tar.gz'
                with tarfile.open(archive, 'w:gz') as tar:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    member.linkname = '/tmp/outside'
                    tar.addfile(member, io.BytesIO(b''))
                deploy.atomic_json(output / 'release.json', {'schema': 1, 'sha': self.sha, 'branch': 'main',
                    'repo': 'owner/repo', 'backend_fingerprint': '0' * 64,
                    'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()})
                with self.assertRaises(ValueError):
                    deploy.extract_release(output, self.root / 'bad', self.sha, 'main', 'owner/repo')

    def test_only_current_successful_trusted_ref_can_deploy(self):
        item = {'conclusion': 'success', 'status': 'completed', 'event': 'push', 'head_branch': 'main',
                'head_sha': self.sha, 'head_repository': {'full_name': 'owner/repo'}, 'path': '.github/workflows/ci.yml'}
        self.assertTrue(deploy.trusted_run(item, 'owner/repo', 'main', self.sha))
        for field, value in [('event', 'pull_request'), ('conclusion', 'failure'), ('head_branch', 'unknown'),
                             ('head_sha', 'b' * 40), ('path', '.github/workflows/other.yml')]:
            bad = dict(item, **{field: value})
            self.assertFalse(deploy.trusted_run(bad, 'owner/repo', 'main', self.sha))
        self.assertFalse(deploy.trusted_run(dict(item, head_repository={'full_name': 'fork/repo'}), 'owner/repo', 'main', self.sha))

    def test_gateway_maintenance_preserves_unrelated_routes(self):
        current = {'apps': {'http': {'servers': {'srv0': {'listen': [':8080'], 'routes': [{'handle': [{'handler': 'reverse_proxy'}]}]}}}}}
        gated = deploy.gateway_config(current, self.sha, True)
        routes = gated['apps']['http']['servers']['srv0']['routes']
        self.assertEqual(routes[1:], current['apps']['http']['servers']['srv0']['routes'])
        self.assertEqual(routes[0]['handle'][0]['routes'][1]['handle'][0]['status_code'], 503)
        ready = deploy.gateway_config(gated, self.sha)
        self.assertEqual(len(ready['apps']['http']['servers']['srv0']['routes']), 2)
        self.assertEqual(len(ready['apps']['http']['servers']['srv0']['routes'][0]['handle'][0]['routes']), 1)
        self.assertEqual(len(current['apps']['http']['servers']['srv0']['routes']), 1)

    def target(self):
        data = self.database(self.root / 'data')
        target = deploy.Target('synthetic', {'data_dir': str(data), 'compose_file': str(self.root / 'compose.yml')}, self.root / 'runtime')
        target.state = {'sha': 'b' * 40}
        return target

    def test_uncommitted_recovery_restores_before_reopening_traffic(self):
        target = self.target()
        backup = self.root / 'backup'
        deploy.snapshot(target.data, backup)
        with sqlite3.connect(target.data / 'db.sqlite') as db:
            db.execute("UPDATE sample SET value='bad release'")
        deploy.atomic_json(target.journal_path, {'phase': 'backup_complete', 'backup': str(backup),
                          'old_image': 'old-image', 'old_state': {'sha': 'b' * 40}})
        calls = []
        with patch.object(target, 'compose', side_effect=lambda **kw: calls.append('stop')), \
             patch.object(target, 'start', side_effect=lambda image: calls.append(image)), \
             patch.object(target, 'smoke', side_effect=lambda: calls.append('smoke')), \
             patch.object(target, 'gate', side_effect=lambda sha: calls.append('reopen')):
            target.recover()
        self.assertEqual(calls, ['stop', 'old-image', 'smoke', 'reopen'])
        with sqlite3.connect(target.data / 'db.sqlite') as db:
            self.assertEqual(db.execute('SELECT value FROM sample').fetchone()[0], 'synthetic')

    def test_committed_recovery_never_rolls_back_new_user_writes(self):
        target = self.target()
        with sqlite3.connect(target.data / 'db.sqlite') as db:
            db.execute("UPDATE sample SET value='new user write'")
        deploy.atomic_json(target.journal_path, {'phase': 'committed', 'new_state': {'sha': self.sha}})
        with patch.object(target, 'gate'), patch.object(target, 'start') as start:
            target.recover()
        start.assert_not_called()
        with sqlite3.connect(target.data / 'db.sqlite') as db:
            self.assertEqual(db.execute('SELECT value FROM sample').fetchone()[0], 'new user write')
        self.assertEqual(target.state['sha'], self.sha)

    def refresh_targets(self):
        source = deploy.Target('production', {'data_dir': str(self.database(self.root / 'formal')),
                              'compose_file': str(self.root / 'formal.yml')}, self.root / 'runtime')
        target = self.target()
        target.cfg.update(container='synthetic-preview', branch='codex/diagnosis-roadmap',
                          data_refresh={'source_target': 'production', 'interval_seconds': 900})
        target.state.update(image_id='old-image', backend_fingerprint='c' * 64)
        with sqlite3.connect(target.data / 'db.sqlite') as db:
            db.execute("UPDATE sample SET value='preview experiment'")
            db.execute("INSERT INTO sample VALUES(99,'preview only')")
        return source, target

    def refresh_mocks(self, source, target, *, compose=None, smoke=None):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(source, 'validate_mount'))
        stack.enter_context(patch.object(target, 'validate_mount', return_value={'Image': 'old-image'}))
        stack.enter_context(patch.object(target, 'compose', side_effect=compose))
        stack.enter_context(patch.object(target, 'gate'))
        stack.enter_context(patch.object(target, 'start'))
        stack.enter_context(patch.object(target, 'smoke', side_effect=smoke))
        stack.enter_context(patch.object(deploy, 'inspect', return_value={'Image': 'old-image'}))
        stack.enter_context(patch.object(deploy, 'run', return_value='[{"Id":"old-image"}]'))
        return stack

    def diagnosis_config(self, target, values=None):
        with sqlite3.connect(target.data / 'db.sqlite') as db:
            db.execute('CREATE TABLE diagnosis_threshold_config ('
                       'id INTEGER PRIMARY KEY, direction_rank_change INTEGER NOT NULL, '
                       'streak_rank_change INTEGER NOT NULL, updated_at TEXT)')
            if values is not None:
                db.execute('INSERT INTO diagnosis_threshold_config '
                           '(id,direction_rank_change,streak_rank_change) VALUES (1,?,?)', values)

    def read_diagnosis_config(self, target):
        with sqlite3.connect(target.data / 'db.sqlite') as db:
            return db.execute('SELECT direction_rank_change,streak_rank_change '
                              'FROM diagnosis_threshold_config WHERE id=1').fetchone()

    def test_refresh_keeps_latest_preview_diagnosis_setting_only(self):
        source, target = self.refresh_targets()
        self.diagnosis_config(source, (150, 90))
        self.diagnosis_config(target, (95, 55))

        def late_edit(image=None, args=()):
            if args and args[0] == 'run':
                with sqlite3.connect(target.data / 'db.sqlite') as db:
                    db.execute('UPDATE diagnosis_threshold_config '
                               'SET direction_rank_change=110, streak_rank_change=70 WHERE id=1')

        with self.refresh_mocks(source, target, compose=late_edit):
            self.assertEqual(target.refresh_data(source), 'refreshed')
        self.assertEqual(self.read_diagnosis_config(target), (110, 70))
        with sqlite3.connect(target.data / 'db.sqlite') as db:
            self.assertEqual(db.execute('SELECT * FROM sample').fetchall(), [(1, 'synthetic')])
        self.assertEqual(self.read_diagnosis_config(source), (150, 90))

    def test_refresh_keeps_preview_default_even_if_source_has_custom_setting(self):
        source, target = self.refresh_targets()
        self.diagnosis_config(source, (150, 90))
        self.diagnosis_config(target)
        with self.refresh_mocks(source, target):
            self.assertEqual(target.refresh_data(source), 'refreshed')
        self.assertIsNone(self.read_diagnosis_config(target))

    def test_refresh_failure_restores_preview_diagnosis_setting(self):
        source, target = self.refresh_targets()
        self.diagnosis_config(source)
        self.diagnosis_config(target, (95, 55))
        with self.refresh_mocks(source, target, smoke=[RuntimeError('Synthetic startup failure'), None]):
            with self.assertRaises(RuntimeError):
                target.refresh_data(source)
        self.assertEqual(self.read_diagnosis_config(target), (95, 55))
        self.assertFalse(target.journal_path.exists())

    def test_refresh_applies_source_inserts_updates_deletes_and_keeps_old_preview_backup(self):
        source, target = self.refresh_targets()
        (source.data / 'raw').mkdir()
        (source.data / 'raw' / 'synthetic.txt').write_text('source upload')
        before = (source.data / 'db.sqlite').read_bytes()
        with self.refresh_mocks(source, target):
            self.assertEqual(target.refresh_data(source), 'refreshed')
        with sqlite3.connect(target.data / 'db.sqlite') as db:
            self.assertEqual(db.execute('SELECT * FROM sample').fetchall(), [(1, 'synthetic')])
        backup = next((target.runtime / 'backups').iterdir())
        with sqlite3.connect(backup / 'db.sqlite') as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sample').fetchone()[0], 2)
        self.assertEqual((target.data / 'raw' / 'synthetic.txt').read_text(), 'source upload')
        self.assertEqual((source.data / 'db.sqlite').read_bytes(), before)
        self.assertFalse(target.journal_path.exists())
        self.assertEqual(target.state['image_id'], 'old-image')

    def test_refresh_interval_and_unchanged_source_do_not_replace_preview_edits(self):
        source, target = self.refresh_targets()
        with self.refresh_mocks(source, target):
            target.refresh_data(source)
            with sqlite3.connect(target.data / 'db.sqlite') as db:
                db.execute("UPDATE sample SET value='new preview experiment'")
            with patch.object(deploy, 'snapshot') as snapshot:
                self.assertEqual(target.refresh_data(source), 'not_due')
                snapshot.assert_not_called()
            target.state['data_refresh']['checked_at'] = 0
            self.assertEqual(target.refresh_data(source), 'unchanged')
        with sqlite3.connect(target.data / 'db.sqlite') as db:
            self.assertEqual(db.execute('SELECT value FROM sample').fetchone()[0], 'new preview experiment')
        self.assertEqual(len(list((target.runtime / 'backups').iterdir())), 1)

    def test_release_change_checks_source_immediately_and_new_source_data_is_applied(self):
        source, target = self.refresh_targets()
        with self.refresh_mocks(source, target):
            target.refresh_data(source)
            with sqlite3.connect(source.data / 'db.sqlite') as db:
                db.execute("UPDATE sample SET value='new formal value'")
                db.execute("INSERT INTO sample VALUES(2,'new formal row')")
            target.state['sha'] = self.sha
            self.assertEqual(target.refresh_data(source), 'refreshed')
        with sqlite3.connect(target.data / 'db.sqlite') as db:
            self.assertEqual(db.execute('SELECT * FROM sample ORDER BY id').fetchall(),
                             [(1, 'new formal value'), (2, 'new formal row')])

    def test_source_migration_failure_keeps_preview_untouched(self):
        source, target = self.refresh_targets()
        before = (target.data / 'db.sqlite').read_bytes()
        with self.refresh_mocks(source, target, compose=RuntimeError('Synthetic migration failure')):
            with self.assertRaises(RuntimeError):
                target.refresh_data(source)
        self.assertEqual((target.data / 'db.sqlite').read_bytes(), before)
        self.assertFalse(target.journal_path.exists())
        self.assertNotIn('data_refresh', target.state)

    def test_refresh_startup_failure_restores_preview_experiments_and_old_state(self):
        source, target = self.refresh_targets()
        before = (source.data / 'db.sqlite').read_bytes()
        with self.refresh_mocks(source, target, smoke=[RuntimeError('Synthetic startup failure'), None]):
            with self.assertRaises(RuntimeError):
                target.refresh_data(source)
        with sqlite3.connect(target.data / 'db.sqlite') as db:
            self.assertEqual(db.execute('SELECT * FROM sample ORDER BY id').fetchall(),
                             [(1, 'preview experiment'), (99, 'preview only')])
        self.assertEqual((source.data / 'db.sqlite').read_bytes(), before)
        self.assertNotIn('data_refresh', target.state)
        self.assertFalse(target.journal_path.exists())

    def test_migrated_wal_is_captured_before_installing_refresh(self):
        source, target = self.refresh_targets()
        connections = []
        def migrate(image=None, args=()):
            if args and args[0] == 'run':
                path = next(arg[:-6] for arg in args if isinstance(arg, str) and arg.endswith(':/data'))
                db = sqlite3.connect(Path(path) / 'db.sqlite')
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('CREATE TABLE migrated (id INTEGER)')
                db.commit()
                connections.append(db)
        try:
            with self.refresh_mocks(source, target, compose=migrate):
                target.refresh_data(source)
            with sqlite3.connect(target.data / 'db.sqlite') as db:
                self.assertEqual(db.execute('SELECT count(*) FROM migrated').fetchone()[0], 0)
        finally:
            for db in connections:
                db.close()

    def test_refresh_configuration_rejects_formal_destination_shared_and_nested_paths(self):
        config = {'targets': {'preview': {'branch': 'codex/diagnosis-roadmap', 'data_dir': str(self.root / 'preview'),
                  'data_refresh': {'source_target': 'production'}}, 'production': {'branch': 'main', 'data_dir': str(self.root / 'formal')}}}
        self.assertEqual(deploy.refresh_source('preview', config), 'production')
        for path in (self.root / 'formal', self.root / 'formal' / 'child', self.root):
            config['targets']['preview']['data_dir'] = str(path)
            with self.assertRaises(ValueError):
                deploy.refresh_source('preview', config)
        config['targets']['preview']['data_dir'] = str(self.root / 'preview')
        config['targets']['preview']['branch'] = 'main'
        with self.assertRaises(ValueError):
            deploy.refresh_source('preview', config)

    def test_raw_file_change_is_detected_and_snapshot_symlinks_are_rejected(self):
        data = self.database(self.root / 'data')
        before = deploy.data_digest(data)
        (data / 'upload.txt').write_text('synthetic upload')
        self.assertNotEqual(before, deploy.data_digest(data))
        (data / 'link').symlink_to(data / 'upload.txt')
        with self.assertRaises(ValueError):
            deploy.data_digest(data)
        with self.assertRaises(ValueError):
            deploy.snapshot(data, self.root / 'unsafe-snapshot')
        self.assertFalse((self.root / 'unsafe-snapshot').exists())


if __name__ == '__main__':
    unittest.main()
