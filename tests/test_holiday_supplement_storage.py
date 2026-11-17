"""Server-owned holiday state, immutable receipts, and SQLite compatibility guards.

Uses real SQLite files. Set ONAIR_STORAGE_SNIPPET while the isolated definitions
are being integrated; the default exercises definitions in backend/main.py.
"""
from __future__ import annotations

import ast
import copy
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import types

from fastapi import HTTPException
import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'backend' / 'main.py'
PUBLIC_HELPERS = {
    'ensure_holiday_supplement_schema', 'read_holiday_supplements',
    'write_holiday_supplements', 'validate_holiday_supplements',
    'activate_holiday_contract', 'verify_holiday_contract',
    'merge_holiday_supplement_operations', 'read_holiday_supplement_operation',
    'write_holiday_supplement_operation',
}
BUSINESS_TABLES = ('members', 'schedules', 'day_meta', 'absences', 'absence_types',
                   'custom_holidays', 'system_rules', 'app_settings',
                   'change_history', 'change_requests')
OPERATION_ID = '11111111-1111-4111-8111-111111111111'


@pytest.fixture(scope='module')
def storage():
    path = Path(os.environ.get('ONAIR_STORAGE_SNIPPET', SOURCE))
    tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and (node.name in PUBLIC_HELPERS or node.name.startswith('hs_storage_')):
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id.startswith('HS_STORAGE_') for t in node.targets):
            nodes.append(node)
    ns = dict(json=json, re=re, sqlite3=sqlite3, date=date, datetime=datetime, hashlib=hashlib,
              HTTPException=HTTPException)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
    return types.SimpleNamespace(**ns)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / 'duty.db'
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.create_function('wlr_schedule_contract', 0, lambda: 1)
    for table in BUSINESS_TABLES:
        if table == 'app_settings':
            conn.execute('CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)')
        else:
            conn.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY, value TEXT)')
            conn.execute(f'INSERT INTO "{table}" VALUES (1, ?)', ('before',))
    conn.execute('CREATE TABLE client_status (id INTEGER PRIMARY KEY, value TEXT)')
    conn.commit()
    yield conn, path
    conn.close()


def state():
    female = {'name': 'F2', 'role': '평일(여)', 'isSubstitute': False, 'zeroScore': True}
    basis = {
        'year': 2027, 'pool_key': 'holiday|M', 'revision': 1,
        'sequence_snapshot': ['A', 'B', 'C'], 'prior_year_credit_hash': 'a' * 64,
        'ordinary_reservation_hash': 'b' * 64, 'ordinary_tail_position': 2,
        'anchor_before_year': 0, 'effective_boundary': '2027-01-01',
        'policy': 'ANNUAL_TAIL', 'adopted_at': '2026-11-09 12:00:00',
    }
    event = {
        'supplement_id': 'supplement-1', 'date_key': '2027-06-17', 'year': 2027,
        'allocation_seq': 1, 'state': 'ACTIVE', 'credit_state': 'CONSUMED',
        'basis_revision': 1, 'pool_key': 'holiday|M', 'order_snapshot': ['A', 'B', 'C'],
        'order_fingerprint': '0a2b4ad995acc6f5a040c90e6daa08ca78405335b76027f3fd2889b714991a1a',
        'annual_basis_hash': '53dc918abd2c66037bdeb0bd134d5cfcf4902b8fc7dfca8aaa108cd21c2c9623',
        'selected_ordinal': 0, 'end_ordinal': 1, 'original_female_slot': female,
        'female_credit': {'pool_key': 'weekday|F', 'before': 1, 'after': 2,
                          'consumes': True, 'order_snapshot': ['F1', 'F2', 'F3']},
        'current_slot_binding': [{**female, 'role': '공휴일(여)'},
                                 {'name': 'C', 'role': '공휴일(남)',
                                  'isSubstitute': False, 'zeroScore': False}],
        'original_holiday': {'name': 'private original label', 'isMajor': False, 'isRemoved': True},
        'skipped_candidates': [{'name': 'A', 'reason': 'private absence detail'}],
        'created_at': '2026-11-09 12:00:00',
    }
    return {'schema': 1, 'requiredContract': 1, 'events': [event], 'bases': [basis], 'operations': []}


