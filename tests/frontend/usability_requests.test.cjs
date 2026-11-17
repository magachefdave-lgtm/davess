'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'../..');
const html=fs.readFileSync(path.join(root,'frontend/dist/index.html'),'utf8');
const base=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m=>m[1]).find(s=>s.includes('function renderTodayCard()'));
function app(){
 const nodes=new Map(),local=new Map(),session=new Map();
 const node=id=>{if(!nodes.has(id)){const classes=new Set();nodes.set(id,{id,isConnected:true,events:{},value:'',checked:false,disabled:false,hidden:false,innerHTML:'',textContent:'',style:{},dataset:{},options:[],children:[],scrollTop:0,scrollLeft:0,classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle(x,v){v?classes.add(x):classes.delete(x)}},setAttribute(k,v){this[k]=v},removeAttribute(k){delete this[k]},appendChild(o){this.children.push(o);this.options.push(o);o.parentNode=this;return o},insertAdjacentHTML(){},append(){},insertBefore(o){this.appendChild(o)},addEventListener(type,fn){(this.events[type]??=[]).push(fn)},dispatchEvent(event){for(const fn of this.events[event.type]||[])fn(event)},querySelector(){return null},querySelectorAll(){return []},focus(){document.activeElement?.blurHandler?.();document.activeElement=this},scrollIntoView(){},remove(){},closest(){return null}});}return nodes.get(id)};
 const storage=map=>({getItem:k=>map.has(k)?map.get(k):null,setItem:(k,v)=>map.set(k,String(v)),removeItem:k=>map.delete(k)});
 const document={getElementById:node,querySelectorAll:()=>[],querySelector:()=>null,events:{},addEventListener(type,fn){(this.events[type]??=[]).push(fn)},dispatchEvent(event){for(const fn of this.events[event.type]||[])fn(event)},createElement:tag=>({...node('new-'+nodes.size),tagName:tag}),head:node('head'),body:node('body'),activeElement:null};
 const ctx=vm.createContext({console,Date,document,window:{addEventListener(){}},localStorage:storage(local),sessionStorage:storage(session),setTimeout:fn=>{fn();return 0},clearTimeout(){},setInterval:()=>0,clearInterval(){},requestAnimationFrame:fn=>fn(),confirm:()=>true,alert:()=>{throw Error('generic alert forbidden')}});
 vm.runInContext(base,ctx);const run=s=>vm.runInContext(s,ctx);
 run(`SYSTEM_MODE='VIEWER';SERVER_CONNECTED=true;dataLoaded=true;todayStr='2026-10-13';myName='A';
 memberList=[{name:'A',gender:'F'},{name:'B',gender:'F'},{name:'C',gender:'M'}];absenceTypes=['연차','교육'];
 closedMonths=[];absences=[];scheduleMap={'2026-10-14':[{name:'A'},{name:'B'}],'2026-10-15':[{name:'A'}]};
 showToast=()=>{};addNotif=()=>{};checkRequestOutcomes=()=>{};renderCalendar=()=>{};renderAbsenceList=()=>{};renderAbsenceTypeUI=()=>{};updateAbsenceDateLabels=()=>{};openVacancyModal=()=>{};offerUndo=()=>{};
 globalThis.sent=[];globalThis.response={ok:true,status:200,body:{receiptToken:'own-token',requestId:12}};
 fetch=async(url,options={})=>{sent.push({url,...options});return {ok:response.ok,status:response.status,json:async()=>response.body}};
 fetchWithAdminToken=async(url,options={})=>{sent.push({url,...options});return {ok:response.ok,status:response.status,json:async()=>response.body}};
 syncFullDataToBackend=async p=>{sent.push(JSON.parse(JSON.stringify(p)));return true};
 window.OnAirUI={escape:esc,dateLabel:x=>x,open:(id)=>{document.getElementById(id).classList.add('active')},close:id=>document.getElementById(id).classList.remove('active')};`);
 vm.runInContext(fs.readFileSync(path.join(root,'frontend/dist/usability_requests.js'),'utf8'),ctx);
 return {run,node,ctx,local,session,json:s=>JSON.parse(run('JSON.stringify('+s+')'))};
}
function fillAbsence(a){for(const [id,v]of Object.entries({'absence-req-name':'A','absence-req-type':'교육','absence-req-start':'2026-10-14','absence-req-end':'2026-10-15','absence-req-reason':'참석 신청'}))a.node(id).value=v;}
function fillAdminAbsence(a){a.run('SYSTEM_MODE="ADMIN"');for(const[id,v]of Object.entries({'abs-name':'A','abs-type':'교육','abs-start':'2026-10-14','abs-end':'2026-10-15'}))a.node(id).value=v;}
test('vacation date plans split around weekends and the current holiday calendar only',()=>{
 const a=app();a.run("finalHolidays={'2026-11-11':{name:'사내 공휴일'}}");
 const plan=a.json("window.OnAirRequests.adminDatePlan('연차/휴가','2026-11-06','2026-11-16')");
 assert.deepEqual(plan.ranges,[{start:'2026-11-06',end:'2026-11-06'},{start:'2026-11-09',end:'2026-11-10'},{start:'2026-11-12',end:'2026-11-13'},{start:'2026-11-16',end:'2026-11-16'}]);assert.equal(plan.days,6);assert.deepEqual(plan.excluded,['2026-11-07','2026-11-08','2026-11-11','2026-11-14','2026-11-15']);
 a.run('finalHolidays={}');assert.equal(a.json("window.OnAirRequests.adminDatePlan('연차','2026-11-06','2026-11-16')").days,7);
 for(const type of ['교육','출장','파견'])assert.deepEqual(a.json(`window.OnAirRequests.adminDatePlan('${type}','2026-11-06','2026-11-16')`).ranges,[{start:'2026-11-06',end:'2026-11-16'}]);
});
test('vacation bulk conflicts and freed duties ignore excluded holiday dates including record zero',()=>{
 const a=app();a.run(`absences=[{id:0,name:'A',type:'교육',start:'2026-11-07',end:'2026-11-08'}];scheduleMap={'2026-11-06':[{name:'A'}],'2026-11-07':[{name:'A'}],'2026-11-09':[{name:'A'}]}`);
 let plan=a.json("window.OnAirRequests.bulkPlan(['A'],'연차','2026-11-06','2026-11-09')");assert.deepEqual(plan.addNames,['A']);assert.deepEqual(plan.freedDates,['2026-11-06','2026-11-09']);
 a.run("absences[0].start='2026-11-06'");plan=a.json("window.OnAirRequests.bulkPlan(['A'],'연차','2026-11-06','2026-11-09')");assert.deepEqual(plan.duplicates,['A']);
});
test('weekend-only vacation is rejected without saving or confirmation',async()=>{
 const a=app();fillAdminAbsence(a);a.node('abs-type').value='연차';a.node('abs-start').value='2026-11-07';a.node('abs-end').value='2026-11-08';a.run("globalThis.confirmCalls=0;askConfirm=async()=>{confirmCalls++;return true}");await a.run('saveAbsenceForm()');assert.equal(a.run('confirmCalls'),0);assert.equal(a.json('sent').length,0);assert.equal(a.node('abs-start')['aria-invalid'],'true');
});
test('holiday changes during confirmation preserve the draft and prevent a stale save',async()=>{
 const a=app();fillAdminAbsence(a);a.node('abs-type').value='연차';a.run('askConfirm=()=>new Promise(resolve=>globalThis.resolveConfirm=resolve)');const pending=a.run('saveAbsenceForm()');a.run("finalHolidays={'2026-10-14':{name:'추가 휴일'}};resolveConfirm(true)");await pending;assert.equal(a.json('sent').length,0);assert.equal(a.node('abs-start').value,'2026-10-14');
});
test('vacation save persists only working-day segments while keeping weekend duties',async()=>{
 const a=app();fillAdminAbsence(a);a.node('abs-type').value='연차';a.node('abs-start').value='2026-11-06';a.node('abs-end').value='2026-11-09';a.run("scheduleMap={'2026-11-06':[{name:'A'}],'2026-11-07':[{name:'A'}],'2026-11-09':[{name:'A'}]};askConfirm=async()=>true");await a.run('saveAbsenceForm()');const payload=a.json('sent')[0];assert.deepEqual(payload.absences.map(a=>[a.start,a.end]),[['2026-11-06','2026-11-06'],['2026-11-09','2026-11-09']]);assert.equal(payload.schedules['2026-11-07'][0].name,'A');assert.deepEqual(payload.schedules['2026-11-06'],[]);assert.deepEqual(payload.schedules['2026-11-09'],[]);
});
test('viewer confirmation reports effective vacation days and does not send after a holiday change',async()=>{
 const a=app();fillAbsence(a);a.node('absence-req-type').value='연차';a.node('absence-req-start').value='2026-11-06';a.node('absence-req-end').value='2026-11-09';a.run('globalThis.confirmMessage="";askConfirm=opts=>{confirmMessage=opts.message;return new Promise(resolve=>globalThis.resolveConfirm=resolve)}');const pending=a.run('submitAbsenceRequest()');assert.match(a.run('confirmMessage'),/실제 휴가 2일 · 토·일·공휴일 2일 제외/);a.run("finalHolidays={'2026-11-09':{name:'추가 공휴일'}};resolveConfirm(true)");await pending;assert.equal(a.json('sent').filter(x=>x.method==='POST').length,0);assert.equal(a.node('absence-req-end').value,'2026-11-09');
});
test('new admin schedule selects an exact or unique partial name from the search',()=>{
 const a=app();a.run("SYSTEM_MODE='ADMIN';memberList=[{name:'김민수'},{name:'김민서'},{name:'박지훈'}]");
 for(const [query,name]of [[' 김민수 ','김민수'],['지훈','박지훈']]){
  a.node('abs-search').value=query;a.run('startNewAbsence()');assert.equal(a.node('abs-name').value,name);assert.equal(a.run('absenceEditDirty()'),false);
 }
});
test('ambiguous or non-name search never assigns an arbitrary employee',()=>{
 const a=app();a.run("SYSTEM_MODE='ADMIN';memberList=[{name:'김민수'},{name:'김민서'}]");
 for(const query of ['김민','교육','없는사람','']){a.node('abs-search').value=query;a.run('startNewAbsence()');assert.equal(a.node('abs-name').value,'');assert.equal(a.node('abs-name').options.find(o=>o.value==='').textContent,'사원을 선택하세요');}
});
test('new schedule preserves a draft when discard is declined or a save is in progress',()=>{
 const a=app();fillAdminAbsence(a);a.run('openAbsenceModal()');a.node('abs-end').value='2026-12-31';a.node('abs-search').value='B';a.run('confirm=()=>false');const before=a.json('[editingAbsenceId,document.getElementById("abs-name").value,document.getElementById("abs-end").value]');
 a.run('startNewAbsence()');assert.deepEqual(a.json('[editingAbsenceId,document.getElementById("abs-name").value,document.getElementById("abs-end").value]'),before);
 a.run('confirm=()=>true;isSaving=true;startNewAbsence()');assert.equal(a.node('abs-end').value,'2026-12-31');
});
test('absence save confirmation locks second submission and releases lock after cancellation',async()=>{
 const a=app();fillAdminAbsence(a);a.run('globalThis.confirmCalls=0;askConfirm=()=>{confirmCalls++;return new Promise(resolve=>globalThis.resolveConfirm=resolve)}');
 const first=a.run('saveAbsenceForm()');await a.run('saveAbsenceForm()');assert.equal(a.run('confirmCalls'),1);assert.equal(a.run('absenceFormSaving'),true);assert.equal(a.node('abs-submit-btn').disabled,true);
 a.run('resolveConfirm(false)');await first;assert.equal(a.json('sent').length,0);assert.equal(a.run('absenceFormSaving'),false);assert.equal(a.node('abs-submit-btn').disabled,false);
});
for(const change of ['dataVersion++',"closedMonths=['2026-10']","scheduleMap['2026-10-15'].push({name:'B'})"])
 test('absence rejects data changed during confirmation: '+change,async()=>{
  const a=app();fillAdminAbsence(a);a.run('askConfirm=()=>new Promise(resolve=>globalThis.resolveConfirm=resolve)');const pending=a.run('saveAbsenceForm()');a.run(change);a.run('resolveConfirm(true)');await pending;
  assert.equal(a.json('sent').length,0);assert.equal(a.run('absences.length'),0);assert.equal(a.node('abs-start').value,'2026-10-14');assert.equal(a.run('absenceFormSaving'),false);
 });
