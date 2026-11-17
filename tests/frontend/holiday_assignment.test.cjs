'use strict';
const assert=require('node:assert/strict');
const {test}=require('node:test');
const {app,plain,generate}=require('./helpers/scheduler.cjs');

// User-reported dates: these are protection cases, not a claim that pristine
// v3.15.5 reproduced the user's saved-data/version-specific misclassification.
for (const [month,key] of [[5,'2027-05-03'],[12,'2027-12-27']]) {
  for (const mode of ['FIXED_ORDER','FAIR_WEIGHT']) for(const shared of [false,true]) {
    test(`${key}: ${mode}, shared=${shared}, holiday needs M1+F1 and leaves weekday rotation untouched`,async()=>{
      const run=app();
      run(`dutyRule.algoType='${mode}';`);
      if(shared) run('dutyRule.fixedOrders.holidayM=[];dutyRule.fixedOrders.holidayF=[];');
      const schedules=await generate(run,2027,month);
      assert.equal(run(`isOffDayKey('${key}')`),true);
      assert.equal(run(`dutyDayKind('${key}')`),'holiday');
      assert.equal(schedules[key].length,2);
      assert.deepEqual(schedules[key].map(w=>run(`genderOf('${w.name}')`)),['M','F']);
      assert.ok(schedules[key].every(w=>w.role.startsWith('공휴일')));
      if(mode==='FIXED_ORDER') {
        let next=0;
        for(const [k,workers] of Object.entries(schedules)) if(run(`slotCategory('${k}')`)==='weekday') {
          assert.equal(workers[0].name,['F1','F2','F3'][next++%3],k);
        }
      }
      assert.deepEqual(plain(run('savedPayload.schedules')),schedules);
    });
  }
}

test('Memorial Day Sunday does not create a substitute holiday on 2027-06-07',()=>{
  const run=app();
  assert.equal(run(`isOffDayKey('2027-06-07')`),false);
  assert.equal(run(`slotCategory('2027-06-07')`),'weekday');
});

for(const gender of ['M','F']) test(`one empty holiday roster shares ${gender} weekend pointer as well as names`,async()=>{
  const run=app();
  run(`dutyRule.fixedOrders.holiday${gender}=[];`);
  const schedules=await generate(run,2027,5);
  const assigned=k=>schedules[k].find(w=>run(`genderOf('${w.name}')`)==gender).name;
  assert.equal(assigned('2027-05-01'),`${gender}1`);
  assert.equal(assigned('2027-05-02'),`${gender}2`);
  assert.equal(assigned('2027-05-03'),`${gender}3`,'Monday holiday continues shared weekend queue');
  assert.equal(assigned('2027-05-05'),`${gender}1`);
  assert.equal(assigned('2027-05-08'),`${gender}2`,'next weekend sees holiday consumption');
  assert.equal(run(`rotationRowActive(rotationOrders(),'holiday|${gender}')`),false,'no phantom separate start row');
});

for(const gender of ['M','F']) test(`partial holiday fallback ${gender} restores the same queue across month re-generation`,async()=>{
  const run=app(); run(`dutyRule.fixedOrders.holiday${gender}=[];`);
  const year=await generate(run,2027,1,{scope:'YEAR'});
  const before=plain(year);
  await generate(run,2027,6);
  const after=plain(run('scheduleMap'));
  assert.deepEqual(after,before,'regenerating June must preserve already generated yearly schedule');
});

test('preview exposes substitute, zero-score and role changes even with identical names',()=>{
  for(const changed of [{isSubstitute:true},{zeroScore:true},{role:'수동당직'}]) {
    const run=app(); run(`curYear=2027;curMonth=5;scheduleMap={'2027-05-04':[{name:'F1',role:'평일(여)'}]};`);
    const before={schedules:{'2027-05-04':[{name:'F1',role:'평일(여)',...changed}]},noDuty:{},comments:{}};
    const row=plain(run(`diffAssignments(${JSON.stringify(before)},'MONTH')`)).rows.find(r=>r.k==='2027-05-04');
    assert.ok(row,JSON.stringify(changed));
    assert.equal(row.kind,'변경');
    assert.notEqual(row.before,row.after,'reviewer can see exactly what metadata changes');
  }
});

