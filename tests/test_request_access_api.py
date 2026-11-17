"""End-to-end access recovery through real FastAPI and synthetic databases."""
import copy
import uuid

import pytest
from test_backend_regression import load_app, SEED, AUTH
from test_backend_amendments import approve_original, payload
from fastapi.testclient import TestClient

PREFIX = '/api/local/request-access'


@pytest.fixture
def site(load_app, tmp_path, monkeypatch):
    app = load_app('source')
    app.save(copy.deepcopy(SEED))
    app.save({'absenceTypes': ['연차', '교육', '파견']})
    profile = tmp_path / 'profile' / 'VIEWER'
    (profile / 'config').mkdir(parents=True)
    profile.chmod(0o700)
    monkeypatch.setattr(app.module, 'MANAGED_CONFIG', True)
    monkeypatch.setattr(app.module, 'CONFIG_DIR', str(profile / 'config'))
    app.client = TestClient(app.module.app, base_url='http://localhost')
    return app


def session(site):
    ticket = site.module.REQUEST_ACCESS.issue_ticket('http://localhost')
    response = site.client.post(PREFIX + '/session', json={'ticket': ticket},
                                headers={'Origin': 'http://localhost'})
    assert response.status_code == 200, response.text
    return {'Origin': 'http://localhost', 'X-OnAir-Local-Session': response.json()['sessionToken']}


def test_admin_recovery_notification_summary_tracks_only_live_pending_requests(site, monkeypatch):
    headers = session(site)
    first = post(site, '/recoveries', {'requestedName': 'A', 'operationId': str(uuid.uuid4())}, headers).json()
    second = post(site, '/recoveries', {'requestedName': 'B', 'operationId': str(uuid.uuid4())}, headers).json()
    def summary():
        return site.client.get('/api/system/info', headers=AUTH).json()['requestRecovery']
    value = summary()
    assert set(value['pendingIds']) == {first['id'], second['id']}
    assert value['datasetKey']
    assert set(value) == {'datasetKey', 'pendingIds'}
    assert site.client.get('/api/system/info', headers=AUTH).json()['pendingCount'] == 0
    assert site.client.get('/api/duty/request-recoveries', headers=AUTH).json()['notifications'] == value
    post(site, '/recoveries/' + first['id'] + '/cancel', {'operationId': str(uuid.uuid4())}, headers)
    assert summary()['pendingIds'] == [second['id']]
    with site.module.REQUEST_ACCESS.db(write=True) as cur:
        cur.execute('UPDATE request_recoveries SET expires_at=0 WHERE id=?', (second['id'],))
    assert summary()['pendingIds'] == []
    monkeypatch.setattr(site.module, 'APP_MODE', 'VIEWER')
    assert summary() is None


def test_recovery_notification_summary_does_not_disclose_to_remote_anonymous_client(site):
    headers = session(site)
    post(site, '/recoveries', {'requestedName': 'A', 'operationId': str(uuid.uuid4())}, headers)
    remote = TestClient(site.module.app, client=('192.0.2.1', 1234))
    assert remote.get('/api/system/info').json()['requestRecovery'] is None
    assert len(remote.get('/api/system/info', headers=AUTH).json()['requestRecovery']['pendingIds']) == 1


def post(site, suffix, body, headers):
    return site.client.post(PREFIX + suffix, json=body, headers=headers)


def test_unguarded_localhost_cannot_read_vault(site):
    response = post(site, '/receipts/read', {}, {'Origin': 'http://localhost'})
    assert response.status_code == 403


def test_new_submission_replay_and_receipt_survive_browser_loss(site):
    headers = session(site)
    operation = str(uuid.uuid4())
    body = {'operationId': operation, 'action': 'SUBMIT', 'request': payload()}
    first = post(site, '/submit', body, headers)
    assert first.status_code == 200, first.text
    second = post(site, '/submit', body, headers)
    assert second.status_code == 200, second.text
    assert first.json()['requestId'] == second.json()['requestId']
    assert first.json()['receiptToken'] == second.json()['receiptToken']
    assert len(site.rows('change_requests')) == 1
    receipts = post(site, '/receipts/read', {}, headers).json()['receipts']
    assert receipts[0]['receipt_token'] == first.json()['receiptToken']
    changed = {**body, 'request': payload(reason='different')}
    assert post(site, '/submit', changed, headers).status_code == 409


