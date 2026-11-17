"""Viewer leave date planning and atomic, receipt-owned segmented approvals."""
import copy
import json
import re
import sqlite3
from pathlib import Path

import pytest
from test_backend_regression import AUTH, SEED, load_app
from test_backend_amendments import own, manage, payload

START, END = '2026-07-16', '2026-07-21'
INCLUDED = ['2026-07-16', '2026-07-20', '2026-07-21']
EXCLUDED = ['2026-07-17', '2026-07-18', '2026-07-19']
RANGES = [{'start': START, 'end': START}, {'start': '2026-07-20', 'end': END}]


@pytest.fixture
def seeded(load_app):
    app = load_app('source')
    app.save({**copy.deepcopy(SEED), 'absenceTypes': ['연차', '특별휴가', '교육', '출장', '파견'],
              'schedules': {f'2026-07-{day}': [{'name': 'A', 'role': '당직'}, {'name': 'B', 'role': '당직'}] for day in range(16, 22)}})
    return app


def submit(app, **changes):
    return app.submit('ABSENCE', target_date=changes.pop('target_date', START),
                      absence_end_date=changes.pop('absence_end_date', END), **changes)


def approved(app, **changes):
    response = submit(app, **changes)
    assert response.status_code == 200, response.text
    receipt = response.json()
    response = app.action(receipt['requestId'])
    assert response.status_code == 200, response.text
    return receipt, response.json()


def amendment_payload(**changes):
    return payload(target_date=changes.pop('target_date', START),
                   absence_end_date=changes.pop('absence_end_date', END),
                   absence_type=changes.pop('absence_type', '연차'), absence_details=None, **changes)


def assert_ranges(app, ranges=RANGES):
    assert sorted((a['start'], a['end']) for a in app.data()['absences']) == [(r['start'], r['end']) for r in ranges]


@pytest.mark.parametrize('kind', ['연차', '특별휴가'])
def test_one_request_approval_creates_only_actual_leave_spans_and_releases_only_included_duties(seeded, kind):
    response = submit(seeded, absence_type=kind)
    assert response.status_code == 200, response.text
    receipt = response.json()
    rows = seeded.get('/api/duty/requests', headers=AUTH).json()['requests']
    assert len(rows) == 1 and rows[0]['affectedDates'] == INCLUDED
    plan = {'ranges': RANGES, 'excluded': EXCLUDED, 'days': 3}
    assert rows[0]['absence_date_plan'] == plan
    assert own(seeded, receipt)['absence_date_plan'] == plan
    result = seeded.action(receipt['requestId'])
    assert result.status_code == 200, result.text
    assert result.json()['removedScheduleDates'] == INCLUDED
    assert result.json()['removedScheduleCount'] == 3
    assert_ranges(seeded)
    assert len(seeded.rows('change_requests')) == 1
    data = seeded.data()
    for day in INCLUDED: assert [w['name'] for w in data['schedules'][day]] == ['B']
    for day in EXCLUDED: assert [w['name'] for w in data['schedules'][day]] == ['A', 'B']
    status = own(seeded, receipt)
    assert status['released_dates'] == INCLUDED and status['can_amend'] is True
    assert status['absence_date_plan'] == plan
    assert status['amendable_absence']['start'] == START and status['amendable_absence']['end'] == END
    assert len(status['amendable_absence']['segments']) == 2
    seeded.save({'customHolidays': {'2026-07-20': {'name': '추가 휴일'}}})
    for url in ['/api/duty/my-requests?name=A', '/api/duty/my-requests?name=A&involving=1']:
        assert seeded.get(url).json()['requests'][0]['absence_date_plan'] == plan
        assert receipt['receiptToken'] not in seeded.get(url).text
    assert own(seeded, receipt)['absence_date_plan'] == plan


def test_current_custom_holidays_are_authoritative_at_submission_edit_and_approval(seeded):
    seeded.save({'customHolidays': {'2026-07-17': {'name': '해제', 'isRemoved': True},
                                      '2026-07-20': {'name': '지정 휴일'},
                                      '2026-07-18': {'name': '토요일 해제', 'isRemoved': True}}})
    receipt = submit(seeded).json()
    assert own(seeded, receipt)['absence_date_plan']['ranges'] == [
        {'start': START, 'end': '2026-07-17'}, {'start': END, 'end': END}]
    seeded.save({'customHolidays': {'2026-07-21': {'name': '추가 휴일'}}})
    result = seeded.action(receipt['requestId'])
    assert result.status_code == 200, result.text
    assert result.json()['removedScheduleDates'] == [START, '2026-07-20']
    assert_ranges(seeded, [{'start': START, 'end': START}, {'start': '2026-07-20', 'end': '2026-07-20'}])


