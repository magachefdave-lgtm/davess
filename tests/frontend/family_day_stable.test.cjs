'use strict';
// v3.17.13 패밀리데이를 여러 번 다시 배정해도 같은 날로 남는지(멱등성) 지킨다.
//
// [위험] 이 파일이 막는 실제 사고 두 가지.
//  1) 2026-09 는 기준 금요일 9/25 가 추석 당일이다. 첫 배정은 9/18 을 패밀리데이로 맞게 잡지만,
//     두 번째 배정부터 9/25 의 명절 규칙 휴무(RULE_MAJOR_DAY)를 '이미 적용된 패밀리데이' 로 착각해
//     9/18 휴무·메모를 풀고 근무를 넣었으며, '패밀리데이' 메모를 추석 당일로 옮겼다.
//     (이번 달·연간·균등 배정·처음부터 다시 짜기·패밀리데이 초기화 모두 같은 증상)
//  2) '처음부터 다시 짜기' 가 대상일을 먼저 계산하고 규칙 표식을 나중에 지워, 규칙 금요일이 나중에
//     공휴일로 등록된 달에도 옛 RULE21 이 남아 한 주 앞으로 옮겨지지 않았다.
const assert = require('node:assert/strict');
const {test} = require('node:test');
const {app, plain, generate} = require('./helpers/scheduler.cjs');

function familyApp({algo = 'FIXED_ORDER', holidays = {}, noDuty = {}, comments = {}} = {}) {
  const run = app();
  run(`dutyRule.rule21FridayOff=true; dutyRule.algoType=${JSON.stringify(algo)};
    customHolidays=${JSON.stringify(holidays)}; noDutyMap=${JSON.stringify(noDuty)}; commentMap=${JSON.stringify(comments)};
    mergeHolidays();`);
  return run;
}
function state(run) {
  return {noDuty: plain(run('noDutyMap')), comments: plain(run('commentMap')), schedules: plain(run('scheduleMap'))};
}
const hasFamily = text => String(text || '').split('\n').some(line => line.includes('패밀리데이'));

const SEP_FAMILY = '2026-09-18';   // 2026-09 패밀리데이 (기준 금요일이 추석 당일이라 한 주 앞)
const CHUSEOK = '2026-09-25';      // 추석 당일 = 기준 금요일

function assertSeptember(s, label) {
  assert.equal(s.noDuty[SEP_FAMILY], 'RULE21', `${label}: 9/18 은 패밀리데이(규칙)`);
  assert.ok(hasFamily(s.comments[SEP_FAMILY]), `${label}: 9/18 에 패밀리데이 메모`);
  assert.deepEqual(s.schedules[SEP_FAMILY], [], `${label}: 9/18 은 근무 없음`);
  assert.equal(s.noDuty[CHUSEOK], 'RULE_MAJOR_DAY', `${label}: 추석 당일은 명절 규칙 휴무`);
  assert.ok(!hasFamily(s.comments[CHUSEOK]), `${label}: 추석 당일에 패밀리데이 메모가 붙으면 안 된다`);
  const ruleDays = Object.keys(s.noDuty).filter(k => k.startsWith('2026-09') && s.noDuty[k] === 'RULE21');
  assert.deepEqual(ruleDays, [SEP_FAMILY], `${label}: 9월 규칙 패밀리데이는 하나`);
}

test('base Friday on Chuseok main day is never taken as the family day', () => {
  const run = familyApp({noDuty: {[CHUSEOK]: 'RULE_MAJOR_DAY'}});
  assert.equal(run(`isMajorMainDay('${CHUSEOK}')`), true);
  assert.equal(run(`dateKeyOf(2026,9,friday21Of(2026,9).getDate())`), CHUSEOK);
  assert.equal(run('familyDayKeyOf(2026,9)'), SEP_FAMILY);
  // 수동 휴무(true)가 추석 당일에 걸려 있어도 다른 이유의 휴무이므로 패밀리데이가 아니다.
  run(`noDutyMap['${CHUSEOK}']=true;`);
  assert.equal(run('familyDayKeyOf(2026,9)'), SEP_FAMILY);
});

