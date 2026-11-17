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


def actor(db, key='', admin=False, now=NOW):
    from backend.dinner_service import DinnerService
    return DinnerService(db.cursor(), key=key, admin=admin, now=now)


def draft(db, **changes):
    body = dict(title='6월 회식', description='일정을 골라 주세요.',
                deadline='2030-06-12T18:00', candidates=[
                    dict(id='one', date='2030-06-14', time='18:30'),
                    dict(id='two', date='2030-06-15', time='19:00')], targets=['A', 'B'])
    body.update(changes)
    return actor(db, admin=True).create_poll(operation(**body))['poll']


def act(db, poll, action, **fields):
    return actor(db, admin=True).action(poll['id'], operation(
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
    role = actor(db, admin=True).manage()['role']
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
    managed = actor(db, admin=True).manage()['polls'][0]
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
    changed = actor(db, admin=True).patch_poll(poll['id'], operation(expectedRevision=poll['revision'],
        candidates=[dict(id='one', date='2030-06-14', time='18:30'),
                    dict(id='two', date='2030-06-16', time='19:00')]))['poll']
    own = actor(db, KEY_A).detail(poll['id'], 'A')['poll']
    assert own['needsConfirmation'] is True
    assert own['confirmationCandidateIds'] == ['two']
    assert own['ownResponse']['answers'] == {'one': 'possible'}
    assert own['revision'] > poll['revision']
    assert changed['candidates'][0]['revision'] == poll['candidates'][0]['revision']
    assert changed['candidates'][1]['revision'] > poll['candidates'][1]['revision']
    result = actor(db, admin=True).manage()['polls'][0]['results']
    assert result['candidates'][0]['possible'] == 1
    assert result['candidates'][1]['difficult'] == 0
    assert result['nonresponders'] == ['B']


def test_stale_revision_and_incomplete_answers_rejected(db):
    from backend.request_access_schema import AccessError
    poll = opened(db)
    changed = actor(db, admin=True).patch_poll(poll['id'], operation(
        expectedRevision=poll['revision'], title='제목 변경'))['poll']
    with pytest.raises(AccessError):
        answer(db, poll)
    with pytest.raises(AccessError):
        answer(db, changed, answers={'one': 'possible'})
    with pytest.raises(AccessError):
        actor(db, admin=True).patch_poll(poll['id'], operation(expectedRevision=changed['revision'], targets=['A']))


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
    extended = actor(db, admin=True, now=after).action(poll['id'], operation(action='EXTEND',
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
    removed = actor(db, admin=True).patch_poll(poll['id'], operation(expectedRevision=poll['revision'],
        candidates=[dict(id='one', date='2030-06-14', time='18:30'),
                    dict(id='three', date='2030-06-16', time='19:00')]))['poll']
    actor(db, admin=True).patch_poll(poll['id'], operation(expectedRevision=removed['revision'],
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
    response = actor(db, admin=True, now=near).action(poll['id'], operation(action='PUBLISH', expectedRevision=poll['revision']))
    assert response['poll']['status'] == 'OPEN'


def test_reminder_reaches_reconfirmation_and_preserves_ca_message(db):
    poll = opened(db)
    answer(db, poll)
    changed = actor(db, admin=True).patch_poll(poll['id'], operation(expectedRevision=poll['revision'],
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
    assert actor(db, admin=True).manage()['polls'][0]['title'] == 'PRIVATE_DRAFT'


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
        actor(db, admin=True).patch_poll(poll['id'], operation(expectedRevision=poll['revision'], **changes))
    actual = actor(db, admin=True).detail(poll['id'])['poll']
    assert actual['revision'] == poll['revision']
    assert actual['deadline'] == poll['deadline']
    assert actual['candidates'] == poll['candidates']


@pytest.mark.parametrize('deadline', ['2030-06-14T18:30', '2030-06-14T20:00'])
def test_extension_cannot_reopen_at_or_after_a_candidate_datetime(db, deadline):
    from backend.request_access_schema import AccessError
    poll = act(db, opened(db), 'CLOSE')
    with pytest.raises(AccessError):
        act(db, poll, 'EXTEND', deadline=deadline)
    actual = actor(db, admin=True).detail(poll['id'])['poll']
    assert actual['status'] == 'CLOSED'
    assert actual['revision'] == poll['revision']