def test_approved_recovery_preserves_schedule_then_allows_reapproval(site):
    original = approve_original(site)
    before = site.data()
    headers = session(site)
    created = post(site, '/recoveries', {'requestedName': 'A', 'operationId': str(uuid.uuid4())}, headers)
    assert created.status_code == 200, created.text
    rid = created.json()['id']
    claim = {'operationId': str(uuid.uuid4())}
    assert post(site, f'/recoveries/{rid}/claim', claim, headers).status_code == 409
    candidates = site.client.get(f'/api/duty/request-recoveries/{rid}/candidates', headers=AUTH)
    assert candidates.status_code == 200, candidates.text
    roots = [candidates.json()['groups'][0]['rootUid']]
    base = f'/api/duty/request-recoveries/{rid}'
    preview = site.client.post(base + '/preview', json={'rootUids': roots}, headers=AUTH)
    assert preview.status_code == 200, preview.text
    decision = {'action': 'APPROVE', 'rootUids': roots,
                'expectedDigest': preview.json()['digest'], 'operationId': str(uuid.uuid4())}
    approved = site.client.post(base + '/decision', json=decision, headers=AUTH)
    assert approved.status_code == 200, approved.text
    done = post(site, f'/recoveries/{rid}/claim', claim, headers)
    assert done.status_code == 200, done.text
    assert done.json()['state'] == 'COMPLETED'
    assert site.data()['absences'] == before['absences']
    assert site.data()['schedules'] == before['schedules']
    own = post(site, '/receipts/read', {}, headers).json()['receipts']
    assert original['receiptToken'] in [r['receipt_token'] for r in own]
    amended = post(site, '/submit', {'operationId': str(uuid.uuid4()), 'action': 'AMEND',
                   'receiptToken': original['receiptToken'], 'request': payload()}, headers)
    assert amended.status_code == 200, amended.text
    assert site.data()['absences'] == before['absences']


@pytest.mark.parametrize('action', ['AMEND', 'CANCEL_ABSENCE'])
def test_child_replay_precedes_pending_child_blocker(site, action):
    original = approve_original(site)
    headers = session(site)
    body = {'operationId': str(uuid.uuid4()), 'action': action,
            'receiptToken': original['receiptToken'], 'request': payload(), 'reason': '취소 사유'}
    first = post(site, '/submit', body, headers)
    assert first.status_code == 200, first.text
    again = post(site, '/submit', body, headers)
    assert again.status_code == 200, again.text
    assert first.json()['receiptToken'] == again.json()['receiptToken']
    assert len(site.rows('change_requests')) == 2


def test_public_openapi_cannot_choose_prepared_receipt(site):
    paths = site.client.get('/openapi.json').json()['paths']
    for url in ['/api/duty/request-change', '/api/duty/request/manage']:
        assert not paths[url]['post'].get('parameters')


def test_new_session_wrong_origin_and_ticket_reuse_rejected(site):
    ticket = site.module.REQUEST_ACCESS.issue_ticket('http://localhost')
    wrong = post(site, '/session', {'ticket': ticket}, {'Origin': 'http://localhost:8001'})
    assert wrong.status_code == 403
    good = post(site, '/session', {'ticket': ticket}, {'Origin': 'http://localhost'})
    assert good.status_code == 200
    assert post(site, '/session', {'ticket': ticket}, {'Origin': 'http://localhost'}).status_code == 403


def test_recovery_binding_failure_can_resume_after_page_reload(site, monkeypatch):
    headers = session(site)
    vault = site.module.REQUEST_ACCESS.vault()
    original_bind = vault.bind_recovery
    def fail(*args):
        from backend.request_access_schema import AccessError
        raise AccessError('LOCAL_PERSIST_FAILED', '저장 실패', 503, True)
    monkeypatch.setattr(vault, 'bind_recovery', fail)
    first = post(site, '/recoveries', {'requestedName': 'A', 'operationId': str(uuid.uuid4())}, headers)
    assert first.status_code == 503
    monkeypatch.setattr(vault, 'bind_recovery', original_bind)
    # New UI operation after reload must recover its earlier private journal.
    again = post(site, '/recoveries', {'requestedName': 'A', 'operationId': str(uuid.uuid4())}, headers)
    assert again.status_code == 200, again.text
    listed = site.client.get(PREFIX + '/recoveries', headers=headers).json()['recoveries']
    assert [r['id'] for r in listed] == [again.json()['id']]


