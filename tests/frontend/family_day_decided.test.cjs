'use strict';
// v3.16.1 패밀리데이 — 사람이 정한 날 인정, '이 달은 없음' 기억.
//
// [위험] 예전 규칙은 계산한 금요일 하루만 봤다.
//   1) 관리자가 다른 날(다른 금요일·목요일)로 정해 둔 달에도 금요일에 하나를 더 만들어
//      한 달에 패밀리데이가 두 번 생겼고, 그만큼 평일 순번이 밀렸다.
//   2) 이 달은 안 하기로 해서 휴무를 풀어 두면 자동 배정 때마다 다시 생겼다.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const htmlPath = process.env.ONAIR_FRONTEND || path.resolve(__dirname, '../../frontend/dist/index.html');
const html = fs.readFileSync(htmlPath, 'utf8');
const main = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(x => x[1]).find(s => s.includes('function friday21Of('));
for (const name of ['manualFamilyDayOf', 'familyOffMonths', 'saveDateModalChanges']) {
  if (!main.includes(`function ${name}(`)) { console.error(`${name} 를 찾지 못했습니다 — 함수 이름이 바뀌었는지 확인하세요.`); process.exit(2); }
}
const plain = x => JSON.parse(JSON.stringify(x));

function app({noDuty = {}, comments = {}, off = [], holidays = {}} = {}) {
  const st = {getItem: () => null, setItem: () => {}, removeItem: () => {}};
  const nodes = new Map();
  const element = id => {
    if (!nodes.has(id)) nodes.set(id, {value: '', innerHTML: '', textContent: '', style: {},
      classList: {add() {}, remove() {}, toggle() {}}});
    return nodes.get(id);
  };
  const ctx = vm.createContext({Date, console, JSON, sessionStorage: st, localStorage: st,
    document: {addEventListener: () => {}, getElementById: element, querySelector: () => null, querySelectorAll: () => []},
    window: {addEventListener: () => {}}, setTimeout, clearTimeout, setInterval, clearInterval});
  vm.runInContext(main, ctx, {filename: htmlPath});
  const run = c => vm.runInContext(c, ctx);
  run(`SYSTEM_MODE='ADMIN'; curYear=2026; curMonth=11;
    memberList=[{id:1,name:'M1',gender:'M'},{id:2,name:'M2',gender:'M'},{id:3,name:'F1',gender:'F'},{id:4,name:'F2',gender:'F'},{id:5,name:'F3',gender:'F'}];
    customHolidays=${JSON.stringify(holidays)}; noDutyMap=${JSON.stringify(noDuty)}; commentMap=${JSON.stringify(comments)}; scheduleMap={};
    dutyRule.rule21FridayOff=true; dutyRule.algoType='FIXED_ORDER'; dutyRule.familyDayOffMonths=${JSON.stringify(off)};
    dutyRule.fixedOrders={weekday:['F1','F2','F3'],weekendM:['M1','M2'],weekendF:['F1','F2'],holidayM:[],holidayF:[],majorM:['M1','M2'],majorF:['F1','F2']};
    renderCalendar=()=>{}; globalThis.toasts=[]; showToast=(m,e)=>{toasts.push({m,e});};
    globalThis.payloads=[]; syncFullDataToBackend=async p=>{payloads.push(cloneJson(p)); return true;};
    captureUndo=()=>{}; offerUndo=()=>{}; closeDateModal=()=>{}; restoreUndoAfterFailedSave=()=>{};
    rememberCommittedState(); mergeHolidays();`);
  return {run, element};
}
async function assign(opts) {
  const a = app(opts);
  await a.run(`generateScheduleByRules({silent:true, scope:'MONTH'})`);
  return {...a, noDuty: plain(a.run('noDutyMap')), comments: plain(a.run('commentMap')), schedules: plain(a.run('scheduleMap'))};
}
const FRI_RULE = '2026-11-20';     // 규칙상 11월 패밀리데이(21일 이전 마지막 금요일)
const familyDays = nd => Object.keys(nd).filter(k => k.startsWith('2026-11')).sort();

test('rule still places the family day on the computed Friday when nobody decided', async () => {
  const r = await assign();
  assert.deepEqual(familyDays(r.noDuty), [FRI_RULE]);
  assert.equal(r.noDuty[FRI_RULE], 'RULE21');
});

for (const [label, day] of [['another Friday', '2026-11-13'], ['a Thursday', '2026-11-19'], ['an early Monday', '2026-11-02']]) {
  test(`a person-chosen family day on ${label} is the month's only family day`, async () => {
    const r = await assign({noDuty: {[day]: true, [FRI_RULE]: 'RULE21'}, comments: {[day]: '패밀리데이', [FRI_RULE]: '패밀리데이'}});
    assert.deepEqual(familyDays(r.noDuty), [day], '한 달에 패밀리데이는 하나여야 한다');
    assert.ok((r.schedules[FRI_RULE] || []).length, '규칙이 만들었던 금요일은 근무일로 돌아와 배정된다');
    assert.ok(!(FRI_RULE in r.comments), '풀린 금요일에 패밀리데이 메모가 남으면 안 된다');
  });
}