test('vacancy application rejects stale/full slots, unknown or wrong-gender names, no-duty and major main days',async()=>{
  const cases=[
    {key:'2027-05-03',name:'M1',gender:'M',schedules:{'2027-05-03':[{name:'M1',role:'공휴일(남)'}]}},
    {key:'2027-05-03',name:'M2',gender:'M',schedules:{'2027-05-03':[{name:'M1',role:'공휴일(남)'}]}},
    {key:'2027-05-03',name:'Unknown',gender:'M'},
    {key:'2027-05-03',name:'F1',gender:'M'},
    {key:'2027-05-04',name:'M1',gender:'M'},
    {key:'2027-05-03',name:'M1',gender:'M',noDuty:{'2027-05-03':true}},
    {key:'2027-02-07',name:'M1',gender:'M'},
  ];
  for(const c of cases){
    const run=app();
    run(`scheduleMap=${JSON.stringify(c.schedules||{})};noDutyMap=${JSON.stringify(c.noDuty||{})};captureUndo=()=>{};discardUndo=()=>{};renderVacancy=()=>{};confirm=()=>true;`);
    const before=plain(run('scheduleMap'));
    await run(`fillVacancy('${c.key}','${c.name}','${c.gender}')`);
    assert.deepEqual(plain(run('scheduleMap')),before,JSON.stringify(c));
    assert.equal(run('typeof savedPayload'),'undefined','invalid operation must not save');
  }
});

test('vacancy candidate list is empty for no-duty and festival main days',()=>{
  const run=app(); run(`noDutyMap['2027-05-03']=true;`);
  for(const key of ['2027-05-03','2027-02-07']) assert.deepEqual(plain(run(`vacancyCandidates('${key}','M')`)),[]);
});

test('known statutory substitute holidays cannot become festival duty merely by proximity',()=>{
  const run=app();
  run(`customHolidays={'2027-05-02':{name:'임의 명절',isMajor:true},'2027-12-26':{name:'임의 명절',isMajor:true}};mergeHolidays();`);
  for(const key of ['2027-05-03','2027-12-27']){
    assert.equal(run(`linkedMajorBlockStart('${key}')`),null);
    assert.equal(run(`dutyDayKind('${key}')`),'holiday');
  }
});

test('calendar-source diagnostics distinguish explicit removals and stale weekday assignments without editing them',()=>{
  const run=app();
  run(`curYear=2027;curMonth=5;customHolidays={'2027-05-03':{name:'대체공휴일',isRemoved:true}};scheduleMap={'2027-05-03':[{name:'F1',role:'평일(여)'}]};mergeHolidays();`);
  const before=plain(run('scheduleMap'));
  const removed=plain(run('inspectMonth(2027,5)'));
  assert.ok((removed.classificationIssues||[]).some(x=>x.k==='2027-05-03'&&x.code==='BASE_HOLIDAY_REMOVED'));
  run(`customHolidays={};mergeHolidays();`);
  const stale=plain(run('inspectMonth(2027,5)'));
  assert.ok((stale.classificationIssues||[]).some(x=>x.k==='2027-05-03'&&x.code==='STALE_ROLE'));
  assert.ok(stale.wrongSlots.some(x=>x.k==='2027-05-03'));
  assert.deepEqual(plain(run('scheduleMap')),before,'diagnosis never rewrites a saved duty record');
});

test('absence conflicts are reported even when gender headcount is complete',()=>{
  const run=app();
  run(`absences=[{name:'M1',type:'연차',start:'2027-05-03',end:'2027-05-03'}];scheduleMap={'2027-05-03':[{name:'M1',role:'공휴일(남)'},{name:'F1',role:'공휴일(여)'}]};`);
  const checked=plain(run('inspectMonth(2027,5)'));
  assert.ok((checked.assignmentIssues||[]).some(x=>x.k==='2027-05-03'&&x.code==='ABSENT_WORKER'));
});

