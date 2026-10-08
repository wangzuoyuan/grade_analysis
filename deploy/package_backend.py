"""Create a source-only, checksummed release; never include runtime data or env files."""
import argparse
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

BUILD_PATHS = ('Dockerfile', '.dockerignore', 'requirements-lock.txt', 'pyproject.toml', 'alembic.ini', 'app', 'alembic')


def build_files(backend):
    result = []
    for name in BUILD_PATHS:
        path = backend / name
        result.extend(path.rglob('*') if path.is_dir() else [path])
    files = sorted(p for p in result if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    if any(p.is_symlink() for p in files):
        raise ValueError('Symlinks are not permitted in release source')
    return files


def fingerprint(backend):
    h = hashlib.sha256()
    for p in build_files(backend):
        h.update(p.relative_to(backend).as_posix().encode() + b'\0' + hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def package(root, output, sha, branch, repo):
    output.mkdir(parents=True, exist_ok=True)
    backend = root / 'backend'
    archive = output / 'backend.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for p in build_files(backend):
            tar.add(p, arcname='backend/' + p.relative_to(backend).as_posix(), recursive=False)
    manifest = {'schema': 1, 'sha': sha, 'branch': branch, 'repo': repo,
                'backend_fingerprint': fingerprint(backend),
                'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
    (output / 'release.json').write_text(json.dumps(manifest))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    branch = subprocess.check_output(['git', 'branch', '--show-current'], cwd=root, text=True).strip()
    # CI provides the original ref even when checkout is detached.
    import os
    branch = os.environ.get('GITHUB_REF_NAME', branch)
    package(root, Path(args.output), sha, branch, os.environ.get('GITHUB_REPOSITORY', 'wangzuoyuan/grade_analysis'))