@pytest.mark.parametrize('start,end', [('2026-07-17', '2026-07-19'), ('2026-07-18', '2026-07-19')])
def test_empty_leave_rejected_on_submit_and_pending_edit_without_mutation(seeded, start, end):
    before = seeded.data(), seeded.rows('change_requests')
    response = submit(seeded, target_date=start, absence_end_date=end)
    assert response.status_code == 400, response.text
    assert (seeded.data(), seeded.rows('change_requests')) == before
    receipt = submit(seeded).json()
    before = seeded.data(), seeded.rows('change_requests')
    response = manage(seeded, receipt, 'EDIT', amendment_payload(target_date=start, absence_end_date=end))
    assert response.status_code == 400, response.text
    assert (seeded.data(), seeded.rows('change_requests')) == before


def test_holiday_change_to_empty_blocks_pending_approval_and_keeps_request(seeded):
    receipt = submit(seeded, target_date=START, absence_end_date=START).json()
    seeded.save({'customHolidays': {START: {'name': '새 휴일'}}})
    assert seeded.get('/api/duty/requests', headers=AUTH).json()['requests'][0]['blocker']
    assert own(seeded, receipt)['absence_date_plan']['days'] == 0
    before = seeded.data(), seeded.rows('change_requests')
    assert seeded.action(receipt['requestId']).status_code == 400
    assert (seeded.data(), seeded.rows('change_requests')) == before


@pytest.mark.parametrize('kind', ['교육', '출장', '파견'])
def test_nonleave_absence_remains_continuous_including_holidays(seeded, kind):
    receipt, result = approved(seeded, absence_type=kind)
    assert result['removedScheduleCount'] == 6
    assert_ranges(seeded, [{'start': START, 'end': END}])
    assert own(seeded, receipt)['absence_date_plan'] == {'ranges': [{'start': START, 'end': END}], 'excluded': [], 'days': 6}
    assert 'segments' not in own(seeded, receipt)['amendable_absence']


def test_overlap_and_pending_duplicate_checks_use_effective_dates(seeded):
    education = submit(seeded, absence_type='교육', target_date='2026-07-17', absence_end_date='2026-07-19').json()
    receipt = submit(seeded).json()
    assert 'requestId' in receipt
    assert submit(seeded).status_code == 409
    assert seeded.action(education['requestId']).status_code == 200
    assert seeded.get('/api/duty/requests', headers=AUTH).json()['requests'][0]['blocker'] == ''
    assert seeded.action(receipt['requestId']).status_code == 200
    assert len(seeded.data()['absences']) == 3


def test_pending_edit_preserves_single_receipt_and_revision_with_new_actual_spans(seeded):
    receipt = submit(seeded).json()
    response = manage(seeded, receipt, 'EDIT', amendment_payload(absence_end_date='2026-07-20'))
    assert response.status_code == 200, response.text
    status = own(seeded, receipt)
    assert status['revision'] == 1 and status['absence_date_plan']['days'] == 2
    assert len(seeded.rows('change_requests')) == 1
    assert seeded.action(receipt['requestId'], request_revision=0).status_code == 409
    assert seeded.action(receipt['requestId'], request_revision=1).status_code == 200


def test_group_amendment_preserves_all_source_segments_until_atomic_approval_and_supports_chain(seeded):
    receipt, _ = approved(seeded)
    original = seeded.data()['absences']
    child = manage(seeded, receipt, request=amendment_payload(target_date='2026-07-15', absence_end_date='2026-07-22')).json()
    assert child.get('receiptToken'), child
    assert seeded.data()['absences'] == original
    pending = own(seeded, child)
    assert len(pending['amendment_original']['segments']) == 2
    assert seeded.action(child['requestId']).status_code == 200
    assert_ranges(seeded, [{'start': '2026-07-15', 'end': START}, {'start': '2026-07-20', 'end': '2026-07-22'}])
    assert own(seeded, receipt)['superseded_by_id'] == child['requestId']
    assert own(seeded, child)['can_amend'] is True
    grand = manage(seeded, child, request=amendment_payload(absence_type='교육')).json()
    assert seeded.action(grand['requestId']).status_code == 200
    assert_ranges(seeded, [{'start': START, 'end': END}])
    assert own(seeded, grand)['can_amend'] is True


@pytest.mark.parametrize('action', ['REJECT', 'WITHDRAW'])
def test_group_amendment_terminal_decision_preserves_all_source_rows(seeded, action):
    receipt, _ = approved(seeded)
    before = seeded.data()['absences']
    child = manage(seeded, receipt, request=amendment_payload(absence_end_date='2026-07-22')).json()
    assert child.get('requestId'), child
    response = seeded.action(child['requestId'], action) if action == 'REJECT' else manage(seeded, child, 'WITHDRAW')
    assert response.status_code == 200
    assert seeded.data()['absences'] == before
    assert own(seeded, receipt)['can_amend'] is True