test('vacancy suggestion shares the per-gender queue restored from weekend plus holiday history',()=>{
  const run=app();
  run(`dutyRule.fixedOrders.holidayM=[];scheduleMap={'2027-05-01':[{name:'M1',role:'공휴일(남)'}],'2027-05-02':[{name:'M2',role:'주말(남)'}],'2027-05-03':[{name:'M3',role:'공휴일(남)'}]};`);
  assert.equal(run(`vacancyCandidates('2027-05-05','M')[0].name`),'M1');
});

test('new members appended at roster tail do not reorder existing fixed weekday workers',async()=>{
  const run=app();
  run(`memberList.push({id:7,name:'F4',gender:'F'});dutyRule.fixedOrders.weekday.push('F4');`);
  const schedules=await generate(run,2027,5);
  const weekdays=Object.keys(schedules).filter(k=>run(`slotCategory('${k}')`)==='weekday');
  assert.deepEqual(weekdays.slice(0,8).map(k=>schedules[k][0].name),['F1','F2','F3','F4','F1','F2','F3','F4']);
  assert.deepEqual(plain(run('dutyRule.fixedOrders.weekday')),['F1','F2','F3','F4']);
});

for(const category of ['holiday','major','weekday']) test(`tail-appended worker waits for normal next turn in ${category} queue, independent of yearly counts`,()=>{
  const run=app();
  const dates=category==='holiday'?['2027-01-01','2027-03-01','2027-05-03','2027-05-05']:
    category==='major'?['2024-02-09','2024-09-16','2025-01-28','2025-10-05']:
    ['2027-05-04','2027-05-06','2027-05-07','2027-05-10'];
  const gender=category==='weekday'?'F':'M';
  const key=category+'|'+gender;
  const roster=category==='weekday'?'weekday':category+gender;
  const historical=Object.fromEntries(dates.map((k,i)=>[k,[{name:gender+[1,2,3,1][i],role:(category==='weekday'?'평일':category==='major'?'명절':'공휴일')+'('+ (gender==='M'?'남':'여')+')'}]]));
  run(`dutyRule.fixedOrders.${roster}=['${gender}1','${gender}2','${gender}3'];scheduleMap=${JSON.stringify(historical)};memberList.push({id:7,name:'${gender}4',gender:'${gender}'});dutyRule.fixedOrders.${roster}.push('${gender}4');`);
  const before=category==='major'?'2026-02-16':'2027-05-13';
  assert.equal(run(`restoreRotationIndexes('${before}')['${key}']`),1,'last regular worker was #1, so #2 must remain next');
  assert.deepEqual(plain(run(`dutyRule.fixedOrders.${roster}`)),[1,2,3,4].map(n=>gender+n));
});

test('an out-of-turn substitute preserves the existing regular fixed holiday pointer',()=>{
  const run=app();
  run(`dutyRule.fixedOrders.holidayM=['M1','M2','M3'];scheduleMap={'2027-01-01':[{name:'M1',role:'공휴일(남)'}],'2027-03-01':[{name:'M3',role:'대체당직',isSubstitute:true,zeroScore:true}]};`);
  assert.equal(run(`restoreRotationIndexes('2027-05-03')['holiday|M']`),1);
});

test('fixed rotations are stable for month regeneration across supported years, odd/even rosters and independent holiday sharing',async()=>{
  let cases=0;
  for(const year of [2023,2024,2025,2026,2027,2028,2029]) for(const size of [3,4]) for(const shared of ['none','M','F','both']){
    const run=app();
    if(size===4)run(`memberList.push({id:7,name:'M4',gender:'M'},{id:8,name:'F4',gender:'F'});for(const key of ['weekday','weekendF','holidayF','majorF'])dutyRule.fixedOrders[key].push('F4');for(const key of ['weekendM','holidayM','majorM'])dutyRule.fixedOrders[key].push('M4');`);
    if(shared==='M'||shared==='both')run('dutyRule.fixedOrders.holidayM=[];');
    if(shared==='F'||shared==='both')run('dutyRule.fixedOrders.holidayF=[];');
    const initial=await generate(run,year,1,{scope:'YEAR'});
    for(let month=1;month<=12;month++){
      await generate(run,year,month);
      assert.deepEqual(plain(run('scheduleMap')),initial,`${year}, ${size} workers/gender, holiday sharing ${shared}, month ${month}`);
      cases++;
    }
  }
  assert.equal(cases,672);
});