def receipt():
    return {
        'operation_id': OPERATION_ID, 'payload_hash': 'e' * 64, 'action': 'ADD',
        'result': {'status': 'success', 'operationId': OPERATION_ID, 'dataVersion': 3,
                   'supplementId': 'supplement-1', 'changedDates': ['2027-06-17'],
                   'snapshot': 'auto_holiday_test.db'},
        'supplement_id': 'supplement-1', 'created_at': '2026-11-09 12:00:00',
    }


def test_legacy_read_is_empty_without_migrating(db, storage):
    conn, _ = db
    before = conn.execute('SELECT name FROM sqlite_master ORDER BY name').fetchall()
    assert storage.read_holiday_supplements(conn.cursor()) == {
        'schema': 1, 'events': [], 'bases': [], 'requiredContract': 0}
    assert conn.execute('SELECT name FROM sqlite_master ORDER BY name').fetchall() == before


def test_roundtrip_survives_new_connection_and_public_data_excludes_private_fields(db, storage):
    conn, path = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    original = state()
    original['operations'] = [receipt()]
    storage.write_holiday_supplements(conn.cursor(), original)
    conn.commit()
    with sqlite3.connect(path) as other:
        assert storage.read_holiday_supplements(other.cursor(), private=True) == original
        public = storage.read_holiday_supplements(other.cursor())
    event = public['events'][0]
    assert event['female_credit']['after'] == 2
    assert event['current_slot_binding'][0]['zeroScore'] is True
    assert 'original_holiday' not in event and 'skipped_candidates' not in event
    assert 'operations' not in public and 'private' not in json.dumps(public)


@pytest.mark.parametrize(('field', 'bad'), [
    ('schema', 2), ('schema', True), ('requiredContract', '1'),
    ('events', {}), ('bases', None),
])
def test_malformed_top_level_data_fails_before_existing_rows_change(db, storage, field, bad):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    original = state()
    storage.write_holiday_supplements(conn.cursor(), original)
    broken = copy.deepcopy(original)
    broken[field] = bad
    with pytest.raises(HTTPException):
        storage.write_holiday_supplements(conn.cursor(), broken)
    assert storage.read_holiday_supplements(conn.cursor(), private=True) == original


@pytest.mark.parametrize(('field', 'bad'), [
    ('date_key', '2027-02-30'), ('year', 2028), ('state', 'MAYBE'),
    ('credit_state', 'RETURNED'), ('selected_ordinal', -1), ('end_ordinal', 4),
    ('basis_revision', 99), ('order_snapshot', ['C', 'B', 'A']),
    ('female_credit', {'pool_key': 'holiday|F'}),
])
def test_invalid_credit_or_basis_reference_cannot_be_saved(db, storage, field, bad):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    broken = state()
    broken['events'][0][field] = bad
    with pytest.raises(HTTPException):
        storage.write_holiday_supplements(conn.cursor(), broken)
    assert storage.read_holiday_supplements(conn.cursor())['events'] == []


def test_active_date_uniqueness_is_enforced_before_write_and_by_sqlite(db, storage):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    original = state()
    duplicate = {**copy.deepcopy(original['events'][0]), 'supplement_id': 'supplement-2', 'allocation_seq': 2,
                 'state': 'NEEDS_REVIEW', 'selected_ordinal': 1, 'end_ordinal': 2}
    with pytest.raises(HTTPException):
        storage.write_holiday_supplements(conn.cursor(), {**original, 'events': original['events'] + [duplicate]})
    storage.write_holiday_supplements(conn.cursor(), original)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute('INSERT INTO holiday_supplements '
                     '(supplement_id,date_key,year,allocation_seq,state,credit_state,basis_revision,pool_key,schema_version,payload_json) '
                     'SELECT ?,date_key,year,2,?,credit_state,basis_revision,pool_key,schema_version,payload_json '
                     'FROM holiday_supplements', ('supplement-2', 'NEEDS_REVIEW'))


