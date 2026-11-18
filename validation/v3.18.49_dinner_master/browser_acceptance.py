"""Exercise the installed-profile web flows on a temporary shared database with synthetic staff.

The dinner calendar is fixed at December 8, 2026, for the annual CA handoff.
The real launcher ticket, local session, Master enrollment, password verifier,
role authorization, FastAPI, SQLite and Chromium are used.
"""
import argparse
import configparser
from contextlib import ExitStack
from datetime import datetime
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import patch
import uuid

from playwright.sync_api import expect, sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / 'v3.18.41_details'))
from runtime import Runtime, fixture

ADMIN_NAME, VIEWER_NAME, OPERATOR, SUCCESSOR, UNANSWERED = '김도윤', '이서연', '김하늘', '박민수', '최수빈'
TITLE = '연말 회식 날짜 취합'
ADMIN_NOTE, VIEWER_NOTE = '두 후보 모두 참석할 수 있습니다.', '목요일보다 금요일을 선호합니다.'
NOW = datetime.fromisoformat('2026-12-08T12:00:00+09:00')
INBOX, MANAGEMENT = '#dinner-modal', '#onair-dinner-admin'
SENSITIVE = {'results', 'responders', 'nonresponders', 'confirmationRequired'}


def assert_private(body, other_note):
    if isinstance(body, dict):
        assert not SENSITIVE.intersection(body), set(body)
        for value in body.values():
            assert_private(value, other_note)
    elif isinstance(body, list):
        for value in body:
            assert_private(value, other_note)
    elif isinstance(body, str):
        assert other_note not in body


def api(page, path, fields=None, method='POST'):
    opts = {} if fields is None else {'method': method, 'body': {'operationId': str(uuid.uuid4()), **fields}}
    return page.evaluate('([path, opts]) => OnAirDinner.api(path, opts)', [path, opts])


def denied(page, path, fields=None):
    options = {} if fields is None else {'method': 'POST', 'body': {'operationId': str(uuid.uuid4()), **fields}}
    status = page.evaluate("""async ([path, options]) => {
      try { await OnAirDinner.api(path, options); return 200; }
      catch (error) { return error.status; }
    }""", [path, options])
    assert status == 403, (path, status)


def close_dialogs(page):
    for selector in (MANAGEMENT, INBOX):
        if page.locator(selector).is_visible():
            page.keyboard.press('Escape')
            expect(page.locator(selector)).to_be_hidden()


def inbox(page):
    close_dialogs(page)
    page.locator('#header-dinner').click()
    return page.locator(INBOX)


def vote(page, answer, note):
    panel = inbox(page)
    radios = panel.locator('input[value="' + answer + '"]')
    expect(radios).to_have_count(2)
    for radio in radios.all():
        radio.locator('..').click()
    panel.locator('#dp-suggestion').fill(note)
    panel.locator('#dp-submit').click()
    expect(panel.locator('.dp-success')).to_be_visible()
    expect(panel).to_contain_text(note)
    expect(panel.get_by_text('내 응답 수정')).to_be_visible()
    close_dialogs(page)


def screenshot(page, captures, name):
    if captures:
        # Let queued notifications finish so screenshots show the underlying interface.
        page.bring_to_front()
        page.mouse.move(0, 0)
        expect(page.locator('#toast')).to_be_hidden(timeout=90000)
        page.screenshot(path=captures / (name + '.png'), animations='disabled')


def setup_roles(admin, operator, successor, captures):
    role_button = inbox(admin).get_by_role('button', name='CA 지정·연결 승인', exact=True)
    role_button.click()
    roles = admin.locator(MANAGEMENT)
    roles.locator('#da-ca-name').select_option(OPERATOR)
    roles.locator('[data-da=save-role]').click()
    admin.locator('[data-da=accept]').click()
    expect(roles.get_by_role('button', name='권한 해제')).to_be_visible()
    api(admin, '/role', dict(roleId=2027, memberName=SUCCESSOR, startDate='2027-01-01',
                             endDate='2027-12-31', expectedRevision=0))
    for page, name, year in ((operator, OPERATOR, 2026), (successor, SUCCESSOR, 2027)):
        pending = api(page, '/connections', dict(memberName=name, roleId=year, deviceLabel='예시 담당자 PC'))
        denied(page, '/manage')
        if year == 2026:
            close_dialogs(admin)
            inbox(admin).get_by_role('button', name='CA 지정·연결 승인', exact=True).click()
            roles.locator('[data-da=connection-review]').click()
            admin.locator('#da-confirm-check').check()
            admin.locator('[data-da=accept]').click()
            expect(roles.locator('[data-da=connection-review]')).to_have_count(0)
        else:
            api(admin, '/connections/' + pending['connection']['id'] + '/decision', {'decision': 'APPROVE'})
    screenshot(admin, captures, '02-admin-ca-appointments')
    close_dialogs(admin)


