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

for (const mode of ['ADMIN', 'VIEWER']) test(`${mode}: person absence details include full periods and other schedules`, () => {
 const a=app(mode,{});
 a.run(`absences=[{name:'김민수',type:'교육',start:'2026-10-12',end:'2026-10-16'},{name:'이서연',type:'연차',start:'2026-10-13',end:'2026-10-13'},{name:'김민수',type:'연차',start:'2026-11-02',end:'2026-11-03'}]`);
 const before=a.run('JSON.stringify(absences)');
 assert.equal(a.run('typeof openPersonAbsenceModal'),'function');
 a.run("openPersonAbsenceModal('김민수','2026-10-13')");
 const content=a.element('person-absence-list').innerHTML;
 for(const text of ['교육','2026-10-12','2026-10-16','5일','선택한 날짜의 일정','연차','2026-11-02']) assert.ok(content.includes(text),text);
 assert.ok(!content.includes('이서연'));
 assert.ok(a.element('person-absence-modal').classList.contains('active'));
 a.run('closePersonAbsenceModal()');
 assert.ok(!a.element('person-absence-modal').classList.contains('active'));
 assert.equal(a.run('JSON.stringify(absences)'),before);
});
test('empty records and unsafe content are handled safely across year boundaries',()=>{
 const a=app('VIEWER',{});assert.equal(a.run('typeof openPersonAbsenceModal'),'function');
 a.run("openPersonAbsenceModal('없음','2026-10-13')");
 assert.match(a.element('person-absence-list').innerHTML,/등록된 일정이 없습니다/);
 a.run(`absences=[{name:'<김>',type:'<img src=x onerror=alert(1)>',start:'2026-12-31',end:'2027-01-02'}];openPersonAbsenceModal('<김>','2027-01-01');`);
 const content=a.element('person-absence-list').innerHTML;
 assert.ok(content.includes('&lt;img'));assert.ok(!content.includes('<img'));assert.ok(content.includes('3일'));
});
