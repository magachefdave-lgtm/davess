'use strict';
// v3.15.7 '처음부터 다시 짜기' 와 패밀리데이 메모 잔재 정리.
//
// [위험] 이 파일이 막는 실제 사고는 세 가지다.
//  1) 날짜 상세 모달에서 패밀리데이 휴무를 껐다 다시 켜면 값이 true 로 굳어
//     자동 배정이 두 번 다시 그 날을 정리하지 못했다. 그 날이 영구히 빈 채로
//     남아 '순번이 밀렸다' 로 보였다.
//  2) '패밀리데이' 메모는 붙이기만 하고 떼지 않아, 패밀리데이가 앞당겨진 뒤에도
//     옛 금요일이 달력에서 패밀리데이처럼 보였다.
//  3) '처음부터 다시 짜기' 가 관리자 수동 휴무(창립기념일 등)까지 지워 버리면
//     일부러 비워 둔 날이 사라진다. 지우는 범위를 못박는다.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const htmlPath = process.env.ONAIR_FRONTEND || path.resolve(__dirname, '../../frontend/dist/index.html');
const html = fs.readFileSync(htmlPath, 'utf8');
const main = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(x => x[1]).find(s => s.includes('function generateScheduleByRules('));
if (!main) { console.error('generateScheduleByRules 를 찾지 못했습니다 — 함수 이름이 바뀌었는지 확인하세요.'); process.exit(2); }

function plain(x) { return JSON.parse(JSON.stringify(x)); }

async function run_assign({noDuty = {}, comments = {}, fresh = false, year = 2026, month = 11} = {}) {
  const storage = {getItem: () => null, setItem: () => {}, removeItem: () => {}};
  const context = vm.createContext({
    Date, console, sessionStorage: storage, localStorage: storage,
    document: {addEventListener: () => {}}, window: {addEventListener: () => {}},
    setTimeout, clearTimeout, setInterval, clearInterval
  });
  vm.runInContext(main, context, {filename: htmlPath});
  const run = code => vm.runInContext(code, context);
  run(`
    SYSTEM_MODE='ADMIN'; curYear=${year}; curMonth=${month};
    memberList=[{id:1,name:'M1',gender:'M'},{id:2,name:'M2',gender:'M'},
                {id:3,name:'F1',gender:'F'},{id:4,name:'F2',gender:'F'},{id:5,name:'F3',gender:'F'}];
    customHolidays={}; noDutyMap=${JSON.stringify(noDuty)}; commentMap=${JSON.stringify(comments)}; scheduleMap={};
    dutyRule.rule21FridayOff=true; dutyRule.algoType='FIXED_ORDER';
    dutyRule.fixedOrders={weekday:['F1','F2','F3'],weekendM:['M1','M2'],weekendF:['F1','F2'],
                          holidayM:[],holidayF:[],majorM:['M1','M2'],majorF:['F1','F2']};
    renderCalendar=()=>{}; showToast=(message,error)=>{globalThis.lastToast={message,error};};
    syncFullDataToBackend=async payload=>{globalThis.savedPayload=cloneJson(payload); return true;};
    rememberCommittedState(); mergeHolidays();
  `);
  await run(`generateScheduleByRules(${JSON.stringify({silent: true, scope: 'MONTH', fresh})})`);
  return {run, noDuty: plain(run('noDutyMap')), comments: plain(run('commentMap')),
          schedules: plain(run('scheduleMap'))};
}

// 2026-11 의 패밀리데이 대상일은 21일을 넘지 않는 마지막 금요일 = 11/20.
const TARGET = '2026-11-20';
const STALE = '2026-11-13';   // 이전에 패밀리데이였다가 옮겨간 금요일

test('family day target is the last Friday on or before the 21st', async () => {
  const {run} = await run_assign();
  assert.equal(run(`familyDayKeyOf(2026,11)`), TARGET);
});

// v3.16.1 사용자 결정: 사람이 다른 날로 정해 둔 패밀리데이는 그 달의 패밀리데이로 인정한다.
//   (v3.15.7 에서는 이것을 '잠긴 잔재' 로 보고 처음부터 다시 짜기에서 풀었으나, 일부러 옮긴 날까지
//    금요일로 되돌리는 문제가 있어 뒤집었다.) 두 모드 모두 그 날을 지키고 금요일에 하나를 더 만들지 않는다.
test('a family day placed by a person on another day is kept in both modes and not duplicated', async () => {
  const placed = {noDuty: {[STALE]: true}, comments: {[STALE]: '패밀리데이'}};
  for (const fresh of [false, true]) {
    const r = await run_assign({...placed, fresh});
    assert.equal(r.noDuty[STALE], true, `fresh=${fresh}: 사람이 정한 날은 남아야 한다`);
    assert.ok(!r.noDuty[TARGET], `fresh=${fresh}: 금요일에 패밀리데이를 하나 더 만들면 안 된다`);
    assert.ok((r.schedules[TARGET] || []).length, `fresh=${fresh}: 규칙상 금요일은 근무일로 배정되어야 한다`);
  }
});

test('fresh rebuild keeps a manual no-duty that is not a family-day leftover', async () => {
  const anniversary = '2026-11-11';
  const {noDuty, schedules} = await run_assign({
    noDuty: {[anniversary]: '창립기념일'}, comments: {[anniversary]: '전사 휴무'}, fresh: true
  });
  assert.equal(noDuty[anniversary], '창립기념일', '관리자가 일부러 비운 날은 남아야 한다');
  assert.deepEqual(schedules[anniversary], []);
});

