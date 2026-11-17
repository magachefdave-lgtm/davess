'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const html = fs.readFileSync(path.resolve(__dirname, '../../frontend/dist/index.html'), 'utf8');
const appScript = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(m => m[1]).find(s => s.includes('function renderMyNameUI()'));
const extensionPath = path.resolve(__dirname, '../../frontend/dist/usability_admin.js');
const extension = fs.existsSync(extensionPath) ? fs.readFileSync(extensionPath, 'utf8') : '';

function app(history = []) {
  const nodes = new Map(), created = [], copied = [];
  let ctx;
  function node(id = '') {
    const classes = new Set();
    const value = {id, style: {}, dataset: {}, value: '', textContent: '', innerHTML: '', children: [],
      classList: {add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x)},
      setAttribute(name, val) { this[name] = val; },
      appendChild(child) { this.children.push(child); return child; },
      insertBefore(child) { this.children.push(child); return child; },
      addEventListener(name, fn) { this['on' + name] = fn; },
      focus(options) { this.focusOptions = options; ctx.document.activeElement = this; },
      select() { copied.push(this.value); }, remove() { this.removed = true; },
      querySelector(selector) {
        if (id === 'history-modal' && selector === '.modal-body') return element('history-body');
        if (id === 'history-body' && selector === '.table-scroll') return element('history-scroll');
        if (selector === '[data-copy-change]') return element('copy-button');
        if (selector === '[data-copy-status]') return element('copy-status');
        return null;
      },
      querySelectorAll() {
        return [...this.innerHTML.matchAll(/data-history-index="(\d+)"/g)].map(m => {
          const row = node(); row.dataset.historyIndex = m[1]; return row;
        });
      }};
    created.push(value); return value;
  }
  function element(id) {
    if (!nodes.has(id)) nodes.set(id, created.find(el => el.id === id) || node(id));
    return nodes.get(id);
  }
  const storage = {getItem: () => null, setItem: () => {}, removeItem: () => {}};
  ctx = vm.createContext({Date, console, sessionStorage: storage, localStorage: storage, navigator: {},
    document: {getElementById: element, createElement: node, addEventListener: () => {},
      body: node('body'), activeElement: null, execCommand: command => command === 'copy'},
    setTimeout, clearTimeout, setInterval, clearInterval, scrollX: 0, scrollY: 200,
    addEventListener: () => {}, scrollTo: (x, y) => { ctx.scrollX = x; ctx.scrollY = y; }});
  vm.runInContext('window=globalThis', ctx);
  ctx.OnAirUI = {escape: value => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'),
    dateLabel: key => String(key), close: () => {},
    open: (id, title, body, options) => { ctx.dialog = {id, title, body, options}; return node('detail-dialog'); }};
  vm.runInContext(appScript, ctx);
  vm.runInContext(`curYear=2026;curMonth=10;changeHistory=${JSON.stringify(history)};`, ctx);
  vm.runInContext(extension, ctx);
  return {ctx, element, copied, created, run: code => vm.runInContext(code, ctx)};
}

const old = {id: 1, timestamp: '2026-10-01 12:30:00', type: '당직 변경', dateKey: '2026-10-17', details: '김민수 → 이서연', host: 'PC1', actor: '관리자'};
const snapshots = {id: 2, timestamp: '2026-10-02 12:30:00', type: '교육 변경', dateKey: '2026-10-19', details: '교육 수정',
  before: {schedules: {'2026-10-19': [{name: '김민수', team: '운영'}]}, absences: [{id: 'a', name: '이서연', type: '교육', start: '2026-10-19', end: '2026-10-20'}]},
  after: {schedules: {'2026-10-19': [{name: '최지우', team: '운영'}]}, absences: []}};

test('search combines person, date and type while retaining month/year/all periods', () => {
  const a = app([old, snapshots, {...old, id: 3, timestamp: '2025-01-01', dateKey: '2025-02-01'}]);
  assert.equal(a.run('typeof OnAirAdmin'), 'object');
  assert.equal(a.run("OnAirAdmin.filterHistory(changeHistory,'MONTH','김민수 2026-10 당직').length"), 1);
  assert.equal(a.run("OnAirAdmin.filterHistory(changeHistory,'YEAR','최지우 교육').length"), 1);
  assert.equal(a.run("OnAirAdmin.filterHistory(changeHistory,'ALL','김민수').length"), 3);
  assert.equal(a.run("OnAirAdmin.filterHistory(changeHistory,'MONTH','없는이름').length"), 0);
});

test('existing history modal retains period controls and searches without adding a main control', () => {
  const a = app([old, {...old, id: 3, timestamp: '2025-01-01', dateKey: '2025-02-01'}]);
  a.run("openHistoryModal('ALL')");
  assert.ok(a.element('history-modal').classList.contains('active'));
  assert.equal(a.created.filter(x => x.id === 'history-search-wrap').length, 1);
  a.element('history-search').value = '2025';
  a.element('history-search').oninput();
  assert.match(a.element('history-tbody').innerHTML, /2025-02-01/);
  assert.doesNotMatch(a.element('history-tbody').innerHTML, /2026-10-17/);
  a.run("openHistoryModal('MONTH')");
  assert.match(a.element('history-tbody').innerHTML, /검색 조건에 맞는/);
  a.run("openHistoryModal('ALL')");
  assert.equal(a.created.filter(x => x.id === 'history-search-wrap').length, 1);
  assert.match(a.element('history-tbody').innerHTML, /tabindex="0"/);
  assert.ok(html.includes("openHistoryModal('YEAR')"));
});

