"""3.17.1 backup history and legacy absence regressions; synthetic temporary DBs only."""
import copy

import pytest

from test_backend_regression import DAY_A, DAY_B, DAY_C, SEED, load_app  # noqa: F401
from test_backend_usability import EDUCATION, absence


def legacy_absence(**changes):
    return {key: value for key, value in absence(**changes).items() if key != 'id'}


def test_supported_json_backup_restores_each_snapshot_and_separate_current_change(load_app):
    origin = load_app()
    origin.save(copy.deepcopy(SEED))
    origin.save({'schedules': {DAY_A: [{'name': 'C', 'role': '당직'}]},
                 'historyLog': {'type': '일정 수정', 'dateKey': DAY_A, 'details': 'A에서 C'}})
    origin.save({'absences': [absence(education=EDUCATION)],
                 'historyLog': {'type': '교육 등록', 'details': '교육 안내'}})
    source = origin.data()
    keys = ('members', 'schedules', 'comments', 'noDuty', 'absences', 'absenceTypes',
            'customHolidays', 'rules', 'globalMemo')
    target = load_app()
    target.save({**{key: source[key] for key in keys}, 'historyBulk': source['history'],
                 'historyLog': {'type': '데이터 복구', 'details': 'JSON 백업 복구'}})
    result = target.data()
    assert all(result[key] == source[key] for key in keys)
    assert len(result['history']) == len(source['history']) + 1
    for old, imported in zip(source['history'], result['history'][1:]):
        assert imported['details'] == '[가져온 이력] ' + old['details']
        for key in ('timestamp', 'type', 'dateKey', 'before', 'after'):
            assert imported.get(key) == old.get(key)
    current = result['history'][0]
    assert current['type'] == '데이터 복구'
    assert current['before']['schedules'] == {DAY_A: []}
    assert current['before']['absences'] == []
    assert current['after']['absences'] == source['absences']


def test_bulk_only_keeps_legacy_history_without_fabricated_snapshots(load_app):
    app = load_app()
    app.save(copy.deepcopy(SEED))
    history = app.data()['history']
    assert len(history) == 2
    imported = next(item for item in history if item['type'] == 'TEST')
    assert 'before' not in imported and 'after' not in imported
    current = history[0]
    assert current['type'] == '데이터 가져오기'
    assert current['timestamp'] != imported['timestamp']
    assert current['before']['schedules'][DAY_A] == []
    assert current['after']['schedules'][DAY_A][0]['name'] == 'A'


@pytest.mark.parametrize('snapshots', [{}, {'before': None, 'after': None}])
def test_history_only_legacy_import_does_not_invent_current_domain_change(load_app, snapshots):
    app = load_app()
    app.save({'historyBulk': [{'type': '과거', 'details': '과거 기록', **snapshots}]})
    history = app.data()['history']
    assert len(history) == 1
    assert 'before' not in history[0] and 'after' not in history[0]


@pytest.mark.parametrize('snapshots', [
    {'before': [], 'after': {}},
    {'before': {'schedules': {}}, 'after': None},
    {'before': {'schedules': []}, 'after': {'schedules': {}}},
    {'before': {'schedules': {DAY_A: None}}, 'after': {'schedules': {}}},
    {'before': {'schedules': {DAY_A: [None]}}, 'after': {'schedules': {}}},
    {'before': {'schedules': {'2030-02-30': []}}, 'after': {'schedules': {}}},
    {'before': {'schedules': {DAY_A: [{'name': ['A']}]}}, 'after': {'schedules': {}}},
    {'before': {'schedules': {DAY_A: [{'name': 'A', 'zeroScore': 'false'}]}}, 'after': {'schedules': {}}},
    {'before': {'absences': {}}, 'after': {'absences': []}},
    {'before': {'absences': [None]}, 'after': {'absences': []}},
    {'before': {'absences': [absence(education={'title': []})]}, 'after': {'absences': []}},
    {'before': {'absences': [absence(start=DAY_C, end=DAY_A)]}, 'after': {'absences': []}},
    {'before': {'absences': [absence(id=True)]}, 'after': {'absences': []}},
    {'before': {'absences': [absence(), absence()]}, 'after': {'absences': []}},
    {'before': {'unknown': 'made up'}, 'after': {'unknown': 'made up'}},
    {'before': {'schedules': {}}, 'after': {'absences': []}},
    {'before': {'schedules': {DAY_A: []}}, 'after': {'schedules': {DAY_B: []}}},
])
def test_invalid_imported_snapshots_drop_only_that_snapshot(load_app, snapshots):
    # v3.17.13 예전에는 전후 기록 하나가 형식에 맞지 않으면 JSON 백업 복구 전체를 거절했다(400).
    #   옛 DB 의 값 하나 때문에 복구가 영영 안 되는 일을 막기 위해, 그 전후 기록만 버리고 이력 행과
    #   나머지 자료는 가져온다. 버렸다는 사실은 이력 내용에 남는다. 만들어 낸 전후 기록은 절대 넣지 않는다.
    app = load_app()
    payload = copy.deepcopy(SEED)
    payload['historyBulk'] = [{'type': 'valid legacy', 'details': '먼저 입력'},
                              {'type': 'invalid', 'details': '잘못된 비교', **snapshots}]
    app.save(payload)
    data = app.data()
    assert data['members'] == SEED['members']
    rows = {item['type']: item for item in data['history']}
    invalid = rows['invalid']
    assert invalid['details'] == '[가져온 이력] 잘못된 비교 (전후 기록 형식 오류로 생략)'
    assert 'before' not in invalid and 'after' not in invalid
    assert rows['valid legacy']['details'] == '[가져온 이력] 먼저 입력'


