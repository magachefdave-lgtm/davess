"""Real Admin/Viewer profiles, shared SQLite, and annual CA UI acceptance."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
import json, sqlite3, tempfile, traceback, uuid
from playwright.sync_api import sync_playwright
from runtime import Runtime
OUT=Path(__file__).parent/'browser';OUT.mkdir(parents=True,exist_ok=True)
checks=[];errors=[];runtimes=[];status='FAIL'
def check(name,value,evidence=None):
    checks.append(dict(name=name,passed=bool(value),evidence=evidence));print(('PASS ' if value else 'FAIL ')+name,flush=True);assert value,name
def op(**values):return dict(operationId=str(uuid.uuid4()),**values)
def body(response):
    assert response.status_code==200,response.text
    return response.json()
def accept(page):
    for c in page.locator('#onair-dinner-confirm input[type=checkbox]').all():c.check()
    page.locator('#onair-dinner-confirm [data-da=accept]').click()
    page.locator('#onair-dinner-confirm.active').wait_for(state='hidden')
def snap(page,name):
    for _ in range(8):
        b=page.locator('#toast button').filter(has_text='닫기')
        if not b.count() or not b.first.is_visible():break
        b.first.click()
    page.screenshot(path=str(OUT/(name+'.png')),animations='disabled')
def geom(page):
    return page.evaluate("()=>Object.fromEntries(['header','#menu-row','.top-grid','.info-desk-grid','.cal-header-bar','.cal-table'].map(s=>{const r=document.querySelector(s)?.getBoundingClientRect();return [s,r?{x:r.x,y:r.y,width:r.width,height:r.height}:null]}))")
try:
    with tempfile.TemporaryDirectory(prefix='onair-ca-years-') as tmp:
        base=Path(tmp);shared=base/'shared';shared.mkdir();sqlite3.connect(shared/'smart_duty.db').close()
        admin=Runtime(base,'admin','ADMIN',shared);runtimes.append(admin);admin.seed()
        current=Runtime(base,'current','VIEWER',shared);runtimes.append(current)
        future=Runtime(base,'future','VIEWER',shared);runtimes.append(future)
        year=datetime.now(timezone(timedelta(hours=9))).year
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            ac,ap=admin.open(browser,width=1920,height=1080)
            cc,cp=current.open(browser,name='김하늘',width=1920,height=1080)
            fc,fp=future.open(browser,name='이서연',width=1920,height=1080)
            for p in [ap,cp,fp]:p.on('pageerror',lambda e:errors.append(str(e)))
            original=geom(ap)
            ap.evaluate('OnAirDinnerAdmin.openRole()')
            ap.locator('#da-ca-name').select_option('김하늘');ap.locator('#da-term-start').fill(f'{year}-01-01');ap.locator('#da-term-end').fill(f'{year}-12-31')
            ap.locator('[data-da=save-role]').click();accept(ap)
            cp.evaluate('OnAirDinnerAdmin.openRole()');cp.locator('[data-da=connect]').click();cp.get_by_text('관리자 확인 대기',exact=True).wait_for()
            ap.evaluate('OnAirDinnerAdmin.openRole()');ap.locator('[data-da=connection-review]').click();accept(ap)
            key=cp.evaluate('OnAirDinner.credentials()')['key']
            check('올해 CA 지정 및 실제 PC 승인',current.get('/api/dinner/manage',headers={'X-OnAir-Dinner-Key':key}).status_code==200)
            ap.locator(f'[data-da=role-year][data-year="{year+1}"]').click();ap.locator('#da-ca-name').select_option('이서연');ap.locator('[data-da=save-role]').click();accept(ap)
            data=body(admin.get('/api/dinner/manage'))
            current_role_id=next(r['roleId'] for r in data['roles'] if r['memberName']=='김하늘')
            check('올해와 내년 담당자 동시 보관',len([r for r in data['roles'] if r['memberName']])==2)
            check('내년 지정 이후 올해 CA 권한 유지',current.get('/api/dinner/manage',headers={'X-OnAir-Dinner-Key':key}).status_code==200)
            fp.evaluate('OnAirDinner.open(null,true)');fp.locator('[data-dinner-action=role]').click();fp.locator('[data-da=connect]').click();fp.get_by_text('관리자 확인 대기',exact=True).wait_for()
            ap.evaluate('OnAirDinnerAdmin.openRole()');ap.locator(f'[data-year="{year+1}"]').click();ap.locator('[data-da=connection-review]').click();accept(ap)
            fp.evaluate('OnAirDinnerAdmin.openRole()');fp.get_by_text('승인완료·임기시작대기',exact=True).wait_for()
            fkey=fp.evaluate('OnAirDinner.credentials()')['key']
            check('내년 PC 미리 승인 후 임기 시작 대기',future.get('/api/dinner/manage',headers={'X-OnAir-Dinner-Key':fkey}).status_code==403)
            check('예약 승인 후 관리 버튼 표시하지 않음',fp.locator('[data-da=reload]').count()==0)
            snap(fp,'viewer-future-approved')
            for i in range(5):
                body(future.send('/api/dinner/connections',op(roleId=year+1,memberName='이서연',deviceLabel=f'이서연 PC {i+1}'),headers={'X-OnAir-Dinner-Key':f'acceptance-future-device-{i}-'+'x'*40}))
            ap.evaluate('OnAirDinnerAdmin.openRole()');ap.locator(f'[data-year="{year+1}"]').click()
            check('선택한 내년 담당자의 요청만 표시',ap.locator('.da-connection').count()==2 and '김하늘' not in ap.locator('.da-approval-panel').inner_text())
            ap.locator('[data-da=connection-page][data-page="1"]').click();check('여러 PC 요청 페이지 이동',ap.locator('.da-connection-pages').inner_text().find('2 / 3')>=0)
            ap.locator('[data-da=connection-page][data-page="0"]').click()
            for width,height in [(1920,1080),(1366,768),(390,844)]:
                ap.set_viewport_size(dict(width=width,height=height))
                g=ap.locator('#onair-dinner-admin .modal-body').evaluate('(n)=>({width:n.clientWidth,scrollWidth:n.scrollWidth,height:n.clientHeight,scrollHeight:n.scrollHeight})')
                check(f'{width} 화면 가로 넘침 없음',g['scrollWidth']<=g['width']+1,g)
                if width>=1366:check(f'{width} 화면 세로 스크롤 없음',g['scrollHeight']<=g['height']+1,g)
                snap(ap,f'admin-pending-{width}')
            ap.set_viewport_size(dict(width=1920,height=1080));ap.locator('[data-da=connection-tab][data-tab=approved]').click();snap(ap,'admin-future-approved')
            ap.locator(f'[data-year="{year}"]').click();ap.locator('[data-da=connection-tab][data-tab=approved]').click();snap(ap,'admin-current-approved')
            ap.evaluate('OnAirUI.close("onair-dinner-admin")')
            check('메인 화면 구성과 위치 유지',geom(ap)==original,dict(before=original,after=geom(ap)))
            ap.evaluate('OnAirDinnerAdmin.openRole()');ap.locator(f'[data-year="{year+1}"]').click()
            ap.locator('#da-ca-name').select_option('김하늘');ap.locator('[data-da=save-role]').click();accept(ap)
            cp.evaluate('OnAirDinnerAdmin.open()');cp.locator('#onair-dinner-admin [data-da=role]').click();cp.locator(f'[data-da=viewer-role][data-role-id="{year+1}"]').click()
            cp.locator('[data-da=connect]').click();cp.get_by_text('관리자 확인 대기',exact=True).wait_for()
            ap.evaluate('OnAirDinnerAdmin.openRole()');ap.locator(f'[data-year="{year+1}"]').click();ap.locator('[data-da=connection-review]').click();accept(ap)
            cp.evaluate('OnAirDinnerAdmin.openRole()');cp.get_by_text('승인완료·임기시작대기',exact=True).wait_for()
            check('연임하는 같은 사람도 내년 PC를 미리 승인',current.get('/api/dinner/manage',headers={'X-OnAir-Dinner-Key':key}).status_code==200)
            cp.locator(f'[data-da=viewer-role][data-role-id="{current_role_id}"]').click();check('현재 임기 연결과 관리 버튼 유지',cp.locator('[data-da=reload]').count()==1)
            snap(cp,'viewer-renewal-current')
            check('브라우저 실행 오류 없음',not errors,errors)
            browser.close()
    status='PASS'
except Exception:
    traceback.print_exc()
finally:
    for r in reversed(runtimes):r.close()
    (OUT/'acceptance.json').write_text(json.dumps(dict(status=status,checks=checks,errors=errors),ensure_ascii=False,indent=2))
if status!='PASS':raise SystemExit(1)