test('fixed rotation regeneration skips absences without changing roster order',async()=>{
  const run=app();
  run(`dutyRule.fixedOrders.holidayM=[];absences=[{name:'F1',type:'연차',start:'2027-05-03',end:'2027-05-10'},{name:'M1',type:'장기부재',start:'2027-04-20',end:'2027-06-09'}];`);
  const order=plain(run('dutyRule.fixedOrders'));
  const yearly=await generate(run,2027,1,{scope:'YEAR'});
  await generate(run,2027,5);
  assert.deepEqual(plain(run('scheduleMap')),yearly);
  assert.deepEqual(plain(run('dutyRule.fixedOrders')),order);
  for(const [k,workers]of Object.entries(yearly))for(const worker of workers)assert.equal(run(`isAbsent('${worker.name}','${k}')`),false,k);
});

test('overlapping holidays create one substitute and Chuseok Saturday alone creates none',()=>{
  const run=app();
  assert.equal(run(`isOffDayKey('2025-05-06')`),true);
  assert.equal(run(`isOffDayKey('2025-05-07')`),false);
  assert.equal(run(`isOffDayKey('2026-09-28')`),false);
  assert.equal(run(`isOffDayKey('2028-10-05')`),true);
  assert.equal(run(`isOffDayKey('2028-10-06')`),false);
  assert.equal(run(`majorDutyBlockStart('2023-10-02')`),null);
  assert.equal(run(`majorDutyBlockStart('2025-01-27')`),null);
});

test('renaming a built-in substitute holiday preserves its statutory festival linkage',()=>{
  const run=app();
  run(`customHolidays={'2027-02-09':{name:'설 대체 휴일',isMajor:false},'2027-05-03':{name:'노동절 대체',isMajor:false},'2027-05-02':{name:'사용자 명절',isMajor:true}};mergeHolidays();`);
  assert.equal(run(`majorDutyBlockStart('2027-02-09')`),'2027-02-06');
  assert.equal(run(`majorDutyBlockStart('2027-05-03')`),null);
});

test('auto assignment reports unfilled holiday slots instead of a complete-success toast',async()=>{
  const run=app();run(`memberList=memberList.filter(m=>m.gender==='F');`);
  const schedules=await generate(run,2027,5);
  assert.equal(schedules['2027-05-03'].length,1);
  assert.match(run('lastToast.message'),/미배정/);
  assert.equal(run('lastToast.error'),true);
});

test('cancelled assignment preview restores transient fallback diagnostics with the records',async()=>{
  const run=app();
  run(`curYear=2027;curMonth=5;fallbackDayKeys.add('2027-05-03');openScopeModal=async()=> 'MONTH';openRotationStartModal=async()=>({});openAssignPreview=async()=>false;`);
  await run(`generateScheduleByRules()`);
  assert.deepEqual(plain(run('[...fallbackDayKeys]')),['2027-05-03']);
  assert.deepEqual(plain(run('scheduleMap')),{});
  assert.equal(run('typeof savedPayload'),'undefined');
});

test('existing single-person weekend fallback is reported as an exception, not complete success',async()=>{
  const run=app();run(`memberList=memberList.filter(m=>m.name==='M1'||m.name==='F1');dutyRule.fixedOrders={weekday:['F1'],weekendM:['M1'],weekendF:['F1'],holidayM:[],holidayF:[],majorM:['M1'],majorF:['F1']};`);
  await generate(run,2027,5);
  const result=plain(run('inspectMonth(2027,5)'));
  assert.ok(result.assignmentIssues.some(x=>x.code==='WEEKEND_REPEAT'));
  assert.equal(run(`assignmentShortages('MONTH').length`),0,'all slots filled, but a rule exception remains');
  assert.equal(run('lastToast.error'),true);
  assert.match(run('lastToast.message'),/예외|확인/);
});

