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


if __name__ == '__main__':
    unittest.main()
