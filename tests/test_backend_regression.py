"""Synthetic API regression checks for restored WLR OnAir.

Run: python -m pytest tests/test_backend_regression.py -q
Bundled original reference is used automatically on Python 3.13.
Override: ONAIR_REFERENCE_MARSHAL=/path/to/extracted/main python -m pytest ...
Never reads or writes an operational DB; all imports and databases are isolated.
"""
from __future__ import annotations

import copy
import marshal
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import types
import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

SOURCE = Path(__file__).resolve().parents[1] / 'backend' / 'main.py'
_BUNDLED_REFERENCE = SOURCE.parents[1] / 'recovery' / 'original_main.marshal'
REFERENCE = os.environ.get('ONAIR_REFERENCE_MARSHAL', str(_BUNDLED_REFERENCE) if _BUNDLED_REFERENCE.is_file() else '')
REFERENCE_USABLE = bool(REFERENCE) and sys.version_info[:2] == (3, 13)
KINDS = ['source'] + (['reference'] if REFERENCE_USABLE else [])
AUTH = {'X-Admin-Token': 'synthetic-test-password'}
DAY_A, DAY_B, DAY_C = '2030-06-17', '2030-06-18', '2030-06-20'
SEED = {
    'members': [{'id': 1, 'name': 'A', 'gender': 'M'},
                {'id': 2, 'name': 'B', 'gender': 'F'},
                {'id': 3, 'name': 'C', 'gender': 'M'}],
    'schedules': {DAY_A: [{'name': 'A', 'role': '당직'}],
                  DAY_B: [{'name': 'B', 'role': '당직'}],
                  DAY_C: [{'name': 'A', 'role': '당직'}]},
    'comments': {DAY_A: '보존 메모'},
    'noDuty': {'2030-06-14': 'FAMILY_DAY'},
    'absences': [], 'absenceTypes': ['연차', '파견'],
    'customHolidays': {'2030-06-19': {'name': '테스트 휴일', 'isMajor': False, 'isRemoved': True}},
    'rules': {'sentinel': ['원본 규칙'], 'nested': {'keep': True}},
    'globalMemo': '보존 공지',
    'historyBulk': [{'timestamp': '2030-01-01 00:00:00', 'type': 'TEST',
                     'dateKey': DAY_A, 'details': '초기 이력'}],
}


class App:
    def __init__(self, module):
        self.module = module
        self.client = TestClient(module.app)

    def get(self, url, **kwargs):
        return self.client.get(url, **kwargs)

    def post(self, url, payload, auth=True):
        return self.client.post(url, json=payload, headers=AUTH if auth else {})

    def data(self):
        response = self.get('/api/duty/all-data')
        assert response.status_code == 200, response.text
        return response.json()

    def save(self, data, code=200):
        # v3.16.0 서버가 baseVersion 없는 저장을 거부하므로(428), 시험 도우미는 화면처럼
        #   현재 버전을 붙여 보낸다. 버전을 일부러 비우는 시험은 post() 를 직접 쓴다.
        if 'baseVersion' not in data:
            data = {**data, 'baseVersion': self.data()['dataVersion']}
        response = self.post('/api/duty/save-all', data)
        assert response.status_code == code, response.text
        return response

    def submit(self, kind, **changes):
        payload = {'requester_name': 'A', 'target_date': DAY_A,
                   'request_type': kind, 'reason': '합성 회귀 시험'}
        if kind == 'SWAP':
            payload.update(swap_date=DAY_B, swap_worker='B')
        elif kind == 'SUBSTITUTE':
            payload.update(substitute_name='C', is_zero_score=True)
        elif kind == 'ABSENCE':
            payload.update(absence_type='연차', absence_end_date=DAY_C)
        payload.update(changes)
        return self.post('/api/duty/request-change', payload, auth=False)

    def action(self, request_id, action='APPROVE', **kw):
        return self.post('/api/duty/request/action', dict(request_id=request_id, action=action, **kw))

    def rows(self, table):
        assert table in {'change_requests', 'app_settings', 'change_history', 'schedules'}
        with sqlite3.connect(self.module.DB_PATH) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]


