'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const html = fs.readFileSync(path.resolve(__dirname, '../../frontend/dist/index.html'), 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(m => m[1]).find(s => s.includes('function renderMyNameUI()'));

function app(mode, schedules) {
  const nodes = new Map();
  function element(id) {
    if (!nodes.has(id)) {
      const classes = new Set();
      nodes.set(id, {style: {}, value: id === 'myduty-year' ? '2026' : '', innerHTML: '', textContent: '',
        classList: {add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x)}});
    }
    return nodes.get(id);
  }
  const storage = {getItem: () => null, setItem: () => {}, removeItem: () => {}};
  const ctx = vm.createContext({Date, console, sessionStorage: storage, localStorage: storage,
    document: {addEventListener: () => {}, getElementById: element}, window: {addEventListener: () => {}},
    setTimeout, clearTimeout, setInterval, clearInterval});
  vm.runInContext(script, ctx);
  const run = code => vm.runInContext(code, ctx);
  run(`SYSTEM_MODE=${JSON.stringify(mode)}; myName='김민수'; todayStr='2026-10-07'; curYear=2026;
    scheduleMap=${JSON.stringify(schedules)}; noDutyMap={'2026-10-16':true};
    commentMap={'2026-10-08':'시험 메모'}; finalHolidays={};
    goToDate=k=>{globalThis.jumpedTo=k;}; renderMyNameUI();`);
  return {run, element};
}
const mine = [{name:'김민수',role:'당직'}, {name:'이서연',role:'당직'}];
for (const mode of ['ADMIN', 'VIEWER']) {
  // Clicking the summary must open the existing full list, never skip it for a single date.
  test(`${mode}: next duty opens sorted personal assignments without navigating`, () => {
    const a = app(mode, {'2026-11-02':mine, '2026-10-01':mine, '2026-10-08':mine,
      '2026-10-16':mine, '2026-10-09':[{name:'다른사람'}], '2027-01-04':mine});
    const before = a.run('JSON.stringify(scheduleMap)');
    a.element('my-next-duty').onclick();
    assert.ok(a.element('myduty-modal').classList.contains('active'));
    assert.equal(a.run('typeof jumpedTo'), 'undefined');
    const rows = a.element('myduty-tbody').innerHTML;
    assert.deepEqual([...rows.matchAll(/<td>(\d{4}-\d{2}-\d{2})/g)].map(m => m[1]),
      ['2026-10-01', '2026-10-08', '2026-11-02']);
    assert.match(rows, /이서연/);
    assert.match(rows, /시험 메모/);
    assert.equal((rows.match(/myd-next/g) || []).length, 1);
    assert.equal(a.run('JSON.stringify(scheduleMap)'), before);
    a.element('myduty-year').value='2027';
    a.run('renderMyDuty()');
    assert.match(a.element('myduty-tbody').innerHTML, /2027-01-04/);
    a.run('closeMyDutyModal()');
    assert.equal(a.element('myduty-modal').classList.contains('active'), false);
  });
}
test('past assignments remain reachable when there is no next duty', () => {
  const a = app('VIEWER', {'2026-10-01':mine});
  assert.match(a.element('my-next-duty').innerHTML, /예정된 당직이 없습니다/);
  assert.equal(typeof a.element('my-next-duty').onclick, 'function');
  a.element('my-next-duty').onclick();
  assert.ok(a.element('myduty-modal').classList.contains('active'));
  assert.match(a.element('myduty-tbody').innerHTML, /2026-10-01/);
});
test('an empty personal schedule opens the existing empty-state list', () => {
  const a = app('ADMIN', {});
  assert.equal(typeof a.element('my-next-duty').onclick, 'function');
  a.element('my-next-duty').onclick();
  assert.ok(a.element('myduty-modal').classList.contains('active'));
  assert.match(a.element('myduty-tbody').innerHTML, /2026년 배정된 당직이 없습니다/);
});
test('clearing my name hides the personal summary', () => {
  const a=app('VIEWER', {'2026-10-08':mine});
  a.run("myName=''; renderMyNameUI();");
  assert.equal(a.element('my-next-duty').style.display, 'none');
});

// [위험] '내 다음 당직' 한 줄이 엉뚱한 날짜를 가리키면 근무자가 당직을 놓친다.
//   휴무로 지정된 날(noDutyMap)의 옛 배정은 건너뛰고, 오늘 당직이면 '오늘', 아니면 D-n 이다.
test('next duty skips no-duty days and past dates and shows D-n', () => {
  // todayStr=2026-10-07, 2026-10-16 is a no-duty day in app()
  const a = app('VIEWER', {'2026-10-01':mine, '2026-10-16':mine, '2026-10-20':mine, '2026-10-09':[{name:'다른사람'}]});
  assert.deepEqual(JSON.parse(a.run("JSON.stringify(nextDutyOf('김민수'))")), {k:'2026-10-20', diff:13});
  const html = a.element('my-next-duty').innerHTML;
  assert.match(html, /<b>2026-10-20 \(화\)<\/b> · <b>D-13<\/b>/);
  assert.match(a.element('my-next-duty').title, /2026-10-20 \(화\) · D-13/);
});
test('next duty today reads 오늘, tomorrow reads D-1', () => {
  const today = app('ADMIN', {'2026-10-07':mine, '2026-10-08':mine});
  assert.deepEqual(JSON.parse(today.run("JSON.stringify(nextDutyOf('김민수'))")), {k:'2026-10-07', diff:0});
  assert.match(today.element('my-next-duty').innerHTML, /<b>2026-10-07 \(수\)<\/b> · <b>오늘<\/b>/);
  const tomorrow = app('ADMIN', {'2026-10-08':mine});
  assert.match(tomorrow.element('my-next-duty').innerHTML, /<b>2026-10-08 \(목\)<\/b> · <b>D-1<\/b>/);
});
test('next duty counts calendar days across a month and year boundary', () => {
  const a = app('VIEWER', {'2027-01-04':mine});
  assert.deepEqual(JSON.parse(a.run("JSON.stringify(nextDutyOf('김민수'))")), {k:'2027-01-04', diff:89});
  assert.match(a.element('my-next-duty').innerHTML, /D-89/);
  assert.equal(a.run("nextDutyOf('')"), null);
  assert.equal(a.run("nextDutyOf('없는사람')"), null);
});
