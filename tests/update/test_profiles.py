"""Profile migration and backend startup checks using temporary synthetic data."""
from __future__ import annotations

import configparser
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import types
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

SOURCE_ROOT = Path(__file__).resolve().parents[2]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
from updater.core import UpdateError
from updater import profiles

BACKEND = SOURCE_ROOT / 'backend' / 'main.py'


def make_db(folder):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'smart_duty.db'
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE sentinel(id INTEGER PRIMARY KEY, value TEXT)')
    connection.execute('INSERT INTO sentinel VALUES(1, ?)', ('원본 보존',))
    connection.commit()
    connection.close()
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_config(path):
    config = configparser.ConfigParser(interpolation=None)
    config.read(path, encoding='utf-8')
    return config


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('WLR_PROFILE_ROOT', str(tmp_path / 'profiles'))
    for name in ('WLR_CONFIG_DIR', 'WLR_HEALTH_FILE', 'WLR_UPDATE_INSTANCE_ID', 'SMART_DUTY_MODE'):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize('role', ['ADMIN', 'VIEWER', 'MASTER'])
def test_profile_role_roots_are_stable(tmp_path, role):
    assert profiles.profile_dir(role) == tmp_path / 'profiles' / role


@pytest.mark.parametrize('role', ['', 'admin', '../ADMIN', 'OTHER'])
def test_invalid_roles_rejected(role):
    with pytest.raises(UpdateError):
        profiles.profile_dir(role)


def test_relative_profile_root_rejected(monkeypatch):
    monkeypatch.setenv('WLR_PROFILE_ROOT', 'relative/root')
    with pytest.raises(UpdateError):
        profiles.profile_dir('ADMIN')


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'cp949'])
@pytest.mark.parametrize('role', ['ADMIN', 'VIEWER'])
@pytest.mark.parametrize('legacy_name', [True, False])
def test_migrate_local_retains_original_db_and_ini(tmp_path, encoding, role, legacy_name):
    legacy = tmp_path / '기존 설치'
    db = make_db(legacy)
    name = 'config.ini' if legacy_name else f'config_{role.lower()}.ini'
    ini = legacy / name
    ini.write_text('[NETWORK]\nshared_folder_path=\nserver_port=8123\nadmin_password=암호%100\n'
                   'open_browser=false\ntray_icon=false\n[OTHER]\nkeep=원문 설정\n', encoding=encoding)
    old_ini, old_db = ini.read_bytes(), digest(db)
    config_dir = profiles.prepare_profile(role, legacy_dir=legacy)
    new = read_config(config_dir / f'config_{role.lower()}.ini')
    assert Path(new['NETWORK']['shared_folder_path']) == legacy
    assert new['NETWORK']['server_port'] == '8123'
    assert new['NETWORK']['admin_password'] == '암호%100'
    assert new['OTHER']['keep'] == '원문 설정'
    assert ini.read_bytes() == old_ini
    assert digest(db) == old_db
    assert not (legacy / (name + '.migrated')).exists()
    assert not (config_dir / 'smart_duty.db').exists()


def test_migrate_shared_path_remains_original(tmp_path):
    shared = tmp_path / 'shared'
    db = make_db(shared)
    legacy = tmp_path / 'legacy'
    legacy.mkdir()
    ini = legacy / 'config_admin.ini'
    ini.write_text(f'[NETWORK]\nshared_folder_path={shared}\n', encoding='utf-8')
    before = digest(db), ini.read_bytes()
    config_dir = profiles.prepare_profile('ADMIN', legacy_dir=legacy)
    assert read_config(config_dir / 'config_admin.ini')['NETWORK']['shared_folder_path'] == str(shared)
    assert (digest(db), ini.read_bytes()) == before


@pytest.mark.parametrize(('source_name', 'expected_port'), [('config_master.ini', '8123'),
                                                           ('config_admin.ini', '8002'), ('config.ini', '8002')])
def test_master_migration_uses_separate_profile_and_shared_original_database(tmp_path, source_name, expected_port):
    data = tmp_path / 'shared'
    db = make_db(data)
    legacy = tmp_path / 'prior'
    legacy.mkdir()
    ini = legacy / source_name
    ini.write_text(f'[NETWORK]\nshared_folder_path={data}\nserver_port=8123\nadmin_password=old\n')
    original_ini, original_db = ini.read_bytes(), digest(db)
    config_dir = profiles.prepare_profile('MASTER', legacy_dir=legacy)
    assert config_dir == tmp_path / 'profiles' / 'MASTER' / 'config'
    cfg = read_config(config_dir / 'config_master.ini')['NETWORK']
    assert cfg['shared_folder_path'] == str(data) and cfg['server_port'] == expected_port
    assert cfg['admin_password'] == 'old'
    profiles.validate_profile_storage('MASTER', config_dir)
    assert (ini.read_bytes(), digest(db)) == (original_ini, original_db)
    assert not (config_dir / 'smart_duty.db').exists()


