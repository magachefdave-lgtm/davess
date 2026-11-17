"""v3.18.0 문구 회귀: 화면·메시지 상자에 영어 예외·내부 경로·개발 용어가 새지 않는다."""
from __future__ import annotations

import copy
import importlib.util
import re
import sqlite3
from pathlib import Path

import pytest

from test_backend_regression import SEED, load_app  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
ENGLISH = re.compile(r'\b(?!WLR\b|OnAir\b|F5\b)[A-Za-z]{3,}\b')


@pytest.fixture
def seeded(load_app):
    app = load_app('source')
    app.save(copy.deepcopy(SEED))
    return app


def test_friendly_db_error_keeps_raw_text_out_of_the_sentence(seeded):
    m = seeded.module
    for raw, expect in [('database is locked', '다른 PC가 저장 중입니다'),
                        ('disk I/O error', '공유 폴더 연결이 불안정합니다'),
                        ('unable to open database file', '공유 폴더에 연결되어 있는지'),
                        ('database disk image is malformed', '자동 백업으로 되돌리기'),
                        ('something odd', '자가 진단 열기')]:
        text = m.friendly_db_error(sqlite3.OperationalError(raw), '저장')
        assert text.startswith('저장하지 못했습니다. ') and expect in text, text
        assert '상세' not in text and raw not in text
        assert not ENGLISH.search(text), text


def test_josa_and_month_label(seeded):
    josa = seeded.module.josa
    assert josa('신청자', '을/를') == '신청자를'
    assert josa('대체 근무자 이름', '을/를') == '대체 근무자 이름을'
    assert josa('신청 사유', '은/는') == '신청 사유는'
    assert josa('2026년 10월', '은/는') == '2026년 10월은'
    assert josa('대상일', '이/가') == '대상일이'
    assert josa('종료일', '으로/로') == '종료일로'
    assert seeded.module.month_label('2026-10') == '2026년 10월'


def test_system_info_reports_a_lost_database_in_korean_without_exception_text(seeded, monkeypatch):
    def broken(*_a, **_k):
        raise sqlite3.OperationalError('unable to open database file')
    monkeypatch.setattr(seeded.module, 'get_db_connection', broken)
    body = seeded.get('/api/system/info').json()
    assert body['dbOk'] is False
    assert '하지 못했습니다' in body['detail'] or '못했습니다' in body['detail']
    assert 'unable' not in body['detail'] and not ENGLISH.search(body['detail'])


def test_ping_answers_without_touching_the_data_file(seeded, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError('ping must not open the data file')
    monkeypatch.setattr(seeded.module, 'get_db_connection', boom)
    response = seeded.get('/api/system/ping')
    assert response.status_code == 200 and response.json()['ok'] is True


def test_request_validation_messages_read_naturally(seeded):
    response = seeded.submit('SWAP', swap_worker='')
    assert response.status_code == 400
    assert '을(를)' not in response.json()['detail'] and '를(을)' not in response.json()['detail']
    response = seeded.submit('ABSENCE', absence_type='없는구분')
    assert '휴가·부재' in response.json()['detail'] and '휴가/부재' not in response.json()['detail']
    bad_day = seeded.post('/api/duty/save-all', {'baseVersion': seeded.data()['dataVersion'], 'schedules': {'2026-13-40': []}})
    assert bad_day.status_code == 400
    assert "'2026-13-40'" not in bad_day.json()['detail'] and '은(는)' not in bad_day.json()['detail']


def test_backend_user_messages_have_no_raw_exception_or_path_interpolation():
    src = (ROOT / 'backend' / 'main.py').read_text(encoding='utf-8')
    details = re.findall(r"detail=(f?['\"].*?['\"])\)", src)
    assert details, 'detail= 문장을 찾지 못했다'
    for text in details:
        assert '{e}' not in text and '{exc}' not in text and '{DB_PATH}' not in text, text
        assert '!r}' not in text, text
        assert '(상세' not in text, text
    assert 'SmartDuty_Admin.exe' not in src
    assert '[월 마감 해제]' not in src
    assert '데이터베이스에 접근할 수 없습니다: {e}' not in src


def _load_entrypoint():
    spec = importlib.util.spec_from_file_location('onair_entrypoint_test', ROOT / 'entrypoint.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_startup_message_box_hides_jargon_and_asks_for_a_capture():
    entry = _load_entrypoint()
    technical = entry.startup_failure_message(RuntimeError('JSON 파일이 허용 크기를 초과합니다.'))
    assert technical.startswith('WLR OnAir를 시작하지 못했습니다.\n\n프로그램을 준비하지 못했습니다. 이 창을 캡처해 관리자에게 보내 주세요.')
    assert '(관리자 전달용: JSON 파일이 허용 크기를 초과합니다.)' in technical
    plain = entry.startup_failure_message(RuntimeError('기존 WLR OnAir 프로그램이 실행 중입니다.'))
    assert plain.endswith('기존 WLR OnAir 프로그램이 실행 중입니다.\n\n이 창을 캡처해 관리자에게 보내 주세요.')
    already = entry.startup_failure_message(RuntimeError('모두 시작하지 못했습니다. 이 창을 캡처해 관리자에게 보내 주세요.'))
    assert already.count('이 창을 캡처해') == 1


def test_launcher_status_messages_use_plain_korean_and_common_ending():
    src = (ROOT / 'updater' / 'launcher.py').read_text(encoding='utf-8')
    status_lines = [line for line in src.splitlines() if re.search(r"check = \(|'start_failed', |write_update_status\(profile, 'start_failed'", line)]
    joined = '\n'.join(status_lines)
    for banned in ('(원인', 'launcher.log 를', '은(는)', '을(를)', '이(가)', '순서 번호', '서명 확인'):
        assert banned not in joined, banned
    assert "FALLBACK_PREVIOUS = '프로그램을 업데이트하지 못해 이전 버전으로 실행합니다. ' + CAPTURE_HINT" in src
    assert "CAPTURE_HINT = '이 창을 캡처해 관리자에게 보내 주세요.'" in src
    for jargon in ('버전 보관 폴더가 연결 폴더입니다', '업데이트 신뢰 설정이 연결 파일입니다', '원본 DB는'):
        assert jargon not in src, jargon