@pytest.fixture
def load_app(tmp_path, monkeypatch):
    loaded = []

    def load(kind='source', mode='ADMIN', initialize=True, source_db=None,
             config_text=None, config_encoding='utf-8', legacy=False, frozen_name=None):
        root = tmp_path / uuid.uuid4().hex
        backend = root / 'backend'
        backend.mkdir(parents=True)
        frontend = root / 'frontend' / 'dist'
        frontend.mkdir(parents=True)
        (frontend / 'index.html').write_text('<!doctype html><title>Test</title>', encoding='utf-8')
        config_name = 'config.ini' if legacy else f'config_{mode.lower()}.ini'
        (backend / config_name).write_text(
            config_text if config_text is not None else
            '[NETWORK]\nopen_browser=false\ntray_icon=false\n'
            'admin_password=synthetic-test-password\nbind_host=127.0.0.1\n'
            'db_timeout_seconds=0.2\n', encoding=config_encoding)
        if source_db:
            shutil.copy2(source_db, backend / 'smart_duty.db')
        monkeypatch.setenv('SMART_DUTY_MODE', mode)
        if frozen_name:
            monkeypatch.setattr(sys, 'frozen', True, raising=False)
            monkeypatch.setattr(sys, '_MEIPASS', str(root), raising=False)
            monkeypatch.setattr(sys, 'executable', str(backend / frozen_name))
        name = 'onair_test_' + uuid.uuid4().hex
        module = types.ModuleType(name)
        module.__file__ = str(backend / 'main.py')
        sys.modules[name] = module
        loaded.append(module)
        if kind == 'reference':
            assert sys.version_info[:2] == (3, 13), 'Reference marshal requires Python 3.13'
            code = marshal.loads(Path(REFERENCE).read_bytes())
        else:
            code = compile(SOURCE.read_text(encoding='utf-8-sig'), str(SOURCE), 'exec')
        exec(code, module.__dict__)
        if initialize:
            module.init_db()
        return App(module)

    yield load
    for module in loaded:
        sys.modules.pop(module.__name__, None)


@pytest.fixture(params=KINDS)
def app(request, load_app):
    return load_app(request.param)


@pytest.fixture
def seeded(app):
    app.save(copy.deepcopy(SEED))
    return app


def test_full_save_roundtrip_and_version(seeded, request):
    data = seeded.data()
    for key in ('members', 'comments', 'noDuty', 'absences', 'absenceTypes', 'customHolidays', 'rules', 'globalMemo'):
        assert data[key] == SEED[key]
    assert data['dataVersion'] == 1
    assert data['schedules'][DAY_A][0]['name'] == 'A'
    # v3.16.0 빈 DB 로 가져온 이력은 서버가 직접 기록한 것과 구분되도록 표시가 붙는다.
    if request.node.callspec.params['app'] == 'source':
        assert data['history'][0]['type'] == '데이터 가져오기'
        assert data['history'][0]['before']['schedules'][DAY_A] == []
        assert data['history'][0]['after']['schedules'][DAY_A][0]['name'] == 'A'
        imported = data['history'][1]
        assert len(data['history']) == 2
    else:  # 기존 marshal 참조 서버는 구조화 전후 이력을 지원하지 않는다.
        imported = data['history'][0]
    assert imported['details'] == '[가져온 이력] 초기 이력'
    assert 'before' not in imported and 'after' not in imported