test('a family memo with extra words still counts as the decided day', async () => {
  const r = await assign({noDuty: {'2026-11-13': '사내 행사'}, comments: {'2026-11-13': '패밀리데이(행사로 앞당김)'}});
  assert.deepEqual(familyDays(r.noDuty), ['2026-11-13']);
});

test('a manual no-duty without a family memo does not count as the family day', async () => {
  const r = await assign({noDuty: {'2026-11-11': '창립기념일'}, comments: {'2026-11-11': '전사 휴무'}});
  assert.deepEqual(familyDays(r.noDuty), ['2026-11-11', FRI_RULE]);
});

// [위험] '이 달은 안 함' 을 기억할 곳이 없어 풀어도 자동 배정 때마다 다시 생겼다.
test('a month remembered as no-family-day gets none, and an old rule day is released', async () => {
  const r = await assign({off: ['2026-11'], noDuty: {[FRI_RULE]: 'RULE21'}, comments: {[FRI_RULE]: '패밀리데이'}});
  assert.deepEqual(familyDays(r.noDuty), []);
  assert.ok((r.schedules[FRI_RULE] || []).length);
  assert.ok(!(FRI_RULE in r.comments));
  assert.equal(r.run('toasts.filter(t => /중단/.test(t.m)).length'), 0, '없음으로 기억한 달 때문에 자동 배정이 멈추면 안 된다');
});

test('remembering one month does not affect other months', async () => {
  const a = app({off: ['2026-12']});
  await a.run(`generateScheduleByRules({silent:true, scope:'MONTH'})`);
  assert.equal(plain(a.run('noDutyMap'))[FRI_RULE], 'RULE21');
});

test('all-holiday month still stops when nobody decided and it is not skipped', async () => {
  const hol = {};
  for (const d of ['2026-11-20', '2026-11-13', '2026-11-06']) hol[d] = {name: '가상 휴일', isMajor: false};
  const blocked = await assign({holidays: hol});
  assert.match(blocked.run('toasts[0].m'), /자동 배정을 중단/);
  const skipped = await assign({holidays: hol, off: ['2026-11']});
  assert.equal(skipped.run('toasts.filter(t => /중단/.test(t.m)).length'), 0, '없음으로 기억한 달은 중단 사유가 아니다');
  const decided = await assign({holidays: hol, noDuty: {'2026-11-19': true}, comments: {'2026-11-19': '패밀리데이'}});
  assert.equal(decided.run('toasts.filter(t => /중단/.test(t.m)).length'), 0, '사람이 정한 날이 있으면 중단 사유가 아니다');
});

// ── 날짜 창에서 풀 때 묻기 ───────────────────────────────────────

async function saveDate(a, {date, wasNoDuty, wasReason, nowNoDuty, memo, confirmAnswer}) {
  a.run(`noDutyMap[${JSON.stringify(date)}] = ${JSON.stringify(wasNoDuty ? (wasReason || true) : null)};
         if (!noDutyMap[${JSON.stringify(date)}]) delete noDutyMap[${JSON.stringify(date)}];
         activeModalDate=${JSON.stringify(date)}; tempIsNoDuty=${nowNoDuty}; tempNoDutyReason=null; tempSelectedWorkers=[];
         globalThis.asked=[]; confirm=m=>{asked.push(m); return ${confirmAnswer};};`);
  a.element('modal-date-comment').value = memo;
  await a.run('saveDateModalChanges()');
}

test('restoring a family day and answering yes remembers the month and strips the memo line', async () => {
  const a = app({comments: {[FRI_RULE]: '패밀리데이'}});
  await saveDate(a, {date: FRI_RULE, wasNoDuty: true, wasReason: 'RULE21', nowNoDuty: false, memo: '패밀리데이\n점검', confirmAnswer: true});
  assert.match(a.run('asked[0]'), /패밀리데이 없이 지낼까요/);   // v3.18.0 부정 의문문 대신
  assert.deepEqual(plain(a.run('dutyRule.familyDayOffMonths')), ['2026-11']);
  const sent = a.run('payloads.at(-1)');
  assert.ok(sent.rules, '규칙 변경은 서버에 함께 저장되어야 한다');
  assert.equal(plain(a.run('commentMap'))[FRI_RULE], '점검', '패밀리데이 줄만 떼고 다른 메모는 남긴다');
  assert.match(sent.historyLog.details, /패밀리데이 없음/);
});

