'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const htmlPath=process.env.ONAIR_FRONTEND||path.resolve(__dirname,'../../frontend/dist/index.html');
const script=[...fs.readFileSync(htmlPath,'utf8').matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(m=>m[1]).find(s=>s.includes('function friday21Of('));
function app(){
  const timers=[];
  const nodes=new Map(),storage={getItem:()=>null,setItem(){},removeItem(){}};
  const element=id=>{if(!nodes.has(id))nodes.set(id,{value:'',style:{},textContent:'',classList:{add(){},remove(){},contains(){return true;}}});return nodes.get(id);};
  const ctx=vm.createContext({console,Date,localStorage:storage,sessionStorage:storage,
    document:{getElementById:element,querySelectorAll:()=>[],addEventListener(){}},
    setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},setInterval:()=>1,clearInterval(){},confirm:()=>true});
  ctx.window=ctx;ctx.addEventListener=()=>{};
  vm.runInContext(script,ctx,{filename:htmlPath});const run=code=>vm.runInContext(code,ctx);
  run(`SYSTEM_MODE='ADMIN';SERVER_CONNECTED=true;dataLoaded=true;dataVersion=1;
    memberList=[{name:'F1',gender:'F'},{name:'F3',gender:'F'}];
    scheduleMap={'2027-02-01':[{name:'F1',role:'평일(여)'}]};
    renderCalendar=()=>{};renderMemberModalList=()=>{};renderAbsenceTypeUI=()=>{};
    updateYearMonthUI=()=>{};renderDateModalUI=()=>{};populateSwapAndSubDropdowns=()=>{};
    markDataFresh=()=>{};markConnected=()=>{};connectionLost=()=>false;
    showToast=message=>{globalThis.message=message};showNotice=()=>{};
    rememberCommittedState();`);
  const remote={members:[{name:'F1',gender:'F'},{name:'F3',gender:'F'}],
    schedules:{'2027-02-01':[{name:'F3',role:'평일(여)'}]},dataVersion:2};
  const posts=[];ctx.fetch=async()=>({ok:true,json:async()=>remote});
  ctx.fetchWithAdminToken=async(url,options)=>{posts.push(JSON.parse(options.body));return {ok:true,json:async()=>({dataVersion:3})};};
  return {run,ctx,element,remote,posts,timers};
}
test('preview suspension defers data fetch without changing its source version',async()=>{
  const a=app();let calls=0;a.ctx.fetch=async()=>{calls++;return {ok:true,json:async()=>a.remote};};
  a.run('suspendAutoRefresh=true');await a.run('fetchAllData()');
  assert.equal(calls,0);assert.equal(a.run('dataVersion'),1);
  assert.equal(a.run("scheduleMap['2027-02-01'][0].name"),'F1');
  assert.equal(a.run('pendingRefresh'),true);
});
test('a response started before preview cannot replace the preview when it arrives',async()=>{
  const a=app();let complete;
  a.ctx.fetch=()=>new Promise(resolve=>{complete=resolve;});
  const loading=a.run('fetchAllData()');a.run('suspendAutoRefresh=true');
  complete({ok:true,json:async()=>a.remote});await loading;
  assert.equal(a.run('dataVersion'),1);assert.equal(a.run('pendingRefresh'),true);
});
test('stale date editor preserves the draft and never sends old workers with a new version',async()=>{
  const a=app();a.run("openDateModal('2027-02-01')");
  a.element('modal-date-comment').value='메모만 수정';
  await a.run('fetchAllData()');await a.run('saveDateModalChanges()');
  assert.equal(a.posts.length,0);
  assert.equal(a.run("scheduleMap['2027-02-01'][0].name"),'F3');
  assert.equal(a.element('modal-date-comment').value,'메모만 수정');
  assert.match(a.ctx.message,/다른|최신|변경/);
});
test('normal date save still sends selected workers and comment at the editor source version',async()=>{
  const a=app();a.run("openDateModal('2027-02-01')");
  a.element('modal-date-comment').value='정상 메모';await a.run('saveDateModalChanges()');
  assert.equal(a.posts.length,1);assert.equal(a.posts[0].baseVersion,1);
  assert.equal(a.posts[0].schedules['2027-02-01'][0].name,'F1');
  assert.equal(a.posts[0].comments['2027-02-01'],'정상 메모');
});
test('late HTTP response cannot roll back a newer loaded data version',async()=>{
  const a=app();let complete;
  a.ctx.fetch=()=>new Promise(resolve=>{complete=resolve;});
  const loading=a.run('fetchAllData()');a.run('dataVersion=3');
  complete({ok:true,json:async()=>a.remote});await loading;
  assert.equal(a.run('dataVersion'),3);
});
test('an already-started GET cannot replace edited workers while their save is in flight',async()=>{
  const a=app();let completeGet,completePost;
  a.ctx.fetch=()=>new Promise(resolve=>{completeGet=resolve;});
  const loading=a.run('fetchAllData()');
  a.run("openDateModal('2027-02-01');tempSelectedWorkers=[{slot:0,name:'F3',isSubstitute:false,zeroScore:false}]");
  a.ctx.fetchWithAdminToken=async(url,options)=>{a.posts.push(JSON.parse(options.body));return new Promise(resolve=>{completePost=resolve;});};
  const saving=a.run('saveDateModalChanges()');
  completeGet({ok:true,json:async()=>({schedules:{'2027-02-01':[{name:'F1',role:'평일(여)'}]},dataVersion:1})});
  await loading;completePost({ok:true,json:async()=>({dataVersion:2})});await saving;
  assert.equal(a.posts[0].schedules['2027-02-01'][0].name,'F3');
  assert.equal(a.run("scheduleMap['2027-02-01'][0].name"),'F3');
  assert.equal(a.run("committedState.schedules['2027-02-01'][0].name"),'F3');
});
test('a new GET is deferred while a save is active',async()=>{
  const a=app();let calls=0;a.ctx.fetch=async()=>{calls++;return {ok:true,json:async()=>a.remote};};
  a.run('isSaving=true');await a.run('fetchAllData()');
  assert.equal(calls,0);assert.equal(a.run('pendingRefresh'),true);
});
test('the first save cleanup cannot release refreshes during a second active save',async()=>{
  const a=app();await a.run("syncFullDataToBackend({comments:{'2027-02-01':'first'}})");
  let complete;a.ctx.fetchWithAdminToken=()=>new Promise(resolve=>{complete=resolve;});
  const second=a.run("syncFullDataToBackend({comments:{'2027-02-01':'second'}})");
  await a.timers[0]();assert.equal(a.run('isSaving'),true);
  complete({ok:true,json:async()=>({dataVersion:4})});await second;
});
