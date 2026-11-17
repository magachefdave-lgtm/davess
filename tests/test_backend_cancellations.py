"""Receipt-owned approved absence cancellations; all databases are synthetic."""
import copy
from datetime import date
import json
from pathlib import Path
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from test_backend_regression import AUTH, DAY_A, DAY_C, SEED, load_app
from test_backend_amendments import approve_original, own, manage, payload

START, END = '2030-06-21', '2030-06-25'


@pytest.fixture
def seeded(load_app):
    app = load_app('source')
    app.save({**copy.deepcopy(SEED), 'absenceTypes': ['연차', '특별휴가', '교육', '출장', '파견'],
              'schedules': {**copy.deepcopy(SEED['schedules']),
                            START: [{'name': 'A', 'role': '당직'}, {'name': 'B', 'role': '당직'}],
                            END: [{'name': 'A', 'role': '당직'}]}})
    return app


def cancel(app, receipt, **changes):
    return app.post('/api/duty/request/manage', {
        'receipt_token': receipt['receiptToken'], 'action': 'CANCEL_ABSENCE', 'reason': '', **changes}, auth=False)


def child(app, receipt, **changes):
    response = cancel(app, receipt, **changes)
    assert response.status_code == 200, response.text
    return response.json()


def frozen_today(monkeypatch, app, day):
    class Today(date):
        @classmethod
        def today(cls): return cls.fromisoformat(day)
    monkeypatch.setattr(app.module, 'date', Today)


def state(app):
    return app.data(), app.rows('change_requests')


@pytest.mark.parametrize('kind', ['연차', '특별휴가', '교육', '출장', '파견'])
def test_cancellation_approval_removes_whole_group_without_restoring_or_changing_duties(seeded, kind):
    original = approve_original(seeded, target_date=START, absence_end_date=END, absence_type=kind,
                                absence_details={'title': '교육 이름', 'notes': '보존'})
    source_row = seeded.rows('change_requests')[0]
    source = own(seeded, original)
    assert source['can_cancel'] and not source['cancellation_blocker']
    before = seeded.data()
    receipt = child(seeded, original, reason='신청자 전용 취소 사유')
    assert receipt['requestType'] == 'ABSENCE_CANCEL'
    assert receipt['receiptToken'] != original['receiptToken']
    assert seeded.data() == before
    assert seeded.rows('change_requests')[0] == source_row
    pending = own(seeded, receipt)
    assert pending['request_type'] == 'ABSENCE_CANCEL'
    assert pending['amendment_original'] == source['cancellable_absence']
    assert pending['absence_date_plan'] == source['absence_date_plan']
    parent = own(seeded, original)
    assert not parent['can_amend'] and not parent['can_cancel']
    assert parent['pending_cancellation_id'] == receipt['requestId']
    assert parent['linked_amendment']['request_type'] == 'ABSENCE_CANCEL'
    admin = seeded.get('/api/duty/requests', headers=AUTH).json()['requests'][0]
    assert admin['blocker'] == '' and admin.get('affectedDates', []) == []
    assert admin['reason'] == '신청자 전용 취소 사유'
    result = seeded.action(receipt['requestId'])
    assert result.status_code == 200, result.text
    assert result.json()['removedScheduleCount'] == 0
    assert result.json()['removedScheduleDates'] == []
    assert seeded.data()['absences'] == []
    assert seeded.data()['schedules'] == before['schedules']
    assert seeded.rows('change_requests')[0] == source_row
    parent = own(seeded, original)
    assert parent['status'] == 'APPROVED' and parent['cancelled_by_id'] == receipt['requestId']
    assert parent['cancellation_processed_at'] == own(seeded, receipt)['processed_at']
    assert not parent['can_cancel'] and not parent['can_amend']
    assert parent['superseded_by_id'] is None and parent['pending_cancellation_id'] is None
    assert not own(seeded, receipt)['can_cancel']
    assert cancel(seeded, original).status_code == 409
    history = seeded.data()['history'][0]
    assert history['type'] == '휴가·부재 취소(승인)'
    assert history['before']['absences'] == before['absences']
    assert history['after'] == {'absences': []}
    seeded.module.imported_history_snapshots(history)


@pytest.mark.parametrize('action', ['REJECT', 'WITHDRAW'])
def test_rejected_or_withdrawn_cancellation_keeps_original_and_allows_retry(seeded, action):
    original = approve_original(seeded)
    before = seeded.data()
    receipt = child(seeded, original)
    response = seeded.action(receipt['requestId'], action, reject_reason='비공개 반려 사유') if action == 'REJECT' else manage(seeded, receipt, 'WITHDRAW')
    assert response.status_code == 200, response.text
    assert seeded.data()['absences'] == before['absences'] and seeded.data()['schedules'] == before['schedules']
    parent = own(seeded, original)
    assert parent['can_cancel'] and parent['can_amend']
    assert parent['linked_amendment']['receiptToken'] == receipt['receiptToken']
    assert child(seeded, original)['requestId'] != receipt['requestId']


