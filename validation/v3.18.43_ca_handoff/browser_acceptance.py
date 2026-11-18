"""Check the handoff on two real Viewer profiles and an isolated shared database."""
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import patch
import uuid

from playwright.sync_api import expect, sync_playwright

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT.parent / 'v3.18.41_details'))
from runtime import Runtime

OUTGOING, INCOMING, RESPONDER = '김하늘', '이서연', '김도윤'
PREFIX = '/api/dinner'
MODAL = '#onair-dinner-admin'
checks, page_errors = [], []


def check(name, valid):
    assert valid, name
    checks.append(name)
    print('PASS ' + name, flush=True)


def op(**params):
    return dict(operationId=str(uuid.uuid4()), **params)


def api(runtime, route, **params):
    reply = runtime.send(PREFIX + route, **params)
    reply.raise_for_status()
    return reply.json()


def viewer_headers(page):
    return {'X-OnAir-Dinner-Key': page.evaluate('OnAirDinner.credentials()')['key']}


def change_date(clock, pages, value):
    clock.write_text(value + '+09:00', encoding='utf-8')
    for page in pages:
        page.clock.set_fixed_time(datetime.fromisoformat(value + '+09:00'))


def open_inbox(page):
    for selector in (MODAL, '#dinner-modal'):
        if page.locator(selector).is_visible():
            page.keyboard.press('Escape')
            expect(page.locator(selector)).to_be_hidden()
    page.locator('#header-dinner').click()
    inbox = page.locator('#dinner-modal')
    expect(inbox.get_by_role('button', name='새로 확인')).to_be_visible()
    return inbox


def open_handoff(page, operator):
    open_inbox(page).get_by_role('button', name='인수인계 현황', exact=True).click()
    modal = page.locator(MODAL)
    expect(modal.get_by_role('heading', name='회식 인수인계')).to_be_visible()
    expect(modal.get_by_text('조회 전용', exact=True)).to_be_visible()
    expect(modal.get_by_text(f'운영 담당 CA {operator}', exact=False)).to_be_visible()
    expect(modal.get_by_role('button', name='새 날짜 취합')).to_have_count(0)
    expect(modal.get_by_role('heading', name='연초 석식간담회')).to_be_visible()
    expect(modal.get_by_text('게시 전 초안')).to_have_count(0)
    return modal


def inspect_results(modal):
    modal.get_by_role('button', name='응답 현황').click()
    expect(modal.get_by_text('인수인계 · 조회 전용')).to_be_visible()
    expect(modal.locator('.da-progress-track')).to_have_attribute('aria-label', '응답 완료 1명 / 2명')
    modal.locator('summary').filter(has_text='직원별 응답과 제안').click()
    expect(modal.get_by_text(RESPONDER, exact=True)).to_be_visible()
    expect(modal.get_by_text('첫 번째 후보 선호', exact=True)).to_be_visible()
    expect(modal.locator('[data-da="edit"], [data-da="finalize"], [data-da="close-poll"], '
                         '[data-da="publish"], [data-da="cancel-poll"], [data-da="remind"]')).to_have_count(0)