def test_relative_legacy_data_path_resolved_against_legacy_folder(tmp_path):
    legacy = tmp_path / 'legacy'
    make_db(legacy / 'data')
    (legacy / 'config_admin.ini').write_text('[NETWORK]\nshared_folder_path=data\n', encoding='utf-8')
    config = profiles.prepare_profile('ADMIN', legacy_dir=legacy)
    assert read_config(config / 'config_admin.ini')['NETWORK']['shared_folder_path'] == str(legacy / 'data')


def test_initialized_profile_is_never_changed(tmp_path):
    shared = tmp_path / 'shared'
    make_db(shared)
    config_dir = profiles.prepare_profile('VIEWER', shared_dir=shared)
    ini = config_dir / 'config_viewer.ini'
    before = ini.read_bytes()
    assert profiles.prepare_profile('VIEWER', shared_dir=tmp_path / 'missing') == config_dir
    assert ini.read_bytes() == before


@pytest.mark.parametrize('role', ['ADMIN', 'VIEWER'])
def test_existing_selection_requires_db(tmp_path, role):
    shared = tmp_path / 'empty'
    shared.mkdir()
    with pytest.raises(UpdateError):
        profiles.prepare_profile(role, shared_dir=shared)
    assert not (shared / 'smart_duty.db').exists()
    assert not (profiles.profile_dir(role) / 'config' / f'config_{role.lower()}.ini').exists()


def test_unreachable_legacy_shared_path_never_uses_local_db(tmp_path):
    legacy = tmp_path / 'legacy'
    local_db = make_db(legacy)
    old_hash = digest(local_db)
    (legacy / 'config_admin.ini').write_text(f'[NETWORK]\nshared_folder_path={tmp_path / "missing"}\n', encoding='utf-8')
    with pytest.raises(UpdateError):
        profiles.prepare_profile('ADMIN', legacy_dir=legacy)
    assert digest(local_db) == old_hash
    assert not (profiles.profile_dir('ADMIN') / 'config').exists()


def test_invalid_ini_fails_without_persisting_profile(tmp_path):
    legacy = tmp_path / 'legacy'
    db = make_db(legacy)
    (legacy / 'config_admin.ini').write_bytes(b'not an ini\x81')
    before = digest(db)
    with pytest.raises(UpdateError):
        profiles.prepare_profile('ADMIN', legacy_dir=legacy)
    assert digest(db) == before
    assert not (profiles.profile_dir('ADMIN') / 'config').exists()


def test_fresh_admin_requires_explicit_permission():
    with pytest.raises(UpdateError):
        profiles.prepare_profile('ADMIN')
    with pytest.raises(UpdateError):
        profiles.prepare_profile('VIEWER', allow_new=True)
    config_dir = profiles.prepare_profile('ADMIN', allow_new=True)
    db = config_dir / 'smart_duty.db'
    assert db.read_bytes().startswith(b'SQLite format 3\x00')
    assert read_config(config_dir / 'config_admin.ini')['NETWORK']['shared_folder_path'] == str(config_dir)
    connection = sqlite3.connect(db)
    assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    connection.close()


def test_fresh_cannot_overwrite_existing_db():
    config_dir = profiles.profile_dir('ADMIN') / 'config'
    db = make_db(config_dir)
    before = digest(db)
    with pytest.raises(UpdateError):
        profiles.prepare_profile('ADMIN', allow_new=True)
    assert digest(db) == before
    assert not (config_dir / 'config_admin.ini').exists()


def test_viewer_explicit_existing_shared_database(tmp_path):
    shared = tmp_path / 'shared'
    db = make_db(shared)
    before = digest(db)
    config_dir = profiles.prepare_profile('VIEWER', shared_dir=shared)
    config = read_config(config_dir / 'config_viewer.ini')
    assert config['NETWORK']['server_port'] == '8001'
    assert config['NETWORK']['shared_folder_path'] == str(shared)
    assert digest(db) == before


@pytest.fixture
def boot_backend(tmp_path, monkeypatch):
    loaded = []
    handlers_before = set(logging.getLogger('smartduty').handlers)

    def boot(config_dir=None, role='ADMIN', config_text=None):
        app_dir = tmp_path / ('binary_' + uuid.uuid4().hex)
        app_dir.mkdir()
        if config_dir is not None:
            monkeypatch.setenv('WLR_CONFIG_DIR', str(config_dir))
        else:
            monkeypatch.delenv('WLR_CONFIG_DIR', raising=False)
            (app_dir / f'config_{role.lower()}.ini').write_text(
                config_text or '[NETWORK]\nopen_browser=false\ntray_icon=false\n', encoding='utf-8')
        monkeypatch.setenv('SMART_DUTY_MODE', role)
        name = 'profile_backend_' + uuid.uuid4().hex
        module = types.ModuleType(name)
        module.__file__ = str(app_dir / 'main.py')
        sys.modules[name] = module
        loaded.append(name)
        exec(compile(BACKEND.read_text(encoding='utf-8'), str(BACKEND), 'exec'), module.__dict__)
        return module

    yield boot
    for name in loaded:
        sys.modules.pop(name, None)
    logger = logging.getLogger('smartduty')
    for handler in set(logger.handlers) - handlers_before:
        handler.close()
        logger.removeHandler(handler)