test('absence confirmation reacquires an unchanged record after object refresh',async()=>{
 const a=app();a.run(`SYSTEM_MODE='ADMIN';absences=[{id:1,name:'A',type:'교육',start:'2026-10-14',end:'2026-10-14',education:{}}];beginAbsenceEdit(1)`);a.node('abs-end').value='2026-10-15';
 a.run('askConfirm=()=>new Promise(resolve=>globalThis.resolveConfirm=resolve)');const pending=a.run('saveAbsenceForm()');a.run('absences=JSON.parse(JSON.stringify(absences));resolveConfirm(true)');await pending;
 assert.equal(a.json('sent')[0].absences[0].end,'2026-10-15');assert.equal(a.json('absences')[0].end,'2026-10-15');
});
test('new absence draft cancellation preserves education, dates and active editor',()=>{
 const a=app();a.run('SYSTEM_MODE="ADMIN";openAbsenceModal();startNewAbsence()');fillAdminAbsence(a);a.node('ur-admin-edu-title').value='작성 중 교육';a.run('confirm=()=>false;closeAbsenceModal()');
 assert.equal(a.node('absence-modal').classList.contains('active'),true);assert.equal(a.node('ur-admin-edu-title').value,'작성 중 교육');assert.equal(a.node('abs-end').value,'2026-10-15');
 a.run('startNewAbsence()');assert.equal(a.node('ur-admin-edu-title').value,'작성 중 교육');assert.equal(a.node('abs-name').value,'A');
 a.run('confirm=()=>true;startNewAbsence()');assert.equal(a.node('ur-admin-edu-title').value,'');assert.equal(a.run('absenceEditDirty()'),false);
});
test('absence editor fields stay disabled during network save and restore after success',async()=>{
 const a=app();fillAdminAbsence(a);const ids=['abs-name','abs-type','abs-start','abs-end','ur-admin-edu-title','abs-submit-btn'];
 a.ctx.document.querySelectorAll=s=>s.startsWith('#abs-editor')?ids.map(a.node):[];
 a.run('syncFullDataToBackend=p=>new Promise(resolve=>{sent.push(JSON.parse(JSON.stringify(p)));globalThis.finishSave=resolve})');const pending=a.run('saveAbsenceForm()');await new Promise(resolve=>setImmediate(resolve));
 for(const id of ids)assert.equal(a.node(id).disabled,true,id);a.run('finishSave(true)');await pending;
 for(const id of ids)assert.equal(a.node(id).disabled,false,id);assert.equal(a.run('absenceFormSaving'),false);
});
test('absence delete confirmation blocks repeated deletion and changed record is retained',async()=>{
 const a=app();a.run(`SYSTEM_MODE='ADMIN';absences=[{id:1,name:'A',type:'연차',start:'2026-10-14',end:'2026-10-14'}];globalThis.confirmCalls=0;askConfirm=()=>{confirmCalls++;return new Promise(resolve=>globalThis.resolveConfirm=resolve)}`);
 const pending=a.run('delAbsence(1)');await a.run('delAbsence(1)');assert.equal(a.run('confirmCalls'),1);a.run("absences[0].end='2026-10-15';resolveConfirm(true)");await pending;
 assert.equal(a.run('absences.length'),1);assert.equal(a.json('absences')[0].end,'2026-10-15');assert.equal(a.json('sent').length,0);assert.equal(a.run('absenceFormSaving'),false);
});
const amendmentOriginal={id:900,name:'A',type:'교육',start:'2026-10-14',end:'2026-10-15',education:{title:'원래 교육',location:'본관'}};
const approvedAbsence={id:70,status:'APPROVED',request_type:'ABSENCE',requester_name:'A',target_date:'2026-10-14',absence_end_date:'2026-10-15',absence_type:'교육',absence_details:{title:'원래 교육',location:'본관'},reason:'원 신청 사유',can_amend:true,amendable_absence:amendmentOriginal};
test('grouped cancellation pending remains in its original row with child action targets',async()=>{
 const parent={...approvedAbsence,can_cancel:false,can_amend:false,pending_cancellation_id:71};
 const child={...approvedAbsence,id:71,request_type:'ABSENCE_CANCEL',status:'PENDING',amends_request_id:70,reason:'개인 취소 사유',created_at:'2026-10-13 09:00:00'};
 const a=amendmentApp({'parent-token':parent,'child-token':child},[child,parent]);await a.run('window.OnAirRequests.openMyRequests("A")');
 const html=a.node('myreq-tbody').innerHTML;
 assert.equal((html.match(/<tr data-ur-request-id=/g)||[]).length,1);
 assert.match(html,/취소 승인 대기/);assert.match(html,/승인 전까지 일정 유지/);
 assert.match(html,/editById\(71\)/);assert.match(html,/withdrawById\(71\)/);
 assert.match(html,/aria-expanded="false"/);assert.match(html,/data-ur-history-request-id="71"/);
 assert.ok(a.json('sent').some(r=>r.url.includes('grouped=1')));
});
test('grouped cancellation rejection preserves approval and keeps private reason in the owned timeline',async()=>{
 const child={...approvedAbsence,id:71,request_type:'ABSENCE_CANCEL',status:'REJECTED',amends_request_id:70,processed_at:'2026-10-13 10:00:00',reject_reason:'취소 <반려> 사유'};
 const a=amendmentApp({'parent-token':approvedAbsence,'child-token':child},[child,approvedAbsence]);await a.run('window.OnAirRequests.openMyRequests("A")');
 const html=a.node('myreq-tbody').innerHTML;
 assert.equal((html.match(/<tr data-ur-request-id=/g)||[]).length,1);
 assert.match(html,/ur-result-approved/);assert.match(html,/취소 반려 · 기존 일정 유지/);
 assert.match(html,/취소 &lt;반려&gt; 사유/);assert.match(html,/amendById\(70\)/);
 const other=amendmentApp({},[child,approvedAbsence]);await other.run('window.OnAirRequests.openMyRequests("A")');assert.doesNotMatch(other.node('myreq-tbody').innerHTML,/취소 &lt;반려&gt; 사유/);
});
test('grouping does not hide orphan cancellations or merge different people and amendment requests',async()=>{
 const orphan={...approvedAbsence,id:71,request_type:'ABSENCE_CANCEL',status:'PENDING',amends_request_id:999};
 const stranger={...orphan,id:72,requester_name:'B',amends_request_id:70};
 const amendment={...approvedAbsence,id:73,status:'PENDING',amends_request_id:70};
 const a=amendmentApp({},[orphan,stranger,amendment,approvedAbsence]);await a.run('window.OnAirRequests.openMyRequests("A")');
 assert.equal((a.node('myreq-tbody').innerHTML.match(/<tr data-ur-request-id=/g)||[]).length,4);
});
test('later approved amendments supersede historical cancellation rejection or withdrawal in owner and name views',async()=>{
 for(const status of ['REJECTED','CANCELLED'])for(const owner of [true,false]){
  const parent={...approvedAbsence,superseded_by_id:72,can_amend:false};
  const cancellation={...approvedAbsence,id:71,request_type:'ABSENCE_CANCEL',status,amends_request_id:70};
  const amendment={...approvedAbsence,id:72,amends_request_id:70,amendment_original:amendmentOriginal};
  const a=amendmentApp(owner?{'parent-token':parent,'cancel-token':cancellation,'amend-token':amendment}:{},[amendment,cancellation,parent]);
  await a.run('window.OnAirRequests.openMyRequests("A")');const html=a.node('myreq-tbody').innerHTML;
  const original=html.split('<tr data-ur-request-id="70"')[1].split('</tr>')[0];
  assert.match(original,/다른 승인 일정으로 변경됨/);assert.doesNotMatch(original,/취소 반려 · 기존 일정 유지|취소 요청 철회 · 기존 일정 유지/);
  assert.equal((html.match(/<tr data-ur-request-id=/g)||[]).length,2);
 }
});
function amendmentApp(records={'parent-token':approvedAbsence},publicRows=Object.values(records)){
 const a=app();a.ctx.ownerRecords=records;a.ctx.publicRows=publicRows;
 a.run(`myRequestStatuses=Object.fromEntries(Object.entries(ownerRecords).map(([k,r])=>[k,r.status]));saveMyRequestStatuses();globalThis.notices=[];addNotif=(text,meta)=>notices.push({text,meta});
 fetch=async(url,options={})=>{sent.push({url,...options});if(options.method==='POST')return {ok:response.ok,status:response.status,json:async()=>response.body};
 return {ok:true,status:200,json:async()=>url.includes('/request-status/')?{request:ownerRecords[decodeURIComponent(url.split('/').at(-1))]}:{requests:publicRows}}};`);
 return a;
}
test('my requests renders labeled cells, readable timestamps and explicit approved/rejected result badges',async()=>{
 const rows=['APPROVED','REJECTED','PENDING','CANCELLED'].map((status,index)=>({...approvedAbsence,id:80+index,status,created_at:'2026-11-04 09:25:30',processed_at:index<2?'2026-11-04 10:15:00':null,reject_reason:status==='REJECTED'?'일정 <확인>\n다시 신청해 주세요.':''}));
 const a=amendmentApp({},rows);await a.run('window.OnAirRequests.openMyRequests("A")');
 const rendered=a.node('myreq-tbody').innerHTML;
 for(const [status,label]of [['approved','승인'],['rejected','반려'],['pending','승인 대기'],['cancelled','철회']])assert.match(rendered,new RegExp(`class="ur-result-badge ur-result-${status}">${label}</span>`));
 for(const field of ['신청 일시','구분','내용','결과','처리 일시','작업'])assert.equal((rendered.match(new RegExp(`data-label="${field}"`,'g'))||[]).length,4,field);
 assert.match(rendered,/<span>2026-11-04<\/span><span class="ur-myreq-clock">09:25:30<\/span>/);
 assert.match(rendered,/일정 &lt;확인&gt;\n다시 신청해 주세요\./);assert.doesNotMatch(rendered,/<확인>/);
 assert.match(rendered,/resubmitById\(81\)/);assert.match(rendered,/resubmitById\(83\)/);
});
test('my requests keeps decision badges separate from owned actions and preserves action targets',async()=>{
 const approved={...approvedAbsence,can_cancel:true,cancellable_absence:amendmentOriginal};
 const pending={...approvedAbsence,id:71,status:'PENDING'};
 const a=amendmentApp({'parent-token':approved,'pending-token':pending},[approved,pending]);
 await a.run('window.OnAirRequests.openMyRequests("A")');
 const rendered=a.node('myreq-tbody').innerHTML;
 const results=[...rendered.matchAll(/<td data-label="결과">([\s\S]*?)<\/td>/g)].map(x=>x[1]);
 assert.equal(results.length,2);for(const result of results){assert.match(result,/ur-result-badge/);assert.doesNotMatch(result,/<button|ur-row-actions/);}
 const actions=[...rendered.matchAll(/<td data-label="작업">([\s\S]*?)<\/td>/g)].map(x=>x[1]);
 assert.match(actions[0],/amendById\(70\)/);assert.match(actions[0],/cancelById\(70\)/);assert.match(actions[0],/>취소 요청<\/button>/);
 assert.match(actions[1],/editById\(71\)/);assert.match(actions[1],/withdrawById\(71\)/);
});
test('completed cancellation exposes its owned history without exposing controls to another PC or administrator',async()=>{
 const child={...approvedAbsence,id:71,request_type:'ABSENCE_CANCEL',status:'APPROVED',amends_request_id:70,amendment_original:amendmentOriginal};
 for(const mode of ['owner','other','admin']){
  const a=amendmentApp(mode==='other'?{}:{'child-token':child},[child]);if(mode==='admin')a.run("SYSTEM_MODE='ADMIN'");
  await a.run('window.OnAirRequests.openMyRequests("A")');const rendered=a.node('myreq-tbody').innerHTML;
  if(mode==='owner'){assert.match(rendered,/editById\(71\)/);assert.match(rendered,/>취소 내역<\/button>/);assert.doesNotMatch(rendered,/withdrawById|resubmitById|cancelById/);}
  else assert.doesNotMatch(rendered,/editById|withdrawById|resubmitById|cancelById/);
 }
});
test('my requests message rows span all columns for empty, loading, unselected and error states',async()=>{
 const a=amendmentApp({},[]);await a.run('window.OnAirRequests.openMyRequests("A")');assert.match(a.node('myreq-tbody').innerHTML,/<td colspan="6">신청 내역이 없습니다/);
 a.node('myreq-name').value='';await a.run('loadMyRequests()');assert.match(a.node('myreq-tbody').innerHTML,/<td colspan="6">이름을 선택/);
 a.node('myreq-name').value='A';a.run('fetch=()=>new Promise(resolve=>globalThis.finishRequest=resolve)');const loading=a.run('loadMyRequests()');assert.match(a.node('myreq-tbody').innerHTML,/<td colspan="6">불러오는 중/);
 a.run('finishRequest({ok:false,status:500,json:async()=>({detail:"일시 오류"})})');await loading;assert.match(a.node('myreq-tbody').innerHTML,/<td colspan="6">/);
});
test('my requests uses owned reasons and education while retaining server-provided education compatibility',async()=>{
 const privateRecord={...approvedAbsence,reason:'개인 <신청>\n두 번째 줄',absence_details:{title:'<교육명>',notes:'준비 & 안내'}};
 const publicRecord={...approvedAbsence,reason:'public reason must not be used',absence_details:{title:'public education must not be used'}};
 const otherRecord={...publicRecord,id:99};
 const a=amendmentApp({'parent-token':privateRecord},[publicRecord,otherRecord]);await a.run('window.OnAirRequests.openMyRequests("A")');
 const rendered=a.node('myreq-tbody').innerHTML;
 assert.match(rendered,/신청 사유/);assert.match(rendered,/개인 &lt;신청&gt;\n두 번째 줄/);assert.match(rendered,/&lt;교육명&gt;/);assert.match(rendered,/준비 &amp; 안내/);
 assert.doesNotMatch(rendered,/public reason|<신청>|<교육명>/);
 assert.equal((rendered.match(/public education must not be used/g)||[]).length,1,'existing server-provided education remains on the other row');
 assert.equal((rendered.match(/ur-myreq-reason/g)||[]).length,1);
 assert.match(rendered,/amendById\(70\)/);assert.doesNotMatch(rendered,/amendById\(99\)/);
});
test('my requests escapes unknown results, malformed timestamps and long request fields',async()=>{
 const row={...approvedAbsence,request_type:'SUBSTITUTE',status:'<img src=x onerror=alert(1)>',substitute_name:'<script>대체자</script>',created_at:'<img src=x>',reject_reason:'<svg onload=alert(1)>'};
 const a=amendmentApp({},[row]);await a.run('window.OnAirRequests.openMyRequests("A")');const rendered=a.node('myreq-tbody').innerHTML;
 assert.match(rendered,/class="ur-result-badge ur-result-unknown">&lt;img/);
 assert.match(rendered,/&lt;script&gt;대체자&lt;\/script&gt;/);assert.match(rendered,/&lt;svg/);assert.doesNotMatch(rendered,/<img|<script|<svg\s+onload/);
});
test('approved absence exposes amendment only for an owned receipt and locks original applicant',async()=>{
 const a=amendmentApp(undefined,[approvedAbsence,{...approvedAbsence,id:99}]);await a.run('window.OnAirRequests.openMyRequests("A")');
 assert.match(a.node('myreq-tbody').innerHTML,/amendById\(70\)/);assert.doesNotMatch(a.node('myreq-tbody').innerHTML,/amendById\(99\)/);
 await a.run('window.OnAirRequests.amendById(70)');assert.equal(a.node('absence-req-name').value,'A');assert.equal(a.node('absence-req-name').disabled,true);assert.equal(a.node('absence-req-start').value,amendmentOriginal.start);assert.equal(a.node('ur-req-edu-location').value,'본관');assert.match(a.node('absence-request-title').textContent,/변경 요청/);
});
test('administrator never receives approved-absence amendment buttons from retained viewer receipts',async()=>{
 const a=amendmentApp();a.run("SYSTEM_MODE='ADMIN'");await a.run('window.OnAirRequests.openMyRequests("A")');assert.doesNotMatch(a.node('myreq-tbody').innerHTML,/amendById/);
});
test('amendment comparison displays old and new schedule and escaped education without mutating DTO',()=>{
 const a=app();a.ctx.review={...approvedAbsence,amends_request_id:70,amendment_original:amendmentOriginal,target_date:'2026-11-20',absence_end_date:'2026-11-21',absence_details:{location:'<img src=x>'}};
 const before=JSON.stringify(a.ctx.review),rendered=a.run('window.OnAirRequests.amendmentComparison(review,true)');
 for(const text of ['변경 전','변경 요청','2026-10-14','2026-11-20','본관','&lt;img'])assert.ok(rendered.includes(text),text);assert.doesNotMatch(rendered,/<img/);assert.equal(JSON.stringify(a.ctx.review),before);
});

