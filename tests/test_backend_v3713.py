"""3.17.13 백엔드 결함 보강 회귀시험.

각 시험 위의 [위험] 주석은 그 시험이 막는 실제 사고다. 지우지 마세요.
운영 DB 는 읽지도 쓰지도 않습니다. 모든 DB 는 시험마다 새로 만든 임시 폴더에 있습니다.
"""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from test_backend_regression import AUTH, DAY_A, DAY_B, DAY_C, SEED, load_app  # noqa: F401  (fixture 재사용)
from test_backend_usability import EDUCATION, absence, request_payload


@pytest.fixture
def seeded(load_app):
    app = load_app('source')
    app.save(copy.deepcopy(SEED))
    return app


def history_rows(app):
    with sqlite3.connect(app.module.DB_PATH) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute('SELECT * FROM change_history ORDER BY id')]


def setting(app, key):
    with sqlite3.connect(app.module.DB_PATH) as db:
        row = db.execute('SELECT value FROM app_settings WHERE key = ?', (key,)).fetchone()
    return row[0] if row else None


def close_month(app, month, closed=True, reason='시험'):
    response = app.post('/api/duty/month-close', {'month': month, 'closed': closed, 'reason': reason,
                                                  'baseVersion': app.data()['dataVersion']})
    assert response.status_code == 200, response.text


def daily_backup_name(app):
    module = app.module
    module.BACKUP_STATE['verified'] = ''
    folder = Path(module.backup_dir_path())
    for f in folder.glob('smart_duty_*.db'):
        f.unlink()
    module.ensure_daily_backup()
    return sorted(folder.glob('smart_duty_*.db'))[-1].name


def other_pc_write(module, comment, version_bump=True):
    """다른 PC(다른 연결)가 저장을 시도한다. 잠금에 막히면 None, 들어가면 새 버전."""
    db = sqlite3.connect(module.DB_PATH, timeout=0.1)
    try:
        db.execute('BEGIN IMMEDIATE')
        v = int(db.execute("SELECT value FROM app_settings WHERE key='data_version'").fetchone()[0]) + 1
        db.execute("UPDATE app_settings SET value=? WHERE key='data_version'", (str(v),))
        db.execute("INSERT INTO change_history(timestamp,type,date_key,details) VALUES('2030-01-01 00:00:00','PC2','x','PC2 save')")
        db.execute('UPDATE day_meta SET comment=? WHERE date_key=?', (comment, DAY_A))
        db.commit()
        return v
    except sqlite3.OperationalError:
        return None
    finally:
        db.close()


# ── 1. 복원 원자성 ─────────────────────────────────────────────────

# [위험] 복원이 버전 확인·이력 읽기·직전 사본·복사·버전 올리기를 서로 다른 연결로 해서, 그 사이 다른 PC 의
#   저장이 직전 사본에도 이력에도 없이 사라지고, 그 PC 의 낡은 화면 저장이 충돌 검사를 통과했다.
def test_restore_write_after_snapshot_is_blocked_or_kept_and_stale_save_gets_409(seeded, monkeypatch):
    module = seeded.module
    name = daily_backup_name(seeded)
    seeded.save({'comments': {DAY_A: '백업 뒤 메모'}, 'historyLog': {'type': 'X', 'details': '백업 뒤 변경'}})
    base = seeded.data()['dataVersion']
    original = module.take_named_snapshot
    seen = {}

    def snapshot_then_other_pc_saves(kind, label='', *args, **kwargs):
        path = original(kind, label, *args, **kwargs)
        seen['path'] = path
        seen['pc2'] = other_pc_write(module, 'PC2 메모')
        return path

    monkeypatch.setattr(module, 'take_named_snapshot', snapshot_then_other_pc_saves)
    response = seeded.post('/api/system/restore', {'name': name, 'baseVersion': base})
    monkeypatch.setattr(module, 'take_named_snapshot', original)
    assert response.status_code == 200, response.text
    new_version = response.json()['dataVersion']
    if seen['pc2'] is not None:
        # 들어갔다면 직전 사본에 있어야 하고, 복원 버전은 그 PC 가 가진 버전보다 커야 한다.
        with sqlite3.connect(seen['path']) as db:
            assert db.execute('SELECT comment FROM day_meta WHERE date_key=?', (DAY_A,)).fetchone()[0] == 'PC2 메모'
        assert new_version > seen['pc2']
    # 다른 PC 는 복원 전 화면(최대 base 또는 자기 저장 버전)을 들고 있다. 그 저장은 거절되어야 한다.
    stale = seen['pc2'] if seen['pc2'] is not None else base
    r = seeded.post('/api/duty/save-all', {'comments': {DAY_A: 'PC2 낡은 덮어쓰기'}, 'baseVersion': stale})
    assert r.status_code == 409, r.text
    assert seeded.data()['comments'].get(DAY_A) != 'PC2 낡은 덮어쓰기'
    assert seen['pc2'] is None, '복원은 쓰기 잠금을 쥔 채 직전 사본을 떠야 한다(다른 PC 저장은 기다리거나 실패)'


