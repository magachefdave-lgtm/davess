'use strict';
const assert = require('node:assert/strict');
const {test} = require('node:test');
const {app, plain, generate} = require('./helpers/scheduler.cjs');

const empty = () => ({schema:1, events:[], bases:[], requiredContract:0});
const slot = (name, role, extra={}) => ({name,role,...extra});
function basis(year=2027, extra={}) {
  return {year,pool_key:'holiday|M',revision:1,sequence_snapshot:['M1','M2','M3'],
    prior_year_credit_hash:'fixture-prior',ordinary_reservation_hash:'fixture-ordinary',
    ordinary_tail_position:2,anchor_before_year:0,effective_boundary:`${year}-01-01`,
    policy:'PRESERVE_EXISTING',...extra};
}
function event(extra={}) {
  return {supplement_id:'s1',date_key:'2027-11-10',year:2027,allocation_seq:1,state:'ACTIVE',credit_state:'CONSUMED',
    basis_revision:1,pool_key:'holiday|M',order_snapshot:['M1','M2','M3'],order_fingerprint:'fixture-order',
    annual_basis_hash:'fixture-basis',selected_ordinal:0,end_ordinal:1,
    original_female_slot:slot('F2','평일(여)'),female_credit:{pool_key:'weekday|F',before:1,after:2,consumes:true,order_snapshot:['F1','F2','F3']},
    current_slot_binding:[slot('F2','공휴일(여)'),slot('M3','공휴일(남)')],created_at:'2026-11-09T00:00:00Z',...extra};
}
function set(run, key, value) { run(`${key}=${JSON.stringify(value)}`); }
function setup(events=[event()], bases=[basis()]) {
  const run = app();
  run(`dutyRule.fixedOrders.holidayM=['M1','M2','M3'];`);
  set(run,'holidaySupplements',{schema:1,events,bases,requiredContract:1});
  set(run,'customHolidays',{'2027-11-10':{name:'추가 휴일'}});
  set(run,'scheduleMap',{'2027-01-01':[slot('M1','공휴일(남)')], '2027-11-09':[slot('F1','평일(여)')],
    '2027-11-10':[slot('F2','공휴일(여)'),slot('M3','공휴일(남)')], '2027-12-27':[slot('M2','공휴일(남)')]});
  run('mergeHolidays()');
  return run;
}