def create_polls(operator):
    fields = dict(description='가능한 날짜에 응답해 주세요.', deadline='2026-12-14T17:00',
                  candidates=[dict(id='thu', date='2026-12-17', time='18:30'),
                              dict(id='fri', date='2026-12-18', time='18:30')],
                  targets=[ADMIN_NAME, VIEWER_NAME, UNANSWERED])
    api(operator, '/polls', dict(title='게시 전 초안', **fields))
    draft = api(operator, '/polls', dict(title=TITLE, **fields))['poll']
    path = '/polls/' + draft['id']
    poll = api(operator, path + '/action', dict(action='PUBLISH', expectedRevision=draft['revision']))['poll']
    return path, poll['revision']


def open_results(page, handoff=False):
    inbox(page).get_by_role('button', name='인수인계 현황' if handoff else '회식 관리', exact=True).click()
    modal = page.locator(MANAGEMENT)
    expect(modal.get_by_role('heading', name='게시 전 초안')).to_have_count(0 if handoff else 1)
    modal.get_by_role('button', name='응답 현황').click()
    expect(modal.locator('.da-progress-track')).to_have_attribute('aria-label', '응답 완료 2명 / 3명')
    modal.locator('summary').filter(has_text='직원별 응답과 제안').click()
    expect(modal.get_by_text(ADMIN_NOTE, exact=True)).to_be_visible()
    expect(modal.get_by_text(VIEWER_NOTE, exact=True)).to_be_visible()
    if handoff:
        expect(modal.get_by_text('인수인계 · 조회 전용')).to_be_visible()
        expect(modal.locator('[data-da=close-poll]')).to_have_count(0)
    else:
        expect(modal.locator('[data-da=close-poll]')).to_be_visible()
    return modal


def master_version_panel(page):
    page.get_by_role('button', name='연결 정보', exact=True).first.click()
    info = page.locator('#serverinfo-modal')
    for selector in ('#master-program-info', '#master-version-detail'):
        details = info.locator(selector)
        if details.get_attribute('open') is None:
            details.locator(':scope > summary').click()
    return info


def close_server_info(page):
    info = page.locator('#serverinfo-modal')
    if info.is_visible():
        info.locator('.modal-head button').click()
        expect(info).to_be_hidden()


def check_admin_and_viewer(admin, viewer, runtime, password, path, captures):
    # Raw, normal application requests capture the actual HTTP status and response body.
    auth = {'X-Admin-Token': password}
    response = runtime.get('/api/dinner/manage', headers=auth)
    assert response.status_code == 403
    assert_private(response.json(), VIEWER_NOTE)
    roles = runtime.get('/api/dinner/role-management', headers=auth)
    roles.raise_for_status()
    assert {'roles', 'members', 'connections'} <= roles.json().keys()
    assert 'polls' not in roles.json()
    assert_private(roles.json(), VIEWER_NOTE)
    for page, owner, own_note, other_note in ((admin, ADMIN_NAME, ADMIN_NOTE, VIEWER_NOTE),
                                               (viewer, VIEWER_NAME, VIEWER_NOTE, ADMIN_NOTE)):
        denied(page, '/manage')
        body = api(page, path + '?name=' + owner)
        assert_private(body, other_note)
        assert body['poll']['ownResponse']['suggestion'] == own_note
        panel = inbox(page)
        expect(panel.get_by_role('button', name='회식 관리', exact=True)).to_have_count(0)
        if page == admin:
            expect(panel.get_by_role('button', name='CA 지정·연결 승인', exact=True)).to_be_visible()
            screenshot(page, captures, '01-admin-dinner-inbox')
        else:
            expect(panel.get_by_role('button', name='CA 지정·연결 승인', exact=True)).to_have_count(0)
        panel.locator('[data-dinner-action=poll]').click()
        expect(panel).to_contain_text(own_note)
        expect(panel).not_to_contain_text(other_note)
        if page == viewer:
            screenshot(page, captures, '03-viewer-own-response')
        close_dialogs(page)
        page.get_by_role('button', name='연결 정보', exact=True).first.click()
        expect(page.locator('#master-program-info')).to_be_hidden()
        expect(page.locator('#master-session-open')).to_be_hidden()
        close_server_info(page)