def test_pending_cancellation_cannot_be_edited_and_direct_submission_cannot_forge_it(seeded):
    original = approve_original(seeded)
    receipt = child(seeded, original)
    before = state(seeded)
    assert manage(seeded, receipt, 'EDIT', payload()).status_code == 400
    assert state(seeded) == before
    assert seeded.submit('ABSENCE_CANCEL', amends_request_id=original['requestId']).status_code == 400
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        assert db.execute('SELECT request_type FROM change_requests WHERE id=?', (receipt['requestId'],)).fetchone()[0] == 'ABSENCE_CANCEL'
        for kind in ['ABSENCE', 'ABSENCE_AMEND']:
            with pytest.raises(sqlite3.IntegrityError):
                db.execute('UPDATE change_requests SET request_type=? WHERE id=?', (kind, receipt['requestId']))


def test_ownership_private_reasons_and_notification_labels(seeded, monkeypatch):
    events = []
    monkeypatch.setattr(seeded.module, 'broadcast_from_thread', events.append)
    original = approve_original(seeded)
    before = state(seeded)
    assert cancel(seeded, {'receiptToken': 'X'*32}).status_code == 404
    assert state(seeded) == before
    receipt = child(seeded, original, reason='비공개 신청 이유')
    assert events[-1]['type'] == 'ABSENCE_CANCEL' and events[-1]['requestLabel'] == '휴가·부재 취소'
    for url in ['/api/duty/my-requests?name=A', '/api/duty/my-requests?name=A&involving=1']:
        response = seeded.get(url)
        assert original['receiptToken'] not in response.text and receipt['receiptToken'] not in response.text
        assert '비공개 신청 이유' not in response.text
        rows = response.json()['requests']
        assert rows[0]['requestLabel'] == '휴가·부재 취소'
        assert rows[1]['pending_cancellation_id'] == receipt['requestId']
    assert seeded.action(receipt['requestId'], 'REJECT', reject_reason='비공개 거절 이유').status_code == 200
    assert own(seeded, receipt)['reject_reason'] == '비공개 거절 이유'
    for url in ['/api/duty/my-requests?name=A', '/api/duty/my-requests?name=A&involving=1', '/api/duty/all-data']:
        assert '비공개 거절 이유' not in seeded.get(url).text
    assert '비공개 거절 이유' not in json.dumps(events, ensure_ascii=False)
    receipt = child(seeded, original)
    assert seeded.action(receipt['requestId']).status_code == 200
    assert events[-1]['requestType'] == 'ABSENCE_CANCEL'
    assert events[-1]['requestLabel'] == '휴가·부재 취소'
    for url in ['/api/duty/my-requests?name=A', '/api/duty/my-requests?name=A&involving=1']:
        assert seeded.get(url).json()['requests'][-1]['cancelled_by_id'] == receipt['requestId']


@pytest.mark.parametrize('reason,code', [('', 200), (None, 200), ('가'*500, 200), ('가'*501, 400)])
def test_optional_reason_limit(seeded, reason, code):
    original = approve_original(seeded)
    assert cancel(seeded, original, reason=reason).status_code == code


@pytest.mark.parametrize('stage', ['submit', 'approve'])
@pytest.mark.parametrize('change', ['delete', 'dates', 'education', 'person', 'recreate'])
def test_every_segment_keeps_identity_and_changed_original_is_never_deleted(seeded, stage, change):
    original = approve_original(seeded, target_date=START, absence_end_date=END)
    receipt = child(seeded, original) if stage == 'approve' else None
    rows = seeded.data()['absences']
    assert len(rows) == 2
    old = copy.deepcopy(rows)
    if change in ('delete', 'recreate'): rows.pop(1)
    elif change == 'dates': rows[1]['end'] = '2030-06-26'
    elif change == 'education': rows[1]['education'] = {'notes': '수정'}
    else: rows[1]['name'] = 'B'
    seeded.save({'absences': rows})
    if change == 'recreate': seeded.save({'absences': old})
    before = state(seeded)
    response = seeded.action(receipt['requestId']) if receipt else cancel(seeded, original)
    assert response.status_code == 409, response.text
    assert state(seeded) == before