test('approval comparison retains changed types, original split ranges and escaped education in the compact view',()=>{
 const a=app();a.ctx.review={request_type:'ABSENCE',amends_request_id:70,target_date:'2026-11-16',absence_end_date:'2026-11-18',absence_type:'교육',absence_details:{location:'<img src=x>'},amendment_original:{start:'2026-11-06',end:'2026-11-09',type:'연차',segments:[{start:'2026-11-06',end:'2026-11-06'},{start:'2026-11-09',end:'2026-11-09'}]}};
 const before=JSON.stringify(a.ctx.review),result=a.run('window.OnAirRequests.amendmentComparison(review,true,true)');
 for(const text of ['기존 승인','변경 요청','2026-11-06(금)','2026-11-09(월)','2026-11-16(월)','연차','교육','&lt;img'])assert.ok(result.includes(text),text);
 assert.doesNotMatch(result,/<img/);assert.equal(JSON.stringify(a.ctx.review),before);
 a.ctx.review.amendment_original.type='교육';a.ctx.review.absence_details={};
 assert.doesNotMatch(a.run('window.OnAirRequests.amendmentComparison(review,true,true)'),/<details/);
 assert.match(a.run('window.OnAirRequests.amendmentComparison(review,true)'),/<details/);
 a.ctx.review.amendment_original.education={location:'본관'};
 const cleared=a.run('window.OnAirRequests.amendmentComparison(review,true,true)');
 assert.match(cleared,/본관/);assert.match(cleared,/변경 요청 교육 안내/);assert.match(cleared,/등록된 교육 안내가 없습니다/);
});