def test_absence_period_edit_persists_identity_and_atomic_schedule_change(seeded):
    record = {'id': 71, 'name': 'A', 'type': '파견', 'start': DAY_A, 'end': DAY_A}
    seeded.save({'absences': [record]})
    before = seeded.data()
    payload = {'baseVersion': before['dataVersion'],
               'absences': [{**record, 'end': DAY_C}],
               'schedules': {DAY_A: before['schedules'][DAY_A], DAY_B: before['schedules'][DAY_B]},
               'historyLog': {'timestamp': '2030-06-01 10:00:00', 'type': '부재 기간·구분 수정',
                              'dateKey': DAY_C, 'details': 'A: 종료일 17일 → 20일; 당직 해제 1일'}}
    seeded.save(payload)
    after = seeded.data()
    assert after['absences'] == [{**record, 'end': DAY_C}]
    assert DAY_C not in after['schedules']
    assert after['schedules'][DAY_A] == before['schedules'][DAY_A]
    assert after['schedules'][DAY_B] == before['schedules'][DAY_B]
    for key in ('comments', 'noDuty', 'members', 'rules', 'customHolidays', 'globalMemo'):
        assert after[key] == before[key]
    assert after['history'][0]['details'] == payload['historyLog']['details']
    # A stale PC cannot restore its old period or old duty assignments.
    seeded.save({**payload, 'absences': [record], 'schedules': before['schedules']}, code=409)
    assert seeded.data() == after
    # A failed transaction must also roll back the schedule delete/insert phase.
    seeded.save({'baseVersion': after['dataVersion'], 'schedules': before['schedules'],
                 'absences': [{'id': 71, 'name': None, 'type': '파견', 'start': DAY_A, 'end': DAY_A}]}, code=500)
    assert seeded.data() == after


def test_partial_save_preserves_omitted_collections(seeded):
    before = seeded.data()
    seeded.save({'comments': {DAY_B: '새 메모'}, 'baseVersion': before['dataVersion']})
    after = seeded.data()
    assert after['comments'] == {DAY_B: '새 메모'}
    for key in before.keys() - {'comments', 'dataVersion', 'history'}:
        assert after[key] == before[key], key
    assert after['dataVersion'] == before['dataVersion'] + 1
    # v3.17.13 화면이 이력을 보내지 않아도 서버가 바뀐 항목을 한 줄 남긴다. 기존 이력은 그대로다.
    assert after['history'][1:] == before['history']
    assert after['history'][0]['type'] == '설정 변경' and '메모' in after['history'][0]['details']


def test_explicit_empty_collection_deletes_only_requested_data(seeded):
    before = seeded.data()
    seeded.save({'absences': [], 'comments': {}, 'globalMemo': '', 'baseVersion': before['dataVersion']})
    after = seeded.data()
    assert after['comments'] == {} and after['globalMemo'] == ''
    assert after['noDuty'] == before['noDuty']
    assert after['members'] == before['members']
    assert after['schedules'] == before['schedules']


def test_no_duty_clear_preserves_comments(seeded):
    seeded.save({'noDuty': {}})
    assert seeded.data()['noDuty'] == {}
    assert seeded.data()['comments'] == SEED['comments']


def test_family_day_memo_and_existing_no_duty_roundtrip_with_conflict_guard(seeded):
    before = seeded.data()
    comments = {**before['comments'], '2030-06-14': '기존 메모\n패밀리데이',
                '2031-12-19': '미래 메모 보존'}
    payload = {'schedules': before['schedules'], 'noDuty': before['noDuty'],
               'comments': comments, 'baseVersion': before['dataVersion']}
    seeded.save(payload)
    saved = seeded.data()
    assert saved['comments'] == comments
    assert saved['noDuty'] == before['noDuty']
    assert saved['schedules'] == before['schedules']
    for key in ('members', 'absences', 'customHolidays', 'rules', 'globalMemo'):
        assert saved[key] == before[key]
    seeded.save({**payload, 'comments': {}, 'noDuty': {}}, 409)
    assert seeded.data() == saved


def test_optimistic_conflict_does_not_overwrite_or_lock(seeded):
    before = seeded.data()
    seeded.save({'globalMemo': '최신 공지', 'baseVersion': before['dataVersion']})
    latest = seeded.data()
    seeded.save({'globalMemo': '오래된 공지', 'members': [], 'baseVersion': before['dataVersion']}, 409)
    assert seeded.data() == latest
    seeded.save({'globalMemo': '충돌 후 정상 저장', 'baseVersion': latest['dataVersion']})


