'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const filename=path.resolve(__dirname,'../../frontend/dist/dinner_polls.js');
const poll={id:'p-one',title:'11월 회식',description:'가능한 날짜를 선택하세요.',deadline:'2099-11-20T17:00:00+09:00',status:'OPEN',revision:2,candidates:[{id:'c-one',date:'2099-11-24',time:'18:30',revision:1},{id:'c-two',date:'2099-12-02',time:'19:00',revision:1}],targets:['이서연'],responded:false,needsConfirmation:false,confirmationCandidateIds:[],ownResponse:null};
function app(options={}){
 const values=new Map(),sent=[],notices=[],panels=[],timers=[],listeners={};
 const button={classList:{toggle(){}},setAttribute(){},querySelector(){return null;},innerHTML:'',textContent:'',disabled:false};
 const modal={onclick:null,onchange:null,oninput:null,onsubmit:null,querySelector(){return null;},querySelectorAll(){return [];},classList:{contains:()=>true},addEventListener(){}};
 let summary={datasetKey:'dataset-1',role:null,connection:null,polls:[structuredClone(poll)],events:[],pendingConnections:0};
 const storage={getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,String(v)),removeItem:k=>values.delete(k)};
 const ctx={console,Date,JSON,Object,Map,Set,Number,String,Boolean,Array,URL,URLSearchParams,encodeURIComponent,crypto:require('node:crypto').webcrypto,TextEncoder,Uint8Array,
  localStorage:storage,sessionStorage:storage,document:{readyState:'loading',hidden:false,getElementById:id=>id==='header-dinner'?button:null,addEventListener:(k,f)=>listeners[k]=f,querySelector:()=>null},
  setTimeout:()=>0,clearTimeout(){},setInterval:(fn,ms)=>{timers.push({fn,ms});return 1;},clearInterval(){},
  myName:'이서연',memberList:[{name:'이서연'}],SYSTEM_MODE:'VIEWER',SERVER_CONNECTED:true,dataLoaded:true,scheduleMap:{},absences:[],
  setMyName(name){ctx.myName=name;},showToast(){},addNotif:(text,meta)=>notices.push({text,meta}),
  OnAirUI:{open:(id,title,html)=>{panels.push({id,title,html});return modal;},close(){}},
  OnAirRequestAccess:{dinnerCredentials:async()=>({key:'stable-personal-key',datasetKey:'dataset-1',deviceLabel:'test'})},
  fetch:async(url,init={})=>{sent.push({url,...init});if(options.network)return options.network(url,init);return {ok:true,status:200,json:async()=>url.includes('/summary')?summary:{poll:summary.polls[0]}};}};
 ctx.window=ctx;vm.createContext(ctx);if(fs.existsSync(filename))vm.runInContext(fs.readFileSync(filename,'utf8'),ctx,{filename});
 return {ctx,api:ctx.OnAirDinner,sent,notices,panels,timers,listeners,button,modal,values,setSummary:s=>{summary=s;},summary:()=>summary};
}
function available(a){assert.ok(a.api,'The dinner module must provide the public integration API');return a.api;}
test('employee writes carry the private key and explicit data contract without placing secrets in JSON',async()=>{
 const a=app();await available(a).api('/polls/p-one/response',{method:'POST',body:{memberName:'이서연',operationId:'retry-1'}});
 const request=a.sent.at(-1);assert.equal(request.headers['X-OnAir-Dinner-Key'],'stable-personal-key');assert.equal(request.headers['X-OnAir-Data-Contract'],'1');assert.equal(JSON.parse(request.body).operationId,'retry-1');assert.ok(!request.body.includes('stable-personal-key'));
});
test('managed credential failures block writes without generating a browser replacement identity',async()=>{
 const a=app();a.ctx.OnAirRequestAccess.dinnerCredentials=async()=>{throw Error('프로필 저장 실패');};
 await assert.rejects(available(a).api('/polls/p-one/response',{method:'POST',body:{}}),/프로필 저장 실패/);assert.equal(a.sent.length,0);assert.equal(a.values.size,0);
});
test('explicit unmanaged profile uses a stable dataset-and-name-specific key and rejects storage failure',async()=>{
 const a=app();a.ctx.OnAirRequestAccess.dinnerCredentials=async()=>null;
 const first=await available(a).credentials(),again=await a.api.credentials();assert.equal(first.key,again.key);assert.ok(first.key.length>=43);
 a.ctx.myName='다른 직원';const other=await a.api.credentials();assert.notEqual(other.key,first.key);
 const b=app();b.ctx.OnAirRequestAccess.dinnerCredentials=async()=>null;b.ctx.localStorage.setItem=()=>{throw Error('blocked');};await assert.rejects(available(b).credentials(),/저장|브라우저/);
});
test('summary events are deduplicated per dataset and employee without turning unread into unanswered',async()=>{
 const a=app();a.setSummary({...a.summary(),events:[{id:'event-1',pollId:'p-one',type:'PUBLISHED',text:'새 취합',createdAt:'2026-11-17T01:00:00Z'}]});
 await available(a).refresh();await a.api.refresh();assert.equal(a.notices.length,1);assert.equal(a.notices[0].meta.kind,'dinner');assert.equal(a.notices[0].meta.pollId,'p-one');
 assert.match(a.button.innerHTML,/새 투표|미응답/);a.ctx.myName='다른 직원';await a.api.refresh();assert.equal(a.notices.length,2);
});
test('an already submitted response from another PC never renders editable answer controls',async()=>{
 const a=app();a.summary().polls[0].responded=true;
 await available(a).open('p-one');const html=a.panels.at(-1).html;assert.match(html,/다른 PC|제출한 PC/);assert.doesNotMatch(html,/type="radio"/);
});
test('a vote renders a complete Sunday-first calendar and independent answer groups across months',async()=>{
 const a=app();await available(a).open('p-one');const html=a.panels.at(-1).html;
 assert.equal((html.match(/data-dinner-cell=/g)||[]).length,42);assert.match(html,/<strong>2099년 11월<\/strong>/);assert.match(html,/data-date="2099-11-24"/);assert.match(html,/2099년 12월/);assert.equal((html.match(/type="radio"/g)||[]).length,6);assert.match(html,/data-dinner-month/);
});
test('server errors preserve their HTTP status and useful user-facing message',async()=>{
 const a=app({network:async()=>({ok:false,status:409,json:async()=>({detail:'후보가 변경되었습니다.',code:'REVISION_CONFLICT'})})});
 await assert.rejects(available(a).api('/polls/p-one/response',{method:'POST',body:{}}),error=>error.status===409&&error.code==='REVISION_CONFLICT'&&error.message==='후보가 변경되었습니다.');
});
test('connection failures become a readable retry message without raw browser jargon',async()=>{
 const a=app({network:async()=>{throw new TypeError('Failed to fetch');}});
 await assert.rejects(available(a).api('/polls/p-one/response',{method:'POST',body:{}}),error=>/연결/.test(error.message)&&/유지/.test(error.message)&&!/Failed to fetch/.test(error.message));
});
test('the automatic refresh waits 30 seconds and skips a hidden document',async()=>{
 const a=app();available(a);await a.listeners.DOMContentLoaded?.();assert.equal(a.timers.at(-1).ms,30000);a.sent.length=0;a.ctx.document.hidden=true;await a.timers.at(-1).fn();assert.equal(a.sent.length,0);
});
test('the header enters a single unanswered poll directly and uses the list for multiple polls',async()=>{
 const a=app();await available(a).open();assert.match(a.panels.at(-1).html,/type="radio"/);
 const b=app();b.summary().polls.push({...structuredClone(poll),id:'p-two'});await available(b).open();assert.doesNotMatch(b.panels.at(-1).html,/type="radio"/);assert.match(b.panels.at(-1).html,/data-poll-id="p-two"/);
});
test('administrators without a selected employee receive deduplicated CA connection notices',async()=>{
 const a=app();a.ctx.myName='';a.ctx.SYSTEM_MODE='ADMIN';a.ctx.fetchWithAdminToken=a.ctx.fetch;a.summary().pendingConnections=[{id:'conn-one',status:'PENDING',memberName:'이서연'}];
 await available(a).refresh();await a.api.refresh();assert.equal(a.notices.length,1);assert.match(a.notices[0].text,/CA.*연결/);assert.equal(a.notices[0].meta.dinnerAction,'role');
});
test('response retry retains the operation id and answers even when randomUUID is unavailable',async()=>{
 const a=app();a.ctx.crypto={getRandomValues:array=>require('node:crypto').webcrypto.getRandomValues(array)};await available(a).open('p-one');
 for(const id of ['c-one','c-two'])a.modal.oninput({target:{dataset:{answerCandidate:id},value:'difficult'}});
 a.ctx.fetch=async(url,options)=>{a.sent.push({url,...options});return {ok:false,status:503,json:async()=>({detail:'저장 실패'})};};
 const event={target:{id:'dp-vote-form'},preventDefault(){}};await a.modal.onsubmit(event);await a.modal.onsubmit(event);
 const sent=a.sent.filter(request=>request.url.endsWith('/response')).map(request=>JSON.parse(request.body));assert.equal(sent.length,2);assert.equal(sent[0].operationId,sent[1].operationId);assert.deepEqual(sent[0].answers,{'c-one':'difficult','c-two':'difficult'});
});
test('confirmed employee detail describes calendar application as historical only when recorded',async()=>{
 const a=app();Object.assign(a.summary().polls[0],{status:'CONFIRMED',selectedCandidateId:'c-one'});await available(a).open('p-one');assert.doesNotMatch(a.panels.at(-1).html,/석식간담회.*반영했습니다/);
 a.summary().polls[0].dutyEffect={date:'2099-11-24',memo:'석식간담회',noDuty:true,appliedAt:'2099-11-20T10:00:00Z'};await a.api.open('p-one');assert.match(a.panels.at(-1).html,/확정 시 이 날짜에 석식간담회 메모와 당직 없음을 반영했습니다/);
});
test('cancelled employee detail explains recorded calendar effects remain for administrator adjustment',async()=>{
 const a=app();Object.assign(a.summary().polls[0],{status:'CANCELLED',selectedCandidateId:'c-one',dutyEffect:{date:'2099-11-24',memo:'석식간담회',noDuty:true}});await available(a).open('p-one');const html=a.panels.at(-1).html;assert.match(html,/석식간담회.*당직 없음.*유지/);assert.match(html,/근무자.*자동.*복원하지/);assert.match(html,/관리자.*달력.*조정/);
 delete a.summary().polls[0].dutyEffect;await a.api.open('p-one');assert.doesNotMatch(a.panels.at(-1).html,/근무자.*복원|달력.*유지/);
});
test('future CA can reach connection setup without receiving an early management button',async()=>{
 const a=app();a.summary().polls=[];
 a.summary().role={memberName:'현재 담당자',active:true};
 a.summary().assignedRole={memberName:'이서연',active:false,roleId:2099};
 a.summary().connection={status:'APPROVED',active:false,roleId:2099};
 await available(a).open(null,true);
 const html=a.panels.at(-1).html;assert.match(html,/data-dinner-action="role"/);assert.doesNotMatch(html,/data-dinner-action="manage"/);
});
test('active approved CA keeps the management entry',async()=>{
 const a=app();a.summary().polls=[];a.summary().role={memberName:'이서연',active:true};
 a.summary().assignedRole=a.summary().role;a.summary().connection={status:'APPROVED',active:true};
 await available(a).open(null,true);assert.match(a.panels.at(-1).html,/data-dinner-action="manage"/);
});