test('removed actual female still consumes her original weekday turn without becoming a worker', () => {
  const run=setup();
  run(`scheduleMap['2027-11-10']=[{name:'M3',role:'공휴일(남)'}]`);
  assert.equal(run(`restoreRotationIndexes('2027-11-11')['weekday|F']`),2);
  assert.deepEqual(plain(run(`scheduleMap['2027-11-10']`)),[slot('M3','공휴일(남)')]);
  assert.equal(run(`rotationYearTallies('2027-12-31').totals['holiday|F']`),0);
});
test('date-level original credit remains when the entire actual date key was deleted', () => {
  const run=setup(); run(`delete scheduleMap['2027-11-10']`);
  assert.equal(run(`restoreRotationIndexes('2027-11-11')['weekday|F']`),2);
  assert.equal(run(`Object.hasOwn(scheduleMap,'2027-11-10')`),false);
});
test('supplement never consumes the actual holiday female or the male queue in calendar order', () => {
  const run=setup();
  const idx=plain(run(`restoreRotationIndexes('2027-11-11')`));
  assert.equal(idx['weekday|F'],2);
  assert.equal(idx['holiday|F'],0);
  assert.equal(idx['holiday|M'],1);
  assert.equal(run(`rotationYearTallies('2027-12-31').totals['holiday|F']`),1,'actual public statistics still include the real holiday worker');
});
test('out-of-order substitute does not gain a weekday credit and zero score is independent', () => {
  const noCredit=event({original_female_slot:slot('F3','평일(여)',{isSubstitute:true,zeroScore:true}),
    female_credit:{pool_key:'weekday|F',before:1,after:1,consumes:false,order_snapshot:['F1','F2','F3']}});
  const run=setup([noCredit]);
  assert.equal(run(`restoreRotationIndexes('2027-11-11')['weekday|F']`),1);
  set(run,'holidaySupplements.events',[event({original_female_slot:slot('F2','평일(여)',{zeroScore:true})})]);
  assert.equal(run(`restoreRotationIndexes('2027-11-11')['weekday|F']`),2);
});
test('male credits fold after the annual ordinary tail exactly at the exclusive January boundary', () => {
  const run=setup();
  assert.equal(run(`restoreRotationIndexes('2027-12-31')['holiday|M']`),2);
  assert.equal(run(`restoreRotationIndexes('2028-01-01')['holiday|M']`),0);
  assert.equal(run(`restoreRotationIndexes('2028-01-02')['holiday|M']`),0);
});
test('reverse-date supplement order uses stored end ordinal including skipped candidates', () => {
  const run=setup([event(),event({supplement_id:'s2',date_key:'2027-04-14',allocation_seq:2,selected_ordinal:3,end_ordinal:4})]);
  assert.equal(run(`restoreRotationIndexes('2028-01-01')['holiday|M']`),0,'ordinary tail 2 plus 4 positions, not count of two events');
});
test('cancelled consumed male keeps its reservation while restored female uses only actual weekday row', () => {
  const run=setup([event({state:'CANCELLED',female_credit:{pool_key:'weekday|F',before:2,after:0,consumes:true,order_snapshot:['F1','F2','F3']}})]);
  run(`delete customHolidays['2027-11-10'];scheduleMap['2027-11-10']=[{name:'F2',role:'평일(여)'}];mergeHolidays()`);
  assert.equal(run(`restoreRotationIndexes('2027-11-11')['weekday|F']`),2);
  assert.equal(run(`restoreRotationIndexes('2028-01-01')['holiday|M']`),0);
  set(run,'holidaySupplements.events',[event({state:'CANCELLED',credit_state:'RELEASED'})]);
  assert.equal(run(`restoreRotationIndexes('2028-01-01')['holiday|M']`),2);
});
test('reconciled next-year anchor is applied after previous tail and before first ordinary date', () => {
  const run=setup([event()],[basis(),basis(2028,{revision:2,anchor_before_year:1,ordinary_tail_position:1})]);
  assert.equal(run(`restoreRotationIndexes('2028-01-01')['holiday|M']`),1);
  run(`scheduleMap['2028-01-03']=[{name:'M2',role:'공휴일(남)'}];customHolidays['2028-01-03']={name:'검증 휴일'};mergeHolidays()`);
  assert.equal(run(`restoreRotationIndexes('2028-01-04')['holiday|M']`),2);
});
for (const [n,sequence,tail,end,want] of [
  [2,['M1','M2','M2','M1'],3,2,1], [3,['M1','M2','M3'],2,2,1],
  [4,['M1','M2','M3','M4','M2','M1','M4','M3'],6,3,1],
  [6,['M1','M2','M3','M4','M5','M6','M2','M1','M4','M3','M6','M5'],10,3,1]
]) test(`shared weekend male tail retains occurrence for ${n} people`, () => {
  const run=setup([event({pool_key:'weekend|M',order_snapshot:sequence,end_ordinal:end,selected_ordinal:end-1})],
    [basis(2027,{pool_key:'weekend|M',sequence_snapshot:sequence,ordinary_tail_position:tail})]);
  set(run,'memberList',[...Array.from({length:n},(_,i)=>({id:i+1,name:`M${i+1}`,gender:'M'})),...Array.from({length:3},(_,i)=>({id:n+i+1,name:`F${i+1}`,gender:'F'}))]);
  set(run,'dutyRule.fixedOrders.weekendM',Array.from({length:n},(_,i)=>`M${i+1}`));
  run(`dutyRule.fixedOrders.holidayM=[]`);
  assert.equal(run(`restoreRotationIndexes('2028-01-01')['weekend|M']`),want);
});