test('approval cancellation keeps every real segment and effective day count without adding excluded weekend',()=>{
 const a=app();a.ctx.review={request_type:'ABSENCE_CANCEL',requester_name:'A',absence_type:'연차',target_date:'2026-11-06',absence_end_date:'2026-11-09',amendment_original:{start:'2026-11-06',end:'2026-11-09',type:'연차',segments:[{start:'2026-11-06',end:'2026-11-06'},{start:'2026-11-09',end:'2026-11-09'}]}};
 const result=a.run('window.OnAirRequests.cancellationDetails(review,true)');
 assert.match(result,/2026-11-06\(금\)/);assert.match(result,/2026-11-09\(월\)/);assert.match(result,/실제 2일/);
 assert.doesNotMatch(result,/2026-11-07|2026-11-08/);
});
test('approved amendment creates new receipt without changing original approved tracking',async()=>{
 const a=amendmentApp();await a.run('window.OnAirRequests.openMyRequests("A");');await a.run('window.OnAirRequests.amendById(70)');
 a.node('absence-req-start').value='2026-10-17';a.node('absence-req-end').value='2026-10-18';a.node('ur-req-edu-location').value='별관';
 a.run(`response.body={requestId:71,receiptToken:'child-token'};`);await a.run('submitAbsenceRequest()');
 const posts=a.json('sent').filter(r=>r.method==='POST');assert.equal(posts.length,1);const payload=JSON.parse(posts[0].body);assert.equal(payload.action,'AMEND');assert.equal(payload.receipt_token,'parent-token');assert.equal(payload.request.absence_details.location,'별관');
 assert.equal(a.run('myRequestStatuses["parent-token"]'),'APPROVED');assert.equal(a.run('myRequestStatuses["child-token"]'),'PENDING');assert.equal(a.json('notices').at(-1).meta.receiptToken,'child-token');
});
test('amendment conflicts retain editable draft instead of treating normal APPROVED parent as processed error',async()=>{
 const a=amendmentApp();await a.run('window.OnAirRequests.openMyRequests("A")');await a.run('window.OnAirRequests.amendById(70)');
 a.node('absence-req-start').value='2026-10-20';a.node('absence-req-end').value='2026-10-21';a.run(`response={ok:false,status:409,body:{detail:'기존 일정이 다른 곳에서 변경되었습니다.'}}`);await a.run('submitAbsenceRequest()');
 assert.equal(a.node('absence-request-modal').classList.contains('active'),true);assert.equal(a.node('ur-request-state').classList.contains('active'),false);assert.equal(JSON.parse(a.session.get('sd_request_draft_absence_v370')).context.amendToken,'parent-token');assert.equal(a.node('absence-req-start').value,'2026-10-20');
});
test('rejected amendment retry keeps original receipt and proposed fields rather than creating unrelated absence',async()=>{
 const child={...approvedAbsence,id:71,status:'REJECTED',amends_request_id:70,amendment_original:amendmentOriginal,target_date:'2026-10-20',absence_end_date:'2026-10-21',reason:'변경 사유',absence_details:{location:'별관'},can_amend:false};
 const a=amendmentApp({'parent-token':approvedAbsence,'child-token':child});await a.run('window.OnAirRequests.openMyRequests("A")');await a.run('window.OnAirRequests.resubmitById(71)');
 assert.equal(a.node('absence-req-start').value,'2026-10-20');assert.equal(a.node('ur-req-edu-location').value,'별관');assert.equal(a.node('absence-req-name').disabled,true);
 a.run(`response.body={requestId:72,receiptToken:'next-child'}`);await a.run('submitAbsenceRequest()');const post=a.json('sent').find(r=>r.method==='POST');assert.equal(JSON.parse(post.body).action,'AMEND');assert.equal(JSON.parse(post.body).receipt_token,'parent-token');
});
test('rejected amendment cannot fall back to a name-only new absence when original receipt is missing',async()=>{
 const child={...approvedAbsence,id:71,status:'REJECTED',amends_request_id:70,amendment_original:amendmentOriginal,can_amend:false};const a=amendmentApp({'child-token':child});await a.run('window.OnAirRequests.openMyRequests("A")');await a.run('window.OnAirRequests.resubmitById(71)');
 assert.equal(a.node('absence-request-modal').classList.contains('active'),false);assert.equal(a.json('sent').filter(r=>r.method==='POST').length,0);
});
test('owner status recovers linked amendment receipt after lost submit response',async()=>{
 const child={...approvedAbsence,id:71,status:'PENDING',amends_request_id:70,amendment_original:amendmentOriginal,can_amend:false};
 const parent={...approvedAbsence,can_amend:false,pending_amendment_id:71,linked_amendment:{id:71,status:'PENDING',receiptToken:'recovered-child'}};
 const a=amendmentApp({'parent-token':parent});a.ctx.ownerRecords['recovered-child']=child;a.ctx.publicRows=[parent,child];await a.run('window.OnAirRequests.openMyRequests("A")');
 assert.equal(a.run('myRequestStatuses["recovered-child"]'),'PENDING');assert.match(a.node('myreq-tbody').innerHTML,/editById\(71\)/);
 await a.run('window.OnAirRequests.editById(71)');const draft=JSON.parse(a.session.get('sd_request_draft_absence_v370'));assert.equal(draft.context.editToken,'recovered-child');assert.equal(draft.context.amendsRequestId,70);assert.equal(draft.context.original.id,900);
});
for(const decided of ['REJECTED','CANCELLED'])test(`lost response recovery retains retry ownership after ${decided}`,async()=>{
 const child={...approvedAbsence,id:71,status:decided,amends_request_id:70,amendment_original:amendmentOriginal,target_date:'2026-10-20',absence_end_date:'2026-10-21',can_amend:false};
 const parent={...approvedAbsence,linked_amendment:{id:71,status:decided,receiptToken:'recovered-terminal'}};
 const a=amendmentApp({'parent-token':parent});a.ctx.ownerRecords['recovered-terminal']=child;a.ctx.publicRows=[parent,child];
 await a.run('window.OnAirRequests.openMyRequests("A")');
 assert.equal(a.run('Object.hasOwn(myRequestStatuses,"recovered-terminal")'),true);
 assert.match(a.node('myreq-tbody').innerHTML,/resubmitById\(71\)/);
 await a.run('window.OnAirRequests.resubmitById(71)');
 assert.equal(a.node('absence-req-start').value,'2026-10-20');
 assert.equal(JSON.parse(a.session.get('sd_request_draft_absence_v370')).context.amendToken,'parent-token');
});
test('terminal recovery does not label a blocked original as superseded by an approved change',async()=>{
 const child={...approvedAbsence,id:71,status:'REJECTED',amends_request_id:70,can_amend:false};
 const parent={...approvedAbsence,can_amend:false,amendment_blocker:'관리자가 원본을 삭제했습니다.',linked_amendment:{id:71,status:'REJECTED',receiptToken:'terminal-child'}};
 const a=amendmentApp({'parent-token':parent,'terminal-child':child});await a.run('window.OnAirRequests.openMyRequests("A")');
 assert.match(a.node('myreq-tbody').innerHTML,/관리자가 원본을 삭제했습니다/);
 assert.doesNotMatch(a.node('myreq-tbody').innerHTML,/다른 승인 일정으로 변경됨|최신 승인 보기/);
});
test('absence draft survives close and restore; personal name fills only new draft',()=>{
 const a=app();a.run('openAbsenceRequestModal()');assert.equal(a.node('absence-req-name').value,'A');fillAbsence(a);a.run('closeAbsenceRequestModal(); myName="B"; openAbsenceRequestModal()');assert.equal(a.node('absence-req-name').value,'A');assert.equal(a.node('absence-req-reason').value,'참석 신청');assert.equal(a.node('absence-req-start').value,'2026-10-14');
});
test('invalid fields focus first field and prevent HTTP without generic alert',async()=>{
 const a=app();a.run('openAbsenceRequestModal()');fillAbsence(a);a.node('absence-req-name').value='';await a.run('submitAbsenceRequest()');assert.equal(a.json('sent').length,0);assert.equal(a.node('absence-req-name')['aria-invalid'],'true');assert.equal(a.ctx.document.activeElement.id,'absence-req-name');
});
test('all education fields can be empty while reason remains required',async()=>{
 const a=app();a.run('openAbsenceRequestModal()');fillAbsence(a);await a.run('submitAbsenceRequest()');const body=JSON.parse(a.json('sent')[0].body);assert.equal(body.reason,'참석 신청');assert.deepEqual(body.absence_details,{title:'',location:'',provider:'',preparation:'',content:'',notes:''});assert.equal(a.session.has('sd_request_draft_absence_v370'),false);
});
// [위험] v3.18.1 사용자 결정 — 휴가·부재 신청의 사유는 선택 입력이다. 비워도 접수되어야 하고, 사유 칸에 * 표시가 붙으면 안 된다.
test('absence request can be submitted with an empty reason and the reason field is not marked required',async()=>{
 const a=app();a.run('openAbsenceRequestModal()');fillAbsence(a);a.node('absence-req-reason').value='';await a.run('submitAbsenceRequest()');
 const sent=a.json('sent').filter(x=>x.url==='/api/duty/request-change');assert.equal(sent.length,1,'사유 없이도 신청이 나가야 합니다');assert.equal(JSON.parse(sent[0].body).reason,'');
 assert.notEqual(a.node('absence-req-reason')['aria-required'],'true');assert.notEqual(a.node('absence-req-reason')['aria-invalid'],'true');
});
test('failed submission keeps draft and education for reopening',async()=>{
 const a=app();a.run('openAbsenceRequestModal()');fillAbsence(a);a.node('ur-req-edu-location').value='회의실';a.run('response={ok:false,status:409,body:{detail:"겹침"}}');await a.run('submitAbsenceRequest()');a.run('closeAbsenceRequestModal();openAbsenceRequestModal()');assert.equal(a.node('absence-req-reason').value,'참석 신청');assert.equal(a.node('ur-req-edu-location').value,'회의실');
});
test('identity confirmation includes selected name and cancellation leaves draft',async()=>{
 const a=app();a.run('openAbsenceRequestModal()');fillAbsence(a);a.run('globalThis.confirmText="";confirm=t=>{confirmText=t;return false}');await a.run('submitAbsenceRequest()');assert.match(a.run('confirmText'),/A/);assert.equal(a.json('sent').length,0);assert.ok(a.session.has('sd_request_draft_absence_v370'));
});
test('bulk plan previews overlaps, unique names, freed duties and closed months',()=>{
 const a=app();a.run(`absences=[{id:1,name:'A',type:'교육',start:'2026-10-13',end:'2026-10-14'}];closedMonths=['2026-11'];`);
 const plan=a.json(`window.OnAirRequests.bulkPlan(['A','B','B'],'교육','2026-10-14','2026-11-01')`);assert.deepEqual(plan.addNames,['B']);assert.deepEqual(plan.duplicates,['A']);assert.deepEqual(plan.freedDates,['2026-10-14']);assert.deepEqual(plan.closedMonths,['2026-11']);
});
test('bulk registration saves all names and freed duties once atomically',async()=>{
 const a=app();a.run('SYSTEM_MODE="ADMIN";editingAbsenceId=null');for(const[id,v]of Object.entries({'abs-name':'A','abs-type':'교육','abs-start':'2026-10-14','abs-end':'2026-10-15'}))a.node(id).value=v;
 a.node('ur-bulk-toggle').checked=true;a.ctx.document.querySelectorAll=s=>s==='[data-ur-bulk-name]:checked'?[{value:'A'},{value:'B'}]:[];
 await a.run('saveAbsenceForm()');const sent=a.json('sent');assert.equal(sent.length,1);assert.deepEqual(sent[0].absences.map(x=>x.name),['A','B']);assert.deepEqual(sent[0].schedules['2026-10-14'],[]);assert.deepEqual(sent[0].schedules['2026-10-15'],[]);
});
test('closed-month bulk attempt cannot mutate or call save',async()=>{
 const a=app();a.run('SYSTEM_MODE="ADMIN";closedMonths=["2026-10"]');for(const[id,v]of Object.entries({'abs-name':'A','abs-type':'연차','abs-start':'2026-10-14','abs-end':'2026-10-15'}))a.node(id).value=v;await a.run('saveAbsenceForm()');assert.deepEqual(a.json('absences'),[]);assert.equal(a.json('sent').length,0);
});
test('education-only edit retains id and does not release existing duty',async()=>{
 const a=app();a.run(`SYSTEM_MODE='ADMIN';absences=[{id:7,name:'A',type:'교육',start:'2026-10-14',end:'2026-10-15',education:{title:'원래 교육',location:'본관'}}];beginAbsenceEdit(7)`);a.node('ur-admin-edu-location').value='별관';await a.run('saveAbsenceForm()');const sent=a.json('sent');assert.equal(sent.length,1);assert.equal(sent[0].absences[0].id,7);assert.equal(sent[0].absences[0].education.title,'원래 교육');assert.equal(sent[0].absences[0].education.location,'별관');assert.equal(sent[0].schedules,undefined);
});
test('filter and sorting search reasons without changing queue DTOs',()=>{
 const a=app();a.run(`globalThis.queue=[{id:1,status:'PENDING',request_type:'SWAP',requester_name:'A',reason:'병원',created_at:'2026-10-02'},{id:2,status:'PENDING',request_type:'ABSENCE',requester_name:'B',reason:'교육',created_at:'2026-10-01'},{id:3,status:'APPROVED',request_type:'ABSENCE',reason:'병원'}];globalThis.before=JSON.stringify(queue)`);
 assert.deepEqual(a.json(`window.OnAirRequests.filterApproval(queue,{query:'병원',type:'ALL',sort:'newest'}).map(x=>x.id)`),[1]);assert.deepEqual(a.json(`window.OnAirRequests.filterApproval(queue,{type:'ALL',sort:'oldest'}).map(x=>x.id)`),[2,1]);assert.equal(a.run('JSON.stringify(queue)===before'),true);
});
test('management rejects an unowned receipt token before any HTTP call',async()=>{
 const a=app();await a.run('window.OnAirRequests.withdraw("not-owned")');assert.equal(a.json('sent').length,0);
});
test('owned withdrawal posts token action and marks cancellation',async()=>{
 const a=app();a.run(`myRequestStatuses={'own-token':'PENDING'};loadMyRequests=async()=>{}`);await a.run('window.OnAirRequests.withdraw("own-token")');const sent=a.json('sent');assert.equal(sent[0].url,'/api/duty/request/manage');assert.deepEqual(JSON.parse(sent[0].body),{receipt_token:'own-token',action:'WITHDRAW'});assert.equal(a.run('myRequestStatuses["own-token"]'),'CANCELLED');
});
test('owned edit posts full DTO with unchanged identity and education',async()=>{
 const a=app();a.run(`myRequestStatuses={'own-token':'PENDING'};window.OnAirRequests.openAbsence({requester_name:'A',request_type:'ABSENCE',target_date:'2026-10-14',absence_end_date:'2026-10-15',absence_type:'교육',reason:'수정 사유',absence_details:{notes:'안내'}},{editToken:'own-token'})`);await a.run('submitAbsenceRequest()');const sent=a.json('sent');assert.equal(sent[0].url,'/api/duty/request/manage');const body=JSON.parse(sent[0].body);assert.equal(body.action,'EDIT');assert.equal(body.request.requester_name,'A');assert.equal(body.request.absence_details.notes,'안내');
});
test('rejected resubmission opens populated form without network submission',()=>{
 const a=app();a.run(`window.OnAirRequests.resubmit({status:'REJECTED',request_type:'ABSENCE',requester_name:'B',target_date:'2026-10-14',absence_end_date:'2026-10-15',absence_type:'교육',reason:'기존 사유',absence_details:{title:'교육'}})`);assert.equal(a.node('absence-req-name').value,'B');assert.equal(a.node('absence-req-reason').value,'기존 사유');assert.equal(a.node('ur-req-edu-title').value,'교육');assert.equal(a.json('sent').length,0);
});
test('personal swap entry opens filled change form and keeps no submit side effect',()=>{
 const a=app();a.run(`window.OnAirRequests.openChange({requester_name:'A',request_type:'SWAP',target_date:'2026-10-14',swap_date:'2026-10-15',swap_worker:'B'})`);assert.equal(a.node('ur-change-name').value,'A');assert.equal(a.node('ur-change-date').value,'2026-10-14');assert.equal(a.node('ur-change-partner').value,'B');assert.equal(a.json('sent').length,0);
});
test('owned rejected retry reads private reason and education from receipt status, not name list',async()=>{
 const a=app();a.run(`myRequestStatuses={'own-token':'REJECTED'};globalThis.privateRequest={id:77,status:'REJECTED',request_type:'ABSENCE',requester_name:'A',target_date:'2026-10-14',absence_end_date:'2026-10-15',absence_type:'교육',reason:'비공개 신청 사유',absence_details:{title:'교육명',location:'실습실',provider:'센터',preparation:'노트북',content:'실습',notes:'안내'}};
 fetch=async url=>({ok:true,status:200,json:async()=>url.includes('/my-requests?')?{requests:[{id:77,status:'REJECTED',request_type:'ABSENCE',requester_name:'A',target_date:'2026-10-14',absence_end_date:'2026-10-15',absence_type:'교육'}]}:{request:privateRequest}});`);
 await a.run('window.OnAirRequests.openMyRequests("A")');a.run('window.OnAirRequests.resubmitById(77)');assert.equal(a.node('absence-req-reason').value,'비공개 신청 사유');for(const[key,text]of Object.entries({title:'교육명',location:'실습실',provider:'센터',preparation:'노트북',content:'실습',notes:'안내'}))assert.equal(a.node('ur-req-edu-'+key).value,text);assert.equal(a.node('absence-req-name').disabled,false);
});
test('declining discard on same-record edit preserves unsaved education',()=>{
 const a=app();a.run(`SYSTEM_MODE='ADMIN';absences=[{id:7,name:'A',type:'교육',start:'2026-10-14',end:'2026-10-15',education:{location:'본관'}}];beginAbsenceEdit(7)`);a.node('ur-admin-edu-location').value='아직 저장 안 한 장소';a.run('confirm=()=>false;beginAbsenceEdit(7)');assert.equal(a.node('ur-admin-edu-location').value,'아직 저장 안 한 장소');
});
test('editable change composer retains gender, duty-day and current assignment constraints',async()=>{
 for(const fixture of [
  {request_type:'SUBSTITUTE',requester_name:'A',target_date:'2026-10-14',substitute_name:'C',reason:'사유'},
  {request_type:'SUBSTITUTE',requester_name:'A',target_date:'2026-10-14',substitute_name:'B',reason:'사유'},
  {request_type:'SWAP',requester_name:'A',target_date:'2026-10-14',swap_date:'2026-10-17',swap_worker:'B',reason:'사유'},
  {request_type:'SWAP',requester_name:'A',target_date:'2026-10-16',swap_date:'2026-10-15',swap_worker:'B',reason:'사유'}
 ]){const a=app();a.run(`scheduleMap['2026-10-15']=[{name:'B'}];scheduleMap['2026-10-17']=[{name:'B'}];confirmConsecutiveRisk=()=>true;window.OnAirRequests.openChange(${JSON.stringify(fixture)})`);await a.run('window.OnAirRequests.submitChange()');assert.equal(a.json('sent').length,0,JSON.stringify(fixture));}
});
test('approval action posts the displayed revision even if queue refreshes during rejection input',async()=>{
 const a=app();a.run(`SYSTEM_MODE='ADMIN';approvalCache=[{id:91,status:'PENDING',request_type:'SUBSTITUTE',requester_name:'A',target_date:'2026-10-14',substitute_name:'B',revision:0}];askRejectReason=async()=>{approvalCache[0].revision=1;return '수정 필요'};fetchAllData=async()=>{};openApprovalModal=async()=>{}`);await a.run('processApproval(91,"REJECT")');const sent=a.json('sent');assert.equal(sent.length,1);assert.equal(JSON.parse(sent[0].body).request_revision,0);
});
test('reopening snapshots draft before legacy field reset triggers a textarea blur change',()=>{
 const a=app();a.run('openAbsenceRequestModal()');fillAbsence(a);a.node('ur-req-edu-notes').value='보존할 안내';a.run('closeAbsenceRequestModal()');const notes=a.node('ur-req-edu-notes');notes.blurHandler=()=>a.session.set('sd_request_draft_absence_v370',JSON.stringify({request:{requester_name:'',reason:'',absence_type:'연차'}}));a.ctx.document.activeElement=notes;a.run('openAbsenceRequestModal()');assert.equal(a.node('absence-req-name').value,'A');assert.equal(a.node('absence-req-reason').value,'참석 신청');assert.equal(a.node('ur-req-edu-notes').value,'보존할 안내');
});
test('approval checkbox survives unchanged refresh and resets only edited or processed request',async()=>{
 const a=app();a.run(`SYSTEM_MODE='ADMIN';updatePendingBadge=()=>{};globalThis.r={id:2,status:'PENDING',revision:0,request_type:'SUBSTITUTE',requester_name:'A',target_date:'2026-10-14',substitute_name:'B',reason:'원래 사유'};response.body={requests:[r],pendingCount:1}`);await a.run('openApprovalModal()');assert.equal(a.node('req-zero-2').checked,true);a.node('req-zero-2').checked=false;await a.run('openApprovalModal()');assert.equal(a.node('req-zero-2').checked,false);a.run(`response.body={requests:[{...r,revision:1,reason:'수정된 사유'}],pendingCount:1}`);await a.run('openApprovalModal()');assert.equal(a.node('req-zero-2').checked,true);a.node('req-zero-2').checked=false;a.run('response.body={requests:[{...r,status:"APPROVED"}],pendingCount:0}');await a.run('openApprovalModal()');a.run('response.body={requests:[r],pendingCount:1}');await a.run('openApprovalModal()');assert.equal(a.node('req-zero-2').checked,true);
});
test('viewing another date preserves authored composer draft and owned edit token',async()=>{
 const a=app();a.run(`scheduleMap={'2026-10-14':[{name:'A'}],'2026-10-15':[{name:'B'}]};myRequestStatuses={'own-token':'PENDING'};confirmConsecutiveRisk=()=>true;window.OnAirRequests.openChange({requester_name:'A',request_type:'SWAP',target_date:'2026-10-14',swap_date:'2026-10-15',swap_worker:'B',reason:'보존할 수정 초안'},{editToken:'own-token'});window.OnAirUI.close('ur-change-modal')`);
 const saved=a.session.get('sd_request_draft_change_v370');
 a.run(`openDateModal('2026-10-15');closeDateModal()`);
 assert.equal(a.session.get('sd_request_draft_change_v370'),saved);
 a.run('window.OnAirRequests.openChange()');await a.run('window.OnAirRequests.submitChange()');
 const sent=a.json('sent').filter(x=>x.method==='POST');assert.equal(sent.length,1);assert.equal(sent[0].url,'/api/duty/request/manage');
 const body=JSON.parse(sent[0].body);assert.equal(body.action,'EDIT');assert.equal(body.receipt_token,'own-token');assert.equal(body.request.reason,'보존할 수정 초안');
});
test('owned absence edit focuses first editable field and restores opener on close',()=>{
 const a=app();a.node('edit-request-button').focus();
 a.run(`window.OnAirRequests.openAbsence({requester_name:'A',request_type:'ABSENCE',target_date:'2026-10-14',absence_end_date:'2026-10-15',absence_type:'교육',reason:'수정'},{editToken:'own-token'})`);
 assert.equal(a.ctx.document.activeElement.id,'absence-req-type');assert.equal(a.node('absence-req-name').disabled,true);
 a.run('closeAbsenceRequestModal()');assert.equal(a.ctx.document.activeElement.id,'edit-request-button');
});

