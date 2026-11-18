"""Dinner lifecycle and capability security against isolated SQLite databases."""
import importlib
import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta, timezone
import uuid

import pytest

KST = timezone(timedelta(hours=9))
NOW = datetime(2030, 6, 10, 12, tzinfo=KST)
KEY_A = 'a' * 43
KEY_B = 'b' * 43
KEY_CA = 'c' * 43


def test_dinner_domain_contract_is_available():
    assert importlib.util.find_spec('backend.dinner_schema'), 'Dinner schema is not implemented'
    assert importlib.util.find_spec('backend.dinner_service'), 'Dinner lifecycle is not implemented'


def operation(**fields):
    return dict(operationId=str(uuid.uuid4()), **fields)


@pytest.fixture
def db():
    assert importlib.util.find_spec('backend.dinner_schema'), 'Dinner schema is not implemented'
    assert importlib.util.find_spec('backend.dinner_service'), 'Dinner lifecycle is not implemented'
    schema = importlib.import_module('backend.dinner_schema')
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    connection.execute('CREATE TABLE members(id INTEGER PRIMARY KEY,name TEXT UNIQUE)')
    connection.executemany('INSERT INTO members(name) VALUES(?)', [('A',), ('B',), ('C',)])
    connection.execute('CREATE TABLE app_settings(key TEXT PRIMARY KEY,value TEXT)')
    connection.executemany('INSERT INTO app_settings VALUES(?,?)',
        [('request_access_dataset_id', 'dataset-1'), ('request_access_generation', 'generation-1')])
    schema.ensure_schema(connection.cursor())
    connection.commit()
    yield connection
    connection.close()


def actor(db, key='', admin=False, now=NOW, *, master=False):
    from backend.dinner_service import DinnerService
    return DinnerService(db.cursor(), key=key, admin=admin, now=now, master=master)


def draft(db, **changes):
    body = dict(title='6월 회식', description='일정을 골라 주세요.',
                deadline='2030-06-12T18:00', candidates=[
                    dict(id='one', date='2030-06-14', time='18:30'),
                    dict(id='two', date='2030-06-15', time='19:00')], targets=['A', 'B'])
    body.update(changes)
    return actor(db, master=True).create_poll(operation(**body))['poll']


def act(db, poll, action, **fields):
    return actor(db, master=True).action(poll['id'], operation(
        action=action, expectedRevision=poll['revision'], **fields))['poll']


def opened(db):
    return act(db, draft(db), 'PUBLISH')


def answer(db, poll, name='A', key=KEY_A, answers=None, **fields):
    return actor(db, key).respond(poll['id'], operation(memberName=name,
        expectedRevision=poll['revision'], answers=answers or {'one': 'possible', 'two': 'difficult'},
        suggestion='금요일 선호', **fields))['poll']


def assigned(db):
    return actor(db, admin=True).set_role(operation(memberName='C', startDate='2030-06-01',
        endDate='2030-06-30', expectedRevision=0))['role']


def connected(db):
    assigned(db)
    connection = actor(db, KEY_CA).request_connection(operation(memberName='C', deviceLabel='CA PC'))['connection']
    actor(db, admin=True).decide_connection(connection['id'], operation(decision='APPROVE'))
    return connection


def test_ca_needs_appointment_and_approved_key_not_selected_name(db):
    from backend.request_access_schema import AccessError
    assigned(db)
    with pytest.raises(AccessError):
        actor(db, KEY_CA).manage()
    connection = actor(db, KEY_CA).request_connection(operation(memberName='C', deviceLabel='CA PC'))['connection']
    assert connection['status'] == 'PENDING'
    assert len(connection['verificationCode']) == 6
    with pytest.raises(AccessError):
        actor(db, KEY_CA).create_poll(operation(title='불가'))
    actor(db, admin=True).decide_connection(connection['id'], operation(decision='APPROVE'))
    assert actor(db, KEY_CA).manage()['members'] == ['A', 'B', 'C']
    with pytest.raises(AccessError):
        actor(db, KEY_A).manage()