# [위험] 복사 뒤 단계에서 실패하면 당직표는 이미 옛것으로 바뀌었는데 버전은 그대로라, 다른 PC 화면이
#   따라오지 않고 그 화면의 저장이 복원을 덮어썼다. 실패하면 아무것도 바뀌지 않아야 한다.
def test_restore_failure_midway_changes_nothing(seeded, monkeypatch):
    module = seeded.module
    name = daily_backup_name(seeded)
    for i in range(2):
        seeded.save({'comments': {DAY_A: f'수정 {i}'}, 'historyLog': {'type': 'X', 'details': f'백업 뒤 {i}'}})
    before = seeded.data()
    rows_before = history_rows(seeded)
    original = module.insert_history

    def boom(cur, timestamp, kind, *args, **kwargs):
        if kind == '백업 복원':
            raise sqlite3.OperationalError('disk I/O error (시험용)')
        return original(cur, timestamp, kind, *args, **kwargs)

    monkeypatch.setattr(module, 'insert_history', boom)
    client = TestClient(module.app, raise_server_exceptions=False)
    response = client.post('/api/system/restore', json={'name': name, 'baseVersion': before['dataVersion']}, headers=AUTH)
    monkeypatch.setattr(module, 'insert_history', original)
    assert response.status_code == 500
    assert '복원' in response.json()['detail'] and '공유 폴더' in response.json()['detail']
    after = seeded.data()
    assert after == before
    assert history_rows(seeded) == rows_before


def test_restore_from_older_schema_backup_is_migrated(seeded):
    module = seeded.module
    seeded.save({'absences': [absence(name='A', type='연차', education=EDUCATION)]})
    folder = Path(module.backup_dir_path())
    folder.mkdir(exist_ok=True)
    old = folder / 'premigration_3.14.5_20260101-000000.db'
    shutil.copyfile(module.DB_PATH, old)
    with sqlite3.connect(old) as db:
        db.execute('DROP INDEX IF EXISTS idx_change_requests_receipt_token')
        for table, column in (('change_history', 'actor'), ('change_history', 'host'),
                              ('change_history', 'before_json'), ('change_history', 'after_json'),
                              ('absences', 'education_json'), ('change_requests', 'revision'),
                              ('change_requests', 'absence_details')):
            db.execute(f'ALTER TABLE {table} DROP COLUMN {column}')
        db.execute("UPDATE app_settings SET value='보존 공지(옛 구조)' WHERE key='global_memo'")
    seeded.save({'globalMemo': '나중 값'})
    response = seeded.post('/api/system/restore', {'name': old.name, 'baseVersion': seeded.data()['dataVersion']})
    assert response.status_code == 200, response.text
    data = seeded.data()
    assert data['globalMemo'] == '보존 공지(옛 구조)'
    assert data['absences'][0]['name'] == 'A' and 'education' not in data['absences'][0]
    with sqlite3.connect(module.DB_PATH) as db:
        assert {'actor', 'host', 'before_json'} <= {r[1] for r in db.execute('PRAGMA table_info(change_history)')}
        assert 'education_json' in {r[1] for r in db.execute('PRAGMA table_info(absences)')}
    # 복원 뒤에도 신청·저장이 정상 동작한다.
    assert seeded.submit('SUBSTITUTE').status_code == 200
    seeded.save({'globalMemo': '복원 뒤 저장'})


