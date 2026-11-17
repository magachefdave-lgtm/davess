"""Atomic extra-holiday workflow with real FastAPI and temporary SQLite."""
import copy
import datetime
import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_backend_regression import AUTH, load_app

CONTRACT = {**AUTH, 'X-OnAir-Data-Contract': '1'}
TARGET = '2027-06-16'
PREFIX = '/api/duty/holiday-supplement'


def post(app, path, payload, headers=None):
    return app.client.post(path, json=payload, headers=CONTRACT if headers is None else headers)


@pytest.fixture
def annual(load_app):
    app = load_app('source')
    members = [{'id': i + 1, 'name': name, 'gender': name[0]} for i, name in enumerate(['M1', 'M2', 'M3', 'F1', 'F2', 'F3'])]
    schedules = {}
    day = datetime.date(2027, 1, 1)
    while day.year == 2027:
        key = day.isoformat()
        holiday = key in app.module.BASE_HOLIDAY_DATES
        slots = [{'name': 'F1', 'role': '주말(여)' if day.weekday() >= 5 else '공휴일(여)' if holiday else '평일(여)', 'isSubstitute': False, 'zeroScore': False}]
        if day.weekday() >= 5 or holiday:
            slots.insert(0, {'name': 'M2' if day.weekday() < 5 else 'M1', 'role': '주말(남)' if day.weekday() >= 5 else '공휴일(남)', 'isSubstitute': False, 'zeroScore': False})
        schedules[key] = slots
        day += datetime.timedelta(days=1)
    schedules[TARGET] = [{'name': 'F2', 'role': '평일(여)', 'isSubstitute': False, 'zeroScore': True}]
    app.save({'members': members, 'schedules': schedules, 'customHolidays': {}, 'absences': [], 'noDuty': {},
              'absenceTypes': ['연차', '교육'], 'comments': {TARGET: '보존할 메모'},
              'rules': {'algoType': 'FIXED_ORDER', 'fixedOrders': {'weekday': ['F1', 'F2', 'F3'], 'weekendM': ['M1', 'M2', 'M3'], 'weekendF': ['F1', 'F2', 'F3'], 'holidayM': ['M1', 'M2', 'M3'], 'holidayF': ['F1', 'F2', 'F3'], 'majorM': ['M1', 'M2', 'M3'], 'majorF': ['F1', 'F2', 'F3']}}})
    return app


def preview(app, **changes):
    draft = {'holidayIntent': 'SET_CUSTOM', 'date_key': TARGET, 'name': '추가 공휴일', 'is_major': False, 'baseVersion': app.data()['dataVersion'], **changes}
    response = post(app, PREFIX + '/preview', draft)
    assert response.status_code == 200, response.text
    return draft, response.json()


def apply_payload(draft, plan):
    return {'operationId': str(uuid.uuid4()), 'draft': draft, 'baseVersion': plan['baseVersion'], 'reviewDigest': plan['reviewDigest']}


def applied(app):
    draft, plan = preview(app)
    assert plan['eligible'], plan
    payload = apply_payload(draft, plan)
    result = post(app, PREFIX + '/apply', payload)
    assert result.status_code == 200, result.text
    return payload, result.json()


def test_preview_reads_annual_tail_without_any_write_and_protects_admin(annual):
    before = annual.data(), annual.rows('change_history')
    draft, plan = preview(annual)
    assert plan['eligible'] and plan['effect'] == 'ADD'
    assert plan['candidate']['name'] == 'M3'
    assert plan['affectedDates'] == [TARGET]
    assert (annual.data(), annual.rows('change_history')) == before
    assert post(annual, PREFIX + '/preview', draft, headers={}).status_code == 401


