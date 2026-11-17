'use strict';
// 고정 순환제(FIXED_ORDER) 순번 규칙의 보호 회귀.
//   돌연변이 검사에서 아래 줄을 망가뜨려도 기존 검사가 하나도 실패하지 않았다. 동작은 바꾸지 않고 지키기만 한다.
//   a) 주말 순번은 토/일 2인 페어. 한 바퀴 돌면 페어 안에서 토·일이 바뀐다 (weekendSeq)
//   b) 토/일에 걸린 공휴일은 공휴일 전용 순번이 따로 있어도 주말 순번을 쓴다
//   c) 날짜 구분 우선순위: 명절 > 주말 > 공휴일 > 평일 (slotCategory)
const assert = require('node:assert/strict');
const {test} = require('node:test');
const {app, plain, generate} = require('./helpers/scheduler.cjs');

const M4 = ['M1', 'M2', 'M3', 'M4'], F4 = ['F1', 'F2', 'F3', 'F4'];
function rotationApp({weekendM = M4, weekendF = F4, holidayM = ['M4', 'M3', 'M2', 'M1'], holidayF = ['F4', 'F3', 'F2', 'F1'], holidays = {}} = {}) {
  const run = app();
  run(`memberList=[{id:1,name:'M1',gender:'M'},{id:2,name:'M2',gender:'M'},{id:3,name:'M3',gender:'M'},{id:7,name:'M4',gender:'M'},
                   {id:4,name:'F1',gender:'F'},{id:5,name:'F2',gender:'F'},{id:6,name:'F3',gender:'F'},{id:8,name:'F4',gender:'F'}];
    dutyRule.algoType='FIXED_ORDER'; dutyRule.rule21FridayOff=false;
    dutyRule.fixedOrders={weekday:['F1','F2','F3','F4'],weekendM:${JSON.stringify(weekendM)},weekendF:${JSON.stringify(weekendF)},
      holidayM:${JSON.stringify(holidayM)},holidayF:${JSON.stringify(holidayF)},majorM:['M1','M2','M3','M4'],majorF:['F1','F2','F3','F4']};
    customHolidays=${JSON.stringify(holidays)}; mergeHolidays();`);
  return run;
}
const nameOf = (list, g) => (list || []).find(w => (g === 'M' ? /^M/ : /^F/).test(w.name))?.name;

// ── a) weekendSeq ──────────────────────────────────────────────
test('weekendSeq: 4 people pair up and swap Saturday/Sunday on the second cycle', () => {
  const run = rotationApp();
  assert.deepEqual(plain(run(`weekendSeq(['A','B','C','D'])`)), ['A', 'B', 'C', 'D', 'B', 'A', 'D', 'C']);
  assert.deepEqual(plain(run(`weekendSeq(['A','B','C','D','E','F'])`)), ['A', 'B', 'C', 'D', 'E', 'F', 'B', 'A', 'D', 'C', 'F', 'E']);
});
test('weekendSeq: an odd roster stays in plain order', () => {
  const run = rotationApp();
  assert.deepEqual(plain(run(`weekendSeq(['A','B','C'])`)), ['A', 'B', 'C']);
  assert.deepEqual(plain(run(`weekendSeq(['A','B','C','D','E'])`)), ['A', 'B', 'C', 'D', 'E']);
});
test('weekendSeq: 2 people give A B | B A, 1 or 0 people are copied as is', () => {
  const run = rotationApp();
  assert.deepEqual(plain(run(`weekendSeq(['A','B'])`)), ['A', 'B', 'B', 'A']);
  assert.deepEqual(plain(run(`weekendSeq(['A'])`)), ['A']);
  assert.deepEqual(plain(run(`weekendSeq([])`)), []);
  assert.deepEqual(plain(run(`weekendSeq(null)`)), []);
  // 원본 배열을 건드리지 않는다.
  assert.equal(run(`(()=>{const o=['A','B'];const s=weekendSeq(o);s.push('X');return o.length;})()`), 2);
});
test('rotationOrders exposes the paired weekend sequences', () => {
  const run = rotationApp();
  assert.deepEqual(plain(run('rotationOrders().weekendMSeq')), ['M1', 'M2', 'M3', 'M4', 'M2', 'M1', 'M4', 'M3']);
  assert.deepEqual(plain(run('rotationOrders().weekendFSeq')), ['F1', 'F2', 'F3', 'F4', 'F2', 'F1', 'F4', 'F3']);
});