def test_corrupt_and_future_stored_payloads_are_not_silently_dropped(db, storage):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    storage.write_holiday_supplements(conn.cursor(), state())
    conn.execute("UPDATE holiday_supplements SET payload_json='not-json'")
    with pytest.raises(HTTPException):
        storage.read_holiday_supplements(conn.cursor())
    conn.execute('UPDATE holiday_supplements SET payload_json=?,schema_version=2', (json.dumps(state()['events'][0]),))
    with pytest.raises(HTTPException):
        storage.read_holiday_supplements(conn.cursor())


def test_known_derived_basis_review_is_not_trusted_or_persisted(db, storage):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    data = state()
    data['bases'][0].update(state='CURRENT', review_code='forged')
    storage.write_holiday_supplements(conn.cursor(), data)
    basis = storage.read_holiday_supplements(conn.cursor(), private=True)['bases'][0]
    assert 'state' not in basis and 'review_code' not in basis


def test_restore_preserves_receipts_and_required_contract(db, storage):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    data = state()
    data['operations'] = [receipt()]
    storage.write_holiday_supplements(conn.cursor(), data)
    storage.write_holiday_supplements(conn.cursor(), {
        'schema': 1, 'events': [], 'bases': [], 'requiredContract': 0, 'operations': []})
    restored = storage.read_holiday_supplements(conn.cursor(), private=True)
    assert restored['events'] == [] and restored['bases'] == []
    assert restored['operations'] == [receipt()]
    assert restored['requiredContract'] == 1
    assert storage.read_holiday_supplement_operation(conn.cursor(), OPERATION_ID) == receipt()


def test_same_receipt_merges_once_but_conflicting_payload_or_result_aborts(db, storage):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    storage.write_holiday_supplement_operation(conn.cursor(), receipt())
    storage.merge_holiday_supplement_operations(conn.cursor(), [receipt(), receipt()])
    for changed in ({**receipt(), 'payload_hash': 'f' * 64},
                    {**receipt(), 'result': {**receipt()['result'], 'dataVersion': 99}}):
        with pytest.raises(HTTPException) as conflict:
            storage.merge_holiday_supplement_operations(conn.cursor(), [changed])
        assert conflict.value.status_code == 409
    assert conn.execute('SELECT COUNT(*) FROM holiday_supplement_operations').fetchone()[0] == 1


@pytest.mark.parametrize('value', [0, None, '1'])
def test_contract_requires_exact_compatible_value(db, storage, value):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    storage.activate_holiday_contract(conn.cursor())
    conn.create_function('wlr_schedule_contract', 0, lambda: value)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE schedules SET value='bad'")


def test_old_connections_cannot_mutate_any_business_table_after_activation(db, storage):
    conn, path = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    conn.commit()
    old = sqlite3.connect(path)
    try:
        old.execute("UPDATE schedules SET value='pre-activation'")
        old.commit()
        storage.activate_holiday_contract(conn.cursor())
        conn.commit()
        for table in BUSINESS_TABLES:
            for sql in (f'DELETE FROM "{table}"',
                        f'UPDATE "{table}" SET ' + ("value='bad'" if table != 'app_settings' else "value='0'"),
                        f'INSERT INTO "{table}" ' + ("(value) VALUES ('bad')" if table != 'app_settings' else "VALUES ('test','bad')")):
                with pytest.raises(sqlite3.DatabaseError):
                    old.execute(sql)
                old.rollback()
        assert old.execute('SELECT value FROM schedules').fetchone()[0] == 'pre-activation'
        old.execute("INSERT INTO client_status VALUES (1,'heartbeat')")
        old.commit()
        conn.execute("UPDATE schedules SET value='compatible'")
        conn.commit()
        assert old.execute('SELECT value FROM schedules').fetchone()[0] == 'compatible'
    finally:
        old.close()