# ── 2. 복원과 월 마감 ──────────────────────────────────────────────

# [위험] 복원하면 closed_months 가 백업 시점 값으로 돌아가, 사유 기록 없이 마감이 풀렸다.
def test_restore_keeps_current_closed_months_and_records_it(seeded):
    name = daily_backup_name(seeded)
    close_month(seeded, '2030-06')
    response = seeded.post('/api/system/restore', {'name': name, 'baseVersion': seeded.data()['dataVersion']})
    assert response.status_code == 200, response.text
    assert seeded.data()['closedMonths'] == ['2030-06']
    last = history_rows(seeded)[-1]
    assert last['type'] == '백업 복원' and '마감' in last['details'] and '2030-06' in last['details']


def test_restore_does_not_reclose_months_reopened_after_backup(seeded):
    close_month(seeded, '2030-05')
    name = daily_backup_name(seeded)
    close_month(seeded, '2030-05', closed=False, reason='정정')
    response = seeded.post('/api/system/restore', {'name': name, 'baseVersion': seeded.data()['dataVersion']})
    assert response.status_code == 200, response.text
    assert seeded.data()['closedMonths'] == []


# ── 3. 마감 달의 부재·휴일 ─────────────────────────────────────────

# [위험] 저장 경로의 마감 검사가 휴가/부재와 지정 휴일을 보지 않아, 신청으로는 423 인 일을 저장으로는 할 수 있었다.
def test_closed_month_blocks_absence_add_change_remove(seeded):
    seeded.save({'absences': [absence(id=80, name='B', type='연차', start='2030-06-03', end='2030-06-04')]})
    close_month(seeded, '2030-06')
    current = seeded.data()['absences']
    seeded.save({'absences': current})   # 그대로면 저장된다
    seeded.save({'absences': current + [absence(id=81, name='C', type='연차', start='2030-06-25', end='2030-07-02')]}, 423)
    seeded.save({'absences': current + [absence(id=82, name='C', type='연차', start='2030-05-28', end='2030-06-01')]}, 423)
    seeded.save({'absences': [{**current[0], 'end': '2030-06-05'}]}, 423)
    seeded.save({'absences': [{**current[0], 'type': '파견'}]}, 423)
    seeded.save({'absences': []}, 423)
    # 마감되지 않은 달은 자유롭다.
    seeded.save({'absences': current + [absence(id=83, name='C', type='연차', start='2030-07-02', end='2030-07-03')]})
    assert len(seeded.data()['absences']) == 2


def test_closed_month_blocks_custom_holiday_changes(seeded):
    close_month(seeded, '2030-06')
    holidays = seeded.data()['customHolidays']
    seeded.save({'customHolidays': holidays})
    seeded.save({'customHolidays': {**holidays, '2030-06-03': {'name': 'x', 'isMajor': True}}}, 423)
    seeded.save({'customHolidays': {}}, 423)
    seeded.save({'customHolidays': {'2030-06-19': {**holidays['2030-06-19'], 'isRemoved': False}}}, 423)
    seeded.save({'customHolidays': {**holidays, '2030-07-03': {'name': 'y', 'isMajor': False}}})


# ── 4. 저장 값 검증과 JSON 백업 복구 ──────────────────────────────

KEYS = ('members', 'schedules', 'comments', 'noDuty', 'absences', 'absenceTypes', 'customHolidays', 'rules', 'globalMemo')