// 2027-11 은 공휴일이 하나도 없고 토/일이 4쌍이라, 주말 시퀀스 8칸이 정확히 한 바퀴 돈다.
const NOV27_WEEKENDS = ['2027-11-06', '2027-11-07', '2027-11-13', '2027-11-14', '2027-11-20', '2027-11-21', '2027-11-27', '2027-11-28'];

for (const [label, order, expected] of [
  ['4 people', M4, ['M1', 'M2', 'M3', 'M4', 'M2', 'M1', 'M4', 'M3']],
  ['3 people', ['M1', 'M2', 'M3'], ['M1', 'M2', 'M3', 'M1', 'M2', 'M3', 'M1', 'M2']],
  ['2 people', ['M1', 'M2'], ['M1', 'M2', 'M2', 'M1', 'M1', 'M2', 'M2', 'M1']],
]) {
  test(`actual Saturday/Sunday assignment follows weekendSeq (${label})`, async () => {
    const run = rotationApp({weekendM: order});
    const schedules = await generate(run, 2027, 11);
    assert.deepEqual(NOV27_WEEKENDS.map(k => run(`slotCategory('${k}','M')`)), NOV27_WEEKENDS.map(() => 'weekend'));
    assert.deepEqual(NOV27_WEEKENDS.map(k => nameOf(schedules[k], 'M')), expected);
    NOV27_WEEKENDS.forEach((k, i) => assert.equal(run(`dowOf('${k}')`), i % 2 ? 0 : 6));
  });
}
test('actual female weekend assignment also follows the paired sequence', async () => {
  const run = rotationApp();
  const schedules = await generate(run, 2027, 11);
  assert.deepEqual(NOV27_WEEKENDS.map(k => nameOf(schedules[k], 'F')), ['F1', 'F2', 'F3', 'F4', 'F2', 'F1', 'F4', 'F3']);
});