def test_backend_managed_config_stable_and_health(boot_backend, monkeypatch):
    config_dir = profiles.prepare_profile('ADMIN', allow_new=True)
    ini = config_dir / 'config_admin.ini'
    before = ini.read_bytes()
    health = config_dir.parent / 'health.json'
    nonce = 'a8' * 24
    monkeypatch.setenv('WLR_HEALTH_FILE', str(health))
    monkeypatch.setenv('WLR_UPDATE_INSTANCE_ID', nonce)
    module = boot_backend(config_dir)
    expected_version = (SOURCE_ROOT / 'VERSION').read_text().strip()
    assert module.APP_VERSION == expected_version
    assert Path(module.DB_PATH) == config_dir / 'smart_duty.db'
    assert Path(module.LOG_PATH).parent == config_dir
    assert not health.exists(), 'A mere import must not claim server readiness.'
    with TestClient(module.app) as client:
        payload = client.get('/api/system/health').json()
        assert payload == json.loads(health.read_text())
        assert payload == {'pid': os.getpid(), 'instance_id': nonce, 'version': expected_version,
                           'mode': 'ADMIN', 'port': 8000, 'host': '127.0.0.1'}
    assert ini.read_bytes() == before
    assert not (Path(module.APP_DIR) / 'smart_duty.db').exists()


def test_backend_requires_absolute_config_dir(boot_backend):
    with pytest.raises(RuntimeError, match='절대'):
        boot_backend('relative/config')


def test_managed_invalid_ini_never_falls_back(boot_backend):
    config_dir = profiles.prepare_profile('ADMIN', allow_new=True)
    ini = config_dir / 'config_admin.ini'
    ini.write_bytes(b'invalid\xff')
    before = digest(config_dir / 'smart_duty.db')
    with pytest.raises(RuntimeError, match='프로필 설정'):
        boot_backend(config_dir)
    assert digest(config_dir / 'smart_duty.db') == before


@pytest.mark.parametrize('existing_folder', [False, True])
def test_missing_explicit_shared_path_fails_closed_on_boot(tmp_path, boot_backend, existing_folder):
    shared = tmp_path / 'unavailable'
    if existing_folder:
        shared.mkdir()
    with pytest.raises(HTTPException) as error:
        boot_backend(config_text=f'[NETWORK]\nshared_folder_path={shared}\nopen_browser=false\ntray_icon=false\n')
    assert error.value.status_code == 503
    assert not list(tmp_path.rglob('smart_duty.db'))


def test_managed_empty_shared_path_fails_closed(boot_backend):
    config_dir = profiles.prepare_profile('ADMIN', allow_new=True)
    (config_dir / 'config_admin.ini').write_text('[NETWORK]\nshared_folder_path=\n', encoding='utf-8')
    with pytest.raises(RuntimeError, match='데이터 경로가 비어'):
        boot_backend(config_dir)


@pytest.mark.parametrize('readonly', [False, True])
def test_disappeared_managed_db_is_never_recreated(boot_backend, readonly):
    config_dir = profiles.prepare_profile('ADMIN', allow_new=True)
    module = boot_backend(config_dir)
    db = Path(module.DB_PATH)
    moved = db.with_suffix('.original')
    db.rename(moved)
    with pytest.raises(HTTPException) as error:
        module.get_db_connection(readonly=readonly)
    assert error.value.status_code == 503
    assert not db.exists()
    assert moved.is_file()


def test_sqlite_rw_mode_prevents_creation_after_existence_race(boot_backend, monkeypatch):
    config_dir = profiles.prepare_profile('ADMIN', allow_new=True)
    module = boot_backend(config_dir)
    db = Path(module.DB_PATH)
    original_check = module.require_existing_shared_database

    def disappears():
        original_check()
        db.rename(db.with_suffix('.original'))
    monkeypatch.setattr(module, 'require_existing_shared_database', disappears)
    with pytest.raises(sqlite3.OperationalError):
        module.get_db_connection(readonly=False)
    assert not db.exists()


def test_readonly_fallback_cannot_create_missing_file(boot_backend, tmp_path):
    module = boot_backend()
    missing = tmp_path / 'never-create.db'
    with pytest.raises(sqlite3.OperationalError):
        module.open_readonly(str(missing))
    assert not missing.exists()


