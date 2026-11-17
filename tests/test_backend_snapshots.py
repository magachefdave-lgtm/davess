"""v3.18.0 '방금 작업 되돌리기' 자동 사본(backups/auto_<kind>_<timestamp>.db) 시험.

실제 API 와 임시 DB 만 쓴다. 운영 DB 는 읽거나 쓰지 않는다.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
import re
import sqlite3
import time

import pytest

from test_backend_regression import AUTH, DAY_A, DAY_B, DAY_C, SEED, load_app  # noqa: F401


@pytest.fixture
def seeded(load_app):
    app = load_app('source')
    app.save(copy.deepcopy(SEED))
    return app


def backups_dir(app):
    return Path(app.module.backup_dir_path())


def auto_files(app):
    folder = backups_dir(app)
    return sorted(p.name for p in folder.iterdir() if p.name.startswith('auto_')) if folder.is_dir() else []


def comment_in(path, day=DAY_A):
    with sqlite3.connect(path) as db:
        row = db.execute('SELECT comment FROM day_meta WHERE date_key = ?', (day,)).fetchone()
    return row[0] if row else None


def test_save_with_snapshot_kind_creates_verified_labeled_copy_of_state_before_save(seeded):
    response = seeded.save({'comments': {DAY_A: '자동 배정 뒤 메모'}, 'snapshotKind': 'assign'})
    body = response.json()
    assert re.fullmatch(r'auto_assign_\d{8}-\d{6}-\d{3}\.db', body['snapshot'])
    assert body['snapshotLabel'].startswith('자동 배정 직전 (') and re.search(r'\(\d{2}:\d{2}\)$', body['snapshotLabel'])
    path = backups_dir(seeded) / body['snapshot']
    assert seeded.module._verify_sqlite_file(str(path)) == ''
    assert comment_in(path) == '보존 메모', '사본은 저장 직전 상태여야 한다'
    assert seeded.data()['comments'][DAY_A] == '자동 배정 뒤 메모'
    listed = seeded.get('/api/system/backups', headers=AUTH).json()['backups']
    entry = next(item for item in listed if item['name'] == body['snapshot'])
    assert entry['kind'] == 'auto' and entry['autoKind'] == 'assign' and entry['label'] == body['snapshotLabel']


def test_plain_save_makes_no_auto_snapshot(seeded):
    seeded.save({'comments': {DAY_A: '평소 저장'}})
    assert auto_files(seeded) == []


def test_unknown_snapshot_kind_is_refused_and_nothing_changes(seeded):
    before = seeded.data()
    response = seeded.post('/api/duty/save-all', {'comments': {DAY_A: 'x'}, 'snapshotKind': '../evil',
                                                  'baseVersion': before['dataVersion']})
    assert response.status_code == 400
    assert seeded.data()['comments'] == before['comments']
    assert auto_files(seeded) == []


def test_snapshot_failure_aborts_the_save(seeded, monkeypatch):
    before = seeded.data()

    def broken(kind):
        raise OSError('disk full')
    monkeypatch.setattr(seeded.module, 'take_auto_snapshot', broken)
    response = seeded.post('/api/duty/save-all', {'comments': {DAY_A: '사라지면 안 됨'}, 'snapshotKind': 'import',
                                                  'baseVersion': before['dataVersion']})
    assert response.status_code == 500
    assert '바뀌지 않았습니다' in response.json()['detail']
    after = seeded.data()
    assert after['comments'] == before['comments'] and after['dataVersion'] == before['dataVersion']


def test_conflicting_save_makes_no_snapshot(seeded):
    response = seeded.post('/api/duty/save-all', {'comments': {DAY_A: 'x'}, 'snapshotKind': 'assign', 'baseVersion': 0})
    assert response.status_code == 409
    assert auto_files(seeded) == []


def test_snapshot_endpoint_requires_admin_and_known_kind(seeded):
    assert seeded.post('/api/system/snapshot', {'kind': 'bulk'}, auth=False).status_code == 401
    assert seeded.post('/api/system/snapshot', {'kind': 'nope'}).status_code == 400
    response = seeded.post('/api/system/snapshot', {'kind': 'bulk'})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['name'].startswith('auto_bulk_') and body['label'].startswith('일괄 작업 직전 (')
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', body['modified'])


def test_viewer_cannot_create_snapshot(load_app):
    viewer = load_app('source', mode='VIEWER')
    assert viewer.post('/api/system/snapshot', {'kind': 'bulk'}).status_code == 403


def test_auto_snapshots_are_pruned_to_newest_twenty_by_mtime(seeded):
    module = seeded.module
    folder = backups_dir(seeded)
    folder.mkdir(parents=True, exist_ok=True)
    # 이름순으로는 가장 뒤지만 수정 시각은 가장 오래된 사본: 이름이 아니라 시각으로 지워져야 한다.
    old_but_late_name = folder / 'auto_member_29991231-235959-999.db'
    seed_copy = Path(module.take_auto_snapshot('manual'))
    seed_copy.rename(old_but_late_name)
    os.utime(old_but_late_name, (time.time() - 10000, time.time() - 10000))
    # 다른 종류의 사본은 자동 사본 정리 대상이 아니다.
    prerestore = Path(module.take_named_snapshot('prerestore', 'keep'))
    os.utime(prerestore, (time.time() - 20000, time.time() - 20000))
    created = []
    for index in range(module.AUTO_SNAPSHOT_KEEP + 3):
        path = Path(module.take_auto_snapshot('assign' if index % 2 else 'approve'))
        os.utime(path, (time.time() - 5000 + index * 10, time.time() - 5000 + index * 10))
        created.append(path.name)
    remaining = auto_files(seeded)
    assert len(remaining) == module.AUTO_SNAPSHOT_KEEP
    assert old_but_late_name.name not in remaining
    assert set(created[-module.AUTO_SNAPSHOT_KEEP + 1:]) <= set(remaining)
    assert prerestore.exists()


def test_newest_snapshot_survives_pruning_even_with_old_mtime(seeded, monkeypatch):
    module = seeded.module
    for _ in range(module.AUTO_SNAPSHOT_KEEP):
        module.take_auto_snapshot('bulk')
    real_snapshot = module._snapshot_to

    def stale(target):
        real_snapshot(target)
        os.utime(target, (1, 1))   # 시계가 뒤로 간 PC: 새 사본이 가장 오래된 것처럼 보인다
    monkeypatch.setattr(module, '_snapshot_to', stale)
    newest = Path(module.take_auto_snapshot('assign'))
    assert newest.exists()
    # 방금 만든 사본은 보호되므로 이 경우에만 한 개가 더 남는다(그 다음 사본에서 정리된다).
    assert len(auto_files(seeded)) == module.AUTO_SNAPSHOT_KEEP + 1


def test_same_second_snapshots_get_distinct_names(seeded):
    names = {Path(seeded.module.take_auto_snapshot('bulk')).name for _ in range(5)}
    assert len(names) == 5


def test_labels_for_every_backup_kind(seeded):
    module = seeded.module
    from datetime import datetime
    today = datetime.now().strftime('%Y%m%d')
    assert module.snapshot_label(f'auto_assign_{today}-143205-001.db') == '자동 배정 직전 (14:32)'
    assert module.snapshot_label('auto_approve_20200105-091500-000.db') == '신청 승인 직전 (1/5 09:15)'
    assert module.snapshot_label('auto_import_20200105-091500.db').startswith('백업 파일 불러오기 직전')
    assert module.snapshot_label('smart_duty_2020-01-01.db', datetime(2020, 1, 1, 8, 1)) == '하루 첫 저장 전 자동 백업 (1/1 08:01)'
    assert module.snapshot_label('prerestore_v3_20200101-000000.db', datetime(2020, 1, 1, 0, 0)).startswith('복원 직전')


def test_restoring_auto_snapshot_brings_back_state_and_keeps_prerestore(seeded):
    body = seeded.save({'comments': {DAY_A: '배정 후'}, 'schedules': {DAY_A: [{'name': 'C', 'role': '당직'}]},
                        'snapshotKind': 'assign', 'historyLog': {'type': '자동 배정', 'details': '시험'}}).json()
    preview = seeded.get('/api/system/backup-preview?name=' + body['snapshot'], headers=AUTH).json()
    assert preview['ok'] is True and preview['backup']['counts']['schedules'] == 3
    version = seeded.data()['dataVersion']
    response = seeded.post('/api/system/restore', {'name': body['snapshot'], 'baseVersion': version})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['restoredLabel'].startswith('자동 배정 직전')
    assert result['safetyLabel'].startswith('복원 직전')
    data = seeded.data()
    assert data['comments'][DAY_A] == '보존 메모'
    assert data['schedules'][DAY_A][0]['name'] == 'A'
    assert data['dataVersion'] > version
    assert (backups_dir(seeded) / result['safetyCopy']).exists()
    history = data['history'][0]
    assert history['type'] == '백업 복원' and '자동 배정 직전' in history['details']


def test_stale_undo_is_refused_with_409(seeded):
    body = seeded.save({'comments': {DAY_A: '배정 후'}, 'snapshotKind': 'assign'}).json()
    stale = seeded.data()['dataVersion']
    seeded.save({'comments': {DAY_A: '그 뒤 다른 변경'}})
    response = seeded.post('/api/system/restore', {'name': body['snapshot'], 'baseVersion': stale})
    assert response.status_code == 409
    assert seeded.data()['comments'][DAY_A] == '그 뒤 다른 변경'


def test_approval_takes_snapshot_before_applying(seeded):
    request_id = seeded.submit('SWAP').json()['requestId']
    response = seeded.action(request_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['snapshot'].startswith('auto_approve_') and body['snapshotLabel'].startswith('신청 승인 직전')
    path = backups_dir(seeded) / body['snapshot']
    with sqlite3.connect(path) as db:
        names = dict(db.execute('SELECT date_key, worker_name FROM schedules').fetchall())
    assert names[DAY_A] == 'A' and names[DAY_B] == 'B', '사본은 승인 전 배정이어야 한다'
    current = seeded.data()['schedules']
    assert current[DAY_A][0]['name'] == 'B'


def test_reject_makes_no_snapshot(seeded):
    request_id = seeded.submit('SWAP').json()['requestId']
    assert seeded.action(request_id, 'REJECT').status_code == 200
    assert auto_files(seeded) == []


def test_approval_continues_when_snapshot_fails(seeded, monkeypatch):
    request_id = seeded.submit('SWAP').json()['requestId']

    def broken(kind):
        raise OSError('read-only share')
    monkeypatch.setattr(seeded.module, 'take_auto_snapshot', broken)
    response = seeded.action(request_id)
    assert response.status_code == 200, response.text
    assert response.json()['snapshot'] == ''
    assert seeded.data()['schedules'][DAY_A][0]['name'] == 'B'


def test_backup_list_is_newest_first_by_time_and_names_are_validated(seeded):
    module = seeded.module
    first = Path(module.take_auto_snapshot('assign'))
    os.utime(first, (time.time() - 100, time.time() - 100))
    second = Path(module.take_named_snapshot('prerestore', 'v1'))
    listed = [item['name'] for item in seeded.get('/api/system/backups', headers=AUTH).json()['backups']]
    assert listed.index(second.name) < listed.index(first.name)
    assert seeded.get('/api/system/backup-preview?name=auto_../x.db', headers=AUTH).status_code == 400
