'use strict';
// Holiday settings change between auto-assignment runs in the same loaded app.
// CRUD persistence and preview confirmation are exercised separately in Chromium.
const assert = require('node:assert/strict');
const {test} = require('node:test');
const {app, plain, generate} = require('./helpers/scheduler.cjs');

const workers = (schedule, key) => (schedule[key] || []).map(w => w.name);
const snapshot = run => plain(run('({schedules:scheduleMap,noDuty:noDutyMap,comments:commentMap,rules:dutyRule})'));

test('weekday holiday addition and deletion reclassify an existing month without a reload or manual merge', async () => {
  const run = app();
  const baseline = await generate(run, 2027, 11);
  assert.deepEqual(workers(baseline, '2027-11-10'), ['F2']);
  run(`customHolidays['2027-11-10']={name:'검증용 임시공휴일',isMajor:false};`);
  const added = await generate(run, 2027, 11);
  assert.deepEqual(workers(added, '2027-11-10'), ['M3','F3']);
  assert.ok(added['2027-11-10'].every(w => w.role.startsWith('공휴일')));
  assert.deepEqual(workers(added, '2027-11-11'), ['F2'], 'weekday pointer is not consumed by a weekday holiday');
  run(`delete customHolidays['2027-11-10'];`);
  assert.deepEqual(await generate(run, 2027, 11), baseline, 'removing the custom day restores the original month');
});

for (const key of ['2027-11-13','2027-11-14']) {
  test(`live weekend holiday ${key} retains weekend fixed order, and deletion restores its roles`, async () => {
    const run = app();
    const baseline = await generate(run, 2027, 11);
    run(`customHolidays['${key}']={name:'주말 임시공휴일',isMajor:false};`);
    const added = await generate(run, 2027, 11);
    assert.deepEqual(workers(added, key), workers(baseline, key));
    for (const day of Object.keys(baseline)) assert.deepEqual(workers(added, day), workers(baseline, day), day);
    assert.equal(run(`slotCategory('${key}')`), 'weekend');
    run(`delete customHolidays['${key}'];`);
    assert.deepEqual(await generate(run, 2027, 11), baseline);
  });
}

test('built-in weekday holiday suppression and restoration replace previously generated slots', async () => {
  const run = app();
  const baseline = await generate(run, 2027, 5);
  assert.equal(baseline['2027-05-03'].length, 2);
  run(`customHolidays['2027-05-03']={name:'대체공휴일',isMajor:false,isRemoved:true};`);
  const removed = await generate(run, 2027, 5);
  assert.deepEqual(workers(removed, '2027-05-03'), ['F1']);
  assert.match(removed['2027-05-03'][0].role, /^평일/);
  assert.equal(run(`customHolidays['2027-05-03'].isRemoved`), true, 'assignment preserves explicit holiday suppression');
  run(`delete customHolidays['2027-05-03'];`);
  assert.deepEqual(await generate(run, 2027, 5), baseline);
});

test('deleting an override on a built-in day restores the built-in holiday instead of making it a weekday', async () => {
  const run = app();
  run(`customHolidays['2027-05-03']={name:'이름 변경',isMajor:false};`);
  await generate(run, 2027, 5);
  run(`delete customHolidays['2027-05-03'];`);
  const restored = await generate(run, 2027, 5);
  assert.equal(restored['2027-05-03'].length, 2);
  assert.equal(run(`finalHolidays['2027-05-03'].name`), run(`BASE_HOLIDAYS['2027-05-03'].name`));
  assert.equal(run(`slotCategory('2027-05-03')`), 'holiday');
});

test('festival main-day suppression removes obsolete no-duty and restoration recreates its same-team block', async () => {
  const run = app();
  const baseline = await generate(run, 2027, 2);
  assert.deepEqual(baseline['2027-02-07'], []);
  assert.equal(run(`noDutyMap['2027-02-07']`), 'RULE_MAJOR_DAY');
  run(`customHolidays['2027-02-07']={name:'설날',isMajor:true,isRemoved:true};`);
  const removed = await generate(run, 2027, 2);
  assert.equal(removed['2027-02-07'].length, 2, 'removed festival main day is still Sunday');
  assert.equal(run(`noDutyMap['2027-02-07']`), undefined);
  assert.equal(run(`slotCategory('2027-02-07')`), 'weekend');
  run(`delete customHolidays['2027-02-07'];`);
  const restored = await generate(run, 2027, 2);
  assert.deepEqual(restored, baseline);
  for (const key of ['2027-02-08','2027-02-09']) assert.deepEqual(workers(restored,key), workers(restored,'2027-02-06'));
});

