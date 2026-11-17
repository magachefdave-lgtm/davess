"""Viewer-first upgrade: prepare old shared data before reading its identity."""
import copy
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_backend_regression import load_app, SEED
from test_request_access_api import session, post, PREFIX
from backend import request_access_schema as schema


@pytest.fixture
def legacy_viewer(load_app, tmp_path, monkeypatch):
    admin = load_app('source')
    admin.save(copy.deepcopy(SEED))
    original = admin.submit('ABSENCE').json()
    assert admin.action(original['requestId']).status_code == 200
    baseline = admin.data()
    conn = admin.module.get_db_connection(readonly=False)
    with conn:
        for table in schema.PROTECTED_TABLES:
            conn.execute(f'DROP TABLE {table}')
        conn.execute('DROP INDEX idx_change_requests_uid')
        conn.execute('ALTER TABLE change_requests DROP COLUMN request_uid')
        conn.execute("DELETE FROM app_settings WHERE key LIKE 'request_access_%'")
    conn.close()
    viewer = load_app('source', mode='VIEWER', source_db=admin.module.DB_PATH)
    profile = tmp_path / 'private' / 'VIEWER'
    (profile / 'config').mkdir(parents=True)
    profile.chmod(0o700)
    monkeypatch.setattr(viewer.module, 'MANAGED_CONFIG', True)
    monkeypatch.setattr(viewer.module, 'CONFIG_DIR', str(profile / 'config'))
    viewer.client = TestClient(viewer.module.app, base_url='http://localhost')
    assert viewer.module.APP_MODE == 'VIEWER'
    with viewer.module.get_db_connection(readonly=True) as check:
        assert not check.execute("SELECT value FROM app_settings WHERE key='request_access_dataset_id'").fetchone()
    return viewer, original, baseline


@pytest.mark.parametrize('first', ['read', 'import', 'list', 'create', 'submit'])
def test_viewer_first_api_prepares_old_data_before_reading_identity(legacy_viewer, first):
    app, original, baseline = legacy_viewer
    headers = session(app)
    if first in ('read', 'import'):
        response = post(app, '/receipts/'+first, {'receipts':[original['receiptToken']]}, headers)
    elif first == 'list':
        response = app.client.get(PREFIX+'/recoveries', headers=headers)
    elif first == 'create':
        response = post(app, '/recoveries', {'requestedName':'A','operationId':str(uuid.uuid4())}, headers)
    else:
        response = post(app, '/submit', {'action':'SUBMIT','operationId':str(uuid.uuid4()),
            'request':{'requester_name':'A','request_type':'ABSENCE','target_date':'2030-07-01',
                       'absence_end_date':'2030-07-02','absence_type':'파견'}}, headers)
    assert response.status_code == 200, response.text
    with app.module.get_db_connection(readonly=True) as conn:
        identity = schema.read_dataset_identity(conn.cursor())
        row = conn.execute('SELECT status,receipt_token FROM change_requests WHERE id=?',(original['requestId'],)).fetchone()
        assert tuple(row) == ('APPROVED', original['receiptToken'])
    assert identity.dataset_id and identity.generation
    again = post(app, '/receipts/read', {}, headers)
    assert again.status_code == 200, again.text
    assert again.json()['datasetId'] == identity.dataset_id
    data = app.data()
    assert data['absences'] == baseline['absences']
    assert data['schedules'] == baseline['schedules']
    assert data['history'] == baseline['history']


@pytest.mark.parametrize('value', [None, '', '   '])
def test_empty_identifiers_are_prepared_without_replacing_existing_values(value):
    from test_request_access_schema import database
    conn = database()
    before = schema.read_dataset_identity(conn.cursor())
    conn.execute("UPDATE app_settings SET value=? WHERE key='request_access_generation'", (value,))
    schema.ensure_request_access_schema(conn.cursor())
    after = schema.read_dataset_identity(conn.cursor())
    assert after.dataset_id == before.dataset_id
    assert after.generation and after.generation.strip()
    assert schema.read_dataset_identity(conn.cursor()) == after


def test_identity_is_not_regenerated_when_existing_recovery_history_is_present():
    import hashlib
    from test_request_access_schema import database
    from backend.request_recovery import create_recovery
    conn = database()
    identity = schema.read_dataset_identity(conn.cursor())
    create_recovery(conn.cursor(), identity, 'Kim', 'device', hashlib.sha256(b'proof').hexdigest(), 'create', 1000)
    conn.execute("DELETE FROM app_settings WHERE key='request_access_dataset_id'")
    with pytest.raises(schema.AccessError, match='식별정보'):
        schema.ensure_request_access_schema(conn.cursor())
    assert not conn.execute("SELECT value FROM app_settings WHERE key='request_access_dataset_id'").fetchone()
    assert conn.execute('SELECT dataset_id FROM request_recoveries').fetchone()[0] == identity.dataset_id
