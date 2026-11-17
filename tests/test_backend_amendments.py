"""Approved absence amendments through real APIs and isolated synthetic SQLite DBs."""
import copy
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from test_backend_regression import AUTH, DAY_A, DAY_B, DAY_C, SEED, load_app

NEW_START, NEW_END = '2030-07-03', '2030-07-05'


@pytest.fixture
def seeded(load_app):
    app = load_app('source')
    app.save(copy.deepcopy(SEED))
    app.save({'absenceTypes': ['연차', '교육', '파견']})
    return app


def payload(**changes):
    result = dict(requester_name='A', request_type='ABSENCE', target_date=NEW_START,
                  absence_end_date=NEW_END, absence_type='교육', reason='일정 변경 사유',
                  absence_details={'location': '온라인'})
    result.update(changes)
    return result


def approve_original(app, **changes):
    receipt = app.submit('ABSENCE', **changes).json()
    response = app.action(receipt['requestId'])
    assert response.status_code == 200, response.text
    return receipt


def own(app, receipt):
    response = app.get('/api/duty/request-status/' + receipt['receiptToken'])
    assert response.status_code == 200, response.text
    return response.json()['request']


def manage(app, receipt, action='AMEND', request=None):
    return app.post('/api/duty/request/manage', {
        'receipt_token': receipt['receiptToken'], 'action': action,
        'request': payload() if request is None and action == 'AMEND' else request}, auth=False)


def amend(app, receipt, **changes):
    response = manage(app, receipt, request=payload(**changes))
    assert response.status_code == 200, response.text
    return response.json()


def test_owner_creates_separate_pending_amendment_without_touching_approved_domain(seeded):
    original = approve_original(seeded)
    before = seeded.data()
    old = seeded.rows('change_requests')[0]
    status = own(seeded, original)
    assert status['can_amend'] is True and status['amendment_blocker'] == ''
    assert status['amendable_absence']['id'] == before['absences'][0]['id']
    child = amend(seeded, original)
    assert child['requestId'] != original['requestId']
    assert child['receiptToken'] != original['receiptToken']
    assert seeded.data() == before
    assert seeded.rows('change_requests')[0] == old
    pending = own(seeded, child)
    assert pending['status'] == 'PENDING'
    assert pending['amends_request_id'] == original['requestId']
    assert pending['amendment_original']['start'] == DAY_A
    status = own(seeded, original)
    assert status['can_amend'] is False
    assert status['pending_amendment_id'] == child['requestId']
    assert status['linked_amendment'] == {'id': child['requestId'], 'status': 'PENDING', 'receiptToken': child['receiptToken'], 'request_type': 'ABSENCE'}


def test_approval_updates_one_absence_id_and_only_releases_new_range_duties(seeded):
    original = approve_original(seeded)
    old_id = seeded.data()['absences'][0]['id']
    seeded.save({'schedules': {DAY_A: [{'name': 'B', 'role': '당직'}], NEW_START: [{'name': 'A', 'role': '당직'}]}})
    child = amend(seeded, original)
    pending = seeded.get('/api/duty/requests', headers=AUTH).json()['requests'][0]
    assert pending['amendment_original']['id'] == old_id
    assert pending['blocker'] == '' and pending['affectedDates'] == [NEW_START]
    response = seeded.action(child['requestId'], request_revision=0)
    assert response.status_code == 200, response.text
    data = seeded.data()
    assert len(data['absences']) == 1
    assert data['absences'][0]['id'] == old_id
    assert (data['absences'][0]['start'], data['absences'][0]['end']) == (NEW_START, NEW_END)
    assert data['absences'][0]['education']['location'] == '온라인'
    assert data['schedules'][DAY_A][0]['name'] == 'B'
    assert NEW_START not in data['schedules']
    assert own(seeded, original)['released_dates'] == [DAY_A, DAY_C]
    assert own(seeded, child)['released_dates'] == [NEW_START]
    assert own(seeded, child)['can_amend'] is True
    source = own(seeded, original)
    assert source['superseded_by_id'] == child['requestId'] and not source['can_amend']
    assert source['linked_amendment']['receiptToken'] == child['receiptToken']
    assert manage(seeded, original).status_code == 409
    grandchild = amend(seeded, child, target_date='2030-08-03', absence_end_date='2030-08-05', absence_details={})
    assert seeded.action(grandchild['requestId']).status_code == 200
    assert seeded.data()['absences'][0]['id'] == old_id
    assert own(seeded, grandchild)['can_amend'] is True