def test_imported_sparse_blank_and_null_education_remains_exact(load_app):
    app = load_app()
    snapshots = {'before': {'absences': [absence(education=None)]},
                 'after': {'absences': [absence(education={'title': '', 'notes': '공개'})]}}
    app.save({'historyBulk': [{'type': '교육 수정', **snapshots}]})
    history = app.data()['history'][0]
    assert {key: history[key] for key in snapshots} == snapshots


def test_existing_history_stays_append_only_even_with_invalid_bulk(load_app):
    app = load_app()
    app.save(copy.deepcopy(SEED))
    old = app.data()['history']
    app.save({'schedules': {}, 'historyBulk': [{'before': 'invalid', 'after': []}]})
    history = app.data()['history']
    assert history[1:] == old
    assert history[0]['type'] == '일정/부재 변경'
    assert history[0]['before']['schedules'][DAY_A][0]['name'] == 'A'


def test_numeric_string_id_preserves_omitted_education_and_normalizes_identity(load_app):
    app = load_app()
    app.save({'absences': [absence(education=EDUCATION)]})
    app.save({'absences': [absence(id='70', end=DAY_C)]})
    assert app.data()['absences'] == [absence(end=DAY_C, education=EDUCATION)]
    app.save({'absences': [absence(id='70', education=None)]})
    assert 'education' not in app.data()['absences'][0]
    app.save({'absences': [absence(id='70', name='B')]})
    assert 'education' not in app.data()['absences'][0]


def test_identical_idless_rows_reuse_each_existing_identity_once(load_app):
    app = load_app()
    records = [legacy_absence(education={'title': '첫째'}),
               legacy_absence(education={'title': '둘째'})]
    app.save({'absences': records})
    original = app.data()['absences']
    for _ in range(2):
        app.save({'absences': [legacy_absence(), legacy_absence()]})
        assert app.data()['absences'] == original
    app.save({'absences': [legacy_absence()] * 3})
    result = app.data()['absences']
    assert result[:2] == original
    assert len({record['id'] for record in result}) == 3
    assert 'education' not in result[2]


@pytest.mark.parametrize('explicit_first', [False, True])
def test_mixed_rows_reserve_explicit_existing_identity(load_app, explicit_first):
    app = load_app()
    app.save({'absences': [absence(id=70, education={'title': 'reserved'}),
                           absence(id=71, education={'title': 'idless'})]})
    records = [legacy_absence(), absence(id='70', end=DAY_C)]
    if explicit_first:
        records.reverse()
    app.save({'absences': records})
    by_id = {record['id']: record for record in app.data()['absences']}
    assert by_id[70]['education']['title'] == 'reserved'
    assert by_id[70]['end'] == DAY_C
    assert by_id[71]['education']['title'] == 'idless'


@pytest.mark.parametrize('explicit_first', [False, True])
def test_mixed_new_rows_do_not_collide_with_sqlite_auto_id(load_app, explicit_first):
    app = load_app()
    records = [legacy_absence(), absence(id='1', name='B')]
    if explicit_first:
        records.reverse()
    app.save({'absences': records})
    result = app.data()['absences']
    assert len({record['id'] for record in result}) == 2
    assert next(record for record in result if record['id'] == 1)['name'] == 'B'


@pytest.mark.parametrize('record_ids', [[70, '70'], [True], [70.5], ['70.5'], [[]], [2**63]])
def test_invalid_or_duplicate_explicit_ids_reject_atomically(load_app, record_ids):
    app = load_app()
    app.save(copy.deepcopy(SEED))
    before = app.data()
    app.save({'schedules': {}, 'absences': [absence(id=value) for value in record_ids]}, code=400)
    assert app.data() == before
