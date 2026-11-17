'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

// Exercise the actual recovered application's scripts and scheduling engine.
// Browser startup listeners are deliberately not invoked. Presentation/network
// methods are replaced only for engine tests; date and assignment code is real.
const htmlPath = process.env.ONAIR_FRONTEND || path.resolve(__dirname, '../../frontend/dist/index.html');
const html = fs.readFileSync(htmlPath, 'utf8');
const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(x => x[1]);
const main = scripts.find(s => s.includes('function friday21Of('));
assert.ok(main, 'application script must be present');
function app() {
  const storage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
  const context = vm.createContext({
    Date, console, sessionStorage: storage, localStorage: storage,
    document: { addEventListener: () => {} }, window: { addEventListener: () => {} },
    setTimeout, clearTimeout, setInterval, clearInterval,
  });
  vm.runInContext(main, context, { filename: htmlPath });
  return code => vm.runInContext(code, context);
}
function plain(x) { return JSON.parse(JSON.stringify(x)); }
async function generate({year=2025, month=9, holidays={}, noDuty={}, comments={}, schedules={}, enabled=true, fixed=false, scope='MONTH', preview=false, confirmed=true, offline=false}={}) {
  const run = app();
  run(`SYSTEM_MODE='ADMIN'; curYear=${year}; curMonth=${month};
    memberList=[{name:'M1',gender:'M'},{name:'M2',gender:'M'},{name:'F1',gender:'F'},{name:'F2',gender:'F'},{name:'F3',gender:'F'}];
    customHolidays=${JSON.stringify(holidays)}; noDutyMap=${JSON.stringify(noDuty)}; commentMap=${JSON.stringify(comments)}; scheduleMap=${JSON.stringify(schedules)};
    dutyRule.rule21FridayOff=${enabled}; dutyRule.algoType=${JSON.stringify(fixed ? 'FIXED_ORDER' : 'FAIR_WEIGHT')};
    dutyRule.fixedOrders={weekday:['F1','F2','F3'],weekendM:['M1','M2'],weekendF:['F1','F2'],holidayM:[],holidayF:[],majorM:['M1','M2'],majorF:['F1','F2']};
    renderCalendar=()=>{}; showToast=(message,error)=>{globalThis.lastToast={message,error};};
    if (!${offline}) syncFullDataToBackend=async payload=>{ globalThis.savedPayload=payload; return true; };
    rememberCommittedState();
    openScopeModal=async()=>${JSON.stringify(scope)};
    openAssignPreview=async(before,scope)=>{globalThis.previewDiff=diffAssignments(before,scope); return ${confirmed};};
  `);
  await run(`generateScheduleByRules({silent:${!preview},scope:${JSON.stringify(scope)}})`);
  return { run, noDuty: plain(run('noDutyMap')), comments: plain(run('commentMap')), schedules: plain(run('scheduleMap')), saved: plain(run('typeof savedPayload === "undefined" ? null : savedPayload')) };
}

