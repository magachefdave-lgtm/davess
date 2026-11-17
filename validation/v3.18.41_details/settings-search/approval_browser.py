"""Audit item 3: search real pending requests using the dates shown in each row.

Only temporary synthetic config/SQLite profiles are written. RED serves the
audited JavaScript; GREEN serves the current source with identical API records.
"""
from pathlib import Path
from datetime import date
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).parent
sys.path.insert(0, str(ROOT / 'validation/v3.18.40_ca_years'))
os.environ['ONAIR_SERVER_PYTHON'] = sys.executable
from runtime import Runtime, NAMES
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['red', 'green'])
    args = parser.parse_args()
    evidence = dict(phase=args.phase, checks=[], searches=[], errors=[])
    runtime = None

    def check(name, passed, detail=None):
        evidence['checks'].append(dict(name=name, passed=bool(passed), detail=detail))

    def ok(response):
        response.raise_for_status()
        return response.json()

    try:
        with tempfile.TemporaryDirectory(prefix='onair-approval-search-31841-') as tmp:
            base = Path(tmp)
            shared = base / 'shared'
            shared.mkdir()
            sqlite3.connect(shared / 'smart_duty.db').close()
            runtime = Runtime(base, 'admin', 'ADMIN', shared)
            runtime.seed()
            year = date.today().year + 1

            def request(name, start, end, reason='합성 교육 신청'):
                dto = dict(requester_name=name, request_type='ABSENCE', absence_type='교육',
                           target_date=start, absence_end_date=end, reason=reason,
                           absence_details=dict(title='업무 안전 연수', notes='회의 자료 준비'))
                return dto, ok(runtime.send('/api/duty/request-change', dto))

            def approve(request):
                row = next(row for row in ok(runtime.get('/api/duty/requests'))['requests']
                           if row['id'] == request['requestId'])
                ok(runtime.send('/api/duty/request/action', dict(request_id=row['id'], action='APPROVE',
                    request_revision=row['revision'], impact_digest=row['impact_digest'])))

            _, new = request(NAMES[0], f'{year}-01-05', f'{year}-01-08')
            old_dto, old = request(NAMES[1], f'{year}-02-01', f'{year}-02-02')
            approve(old)
            amended = ok(runtime.send('/api/duty/request/manage', dict(receipt_token=old['receiptToken'],
                action='AMEND', request=dict(old_dto, target_date=f'{year}-02-03', absence_end_date=f'{year}-02-06'))))
            _, old_cancel = request(NAMES[2], f'{year}-03-01', f'{year}-03-04')
            approve(old_cancel)
            cancelled = ok(runtime.send('/api/duty/request/manage', dict(receipt_token=old_cancel['receiptToken'],
                action='CANCEL_ABSENCE', reason='교육 일정 취소')))
            substitute = ok(runtime.send('/api/duty/request-change', dict(requester_name=NAMES[5],
                request_type='SUBSTITUTE', target_date=f'{year}-04-03', substitute_name=NAMES[6],
                is_zero_score=False, reason='점수 반영 선택 검증')))
            before = ok(runtime.get('/api/duty/requests'))['requests']
            education_ids = sorted(row['id'] for row in before if row['status'] == 'PENDING'
                                   and (row.get('absence_details') or {}).get('title') == '업무 안전 연수')
            evidence['fixture'] = [dict(id=row['id'], kind=row['request_type'], status=row['status'],
                                       education=row.get('absence_details')) for row in before]
            baseline = subprocess.check_output(['git', 'show', 'dc51d7e:frontend/dist/usability_requests.js'], cwd=ROOT).decode()
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
                                            args=['--no-sandbox'])
                for width, height in [(1920, 1080), (1366, 768), (1024, 768)]:
                    context = browser.new_context(viewport=dict(width=width, height=height),
                                                  locale='ko-KR', timezone_id='Asia/Seoul')
                    if args.phase == 'red':
                        context.route('**/usability_requests.js*', lambda route: route.fulfill(
                            status=200, content_type='text/javascript', body=baseline))
                    page = context.new_page()
                    page.on('pageerror', lambda error: evidence['errors'].append(str(error)))
                    page.goto(runtime.url + '/#onair_access=' + runtime.tickets.pop())
                    page.wait_for_function('dataLoaded && memberList.length === 12')
                    page.evaluate('document.fonts.ready')
                    page.evaluate('openApprovalModal()')
                    search = page.locator('#ur-approval-search')
                    search.wait_for()
                    page.screenshot(path=str(OUT / f'approval-{args.phase}-{width}-all.png'), animations='disabled')
                    geom = page.locator('#approval-modal .modal-body').evaluate('(n)=>({width:n.clientWidth,scrollWidth:n.scrollWidth})')
                    check(f'{width} dialog has no horizontal overflow', geom['scrollWidth'] <= geom['width'] + 1, geom)
                    cases = [('new-start', new['requestId'], f'{year}-01-05'),
                             ('new-end', new['requestId'], f'{year}-01-08'),
                             ('old-start', amended['requestId'], f'{year}-02-01'),
                             ('old-end', amended['requestId'], f'{year}-02-02'),
                             ('changed-start', amended['requestId'], f'{year}-02-03'),
                             ('changed-end', amended['requestId'], f'{year}-02-06'),
                             ('cancel-start', cancelled['requestId'], f'{year}-03-01'),
                             ('cancel-end', cancelled['requestId'], f'{year}-03-04')]
                    weekdays = ['월', '화', '수', '목', '금', '토', '일']
                    for label, request_id, day in cases:
                        row = page.locator(f'tr[data-ur-request-id="{request_id}"]')
                        search.fill('')
                        displayed = day + '(' + weekdays[date.fromisoformat(day).weekday()] + ')'
                        check(f'{width} {label} fixture appears with weekday', displayed in row.inner_text(), row.inner_text())
                        for query in [day, displayed]:
                            search.focus()
                            search.press('Control+A')
                            search.press('Backspace')
                            page.keyboard.type(query)
                            visible = page.locator('#approval-tbody tr[data-ur-request-id]:visible').evaluate_all('(rows)=>rows.map(row=>Number(row.dataset.urRequestId))')
                            evidence['searches'].append(dict(width=width, kind=label, query=query, visible=visible, expected=[request_id]))
                            check(f'{width} {label} search {query}', visible == [request_id], visible)
                            check(f'{width} {label} search keeps keyboard focus', search.evaluate('(n)=>document.activeElement===n'))
                    search.fill(f'{year}-02-02')
                    page.screenshot(path=str(OUT / f'approval-{args.phase}-{width}-original-end.png'), animations='disabled')
                    search.fill('업무 안전 연수')
                    education_matches = page.locator('#approval-tbody tr[data-ur-request-id]:visible').evaluate_all('(rows)=>rows.map(row=>Number(row.dataset.urRequestId)).sort((a,b)=>a-b)')
                    check(f'{width} education value stays searchable', education_matches == education_ids,
                          dict(actual=education_matches, expected=education_ids))
                    search.fill('"title"')
                    check(f'{width} JSON field names do not create matches', page.locator('#approval-tbody tr[data-ur-request-id]:visible').count() == 0)
                    search.fill('')
                    score = page.locator(f'#req-zero-{substitute["requestId"]}')
                    score.focus()
                    page.keyboard.press('Space')
                    check(f'{width} keyboard toggles substitute draft', score.is_checked())
                    search.fill(f'{year}-01-08')
                    search.fill('')
                    page.evaluate('openApprovalModal()')
                    check(f'{width} search and refresh retain substitute draft', score.is_checked())
                    search.fill(NAMES[0])
                    action = page.get_by_role('button', name=NAMES[0] + ' 요청 승인', exact=True)
                    action.focus()
                    page.keyboard.press('Enter')
                    page.locator('#oa-confirm-dialog.active').wait_for()
                    page.keyboard.press('Escape')
                    check(f'{width} keyboard decision cancellation retains pending list', page.locator('#approval-modal.active').count() == 1)
                    context.close()
                browser.close()
            after = ok(runtime.get('/api/duty/requests'))['requests']
            check('filtering and cancelling approval confirmation keep server requests unchanged', before == after)
    except Exception:
        evidence['fatal'] = traceback.format_exc()
    finally:
        if runtime:
            runtime.close()
        (OUT / f'approval-{args.phase}.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    failed = sum(not check['passed'] for check in evidence['checks'])
    print(json.dumps(dict(phase=args.phase, checks=len(evidence['checks']), failed=failed,
                          errors=evidence['errors'], fatal=evidence.get('fatal')), ensure_ascii=False))
    return 1 if evidence.get('fatal') or evidence['errors'] or (args.phase == 'green' and failed) else 0


if __name__ == '__main__':
    raise SystemExit(main())
