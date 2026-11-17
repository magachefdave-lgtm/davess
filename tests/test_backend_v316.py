"""v3.16.0 백엔드 보강 회귀시험.

각 시험 위의 [위험] 주석은 그 시험이 막는 실제 사고다. 지우지 마세요.
운영 DB 는 읽지도 쓰지도 않습니다. 모든 DB 는 시험마다 새로 만든 임시 폴더에 있습니다.
"""
from __future__ import annotations

import copy
import csv
import io
import os
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from test_backend_regression import App, AUTH, DAY_A, DAY_B, DAY_C, SEED, load_app  # noqa: F401  (fixture 재사용)


@pytest.fixture
def seeded(load_app):
    app = load_app('source')
    app.save(copy.deepcopy(SEED))
    return app


def raw_save(app, payload, code):
    response = app.post('/api/duty/save-all', payload)
    assert response.status_code == code, response.text
    return response


def history_rows(app):
    with sqlite3.connect(app.module.DB_PATH) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute('SELECT * FROM change_history ORDER BY id')]


# ── A-1 ────────────────────────────────────────────────────────────

# [위험] all-data 가 트랜잭션 없이 읽고 dataVersion 을 마지막에 읽으면, 그 사이 다른 PC 가 저장했을 때
#   '옛 당직표 + 새 버전 번호' 를 받는다. 그 상태로 저장하면 충돌 검사를 통과해 남의 저장을 지운다.
def test_all_data_is_one_consistent_snapshot(seeded, monkeypatch):
    module = seeded.module
    original = module.read_closed_months
    injected = {'done': False}

    def write_from_other_pc(cur):
        # 당직표를 이미 읽은 뒤, 다른 PC 가 새 당직을 저장하고 버전을 올린다.
        if not injected['done']:
            injected['done'] = True
            other = sqlite3.connect(module.DB_PATH, timeout=0.1)
            try:
                other.execute("INSERT INTO schedules (date_key, slot_index, worker_name, role) VALUES ('2030-06-25', 0, 'C', '당직')")
                other.execute("UPDATE app_settings SET value = CAST(value AS INTEGER) + 1 WHERE key = 'data_version'")
                other.commit()
            except sqlite3.OperationalError:
                pass   # 읽기 트랜잭션이 잠금을 쥐고 있으면 쓰기가 기다리다 실패한다 — 정상
            finally:
                other.close()
        return original(cur)

    monkeypatch.setattr(module, 'read_closed_months', write_from_other_pc)
    data = seeded.data()
    monkeypatch.setattr(module, 'read_closed_months', original)
    after = seeded.data()
    has_injected = '2030-06-25' in data['schedules']
    # 받은 당직표와 버전 번호가 같은 시점이어야 한다.
    if has_injected:
        assert data['dataVersion'] == after['dataVersion']
    else:
        injected_landed = '2030-06-25' in after['schedules']
        assert data['dataVersion'] == (after['dataVersion'] - 1 if injected_landed else after['dataVersion'])


# [위험] 버전을 먼저 읽기만 하고 트랜잭션으로 묶지 않으면, 버전을 읽은 직후 다른 PC 가 저장했을 때
#   '옛 버전 번호 + 새 당직표' 가 섞인다. 모든 읽기가 한 시점을 봐야 한다.
def test_all_data_reads_everything_from_the_same_moment(seeded, monkeypatch):
    module = seeded.module
    original = module.read_data_version
    state = {'armed': True}

    def version_then_other_pc_saves(cur):
        value = original(cur)
        if state['armed']:
            state['armed'] = False
            other = sqlite3.connect(module.DB_PATH, timeout=0.1)
            try:
                other.execute("INSERT INTO schedules (date_key, slot_index, worker_name, role) VALUES ('2030-06-26', 0, 'C', '당직')")
                other.execute("UPDATE app_settings SET value = CAST(value AS INTEGER) + 1 WHERE key = 'data_version'")
                other.commit()
            except sqlite3.OperationalError:
                pass
            finally:
                other.close()
        return value

    monkeypatch.setattr(module, 'read_data_version', version_then_other_pc_saves)
    data = seeded.client.get('/api/duty/all-data').json()
    monkeypatch.setattr(module, 'read_data_version', original)
    after = seeded.data()
    if '2030-06-26' in data['schedules']:
        assert data['dataVersion'] == after['dataVersion'], '새 당직표에는 새 버전 번호가 따라와야 한다'
    else:
        assert '2030-06-26' not in after['schedules'] or data['dataVersion'] == after['dataVersion'] - 1


