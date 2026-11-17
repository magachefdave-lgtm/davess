"""v3.18.0 조회용 신청·알림 흐름: 결재 출처 표시, 상대방 결과 조회, 처리된 신청 안내."""
from __future__ import annotations

import copy

import pytest

from test_backend_regression import AUTH, DAY_A, DAY_B, DAY_C, SEED, load_app  # noqa: F401


@pytest.fixture
def seeded(load_app):
    app = load_app('source')
    app.save(copy.deepcopy(SEED))
    return app


def info(app):
    response = app.get('/api/system/info')
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('decision,status', [('APPROVE', 'APPROVED'), ('REJECT', 'REJECTED')])
def test_processed_request_marks_last_write_source_and_plain_save_clears_it(seeded, decision, status):
    request_id = seeded.submit('SWAP').json()['requestId']
    assert seeded.action(request_id, decision).status_code == 200
    # 조회용 화면은 이 표시로 '당직표 갱신' 알림을 결과 알림 하나로 합친다.
    assert info(seeded)['lastWrite']['source'] == f'request:{request_id}:{status}'
    seeded.save({'comments': {DAY_B: '다른 저장'}})
    assert info(seeded)['lastWrite']['source'] == ''


def test_involving_lists_partner_requests_without_private_reason(seeded):
    swap = seeded.submit('SWAP').json()['requestId']
    sub = seeded.submit('SUBSTITUTE', target_date=DAY_A).json()['requestId']
    assert seeded.action(swap, 'REJECT', reject_reason='상대에게는 보이지 않음').status_code == 200
    # 예전 동작: 이름 조회는 신청자 본인 행만 준다.
    assert seeded.get('/api/duty/my-requests', params={'name': 'B'}).json()['requests'] == []
    rows = seeded.get('/api/duty/my-requests', params={'name': 'B', 'involving': 1}).json()['requests']
    assert [(r['id'], r['role']) for r in rows] == [(swap, 'partner')]
    assert rows[0]['reject_reason'] == '' and 'reason' not in rows[0]
    rows = seeded.get('/api/duty/my-requests', params={'name': 'C', 'involving': 1}).json()['requests']
    assert [(r['id'], r['role']) for r in rows] == [(sub, 'partner')]
    own = seeded.get('/api/duty/my-requests', params={'name': 'A', 'involving': 1}).json()['requests']
    assert {r['id']: r['role'] for r in own} == {swap: 'requester', sub: 'requester'}
    assert [r['reject_reason'] for r in own if r['id'] == swap] == ['상대에게는 보이지 않음']


def test_involving_does_not_treat_absence_type_as_a_person(seeded):
    seeded.save({'members': SEED['members'] + [{'id': 4, 'name': '연차', 'gender': 'M'}]})
    assert seeded.submit('ABSENCE').status_code == 200
    rows = seeded.get('/api/duty/my-requests', params={'name': '연차', 'involving': 1}).json()['requests']
    assert rows == []


@pytest.mark.parametrize('decision,word', [('APPROVE', '이미 승인된 신청입니다.'), ('REJECT', '이미 반려된 신청입니다.')])
def test_editing_a_processed_request_says_how_it_was_processed(seeded, decision, word):
    receipt = seeded.submit('SUBSTITUTE').json()
    assert seeded.action(receipt['requestId'], decision).status_code == 200
    for action in ('EDIT', 'WITHDRAW'):
        body = {'receipt_token': receipt['receiptToken'], 'action': action}
        if action == 'EDIT':
            body['request'] = {'requester_name': 'A', 'target_date': DAY_A, 'request_type': 'SUBSTITUTE',
                               'substitute_name': 'C', 'reason': '수정'}
        response = seeded.post('/api/duty/request/manage', body, auth=False)
        assert response.status_code == 409
        assert response.json()['detail'].startswith(word)
        assert '토큰' not in response.json()['detail']


@pytest.mark.parametrize('requested', [True, False])
def test_substitute_approval_keeps_requester_zero_choice_and_uses_one_term(seeded, requested):
    request_id = seeded.submit('SUBSTITUTE', is_zero_score=requested).json()['requestId']
    listed = seeded.get('/api/duty/requests', headers=AUTH).json()['requests'][0]
    assert bool(listed['is_zero_score']) is requested
    assert seeded.action(request_id).status_code == 200
    slot = seeded.data()['schedules'][DAY_A][0]
    assert slot['name'] == 'C' and slot['zeroScore'] is requested
    details = seeded.data()['history'][0]['details']
    assert f"대체 근무 0점 처리: {'예' if requested else '아니오'}" in details
    assert '주말 0점' not in details


def test_approved_absence_status_lists_only_duties_it_actually_released(seeded):
    """3.18.0 QA: 당직이 없던 교육 기간도 '이 기간의 내 당직은 해제됩니다' 라고 알렸다.

    결과 조회(request-status)가 실제로 풀린 날짜(released_dates)를 알려 주고, 알림은 그 목록으로만 말한다."""
    with_duty = seeded.submit('ABSENCE').json()                                   # A: DAY_A~DAY_C, 당직 2건
    assert seeded.action(with_duty['requestId']).status_code == 200
    body = seeded.get(f"/api/duty/request-status/{with_duty['receiptToken']}").json()['request']
    assert body['released_dates'] == [DAY_A, DAY_C]
    none = seeded.submit('ABSENCE', requester_name='C', target_date='2030-06-24', absence_end_date='2030-06-26').json()
    assert seeded.action(none['requestId']).status_code == 200
    body = seeded.get(f"/api/duty/request-status/{none['receiptToken']}").json()['request']
    assert body['released_dates'] == []
    pending = seeded.submit('ABSENCE', requester_name='B', target_date='2030-07-01', absence_end_date='2030-07-02').json()
    body = seeded.get(f"/api/duty/request-status/{pending['receiptToken']}").json()['request']
    assert 'released_dates' not in body                                             # 아직 처리 전이면 모른다
