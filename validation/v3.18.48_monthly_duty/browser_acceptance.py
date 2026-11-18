"""Check the real monthly duty panel on Admin and Viewer with synthetic staff."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v3.18.41_details'))
from runtime import Runtime, fixture

BUTTONS = '#member-duty-buttons .member-duty-btn'
MONTH = '2028-10'
VIEWPORTS = [(1920, 1080), (1600, 900), (1440, 900), (1366, 768)]


def monthly_fixture():
    data = fixture()
    data['members'][1]['name'] = '제갈유진'
    for day in range(1, 32):
        names = [data['members'][i]['name'] for i in (day % 10, (day + 5) % 10)]
        if day in (1, 2, 9, 14, 22) and '김도윤' not in names:
            names[0] = '김도윤'
        data['schedules'][f'{MONTH}-{day:02}'] = [dict(name=name, role='당직') for name in names]
    today = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
    data['schedules'][today] = [dict(name=name, role='당직') for name in ('김도윤', '제갈유진')]
    data['globalMemo'] = ''
    return data


def check_geometry(page, count):
    buttons = page.locator(BUTTONS)
    expect(buttons).to_have_count(count)
    errors = buttons.evaluate_all("""buttons => buttons.flatMap(b => {
      const box = b.getBoundingClientRect(), grid = b.parentElement.getBoundingClientRect();
      const label = b.querySelector('.member-duty-name'), row = b.querySelector('.member-duty-dates');
      const name = label.getBoundingClientRect(), dates = row.getBoundingClientRect();
      const fit = box.left >= grid.left-1 && box.right <= grid.right+1 && box.top >= grid.top-1 &&
        box.bottom <= grid.bottom+1 && name.bottom <= dates.top+0.5 && name.top >= box.top-0.5 &&
        dates.bottom <= box.bottom+0.5 && dates.height >= 10 && label.clientWidth >= label.scrollWidth &&
        Math.abs((name.left + name.right) - (box.left + box.right)) < 2;
      return fit ? [] : [[b.dataset.member, box.height, name.height, dates.top-name.bottom, dates.height,
                           box.bottom-dates.bottom]];
    })""")
    assert not errors, errors


def check_style(page):
    for gender in ('male', 'female'):
        equal = page.locator(f'{BUTTONS}.{gender}').first.evaluate("""button => {
          const gender = button.classList.contains('male') ? 'male' : 'female';
          const duty = document.querySelector('.today-person.' + gender + '-card');
          const reference = getComputedStyle(duty), css = getComputedStyle(button);
          return css.backgroundImage.includes('linear-gradient') &&
            ['backgroundImage','borderRadius','boxShadow'].every(k => css[k] === reference[k]) &&
            getComputedStyle(button.querySelector('.member-duty-name')).color ===
              getComputedStyle(duty.querySelector('.person-name')).color;
        }""")
        assert equal, gender


def check_selection(page, button, data, keyboard=False):
    name = button.get_attribute('data-member')
    if keyboard:
        button.focus()
        button.press('Enter')
        assert button.evaluate("b => b.matches(':focus-visible') && getComputedStyle(b).outlineStyle === 'dashed'")
    else:
        button.click()
    expect(button).to_have_attribute('aria-pressed','true')
    expect(page.locator(BUTTONS + '.active')).to_have_count(1)
    selected = page.locator('td.member-duty-highlight').evaluate_all('cells=>cells.map(c=>c.dataset.dateKey)')
    assert sorted(selected) == sorted(k for k, workers in data['schedules'].items()
                                      if k.startswith(MONTH) and any(w['name'] == name for w in workers))
    assert button.evaluate("b => getComputedStyle(b).boxShadow.includes('2px inset')")


def run(captures):
    data = monthly_fixture()
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw, ExitStack() as stack:
        root = Path(tmp)
        share = root / 'shared'
        share.mkdir()
        sqlite3.connect(share / 'smart_duty.db').close()
        admin = Runtime(root, 'admin', 'ADMIN', share)
        stack.callback(admin.close)

        def save():
            version = admin.get('/api/duty/all-data').json()['dataVersion']
            admin.send('/api/duty/save-all', dict(baseVersion=version, **data)).raise_for_status()
        save()
        viewer = Runtime(root, 'viewer', 'VIEWER', share)
        stack.callback(viewer.close)
        browser = pw.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        stack.callback(browser.close)
        pages = {}
        for role, app in [('admin',admin),('viewer',viewer)]:
            context, page = app.open(browser, width=1920, height=1080, reduced=True)
            stack.callback(context.close)
            pages[role] = page

        for total in (12,16):
            if total == 16:
                for i,name in enumerate(('황다은','한정민','고은우','이서우')):
                    data['members'].append(dict(id=100+i, name=name, gender='F' if i%2 else 'M'))
                save()
            for role,page in pages.items():
                page.evaluate('fetchAllData()')
                page.evaluate('curYear=2028;curMonth=10;updateYearMonthUI();renderCalendar()')
                for width,height in VIEWPORTS:
                    page.set_viewport_size(dict(width=width, height=height))
                    page.evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                    page.evaluate('fitMemberDutyDates()')
                    for theme in ('light','dark'):
                        page.evaluate("t=>{document.documentElement.dataset.theme=t;document.getElementById('member-search-input').value='';executeMemberSearch()}",theme)
                        page.mouse.move(0,0)
                        check_geometry(page,total)
                        check_style(page)
                        first = page.locator(BUTTONS + '[data-member="김도윤"]')
                        assert first.locator('.md-day.maj').count() and first.locator('.md-day.hol').count()
                        expect(page.locator(BUTTONS + ' .md-none').first).to_have_text('배정 없음')
                        check_selection(page,first,data)
                        if captures and total == 12 and width in (1920,1366):
                            page.mouse.move(0,0)
                            page.locator('.member-duty-card').screenshot(path=captures/f'{role}-{width}-{theme}.png')
                        check_selection(page,page.locator(BUTTONS + '[data-member="제갈유진"]'),data,keyboard=True)
        print('PASS: 32 role/viewport/roster/theme combinations, duty styles, holiday labels, name/date layout, pointer and keyboard selection with calendar highlights.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--captures',type=Path)
    args=parser.parse_args()
    if args.captures:
        args.captures.mkdir(parents=True,exist_ok=True)
    run(args.captures)
