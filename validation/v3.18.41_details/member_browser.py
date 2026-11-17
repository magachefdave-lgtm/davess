"""Actual protected registration, max4 and safe cancel/retry without operational data."""
import configparser,json,sqlite3,tempfile,traceback
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import sync_playwright
from runtime import Runtime,fixture
OUT=Path(__file__).parent/'names';OUT.mkdir(exist_ok=True);checks=[];r=None;errors=[];password='synthetic-registration-password'
def check(name,value,evidence=None):
 checks.append(dict(name=name,passed=bool(value),evidence=evidence));print(('PASS ' if value else 'FAIL ')+name,flush=True);assert value,name
def snapshot(page,name):page.screenshot(path=str(OUT/(name+'.png')),animations='disabled')
def open_members(p):
 button=p.locator('button[onclick="openMemberModal()"]')
 if not button.is_visible():button.locator('xpath=ancestor::*[contains(@class,"menu-pop-wrap")]').locator('.menu-pop-btn').click()
 button.click();p.locator('#new-member-name').wait_for(state='visible')
try:
 with tempfile.TemporaryDirectory(prefix='onair-member-final-') as tmp:
  base=Path(tmp);shared=base/'shared';shared.mkdir();sqlite3.connect(shared/'smart_duty.db').close()
  native=configparser.ConfigParser.write
  def write(cfg,*a,**k):cfg['NETWORK']['admin_password']=password;return native(cfg,*a,**k)
  with patch.object(configparser.ConfigParser,'write',write):r=Runtime(base,'admin','ADMIN',shared)
  response=r.send('/api/duty/save-all',dict(baseVersion=r.get('/api/duty/all-data').json()['dataVersion'],**fixture()),headers={'X-Admin-Token':password});response.raise_for_status()
  before=r.get('/api/duty/all-data').json()
  with sync_playwright() as pw:
   browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox']);ctx,p=r.open(browser,width=1366,height=768);p.on('pageerror',lambda e:errors.append(str(e)))
   open_members(p);name=p.locator('#new-member-name');check('새 사원 입력 최대4자 안내',name.get_attribute('maxlength')=='4' and '4자' in name.get_attribute('placeholder'))
   name.fill('남궁민수');p.locator('#member-modal button').filter(has_text='추가').click();p.locator('#admin-password-modal').wait_for(state='visible');p.keyboard.press('Escape');p.wait_for_function('!isSaving')
   check('암호 취소 시 이름과 원래 입력창 유지',name.input_value()=='남궁민수' and p.locator('#member-modal').is_visible())
   check('취소 후 입력란 초점 복귀',name.evaluate('n=>document.activeElement===n'))
   check('취소 시 DB 등록 없음',r.get('/api/duty/all-data').json()['members']==before['members']);snapshot(p,'registration-cancel-preserved')
   p.locator('#member-modal button').filter(has_text='추가').click();p.locator('#admin-password-input').fill('wrong');p.locator('#admin-password-input').press('Enter');p.locator('#admin-password-error').wait_for(state='visible');p.locator('#admin-password-input').fill(password);p.locator('#admin-password-input').press('Enter')
   p.wait_for_function("memberList.some(m=>m.name==='남궁민수') && !isSaving");check('4자 이름 실제 등록 및 입력 초기화',name.input_value()=='' and any(m['name']=='남궁민수' for m in r.get('/api/duty/all-data').json()['members']))
   # Programmatic input bypass: UI validation must still reject before saving.
   version=r.get('/api/duty/all-data').json()['dataVersion'];name.evaluate("n=>{n.value='홍길동사원';}");p.locator('#member-modal button').filter(has_text='추가').click()
   check('5자 우회 입력을 저장 전에 거절',r.get('/api/duty/all-data').json()['dataVersion']==version and name.input_value()=='홍길동사원')
   name.fill('검수사원');name.evaluate("n=>n.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true}))")
   check('한글 조합 중 Enter로 조기 등록하지 않음',r.get('/api/duty/all-data').json()['dataVersion']==version)
   name.fill('');p.keyboard.press('Escape');p.locator('#member-modal').wait_for(state='hidden')
   check('닫은 뒤 본화면 사용 가능',not p.locator('.modal.active').count() and not p.locator('header').evaluate('n=>n.inert'))
   check('브라우저 오류 없음',not errors,errors);browser.close()
except Exception:errors.append(traceback.format_exc());traceback.print_exc()
finally:
 if r:r.close()
 (OUT/'registration-evidence.json').write_text(json.dumps(dict(checks=checks,errors=errors),ensure_ascii=False,indent=2))
if errors or any(not c['passed'] for c in checks):raise SystemExit(1)