def test_apply_changes_only_target_preserves_female_and_commits_metadata(annual):
    before = annual.data()
    payload, result = applied(annual)
    after = annual.data()
    assert result['changedDates'] == [TARGET]
    assert after['dataVersion'] == before['dataVersion'] + 1
    assert {k: v for k, v in before['schedules'].items() if k != TARGET} == {k: v for k, v in after['schedules'].items() if k != TARGET}
    female = next(w for w in after['schedules'][TARGET] if w['name'] == 'F2')
    assert female == {**before['schedules'][TARGET][0], 'role': '공휴일(여)'}
    assert next(w for w in after['schedules'][TARGET] if w['name'] == 'M3')['zeroScore'] is False
    assert after['comments'] == before['comments'] and after['absences'] == before['absences']
    meta = after['holidaySupplements']
    assert meta['requiredContract'] == 1 and len(meta['events']) == 1
    assert meta['events'][0]['female_credit']['consumes'] is True
    assert 'operations' not in meta and 'original_holiday' not in meta['events'][0]
    retry = post(annual, PREFIX + '/apply', payload)
    assert retry.status_code == 200, retry.text
    assert retry.json()['operationId'] == result['operationId']
    assert annual.data() == after


def test_same_operation_different_payload_rejected_and_same_date_no_extra_credit(annual):
    payload, result = applied(annual)
    changed = copy.deepcopy(payload)
    changed['draft']['name'] = '다른 이름'
    assert post(annual, PREFIX + '/apply', changed).status_code == 409
    draft, plan = preview(annual)
    assert plan['effect'] in ('NOOP', 'LABEL_ONLY')
    before = annual.data()
    second = post(annual, PREFIX + '/apply', apply_payload(draft, plan))
    assert second.status_code == 200, second.text
    assert len(annual.data()['holidaySupplements']['events']) == 1
    assert annual.data()['schedules'] == before['schedules']


def test_preview_detects_new_pending_request_even_without_data_version_change(annual):
    draft, plan = preview(annual)
    version = annual.data()['dataVersion']
    req = annual.submit('ABSENCE', requester_name='M3', target_date=TARGET, absence_end_date=TARGET, absence_type='교육')
    assert req.status_code == 200, req.text
    assert annual.data()['dataVersion'] == version
    before = annual.data()
    result = post(annual, PREFIX + '/apply', apply_payload(draft, plan))
    assert result.status_code == 409, result.text
    assert annual.data() == before


def test_activation_blocks_old_http_and_old_direct_database_writes(annual):
    applied(annual)
    before = annual.data()
    result = post(annual, '/api/duty/save-all', {'baseVersion': before['dataVersion'], 'comments': {TARGET: 'overwrite'}}, headers=AUTH)
    assert result.status_code == 428, result.text
    with sqlite3.connect(annual.module.DB_PATH) as db:
        with pytest.raises(sqlite3.DatabaseError):
            db.execute('DELETE FROM schedules WHERE date_key=?', (TARGET,))
    assert annual.data() == before
    result = post(annual, '/api/duty/save-all', {'baseVersion': before['dataVersion'], 'comments': {TARGET: '정상 새 버전 저장'}})
    assert result.status_code == 200, result.text
    assert len(annual.data()['holidaySupplements']['events']) == 1


def test_missing_or_changed_review_digest_never_applies(annual):
    draft, plan = preview(annual)
    before = annual.data()
    payload = apply_payload(draft, plan)
    payload['reviewDigest'] = '0' * 64
    assert post(annual, PREFIX + '/apply', payload).status_code == 409
    assert annual.data() == before


def test_apply_rollback_if_history_write_fails(annual, monkeypatch):
    draft, plan = preview(annual)
    before = annual.data()
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('synthetic history failure')
    monkeypatch.setattr(annual.module, 'insert_history', fail)
    result = post(annual, PREFIX + '/apply', apply_payload(draft, plan))
    assert result.status_code == 500, result.text
    assert annual.data() == before