test('explicit legacy swap action transfers draft to composer without submitting',async()=>{
 const a=app();a.run("openDateModal('2026-10-14')");a.node('req-reason-input').value='명시적으로 이어 작성';a.node('swap-my-worker').value='A';a.node('swap-target-date').value='2026-10-15';a.node('swap-target-worker').value='B';
 a.run('closeDateModal()');a.run("openDateModal('2026-10-14')");await a.run('handleSwapAction()');
 assert.equal(a.node('ur-change-reason').value,'명시적으로 이어 작성');assert.equal(a.node('ur-change-name').value,'A');assert.equal(a.node('ur-change-partner').value,'B');assert.equal(a.json('sent').length,0);assert.equal(a.session.has('sd_request_draft_change_date_2026-10-14_v370'),false);
});

test('absence editor Tab trap wraps both directions and yields to a later active dialog',()=>{
 const a=app();a.run("openAbsenceRequestModal()");
 const modal=a.node('absence-request-modal'),first=a.node('absence-close'),last=a.node('btn-submit-absence-request');
 for(const field of [first,last]){field.tabIndex=0;field.getClientRects=()=>[{}];}
 modal.querySelectorAll=()=>[first,last];modal.contains=field=>field===first||field===last;
 const tab=shiftKey=>{let prevented=false;a.ctx.document.dispatchEvent({key:'Tab',type:'keydown',shiftKey,preventDefault(){prevented=true}});return prevented;};
 first.focus();assert.equal(tab(true),true);assert.equal(a.ctx.document.activeElement,last);
 assert.equal(tab(false),true);assert.equal(a.ctx.document.activeElement,first);
 a.node('behind-dialog').focus();assert.equal(tab(false),true);assert.equal(a.ctx.document.activeElement,first);
 a.run("MODAL_CLOSERS.push(['top-dialog',()=>{}]);document.getElementById('top-dialog').classList.add('active')");
 a.node('top-dialog-button').focus();assert.equal(tab(false),false);assert.equal(a.ctx.document.activeElement.id,'top-dialog-button');
 a.run("document.getElementById('top-dialog').classList.remove('active');closeAbsenceRequestModal()");
 a.node('behind-dialog').focus();assert.equal(tab(false),false);assert.equal(a.ctx.document.activeElement.id,'behind-dialog');
});