// 명절 설정이 바뀌어 남은 옛 명절 규칙 휴무(RULE_MAJOR_DAY)도 '패밀리데이 적용 완료' 가 아니다.
test('a stale major-day rule tag on a holiday Friday does not become the family day', async () => {
  const fri = '2026-11-20';
  const run = familyApp({holidays: {[fri]: {name: '임시공휴일'}}, noDuty: {[fri]: 'RULE_MAJOR_DAY'}});
  assert.equal(run(`isMajorMainDay('${fri}')`), false);
  assert.equal(run('familyDayKeyOf(2026,11)'), '2026-11-13');
  await generate(run, 2026, 11);
  const s = state(run);
  assert.equal(s.noDuty['2026-11-13'], 'RULE21');
  assert.equal(s.noDuty[fri], undefined, '옛 명절 규칙 휴무는 풀리고 패밀리데이로 바뀌지도 않는다');
  assert.ok(s.schedules[fri].length > 0, '공휴일 근무가 배정된다');
  assert.ok(!hasFamily(s.comments[fri]));
});

for (const algo of ['FIXED_ORDER', 'FAIR_WEIGHT']) {
  for (const [label, opts] of [['MONTH', {}], ['fresh', {fresh: true}], ['familyReset', {familyReset: true}]]) {
    test(`2026-09 family day stays on 9/18 across three runs (${algo}, ${label})`, async () => {
      const run = familyApp({algo});
      for (let i = 1; i <= 3; i++) {
        await generate(run, 2026, 9, opts);
        assertSeptember(state(run), `${algo} ${label} run ${i}`);
      }
    });
  }
  test(`2026-09 family day stays on 9/18 across three YEAR runs (${algo})`, async () => {
    const run = familyApp({algo});
    for (let i = 1; i <= 3; i++) {
      await generate(run, 2026, 1, {scope: 'YEAR'});
      assertSeptember(state(run), `${algo} YEAR run ${i}`);
    }
  });
}

test('mixing modes on 2026-09 (YEAR → MONTH → fresh → familyReset) keeps one answer', async () => {
  const run = familyApp();
  await generate(run, 2026, 1, {scope: 'YEAR'});
  const first = state(run);
  for (const opts of [{}, {fresh: true}, {familyReset: true}, {fresh: true, familyReset: true}, {scope: 'YEAR'}]) {
    await generate(run, 2026, opts.scope === 'YEAR' ? 1 : 9, opts);
    const s = state(run);
    assertSeptember(s, JSON.stringify(opts));
    for (const k of Object.keys(first.schedules).filter(k => k.startsWith('2026-09')))
      assert.deepEqual(s.schedules[k], first.schedules[k], `${JSON.stringify(opts)}: ${k} 배정이 바뀌면 안 된다`);
  }
});

test('data damaged by the old repeated run (memo on Chuseok, duty on 9/18) is repaired', async () => {
  const run = familyApp({
    noDuty: {[CHUSEOK]: 'RULE_MAJOR_DAY'},
    comments: {[CHUSEOK]: '추석\n패밀리데이'}
  });
  run(`scheduleMap={'${SEP_FAMILY}':[{name:'F2',role:'평일(여)'}]};`);
  await generate(run, 2026, 9);
  const s = state(run);
  assertSeptember(s, 'repair');
  assert.equal(s.comments[CHUSEOK], '추석', '사용자가 쓴 다른 메모 줄은 남긴다');
});