def test_two_concurrent_retries_consume_one_credit(annual):
    # The generic fixture's 0.2s budget is deliberately smaller than an annual
    # preview+snapshot; allow bounded serialization (production default: 30s).
    annual.module.DB_TIMEOUT = 3.0
    draft, plan = preview(annual)
    payload = apply_payload(draft, plan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: post(annual, PREFIX + '/apply', payload), range(2)))
    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    assert annual.data()['dataVersion'] == plan['baseVersion'] + 1
    assert len(annual.data()['holidaySupplements']['events']) == 1


def test_approval_review_digest_detects_changed_absence_impact_before_activation(annual):
    response = annual.submit('ABSENCE', requester_name='F2', target_date=TARGET, absence_end_date='2027-06-17', absence_type='연차')
    assert response.status_code == 200, response.text
    request_id = response.json()['requestId']
    review = annual.get('/api/duty/requests', headers=AUTH).json()['requests'][0]
    assert review.get('impact_digest')
    annual.save({'customHolidays': {TARGET: {'name': '다른 관리자의 휴일'}}})
    before = annual.data()
    result = post(annual, '/api/duty/request/action', {'request_id': request_id, 'action': 'APPROVE', 'request_revision': review['revision'], 'impact_digest': review['impact_digest']})
    assert result.status_code == 409, result.text
    assert annual.data() == before


def test_server_rejects_swap_between_weekday_and_weekend(annual):
    response = annual.submit('SWAP', requester_name='F2', target_date=TARGET, swap_date='2027-06-19', swap_worker='F1')
    assert response.status_code == 400, response.text
    assert annual.rows('change_requests') == []


def test_json_export_restore_roundtrip_preserves_private_metadata_and_receipts(annual):
    payload, result = applied(annual)
    exported = annual.get('/api/system/export-json', headers=AUTH)
    assert exported.status_code == 200, exported.text
    data = exported.json()
    assert data['formatVersion'] == 2
    assert data['holidaySupplements']['operations']
    assert 'original_holiday' in data['holidaySupplements']['events'][0]
    assert annual.get('/api/system/export-json').status_code == 401
    before = annual.data()
    changed = copy.deepcopy(data)
    changed['comments'][TARGET] = '복원한 메모'
    response = post(annual, '/api/system/import-json', {'data': changed, 'baseVersion': before['dataVersion'], 'restoreLegacy': False})
    assert response.status_code == 200, response.text
    assert annual.data()['holidaySupplements'] == before['holidaySupplements']
    assert annual.data()['comments'][TARGET] == '복원한 메모'
    assert post(annual, PREFIX + '/apply', payload).status_code == 200


def test_legacy_json_restore_is_explicit_and_does_not_lower_write_contract(annual):
    legacy = annual.data()
    payload, _ = applied(annual)
    # A real pre-supplement export had no domain key at all.
    legacy.pop('holidaySupplements', None)
    body = {'data': legacy, 'baseVersion': annual.data()['dataVersion'], 'restoreLegacy': False}
    before = annual.data()
    assert post(annual, '/api/system/import-json', body).status_code == 409
    assert annual.data() == before
    body['restoreLegacy'] = True
    result = post(annual, '/api/system/import-json', body)
    assert result.status_code == 200, result.text
    current = annual.data()
    assert current['schedules'] == legacy['schedules']
    assert current['holidaySupplements']['events'] == []
    assert current['holidaySupplements']['requiredContract'] == 1
    replay = post(annual, PREFIX + '/apply', payload)
    assert replay.status_code == 200 and replay.json()['currentState'] == 'RESTORED_OUT'
    assert annual.data() == current