// ── b) 토/일 공휴일은 주말 순번, 평일 공휴일은 공휴일 순번 ──────────────
test('a Saturday holiday with a separate holiday order uses the weekend order', async () => {
  const sat = '2027-11-13';
  const run = rotationApp({holidays: {[sat]: {name: '가상 공휴일'}}});
  assert.equal(run(`dowOf('${sat}')`), 6);
  assert.ok(run(`!!finalHolidays['${sat}']`));
  assert.equal(run('rotationOrders().holidayUsesWeekendM'), false, '공휴일 순번이 따로 있는 설정');
  assert.equal(run(`slotCategory('${sat}','M')`), 'weekend');
  assert.equal(run(`slotCategory('${sat}','F')`), 'weekend');
  assert.equal(run(`slotCategory('${sat}')`), 'weekend');
  const schedules = await generate(run, 2027, 11);
  // 주말 시퀀스 셋째 칸(M3/F3). 공휴일 순번이었다면 첫 칸(M4/F4)이 나온다.
  assert.equal(nameOf(schedules[sat], 'M'), 'M3');
  assert.equal(nameOf(schedules[sat], 'F'), 'F3');
  // 주말 시퀀스는 끊기지 않는다.
  assert.deepEqual(NOV27_WEEKENDS.map(k => nameOf(schedules[k], 'M')), ['M1', 'M2', 'M3', 'M4', 'M2', 'M1', 'M4', 'M3']);
});
test('a Sunday holiday with a separate holiday order uses the weekend order', async () => {
  const sun = '2027-11-14';
  const run = rotationApp({holidays: {[sun]: {name: '가상 공휴일'}}});
  assert.equal(run(`slotCategory('${sun}','M')`), 'weekend');
  const schedules = await generate(run, 2027, 11);
  assert.equal(nameOf(schedules[sun], 'M'), 'M4');
});
test('a weekday holiday with a separate holiday order uses the holiday order', async () => {
  const wed = '2027-11-10';
  const run = rotationApp({holidays: {[wed]: {name: '가상 공휴일'}}});
  assert.equal(run(`dowOf('${wed}')`), 3);
  assert.equal(run(`slotCategory('${wed}','M')`), 'holiday');
  assert.equal(run(`slotCategory('${wed}','F')`), 'holiday');
  assert.equal(run(`slotCategory('${wed}')`), 'holiday');
  const schedules = await generate(run, 2027, 11);
  assert.equal(nameOf(schedules[wed], 'M'), 'M4', '공휴일 남 순번 첫 칸');
  assert.equal(nameOf(schedules[wed], 'F'), 'F4', '공휴일 여 순번 첫 칸');
  // 평일 공휴일은 주말 시퀀스를 소비하지 않는다.
  assert.deepEqual(NOV27_WEEKENDS.map(k => nameOf(schedules[k], 'M')), ['M1', 'M2', 'M3', 'M4', 'M2', 'M1', 'M4', 'M3']);
});
test('a weekday holiday without a holiday order shares the weekend order (per gender)', () => {
  const wed = '2027-11-10';
  const both = rotationApp({holidayM: [], holidayF: [], holidays: {[wed]: {name: '가상 공휴일'}}});
  assert.equal(both(`slotCategory('${wed}','M')`), 'weekend');
  assert.equal(both(`slotCategory('${wed}','F')`), 'weekend');
  assert.equal(both(`slotCategory('${wed}')`), 'weekend');
  const onlyF = rotationApp({holidayM: [], holidays: {[wed]: {name: '가상 공휴일'}}});
  assert.equal(onlyF(`slotCategory('${wed}','M')`), 'weekend');
  assert.equal(onlyF(`slotCategory('${wed}','F')`), 'holiday');
  assert.equal(onlyF(`slotCategory('${wed}')`), 'holiday', '성별 없이 물으면 둘 다 비었을 때만 공유');
});

// ── c) 우선순위: 명절 > 주말 > 공휴일 > 평일 ─────────────────────────
test('slotCategory precedence is major > weekend > holiday > weekday', () => {
  const run = rotationApp({holidays: {'2027-11-13': {name: '가상 공휴일'}, '2027-11-10': {name: '가상 공휴일'}}});
  // 2026 추석 연휴 9/24(목)~9/26(토): 토요일이면서 공휴일이면서 명절 → 명절
  for (const g of ['M', 'F', undefined]) {
    const arg = g ? `,'${g}'` : '';
    assert.equal(run(`slotCategory('2026-09-26'${arg})`), 'major', `명절 토요일 ${g}`);
    assert.equal(run(`slotCategory('2026-09-24'${arg})`), 'major', `명절 평일 ${g}`);
    assert.equal(run(`slotCategory('2027-11-13'${arg})`), 'weekend', `토요일 공휴일 ${g}`);
    assert.equal(run(`slotCategory('2027-11-06'${arg})`), 'weekend', `토요일 ${g}`);
    assert.equal(run(`slotCategory('2027-11-10'${arg})`), 'holiday', `평일 공휴일 ${g}`);
    assert.equal(run(`slotCategory('2027-11-09'${arg})`), 'weekday', `평일 ${g}`);
  }
  assert.equal(run(`dowOf('2026-09-26')`), 6);
  assert.ok(run(`!!finalHolidays['2026-09-26']`));
  // 실제 달력: 설 연휴에 붙은 평일 대체공휴일 2027-02-09(화)는 명절 팀 → major,
  //   토요일 개천절 2026-10-03 → weekend, 평일 대체공휴일 2026-10-05(월)·한글날 2026-10-09(금) → holiday
  for (const g of ['M', 'F']) {
    assert.equal(run(`slotCategory('2027-02-09','${g}')`), 'major');
    assert.equal(run(`slotCategory('2026-10-03','${g}')`), 'weekend');
    assert.equal(run(`slotCategory('2026-10-05','${g}')`), 'holiday');
    assert.equal(run(`slotCategory('2026-10-09','${g}')`), 'holiday');
  }
});