// 2026~2029 모든 달: 규칙 결과 = 기준 금요일, 공휴일이면 같은 달 안에서 한 주씩 앞. 두 번째 실행도 같아야 한다.
test('2026-2029 rule family days equal the Friday rule and are identical after a second run', async () => {
  for (const algo of ['FIXED_ORDER', 'FAIR_WEIGHT']) {
    const run = familyApp({algo});
    for (let y = 2026; y <= 2029; y++) {
      await generate(run, y, 1, {scope: 'YEAR'});
      const first = state(run);
      const holidays = plain(run('(mergeHolidays(),finalHolidays)'));
      for (let m = 1; m <= 12; m++) {
        const mm = `${y}-${String(m).padStart(2, '0')}`;
        let d = run(`friday21Of(${y},${m}).getDate()`);
        let expected = null;
        for (; d >= 1; d -= 7) {
          const k = `${mm}-${String(d).padStart(2, '0')}`;
          if (!holidays[k]) { expected = k; break; }
        }
        const actual = Object.keys(first.noDuty).filter(k => k.startsWith(mm) && first.noDuty[k] === 'RULE21');
        assert.deepEqual(actual, expected ? [expected] : [], `${algo} ${mm}`);
        if (expected) assert.deepEqual(first.schedules[expected], [], `${algo} ${expected} 근무 없음`);
        const memoDays = Object.keys(first.comments).filter(k => k.startsWith(mm) && hasFamily(first.comments[k]));
        assert.deepEqual(memoDays, expected ? [expected] : [], `${algo} ${mm} 패밀리데이 메모는 그 날 하나`);
      }
      await generate(run, y, 1, {scope: 'YEAR'});
      const second = state(run);
      assert.deepEqual(second.noDuty, first.noDuty, `${algo} ${y} 두 번째 실행 휴무`);
      assert.deepEqual(second.comments, first.comments, `${algo} ${y} 두 번째 실행 메모`);
      assert.deepEqual(second.schedules, first.schedules, `${algo} ${y} 두 번째 실행 배정`);
    }
  }
});

test('2027 confirmed family days and 2026-10-23 are pinned', async () => {
  const confirmed = ['2027-01-22', '2027-02-19', '2027-03-19', '2027-04-23', '2027-05-21', '2027-06-25',
    '2027-07-23', '2027-08-20', '2027-09-24', '2027-10-22', '2027-11-19', '2027-12-24'];
  const run = familyApp();
  await generate(run, 2027, 1, {scope: 'YEAR'});
  await generate(run, 2027, 1, {scope: 'YEAR'});
  const s = state(run);
  assert.deepEqual(Object.keys(s.noDuty).filter(k => s.noDuty[k] === 'RULE21').sort(), confirmed);
  const oct = familyApp();
  await generate(oct, 2026, 10);
  await generate(oct, 2026, 10);
  const o = state(oct);
  assert.deepEqual(Object.keys(o.noDuty).filter(k => o.noDuty[k] === 'RULE21'), ['2026-10-23']);
  assert.ok(hasFamily(o.comments['2026-10-23']));
});

// ── 결함 2: 처음부터 다시 짜기 ──────────────────────────────────────
const NOV_RULE = '2026-11-20', NOV_EARLIER = '2026-11-13';

test('fresh rebuild moves RULE21 off a Friday that later became a custom holiday', async () => {
  for (const algo of ['FIXED_ORDER', 'FAIR_WEIGHT']) {
    const run = familyApp({algo});
    await generate(run, 2026, 11);
    assert.equal(state(run).noDuty[NOV_RULE], 'RULE21');
    run(`customHolidays={'${NOV_RULE}':{name:'임시공휴일'}}; mergeHolidays();`);
    await generate(run, 2026, 11, {fresh: true});
    const s = state(run);
    assert.equal(s.noDuty[NOV_EARLIER], 'RULE21', `${algo}: 한 주 앞 금요일로 옮긴다`);
    assert.equal(s.noDuty[NOV_RULE], undefined, `${algo}: 공휴일이 된 금요일의 RULE21 은 푼다`);
    assert.ok(s.schedules[NOV_RULE].length > 0, `${algo}: 공휴일 근무는 배정된다`);
    assert.deepEqual(s.schedules[NOV_EARLIER], []);
    assert.ok(!hasFamily(s.comments[NOV_RULE]), `${algo}: 옛 금요일 메모는 뗀다`);
    assert.ok(hasFamily(s.comments[NOV_EARLIER]));
    // 다시 돌려도 그대로다.
    await generate(run, 2026, 11, {fresh: true});
    assert.deepEqual(state(run), s, `${algo}: 두 번째 처음부터 다시 짜기도 같은 결과`);
  }
});

test('without fresh, an existing RULE21 on a newly registered holiday is still kept (control)', async () => {
  const run = familyApp();
  await generate(run, 2026, 11);
  run(`customHolidays={'${NOV_RULE}':{name:'임시공휴일'}}; mergeHolidays();`);
  await generate(run, 2026, 11);
  const s = state(run);
  assert.equal(s.noDuty[NOV_RULE], 'RULE21');
  assert.equal(s.noDuty[NOV_EARLIER], undefined);
});