# ── A-2 ────────────────────────────────────────────────────────────

# [위험] baseVersion 없는 저장을 받으면 충돌 검사를 건너뛰고 전체 교체를 해, 캐시된 옛 화면 하나가
#   다른 PC 의 저장을 지웠다.
def test_save_without_base_version_is_refused_and_changes_nothing(seeded):
    before = seeded.data()
    raw_save(seeded, {'members': [], 'schedules': {}, 'globalMemo': '덮어쓰기'}, 428)
    assert seeded.data() == before
    raw_save(seeded, {'globalMemo': '덮어쓰기', 'baseVersion': None}, 428)
    assert seeded.data() == before


# ── B-1 / B-2 ──────────────────────────────────────────────────────

# [위험] historyBulk 를 받으면 이력 표를 통째로 지우고 화면이 보낸 내용으로 다시 채웠다.
#   심사에서 근거를 물으면 이미 사라졌거나 화면이 보낸 대로 바뀐 상태일 수 있었다.
def test_history_bulk_never_deletes_existing_history(seeded):
    before = history_rows(seeded)
    assert before
    seeded.save({'historyBulk': [{'timestamp': '2000-01-01 00:00:00', 'type': '위조', 'dateKey': DAY_A,
                                  'details': '바꿔치기'}]})
    after = history_rows(seeded)
    assert [r['details'] for r in after] == [r['details'] for r in before]
    assert not any('바꿔치기' in r['details'] for r in after)


# [위험] 저장할 때마다 500건을 넘는 이력을 지웠다.
def test_history_is_not_truncated_at_500(seeded):
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        db.executemany("INSERT INTO change_history (timestamp, type, date_key, details) VALUES ('2030-01-01 00:00:00', 'T', ?, ?)",
                       [(DAY_A, f'이력 {i}') for i in range(520)])
    total = len(history_rows(seeded))
    seeded.save({'globalMemo': '한 건 더', 'historyLog': {'timestamp': '2030-01-02 00:00:00', 'type': '변경',
                                                         'dateKey': DAY_A, 'details': '추가'}})
    assert len(history_rows(seeded)) == total + 1
    assert len(seeded.data()['history']) == 500   # 화면에는 최근 500건만


# [위험] 관리자 암호가 공용이라 누가 바꿨는지 알 수 없었다. PC·계정은 서버가 채우고 화면 값은 무시한다.
def test_history_records_server_side_host_and_account(seeded):
    seeded.save({'globalMemo': '기록', 'historyLog': {'timestamp': '2030-01-03 00:00:00', 'type': '변경',
                                                     'dateKey': DAY_A, 'details': '누가', 'actor': '가짜', 'host': '가짜'}})
    row = history_rows(seeded)[-1]
    assert row['details'] == '누가'
    assert row['host'] == seeded.module.HOST_LABEL and row['host'] != '가짜'
    assert row['actor'] == seeded.module.ACTOR_LABEL and row['actor'] != '가짜'
    item = seeded.data()['history'][0]
    assert item['host'] == seeded.module.HOST_LABEL and item['actor'] == seeded.module.ACTOR_LABEL


def test_history_export_csv_has_bom_range_and_all_rows(seeded):
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        db.executemany("INSERT INTO change_history (timestamp, type, date_key, details) VALUES (?, 'T', ?, ?)",
                       [(f'2031-0{m}-15 10:00:00', DAY_A, f'월{m}') for m in range(1, 7)])
    response = seeded.get('/api/duty/history-export?start=2031-02-01&end=2031-04-30')
    assert response.status_code == 200
    assert response.content.startswith('﻿'.encode('utf-8'))
    rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
    assert rows[0][:4] == ['시각', '구분', '대상일', '내용']
    assert [r[3] for r in rows[1:]] == ['월2', '월3', '월4']
    assert seeded.get('/api/duty/history-export?start=2031-05-01&end=2031-01-01').status_code == 400