test('fresh rebuild re-applies the family-day rule to the correct Friday', async () => {
  const {noDuty, comments} = await run_assign({
    noDuty: {[STALE]: 'RULE21'}, comments: {[STALE]: '패밀리데이'}, fresh: true
  });
  assert.ok(!noDuty[STALE], '옛 규칙 휴무는 사라진다');
  assert.equal(noDuty[TARGET], 'RULE21', '대상 금요일에 다시 지정된다');
  assert.ok(String(comments[TARGET] || '').includes('패밀리데이'));
});

test('stale family-day memo is stripped from working days in both modes', async () => {
  for (const fresh of [false, true]) {
    const {comments} = await run_assign({comments: {[STALE]: '패밀리데이'}, fresh});
    assert.ok(!(STALE in comments), `fresh=${fresh}: 근무일에 남은 패밀리데이 메모는 지워야 한다`);
  }
});

test('stripping the family-day memo never eats the rest of the memo', async () => {
  const memo = '설비 점검 14시\n패밀리데이\n김과장 보고';
  const {comments} = await run_assign({comments: {[STALE]: memo}});
  assert.equal(comments[STALE], '설비 점검 14시\n김과장 보고');
});

test('memo on the real family day is left alone', async () => {
  const {comments} = await run_assign({comments: {[TARGET]: '패밀리데이'}});
  assert.ok(String(comments[TARGET] || '').includes('패밀리데이'));
});

test('toggling a rule no-duty off and on keeps its rule tag', async () => {
  const {run} = await run_assign({noDuty: {[TARGET]: 'RULE21'}});
  run(`noDutyMap=${JSON.stringify({[TARGET]: 'RULE21'})};
       document={addEventListener:()=>{},getElementById:()=>({value:'',textContent:'',innerHTML:'',
         classList:{add:()=>{},remove:()=>{},toggle:()=>{}},style:{},readOnly:false})};
       renderDateModalUI=()=>{}; activeModalDate=${JSON.stringify(TARGET)};
       tempIsNoDuty=true; tempNoDutyReason='RULE21'; originalNoDutyReason='RULE21';`);
  run(`toggleNoDutyStatus();`);                 // 정상 운영으로 복구
  assert.equal(run('tempIsNoDuty'), false);
  run(`toggleNoDutyStatus();`);                 // 다시 휴무로
  assert.equal(run('tempIsNoDuty'), true);
  assert.equal(run('tempNoDutyReason'), 'RULE21',
    '표식을 잃으면 true 로 굳어 자동 배정이 영구히 못 건드린다');
});

test('a hand-made no-duty still saves as manual, not as a rule tag', async () => {
  const other = '2026-11-18';
  const {run} = await run_assign();
  run(`renderDateModalUI=()=>{}; activeModalDate=${JSON.stringify(other)};
       tempIsNoDuty=false; tempNoDutyReason=null; originalNoDutyReason=null;`);
  run(`toggleNoDutyStatus();`);
  assert.equal(run('tempIsNoDuty'), true);
  assert.equal(run('tempNoDutyReason'), null, '손으로 만든 휴무는 규칙 표식을 얻어선 안 된다');
});

test('scope modal object is accepted and a bare string still works', async () => {
  const storage = {getItem: () => null, setItem: () => {}, removeItem: () => {}};
  for (const [label, stub] of [['객체', `async()=>({scope:'MONTH',fresh:true})`],
                               ['문자열', `async()=>'MONTH'`]]) {
    const context = vm.createContext({Date, console, sessionStorage: storage, localStorage: storage,
      document: {addEventListener: () => {}}, window: {addEventListener: () => {}},
      setTimeout, clearTimeout, setInterval, clearInterval});
    vm.runInContext(main, context, {filename: htmlPath});
    const run = code => vm.runInContext(code, context);
    run(`
      SYSTEM_MODE='ADMIN'; curYear=2026; curMonth=11;
      memberList=[{id:1,name:'M1',gender:'M'},{id:2,name:'F1',gender:'F'}];
      customHolidays={}; noDutyMap={${JSON.stringify(STALE)}:'RULE21', '2026-11-11':'창립기념일'}; commentMap={${JSON.stringify(STALE)}:'패밀리데이'}; scheduleMap={};
      dutyRule.rule21FridayOff=true; dutyRule.algoType='FIXED_ORDER';
      dutyRule.fixedOrders={weekday:['F1'],weekendM:['M1'],weekendF:['F1'],holidayM:[],holidayF:[],majorM:['M1'],majorF:['F1']};
      renderCalendar=()=>{}; showToast=()=>{}; syncFullDataToBackend=async()=>true;
      rememberCommittedState(); mergeHolidays();
      openScopeModal=${stub};
      openRotationStartModal=async()=>({});
      openAssignPreview=async(before,scope,scopeLabel,info)=>{globalThis.info=info; return true;};
    `);
    await run(`generateScheduleByRules()`);
    const info = plain(run('info'));
    if (label === '객체') {
      assert.equal(info.fresh, true, '객체: 체크 상태가 전달되어야 한다');
      assert.ok(info.cleared.some(x => x.k === STALE), '객체: 해제 목록에 날짜가 있어야 한다');
    } else {
      assert.equal(info.fresh, false, '문자열: 기존 호출은 지금 동작을 유지한다');
      assert.deepEqual(info.cleared, []);
      assert.ok(info.keptManual.some(x => x.k === '2026-11-11'), '문자열: 관리자 휴무는 유지되는 휴무로 보고한다');
    }
  }
});