test('recorded schedule and absence snapshots appear as before/after and do not use current state', () => {
  const a = app([snapshots]);
  assert.equal(a.run('typeof OnAirAdmin'), 'object');
  const before = a.run('JSON.stringify(changeHistory)');
  a.run("scheduleMap={'2026-10-19':[{name:'현재 사람'}]};OnAirAdmin.openChange(0)");
  for (const text of ['변경 전', '변경 후', '김민수', '최지우', '이서연', '2026-10-20', '없음']) assert.ok(a.ctx.dialog.body.includes(text), text);
  assert.ok(!a.ctx.dialog.body.includes('현재 사람'));
  assert.equal(a.run('JSON.stringify(changeHistory)'), before);
});

test('legacy text-only history shows its original detail without claiming a comparison', () => {
  const a = app([old]);
  assert.equal(a.run('typeof OnAirAdmin'), 'object');
  a.run('OnAirAdmin.openChange(0)');
  assert.ok(a.ctx.dialog.body.includes('김민수 → 이서연'));
  assert.ok(a.ctx.dialog.body.includes('전후 기록이 없는 과거 이력'));
  assert.ok(!a.ctx.dialog.body.includes('<th>변경 전</th>'));
  assert.equal(a.run('OnAirAdmin.historyNoticeText(changeHistory[0])').includes('변경 전:'), false);
});

test('history details and snapshot values are escaped', () => {
  const unsafe = {...snapshots, details: '<img src=x onerror=alert(1)>', after: {schedules: {'2026-10-19': [{name: '<script>bad</script>'}]}, absences: []}};
  const a = app([unsafe]);
  assert.equal(a.run('typeof OnAirAdmin'), 'object');
  a.run('OnAirAdmin.openChange(0)');
  assert.match(a.ctx.dialog.body, /&lt;img/);
  assert.match(a.ctx.dialog.body, /&lt;script&gt;/);
  assert.doesNotMatch(a.ctx.dialog.body, /<img|<script>/);
});

test('notice copy uses the selected record and clipboard without network or mutation', async () => {
  const a = app([old, snapshots]);
  assert.equal(a.run('typeof OnAirAdmin'), 'object');
  const copied = [];
  a.ctx.navigator.clipboard = {writeText: async text => copied.push(text)};
  a.ctx.fetch = () => { throw Error('copy must not transmit'); };
  const before = a.run('JSON.stringify(changeHistory)');
  assert.equal(await a.run('OnAirAdmin.copyNotice(changeHistory[1])'), true);
  assert.equal(copied.length, 1);
  assert.ok(copied[0].includes('최지우'));
  assert.ok(copied[0].includes('교육 수정'));
  assert.equal(a.run('JSON.stringify(changeHistory)'), before);
});

test('clipboard rejection falls back to copy with focus and scroll preserved', async () => {
  const a = app([old]);
  assert.equal(a.run('typeof OnAirAdmin'), 'object');
  a.ctx.navigator.clipboard = {writeText: async () => { throw Error('denied'); }};
  const focused = a.element('row'); focused.focus();
  assert.equal(await a.run('OnAirAdmin.copyNotice(changeHistory[0])'), true);
  assert.equal(a.copied.length, 1);
  assert.ok(a.copied[0].includes('김민수 → 이서연'));
  assert.equal(a.ctx.document.activeElement, focused);
  assert.equal(a.ctx.scrollY, 200);
  assert.ok(a.created.some(x => x.tag === 'textarea' || x.removed));
});

test('failed copy reports failure so a dialog does not claim that text was copied', async () => {
  const a = app([old]);
  assert.equal(a.run('typeof OnAirAdmin'), 'object');
  a.ctx.document.execCommand = () => false;
  assert.equal(await a.run('OnAirAdmin.copyNotice(changeHistory[0])'), false);
});

test('recorded role, substitute and score changes remain visible when the person is unchanged', () => {
  const a=app();
  a.run(`globalThis.flagRecord={type:'당직 수정',dateKey:'2026-10-18',before:{schedules:{'2026-10-18':[{name:'김민수',role:'당직',isSubstitute:false,zeroScore:false}]}},after:{schedules:{'2026-10-18':[{name:'김민수',role:'주말 당직',isSubstitute:true,zeroScore:true}]}}};`);
  const rendered=a.run('OnAirAdmin.renderComparison(flagRecord)');
  assert.match(rendered,/점수 반영/);assert.match(rendered,/0점/);assert.match(rendered,/대체 근무/);assert.match(rendered,/주말 당직/);
  const notice=a.run('OnAirAdmin.historyNoticeText(flagRecord)');
  assert.match(notice,/점수 반영/);assert.match(notice,/0점/);
});