@pytest.mark.parametrize('stage', ['submit', 'approve'])
@pytest.mark.parametrize('guard', ['past', 'closed'])
def test_past_days_and_closed_months_are_checked_again_at_approval(seeded, monkeypatch, stage, guard):
    original = approve_original(seeded, target_date=START, absence_end_date=END)
    receipt = child(seeded, original) if stage == 'approve' else None
    if guard == 'past': frozen_today(monkeypatch, seeded, '2030-06-22')
    else: seeded.post('/api/duty/month-close', {'month': '2030-06', 'closed': True, 'reason': '검증', 'baseVersion': seeded.data()['dataVersion']})
    before = state(seeded)
    response = seeded.action(receipt['requestId']) if receipt else cancel(seeded, original)
    assert response.status_code == (423 if guard == 'closed' else 409), response.text
    assert state(seeded) == before
    assert own(seeded, original)['cancellation_blocker']
    if receipt: assert seeded.get('/api/duty/requests', headers=AUTH).json()['requests'][0]['blocker']


def test_today_allowed_and_excluded_past_days_and_closed_month_are_ignored(seeded, monkeypatch):
    original = approve_original(seeded, target_date='2030-06-29', absence_end_date='2030-07-02')
    frozen_today(monkeypatch, seeded, '2030-07-01')
    seeded.post('/api/duty/month-close', {'month': '2030-06', 'closed': True, 'reason': '검증', 'baseVersion': seeded.data()['dataVersion']})
    receipt = child(seeded, original)
    assert seeded.action(receipt['requestId']).status_code == 200


def test_cancel_plan_uses_original_spans_after_holidays_change_and_unchanged_full_save(seeded):
    original = approve_original(seeded, target_date=START, absence_end_date=END)
    plan = own(seeded, original)['absence_date_plan']
    receipt = child(seeded, original)
    seeded.save({'customHolidays': {START: {'name': '새 휴일'}, '2030-06-24': {'name': '새 휴일'}}})
    seeded.save({'absences': seeded.data()['absences']})
    assert own(seeded, receipt)['absence_date_plan'] == plan
    assert seeded.get('/api/duty/requests', headers=AUTH).json()['requests'][0]['absence_date_plan'] == plan
    assert seeded.action(receipt['requestId']).status_code == 200
    assert own(seeded, receipt)['absence_date_plan'] == plan


@pytest.mark.parametrize('actions', [('CANCEL_ABSENCE', 'CANCEL_ABSENCE'), ('CANCEL_ABSENCE', 'AMEND')])
def test_concurrent_requests_share_one_pending_child_constraint(seeded, actions):
    original = approve_original(seeded)
    barrier = threading.Barrier(2)
    def call(action):
        client = TestClient(seeded.module.app)
        barrier.wait(5)
        return client.post('/api/duty/request/manage', json={'receipt_token': original['receiptToken'], 'action': action, 'request': payload() if action == 'AMEND' else None})
    with ThreadPoolExecutor(2) as pool: responses = list(pool.map(call, actions))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len([r for r in seeded.rows('change_requests') if r['status'] == 'PENDING']) == 1


def test_approve_withdraw_race_has_one_winner(seeded):
    original = approve_original(seeded)
    receipt = child(seeded, original)
    barrier = threading.Barrier(2)
    def call(action):
        client = TestClient(seeded.module.app)
        barrier.wait(5)
        if action == 'APPROVE': return client.post('/api/duty/request/action', headers=AUTH, json={'request_id': receipt['requestId'], 'action': action})
        return client.post('/api/duty/request/manage', json={'receipt_token': receipt['receiptToken'], 'action': action})
    with ThreadPoolExecutor(2) as pool: responses = list(pool.map(call, ['APPROVE', 'WITHDRAW']))
    assert len([r for r in responses if r.status_code == 200]) == 1
    status = own(seeded, receipt)['status']
    assert status in ('APPROVED', 'CANCELLED')
    assert bool(seeded.data()['absences']) == (status == 'CANCELLED')