@pytest.mark.parametrize('action', ['REJECT', 'WITHDRAW'])
def test_reject_or_withdraw_changes_only_pending_amendment_and_allows_retry(seeded, action):
    original = approve_original(seeded)
    before = seeded.data()
    child = amend(seeded, original)
    response = seeded.action(child['requestId'], action) if action == 'REJECT' else manage(seeded, child, 'WITHDRAW')
    assert response.status_code == 200, response.text
    assert seeded.data()['absences'] == before['absences']
    assert seeded.data()['schedules'] == before['schedules']
    assert own(seeded, original)['can_amend'] is True
    assert amend(seeded, original)['requestId'] != child['requestId']


def test_pending_edit_keeps_link_and_requires_fresh_admin_revision(seeded):
    original = approve_original(seeded)
    child = amend(seeded, original)
    original_snapshot = own(seeded, child)['amendment_original']
    response = manage(seeded, child, 'EDIT', payload(target_date=DAY_B, absence_end_date=DAY_C))
    assert response.status_code == 200, response.text
    status = own(seeded, child)
    assert status['amendment_original'] == original_snapshot
    assert status['amends_request_id'] == original['requestId'] and status['revision'] == 1
    assert seeded.action(child['requestId'], request_revision=0).status_code == 409
    assert seeded.action(child['requestId'], request_revision=1).status_code == 200
    assert seeded.data()['absences'][0]['start'] == DAY_B


@pytest.mark.parametrize('changes', [
    {'requester_name': 'B'}, {'request_type': 'SUBSTITUTE', 'substitute_name': 'C'},
    {'absence_end_date': '2030-01-01'}, {'absence_type': '없음'},
    {'absence_details': {'notes': True}}, {'target_date': '잘못된 날짜'},
])
def test_invalid_amendment_never_changes_source(seeded, changes):
    original = approve_original(seeded)
    before = seeded.data(), seeded.rows('change_requests')
    assert manage(seeded, original, request=payload(**changes)).status_code == 400
    assert (seeded.data(), seeded.rows('change_requests')) == before


def test_pending_amendment_cannot_change_request_kind(seeded):
    original = approve_original(seeded)
    child = amend(seeded, original)
    assert manage(seeded, child, 'EDIT', payload(request_type='SUBSTITUTE', substitute_name='C')).status_code == 400
    assert own(seeded, child)['request_type'] == 'ABSENCE'


@pytest.mark.parametrize('stage', ['before_submit', 'before_approve'])
@pytest.mark.parametrize('change', ['deleted', 'dates', 'education', 'other_person'])
def test_changed_or_deleted_original_cannot_be_overwritten(seeded, stage, change):
    original = approve_original(seeded)
    child = amend(seeded, original) if stage == 'before_approve' else None
    rows = seeded.data()['absences']
    if change == 'deleted': rows = []
    elif change == 'dates': rows[0]['end'] = DAY_B
    elif change == 'education': rows[0]['education'] = {'notes': '관리자 수정'}
    else: rows[0]['name'] = 'B'
    seeded.save({'absences': rows})
    before = seeded.data(), seeded.rows('change_requests')
    response = seeded.action(child['requestId']) if child else manage(seeded, original)
    assert response.status_code == 409, response.text
    assert (seeded.data(), seeded.rows('change_requests')) == before