def test_duplicate_members_rollback_every_collection(seeded):
    before = seeded.data()
    seeded.save({'members': [{'id': 10, 'name': '중복', 'gender': 'M'},
                             {'id': 11, 'name': '중복', 'gender': 'F'}],
                 'schedules': {}, 'globalMemo': '삭제되면 안 됨'}, 400)
    assert seeded.data() == before
    seeded.save({'globalMemo': '오류 후 정상 저장'})


@pytest.mark.parametrize('method,url,payload', [
    ('POST', '/api/duty/save-all', {'globalMemo': 'unauthorized'}),
    ('POST', '/api/duty/request/action', {'request_id': 1, 'action': 'APPROVE'}),
    ('GET', '/api/duty/requests', None),
    ('GET', '/api/system/diagnostics', None),
    ('POST', '/api/system/check-path', {'path': '.'}),
])
def test_admin_auth_protects_sensitive_routes(seeded, method, url, payload):
    before = seeded.data()
    response = seeded.client.request(method, url, json=payload)
    assert response.status_code == 401, response.text
    assert seeded.data() == before


def test_wrong_admin_token_rejected(seeded):
    response = seeded.client.post('/api/duty/save-all', json={'globalMemo': 'bad'}, headers={'X-Admin-Token': 'incorrect'})
    assert response.status_code == 401
    assert seeded.data()['globalMemo'] == SEED['globalMemo']


@pytest.mark.parametrize('kind', KINDS)
def test_viewer_missing_db_never_created(load_app, kind):
    viewer = load_app(kind, 'VIEWER')
    path = Path(viewer.module.DB_PATH)
    assert not path.exists()
    assert viewer.get('/api/duty/all-data').status_code >= 400
    assert viewer.submit('SUBSTITUTE').status_code == 503
    assert viewer.post('/api/duty/save-all', {'globalMemo': 'no'}).status_code == 403
    assert not path.exists()


@pytest.mark.parametrize('kind', KINDS)
def test_viewer_can_request_but_cannot_directly_mutate(load_app, kind):
    admin = load_app(kind)
    admin.save(copy.deepcopy(SEED))
    viewer = load_app(kind, 'VIEWER', source_db=admin.module.DB_PATH)
    before = viewer.data()
    assert viewer.post('/api/duty/save-all', {'members': []}).status_code == 403
    result = viewer.submit('SUBSTITUTE')
    assert result.status_code == 200, result.text
    assert viewer.action(result.json()['requestId']).status_code == 403
    after = viewer.data()
    assert after['schedules'] == before['schedules']
    assert after['dataVersion'] == before['dataVersion']


@pytest.mark.parametrize('request_type', ['SWAP', 'SUBSTITUTE', 'ABSENCE'])
def test_submission_receipt_then_rejection_preserves_schedule(seeded, request_type):
    before = seeded.data()
    result = seeded.submit(request_type)
    assert result.status_code == 200, result.text
    request = result.json()
    status_url = '/api/duty/request-status/' + request['receiptToken']
    assert seeded.get(status_url).json()['request']['status'] == 'PENDING'
    response = seeded.action(request['requestId'], 'REJECT', reject_reason='합성 반려 사유')
    assert response.status_code == 200, response.text
    status = seeded.get(status_url).json()['request']
    assert status['status'] == 'REJECTED' and status['reject_reason'] == '합성 반려 사유'
    assert seeded.data()['schedules'] == before['schedules']
    assert seeded.action(request['requestId']).status_code == 400


def test_swap_approval_moves_both_people_and_versions(seeded):
    request = seeded.submit('SWAP').json()
    response = seeded.action(request['requestId'])
    assert response.status_code == 200, response.text
    data = seeded.data()
    assert data['schedules'][DAY_A][0]['name'] == 'B'
    assert data['schedules'][DAY_B][0]['name'] == 'A'
    assert data['dataVersion'] == 2
    assert seeded.rows('change_requests')[0]['status'] == 'APPROVED'
    assert data['history'][0]['type'] == '1:1 맞교환(승인)'