def test_ca_expiry_role_change_and_restore_revoke_connections(db):
    from backend.request_access_schema import AccessError
    from backend import dinner_schema
    connected(db)
    with pytest.raises(AccessError):
        actor(db, KEY_CA, now=datetime(2030, 7, 1, tzinfo=KST)).manage()
    role = actor(db, admin=True).role_management()['role']
    actor(db, admin=True).set_role(operation(memberName='B', startDate='2030-06-01',
        endDate='2030-06-30', expectedRevision=role['revision']))
    with pytest.raises(AccessError):
        actor(db, KEY_CA).manage()
    dinner_schema.after_restore(db.cursor())
    assert all(r[0] == 'INVALIDATED' for r in db.execute('SELECT status FROM dinner_connections'))


def test_role_revision_and_connection_request_replay(db):
    from backend.request_access_schema import AccessError
    assigned(db)
    first = actor(db, KEY_CA).request_connection(operation(memberName='C', deviceLabel='PC'))
    second = actor(db, KEY_CA).request_connection(operation(memberName='C', deviceLabel='PC'))
    assert first == second
    with pytest.raises(AccessError):
        actor(db, admin=True).set_role(operation(memberName='B', startDate='2030-06-01',
            endDate='2030-06-30', expectedRevision=0))


def test_poll_lifecycle_results_and_all_difficult_are_valid(db):
    poll = draft(db)
    assert actor(db, KEY_A).summary('A')['polls'] == []
    poll = act(db, poll, 'PUBLISH')
    answered = answer(db, poll, answers={'one': 'difficult', 'two': 'difficult'})
    assert answered['responded'] and answered['ownResponse']['answers']['one'] == 'difficult'
    managed = actor(db, master=True).manage()['polls'][0]
    assert managed['results']['nonresponders'] == ['B']
    assert managed['results']['candidates'][0]['difficult'] == 1
    assert managed['results']['candidates'][0]['unanswered'] == 1
    poll = act(db, managed, 'CLOSE')
    poll = act(db, poll, 'CONFIRM', selectedCandidateId='two', venue='서울 식당', notice='18시 집결')
    assert poll['status'] == 'CONFIRMED' and poll['selectedCandidateId'] == 'two'
    assert poll['venue'] == '서울 식당'
    assert any(e['type'] == 'CONFIRMED' for e in actor(db, KEY_A).summary('A')['events'])


def test_response_owner_key_required_and_private_projection(db):
    from backend.request_access_schema import AccessError
    poll = opened(db)
    answer(db, poll)
    outsider = actor(db, KEY_B).detail(poll['id'], 'A')['poll']
    assert outsider['responded'] is True and outsider['ownResponse'] is None
    with pytest.raises(AccessError):
        answer(db, poll, key=KEY_B)
    with pytest.raises(AccessError):
        answer(db, poll, name='C')
    with pytest.raises(AccessError):
        answer(db, poll, key='')
    serialized = json.dumps(actor(db, KEY_B).summary('A'))
    assert '금요일 선호' not in serialized
    assert KEY_A not in serialized and 'keyHash' not in serialized and 'key_hash' not in serialized


def test_changed_candidate_only_needs_reconfirmation_other_answer_preserved(db):
    poll = opened(db)
    answer(db, poll)
    changed = actor(db, master=True).patch_poll(poll['id'], operation(expectedRevision=poll['revision'],
        candidates=[dict(id='one', date='2030-06-14', time='18:30'),
                    dict(id='two', date='2030-06-16', time='19:00')]))['poll']
    own = actor(db, KEY_A).detail(poll['id'], 'A')['poll']
    assert own['needsConfirmation'] is True
    assert own['confirmationCandidateIds'] == ['two']
    assert own['ownResponse']['answers'] == {'one': 'possible'}
    assert own['revision'] > poll['revision']
    assert changed['candidates'][0]['revision'] == poll['candidates'][0]['revision']
    assert changed['candidates'][1]['revision'] > poll['candidates'][1]['revision']
    result = actor(db, master=True).manage()['polls'][0]['results']
    assert result['candidates'][0]['possible'] == 1
    assert result['candidates'][1]['difficult'] == 0
    assert result['nonresponders'] == ['B']