def test_missing_or_altered_contract_guard_blocks_verification_without_repair(db, storage):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    storage.activate_holiday_contract(conn.cursor())
    assert storage.verify_holiday_contract(conn.cursor()) == 1
    name = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='schedules' LIMIT 1").fetchone()[0]
    conn.execute(f'DROP TRIGGER "{name}"')
    with pytest.raises(HTTPException) as missing:
        storage.verify_holiday_contract(conn.cursor())
    assert missing.value.status_code == 503
    with pytest.raises(HTTPException):
        storage.activate_holiday_contract(conn.cursor())
    conn.execute(f'CREATE TRIGGER "{name}" BEFORE UPDATE ON schedules BEGIN SELECT 1; END')
    with pytest.raises(HTTPException):
        storage.verify_holiday_contract(conn.cursor())


def test_sqlite_backup_keeps_guards_and_restore_copy_needs_registered_udf(db, storage, tmp_path):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    storage.write_holiday_supplements(conn.cursor(), state())
    conn.commit()
    backup_path = tmp_path / 'restore.db'
    with sqlite3.connect(backup_path) as target:
        conn.backup(target)
    with sqlite3.connect(backup_path) as restore:
        assert restore.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        assert storage.read_holiday_supplements(restore.cursor())['events'][0]['supplement_id'] == 'supplement-1'
        with pytest.raises(sqlite3.DatabaseError):
            restore.execute("INSERT OR IGNORE INTO app_settings VALUES ('data_version','0')")
        restore.create_function('wlr_schedule_contract', 0, lambda: 1)
        storage.ensure_holiday_supplement_schema(restore.cursor())
        restore.execute("INSERT OR IGNORE INTO app_settings VALUES ('data_version','0')")
        assert storage.verify_holiday_contract(restore.cursor()) == 1


def test_write_respects_callers_transaction_rollback(db, storage):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    conn.commit()
    conn.execute('BEGIN IMMEDIATE')
    storage.write_holiday_supplements(conn.cursor(), state())
    conn.rollback()
    assert storage.read_holiday_supplements(conn.cursor()) == {
        'schema': 1, 'requiredContract': 0, 'events': [], 'bases': []}
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0] == 0


def test_label_only_receipt_does_not_activate_unused_feature(db, storage):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    noop = receipt()
    noop.update(action='NOOP', supplement_id=None)
    noop['result'].update(supplementId=None, changedDates=[], snapshot='')
    storage.write_holiday_supplements(conn.cursor(), {
        'schema': 1, 'requiredContract': 0, 'events': [], 'bases': [], 'operations': [noop]})
    assert storage.verify_holiday_contract(conn.cursor()) == 0
    assert storage.read_holiday_supplement_operation(conn.cursor(), OPERATION_ID) == noop
    assert storage.read_holiday_supplements(conn.cursor())['requiredContract'] == 0


@pytest.mark.parametrize('change', ['non_scalar_pool', 'non_scalar_receipt_date'])
def test_nested_malformed_values_raise_http_error_not_type_error(db, storage, change):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    broken = state()
    if change == 'non_scalar_pool':
        broken['events'][0]['pool_key'] = []
    else:
        broken['operations'] = [receipt()]
        broken['operations'][0]['result']['changedDates'] = [{}]
    with pytest.raises(HTTPException):
        storage.write_holiday_supplements(conn.cursor(), broken)


def test_guard_activation_without_explicit_begin_is_still_rollback_safe(db, storage):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    conn.commit()
    storage.activate_holiday_contract(conn.cursor())
    conn.rollback()
    assert storage.verify_holiday_contract(conn.cursor()) == 0
    assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0] == 0