def test_recovery_claim_disk_failure_resumes_without_schedule_change(site, monkeypatch):
    approve_original(site)
    before = site.data()
    headers = session(site)
    rid = post(site, '/recoveries', {'requestedName': 'A', 'operationId': str(uuid.uuid4())}, headers).json()['id']
    base = f'/api/duty/request-recoveries/{rid}'
    root = site.client.get(base+'/candidates', headers=AUTH).json()['groups'][0]['rootUid']
    preview = site.client.post(base+'/preview', json={'rootUids':[root]}, headers=AUTH).json()
    site.client.post(base+'/decision', json={'action':'APPROVE','rootUids':[root],
                    'expectedDigest':preview['digest'],'operationId':str(uuid.uuid4())}, headers=AUTH)
    from backend import request_access_local as local
    from backend.request_access_schema import AccessError
    persist = local.persist_claim
    def fail(*args):
        raise AccessError('LOCAL_PERSIST_FAILED', '저장 실패', 503, True)
    monkeypatch.setattr(local, 'persist_claim', fail)
    failed = post(site, f'/recoveries/{rid}/claim', {'operationId':str(uuid.uuid4())}, headers)
    assert failed.status_code == 503
    state = site.client.get(PREFIX+f'/recoveries/{rid}',headers=headers).json()['state']
    assert state == 'CLAIMED'
    monkeypatch.setattr(local, 'persist_claim', persist)
    retry = post(site, f'/recoveries/{rid}/claim', {'operationId':str(uuid.uuid4())}, headers)
    assert retry.status_code == 200, retry.text
    assert retry.json()['state'] == 'COMPLETED'
    assert site.data()['absences'] == before['absences']


def test_actual_backup_restore_invalidates_pending_recovery_and_keeps_audit(site):
    headers = session(site)
    rid = post(site, '/recoveries', {'requestedName':'A','operationId':str(uuid.uuid4())}, headers).json()['id']
    with site.module.get_db_connection(readonly=True) as conn:
        old_id = dict(conn.execute("SELECT key,value FROM app_settings"))['request_access_dataset_id']
    snapshot = site.module.take_named_snapshot('premigration', 'recovery-test')
    from pathlib import Path
    restored = site.post('/api/system/restore', {'name':Path(snapshot).name,'baseVersion':site.data()['dataVersion']})
    assert restored.status_code == 200, restored.text
    rows = site.client.get(PREFIX+'/recoveries',headers=headers).json()['recoveries']
    assert rows[0]['id'] == rid and rows[0]['state'] == 'INVALIDATED'
    with site.module.get_db_connection(readonly=True) as conn:
        assert conn.execute('SELECT COUNT(*) FROM request_recovery_events').fetchone()[0] >= 1
        assert dict(conn.execute("SELECT key,value FROM app_settings"))['request_access_dataset_id'] == old_id


def test_holiday_contract_requires_header_and_compatible_local_path_works(site):
    headers = session(site)
    with site.module.get_db_connection(readonly=False) as conn:
        site.module.activate_holiday_contract(conn.cursor())
        conn.commit()
    assert post(site, '/receipts/read', {}, headers).status_code == 428
    compatible = dict(headers, **{'X-OnAir-Data-Contract':'1'})
    assert post(site, '/receipts/read', {}, compatible).status_code == 200
    assert post(site, '/recoveries', {'requestedName':'A','operationId':str(uuid.uuid4())}, compatible).status_code == 200


def test_candidates_show_kind_time_and_excluded_orphan_reason(site):
    approve_original(site)
    headers = session(site)
    rid = post(site, '/recoveries', {'requestedName':'A','operationId':str(uuid.uuid4())}, headers).json()['id']
    with site.module.get_db_connection(readonly=False) as conn:
        conn.execute("INSERT INTO change_requests(requester_name,target_date,request_type,status,created_at,amends_request_id) VALUES('A','2030-08-01','ABSENCE_AMEND','REJECTED','2030-01-01 09:00:00',99999)")
        conn.commit()
    data = site.client.get(f'/api/duty/request-recoveries/{rid}/candidates',headers=AUTH).json()
    assert data['groups'][0]['absence_type'] == '연차'
    assert data['groups'][0]['created_at']
    assert any(b['code']=='ORPHAN' and b['detail'] for b in data['blockers'])
