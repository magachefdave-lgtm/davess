'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {app,plain,generate}=require('./helpers/scheduler.cjs');
async function preview(run,year,month,scope='MONTH'){
 run(`openScopeModal=async()=>${JSON.stringify(scope)};openRotationStartModal=async()=>({});openAssignPreview=async(before,scope,label,info)=>{globalThis.explanation=info;return true;};`);
 const schedules=await generate(run,year,month,{silent:false});
 return {schedules,reasons:plain(run('explanation.assignmentReasons'))};
}
test('actual fixed choice explains absence skip and matches the silent assignment map',async()=>{
 const live=app(),silent=app(),config="absences=[{name:'F1',type:'연차/휴가',start:'2027-11-01',end:'2027-11-01'}]";
 live(config);silent(config);const result=await preview(live,2027,11);
 assert.deepEqual(result.schedules,await generate(silent,2027,11));
 const first=result.reasons.find(r=>r.date==='2027-11-01');
 assert.equal(first.name,'F2');assert.equal(first.ordinal,2);assert.deepEqual(first.skipped,[{name:'F1',reason:'2027-11-01 연차/휴가'}]);
 for(const r of result.reasons)if(r.name)assert.ok(result.schedules[r.date].some(w=>w.name===r.name));
});
test('Sunday reason identifies the actual previous worker excluded by the engine',async()=>{
 const run=app();run("dutyRule.fixedOrders.weekendM=['M1','M2'];absences=[{name:'M1',type:'교육',start:'2027-11-06',end:'2027-11-06'}]");
 const result=await preview(run,2027,11),row=result.reasons.find(r=>r.date==='2027-11-07'&&r.key==='weekend|M');
 assert.equal(row.name,'M1');assert.ok(row.skipped.some(x=>x.name==='M2'&&x.reason.includes('토·일 연속')));
});
test('festival block availability and retained team explanations preserve the assignments',async()=>{
 const live=app(),silent=app(),config="absences=[{name:'M1',type:'교육',start:'2027-02-09',end:'2027-02-09'}]";
 live(config);silent(config);const result=await preview(live,2027,2);
 assert.deepEqual(result.schedules,await generate(silent,2027,2));
 const major=result.reasons.filter(r=>r.key==='major|M');assert.ok(major.length>=2);
 assert.equal(major[0].name,'M2');assert.ok(major[0].skipped.some(x=>x.name==='M1'&&x.reason==='2027-02-09 교육'));
 assert.ok(major.slice(1).every(r=>r.reason.includes('연휴 담당 팀 유지')));
});
test('empty fixed roster fallback is described without inventing an ordinal',async()=>{
 const run=app();run('dutyRule.fixedOrders.weekday=[]');const result=await preview(run,2027,11);
 const row=result.reasons.find(r=>r.key==='weekday|F');assert.equal(row.ordinal,null);assert.equal(row.fallback,true);assert.match(row.reason,/가용 명단/);
});
test('restored start provenance records only accepted history and stays out of persisted indexes',()=>{
 const run=app();run("scheduleMap={'2027-10-29':[{name:'F2',role:'평일(여)'}]}");
 assert.deepEqual(plain(run("restoreRotationIndexes('2027-11-01').__basis['weekday|F']")),{kind:'record',date:'2027-10-29',name:'F2',substitute:false});
 assert.equal(run("Object.keys(restoreRotationIndexes('2027-11-01')).includes('__basis')"),false);
});
test('explanation UI escapes employee content and labels results before saving',()=>{
 const context={window:{},document:{getElementById:()=>null}};vm.createContext(context);
 vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../../frontend/dist/assignment_explanations.js'),'utf8'),context);
 const html=context.window.OnAirAssignment.explanationHtml([{date:'2027-11-01',key:'weekday|F',name:'<script>bad</script>',reason:'순번 & 근거',skipped:[{name:'A',reason:'교육'}]}]);
 assert.ok(html.includes('&lt;script&gt;bad&lt;/script&gt;'));assert.ok(!html.includes('<script>'));assert.match(html,/아직 저장 전/);assert.match(html,/실제 배정 대상/);
});

test('retained supplement workers claim only accepted original female credit',async()=>{
 for(const credit of [null,{consumes:false,after:1},{consumes:true,after:'1'},{consumes:true,after:1}]){
  const run=app(),female_credit=credit && {pool_key:'weekday|F',before:0,order_snapshot:['F1','F2','F3'],name:'원래 <여성>',...credit};
  run(`holidaySupplements={events:[${JSON.stringify({date_key:'2027-11-01',year:2027,state:'ACTIVE',female_credit,original_female_slot:{name:'F1'}})}],bases:[]};scheduleMap={'2027-11-01':[{name:'M3',role:'공휴일(남)'},{name:'F3',role:'공휴일(여)'}]}`);
  const result=await preview(run,2027,11),rows=result.reasons.filter(r=>r.date==='2027-11-01');
  assert.deepEqual(rows.map(r=>r.name),['M3','F3']);
  for(const row of rows){
   assert.match(row.reason,/기존 근무 유지/);
   if(credit?.consumes && Number.isInteger(credit.after)){
    assert.match(row.reason,/원래 <여성>의 차례/);assert.ok(!row.reason.includes(row.name+'의 차례'));
   }else assert.ok(!row.reason.includes('순번 소비'));
  }
 }
});
test('festival first-day ordinal and empty-roster fallback survive final trace',async()=>{
 for(const empty of [false,true]){
  const live=app(),silent=app();if(empty){live('dutyRule.fixedOrders.majorM=[]');silent('dutyRule.fixedOrders.majorM=[]');}
  const result=await preview(live,2027,2);assert.deepEqual(result.schedules,await generate(silent,2027,2));
  const row=result.reasons.find(r=>r.key==='major|M');
  assert.equal(row.ordinal,empty?null:1);assert.equal(row.fallback,empty);assert.equal(row.name,'M1');
  if(empty){
   const context={window:{},document:{getElementById:()=>null}};vm.createContext(context);
   vm.runInContext(fs.readFileSync(path.resolve(__dirname,'../../frontend/dist/assignment_explanations.js'),'utf8'),context);
   assert.match(context.window.OnAirAssignment.explanationHtml([row]),/순번 밖·미배정 1건/);
  }
 }
});
test('inherited festival team temporary replacement is marked fallback without team ordinal',async()=>{
 const run=app();
 run(`const originalSlotCategory=slotCategory;majorDutyBlockStart=key=>key==='2027-11-01'?'2027-10-31':null;majorDutyDays=()=>['2027-10-31','2027-11-01'];slotCategory=(key,gender)=>key==='2027-11-01'?'major':originalSlotCategory(key,gender);scheduleMap={'2027-10-31':[{name:'M1',role:'명절(남)'},{name:'F1',role:'명절(여)'}]};absences=[{name:'M1',type:'교육',start:'2027-11-01',end:'2027-11-01'}]`);
 const result=await preview(run,2027,11),row=result.reasons.find(r=>r.date==='2027-11-01'&&r.key==='major|M');
 assert.equal(row.name,'M2');assert.equal(row.fallback,true);assert.equal(row.ordinal,null);assert.ok(row.skipped.some(r=>r.name==='M1'));
});
