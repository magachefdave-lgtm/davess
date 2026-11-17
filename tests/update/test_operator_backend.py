"""3.18.0 [운영] /api/system/update-status : 직원 PC 업데이트 상태 · 이 PC 운영 동작 · 자주 쓰는 설정.

실행기(launcher)가 만든 프로필 구조(…/ADMIN/config/config_admin.ini, …/ADMIN/update_status.json)를
임시 폴더에 흉내 내고, 백엔드 소스를 그대로 올려 시험한다. 운영 DB 는 쓰지 않는다.
"""
import json
from pathlib import Path
import sqlite3
import sys
import types
import uuid

import pytest
from fastapi.testclient import TestClient

SOURCE = Path(__file__).resolve().parents[2] / 'backend' / 'main.py'


@pytest.fixture
def backend(tmp_path, monkeypatch):
    def load(password='', mode='ADMIN', bind='127.0.0.1'):
        profile = tmp_path / uuid.uuid4().hex / mode
        config = profile / 'config'
        data = tmp_path / 'data'
        config.mkdir(parents=True)
        data.mkdir(exist_ok=True)
        sqlite3.connect(data / 'smart_duty.db').close()
        (config / f'config_{mode.lower()}.ini').write_text(
            f'[NETWORK]\nshared_folder_path = {data}\nopen_browser = false\ntray_icon = false\n'
            f'bind_host = {bind}\nadmin_password = {password}\n; 주석은 남아야 한다\n', encoding='utf-8')
        monkeypatch.setenv('SMART_DUTY_MODE', mode)
        monkeypatch.setenv('WLR_CONFIG_DIR', str(config))
        name = 'onair_opstatus_' + uuid.uuid4().hex
        module = types.ModuleType(name)
        module.__file__ = str(SOURCE)
        sys.modules[name] = module
        exec(compile(SOURCE.read_text(encoding='utf-8-sig'), str(SOURCE), 'exec'), module.__dict__)
        module.init_db()
        return module, TestClient(module.app), profile
    return load


def test_get_reports_launcher_status_and_settings(backend):
    module, client, profile = backend()
    response = client.get('/api/system/update-status')
    assert response.status_code == 200
    body = response.json()
    assert body['managed'] is True and body['status'] is None and body['local'] is True
    assert body['settings'] == {'admin_password_set': False, 'server_port': '', 'allowed_hosts': '',
                                'backup_mirror_dir': ''}
    (profile / 'update_status.json').write_text(json.dumps(
        {'schema': 1, 'checked_at': '2026-10-14T09:00:00+09:00', 'result': 'check_failed', 'notice': True,
         'message': '업데이트 폴더를 확인하지 못해…', 'current_version': '3.17.13', 'available_version': None,
         'log_path': str(profile / 'launcher.log')}, ensure_ascii=False), encoding='utf-8')
    status = client.get('/api/system/update-status').json()['status']
    assert status['result'] == 'check_failed' and status['notice'] is True and status['log_path']


def test_remote_caller_cannot_run_local_actions(backend, monkeypatch):
    module, client, profile = backend()
    monkeypatch.setattr(module, 'is_loopback_client', lambda request: False)
    assert client.post('/api/system/update-status', json={'action': 'reset_blocked'}).status_code == 403
    assert not (profile / 'reset_blocked.request').exists()
    body = client.get('/api/system/update-status').json()
    assert body['settings'] is None and body['canOpen'] is False


def test_password_is_required_when_set(backend):
    module, client, profile = backend(password='synthetic-pass')
    assert client.get('/api/system/update-status').status_code == 401
    assert client.post('/api/system/update-status', json={'action': 'reset_blocked'}).status_code == 401
    ok = client.post('/api/system/update-status', json={'action': 'reset_blocked'},
                     headers={'X-Admin-Token': 'synthetic-pass'})
    assert ok.status_code == 200 and (profile / 'reset_blocked.request').exists()


def test_save_settings_writes_ini_with_backup_and_restart_notice(backend):
    module, client, profile = backend()
    ini = profile / 'config' / 'config_admin.ini'
    response = client.post('/api/system/update-status',
                           json={'action': 'save_settings', 'settings': {'server_port': '8010', 'admin_password': 'new pw'}})
    assert response.status_code == 200 and '다시 시작해야 적용' in response.json()['message']
    text = ini.read_text(encoding='utf-8')
    assert 'server_port = 8010' in text and 'admin_password = new pw' in text and '; 주석은 남아야 한다' in text
    assert list(ini.parent.glob('config_admin.ini.bak-*'))
    assert module.ADMIN_PASSWORD == ''                         # 실행 중인 프로그램에는 다시 시작 전까지 적용 안 됨
    bad = client.post('/api/system/update-status', json={'action': 'save_settings', 'settings': {'server_port': '1'}})
    assert bad.status_code == 400 and '1024' in bad.json()['detail']