# ── A-3 / A-4 / A-5 ────────────────────────────────────────────────

def _drop_history_extras(path):
    with sqlite3.connect(path) as db:
        db.execute('ALTER TABLE change_history DROP COLUMN actor')
        db.execute('ALTER TABLE change_history DROP COLUMN host')


# [위험] 구조 변경(ALTER TABLE, 자료 이전 UPDATE)이 그날 첫 백업보다 먼저 돌아, 변경 직전 상태가 남지 않았다.
def test_schema_change_takes_snapshot_of_the_old_structure_first(seeded):
    module = seeded.module
    _drop_history_extras(module.DB_PATH)
    assert module.pending_schema_migrations()
    module.init_db()
    folder = Path(module.backup_dir_path())
    snaps = sorted(folder.glob('premigration_*.db'))
    assert snaps, '구조를 바꾸기 전에 스냅샷이 있어야 한다'
    with sqlite3.connect(snaps[-1]) as db:
        cols = {r[1] for r in db.execute('PRAGMA table_info(change_history)')}
    assert 'actor' not in cols, '스냅샷은 바꾸기 전 구조여야 한다'
    with sqlite3.connect(module.DB_PATH) as db:
        assert 'actor' in {r[1] for r in db.execute('PRAGMA table_info(change_history)')}


# [위험] 안전 사본을 못 만들었는데도 구조를 바꾸면 되돌릴 근거가 없다.
def test_schema_change_is_refused_when_snapshot_fails(seeded, monkeypatch):
    module = seeded.module
    _drop_history_extras(module.DB_PATH)

    def boom(*a, **k):
        raise OSError('쓰기 불가')
    monkeypatch.setattr(module, '_snapshot_to', boom)
    with pytest.raises(RuntimeError):
        module.init_db()
    with sqlite3.connect(module.DB_PATH) as db:
        assert 'actor' not in {r[1] for r in db.execute('PRAGMA table_info(change_history)')}


# [위험] 프로그램이 켜질 때 하루 첫 백업이 구조 변경 뒤에 돌면, 그날 백업에도 바뀐 구조만 남는다.
def test_startup_daily_backup_holds_the_structure_before_migration(load_app):
    first = load_app('source')
    first.save(copy.deepcopy(SEED))
    _drop_history_extras(first.module.DB_PATH)
    second = load_app('source', source_db=first.module.DB_PATH, initialize=False)
    daily = sorted(Path(second.module.backup_dir_path()).glob('smart_duty_*.db'))
    assert daily, '시작할 때 하루 첫 백업이 있어야 한다'
    with sqlite3.connect(daily[-1]) as db:
        assert 'actor' not in {r[1] for r in db.execute('PRAGMA table_info(change_history)')}
    with sqlite3.connect(second.module.DB_PATH) as db:
        assert 'actor' in {r[1] for r in db.execute('PRAGMA table_info(change_history)')}


# [위험] 복사가 끊겨 반쪽이 된 파일이 오늘 백업 이름을 얻으면, 그날은 다시 백업하지 않는다.
def test_unverified_snapshot_never_becomes_todays_backup(seeded, monkeypatch):
    module = seeded.module
    folder = Path(module.backup_dir_path())
    for f in folder.glob('smart_duty_*.db'):
        f.unlink()
    module.BACKUP_STATE['verified'] = ''
    real = module._verify_sqlite_file
    monkeypatch.setattr(module, '_verify_sqlite_file',
                        lambda path: '반쪽 파일' if '.backup-' in os.path.basename(path) else real(path))
    module.ensure_daily_backup()
    assert not list(folder.glob('smart_duty_*.db'))
    assert module.BACKUP_STATE['ok'] is False
    assert not list(folder.glob('.backup-*')), '임시 파일은 남지 않아야 한다'


