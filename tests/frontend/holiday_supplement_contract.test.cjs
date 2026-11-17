'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const dist=path.resolve(__dirname,'../../frontend/dist');
const common=fs.readFileSync(path.join(dist,'usability_common.js'),'utf8');
const requests=fs.readFileSync(path.join(dist,'usability_requests.js'),'utf8');
const html=fs.readFileSync(path.join(dist,'index.html'),'utf8');
const base=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m=>m[1]).find(s=>s.includes('async function processApproval('));
const origin='https://onair.example:8443';
const contract='X-OnAir-Data-Contract';

function boundary(){
  const calls=[],response=new Response('{}');
  const ctx=vm.createContext({URL,Headers,Request,location:new URL(origin+'/index.html'),document:{baseURI:origin+'/index.html'},fetch:function(...args){calls.push({args,receiver:this});return Promise.resolve(response);}});
  ctx.window=ctx;
  vm.runInContext(common,ctx);
  return {ctx,calls,response,fetch:(...args)=>ctx.fetch(...args)};
}
function outgoing(a){
  const [input,init]=a.calls.at(-1).args;
  return new Request(input instanceof Request?input:new URL(input,origin),init);
}

test('business mutations advertise the contract without changing caller headers or body',async()=>{
  const a=boundary();
  for(const [url,method] of [
    ['/api/duty/save-all','POST'],['/api/duty/request/action','POST'],
    ['/api/duty/request/manage','POST'],['/api/duty/request-change','POST'],
    ['/api/duty/month-close','POST'],['/api/duty/holiday-supplement/apply','POST'],
    ['/api/system/restore?source=backup','POST'],['/api/system/import-json','POST'],
    ['/api/duty/save-all','PUT'],['/api/duty/save-all','PATCH'],['/api/duty/save-all','DELETE'],
  ]){
    const headers={'Content-Type':'application/json','X-Admin-Token':'existing-token'};
    const init={method,headers,body:'{"baseVersion":7}',credentials:'same-origin'};
    assert.equal(await a.fetch(url,init),a.response);
    const sent=outgoing(a);
    assert.equal(sent.url,origin+url);assert.equal(sent.headers.get(contract),'1',url+' '+method);
    assert.equal(sent.headers.get('X-Admin-Token'),'existing-token');
    assert.equal(await sent.text(),'{"baseVersion":7}');assert.equal(sent.credentials,'same-origin');
    assert.deepEqual(headers,{'Content-Type':'application/json','X-Admin-Token':'existing-token'});
    assert.equal(init.headers,headers);
  }
});

test('same-origin URL and Request inputs retain their native request payload and cancellation',async()=>{
  const a=boundary(),controller=new AbortController();
  const input=new Request(origin+'/api/duty/save-all',{method:'POST',headers:new Headers({'X-Admin-Token':'request-token','Content-Type':'application/json'}),body:'{"baseVersion":8}',signal:controller.signal,credentials:'include',cache:'no-store',redirect:'error',referrerPolicy:'no-referrer'});
  // Node and Chromium differ on inherited referrer policy; compare with the native
  // constructor, independently of the wrapper. Chromium is checked separately.
  const nativePolicy=new Request(input.clone()).referrerPolicy;
  await a.fetch(input);
  assert.equal(input.headers.has(contract),false);
  const sent=outgoing(a);
  assert.equal(sent.headers.get(contract),'1');assert.equal(sent.headers.get('X-Admin-Token'),'request-token');
  assert.equal(sent.credentials,'include');assert.equal(sent.cache,'no-store');assert.equal(sent.redirect,'error');assert.equal(sent.referrerPolicy,nativePolicy);
  assert.equal(await sent.text(),'{"baseVersion":8}');
  controller.abort();assert.equal(sent.signal.aborted,true);
  const url=new URL(origin+'/api/duty/request-change');
  await a.fetch(url,{method:'POST',body:'request'});
  const fromURL=outgoing(a);assert.equal(fromURL.url,origin+'/api/duty/request-change');assert.equal(fromURL.headers.get(contract),'1');
});

