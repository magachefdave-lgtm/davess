"""3.18.5 approvals: synthetic data, migration, atomic failures and result identity."""
import copy
from datetime import datetime
import os
from pathlib import Path
import sqlite3

import pytest
import test_backend_regression as regression
from test_backend_regression import AUTH, DAY_A, DAY_B, DAY_C, SEED, load_app


@pytest.fixture(autouse=True)
def source_override(monkeypatch):
    if os.environ.get('WLR_REGRESSION_SOURCE'):
        monkeypatch.setattr(regression, 'SOURCE', Path(os.environ['WLR_REGRESSION_SOURCE']))


@pytest.fixture
def seeded(load_app):
    app = load_app('source')
    app.save(copy.deepcopy(SEED))
    return app


def status(app, receipt):
    response = app.get('/api/duty/request-status/' + receipt['receiptToken'])
    assert response.status_code == 200, response.text
    return response.json()['request']


@pytest.mark.parametrize('kind,removed', [('SUBSTITUTE', 'C'), ('SWAP', 'A'), ('SWAP', 'B')])
def test_deleted_new_assignees_block_listing_and_atomic_approval(seeded, kind, removed):
    receipt = seeded.submit(kind).json()
    seeded.save({'members': [m for m in SEED['members'] if m['name'] != removed]})
    before = seeded.data()
    pending = seeded.get('/api/duty/requests', headers=AUTH).json()['requests'][0]
    assert removed in pending['blocker'] and pending['blocker']
    response = seeded.action(receipt['requestId'])
    assert response.status_code in (400, 409), response.text
    assert seeded.data() == before
    assert status(seeded, receipt)['status'] == 'PENDING'


@pytest.mark.parametrize('requested', [True, False])
def test_final_zero_override_matches_all_result_apis_and_preserves_original_in_history(seeded, requested):
    receipt = seeded.submit('SUBSTITUTE', is_zero_score=requested).json()
    assert seeded.action(receipt['requestId'], zero_score=not requested).status_code == 200
    expected = not requested
    assert seeded.data()['schedules'][DAY_A][0]['zeroScore'] is expected
    assert bool(status(seeded, receipt)['is_zero_score']) is expected
    for name, involving in [('A', 0), ('A', 1), ('C', 1)]:
        row = seeded.get('/api/duty/my-requests', params={'name': name, 'involving': involving}).json()['requests'][0]
        assert bool(row['is_zero_score']) is expected
    assert f"신청 값 {'예' if requested else '아니오'} → 관리자 변경" in seeded.data()['history'][0]['details']
    assert seeded.action(receipt['requestId'], zero_score=requested).status_code == 400
    assert bool(status(seeded, receipt)['is_zero_score']) is expected


class FrozenClock(datetime):
    @classmethod
    def now(cls):
        return datetime(2030, 1, 1, 12, 0, 0)


def approve_same_second(app, monkeypatch):
    monkeypatch.setattr(app.module, 'datetime', FrozenClock)
    receipts = [app.submit('ABSENCE', requester_name=name).json() for name in ('A', 'B', 'C')]
    for receipt in receipts:
        assert app.action(receipt['requestId']).status_code == 200
    assert len({status(app, r)['processed_at'] for r in receipts}) == 1
    return receipts


def test_same_second_absence_results_have_explicit_history_identity(seeded, monkeypatch):
    receipts = approve_same_second(seeded, monkeypatch)
    for receipt, expected in zip(receipts, ([DAY_A, DAY_C], [DAY_B], [])):
        assert status(seeded, receipt)['released_dates'] == expected
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        rows = db.execute('SELECT approved_history_id FROM change_requests ORDER BY id').fetchall()
        assert len({r[0] for r in rows}) == 3 and all(r[0] for r in rows)


def test_legacy_same_second_history_fallback_matches_person_type_and_span(seeded, monkeypatch):
    receipts = approve_same_second(seeded, monkeypatch)
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        cols = {r[1] for r in db.execute('PRAGMA table_info(change_requests)')}
        if 'approved_history_id' in cols:
            db.execute('UPDATE change_requests SET approved_history_id = NULL')
    for receipt, expected in zip(receipts, ([DAY_A, DAY_C], [DAY_B], [])):
        assert status(seeded, receipt)['released_dates'] == expected
    # A different or incomplete candidate must never masquerade as A's outcome.
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        db.execute("UPDATE change_history SET after_json = '{}' WHERE details LIKE ?", ("% 'A' %",))
    assert 'released_dates' not in status(seeded, receipts[0])