def test_stale_revision_and_incomplete_answers_rejected(db):
    from backend.request_access_schema import AccessError
    poll = opened(db)
    changed = actor(db, master=True).patch_poll(poll['id'], operation(
        expectedRevision=poll['revision'], title='제목 변경'))['poll']
    with pytest.raises(AccessError):
        answer(db, poll)
    with pytest.raises(AccessError):
        answer(db, changed, answers={'one': 'possible'})
    with pytest.raises(AccessError):
        actor(db, master=True).patch_poll(poll['id'], operation(expectedRevision=changed['revision'], targets=['A']))


def test_response_idempotency_replay_and_conflict(db):
    from backend.request_access_schema import AccessError
    poll = opened(db)
    body = operation(memberName='A', expectedRevision=poll['revision'],
        answers={'one': 'possible', 'two': 'maybe'}, suggestion='')
    initial = actor(db, KEY_A).respond(poll['id'], body)
    assert actor(db, KEY_A).respond(poll['id'], body) == initial
    assert db.execute('SELECT COUNT(*) FROM dinner_responses').fetchone()[0] == 1
    with pytest.raises(AccessError):
        actor(db, KEY_A).respond(poll['id'], dict(body, suggestion='changed'))


def test_deadline_uses_kst_and_extension_reopens(db):
    from backend.request_access_schema import AccessError
    poll = opened(db)
    after = datetime(2030, 6, 12, 9, tzinfo=timezone.utc)  # 18:00 KST exactly.
    with pytest.raises(AccessError):
        actor(db, KEY_A, now=after).respond(poll['id'], operation(memberName='A',
            expectedRevision=poll['revision'], answers={'one':'possible','two':'possible'}))
    assert actor(db, KEY_A, now=after).detail(poll['id'], 'A')['poll']['status'] == 'CLOSED'
    extended = actor(db, master=True, now=after).action(poll['id'], operation(action='EXTEND',
        expectedRevision=poll['revision'], deadline='2030-06-13T18:00'))['poll']
    assert extended['status'] == 'OPEN'


def test_cancel_prevents_writes_and_reminders_only_target_nonresponders(db):
    from backend.request_access_schema import AccessError
    poll = opened(db)
    answer(db, poll)
    poll = act(db, poll, 'REMIND')
    assert not any(e['type'] == 'REMIND' for e in actor(db, KEY_A).summary('A')['events'])
    assert any(e['type'] == 'REMIND' for e in actor(db, KEY_B).summary('B')['events'])
    poll = act(db, poll, 'CANCEL', notice='사정상 취소')
    with pytest.raises(AccessError):
        answer(db, poll)
    with pytest.raises(AccessError):
        act(db, poll, 'PUBLISH')


@pytest.mark.parametrize('change', [dict(candidates=[]), dict(targets=[]),
    dict(targets=['Ghost']), dict(candidates=[dict(id='a', date='2030-02-30', time='25:00')]),
    dict(deadline='2030-06-10T11:59'), dict(title=''), dict(targets=['A', 'A'])])
def test_invalid_poll_inputs_do_not_create_records(db, change):
    from backend.request_access_schema import AccessError
    with pytest.raises(AccessError):
        draft(db, **change)
    assert db.execute('SELECT COUNT(*) FROM dinner_polls').fetchone()[0] == 0


def test_ca_cannot_appoint_itself_or_approve_connections(db):
    from backend.request_access_schema import AccessError
    connection = connected(db)
    with pytest.raises(AccessError):
        actor(db, KEY_CA).set_role(operation(memberName='C', expectedRevision=1,
            startDate='2030-01-01', endDate='2035-01-01'))
    with pytest.raises(AccessError):
        actor(db, KEY_CA).decide_connection(connection['id'], operation(decision='APPROVE'))


def test_sensitive_raw_key_never_persisted_in_shared_database(db):
    connected(db)
    poll = opened(db)
    answer(db, poll)
    dump = '\n'.join(db.iterdump())
    assert KEY_A not in dump and KEY_CA not in dump