def test_two_additions_reverse_date_and_contiguous_cancel_tail(annual):
    first_payload, _ = applied(annual)
    earlier = '2027-04-14'
    draft, plan = preview(annual, date_key=earlier)
    assert plan['candidate']['name'] == 'M1', plan
    assert post(annual, PREFIX + '/apply', apply_payload(draft, plan)).status_code == 200
    draft, plan = preview(annual, holidayIntent='DELETE_CUSTOM')
    result = post(annual, PREFIX + '/apply', apply_payload(draft, plan))
    assert result.status_code == 200, result.text
    events = annual.data()['holidaySupplements']['events']
    first = next(e for e in events if e['date_key'] == TARGET)
    assert first['state'] == 'CANCELLED' and first['credit_state'] == 'CONSUMED'
    draft, plan = preview(annual, holidayIntent='DELETE_CUSTOM', date_key=earlier)
    result = post(annual, PREFIX + '/apply', apply_payload(draft, plan))
    assert result.status_code == 200, result.text
    assert all(e['credit_state'] == 'RELEASED' for e in annual.data()['holidaySupplements']['events'])
    version = annual.data()['dataVersion']
    retry = post(annual, PREFIX + '/apply', first_payload)
    assert retry.status_code == 200 and retry.json()['currentState'] == 'CANCELLED'
    assert annual.data()['dataVersion'] == version


def test_approved_absence_removes_actual_female_but_preserves_original_credit(annual):
    applied(annual)
    before = annual.data()['holidaySupplements']['events'][0]['female_credit']
    result = post(annual, '/api/duty/request-change', {'requester_name':'F2','target_date':TARGET,'request_type':'ABSENCE','absence_type':'교육','absence_end_date':TARGET,'reason':'교육'})
    assert result.status_code == 200, result.text
    request_id = result.json()['requestId']
    review = annual.get('/api/duty/requests', headers=AUTH).json()['requests'][0]
    body = {'request_id':request_id,'action':'APPROVE','request_revision':review['revision']}
    assert post(annual, '/api/duty/request/action', body).status_code == 428
    body['impact_digest'] = review['impact_digest']
    result = post(annual, '/api/duty/request/action', body)
    assert result.status_code == 200, result.text
    current = annual.data()
    assert [w['name'] for w in current['schedules'][TARGET]] == ['M3']
    event = current['holidaySupplements']['events'][0]
    assert event['female_credit'] == before
    assert event['current_slot_binding'] == current['schedules'][TARGET]
    draft, plan = preview(annual, holidayIntent='DELETE_CUSTOM')
    assert not plan['eligible'] and any(b['code']=='SLOT_BINDING_CHANGED' for b in plan['blockers'])


def test_actual_db_backup_restore_and_restored_out_receipt(annual):
    # The pre-feature current-version snapshot has the new empty schema.
    legacy_snapshot = annual.module.take_auto_snapshot('manual')
    payload, _ = applied(annual)
    active_snapshot = annual.module.take_auto_snapshot('manual')
    body = {'name':legacy_snapshot.rsplit('/',1)[-1], 'baseVersion':annual.data()['dataVersion'], 'restoreLegacy':True}
    result = post(annual, '/api/system/restore', body)
    assert result.status_code == 200, result.text
    assert annual.data()['holidaySupplements']['events'] == []
    retry = post(annual, PREFIX + '/apply', payload)
    assert retry.json()['currentState'] == 'RESTORED_OUT'
    body.update(name=active_snapshot.rsplit('/',1)[-1], baseVersion=annual.data()['dataVersion'])
    result = post(annual, '/api/system/restore', body)
    assert result.status_code == 200, result.text
    assert len(annual.data()['holidaySupplements']['events']) == 1
    assert annual.data()['holidaySupplements']['requiredContract'] == 1