# [위험] 오늘 날짜 백업 파일이 '있기만 하면' 정상으로 보고해, 0바이트·반쪽 파일이 그날의 유일한 백업이었다.
def test_corrupt_daily_backup_is_detected_and_rebuilt(seeded):
    module = seeded.module
    module.ensure_daily_backup()
    folder = Path(module.backup_dir_path())
    daily = sorted(folder.glob('smart_duty_*.db'))[-1]
    daily.write_bytes(b'not a database')
    module.BACKUP_STATE['verified'] = ''
    module.ensure_daily_backup()
    assert module._verify_sqlite_file(str(daily)) == ''
    assert list(folder.glob(daily.name + '.broken-*'))
    assert module.BACKUP_STATE['ok'] is True


def test_backup_mirror_keeps_a_second_copy(load_app, tmp_path):
    mirror = tmp_path / 'mirror'
    app = load_app('source', config_text='[NETWORK]\nopen_browser=false\ntray_icon=false\n'
                   'admin_password=synthetic-test-password\nbind_host=127.0.0.1\n'
                   f'db_timeout_seconds=0.2\nbackup_mirror_dir={mirror}\n')
    app.save(copy.deepcopy(SEED))
    copies = list(mirror.glob('smart_duty_*.db'))
    assert copies and app.module._verify_sqlite_file(str(copies[0])) == ''


# [위험] 백업만 쌓이고 되돌리는 방법이 파일 복사뿐이었다. 복원 자체도 되돌릴 수 있어야 한다.
def test_restore_brings_back_backup_and_keeps_prerestore_copy(seeded):
    module = seeded.module
    seeded.save({'globalMemo': '백업 시점'})
    module.BACKUP_STATE['verified'] = ''
    folder = Path(module.backup_dir_path())
    for f in folder.glob('smart_duty_*.db'):
        f.unlink()
    module.ensure_daily_backup()
    name = sorted(folder.glob('smart_duty_*.db'))[-1].name
    seeded.save({'globalMemo': '복원으로 사라질 값', 'historyLog': {'timestamp': '2030-02-01 00:00:00', 'type': '변경',
                                                                    'dateKey': DAY_A, 'details': '복원 전 마지막 변경'}})
    before = seeded.data()

    preview = seeded.get(f'/api/system/backup-preview?name={name}', headers=AUTH).json()
    assert preview['ok'] and preview['backup']['counts']['members'] == len(SEED['members'])

    assert seeded.post('/api/system/restore', {'name': name, 'baseVersion': before['dataVersion'] - 1}).status_code == 409
    response = seeded.post('/api/system/restore', {'name': name, 'baseVersion': before['dataVersion']})
    assert response.status_code == 200, response.text
    after = seeded.data()
    assert after['globalMemo'] == '백업 시점'
    assert after['dataVersion'] > before['dataVersion'], '다른 PC 화면이 따라오려면 버전이 커야 한다'
    safety = folder / response.json()['safetyCopy']
    assert safety.name.startswith('prerestore_') and safety.is_file()
    with sqlite3.connect(safety) as db:
        memo = db.execute("SELECT value FROM app_settings WHERE key = 'global_memo'").fetchone()[0]
    assert memo == '복원으로 사라질 값', '복원 직전 상태가 그대로 보관되어야 복원도 되돌릴 수 있다'
    rows = history_rows(seeded)
    assert any(r['type'] == '백업 복원' for r in rows)
    # [위험] 이력도 DB 안에 있어 복원하면 백업 이후의 이력이 사라졌다. 이력은 복원을 넘어 이어져야 한다.
    assert [r for r in rows if r['details'] == '복원 전 마지막 변경'], '백업 이후의 이력이 복원 뒤에도 남아야 한다'


@pytest.mark.parametrize('name', ['../smart_duty.db', 'smart_duty.db', 'smart_duty_2030-01-01.db/../x', 'x.db'])
def test_restore_rejects_paths_outside_backup_folder(seeded, name):
    version = seeded.data()['dataVersion']
    assert seeded.post('/api/system/restore', {'name': name, 'baseVersion': version}).status_code in (400, 404)