async function generatedSupplement({rule21=false,removed=false,manualFamily=false}={}) {
  const run=app(); set(run,'holidaySupplements',empty());
  run(`dutyRule.fixedOrders.holidayM=['M1','M2','M3'];dutyRule.rule21FridayOff=${rule21}`);
  if (manualFamily) run(`noDutyMap['2027-11-05']=true;commentMap['2027-11-05']='패밀리데이'`);
  const before=await generate(run,2027,11,{scope:'YEAR'});
  const original=before['2027-11-10'][0];
  const beforeCredit=run(`restoreRotationIndexes('2027-11-10')['weekday|F']`);
  const tail=run(`restoreRotationIndexes('2028-01-01')['holiday|M']`);
  const e=event({original_female_slot:original,female_credit:{pool_key:'weekday|F',before:beforeCredit,after:(['F1','F2','F3'].indexOf(original.name)+1)%3,consumes:true,order_snapshot:['F1','F2','F3']},
    current_slot_binding:[{...original,role:'공휴일(여)'},slot('M3','공휴일(남)')]});
  set(run,'holidaySupplements',{schema:1,events:[e],bases:[basis(2027,{ordinary_tail_position:tail})],requiredContract:1});
  set(run,`customHolidays['2027-11-10']`,{name:'추가 휴일'});
  set(run,`scheduleMap['2027-11-10']`,removed?[slot('M3','공휴일(남)')]:e.current_slot_binding);
  run('mergeHolidays()');
  return {run,before,actual:plain(run(`scheduleMap['2027-11-10']`))};
}
for (const options of [{scope:'MONTH'},{scope:'FROM_MONTH'},{scope:'YEAR'},{scope:'MONTH',fresh:true},{scope:'FROM_MONTH',fresh:true},{scope:'YEAR',fresh:true},{scope:'YEAR',familyReset:true}]) {
  test(`actual generator preserves supplemental date and ordinary rows: ${JSON.stringify(options)}`,async()=>{
    const {run,before,actual}=await generatedSupplement({rule21:!!options.familyReset});
    run('globalThis.savedPayload=null');
    const after=await generate(run,2027,11,options);
    assert.ok(run('savedPayload'),'valid unchanged reservation basis must permit generation');
    assert.deepEqual(after['2027-11-10'],actual);
    const ordinaryBefore={...before}, ordinaryAfter={...after};delete ordinaryBefore['2027-11-10'];delete ordinaryAfter['2027-11-10'];
    assert.deepEqual(ordinaryAfter,ordinaryBefore);
  });
}
test('generator never resurrects the original female after an approved absence',async()=>{
  const {run,before,actual}=await generatedSupplement({removed:true});
  run('globalThis.savedPayload=null');
  const after=await generate(run,2027,11,{scope:'MONTH',fresh:true});
  assert.ok(run('savedPayload'));
  assert.deepEqual(after['2027-11-10'],actual);
  assert.deepEqual(after['2027-11-11'],before['2027-11-11']);
});
test('NEEDS_REVIEW blocks only a scope which actually regenerates that pool',async()=>{
  const run=setup([event({state:'NEEDS_REVIEW'})]);
  const before=plain(run('scheduleMap'));
  await generate(run,2027,10,{scope:'MONTH'});
  assert.deepEqual(plain(run('scheduleMap')),before);
  assert.match(run('lastToast.message'),/기준|재확인/);
  run(`globalThis.savedPayload=null`);
  await generate(run,2027,6,{scope:'MONTH'});
  assert.ok(run('savedPayload'),'June has no ordinary weekday holiday male queue work');
});
test('next-year stale basis blocks generation even without a same-year supplement event',async()=>{
  const run=setup([event()],[basis(),basis(2028,{state:'NEEDS_REVIEW',review_code:'BASIS_RECONFIRM_REQUIRED'})]);
  const before=plain(run('scheduleMap'));
  await generate(run,2028,3,{scope:'MONTH'});
  assert.deepEqual(plain(run('scheduleMap')),before);
});
test('changed consumed queue sequence cannot silently reinterpret old ordinals',async()=>{
  const run=setup();run(`dutyRule.fixedOrders.holidayM=['M2','M1','M3']`);
  const before=plain(run('scheduleMap'));
  await generate(run,2027,10,{scope:'MONTH'});
  assert.deepEqual(plain(run('scheduleMap')),before);
});
test('explicit start override cannot silently move a consumed supplement queue',async()=>{
  const {run}=await generatedSupplement();const before=plain(run('scheduleMap'));
  await generate(run,2027,1,{scope:'YEAR',rotationOverrides:{'holiday|M':{cycle:1,name:'M1'}}});
  assert.deepEqual(plain(run('scheduleMap')),before);
});
test('all-data refresh and failed-edit rollback retain authoritative supplemental metadata',async()=>{
  const run=app();const metadata={schema:1,events:[event()],bases:[basis()],requiredContract:1};
  set(run,'globalThis.serverData',{members:[],schedules:{},comments:{},noDuty:{},absences:[],absenceTypes:[],customHolidays:{},rules:{algoType:'FIXED_ORDER'},history:[],globalMemo:'',closedMonths:[],dataVersion:9,holidaySupplements:metadata});
  run(`SERVER_CONNECTED=true;globalThis.fetch=async()=>({ok:true,json:async()=>serverData});discardUndo=()=>{};markConnected=()=>{};renderMemberModalList=()=>{};renderAbsenceTypeUI=()=>{}`);
  assert.equal(await run('fetchAllData()'),true);
  assert.deepEqual(plain(run(`typeof holidaySupplements==='undefined'?null:holidaySupplements`)),metadata);
  run(`holidaySupplements.events=[];restoreCommittedState()`);
  assert.deepEqual(plain(run('holidaySupplements')),metadata);
  run(`delete serverData.holidaySupplements;serverData.dataVersion=10`);
  await run('fetchAllData()');
  assert.deepEqual(plain(run('holidaySupplements')),empty());
});