def test_real_previous_backend_cannot_write_same_activated_database(annual, load_app, monkeypatch):
    import test_backend_regression
    from pathlib import Path
    previous = Path('/workspace/wlr_fix/WLR_OnAir_v3.18.31_Source_Python311-314_r4/backend/main.py')
    if not previous.is_file():
        pytest.skip('Previous release source is required for mixed-version validation')
    pending = post(annual, '/api/duty/request-change', {'requester_name':'F1','target_date':'2027-07-01','request_type':'ABSENCE','absence_type':'교육','absence_end_date':'2027-07-01','reason':'혼용 검증'})
    assert pending.status_code == 200, pending.text
    snapshot = annual.module.take_auto_snapshot('manual')
    applied(annual)
    before = annual.data()
    requests_before = annual.rows('change_requests')
    monkeypatch.setattr(test_backend_regression, 'SOURCE', previous)
    old = load_app('source', initialize=False)
    old.module.DB_PATH = annual.module.DB_PATH
    old.module.STORAGE_DIR = annual.module.STORAGE_DIR
    old.module.ensure_daily_backup = lambda: None
    read = old.get('/api/duty/all-data')
    assert read.status_code == 200, read.text
    result = old.post('/api/duty/save-all', {'baseVersion':before['dataVersion'],'comments':{TARGET:'구버전 덮어쓰기'}})
    assert result.status_code == 500, result.text
    result = old.post('/api/duty/request/action', {'request_id':pending.json()['requestId'],'action':'APPROVE','request_revision':0})
    assert result.status_code == 500, result.text
    result = old.post('/api/system/restore', {'name':snapshot.rsplit('/',1)[-1],'baseVersion':before['dataVersion']})
    assert result.status_code >= 400, result.text
    old.module.APP_MODE = 'VIEWER'
    result = old.post('/api/duty/request-change', {'requester_name':'F2','target_date':'2027-07-02','request_type':'ABSENCE','absence_type':'교육','absence_end_date':'2027-07-02','reason':'구형 조회용'}, auth=False)
    assert result.status_code == 500, result.text
    assert annual.data() == before
    assert annual.rows('change_requests') == requests_before


def test_guard_tampering_fails_closed_and_json_mismatch_cannot_partial_restore(annual):
    applied(annual)
    export = annual.get('/api/system/export-json', headers=AUTH).json()
    export['schedules'][TARGET] = []
    before = annual.data()
    result = post(annual, '/api/system/import-json', {'data':export,'baseVersion':before['dataVersion']})
    assert result.status_code == 400, result.text
    assert annual.data() == before
    with sqlite3.connect(annual.module.DB_PATH) as db:
        trigger = db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='schedules' AND name LIKE 'wlr_hs_contract_%'").fetchone()
        assert trigger
        db.execute('DROP TRIGGER "' + trigger[0] + '"')
    result = post(annual, '/api/duty/save-all', {'baseVersion':before['dataVersion'],'comments':{}})
    assert result.status_code == 503, result.text
    assert annual.data() == before


def test_application_import_alone_prepares_schema_without_second_init(load_app):
    app = load_app('source', initialize=False)
    response = app.get('/api/duty/all-data')
    assert response.status_code == 200, response.text
    assert response.json()['holidaySupplements'] == {'schema':1,'events':[],'bases':[],'requiredContract':0}


def test_busy_database_has_retryable_response_without_partial_apply(annual):
    draft, plan = preview(annual)
    before = annual.data()
    with sqlite3.connect(annual.module.DB_PATH) as lock:
        lock.execute('BEGIN IMMEDIATE')
        result = post(annual, PREFIX + '/apply', apply_payload(draft, plan))
        assert result.status_code == 503, result.text
    assert annual.data() == before