@pytest.mark.parametrize('month', ['2030-06', '2030-07'])
@pytest.mark.parametrize('stage', ['before_submit', 'before_approve', 'before_edit'])
def test_both_original_and_proposed_closed_months_are_protected(seeded, month, stage):
    original = approve_original(seeded)
    child = None if stage == 'before_submit' else amend(seeded, original)
    assert seeded.post('/api/duty/month-close', {'month': month, 'closed': True, 'reason': '검증', 'baseVersion': seeded.data()['dataVersion']}).status_code == 200
    before = seeded.data(), seeded.rows('change_requests')
    response = (manage(seeded, original) if stage == 'before_submit' else seeded.action(child['requestId'])
                if stage == 'before_approve' else manage(seeded, child, 'EDIT', payload()))
    assert response.status_code == 423, response.text
    assert (seeded.data(), seeded.rows('change_requests')) == before


@pytest.mark.parametrize('stage', ['before_submit', 'before_approve'])
def test_overlap_excludes_only_the_original_absence(seeded, stage):
    original = approve_original(seeded)
    child = amend(seeded, original) if stage == 'before_approve' else None
    rows = seeded.data()['absences'] + [{'id': 500, 'name': 'A', 'type': '파견', 'start': NEW_START, 'end': NEW_END}]
    seeded.save({'absences': rows})
    before = seeded.data(), seeded.rows('change_requests')
    response = seeded.action(child['requestId']) if child else manage(seeded, original)
    assert response.status_code == 409
    assert (seeded.data(), seeded.rows('change_requests')) == before


def test_overlapping_own_original_and_reason_only_change_are_approvable(seeded):
    original = approve_original(seeded)
    child = amend(seeded, original, target_date=DAY_A, absence_end_date=DAY_C, absence_type='연차', absence_details=None)
    assert seeded.action(child['requestId']).status_code == 200
    assert own(seeded, child)['can_amend'] is True
    assert own(seeded, child)['released_dates'] == []


def test_tokens_are_private_and_unknown_token_cannot_amend(seeded):
    original = approve_original(seeded)
    child = amend(seeded, original)
    for response in [seeded.get('/api/duty/requests', headers=AUTH), seeded.get('/api/duty/my-requests?name=A'), seeded.get('/api/duty/my-requests?name=A&involving=1')]:
        assert original['receiptToken'] not in response.text and child['receiptToken'] not in response.text
        assert 'amends_request_id' in response.text and 'amendment_original' in response.text
    before = seeded.rows('change_requests')
    assert manage(seeded, {'receiptToken': 'X'*32}).status_code == 404
    assert seeded.rows('change_requests') == before