def test_health_reports_preserved_specific_host(boot_backend):
    config_dir = profiles.prepare_profile('ADMIN', allow_new=True)
    ini = config_dir / 'config_admin.ini'
    config = read_config(ini)
    config['NETWORK']['bind_host'] = '192.0.2.10'
    with ini.open('w', encoding='utf-8') as stream:
        config.write(stream)
    module = boot_backend(config_dir)
    assert module.startup_health_payload()['host'] == '192.0.2.10'


def test_health_uses_actual_resolved_port(boot_backend):
    module = boot_backend()
    module.RUN_PORT = 8120
    assert module.startup_health_payload()['port'] == 8120


def test_corrupt_db_never_emits_health(tmp_path, boot_backend, monkeypatch):
    config_dir = profiles.prepare_profile('ADMIN', allow_new=True)
    db = config_dir / 'smart_duty.db'
    db.write_bytes(b'NOT SQLITE, preserve these bytes')
    original = db.read_bytes()
    health = config_dir.parent / 'health.json'
    monkeypatch.setenv('WLR_HEALTH_FILE', str(health))
    monkeypatch.setenv('WLR_UPDATE_INSTANCE_ID', 'ab' * 24)
    with pytest.raises(RuntimeError, match='당직 데이터 파일을 준비하지 못했습니다'):
        boot_backend(config_dir)
    assert not health.exists()
    assert db.read_bytes() == original


def test_same_table_names_with_missing_required_column_never_emit_health(boot_backend, monkeypatch):
    config_dir = profiles.prepare_profile('ADMIN', allow_new=True)
    first = boot_backend(config_dir)
    connection = sqlite3.connect(first.DB_PATH)
    connection.execute('ALTER TABLE schedules DROP COLUMN worker_name')
    connection.commit()
    # The damaged table still exists: readiness must inspect its required columns,
    # independently of unrelated additive feature tables.
    assert connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schedules'").fetchone() == ('schedules',)
    connection.close()
    health = config_dir.parent / 'schema-health.json'
    monkeypatch.setenv('WLR_HEALTH_FILE', str(health))
    monkeypatch.setenv('WLR_UPDATE_INSTANCE_ID', 'b7' * 24)
    module = boot_backend(config_dir)
    with pytest.raises(RuntimeError, match='worker_name'):
        with TestClient(module.app):
            pass
    assert not health.exists()


def test_managed_startup_detects_bad_schema_without_health_file(boot_backend):
    config_dir = profiles.prepare_profile('ADMIN', allow_new=True)
    module = boot_backend(config_dir)
    connection = sqlite3.connect(module.DB_PATH)
    connection.execute('ALTER TABLE schedules DROP COLUMN worker_name')
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeError, match='worker_name'):
        with TestClient(module.app):
            pass


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'cp949'])
def test_validate_profile_storage_reads_without_modification(tmp_path, encoding):
    shared = tmp_path / '공유 폴더'
    db = make_db(shared)
    config = profiles.profile_dir('ADMIN') / 'config'
    config.mkdir(parents=True)
    ini = config / 'config_admin.ini'
    ini.write_text(f'[NETWORK]\nshared_folder_path={shared}\n', encoding=encoding)
    before = ini.read_bytes(), digest(db)
    profiles.validate_profile_storage('ADMIN', config)
    assert (ini.read_bytes(), digest(db)) == before


@pytest.mark.parametrize('data_path', ['', 'relative/folder'])
def test_validate_profile_storage_rejects_blank_or_relative(tmp_path, data_path):
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'config_viewer.ini').write_text(f'[NETWORK]\nshared_folder_path={data_path}\n')
    with pytest.raises(UpdateError):
        profiles.validate_profile_storage('VIEWER', config)
    assert not list(tmp_path.rglob('smart_duty.db'))


def test_validate_profile_storage_detects_disappeared_original_db(tmp_path):
    shared = tmp_path / 'shared'
    db = make_db(shared)
    config = profiles.prepare_profile('VIEWER', shared_dir=shared)
    original_ini = (config / 'config_viewer.ini').read_bytes()
    db.rename(db.with_suffix('.original'))
    with pytest.raises(UpdateError):
        profiles.validate_profile_storage('VIEWER', config)
    assert not db.exists()
    assert (config / 'config_viewer.ini').read_bytes() == original_ini


def test_validate_profile_storage_rejects_corrupt_db_without_mutation(tmp_path):
    config = profiles.prepare_profile('ADMIN', allow_new=True)
    db = config / 'smart_duty.db'
    db.write_bytes(b'not a database')
    with pytest.raises(UpdateError):
        profiles.validate_profile_storage('ADMIN', config)
    assert db.read_bytes() == b'not a database'