test('existing split-festival fallback is reported without silently changing its policy',async()=>{
  const run=app();
  run(`absences=[{name:'M1',type:'연차',start:'2027-02-08',end:'2027-02-08'},{name:'M2',type:'파견',start:'2027-02-06',end:'2027-02-06'},{name:'M3',type:'장기부재',start:'2027-02-01',end:'2027-02-28'}];`);
  const schedules=await generate(run,2027,2);
  assert.equal(schedules['2027-02-06'].find(w=>w.name.startsWith('M')).name,'M1');
  assert.equal(schedules['2027-02-08'].find(w=>w.name.startsWith('M')).name,'M2');
  assert.ok(plain(run('inspectMonth(2027,2)')).assignmentIssues.some(x=>x.code==='MAJOR_TEAM_SPLIT'));
  assert.equal(run('lastToast.error'),true);
});

test('month boundary does not anchor an out-of-turn weekend substitute as a regular turn',()=>{
  const run=app();
  run(`scheduleMap={'2027-04-03':[{name:'M1',role:'주말(남)'}],'2027-04-04':[{name:'M3',role:'대체당직',isSubstitute:true}]};`);
  assert.equal(run(`restoreRotationIndexes('2027-05-01')['weekend|M']`),1);
});

test('tail append plus later out-of-turn substitute preserves the next regular holiday worker',()=>{
  const run=app();
  run(`memberList.push({id:7,name:'M4',gender:'M'});dutyRule.fixedOrders.holidayM=['M1','M2','M3','M4'];scheduleMap={'2027-01-01':[{name:'M1',role:'공휴일(남)'}],'2027-03-01':[{name:'M2',role:'공휴일(남)'}],'2027-05-03':[{name:'M3',role:'공휴일(남)'}],'2027-05-05':[{name:'M1',role:'공휴일(남)'}],'2027-05-13':[{name:'M3',role:'대체당직',isSubstitute:true}]};`);
  assert.equal(run(`restoreRotationIndexes('2027-07-19')['holiday|M']`),1);
});

test('an in-turn substitute keeps the existing pointer-consumption behavior after tail append',()=>{
  const run=app();
  run(`memberList.push({id:7,name:'M4',gender:'M'});dutyRule.fixedOrders.holidayM=['M1','M2','M3','M4'];scheduleMap={'2027-01-01':[{name:'M1',role:'공휴일(남)'}],'2027-03-01':[{name:'M2',role:'공휴일(남)'}],'2027-05-03':[{name:'M3',role:'공휴일(남)'}],'2027-05-05':[{name:'M1',role:'공휴일(남)'}],'2027-05-13':[{name:'M2',role:'대체당직',isSubstitute:true}]};`);
  assert.equal(run(`restoreRotationIndexes('2027-07-19')['holiday|M']`),2,'M2 was the next available turn and is consumed');
});

test('removing the first festival day does not detach its built-in substitute from remaining festival days',()=>{
  const run=app();run(`customHolidays={'2027-02-06':{name:'설연휴',isRemoved:true}};mergeHolidays();`);
  assert.equal(run(`majorDutyBlockStart('2027-02-08')`),'2027-02-07');
  assert.equal(run(`majorDutyBlockStart('2027-02-09')`),'2027-02-07');
});

test('cross-month festival split is reported once inside the regenerated range',async()=>{
  const run=app();
  run(`customHolidays={'2027-04-30':{name:'설연휴',isMajor:true},'2027-05-01':{name:'설날',isMajor:true},'2027-05-02':{name:'설연휴',isMajor:true}};scheduleMap={'2027-04-30':[{name:'M1',role:'명절(남)'},{name:'F1',role:'명절(여)'}]};absences=[{name:'M1',type:'연차',start:'2027-05-02',end:'2027-05-02'}];`);
  await generate(run,2027,5);
  const review=plain(run(`assignmentReviewIssues('MONTH')`)).filter(x=>x.code==='MAJOR_TEAM_SPLIT');
  assert.equal(review.length,1);
  assert.equal(review[0].k,'2027-05-02');
  assert.ok(plain(run(`inspectMonth(2027,5)`)).assignmentIssues.some(x=>x.code==='MAJOR_TEAM_SPLIT'));
  assert.equal(run('lastToast.error'),true);
});