def json_roundtrip(load_app, origin):
    src = origin.data()
    target = load_app('source')
    response = target.post('/api/duty/save-all', {**{k: src[k] for k in KEYS}, 'historyBulk': src['history'],
                                                  'historyLog': {'type': '데이터 복구', 'details': 'JSON'}, 'baseVersion': 0})
    return src, target, response


# [위험] 저장은 role null·시작>종료 부재·달력에 없는 날짜를 받아 두고, JSON 백업 복구는 그 값을 거절해 복구 전체가 실패했다.
@pytest.mark.parametrize('bad,fragment', [
    ({'absences': [absence(start=DAY_C, end=DAY_A)]}, '종료일'),
    ({'absences': [absence(start='2030-02-30', end='2030-03-01')]}, '날짜'),
    ({'absences': [absence(start='2030-6-1', end='2030-06-02')]}, '날짜'),
    ({'absences': [absence(start=None)]}, '날짜'),
    ({'schedules': {'2030-02-30': [{'name': 'A', 'role': '당직'}]}}, '날짜'),
    ({'schedules': {DAY_A: [{'name': 'A', 'role': 5}]}}, '역할'),
    ({'schedules': {DAY_A: 'A'}}, '형식'),
    ({'schedules': {DAY_A: ['A']}}, '형식'),
])
def test_save_rejects_values_backup_import_would_reject(seeded, bad, fragment):
    before = seeded.data()
    response = seeded.post('/api/duty/save-all', {**bad, 'baseVersion': before['dataVersion']})
    assert response.status_code == 400, response.text
    assert fragment in response.json()['detail']
    assert seeded.data() == before


def test_null_role_is_normalized_and_json_backup_recovers(load_app, seeded):
    seeded.save({'schedules': {**SEED['schedules'], DAY_A: [{'name': 'A', 'role': None}]}})
    assert seeded.data()['schedules'][DAY_A] == [{'name': 'A', 'role': '', 'isSubstitute': False, 'zeroScore': False}]
    seeded.save({'schedules': {**SEED['schedules'], DAY_A: [{'name': 'C', 'role': '당직'}]}})
    src, target, response = json_roundtrip(load_app, seeded)
    assert response.status_code == 200, response.text
    assert all(target.data()[k] == src[k] for k in KEYS)


# [위험] 옛 DB 에 이미 들어 있는 잘못된 전후 기록 하나 때문에 JSON 백업 복구 전체가 실패했다.
#   그 전후 기록만 빼고 이력 행은 남긴다.
def test_history_import_drops_only_malformed_snapshot(load_app, seeded):
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        db.execute("INSERT INTO change_history (timestamp, type, date_key, details, before_json, after_json) VALUES "
                   "('2030-01-02 00:00:00', '옛 수정', ?, '깨진 전후', ?, ?)",
                   (DAY_A, '{"schedules": {"2030-06-17": [{"name": "A", "role": null}]}}',
                    '{"schedules": {"2030-06-17": []}}'))
        db.execute("INSERT INTO change_history (timestamp, type, date_key, details, before_json, after_json) VALUES "
                   "('2030-01-03 00:00:00', '옛 부재', ?, '거꾸로 부재', ?, ?)",
                   (DAY_A, '{"absences": [{"id": 1, "name": "A", "type": "연차", "start": "2030-06-20", "end": "2030-06-18"}]}',
                    '{"absences": []}'))
    src, target, response = json_roundtrip(load_app, seeded)
    assert response.status_code == 200, response.text
    imported = {h['details']: h for h in target.data()['history']}
    for details in ('깨진 전후', '거꾸로 부재'):
        row = imported['[가져온 이력] ' + details + ' (전후 기록 형식 오류로 생략)']
        assert 'before' not in row and 'after' not in row
    good = imported['[가져온 이력] [가져온 이력] 초기 이력']
    assert good['timestamp'] == '2030-01-01 00:00:00'


# ── 5. 안전 사본 정리 순서 ─────────────────────────────────────────

