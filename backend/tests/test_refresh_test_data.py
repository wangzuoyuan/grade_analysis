"""刷新测试数据的快照一致性与失败保护，全部使用合成 SQLite。"""
import importlib.util
from pathlib import Path
import sqlite3

import pytest

spec = importlib.util.spec_from_file_location(
    'refresh_test_data', Path(__file__).resolve().parents[2] / 'scripts/refresh_test_data.py')
refresh_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh_module)


def read_values(path):
    db = sqlite3.connect(path)
    try:
        return db.execute('SELECT value FROM sample ORDER BY value').fetchall()
    finally:
        db.close()


def test_refresh_reads_new_wal_commits_and_backs_up_previous_test_data(tmp_path):
    source = tmp_path / 'production.sqlite'
    writer = sqlite3.connect(source)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE sample(value INTEGER)')
        writer.execute('INSERT INTO sample VALUES(1)')
        writer.commit()
        data, backups = tmp_path / 'test', tmp_path / 'backups'
        refresh_module.refresh(source, data, backups, migrator=lambda _: None)
        local = sqlite3.connect(data / 'db.sqlite')
        local.execute('INSERT INTO sample VALUES(99)')
        local.commit()
        local.close()
        writer.execute('INSERT INTO sample VALUES(2)')
        writer.commit()
        saved = refresh_module.refresh(source, data, backups, migrator=lambda _: None)
        assert read_values(data / 'db.sqlite') == [(1,), (2,)]
        assert read_values(saved) == [(1,), (99,)]
        assert read_values(source) == [(1,), (2,)]
        assert (data / 'db.sqlite').stat().st_mode & 0o777 == 0o600
    finally:
        writer.close()


def test_failed_migration_keeps_previous_database(tmp_path):
    source = tmp_path / 'production.sqlite'
    db = sqlite3.connect(source)
    db.execute('CREATE TABLE sample(value INTEGER)')
    db.execute('INSERT INTO sample VALUES(1)')
    db.commit()
    db.close()
    data, backups = tmp_path / 'test', tmp_path / 'backups'
    refresh_module.refresh(source, data, backups, migrator=lambda _: None)
    before = (data / 'db.sqlite').read_bytes()

    def fail(stage):
        (stage / 'db.sqlite').write_bytes(b'failed migration')
        raise RuntimeError('synthetic failure')

    with pytest.raises(RuntimeError, match='synthetic failure'):
        refresh_module.refresh(source, data, backups, migrator=fail)
    assert (data / 'db.sqlite').read_bytes() == before
    assert read_values(source) == [(1,)]
    assert not list(tmp_path.glob('.refresh-*'))


def test_refuse_same_source_and_destination(tmp_path):
    with pytest.raises(RuntimeError, match='源数据库与目标相同'):
        refresh_module.refresh(tmp_path / 'db.sqlite', tmp_path, tmp_path / 'backups')