def test_removed_active_date_index_cannot_silently_disable_protection(db, storage):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    storage.activate_holiday_contract(conn.cursor())
    conn.execute('DROP INDEX holiday_supplements_live_date')
    with pytest.raises(HTTPException) as failure:
        storage.verify_holiday_contract(conn.cursor())
    assert failure.value.status_code == 503


def test_receipt_batch_conflict_does_not_partially_insert_other_receipts(db, storage):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    storage.write_holiday_supplement_operation(conn.cursor(), receipt())
    other = copy.deepcopy(receipt())
    other['operation_id'] = '22222222-2222-4222-8222-222222222222'
    other['result']['operationId'] = other['operation_id']
    conflict = {**receipt(), 'payload_hash': 'f' * 64}
    with pytest.raises(HTTPException):
        storage.merge_holiday_supplement_operations(conn.cursor(), [other, conflict])
    assert storage.read_holiday_supplement_operation(conn.cursor(), other['operation_id']) is None


def test_reconciliation_keeps_old_basis_revision_for_historical_events(db, storage):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    data = state()
    data['bases'].append({**data['bases'][0], 'revision': 2, 'policy': 'PRESERVE_EXISTING',
                          'anchor_before_year': 1, 'prior_year_credit_hash': 'f' * 64})
    storage.write_holiday_supplements(conn.cursor(), data)
    reread = storage.read_holiday_supplements(conn.cursor(), private=True)
    assert len(reread['bases']) == 2
    assert reread['events'][0]['basis_revision'] == 1


def test_empty_nonconsuming_female_order_and_removed_actual_slot_roundtrip(db, storage):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    data = state()
    data['events'][0]['female_credit'] = {
        'pool_key': 'weekday|F', 'before': 0, 'after': 0, 'consumes': False, 'order_snapshot': []}
    data['events'][0]['current_slot_binding'] = []
    storage.write_holiday_supplements(conn.cursor(), data)
    reread = storage.read_holiday_supplements(conn.cursor(), private=True)
    assert reread['events'][0]['current_slot_binding'] == []
    assert reread['events'][0]['original_female_slot']['name'] == 'F2'


def test_legacy_slot_flag_omission_normalizes_to_false_without_mutating_input(db, storage):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    data = state()
    original = data['events'][0]['original_female_slot']
    del original['isSubstitute']
    del original['zeroScore']
    data['events'][0]['current_slot_binding'][0] = {'name': 'F2', 'role': '공휴일(여)'}
    storage.write_holiday_supplements(conn.cursor(), data)
    event = storage.read_holiday_supplements(conn.cursor(), private=True)['events'][0]
    assert event['original_female_slot'] == {
        'name': 'F2', 'role': '평일(여)', 'isSubstitute': False, 'zeroScore': False}
    assert event['current_slot_binding'][0]['isSubstitute'] is False
    assert original == {'name': 'F2', 'role': '평일(여)'}


@pytest.mark.parametrize('change', [
    'wrong_after', 'regular_marked_nonconsuming', 'expected_substitute_marked_nonconsuming',
    'consuming_name_not_in_historical_order', 'duplicate_occurrence_uses_wrong_turn',
])
def test_historical_female_credit_must_match_original_slot_before_any_write(db, storage, change):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    original = state()
    storage.write_holiday_supplements(conn.cursor(), original)
    broken = copy.deepcopy(original)
    event = broken['events'][0]
    credit = event['female_credit']
    if change == 'wrong_after':
        credit['after'] = 0
    elif change == 'regular_marked_nonconsuming':
        credit.update(consumes=False, after=1)
    elif change == 'expected_substitute_marked_nonconsuming':
        event['original_female_slot']['isSubstitute'] = True
        credit.update(consumes=False, after=1)
    elif change == 'consuming_name_not_in_historical_order':
        event['original_female_slot']['name'] = 'F4'
    else:
        credit.update(order_snapshot=['F1', 'F2', 'F3', 'F2'], before=2, after=2)
    with pytest.raises(HTTPException) as failure:
        storage.write_holiday_supplements(conn.cursor(), broken)
    assert failure.value.status_code == 400
    assert storage.read_holiday_supplements(conn.cursor(), private=True) == original