test('explicit Request init overrides keep Headers semantics, body, auth and AbortSignal',async()=>{
  const a=boundary(),controller=new AbortController();
  const input=new Request(origin+'/api/duty/save-all',{method:'POST',headers:{'X-Old':'discard'},body:'old'});
  const headers=new Headers({'X-Admin-Token':'override-token','X-OnAir-Data-Contract':'0'});
  const options={method:'PATCH',headers,body:'new',signal:controller.signal,credentials:'omit',keepalive:true};
  await a.fetch(input,options);
  const sent=outgoing(a);
  assert.equal(sent.credentials,'omit');assert.equal(sent.keepalive,true);
  assert.equal(sent.method,'PATCH');assert.equal(sent.headers.get(contract),'1');assert.equal(sent.headers.get('X-Admin-Token'),'override-token');
  assert.equal(sent.headers.has('X-Old'),false,'init.headers replaces Request headers instead of merging them');
  assert.equal(await sent.text(),'new');assert.equal(await input.text(),'old');assert.equal(headers.get(contract),'0');assert.equal(options.headers,headers);
  controller.abort();assert.equal(sent.signal.aborted,true);
  const tupleHeaders=[['X-Admin-Token','tuple-token']];
  await a.fetch('/api/duty/request/manage',{method:'POST',headers:tupleHeaders});
  assert.equal(outgoing(a).headers.get('X-Admin-Token'),'tuple-token');
  assert.deepEqual(tupleHeaders,[['X-Admin-Token','tuple-token']]);
});

test('reads, non-business endpoints and external origins pass through without decoration',async()=>{
  const a=boundary();
  for(const [input,init] of [
    ['/api/duty/all-data',undefined],['/api/duty/save-all',{method:'GET'}],
    ['/api/duty/save-all',{method:'HEAD'}],['/api/duty/save-all',{method:'OPTIONS'}],
    ['/api/system/backup',{method:'POST'}],['/api/system/snapshot',{method:'POST'}],
    ['/api/system/check-path',{method:'POST'}],['/api/system/recheck-storage',{method:'POST'}],
    ['/api/system/manual-update-ready',{method:'POST'}],['/unrelated',{method:'POST'}],
    ['https://external.example/api/duty/save-all',{method:'POST'}],
    ['//external.example/api/duty/save-all',{method:'POST'}],
    ['https://onair.example/api/duty/save-all',{method:'POST'}],
    ['http://onair.example:8443/api/duty/save-all',{method:'POST'}],
    [new Request('https://external.example/api/duty/save-all',{method:'POST',body:'private'}),undefined],
    [new Request(origin+'/api/duty/save-all',{method:'POST'}),{method:'GET'}],
  ]){
    await a.fetch(input,init);
    const [sentInput,sentInit]=a.calls.at(-1).args;
    assert.equal(sentInput,input);assert.equal(sentInit,init);
    assert.equal(new Headers(sentInit?.headers).has(contract),false,String(input));
  }
});

