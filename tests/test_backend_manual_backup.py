"""Server-side manual JSON backups; synthetic databases and separate client/server paths."""
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3

import pytest
import test_backend_regression as regression
from test_backend_regression import AUTH, DAY_A, DAY_B, SEED, load_app  # noqa: F401

URL = '/api/system/backup'
DATA_KEYS = ('members', 'schedules', 'comments', 'noDuty', 'absences', 'absenceTypes',
             'customHolidays', 'rules', 'globalMemo')


@pytest.fixture(autouse=True)
def source_override(monkeypatch):
    if os.environ.get('WLR_REGRESSION_SOURCE'):
        monkeypatch.setattr(regression, 'SOURCE', Path(os.environ['WLR_REGRESSION_SOURCE']))


@pytest.fixture
def server_app(load_app, tmp_path, monkeypatch):
    # The running program's local profile is deliberately different from the shared server.
    monkeypatch.delenv('WLR_CONFIG_DIR', raising=False)
    app = load_app('source')
    server = tmp_path / '서버 데이터 공백'
    server.mkdir()
    shutil.copy2(app.module.DB_PATH, server / 'smart_duty.db')
    monkeypatch.setattr(app.module, 'SHARED_PATH', str(server))
    monkeypatch.setattr(app.module, 'STORAGE_DIR', str(server))
    monkeypatch.setattr(app.module, 'DB_PATH', str(server / 'smart_duty.db'))
    fixture = copy.deepcopy(SEED)
    fixture['absenceTypes'].append('교육')
    fixture['absences'] = [{'id': 31, 'name': 'B', 'type': '교육', 'start': DAY_A, 'end': DAY_B,
                            'education': {'title': '합성 교육', 'content': '첫 줄\n둘째 줄'}}]
    app.save(fixture)
    assert Path(app.module.CONFIG_DIR) != server
    return app


def files(folder):
    folder = Path(folder)
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob('*') if p.is_file()} if folder.exists() else {}


def backup(app):
    # No browser state or destination is sent to the server.
    return app.client.post(URL, headers=AUTH)


def read_success(app, response):
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['ok'] is True
    target = Path(body['path'])
    assert target.parent == Path(app.module.SHARED_PATH) / 'back-up'
    assert target.name == body['name']
    assert target.name.startswith('smart_duty_backup_') and target.suffix == '.json'
    assert body['byteSize'] == target.stat().st_size > 0
    data = json.loads(target.read_text(encoding='utf-8'))
    assert data['formatVersion'] == 2
    assert data['exportedAt'] == body['exportedAt']
    assert datetime.fromisoformat(body['exportedAt'].replace('Z', '+00:00'))
    return body, data


def test_backup_creates_server_folder_from_latest_database_and_preserves_data(server_app):
    app = server_app
    cached = app.data()
    app.save({'globalMemo': '다른 PC가 저장한 최신 공지'})
    current = app.data()
    automatic_before = files(Path(app.module.SHARED_PATH) / 'backups')
    assert automatic_before, 'fixture includes an existing automatic backup'
    server_before = files(app.module.SHARED_PATH)
    body, data = read_success(app, backup(app))
    assert data['globalMemo'] != cached['globalMemo']
    for key in DATA_KEYS:
        assert data[key] == current[key], key
    assert data['history'] == current['history']
    assert body['dataVersion'] == current['dataVersion']
    assert app.data() == current
    assert files(Path(app.module.SHARED_PATH) / 'backups') == automatic_before
    assert all(files(app.module.SHARED_PATH)[name] == digest for name, digest in server_before.items())
    assert not (Path(app.module.CONFIG_DIR) / 'back-up').exists()
    assert sorted(files(Path(app.module.SHARED_PATH) / 'back-up')) == [body['name']]


def test_backup_json_can_be_restored_through_existing_save_contract(server_app, load_app):
    _, data = read_success(server_app, backup(server_app))
    target = load_app('source')
    payload = {key: copy.deepcopy(data[key]) for key in DATA_KEYS}
    payload['historyBulk'] = copy.deepcopy(data['history'])
    payload['baseVersion'] = target.data()['dataVersion']
    dto = target.module.SyncFullDataDTO(**payload)
    with target.module.get_db_connection() as conn:
        target.module.validate_save_payload(dto, conn.cursor())
    target.save(payload)
    restored = target.data()
    for key in DATA_KEYS:
        assert restored[key] == data[key], key
    assert len(restored['history']) >= len(data['history'])


def test_export_contains_full_history_while_public_screen_keeps_500_limit(server_app):
    app = server_app
    with sqlite3.connect(app.module.DB_PATH) as conn:
        conn.executemany('INSERT INTO change_history (timestamp,type,date_key,details) VALUES (?,?,?,?)',
                         [('2030-06-01 12:00:00', '합성 이력', DAY_A, f'행 {i}') for i in range(620)])
        total = conn.execute('SELECT COUNT(*) FROM change_history').fetchone()[0]
        ids = [row[0] for row in conn.execute('SELECT id FROM change_history ORDER BY id DESC')]
    current = app.data()
    assert len(current['history']) == 500
    body, data = read_success(app, backup(app))
    assert len(data['history']) == total > 500
    assert [item['id'] for item in data['history']] == ids
    assert body['dataVersion'] == current['dataVersion']
    assert app.data() == current