def test_removed_candidate_id_cannot_resurrect_an_old_answer(db):
    poll = opened(db)
    answer(db, poll)
    removed = actor(db, master=True).patch_poll(poll['id'], operation(expectedRevision=poll['revision'],
        candidates=[dict(id='one', date='2030-06-14', time='18:30'),
                    dict(id='three', date='2030-06-16', time='19:00')]))['poll']
    actor(db, master=True).patch_poll(poll['id'], operation(expectedRevision=removed['revision'],
        candidates=[dict(id='one', date='2030-06-14', time='18:30'),
                    dict(id='two', date='2030-06-15', time='19:00')]))
    own = actor(db, KEY_A).detail(poll['id'], 'A')['poll']
    assert own['needsConfirmation'] is True
    assert own['confirmationCandidateIds'] == ['two']
    assert own['ownResponse']['answers'] == {'one': 'possible'}


def test_removed_member_cannot_keep_ca_management_authority(db):
    from backend.request_access_schema import AccessError
    connected(db)
    db.execute("DELETE FROM members WHERE name='C'")
    with pytest.raises(AccessError):
        actor(db, KEY_CA).manage()


def test_candidate_retirement_metadata_does_not_enter_public_poll(db):
    poll = opened(db)
    own = actor(db, KEY_A).detail(poll['id'], 'A')['poll']
    assert set(own) == {'id','title','description','deadline','status','revision','candidates',
        'targets','ownResponse','responded','needsConfirmation','confirmationCandidateIds',
        'selectedCandidateId','venue','notice','createdAt'}


def test_non_finite_json_input_is_rejected_without_unhandled_error(db):
    from backend.request_access_schema import AccessError
    with pytest.raises(AccessError):
        draft(db, extra=float('nan'))


def test_deadline_seconds_are_not_rounded_back_to_an_expired_minute(db):
    near = datetime(2030, 6, 10, 12, 0, 30, tzinfo=KST)
    poll = draft(db, deadline='2030-06-10T12:00:59')
    response = actor(db, master=True, now=near).action(poll['id'], operation(action='PUBLISH', expectedRevision=poll['revision']))
    assert response['poll']['status'] == 'OPEN'


def test_reminder_reaches_reconfirmation_and_preserves_ca_message(db):
    poll = opened(db)
    answer(db, poll)
    changed = actor(db, master=True).patch_poll(poll['id'], operation(expectedRevision=poll['revision'],
        candidates=[dict(id='one', date='2030-06-14', time='18:30'),
                    dict(id='two', date='2030-06-16', time='19:00')]))['poll']
    act(db, changed, 'REMIND', notice='새 후보를 오늘 안에 확인해 주세요.')
    for name, key in [('A', KEY_A), ('B', KEY_B)]:
        notices = [e for e in actor(db, key).summary(name)['events'] if e['type'] == 'REMIND']
        assert len(notices) == 1
        assert '새 후보를 오늘 안에 확인해 주세요.' in notices[0]['text']


@pytest.mark.parametrize('notice', [123, 'x' * 2001])
def test_reminder_validates_notice_before_creating_events(db, notice):
    from backend.request_access_schema import AccessError
    poll = opened(db)
    before = db.execute('SELECT COUNT(*) FROM dinner_events').fetchone()[0]
    with pytest.raises(AccessError):
        act(db, poll, 'REMIND', notice=notice)
    assert db.execute('SELECT COUNT(*) FROM dinner_events').fetchone()[0] == before


def test_never_published_cancelled_draft_stays_private(db):
    from backend.request_access_schema import AccessError
    poll = draft(db, title='PRIVATE_DRAFT')
    assert actor(db, KEY_A).summary('A')['polls'] == []
    with pytest.raises(AccessError) as before:
        actor(db, KEY_A).detail(poll['id'], 'A')
    assert before.value.status == 403
    cancelled = act(db, poll, 'CANCEL')
    assert cancelled['status'] == 'CANCELLED'
    assert actor(db, KEY_A).summary('A')['polls'] == []
    assert actor(db, KEY_A).summary('A')['events'] == []
    with pytest.raises(AccessError) as after:
        actor(db, KEY_A).detail(poll['id'], 'A')
    assert after.value.status == 403
    assert actor(db, master=True).manage()['polls'][0]['title'] == 'PRIVATE_DRAFT'