// [위험] v3.17.12 '내 신청 내역'은 교육 신청마다 "등록된 교육 안내가 없습니다"를 붙였다.
//   이름으로 조회하는 /api/duty/my-requests 는 교육 안내(absence_details)를 내려주지 않으므로
//   '값이 없다'와 '받지 않았다'를 구분해야 한다. 받은 경우에는 종전대로 안내 또는 '없음'을 보인다.
async function myRequestsWith(details){
 const a=app();const row={id:5,status:'PENDING',request_type:'ABSENCE',requester_name:'A',target_date:'2026-10-14',absence_end_date:'2026-10-15',absence_type:'교육'};
 if(details!==undefined)row.absence_details=details;
 a.run(`globalThis.myRow=${JSON.stringify(row)};fetch=async url=>({ok:true,status:200,json:async()=>url.includes('/my-requests?')?{requests:[myRow]}:{}});`);
 await a.run('window.OnAirRequests.openMyRequests("A")');return a.node('myreq-tbody').innerHTML;
}
test('my requests: education row without absence_details shows no false "no education info" note',async()=>{
 const html=await myRequestsWith(undefined);assert.match(html,/data-ur-request-id="5"/);assert.doesNotMatch(html,/등록된 교육 안내가 없습니다/);assert.doesNotMatch(html,/ur-education-details/);
});
test('my requests: education details are still shown when the server includes them',async()=>{
 const html=await myRequestsWith({title:'안전 교육',location:'본관'});assert.match(html,/ur-education-details/);assert.match(html,/안전 교육/);assert.match(html,/본관/);
 assert.match(await myRequestsWith({}),/등록된 교육 안내가 없습니다/);
});
// [위험] 서버가 같은 숫자로 길이를 막는다(신청 사유 500, 교육 안내 500·내용/메모 2000, 이름 50).
//   화면 제한이 서버보다 길면 입력을 다 해 놓고 제출에서 거절된다.
test('request inputs carry the shared maxlength limits',()=>{
 const a=app();a.run(`document.getElementById('absence-request-modal').querySelector=sel=>sel==='#absence-impact'?{insertAdjacentHTML:(pos,html)=>{globalThis.eduHtml=html;}}:null;openAbsenceRequestModal();`);
 const edu=a.ctx.eduHtml;assert.ok(edu,'education fields were inserted');
 for(const key of ['title','location','provider','preparation','content','notes'])assert.match(edu,new RegExp(`id="ur-req-edu-${key}"[^>]*maxlength="${['content','notes'].includes(key)?2000:500}"`),key);
 a.run(`window.OnAirUI.open=(id,title,body)=>{globalThis.changeBody=body;document.getElementById(id).classList.add('active');return document.getElementById(id)};window.OnAirRequests.openChange({requester_name:'A',request_type:'SWAP',target_date:'2026-10-14'})`);
 assert.match(a.ctx.changeBody,/<textarea id="ur-change-reason"[^>]*maxlength="500"/);
 const tag=id=>html.match(new RegExp(`<(?:input|textarea)[^>]*id="${id}"[^>]*>`))?.[0]||'';
 for(const [id,len] of [['absence-req-reason',500],['req-reason-input',500],['new-member-name',4],['new-abs-type-input',50],['hol-name',50],['modal-date-comment',1000]])assert.match(tag(id),new RegExp(`maxlength="${len}"`),id);
});

