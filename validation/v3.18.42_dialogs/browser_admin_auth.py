"""Exercise the administrator header against the real application and temporary data."""
import configparser
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import traceback
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT.parent / 'v3.18.41_details'))
from runtime import Runtime, fixture

PASSWORD = 'synthetic-admin-auth-v42'
VERIFY = '/api/system/admin-auth'
checks = []
page_errors = []


def check(name, value):
    checks.append({'name': name, 'passed': bool(value)})
    print(('PASS ' if value else 'FAIL ') + name, flush=True)
    assert value, name


def seed_protected(admin):
    before = admin.get('/api/duty/all-data').json()
    admin.send('/api/duty/save-all', dict(baseVersion=before['dataVersion'], **fixture()),
               headers={'X-Admin-Token': PASSWORD}).raise_for_status()


def check_header(browser, admin, width, height):
    context, page = admin.open(browser, width=width, height=height)
    try:
        page.on('pageerror', lambda error: page_errors.append(str(error)))
        badge = page.locator('#auth-status-badge')
        expect(badge).to_be_visible()
        auth = page.get_by_role('button', name='암호 인증', exact=False)
        check(f'{width}: 관리자 헤더에서 암호 인증 버튼이 보임', auth.count() == 1)
        auth = badge
        password = page.locator('#admin-password-input')
        modal = page.locator('#admin-password-modal')
        error = modal.locator('#admin-password-error')
        check(f'{width}: 인증 없이 올바른 방식의 호출도 401', admin.send(VERIFY).status_code == 401)
        before = admin.get('/api/duty/all-data').json()
        auth.focus()
        auth.press('Enter')
        expect(password).to_be_focused()
        check(f'{width}: 키보드로 암호 입력창 열기 및 비밀번호 가리기', password.get_attribute('type') == 'password')
        page.screenshot(path=str(OUT / f'admin-password-{width}.png'), animations='disabled')
        password.press('Enter')
        expect(error).to_contain_text('암호를 입력하세요')
        page.keyboard.press('Escape')
        expect(modal).to_be_hidden()
        expect(auth).to_be_focused()
        check(f'{width}: 취소하면 헤더로 돌아오고 인증 상태 유지', page.evaluate('!ADMIN_TOKEN && !sessionStorage.getItem("sd_admin_token")'))
        auth.click()
        password.fill('incorrect-test-password')
        password.press('Enter')
        expect(error).to_contain_text('올바르지 않습니다')
        expect(password).to_be_focused()
        check(f'{width}: 틀린 암호는 저장하지 않고 같은 입력창에서 재시도', password.input_value() == '' and page.evaluate('!ADMIN_TOKEN && !sessionStorage.getItem("sd_admin_token")'))
        password.fill(PASSWORD)
        modal.get_by_role('button', name='확인', exact=True).click()
        expect(modal).to_be_hidden()
        expect(auth).to_contain_text('암호 인증됨')
        check(f'{width}: 올바른 암호로 보호된 실제 API 사용 가능', page.evaluate('async () => (await fetchWithAdminToken("/api/duty/requests")).ok'))
        check(f'{width}: 인증 요청으로 당직 자료 버전과 신청은 그대로', admin.get('/api/duty/all-data').json()['dataVersion'] == before['dataVersion'])
        page.screenshot(path=str(OUT / f'authenticated-{width}.png'), animations='disabled')
        auth.click()
        expect(password).to_be_focused()
        expect(error).to_be_hidden()
        modal.get_by_role('button', name='취소', exact=True).click()
        expect(modal).to_be_hidden()
        expect(auth).to_contain_text('암호 인증됨')
        check(f'{width}: 인증 후 다시 누르면 암호 창이 열리고 취소 시 기존 인증 유지', page.evaluate('!!ADMIN_TOKEN'))
    finally:
        context.close()


def main():
    runtimes = []
    fatal = None
    try:
        with tempfile.TemporaryDirectory(prefix='onair42-auth-') as temporary:
            root = Path(temporary)
            shared = root / 'shared'
            shared.mkdir()
            sqlite3.connect(shared / 'smart_duty.db').close()
            original_write = configparser.ConfigParser.write

            def protected_write(config, *args, **kwargs):
                config['NETWORK']['admin_password'] = PASSWORD
                return original_write(config, *args, **kwargs)

            with patch.object(configparser.ConfigParser, 'write', protected_write):
                admin = Runtime(root, 'admin-protected', 'ADMIN', shared)
                runtimes.append(admin)
            seed_protected(admin)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
                                                      args=['--no-sandbox'])
                for width, height in ((1366, 768), (1920, 1080)):
                    check_header(browser, admin, width, height)
                viewer = Runtime(root, 'viewer', 'VIEWER', shared)
                runtimes.append(viewer)
                context, page = viewer.open(browser)
                expect(page.locator('#auth-status-badge')).to_be_hidden()
                check('조회용에서는 버튼을 표시하지 않으며 API가 권한을 거절', viewer.send(VERIFY, headers={'X-Admin-Token': PASSWORD}).status_code == 403)
                context.close()
                unprotected = Runtime(root, 'admin-unprotected', 'ADMIN', shared)
                runtimes.append(unprotected)
                context, page = unprotected.open(browser)
                check('암호가 설정되지 않은 관리자 화면의 버튼은 숨김', not page.locator('#auth-status-badge').is_visible())
                context.close()
                check('브라우저 오류 없음', not page_errors)
                browser.close()
    except Exception:
        fatal = traceback.format_exc()
        print(fatal, flush=True)
    finally:
        for runtime in reversed(runtimes):
            runtime.close()
        (OUT / 'browser-auth.json').write_text(json.dumps({'checks': checks, 'page_errors': page_errors, 'fatal': fatal},
                                                         ensure_ascii=False, indent=2), encoding='utf-8')
    return 1 if fatal or page_errors else 0


if __name__ == '__main__':
    sys.exit(main())