def test_published_cancelled_poll_keeps_staff_visibility_and_notice(db):
    poll = opened(db)
    cancelled = act(db, poll, 'CANCEL')
    summary = actor(db, KEY_A).summary('A')
    assert [p['id'] for p in summary['polls']] == [poll['id']]
    assert any(e['type'] == 'CANCELLED' for e in summary['events'])
    assert actor(db, KEY_A).detail(poll['id'], 'A')['poll']['status'] == 'CANCELLED'
    assert 'publishedAt' not in cancelled


def test_candidates_must_have_distinct_dates_even_with_different_times(db):
    from backend.request_access_schema import AccessError
    with pytest.raises(AccessError):
        draft(db, candidates=[dict(id='one', date='2030-06-14', time='18:00'),
                              dict(id='two', date='2030-06-14', time='19:00')])
    assert db.execute('SELECT COUNT(*) FROM dinner_polls').fetchone()[0] == 0


@pytest.mark.parametrize('deadline', ['2030-06-14T18:30', '2030-06-14T19:00'])
def test_creation_deadline_must_precede_every_candidate_datetime(db, deadline):
    from backend.request_access_schema import AccessError
    with pytest.raises(AccessError):
        draft(db, deadline=deadline)
    assert db.execute('SELECT COUNT(*) FROM dinner_polls').fetchone()[0] == 0


@pytest.mark.parametrize('changes', [
    dict(deadline='2030-06-14T18:30'),
    dict(candidates=[dict(id='one', date='2030-06-12', time='17:30'),
                     dict(id='two', date='2030-06-15', time='19:00')]),
    dict(candidates=[dict(id='one', date='2030-06-14', time='18:30'),
                     dict(id='two', date='2030-06-14', time='19:00')]),
])
def test_edit_cannot_make_deadline_later_than_candidate_or_duplicate_dates(db, changes):
    from backend.request_access_schema import AccessError
    poll = opened(db)
    with pytest.raises(AccessError):
        actor(db, master=True).patch_poll(poll['id'], operation(expectedRevision=poll['revision'], **changes))
    actual = actor(db, master=True).detail(poll['id'])['poll']
    assert actual['revision'] == poll['revision']
    assert actual['deadline'] == poll['deadline']
    assert actual['candidates'] == poll['candidates']


@pytest.mark.parametrize('deadline', ['2030-06-14T18:30', '2030-06-14T20:00'])
def test_extension_cannot_reopen_at_or_after_a_candidate_datetime(db, deadline):
    from backend.request_access_schema import AccessError
    poll = act(db, opened(db), 'CLOSE')
    with pytest.raises(AccessError):
        act(db, poll, 'EXTEND', deadline=deadline)
    actual = actor(db, master=True).detail(poll['id'])['poll']
    assert actual['status'] == 'CLOSED'
    assert actual['revision'] == poll['revision']


def annual_role(db, year, name='C', revision=0):
    return actor(db, admin=True).set_role(operation(roleId=year, memberName=name,
        startDate=f'{year}-01-01', endDate=f'{year}-12-31', expectedRevision=revision))['role']


def approve_term(db, role_id, name, key):
    connection = actor(db, key).request_connection(operation(roleId=role_id, memberName=name))['connection']
    return actor(db, admin=True).decide_connection(connection['id'], operation(decision='APPROVE'))['connection']


def test_current_and_next_appointments_persist_and_switch_at_kst_midnight(db):
    from backend.request_access_schema import AccessError
    current = annual_role(db, 2030)
    future = annual_role(db, 2031, 'B')
    assert current['roleId'] == 2030 and future['roleId'] == 2031
    approve_term(db, 2030, 'C', KEY_CA)
    approved = approve_term(db, 2031, 'B', KEY_B)
    assert approved['status'] == 'APPROVED' and approved['active'] is False
    assert actor(db, KEY_CA).manage()['role']['roleId'] == 2030
    assert actor(db, KEY_B).summary('B')['assignedRole']['roleId'] == 2031
    with pytest.raises(AccessError):
        actor(db, KEY_B).manage()
    before = datetime(2030, 12, 31, 14, 59, 59, tzinfo=timezone.utc)
    boundary = datetime(2030, 12, 31, 15, tzinfo=timezone.utc)
    december = actor(db, KEY_CA, now=before).manage()
    assert december['handoff'] is None and december['role']['roleId'] == 2030
    assert actor(db, KEY_B, now=before).manage()['handoff']['memberName'] == 'B'
    assert actor(db, KEY_CA, now=boundary).manage()['handoff']['memberName'] == 'C'
    january = actor(db, KEY_B, now=boundary).manage()
    assert january['handoff'] is None and january['role']['roleId'] == 2031


