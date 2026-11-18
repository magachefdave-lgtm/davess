"""The shipped Master authority using a temporary shared dataset and local profile."""
from dataclasses import replace
from pathlib import Path
import logging
import runpy
import sqlite3
import sys
import uuid

from fastapi.testclient import TestClient
import pytest

from backend import master_auth
from test_backend_regression import load_app


ORIGIN = 'https://localhost:8002'
PASSWORD = 'test-only master password'
UNLOCK = '/api/master/unlock'
STATUS = '/api/master/session'
LOCK = '/api/master/lock'


@pytest.fixture
def master(load_app, tmp_path, monkeypatch):
    app = load_app('source', mode='MASTER')
    profile = tmp_path / 'local-master'
    (profile / 'config').mkdir(parents=True)
    profile.chmod(0o700)
    monkeypatch.setattr(app.module, 'MANAGED_CONFIG', True)
    monkeypatch.setattr(app.module, 'CONFIG_DIR', str(profile / 'config'))
    app.client = TestClient(app.module.app, base_url=ORIGIN)
    return app


def launched(master):
    ticket = master.module.REQUEST_ACCESS.issue_ticket(ORIGIN)
    exchange = master.client.post('/api/local/request-access/session',
                                  headers={'origin': ORIGIN}, json={'ticket': ticket})
    assert exchange.status_code == 200
    return {'origin': ORIGIN, 'x-onair-local-session': exchange.json()['sessionToken']}


def approve(master):
    headers = launched(master)
    device = master.client.get(STATUS, headers=headers).json()['deviceId']
    code = master_auth.provision(master.module.DB_PATH, device)
    return headers, device, code


def unlock(master, headers, **kwargs):
    return master.client.post(UNLOCK, headers=headers, json={'password': PASSWORD, **kwargs})


def test_distribution_roles_and_no_implicit_enrollment(master, load_app):
    module = master.module
    info = master.client.get('/api/system/info').json()
    assert (info['mode'], info['distributionRole'], info['effectiveMode']) == ('MASTER', 'MASTER', 'ADMIN')
    assert master.client.get('/api/duty/all-data').json()['mode'] == 'ADMIN'
    assert module.CONFIG_NAME == 'config_master.ini' and module.SERVER_PORT == 8002
    assert Path(module.LOG_PATH).name == 'smart_duty_master.log'
    assert module.startup_health_payload()['mode'] == 'MASTER'
    assert master.client.get(STATUS).json()['deviceId'] is None
    headers = launched(master)
    result = master.client.get(STATUS, headers=headers).json()
    assert result['available'] and result['deviceId']
    assert not any(result[k] for k in ('active', 'enrolled', 'grantPending'))
    assert unlock(master, headers, enrollmentCode='unissued').status_code == 403
    assert not Path(module.DB_PATH).with_name(master_auth.SIDECAR).exists()
    for role in ('ADMIN', 'VIEWER'):
        app = load_app('source', mode=role)
        assert app.client.get(STATUS).json()['available'] is False
        assert app.client.post(UNLOCK, headers=headers, json={'password': PASSWORD}).status_code == 403


def test_logs_omit_master_credentials():
    record = logging.LogRecord('test', logging.INFO, __file__, 1,
                               'POST /api/master/unlock?password=secret '
                               'body=%s Cookie=onair_master=secret-cookie',
                               ({'password': 'two words', 'enrollmentCode': 'secret-code'},), None)
    master_auth.PrivateAccessLogFilter().filter(record)
    assert '/api/master/unlock' in record.getMessage()
    for secret in ('secret', 'two words'):
        assert secret not in record.getMessage()


def test_bound_one_use_enrollment_password_and_revocation(master):
    headers, device, code = approve(master)
    state = master.client.get(STATUS, headers=headers).json()
    assert state['grantPending'] and not state['enrolled']
    assert unlock(master, headers, enrollmentCode=code, password=master.module.ADMIN_PASSWORD).status_code == 400
    assert unlock(master, headers, enrollmentCode=code, password='short').status_code == 400
    response = unlock(master, headers, enrollmentCode=code)
    assert response.status_code == 200, response.text
    assert response.json()['active'] and not response.json()['grantPending']
    assert all(t in response.headers['set-cookie'] for t in ('HttpOnly', 'Secure', 'SameSite=strict'))
    assert master.client.get(STATUS, headers=headers).json()['active']
    assert not master.client.get(STATUS).json()['active']
    assert not master.client.get(STATUS, headers=launched(master)).json()['active']
    assert master.client.post(LOCK, headers=headers, json={}).json()['active'] is False
    assert unlock(master, headers, enrollmentCode=code).status_code == 403
    assert unlock(master, headers, password='another test password').status_code == 403
    assert unlock(master, headers).json()['active']
    master_auth.provision(master.module.DB_PATH, device, revoke=True)
    assert not master.client.get(STATUS, headers=headers).json()['active']
    assert unlock(master, headers).status_code == 403
    # No registration code or password is persisted in the DB or app logs.
    for path in (Path(master.module.DB_PATH).with_name(master_auth.SIDECAR), Path(master.module.LOG_PATH)):
        raw = path.read_bytes()
        assert code.encode() not in raw and PASSWORD.encode() not in raw