# [위험] 이름 문자열로 정렬해 v100 이 v99 앞에 오고, 방금 만든 복원 직전 사본이 바로 지워졌다.
def test_prune_keeps_newest_prerestore_even_when_name_sorts_first(seeded):
    module = seeded.module
    folder = Path(module.backup_dir_path())
    folder.mkdir(exist_ok=True)
    old_time = time.time() - 3600
    for v in range(90, 100):
        p = folder / f'prerestore_v{v}_20260101-000000.db'
        p.write_bytes(b'x')
        os.utime(p, (old_time + v, old_time + v))
    newest = Path(module.take_named_snapshot('prerestore', 'v100'))
    assert newest.exists()
    remaining = sorted(p.name for p in folder.glob('prerestore_*.db'))
    assert len(remaining) == module.SNAPSHOT_KEEP
    assert 'prerestore_v90_20260101-000000.db' not in remaining


def test_prune_keeps_newest_premigration_with_two_digit_patch(seeded):
    module = seeded.module
    folder = Path(module.backup_dir_path())
    folder.mkdir(exist_ok=True)
    old_time = time.time() - 3600
    for i, v in enumerate([f'3.9.{n}' for n in range(10)]):
        p = folder / f'premigration_{v}_20260101-0000{i:02d}.db'
        p.write_bytes(b'x')
        os.utime(p, (old_time + i, old_time + i))
    newest = Path(module.take_named_snapshot('premigration', '3.10.0'))
    assert newest.exists(), "'3.10.0' 이 '3.9.x' 보다 이름순으로 앞서도 새 사본은 남아야 한다"
    assert not (folder / 'premigration_3.9.0_20260101-000000.db').exists()
    assert (folder / 'premigration_3.9.9_20260101-000009.db').exists()


# ── 6. 신청 글자 수 제한 ───────────────────────────────────────────

# [위험] 인증 없는 신청 API 가 글자 수를 제한하지 않아 수 MB 사유로 공유 DB 가 부풀었다.
@pytest.mark.parametrize('changes,fragment', [
    ({'reason': 'x' * 501}, '사유'),
    ({'absence_details': {**EDUCATION, 'title': 'x' * 501}}, '교육'),
    ({'absence_details': {**EDUCATION, 'content': 'x' * 2001}}, '교육'),
    ({'requester_name': 'x' * 51}, '50자'),
    ({'absence_type': 'x' * 51}, '50자'),
])
def test_request_text_length_limits(seeded, changes, fragment):
    seeded.save({'absenceTypes': ['연차', '파견', '교육']})
    response = seeded.post('/api/duty/request-change', request_payload('ABSENCE', **changes), auth=False)
    assert response.status_code == 400, response.text
    assert fragment in response.json()['detail']
    assert not seeded.rows('change_requests')


def test_request_length_limits_allow_frontend_maximums_and_apply_to_edits(seeded):
    seeded.save({'absenceTypes': ['연차', '파견', '교육']})
    details = {**EDUCATION, 'title': 't' * 500, 'content': 'c' * 2000, 'notes': 'n' * 2000}
    ok = seeded.post('/api/duty/request-change', request_payload('ABSENCE', reason='r' * 500, absence_details=details), auth=False)
    assert ok.status_code == 200, ok.text
    token = ok.json()['receiptToken']
    edit = seeded.post('/api/duty/request/manage', {'receipt_token': token, 'action': 'EDIT',
                                                    'request': request_payload('ABSENCE', reason='r' * 501)}, auth=False)
    assert edit.status_code == 400 and '사유' in edit.json()['detail']
    for kind, extra in (('SWAP', {'swap_worker': 'x' * 51}), ('SUBSTITUTE', {'substitute_name': 'x' * 51})):
        r = seeded.post('/api/duty/request-change', request_payload(kind, **extra), auth=False)
        assert r.status_code == 400 and '50자' in r.json()['detail'], r.text


# ── 7. 이력 ────────────────────────────────────────────────────────

