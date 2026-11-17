"""Real Chromium regression for shared legacy/dynamic modal keyboard lifecycle.
Synthetic temporary SQLite and installed profile only; no final business save.
"""
import argparse, configparser, json, sqlite3, sys, tempfile, traceback
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'validation/v3.18.40_ca_years'))
from runtime import Runtime,fixture
OUT=Path(__file__).parent
PASSWORD='modal-regression-password'
parser=argparse.ArgumentParser();parser.add_argument('--baseline',action='store_true');parser.add_argument('--focus-return-only',action='store_true');parser.add_argument('--calendar-escape-only',action='store_true');args=parser.parse_args()
checks=[];errors=[]
def check(name, passed, detail=None):
 checks.append(dict(name=name,passed=bool(passed),detail=detail));print(('PASS ' if passed else 'FAIL ')+name,flush=True)
def state(p):
 return p.evaluate('''()=>({focus:document.activeElement.id||document.activeElement.tagName,modal:document.activeElement.closest('.modal')?.id||null,open:[...document.querySelectorAll('.modal')].filter(n=>isModalOpen(n.id)).map(n=>n.id),top:window.OnAirUI?.top?.()?.id||null})''')
def menu(p,selector):
 target=p.locator(selector).first
 if not target.is_visible():target.locator('xpath=ancestor::*[contains(@class,"menu-pop-wrap")]').locator('.menu-pop-btn').click()
 target.click()
def trap(p,id,label):
 focusable='''n=>[...n.querySelectorAll('button,input,select,textarea,a[href],summary,[tabindex]')].filter(e=>!e.disabled&&e.tabIndex>=0&&!e.closest('[inert]')&&e.getClientRects().length)'''
 p.locator('#'+id).evaluate('n=>('+focusable+')(n).at(-1)?.focus()');p.keyboard.press('Tab')
 check(label+' last Tab remains in modal',state(p)['modal']==id,state(p))
 p.locator('#'+id).evaluate('n=>('+focusable+')(n)[0]?.focus()');p.keyboard.press('Shift+Tab')
 check(label+' first Shift+Tab remains in modal',state(p)['modal']==id,state(p))
 p.evaluate('document.activeElement.blur()');p.keyboard.press('Tab')
 check(label+' lost focus Tab recovers into modal',state(p)['modal']==id,state(p))
