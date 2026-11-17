'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const file=path.resolve(__dirname,'../../frontend/dist/dinner_admin.js');
function load(overrides={}){
  const ctx=vm.createContext({console,Date,Intl,JSON,Math,Map,Set,Promise,crypto:require('node:crypto').webcrypto,document:{},setTimeout,clearTimeout,...overrides});
  vm.runInContext('window=globalThis',ctx);
  const source=fs.existsSync(file)?fs.readFileSync(file,'utf8'):'';
  vm.runInContext(source.replace(/\}\)\(\);\s*$/,'window.__model=M;})();'),ctx);
  return ctx;
}
const draft=()=>({title:'회식 날짜 취합',description:'',deadline:'2027-01-10T17:00',candidates:[{id:'a',date:'2027-01-15',time:'18:30'},{id:'b',date:'2027-01-16',time:'18:30'}],targets:['김하늘','이서연']});
test('dinner manager exposes usable manager and CA entry points',()=>{const c=load();assert.equal(typeof c.OnAirDinnerAdmin?.open,'function');assert.equal(typeof c.OnAirDinnerAdmin?.openRole,'function');});
test('one and two year CA terms keep valid leap-day boundaries',()=>{const m=load().__model;assert.equal(m.termEnd('2024-02-29',1),'2025-02-28');assert.equal(m.termEnd('2026-01-01',2),'2027-12-31');assert.equal(m.termEnd('2026-11-17',1),'2027-11-16');});
test('invalid calendar dates are not silently normalized',()=>{const m=load().__model;assert.equal(m.validDate('2026-02-30'),false);assert.equal(m.validDate('2028-02-29'),true);assert.throws(()=>m.termEnd('2026-02-30',1),/시작일/);});
test('draft validation rejects duplicate candidate dates and empty targets',()=>{const m=load().__model;let d=draft();d.candidates[1].date=d.candidates[0].date;assert.throws(()=>m.validateDraft(d,'2027-01-01T00:00'),/같은 날짜/);d=draft();d.targets=[];assert.throws(()=>m.validateDraft(d,'2027-01-01T00:00'),/대상/);});
test('deadline cannot be past or after any candidate, with KST wall-clock comparison',()=>{const m=load().__model;const d=draft();assert.throws(()=>m.validateDraft(d,'2027-01-10T17:00'),/마감/);d.deadline='2027-01-15T18:31';assert.throws(()=>m.validateDraft(d,'2027-01-01T00:00'),/후보/);});
test('valid draft preserves candidate IDs and removes accidental duplicate targets',()=>{const m=load().__model;const d=draft();d.targets.push('이서연');const result=m.validateDraft(d,'2027-01-01T00:00');assert.deepEqual(JSON.parse(JSON.stringify(result.candidates)),d.candidates);assert.equal(result.targets.length,2);});
test('candidate count must stay within the approved 2–5 range',()=>{const m=load().__model;const d=draft();d.candidates.length=1;assert.throws(()=>m.validateDraft(d,'2027-01-01T00:00'),/2.*5/);});
test('date calendar always contains 42 chronological days including year boundary',()=>{const m=load().__model;const days=m.monthDays('2026-12');assert.equal(days.length,42);assert.equal(days[0],'2026-11-29');assert.equal(days[41],'2027-01-09');});
test('operation retry retains ID only for identical action and payload',()=>{const m=load().__model;const ops=m.operationBook();const one=ops.id('/polls',{title:'a'});assert.equal(ops.id('/polls',{title:'a'}),one);assert.notEqual(ops.id('/polls',{title:'b'}),one);assert.notEqual(ops.id('/role',{title:'a'}),one);ops.done('/polls',{title:'a'});assert.notEqual(ops.id('/polls',{title:'a'}),one);});
test('complete all-difficult votes count as submitted; partial responses remain outstanding',()=>{const m=load().__model;const p={targets:['a','b','c'],results:{responders:[{name:'a',complete:true,answers:{a:'difficult',b:'difficult'}},{name:'b',complete:false,answers:{a:'possible'}}],nonresponders:['b','c']}};assert.equal(m.completed(p),1);});
test('changed-candidate confirmations remain actionable even with zero first-time nonresponders',()=>{const m=load().__model;const p={results:{nonresponders:[],confirmationRequired:['김하늘'],responders:[{name:'김하늘',complete:false}]}};assert.deepEqual(JSON.parse(JSON.stringify(m.outstanding(p))),[{name:'김하늘',needsConfirmation:true}]);});
test('outstanding list deduplicates partial responses and labels first responses separately',()=>{const m=load().__model;const p={results:{nonresponders:['이서연'],confirmationRequired:['김하늘'],responders:[{name:'김하늘',complete:false},{name:'박준호',complete:true}]}};assert.deepEqual(JSON.parse(JSON.stringify(m.outstanding(p))),[{name:'이서연',needsConfirmation:false},{name:'김하늘',needsConfirmation:true}]);});
test('operation identities work on direct HTTP origins without crypto.randomUUID',()=>{const wc=require('node:crypto').webcrypto;const m=load({crypto:{getRandomValues:wc.getRandomValues.bind(wc)}}).__model;assert.match(m.operationBook().id('/polls',{title:'x'}),/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);});