# [위험] 화면이 보낸 시각을 그대로 써서 이력 시각을 과거로 꾸밀 수 있었다.
def test_history_log_timestamp_is_server_time(seeded):
    seeded.save({'comments': {DAY_A: 'q'}, 'historyLog': {'timestamp': '2001-01-01 00:00:00', 'type': '임의',
                                                         'dateKey': DAY_A, 'details': '과거로'}})
    row = history_rows(seeded)[-1]
    assert row['details'] == '과거로'
    assert row['timestamp'][:10] == datetime.now().strftime('%Y-%m-%d')


# [위험] 사원·규칙·휴일·메모·휴무·공지·부재 구분을 바꿔도 화면이 이력을 보내지 않으면 아무 기록이 없었다.
@pytest.mark.parametrize('change,label', [
    ({'members': [{'id': 1, 'name': 'A', 'gender': 'M'}]}, '사원'),
    ({'rules': {'x': 1}}, '규칙'),
    ({'customHolidays': {}}, '휴일'),
    ({'comments': {}}, '메모'),
    ({'noDuty': {}}, '휴무'),
    ({'globalMemo': '바뀐 공지'}, '공지'),
    ({'absenceTypes': ['연차']}, '부재 구분'),
])
def test_save_without_history_log_records_changed_collections(seeded, change, label):
    count = len(history_rows(seeded))
    seeded.save(change)
    rows = history_rows(seeded)
    assert len(rows) == count + 1
    assert label in rows[-1]['details']
    assert rows[-1]['host'] == seeded.module.HOST_LABEL and rows[-1]['actor'] == seeded.module.ACTOR_LABEL


def test_identical_save_without_history_log_adds_no_row(seeded):
    data = seeded.data()
    count = len(history_rows(seeded))
    seeded.save({k: data[k] for k in KEYS})
    assert len(history_rows(seeded)) == count


# ── 8. 교차 사이트 ─────────────────────────────────────────────────

CFG_OPEN = ('[NETWORK]\nopen_browser=false\ntray_icon=false\nadmin_password=\nbind_host=0.0.0.0\n'
            'db_timeout_seconds=0.2\nallowed_hosts=onair.corp.example, alias2\n')


# [위험] check-path 가 GET 이라 다른 사이트의 <img src> 한 줄로 이 PC 가 임의 UNC 경로에 접속하고 파일을 썼다.
def test_check_path_is_post_only(seeded, tmp_path):
    target = tmp_path / 'share'
    target.mkdir()
    r = seeded.client.post('/api/system/check-path', json={'path': str(target)}, headers=AUTH)
    assert r.status_code == 200 and r.json()['writable'] is True
    g = seeded.client.get(f'/api/system/check-path?path={target}', headers=AUTH)
    assert g.status_code != 200 or 'writable' not in g.text
    assert seeded.client.post('/api/system/check-path', json={'path': str(target)}).status_code == 401


def test_websocket_rejects_foreign_origin(seeded):
    client = seeded.client
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/ws/duty', headers={'Origin': 'http://evil.example'}):
            pytest.fail('다른 사이트의 웹소켓 연결이 받아들여졌다')
    port = seeded.module.SERVER_PORT
    for origin in (f'http://127.0.0.1:{port}', f'http://localhost:{port}', 'http://testserver'):
        with client.websocket_connect('/ws/duty', headers={'Origin': origin}):
            pass
    with client.websocket_connect('/ws/duty'):
        pass


def test_host_header_allowlist(load_app):
    app = load_app('source', config_text=CFG_OPEN)
    client = app.client
    ok_hosts = ['127.0.0.1:8000', 'localhost:8000', '[::1]:8000', '10.1.2.3:8000', '10.1.2.3',
                socket.gethostname(), socket.gethostname().upper() + ':8000', 'onair.corp.example:8000', 'ALIAS2',
                app.module.HOST_LABEL]
    for host in ok_hosts:
        r = client.get('/api/system/health', headers={'Host': host})
        assert r.status_code == 200, (host, r.text)
    for host in ('evil.example', 'evil.example:8000', 'rebind.attacker.test:8000'):
        r = client.get('/api/system/health', headers={'Host': host})
        assert r.status_code == 400, host
        # v3.18.0 설정 키 이름 대신 화면의 설정 이름으로 안내한다.
        assert '허용할 접속 이름' in r.json()['detail'] and 'allowed_hosts' not in r.text
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/ws/duty', headers={'Host': 'evil.example'}):
            pytest.fail('허용되지 않은 Host 의 웹소켓 연결이 받아들여졌다')