def test_handoff_starts_and_ends_at_kst_midnight_on_approved_pcs(db):
    from backend.request_access_schema import AccessError
    annual_role(db, 2030)
    annual_role(db, 2031, 'B')
    approve_term(db, 2030, 'C', KEY_CA)
    pending = actor(db, KEY_B).request_connection(operation(roleId=2031, memberName='B'))['connection']
    december = datetime(2030, 11, 30, 15, tzinfo=timezone.utc)
    february = datetime(2031, 1, 31, 15, tzinfo=timezone.utc)
    assert actor(db, KEY_B, now=december).summary('B').get('handoff') is None
    with pytest.raises(AccessError) as error:
        actor(db, KEY_B, now=december).manage()
    assert error.value.status == 403
    actor(db, admin=True).decide_connection(pending['id'], operation(decision='APPROVE'))
    expected = {'memberName': 'B', 'startDate': '2030-12-01', 'endDate': '2031-01-31'}
    incoming = actor(db, KEY_B, now=december)
    assert incoming.summary('B')['handoff'] == incoming.manage()['handoff'] == expected
    assert incoming.summary('A')['handoff'] is None
    assert actor(db, KEY_A, now=december).summary('B')['handoff'] is None
    outgoing = actor(db, KEY_CA, now=february - timedelta(seconds=1))
    assert outgoing.summary('C')['handoff'] == (expected | {'memberName': 'C'})
    assert actor(db, KEY_B, now=february).manage()['handoff'] is None
    for key, now in [(KEY_B, december - timedelta(seconds=1)), (KEY_CA, february), (KEY_A, december)]:
        with pytest.raises(AccessError) as error:
            actor(db, key, now=now).manage()
        assert error.value.status == 403


def test_handoff_lists_published_work_in_scope_and_keeps_writes_with_active_ca(db):
    from backend.request_access_schema import AccessError
    annual_role(db, 2030)
    annual_role(db, 2031, 'B')
    approve_term(db, 2030, 'C', KEY_CA)
    approve_term(db, 2031, 'B', KEY_B)
    year_end = dict(deadline='2031-01-09T18:00', candidates=[
        dict(id='one', date='2031-01-14', time='18:30'),
        dict(id='two', date='2031-01-15', time='19:00')], targets=['A', 'C'])
    unpublished = draft(db, title='게시 전 초안', **year_end)
    private_cancel = act(db, draft(db, title='게시 전 취소', **year_end), 'CANCEL')
    old_closed = act(db, opened(db), 'CLOSE')
    poll = act(db, draft(db, title='연초 취합', **year_end), 'PUBLISH')
    answer(db, poll)
    current = actor(db, KEY_CA, now=datetime(2030, 12, 15, tzinfo=KST))
    incoming = actor(db, KEY_B, now=current.now)
    incoming_polls = {p['id']: p for p in incoming.manage()['polls']}
    assert set(incoming_polls) == {poll['id']}
    current_polls = {p['id']: p for p in current.manage()['polls']}
    assert {unpublished['id'], private_cancel['id'], old_closed['id']} <= set(current_polls)
    assert incoming_polls[poll['id']]['results'] == current_polls[poll['id']]['results']
    assert incoming_polls[poll['id']]['results']['responders'][0]['name'] == 'A'
    assert incoming.manage()['connections'] == incoming.manage()['members'] == []
    with pytest.raises(AccessError) as error:
        incoming.detail(poll['id'], 'B')
    assert error.value.status == 403
    assert 'results' not in incoming.detail(old_closed['id'], 'B')['poll']
    with pytest.raises(AccessError) as error:
        incoming.create_poll(operation(**year_end))
    assert error.value.status == 403
    with pytest.raises(AccessError) as error:
        incoming.patch_poll(poll['id'], operation(expectedRevision=poll['revision'], title='변경'))
    assert error.value.status == 403
    for action, subject in [('PUBLISH', unpublished), ('CLOSE', poll), ('CONFIRM', poll),
                            ('CANCEL', poll), ('REMIND', poll)]:
        with pytest.raises(AccessError) as error:
            incoming.action(subject['id'], operation(expectedRevision=subject['revision'], action=action))
        assert error.value.status == 403
    assert current.detail(poll['id'])['poll']['status'] == 'OPEN'
    assert current.action(poll['id'], operation(action='CLOSE', expectedRevision=poll['revision']))['poll']['status'] == 'CLOSED'
    confirmed = actor(db, KEY_B, now=datetime(2031, 1, 2, tzinfo=KST)).action(
        poll['id'], operation(action='CONFIRM', expectedRevision=poll['revision']+1, selectedCandidateId='one'))['poll']
    outgoing = actor(db, KEY_CA, now=datetime(2031, 1, 2, tzinfo=KST))
    assert [p['id'] for p in outgoing.manage()['polls']] == [confirmed['id']]
    with pytest.raises(AccessError) as error:
        outgoing.action(poll['id'], operation(action='CANCEL', expectedRevision=confirmed['revision']))
    assert error.value.status == 403