def test_wrong_device_session_origin_and_generation(master):
    headers = launched(master)
    other = master_auth.provision(master.module.DB_PATH, str(uuid.uuid4()))
    assert unlock(master, headers, enrollmentCode=other).status_code == 403
    headers, _, code = approve(master)
    no_origin = {k: v for k, v in headers.items() if k != 'origin'}
    assert unlock(master, no_origin, enrollmentCode=code).status_code == 403
    assert unlock(master, dict(headers, origin='https://example.org'), enrollmentCode=code).status_code == 403
    assert unlock(master, headers, enrollmentCode=code).json()['active']
    with master.module.REQUEST_ACCESS.db(write=True) as cur:
        cur.execute("UPDATE app_settings SET value=? WHERE key='request_access_generation'",
                    (str(uuid.uuid4()),))
    assert not master.client.get(STATUS, headers=headers).json()['active']
    assert unlock(master, headers).status_code == 403


def test_throttling_survives_restart_and_enrollment_expiry(master):
    headers, _, code = approve(master)
    for i in range(master_auth.FAILURE_LIMIT):
        assert unlock(master, headers, enrollmentCode='incorrect').status_code == 403
    assert unlock(master, headers, enrollmentCode=code).status_code == 429
    fresh = master_auth.MasterAuthority(master.module.MASTER.h)
    assert fresh._session is None
    with sqlite3.connect(Path(master.module.DB_PATH).with_name(master_auth.SIDECAR)) as conn:
        row = conn.execute('SELECT locked_until FROM master_devices').fetchone()
        assert row[0] > 0
        conn.execute('UPDATE master_devices SET locked_until=0, code_expires=0')
    assert unlock(master, headers, enrollmentCode=code).status_code == 403
    assert not master.client.get(STATUS, headers=headers).json()['grantPending']


def test_windows_session_events_relogin_restart_and_expiry(master):
    headers, _, code = approve(master)
    assert unlock(master, headers, enrollmentCode=code).json()['active']
    authority = master.module.MASTER
    authority.workstation_changed(True)
    assert not master.client.get(STATUS, headers=headers).json()['active']
    assert unlock(master, headers).status_code == 403
    authority.workstation_changed(False)
    assert not master.client.get(STATUS, headers=headers).json()['active']
    assert unlock(master, headers).json()['active']
    authority._session = replace(authority._session, deadline=0)
    assert not master.client.get(STATUS, headers=headers).json()['active']
    assert unlock(master, headers).json()['active']
    # The same browser can keep its cookies across a server stop/start.
    with TestClient(master.module.app, base_url=ORIGIN) as restarted:
        restarted.cookies.update(master.client.cookies)
        assert restarted.get(STATUS, headers=headers).json()['active']
    assert not master.client.get(STATUS, headers=headers).json()['active']


@pytest.mark.parametrize(('compiled_role', 'filename', 'env_role', 'expected'), [
    ('ADMIN', 'WLR_OnAir_Master.exe', 'MASTER', 'ADMIN'),
    ('VIEWER', 'WLR_OnAir_Master.exe', 'MASTER', 'VIEWER'),
    (None, 'WLR_OnAir_Master.exe', 'MASTER', 'VIEWER'),
    ('MASTER', 'WLR_OnAir_Viewer.exe', 'VIEWER', 'MASTER'),
])
def test_frozen_backend_uses_compiled_role_only(tmp_path, monkeypatch, compiled_role, filename,
                                               env_role, expected):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / filename))
    monkeypatch.setenv('SMART_DUTY_MODE', env_role)
    entry = Path(__file__).resolve().parents[1] / 'backend' / 'main.py'
    state = runpy.run_path(str(entry), init_globals={'BUILD_ROLE': compiled_role})
    assert state['DISTRIBUTION_ROLE'] == expected
    assert state['CONFIG_NAME'] == f'config_{expected.lower()}.ini'
