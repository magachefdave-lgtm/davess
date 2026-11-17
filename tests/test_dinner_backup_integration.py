"""Manual server backups retain dinner data and use the existing verified restore UI."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import secrets
import sqlite3

import pytest

from test_backend_regression import AUTH, load_app
from test_backend_manual_backup import server_app, backup, files, read_success
from test_dinner_api import assign, assign_year, key_headers, op, publish, send, KST
from datetime import datetime


def answered(app):
    poll = publish(app)
    key = secrets.token_urlsafe(32)
    result = send(app, '/polls/' + poll['id'] + '/response', op(memberName='A',
                  answers={'first': 'possible', 'second': 'difficult'}, suggestion='첫날 선호',
                  expectedRevision=poll['revision']), key=key, admin=False)
    assert result.status_code == 200, result.text
    return poll, key


def full_backup(app):
    response = backup(app)
    body, payload = read_success(app, response)
    assert body.get('fullBackupName'), 'Dinner data needs a complete SQLite backup in addition to duty JSON.'
    full = Path(body['fullBackupPath'])
    assert full.parent == Path(app.module.SHARED_PATH) / 'back-up'
    assert full.name == body['fullBackupName'] and full.suffix == '.db'
    assert app.module._verify_sqlite_file(str(full)) == ''
    return body, payload, full


def test_dinner_backup_keeps_poll_and_response_and_is_visible_in_restore_preview(server_app):
    app = server_app
    poll, key = answered(app)
    body, payload, full = full_backup(app)
    assert 'dinner_polls' not in payload
    with sqlite3.connect(full) as conn:
        assert conn.execute('SELECT COUNT(*) FROM dinner_polls').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM dinner_responses').fetchone()[0] == 1
    listing = app.client.get('/api/system/backups', headers=AUTH)
    assert listing.status_code == 200
    item = next(item for item in listing.json()['backups'] if item['name'] == full.name)
    assert item['kind'] == 'manual'
    assert '전체' in item['label']
    preview = app.client.get('/api/system/backup-preview', params={'name': full.name}, headers=AUTH)
    assert preview.status_code == 200 and preview.json()['ok'] is True
    assert preview.json()['backup']['counts']['dinner_polls'] == 1
    assert preview.json()['backup']['counts']['dinner_responses'] == 1
    assert key not in full.read_bytes().decode('utf-8', 'ignore')


def test_restore_manual_full_backup_recovers_original_response_and_revokes_ca_connection(server_app):
    app = server_app
    assign(app)
    ca_key = secrets.token_urlsafe(32)
    connection = send(app, '/connections', op(memberName='A', deviceLabel='CA PC'), key=ca_key, admin=False).json()['connection']
    approved = send(app, '/connections/' + connection['id'] + '/decision', op(decision='APPROVE', reason='확인'))
    assert approved.status_code == 200, approved.text
    poll, key = answered(app)
    body, _, full = full_backup(app)
    changed = send(app, '/polls/' + poll['id'], op(title='백업 뒤 변경된 제목', expectedRevision=poll['revision']), method='patch')
    assert changed.status_code == 200, changed.text
    revised = changed.json()['poll']
    amended = send(app, '/polls/' + poll['id'] + '/response', op(memberName='A', answers={'first':'difficult','second':'possible'},
                   suggestion='변경 의견', expectedRevision=revised['revision']), key=key, admin=False)
    assert amended.status_code == 200, amended.text
    restored = app.post('/api/system/restore', {'name': full.name, 'baseVersion': app.data()['dataVersion']})
    assert restored.status_code == 200, restored.text
    result = app.client.get('/api/dinner/polls/' + poll['id'], params={'name':'A'}, headers=key_headers(key)).json()['poll']
    assert result['title'] == poll['title']
    assert result['ownResponse']['answers'] == {'first':'possible','second':'difficult'}
    assert result['ownResponse']['suggestion'] == '첫날 선호'
    assert app.client.get('/api/dinner/manage', headers=key_headers(ca_key)).status_code == 403


def test_role_only_data_also_gets_full_backup(server_app):
    assign(server_app)
    _, _, full = full_backup(server_app)
    with sqlite3.connect(full) as conn:
        assert conn.execute('SELECT COUNT(*) FROM dinner_roles').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM dinner_polls').fetchone()[0] == 0


def test_annual_roles_backed_up_and_restore_preserves_latest_appointments(server_app):
    app = server_app
    year = datetime.now(KST).year
    assign_year(app, year)
    assign_year(app, year + 1, 'B')
    keys = [secrets.token_urlsafe(32), secrets.token_urlsafe(32)]
    for name, key in zip(('A', 'B'), keys):
        connection = send(app, '/connections', op(memberName=name), key=key, admin=False).json()['connection']
        assert send(app, '/connections/' + connection['id'] + '/decision', op(decision='APPROVE')).status_code == 200
    _, _, full = full_backup(app)
    with sqlite3.connect(full) as conn:
        assert conn.execute('SELECT COUNT(*) FROM dinner_appointments').fetchone()[0] == 2
        assert conn.execute('SELECT COUNT(DISTINCT role_id) FROM dinner_connections').fetchone()[0] == 2
    assign_year(app, year + 1, 'C', revision=1)
    restored = app.post('/api/system/restore', {'name': full.name, 'baseVersion': app.data()['dataVersion']})
    assert restored.status_code == 200, restored.text
    managed = app.client.get('/api/dinner/manage', headers=AUTH).json()
    assert [(r['roleId'], r['memberName']) for r in managed['roles']] == [(year, 'A'), (year + 1, 'C')]
    assert all(c['status'] == 'INVALIDATED' for c in managed['connections'])
    assert app.client.get('/api/dinner/manage', headers=key_headers(keys[0])).status_code == 403


@pytest.mark.parametrize('failure', ['verify_full', 'publish_json', 'partial_full'])
def test_failed_pair_creation_cleans_new_files_and_keeps_existing_backups(server_app, monkeypatch, failure):
    app = server_app
    answered(app)
    full_backup(app)
    folder = Path(app.module.SHARED_PATH) / 'back-up'
    before = files(folder)
    current = app.data()
    if failure == 'verify_full':
        monkeypatch.setattr(app.module, '_verify_sqlite_file', lambda path: 'synthetic corrupt backup')
    elif failure == 'publish_json':
        original = app.module.os.replace
        def replace(source, target):
            if Path(target).parent == folder and Path(target).suffix == '.json':
                raise OSError('synthetic JSON publication failure after full snapshot')
            return original(source, target)
        monkeypatch.setattr(app.module.os, 'replace', replace)
    else:
        def partial(target):
            Path(target).write_bytes(b'incomplete')
            raise OSError('synthetic snapshot failure')
        monkeypatch.setattr(app.module, '_snapshot_to', partial)
    result = backup(app)
    assert result.status_code == 503, result.text
    assert files(folder) == before
    assert app.data() == current


def test_concurrent_dinner_backups_create_complete_distinct_pairs(server_app):
    app = server_app
    answered(app)
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda _: full_backup(app), range(3)))
    assert len({body['name'] for body, _, _ in results}) == 3
    assert len({body['fullBackupName'] for body, _, _ in results}) == 3
    assert len(files(Path(app.module.SHARED_PATH) / 'back-up')) == 6


@pytest.mark.parametrize('name', ['../smart_duty_full_20300101_000000_000000_1234567890abcdef.db',
                                'smart_duty_full_20300101_000000_000000_1234567890abcdef.db.extra',
                                'smart_duty_full_arbitrary.db', 'smart_duty_full_20300101_000000_000000_1234567890abcdef.db\n'])
def test_manual_restore_requires_exact_registered_filename(server_app, name):
    result = server_app.client.get('/api/system/backup-preview', params={'name':name}, headers=AUTH)
    assert result.status_code == 400


def test_manual_backup_symlink_and_wrong_folder_do_not_escape_restore_directory(server_app, tmp_path):
    app = server_app
    answered(app)
    _, _, full = full_backup(app)
    outside = tmp_path / 'outside.db'
    full.rename(outside)
    full.symlink_to(outside)
    result = app.client.get('/api/system/backup-preview', params={'name':full.name}, headers=AUTH)
    assert result.status_code == 400
    names = {row['name'] for row in app.client.get('/api/system/backups', headers=AUTH).json()['backups']}
    assert full.name not in names
    full.unlink()
    outside.rename(Path(app.module.backup_dir_path()) / full.name)
    result = app.client.get('/api/system/backup-preview', params={'name':full.name}, headers=AUTH)
    assert result.status_code == 404