def test_handoff_uses_actual_adjacent_terms_and_revokes_replaced_pc(db):
    from backend.request_access_schema import AccessError
    admin = actor(db, admin=True)
    admin.set_role(operation(memberName='C', startDate='2029-01-01', endDate='2030-12-31', expectedRevision=0))
    future = annual_role(db, 2031, 'B')
    approve_term(db, 1, 'C', KEY_CA)
    approve_term(db, 2031, 'B', KEY_B)
    december = datetime(2030, 12, 15, tzinfo=KST)
    assert actor(db, KEY_B, now=december).manage()['handoff']['memberName'] == 'B'
    future = annual_role(db, 2031, 'A', revision=future['revision'])
    with pytest.raises(AccessError) as error:
        actor(db, KEY_B, now=december).manage()
    assert error.value.status == 403
    approve_term(db, 2031, 'A', KEY_A)
    assert actor(db, KEY_A, now=december).manage()['handoff']['memberName'] == 'A'
    admin.set_role(operation(roleId=future['roleId'], memberName='A',
        startDate='2031-01-02', endDate='2031-12-31', expectedRevision=future['revision']))
    approve_term(db, 2031, 'A', KEY_A)
    with pytest.raises(AccessError) as error:
        actor(db, KEY_A, now=december).manage()
    assert error.value.status == 403


def test_same_person_and_key_can_hold_two_terms_without_future_superseding_current(db):
    annual_role(db, 2030)
    annual_role(db, 2031)
    current = approve_term(db, 2030, 'C', KEY_CA)
    future = approve_term(db, 2031, 'C', KEY_CA)
    assert current['id'] != future['id']
    assert actor(db, KEY_CA).summary('C')['connection']['id'] == current['id']
    assert actor(db, KEY_CA).manage()['role']['roleId'] == 2030
    annual_role(db, 2031, name='B', revision=1)
    assert actor(db, KEY_CA).manage()['role']['roleId'] == 2030
    statuses = {r['id']: r['status'] for r in actor(db, admin=True).role_management()['connections']}
    assert statuses == {current['id']: 'APPROVED', future['id']: 'INVALIDATED'}


def test_summary_own_connections_are_per_term_and_scoped_to_key_and_selected_name(db):
    annual_role(db, 2030)
    annual_role(db, 2031)
    current = approve_term(db, 2030, 'C', KEY_CA)
    future = actor(db, KEY_CA).request_connection(operation(roleId=2031, memberName='C'))['connection']
    other = actor(db, KEY_B).request_connection(operation(roleId=2031, memberName='C'))['connection']
    summary = actor(db, KEY_CA).summary('C')
    assert summary['assignedRole']['roleId'] == 2030
    assert summary['connection']['id'] == current['id']
    assert summary['ownConnections'] == {'2030': current, '2031': future}
    assert other['id'] not in json.dumps(summary)
    assert actor(db, KEY_CA).summary('B')['ownConnections'] == {}
    assert actor(db, KEY_CA).summary('')['ownConnections'] == {}
    for key in ('', KEY_A):
        public = actor(db, key).summary('C')
        assert public['ownConnections'] == {} and public['connection'] is None
        assert 'verificationCode' not in json.dumps(public)


