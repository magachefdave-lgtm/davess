"""Recovery regressions against real ADMIN/VIEWER servers and isolated synthetic data."""
from pathlib import Path
from datetime import date, timedelta
import sys, json, sqlite3, tempfile, traceback, hashlib, time
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'v3.18.40_ca_years'))
from runtime import Runtime
from playwright.sync_api import sync_playwright

OUT=Path(__file__).parent
PHASE=sys.argv[1] if len(sys.argv)>1 else 'green'
results={'phase':PHASE,'checks':[], 'pageErrors':[]}
runtimes=[]
def note(name, passed, detail=None):
    item={'name':name, 'passed':bool(passed), 'detail':detail}
    results['checks'].append(item)
    print(json.dumps(item, ensure_ascii=False), flush=True)
def ok(r):
    if r.status_code!=200:raise RuntimeError(str(r.status_code)+' '+r.text)
    return r.json()
def shot(p,name):p.screenshot(path=str(OUT/(PHASE+'-'+name+'.png')),animations='disabled')
def focus(p):
    return p.evaluate("""()=>({tag:document.activeElement?.tagName,id:document.activeElement?.id,
      text:document.activeElement?.tagName==='BODY'?'':document.activeElement?.textContent,
      group:document.activeElement?.dataset?.raGroup,root:document.activeElement?.dataset?.raRoot,
      listScroll:document.querySelector('.ra-candidates')?.scrollTop,panelScroll:document.querySelector('.ra-panel')?.scrollTop,
      selected:[...document.querySelectorAll('[data-ra-group]:checked')].map(e=>e.dataset.raGroup),
      active:[...document.querySelectorAll('.modal.active')].map(e=>e.id)})""")
def recovery_open(p):
    p.evaluate("openMyRequestsModal('김도윤')")
    p.wait_for_function("document.querySelector('#ra-banner button')")
    p.locator('#ra-banner').get_by_role('button',name='신청 권한 복구',exact=True).click()
    p.locator('#ra-content').get_by_role('button',name='다시 확인',exact=True).wait_for()
def fail_once(p,pattern,message):
    p.route(pattern,lambda route:route.fulfill(status=503,content_type='application/json',body=json.dumps({'detail':message},ensure_ascii=False)),times=1)