test('ordinary male reservation changes require rebase before regeneration can save',async()=>{
  const {run}=await generatedSupplement();
  run(`scheduleMap['2027-10-04']=[{name:'M3',role:'공휴일(남)'},{name:'F1',role:'공휴일(여)'}]`);
  const before=plain(run('scheduleMap'));run('globalThis.savedPayload=null');
  await generate(run,2027,10,{scope:'MONTH'});
  assert.deepEqual(plain(run('scheduleMap')),before);
  assert.equal(run('savedPayload'),null);
  assert.match(run('lastToast.message'),/기준|재확인/);
});
test('family reset conflicting with the preserved weekday credit rolls back all local changes',async()=>{
  const {run}=await generatedSupplement({rule21:true,manualFamily:true});
  // A previously chosen family day before the supplement is cleared by explicit reset.
  // Regeneration would change the female cursor at the preserved original weekday turn.
  const before=plain(run('({scheduleMap,noDutyMap,commentMap})'));run('globalThis.savedPayload=null');
  await generate(run,2027,11,{scope:'MONTH',familyReset:true});
  assert.deepEqual(plain(run('({scheduleMap,noDutyMap,commentMap})')),before);
  assert.equal(run('savedPayload'),null);
});
test('a removed supplemental date remains absent through actual generation',async()=>{
  const {run,before}=await generatedSupplement();run(`delete scheduleMap['2027-11-10']`);
  run('globalThis.savedPayload=null');
  const after=await generate(run,2027,11,{scope:'MONTH'});
  assert.ok(run('savedPayload'));
  assert.equal(Object.hasOwn(after,'2027-11-10'),false);
  assert.deepEqual(after['2027-11-11'],before['2027-11-11']);
});
test('actual next-year generation starts the dedicated holiday pool after the consumed year tail',async()=>{
  const run=setup();
  const after=await generate(run,2028,1,{scope:'YEAR'});
  assert.equal(after['2028-03-01'].find(w=>w.role==='공휴일(남)').name,'M1');
});
test('weekday sequence changes block original female credit without globally locking the year',async()=>{
  const {run}=await generatedSupplement();run(`dutyRule.fixedOrders.weekday=['F2','F1','F3']`);
  const before=plain(run('scheduleMap'));
  await generate(run,2027,11,{scope:'MONTH'});
  assert.deepEqual(plain(run('scheduleMap')),before);
});
test('the public metadata hook returns a detached copy',()=>{
  const run=setup();
  const available=run(`typeof getHolidaySupplementState === 'function'`);
  assert.equal(available,true);
  run('getHolidaySupplementState().events.length=0');
  assert.equal(run('holidaySupplements.events.length'),1);
});