test('turning a festival main day into an ordinary holiday and back refreshes duty and no-duty classification', async () => {
  const run = app();
  await generate(run, 2027, 2);
  run(`customHolidays['2027-02-07']={name:'설날',isMajor:false};`);
  const ordinary = await generate(run, 2027, 2);
  assert.equal(ordinary['2027-02-07'].length, 2);
  assert.equal(run(`noDutyMap['2027-02-07']`), undefined);
  run(`customHolidays['2027-02-07'].isMajor=true;`);
  const major = await generate(run, 2027, 2);
  assert.deepEqual(major['2027-02-07'], []);
  assert.equal(run(`noDutyMap['2027-02-07']`), 'RULE_MAJOR_DAY');
});

test('custom three-day festival uses one team around its core and disappears cleanly on deletion', async () => {
  const run = app();
  const baseline = await generate(run, 2027, 11);
  run(`customHolidays={'2027-11-08':{name:'설 연휴',isMajor:true},'2027-11-09':{name:'설날',isMajor:true},'2027-11-10':{name:'설 연휴',isMajor:true}};`);
  const festival = await generate(run, 2027, 11);
  assert.deepEqual(workers(festival,'2027-11-08'), ['M1','F1']);
  assert.deepEqual(festival['2027-11-09'], []);
  assert.deepEqual(workers(festival,'2027-11-10'), workers(festival,'2027-11-08'));
  run('customHolidays={};');
  assert.deepEqual(await generate(run, 2027, 11), baseline);
  assert.equal(run(`noDutyMap['2027-11-09']`), undefined);
});

test('live holiday addition uses pre-month holiday history, keeps earlier dates and retains roster order', async () => {
  const run = app();
  run(`scheduleMap={'2027-10-05':[{name:'M3',role:'공휴일(남)'},{name:'F3',role:'공휴일(여)'}]};
    customHolidays['2027-10-05']={name:'전월 검증 휴일'};`);
  const roster = plain(run('dutyRule.fixedOrders'));
  const before = await generate(run, 2027, 11);
  run(`customHolidays['2027-11-10']={name:'다음 순번 검증 휴일'};`);
  const changed = await generate(run, 2027, 11);
  assert.deepEqual(workers(changed,'2027-11-10'), ['M2','F2']);
  assert.deepEqual(changed['2027-10-05'], before['2027-10-05']);
  assert.deepEqual(plain(run('dutyRule.fixedOrders')), roster);
  run(`delete customHolidays['2027-11-10'];`);
  assert.deepEqual(await generate(run, 2027, 11), before);
});

test('live holiday addition skips absent workers and does not rewrite fixed rosters', async () => {
  const run = app();
  const rosters = plain(run('dutyRule.fixedOrders'));
  await generate(run, 2027, 11);
  run(`customHolidays['2027-11-10']={name:'부재 검증 휴일'};
    absences=[{id:1,name:'M3',type:'연차',start:'2027-11-10',end:'2027-11-10'},{id:2,name:'F3',type:'교육',start:'2027-11-10',end:'2027-11-10'}];`);
  const absent = await generate(run, 2027, 11);
  assert.deepEqual(workers(absent,'2027-11-10'), ['M2','F2']);
  assert.deepEqual(plain(run('dutyRule.fixedOrders')), rosters);
  run('absences=[];');
  const returned = await generate(run, 2027, 11);
  assert.deepEqual(workers(returned,'2027-11-10'), ['M3','F3']);
  assert.deepEqual(plain(run('dutyRule.fixedOrders')), rosters);
});

for (const gender of ['M','F']) {
  test(`live weekday holiday shares only the empty ${gender} holiday roster with weekends`, async () => {
    const run = app();
    run(`dutyRule.fixedOrders.holiday${gender}=[];`);
    const baseline = await generate(run, 2027, 11);
    run(`customHolidays['2027-11-10']={name:'공유 순번 검증'};`);
    const added = await generate(run, 2027, 11);
    const name = k => added[k].find(w=>w.name.startsWith(gender)).name;
    assert.equal(name('2027-11-10'), gender+'3', 'continues after first Saturday and Sunday');
    assert.equal(name('2027-11-13'), gender+'1', 'following weekend sees the holiday turn');
    const independent = gender === 'M' ? 'F' : 'M';
    assert.equal(added['2027-11-13'].find(w=>w.name.startsWith(independent)).name, independent+'3');
    run(`delete customHolidays['2027-11-10'];`);
    assert.deepEqual(await generate(run, 2027, 11), baseline);
  });
}