// ── v3.18.0 조회용 신청 흐름 ───────────────────────────────────────
// [위험] 날짜 칸 → 날짜 창의 신청 양식 → '맞교환 승인 요청' → 같은 내용을 다시 묻는 두 번째 양식이 열렸다.
//   당직 없는 날을 눌러도 '내 근무자'가 빈 맞교환 양식이 열리고 상대가 미리 골라져 있었다.
const dateClick=(a,data)=>a.ctx.document.dispatchEvent({type:'click',target:{closest:sel=>sel==='[data-ur-date-action]'?{dataset:data}:null},preventDefault(){}});
test('VIEWER date window is information plus one entry button; no second form and no empty swap form on a no-duty day',()=>{
 const a=app();a.run(`noDutyMap={'2026-10-16':true};commentMap={'2026-10-16':'패밀리데이'};openDateModal('2026-10-16')`);
 assert.equal(a.node('change-panel-title').parentNode,undefined,'legacy panel is looked up but stubs have no parent');
 const info=a.node('ur-date-viewer').innerHTML;assert.match(info,/당직 없는 날입니다/);assert.doesNotMatch(info,/data-ur-date-action/);
 assert.equal(a.node('ur-change-modal').classList.contains('active'),false);
 assert.equal(a.node('modal-date-comment').style.display,'none');assert.equal(a.node('ur-date-memo').textContent,'패밀리데이');
 a.run(`openDateModal('2026-10-14')`);const own=a.node('ur-date-viewer').innerHTML;
 assert.match(own,/data-ur-date-action="swap"/);assert.match(own,/data-ur-date-action="sub"/);assert.match(own,/<b>A<\/b>, <b>B<\/b>/);
 dateClick(a,{urDateAction:'swap',date:'2026-10-14'});
 assert.ok(a.node('ur-change-modal').classList.contains('active'));assert.equal(a.node('ur-change-name').value,'A');assert.equal(a.node('ur-change-date').value,'2026-10-14');
 assert.equal(a.node('ur-change-partner').value,'','partner is the employee\'s own choice');assert.equal(a.json('sent').length,0);
});
test('VIEWER without a duty on that date: swap-with offers an eligible own duty; past dates offer nothing',()=>{
 const a=app();a.run(`myName='B';scheduleMap={'2026-10-14':[{name:'A'}],'2026-10-15':[{name:'B'}],'2026-10-12':[{name:'A'}]};openDateModal('2026-10-14')`);
 assert.match(a.node('ur-date-viewer').innerHTML,/data-ur-date-action="swap-with"[^>]*data-mine="2026-10-15"[^>]*data-partner="A"/);
 dateClick(a,{urDateAction:'swap-with',date:'2026-10-14',mine:'2026-10-15',partner:'A',name:'B'});
 assert.equal(a.node('ur-change-name').value,'B');assert.equal(a.node('ur-change-date').value,'2026-10-15');assert.equal(a.node('ur-change-partner').value,'A');assert.equal(a.node('ur-change-otherdate').value,'2026-10-14');
 a.run(`openDateModal('2026-10-12')`);assert.match(a.node('ur-date-viewer').innerHTML,/지난 날짜는 신청할 수 없습니다/);assert.doesNotMatch(a.node('ur-date-viewer').innerHTML,/data-ur-date-action/);
});
function ownSwapDates(a){return [...a.node('ur-date-viewer').innerHTML.matchAll(/data-mine="([^"]+)"/g)].map(m=>m[1]);}
function ownSwapSetup(a,today='2026-10-13',target='2026-10-14'){
 a.run(`todayStr=${JSON.stringify(today)};myName='A';noDutyMap={};closedMonths=[];absences=[];majorDutyBlockStart=()=>null;isMajorMainDay=()=>false;scheduleMap={${JSON.stringify(target)}:[{name:'B'}]};globalThis.swapTarget=${JSON.stringify(target)};showToast=text=>{globalThis.lastToast=text};`);
}
test('clicked partner date lists every own duty from today through three calendar months in date order',()=>{
 const a=app();ownSwapSetup(a);
 a.run(`window.OnAirUI.dateLabel=(key,fullYear)=>{const [y,m,d]=key.split('-');return fullYear?y+'년 '+m+'월 '+d+'일':m+'/'+d}`);
 a.run(`Object.assign(scheduleMap,{'2027-01-13':[{name:'A'}],'2026-10-12':[{name:'A'}],'2026-11-20':[{name:'A'},{name:'A'}],'2026-10-13':[{name:'A'}],'2027-01-14':[{name:'A'}],'2026-12-20':[{name:'A'}]});noDutyMap['2026-12-20']=true;openDateModal(swapTarget)`);
 assert.deepEqual(ownSwapDates(a),['2026-10-13','2026-11-20','2027-01-13']);
 const text=a.node('ur-date-viewer').innerHTML;assert.match(text,/data-range-start="2026-10-13"/);assert.match(text,/data-range-end="2027-01-13"/);assert.match(text,/내 당직 3건/);assert.match(text,/오늘부터 3개월/);
 assert.match(text,/<p class="ur-own-swaps-range">오늘부터 3개월 · 2026년 10월 13일 ~ 2027년 01월 13일<\/p>/,'both range endpoints retain their years across the year boundary');
 assert.equal(a.json('sent').length,0,'listing candidates must not submit or save');
});
for(const [today,end,after] of [['2026-11-30','2027-02-28','2027-03-01'],['2027-11-30','2028-02-29','2028-03-01'],['2026-05-31','2026-08-31','2026-09-01']])
 test('three-month range uses inclusive calendar months with month-end clamp: '+today,()=>{
  const a=app();ownSwapSetup(a,today,today);
  a.run(`scheduleMap[${JSON.stringify(end)}]=[{name:'A'}];scheduleMap[${JSON.stringify(after)}]=[{name:'A'}];openDateModal(swapTarget)`);
  assert.deepEqual(ownSwapDates(a),[end]);assert.match(a.node('ur-date-viewer').innerHTML,new RegExp('data-range-end="'+end+'"'));
 });
test('second own duty row opens the exact chosen source and clicked target, preserving other composer date options',()=>{
 const a=app();ownSwapSetup(a);a.run(`Object.assign(scheduleMap,{'2026-10-15':[{name:'A'}],'2026-11-03':[{name:'A'}],'2027-02-02':[{name:'A'}]});openDateModal(swapTarget)`);
 assert.deepEqual(ownSwapDates(a),['2026-10-15','2026-11-03']);
 dateClick(a,{urDateAction:'swap-with',date:'2026-10-14',mine:'2026-11-03',partner:'B',name:'A'});
 assert.equal(a.node('ur-change-name').value,'A');assert.equal(a.node('ur-change-date').value,'2026-11-03');assert.equal(a.node('ur-change-partner').value,'B');assert.equal(a.node('ur-change-otherdate').value,'2026-10-14');
 assert.match(a.node('ur-change-date').innerHTML,/2027-02-02/,'the existing general composer is not restricted to this shortcut range');assert.equal(a.json('sent').length,0);
});
for(const [name,change,reason] of [
 ['closed source month',"closedMonths=['2026-11']",/마감된 달/],
 ['major source day',"majorDutyBlockStart=k=>k==='2026-11-03'?'2026-11-02':null",/명절 당직/],
 ['weekday versus holiday',"isOffDayKey=k=>k==='2026-11-03'",/평일은 평일끼리/],
 ['partner absent on source',"absences=[{name:'B',type:'연차',start:'2026-11-03',end:'2026-11-03'}]",/휴가·부재/],
 ['partner already on source',"scheduleMap['2026-11-03'].push({name:'B'})",/이미 당직/],
 ['own absence on target',"absences=[{name:'A',type:'연차',start:'2026-10-14',end:'2026-10-14'}]",/휴가·부재/],
 ['authoritative personal rule rejects exchange',"window.OnAirPersonal={swapEligibility:()=>false}",/일정이 바뀌어/]
])test('own duty stays visible with disabled request and existing reason: '+name,()=>{
 const a=app();ownSwapSetup(a);a.run(`scheduleMap['2026-11-03']=[{name:'A'}];${change};openDateModal(swapTarget)`);
 const text=a.node('ur-date-viewer').innerHTML;assert.deepEqual(ownSwapDates(a),['2026-11-03']);assert.match(text,/data-mine="2026-11-03"[^>]*disabled/);assert.match(text,reason);assert.match(text,/신청 가능 0건/);
});
for(const [name,change] of [
 ['source removed',"delete scheduleMap['2026-11-03']"],
 ['target partner removed',"scheduleMap['2026-10-14']=[{name:'C'}]"],
 ['identity changed',"myName='B'"],
 ['window moved before chosen date',"todayStr='2026-07-01'"],
 ['chosen date now past',"todayStr='2026-11-04'"],
 ['month closed',"closedMonths=['2026-11']"],
 ['disconnected',"SERVER_CONNECTED=false"],
 ['role changed',"SYSTEM_MODE='ADMIN'"]
])test('stale own-duty shortcut is rechecked before closing the date dialog or replacing a draft: '+name,()=>{
 const a=app();ownSwapSetup(a);a.run(`scheduleMap['2026-11-03']=[{name:'A'}];openDateModal(swapTarget)`);a.session.set('sd_request_draft_change_v370','{"keep":true}');a.run(change);
 dateClick(a,{urDateAction:'swap-with',date:'2026-10-14',mine:'2026-11-03',partner:'B',name:'A'});
 assert.equal(a.node('ur-change-modal').classList.contains('active'),false);assert.equal(a.node('date-modal').classList.contains('active'),true);assert.equal(a.session.get('sd_request_draft_change_v370'),'{"keep":true}');assert.ok(a.ctx.lastToast);assert.equal(a.json('sent').length,0);
});
test('no own duties within the inclusive three-month range explains the empty state',()=>{
 const a=app();ownSwapSetup(a);a.run(`scheduleMap['2027-01-14']=[{name:'A'}];openDateModal(swapTarget)`);
 assert.deepEqual(ownSwapDates(a),[]);assert.match(a.node('ur-date-viewer').innerHTML,/오늘부터 3개월/);assert.match(a.node('ur-date-viewer').innerHTML,/이 기간에 배정된 내 당직이 없습니다/);
});
test('one composer: choosing a partner defaults the exchange date to their next future duty; a past date says so',async()=>{
 const a=app();a.run(`scheduleMap={'2026-10-14':[{name:'A'}],'2026-10-12':[{name:'B'}],'2026-10-15':[{name:'B'}],'2026-10-16':[{name:'B'}],'2026-10-17':[{name:'C'}]};confirmConsecutiveRisk=()=>true;
  window.OnAirRequests.openChange({requester_name:'A',request_type:'SWAP',target_date:'2026-10-14'})`);
 const partners=a.node('ur-change-partner').innerHTML;assert.match(partners,/value="B"/);assert.doesNotMatch(partners,/value="C"/,'other gender is not offered');
 a.node('ur-change-partner').value='B';a.node('ur-change-partner').dispatchEvent({type:'change'});
 assert.equal(a.node('ur-change-otherdate').value,'2026-10-15');assert.doesNotMatch(a.node('ur-change-otherdate').innerHTML,/2026-10-12/,'past duties are not selectable');
 a.run(`window.OnAirRequests.openChange({requester_name:'A',request_type:'SWAP',target_date:'2026-10-14',swap_worker:'B',swap_date:'2026-10-12',reason:'사유'})`);
 assert.match(a.node('ur-change-otherdate').innerHTML,/지난 날짜/);await a.run('window.OnAirRequests.submitChange()');
 assert.equal(a.node('ur-change-otherdate-error').textContent,'지난 날짜는 교환할 수 없습니다. 오늘 이후 날짜를 선택하세요.');assert.equal(a.json('sent').length,0);
 assert.match(a.node('ur-change-submit').textContent||a.ctx.changeBodyLabel||'신청하기',/신청하기/);
});
test('substitute list shows only same-gender, available people under "대신 근무할 사람"',()=>{
 const a=app();a.run(`memberList.push({name:'D',gender:'F'},{name:'E',gender:'F'});absences=[{name:'D',type:'연차',start:'2026-10-14',end:'2026-10-14'}];isAbsent=(n,k)=>n==='D'&&k==='2026-10-14';
  window.OnAirRequests.openChange({requester_name:'A',request_type:'SUBSTITUTE',target_date:'2026-10-14'})`);
 const list=a.node('ur-change-partner').innerHTML;assert.match(list,/value="E"/);for(const name of ['B','C','D'])assert.doesNotMatch(list,new RegExp(`value="${name}"`),name);
 assert.match(a.node('ur-change-partner-label').innerHTML,/대신 근무할 사람/);assert.equal(a.node('ur-change-otherdate-wrap').hidden,true);assert.equal(a.node('ur-change-zero-wrap').hidden,false);
});
test('0점 option: substitute only, weekend/holiday default by rule, weekday not preselected, explicit choice kept',()=>{
 const a=app();a.run(`dutyRule.substituteScoreZero=true;scheduleMap={'2026-10-14':[{name:'A'}],'2026-10-17':[{name:'A'},{name:'C'}]};window.OnAirRequests.openChange({requester_name:'A',request_type:'SUBSTITUTE',target_date:'2026-10-14'})`);
 assert.equal(a.node('ur-change-zero').checked,false);assert.match(a.node('ur-change-zero-note').textContent,/평일/);
 a.node('ur-change-date').value='2026-10-17';a.node('ur-change-date').dispatchEvent({type:'change'});assert.equal(a.node('ur-change-zero').checked,true);assert.match(a.node('ur-change-zero-note').textContent,/주말·공휴일/);
 a.run(`window.OnAirRequests.openChange({requester_name:'A',request_type:'SUBSTITUTE',target_date:'2026-10-17',is_zero_score:false})`);assert.equal(a.node('ur-change-zero').checked,false);
 a.run(`window.OnAirRequests.openChange({requester_name:'A',request_type:'SWAP',target_date:'2026-10-14'})`);assert.equal(a.node('ur-change-zero-wrap').hidden,true);
 assert.doesNotMatch(html,/주말 0점/,'the legacy date form uses the same term');
});
// [위험] v3.18.1 사용자 결정 — 맞교환·대체 근무 신청 사유도 선택 입력이다. 비워도 확인창까지 가야 하고, 사유 칸에 * 가 없어야 한다.
test('required markers, optional reason and a confirmation that summarizes the whole request',async()=>{
 const a=app();a.run(`globalThis.changeBody='';const open=window.OnAirUI.open;window.OnAirUI.open=(id,title,body)=>{if(id==='ur-change-modal')globalThis.changeBody=body;return open(id,title,body)};
  scheduleMap={'2026-10-14':[{name:'A'}],'2026-10-15':[{name:'B'}]};confirmConsecutiveRisk=()=>true;globalThis.confirmText='';confirm=t=>{confirmText=t;return false};
  window.OnAirRequests.openChange({requester_name:'A',request_type:'SWAP',target_date:'2026-10-14',swap_worker:'B',swap_date:'2026-10-15'})`);
 const body=a.ctx.changeBody;for(const field of ['신청자','신청 구분','내 당직 날짜','맞교환할 사람','교환할 날짜'])assert.match(body,new RegExp(field+' <b class="ur-req"'),field);
 assert.ok(!/사유 <b class="ur-req"/.test(body),'사유 칸에는 * 가 없어야 합니다');assert.doesNotMatch(body,/id="ur-change-reason"[^>]*aria-required/);
 assert.match(body,/id="ur-change-reason-hint"[^>]*>필요하면 적어 주세요/);assert.match(body,/id="ur-change-submit" class="btn-main">신청하기</);
 await a.run('window.OnAirRequests.submitChange()');assert.notEqual(a.node('ur-change-reason-error').textContent,'사유를 입력하세요. 관리자가 승인할 때 확인합니다.');assert.ok(a.run('confirmText').includes('사유: (적지 않음)'),'사유 없이도 확인창이 떠야 합니다');a.run("confirmText=''");
 a.node('ur-change-reason').value='병원 예약';await a.run('window.OnAirRequests.submitChange()');const text=a.run('confirmText');
 for(const part of ['신청자: A','구분: 맞교환','내 당직: 2026-10-14','상대: B · 교환일: 2026-10-15','사유: 병원 예약'])assert.ok(text.includes(part),part);
 a.run('openAbsenceRequestModal()');fillAbsence(a);await a.run('submitAbsenceRequest()');const absence=a.run('confirmText');
 assert.ok(!absence.includes('휴가 기간'),'3.18.0 QA: 교육은 휴가 기간이 아니다');
 for(const part of ['구분: 휴가·부재','교육 기간: 2026-10-14 ~ 2026-10-15 (2일)','휴가·부재 구분: 교육','사유: 참석 신청'])assert.ok(absence.includes(part),part);
 assert.equal(a.node('btn-submit-absence-request').textContent,'신청하기');
});
test('vacation form opens on the next day without my duty and warns about released duties only after I pick dates',()=>{
 const a=app();a.run(`scheduleMap={'2026-10-14':[{name:'A'}],'2026-10-15':[{name:'A'}]};renderAbsenceImpact=renderAbsenceImpact;openAbsenceRequestModal()`);
 assert.equal(a.node('absence-req-start').value,'2026-10-16');assert.equal(a.node('absence-req-end').value,'2026-10-16');assert.equal(a.node('absence-impact').style.display,'none');
 a.node('absence-req-start').value='2026-10-14';a.node('absence-req-end').value='2026-10-15';a.node('absence-request-modal').dispatchEvent({type:'change',target:a.node('absence-req-end')});
 assert.equal(a.node('absence-impact').style.display,'');assert.match(a.node('absence-impact').innerHTML,/내 당직 2건/);
});
test('editing a request decided meanwhile explains the decision with the reject reason and offers 다시 작성',async()=>{
 const a=app();a.run(`myRequestStatuses={'own-token':'PENDING'};globalThis.stateBody='';const open=window.OnAirUI.open;window.OnAirUI.open=(id,title,body)=>{if(id==='ur-request-state')globalThis.stateBody=body;return open(id,title,body)};
  fetch=async(url,options={})=>{sent.push({url,...options});if(url==='/api/duty/request/manage')return {ok:false,status:409,json:async()=>({detail:'이미 반려된 신청입니다. 대기 중인 신청만 수정하거나 철회할 수 있습니다.'})};return {ok:true,status:200,json:async()=>({request:{id:5,status:'REJECTED',request_type:'SUBSTITUTE',requester_name:'A',target_date:'2026-10-14',substitute_name:'B',reject_reason:'인원 부족'}})}};
  scheduleMap={'2026-10-14':[{name:'A'}]};confirmConsecutiveRisk=()=>true;window.OnAirRequests.openChange({requester_name:'A',request_type:'SUBSTITUTE',target_date:'2026-10-14',substitute_name:'B',reason:'수정'},{editToken:'own-token'})`);
 await a.run('window.OnAirRequests.submitChange()');const body=a.ctx.stateBody;
 assert.match(body,/이미 반려된 신청입니다/);assert.match(body,/인원 부족/);assert.match(body,/다시 작성/);assert.doesNotMatch(body,/토큰/);
 assert.equal(a.node('ur-change-modal').classList.contains('active'),false);
});
test('requests from another PC say where they can be changed instead of silently hiding buttons; no token wording',async()=>{
 const a=app();a.run(`fetch=async url=>({ok:true,status:200,json:async()=>url.includes('/my-requests?')?{requests:[{id:8,status:'PENDING',request_type:'SWAP',requester_name:'A',target_date:'2026-10-14',swap_date:'2026-10-15',swap_worker:'B'}]}:{}})`);
 await a.run('window.OnAirRequests.openMyRequests("A")');const html=a.node('myreq-tbody').innerHTML;
 assert.match(html,/신청한 PC에서만 수정·철회할 수 있습니다/);assert.doesNotMatch(html,/OnAirRequests.editById/);
 a.run(`globalThis.toasts=[];showToast=(m)=>toasts.push(m);window.OnAirRequests.withdraw('not-mine');window.OnAirRequests.openOwnedRequest('not-mine')`);
 assert.ok(a.json('toasts').every(m=>!/토큰/.test(m)&&/신청한 PC에서만/.test(m)));
});
test('pending requests are exposed for 내 일정 and calendar badges, including the swap partner side',async()=>{
 const a=app();a.run(`fetch=async url=>(sent.push({url}),{ok:true,status:200,json:async()=>({requests:[{id:3,status:'PENDING',request_type:'SWAP',requester_name:'A',target_date:'2026-10-14',swap_date:'2026-10-15',swap_worker:'B',role:'requester'},{id:4,status:'APPROVED',request_type:'SWAP',requester_name:'A',target_date:'2026-10-20',swap_date:'2026-10-21',swap_worker:'B'}]})})`);
 await a.run('window.OnAirRequests.refreshPending()');
 assert.deepEqual(a.json(`window.OnAirRequests.pendingFor('A','2026-10-14')`),[{id:3,text:'A 10/14 ↔ B 10/15 맞교환'}]);
 assert.equal(a.json(`window.OnAirRequests.pendingFor('B','2026-10-15')`).length,1);assert.equal(a.json(`window.OnAirRequests.pendingFor('A','2026-10-20')`).length,0);
 assert.ok(a.json('sent').some(x=>x.url==='/api/duty/my-requests?name=A&involving=1'));
});
// [위험] 조회용 '연결 정보'가 직원에게 "관리자 암호 설정 안 됨 (누구나 수정 가능)"과 DB 경로를 보여 줬다.
test('connection info hides data location and admin password state from the employee program',async()=>{
 for(const [mode,shown] of [['VIEWER',false],['ADMIN',true]]){
  const a=app();a.ctx.location={origin:'http://127.0.0.1:8000'};a.run(`SYSTEM_MODE='${mode}';fetch=async()=>({ok:true,status:200,json:async()=>({mode:'${mode}',version:'3.18.0',dbPath:'\\\\\\\\server\\\\share\\\\smart_duty.db',dbOk:true,authRequired:false})})`);
  await a.run('openServerInfo()');const html=a.node('serverinfo-body').innerHTML;
  assert.equal(/데이터 위치/.test(html),shown,mode);assert.equal(/관리자 암호/.test(html),shown,mode);assert.equal(/누구나 수정 가능/.test(html),shown,mode);assert.equal(/smart_duty\.db/.test(html),shown,mode);assert.match(html,/데이터 연결/);
 }
});
// [위험] 실제 <select> 는 선택지에 없는 값을 받지 않는다. 선택지를 만들기 전에 값을 넣으면 첫 당직·빈 상대로 바뀌어,
//   10/26 후보를 골라도 신청서에는 10/14 가 채워졌다(브라우저 점검에서 발견).
test('prefilled composer keeps the seeded date, partner and exchange date with real select semantics',()=>{
 const a=app();for(const id of ['ur-change-date','ur-change-partner','ur-change-otherdate']){const n=a.node(id);let v='';Object.defineProperty(n,'value',{get:()=>v,set:x=>{x=String(x??'');v=x===''||String(n.innerHTML).includes(`value="${x}"`)?x:'';},configurable:true});}
 a.run(`scheduleMap={'2026-10-14':[{name:'A'}],'2026-10-26':[{name:'A'}],'2026-10-20':[{name:'B'}],'2026-11-02':[{name:'B'}]};window.OnAirRequests.openChange({requester_name:'A',request_type:'SWAP',target_date:'2026-10-26',swap_worker:'B',swap_date:'2026-10-20'})`);
 assert.equal(a.node('ur-change-date').value,'2026-10-26');assert.equal(a.node('ur-change-partner').value,'B');assert.equal(a.node('ur-change-otherdate').value,'2026-10-20');
});
// ── 3.18.0 QA ───────────────────────────────────────────────────────
test('3.18.0 QA: swap "내 당직 날짜" shows the year for dates outside this year',()=>{
 const a=app();a.run(`scheduleMap['2027-01-05']=[{name:'A'}];window.OnAirUI.dateLabel=(k,full)=>full?'FULL:'+k:'SHORT:'+k;`);
 a.run('window.OnAirRequests.openChange()');const html=a.node('ur-change-date').innerHTML;
 assert.match(html,/FULL:2027-01-05/);assert.match(html,/SHORT:2026-10-14/);assert.doesNotMatch(html,/FULL:2026/);
});
test('3.18.0 QA: a sent request keeps a local copy so a vanished request can be rewritten',async()=>{
 const a=app();a.run('openAbsenceRequestModal()');fillAbsence(a);await a.run('submitAbsenceRequest()');
 const copies=JSON.parse(a.local.get('sd_my_request_copies_v1'));
 assert.equal(copies['own-token'].request.absence_type,'교육');assert.equal(copies['own-token'].request.reason,'참석 신청');
});

// v3.18.25 — cancel an already approved absence without replacing its original approval.
function cancellationFixture(a,extra={}){
 const original={id:42,name:'A',type:'연차',start:'2026-11-06',end:'2026-11-09',education:null,segments:[{id:42,name:'A',type:'연차',start:'2026-11-06',end:'2026-11-06'},{id:43,name:'A',type:'연차',start:'2026-11-09',end:'2026-11-09'}]};
 a.ctx.cancelParent={id:20,requester_name:'A',request_type:'ABSENCE',status:'APPROVED',target_date:original.start,absence_end_date:original.end,absence_type:'연차',can_amend:true,can_cancel:true,cancellable_absence:original,...extra};
 a.run(`myRequestStatuses={'cancel-owner-receipt':'APPROVED'};globalThis.opened={};window.OnAirUI.open=(id,title,body)=>{opened={id,title,body};document.getElementById(id).classList.add('active');return document.getElementById(id)};
 fetch=async(url,opts={})=>{sent.push({url,...opts});return {ok:true,status:200,json:async()=>url.includes('/my-requests?')?{requests:[cancelParent]}:url.includes('/request-status/')?{request:cancelParent}:{receiptToken:'new-cancel-receipt',requestId:21,message:'취소 요청을 접수했습니다.'}}};`);
 return original;
}
test('owned approved absence exposes category-specific cancellation with historical segments',async()=>{
 const a=app();cancellationFixture(a);await a.run('OnAirRequests.openMyRequests("A")'.replace('OnAirRequests','window.OnAirRequests'));
 assert.match(a.node('myreq-tbody').innerHTML,/휴가 취소 요청/);await a.run('window.OnAirRequests.cancelById(20)');
 const o=a.json('opened');assert.equal(o.title,'휴가 취소 요청');assert.match(o.body,/실제 2일/);assert.match(o.body,/2026-11-06/);assert.match(o.body,/2026-11-09/);assert.match(o.body,/승인하기 전까지 기존 일정/);assert.match(o.body,/선택/);
});
test('cancellation cannot be opened using an unowned id or administrator mode',async()=>{
 const a=app();cancellationFixture(a);await a.run('window.OnAirRequests.cancelById(20)');assert.deepEqual(a.json('opened'),{});
 await a.run('window.OnAirRequests.openMyRequests("A")');a.run("SYSTEM_MODE='ADMIN'");await a.run('window.OnAirRequests.cancelById(20)');assert.deepEqual(a.json('opened'),{});assert.equal(a.json('sent').filter(r=>r.method==='POST').length,0);
});
test('cancellation posts only original receipt and optional reason, no client-selected deletion ids',async()=>{
 const a=app();cancellationFixture(a);await a.run('window.OnAirRequests.openMyRequests("A")');await a.run('window.OnAirRequests.cancelById(20)');a.node('ur-cancel-reason').value='일정 변경';const before=a.json('[absences,scheduleMap]');await a.run('window.OnAirRequests.submitCancellation()');const sent=a.json('sent').filter(x=>x.method==='POST');assert.equal(sent.length,1);assert.deepEqual(JSON.parse(sent[0].body),{receipt_token:'cancel-owner-receipt',action:'CANCEL_ABSENCE',reason:'일정 변경'});assert.deepEqual(a.json('[absences,scheduleMap]'),before);assert.ok(a.run("Object.hasOwn(myRequestStatuses,'new-cancel-receipt')"));
});
test('cancellation does not double-submit and keeps draft on server rejection',async()=>{
 const a=app();cancellationFixture(a);await a.run('window.OnAirRequests.openMyRequests("A")');await a.run('window.OnAirRequests.cancelById(20)');a.node('ur-cancel-reason').value='보존할 사유';a.run("fetch=async(url,opts={})=>{sent.push({url,...opts});return new Promise(resolve=>globalThis.finishCancel=resolve)}");const first=a.run('window.OnAirRequests.submitCancellation()');await a.run('window.OnAirRequests.submitCancellation()');assert.equal(a.json('sent').filter(x=>x.method==='POST').length,1);a.run("finishCancel({ok:false,status:409,json:async()=>({detail:'기존 일정이 변경되었습니다.'})})");await first;assert.equal(a.node('ur-cancel-reason').value,'보존할 사유');assert.equal(a.node('ur-cancel-submit').disabled,false);assert.ok(a.node('ur-absence-cancel').classList.contains('active'));
});
test('cancellation validates reason length before send and respects server cancellation blocker',async()=>{
 const a=app();cancellationFixture(a);await a.run('window.OnAirRequests.openMyRequests("A")');await a.run('window.OnAirRequests.cancelById(20)');a.node('ur-cancel-reason').value='가'.repeat(501);await a.run('window.OnAirRequests.submitCancellation()');assert.equal(a.node('ur-cancel-reason')['aria-invalid'],'true');assert.equal(a.json('sent').filter(x=>x.method==='POST').length,0);
 const b=app();cancellationFixture(b,{can_cancel:false,cancellation_blocker:'마감된 달은 취소할 수 없습니다.'});await b.run('window.OnAirRequests.openMyRequests("A")');await b.run('window.OnAirRequests.cancelById(20)');assert.deepEqual(b.json('opened'),{});assert.doesNotMatch(b.node('myreq-tbody').innerHTML,/cancelById/);
});
test('parent cancelled state and pending cancellation have distinct labels with no edit form',async()=>{
 const a=app();cancellationFixture(a,{can_amend:false,can_cancel:false,cancelled_by_id:21});await a.run('window.OnAirRequests.openMyRequests("A")');assert.match(a.node('myreq-tbody').innerHTML,/취소 완료/);assert.doesNotMatch(a.node('myreq-tbody').innerHTML,/cancelById|amendById/);
 a.run("cancelParent={...cancelParent,id:21,request_type:'ABSENCE_CANCEL',status:'PENDING',amends_request_id:20,cancelled_by_id:null,reason:'개인 사유'}");await a.run('window.OnAirRequests.openMyRequests("A")');const body=a.node('myreq-tbody').innerHTML;assert.match(body,/취소 승인 대기/);assert.match(body,/취소 요청 보기/);assert.doesNotMatch(body,/>수정<|amendById|cancelById/);await a.run('window.OnAirRequests.editById(21)');assert.equal(a.json('opened').id,'ur-cancellation-state');assert.ok(!a.node('absence-request-modal').classList.contains('active'));
});
test('cancellation approval confirmation describes removal without duty release or vacation registration',async()=>{
 const a=app();a.run(`SYSTEM_MODE='ADMIN';approvalCache=[{id:21,request_type:'ABSENCE_CANCEL',status:'PENDING',requester_name:'A',target_date:'2026-11-06',absence_end_date:'2026-11-09',absence_type:'연차',amends_request_id:20,absence_date_plan:{ranges:[{start:'2026-11-06',end:'2026-11-06'},{start:'2026-11-09',end:'2026-11-09'}],days:2,excluded:['2026-11-07','2026-11-08']}}];globalThis.cancelConfirm='';askConfirm=async o=>{cancelConfirm=o.message;return false}`);await a.run('processApproval(21,"APPROVE")');assert.match(a.run('cancelConfirm'),/취소/);assert.match(a.run('cancelConfirm'),/자동 복원되지 않습니다/);assert.match(a.run('cancelConfirm'),/실제 2일/);assert.equal(a.json('sent').filter(x=>x.method==='POST').length,0);
});

test('cancellation details keep years visible for a range crossing a year boundary',()=>{
 const a=app();a.run("window.OnAirUI.dateLabel=(key,full=false)=>full?key+'(화)':key.slice(5)+'(화)'");const html=a.run(`window.OnAirRequests.cancellationDetails({requester_name:'A',absence_type:'파견',amendment_original:{start:'2026-12-29',end:'2027-01-05',type:'파견'}})`);assert.match(html,/2026/);assert.match(html,/2027/);
});
