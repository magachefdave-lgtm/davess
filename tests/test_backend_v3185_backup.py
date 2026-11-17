"""Independent processes share a temporary SQLite folder; no operational data."""
import importlib.util
import multiprocessing as mp
import os
from pathlib import Path
import sqlite3

import pytest

SOURCE = Path(os.environ.get('WLR_REGRESSION_SOURCE', str(Path(__file__).resolve().parents[1] / 'backend/main.py')))


def load(folder, label):
    os.environ.update(WLR_CONFIG_DIR=str(folder), SMART_DUTY_MODE='ADMIN')
    spec = importlib.util.spec_from_file_location('daily_test_' + label, SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def worker(folder, label, pipe, release):
    module = load(folder, label)
    pipe.send('ready')
    assert pipe.recv() == 'backup'
    module.BACKUP_STATE['verified'] = ''
    original = module._snapshot_to
    if label == 'B':
        def pause(target):
            pipe.send('before_snapshot')
            assert release.wait(15)
            return original(target)
        module._snapshot_to = pause
    module.ensure_daily_backup()
    if label == 'A':
        current = module.get_all_duty_data()['dataVersion']
        module.save_all_data(module.SyncFullDataDTO(baseVersion=current, globalMemo='AFTER_FIRST_WRITE'), x_admin_token='test-password')
    pipe.send(dict(module.BACKUP_STATE))


@pytest.fixture
def shared(tmp_path, monkeypatch):
    monkeypatch.setenv('WLR_CONFIG_DIR', str(tmp_path))
    monkeypatch.setenv('SMART_DUTY_MODE', 'ADMIN')
    sqlite3.connect(tmp_path / 'smart_duty.db').close()
    (tmp_path / 'config_admin.ini').write_text('[NETWORK]\nshared_folder_path = ' + str(tmp_path) + '\nbind_host = 127.0.0.1\nadmin_password = test-password\nopen_browser = false\ntray_icon = false\n', encoding='utf-8')
    module = load(tmp_path, 'seed')
    module.save_all_data(module.SyncFullDataDTO(baseVersion=0, globalMemo='BEFORE_FIRST_WRITE'), x_admin_token='test-password')
    today = module.datetime.now().strftime('%Y-%m-%d')
    target = tmp_path / 'backups' / f'smart_duty_{today}.db'
    target.unlink()
    module.BACKUP_STATE['verified'] = ''
    return module, target


def test_cross_process_first_valid_daily_backup_survives_late_snapshot(shared):
    module, target = shared
    ctx = mp.get_context('spawn')
    release = ctx.Event()
    pipes = {}
    processes = []
    try:
        for label in ('A', 'B'):
            parent, child = ctx.Pipe()
            pipes[label] = parent
            process = ctx.Process(target=worker, args=(str(target.parent.parent), label, child, release))
            process.start()
            processes.append(process)
        for pipe in pipes.values():
            assert pipe.poll(15) and pipe.recv() == 'ready'
        # Imports can create the startup daily copy. Both processes are now ready.
        if target.exists():
            target.unlink()
        pipes['B'].send('backup')
        assert pipes['B'].poll(15) and pipes['B'].recv() == 'before_snapshot'
        pipes['A'].send('backup')
        # With the shared lock A waits. In the old implementation it publishes and saves.
        early_a = pipes['A'].recv() if pipes['A'].poll(1) else None
        release.set()
        if early_a is None:
            assert pipes['A'].poll(15)
            early_a = pipes['A'].recv()
        assert pipes['B'].poll(15)
        b = pipes['B'].recv()
        assert early_a['ok'] and b['ok']
        for process in processes:
            process.join(15)
            assert process.exitcode == 0
        with sqlite3.connect(target) as db:
            assert db.execute("SELECT value FROM app_settings WHERE key='global_memo'").fetchone()[0] == 'BEFORE_FIRST_WRITE'
        assert module.get_all_duty_data()['globalMemo'] == 'AFTER_FIRST_WRITE'
    finally:
        release.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(5)


def test_failed_publication_releases_lock_cleans_temp_and_retry_keeps_valid_copy(shared, monkeypatch):
    module, target = shared
    original = module.os.replace
    def fail_daily(source, destination):
        if Path(destination) == target:
            raise OSError('synthetic publication failure')
        return original(source, destination)
    monkeypatch.setattr(module.os, 'replace', fail_daily)
    module.ensure_daily_backup()
    assert module.BACKUP_STATE['ok'] is False
    assert not target.exists()
    assert not list(target.parent.glob('.backup-*.tmp'))
    monkeypatch.setattr(module.os, 'replace', original)
    # A separate process must acquire the released file lock and retry successfully.
    ctx = mp.get_context('spawn')
    parent, child = ctx.Pipe()
    release = ctx.Event()
    process = ctx.Process(target=worker, args=(str(target.parent.parent), 'A', child, release))
    process.start()
    try:
        assert parent.poll(15) and parent.recv() == 'ready'
        parent.send('backup')
        assert parent.poll(15) and parent.recv()['ok']
        process.join(15)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
        process.join(5)
    first = target.read_bytes()
    module.ensure_daily_backup()
    assert target.read_bytes() == first
    assert module.BACKUP_STATE['ok'] is True


def test_invalid_daily_copy_is_archived_and_replaced_under_lock(shared):
    module, target = shared
    target.write_bytes(b'broken synthetic sqlite')
    module.ensure_daily_backup()
    assert module.BACKUP_STATE['ok'] is True
    assert module._verify_sqlite_file(target) == ''
    assert any(p.read_bytes() == b'broken synthetic sqlite' for p in target.parent.glob(target.name + '.broken-*'))
