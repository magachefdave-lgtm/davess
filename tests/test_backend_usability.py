"""Usability changes tested through the real API against synthetic temporary DBs."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from test_backend_regression import AUTH, DAY_A, DAY_B, DAY_C, SEED, load_app  # noqa: F401


EDUCATION = {
    'title': '합성 교육', 'location': '회의실', 'provider': '시험 기관',
    'preparation': '노트북', 'content': '교육 내용', 'notes': '공개 메모',
}


@pytest.fixture
def seeded(load_app):
    app = load_app('source')
    app.save(copy.deepcopy(SEED))
    app.save({'absenceTypes': ['연차', '파견', '교육']})
    return app


def request_payload(kind='ABSENCE', **changes):
    value = {'requester_name': 'A', 'target_date': DAY_A, 'request_type': kind,
             'reason': '비공개 신청 사유', 'absence_type': '교육', 'absence_end_date': DAY_C}
    if kind == 'SWAP':
        value.update(swap_date=DAY_B, swap_worker='B')
    elif kind == 'SUBSTITUTE':
        value.update(substitute_name='C', is_zero_score=False)
    value.update(changes)
    return value


def submit(app, payload):
    response = app.post('/api/duty/request-change', payload, auth=False)
    assert response.status_code == 200, response.text
    return response.json()


def manage(app, receipt, action, payload=None):
    body = {'receipt_token': receipt['receiptToken'], 'action': action}
    if payload is not None:
        body['request'] = payload
    return app.post('/api/duty/request/manage', body, auth=False)


def absence(**changes):
    return {'id': 70, 'name': 'A', 'type': '교육', 'start': DAY_A, 'end': DAY_A,
            **changes}


def status(app, receipt):
    response = app.get('/api/duty/request-status/' + receipt['receiptToken'])
    assert response.status_code == 200, response.text
    return response.json()['request']


def test_education_roundtrips_six_optional_strings(seeded):
    record = absence(education=EDUCATION)
    seeded.save({'absences': [record]})
    assert seeded.data()['absences'] == [record]
    seeded.save({'absences': [absence(education={key: '' for key in EDUCATION})]})
    assert seeded.data()['absences'][0]['education'] == {key: '' for key in EDUCATION}


def test_legacy_absence_save_preserves_metadata_while_period_changes(seeded):
    seeded.save({'absences': [absence(education=EDUCATION)]})
    seeded.save({'absences': [absence(end=DAY_C)]})
    assert seeded.data()['absences'] == [absence(end=DAY_C, education=EDUCATION)]


def test_legacy_absence_without_id_matches_existing_metadata(seeded):
    seeded.save({'absences': [absence(education=EDUCATION)]})
    record = absence()
    record.pop('id')
    seeded.save({'absences': [record]})
    assert seeded.data()['absences'][0]['education'] == EDUCATION


def test_legacy_id_reused_for_other_person_does_not_copy_education(seeded):
    seeded.save({'absences': [absence(education=EDUCATION)]})
    seeded.save({'absences': [absence(name='B')]})
    assert 'education' not in seeded.data()['absences'][0]


def test_invalid_education_rolls_back_other_collections(seeded):
    before = seeded.data()
    seeded.save({'absences': [absence(education={'title': 12})], 'schedules': {}}, code=400)
    assert seeded.data() == before


def test_absence_request_approval_carries_metadata_not_reason(seeded):
    receipt = submit(seeded, request_payload(absence_details=EDUCATION))
    pending = seeded.get('/api/duty/requests', headers=AUTH).json()['requests'][0]
    assert pending['absence_details'] == EDUCATION
    assert status(seeded, receipt)['absence_details'] == EDUCATION
    response = seeded.action(receipt['requestId'])
    assert response.status_code == 200, response.text
    record = seeded.data()['absences'][0]
    assert record['education'] == EDUCATION
    assert 'reason' not in record and '비공개 신청 사유' not in str(record)


def test_blank_education_request_is_valid(seeded):
    receipt = submit(seeded, request_payload(absence_details={}))
    assert seeded.action(receipt['requestId']).status_code == 200
    assert seeded.data()['absences'][0]['education'] == {key: '' for key in EDUCATION}


def test_migration_preserves_legacy_rows_and_adds_optional_storage(seeded):
    seeded.save({'absences': [absence()]})
    module = seeded.module
    with sqlite3.connect(module.DB_PATH) as db:
        for table, column in [('absences', 'education_json'), ('change_requests', 'absence_details'),
                              ('change_history', 'before_json'), ('change_history', 'after_json')]:
            columns = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
            if column in columns:
                db.execute(f'ALTER TABLE {table} DROP COLUMN {column}')
    module.init_db()
    with sqlite3.connect(module.DB_PATH) as db:
        assert 'education_json' in {r[1] for r in db.execute('PRAGMA table_info(absences)')}
        assert 'absence_details' in {r[1] for r in db.execute('PRAGMA table_info(change_requests)')}
        assert {'before_json', 'after_json'} <= {r[1] for r in db.execute('PRAGMA table_info(change_history)')}
    assert seeded.data()['absences'] == [absence()]
    module.init_db()
    assert seeded.data()['absences'] == [absence()]


def test_viewer_reads_old_absence_schema_and_can_submit_details(load_app):
    admin = load_app('source')
    admin.save(copy.deepcopy(SEED))
    with sqlite3.connect(admin.module.DB_PATH) as db:
        for table, column in [('absences', 'education_json'), ('change_requests', 'absence_details')]:
            if column in {r[1] for r in db.execute(f'PRAGMA table_info({table})')}:
                db.execute(f'ALTER TABLE {table} DROP COLUMN {column}')
    viewer = load_app('source', 'VIEWER', source_db=admin.module.DB_PATH)
    assert viewer.data()['absences'] == []
    receipt = submit(viewer, request_payload(absence_type='파견', absence_details=EDUCATION))
    assert status(viewer, receipt)['absence_details'] == EDUCATION


def test_approval_upgrades_optional_legacy_storage_atomically(seeded):
    receipt = submit(seeded, request_payload(absence_details=EDUCATION))
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        for table, column in [('absences', 'education_json'), ('change_history', 'before_json'), ('change_history', 'after_json')]:
            db.execute(f'ALTER TABLE {table} DROP COLUMN {column}')
    response = seeded.action(receipt['requestId'])
    assert response.status_code == 200, response.text
    assert seeded.data()['absences'][0]['education'] == EDUCATION
    assert 'before' in seeded.data()['history'][0]


def test_owner_can_withdraw_pending_request_and_resubmit(seeded):
    receipt = submit(seeded, request_payload())
    before = seeded.data()
    response = manage(seeded, receipt, 'WITHDRAW')
    assert response.status_code == 200, response.text
    assert response.json()['requestStatus'] == 'CANCELLED'
    assert status(seeded, receipt)['status'] == 'CANCELLED'
    assert seeded.data()['schedules'] == before['schedules']
    assert seeded.data()['absences'] == before['absences']
    assert manage(seeded, receipt, 'WITHDRAW').status_code == 409
    assert seeded.action(receipt['requestId']).status_code == 400
    submit(seeded, request_payload())


@pytest.mark.parametrize('token', ['', 'A', 'x' * 32])
def test_manage_unknown_token_cannot_read_or_mutate_requests(seeded, token):
    receipt = submit(seeded, request_payload())
    before = seeded.rows('change_requests')
    response = seeded.post('/api/duty/request/manage',
                           {'receipt_token': token, 'action': 'WITHDRAW'}, auth=False)
    assert response.status_code == 404, response.text
    assert response.json() == {'detail': '신청 결과를 찾을 수 없습니다.'}
    assert seeded.rows('change_requests') == before
    assert receipt['receiptToken'] not in str(seeded.get('/api/duty/my-requests?name=A').json())


def test_owner_edit_retains_identity_and_updates_request_fields(seeded):
    receipt = submit(seeded, request_payload(absence_details=EDUCATION))
    original = seeded.rows('change_requests')[0]
    updated = request_payload(target_date=DAY_B, absence_end_date=DAY_C,
                              reason='수정 사유', absence_details={'location': '온라인'})
    response = manage(seeded, receipt, 'EDIT', updated)
    assert response.status_code == 200, response.text
    current = seeded.rows('change_requests')[0]
    for key in ('id', 'receipt_token', 'requester_name', 'created_at', 'requester_host', 'requester_account'):
        assert current[key] == original[key]
    own = status(seeded, receipt)
    assert own['target_date'] == DAY_B and own['reason'] == '수정 사유'
    assert own['absence_details']['location'] == '온라인'
    assert own['status'] == 'PENDING'
    assert seeded.action(receipt['requestId']).status_code == 200
    assert seeded.data()['absences'][0]['education']['location'] == '온라인'


def test_name_lookup_hides_original_reason_and_education_details(seeded):
    receipt = submit(seeded, request_payload(absence_details=EDUCATION))
    public = seeded.get('/api/duty/my-requests?name=A').json()['requests'][0]
    assert 'reason' not in public and 'absence_details' not in public
    assert 'receipt_token' not in public
    owned = status(seeded, receipt)
    assert owned['reason'] == '비공개 신청 사유'
    assert owned['absence_details'] == EDUCATION


def test_edit_cannot_transfer_request_to_another_name(seeded):
    receipt = submit(seeded, request_payload())
    before = seeded.rows('change_requests')
    response = manage(seeded, receipt, 'EDIT', request_payload(requester_name='B'))
    assert response.status_code == 400, response.text
    assert seeded.rows('change_requests') == before


@pytest.mark.parametrize('changes,code', [
    ({'absence_end_date': '2030-06-01'}, 400),
    ({'absence_type': '미등록'}, 400),
    ({'target_date': 'not-a-date'}, 400),
    ({'absence_details': {'title': True}}, 400),
])
def test_edit_revalidates_fields_and_rolls_back(seeded, changes, code):
    receipt = submit(seeded, request_payload())
    before = seeded.rows('change_requests')
    response = manage(seeded, receipt, 'EDIT', request_payload(**changes))
    assert response.status_code == code, response.text
    assert seeded.rows('change_requests') == before


def test_edit_closed_month_is_refused_but_withdrawal_is_allowed(seeded):
    receipt = submit(seeded, request_payload())
    seeded.post('/api/duty/month-close', {'month': '2030-06', 'closed': True,
                                        'baseVersion': seeded.data()['dataVersion']})
    before = seeded.rows('change_requests')
    response = manage(seeded, receipt, 'EDIT', request_payload(reason='수정'))
    assert response.status_code == 423, response.text
    assert seeded.rows('change_requests') == before
    assert manage(seeded, receipt, 'WITHDRAW').status_code == 200


def test_edit_overlap_with_another_pending_request_is_refused(seeded):
    receipt = submit(seeded, request_payload(absence_end_date=DAY_A))
    submit(seeded, request_payload(target_date=DAY_C, absence_end_date=DAY_C))
    before = seeded.rows('change_requests')
    response = manage(seeded, receipt, 'EDIT', request_payload())
    assert response.status_code == 409, response.text
    assert seeded.rows('change_requests') == before


def test_edit_schedule_conflict_does_not_partially_update(seeded):
    receipt = submit(seeded, request_payload('SUBSTITUTE'))
    seeded.save({'absences': [absence(name='C')]})
    before = seeded.rows('change_requests')
    response = manage(seeded, receipt, 'EDIT', request_payload('SUBSTITUTE', reason='수정'))
    assert response.status_code == 409, response.text
    assert seeded.rows('change_requests') == before


@pytest.mark.parametrize('decision', ['APPROVE', 'REJECT'])
def test_processed_request_cannot_be_edited_or_withdrawn(seeded, decision):
    receipt = submit(seeded, request_payload())
    assert seeded.action(receipt['requestId'], decision).status_code == 200
    before = seeded.rows('change_requests')
    assert manage(seeded, receipt, 'EDIT', request_payload()).status_code == 409
    assert manage(seeded, receipt, 'WITHDRAW').status_code == 409
    assert seeded.rows('change_requests') == before


def test_approval_withdrawal_race_has_one_winner(seeded):
    receipt = submit(seeded, request_payload())
    barrier = threading.Barrier(2)
    def run(action):
        client = TestClient(seeded.module.app)
        barrier.wait(timeout=5)
        if action == 'APPROVE':
            return client.post('/api/duty/request/action',
                               json={'request_id': receipt['requestId'], 'action': action}, headers=AUTH)
        return client.post('/api/duty/request/manage',
                           json={'receipt_token': receipt['receiptToken'], 'action': 'WITHDRAW'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(run, ['APPROVE', 'WITHDRAW']))
    assert sum(r.status_code == 200 for r in responses) == 1, [r.text for r in responses]
    assert all(r.status_code in (200, 400, 409) for r in responses)
    final = status(seeded, receipt)['status']
    assert final in ('APPROVED', 'CANCELLED')
    assert bool(seeded.data()['absences']) == (final == 'APPROVED')


@pytest.mark.parametrize('decision', ['APPROVE', 'REJECT'])
def test_edit_revision_refuses_stale_decision_and_accepts_fresh_decision(seeded, decision):
    receipt = submit(seeded, request_payload())
    pending = seeded.get('/api/duty/requests', headers=AUTH).json()['requests'][0]
    assert pending.get('revision') == 0
    response = manage(seeded, receipt, 'EDIT', request_payload(reason='수정된 내용을 확인해야 함'))
    assert response.status_code == 200, response.text
    assert response.json()['requestRevision'] == 1
    assert status(seeded, receipt)['revision'] == 1
    before = seeded.data()
    stale = seeded.action(receipt['requestId'], decision, request_revision=0)
    assert stale.status_code == 409, stale.text
    assert seeded.data() == before
    assert status(seeded, receipt)['status'] == 'PENDING'
    fresh = seeded.action(receipt['requestId'], decision, request_revision=1)
    assert fresh.status_code == 200, fresh.text
    assert status(seeded, receipt)['status'] == ('APPROVED' if decision == 'APPROVE' else 'REJECTED')


def test_revision_migration_keeps_old_pending_request_usable(seeded):
    receipt = submit(seeded, request_payload())
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        columns = {r[1] for r in db.execute('PRAGMA table_info(change_requests)')}
        if 'revision' in columns:
            db.execute('ALTER TABLE change_requests DROP COLUMN revision')
    assert status(seeded, receipt).get('revision') == 0
    seeded.module.init_db()
    assert seeded.get('/api/duty/requests', headers=AUTH).json()['requests'][0]['revision'] == 0
    assert seeded.action(receipt['requestId']).status_code == 200


def test_save_history_before_after_comes_from_server_and_preserves_old_history(seeded):
    old = copy.deepcopy(seeded.data()['history'])
    workers = [{'name': 'C', 'role': '당직', 'isSubstitute': False, 'zeroScore': False}]
    seeded.save({'schedules': {DAY_A: workers, DAY_B: SEED['schedules'][DAY_B]},
                 'historyLog': {'type': '수정', 'dateKey': DAY_A, 'details': '일정 변경',
                                'before': {'fake': True}, 'after': {'fake': True}}})
    item = seeded.data()['history'][0]
    assert item['before']['schedules'][DAY_A][0]['name'] == 'A'
    assert item['before']['schedules'][DAY_C][0]['name'] == 'A'
    assert item['after']['schedules'] == {DAY_A: workers, DAY_C: []}
    assert 'fake' not in str(item['before']) + str(item['after'])
    assert seeded.data()['history'][1:] == old


def test_save_without_client_history_records_absence_before_after(seeded):
    seeded.save({'absences': [absence(education=EDUCATION)]})
    first = seeded.data()['history'][0]
    assert first['before']['absences'] == []
    assert first['after']['absences'] == [absence(education=EDUCATION)]
    seeded.save({'absences': [absence(end=DAY_C)]})
    latest = seeded.data()['history'][0]
    assert latest['before']['absences'] == [absence(education=EDUCATION)]
    assert latest['after']['absences'] == [absence(end=DAY_C, education=EDUCATION)]


@pytest.mark.parametrize('kind', ['SWAP', 'SUBSTITUTE', 'ABSENCE'])
def test_approval_history_records_actual_schedule_and_absence_before_after(seeded, kind):
    receipt = submit(seeded, request_payload(kind, absence_details=EDUCATION))
    original = seeded.data()['schedules']
    assert seeded.action(receipt['requestId']).status_code == 200
    current = seeded.data()
    history = current['history'][0]
    assert history['before']['schedules'][DAY_A] == original[DAY_A]
    assert history['after']['schedules'][DAY_A] == current['schedules'].get(DAY_A, [])
    if kind == 'ABSENCE':
        assert history['before']['absences'] == []
        assert history['after']['absences'] == current['absences']
    elif kind == 'SUBSTITUTE':
        assert current['schedules'][DAY_A][0]['zeroScore'] is False
    else:
        assert history['before']['schedules'][DAY_B] == original[DAY_B]
        assert history['after']['schedules'][DAY_B] == current['schedules'][DAY_B]


def test_old_history_rows_remain_compatible_without_optional_snapshots(seeded):
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        db.execute("INSERT INTO change_history (timestamp, type, date_key, details) VALUES ('2030-01-01', '과거', ?, '과거 이력')", (DAY_A,))
    item = seeded.data()['history'][0]
    assert item['details'] == '과거 이력'
    assert 'before' not in item and 'after' not in item


def test_restoration_preserves_structured_history_added_after_backup(seeded):
    backup = Path(seeded.module.take_named_snapshot('premigration', 'usability'))
    seeded.save({'absences': [absence(education=EDUCATION)],
                 'historyLog': {'type': '교육 등록', 'details': '보존할 구조화 이력'}})
    original = seeded.data()['history'][0]
    response = seeded.post('/api/system/restore', {'name': backup.name,
                                                  'baseVersion': seeded.data()['dataVersion']})
    assert response.status_code == 200, response.text
    restored = next(item for item in seeded.data()['history'] if item['details'] == original['details'])
    assert restored['before'] == original['before']
    assert restored['after'] == original['after']