// The network boundary is the only I/O double. Approval, authentication retries,
// request rendering and the all-data reload execute the shipping scripts.
function approvalApp(extra={}){
  const nodes=new Map(),calls=[],toasts=[],vacancies=[],passwordPrompts=[];
  let document;
  const node=id=>{
    if(!nodes.has(id)){
      const classes=new Set();
      nodes.set(id,{id,isConnected:true,value:'',checked:false,disabled:false,hidden:false,innerHTML:'',textContent:'',style:{},dataset:{},children:[],scrollTop:0,scrollLeft:0,
        classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle:(x,v)=>v?classes.add(x):classes.delete(x)},
        setAttribute(k,v){this[k]=v;},removeAttribute(k){delete this[k];},getAttribute(k){return this[k];},
        appendChild(child){this.children.push(child);return child;},addEventListener(){},insertAdjacentHTML(){},
        querySelector(selector){return id==='approval-modal'&&selector==='.approval-scroll'?node('approval-scroll'):null;},
        querySelectorAll(){return [];},closest(){return null;},focus(){document.activeElement=this;}});
    }
    return nodes.get(id);
  };
  document={baseURI:origin+'/index.html',getElementById:id=>/^req-zero-/.test(id)?nodes.get(id)||null:node(id),querySelector:()=>null,querySelectorAll:()=>[],addEventListener(){},createElement:()=>node('created-'+nodes.size),head:node('head'),body:node('body'),activeElement:null};
  const storage={getItem:()=>null,setItem(){},removeItem(){}};
  const row={id:41,status:'PENDING',revision:0,impact_digest:'impact-displayed',request_type:'ABSENCE',requester_name:'가영',target_date:'2026-11-11',absence_end_date:'2026-11-12',absence_type:'교육',reason:'교육 참석',created_at:'2026-11-09 09:00:00',affectedDates:['2026-11-11'],...extra};
  const state={rows:[row],actionStatus:200,actionBody:{removedScheduleDates:[]},password:null,
    data:{members:[{name:'가영',gender:'F'},{name:'민수',gender:'M'}],schedules:{'2026-11-11':[{name:'가영',role:'공휴일',isSubstitute:false,zeroScore:false},{name:'민수',role:'공휴일',isSubstitute:false,zeroScore:false}]},comments:{},noDuty:{},absences:[],absenceTypes:['교육','연차'],customHolidays:{'2026-11-11':{name:'지정 공휴일'}},rules:{},history:[],globalMemo:'',closedMonths:[],dataVersion:2,holidaySupplements:{schema:1,events:[],bases:[],requiredContract:1}}};
  const ctx=vm.createContext({console,Date,URL,Headers,Request,Response,document,location:new URL(origin+'/index.html'),localStorage:storage,sessionStorage:storage,addEventListener(){},setTimeout:()=>0,clearTimeout(){},setInterval:()=>0,clearInterval(){},requestAnimationFrame:fn=>fn(),confirm:()=>true,
    fetch:async(input,options={})=>{
      const sent=new Request(input instanceof Request?input:new URL(input,origin),options);
      const url=new URL(sent.url).pathname,body=await sent.text();
      const entry={url,method:sent.method,headers:sent.headers,body:body?JSON.parse(body):undefined};calls.push(entry);
      if(url==='/api/duty/requests')return Response.json({requests:state.rows,pendingCount:state.rows.filter(r=>r.status==='PENDING').length});
      if(url==='/api/duty/all-data')return Response.json(state.data);
      if(url==='/api/duty/request/action')return state.onAction?state.onAction(entry):Response.json(state.actionBody,{status:state.actionStatus});
      throw new Error('Unexpected API request: '+url);
    },toasts,vacancies,passwordPrompts,state});
  ctx.window=ctx;
  vm.runInContext(base,ctx);
  const run=source=>vm.runInContext(source,ctx);
  run(`SYSTEM_MODE='ADMIN';SERVER_CONNECTED=true;dataLoaded=true;dataVersion=1;ADMIN_TOKEN='existing-token';
    memberList=[{name:'가영',gender:'F'},{name:'민수',gender:'M'}];scheduleMap={'2026-11-11':[{name:'가영',role:'평일'}]};
    closedMonths=[];absences=[];customHolidays={};
    renderCalendar=()=>{};renderMemberModalList=()=>{};renderAbsenceTypeUI=()=>{};updateYearMonthUI=()=>{};
    showToast=(text,error)=>toasts.push({text,error});openVacancyModal=(dates)=>vacancies.push({dates,version:dataVersion});
    askConfirm=async()=>true;confirmConsecutiveRisk=async()=>true;askRejectReason=async()=> '보완 필요';
    requestAdminPassword=async(message)=>{passwordPrompts.push(message);return state.password;};`);
  vm.runInContext(common,ctx);vm.runInContext(requests,ctx);
  if(row.request_type==='SUBSTITUTE')node('req-zero-41');
  return {ctx,run,node,state,calls,toasts,vacancies,passwordPrompts,load:()=>run('openApprovalModal()'),act:action=>run(`processApproval(41, '${action}')`)};
}

test('approval submits the displayed impact and revision even when confirmation refreshes the queue',async()=>{
  const a=approvalApp();await a.load();
  a.state.actionBody={removedScheduleDates:['2026-11-11']};
  a.run(`askConfirm=async()=>{approvalCache[0].revision=1;approvalCache[0].impact_digest='impact-newer';return true;}`);
  await a.act('APPROVE');
  const sent=a.calls.find(c=>c.method==='POST');
  assert.deepEqual(sent.body,{request_id:41,action:'APPROVE',reject_reason:'',request_revision:0,impact_digest:'impact-displayed'});
  assert.equal(sent.headers.get(contract),'1');assert.equal(sent.headers.get('X-Admin-Token'),'existing-token');
  assert.deepEqual(JSON.parse(JSON.stringify(a.vacancies)),[{dates:['2026-11-11'],version:2}]);
  assert.equal(a.calls.filter(c=>c.method==='POST').length,1);
});