def test_substitute_approval_preserves_score_flags(seeded):
    request = seeded.submit('SUBSTITUTE').json()
    response = seeded.action(request['requestId'])
    assert response.status_code == 200, response.text
    assert seeded.data()['schedules'][DAY_A] == [
        {'name': 'C', 'role': '대체당직', 'isSubstitute': True, 'zeroScore': True}]


def test_absence_approval_removes_only_matching_schedules(seeded):
    request = seeded.submit('ABSENCE').json()
    response = seeded.action(request['requestId'])
    assert response.status_code == 200, response.text
    body, data = response.json(), seeded.data()
    assert body['removedScheduleCount'] == 2
    assert set(body['removedScheduleDates']) == {DAY_A, DAY_C}
    assert DAY_A not in data['schedules'] and DAY_C not in data['schedules']
    assert data['schedules'][DAY_B][0]['name'] == 'B'
    assert data['absences'][0]['name'] == 'A'
    assert data['absences'][0]['start'] == DAY_A and data['absences'][0]['end'] == DAY_C


@pytest.mark.parametrize('kind', ['SWAP', 'SUBSTITUTE'])
def test_changed_schedule_blocks_approval_without_partial_write(seeded, kind):
    request = seeded.submit(kind).json()
    seeded.save({'schedules': {DAY_B: [{'name': 'B', 'role': '당직'}]}})
    before = seeded.data()
    pending = seeded.get('/api/duty/requests', headers=AUTH).json()
    assert pending['requests'][0]['blocker']
    assert seeded.action(request['requestId']).status_code == 409
    assert seeded.data() == before
    assert seeded.rows('change_requests')[0]['status'] == 'PENDING'


def test_absent_substitute_blocks_approval(seeded):
    request = seeded.submit('SUBSTITUTE').json()
    seeded.save({'absences': [{'id': 1, 'name': 'C', 'type': '연차', 'start': DAY_A, 'end': DAY_A}]})
    before = seeded.data()
    assert seeded.action(request['requestId']).status_code == 409
    assert seeded.data() == before
    assert seeded.rows('change_requests')[0]['status'] == 'PENDING'


def test_overlapping_absence_pending_duplicate_rejected(seeded):
    assert seeded.submit('ABSENCE').status_code == 200
    assert seeded.submit('ABSENCE', target_date=DAY_B).status_code == 409
    assert len(seeded.rows('change_requests')) == 1


def test_approved_absence_overlap_retains_pending_request(seeded):
    request = seeded.submit('ABSENCE').json()
    seeded.save({'absences': [{'id': 1, 'name': 'A', 'type': '연차', 'start': DAY_B, 'end': DAY_C}]})
    before = seeded.data()
    assert seeded.action(request['requestId']).status_code == 409
    assert seeded.data() == before
    assert seeded.rows('change_requests')[0]['status'] == 'PENDING'


@pytest.mark.parametrize('request_type,changes', [
    ('UNKNOWN', {}), ('SWAP', {'swap_worker': 'A'}),
    ('SUBSTITUTE', {'substitute_name': 'A'}),
    ('SUBSTITUTE', {'substitute_name': '미등록'}),
    ('ABSENCE', {'absence_end_date': '2030-06-01'}),
    ('ABSENCE', {'absence_type': '미등록'}),
    ('ABSENCE', {'target_date': '20300617'}),
])
def test_invalid_request_never_changes_data(seeded, request_type, changes):
    before = seeded.data()
    response = seeded.submit(request_type, **changes)
    assert response.status_code == 400, response.text
    assert seeded.data() == before
    assert not seeded.rows('change_requests')


def normalize(value):
    ignored = {'timestamp', 'created_at', 'processed_at', 'receiptToken', 'receipt_token',
               'lastWriteTime', 'lastWriteVersion', 'maxWriteVersion', 'appVersion'}
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items() if key not in ignored}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


