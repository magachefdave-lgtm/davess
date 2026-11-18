"""Two independent SQLite clients exercise lock/revision/receipt races."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

from backend.request_access_schema import AccessError
from test_dinner_service import actor, db, opened, operation, KEY_A, KEY_B


def race(db, tmp_path, callbacks):
    db.commit()
    path = tmp_path / 'shared-dinner.db'
    with sqlite3.connect(path) as destination:
        db.backup(destination)
    barrier = threading.Barrier(len(callbacks))

    def run(callback):
        connection = sqlite3.connect(path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            barrier.wait(timeout=5)
            connection.execute('BEGIN IMMEDIATE')
            result = callback(connection)
            connection.commit()
            return 200, result
        except AccessError as error:
            connection.rollback()
            return error.status, error.code
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=len(callbacks)) as pool:
        results = list(pool.map(run, callbacks))
    return path, results


def test_two_managers_cannot_overwrite_same_poll_revision(db, tmp_path):
    poll = opened(db)
    def edit(title):
        return lambda connection: actor(connection, master=True).patch_poll(poll['id'],
            operation(expectedRevision=poll['revision'], title=title))
    path, results = race(db, tmp_path, [edit('첫 제목'), edit('두 번째 제목')])
    assert sorted(status for status, _ in results) == [200, 409]
    accepted = next(result['poll']['title'] for status, result in results if status == 200)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        actual = actor(connection, master=True).detail(poll['id'])['poll']
        assert actual['title'] == accepted
        assert actual['revision'] == poll['revision'] + 1


def test_racing_first_responses_cannot_take_over_an_existing_owner(db, tmp_path):
    poll = opened(db)
    def submit(key, choice):
        return lambda connection: actor(connection, key).respond(poll['id'], operation(
            memberName='A', expectedRevision=poll['revision'],
            answers={'one':choice,'two':choice}, suggestion=''))
    path, results = race(db, tmp_path, [submit(KEY_A, 'possible'), submit(KEY_B, 'difficult')])
    assert sorted(status for status, _ in results) == [200, 403]
    accepted = next(result['poll']['ownResponse']['answers'] for status, result in results if status == 200)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        assert connection.execute('SELECT COUNT(*) FROM dinner_responses').fetchone()[0] == 1
        result = actor(connection, master=True).manage()['polls'][0]['results']['responders'][0]
        assert result['answers'] == accepted


def test_concurrent_identical_retry_commits_one_receipt_and_one_operation(db, tmp_path):
    poll = opened(db)
    body = operation(memberName='A', expectedRevision=poll['revision'],
        answers={'one':'possible','two':'maybe'}, suggestion='')
    callback = lambda connection: actor(connection, KEY_A).respond(poll['id'], body)
    path, results = race(db, tmp_path, [callback, callback])
    assert [status for status, _ in results] == [200, 200]
    assert results[0][1] == results[1][1]
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM dinner_responses').fetchone()[0] == 1
        assert connection.execute('SELECT COUNT(*) FROM dinner_operations WHERE operation_id=?',
                                  (body['operationId'],)).fetchone()[0] == 1


def test_overlapping_appointments_racing_on_separate_ids_cannot_both_commit(db, tmp_path):
    def appoint(role_id, name):
        return lambda connection: actor(connection, admin=True).set_role(operation(
            roleId=role_id, memberName=name, startDate='2030-06-01', endDate='2031-12-31', expectedRevision=0))
    path, results = race(db, tmp_path, [appoint(2030, 'A'), appoint(2031, 'B')])
    assert sorted(status for status, _ in results) == [200, 409]
    assert next(result for status, result in results if status == 409) == 'ROLE_OVERLAP'
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM dinner_appointments').fetchone()[0] == 1