def run(captures):
    with tempfile.TemporaryDirectory(prefix='onair-master-browser-') as tmp, ExitStack() as stack:
        root = Path(tmp)
        share = root / 'shared'
        share.mkdir()
        db = share / 'smart_duty.db'
        sqlite3.connect(db).close()
        server_python = os.environ.get('ONAIR_SERVER_PYTHON') or sys.executable
        stack.enter_context(patch.dict(os.environ, {'ONAIR_SERVER_PYTHON': server_python}))
        real_popen, real_write = subprocess.Popen, configparser.ConfigParser.write
        password = secrets.token_urlsafe(24)

        def start(args, **options):
            # Only the dinner service's business date is fixed. Master authentication uses real time.
            freeze = ("from datetime import datetime\nfrom backend import dinner_service\n"
                      "real_dinner=dinner_service.DinnerService\n"
                      f"dinner_service.DinnerService=lambda cur,**kwargs: real_dinner(cur,now=datetime.fromisoformat({NOW.isoformat()!r}),**kwargs)\n")
            return real_popen([*args[:-1], freeze + args[-1]], **options)

        def configure(config, stream):
            if Path(stream.name).name == 'config_admin.ini':
                config['NETWORK']['admin_password'] = password
            return real_write(config, stream)

        def installed(label, mode):
            with patch('runtime.subprocess.Popen', side_effect=start), patch.object(configparser.ConfigParser, 'write', configure):
                instance = Runtime(root, label, mode, share)
            stack.callback(instance.close)
            return instance

        admin_runtime = installed('admin', 'ADMIN')
        version = admin_runtime.get('/api/duty/all-data').json()['dataVersion']
        admin_runtime.send('/api/duty/save-all', dict(baseVersion=version, **fixture()),
                           headers={'X-Admin-Token': password}).raise_for_status()
        pw = stack.enter_context(sync_playwright())
        chromium = os.environ.get('CHROMIUM_PATH') or shutil.which('chromium')
        browser = pw.chromium.launch(executable_path=chromium, args=['--no-sandbox'])
        stack.callback(browser.close)
        errors = []

        def open_page(runtime, name):
            context, page = runtime.open(browser, width=1440, height=1000, reduced=True)
            stack.callback(context.close)
            page.on('pageerror', lambda error: errors.append(str(error)))
            if name or runtime is admin_runtime:
                # The staff calendar must match the December handoff fixture.
                page.clock.set_fixed_time(NOW)
            if name:
                page.evaluate('(name) => setMyName(name, {remember:true})', name)
            return page

        admin = open_page(admin_runtime, '')
        admin.locator('#auth-status-badge').click()
        admin.locator('#admin-password-input').fill(password)
        admin.locator('#admin-password-input').press('Enter')
        expect(admin.locator('#auth-status-badge')).to_contain_text('암호 인증됨')
        admin.evaluate('(name) => setMyName(name, {remember:true})', ADMIN_NAME)
        viewer = open_page(installed('viewer', 'VIEWER'), VIEWER_NAME)
        operator = open_page(installed('operator', 'VIEWER'), OPERATOR)
        successor = open_page(installed('successor', 'VIEWER'), SUCCESSOR)

        setup_roles(admin, operator, successor, captures)
        print('PASS authenticated Admin appointment and connection approval; pending CA denied', flush=True)
        path, revision = create_polls(operator)
        vote(admin, 'possible', ADMIN_NOTE)
        vote(viewer, 'maybe', VIEWER_NOTE)
        check_admin_and_viewer(admin, viewer, admin_runtime, password, path, captures)
        print('PASS Admin and Viewer own answers; direct authenticated Admin management HTTP 403, clean body and role-only response', flush=True)
        open_results(operator)
        screenshot(operator, captures, '04-approved-ca-results')
        open_results(successor, handoff=True)
        screenshot(successor, captures, '05-handoff-read-only')
        denied(successor, path + '/action', {'action': 'CLOSE', 'expectedRevision': revision})
        print('PASS approved CA sees aggregate and operates; annual handoff sees published results read-only', flush=True)

        master_runtime = installed('master', 'MASTER')
        master = open_page(master_runtime, '')
        # Enrollment uses the same operator utility that an authorized database owner runs.
        sys.path.insert(0, str(ROOT))
        from backend.master_auth import provision
        from backend.request_access_store import ReceiptVault
        device_id = ReceiptVault(root / 'master').device_id
        before = master_version_panel(master)
        expect(before.locator('#master-device-info')).to_contain_text(device_id)
        expect(before.locator('#master-session-open')).to_be_hidden()
        close_server_info(master)
        code = provision(db, device_id)
        denied(master, '/manage')
        expect(inbox(master).get_by_role('button', name='회식 관리', exact=True)).to_have_count(0)
        close_dialogs(master)
        expect(master.get_by_role('button', name='관리 세션 열기')).to_have_count(0)
        version_panel = master_version_panel(master)
        version_panel.locator('#master-session-open').click()
        form = master.locator('#master-session-dialog')
        expect(form.locator('#master-password')).to_have_value('')
        expect(form.locator('#master-code')).to_have_value('')
        screenshot(master, captures, '06-master-enrollment')
        master_password = secrets.token_urlsafe(24)
        form.locator('#master-code').fill(code)
        form.locator('#master-password').fill(master_password)
        form.locator('#master-confirm').fill(master_password)
        form.locator('#master-submit').click()
        expect(form).to_be_hidden()
        version_panel.locator('#master-session-lock').click()
        denied(master, '/manage')
        version_panel.locator('#master-session-open').click()
        expect(form.locator('#master-code')).to_have_count(0)
        expect(form.locator('#master-password')).to_have_value('')
        screenshot(master, captures, '06b-master-password')
        form.locator('#master-password').fill(password)
        form.locator('#master-submit').click()
        expect(form.locator('#master-error')).not_to_have_text('')
        denied(master, '/manage')
        form.locator('#master-password').fill(master_password)
        form.locator('#master-submit').click()
        expect(form).to_be_hidden()
        expect(version_panel.locator('#master-session-lock')).to_be_visible()
        close_server_info(master)
        separate_session = open_page(master_runtime, '')
        denied(separate_session, '/manage')
        data = api(master, '/manage')
        poll = next(p for p in data['polls'] if p['title'] == TITLE)
        assert {r['name'] for r in poll['results']['responders']} == {ADMIN_NAME, VIEWER_NAME}
        assert poll['results']['nonresponders'] == [UNANSWERED]
        open_results(master)
        screenshot(master, captures, '07-master-aggregate')
        close_dialogs(master)
        version_panel = master_version_panel(master)
        version_panel.locator('#master-session-lock').click()
        expect(master.locator('#master-session-lock')).to_be_hidden()
        denied(master, '/manage')
        version_panel.locator('#master-session-open').click()
        form.locator('#master-password').fill(master_password)
        form.locator('#master-submit').click()
        expect(form).to_be_hidden()
        expect(version_panel.locator('#master-session-lock')).to_be_visible()
        close_server_info(master)
        results = open_results(master)
        provision(db, device_id, revoke=True)
        # An owner revokes this disposable PC while the aggregate is already on screen.
        # The frontend's periodic session check must clear both rendered and hidden content.
        expect(results).to_be_hidden(timeout=18000)
        expect(results).not_to_contain_text(VIEWER_NOTE)
        denied(master, '/manage')
        assert not errors, errors
        print('PASS Master enrollment, dedicated password, independent session, explicit lock, live owner revocation and removal of open results; no page errors', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--captures', type=Path, help='Optional directory for synthetic screenshots')
    args = parser.parse_args()
    if args.captures:
        args.captures.mkdir(parents=True, exist_ok=True)
    run(args.captures)