# [위험] 이름 검사가 느슨하면 backups 폴더에 놓인 아무 DB 파일(또는 하위 폴더 경로)로 덮어쓸 수 있다.
def test_restore_only_accepts_known_backup_names(seeded):
    module = seeded.module
    module.ensure_daily_backup()
    folder = Path(module.backup_dir_path())
    daily = sorted(folder.glob('smart_duty_*.db'))[-1]
    import shutil
    shutil.copyfile(daily, folder / 'other.db')
    (folder / 'sub').mkdir()
    shutil.copyfile(daily, folder / 'sub' / daily.name)
    version = seeded.data()['dataVersion']
    for name in ('other.db', f'sub/{daily.name}', f'sub\\{daily.name}'):
        assert seeded.post('/api/system/restore', {'name': name, 'baseVersion': version}).status_code == 400, name
    assert seeded.data()['dataVersion'] == version


def test_backup_and_restore_need_admin(seeded):
    assert seeded.client.get('/api/system/backups').status_code == 401
    assert seeded.client.post('/api/system/restore', json={'name': 'x', 'baseVersion': 1}).status_code == 401


# ── A-6 ────────────────────────────────────────────────────────────

# [위험] TRUNCATE 는 연결별 설정이라 init_db 연결에서만 걸려 있었고, 실제 저장 연결은 DELETE 로 돌았다.
#   db_timeout_seconds 는 바로 다음 줄의 busy_timeout=30000 이 덮어써 효과가 없었다.
def test_write_connections_use_truncate_journal_and_configured_timeout(seeded):
    conn = seeded.module.get_db_connection(readonly=False)
    try:
        assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'truncate'
        assert conn.execute('PRAGMA busy_timeout').fetchone()[0] == 200   # 시험 설정 0.2초
    finally:
        conn.close()


def test_friendly_error_tells_next_action():
    from test_backend_regression import SOURCE  # noqa
    import importlib  # noqa
    text = 'database is locked'
    ns = {}
    src = SOURCE.read_text(encoding='utf-8')
    start = src.index('def friendly_db_error(')
    end = src.index('\n\n\n', start)
    exec(src[start:end], ns)
    assert '다른 PC가 저장 중' in ns['friendly_db_error'](Exception(text), '저장')
    assert '권한' in ns['friendly_db_error'](Exception('attempt to write a readonly database'))
    # v3.18.0 원문(영어 예외)은 화면 문장에 붙이지 않고 로그에만 남긴다.
    io_text = ns['friendly_db_error'](Exception('disk I/O error'))
    assert '공유 폴더 연결이 불안정' in io_text and 'disk I/O error' not in io_text and '상세' not in io_text


def test_log_file_is_separate_per_mode(load_app):
    admin = load_app('source', mode='ADMIN')
    viewer = load_app('source', mode='VIEWER', initialize=False)
    assert Path(admin.module.LOG_PATH).name == 'smart_duty_admin.log'
    assert Path(viewer.module.LOG_PATH).name == 'smart_duty_viewer.log'


# ── B-3 월 마감 ───────────────────────────────────────────────────

def close_month(app, month, closed=True, reason='', code=200):
    version = app.data()['dataVersion']
    response = app.post('/api/duty/month-close', {'month': month, 'closed': closed, 'reason': reason, 'baseVersion': version})
    assert response.status_code == code, response.text
    return response


# [위험] 마감 개념이 없어 지난달 당직표를 소급 수정할 수 있었다. 마감은 선택 기능이고 기본은 아무것도 막지 않는다.
def test_closed_month_blocks_changes_but_allows_identical_full_saves(seeded):
    before = seeded.data()
    assert before['closedMonths'] == []
    close_month(seeded, '2030-06', reason='6월 확정')
    data = seeded.data()
    assert data['closedMonths'] == ['2030-06']
    # 화면은 늘 전체 지도를 보낸다. 마감된 달이 그대로면 저장되어야 한다.
    seeded.save({'schedules': data['schedules'], 'noDuty': data['noDuty'], 'comments': data['comments'],
                 'globalMemo': '다른 것만 바꿈'})
    changed = copy.deepcopy(seeded.data()['schedules'])
    changed[DAY_A] = [{'name': 'C', 'role': '당직'}]
    seeded.save({'schedules': changed}, 423)
    seeded.save({'comments': {**data['comments'], DAY_B: '마감 뒤 메모'}}, 423)
    seeded.save({'noDuty': {}}, 423)
    other = copy.deepcopy(seeded.data()['schedules'])
    other['2030-07-01'] = [{'name': 'C', 'role': '당직'}]
    seeded.save({'schedules': other})