def test_concurrent_manual_backups_have_distinct_complete_files(server_app):
    app = server_app
    current = app.data()
    with ThreadPoolExecutor(max_workers=6) as executor:
        responses = list(executor.map(lambda _: backup(app), range(6)))
    results = [read_success(app, response) for response in responses]
    assert len({body['name'] for body, _ in results}) == 6
    assert len(files(Path(app.module.SHARED_PATH) / 'back-up')) == 6
    for body, data in results:
        assert body['dataVersion'] == current['dataVersion']
        assert data['globalMemo'] == current['globalMemo']
    assert app.data() == current


@pytest.mark.parametrize('failure', ['publication', 'flush'])
def test_failed_write_leaves_no_partial_files_or_changes_to_existing_backup(server_app, monkeypatch, failure):
    app = server_app
    read_success(app, backup(app))
    folder = Path(app.module.SHARED_PATH) / 'back-up'
    before = files(app.module.SHARED_PATH)
    current = app.data()
    if failure == 'publication':
        original = app.module.os.replace
        def fail_backup_replace(source, destination):
            if Path(destination).parent == folder:
                raise OSError('synthetic publication failure')
            return original(source, destination)
        monkeypatch.setattr(app.module.os, 'replace', fail_backup_replace)
    else:
        def fail_fsync(fd):
            raise OSError('synthetic disk flush failure')
        monkeypatch.setattr(app.module.os, 'fsync', fail_fsync)
    response = backup(app)
    assert response.status_code in (500, 503), response.text
    assert files(app.module.SHARED_PATH) == before
    assert app.data() == current
    assert not (Path(app.module.CONFIG_DIR) / 'back-up').exists()


def test_missing_shared_path_does_not_fall_back_to_local_profile(server_app, monkeypatch):
    app = server_app
    profile = Path(app.module.CONFIG_DIR)
    monkeypatch.setattr(app.module, 'SHARED_PATH', '')
    monkeypatch.setattr(app.module, 'STORAGE_DIR', str(profile))
    monkeypatch.setattr(app.module, 'DB_PATH', str(profile / 'smart_duty.db'))
    before_db = (profile / 'smart_duty.db').read_bytes()
    response = backup(app)
    assert response.status_code == 503, response.text
    assert not (profile / 'back-up').exists()
    assert (profile / 'smart_duty.db').read_bytes() == before_db


def test_relative_shared_path_is_rejected_even_if_current_db_is_available(server_app, monkeypatch):
    app = server_app
    original_server = Path(app.module.SHARED_PATH)
    before = files(original_server)
    monkeypatch.setattr(app.module, 'SHARED_PATH', 'relative-server')
    response = backup(app)
    assert response.status_code == 503, response.text
    assert files(original_server) == before
    assert not (Path(app.module.CONFIG_DIR) / 'back-up').exists()


def test_disconnected_shared_database_does_not_create_replacement_or_backup(server_app):
    app = server_app
    db = Path(app.module.DB_PATH)
    db.rename(db.with_suffix('.offline'))
    before = files(db.parent)
    response = backup(app)
    assert response.status_code == 503, response.text
    assert not db.exists()
    assert not (db.parent / 'back-up').exists()
    assert files(db.parent) == before


@pytest.mark.parametrize('mode,headers,expected', [('VIEWER', AUTH, 403), ('ADMIN', {}, 401),
                                                   ('ADMIN', {'X-Admin-Token': 'wrong'}, 401)])
def test_backup_requires_admin_mode_and_valid_token_before_any_file_creation(server_app, monkeypatch, mode, headers, expected):
    app = server_app
    monkeypatch.setattr(app.module, 'APP_MODE', mode)
    before = files(app.module.SHARED_PATH)
    response = app.client.post(URL, headers=headers)
    assert response.status_code == expected, response.text
    assert files(app.module.SHARED_PATH) == before
    assert not (Path(app.module.CONFIG_DIR) / 'back-up').exists()


def test_cross_site_post_cannot_create_backup(server_app):
    app = server_app
    before = files(app.module.SHARED_PATH)
    response = app.client.post(URL, headers={**AUTH, 'Origin': 'https://unrelated.example'})
    assert response.status_code == 403
    assert files(app.module.SHARED_PATH) == before


def test_backup_version_and_contents_are_from_one_read_snapshot(server_app, monkeypatch):
    app = server_app
    module = app.module
    initial = app.data()
    original = module.read_data_version
    attempted = {'value': False}

    def version_then_other_pc_writes(cursor):
        version = original(cursor)
        if not attempted['value']:
            attempted['value'] = True
            other = sqlite3.connect(module.DB_PATH, timeout=0.1)
            try:
                other.execute("UPDATE app_settings SET value='CONCURRENT_NEW_MEMO' WHERE key='global_memo'")
                other.execute("INSERT INTO schedules (date_key,slot_index,worker_name,role) VALUES ('2030-06-26',0,'C','당직')")
                other.execute("UPDATE app_settings SET value=CAST(value AS INTEGER)+1 WHERE key='data_version'")
                other.commit()
            except sqlite3.OperationalError:
                # A read transaction can legitimately keep the other writer waiting.
                other.rollback()
            finally:
                other.close()
        return version

    monkeypatch.setattr(module, 'read_data_version', version_then_other_pc_writes)
    body, data = read_success(app, backup(app))
    assert attempted['value']
    contains_new = data['globalMemo'] == 'CONCURRENT_NEW_MEMO'
    assert ('2030-06-26' in data['schedules']) == contains_new
    assert body['dataVersion'] == initial['dataVersion'] + int(contains_new)
    if not contains_new:
        assert data['globalMemo'] == initial['globalMemo']