test('restoring a family day and answering no only restores the day', async () => {
  const a = app({comments: {[FRI_RULE]: '패밀리데이'}});
  await saveDate(a, {date: FRI_RULE, wasNoDuty: true, wasReason: 'RULE21', nowNoDuty: false, memo: '패밀리데이', confirmAnswer: false});
  assert.deepEqual(plain(a.run('dutyRule.familyDayOffMonths')), []);
  assert.ok(!a.run('payloads.at(-1)').rules);
});

test('restoring an ordinary no-duty day never asks about family day', async () => {
  const a = app({comments: {'2026-11-11': '창립기념일'}});
  await saveDate(a, {date: '2026-11-11', wasNoDuty: true, wasReason: '창립기념일', nowNoDuty: false, memo: '', confirmAnswer: true});
  assert.equal(a.run('asked.length'), 0);
  assert.deepEqual(plain(a.run('dutyRule.familyDayOffMonths')), []);
});

test('choosing a family day again in a remembered month clears the memory', async () => {
  const a = app({off: ['2026-11'], noDuty: {[FRI_RULE]: 'RULE21'}});
  await saveDate(a, {date: '2026-11-19', wasNoDuty: false, nowNoDuty: true, memo: '패밀리데이', confirmAnswer: true});
  assert.deepEqual(plain(a.run('dutyRule.familyDayOffMonths')), []);
  assert.ok(a.run('payloads.at(-1)').rules);
});

// ── v3.16.2 패밀리데이도 규칙대로 다시 정하기 ─────────────────────
// [위험] 여러 판을 거치며 규칙 금요일·직접 지정·잠긴 휴무가 섞인 달은, 사람이 정한 날을 존중하는
//   규칙 때문에 잘못 남은 날까지 '사람이 정한 날' 로 인정되어 바로잡을 방법이 없었다.
const MIXED = {
  off: ['2026-11'],
  noDuty: {'2026-11-13': 'RULE21', '2026-11-19': true, [FRI_RULE]: 'RULE21', '2026-11-11': '창립기념일'},
  comments: {'2026-11-13': '패밀리데이', '2026-11-19': '패밀리데이\n설비 점검', [FRI_RULE]: '패밀리데이', '2026-11-11': '전사 휴무'}
};

async function assignReset(opts, extra = {}) {
  const a = app(opts);
  await a.run(`generateScheduleByRules(${JSON.stringify({silent: true, scope: 'MONTH', familyReset: true, ...extra})})`);
  return {...a, noDuty: plain(a.run('noDutyMap')), comments: plain(a.run('commentMap')), schedules: plain(a.run('scheduleMap'))};
}

test('family reset turns a mixed month into exactly one rule family day', async () => {
  const r = await assignReset(MIXED);
  assert.deepEqual(familyDays(r.noDuty), ['2026-11-11', FRI_RULE], '관리자 휴무(창립기념일)와 규칙 금요일 하나만 남는다');
  assert.equal(r.noDuty[FRI_RULE], 'RULE21');
  for (const k of ['2026-11-13', '2026-11-19']) assert.ok((r.schedules[k] || []).length, `${k} 는 근무일로 배정되어야 한다`);
  assert.equal(r.comments['2026-11-19'], '설비 점검', '패밀리데이 줄만 지우고 다른 메모는 남긴다');
  assert.ok(!('2026-11-13' in r.comments));
  assert.equal(r.comments['2026-11-11'], '전사 휴무');
  assert.deepEqual(plain(r.run('dutyRule.familyDayOffMonths')), [], "'없음' 기억도 지운다");
  const sent = r.run('payloads.at(-1)');
  assert.ok(sent.rules, "'없음' 기억이 바뀌었으면 규칙도 함께 저장한다");
  assert.match(sent.historyLog.details, /패밀리데이 규칙대로 다시 지정/);
});

test('without family reset a person-chosen day is still respected (control)', async () => {
  const r = await assign({noDuty: {'2026-11-19': true}, comments: {'2026-11-19': '패밀리데이'}});
  assert.deepEqual(familyDays(r.noDuty), ['2026-11-19']);
});

// 고정 순번을 '알맞게' 따른다는 것: 평일 여 순번이 패밀리데이 하나만 건너뛰고 끊김 없이 돈다.
test('after family reset the weekday rotation runs in fixed order skipping only the one family day', async () => {
  const r = await assignReset(MIXED);
  const order = ['F1', 'F2', 'F3'];
  const weekdays = Object.keys(r.schedules).filter(k => k.startsWith('2026-11')).sort()
    .filter(k => r.run(`slotCategory('${k}')`) === 'weekday' && !r.noDuty[k]);
  const names = weekdays.map(k => r.schedules[k][0].name);
  const start = order.indexOf(names[0]);
  names.forEach((n, i) => assert.equal(n, order[(start + i) % 3], `${weekdays[i]} 순번이 끊겼다`));
  assert.ok(!weekdays.includes(FRI_RULE) && weekdays.includes('2026-11-13') && weekdays.includes('2026-11-19'));
});