def run(browser, admin, outgoing, incoming, clock):
    pages = [runtime.open(browser, name=name, width=1366, height=768)[1]
             for runtime, name in ((outgoing, OUTGOING), (incoming, INCOMING))]
    for page in pages:
        page.on('pageerror', lambda error: page_errors.append(str(error)))
    old_page, new_page = pages
    for year, runtime, page, name in ((2026, outgoing, old_page, OUTGOING),
                                      (2027, incoming, new_page, INCOMING)):
        api(admin, '/role', body=op(roleId=year, memberName=name, startDate=f'{year}-01-01',
                                    endDate=f'{year}-12-31', expectedRevision=0))
        connection = api(runtime, '/connections', body=op(roleId=year, memberName=name),
                         headers=viewer_headers(page))['connection']
        api(admin, f"/connections/{connection['id']}/decision", body=op(decision='APPROVE'))

    body = dict(description='1월 날짜를 함께 확인합니다.', deadline='2027-01-12T17:00',
                candidates=[dict(id='first', date='2027-01-15', time='18:00'),
                            dict(id='second', date='2027-01-18', time='18:30')],
                targets=[RESPONDER, '박민수'])
    api(admin, '/polls', body=op(title='게시 전 초안', **body))
    draft = api(admin, '/polls', body=op(title='연초 석식간담회', **body))['poll']
    poll_path = '/polls/' + draft['id']
    poll = api(admin, poll_path + '/action',
               body=op(action='PUBLISH', expectedRevision=draft['revision']))['poll']
    api(incoming, poll_path + '/response',
        body=op(memberName=RESPONDER, expectedRevision=poll['revision'], suggestion='첫 번째 후보 선호',
                answers={'first': 'possible', 'second': 'maybe'}),
        headers={'X-OnAir-Dinner-Key': 'fixture-answer-key-for-january-' + 'a' * 43})

    change_date(clock, pages, '2026-11-30T23:59:59')
    expect(open_inbox(new_page).get_by_role('button', name='CA 사용 연결')).to_be_visible()
    check('12월 직전에는 승인된 차기 CA도 조회 대기',
          incoming.get(PREFIX + '/manage', headers=viewer_headers(new_page)).status_code == 403)

    change_date(clock, pages, '2026-12-01T00:00:00')
    modal = open_handoff(new_page, OUTGOING)
    check('12월에 차기 CA가 회식 메뉴에서 인수인계 목록 조회', modal.locator('.da-poll-card').count() == 1)
    new_page.screenshot(path=str(OUT / 'december-handoff-list.png'), animations='disabled')
    inspect_results(modal)
    new_page.screenshot(path=str(OUT / 'december-handoff-results.png'), animations='disabled')
    check('개별 응답과 제안을 조회하며 화면에 운영 버튼 없음',
          not modal.locator('.da-management-actions').count())
    for width, height in [(1366, 768), (390, 844)]:
        new_page.set_viewport_size(dict(width=width, height=height))
        body_el = modal.locator('.modal-body')
        check(f'{width}px 상세 화면 가로 넘침 없음',
              body_el.evaluate('(node)=>node.scrollWidth <= node.clientWidth + 1'))
    new_page.set_viewport_size(dict(width=1366, height=768))
    fake_close = incoming.send(PREFIX + poll_path + '/action',
                               body=op(action='CLOSE', expectedRevision=poll['revision']),
                               headers=viewer_headers(new_page))
    check('12월 차기 CA의 서버 마감 요청 거절', fake_close.status_code == 403)
    manage = open_inbox(old_page).get_by_role('button', name='회식 관리', exact=True)
    manage.click()
    expect(old_page.locator(MODAL).get_by_role('button', name='새 날짜 취합')).to_be_visible()

    change_date(clock, pages, '2027-01-01T00:00:00')
    modal = open_handoff(old_page, INCOMING)
    inspect_results(modal)
    old_page.screenshot(path=str(OUT / 'january-handoff-results.png'), animations='disabled')
    open_inbox(new_page).get_by_role('button', name='회식 관리', exact=True).click()
    expect(new_page.locator(MODAL).get_by_role('button', name='새 날짜 취합')).to_be_visible()
    check('1월 1일 현황을 보던 CA가 운영 권한을 받고 이전 CA는 조회 전용',
          outgoing.send(PREFIX + poll_path + '/action',
                        body=op(action='CONFIRM', expectedRevision=poll['revision'], selectedCandidateId='first'),
                        headers=viewer_headers(old_page)).status_code == 403)
    closed = api(incoming, poll_path + '/action', body=op(action='CLOSE', expectedRevision=poll['revision']),
                 headers=viewer_headers(new_page))['poll']
    check('새 임기 담당자가 같은 취합의 응답을 마감', closed['status'] == 'CLOSED')
    check('개인 PC 연결을 제시하지 않은 직원은 응답 현황에 접근 불가',
          outgoing.get(PREFIX + '/manage').status_code == 403)

    change_date(clock, pages, '2027-02-01T00:00:00')
    expect(open_inbox(old_page).get_by_role('button', name='인수인계 현황')).to_have_count(0)
    check('2월 1일에 전임 CA의 보기 메뉴와 서버 권한 종료',
          outgoing.get(PREFIX + '/manage', headers=viewer_headers(old_page)).status_code == 403)
    check('브라우저 실행 오류 없음', not page_errors)


def main():
    runtimes = []
    try:
        with tempfile.TemporaryDirectory(prefix='onair-ca-handoff-') as temporary:
            root = Path(temporary)
            shared = root / 'shared'
            shared.mkdir()
            sqlite3.connect(shared / 'smart_duty.db').close()
            clock = root / 'dinner-clock.txt'
            change_date(clock, [], '2026-11-18T12:00:00')
            original_popen = subprocess.Popen
            freeze = f"""
from datetime import datetime
from pathlib import Path
from backend import dinner_service
RealDinnerService = dinner_service.DinnerService
def dinner_at_test_time(cur, **kwargs):
    return RealDinnerService(cur, now=datetime.fromisoformat(Path({str(clock)!r}).read_text()), **kwargs)
dinner_service.DinnerService = dinner_at_test_time
"""
            def start(args, **options):
                return original_popen([*args[:-1], freeze + args[-1]], **options)

            with patch('runtime.subprocess.Popen', side_effect=start):
                admin = Runtime(root, 'admin', 'ADMIN', shared)
                runtimes.append(admin)
                admin.seed()
                outgoing = Runtime(root, 'outgoing', 'VIEWER', shared)
                runtimes.append(outgoing)
                incoming = Runtime(root, 'incoming', 'VIEWER', shared)
                runtimes.append(incoming)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path='/usr/bin/chromium',
                                                     headless=True, args=['--no-sandbox'])
                run(browser, admin, outgoing, incoming, clock)
                browser.close()
    finally:
        for runtime in reversed(runtimes):
            runtime.close()
        (OUT / 'acceptance.json').write_text(json.dumps({'checks': checks, 'page_errors': page_errors},
                                                       ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
