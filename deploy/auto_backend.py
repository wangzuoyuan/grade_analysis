"""Host-side release controller. Only trusted successful CI artifacts may deploy.

Uses stdlib + installed Docker/gh; configuration, credentials, databases and state
remain outside Git. No self-hosted GitHub runner or inbound SSH is required.
"""
import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

SHA = re.compile(r'^[0-9a-f]{40}$')
FINGERPRINT = re.compile(r'^[0-9a-f]{64}$')
GATE_ID = 'grade_analysis_release_gate'


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as f:
        os.chmod(tmp, 0o600)
        json.dump(value, f)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def read_json(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).exists() else default


def run(argv, timeout=300):
    result = subprocess.run([str(x) for x in argv], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # Docker/gh errors may contain env/config values; never echo their output.
        raise RuntimeError(f'{Path(str(argv[0])).name} operation failed (exit {result.returncode})')
    return result.stdout


def inspect(name):
    return json.loads(run(['docker', 'inspect', name]))[0]


def db_check(path):
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('Database integrity verification failed')


def snapshot(data, destination):
    """Online SQLite backup; caller stops writers for the final recovery snapshot."""
    data, destination = Path(data), Path(destination)
    db_path = data / 'db.sqlite'
    if not db_path.is_file():
        raise ValueError('Explicit existing database required')
    shutil.copytree(data, destination, ignore=shutil.ignore_patterns('backups', 'db.sqlite', 'db.sqlite-wal', 'db.sqlite-shm'))
    with sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True) as src, sqlite3.connect(destination / 'db.sqlite') as dst:
        src.backup(dst)
    os.chmod(destination, 0o700)
    os.chmod(destination / 'db.sqlite', 0o600)
    db_check(destination / 'db.sqlite')
    atomic_json(destination / '.release-backup.json', {'complete': True})


def restore(data, backup):
    data, backup = Path(data), Path(backup)
    if read_json(backup / '.release-backup.json', {}).get('complete') is not True:
        raise RuntimeError('Incomplete backup; recovery requires operator intervention')
    db_check(backup / 'db.sqlite')
    for p in data.iterdir():
        if p.name == 'backups':
            continue
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink()
    for p in backup.iterdir():
        if p.name == '.release-backup.json':
            continue
        if p.is_dir():
            shutil.copytree(p, data / p.name)
        else:
            shutil.copy2(p, data / p.name)
    db_check(data / 'db.sqlite')


def extract_release(directory, destination, sha, branch, repo):
    manifest = read_json(directory / 'release.json')
    if not manifest or manifest.get('schema') != 1 or any(manifest.get(k) != v for k, v in {'sha': sha, 'branch': branch, 'repo': repo}.items()):
        raise ValueError('Release identity mismatch')
    if not SHA.fullmatch(sha) or not FINGERPRINT.fullmatch(manifest.get('backend_fingerprint', '')):
        raise ValueError('Invalid release identity')
    archive = directory / 'backend.tar.gz'
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest.get('archive_sha256'):
        raise ValueError('Release archive checksum mismatch')
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        if sum(m.size for m in members) > 100 * 1024 * 1024:
            raise ValueError('Release exceeds allowed source size')
        for m in members:
            p = Path(m.name)
            if not m.isfile() or p.is_absolute() or '..' in p.parts or len(p.parts) < 2 or p.parts[0] != 'backend':
                raise ValueError('Unsafe release archive path/type')
            if p.parts[1] not in ('Dockerfile', '.dockerignore', 'requirements-lock.txt', 'pyproject.toml', 'alembic.ini', 'app', 'alembic'):
                raise ValueError('Unexpected release file')
        tar.extractall(destination, members=members, filter='data')
    from package_backend import fingerprint
    if fingerprint(destination / 'backend') != manifest['backend_fingerprint']:
        raise ValueError('Extracted build fingerprint mismatch')
    return manifest


def trusted_run(item, repo, branch, sha):
    return (item.get('conclusion') == 'success' and item.get('status') == 'completed'
            and item.get('event') in ('push', 'workflow_dispatch')
            and item.get('head_branch') == branch and item.get('head_sha') == sha
            and item.get('head_repository', {}).get('full_name') == repo
            and item.get('path') == '.github/workflows/ci.yml')


def gateway_config(current, sha, maintenance=False):
    result = copy.deepcopy(current)
    servers = result['apps']['http']['servers']
    if len(servers) != 1:
        raise ValueError('Expected one explicitly configured Caddy server')
    server = next(iter(servers.values()))
    routes = [r for r in server.get('routes', []) if r.get('@id') != GATE_ID]
    status = {'sha': sha, 'ready': not maintenance}
    handlers = [{'match': [{'path': ['/api/deployment']}], 'handle': [{'handler': 'static_response',
                 'body': json.dumps(status), 'headers': {'Content-Type': ['application/json'], 'Cache-Control': ['no-store']}}]}]
    if maintenance:
        handlers.append({'match': [{'path': ['/api/*']}], 'handle': [{'handler': 'static_response',
                        'status_code': 503, 'body': 'Backend update in progress',
                        'headers': {'Retry-After': ['30'], 'Cache-Control': ['no-store']}}]})
    server['routes'] = [{'@id': GATE_ID, 'handle': [{'handler': 'subroute', 'routes': handlers}]}] + routes
    return result