@pytest.mark.parametrize('stage', ['before_submit', 'before_approve'])
@pytest.mark.parametrize('change', ['delete', 'alter', 'recreate'])
def test_every_constituent_segment_has_identity_protection(seeded, stage, change):
    receipt, _ = approved(seeded)
    child = manage(seeded, receipt, request=amendment_payload(absence_end_date='2026-07-22')).json() if stage == 'before_approve' else None
    rows = seeded.data()['absences']
    source_rows = copy.deepcopy(rows)
    if change in ('delete', 'recreate'): rows.pop(0)
    else: rows[0]['education'] = {'notes': '관리자 변경'}
    seeded.save({'absences': rows})
    if change == 'recreate': seeded.save({'absences': source_rows})
    before = seeded.data(), seeded.rows('change_requests')
    response = seeded.action(child['requestId']) if child else manage(seeded, receipt, request=amendment_payload())
    assert response.status_code == 409, response.text
    assert (seeded.data(), seeded.rows('change_requests')) == before


def test_failed_group_insert_rolls_back_old_group_and_duties(seeded):
    receipt, _ = approved(seeded)
    child = manage(seeded, receipt, request=amendment_payload(target_date='2026-07-22', absence_end_date='2026-08-04')).json()
    assert child.get('requestId'), child
    before = seeded.data(), seeded.rows('change_requests')
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        db.execute("CREATE TRIGGER group_fault BEFORE INSERT ON absences WHEN NEW.start_date='2026-08-03' BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
    assert seeded.action(child['requestId']).status_code == 500
    assert (seeded.data(), seeded.rows('change_requests')) == before
    with sqlite3.connect(seeded.module.DB_PATH) as db: db.execute('DROP TRIGGER group_fault')
    assert seeded.action(child['requestId']).status_code == 200
    assert own(seeded, child)['can_amend'] is True


def test_builtin_holiday_dates_match_calendar_source(seeded):
    source = (Path(__file__).resolve().parents[1] / 'frontend' / 'dist' / 'index.html').read_text(encoding='utf-8')
    table = re.search(r'const BASE_HOLIDAYS = \{(.*?)\n    \};', source, re.S).group(1)
    assert set(re.findall(r'"(\d{4}-\d{2}-\d{2})"\s*:', table)) == set(seeded.module.BASE_HOLIDAY_DATES)


def test_trimmed_single_span_keeps_request_envelope_for_owned_amendments(seeded):
    receipt, _ = approved(seeded, target_date='2026-07-17', absence_end_date='2026-07-20')
    status = own(seeded, receipt)
    assert status['can_amend'] is True
    record = status['amendable_absence']
    assert record['start'] == '2026-07-17' and record['end'] == '2026-07-20'
    assert len(record['segments']) == 1
    assert record['segments'][0]['start'] == '2026-07-20'
    child = manage(seeded, receipt, request=amendment_payload()).json()
    assert seeded.action(child['requestId']).status_code == 200
    assert_ranges(seeded)


@pytest.mark.parametrize('stage', ['submit', 'approve', 'edit'])
def test_closed_included_month_remains_protected(seeded, stage):
    receipt = submit(seeded).json() if stage != 'submit' else None
    seeded.post('/api/duty/month-close', {'month': '2026-07', 'closed': True, 'reason': '검증', 'baseVersion': seeded.data()['dataVersion']})
    before = seeded.data(), seeded.rows('change_requests')
    response = submit(seeded) if stage == 'submit' else seeded.action(receipt['requestId']) if stage == 'approve' else manage(seeded, receipt, 'EDIT', amendment_payload())
    assert response.status_code == 423, response.text
    assert (seeded.data(), seeded.rows('change_requests')) == before
    if receipt: assert seeded.get('/api/duty/requests', headers=AUTH).json()['requests'][0]['blocker']


def test_excluded_only_month_is_untouched_and_does_not_block_included_open_month(seeded):
    seeded.post('/api/duty/month-close', {'month': '2026-08', 'closed': True, 'reason': '검증', 'baseVersion': seeded.data()['dataVersion']})
    receipt, _ = approved(seeded, target_date='2026-07-31', absence_end_date='2026-08-02')
    assert_ranges(seeded, [{'start': '2026-07-31', 'end': '2026-07-31'}])
    assert own(seeded, receipt)['can_amend'] is True
    child = manage(seeded, receipt, request=amendment_payload(target_date='2026-07-30', absence_end_date='2026-08-02')).json()
    assert seeded.action(child['requestId']).status_code == 200


@pytest.mark.parametrize('stage', ['submit', 'approve'])
def test_amendment_protects_closed_month_of_any_source_segment(seeded, stage):
    receipt, _ = approved(seeded, target_date='2026-07-31', absence_end_date='2026-08-03')
    assert len(own(seeded, receipt)['amendable_absence']['segments']) == 2
    child = manage(seeded, receipt, request=amendment_payload()).json() if stage == 'approve' else None
    seeded.post('/api/duty/month-close', {'month': '2026-08', 'closed': True, 'reason': '검증', 'baseVersion': seeded.data()['dataVersion']})
    before = seeded.data(), seeded.rows('change_requests')
    response = seeded.action(child['requestId']) if child else manage(seeded, receipt, request=amendment_payload())
    assert response.status_code == 423, response.text
    assert (seeded.data(), seeded.rows('change_requests')) == before


def test_group_history_identity_must_match_even_when_another_history_has_same_rows(seeded):
    receipt, _ = approved(seeded)
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        db.execute('UPDATE change_requests SET approved_history_id=999999 WHERE id=?', (receipt['requestId'],))
    assert own(seeded, receipt)['can_amend'] is False
    assert manage(seeded, receipt, request=amendment_payload()).status_code == 409


def test_group_receipt_snapshot_protects_all_constituents_and_unchanged_full_save_is_safe(seeded):
    receipt, _ = approved(seeded)
    child = manage(seeded, receipt, request=amendment_payload()).json()
    source = own(seeded, child)['amendment_original']
    seeded.save({'absences': seeded.data()['absences']})
    assert own(seeded, child)['amendment_original'] == source
    assert seeded.action(child['requestId']).status_code == 200
    assert own(seeded, child)['can_amend'] is True
    histories = [r for r in seeded.data()['history'] if r['type'] == '휴가·부재 변경(승인)']
    assert len(histories[0]['before']['absences']) == 2
    assert len(histories[0]['after']['absences']) == 2
    # Snapshot format remains compatible with the existing JSON backup validator.
    seeded.module.imported_history_snapshots(histories[0])


def test_existing_actual_overlap_blocks_whole_group_and_preserves_pending_request(seeded):
    receipt = submit(seeded).json()
    seeded.save({'absences': [{'id': 500, 'name': 'A', 'type': '파견', 'start': END, 'end': END}]})
    assert seeded.get('/api/duty/requests', headers=AUTH).json()['requests'][0]['blocker']
    before = seeded.data(), seeded.rows('change_requests')
    assert seeded.action(receipt['requestId']).status_code == 409
    assert (seeded.data(), seeded.rows('change_requests')) == before


def test_amendment_pending_edit_preserves_original_group_and_checks_empty_days(seeded):
    receipt, _ = approved(seeded)
    child = manage(seeded, receipt, request=amendment_payload(absence_end_date='2026-07-22')).json()
    source = own(seeded, child)['amendment_original']
    response = manage(seeded, child, 'EDIT', amendment_payload(target_date='2026-07-17', absence_end_date='2026-07-19'))
    assert response.status_code == 400
    assert own(seeded, child)['amendment_original'] == source
    assert manage(seeded, child, 'EDIT', amendment_payload(absence_end_date='2026-07-23')).status_code == 200
    assert own(seeded, child)['amendment_original'] == source
    assert own(seeded, child)['revision'] == 1
    assert seeded.action(child['requestId'], request_revision=0).status_code == 409
    assert seeded.action(child['requestId'], request_revision=1).status_code == 200
    assert own(seeded, child)['can_amend'] is True


def test_legacy_continuous_leave_stays_historical_until_new_amendment_is_approved(seeded):
    # Simulate an approval made by the previous server, whose leave dates were continuous.
    receipt, _ = approved(seeded, absence_type='교육')
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        db.execute("UPDATE absences SET type='연차'")
        db.execute("UPDATE change_requests SET absence_type='연차' WHERE id=?", (receipt['requestId'],))
        history_id = db.execute('SELECT approved_history_id FROM change_requests WHERE id=?', (receipt['requestId'],)).fetchone()[0]
        after = json.loads(db.execute('SELECT after_json FROM change_history WHERE id=?', (history_id,)).fetchone()[0])
        after['absences'][0]['type'] = '연차'
        db.execute('UPDATE change_history SET after_json=? WHERE id=?', (json.dumps(after), history_id))
    status = own(seeded, receipt)
    assert status['can_amend'] is True and status['absence_date_plan']['days'] == 6
    assert status['absence_date_plan']['excluded'] == []
    child = manage(seeded, receipt, request=amendment_payload()).json()
    assert_ranges(seeded, [{'start': START, 'end': END}])
    assert seeded.action(child['requestId']).status_code == 200
    assert_ranges(seeded)
    assert own(seeded, receipt)['absence_date_plan']['days'] == 6
    assert own(seeded, child)['absence_date_plan']['days'] == 3
