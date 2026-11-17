"""A dinner confirmation atomically releases only its selected duty date."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import sqlite3
import threading

import pytest

from test_backend_regression import AUTH, load_app
from test_dinner_api import dinner_site, publish, op, send, assign, key_headers
from test_backend_holiday_supplement import (annual, applied, TARGET, CONTRACT,
    preview as holiday_preview, apply_payload as holiday_payload, post as holiday_post)

KST = timezone(timedelta(hours=9))


@pytest.fixture
def duty_site(dinner_site):
    site = dinner_site
    poll = publish(site)
    target = poll['candidates'][0]['date']
    selected = datetime.fromisoformat(target).date()
    neighbors = [(selected + timedelta(days=offset)).isoformat() for offset in (-1, 1)]
    site.save({'schedules': {
        neighbors[0]: [{'name':'B','role':'이전날','isSubstitute':True,'zeroScore':True}],
        target: [{'name':'A','role':'정규 당직'}, {'name':'C','role':'대체 당직','isSubstitute':True,'zeroScore':False}],
        neighbors[1]: [{'name':'B','role':'다음날','isSubstitute':False,'zeroScore':False}]},
        'comments': {target:'원래 메모  \n둘째 줄', neighbors[1]:'다음날 보존'},
        'noDuty': {neighbors[0]:'이미 지정한 휴무'}})
    site.dinner_poll = poll
    site.dinner_target = target
    site.dinner_neighbors = neighbors
    return site


def confirmation(site, poll=None, **changes):
    poll = poll or site.dinner_poll
    return op(action='CONFIRM', expectedRevision=poll['revision'],
        baseVersion=site.data()['dataVersion'], selectedCandidateId=poll['candidates'][0]['id'],
        venue='식당', notice='모여 주세요.', **changes)


def confirm(site, body=None, poll=None, **kwargs):
    poll = poll or site.dinner_poll
    return send(site, '/polls/' + poll['id'] + '/action', body or confirmation(site, poll), **kwargs)


def private_poll(site, poll_id=None):
    with site.module.get_db_connection(readonly=True) as connection:
        row = connection.execute('SELECT data_json FROM dinner_polls WHERE id=?',
                                 (poll_id or site.dinner_poll['id'],)).fetchone()
        return json.loads(row[0])


def assert_untouched_neighbors(before, after, target):
    assert {k:v for k,v in before['schedules'].items() if k != target} == {k:v for k,v in after['schedules'].items() if k != target}
    assert {k:v for k,v in before['comments'].items() if k != target} == {k:v for k,v in after['comments'].items() if k != target}
    assert {k:v for k,v in before['noDuty'].items() if k != target} == {k:v for k,v in after['noDuty'].items() if k != target}
    for field in ('rules','customHolidays','members','absences','absenceTypes','globalMemo','closedMonths'):
        assert after[field] == before[field], field


def test_confirm_releases_only_selected_day_preserves_memo_and_records_private_before(duty_site):
    site = duty_site
    before = site.data()
    result = confirm(site)
    assert result.status_code == 200, result.text
    after = site.data()
    target = site.dinner_target
    assert after['schedules'].get(target, []) == []
    assert after['comments'][target] == before['comments'][target] + '\n석식간담회'
    assert after['noDuty'][target] == '석식간담회'
    assert after['dataVersion'] == before['dataVersion'] + 1
    assert_untouched_neighbors(before, after, target)
    effect = result.json()['poll']['dutyEffect']
    assert effect['date'] == target and effect['memo'] == '석식간담회' and effect['noDuty'] is True
    assert effect['dataVersion'] == after['dataVersion']
    assert effect['appliedAt']
    private = private_poll(site)['_dutyEffect']
    assert private['before']['dayMeta']['comment'] == before['comments'][target]
    assert [w['worker_name'] for w in private['before']['workers']] == ['A', 'C']
    public = site.client.get('/api/dinner/polls/' + site.dinner_poll['id'] + '?name=A').json()['poll']
    assert '_dutyEffect' not in public and 'before' not in public['dutyEffect']
    assert 'dayMeta' not in json.dumps(public)
    history = after['history'][0]
    assert history['type'] == '석식간담회 확정'
    assert history['before']['schedules'][target] == before['schedules'][target]
    assert history['after']['schedules'][target] == []
    site.module.imported_history_snapshots(history)


def test_manage_and_detail_include_version_from_same_database_snapshot(duty_site):
    site = duty_site
    version = site.data()['dataVersion']
    assert site.client.get('/api/dinner/manage', headers=AUTH).json()['dutyDataVersion'] == version
    assert site.client.get('/api/dinner/polls/' + site.dinner_poll['id'] + '?name=A').json()['dutyDataVersion'] == version


@pytest.mark.parametrize('previous,expected', [('RULE21','석식간담회'), ('RULE_MAJOR_DAY','석식간담회'),
    ('직접 지정한 휴무','직접 지정한 휴무'), (True,True)])
def test_manual_no_duty_reason_preserved_but_automatic_reasons_become_manual(duty_site, previous, expected):
    site = duty_site
    before = site.data()
    site.save({'noDuty': dict(before['noDuty'], **{site.dinner_target:previous})})
    response = confirm(site)
    assert response.status_code == 200, response.text
    assert site.data()['noDuty'][site.dinner_target] == expected
    assert site.data()['schedules'].get(site.dinner_target, []) == []


@pytest.mark.parametrize('memo', ['', '석식간담회', '기존 메모\n석식간담회\n마지막 줄', '끝 공백  \n'])
def test_memo_append_is_exact_and_does_not_duplicate_existing_line(duty_site, memo):
    site = duty_site
    site.save({'comments': {site.dinner_target:memo}})
    response = confirm(site)
    assert response.status_code == 200, response.text
    expected = memo if '석식간담회' in memo.splitlines() else (memo + '\n석식간담회' if memo else '석식간담회')
    assert site.data()['comments'][site.dinner_target] == expected


@pytest.mark.parametrize('mode,status', [('missing',428), ('stale',409), ('closed',423), ('past',400)])
def test_confirmation_revalidates_duty_context_and_rolls_back_poll_too(duty_site, mode, status):
    site = duty_site
    body = confirmation(site)
    if mode == 'missing':
        body.pop('baseVersion')
    elif mode == 'stale':
        site.save({'globalMemo':'관리자가 먼저 저장'})
    elif mode == 'closed':
        with site.module.get_db_connection(readonly=False) as connection:
            connection.execute("INSERT INTO app_settings(key,value) VALUES('closed_months',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                               (json.dumps([site.dinner_target[:7]]),))
            connection.commit()
    else:
        poll = private_poll(site)
        poll['candidates'][0]['date'] = (datetime.now(KST).date() - timedelta(days=1)).isoformat()
        with site.module.get_db_connection(readonly=False) as connection:
            connection.execute('UPDATE dinner_polls SET data_json=? WHERE id=?', (json.dumps(poll),poll['id']))
            connection.commit()
    before, old_poll = site.data(), private_poll(site)
    response = confirm(site, body)
    assert response.status_code == status, response.text
    assert site.data() == before
    assert private_poll(site) == old_poll


def test_confirm_retry_does_not_snapshot_version_or_history_twice(duty_site, monkeypatch):
    site = duty_site
    calls = []
    snapshot = site.module.take_auto_snapshot
    def record(kind):
        calls.append(kind)
        return snapshot(kind)
    monkeypatch.setattr(site.module, 'take_auto_snapshot', record)
    body = confirmation(site)
    first = confirm(site, body)
    assert first.status_code == 200, first.text
    before = site.data()
    second = confirm(site, body)
    assert second.status_code == 200, second.text
    assert first.json() == second.json()
    assert len(calls) == 1
    assert site.data() == before
    snapshots = list(Path(site.module.backup_dir_path()).glob('auto_dinner_*.db'))
    assert len(snapshots) == 1
    with sqlite3.connect(snapshots[0]) as connection:
        assert connection.execute('SELECT COUNT(*) FROM schedules WHERE date_key=?', (site.dinner_target,)).fetchone()[0] == 2
        assert json.loads(connection.execute('SELECT data_json FROM dinner_polls WHERE id=?',
                          (site.dinner_poll['id'],)).fetchone()[0])['status'] == 'OPEN'


def test_snapshot_failure_does_not_confirm_or_remove_any_duty(duty_site, monkeypatch):
    site = duty_site
    before, poll = site.data(), private_poll(site)
    def fail(_kind):
        raise OSError('synthetic backup storage failure')
    monkeypatch.setattr(site.module, 'take_auto_snapshot', fail)
    response = confirm(site)
    assert response.status_code in (500,503), response.text
    assert site.data() == before and private_poll(site) == poll


def test_empty_snapshot_result_also_aborts_confirmation(duty_site, monkeypatch):
    site = duty_site
    before, poll = site.data(), private_poll(site)
    monkeypatch.setattr(site.module, 'take_auto_snapshot', lambda _kind: '')
    response = confirm(site)
    assert response.status_code == 503, response.text
    assert site.data() == before and private_poll(site) == poll


def test_failure_after_duty_write_rolls_back_poll_history_version_and_rows(duty_site):
    site = duty_site
    with site.module.get_db_connection(readonly=False) as connection:
        connection.execute("CREATE TRIGGER dinner_test_commit_failure BEFORE INSERT ON dinner_operations "
                           "BEGIN SELECT RAISE(ABORT,'synthetic operation receipt failure'); END")
        connection.commit()
    before, poll = site.data(), private_poll(site)
    response = confirm(site)
    assert response.status_code == 503, response.text
    assert site.data() == before and private_poll(site) == poll


def test_old_admin_screen_cannot_restore_workers_after_confirmation(duty_site):
    site = duty_site
    stale = site.data()
    result = confirm(site)
    assert result.status_code == 200, result.text
    after = site.data()
    overwrite = site.client.post('/api/duty/save-all', json={'baseVersion':stale['dataVersion'],
        'schedules':stale['schedules'],'comments':stale['comments'],'noDuty':stale['noDuty']}, headers=AUTH)
    assert overwrite.status_code == 409, overwrite.text
    assert site.data() == after


def test_confirmation_and_duty_save_race_have_one_version_winner(duty_site):
    site = duty_site
    before = site.data()
    body = confirmation(site)
    barrier = threading.Barrier(2)
    def confirm_worker():
        barrier.wait(timeout=5)
        response = confirm(site,body)
        return 'dinner', response.status_code
    def save_worker():
        barrier.wait(timeout=5)
        response = site.client.post('/api/duty/save-all',json={'baseVersion':before['dataVersion'],
            'globalMemo':'동시 저장 공지'},headers=AUTH)
        return 'duty', response.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result(timeout=15) for future in [pool.submit(confirm_worker),pool.submit(save_worker)]]
    assert sorted(status for _,status in results) == [200,409], results
    after = site.data()
    assert after['dataVersion'] == before['dataVersion'] + 1
    winner = next(name for name,status in results if status == 200)
    if winner == 'dinner':
        assert after['schedules'].get(site.dinner_target,[]) == []
        assert after['globalMemo'] == before['globalMemo']
    else:
        assert after['schedules'] == before['schedules']
        assert private_poll(site)['status'] == 'OPEN'
        assert after['globalMemo'] == '동시 저장 공지'


def test_confirm_broadcasts_existing_duty_sync_only_after_commit(duty_site, monkeypatch):
    site = duty_site
    events = []
    def receive(message):
        if message.get('event') == 'DATA_SYNCED':
            with site.module.get_db_connection(readonly=True) as connection:
                assert connection.execute('SELECT COUNT(*) FROM schedules WHERE date_key=?', (site.dinner_target,)).fetchone()[0] == 0
        events.append(message)
    monkeypatch.setattr(site.module, 'broadcast_from_thread', receive)
    response = confirm(site)
    assert response.status_code == 200, response.text
    assert any(e.get('event') == 'DATA_SYNCED' for e in events)
    assert any(e.get('type') == 'dinner_updated' for e in events)


def test_ca_approved_key_can_confirm_but_name_or_pending_key_cannot(duty_site):
    site = duty_site
    assign(site)
    key = secrets.token_urlsafe(32)
    connection = send(site, '/connections', op(memberName='A',deviceLabel='CA PC'),key=key,admin=False).json()['connection']
    before = site.data()
    assert confirm(site,key=key,admin=False).status_code == 403
    assert site.data() == before
    approve = send(site, '/connections/' + connection['id'] + '/decision',op(decision='APPROVE'))
    assert approve.status_code == 200, approve.text
    result = confirm(site,key=key,admin=False)
    assert result.status_code == 200, result.text
    assert site.data()['schedules'].get(site.dinner_target, []) == []


def test_cancellation_keeps_applied_duty_day_and_later_admin_edits_unchanged(duty_site):
    site = duty_site
    result = confirm(site)
    assert result.status_code == 200, result.text
    poll = result.json()['poll']
    current = site.data()
    site.save({'comments':dict(current['comments'],**{site.dinner_target:'관리자가 나중에 수정'})})
    before = site.data()
    cancelled = send(site,'/polls/'+poll['id']+'/action',op(action='CANCEL',expectedRevision=poll['revision'],notice='취소'))
    assert cancelled.status_code == 200, cancelled.text
    assert site.data() == before
    assert cancelled.json()['poll']['dutyEffect'] == poll['dutyEffect']


def test_two_polls_can_share_selected_day_without_duplicate_memo(duty_site):
    site = duty_site
    first = confirm(site)
    assert first.status_code == 200, first.text
    another = publish(site)
    second = confirm(site,poll=another)
    assert second.status_code == 200, second.text
    assert site.data()['comments'][site.dinner_target].splitlines().count('석식간담회') == 1
    assert site.data()['schedules'].get(site.dinner_target, []) == []


def test_pending_duty_request_cannot_repopulate_the_confirmed_no_duty_day(duty_site):
    site = duty_site
    requested = site.submit('SUBSTITUTE',requester_name='A',target_date=site.dinner_target,substitute_name='B')
    assert requested.status_code == 200, requested.text
    request_id = requested.json()['requestId']
    result = confirm(site)
    assert result.status_code == 200, result.text
    before = site.data()
    approval = site.action(request_id)
    assert approval.status_code == 409, approval.text
    assert site.data() == before


def test_older_confirmed_poll_is_not_backfilled_when_read(duty_site):
    site = duty_site
    poll = private_poll(site)
    poll.update(status='CONFIRMED',selectedCandidateId=poll['candidates'][0]['id'])
    with site.module.get_db_connection(readonly=False) as connection:
        connection.execute('UPDATE dinner_polls SET data_json=? WHERE id=?', (json.dumps(poll),poll['id']))
        connection.commit()
    before = site.data()
    detail = site.client.get('/api/dinner/polls/'+poll['id']+'?name=A').json()['poll']
    assert 'dutyEffect' not in detail
    assert site.data() == before


def test_holiday_target_cancellation_preserves_consumed_reservation_and_validates_backups(annual):
    site = annual
    applied(site)
    other_day = '2027-04-14'
    other_draft, plan = holiday_preview(site,date_key=other_day)
    assert plan['eligible'], plan
    other_result = holiday_post(site,'/api/duty/holiday-supplement/apply',holiday_payload(other_draft,plan))
    assert other_result.status_code == 200, other_result.text
    deadline = (datetime.fromisoformat(TARGET).date() - timedelta(days=1)).isoformat() + 'T18:00'
    created = send(site,'/polls',op(title='공휴일 석식',description='',deadline=deadline,
        candidates=[dict(id='selected',date=TARGET,time='18:30'),dict(id='other',date='2027-06-17',time='18:30')],
        targets=['M1','F2']))
    assert created.status_code == 200, created.text
    poll = created.json()['poll']
    posted = send(site,'/polls/'+poll['id']+'/action',op(action='PUBLISH',expectedRevision=poll['revision']))
    assert posted.status_code == 200, posted.text
    poll = posted.json()['poll']
    before = site.data()
    private_before = site.module._read_duty_data(private_supplements=True)['holidaySupplements']
    result = confirm(site,poll=poll)
    assert result.status_code == 200, result.text
    after = site.data()
    assert after['schedules'].get(TARGET,[]) == [] and after['noDuty'][TARGET] == '석식간담회'
    assert_untouched_neighbors(before,after,TARGET)
    metadata = site.module._read_duty_data(private_supplements=True)['holidaySupplements']
    event = next(e for e in metadata['events'] if e['date_key'] == TARGET)
    old = next(e for e in private_before['events'] if e['date_key'] == TARGET)
    assert event['state'] == 'CANCELLED' and event['credit_state'] == 'CONSUMED'
    assert event['current_slot_binding'] == [] and event['cancelled_at'] and event['updated_at']
    for field in ('allocation_seq','selected_ordinal','end_ordinal','female_credit','order_snapshot','annual_basis_hash'):
        assert event[field] == old[field]
    assert metadata['bases'] == private_before['bases']
    other_event = next(e for e in metadata['events'] if e['date_key'] == other_day)
    assert other_event == next(e for e in private_before['events'] if e['date_key'] == other_day)
    site.module.validate_holiday_supplements(metadata)
    with site.module.get_db_connection(readonly=True) as connection:
        site.module.hs_validate_restore_binding(connection.cursor(),metadata)
    exported = site.client.get('/api/system/export-json',headers=AUTH)
    assert exported.status_code == 200, exported.text
    assert exported.json()['holidaySupplements']['events'][0]['state'] == 'CANCELLED'
    # all-data intentionally redacts private supplement history; validate the
    # authenticated backup representation that is actually accepted on import.
    site.module.imported_history_snapshots(exported.json()['history'][0])
    imported = site.client.post('/api/system/import-json',json={'data':exported.json(),
        'baseVersion':after['dataVersion']},headers=CONTRACT)
    assert imported.status_code == 200, imported.text
    assert site.data()['schedules'].get(TARGET,[]) == []
    assert site.data()['holidaySupplements']['events'][0]['credit_state'] == 'CONSUMED'
    snapshot = Path(site.module.take_auto_snapshot('dinner'))
    restored = site.client.post('/api/system/restore',json={'name':snapshot.name,'baseVersion':site.data()['dataVersion']},headers=CONTRACT)
    assert restored.status_code == 200, restored.text
    assert site.data()['schedules'].get(TARGET,[]) == []
    assert site.data()['holidaySupplements']['events'][0]['state'] == 'CANCELLED'
