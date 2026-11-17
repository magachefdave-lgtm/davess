/* Real Chromium interaction check; API boundary is an isolated synthetic fixture. Run directly. */
'use strict';
const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
let checks=0;const assert=new Proxy(require('node:assert/strict'),{get(target,key){const value=target[key];return typeof value==='function'?(...args)=>{checks++;return value(...args);}:value;}});
const root=path.resolve(__dirname,'../..');
let browser;
(async()=>{
 browser=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1280,height:900},locale:'ko-KR',timezoneId:'Asia/Seoul'});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://127.0.0.1:9188/**',route=>route.fulfill({contentType:'text/html',body:'<!doctype html><title>fixture</title>'}));
 await page.goto('http://127.0.0.1:9188/');
 await page.setContent('<!doctype html><html lang="ko"><head><style>:root{--text-main:#24343c;--text-sub:#687b84;--primary:#087f76;--primary-light:#edf8f6;--primary-border:#b6dbd6;--border:#dce5e8;--card:#fff;--surface:#f5f8f9}body{font:14px "Noto Sans CJK KR",sans-serif;background:#edf3f5}.modal{display:none;position:fixed;inset:0;background:#0005;align-items:center;justify-content:center}.modal.active{display:flex}.modal-body{border-radius:14px;background:white}.modal-head{display:flex;align-items:center;justify-content:space-between;padding:16px 22px;border-bottom:1px solid var(--border)}.modal-title{font-size:18px;font-weight:bold}.oa-close{border:0;background:none;font-size:18px}</style></head><body><button id="trigger">열기</button></body></html>');
 await page.addStyleTag({content:fs.readFileSync(path.join(root,'frontend/dist/usability_common.css'),'utf8')});
 await page.addStyleTag({content:fs.readFileSync(path.join(root,'frontend/dist/dinner_admin.css'),'utf8')});
 await page.addScriptTag({content:fs.readFileSync(path.join(root,'frontend/dist/usability_common.js'),'utf8')});
 await page.addScriptTag({content:`window.SYSTEM_MODE='ADMIN';window.MODAL_CLOSERS=[];
 window.fixture={dutyDataVersion:7,role:{id:'role1',memberName:'김하늘',startDate:'2026-01-01',endDate:'2027-12-31',revision:1,active:true},connections:[{id:'conn1',memberName:'김하늘',deviceLabel:'CA-PC',verificationCode:'123456',status:'PENDING'}],members:['김하늘','이서연','박준호'],polls:[]};
 window.calls=[];window.failNext=false;window.failManageOnce=false;window.nextId=1;window.calendarRefreshes=0;window.toasts=[];window.showToast=text=>toasts.push(text);window.fetchAllData=async()=>{calendarRefreshes++;return true;};
 window.OnAirDinner={getName:()=> '김하늘',esc:OnAirUI.escape,credentials:async()=>({deviceLabel:'CA-PC'}),refresh:async()=>{},api:async(path,options={})=>{calls.push({path,options:structuredClone(options)});if(failNext){failNext=false;throw Error('시험 네트워크 오류');}if(path==='/manage'){if(failManageOnce){failManageOnce=false;throw Error('집계 조회 오류');}return structuredClone(fixture);}if(path.startsWith('/summary'))return {role:fixture.role,roles:fixture.roles,assignedRole:fixture.assignedRole,ownConnections:fixture.ownConnections,connection:fixture.viewerConnection||fixture.connections[0]};const b=options.body||{};if(path==='/connections'){fixture.viewerConnection={id:'future-pc',roleId:b.roleId,memberName:b.memberName,deviceLabel:b.deviceLabel,status:'PENDING',verificationCode:'654321'};if(fixture.ownConnections)fixture.ownConnections[String(b.roleId)]=fixture.viewerConnection;return {connection:fixture.viewerConnection};}if(path==='/role'&&fixture.roles){const previous=fixture.roles.find(r=>r.roleId===b.roleId);if(b.expectedRevision!==(previous?.revision||0))throw Error('역할 revision 충돌');const next={id:'ca',...b,revision:(previous?.revision||0)+1,active:b.startDate<='2026-11-17'&&b.endDate>='2026-11-17'&&!!b.memberName};if(b.memberName&&fixture.roles.some(r=>r.roleId!==b.roleId&&r.memberName&&r.startDate<=b.endDate&&r.endDate>=b.startDate))throw Error('다른 CA 임기와 겹칩니다. 기존 임기를 먼저 조정해 주세요.');fixture.roles=fixture.roles.filter(r=>r.roleId!==b.roleId).concat(next);fixture.role=fixture.roles.find(r=>r.active)||null;return {role:next};}if(path==='/role'){if(b.expectedRevision!==fixture.role.revision)throw Error('역할 revision 충돌');fixture.role={id:'role1',...b,revision:fixture.role.revision+1,active:!!b.memberName};if(!b.memberName){fixture.role.startDate='';fixture.role.endDate='';fixture.connections=[];}return {role:fixture.role};}if(path.includes('/decision')){fixture.connections[0].status=b.decision==='APPROVE'?'APPROVED':'REJECTED';return {connection:fixture.connections[0]};}if(path==='/polls'){const p={...b,id:'p'+nextId++,revision:1,status:'DRAFT',createdAt:'2026-11-17',results:{responders:[],candidates:[],nonresponders:b.targets}};fixture.polls.push(p);return {poll:structuredClone(p)};}const p=fixture.polls.find(p=>path.includes('/'+p.id));if(options.method==='PATCH'){Object.assign(p,b);p.revision++;if(p.results.responders.length){p.results.responders.forEach(r=>r.complete=false);p.results.confirmationRequired=p.results.responders.map(r=>r.name);p.results.nonresponders=[];}return {poll:structuredClone(p)};}if(path.endsWith('/action')){if(b.action==='CONFIRM'&&b.baseVersion!==fixture.dutyDataVersion)throw Error('근무 자료 버전 충돌');p.revision++;if(b.action==='PUBLISH')p.status='OPEN';if(b.action==='CLOSE')p.status='CLOSED';if(b.action==='EXTEND'){p.status='OPEN';p.deadline=b.deadline;}if(b.action==='CONFIRM'){p.status='CONFIRMED';p.selectedCandidateId=b.selectedCandidateId;p.venue=b.venue;p.notice=b.notice;p.dutyEffect={date:p.candidates.find(c=>c.id===b.selectedCandidateId).date,memo:'석식간담회',noDuty:true,appliedAt:'2026-11-17T12:00:00Z'};fixture.dutyDataVersion++;}if(b.action==='CANCEL'){p.status='CANCELLED';p.notice=b.notice;}return {poll:structuredClone(p)};}throw Error('Unexpected '+path);}};`});
 await page.addScriptTag({content:fs.readFileSync(path.join(root,'frontend/dist/dinner_admin.js'),'utf8')});
 const click=async action=>{await page.locator('#onair-dinner-admin [data-da="'+action+'"]').first().click();await page.waitForFunction(()=>!document.querySelector('#onair-dinner-admin [data-da-was-disabled]'));};
 const accept=async()=>{await page.locator('#onair-dinner-confirm [data-da="accept"]').click();await page.waitForFunction(()=>!document.querySelector('#onair-dinner-confirm [data-da="accept"]:disabled'));};
 const main=page.locator('#onair-dinner-admin');
 await page.evaluate(()=>OnAirDinnerAdmin.openRole());
 assert.equal(await main.locator('#da-ca-name').inputValue(),'김하늘');
 await main.locator('#da-term-start').fill('2024-02-29');await main.locator('[data-years="1"]').click();
 assert.equal(await main.locator('#da-term-end').inputValue(),'2025-02-28');
 await click('connection-review');await accept();assert.match(await page.locator('#da-confirm-error').innerText(),/확인/);
 await page.locator('#da-confirm-check').check();await accept();await main.locator('[data-da="connection-tab"][data-tab="approved"]').click();assert.equal(await main.locator('.da-connected').count(),1);
 await page.evaluate(()=>OnAirDinnerAdmin.open());await click('new');
 await main.locator('#da-title').fill('12월 부서 회식');await main.locator('#da-description').fill('일정 확인 부탁드립니다.');
 await main.locator('#da-deadline').fill('2027-01-10T17:00');
 const dateInputs=main.locator('[data-da-date]');await dateInputs.nth(0).fill('2027-01-15');await dateInputs.nth(1).fill('2027-01-16');
 const originalIds=await dateInputs.evaluateAll(nodes=>nodes.map(n=>n.dataset.daDate));
 await main.locator('[data-delta="1"]').click();assert.equal(await main.locator('#da-title').inputValue(),'12월 부서 회식');assert.equal(await page.evaluate(()=>document.activeElement?.dataset.delta),'1');
 await click('targets');await page.locator('#da-target-search').fill('이서연');assert.equal(await page.locator('#onair-dinner-confirm [data-da-person]:visible').count(),1);
 await page.locator('#onair-dinner-confirm [data-da="target-apply"]').click();
 await page.evaluate(()=>{failNext=true;});await click('save-draft');assert.match(await main.locator('#da-error').innerText(),/네트워크/);assert.equal(await main.locator('#da-title').inputValue(),'12월 부서 회식');
 const failedOp=await page.evaluate(()=>calls.at(-1).options.body.operationId);
 await page.evaluate(()=>{failManageOnce=true;});await click('save-draft');
 assert.equal(await page.evaluate(()=>fixture.polls.length),1);
 assert.equal(await page.evaluate(()=>calls.findLast(c=>c.path==='/polls').options.body.operationId),failedOp);
 assert.deepEqual(await page.evaluate(()=>fixture.polls[0].candidates.map(c=>c.id)),originalIds);
 await click('publish');await accept();assert.match(await main.innerText(),/응답 중/);
 await page.evaluate(()=>{fixture.polls[0].results={responders:fixture.members.map(name=>({name,complete:true,answers:{},suggestion:''})),candidates:[],nonresponders:[],confirmationRequired:[]};});
 await page.evaluate(()=>OnAirDinnerAdmin.open());await click('detail');
 await click('edit');await main.locator('[data-da-time]').nth(0).fill('19:00');await click('save-draft');await page.locator('#da-confirm-check').check();await accept();
 assert.deepEqual(await page.evaluate(()=>fixture.polls[0].candidates.map(c=>c.id)),originalIds);
 assert.equal(await main.locator('[data-da="remind"]').isEnabled(),true);assert.equal(await main.locator('.da-name-list small').count(),3);
 await click('remind');await page.locator('#da-action-notice').fill('응답 부탁드립니다.');await accept();assert.equal(await page.evaluate(()=>calls.filter(c=>c.path.endsWith('/action')).at(-1).options.body.action),'REMIND');
 await click('close-poll');await accept();assert.match(await main.innerText(),/마감 · 집계 중/);
 await click('extend');await page.locator('#da-new-deadline').fill('2027-01-11T17:00');await accept();assert.match(await main.innerText(),/응답 중/);
 for(const width of [1920,1024,360]){await page.setViewportSize({width,height:900});const overflow=await main.locator('.modal-body').evaluate(n=>({scroll:n.scrollWidth,client:n.clientWidth}));assert.ok(overflow.scroll<=overflow.client+1,'No horizontal overflow at '+width+': '+JSON.stringify(overflow));}
 await page.setViewportSize({width:1280,height:900});await page.evaluate(()=>{fixture.dutyDataVersion=29;});await click('finalize');
 const confirmation=page.locator('#onair-dinner-confirm');
 assert.match(await confirmation.innerText(),/하루에만.*석식간담회 메모를 추가/);assert.match(await confirmation.innerText(),/배정된 근무자를 제거/);assert.match(await confirmation.innerText(),/다른 날짜의 배정과 순번은 변경하지/);
 await accept();assert.match(await page.locator('#da-confirm-error').innerText(),/체크/);assert.equal(await page.evaluate(()=>calls.filter(c=>c.options.body?.action==='CONFIRM').length),0);
 await page.locator('#da-venue').fill('부서 회식 장소');
 for(const width of [1024,390,320]){await page.setViewportSize({width,height:900});const g=await confirmation.locator('.modal-body').evaluate(n=>({s:n.scrollWidth,c:n.clientWidth}));assert.ok(g.s<=g.c+1,'Confirmation no horizontal overflow at '+width);const boxes=await confirmation.locator('.da-footer button').evaluateAll(nodes=>nodes.map(n=>({top:n.getBoundingClientRect().top,height:n.getBoundingClientRect().height})));assert.equal(boxes[0].top,boxes[1].top,'Confirmation buttons align at '+width);assert.equal(boxes[0].height,boxes[1].height);}
 await page.screenshot({path:'/tmp/onair-dinner-confirm-320.png'});await page.setViewportSize({width:1280,height:900});await page.screenshot({path:'/tmp/onair-dinner-confirm.png'});
 await page.locator('#da-confirm-check').check();await accept();assert.match(await main.innerText(),/날짜 확정/);assert.match(await main.innerText(),/확정 시 이 날짜에 석식간담회 메모와 당직 없음을 반영했습니다/);assert.equal(await page.evaluate(()=>calls.findLast(c=>c.options.body?.action==='CONFIRM').options.body.baseVersion),29);assert.equal(await page.evaluate(()=>calendarRefreshes),1);
 await page.screenshot({path:'/tmp/onair-dinner-admin-results.png'});
 await click('cancel-poll');assert.match(await confirmation.innerText(),/석식간담회 메모와 당직 없음 처리는 유지/);assert.match(await confirmation.innerText(),/근무자는 자동으로 복원하지/);assert.match(await confirmation.innerText(),/관리자가 달력에서 직접 조정/);await page.locator('#da-action-notice').fill('일정 재조정으로 취소합니다.');await page.locator('#da-confirm-check').check();await accept();assert.match(await main.innerText(),/일정 재조정으로 취소합니다/);assert.match(await main.innerText(),/근무자는 자동으로 복원하지/);assert.equal(await main.locator('.da-result-card .da-pill').innerText(),'기존 확정 날짜');assert.equal(await main.locator('.da-result-card .da-pill.good').count(),0);assert.equal(await page.evaluate(()=>calendarRefreshes),1);
 await page.evaluate(()=>{const legacy=structuredClone(fixture.polls[0]);legacy.id='legacy';legacy.status='CONFIRMED';delete legacy.dutyEffect;fixture.polls.push(legacy);});await page.evaluate(()=>OnAirDinnerAdmin.open());await main.locator('[data-filter="past"]').click();await main.locator('[data-da="detail"][data-id="legacy"]').click();assert.doesNotMatch(await main.innerText(),/석식간담회.*반영했습니다/);await click('cancel-poll');assert.doesNotMatch(await confirmation.innerText(),/근무자는 자동으로 복원하지/);await confirmation.locator('[data-da="dismiss"]').click();
 await page.evaluate(()=>OnAirDinnerAdmin.openRole());await page.screenshot({path:'/tmp/onair-dinner-admin-role.png'});
 for(const width of [1024,360]){await page.setViewportSize({width,height:900});const g=await main.locator('.modal-body').evaluate(n=>({s:n.scrollWidth,c:n.clientWidth}));assert.ok(g.s<=g.c+1,'CA screen no horizontal overflow at '+width);}
 await page.setViewportSize({width:1280,height:900});
 await page.evaluate(()=>OnAirDinnerAdmin.open());await click('new');await page.screenshot({path:'/tmp/onair-dinner-admin-create.png'});
 for(const width of [1024,360]){await page.setViewportSize({width,height:900});const g=await main.locator('.modal-body').evaluate(n=>({s:n.scrollWidth,c:n.clientWidth}));assert.ok(g.s<=g.c+1,'Create screen no horizontal overflow at '+width);}
 await page.setViewportSize({width:1280,height:900});await main.locator('#da-title').fill('게시 전 초안');await click('save-draft');await click('cancel-poll');assert.match(await page.locator('#onair-dinner-confirm').innerText(),/직원에게 표시하거나 알리지 않습니다/);assert.equal(await page.locator('#onair-dinner-confirm textarea, #onair-dinner-confirm input[type=checkbox]').count(),0);await accept();assert.match(await main.innerText(),/취소/);
 await page.evaluate(()=>OnAirDinnerAdmin.openRole());await click('remove-role');await accept();assert.equal(await main.locator('.da-section-head .da-pill').first().innerText(),'미지정');assert.equal(await main.locator('[data-da="remove-role"]').count(),0);assert.equal(await main.locator('[data-da="save-role"]').innerText(),'CA 지정');
 await page.evaluate(()=>OnAirDinnerAdmin.open());assert.match(await main.innerText(),/담당자 미지정/);assert.doesNotMatch(await main.innerText(),/임기 종료/);
 await page.evaluate(()=>OnAirDinnerAdmin.openRole());await main.locator('#da-ca-name').selectOption('이서연');await click('save-role');assert.equal(await page.locator('#onair-dinner-confirm-title').innerText(),'CA 지정 내용 확인');await accept();assert.equal(await page.evaluate(()=>calls.findLast(c=>c.path==='/role').options.body.expectedRevision),2);assert.equal(await main.locator('#da-ca-name').inputValue(),'이서연');
 // Current and next year have independent appointment revisions and PC queues.
 await page.evaluate(()=>{fixture.role={id:'ca',roleId:1,memberName:'김하늘',startDate:'2026-01-01',endDate:'2026-12-31',revision:8,active:true};fixture.roles=[fixture.role];fixture.connections=[{id:'current-pc',roleId:1,memberName:'김하늘',deviceLabel:'올해 PC',verificationCode:'111111',status:'PENDING'}];});
 await page.evaluate(()=>OnAirDinnerAdmin.openRole());
 assert.equal(await main.locator('.da-year-card').count(),2);
 assert.match(await main.locator('.da-year-card').nth(0).innerText(),/올해.*2026년[\s\S]*김하늘[\s\S]*임기중[\s\S]*승인대기/);
 assert.match(await main.locator('.da-year-card').nth(1).innerText(),/내년.*2027년[\s\S]*담당자 미지정[\s\S]*연결미요청/);
 await main.locator('[data-year="2027"]').click();
 assert.equal(await main.locator('#da-term-start').inputValue(),'2027-01-01');assert.equal(await main.locator('#da-term-end').inputValue(),'2027-12-31');
 assert.doesNotMatch(await main.locator('.da-approval-panel').innerText(),/올해 PC|111111/);
 await main.locator('#da-ca-name').selectOption('이서연');
 await main.locator('[data-year="2026"]').click();assert.equal(await main.locator('#da-ca-name').inputValue(),'김하늘');
 await main.locator('[data-year="2027"]').click();assert.equal(await main.locator('#da-ca-name').inputValue(),'이서연','Unsaved edits survive year selection');
 await click('save-role');await accept();
 const annual=await page.evaluate(()=>({body:calls.findLast(c=>c.path==='/role').options.body,current:fixture.role,next:fixture.roles.find(r=>r.roleId===2027)}));
 assert.equal(annual.body.roleId,2027);assert.equal(annual.body.expectedRevision,0);assert.equal(annual.current.memberName,'김하늘');assert.equal(annual.current.revision,8);assert.equal(annual.next.memberName,'이서연');
 assert.match(await main.locator('[data-year="2027"]').innerText(),/이서연[\s\S]*예정/);
 await page.evaluate(()=>{fixture.connections.push(...Array.from({length:7},(_,i)=>({id:'next-'+i,roleId:2027,memberName:'이서연',deviceLabel:'내년 PC '+i,verificationCode:'22222'+i,status:i<5?'PENDING':i===5?'APPROVED':'INVALIDATED'})));});
 await page.evaluate(()=>OnAirDinnerAdmin.openRole());await main.locator('[data-year="2027"]').click();
 assert.equal(await main.locator('.da-connection').count(),2);assert.doesNotMatch(await main.locator('.da-approval-panel').innerText(),/올해 PC/);
 await main.locator('[data-da="connection-page"][data-page="1"]').click();assert.match(await main.locator('.da-connections').innerText(),/내년 PC 2/);
 await main.locator('[data-da="connection-tab"][data-tab="approved"]').click();assert.match(await main.locator('.da-connections').innerText(),/승인완료·임기대기/);
 await main.locator('[data-da="connection-tab"][data-tab="history"]').click();assert.match(await main.locator('.da-connections').innerText(),/연결 무효/);assert.doesNotMatch(await main.locator('.da-connections').innerText(),/INVALIDATED/);
 await main.locator('[data-da="connection-tab"][data-tab="pending"]').click();
 for(const viewport of [{width:1920,height:1080},{width:1366,height:768},{width:320,height:768}]){
  await page.setViewportSize(viewport);const geometry=await main.locator('.modal-body').evaluate(n=>({sw:n.scrollWidth,cw:n.clientWidth,sh:n.scrollHeight,ch:n.clientHeight}));
  assert.ok(geometry.sw<=geometry.cw+1,'Annual CA no horizontal overflow '+JSON.stringify(viewport)+' '+JSON.stringify(geometry));
  if(viewport.width>1000)assert.ok(geometry.sh<=geometry.ch+1,'Annual CA no vertical scroll '+JSON.stringify(viewport)+' '+JSON.stringify(geometry));
  await page.screenshot({path:'/tmp/onair-ca-years-'+viewport.width+'.png'});
 }
 await page.setViewportSize({width:1366,height:768});
 // Approval reloads must not upgrade the revision belonging to preserved unsaved edits.
 await main.locator('#da-ca-name').selectOption('박준호');await main.locator('[data-year="2026"]').click();await click('connection-review');
 await page.evaluate(()=>{const next=fixture.roles.find(r=>r.roleId===2027);next.memberName='김하늘';next.revision=2;});
 await page.locator('#da-confirm-check').check();await accept();await main.locator('[data-year="2027"]').click();
 assert.equal(await main.locator('#da-ca-name').inputValue(),'박준호');assert.match(await main.locator('.da-draft-conflict').innerText(),/입력 내용은 보존/);
 await click('save-role');await accept();assert.match(await page.locator('#da-confirm-error').innerText(),/revision.*입력 내용은 보존/);
 assert.equal(await page.evaluate(()=>calls.findLast(c=>c.path==='/role').options.body.expectedRevision),1);assert.equal(await page.evaluate(()=>fixture.roles.find(r=>r.roleId===2027).memberName),'김하늘');
 await confirmation.locator('[data-da="dismiss"]').click();assert.equal(await main.locator('#da-ca-name').inputValue(),'박준호');await click('role-refresh');assert.equal(await main.locator('#da-ca-name').inputValue(),'김하늘');assert.equal(await main.locator('.da-draft-conflict').count(),0);
 await page.evaluate(()=>{const next=fixture.roles.find(r=>r.roleId===2027);next.memberName='이서연';next.revision=1;});await page.evaluate(()=>OnAirDinnerAdmin.openRole());await main.locator('[data-year="2027"]').click();
 // A conflicting edit stays reviewable and cannot overwrite the current appointment.
 await main.locator('#da-term-start').fill('2026-12-01');await click('save-role');await accept();
 assert.match(await page.locator('#da-confirm-error').innerText(),/기존 임기를 먼저 조정/);assert.equal(await page.evaluate(()=>fixture.role.revision),8);
 await confirmation.locator('[data-da="dismiss"]').click();await main.locator('#da-term-start').fill('2027-01-01');
 await click('remove-role');await accept();
 const removed=await page.evaluate(()=>({body:calls.findLast(c=>c.path==='/role').options.body,current:fixture.role}));assert.equal(removed.body.roleId,2027);assert.equal(removed.body.expectedRevision,1);assert.equal(removed.current.memberName,'김하늘');assert.equal(removed.current.revision,8);
 // A two-year appointment is one editable role on both cards, never an implicit replacement.
 await page.evaluate(()=>{fixture.roles=[{...fixture.role,endDate:'2027-12-31'}];});await page.evaluate(()=>OnAirDinnerAdmin.openRole());await main.locator('[data-year="2027"]').click();
 assert.equal(await main.locator('#da-term-start').inputValue(),'2026-01-01');assert.equal(await main.locator('#da-ca-name').inputValue(),'김하늘');
 await click('save-role');await accept();assert.equal(await page.evaluate(()=>calls.findLast(c=>c.path==='/role').options.body.roleId),1);
 // Custom dates can put two distinct appointments in one calendar year; both remain editable.
 await page.evaluate(()=>{fixture.role={id:'ca',roleId:1,memberName:'김하늘',startDate:'2026-01-01',endDate:'2027-06-30',revision:8,active:true};fixture.roles=[fixture.role,{id:'ca',roleId:2027,memberName:'이서연',startDate:'2027-07-01',endDate:'2027-12-31',revision:4,active:false}];fixture.connections=[{id:'first-half',roleId:1,memberName:'김하늘',deviceLabel:'상반기 PC',status:'PENDING'},{id:'second-half',roleId:2027,memberName:'이서연',deviceLabel:'하반기 PC',status:'PENDING'}];});
 await page.evaluate(()=>OnAirDinnerAdmin.openRole());await main.locator('[data-year="2027"]').click();assert.equal(await main.locator('#da-role-appointment option').count(),2);assert.equal(await main.locator('#da-ca-name').inputValue(),'김하늘');
 await main.locator('#da-role-appointment').selectOption('2027');assert.equal(await main.locator('#da-ca-name').inputValue(),'이서연');assert.equal(await main.locator('#da-term-start').inputValue(),'2027-07-01');assert.match(await main.locator('.da-connections').innerText(),/하반기 PC/);assert.doesNotMatch(await main.locator('.da-connections').innerText(),/상반기 PC/);
 await click('save-role');await accept();assert.equal(await page.evaluate(()=>calls.findLast(c=>c.path==='/role').options.body.roleId),2027);assert.equal(await page.evaluate(()=>calls.findLast(c=>c.path==='/role').options.body.expectedRevision),4);assert.equal(await page.evaluate(()=>fixture.roles.find(r=>r.roleId===1).revision),8);assert.equal(await main.locator('#da-role-appointment').inputValue(),'2027');
 await main.locator('#da-role-appointment').selectOption('1');assert.equal(await main.locator('#da-term-end').inputValue(),'2027-06-30');assert.match(await main.locator('.da-connections').innerText(),/상반기 PC/);
 // Future assignees can request and be approved before their term, but cannot open management.
 await page.evaluate(()=>{SYSTEM_MODE='VIEWER';fixture.assignedRole={id:'ca',roleId:2027,memberName:'김하늘',startDate:'2027-01-01',endDate:'2027-12-31',revision:1,active:false};fixture.connections=[];fixture.viewerConnection=null;});
 await page.evaluate(()=>OnAirDinnerAdmin.openRole());assert.match(await main.innerText(),/임기 전에.*사용 연결을 요청/);await click('connect');
 assert.equal(await page.evaluate(()=>calls.findLast(c=>c.path==='/connections').options.body.roleId),2027);assert.match(await main.innerText(),/관리자 확인 대기/);
 await page.evaluate(()=>{fixture.viewerConnection.status='APPROVED';});await page.evaluate(()=>OnAirDinnerAdmin.openRole());assert.match(await main.innerText(),/승인완료·임기시작대기/);assert.equal(await main.locator('[data-da="reload"]').count(),0);
 // An employee appointed in both years can select the future term independently on the same PC.
 await page.evaluate(()=>{fixture.role={id:'ca',roleId:2026,memberName:'김하늘',startDate:'2026-01-01',endDate:'2026-12-31',revision:1,active:true};const next={...fixture.role,roleId:2027,startDate:'2027-01-01',endDate:'2027-12-31',active:false};fixture.roles=[fixture.role,next];fixture.assignedRole=fixture.role;fixture.ownConnections={'2026':{id:'this-pc',roleId:2026,memberName:'김하늘',status:'APPROVED'}};fixture.viewerConnection=fixture.ownConnections['2026'];});
 await page.evaluate(()=>OnAirDinnerAdmin.open());assert.equal(await main.locator('[data-da="role"]').innerText(),'CA 사용 연결');await click('role');assert.equal(await main.locator('[data-da="viewer-role"]').count(),2);assert.equal(await main.locator('[data-da="reload"]').count(),1);
 await main.locator('[data-da="viewer-role"][data-role-id="2027"]').click();assert.equal(await main.locator('[data-da="reload"]').count(),0);assert.equal(await main.locator('[data-da="connect"]').innerText(),'이 PC 사용 연결 요청');await click('connect');
 assert.equal(await page.evaluate(()=>calls.findLast(c=>c.path==='/connections').options.body.roleId),2027);assert.match(await main.innerText(),/관리자 확인 대기/);assert.match(await main.locator('[data-da="viewer-role"][aria-pressed="true"]').innerText(),/2027/);
 await page.evaluate(()=>{fixture.ownConnections['2027'].status='APPROVED';});await page.evaluate(()=>OnAirDinnerAdmin.openRole());assert.match(await main.innerText(),/승인완료·임기시작대기/);assert.equal(await main.locator('[data-da="reload"]').count(),0);
 await main.locator('[data-da="viewer-role"][data-role-id="2026"]').click();assert.equal(await main.locator('[data-da="reload"]').count(),1);assert.equal(await page.evaluate(()=>fixture.ownConnections['2026'].status),'APPROVED');
 await page.setViewportSize({width:320,height:768});const viewerGeometry=await main.locator('.modal-body').evaluate(n=>({sw:n.scrollWidth,cw:n.clientWidth}));assert.ok(viewerGeometry.sw<=viewerGeometry.cw+1,'Viewer appointment choice fits 320px');
 assert.deepEqual(errors,[]);console.log(JSON.stringify({status:'PASS',checks,screenshots:['/tmp/onair-dinner-confirm.png','/tmp/onair-dinner-confirm-320.png','/tmp/onair-dinner-admin-results.png','/tmp/onair-dinner-admin-role.png','/tmp/onair-dinner-admin-create.png','/tmp/onair-ca-years-1920.png','/tmp/onair-ca-years-1366.png','/tmp/onair-ca-years-320.png']}));
 await browser.close();
})().catch(async e=>{console.error(e);if(browser)await browser.close();process.exitCode=1;});
