"""Real admin profile: own absence, authentication cancellation/retry and freed-duty followup."""
import sys, tempfile, sqlite3, json, traceback, configparser
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime import Runtime, fixture
from playwright.sync_api import sync_playwright
OUT = Path(__file__).parent
RED = '--red' in sys.argv
PASSWORD = 'synthetic-absence-password'
checks, errors = [], []
def check(label, value, evidence=None):
 checks.append(dict(name=label, passed=bool(value), evidence=evidence))
 print(('PASS ' if value else 'FAIL ') + label, flush=True)
 if not RED: assert value, label
def top(page, selector):
 return page.locator(selector).last.evaluate('''n=>{const r=n.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {reachable:n===hit||n.contains(hit),hit:hit?.closest('.modal')?.id,focus:document.activeElement.closest('.modal')?.id,stack:[...document.querySelectorAll('.modal.active')].map(n=>({id:n.id,z:getComputedStyle(n).zIndex})),escape:[...MODAL_CLOSERS].reverse().find(([id])=>isModalOpen(id))?.[0]}}''')
def shot(page, name):
 for b in page.locator('#oa-toast-errors button').all():
  if b.is_visible():b.click()
 for _ in range(10):
  buttons=page.locator('#toast button').filter(has_text='닫기')
  if not buttons.count() or not buttons.first.is_visible():break
  buttons.first.click()
 page.screenshot(path=str(OUT/(('red-' if RED else 'green-')+name+'.png')),animations='disabled')
def open_form(page):
 page.locator('button[onclick="openAbsenceModal()"]').click()
 page.locator('#abs-name').select_option('김도윤')
 page.locator('#abs-start').fill('2026-12-01');page.locator('#abs-end').fill('2026-12-02')
def submit(page):
 page.locator('#abs-submit-btn').click();page.locator('#oa-confirm-dialog [data-oa-btn="0"]').click()