@pytest.mark.parametrize('fault', ['delete', 'status'])
def test_failed_delete_rolls_back_all_segments_history_and_status(seeded, fault):
    original = approve_original(seeded, target_date=START, absence_end_date=END)
    receipt = child(seeded, original)
    rows = seeded.data()['absences']
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        trigger = f'BEFORE DELETE ON absences WHEN OLD.id={rows[-1]["id"]}' if fault == 'delete' else 'BEFORE UPDATE OF status ON change_requests'
        db.execute(f"CREATE TRIGGER cancel_fault {trigger} BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
    before = state(seeded)
    assert seeded.action(receipt['requestId']).status_code == 500
    assert state(seeded) == before
    with sqlite3.connect(seeded.module.DB_PATH) as db: db.execute('DROP TRIGGER cancel_fault')
    assert seeded.action(receipt['requestId']).status_code == 200


def test_latest_amended_approval_can_be_cancelled_and_old_parent_cannot(seeded):
    original = approve_original(seeded)
    amendment = manage(seeded, original).json()
    assert seeded.action(amendment['requestId']).status_code == 200
    assert cancel(seeded, original).status_code == 409
    receipt = child(seeded, amendment)
    assert seeded.action(receipt['requestId']).status_code == 200
    assert own(seeded, original)['superseded_by_id'] == amendment['requestId']
    assert own(seeded, amendment)['cancelled_by_id'] == receipt['requestId']


def test_legacy_continuous_leave_cancel_and_json_backup_roundtrip(seeded, load_app, monkeypatch):
    original = approve_original(seeded, target_date=START, absence_end_date=END, absence_type='교육')
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        db.execute("UPDATE absences SET type='연차'")
        db.execute("UPDATE change_requests SET absence_type='연차', approved_history_id=NULL WHERE id=?", (original['requestId'],))
        row = db.execute("SELECT id,after_json FROM change_history WHERE type='휴가·부재(승인)'").fetchone()
        after = json.loads(row[1]); after['absences'][0]['type'] = '연차'
        db.execute('UPDATE change_history SET after_json=? WHERE id=?', (json.dumps(after), row[0]))
    receipt = child(seeded, original)
    assert own(seeded, receipt)['absence_date_plan']['days'] == 5
    assert seeded.action(receipt['requestId']).status_code == 200
    monkeypatch.setattr(seeded.module, 'SHARED_PATH', str(Path(seeded.module.DB_PATH).parent))
    response = seeded.client.post('/api/system/backup', headers=AUTH)
    assert response.status_code == 200, response.text
    data = json.loads(Path(response.json()['path']).read_text(encoding='utf-8'))
    target = load_app('source')
    keys = ('members', 'schedules', 'comments', 'noDuty', 'absences', 'absenceTypes', 'customHolidays', 'rules', 'globalMemo')
    target.save({**{key: data[key] for key in keys}, 'historyBulk': data['history']})
    assert target.data()['absences'] == []
    assert any(h['type'] == '휴가·부재 취소(승인)' and h['after'] == {'absences': []} for h in target.data()['history'])


def test_viewer_can_request_but_never_approve(seeded, load_app):
    original = approve_original(seeded)
    viewer = load_app('source', 'VIEWER', source_db=seeded.module.DB_PATH)
    receipt = child(viewer, original)
    assert viewer.action(receipt['requestId']).status_code == 403
    assert own(viewer, receipt)['status'] == 'PENDING'


def test_cancellation_payload_is_derived_only_from_owned_parent(seeded):
    original = approve_original(seeded)
    receipt = child(seeded, original, request=payload(requester_name='B', absence_type='파견'),
                    amends_request_id=999, amendment_original={'id': 999})
    pending = own(seeded, receipt)
    assert pending['requester_name'] == 'A' and pending['absence_type'] == '연차'
    assert pending['amends_request_id'] == original['requestId']
    assert pending['target_date'] == DAY_A and pending['absence_end_date'] == DAY_C


@pytest.mark.parametrize('corruption', ['history_id', 'ambiguous'])
def test_unproven_original_approval_cannot_be_cancelled(seeded, corruption):
    original = approve_original(seeded)
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        if corruption == 'history_id':
            db.execute('UPDATE change_requests SET approved_history_id=999999')
        else:
            db.execute('UPDATE change_requests SET approved_history_id=NULL')
            db.execute("INSERT INTO change_history(timestamp,type,date_key,details,before_json,after_json) SELECT timestamp,type,date_key,details,before_json,after_json FROM change_history WHERE type='휴가·부재(승인)'")
    before = state(seeded)
    assert not own(seeded, original)['can_cancel']
    assert cancel(seeded, original).status_code == 409
    assert state(seeded) == before


def test_sqlite_snapshot_restore_preserves_pending_cancel_receipt_and_guards(seeded):
    original = approve_original(seeded, target_date=START, absence_end_date=END)
    receipt = child(seeded, original, reason='백업에 보존되는 비공개 사유')
    pending = own(seeded, receipt)
    response = seeded.post('/api/system/snapshot', {'kind': 'manual'})
    assert response.status_code == 200, response.text
    backup = response.json()['name']
    assert seeded.get('/api/system/backup-preview?name=' + backup, headers=AUTH).json()['ok']
    assert manage(seeded, receipt, 'WITHDRAW').status_code == 200
    response = seeded.post('/api/system/restore', {'name': backup, 'baseVersion': seeded.data()['dataVersion']})
    assert response.status_code == 200, response.text
    assert own(seeded, receipt) == pending
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE change_requests SET request_type='ABSENCE' WHERE id=?", (receipt['requestId'],))
    assert seeded.action(receipt['requestId']).status_code == 200