test('new holiday on an applied family day is preserved normally, moved by fresh, and restored when deleted', async () => {
  const run = app();run('dutyRule.rule21FridayOff=true;');
  const baseline = await generate(run, 2026, 11);
  run(`customHolidays['2026-11-20']={name:'임시공휴일'};`);
  const normal = await generate(run, 2026, 11);
  assert.deepEqual(normal['2026-11-20'], []);
  assert.equal(run(`noDutyMap['2026-11-20']`), 'RULE21');
  const fresh = await generate(run, 2026, 11, {fresh:true});
  assert.equal(fresh['2026-11-20'].length, 2);
  assert.deepEqual(fresh['2026-11-13'], []);
  assert.equal(run(`noDutyMap['2026-11-13']`), 'RULE21');
  run(`delete customHolidays['2026-11-20'];`);
  const removed = await generate(run, 2026, 11);
  assert.deepEqual(removed, baseline);
  assert.equal(run(`noDutyMap['2026-11-20']`), 'RULE21');
  assert.equal(run(`noDutyMap['2026-11-13']`), undefined);
  assert.ok(!String(run(`commentMap['2026-11-13']`) || '').includes('패밀리데이'));
});

test('suppressing Christmas frees the base Friday; restoration keeps that applied day until fresh recalculates it', async () => {
  const run = app();run('dutyRule.rule21FridayOff=true;');
  const baseline = await generate(run, 2026, 12);
  assert.equal(run(`noDutyMap['2026-12-18']`), 'RULE21');
  assert.equal(baseline['2026-12-25'].length, 2);
  run(`customHolidays['2026-12-25']={name:'성탄절',isMajor:false,isRemoved:true};`);
  const removed = await generate(run, 2026, 12);
  assert.equal(run(`noDutyMap['2026-12-18']`), undefined);
  assert.equal(run(`noDutyMap['2026-12-25']`), 'RULE21');
  assert.deepEqual(removed['2026-12-25'], []);
  assert.equal(removed['2026-12-18'].length, 1);
  run(`delete customHolidays['2026-12-25'];`);
  const restoredNormally = await generate(run, 2026, 12);
  assert.equal(run(`noDutyMap['2026-12-25']`), 'RULE21');
  assert.deepEqual(restoredNormally['2026-12-25'], []);
  const fresh = await generate(run, 2026, 12, {fresh:true});
  assert.equal(run(`noDutyMap['2026-12-18']`), 'RULE21');
  assert.equal(run(`noDutyMap['2026-12-25']`), undefined);
  assert.deepEqual(fresh, baseline);
});

test('family reset recalculates manual family day while ordinary fresh preserves it', async () => {
  const run = app();
  run(`dutyRule.rule21FridayOff=true;noDutyMap['2026-11-19']=true;commentMap['2026-11-19']='패밀리데이';customHolidays['2026-11-20']={name:'임시공휴일'};`);
  const fresh = await generate(run, 2026, 11, {fresh:true});
  assert.deepEqual(fresh['2026-11-19'], []);
  assert.equal(run(`noDutyMap['2026-11-19']`), true);
  const reset = await generate(run, 2026, 11, {familyReset:true});
  assert.equal(reset['2026-11-19'].length, 1);
  assert.deepEqual(reset['2026-11-13'], []);
  assert.equal(run(`noDutyMap['2026-11-13']`), 'RULE21');
  assert.equal(run(`noDutyMap['2026-11-19']`), undefined);
});

test('all candidate Fridays becoming holidays stops fresh assignment and preserves the existing schedule', async () => {
  const run = app();run('dutyRule.rule21FridayOff=true;dutyRule.familyDayOffMonths=[];');
  await generate(run, 2026, 11);
  const before = snapshot(run);
  run(`customHolidays={'2026-11-20':{name:'휴일'},'2026-11-13':{name:'휴일'},'2026-11-06':{name:'휴일'}};globalThis.savedPayload=undefined;`);
  await generate(run, 2026, 11, {fresh:true});
  assert.deepEqual(snapshot(run), before);
  assert.equal(run('typeof savedPayload'), 'undefined');
  assert.match(run('lastToast.message'), /자동 배정을 중단/);
});