def test_cross_origin_write_is_refused(load_app):
    app = load_app('source', config_text=CFG_OPEN.replace('bind_host=0.0.0.0', 'bind_host=127.0.0.1'))
    app.save(copy.deepcopy(SEED))
    before = app.data()
    body = {'globalMemo': '교차 사이트', 'baseVersion': before['dataVersion']}
    r = app.client.post('/api/duty/save-all', json=body, headers={'Origin': 'http://evil.example'})
    assert r.status_code == 403
    r = app.client.post('/api/duty/save-all', json=body, headers={'Origin': 'null'})
    assert r.status_code == 403
    assert app.data() == before
    r = app.client.post('/api/duty/save-all', json=body, headers={'Origin': 'http://testserver'})
    assert r.status_code == 200, r.text


# ── 9. 관리자 신청 목록 ────────────────────────────────────────────

def test_admin_request_list_hides_receipt_tokens(seeded):
    assert seeded.submit('SUBSTITUTE').status_code == 200
    rows = seeded.get('/api/duty/requests', headers=AUTH).json()['requests']
    assert rows and all('receipt_token' not in r for r in rows)
    assert rows[0]['requester_name'] == 'A'


# ── 10. 시스템 정보 경로 노출 ──────────────────────────────────────

def test_system_info_hides_paths_from_remote_callers(load_app):
    app = load_app('source', config_text=CFG_OPEN)
    app.save(copy.deepcopy(SEED))
    remote = TestClient(app.module.app, client=('10.1.2.3', 50000))
    local = TestClient(app.module.app, client=('127.0.0.1', 50000))
    r = remote.get('/api/system/info').json()
    assert r['mode'] == 'ADMIN' and r['dbOk'] is True and 'dataVersion' in r
    for key in ('dbPath', 'storageDir', 'hostLabel'):
        assert not r.get(key), key
    assert not r['lastWrite'].get('host')
    assert app.module.DB_PATH not in str(r) and app.module.STORAGE_DIR not in str(r)
    full = local.get('/api/system/info').json()
    assert full['dbPath'] == app.module.DB_PATH and full['storageDir'] == app.module.STORAGE_DIR
    assert full['hostLabel'] == app.module.HOST_LABEL and full['lastWrite']['host']


def test_system_info_remote_with_password_needs_token_for_paths(load_app):
    app = load_app('source')
    remote = TestClient(app.module.app, client=('10.1.2.3', 50000))
    assert not remote.get('/api/system/info').json().get('dbPath')
    assert remote.get('/api/system/info', headers=AUTH).json()['dbPath'] == app.module.DB_PATH


# ── 11. 막히는 작업은 스레드에서 ──────────────────────────────────

# [위험] async 핸들러 안에서 DB 잠금 대기·SMB 복사를 해, 그동안 이 PC 서버 전체(화면·웹소켓)가 멈췄다.
def test_blocking_handlers_are_sync_and_still_broadcast(seeded):
    module = seeded.module
    for fn in (module.save_all_data, module.process_request_action, module.submit_change_request,
               module.manage_change_request, module.set_month_close, module.restore_backup):
        assert not asyncio.iscoroutinefunction(fn), fn.__name__
    with seeded.client.websocket_connect('/ws/duty') as ws:
        seeded.save({'globalMemo': '방송 확인'}, code=200)
        assert ws.receive_json()['event'] == 'DATA_SYNCED'
        r = seeded.submit('SUBSTITUTE')
        assert r.status_code == 200
        assert ws.receive_json()['event'] == 'NEW_REQUEST_SUBMITTED'
        assert seeded.action(r.json()['requestId']).status_code == 200
        assert ws.receive_json()['event'] == 'REQUEST_PROCESSED'