test('family reset never touches major-holiday rule days', async () => {
  // 11/6 을 명절 당일로 보게 해서, 명절 규칙 휴무에 패밀리데이 메모가 붙어 있어도 초기화가 지우지 않는지 본다.
  const a = app({noDuty: {'2026-11-06': 'RULE_MAJOR_DAY'}, comments: {'2026-11-06': '패밀리데이'}});
  a.run(`isMajorMainDay = k => k === '2026-11-06';
         openScopeModal=async()=>({scope:'MONTH', fresh:false, familyReset:true});
         openRotationStartModal=async()=>({});
         openAssignPreview=async(before,scope,label,info)=>{globalThis.info=info; return true;};`);
  await a.run('generateScheduleByRules()');
  assert.equal(plain(a.run('noDutyMap'))['2026-11-06'], 'RULE_MAJOR_DAY');
  // 끝 상태만 보면 명절 규칙이 다시 지정해 구분되지 않으므로, 초기화가 지운 목록에 없는지 본다.
  assert.ok(!plain(a.run('info')).cleared.some(x => x.k === '2026-11-06'), '명절 규칙 휴무는 패밀리데이 초기화 대상이 아니다');
});

for (const reason of ['석식간담회', true, '기존 행사']) {
  test(`family reset preserves a confirmed dinner day while moving only its family marker (${reason})`, async () => {
    const key = '2026-11-19';
    const r = await assignReset({noDuty: {[key]: reason}, comments: {[key]: '패밀리데이\n기존 점검 안내\n석식간담회'}});
    assert.equal(r.noDuty[key], reason, '회식으로 정한 휴무는 패밀리데이 초기화가 해제할 수 없다');
    assert.equal((r.schedules[key] || []).length, 0, '확정한 회식 날짜에 근무자를 다시 배정하지 않는다');
    assert.equal(r.comments[key], '기존 점검 안내\n석식간담회');
    assert.equal(r.noDuty[FRI_RULE], 'RULE21', '패밀리데이는 요청한 규칙 날짜로 별도 정리한다');
    assert.deepEqual(familyDays(r.noDuty), [key, FRI_RULE]);
  });
}

test('cancelling the preview restores family days, memos and the no-family memory', async () => {
  const a = app(MIXED);
  a.run(`openScopeModal=async()=>({scope:'MONTH', fresh:false, familyReset:true});
         openRotationStartModal=async()=>({});
         openAssignPreview=async(before,scope,label,info)=>{globalThis.info=info; return false;};`);
  await a.run('generateScheduleByRules()');
  assert.deepEqual(plain(a.run('noDutyMap')), MIXED.noDuty);
  assert.deepEqual(plain(a.run('commentMap')), MIXED.comments);
  assert.deepEqual(plain(a.run('dutyRule.familyDayOffMonths')), ['2026-11']);
  assert.equal(a.run('payloads.length'), 0);
  const info = plain(a.run('info'));
  assert.deepEqual(info.cleared.map(x => x.k).sort(), ['2026-11-13', '2026-11-19', FRI_RULE]);
  assert.deepEqual(info.familyUnremembered, ['2026-11']);
  // 규칙이 같은 날(11/20)에 다시 지정한 경우는 미리보기에서 '해제' 로 오해하지 않게 적는다.
  assert.match(info.cleared.find(x => x.k === FRI_RULE).why, /다시 지정/);
  assert.doesNotMatch(info.cleared.find(x => x.k === '2026-11-13').why, /다시 지정/);
});

test('cancelling the rotation start window also restores everything', async () => {
  const a = app(MIXED);
  a.run(`openScopeModal=async()=>({scope:'MONTH', fresh:false, familyReset:true});
         openRotationStartModal=async()=>null;`);
  await a.run('generateScheduleByRules()');
  assert.deepEqual(plain(a.run('noDutyMap')), MIXED.noDuty);
  assert.deepEqual(plain(a.run('commentMap')), MIXED.comments);
  assert.deepEqual(plain(a.run('dutyRule.familyDayOffMonths')), ['2026-11']);
});

test('an all-holiday stop after family reset leaves nothing changed', async () => {
  const hol = {};
  for (const d of ['2026-11-20', '2026-11-13', '2026-11-06']) hol[d] = {name: '가상 휴일', isMajor: false};
  const r = await assignReset({...MIXED, holidays: hol});
  assert.match(r.run('toasts[0].m'), /중단/);
  assert.deepEqual(r.noDuty, MIXED.noDuty);
  assert.deepEqual(r.comments, MIXED.comments);
  assert.deepEqual(plain(r.run('dutyRule.familyDayOffMonths')), ['2026-11']);
});