PROBE = r'''
import os
from fastapi.testclient import TestClient
from app.main import app
host=os.environ.get('PUBLIC_HOST') or 'candidate.invalid'
assert os.environ.get('APP_PASSWORD'), 'Application authentication is required'
with TestClient(app, base_url='http://'+host) as c:
 assert c.get('/api/health').status_code==200
 assert c.get('/api/v1/shared/config').status_code==401
 assert c.post('/api/login',json={'password':os.environ['APP_PASSWORD']}).status_code==200
 assert c.get('/api/v1/shared/config').status_code==200
 assert c.post('/api/logout').status_code==200
'''
LIVE_PROBE = r'''
import json,urllib.request
r=urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=5)
assert r.status==200 and json.load(r)['ok']
'''


class Target:
    def __init__(self, name, cfg, runtime):
        self.name, self.cfg = name, cfg
        self.runtime = runtime / name
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_path = self.runtime / 'state.json'
        self.journal_path = self.runtime / 'journal.json'
        self.data = Path(cfg['data_dir']).resolve()
        self.compose_file = Path(cfg['compose_file']).resolve()
        self.state = read_json(self.state_path, {})

    def compose(self, image=None, args=()):
        argv = ['docker', 'compose', '--project-directory', str(self.compose_file.parent), '-f', str(self.compose_file)]
        if self.cfg.get('env_file'):
            argv += ['--env-file', self.cfg['env_file']]
        if image:
            override = self.runtime / 'image.json'
            atomic_json(override, {'services': {'backend': {'image': image}}})
            argv += ['-f', str(override)]
        return run(argv + list(args), timeout=900)

    def validate_mount(self):
        container = inspect(self.cfg['container'])
        mounts = {m['Destination']: Path(m['Source']).resolve() for m in container['Mounts'] if m['Type'] == 'bind'}
        labels = container['Config']['Labels']
        if mounts.get('/data') != self.data or labels.get('com.docker.compose.project') != self.cfg['project']:
            raise ValueError('Container project/data mount mismatch')
        if labels.get('com.docker.compose.service') != 'backend':
            raise ValueError('Wrong deployment service')
        if self.runtime == self.data or self.runtime.is_relative_to(self.data):
            raise ValueError('Runtime and data directories must be independent')
        return container

    def gate(self, sha, maintenance=False):
        proxy = self.cfg['proxy_container']
        current = json.loads(run(['docker', 'exec', proxy, 'wget', '-qO-', 'http://127.0.0.1:2019/config/']))
        updated = gateway_config(current, sha, maintenance)
        if updated == current and not maintenance:
            return
        path = self.runtime / 'gateway.json'
        atomic_json(path, updated)
        run(['docker', 'cp', path, proxy + ':/tmp/grade-analysis-release.json'])
        run(['docker', 'exec', proxy, 'caddy', 'reload', '--config', '/tmp/grade-analysis-release.json'])
        if maintenance:
            # Verify the block before stopping the writer or taking a recovery snapshot.
            code = "import urllib.request as u,urllib.error as e\ntry:u.urlopen('http://" + self.cfg['proxy_service'] + ":8080/api/v1/shared/config',timeout=5);raise RuntimeError('Gate missing')\nexcept e.HTTPError as x:assert x.code==503"
            run(['docker', 'exec', self.cfg['container'], 'python', '-c', code])

    def start(self, image):
        self.compose(image, ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--force-recreate', 'backend'])

    def smoke(self):
        for _ in range(60):
            try:
                run(['docker', 'exec', self.cfg['container'], 'python', '-c', LIVE_PROBE], timeout=10)
                run(['docker', 'exec', self.cfg['container'], 'python', '-c', PROBE], timeout=30)
                return
            except (RuntimeError, subprocess.TimeoutExpired):
                time.sleep(1)
        raise RuntimeError('New backend did not pass health/auth/config verification')

    def recover(self):
        journal = read_json(self.journal_path)
        if not journal:
            return
        if journal['phase'] == 'committed':
            self.state = journal['new_state']
            atomic_json(self.state_path, self.state)
            self.gate(self.state['sha'])
        else:
            self.compose(args=['stop', '-t', '30', 'backend'])
            backup = Path(journal['backup'])
            if read_json(backup / '.release-backup.json', {}).get('complete'):
                restore(self.data, backup)
            self.start(journal['old_image'])
            self.smoke()
            self.state = journal['old_state']
            atomic_json(self.state_path, self.state)
            self.gate(self.state.get('sha', ''))
        self.journal_path.unlink()

    def deploy(self, image, manifest):
        self.recover()
        self.validate_mount()
        # Validate migration + authenticated business reads on a separate live snapshot.
        with tempfile.TemporaryDirectory(dir=self.runtime, prefix='preflight-') as temp:
            scratch = Path(temp) / 'data'
            snapshot(self.data, scratch)
            backups = Path(temp) / 'backups'
            backups.mkdir()
            self.compose(image, ['run', '--rm', '--no-deps', '-T', '-v', str(scratch) + ':/data',
                                '-v', str(backups) + ':/backups', '-v', str(backups) + ':/data/backups',
                                'backend', 'python', '-c', PROBE])
            db_check(scratch / 'db.sqlite')
        old = self.validate_mount()
        backup = self.runtime / 'backups' / (time.strftime('%Y%m%dT%H%M%S') + '-' + manifest['sha'][:12])
        journal = {'phase': 'gating', 'old_image': old['Image'], 'old_state': self.state,
                   'backup': str(backup), 'new_state': {'sha': manifest['sha'], 'backend_fingerprint': manifest['backend_fingerprint']}}
        atomic_json(self.journal_path, journal)
        try:
            self.gate(self.state.get('sha', ''), maintenance=True)
            self.compose(args=['stop', '-t', '30', 'backend'])
            snapshot(self.data, backup)
            journal['phase'] = 'backup_complete'
            atomic_json(self.journal_path, journal)
            self.start(image)
            self.validate_mount()
            if inspect(self.cfg['container'])['Image'] != json.loads(run(['docker', 'image', 'inspect', image]))[0]['Id']:
                raise RuntimeError('Running image identity mismatch')
            self.smoke()
            journal['new_state']['image_id'] = inspect(self.cfg['container'])['Image']
            journal['phase'] = 'committed'
            atomic_json(self.journal_path, journal)
            # Commit precedes reopening traffic. Recovery after this point must not restore data.
            self.recover()
        except Exception:
            self.recover()
            raise


def tick(config):
    runtime = Path(config['runtime_dir']).resolve()
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (runtime / 'controller.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        repo = config['repo']
        statuses = {}
        data_paths = [Path(c['data_dir']).resolve() for c in config['targets'].values()]
        if len(set(data_paths)) != len(data_paths):
            raise ValueError('Targets must have separate data directories')
        for name, cfg in config['targets'].items():
            target = Target(name, cfg, runtime)
            try:
                target.recover()
                branch = cfg['branch']
                sha = json.loads(run(['gh', 'api', f'repos/{repo}/commits/{quote(branch, safe="")}']))['sha']
                if not SHA.fullmatch(sha):
                    raise ValueError('Invalid remote SHA')
                actual = target.validate_mount()
                if target.state.get('sha') == sha and target.state.get('image_id') == actual['Image']:
                    run(['docker', 'exec', cfg['container'], 'python', '-c', LIVE_PROBE], timeout=10)
                    target.gate(sha)
                    statuses[name] = {'status': 'current', 'sha': sha}
                    continue
                query = f'repos/{repo}/actions/workflows/ci.yml/runs?branch={quote(branch, safe="")}&head_sha={sha}&per_page=20'
                runs = json.loads(run(['gh', 'api', query]))['workflow_runs']
                approved = next((r for r in runs if trusted_run(r, repo, branch, sha)), None)
                if not approved:
                    statuses[name] = {'status': 'waiting_for_CI', 'sha': sha}
                    continue
                with tempfile.TemporaryDirectory(dir=runtime, prefix='artifact-') as temp:
                    temp = Path(temp)
                    run(['gh', 'run', 'download', str(approved['id']), '--repo', repo,
                         '--name', 'backend-release', '--dir', str(temp)], timeout=180)
                    source = temp / 'source'
                    manifest = extract_release(temp, source, sha, branch, repo)
                    # Never deploy an artifact that became stale while testing/downloading.
                    current = json.loads(run(['gh', 'api', f'repos/{repo}/commits/{quote(branch, safe="")}']))['sha']
                    if current != sha:
                        statuses[name] = {'status': 'superseded', 'sha': sha}
                        continue
                    target.validate_mount()
                    if (target.state.get('backend_fingerprint') == manifest['backend_fingerprint']
                            and target.state.get('image_id') == inspect(cfg['container'])['Image']):
                        target.smoke()
                        target.gate(sha)
                        target.state = {'sha': sha, 'backend_fingerprint': manifest['backend_fingerprint'],
                                        'image_id': inspect(cfg['container'])['Image']}
                        atomic_json(target.state_path, target.state)
                    else:
                        image = 'grade-analysis-backend:' + sha
                        run(['docker', 'build', '--label', 'org.opencontainers.image.revision=' + sha,
                             '-t', image, str(source / 'backend')], timeout=900)
                        target.deploy(image, manifest)
                statuses[name] = {'status': 'deployed', 'sha': sha}
            except Exception as exc:
                # Do not publish tool outputs, student data, secrets or URLs with tokens.
                statuses[name] = {'status': 'error', 'error_type': type(exc).__name__, 'message': str(exc) if isinstance(exc, (ValueError, RuntimeError)) else 'Operation failed'}
        atomic_json(runtime / 'status.json', {'checked_at': time.time(), 'targets': statuses})
        print(json.dumps(statuses), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    tick(read_json(args.config))