def test_open_actions_are_windows_only_and_unknown_action_rejected(backend, monkeypatch):
    module, client, profile = backend()
    if module.os.name != 'nt':
        for action in ('open_logs', 'open_config'):
            response = client.post('/api/system/update-status', json={'action': action})
            assert response.status_code == 400 and 'Windows' in response.json()['detail']
    assert client.post('/api/system/update-status', json={'action': 'rm -rf'}).status_code == 400


def test_open_logs_uses_explorer_on_windows(backend, monkeypatch):
    module, client, profile = backend()
    opened = []
    monkeypatch.setattr(module.os, 'name', 'nt')
    monkeypatch.setattr(module.os, 'startfile', lambda path: opened.append(path), raising=False)
    response = client.post('/api/system/update-status', json={'action': 'open_logs'})
    assert response.status_code == 200 and opened == [str(profile)]


def test_frontend_hooks_update_status_into_connection_info():
    root = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
    page = (root / 'index.html').read_text(encoding='utf-8')
    assert '<div id="serverinfo-update"' in page and '<script src="/update_status.js"></script>' in page
    script = (root / 'update_status.js').read_text(encoding='utf-8')
    for words in ('업데이트 상태', '로그 폴더 열기', '설정 파일 열기', '차단된 업데이트 다시 허용',
                  '프로그램을 다시 시작해야 적용됩니다', 'onair-update-notice-seen'):
        assert words in script


def test_viewer_settings_hide_and_reject_admin_only_fields(backend):
    """3.18.0 QA: 조회용 [연결 정보]에 관리자 암호·추가 백업 폴더가 보였고, 저장도 받았다."""
    module, client, profile = backend(mode='VIEWER')
    body = client.get('/api/system/update-status').json()
    assert body['mode'] == 'VIEWER'
    assert body['settings'] == {'server_port': ''}            # 루프백으로만 열리면 허용할 접속 이름도 감춘다
    ini = profile / 'config' / 'config_viewer.ini'
    before = ini.read_text(encoding='utf-8')
    for settings in ({'admin_password': 'x'}, {'backup_mirror_dir': '/tmp/m'}, {'server_port': '8011', 'admin_password': ''}):
        response = client.post('/api/system/update-status', json={'action': 'save_settings', 'settings': settings})
        assert response.status_code == 403 and '조회용' in response.json()['detail']
    assert ini.read_text(encoding='utf-8') == before
    ok = client.post('/api/system/update-status', json={'action': 'save_settings', 'settings': {'server_port': '8011'}})
    assert ok.status_code == 200 and 'server_port = 8011' in ini.read_text(encoding='utf-8')


def test_viewer_open_to_network_still_shows_allowed_hosts(backend):
    module, client, profile = backend(mode='VIEWER', bind='0.0.0.0', password='synthetic-pass')
    body = client.get('/api/system/update-status', headers={'X-Admin-Token': 'synthetic-pass'}).json()
    assert set(body['settings']) == {'server_port', 'allowed_hosts'}


def test_admin_settings_are_unchanged(backend):
    module, client, profile = backend()
    body = client.get('/api/system/update-status').json()
    assert body['mode'] == 'ADMIN' and set(body['settings']) == {'admin_password_set', 'server_port', 'allowed_hosts', 'backup_mirror_dir'}


def test_live_check_local_only_and_password(backend, monkeypatch):
    module, client, profile = backend(password='secret')
    from updater.manual_update import live_check
    calls = []
    monkeypatch.setattr(live_check, 'start', lambda *args: calls.append(args))
    assert client.post('/api/system/update-status', json={'action':'check_now'}).status_code == 401
    assert not calls
    assert client.post('/api/system/update-status', json={'action':'check_now'}, headers={'x-admin-token':'secret'}).status_code == 200
    assert len(calls) == 1
    monkeypatch.setattr(module, 'is_loopback_client', lambda request: False)
    assert client.post('/api/system/update-status', json={'action':'check_now'}, headers={'x-admin-token':'secret'}).status_code == 403
    assert len(calls) == 1