test('zero-score and display-role changes do not masquerade as consumed male reservation changes',async()=>{
  const {run}=await generatedSupplement();
  run(`scheduleMap['2027-10-04'].find(w=>workerGender(w)==='M').zeroScore=true;globalThis.savedPayload=null`);
  await generate(run,2027,10,{scope:'MONTH'});
  assert.ok(run('savedPayload'),'normal regeneration may change scoring attributes without moving the reserved occurrence');
});

test('successful ordinary save adopts server reconciliation metadata before creating the rollback point',async()=>{
  const fs=require('node:fs'),path=require('node:path');
  const html=fs.readFileSync(path.resolve(__dirname,'../../frontend/dist/index.html'),'utf8');
  const start=html.indexOf('    async function syncFullDataToBackend(');
  const stop=html.indexOf('    // ── 달력',start);
  const run=setup();run(html.slice(start,stop));
  const metadata={schema:1,events:[event({state:'NEEDS_REVIEW'})],bases:[basis(2027,{state:'NEEDS_REVIEW'})],requiredContract:1};
  set(run,'globalThis.serverResponse',{dataVersion:10,holidaySupplements:metadata});
  run(`SERVER_CONNECTED=true;dataLoaded=true;CONN.server=true;CONN.shared=true;
    fetchWithAdminToken=async()=>({status:200,ok:true,json:async()=>serverResponse});globalThis.setTimeout=()=>0`);
  assert.equal(await run(`syncFullDataToBackend({comments:{'2027-11-10':'보존'}})`),true);
  assert.deepEqual(plain(run('holidaySupplements')),metadata);
  run('holidaySupplements.events=[];restoreCommittedState()');
  assert.deepEqual(plain(run('holidaySupplements')),metadata);
});

const canonical = require('../fixtures/holiday_supplement_cases.json');
function loadCanonicalState(state) {
  const run=app();
  for (const [key,source] of Object.entries({memberList:'members',scheduleMap:'schedules',noDutyMap:'noDuty',absences:'absences',customHolidays:'customHolidays',dutyRule:'rules',closedMonths:'closedMonths',holidaySupplements:'holidaySupplements'}))
    set(run,key,state[source]);
  run('mergeHolidays()');return run;
}
for (const fixture of canonical.cases) {
  for (const variant of ['after_state','female_removed_state']) {
    test(`shared Python/JS fixture restore: ${fixture.id}, ${variant}`,()=>{
      const run=loadCanonicalState(fixture[variant]);
      for (const check of fixture.restore_checks)
        assert.equal(run(`restoreRotationIndexes('${check.before_key}')['${check.pool_key}']`),check.cursor,check.before_key+' '+check.pool_key);
    });
  }
  test(`shared Python/JS fixture regenerates actual year without moving existing reservations: ${fixture.id}`,async()=>{
    const run=loadCanonicalState(fixture.after_state);
    const before=plain(run('scheduleMap'));run('globalThis.savedPayload=null');
    const after=await generate(run,2027,6,{scope:'YEAR'});
    assert.ok(run('savedPayload'),'valid canonical state must allow normal generation');
    assert.deepEqual(after,before);
  });
}
test('every supported base calendar date has identical JS and Python classification',()=>{
  const run=app();
  set(run,'globalThis.classificationInputs',canonical.classification.map(row=>row.date_key));
  const actual=plain(run(`classificationInputs.map(date_key=>({date_key,category:slotCategory(date_key,'M'),major:majorDutyBlockStart(date_key),main:isMajorMainDay(date_key)}))`));
  assert.deepEqual(actual,canonical.classification);
});