try:
 with tempfile.TemporaryDirectory(prefix='onair-341-modal-') as tmp,sync_playwright() as pw:
  base=Path(tmp);shared=base/'synthetic-shared';shared.mkdir();sqlite3.connect(shared/'smart_duty.db').close()
  native=configparser.ConfigParser.write
  def write(cfg,*a,**kw):cfg['NETWORK']['admin_password']=PASSWORD;return native(cfg,*a,**kw)
  with patch.object(configparser.ConfigParser,'write',write):r=Runtime(base,'admin','ADMIN',shared)
  r.send('/api/duty/save-all',dict(baseVersion=r.get('/api/duty/all-data').json()['dataVersion'],**fixture()),headers={'X-Admin-Token':PASSWORD}).raise_for_status()
  b=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  try:
   for w,h in ((1366,768),(1920,1080)):
    if args.calendar_escape_only:break
    ctx,p=r.open(b,width=w,height=h);p.on('pageerror',lambda e:errors.append(str(e)))
    rule='button[onclick="openRuleModal()"]'
    if args.focus_return_only:
     member='button[onclick="openMemberModal()"]';menu(p,member);p.keyboard.press('Escape');p.wait_for_timeout(80)
     check(f'{w}: hidden menu opener returns to visible menu toggle',p.locator(member).evaluate('n=>n.closest(".menu-pop-wrap").querySelector(".menu-pop-btn")===document.activeElement'),state(p));ctx.close();continue
    menu(p,rule);p.keyboard.press('Tab')
    check(f'{w}: Rule Tab stays inside',state(p)['modal']=='rule-modal',state(p));p.keyboard.press('Enter');p.wait_for_timeout(100)
    check(f'{w}: Rule Enter cannot activate background auto assignment','scope-modal' not in state(p)['open'],state(p))
    p.screenshot(path=str(OUT/f'{"baseline" if args.baseline else "fixed"}-rule-tab-enter-{w}.png'),animations='disabled')
    if args.baseline:ctx.close();continue
    if not p.locator('#rule-modal').is_visible():menu(p,rule)
    trap(p,'rule-modal',f'{w}: rule')
    p.keyboard.press('Escape');p.wait_for_timeout(80)
    check(f'{w}: close returns to rule opener',p.locator(rule).evaluate('n=>n===document.activeElement'),state(p))
    # Every legacy route audited for focus entry and both boundaries.
    screens=[('member-modal','button[onclick="openMemberModal()"]'),('stats-modal','#btn-duty-stats'),('history-modal','button[onclick="openHistoryModal(\'MONTH\')"]'),('festival-modal','button[onclick="openFestivalModal()"]'),('holiday-modal','button[onclick="openHolidayModal()"]')]
    for id,selector in screens:
     menu(p,selector);p.locator('#'+id).wait_for(state='visible');p.wait_for_timeout(30)
     check(f'{w}: {id} initial focus',state(p)['modal']==id,state(p));trap(p,id,f'{w}: {id}');p.keyboard.press('Escape');p.wait_for_timeout(80)
     check(f'{w}: {id} clean Escape closes',not p.locator('#'+id).is_visible(),state(p))
     check(f'{w}: {id} close returns to visible opener',p.locator(selector).first.evaluate('n=>{const target=n.getClientRects().length?n:n.closest(".menu-pop-wrap")?.querySelector(".menu-pop-btn");return target===document.activeElement;}'),state(p))
    # Dirty parent -> guarded confirmation: one Escape closes child and preserves draft.
    menu(p,rule);old=p.locator('#rule-sub-zero').input_value();changed='false' if old=='true' else 'true';p.locator('#rule-sub-zero').select_option(changed)
    p.locator('#rule-modal .modal-head button').click();p.locator('#oa-admin-discard').wait_for(state='visible');trap(p,'oa-admin-discard',f'{w}: discard')
    check(f'{w}: discard visually top',p.locator('#oa-admin-discard .oa-close').evaluate('n=>{const r=n.getBoundingClientRect();return n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}'))
    p.keyboard.press('Escape');p.wait_for_timeout(80)
    check(f'{w}: child Escape preserves dirty rule and focus',p.locator('#rule-modal').is_visible() and not p.locator('#oa-admin-discard').is_visible() and p.locator('#rule-sub-zero').input_value()==changed and state(p)['modal']=='rule-modal',state(p))
    p.locator('#rule-modal button[onclick="saveRulesOnly()"]').click();p.wait_for_timeout(100)
    if p.locator('#oa-admin-dialog').is_visible():p.locator('#oa-admin-dialog [data-oa-btn="0"]').click()
    p.locator('#admin-password-modal').wait_for(state='visible');trap(p,'admin-password-modal',f'{w}: password')
    check(f'{w}: password visually top',p.locator('#admin-password-input').evaluate('n=>{const r=n.getBoundingClientRect();const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return hit===n||n.contains(hit);}'))
    p.keyboard.press('Escape');p.wait_for_timeout(120)
    check(f'{w}: password Escape preserves dirty parent and focus',p.locator('#rule-modal').is_visible() and not p.locator('#admin-password-modal').is_visible() and p.locator('#rule-sub-zero').input_value()==changed and state(p)['modal']=='rule-modal',state(p))
    p.screenshot(path=str(OUT/f'nested-rule-restored-{w}.png'),animations='disabled')
    # Reused dynamic dialog refresh keeps the currently focused persistent close control.
    p.evaluate('''()=>{OnAirUI.open('oa-modal-regression','검증','<button id="modal-refresh-action">작업</button>');document.querySelector('#oa-modal-regression .oa-close').focus();OnAirUI.open('oa-modal-regression','검증','<button>갱신됨</button>');}''')
    check(f'{w}: dynamic content refresh does not steal focus',p.locator('#oa-modal-regression .oa-close').evaluate('n=>n===document.activeElement'),state(p))
    p.evaluate('''()=>{OnAirUI.open('oa-modal-child-regression','중첩 확인','<button>확인</button>');document.querySelector('#oa-modal-child-regression .oa-close').focus();OnAirUI.open('oa-modal-regression','갱신','<button>부모 갱신</button>');}''')
    check(f'{w}: parent refresh preserves child focus and top layer',state(p)['modal']=='oa-modal-child-regression' and state(p)['top']=='oa-modal-child-regression',state(p))
    check(f'{w}: refreshed parent remains visually below child',p.locator('#oa-modal-child-regression .oa-close').evaluate('n=>{const r=n.getBoundingClientRect();return n===document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);}'))
    p.keyboard.press('Escape');p.wait_for_timeout(50)
    check(f'{w}: child Escape after parent refresh preserves parent',p.locator('#oa-modal-regression').is_visible() and not p.locator('#oa-modal-child-regression').is_visible() and state(p)['modal']=='oa-modal-regression',state(p))
    trap(p,'oa-modal-regression',f'{w}: dynamic')
    p.keyboard.press('Escape');p.wait_for_timeout(50)
    check(f'{w}: dynamic Escape leaves original parent',p.locator('#rule-modal').is_visible() and not p.locator('#oa-modal-regression').is_visible() and state(p)['modal']=='rule-modal',state(p))
    ctx.close()
   if not args.baseline and not args.focus_return_only and not args.calendar_escape_only:
    ctx,p=r.open(b,width=1366,height=768);p.on('pageerror',lambda e:errors.append(str(e)))
    menu(p,'button[onclick="openServerInfo()"]');p.locator('#admin-password-modal').wait_for(state='visible');p.keyboard.press('Escape');p.wait_for_timeout(80)
    check('Connection password cancellation leaves parent and focus',p.locator('#serverinfo-modal').is_visible() and state(p)['modal']=='serverinfo-modal',state(p));p.keyboard.press('Escape');p.wait_for_timeout(80)
    check('Second Escape closes connection parent',not p.locator('#serverinfo-modal').is_visible(),state(p));ctx.close()
   if not args.baseline and not args.focus_return_only:
    viewer=Runtime(base,'viewer','VIEWER',shared)
    try:
     for w,h in ((1366,768),(1920,1080)):
      ctx,p=viewer.open(b,name='김도윤',width=w,height=h);p.on('pageerror',lambda e:errors.append(str(e)))
      p.evaluate('openAbsenceRequestModal();OnAirAbsenceRequestCalendar.selectDate(todayStr)');p.keyboard.press('Escape');p.wait_for_timeout(60)
      check(f'{w}: calendar Escape cancels anchor before closing editor',p.locator('#absence-request-modal').is_visible() and not p.evaluate('OnAirAbsenceRequestCalendar.state.anchor'),state(p))
      p.keyboard.press('Escape');p.wait_for_timeout(60)
      check(f'{w}: next Escape closes editor after selection cancel',not p.locator('#absence-request-modal').is_visible(),state(p));ctx.close()
    finally:viewer.close()
  finally:b.close();r.close()
except Exception:errors.append(traceback.format_exc());traceback.print_exc()
finally:
 result=dict(checks=checks,errors=errors);(OUT/('baseline.json' if args.baseline else 'return-baseline.json' if args.focus_return_only else 'calendar-baseline.json' if args.calendar_escape_only else 'evidence.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
 print(json.dumps(dict(checks=len(checks),failed=sum(not c['passed'] for c in checks),errors=errors),ensure_ascii=False),flush=True)
if errors or any(not c['passed'] for c in checks):sys.exit(1)