test('all recovered inline JavaScript parses', () => {
  assert.equal(scripts.filter(script => script.trim()).length, 3);
  scripts.forEach((s,i) => new vm.Script(s, {filename:`inline-${i}.js`}));
});
test('Sunday 21st anchors to previous Friday 19th, never Friday 26th', () => {
  const run = app();
  assert.equal(run(`friday21Of(2025,9).getDate()`), 19);
});
// v3.16.3 기준 정정: 21일이 들어 있는 주(월~일)의 금요일. 그 주 월요일 = 금요일-4, 일요일 = 금요일+2.
test('all 84 supported months choose the Friday of the Monday-Sunday week containing the 21st', () => {
  const run = app();
  const weekdays = new Set();
  for (let y=2023;y<=2029;y++) for(let m=1;m<=12;m++) {
    const candidateDays=[];
    for(let d=1;d<=28;d++) if(new Date(y,m-1,d).getDay()===5 && d-4<=21 && 21<=d+2) candidateDays.push(d);
    assert.equal(candidateDays.length, 1, `${y}-${m} 기준 금요일은 하나`);
    const result=run(`friday21Of(${y},${m})`);
    assert.equal(result.getDate(), candidateDays[0], `${y}-${m}`);
    assert.equal(result.getMonth(), m-1);
    assert.equal(result.getDay(),5);
    weekdays.add(new Date(y,m-1,21).getDay());
  }
  assert.equal(weekdays.size,7);
});
test('Sunday through Saturday week boundaries hold across month and year', () => {
  const run = app();
  assert.deepEqual(plain(run(`weekKeysOf('2025-09-21')`)), ['2025-09-21','2025-09-22','2025-09-23','2025-09-24','2025-09-25','2025-09-26','2025-09-27']);
  assert.deepEqual(plain(run(`weekKeysOf('2026-01-01')`)), ['2025-12-28','2025-12-29','2025-12-30','2025-12-31','2026-01-01','2026-01-02','2026-01-03']);
  assert.equal(run(`weekKeysOf('2026-02-28')[6]`),'2026-02-28');
});
test('regeneration moves stale RULE21 to 19th and restores old 26th duty', async () => {
  for(const fixed of [false,true]) {
    const result=await generate({fixed,noDuty:{'2025-09-26':'RULE21','2025-09-03':true,'2025-08-22':'RULE21'}});
    assert.equal(result.noDuty['2025-09-19'],'RULE21');
    assert.equal(result.noDuty['2025-09-26'],undefined);
    assert.equal(result.noDuty['2025-09-03'],true);
    assert.equal(result.noDuty['2025-08-22'],'RULE21');
    assert.deepEqual(result.schedules['2025-09-19'],[]);
    assert.ok(result.schedules['2025-09-26'].length>0);
    assert.deepEqual(result.saved.noDuty,result.noDuty);
  }
});
test('holiday and substitute holiday each move family day seven days earlier', async () => {
  for(const name of ['공휴일','대체공휴일']) {
    const result=await generate({holidays:{'2025-09-19':{name}}});
    assert.equal(result.noDuty['2025-09-12'],'RULE21');
    assert.equal(result.noDuty['2025-09-19'],undefined);
    assert.ok(result.schedules['2025-09-19'].length>0,'holiday duty remains assigned');
    assert.deepEqual(result.schedules['2025-09-12'],[]);
  }
});
test('consecutive Friday holidays move 19 → 12 → 5', async () => {
  const result=await generate({holidays:{'2025-09-19':{name:'공휴일'},'2025-09-12':{name:'대체공휴일'}}});
  assert.equal(result.noDuty['2025-09-05'],'RULE21');
  assert.deepEqual(result.schedules['2025-09-05'],[]);
  assert.ok(result.schedules['2025-09-12'].length>0);
  assert.ok(result.schedules['2025-09-19'].length>0);
});
test('base holiday merges, removal restores the original Friday, manual days survive', async () => {
  // 2026-12: 21일이 월요일이라 기준 금요일은 12/25(성탄절, 기본 공휴일) → 한 주 앞 12/18.
  const normal=await generate({year:2026,month:12});
  assert.equal(normal.noDuty['2026-12-18'],'RULE21');
  const removed=await generate({year:2026,month:12,holidays:{'2026-12-25':{name:'성탄절',isRemoved:true}},noDuty:{'2026-12-18':'RULE21','2026-12-02':true}});
  assert.equal(removed.noDuty['2026-12-25'],'RULE21');
  assert.equal(removed.noDuty['2026-12-18'],undefined);
  assert.equal(removed.noDuty['2026-12-02'],true);
});
test('turning rule off clears automatic family days and preserves manual and other months', async () => {
  const result=await generate({enabled:false,noDuty:{'2025-09-19':'RULE21','2025-09-26':'RULE21','2025-09-12':true,'2025-08-08':'RULE21'}});
  assert.equal(result.noDuty['2025-09-19'],undefined);
  assert.equal(result.noDuty['2025-09-26'],undefined);
  assert.equal(result.noDuty['2025-09-12'],true);
  assert.equal(result.noDuty['2025-08-08'],'RULE21');
});
test('a manual no-duty flag on the target Friday remains manual', async () => {
  const result=await generate({noDuty:{'2025-09-19':true,'2025-09-26':'RULE21'}});
  assert.equal(result.noDuty['2025-09-19'],true);
  assert.equal(result.noDuty['2025-09-26'],undefined);
});
test('year generation uses the same family day rule for each month', async () => {
  const result=await generate({scope:'YEAR'});
  const holidays=plain(result.run('(mergeHolidays(),finalHolidays)'));
  for(let m=1;m<=12;m++) {
    let expected=null;
    let base=0;
    for(let d=1;d<=28;d++) if(new Date(2025,m-1,d).getDay()===5 && d-4<=21 && 21<=d+2) base=d;
    for(let d=base;d>=1;d-=7) {
      const k=`2025-${String(m).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
      if(!holidays[k]) {expected=k;break;}
    }
    const actual=Object.keys(result.noDuty).filter(k=>k.startsWith(`2025-${String(m).padStart(2,'0')}-`)&&result.noDuty[k]==='RULE21');
    assert.deepEqual(actual,expected?[expected]:[]);
  }
});
test('all eligible Fridays being holidays aborts before changing or saving any records', async () => {
  const result=await generate({holidays:{'2025-09-19':{name:'공휴일'},'2025-09-12':{name:'대체공휴일'},'2025-09-05':{name:'임시공휴일'}},noDuty:{'2025-08-29':true,'2025-09-26':'RULE21'}});
  assert.equal(result.noDuty['2025-08-29'],true);
  assert.equal(result.noDuty['2025-09-26'],'RULE21');
  assert.deepEqual(result.schedules,{});
  assert.equal(result.saved,null);
  assert.match(result.run('lastToast.message'),/자동 배정을 중단/);
  assert.equal(result.run('lastToast.error'),true);
  assert.equal(result.run('suspendAutoRefresh'),false);
});

// New 3.15.2 contract: an existing no-duty flag wins before holiday fallback.
test('existing family day is kept even if also registered as a holiday', async () => {
  for (const fixed of [false,true]) for (const flag of [true,'RULE21']) {
    const result=await generate({fixed,holidays:{'2025-09-19':{name:'공휴일'}},noDuty:{'2025-09-19':flag}});
    assert.equal(result.noDuty['2025-09-19'],flag);
    assert.equal(result.noDuty['2025-09-12'],undefined);
    assert.deepEqual(result.schedules['2025-09-19'],[]);
    assert.equal(result.comments['2025-09-19'],'패밀리데이');
    assert.deepEqual(result.saved.comments,result.comments);
  }
});
test('an already applied earlier Friday prevents another shift', async () => {
  const result=await generate({holidays:{'2025-09-19':{name:'공휴일'},'2025-09-12':{name:'대체공휴일'}},noDuty:{'2025-09-12':true}});
  assert.equal(result.noDuty['2025-09-12'],true);
  assert.equal(result.noDuty['2025-09-05'],undefined);
  assert.equal(result.comments['2025-09-12'],'패밀리데이');
});
test('new family day memo preserves other notes and is idempotent', async () => {
  const comments={'2025-09-19':'인수인계\n장비 점검','2025-09-03':'수동 메모','2026-12-18':'미래 기록'};
  const first=await generate({comments});
  assert.equal(first.comments['2025-09-19'],'인수인계\n장비 점검\n패밀리데이');
  assert.equal(first.comments['2025-09-03'],comments['2025-09-03']);
  assert.equal(first.comments['2026-12-18'],comments['2026-12-18']);
  const second=await generate({comments:first.comments,noDuty:first.noDuty,schedules:first.schedules});
  assert.deepEqual(second.comments,first.comments);
  assert.deepEqual(second.noDuty,first.noDuty);
  assert.deepEqual(second.saved.comments,first.comments);
});
test('holiday fallback memo is on the selected date only', async () => {
  const result=await generate({holidays:{'2025-09-19':{name:'대체공휴일'}},comments:{'2025-09-19':'공휴일 행사'}});
  assert.equal(result.comments['2025-09-12'],'패밀리데이');
  assert.equal(result.comments['2025-09-19'],'공휴일 행사');
});
test('preview exposes memo-only changes and cancel restores all original maps', async () => {
  const base=await generate();
  const comments={'2025-09-19':'기존 메모'};
  const result=await generate({comments,noDuty:base.noDuty,schedules:base.schedules,preview:true,confirmed:false});
  const row=plain(result.run('previewDiff.rows')).find(r=>r.k==='2025-09-19');
  assert.ok(row,'memo-only change must be reviewable');
  assert.match(row.after,/패밀리데이/);
  assert.deepEqual(result.comments,comments);
  assert.deepEqual(result.noDuty,base.noDuty);
  assert.deepEqual(result.schedules,base.schedules);
  assert.equal(result.saved,null);
  assert.equal(result.run('suspendAutoRefresh'),false);
});
test('offline save restores memo, no-duty and schedules together', async () => {
  const comments={'2025-09-19':'기존 기록'};
  const schedules={'2025-09-03':[{name:'F1',role:'수동'}]};
  const result=await generate({comments,schedules,offline:true});
  assert.deepEqual(result.comments,comments);
  assert.deepEqual(result.noDuty,{});
  assert.deepEqual(result.schedules,schedules);
  assert.equal(result.run('lastToast.error'),true);
});
test('next-year existing family days remain single through December', async () => {
  // v3.16.3 사용자 확인 2027년 패밀리데이(21일이 든 월~일 주의 금요일).
  const targets=['2027-01-22','2027-02-19','2027-03-19','2027-04-23','2027-05-21','2027-06-25','2027-07-23','2027-08-20','2027-09-24','2027-10-22','2027-11-19','2027-12-24'];
  const noDuty=Object.fromEntries(targets.map(k=>[k,true]));
  const holidays=Object.fromEntries(targets.map(k=>[k,{name:'기존 휴무 등록'}]));
  const comments={'2026-12-18':'이전 해 기록'};
  const result=await generate({year:2027,scope:'YEAR',noDuty,holidays,comments});
  for (const k of targets) assert.equal(result.noDuty[k],true);
  assert.deepEqual(Object.keys(result.noDuty).filter(k=>result.noDuty[k]==='RULE21'),[]);
  for (const k of targets) assert.equal(result.comments[k],'패밀리데이');
  assert.equal(result.comments['2026-12-18'],'이전 해 기록');
});
test('disabled rule never appends family-day memos', async () => {
  const comments={'2025-09-19':'보존'};
  const result=await generate({enabled:false,noDuty:{'2025-09-19':true},comments});
  assert.deepEqual(result.comments,comments);
});

// [위험] 3.14.6~3.16.2 는 '21일을 넘지 않는 마지막 금요일' 로 계산해, 21일이 월~목인 달은 한 주 이른 금요일을
//   패밀리데이로 잡았다(2027년 12달 중 7달). 사용자가 확인한 2027년 날짜를 그대로 못박는다.
test('2027 family days match the dates confirmed by the user', async () => {
  const confirmed=['2027-01-22','2027-02-19','2027-03-19','2027-04-23','2027-05-21','2027-06-25','2027-07-23','2027-08-20','2027-09-24','2027-10-22','2027-11-19','2027-12-24'];
  const result=await generate({year:2027,scope:'YEAR',fixed:true});
  const actual=Object.keys(result.noDuty).filter(k=>k.startsWith('2027-')&&result.noDuty[k]==='RULE21').sort();
  assert.deepEqual(actual,confirmed);
  for (const k of confirmed) assert.ok(String(result.comments[k]||'').includes('패밀리데이'), k);
});

test('a wrongly placed 2027-04-16 family day is moved to 04-23 by family reset', async () => {
  const run=app();
  run(`SYSTEM_MODE='ADMIN'; curYear=2027; curMonth=4;
    memberList=[{name:'M1',gender:'M'},{name:'M2',gender:'M'},{name:'F1',gender:'F'},{name:'F2',gender:'F'},{name:'F3',gender:'F'}];
    customHolidays={}; noDutyMap={'2027-04-16':true}; commentMap={'2027-04-16':'패밀리데이'}; scheduleMap={};
    dutyRule.rule21FridayOff=true; dutyRule.algoType='FIXED_ORDER';
    dutyRule.fixedOrders={weekday:['F1','F2','F3'],weekendM:['M1','M2'],weekendF:['F1','F2'],holidayM:[],holidayF:[],majorM:['M1','M2'],majorF:['F1','F2']};
    renderCalendar=()=>{}; showToast=()=>{}; syncFullDataToBackend=async()=>true; rememberCommittedState();`);
  await run(`generateScheduleByRules({silent:true,scope:'MONTH',familyReset:true})`);
  const noDuty=plain(run('noDutyMap'));
  assert.deepEqual(Object.keys(noDuty).filter(k=>k.startsWith('2027-04')),['2027-04-23']);
  assert.ok(plain(run('scheduleMap'))['2027-04-16'].length>0,'4/16 은 근무일로 돌아와 배정된다');
});
