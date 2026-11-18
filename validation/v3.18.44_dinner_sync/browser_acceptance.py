"""Two installed role profiles, one Windows-user-style root, and a separate employee PC."""
from datetime import date, timedelta
from pathlib import Path
import os
import sqlite3
import sys
import tempfile
import uuid

from playwright.sync_api import expect, sync_playwright

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT.parent / 'v3.18.41_details'))
from runtime import Runtime

NAME = '김하늘'
POLL = '부서 회식 날짜 선택'


def post(runtime, path, **fields):
    response = runtime.send('/api/dinner' + path, dict(operationId=str(uuid.uuid4()), **fields))
    response.raise_for_status()
    return response.json()


def vote(page, choice, note):
    radios = page.locator('#dp-vote-form input[value="' + choice + '"]')
    expect(radios).to_have_count(2)
    for radio in radios.all():
        radio.locator('..').click()
    page.locator('#dp-suggestion').fill(note)
    page.locator('#dp-submit').click()
    expect(page.locator('#dinner-modal .dp-success')).to_be_visible()


def run(browser, admin, viewer, other, baseline):
    a, v, outsider = [runtime.open(browser, name=NAME, width=1366, height=768)[1]
                      for runtime in (admin, viewer, other)]
    errors = []
    for page in (a, v, outsider):
        page.on('pageerror', lambda error: errors.append(str(error)))
    box = v.locator('#dinner-modal')
    v.locator('#header-dinner').click()
    expect(box.get_by_text('현재 받은 회식 날짜 취합이 없습니다.')).to_be_visible()
    today = date.today()
    poll = post(admin, '/polls', title=POLL, description='', targets=[NAME],
                deadline=(today + timedelta(days=3)).isoformat() + 'T17:00',
                candidates=[dict(id=str(i), date=(today + timedelta(days=i)).isoformat(), time='18:00')
                            for i in (5, 6)])['poll']
    post(admin, '/polls/' + poll['id'] + '/action', action='PUBLISH', expectedRevision=poll['revision'])
    v.evaluate('OnAirDinner.refresh()')
    visible = box.get_by_text(POLL, exact=True).is_visible()
    print('open viewer list shows published poll:', visible, flush=True)
    a.locator('#header-dinner').click()
    vote(a, 'possible', '첫 번째 날짜 선호')
    if baseline:
        v.evaluate('OnAirDinner.open(null,true)')
    else:
        assert visible
        v.evaluate('window.dispatchEvent(new Event("focus"))')
        expect(box.get_by_text('일정·내 응답 보기')).to_be_visible()
    box.locator('[data-dinner-action=poll]').click()
    expect(box.locator('.dp-answer-table')).to_be_visible()
    owned = box.get_by_text('내 응답 수정').is_visible()
    print('viewer recognizes response submitted by admin:', owned, flush=True)
    if baseline:
        assert not visible and not owned
        return
    assert owned
    expect(box.get_by_text('내 다른 날짜 제안: 첫 번째 날짜 선호')).to_be_visible()
    v.screenshot(path=str(OUT / 'viewer-response.png'), animations='disabled')
    outsider.evaluate('OnAirDinner.open(null,true)')
    outsider.locator('[data-dinner-action=poll]').click()
    expect(outsider.locator('#dinner-modal')).to_contain_text('다른 PC에서 제출한 응답')
    expect(outsider.locator('#dinner-modal')).not_to_contain_text('첫 번째 날짜 선호')
    box.get_by_text('내 응답 수정').click()
    vote(v, 'maybe', '두 번째 날짜 선호')
    a.evaluate('window.dispatchEvent(new Event("focus"))')
    expect(a.locator('#dinner-modal')).to_contain_text('두 번째 날짜 선호')
    assert a.locator('.dp-answer-table').get_by_text('미정', exact=True).count() == 2
    a.locator('#dinner-modal').get_by_text('내 응답 수정').click()
    a.locator('#dp-suggestion').fill('작성 중인 내용')
    box.get_by_text('내 응답 수정').click()
    vote(v, 'difficult', '최신 응답')
    a.evaluate('OnAirDinner.refresh()')
    expect(a.locator('#dp-suggestion')).to_have_value('작성 중인 내용')
    a.locator('#dp-submit').click()
    expect(a.locator('#dp-error')).to_contain_text('다른 창에서 응답을 저장했습니다.')
    expect(a.locator('#dinner-modal')).to_contain_text('내 다른 날짜 제안: 최신 응답')
    key = v.evaluate('OnAirDinner.credentials()')['key']
    without_session = viewer.get('/api/dinner/polls/' + poll['id'], params={'name': NAME},
                                 headers={'X-OnAir-Dinner-Key': key})
    assert without_session.json()['poll']['ownResponse'] is None
    print('pending input preserved, stale save rejected, sibling proof requires local session', flush=True)
    assert not errors, errors
    print('reverse edit, isolation of other PC, and open detail refresh passed', flush=True)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        shared = root / 'shared'
        shared.mkdir()
        sqlite3.connect(shared / 'smart_duty.db').close()
        os.environ['WLR_PROFILE_ROOT'] = str(root)
        runtimes = []
        try:
            admin = Runtime(root, 'ADMIN', 'ADMIN', shared)
            runtimes.append(admin)
            admin.seed()
            viewer = Runtime(root, 'VIEWER', 'VIEWER', shared)
            runtimes.append(viewer)
            other_root = root / 'other-user'
            other_root.mkdir()
            os.environ['WLR_PROFILE_ROOT'] = str(other_root)
            other = Runtime(other_root, 'VIEWER', 'VIEWER', shared)
            runtimes.append(other)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox'])
                run(browser, admin, viewer, other, '--baseline' in sys.argv)
                browser.close()
        finally:
            for runtime in reversed(runtimes):
                runtime.close()


if __name__ == '__main__':
    main()