test('substitute scoring and rejection remain independent of the approval impact digest',async()=>{
  const a=approvalApp({request_type:'SUBSTITUTE',substitute_name:'민수',is_zero_score:true});await a.load();
  a.node('req-zero-41').checked=false;await a.act('APPROVE');
  const approved=a.calls.find(c=>c.method==='POST');
  assert.equal(approved.body.zero_score,false);assert.equal(approved.body.impact_digest,'impact-displayed');
  const b=approvalApp({request_type:'SUBSTITUTE',substitute_name:'민수'});await b.load();
  b.run(`askRejectReason=async()=>{approvalCache[0].revision=1;approvalCache[0].impact_digest='changed';return '보완 필요';}`);
  await b.act('REJECT');
  assert.deepEqual(b.calls.find(c=>c.method==='POST').body,{request_id:41,action:'REJECT',reject_reason:'보완 필요',request_revision:0});
});

test('older request responses do not cause a fabricated approval digest',async()=>{
  const a=approvalApp({impact_digest:undefined});await a.load();await a.act('APPROVE');
  const sent=a.calls.find(c=>c.method==='POST');
  assert.equal(Object.hasOwn(sent.body,'impact_digest'),false);assert.equal(sent.body.request_revision,0);
});

test('an approval impact conflict reloads calendar and queue without approving again',async()=>{
  const a=approvalApp();await a.load();
  a.state.rows=[{...a.state.rows[0],impact_digest:'impact-current',blocker:'공휴일로 바뀌어 승인 조건을 다시 확인하세요.'}];
  a.state.actionStatus=409;a.state.actionBody={detail:'일정이나 공휴일 조건이 바뀌었습니다. 최신 내용을 확인한 뒤 다시 승인하세요.'};
  await a.act('APPROVE');
  assert.deepEqual(a.calls.map(c=>c.url),['/api/duty/requests','/api/duty/request/action','/api/duty/all-data','/api/duty/requests']);
  assert.equal(a.run('dataVersion'),2);assert.equal(a.run("scheduleMap['2026-11-11'].length"),2);
  assert.equal(a.run("finalHolidays['2026-11-11'].name"),'지정 공휴일');
  assert.equal(a.run('approvalCache[0].impact_digest'),'impact-current');
  assert.match(a.node('approval-tbody').innerHTML,/공휴일로 바뀌어/);
  assert.match(a.toasts.at(-1).text,/최신 내용을 확인한 뒤 다시 승인/);assert.equal(a.toasts.at(-1).error,true);
  assert.equal(a.vacancies.length,0);assert.equal(a.run('approvalProcessing'),false);
});

test('authentication retries keep the approved digest and the new token',async()=>{
  const a=approvalApp();await a.load();a.state.password='renewed-token';
  a.state.onAction=entry=>entry.headers.get('X-Admin-Token')==='existing-token'
    ?Response.json({detail:'관리자 암호를 확인하세요.'},{status:401}):Response.json({removedScheduleDates:[]});
  await a.act('APPROVE');
  const actions=a.calls.filter(c=>c.method==='POST');
  assert.equal(actions.length,2);assert.equal(a.passwordPrompts.length,1);
  for(const action of actions){assert.equal(action.body.impact_digest,'impact-displayed');assert.equal(action.body.request_revision,0);assert.equal(action.headers.get(contract),'1');}
  assert.equal(actions[0].headers.get('X-Admin-Token'),'existing-token');assert.equal(actions[1].headers.get('X-Admin-Token'),'renewed-token');
});

test('cancelled authentication does not trigger a business reload or an approval retry',async()=>{
  const a=approvalApp();await a.load();a.state.actionStatus=401;a.state.actionBody={detail:'관리자 암호를 확인하세요.'};
  await a.act('APPROVE');
  assert.deepEqual(a.calls.map(c=>c.url),['/api/duty/requests','/api/duty/request/action']);
  assert.equal(a.passwordPrompts.length,1);assert.match(a.toasts.at(-1).text,/관리자 암호 입력을 취소/);
  assert.equal(a.run('dataVersion'),1);assert.equal(a.vacancies.length,0);
});
