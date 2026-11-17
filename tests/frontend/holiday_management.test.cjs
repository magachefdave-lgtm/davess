'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'frontend/dist/index.html'), 'utf8');
const main = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m => m[1]).find(s => s.includes('function friday21Of('));
const admin = fs.readFileSync(path.join(root, 'frontend/dist/usability_admin.js'), 'utf8');
const holiday = fs.readFileSync(path.join(root, 'frontend/dist/holiday_management.js'), 'utf8');
function node(id) {
  const classes = new Set();
  return {id, value: '', checked: false, disabled: false, hidden: false, innerHTML: '', textContent: '', style: {}, dataset: {}, listeners: {}, options: [],
    classList: {add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c), toggle(c, force) {const on = force === undefined ? !classes.has(c) : force; if (on) classes.add(c); else classes.delete(c); return on;}},
    add(option) {this.options.push(option);}, addEventListener(type, handler) {this.listeners[type] = handler;},
    setAttribute(key, value) {this[key] = value;}, getAttribute(key) {return this[key];}, removeAttribute(key) {delete this[key];},
    querySelector: () => null, querySelectorAll: () => [], appendChild() {}, insertBefore() {}, remove() {}, focus() {this.focused = true;}};
}
function app(setup = '') {
  const nodes = new Map(), get = id => {if (!nodes.has(id)) nodes.set(id, node(id)); return nodes.get(id);};
  const storage = {getItem: () => null, setItem() {}, removeItem() {}};
  const context = vm.createContext({Date, console, JSON, Math, Promise, localStorage: storage, sessionStorage: storage, navigator: {},
    document: {getElementById: get, querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, createElement: tag => node(tag), body: node('body'), createTextNode: value => ({textContent: value})},
    Option: function(text, value) {this.text = text; this.value = value;},
    setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {}, requestAnimationFrame() {}, addEventListener() {}, scrollTo() {},
    confirm: () => {throw Error('Native confirm is not used');}, alert() {}});
  vm.runInContext('window=globalThis', context);
  vm.runInContext(main, context);
  const run = code => vm.runInContext(code, context);
  run(`SYSTEM_MODE='ADMIN'; SERVER_CONNECTED=true; dataLoaded=true; todayStr='2026-11-05'; curYear=2026; curMonth=11; dataVersion=1;
    globalThis.saved=[]; globalThis.toasts=[]; globalThis.answers=[]; globalThis.asked=[];
    showToast=(message,error)=>toasts.push({message,error}); renderCalendar=()=>{}; renderConnState=()=>{};
    syncFullDataToBackend=async p=>{saved.push(JSON.parse(JSON.stringify(p)));dataVersion++;return true;};
    mergeHolidays();`);
  vm.runInContext(admin, context);
  run(`OnAirAdmin.askHook=opts=>{asked.push(opts); return answers.length?answers.shift():null;};${setup}`);
  vm.runInContext(holiday, context);
  return {run, get, plain: code => JSON.parse(JSON.stringify(run(code)))};
}
test('calendar and year navigation leave the main month unchanged and cross year safely', () => {
  const a = app(); a.run('openHolidayModal()');
  assert.equal(a.get('hol-date').value, '2026-11-05');
  a.run('OnAirHolidayManagement.moveMonth(2)');
  assert.deepEqual(a.plain('[curYear,curMonth]'), [2026,11]);
  assert.equal(a.get('hol-month-label').textContent, '2027년 1월');
  assert.equal(a.get('hol-year').value, '2027');
  assert.equal(a.get('hol-date').value, '2026-11-05', 'Browsing does not silently discard the editor');
  a.get('hol-year').value = '2025'; a.run('OnAirHolidayManagement.changeYear()');
  assert.equal(a.get('hol-month-label').textContent, '2025년 1월');
  assert.match(a.get('hol-calendar-grid').innerHTML, /data-hol-date="2025-01-01"/);
});
test('calendar date selection loads a holiday; base removals and overrides keep source semantics', async () => {
  const a = app(`customHolidays={'2026-10-09':{name:'한글날',isMajor:false,isRemoved:true},'2026-11-20':{name:'검증용 기념일',isMajor:true,isRemoved:false}};mergeHolidays();`);
  a.run('openHolidayModal()');
  await a.run(`OnAirHolidayManagement.select('2026-11-20')`);
  assert.equal(a.get('hol-name').value, '검증용 기념일'); assert.equal(a.get('hol-major').checked, true);
  assert.equal(a.get('hol-selected-kind').textContent, '직접 지정');
  assert.equal(a.get('hol-submit').textContent, '변경 반영');
  await a.run(`OnAirHolidayManagement.select('2026-10-09')`);
  assert.equal(a.get('hol-selected-kind').textContent, '해제됨');
  assert.match(a.get('hol-tbody').innerHTML, /restoreBaseHoliday\('2026-10-09'\)/);
  assert.equal(a.run('OnAirHolidayManagement._test.dateOf("2026-02-30")'), null);
  assert.equal(a.run('OnAirHolidayManagement._test.label("2026-11-20")'), '2026.11.20 (금)');
});
test('search and pages reveal every matching record including removed holidays', () => {
  const a = app(`customHolidays=Object.fromEntries(Array.from({length:12},(_,i)=>['2026-11-'+String(i+10).padStart(2,'0'),{name:'특별휴일 '+i,isMajor:false,isRemoved:false}]));mergeHolidays();`);
  a.run('openHolidayModal()'); a.get('hol-search').value = '특별휴일'; a.get('hol-search').listeners.input();
  assert.equal(a.get('hol-list-count').textContent, '12건'); assert.equal(a.get('hol-page-label').textContent, '1 / 3');
  a.get('hol-next-page').listeners.click(); a.get('hol-next-page').listeners.click();
  assert.equal(a.get('hol-range').textContent, '11–12 / 12건'); assert.equal(a.get('hol-next-page').disabled, true);
  a.get('hol-search').value = '없는 이름'; a.get('hol-search').listeners.input();
  assert.match(a.get('hol-tbody').innerHTML, /표시할 공휴일이 없습니다/); assert.equal(a.get('hol-page-label').textContent, '1 / 1');
});
test('loaded holiday closes without false dirty warning; edited name guards selection and close', async () => {
  const a = app(); a.run('openHolidayModal()'); await a.run(`OnAirHolidayManagement.select('2026-10-09')`);
  a.run('closeHolidayModal()'); assert.equal(a.get('holiday-modal').classList.contains('active'), false); assert.equal(a.run('asked.length'), 0);
  a.run('openHolidayModal()'); a.get('hol-name').value = '저장 전 입력';
  assert.equal(await a.run(`OnAirHolidayManagement.select('2026-11-21')`), false);
  assert.equal(a.get('hol-name').value, '저장 전 입력'); assert.equal(a.get('hol-date').value, '2026-11-05');
  a.run('answers.push(true)'); assert.equal(await a.run(`OnAirHolidayManagement.select('2026-11-21')`), true);
  assert.equal(a.get('hol-name').value, '');
});
test('new calendar editor persists through existing save and success action; failed save retains draft', async () => {
  const a = app(); a.run('openHolidayModal()'); await a.run(`OnAirHolidayManagement.select('2026-11-20')`);
  a.get('hol-name').value = '검증용 휴일'; a.get('hol-major').checked = true;
  assert.equal(await a.run('addCustomHoliday()'), true);
  assert.deepEqual(a.plain(`saved[0].customHolidays['2026-11-20']`), {name:'검증용 휴일',isMajor:true,isRemoved:false});
  assert.equal(a.get('hol-name').value, '검증용 휴일'); assert.equal(a.run('OnAirHolidayManagement._test.dirty()'), false);
  assert.equal(a.run('OnAirAdmin._test.state.lastActionToast.actions[0].label'), '이 달 다시 배정 미리보기');
  a.get('hol-name').value = '실패 시 보존'; a.run('syncFullDataToBackend=async()=>false');
  assert.equal(await a.run('addCustomHoliday()'), false); assert.equal(a.get('hol-name').value, '실패 시 보존');
  assert.equal(a.run('OnAirHolidayManagement._test.dirty()'), true); assert.equal(a.run('OnAirHolidayManagement.state.saving'), false);
});
test('selected record changed by another client is not overwritten from a stale editor', async () => {
  const a = app(`customHolidays={'2026-11-20':{name:'기존',isMajor:false,isRemoved:false}};mergeHolidays();`);
  a.run('openHolidayModal()'); await a.run(`OnAirHolidayManagement.select('2026-11-20')`); a.get('hol-name').value = '내 변경';
  a.run(`customHolidays['2026-11-20']={name:'다른 PC 변경',isMajor:false,isRemoved:false};mergeHolidays();`);
  assert.equal(await a.run('addCustomHoliday()'), false); assert.equal(a.run('saved.length'), 0); assert.equal(a.get('hol-name').value, '내 변경');
  assert.match(a.run('toasts.at(-1).message'), /다른 PC/);
});
test('VIEWER, offline and closed-month restrictions stay in force', async () => {
  const a = app(); a.run('openHolidayModal()'); await a.run(`OnAirHolidayManagement.select('2026-11-20')`); a.get('hol-name').value = '저장 금지';
  a.run('SYSTEM_MODE="VIEWER"'); assert.equal(await a.run('addCustomHoliday()'), false); assert.equal(a.run('saved.length'), 0);
  a.run('SYSTEM_MODE="ADMIN"; SERVER_CONNECTED=false'); assert.equal(await a.run('addCustomHoliday()'), false); assert.equal(a.get('hol-name').value, '저장 금지');
  a.run('SERVER_CONNECTED=true; closedMonths=["2026-11"]'); await a.run('addCustomHoliday()'); assert.equal(a.run('saved.length'), 0);
  assert.equal(a.get('hol-name').value, '저장 금지');
});
test('duplicate saves are blocked until the current save settles', async () => {
  const a = app(); a.run('openHolidayModal()'); await a.run(`OnAirHolidayManagement.select('2026-11-20')`); a.get('hol-name').value = '한 번만';
  a.run('syncFullDataToBackend=p=>{saved.push(p);return new Promise(resolve=>globalThis.finishSave=resolve)}');
  const first = a.run('addCustomHoliday()'); assert.equal(await a.run('addCustomHoliday()'), false); assert.equal(a.run('saved.length'), 1);
  a.run('finishSave(true)'); assert.equal(await first, true); assert.equal(a.run('OnAirHolidayManagement.state.saving'), false);
});
test('pending discard confirmation prevents duplicate mutations and selection changes', async () => {
  const a = app(`customHolidays={'2026-11-20':{name:'기존 휴일',isMajor:false,isRemoved:false}};mergeHolidays();`);
  a.run('openHolidayModal()'); await a.run(`OnAirHolidayManagement.select('2026-11-20')`); a.get('hol-name').value = '저장하지 않은 이름';
  a.run('OnAirAdmin.confirmDiscard=()=>new Promise(resolve=>globalThis.finishDiscard=resolve)');
  const first = a.run(`delCustomHoliday('2026-11-20')`);
  assert.equal(await a.run(`delCustomHoliday('2026-11-20')`), false);
  assert.equal(await a.run('addCustomHoliday()'), false);
  assert.equal(await a.run(`OnAirHolidayManagement.select('2026-11-21')`), false);
  assert.equal(a.run('closeHolidayModal()'), false);
  a.run('finishDiscard(true)'); assert.equal(await first, true);
  assert.equal(a.run('saved.length'), 1); assert.equal(a.run('OnAirHolidayManagement.state.selecting'), false);
});
test('incomplete keyboard date input stays editable; complete date commits on blur', async () => {
  const a = app(); a.run('openHolidayModal()');
  a.get('hol-date').value = '0002-11-05';
  assert.equal(a.get('hol-date').listeners.change, undefined, 'Intermediate change does not reset the field');
  assert.equal(await a.get('hol-date').listeners.blur(), false);
  assert.equal(a.get('hol-date').value, '0002-11-05'); assert.equal(a.get('hol-date')['aria-invalid'], 'true');
  a.get('hol-date').value = '2027-11-05'; assert.equal(await a.get('hol-date').listeners.blur(), true);
  assert.equal(a.get('hol-date').value, '2027-11-05'); assert.equal(a.get('hol-month-label').textContent, '2027년 11월');
  assert.equal(a.get('hol-date')['aria-invalid'], undefined); assert.deepEqual(a.plain('[curYear,curMonth]'), [2026,11]);
});
test('a replacement holiday received during discard confirmation is not deleted', async () => {
  const a = app(`customHolidays={'2026-11-20':{name:'기존 휴일',isMajor:false,isRemoved:false}};mergeHolidays();`);
  a.run('openHolidayModal()'); await a.run(`OnAirHolidayManagement.select('2026-11-20')`); a.get('hol-name').value = '저장하지 않은 이름';
  a.run('OnAirAdmin.confirmDiscard=()=>new Promise(resolve=>globalThis.finishDiscard=resolve)');
  const pending = a.run(`delCustomHoliday('2026-11-20')`);
  a.run(`customHolidays['2026-11-20']={name:'다른 PC의 최신 휴일',isMajor:false,isRemoved:false};dataVersion++;mergeHolidays();finishDiscard(true);`);
  assert.equal(await pending, false); assert.equal(a.run('saved.length'), 0);
  assert.equal(a.run(`customHolidays['2026-11-20'].name`), '다른 PC의 최신 휴일');
  assert.equal(a.get('hol-name').value, '저장하지 않은 이름'); assert.match(a.run('toasts.at(-1).message'), /확인하는 동안/);
});