def test_approval_failure_rolls_back_score_schedule_history_and_status_then_retries(seeded):
    receipt = seeded.submit('SUBSTITUTE', is_zero_score=True).json()
    before = seeded.data()
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        db.execute("CREATE TRIGGER approval_fault BEFORE UPDATE OF status ON change_requests BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
    assert seeded.action(receipt['requestId'], zero_score=False).status_code == 500
    assert seeded.data() == before
    row = status(seeded, receipt)
    assert row['status'] == 'PENDING' and row['is_zero_score'] == 1
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        db.execute('DROP TRIGGER approval_fault')
    assert seeded.action(receipt['requestId'], zero_score=False).status_code == 200
    assert status(seeded, receipt)['is_zero_score'] == 0


def test_upgrade_and_restore_preserve_request_history_linkage(seeded, monkeypatch):
    receipt = seeded.submit('ABSENCE').json()
    assert seeded.action(receipt['requestId']).status_code == 200
    snapshot = seeded.post('/api/system/snapshot', {'kind': 'manual'}).json()['name']
    seeded.save({'globalMemo': 'later'})
    restored = seeded.post('/api/system/restore', {'name': snapshot, 'baseVersion': seeded.data()['dataVersion']})
    assert restored.status_code == 200, restored.text
    assert status(seeded, receipt)['released_dates'] == [DAY_A, DAY_C]
    # Simulate an existing previous-version schema and preserve every request row.
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        original = db.execute('SELECT id, receipt_token, status FROM change_requests').fetchall()
        db.execute('ALTER TABLE change_requests DROP COLUMN approved_history_id')
    assert 'change_requests.approved_history_id' in seeded.module.pending_schema_migrations()
    seeded.module.init_db()
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        assert db.execute('SELECT id, receipt_token, status FROM change_requests').fetchall() == original
        assert db.execute('SELECT approved_history_id FROM change_requests').fetchone() == (None,)
    assert status(seeded, receipt)['released_dates'] == [DAY_A, DAY_C]


@pytest.mark.parametrize('field,value', [('requester_name', 'C'), ('absence_type', '파견'), ('absence_end_date', DAY_B)])
def test_legacy_history_mismatched_identity_is_unknown(seeded, field, value):
    receipt = seeded.submit('ABSENCE').json()
    assert seeded.action(receipt['requestId']).status_code == 200
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        cols = {r[1] for r in db.execute('PRAGMA table_info(change_requests)')}
        if 'approved_history_id' in cols:
            db.execute('UPDATE change_requests SET approved_history_id = NULL')
        db.execute(f'UPDATE change_requests SET {field} = ? WHERE id = ?', (value, receipt['requestId']))
    assert 'released_dates' not in status(seeded, receipt)


def test_legacy_duplicate_history_is_unknown_but_explicit_link_remains_exact(seeded):
    receipt = seeded.submit('ABSENCE').json()
    assert seeded.action(receipt['requestId']).status_code == 200
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        db.execute('INSERT INTO change_history (timestamp, type, date_key, details, before_json, after_json) '
                   "SELECT timestamp, type, date_key, details, before_json, after_json FROM change_history WHERE type = '휴가·부재(승인)'")
    assert status(seeded, receipt)['released_dates'] == [DAY_A, DAY_C]
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        cols = {r[1] for r in db.execute('PRAGMA table_info(change_requests)')}
        if 'approved_history_id' in cols:
            db.execute('UPDATE change_requests SET approved_history_id = NULL')
    assert 'released_dates' not in status(seeded, receipt)


def test_absence_status_failure_rolls_back_link_and_history_before_retry(seeded):
    receipt = seeded.submit('ABSENCE').json()
    before = seeded.data()
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        db.execute("CREATE TRIGGER approval_fault BEFORE UPDATE OF status ON change_requests BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
    assert seeded.action(receipt['requestId']).status_code == 500
    assert seeded.data() == before
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        assert db.execute('SELECT approved_history_id, status FROM change_requests').fetchone() == (None, 'PENDING')
        db.execute('DROP TRIGGER approval_fault')
    assert seeded.action(receipt['requestId']).status_code == 200
    assert status(seeded, receipt)['released_dates'] == [DAY_A, DAY_C]


def test_restore_old_schema_backup_preserves_legacy_request_result(seeded):
    receipt = seeded.submit('ABSENCE').json()
    assert seeded.action(receipt['requestId']).status_code == 200
    snapshot = seeded.post('/api/system/snapshot', {'kind': 'manual'}).json()['name']
    backup_path = Path(seeded.module.backup_dir_path()) / snapshot
    with sqlite3.connect(backup_path) as db:
        db.execute('ALTER TABLE change_requests DROP COLUMN approved_history_id')
    seeded.save({'globalMemo': 'later'})
    restored = seeded.post('/api/system/restore', {'name': snapshot, 'baseVersion': seeded.data()['dataVersion']})
    assert restored.status_code == 200, restored.text
    assert status(seeded, receipt)['released_dates'] == [DAY_A, DAY_C]