def test_duplicate_concurrent_amendments_create_one_pending_child(seeded):
    original = approve_original(seeded)
    barrier = threading.Barrier(2)
    def call(_):
        client = TestClient(seeded.module.app); barrier.wait(5)
        return client.post('/api/duty/request/manage', json={'receipt_token': original['receiptToken'], 'action': 'AMEND', 'request': payload()})
    with ThreadPoolExecutor(2) as pool: responses = list(pool.map(call, range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    pending = [r for r in seeded.rows('change_requests') if r['status'] == 'PENDING']
    assert len(pending) == 1
    assert own(seeded, original)['linked_amendment']['id'] == pending[0]['id']


def test_failed_approval_rolls_back_absence_schedule_and_history_then_retries(seeded):
    original = approve_original(seeded); child = amend(seeded, original)
    before = seeded.data(), seeded.rows('change_requests')
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        db.execute("CREATE TRIGGER amendment_fault BEFORE UPDATE OF status ON change_requests BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
    assert seeded.action(child['requestId']).status_code == 500
    assert (seeded.data(), seeded.rows('change_requests')) == before
    with sqlite3.connect(seeded.module.DB_PATH) as db: db.execute('DROP TRIGGER amendment_fault')
    assert seeded.action(child['requestId']).status_code == 200


def test_legacy_history_fallback_and_schema_migration_preserve_requests(seeded):
    original = approve_original(seeded)
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        db.execute('UPDATE change_requests SET approved_history_id=NULL')
        db.execute('DROP INDEX IF EXISTS idx_change_requests_pending_amendment')
        db.execute('DROP TRIGGER IF EXISTS guard_absence_amendment_kind')
        for col in ['amends_request_id','amendment_original']:
            if col in {r[1] for r in db.execute('PRAGMA table_info(change_requests)')}:db.execute(f'ALTER TABLE change_requests DROP COLUMN {col}')
    assert 'change_requests.amends_request_id' in seeded.module.pending_schema_migrations()
    seeded.module.init_db()
    assert own(seeded, original)['can_amend'] is True
    assert amend(seeded, original)['requestId'] != original['requestId']


def test_ambiguous_legacy_history_refuses_mapping(seeded):
    original = approve_original(seeded)
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        db.execute('UPDATE change_requests SET approved_history_id=NULL')
        db.execute("INSERT INTO change_history(timestamp,type,date_key,details,before_json,after_json) SELECT timestamp,type,date_key,details,before_json,after_json FROM change_history WHERE type='휴가·부재(승인)'")
    assert own(seeded, original)['can_amend'] is False
    assert manage(seeded, original).status_code == 409


def test_stored_kind_blocks_old_approval_and_old_edit_downgrade(seeded):
    original = approve_original(seeded); child = amend(seeded, original)
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        kind = db.execute('SELECT request_type FROM change_requests WHERE id=?', (child['requestId'],)).fetchone()[0]
        assert kind == 'ABSENCE_AMEND'
        # Older servers approve only the three known request kinds.
        assert kind not in {'SWAP', 'SUBSTITUTE', 'ABSENCE'}
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE change_requests SET request_type='ABSENCE' WHERE id=?", (child['requestId'],))
    assert own(seeded, child)['request_type'] == 'ABSENCE'
    assert manage(seeded, child, 'EDIT', payload(reason='최신 버전 수정')).status_code == 200
    assert seeded.action(child['requestId'], request_revision=1).status_code == 200


def test_pending_amendment_conflicts_with_ordinary_pending_absence(seeded):
    original = approve_original(seeded); child = amend(seeded, original)
    assert seeded.submit('ABSENCE', target_date=NEW_START, absence_end_date=NEW_END).status_code == 409
    assert manage(seeded, child, 'WITHDRAW').status_code == 200
    other = seeded.submit('ABSENCE', target_date=NEW_START, absence_end_date=NEW_END).json()
    assert manage(seeded, original).status_code == 409
    assert own(seeded, other)['status'] == 'PENDING'


def test_viewer_can_amend_but_cannot_approve(seeded, load_app):
    original = approve_original(seeded)
    viewer = load_app('source', 'VIEWER', source_db=seeded.module.DB_PATH)
    child = amend(viewer, original)
    assert viewer.action(child['requestId']).status_code == 403
    assert own(viewer, child)['status'] == 'PENDING'
    assert viewer.data()['absences'] == seeded.data()['absences']


@pytest.mark.parametrize('stage', ['before_submit', 'before_approve'])
def test_deleted_then_identically_recreated_id_is_not_the_approved_original(seeded, stage):
    original = approve_original(seeded)
    child = amend(seeded, original) if stage == 'before_approve' else None
    original_absences = seeded.data()['absences']
    seeded.save({'absences': []})
    seeded.save({'absences': original_absences})
    before = seeded.data(), seeded.rows('change_requests')
    response = seeded.action(child['requestId']) if child else manage(seeded, original)
    assert response.status_code == 409, response.text
    assert (seeded.data(), seeded.rows('change_requests')) == before


def test_unchanged_full_save_preserves_approved_absence_identity(seeded):
    original = approve_original(seeded)
    child = amend(seeded, original)
    seeded.save({'absences': seeded.data()['absences']})
    assert seeded.action(child['requestId']).status_code == 200


@pytest.mark.parametrize('status', ['REJECTED', 'CANCELLED'])
def test_owner_recovers_lost_child_receipt_after_terminal_decision(seeded, status):
    original = approve_original(seeded); child = amend(seeded, original)
    response = seeded.action(child['requestId'], 'REJECT') if status == 'REJECTED' else manage(seeded, child, 'WITHDRAW')
    assert response.status_code == 200
    source = own(seeded, original)
    assert source['can_amend'] is True
    assert source['linked_amendment'] == {'id': child['requestId'], 'status': status, 'receiptToken': child['receiptToken'], 'request_type': 'ABSENCE'}
    assert child['receiptToken'] not in seeded.get('/api/duty/my-requests?name=A').text