test('fresh rebuild keeps a manual no-duty on a holiday Friday (control)', async () => {
  const run = familyApp({holidays: {[NOV_RULE]: {name: '임시공휴일'}}, noDuty: {[NOV_RULE]: true}});
  await generate(run, 2026, 11, {fresh: true});
  const s = state(run);
  assert.equal(s.noDuty[NOV_RULE], true);
  assert.equal(s.noDuty[NOV_EARLIER], undefined);
});

test('fresh rebuild preview lists the moved day and cancel restores everything', async () => {
  const base = familyApp();
  await generate(base, 2026, 11);
  const before = state(base);
  for (const confirmed of [false, true]) {
    const run = familyApp({holidays: {[NOV_RULE]: {name: '임시공휴일'}}, noDuty: before.noDuty, comments: before.comments});
    run(`scheduleMap=${JSON.stringify(before.schedules)}; curYear=2026; curMonth=11; rememberCommittedState(); globalThis.savedPayload=undefined;
      openScopeModal=async()=>({scope:'MONTH', fresh:true, familyReset:false});
      openRotationStartModal=async()=>({});
      openAssignPreview=async(b,scope,label,info)=>{globalThis.info=cloneJson(info); globalThis.rows=diffAssignments(b,scope).rows; return ${confirmed};};`);
    await run('generateScheduleByRules()');
    const info = plain(run('info'));
    const moved = info.cleared.find(x => x.k === NOV_RULE);
    assert.ok(moved, '옛 RULE21 금요일이 지운 목록에 나온다');
    assert.doesNotMatch(moved.why, /다시 지정/, '옮겨진 날은 다시 지정으로 적지 않는다');
    const rowKeys = plain(run('rows')).map(r => r.k);
    assert.ok(rowKeys.includes(NOV_RULE) && rowKeys.includes(NOV_EARLIER), '미리보기에 두 날짜가 모두 나온다');
    const s = state(run);
    if (!confirmed) {
      assert.deepEqual(s.noDuty, before.noDuty, '취소하면 휴무가 그대로');
      assert.deepEqual(s.comments, before.comments, '취소하면 메모가 그대로');
      assert.deepEqual(s.schedules, before.schedules, '취소하면 배정이 그대로');
      assert.equal(run('typeof savedPayload'), 'undefined');
      assert.equal(run('suspendAutoRefresh'), false);
    } else {
      assert.equal(s.noDuty[NOV_EARLIER], 'RULE21');
      assert.equal(s.noDuty[NOV_RULE], undefined);
      assert.deepEqual(plain(run('savedPayload.noDuty')), s.noDuty);
    }
  }
});

test('fresh rebuild cancelled at the rotation start window restores the cleared rule tags', async () => {
  const base = familyApp();
  await generate(base, 2026, 9);
  const before = state(base);
  const run = familyApp({noDuty: before.noDuty, comments: before.comments});
  run(`scheduleMap=${JSON.stringify(before.schedules)}; curYear=2026; curMonth=9; rememberCommittedState();
    openScopeModal=async()=>({scope:'MONTH', fresh:true, familyReset:false});
    openRotationStartModal=async()=>null;`);
  await run('generateScheduleByRules()');
  assert.deepEqual(state(run), before);
});

test('fresh rebuild that must stop (all Fridays holidays) restores the cleared rule tags', async () => {
  const run = familyApp({noDuty: {[NOV_RULE]: 'RULE21'}, comments: {[NOV_RULE]: '패밀리데이'}});
  run(`customHolidays={'2026-11-20':{name:'가'},'2026-11-13':{name:'나'},'2026-11-06':{name:'다'}}; mergeHolidays();`);
  await generate(run, 2026, 11, {fresh: true});
  const s = state(run);
  assert.match(run('lastToast.message'), /중단/);
  assert.deepEqual(s.noDuty, {[NOV_RULE]: 'RULE21'});
  assert.deepEqual(s.comments, {[NOV_RULE]: '패밀리데이'});
});