@pytest.mark.skipif(not REFERENCE_USABLE, reason='Original reference requires Python 3.13 and its marshal file')
def test_differential_api_sequence_and_database_snapshot(load_app):
    def exercise(app):
        snapshots = []
        app.save(copy.deepcopy(SEED))
        snapshots.append(normalize(app.data()))
        app.save({'comments': {DAY_A: '부분 저장'}, 'baseVersion': 1})
        snapshots.append(normalize(app.data()))
        snapshots.append(app.save({'globalMemo': 'conflict', 'baseVersion': 1}, 409).json())
        swap = app.submit('SWAP').json()
        snapshots.append(normalize(swap))
        snapshots.append(normalize(app.get('/api/duty/requests', headers=AUTH).json()))
        snapshots.append(normalize(app.action(swap['requestId']).json()))
        substitute = app.submit('SUBSTITUTE', requester_name='B').json()
        snapshots.append(normalize(app.action(substitute['requestId']).json()))
        absence = app.submit('ABSENCE', requester_name='C', absence_end_date=DAY_A).json()
        snapshots.append(normalize(app.action(absence['requestId']).json()))
        rejected = app.submit('ABSENCE', requester_name='B', target_date=DAY_B, absence_end_date=DAY_B).json()
        snapshots.append(normalize(app.action(rejected['requestId'], 'REJECT', reject_reason='검증 반려').json()))
        snapshots.append(normalize(app.data()))
        snapshots.append(normalize(app.rows('change_requests')))
        snapshots.append(normalize(app.rows('schedules')))
        return snapshots
    assert exercise(load_app('source')) == exercise(load_app('reference'))


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('encoding', ['utf-8-sig', 'cp949'])
def test_bootstrap_reads_bom_and_cp949_settings(load_app, kind, encoding):
    app = load_app(kind, config_encoding=encoding, config_text=(
        '[NETWORK]\nadmin_password=합성암호\nserver_port=9123\n'
        'open_browser=false\ntray_icon=false\n'))
    assert app.module.SERVER_PORT == 9123
    assert app.module.ADMIN_PASSWORD == '합성암호'
    assert app.module.OPEN_BROWSER is False and app.module.TRAY_ENABLED is False
    assert app.module.BIND_HOST == '127.0.0.1'
    assert Path(app.module.DB_PATH).is_file()


@pytest.mark.parametrize('kind', KINDS)
def test_bootstrap_legacy_viewer_config_migrates_port_without_db(load_app, kind):
    app = load_app(kind, 'VIEWER', legacy=True, config_text=(
        '[NETWORK]\nserver_port=8000\nopen_browser=false\ntray_icon=false\n'))
    assert app.module.SERVER_PORT == 8001
    assert Path(app.module.CONFIG_PATH).is_file()
    assert not Path(app.module.LEGACY_CONFIG_PATH).exists()
    assert Path(app.module.LEGACY_CONFIG_PATH + '.migrated').is_file()
    assert not Path(app.module.DB_PATH).exists()


@pytest.mark.parametrize('kind', KINDS)
def test_bootstrap_invalid_numeric_settings_fall_back(load_app, kind):
    app = load_app(kind, config_text='[NETWORK]\nserver_port=not-a-port\ndb_timeout_seconds=invalid\n')
    assert app.module.SERVER_PORT == 8000
    assert app.module.DB_TIMEOUT == 30.0


@pytest.mark.parametrize('kind', KINDS)
def test_bootstrap_missing_share_exposes_warning(load_app, kind, tmp_path):
    missing = tmp_path / 'definitely-missing-share'
    if kind == 'source':
        # Intentional 3.15.0 change: invalid configured share aborts startup,
        # instead of serving against a potentially unrelated local fallback DB.
        with pytest.raises(HTTPException) as exc:
            load_app(kind, 'VIEWER', config_text=f'[NETWORK]\nshared_folder_path={missing}\n')
        assert exc.value.status_code == 503
        # v3.18.0 경로는 화면 문장에 넣지 않고 로그에만 남긴다(조회용 사용자에게 내부 경로를 보이지 않는다).
        assert str(missing) not in exc.value.detail
        assert '공유 폴더에 연결할 수 없습니다' in exc.value.detail
        assert not list(tmp_path.rglob('smart_duty.db'))
        return
    app = load_app(kind, 'VIEWER', config_text=f'[NETWORK]\nshared_folder_path={missing}\n')
    assert str(missing) in app.module.STORAGE_WARNING
    info = app.get('/api/system/info')
    assert info.status_code == 200
    assert not Path(app.module.DB_PATH).exists()