try:
  results['requestAccessSha256']=hashlib.sha256((OUT.parents[2]/'frontend/dist/request_access.js').read_bytes()).hexdigest()
  with tempfile.TemporaryDirectory(prefix='onair-recovery-proof-') as tmp:
    base=Path(tmp);shared=base/'shared';shared.mkdir();sqlite3.connect(shared/'smart_duty.db').close()
    admin=Runtime(base,'admin','ADMIN',shared);runtimes.append(admin);admin.seed()
    viewer=Runtime(base,'viewer','VIEWER',shared);runtimes.append(viewer)
    for i in range(55):
      day=(date(2027,1,1)+timedelta(days=i)).isoformat()
      ok(admin.send('/api/duty/request-change',dict(requester_name='김도윤',target_date=day,
        request_type='ABSENCE',absence_type='교육',absence_end_date=day,reason=f'복구 회귀 확인용 합성 신청 {i+1}',
        absence_details={'title':'역량 강화 교육','institution':'예시 교육원','location':'예시 교육장','hours':'8','content':'합성 교육 자료'})))
    with sync_playwright() as pw:
      browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
      ac,ap=admin.open(browser,width=1366,height=768)
      vc,vp=viewer.open(browser,name='김도윤',width=1366,height=768)
      for p in (ap,vp):p.on('pageerror',lambda e:results['pageErrors'].append(str(e)))
      print('BROWSER_SOURCE_LOADED',flush=True)
      recovery_open(vp)
      refresh=vp.locator('#ra-content').get_by_role('button',name='다시 확인',exact=True)
      refresh.focus();before=focus(vp)
      vp.evaluate('OnAirRequestAccess.refresh()');after=focus(vp)
      note('employee manual refresh retains action focus',after['tag']=='BUTTON' and after['text']=='다시 확인',{'before':before,'after':after})
      refresh.focus();before=focus(vp)
      with vp.expect_response(lambda r:r.url.endswith('/api/local/request-access/recoveries') and r.request.method=='GET',timeout=12500):
        vp.wait_for_timeout(10500)
      after=focus(vp)
      note('actual ten second employee poll retains focus',after['tag']=='BUTTON' and after['text']=='다시 확인',{'before':before,'after':after})
      old=vp.locator('#ra-content').inner_text();refresh.focus()
      fail_once(vp,'**/api/local/request-access/recoveries','합성 직원 연결 오류')
      vp.evaluate('OnAirRequestAccess.refresh()')
      note('employee failed refresh preserves existing UI and focus',vp.locator('#ra-content').inner_text()==old and focus(vp)['text']=='다시 확인',{'focus':focus(vp),'error':vp.locator('#ra-error').inner_text()})
      vp.evaluate('OnAirRequestAccess.refresh()')
      note('employee successful retry clears stale error',vp.locator('#ra-error').inner_text()=='',{'error':vp.locator('#ra-error').inner_text(),'focus':focus(vp)})
      shot(vp,'employee-refresh-retry')
      vp.keyboard.press('Escape');vp.wait_for_timeout(50)
      after=focus(vp)
      note('one Escape closes recovery only and returns focus to opener',after['active']==['myreq-modal'] and after['text']=='신청 권한 복구',after)
      shot(vp,'escape-child-only')
      recovery_open(vp)
      vp.locator('#ra-content').get_by_role('button',name='복구 요청 등록',exact=True).click()
      vp.locator('#ra-content').get_by_text('관리자 확인 대기',exact=True).wait_for()
      note('employee create transition keeps focus on available recovery action',focus(vp)['text']=='다시 확인',focus(vp))
      ap.evaluate('OnAirRequestAccess.openAdminRecovery()')
      ap.get_by_role('button',name='대상 신청 확인',exact=True).click()
      ap.locator('[data-ra-group]').first.wait_for()
      note('55 real synthetic roots yield first page of 50',ap.locator('[data-ra-group]').count()==50,{'displayed':ap.locator('[data-ra-group]').count(),'total':55})
      ap.locator('[data-ra-group="0"]').focus();before=focus(ap)
      ap.keyboard.press('Space');ap.wait_for_function("document.querySelector('#ra-content')?.textContent.includes('선택 1건')")
      after=focus(ap)
      note('admin checkbox Space retains same group focus',after['group']=='0',{'before':before,'after':after})
      ap.keyboard.press('Tab')
      note('next Tab proceeds to next group',focus(ap)['group']=='1',focus(ap))
      ap.locator('[data-ra-group="30"]').focus();ap.locator('.ra-candidates').evaluate('(n)=>n.scrollTop=1800');before=focus(ap)
      ap.keyboard.press('Space');ap.wait_for_function("document.querySelector('#ra-content')?.textContent.includes('선택 2건')")
      after=focus(ap)
      note('admin checkbox selection retains candidate scroll and focus',after['listScroll']==before['listScroll'] and after['group']=='30',{'before':before,'after':after})
      shot(ap,'checkbox-scroll-focus')
      ap.locator('#ra-search').fill('교육');ap.locator('#ra-search').focus();old=ap.locator('#ra-content').inner_text()
      fail_once(ap,'**/api/duty/request-recoveries/*/candidates?*','합성 관리자 연결 오류')
      ap.evaluate('OnAirRequestAccess.search()')
      note('failed admin search keeps typed input and focus',ap.locator('#ra-search').input_value()=='교육' and focus(ap)['id']=='ra-search',{'input':ap.locator('#ra-search').input_value(),'focus':focus(ap),'error':ap.locator('#ra-error').inner_text()})
      ap.evaluate('OnAirRequestAccess.search()')
      note('successful admin search retry clears stale error and retains input focus',ap.locator('#ra-error').inner_text()=='' and ap.locator('#ra-search').input_value()=='교육' and focus(ap)['id']=='ra-search',{'input':ap.locator('#ra-search').input_value(),'focus':focus(ap),'error':ap.locator('#ra-error').inner_text()})
      ap.locator('#ra-search').fill('');ap.evaluate('OnAirRequestAccess.search()')
      ap.locator('#ra-next').click();ap.wait_for_function("document.querySelectorAll('[data-ra-group]').length===5")
      note('second page contains five remaining candidates',ap.locator('[data-ra-group]').count()==5)
      ap.locator('[data-ra-group="0"]').check();ap.wait_for_function("document.querySelector('#ra-content')?.textContent.includes('선택 3건')")
      ap.locator('#ra-prev').click();ap.wait_for_function("document.querySelectorAll('[data-ra-group]').length===50")
      note('page navigation preserves previous selections',ap.locator('[data-ra-group="0"]').is_checked() and ap.locator('[data-ra-group="30"]').is_checked())
      # Hold an actual GET response while the user leaves its screen.
      held=[]
      ap.route('**/api/duty/request-recoveries/*/candidates?*',lambda route:held.append(route),times=1)
      ap.locator('#ra-search').fill('교육')
      ap.evaluate('void OnAirRequestAccess.search()');ap.wait_for_timeout(100)
      ap.evaluate('OnAirRequestAccess.adminList()');before=focus(ap);old=ap.locator('#ra-content').inner_html()
      if held:
        held[0].fulfill(response=held[0].fetch());ap.wait_for_timeout(100)
      after=focus(ap)
      note('delayed admin candidates cannot replace navigated list or steal focus',bool(held) and ap.locator('#ra-content').inner_html()==old and after==before,{'held':len(held),'before':before,'after':after})
      # Manual admin refresh clears a related older failure while preserving list focus.
      fail_once(ap,'**/api/duty/request-recoveries','합성 관리자 목록 연결 오류')
      ap.get_by_role('button',name='대상 신청 확인',exact=True).first.focus()
      ap.evaluate('OnAirRequestAccess.refresh()');failed=ap.locator('#ra-error').inner_text()
      ap.evaluate('OnAirRequestAccess.refresh()')
      note('successful admin list refresh clears stale error and retains review focus',failed=='합성 관리자 목록 연결 오류' and ap.locator('#ra-error').inner_text()=='' and focus(ap)['text']=='대상 신청 확인',{'failedError':failed,'error':ap.locator('#ra-error').inner_text(),'focus':focus(ap)})
      held=[]
      vp.route('**/api/local/request-access/recoveries',lambda route:held.append(route),times=1)
      vp.evaluate('void OnAirRequestAccess.refresh()');vp.wait_for_timeout(100)
      vp.evaluate('OnAirRequestAccess.close()');vp.wait_for_timeout(50)
      before=focus(vp);old=vp.locator('#ra-content').inner_html()
      if held:
        held[0].fulfill(response=held[0].fetch());vp.wait_for_timeout(100)
      after=focus(vp)
      note('delayed employee refresh cannot render or steal focus after close',bool(held) and vp.locator('#ra-content').inner_html()==old and after==before,{'held':len(held),'before':before,'after':after})
      recovery_open(vp)
      held=[]
      vp.route('**/api/local/request-access/recoveries',lambda route:held.append(route),times=1)
      vp.evaluate('void OnAirRequestAccess.refresh()');vp.wait_for_timeout(100)
      vp.evaluate('OnAirRequestAccess.close()');vp.wait_for_timeout(50)
      recovery_open(vp)
      fresh=[]
      fresh_response=lambda response:fresh.append(response.status) if response.url.endswith('/api/local/request-access/recoveries') and response.request.method=='GET' else None
      vp.on('response',fresh_response)
      vp.locator('#ra-content').get_by_role('button',name='다시 확인',exact=True).focus()
      vp.evaluate('OnAirRequestAccess.refresh()')
      vp.remove_listener('response',fresh_response)
      note('reopened dialog can refresh while old dialog read remains pending',fresh==[200] and focus(vp)['text']=='다시 확인',{'responses':fresh,'focus':focus(vp)})
      before=focus(vp);old=vp.locator('#ra-content').inner_html()
      if held:
        held[0].fulfill(status=503,content_type='application/json',body=json.dumps({'detail':'합성 이전 창 연결 오류'},ensure_ascii=False));vp.wait_for_timeout(100)
      note('old failed response cannot overwrite reopened recovery state',bool(held) and vp.locator('#ra-error').inner_text()=='' and vp.locator('#ra-content').inner_html()==old and focus(vp)==before,{'held':len(held),'error':vp.locator('#ra-error').inner_text(),'before':before,'after':focus(vp)})
      vp.evaluate('OnAirRequestAccess.close()');vp.wait_for_timeout(50)
      recovery_open(vp)
      held=[]
      vp.route('**/api/local/request-access/recoveries',lambda route:held.append(route),times=1)
      vp.evaluate('void OnAirRequestAccess.refresh()');vp.wait_for_timeout(100)
      old=vp.locator('#ra-content').inner_html();before=focus(vp)
      vp.evaluate("document.getElementById('myreq-name').value='이서연'")
      vp.evaluate('OnAirRequestAccess.refresh()')
      # The busy guard may defer a second refresh until the first response settles.
      if held:
        held[0].fulfill(response=held[0].fetch());vp.wait_for_timeout(100)
      note('late employee response for old name is ignored after name change',bool(held) and vp.locator('#ra-content').inner_html()==old and focus(vp)==before,{'held':len(held),'content':vp.locator('#ra-content').inner_text(),'before':before,'after':focus(vp)})
      vp.evaluate('OnAirRequestAccess.refresh()')
      note('new name refresh renders the new employee', '선택한 이름: 이서연' in vp.locator('#ra-content').inner_text(),{'content':vp.locator('#ra-content').inner_text()})
      vp.evaluate('OnAirRequestAccess.close()')
      vp.evaluate("document.getElementById('myreq-name').value='김도윤'")
      recovery_open(vp)
      # Perform a real selected-scope approval and verified local receipt claim.
      ap.get_by_role('button',name='대상 신청 확인',exact=True).first.click()
      ap.locator('[data-ra-group]').first.check()
      fail_once(ap,'**/api/duty/request-recoveries/*/preview','합성 미리보기 연결 오류')
      ap.get_by_role('button',name='선택 범위 미리보기',exact=True).click()
      ap.wait_for_function("document.getElementById('ra-error').textContent==='합성 미리보기 연결 오류'")
      ap.get_by_role('button',name='선택 범위 미리보기',exact=True).click()
      ap.get_by_role('button',name='권한 연결 승인',exact=True).wait_for()
      note('successful preview retry clears related stale error',ap.locator('#ra-error').inner_text()=='',{'error':ap.locator('#ra-error').inner_text(),'preview':ap.locator('#ra-preview').inner_text()})
      held_preview=[]
      ap.route('**/api/duty/request-recoveries/*/preview',lambda route:held_preview.append(route),times=1)
      ap.evaluate('void OnAirRequestAccess.preview()');ap.wait_for_timeout(100)
      ap.evaluate('OnAirRequestAccess.preview()');old_preview=ap.locator('#ra-preview').inner_html();before=focus(ap)
      if held_preview:
        held_preview[0].fulfill(status=503,content_type='application/json',body=json.dumps({'detail':'합성 이전 미리보기 연결 오류'},ensure_ascii=False));ap.wait_for_timeout(100)
      note('older failed preview cannot overwrite newer preview success',bool(held_preview) and ap.locator('#ra-error').inner_text()=='' and ap.locator('#ra-preview').inner_html()==old_preview and focus(ap)==before,{'held':len(held_preview),'error':ap.locator('#ra-error').inner_text(),'before':before,'after':focus(ap)})
      preview=ap.locator('#ra-preview').inner_text()
      note('selected-scope preview includes exactly one real request', '연결 범위 · 1건' in preview,{'preview':preview})
      ap.get_by_role('button',name='권한 연결 승인',exact=True).click()
      vp.evaluate('OnAirRequestAccess.refresh()')
      vp.locator('#ra-content').get_by_text('선택한 신청의 권한이 연결되었습니다',exact=True).wait_for(timeout=15000)
      count=vp.evaluate('Object.keys(readMyRequestStatuses()).length')
      note('approval claims only selected receipt after verified completion',count==1,{'receiptCount':count,'status':vp.locator('#ra-content').inner_text()})
      shot(vp,'verified-claim-completed')
      # Reopen the admin panel at the list for responsive checks.
      ap.evaluate('OnAirRequestAccess.openAdminRecovery()')
      for width,height in [(1920,1080),(1366,768),(1024,768)]:
        ap.set_viewport_size(dict(width=width,height=height))
        geom=ap.locator('.ra-panel').evaluate('(n)=>({width:n.clientWidth,scrollWidth:n.scrollWidth,height:n.clientHeight,scrollHeight:n.scrollHeight})')
        note(f'recovery {width} has no horizontal overflow',geom['scrollWidth']<=geom['width']+1,geom)
      note('no browser script errors',not results['pageErrors'],results['pageErrors'])
      browser.close()
except Exception:
  results['fatal']=traceback.format_exc();print(results['fatal'],flush=True)
finally:
  for r in reversed(runtimes):r.close()
  (OUT/(PHASE+'-evidence.json')).write_text(json.dumps(results,ensure_ascii=False,indent=2))
if results.get('fatal') or (PHASE=='green' and any(not c['passed'] for c in results['checks'])):sys.exit(1)