def idle(page):page.wait_for_function('!absenceFormSaving && !isSaving')
def layout(page):return page.evaluate("()=>Object.fromEntries(['header','#menu-row','.top-grid','.info-desk-grid','.cal-header-bar','.cal-table'].map(s=>{const r=document.querySelector(s)?.getBoundingClientRect();return [s,r?{x:r.x,y:r.y,width:r.width,height:r.height}:null]}))")
status='FAIL'
try:
 with tempfile.TemporaryDirectory(prefix='absence-modal-') as tmp, sync_playwright() as pw:
  browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
  for protected in (True, False):
   base=Path(tmp)/str(protected);base.mkdir();shared=base/'shared';shared.mkdir();sqlite3.connect(shared/'smart_duty.db').close()
   native_write=configparser.ConfigParser.write
   def config_write(cfg,*args,**kwargs):
    if protected:cfg['NETWORK']['admin_password']=PASSWORD
    return native_write(cfg,*args,**kwargs)
   with patch.object(configparser.ConfigParser,'write',config_write):r=Runtime(base,'admin','ADMIN',shared)
   try:
    data=fixture();data['schedules']={'2026-12-01':[{'name':'김도윤','gender':'M'}]}
    response=r.send('/api/duty/save-all',dict(baseVersion=r.get('/api/duty/all-data').json()['dataVersion'],**data),headers={'X-Admin-Token':PASSWORD} if protected else {})
    response.raise_for_status()
    ctx,page=r.open(browser,width=1366,height=768);page.on('pageerror',lambda e:errors.append(str(e)))
    page.locator('#my-name-btn').click();page.locator('[data-oa-action=pick-name][data-name="김도윤"]').click();page.locator('[data-oa-action=apply-name]').click()
    initial=layout(page);open_form(page)
    # First cancel confirmation without saving and retain input.
    page.locator('#abs-submit-btn').click();info=top(page,'#oa-confirm-dialog [data-oa-btn="1"]')
    check('confirmation reachable '+str(protected),info['reachable'],info)
    page.locator('#oa-confirm-dialog [data-oa-btn="1"]').click();idle(page)
    check('confirmation cancellation retains input '+str(protected),page.locator('#abs-start').input_value()=='2026-12-01' and not page.locator('#abs-submit-btn').is_disabled())
    submit(page)
    if protected:
     page.locator('#admin-password-modal.active').wait_for();page.wait_for_timeout(75)
     info=top(page,'#admin-password-input');shot(page,'password')
     check('password input and Escape target visible over absence editor',info['reachable'] and info['escape']=='admin-password-modal',info)
     if RED:
      try:page.locator('#admin-password-input').click(timeout=1200)
      except Exception as exc:checks.append(dict(name='real password click times out before fix',passed=True,evidence=str(exc)[:1000]))
      continue
     page.locator('#admin-password-modal button[onclick="cancelAdminPassword()"]').last.click();idle(page)
     check('password cancel unlocks unchanged draft and does not save',page.locator('#abs-start').input_value()=='2026-12-01' and len(r.get('/api/duty/all-data').json()['absences'])==0)
     submit(page);page.locator('#admin-password-input').fill('wrong-fixture-password');page.locator('#admin-password-input').press('Enter')
     page.locator('#admin-password-error').wait_for(state='visible')
     check('wrong password retry remains reachable',top(page,'#admin-password-input')['reachable'])
     page.keyboard.press('Escape');idle(page)
     check('Escape cancels password and keeps absence editor',page.locator('#absence-modal').is_visible() and not page.locator('#admin-password-modal').is_visible())
     submit(page);page.locator('#admin-password-input').fill(PASSWORD);page.locator('#admin-password-input').press('Enter')
    page.locator('#vacancy-modal.active').wait_for();idle(page)
    info=top(page,'#vacancy-modal button[onclick="closeVacancyModal()"]').copy();shot(page,'vacancy-'+str(protected))
    check('freed-duty followup and Escape target visible '+str(protected),info['reachable'] and info['escape']=='vacancy-modal',info)
    if RED:continue
    check('own absence saved exactly once and original duty freed '+str(protected),len(r.get('/api/duty/all-data').json()['absences'])==1 and not r.get('/api/duty/all-data').json()['schedules'].get('2026-12-01'))
    if protected:page.locator('#vacancy-modal button[onclick="closeVacancyModal()"]').last.click()
    else:page.keyboard.press('Escape')
    check('later or Escape returns focus to enabled absence editor '+str(protected),page.locator('#absence-modal').is_visible() and not page.locator('#abs-submit-btn').is_disabled() and page.evaluate("document.activeElement.id === 'abs-submit-btn'"))
    page.locator('#absence-modal .abs-close').click();page.locator('#absence-modal').wait_for(state='hidden')
    check('return to main leaves no hidden active modal trap '+str(protected),page.locator('.modal.active').count()==0)
    after=layout(page);check('main header/menu/cards and calendar horizontal layout unchanged '+str(protected),all(after[k]==v for k,v in initial.items() if not k.startswith('.cal-')) and all(after[k][axis]==initial[k][axis] for k in ('.cal-header-bar','.cal-table') for axis in ('x','width')),dict(before=initial,after=after))
    # Reopen the management and edit the saved record; reused dialogs stay in order.
    open_form(page);page.locator('#abs-search').fill('김도윤');page.locator('#abs-tbody button').filter(has_text='수정').first.click()
    page.locator('#abs-end').fill('2026-12-03');page.locator('#abs-submit-btn').click();page.keyboard.press('Escape');idle(page)
    check('edit confirmation Escape retains draft '+str(protected),page.locator('#abs-end').input_value()=='2026-12-03')
    page.on('dialog',lambda d:d.accept());page.locator('#absence-modal .abs-close').click()
    shot(page,'back-'+str(protected));ctx.close()
   finally:r.close()
  check('no browser script errors',not errors,errors)
  browser.close()
 status='RED reproduced' if RED and any(not c['passed'] for c in checks) else 'PASS'
except Exception:
 traceback.print_exc()
finally:
 (OUT/('red-evidence.json' if RED else 'evidence.json')).write_text(json.dumps(dict(status=status,checks=checks,errors=errors),ensure_ascii=False,indent=2))
if status=='FAIL':raise SystemExit(1)