@pytest.mark.parametrize('kind', KINDS)
def test_portable_viewer_filename_selects_viewer_mode(load_app, kind):
    app = load_app(kind, 'ADMIN', frozen_name='SmartDuty_Viewer.exe')
    assert app.module.APP_MODE == 'VIEWER'
    assert app.module.CONFIG_NAME == 'config_viewer.ini'
    assert app.module.SERVER_PORT == 8001
    assert not Path(app.module.DB_PATH).exists()
    assert app.get('/').status_code == 200


@pytest.mark.parametrize('kind', KINDS)
def test_bootstrap_never_opens_browser_or_starts_server_on_import(load_app, kind, monkeypatch):
    import webbrowser
    import uvicorn
    def forbidden(*args, **kwargs):
        pytest.fail('Import attempted to launch an external process/server')
    monkeypatch.setattr(webbrowser, 'open', forbidden)
    monkeypatch.setattr(uvicorn, 'run', forbidden)
    app = load_app(kind)
    assert app.get('/').status_code == 200
    assert 'no-store' in app.get('/').headers['cache-control']


@pytest.mark.parametrize('kind', KINDS)
def test_existing_database_initialization_is_idempotent(load_app, kind):
    app = load_app(kind)
    app.save(copy.deepcopy(SEED))
    before = app.data()
    app.module.init_db()
    app.module.init_db()
    assert app.data() == before


@pytest.mark.parametrize('month,target_date', [(5, '2027-05-03'), (12, '2027-12-27')])
def test_fixed_holiday_generation_save_reload_and_conflict_preserves_other_records(load_app, month, target_date):
    """Real HTML engine -> real API -> temporary SQLite -> reloaded records."""
    import json
    import subprocess

    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for HTML-engine/API integration')
    helper = SOURCE.parents[1] / 'tests/frontend/helpers/generate_payload.cjs'
    manual_date = '2026-12-30'
    manual = {'name': 'F2', 'role': '수동(여)', 'isSubstitute': True, 'zeroScore': True}
    seed = {'schedules': {manual_date: [manual]}, 'noDuty': {'2026-12-31': True},
            'comments': {manual_date: '원본 수동 기록 보존'}, 'absences': [], 'customHolidays': {}}
    generated = json.loads(subprocess.run([node, str(helper), '2027', str(month)],
        input=json.dumps(seed), text=True, capture_output=True, check=True).stdout)
    backend = load_app()
    backend.save({**seed, 'members': generated['members'], 'rules': generated['rules']})
    before = backend.data()
    payload = {**generated['payload'], 'baseVersion': before['dataVersion']}
    backend.save(payload)
    reloaded = backend.data()
    by_name = {m['name']: m['gender'] for m in reloaded['members']}
    assert [by_name[w['name']] for w in reloaded['schedules'][target_date]] == ['M', 'F']
    assert all(w['role'].startswith('공휴일') for w in reloaded['schedules'][target_date])
    assert reloaded['schedules'][manual_date] == [manual]
    assert reloaded['comments'][manual_date] == seed['comments'][manual_date]
    assert reloaded['noDuty']['2026-12-31'] is True
    assert reloaded['rules'] == generated['rules']
    assert reloaded['dataVersion'] == before['dataVersion'] + 1
    backend.save(payload, code=409)
    after_conflict = backend.data()
    assert after_conflict['schedules'] == reloaded['schedules']
    assert after_conflict['dataVersion'] == reloaded['dataVersion']