test('a changed basis without retained credits opens the supported basis recheck flow',async()=>{
  const run=setup([],[basis(2027,{state:'NEEDS_REVIEW',review_code:'BASIS_REVIEW_REQUIRED'})]);
  run(`openScopeModal=async()=>({scope:'MONTH'});window.HolidaySupplement={openReconcile:input=>{globalThis.reviewInput=input}};globalThis.reviewInput=null`);
  await generate(run,2027,10,{scope:'MONTH',silent:false});
  assert.equal(run('reviewInput && reviewInput.pool_key'),'holiday|M');
  assert.equal(run('reviewInput && reviewInput.expected_basis_revision'),1);
});

test('switching a consumed dedicated holiday queue to shared weekend queue blocks the new affected queue',async()=>{
  const {run}=await generatedSupplement();run('dutyRule.fixedOrders.holidayM=[]');
  const before=plain(run('scheduleMap'));run('globalThis.savedPayload=null');
  await generate(run,2027,11,{scope:'MONTH'});
  assert.deepEqual(plain(run('scheduleMap')),before);
  assert.equal(run('savedPayload'),null);
});
test('switching a consumed shared queue to a dedicated queue blocks dedicated holiday generation',async()=>{
  const fixture=canonical.cases.find(value=>value.id==='annual_3_shared');
  const run=loadCanonicalState(fixture.after_state);run(`dutyRule.fixedOrders.holidayM=['M1','M2','M3']`);
  const before=plain(run('scheduleMap'));run('globalThis.savedPayload=null');
  await generate(run,2027,10,{scope:'MONTH'});
  assert.deepEqual(plain(run('scheduleMap')),before);
  assert.equal(run('savedPayload'),null);
});
test('a new year cannot reinterpret a consumed prior-year cursor after queue order changed',async()=>{
  const run=setup();run(`dutyRule.fixedOrders.holidayM=['M2','M1','M3']`);
  const before=plain(run('scheduleMap'));run('globalThis.savedPayload=null');
  await generate(run,2028,3,{scope:'MONTH'});
  assert.deepEqual(plain(run('scheduleMap')),before);
  assert.equal(run('savedPayload'),null);
});

test('a retained cancelled prior-year credit still carries a stale basis generation block',async()=>{
  const run=setup([event({state:'CANCELLED'})],[basis(2027,{state:'NEEDS_REVIEW',review_code:'BASIS_REBASE_REQUIRED'})]);
  run(`delete customHolidays['2027-11-10'];scheduleMap['2027-11-10']=[{name:'F2',role:'평일(여)'}];mergeHolidays()`);
  const before=plain(run('scheduleMap'));run('globalThis.savedPayload=null');
  await generate(run,2028,3,{scope:'MONTH'});
  assert.deepEqual(plain(run('scheduleMap')),before);
  assert.equal(run('savedPayload'),null);
});
test('a valid adopted next-year anchor permits that year after a prior-year order change',async()=>{
  const run=setup([event()],[basis(),basis(2028,{sequence_snapshot:['M2','M1','M3'],anchor_before_year:1})]);
  run(`dutyRule.fixedOrders.holidayM=['M2','M1','M3'];globalThis.savedPayload=null`);
  const after=await generate(run,2028,3,{scope:'MONTH'});
  assert.ok(run('savedPayload'));
  assert.equal(after['2028-03-01'].find(w=>w.name.startsWith('M'))?.name,'M1');
});

test('an unconfirmed annual-tail basis cannot bypass inherited order drift',async()=>{
  const run=setup([event()],[basis(),basis(2028,{policy:'ANNUAL_TAIL',sequence_snapshot:['M2','M1','M3'],anchor_before_year:1})]);
  run(`dutyRule.fixedOrders.holidayM=['M2','M1','M3'];globalThis.savedPayload=null`);
  const before=plain(run('scheduleMap'));
  await generate(run,2028,3,{scope:'MONTH'});
  assert.equal(run('savedPayload'),null);
  assert.deepEqual(plain(run('scheduleMap')),before);
});