# ── 12. 접수 토큰 고유 색인 ───────────────────────────────────────

# [위험] 옛 DB 에 receipt_token='' 행이 둘 이상이면 고유 색인 생성이 실패해 신청 접수·결재가 모두 막혔다.
def test_receipt_token_index_survives_old_empty_tokens(seeded):
    module = seeded.module
    with sqlite3.connect(module.DB_PATH) as db:
        db.execute('DROP INDEX idx_change_requests_receipt_token')
        for i in range(2):
            db.execute("INSERT INTO change_requests (requester_name, target_date, request_type, created_at, receipt_token) "
                       "VALUES ('A', ?, 'SUBSTITUTE', '2030-01-01 00:00:00', '')", (DAY_A,))
    assert seeded.submit('SWAP').status_code == 200
    with sqlite3.connect(module.DB_PATH) as db:
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='idx_change_requests_receipt_token'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM change_requests WHERE receipt_token = ''").fetchone()[0] == 0


# [위험] 검증을 새로 넣으면서 이미 DB 에 들어 있던 옛 기록(시작>종료 부재, 0 없는 날짜 키)까지 거절하면,
#   화면이 늘 전체를 다시 보내므로 그 뒤 모든 저장이 막힌다. 그대로 돌려보낸 옛 기록은 받고, 고치거나 새로 넣는 값만 검사한다.
def test_existing_legacy_rows_do_not_block_ordinary_saves(seeded):
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        db.execute("INSERT INTO absences (id, name, type, start_date, end_date) VALUES (90, 'B', '연차', '2030-08-20', '2030-08-18')")
        db.execute("INSERT INTO schedules (date_key, slot_index, worker_name, role) VALUES ('2030-8-1', 0, 'C', NULL)")
    data = seeded.data()
    legacy = data['absences']
    assert data['schedules']['2030-8-1'][0]['role'] is None
    seeded.save({'schedules': data['schedules'], 'absences': legacy + [absence(id=91, name='C', type='연차', start='2030-09-01', end='2030-09-02')]})
    after = seeded.data()
    assert after['schedules']['2030-8-1'][0]['role'] == ''
    assert {a['id'] for a in after['absences']} == {90, 91}
    changed = [{**legacy[0], 'start': '2030-08-21'}]
    r = seeded.post('/api/duty/save-all', {'absences': changed, 'baseVersion': after['dataVersion']})
    assert r.status_code == 400 and '종료일' in r.json()['detail']
    new_bad_key = {**after['schedules'], '2030-9-1': [{'name': 'A', 'role': '당직'}]}
    r = seeded.post('/api/duty/save-all', {'schedules': new_bad_key, 'baseVersion': after['dataVersion']})
    assert r.status_code == 400 and '날짜' in r.json()['detail']


# [위험] 화면 maxlength 와 서버 제한이 어긋나면, 화면에서 입력이 허용된 글자가 저장 때 400 으로 거절된다.
def test_frontend_education_maxlength_matches_server_limits():
    import pathlib, re, importlib.util
    root = pathlib.Path(__file__).resolve().parents[1]
    js = (root / 'frontend' / 'dist' / 'usability_requests.js').read_text(encoding='utf-8')
    src = (root / 'backend' / 'main.py').read_text(encoding='utf-8')
    limits = eval(re.search(r'^EDUCATION_TEXT_LIMITS = (\{.*\})$', src, re.M).group(1))
    m = re.search(r"\['content','notes'\]\.includes\(key\)\?`<textarea[^`]*maxlength=\"(\d+)\"[^`]*`:`<input[^`]*maxlength=\"(\d+)\"", js)
    assert m, 'usability_requests.js 의 교육 입력 maxlength 를 찾지 못했습니다'
    long_limit, short_limit = int(m.group(1)), int(m.group(2))
    for key, limit in limits.items():
        assert limit == (long_limit if key in ('content', 'notes') else short_limit), key