def test_closed_month_blocks_requests_and_approvals(seeded):
    pending = seeded.submit('SUBSTITUTE')
    assert pending.status_code == 200
    close_month(seeded, '2030-06')
    assert seeded.submit('ABSENCE').status_code == 423
    request_id = pending.json()['requestId']
    assert seeded.action(request_id).status_code == 423
    assert seeded.action(request_id, 'REJECT', reject_reason='마감').status_code == 200   # 반려는 된다


def test_reopening_needs_a_reason_and_is_recorded(seeded):
    close_month(seeded, '2030-06', reason='확정')
    close_month(seeded, '2030-06', closed=False, reason='', code=400)
    close_month(seeded, '2030-06', closed=False, reason='수당 정정')
    kinds = [(r['type'], r['details']) for r in history_rows(seeded)]
    assert ('월 마감', '2030-06 마감 (사유: 확정)') in kinds
    assert ('월 마감 해제', '2030-06 마감 해제 (사유: 수당 정정)') in kinds
    assert seeded.client.post('/api/duty/month-close', json={'month': '2030-06', 'closed': True, 'baseVersion': 0}).status_code == 401


# ── 신청 흐름 ──────────────────────────────────────────────────────

# [위험] 사전 판정이 예외를 삼키고 '' 를 돌려줘, 판정이 실패해도 '승인 가능' 으로 보였다.
def test_approval_blocker_failure_is_not_reported_as_ok(seeded, monkeypatch):
    assert seeded.submit('SUBSTITUTE').status_code == 200

    def boom(*a, **k):
        raise sqlite3.OperationalError('disk I/O error')
    monkeypatch.setattr(seeded.module, 'is_absent_on', boom)
    items = seeded.get('/api/duty/requests', headers=AUTH).json()['requests']
    assert items and items[0]['blocker'], '판정 실패는 막는 사유로 보여야 한다'


# [위험] 맞교환·대체에는 중복 검사가 없어 같은 신청이 수십 건 쌓일 수 있었다.
@pytest.mark.parametrize('kind', ['SWAP', 'SUBSTITUTE'])
def test_duplicate_pending_swap_or_substitute_is_refused(seeded, kind):
    first = seeded.submit(kind)
    assert first.status_code == 200
    assert seeded.submit(kind).status_code == 409
    assert seeded.action(first.json()['requestId'], 'REJECT').status_code == 200
    assert seeded.submit(kind).status_code == 200


# [위험] 0점 처리 여부를 신청자가 정했고, 관리자가 바로잡으려면 반려 후 재신청밖에 없었다.
def test_admin_can_override_zero_score_on_substitute_approval(seeded):
    request_id = seeded.submit('SUBSTITUTE', is_zero_score=True).json()['requestId']
    assert seeded.action(request_id, zero_score=False).status_code == 200
    row = [r for r in seeded.rows('schedules') if r['date_key'] == DAY_A][0]
    assert row['worker_name'] == 'C' and row['zero_score'] == 0
    assert '관리자 변경' in history_rows(seeded)[-1]['details']


def test_substitute_approval_keeps_requested_zero_score_when_not_overridden(seeded):
    request_id = seeded.submit('SUBSTITUTE', is_zero_score=True).json()['requestId']
    assert seeded.action(request_id).status_code == 200
    row = [r for r in seeded.rows('schedules') if r['date_key'] == DAY_A][0]
    assert row['zero_score'] == 1


def test_request_records_pc_and_account_and_approval_records_actor(seeded):
    request_id = seeded.submit('SUBSTITUTE').json()['requestId']
    row = [r for r in seeded.rows('change_requests') if r['id'] == request_id][0]
    assert row['requester_host'] == seeded.module.HOST_LABEL
    assert row['requester_account'] == seeded.module.ACTOR_LABEL
    seeded.action(request_id)
    last = history_rows(seeded)[-1]
    assert last['type'] == '대체근무(승인)' and last['actor'] == seeded.module.ACTOR_LABEL