// Exercise the actual modal handlers and transport boundary, without exposing controller state.
function manager(options={}){
 const nodes=new Map(),panels=new Map(),calls=[],toasts=[];let refreshes=0;
 const poll={...draft(),id:'poll-1',status:'OPEN',revision:2,results:{responders:[],candidates:[],nonresponders:['김하늘','이서연']},...options.poll};
 const fixture={dutyDataVersion:12,role:null,members:poll.targets,polls:[poll]};
 const node=()=>({value:'',checked:false,hidden:false,textContent:'',disabled:false,isConnected:true,dataset:{},classList:{contains:()=>true},contains:()=>true,querySelectorAll:()=>[],focus(){}});
 const ui={dateLabel:value=>value,escape:value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;'),open(id,title,html){const el=node();nodes.set(id,el);panels.set(id,{title,html,closed:false});for(const match of html.matchAll(/<(?:input|textarea|div)[^>]*\bid="([^"]+)"[^>]*>/g)){const input=node(),value=match[0].match(/\bvalue="([^"]*)"/);if(value)input.value=value[1];nodes.set(match[1],input);}return el;},close(id){panels.get(id).closed=true;}};
 const ctx=load({SYSTEM_MODE:'ADMIN',document:{getElementById:id=>nodes.get(id)},OnAirUI:ui,showToast:text=>toasts.push(text),fetchAllData:async()=>{refreshes++;if(options.refreshGate)await options.refreshGate;if(options.refreshError)throw Error('calendar unavailable');return options.refreshResult??true;},OnAirDinner:{esc:ui.escape,refresh:async()=>{},api:async(path,request={})=>{calls.push({path,...structuredClone(request)});if(path==='/manage'){if(options.manageError)throw Error('manage unavailable');const snapshot=structuredClone(fixture);if(options.manageGate&&poll.status==='CONFIRMED'){const gate=options.manageGate;options.manageGate=null;await gate;}return snapshot;}if(path.endsWith('/action')){if(options.actionError)throw Error('다른 PC에서 근무 자료가 변경되었습니다.');const body=request.body;poll.revision++;if(body.action==='CONFIRM'){Object.assign(poll,{status:'CONFIRMED',selectedCandidateId:body.selectedCandidateId,dutyEffect:{date:poll.candidates.find(c=>c.id===body.selectedCandidateId).date,memo:'석식간담회',noDuty:true,appliedAt:'2026-11-17T12:00:00Z'}});fixture.dutyDataVersion++;}if(body.action==='CANCEL')poll.status='CANCELLED';return {poll:structuredClone(poll),dutyDataVersion:fixture.dutyDataVersion};}throw Error('Unexpected '+path);}}});
 const click=async(action,id)=>{const target={disabled:false,dataset:{da:action,...(id?{id}:{})}};return nodes.get('onair-dinner-admin').onclick({target:{closest:()=>target}});};
 const accept=async()=>nodes.get('onair-dinner-confirm').onclick({target:{closest:()=>({dataset:{da:'accept'}})}});
 return {ctx,nodes,panels,calls,toasts,fixture,poll,click,accept,refreshes:()=>refreshes,async open(){await ctx.OnAirDinnerAdmin.open();await click('detail',poll.id);}};
}
test('confirmation shows the refreshed chosen date and sends its current duty version only after the required check',async()=>{
 const a=manager();await a.open();a.fixture.dutyDataVersion=29;a.poll.revision=4;a.poll.candidates[0].date='2027-01-17';await a.click('finalize','a');
 const html=a.panels.get('onair-dinner-confirm').html;assert.match(html,/2027-01-17/);assert.match(html,/석식간담회/);assert.match(html,/메모.*추가/);assert.match(html,/배정된 근무자.*제거/);assert.match(html,/다른 날짜.*순번.*변경하지/);
 await a.accept();assert.equal(a.calls.filter(c=>c.body?.action==='CONFIRM').length,0);assert.match(a.nodes.get('da-confirm-error').textContent,/체크/);
 a.nodes.get('da-confirm-check').checked=true;await a.accept();const call=a.calls.find(c=>c.body?.action==='CONFIRM');assert.equal(call.body.baseVersion,29);assert.equal(call.body.expectedRevision,4);assert.equal(call.body.selectedCandidateId,'a');assert.equal(a.refreshes(),1);assert.equal(a.panels.get('onair-dinner-confirm').closed,true);assert.match(a.panels.get('onair-dinner-admin').html,/확정 시 이 날짜에 석식간담회 메모와 당직 없음을 반영했습니다/);
});
for(const failure of [{refreshResult:false},{refreshError:true}])test('a committed confirmation stays successful when calendar refresh '+(failure.refreshError?'throws':'returns false'),async()=>{
 const a=manager(failure);await a.open();await a.click('finalize','a');a.nodes.get('da-confirm-check').checked=true;await a.accept();assert.equal(a.poll.status,'CONFIRMED');assert.equal(a.panels.get('onair-dinner-confirm').closed,true);assert.equal(a.refreshes(),1);assert.ok(a.toasts.some(text=>/확정.*저장/.test(text)&&/달력/.test(text)));assert.equal(a.nodes.get('da-confirm-error').textContent,'');
});
test('a rejected confirmation leaves its dialog open and does not refresh the calendar',async()=>{
 const a=manager({actionError:true});await a.open();await a.click('finalize','a');a.nodes.get('da-confirm-check').checked=true;await a.accept();assert.equal(a.panels.get('onair-dinner-confirm').closed,false);assert.match(a.nodes.get('da-confirm-error').textContent,/다른 PC/);assert.equal(a.refreshes(),0);
});
test('confirmation refuses to proceed without a duty version from the refreshed management response',async()=>{
 const a=manager();await a.open();delete a.fixture.dutyDataVersion;await a.click('finalize','a');assert.equal(a.panels.has('onair-dinner-confirm'),false);assert.match(a.nodes.get('da-error').textContent,/최신 근무 자료.*확인하지/);assert.equal(a.calls.filter(c=>c.body?.action==='CONFIRM').length,0);
});
test('legacy confirmed polls make no calendar claim and cancellation preserves only recorded calendar effects',async()=>{
 const old=manager({poll:{status:'CONFIRMED',selectedCandidateId:'a'}});await old.open();assert.doesNotMatch(old.panels.get('onair-dinner-admin').html,/석식간담회.*반영했습니다/);await old.click('cancel-poll');assert.doesNotMatch(old.panels.get('onair-dinner-confirm').html,/근무자.*복원|달력.*유지/);
 const a=manager({poll:{status:'CONFIRMED',selectedCandidateId:'a',dutyEffect:{date:'2027-01-15',memo:'석식간담회',noDuty:true}}});await a.open();await a.click('cancel-poll');const html=a.panels.get('onair-dinner-confirm').html;assert.match(html,/석식간담회.*당직 없음.*유지/);assert.match(html,/근무자.*자동.*복원하지/);assert.match(html,/관리자.*달력.*조정/);a.nodes.get('da-action-notice').value='일정 취소';a.nodes.get('da-confirm-check').checked=true;await a.accept();assert.equal(a.refreshes(),0);assert.match(a.panels.get('onair-dinner-admin').html,/근무자.*자동.*복원하지/);assert.match(a.panels.get('onair-dinner-admin').html,/<span class="da-pill ">기존 확정 날짜<\/span>/);
});

test('committed confirmation closes and shows success while calendar and management reads remain pending',async()=>{
 let releaseCalendar,releaseManage;const options={refreshGate:new Promise(resolve=>releaseCalendar=resolve),manageGate:new Promise(resolve=>releaseManage=resolve)},a=manager(options);
 await a.open();await a.click('finalize','a');a.nodes.get('da-confirm-check').checked=true;const submitted=a.accept();await new Promise(setImmediate);
 const observed={committed:a.poll.status,closed:a.panels.get('onair-dinner-confirm').closed,success:/확정 시 이 날짜에/.test(a.panels.get('onair-dinner-admin').html)};
 releaseCalendar();releaseManage();await submitted;await new Promise(setImmediate);assert.deepEqual(observed,{committed:'CONFIRMED',closed:true,success:true});
});
test('late confirmation management refresh does not replace a subsequently opened role screen',async()=>{
 let releaseManage;const a=manager({manageGate:new Promise(resolve=>releaseManage=resolve)});await a.open();await a.click('finalize','a');a.nodes.get('da-confirm-check').checked=true;const submitted=a.accept();await new Promise(setImmediate);
 await a.ctx.OnAirDinnerAdmin.openRole();const roleHtml=a.panels.get('onair-dinner-admin').html;releaseManage();await submitted;await new Promise(setImmediate);assert.equal(a.panels.get('onair-dinner-admin').html,roleHtml);assert.match(roleHtml,/담당 직원/);
});