def test_reconcile_reactivates_unmodified_future_event_in_same_transaction(annual):
    from test_holiday_supplement_calculation import CASES
    seed = copy.deepcopy(CASES['cases'][0]['future_state'])
    payload = {k:seed[k] for k in ('members','schedules','noDuty','absences','customHolidays','rules')}
    payload['baseVersion'] = annual.data()['dataVersion']
    assert post(annual, '/api/duty/save-all', payload).status_code == 200
    for key in ('2028-06-07','2027-06-09'):
        draft, plan = preview(annual, date_key=key)
        assert plan['eligible'], plan
        result = post(annual, PREFIX+'/apply', apply_payload(draft,plan))
        assert result.status_code == 200, result.text
    assert post(annual, '/api/duty/save-all', {'baseVersion':annual.data()['dataVersion'],'comments':{'2027-01-02':'메모'}}).status_code == 200
    before = annual.data()
    event = next(e for e in before['holidaySupplements']['events'] if e['year']==2028)
    assert event['state'] == 'NEEDS_REVIEW'
    basis = max((b for b in before['holidaySupplements']['bases'] if b['year']==2028),key=lambda b:b['revision'])
    draft = {'holidayIntent':'RECONCILE','year':2028,'pool_key':'holiday|M','expected_basis_revision':basis['revision'],'reconcilePolicy':'PRESERVE_EXISTING'}
    plan = post(annual, PREFIX+'/preview',draft).json()
    assert plan['eligible'], plan
    result = post(annual, PREFIX+'/apply',apply_payload(draft,plan))
    assert result.status_code == 200, result.text
    after = annual.data()
    event = next(e for e in after['holidaySupplements']['events'] if e['year']==2028)
    assert event['state'] == 'ACTIVE'
    assert after['schedules'] == before['schedules']
    _, removal = preview(annual,date_key='2028-06-07',holidayIntent='DELETE_CUSTOM')
    assert removal['eligible'], removal


def test_closed_month_db_restore_rolls_back_every_collection(annual):
    prior = annual.module.take_auto_snapshot('manual')
    applied(annual)
    result = post(annual,'/api/duty/month-close',{'month':'2027-06','closed':True,'reason':'마감 검증','baseVersion':annual.data()['dataVersion']})
    assert result.status_code == 200, result.text
    before, history, requests = annual.data(), annual.rows('change_history'), annual.rows('change_requests')
    result = post(annual,'/api/system/restore',{'name':prior.rsplit('/',1)[-1],'baseVersion':before['dataVersion'],'restoreLegacy':True})
    assert result.status_code == 423, result.text
    assert annual.data() == before and annual.rows('change_history') == history and annual.rows('change_requests') == requests


def test_closed_event_does_not_block_restoring_only_open_month_supplement(annual):
    applied(annual)
    assert post(annual,'/api/duty/month-close',{'month':'2027-06','closed':True,'reason':'마감','baseVersion':annual.data()['dataVersion']}).status_code == 200
    exported = annual.get('/api/system/export-json',headers=AUTH).json()
    draft,plan = preview(annual,date_key='2027-07-14')
    assert post(annual,PREFIX+'/apply',apply_payload(draft,plan)).status_code == 200
    result = post(annual,'/api/system/import-json',{'data':exported,'baseVersion':annual.data()['dataVersion']})
    assert result.status_code == 200, result.text
    after = annual.data()
    assert len(after['holidaySupplements']['events']) == 1
    assert after['schedules'][TARGET] == exported['schedules'][TARGET] and '2027-06' in after['closedMonths']


def test_compatible_internal_heartbeat_updates_after_activation_without_http_header(annual):
    applied(annual)
    before = annual.data()
    annual.module.HOST_LABEL = 'new-compatible-pc'
    annual.module.record_client_heartbeat(force=True)
    with annual.module.get_db_connection(readonly=True) as conn:
        row = conn.execute('SELECT app_version FROM client_status WHERE host=?',('new-compatible-pc',)).fetchone()
    assert row and row[0] == annual.module.APP_VERSION
    assert annual.data() == before


def test_real_legacy_v2_json_requires_explicit_restore_after_activation(annual):
    exported = annual.get('/api/system/export-json',headers=AUTH).json()
    exported.pop('holidaySupplements')
    assert exported['formatVersion'] == 2  # Actual v3.18.31 exports also used format 2.
    applied(annual)
    before = annual.data()
    body = {'data':exported,'baseVersion':before['dataVersion'],'restoreLegacy':False}
    result = post(annual,'/api/system/import-json',body)
    assert result.status_code == 409, result.text
    assert annual.data() == before
    body['restoreLegacy'] = True
    result = post(annual,'/api/system/import-json',body)
    assert result.status_code == 200, result.text
    assert annual.data()['holidaySupplements']['events'] == []
    assert annual.data()['holidaySupplements']['requiredContract'] == 1
