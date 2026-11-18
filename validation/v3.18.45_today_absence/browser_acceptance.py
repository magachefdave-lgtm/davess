"""Exercise the real Admin and Viewer pages with a temporary shared database."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
import argparse
import sqlite3
import sys
import tempfile

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v3.18.41_details'))
from runtime import Runtime, fixture

DAY = datetime.now(timezone(timedelta(hours=9))).date()
STAFF = ['김도윤', '제갈유진', '이준호', '김하늘', '박민수', '박지은', '정우진', '정유나', '한지훈', '한나래']
TYPES = ['연차/휴가', '교육', '연차/휴가', '연차/휴가', '교육', '출장', '연차/휴가', '교육', '연차/휴가', '연차/휴가']
LIST = '#today-vacation-container'
VIEWPORTS = [(1920, 1080), (1366, 768)]


def save_data(admin, data, count, same_type=False):
    data['absences'] = [
        dict(id=i + 1, name=name, type='연차/휴가' if same_type else TYPES[i % len(TYPES)],
             start=DAY.isoformat(), end=DAY.isoformat()) for i, name in enumerate(STAFF[:count])
    ]
    data['absences'].append(dict(id=100, name='최현우', type='파견',
                                 start=(DAY - timedelta(days=25)).isoformat(), end=(DAY + timedelta(days=25)).isoformat()))
    admin.send('/api/duty/save-all', dict(baseVersion=admin.get('/api/duty/all-data').json()['dataVersion'],
                                         **data)).raise_for_status()


def check_names(page, count):
    names = page.locator(LIST + ' [data-oa-action="absence"]')
    expect(names).to_have_count(count)
    assert set(names.all_text_contents()) == set(STAFF[:count])
    expect(page.locator('#today-vacation-count')).to_have_text(f'{count}명')
    if not count:
        expect(page.locator(LIST)).to_contain_text('없습니다')
        return
    geometry = page.evaluate("""() => [...document.querySelectorAll('#today-vacation-container [data-oa-action=absence]')].map(b => {
      const r=b.getBoundingClientRect(), parent=b.closest('#today-vacation-container').getBoundingClientRect();
      const name=b.querySelector('span')||b, range=document.createRange(); range.selectNodeContents(name);
      const label=range.getBoundingClientRect();
      return {x:r.left,y:r.top,w:r.width,h:r.height,bottom:r.bottom,labelW:label.width,labelH:label.height,dy:label.top-r.top,dx:label.left-r.left,
        fits:r.top>=parent.top-.5 && r.bottom<=parent.bottom+.5 && r.left>=parent.left-.5 &&
          r.right<=parent.right+.5 && label.left>=r.left-.5 && label.right<=r.right+.5 &&
          label.top>=r.top-.5 && label.bottom<=r.bottom+.5};
    })""")
    assert all(b['fits'] for b in geometry), geometry
    if count == 2:
        first, second = geometry
        assert abs(second['y'] - first['bottom'] - 3) < .05, geometry
        assert all(abs(first[k] - second[k]) < .05 for k in ('x','w','h')), geometry
        assert 20 <= first['h'], geometry
    if count in (3, 4):
        assert len({b['x'] for b in geometry}) == 1, geometry


def check_style(page):
    # Use a detached-from-layout copy of the real duty tile with the absence palette.
    # Comparing computed properties catches hover rules, theme overrides and radius drift.
    differences = page.locator(LIST + ' [data-oa-action="absence"]').evaluate_all("""buttons => {
      const source = document.querySelector('#today-person-container .today-person');
      const dutyLabel = getComputedStyle(source.querySelector('.person-name'));
      const probe = source.cloneNode(false);
      probe.style.position = 'absolute';
      probe.style.visibility = 'hidden';
      source.after(probe);
      try {
        return buttons.flatMap(button => {
          const css = getComputedStyle(button);
          const label = getComputedStyle(button.querySelector('span') || button);
          const prefix = button.classList.contains('kind-short') ? '--short-absence' : '--vacation';
          probe.style.setProperty('--person-tint', 'var(' + prefix + '-bg)');
          probe.style.setProperty('--person-glow', 'color-mix(in srgb, var(' + prefix + '-text) 60%, transparent)');
          probe.style.color = 'var(' + prefix + '-text)';
          const duty = getComputedStyle(probe);
          const mismatch = ['backgroundImage','borderRadius','borderWidth','boxShadow','color']
            .filter(k => css[k] !== duty[k]).map(k => [k, css[k], duty[k]]);
          for (const k of ['fontWeight','textAlign']) {
            if (label[k] !== dutyLabel[k]) mismatch.push([k, label[k], dutyLabel[k]]);
          }
          if (Math.abs(parseFloat(label.letterSpacing)/parseFloat(label.fontSize) -
                       parseFloat(dutyLabel.letterSpacing)/parseFloat(dutyLabel.fontSize)) > 0.001)
            mismatch.push(['letterSpacing', label.letterSpacing, dutyLabel.letterSpacing]);
          if (!css.backgroundImage.includes('linear-gradient')) mismatch.push(['gradient', css.backgroundImage]);
          return mismatch;
        });
      } finally { probe.remove(); }
    }""")
    assert not differences, differences


def verify_click(page, name):
    trigger = page.locator(LIST).get_by_role('button', name=name, exact=False)
    trigger.click()
    dialog = page.locator('[role=dialog]:visible').last
    expect(dialog).to_contain_text(name)
    page.keyboard.press('Escape')
    expect(dialog).to_be_hidden()
    expect(trigger).to_be_focused()


def run(captures):
    data = fixture()
    data['members'][1]['name'] = '제갈유진'
    data['globalMemo'] = ''
    tomorrow = (DAY + timedelta(days=1)).isoformat()
    data['schedules'] = {DAY.isoformat(): [dict(name=n, role='당직') for n in ['최현우','최수빈']],
                         tomorrow: [dict(name='김하늘', role='당직')]}
    data['comments'] = {DAY.isoformat():'부서 주간 회의'}
    counts = list(range(11)) + [2, 1, 0]
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw, ExitStack() as stack:
        base = Path(tmp)
        shared = base / 'shared'
        shared.mkdir()
        sqlite3.connect(shared / 'smart_duty.db').close()
        admin = Runtime(base, 'admin', 'ADMIN', shared)
        stack.callback(admin.close)
        save_data(admin, data, 0)
        viewer = Runtime(base, 'viewer', 'VIEWER', shared)
        stack.callback(viewer.close)
        browser = pw.chromium.launch(executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        class RetinaBrowser:
            def new_context(self, **options):
                return browser.new_context(device_scale_factor=2, **options)
        pages = {}
        errors = []
        for role, app in [('admin', admin), ('viewer', viewer)]:
            for width, height in VIEWPORTS:
                ctx, page = app.open(RetinaBrowser(), name='김하늘', width=width, height=height)
                stack.callback(ctx.close)
                page.on('pageerror', lambda exc: errors.append(str(exc)))
                pages[role,width] = page
        for idx,count in enumerate(counts):
            save_data(admin, data, count, same_type=(idx > 10))
            for (role,width),page in pages.items():
                assert page.evaluate('fetchAllData()')
                check_names(page, count)
                if count in (1, 2, 4, 10):
                    check_style(page)
                if count in (2, 6, 10):
                    for name in (STAFF[:2] if count == 2 else STAFF[count - 1:count]):
                        verify_click(page, name)
                if captures and role == 'viewer' and count in (2,4,8):
                    label = f'{width}-{count}' + ('-same' if idx > 10 else '')
                    page.mouse.move(0,0)
                    page.evaluate('document.activeElement?.blur()')
                    page.locator('.today-summary-card').screenshot(path=captures/f'{label}-card.png')
                    page.locator('.today-current-split').screenshot(path=captures/f'{label}-detail.png')
        save_data(admin, data, 2)
        for (role, width), page in pages.items():
            assert page.evaluate('fetchAllData()')
            page.evaluate("document.documentElement.dataset.theme='dark'")
            page.locator(LIST + ' button').first.hover()
            page.locator(LIST + ' button').evaluate_all("""buttons =>
                Promise.all(buttons.flatMap(b => b.getAnimations()).map(a => a.finished))""")
            check_style(page)
            if captures and (role,width) == ('viewer',1920):
                page.mouse.move(0,0)
                page.locator('.today-current-split').screenshot(path=captures/'1920-2-dark-detail.png')
            page.evaluate("document.documentElement.removeAttribute('data-theme')")
        # Reflow the panel while the underlying list and browser session stay live.
        save_data(admin, data, 10)
        page = pages['viewer',1920]
        assert page.evaluate('fetchAllData()')
        for width,height in reversed(VIEWPORTS):
            page.set_viewport_size(dict(width=width,height=height))
            page.evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
            check_names(page, 10)
        # A larger roster that cannot fit retains access to the remaining names.
        for i in range(32):
            data['members'].append(dict(id=1000+i,name='사원'+str(i+1),gender='F'))
        save_data(admin, data, 10)
        data['absences'].extend(dict(id=200+i,name='사원'+str(i+1),type='교육',start=DAY.isoformat(),end=DAY.isoformat()) for i in range(32))
        admin.send('/api/duty/save-all',dict(baseVersion=admin.get('/api/duty/all-data').json()['dataVersion'],**data)).raise_for_status()
        assert page.evaluate('fetchAllData()')
        more = page.locator(LIST+' [data-oa-action="all-vacation"]')
        more.click()
        expect(page.locator('[role=dialog]:visible').last).to_contain_text('사원32')
        assert not errors, errors
        print('PASS: 56 population/render checks, shared duty/absence styles and theme colors, separate 3px-gap pairs, detail/focus, resize and overflow.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--captures',type=Path)
    args=parser.parse_args()
    if args.captures:
        args.captures.mkdir(parents=True,exist_ok=True)
    run(args.captures)