@pytest.mark.parametrize(('order', 'before', 'after', 'substitute', 'consumes'), [
    (['F1', 'F2', 'F3', 'F2'], 2, 0, False, True),
    (['F2'], 0, 0, False, True),
    (['F1', 'F2', 'F3'], 0, 2, True, True),
    (['F1', 'F2', 'F3'], 0, 0, True, False),
    (['F1', 'F3'], 1, 1, True, False),
    (['F1', 'F3'], 1, 1, False, False),
])
def test_historical_female_credit_preserves_legitimate_substitutes_and_occurrences(
        db, storage, order, before, after, substitute, consumes):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    data = state()
    event = data['events'][0]
    event['original_female_slot']['isSubstitute'] = substitute
    event['female_credit'].update(order_snapshot=order, before=before, after=after, consumes=consumes)
    # Later approvals may remove the actual woman; validation uses historical data only.
    event['current_slot_binding'] = []
    storage.write_holiday_supplements(conn.cursor(), data)
    actual = storage.read_holiday_supplements(conn.cursor(), private=True)['events'][0]
    assert actual['female_credit'] == event['female_credit']
    assert actual['current_slot_binding'] == []


@pytest.mark.parametrize('change', [
    'order_hash', 'basis_hash', 'changed_anchor', 'changed_prior_credit', 'changed_order',
])
def test_fingerprints_must_match_historical_order_and_referenced_basis(db, storage, change):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    original = state()
    storage.write_holiday_supplements(conn.cursor(), original)
    broken = copy.deepcopy(original)
    event, basis = broken['events'][0], broken['bases'][0]
    if change == 'order_hash':
        event['order_fingerprint'] = '0' * 64
    elif change == 'basis_hash':
        event['annual_basis_hash'] = '0' * 64
    elif change == 'changed_anchor':
        basis['anchor_before_year'] = 1
    elif change == 'changed_prior_credit':
        basis['prior_year_credit_hash'] = 'f' * 64
    else:
        event['order_snapshot'] = ['C', 'B', 'A']
        basis['sequence_snapshot'] = ['C', 'B', 'A']
    with pytest.raises(HTTPException) as failure:
        storage.write_holiday_supplements(conn.cursor(), broken)
    assert failure.value.status_code == 400
    assert storage.read_holiday_supplements(conn.cursor(), private=True) == original


@pytest.mark.parametrize('year', [2022, 2030, 9999])
def test_unsupported_year_basis_is_rejected_before_it_can_break_calendar_replay(db, storage, year):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    data = state()
    data['events'] = []
    data['bases'][0].update(year=year, effective_boundary=f'{year}-01-01')
    with pytest.raises(HTTPException) as failure:
        storage.write_holiday_supplements(conn.cursor(), data)
    assert failure.value.status_code == 400
    assert storage.read_holiday_supplements(conn.cursor())['bases'] == []


@pytest.mark.parametrize('year', [2023, 2029])
def test_supported_calendar_boundary_years_remain_restorable(db, storage, year):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    data = state()
    data['events'] = []
    data['bases'][0].update(year=year, effective_boundary=f'{year}-01-01')
    storage.write_holiday_supplements(conn.cursor(), data)
    assert storage.read_holiday_supplements(conn.cursor())['bases'][0]['year'] == year


def test_corrupted_persisted_credit_fails_closed_on_read(db, storage):
    conn, _ = db
    storage.ensure_holiday_supplement_schema(conn.cursor())
    storage.write_holiday_supplements(conn.cursor(), state())
    event = state()['events'][0]
    event['female_credit']['after'] = 0
    conn.execute('UPDATE holiday_supplements SET payload_json=?', (json.dumps(event),))
    with pytest.raises(HTTPException) as failure:
        storage.read_holiday_supplements(conn.cursor())
    assert failure.value.status_code == 503