def test_future_pending_visible_and_only_selected_appointment_can_be_cleared(db):
    annual_role(db, 2030)
    annual_role(db, 2031, 'B')
    approve_term(db, 2030, 'C', KEY_CA)
    pending = actor(db, KEY_B).request_connection(operation(memberName='B'))['connection']
    assert pending['roleId'] == 2031
    assert actor(db, admin=True).summary('')['pendingConnections'] == [pending]
    cleared = actor(db, admin=True).set_role(operation(roleId=2031, memberName='', expectedRevision=1))['role']
    assert cleared['memberName'] == '' and cleared['revision'] == 2
    assert actor(db, KEY_CA).manage()['role']['roleId'] == 2030
    assert actor(db, admin=True).summary('')['pendingConnections'] == []


def test_annual_appointment_overlap_stale_write_and_retry_are_atomic(db):
    from backend.request_access_schema import AccessError
    annual_role(db, 2030)
    for name in ('B', 'C'):
        with pytest.raises(AccessError) as error:
            actor(db, admin=True).set_role(operation(roleId=2031, memberName=name,
                startDate='2030-12-31', endDate='2031-12-31', expectedRevision=0))
        assert error.value.status == 409
    assert len(actor(db, admin=True).role_management()['roles']) == 1
    body = operation(roleId=2031, memberName='B', startDate='2031-01-01',
                     endDate='2031-12-31', expectedRevision=0)
    result = actor(db, admin=True).set_role(body)
    assert actor(db, admin=True).set_role(body) == result
    with pytest.raises(AccessError):
        actor(db, admin=True).set_role(operation(roleId=2031, memberName='', expectedRevision=0))
    assert len(actor(db, admin=True).role_management()['roles']) == 2


@pytest.mark.parametrize('role_id', [True, '2031', 0, 2, 1899, 10000])
def test_role_ids_must_be_legacy_or_numeric_year(db, role_id):
    from backend.request_access_schema import AccessError
    with pytest.raises(AccessError):
        actor(db, admin=True).set_role(operation(roleId=role_id, memberName='', expectedRevision=0))


def test_legacy_schema_upgrade_preserves_role_and_approved_connection(db):
    from backend import dinner_schema
    connected(db)
    db.execute('DROP TABLE dinner_appointments')
    db.execute('ALTER TABLE dinner_connections DROP COLUMN role_id')
    # Read-only access must also work before the first upgraded writer migrates.
    assert actor(db, KEY_CA).manage()['role']['roleId'] == 1
    dinner_schema.ensure_schema(db.cursor())
    dinner_schema.ensure_schema(db.cursor())
    assert actor(db, KEY_CA).manage()['role']['roleId'] == 1
    assert db.execute('SELECT role_id FROM dinner_connections').fetchone()[0] == 1
    annual_role(db, 2031, 'B')
    assert len(actor(db, admin=True).role_management()['roles']) == 2


def test_restore_preserves_both_appointments_but_invalidates_connections(db):
    from backend import dinner_schema
    from backend.request_access_schema import AccessError
    annual_role(db, 2030)
    annual_role(db, 2031, 'B')
    approve_term(db, 2030, 'C', KEY_CA)
    approve_term(db, 2031, 'B', KEY_B)
    assert 'dinner_appointments' in dinner_schema.TABLES
    assert 'dinner_appointments' in dinner_schema.RESTORE_SKIP_TABLES
    dinner_schema.after_restore(db.cursor())
    assert len(actor(db, admin=True).role_management()['roles']) == 2
    assert all(c['status'] == 'INVALIDATED' for c in actor(db, admin=True).role_management()['connections'])
    with pytest.raises(AccessError):
        actor(db, KEY_CA).manage()