# [위험] 반려 사유가 브라우저 저장소의 접수 토큰으로만 보여, 다른 PC·브라우저에서는 영구히 확인할 수 없었다.
def test_my_requests_shows_result_and_reject_reason_without_token(seeded):
    request_id = seeded.submit('SUBSTITUTE').json()['requestId']
    seeded.action(request_id, 'REJECT', reject_reason='인원 부족')
    items = seeded.get('/api/duty/my-requests?name=A').json()['requests']
    assert items[0]['id'] == request_id
    assert items[0]['status'] == 'REJECTED' and items[0]['reject_reason'] == '인원 부족'
    assert 'receipt_token' not in items[0]
    assert seeded.get('/api/duty/my-requests?name=').json()['requests'] == []


# ── D-2 / D-3 ──────────────────────────────────────────────────────

# [위험] 어느 PC 가 몇 번 버전으로 도는지 관리자가 알 방법이 없었다.
def test_heartbeat_records_this_pc_and_flags_outdated_ones(seeded):
    module = seeded.module
    module.record_client_heartbeat(force=True)
    with sqlite3.connect(module.DB_PATH) as db:
        db.execute("INSERT INTO client_status (host, role, app_version, last_seen) VALUES ('OLD-PC', 'VIEWER', '3.14.5', '2030-01-01 00:00:00')")
    data = seeded.get('/api/system/clients', headers=AUTH).json()
    hosts = {c['host']: c for c in data['clients']}
    assert hosts[module.HOST_LABEL]['app_version'] == module.APP_VERSION
    assert hosts['OLD-PC']['outdated'] is True
    assert hosts[module.HOST_LABEL]['outdated'] is False
    version = seeded.data()['dataVersion']
    module.record_client_heartbeat(force=True)
    assert seeded.data()['dataVersion'] == version, '하트비트가 다른 PC 화면을 새로고침시키면 안 된다'


def test_heartbeat_is_throttled(seeded):
    module = seeded.module
    module.record_client_heartbeat(force=True)
    with sqlite3.connect(module.DB_PATH) as db:
        db.execute("UPDATE client_status SET last_seen = '2000-01-01 00:00:00'")
    module.record_client_heartbeat()
    with sqlite3.connect(module.DB_PATH) as db:
        assert db.execute('SELECT last_seen FROM client_status').fetchone()[0] == '2000-01-01 00:00:00'


def test_diagnostics_reports_leftover_journal(seeded):
    Path(seeded.module.DB_PATH + '-journal').write_bytes(b'x' * 10)
    out = seeded.get('/api/system/diagnostics', headers=AUTH).json()
    assert out['journal']['exists'] and out['journal']['hint']


# ── C-3 ────────────────────────────────────────────────────────────

# [위험] 암호 없이 사내망에 열면 누구나 진단·경로 확인으로 임의 폴더의 존재·쓰기 가능 여부를 탐색할 수 있었다.
def test_exposed_without_password_limits_diagnostics_to_this_pc(load_app):
    app = load_app('source', config_text='[NETWORK]\nopen_browser=false\ntray_icon=false\n'
                   'admin_password=\nbind_host=0.0.0.0\ndb_timeout_seconds=0.2\n')
    remote = TestClient(app.module.app, client=('10.1.2.3', 50000))
    local = TestClient(app.module.app, client=('127.0.0.1', 50000))
    for url in ('/api/system/diagnostics', '/api/system/clients'):
        assert remote.get(url).status_code == 403, url
        assert local.get(url).status_code == 200, url
    # v3.17.13 check-path 는 부수 효과(임의 경로 접속·시험 파일 쓰기)가 있어 POST 로만 받는다.
    assert remote.post('/api/system/check-path', json={'path': '.'}).status_code == 403
    assert local.post('/api/system/check-path', json={'path': '.'}).status_code == 200
    assert local.get('/api/system/info').json()['exposedWithoutPassword'] is True


def test_local_only_without_password_keeps_old_behavior(load_app):
    app = load_app('source', config_text='[NETWORK]\nopen_browser=false\ntray_icon=false\n'
                   'admin_password=\nbind_host=127.0.0.1\ndb_timeout_seconds=0.2\n')
    remote = TestClient(app.module.app, client=('10.1.2.3', 50000))
    assert remote.get('/api/system/diagnostics').status_code == 200
    assert remote.get('/api/system/info').json()['exposedWithoutPassword'] is False
