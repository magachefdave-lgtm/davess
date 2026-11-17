'use strict';
// v3.18.0 관리자 작업 흐름(usability_admin.js 두 번째 묶음) 시험.
// 실제 index.html 본 스크립트와 실제 usability_admin.js 를 Node VM 에서 실행한다.
// 화면 DOM·서버 통신·확인 창만 대역을 쓰고, 날짜·배정·저장 흐름 코드는 실제 코드다.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'frontend/dist/index.html'), 'utf8');
const main = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m => m[1]).find(s => s.includes('function friday21Of('));
// ONAIR_ADMIN_JS: 돌연변이 검사에서 바꾼 사본을 넣어 볼 때만 쓴다.
const adminJs = fs.readFileSync(process.env.ONAIR_ADMIN_JS || path.join(root, 'frontend/dist/usability_admin.js'), 'utf8');

function makeNode(id = '') {
  const classes = new Set();
  return {
    id, value: '', checked: false, textContent: '', innerHTML: '', style: {}, dataset: {}, children: [], listeners: {},
    disabled: false, readOnly: false, hidden: false,
    classList: {add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c),
      toggle: (c, force) => { const on = force === undefined ? !classes.has(c) : !!force; if (on) classes.add(c); else classes.delete(c); return on; }},
    setAttribute(k, v) { this[k] = v; }, getAttribute(k) { return this[k]; }, removeAttribute(k) { delete this[k]; },
    appendChild(child) { this.children.push(child); return child; },
    removeChild(child) { this.children = this.children.filter(c => c !== child); return child; },
    insertBefore(child) { this.children.push(child); return child; },
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
    querySelector() { return null; }, querySelectorAll() { return []; },
    focus() {}, click() { this.clicked = (this.clicked || 0) + 1; }, remove() { this.removed = true; },
  };
}

function app({setup = '', beforeAdmin = '', realSync = false} = {}) {
  const nodes = new Map();
  const element = id => { if (!nodes.has(id)) nodes.set(id, makeNode(id)); return nodes.get(id); };
  const storage = {getItem: () => null, setItem: () => {}, removeItem: () => {}};
  const timers = [], created = [];
  const ctx = vm.createContext({
    Date, console, JSON, Math, Promise, Event: class { constructor(type) { this.type = type; } },
    sessionStorage: storage, localStorage: storage, navigator: {},
    document: {getElementById: element, createElement: tag => { const n = Object.assign(makeNode(), {tag}); created.push(n); return n; }, body: makeNode('body'),
      addEventListener: () => {}, querySelector: () => null, querySelectorAll: () => [], activeElement: null, createTextNode: t => ({nodeType: 3, textContent: t})},
    setTimeout: (fn, ms) => { if (!ms) Promise.resolve().then(fn); else timers.push(fn); return timers.length; },
    clearTimeout: () => {}, setInterval: () => 1, clearInterval: () => {},
    requestAnimationFrame: () => 1, addEventListener: () => {}, scrollTo: () => {},
    confirm: () => { throw new Error('native confirm must not be used'); },
    prompt: () => { throw new Error('native prompt must not be used'); },
    alert: () => { throw new Error('native alert must not be used'); },
  });
  vm.runInContext('window=globalThis', ctx);
  vm.runInContext(main, ctx, {filename: 'index.html'});
  const run = code => vm.runInContext(code, ctx);
  run(`SYSTEM_MODE='ADMIN'; SERVER_CONNECTED=true; dataLoaded=true; todayStr='2026-10-14'; curYear=2026; curMonth=11; dataVersion=1;
    memberList=[{id:1,name:'김민수',gender:'M'},{id:2,name:'박지훈',gender:'M'},{id:3,name:'김지은',gender:'F'},{id:4,name:'이서연',gender:'F'},{id:5,name:'최유진',gender:'F'}];
    globalThis.saved=[]; globalThis.toasts=[]; globalThis.asked=[]; globalThis.answers=[]; globalThis.fetches=[]; globalThis.rendered=0;
    renderCalendar=()=>{globalThis.rendered++;}; showToast=(m,e)=>toasts.push({m,e});
    if (!${realSync}) syncFullDataToBackend=async p=>{saved.push(JSON.parse(JSON.stringify(p))); dataVersion++; rememberCommittedState(); return true;};
    fetchWithAdminToken=async(url,options={})=>{fetches.push({url,options}); const body=(globalThis.respond||(()=>({})))(url,options);
      return {ok:!body.__status||body.__status<400,status:body.__status||200,json:async()=>body,clone(){return this;}};};
    fetchAllData=async()=>{globalThis.refetched=(globalThis.refetched||0)+1;};
    openVacancyModal=(dates,label)=>{globalThis.vacancy={dates,label};};
    mergeHolidays(); rememberCommittedState();
    ${beforeAdmin}`);
  vm.runInContext(adminJs, ctx, {filename: 'usability_admin.js'});
  run(`OnAirAdmin.askHook=opts=>{asked.push(opts); return answers.length?answers.shift():null;};${setup}`);
  return {ctx, run, element, timers, created, plain: code => JSON.parse(JSON.stringify(run(code)))};
}

function openDate(a, key, comment = '') {
  a.run(`activeModalDate='${key}'; tempIsNoDuty=!!noDutyMap['${key}']; tempNoDutyReason=typeof noDutyMap['${key}']==='string'?noDutyMap['${key}']:null;
    originalNoDutyReason=tempNoDutyReason; tempSelectedWorkers=(scheduleMap['${key}']||[]).map((w,i)=>({slot:i,name:w.name,isSubstitute:!!w.isSubstitute,zeroScore:!!w.zeroScore}));`);
  a.element('modal-date-comment').value = comment;
  a.run('dateModalBaseline=dateModalState()');
}

// ── 1. 날짜 창 맞교환이 적던 메모를 버리지 않는다 ─────────────────
const SWAP_SETUP = `scheduleMap={'2026-11-24':[{name:'김지은',role:'평일(여)'}],'2026-11-25':[{name:'이서연',role:'평일(여)'}]}; rememberCommittedState();`;
function prepareSwap(a) {
  openDate(a, '2026-11-24');
  a.element('swap-my-worker').value = '김지은';
  a.element('swap-target-date').value = '2026-11-25';
  a.element('swap-target-worker').value = '이서연';
  a.element('modal-date-comment').value = '교대 시간 확인';
}
test('swap with an unsaved memo saves the memo together in one save when chosen', async () => {
  const a = app({beforeAdmin: SWAP_SETUP});
  prepareSwap(a);
  a.run("answers.push('together')");
  await a.run('handleSwapAction()');
  const saved = a.plain('saved');
  assert.equal(saved.length, 1, '한 번에 저장');
  assert.equal(saved[0].comments['2026-11-24'], '교대 시간 확인');
  assert.equal(saved[0].schedules['2026-11-24'][0].name, '이서연');
  assert.equal(saved[0].schedules['2026-11-25'][0].name, '김지은');
  assert.match(saved[0].historyLog.details, /메모도 함께 저장/);
  assert.match(a.run('asked[0].html'), /메모/);
  assert.equal(a.run('commentMap["2026-11-24"]'), '교대 시간 확인');
});
test('swap dialog can discard pending edits or cancel without saving', async () => {
  const a = app({beforeAdmin: SWAP_SETUP});
  prepareSwap(a);
  a.run("answers.push(null)");
  assert.equal(await a.run('handleSwapAction()'), false);
  assert.equal(a.run('saved.length'), 0, '취소는 저장하지 않는다');
  a.run("answers.push('discard')");
  await a.run('handleSwapAction()');
  const saved = a.plain('saved');
  assert.equal(saved.length, 1);
  assert.equal(saved[0].comments, undefined, '버리기를 고르면 메모는 보내지 않는다');
  assert.equal(a.run('commentMap["2026-11-24"]'), undefined);
});
test('swap without pending edits asks nothing', async () => {
  const a = app({beforeAdmin: SWAP_SETUP});
  prepareSwap(a);
  a.element('modal-date-comment').value = '';
  await a.run('handleSwapAction()');
  assert.equal(a.run('asked.length'), 0);
  assert.equal(a.run('saved.length'), 1);
});
test('swap with changed workers saves the date edits first, then swaps', async () => {
  const a = app({beforeAdmin: SWAP_SETUP + "scheduleMap['2026-11-26']=[{name:'최유진',role:'평일(여)'}];"});
  prepareSwap(a);
  a.element('modal-date-comment').value = '';
  a.run("tempSelectedWorkers=[{slot:0,name:'김지은',isSubstitute:false,zeroScore:true}]");
  a.run("answers.push('together')");
  await a.run('handleSwapAction()');
  const saved = a.plain('saved');
  assert.equal(saved.length, 2);
  assert.equal(saved[0].historyLog.type, '수동 변경');
  assert.equal(saved[0].schedules['2026-11-24'][0].zeroScore, true);
  assert.equal(saved[1].historyLog.type, '1:1 맞교환');
});

// 3.18.0 QA: 근무자를 손으로 바꾼 뒤 [함께 저장하고 맞교환] → 근무자만 저장되고 맞교환은 조용히 빠졌다.
//   '내 근무자' 선택이 저장된 사람(바뀌기 전)을 가리켰고, 저장이 창을 닫은 뒤 그 값으로 맞교환을 찾지 못했다.
test('3.18.0 QA: changing the worker updates "내 근무자" and "함께 저장하고 맞교환" swaps the new worker', async () => {
  const a = app({beforeAdmin: SWAP_SETUP});
  openDate(a, '2026-11-24');
  a.run("populateSwapAndSubDropdowns()");
  a.element('swap-my-worker').value = '김지은';
  a.run("selectManualWorker(0,'최유진')");
  assert.equal(a.element('swap-my-worker').value, '최유진', '바꾼 근무자가 곧바로 맞교환 대상이 된다');
  assert.equal(a.element('sub-origin-worker').value, '최유진');
  a.element('swap-target-date').value = '2026-11-25';
  a.element('swap-target-worker').value = '이서연';
  a.run("answers.push('together')");
  await a.run('handleSwapAction()');
  const saved = a.plain('saved');
  assert.equal(saved.length, 2, '근무자 저장 + 맞교환');
  assert.equal(saved[0].historyLog.type, '수동 변경');
  assert.equal(saved[0].schedules['2026-11-24'][0].name, '최유진');
  assert.equal(saved[1].historyLog.type, '1:1 맞교환');
  assert.match(saved[1].historyLog.details, /'최유진' ↔ \[2026-11-25\] '이서연'/);
  assert.equal(saved[1].schedules['2026-11-24'][0].name, '이서연');
  assert.equal(saved[1].schedules['2026-11-25'][0].name, '최유진');
  assert.ok(a.plain('toasts').some(t => /맞교환했습니다/.test(t.m)));
});
test('3.18.0 QA: discarding a worker change does not swap someone who is not on duty', async () => {
  const a = app({beforeAdmin: SWAP_SETUP});
  openDate(a, '2026-11-24');
  a.run("populateSwapAndSubDropdowns(); openDateModal=k=>{globalThis.reopened=k; activeModalDate=k;}");
  a.run("selectManualWorker(0,'최유진')");
  a.element('swap-target-date').value = '2026-11-25';
  a.element('swap-target-worker').value = '이서연';
  a.run("answers.push('discard')");
  assert.equal(await a.run('handleSwapAction()'), false);
  assert.equal(a.run('saved.length'), 0);
  assert.equal(a.run('reopened'), '2026-11-24', '저장된 상태로 날짜 창을 다시 연다');
  assert.ok(a.plain('toasts').some(t => /다시 고르세요/.test(t.m)));
});
test('3.18.0 QA: if the swap is cancelled after the worker save, the date window reopens with the picks', async () => {
  const a = app({beforeAdmin: SWAP_SETUP});
  openDate(a, '2026-11-24');
  a.run("populateSwapAndSubDropdowns(); openDateModal=k=>{globalThis.reopened=k; activeModalDate=k;}; confirmConsecutiveRisk=async()=>false;");
  a.run("selectManualWorker(0,'최유진')");
  a.element('swap-target-date').value = '2026-11-25';
  a.element('swap-target-worker').value = '이서연';
  a.run("answers.push('together')");
  await a.run('handleSwapAction()');
  assert.equal(a.run('saved.length'), 1, '근무자 지정만 저장');
  assert.equal(a.run('reopened'), '2026-11-24');
  assert.equal(a.element('swap-target-worker').value, '이서연');
});

// 3.18.0 QA: 재연결 뒤 관리자 화면에 빈 '저장하지 못한 변경' 띠가 떴다(applyModeUI 가 인라인 display 를 "" 로 지움).
test('3.18.0 QA: reconnect mode refresh keeps self-managed admin bars hidden and restores flex rows', () => {
  const a = app();
  const unsaved = a.element('unsaved-bar'); unsaved.dataset.adminDisplay = 'self'; unsaved.style.display = 'none';
  const undo = a.element('undo-bar'); undo.dataset.adminDisplay = 'self';
  const row = a.element('noduty-row'); row.dataset.adminDisplay = 'flex'; row.style.display = 'flex';
  const plainButton = a.element('plain-admin-button'); plainButton.style.display = 'none';
  a.ctx.document.querySelectorAll = sel => sel === '.admin-only' ? [unsaved, undo, row, plainButton] : [];
  a.run('unsavedList=[]; SYSTEM_MODE="ADMIN"; SERVER_CONNECTED=true; applyModeUI()');
  assert.equal(unsaved.style.display, 'none', '보관한 변경이 없으면 띠를 열지 않는다');
  assert.equal(undo.style.display, undefined, '되돌리기 띠는 스스로 열고 닫는다');
  assert.equal(row.style.display, 'flex', '가로 배치 줄은 flex 로 되돌린다');
  assert.equal(plainButton.style.display, '', '보통 버튼은 그대로 다시 보인다');
  a.run('unsavedList=[{at:"10:00",payload:{}}]; applyModeUI()');
  assert.equal(unsaved.style.display, 'flex', '보관한 변경이 있으면 띠를 연다');
  a.run('SYSTEM_MODE="VIEWER"; applyModeUI()');
  assert.deepEqual([unsaved, undo, row, plainButton].map(n => n.style.display), ['none', 'none', 'none', 'none']);
  assert.match(html, /<div id="unsaved-bar"[^>]*data-admin-display="self"/);
  assert.match(html, /#unsaved-bar\.strip-bar \{ display: none; \}/);
});

// ── 2. 패밀리데이 해제 3갈래 ─────────────────────────────────────
const FAMILY = `dutyRule.rule21FridayOff=true; noDutyMap={'2026-10-23':'RULE21'}; commentMap={'2026-10-23':'패밀리데이'}; scheduleMap={'2026-10-22':[{name:'김지은',role:'평일(여)'}]}; rememberCommittedState();`;
function reopenFamily(a) {
  openDate(a, '2026-10-23', '패밀리데이');
  a.run('tempIsNoDuty=false; tempNoDutyReason=null; tempSelectedWorkers=[];');
}
test('family day reopen: "이 달은 패밀리데이 없음" remembers the month and records a clear history', async () => {
  const a = app({beforeAdmin: FAMILY});
  reopenFamily(a);
  a.run("answers.push('none')");
  await a.run('saveDateModalChanges()');
  const [payload] = a.plain('saved');
  assert.deepEqual(payload.rules.familyDayOffMonths, ['2026-10']);
  assert.equal(payload.historyLog.type, '패밀리데이 해제');
  assert.match(payload.historyLog.details, /패밀리데이 해제 → 근무일로 복구/);
  assert.match(payload.historyLog.details, /당직자 아직 없음/);
  assert.match(payload.historyLog.details, /패밀리데이 없음으로 기억/);
  assert.doesNotMatch(payload.historyLog.details, /당직자: 없음/);
  assert.deepEqual(a.plain('asked[0].buttons.map(b=>b.label)'), ['이 달은 패밀리데이 없음', '다른 날로 옮길 예정', '취소(저장 안 함)']);
  assert.deepEqual(a.plain('vacancy.dates'), ['2026-10-23'], '빈 날은 빈 자리 채우기로 이어진다');
});
test('family day reopen: "다른 날로 옮길 예정" does not remember the month', async () => {
  const a = app({beforeAdmin: FAMILY});
  reopenFamily(a);
  a.run("answers.push('move')");
  await a.run('saveDateModalChanges()');
  const [payload] = a.plain('saved');
  assert.equal(payload.rules, undefined);
  assert.match(payload.historyLog.details, /다른 날로 옮길 예정/);
  assert.equal(a.run('commentMap["2026-10-23"]'), '패밀리데이', '옮길 예정이면 메모는 그대로');
});
test('family day reopen: cancel saves nothing and keeps the day off', async () => {
  const a = app({beforeAdmin: FAMILY});
  reopenFamily(a);
  a.run('answers.push(null)');
  assert.equal(await a.run('saveDateModalChanges()'), false);
  assert.equal(a.run('saved.length'), 0);
  assert.equal(a.run('noDutyMap["2026-10-23"]'), 'RULE21');
  assert.equal(a.run('typeof vacancy'), 'undefined');
});
test('reopening a day with a worker records the worker and skips the vacancy flow', async () => {
  const a = app({beforeAdmin: FAMILY});
  reopenFamily(a);
  a.run("tempSelectedWorkers=[{slot:0,name:'최유진',isSubstitute:false}]; answers.push('none')");
  await a.run('saveDateModalChanges()');
  assert.match(a.run('saved[0].historyLog.details'), /당직자: 최유진/);
  assert.equal(a.run('typeof vacancy'), 'undefined');
});

// ── 3. 되돌리기용 서버 사본 ──────────────────────────────────────
test('auto-assign confirm asks the server for a labeled snapshot and offers "실행 취소(되돌리기)"', async () => {
  // 본 스크립트의 실제 저장 함수(syncFullDataToBackend)를 쓰고 서버 응답만 대역으로 둔다.
  const c = app({realSync: true});
  c.run(`globalThis.respond=(url,o)=>url==='/api/duty/save-all'?{status:'success',dataVersion:7,snapshot:'auto_assign_20261014-143200-001.db',snapshotLabel:'자동 배정 직전 (14:32)'}:({});`);
  c.run(`OnAirAdmin._test.state.pendingSnapshotKind='assign';`);
  const ok = await c.run(`syncFullDataToBackend({schedules:scheduleMap,historyLog:{type:'자동 배정',details:'x'}})`);
  assert.equal(ok, true);
  const sent = JSON.parse(c.run('fetches[0].options.body'));
  assert.equal(sent.snapshotKind, 'assign');
  assert.equal(c.run('dataVersion'), 7);
  assert.equal(c.run('OnAirAdmin._test.state.pendingSnapshotKind'), null);
  assert.equal(c.run('OnAirAdmin._test.state.lastSnapshot.name'), 'auto_assign_20261014-143200-001.db');
  const toastInfo = c.run('OnAirAdmin._test.state.lastActionToast');
  assert.match(toastInfo.text, /자동 배정/);
  // v3.18.0 세 가지 되돌리기 이름을 구분한다: 백업 파일 불러오기(JSON) / 자동 백업으로 되돌리기 / 실행 취소(되돌리기)
  assert.equal(toastInfo.actions[0].label, '실행 취소(되돌리기)');
  // 되돌리기: 미리보기 → 확인 → 기존 복원 경로(baseVersion 은 미리보기가 알려 준 현재 버전)
  c.run(`globalThis.respond=(url,o)=>url.startsWith('/api/system/backup-preview')?{ok:true,current:{counts:{schedules:9},dataVersion:7,scheduleFrom:'2026-11-01',scheduleTo:'2026-11-30',lastChange:'2026-10-14 14:32:10'},backup:{counts:{schedules:5},scheduleFrom:'2026-11-01',scheduleTo:'2026-11-30',lastChange:'2026-10-14 14:00:00'}}
    :url==='/api/system/restore'?{status:'success',safetyCopy:'prerestore_v7_x.db',safetyLabel:'복원 직전 (14:35)'}:({}); answers.push(true);`);
  assert.equal(await c.run('OnAirAdmin.undoToSnapshot(OnAirAdmin._test.state.lastSnapshot.name, OnAirAdmin._test.state.lastSnapshot.label)'), true);
  const restore = c.plain('fetches').find(f => f.url === '/api/system/restore');
  assert.ok(restore, '복원 요청을 보낸다');
  assert.deepEqual(JSON.parse(restore.options.body), {name: 'auto_assign_20261014-143200-001.db', baseVersion: 7});
  assert.match(c.run('asked[0].html'), /자동 배정 직전 \(14:32\)/);
  assert.match(c.run('asked[0].html'), /<td>9<\/td><td class="oa-diff-cell">5<\/td>/);
  // v3.18.0 파일 이름 대신 사람이 읽는 백업 이름 + '되돌리기 직전 상태도 따로 보관했습니다.'
  assert.equal(c.run('OnAirAdmin._test.state.lastActionToast.text'), '자동 배정 직전 (14:32) 백업으로 되돌렸습니다. 되돌리기 직전 상태도 따로 보관했습니다.');
  assert.doesNotMatch(c.run('OnAirAdmin._test.state.lastActionToast.text'), /\.db/);
  assert.equal(c.run('refetched'), 1);
});
test('3.18.0 QA: the backup list preview is titled as a preview of that backup, not "실행 취소"', async () => {
  const c = app();
  c.run(`globalThis.respond=(url)=>url==='/api/system/backups'?{backups:[{name:'daily_20261014.db',label:'10/14 첫 저장 전',kind:'daily',modified:'2026-10-14 09:00',size:2048}]}
    :url.startsWith('/api/system/backup-preview')?{ok:true,current:{counts:{},dataVersion:3},backup:{counts:{}}}:({});`);
  await c.run('OnAirAdmin.openAutoBackupDialog()');
  assert.equal(c.run('asked[0].title'), '자동 백업으로 되돌리기');
  const listeners = [];
  const button = {getAttribute: k => ({'data-oa-auto-preview': 'daily_20261014.db', 'data-label': '10/14 첫 저장 전'})[k],
    addEventListener: (type, fn) => listeners.push(fn)};
  c.run('asked[0]').onOpen({querySelectorAll: () => [button]});
  assert.equal(listeners.length, 1);
  listeners[0]();
  for (let i = 0; i < 10 && c.run('asked.length') < 2; i++) await new Promise(r => setImmediate(r));
  assert.equal(c.run('asked[1].title'), '자동 백업 미리 보기 — 10/14 첫 저장 전');
});
test('declining the undo preview sends no restore', async () => {
  const c = app();
  c.run(`globalThis.respond=(url)=>url.startsWith('/api/system/backup-preview')?{ok:true,current:{counts:{},dataVersion:3},backup:{counts:{}}}:({}); answers.push(null);`);
  assert.equal(await c.run(`OnAirAdmin.undoToSnapshot('auto_bulk_x.db','일괄 작업 직전 (09:00)')`), false);
  assert.ok(!c.plain('fetches').some(f => f.url === '/api/system/restore'));
});
test('plain saves send no snapshot kind; two or more new absences count as a bulk operation', () => {
  const a = app();
  assert.equal(a.run('OnAirAdmin._test.autoSnapshotKind({schedules:{}})'), null);
  a.run(`absences=[{id:1,name:'김지은',type:'연차',start:'2026-11-02',end:'2026-11-02'}]; rememberCommittedState();`);
  assert.equal(a.run(`OnAirAdmin._test.autoSnapshotKind({absences:[...absences,{id:2},{id:3}]})`), 'bulk');
  assert.equal(a.run(`OnAirAdmin._test.autoSnapshotKind({absences:[...absences,{id:2}]})`), null);
});
test('approval responses with a snapshot offer an undo toast', async () => {
  const a = app();
  a.run(`globalThis.respond=()=>({status:'success',snapshot:'auto_approve_20261014-101010-000.db',snapshotLabel:'신청 승인 직전 (10:10)'});`);
  await a.run(`fetchWithAdminToken('/api/duty/request/action',{method:'POST',body:'{}'})`);
  await new Promise(r => setTimeout(r, 0));
  const info = a.run('OnAirAdmin._test.state.lastActionToast');
  assert.match(info.text, /승인/);
  assert.equal(a.run('OnAirAdmin._test.state.lastSnapshot.label'), '신청 승인 직전 (10:10)');
});
test('stale undo (409) refreshes and reports instead of restoring', async () => {
  const a = app();
  a.run(`globalThis.respond=(url)=>url==='/api/system/restore'?{__status:409,detail:'conflict'}:({}); answers.push(true);`);
  assert.equal(await a.run(`OnAirAdmin.performRestore('auto_assign_x.db',3,'자동 배정 직전 (14:32)')`), false);
  assert.equal(a.run('refetched'), 1);
  assert.match(a.run('asked[0].html'), /다른 PC/);
});

// ── 4. 백업 파일 불러오기(JSON) ─────────────────────────────────
function localStamp(iso) {
  const d = new Date(iso), p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
const BACKUP = {formatVersion: 2, exportedAt: '2026-10-14T04:22:05.000Z', members: [{id: 1, name: '김민수', gender: 'M'}],
  schedules: {'2026-11-01': [{name: '김민수'}], '2026-11-30': [{name: '김민수'}], '2026-11-15': []}, comments: {}, noDuty: {},
  absences: [{id: 1}], customHolidays: {'2026-11-20': {name: '창립일'}}, rules: {}, history: [{timestamp: '2026-10-13 09:00:00'}, {timestamp: '2026-10-14 13:00:00'}]};
test('backup file preview shows counts, date range, last change and full creation time', () => {
  const a = app();
  const s = a.plain(`OnAirAdmin._test.backupFileSummary(${JSON.stringify(BACKUP)})`);
  assert.equal(s.ok, true);
  assert.equal(s.members, 1); assert.equal(s.scheduleDays, 2); assert.equal(s.absences, 1); assert.equal(s.holidays, 1);
  assert.equal(s.from, '2026-11-01'); assert.equal(s.to, '2026-11-30'); assert.equal(s.lastChange, '2026-10-14 13:00:00');
  assert.equal(s.exportedAt, localStamp(BACKUP.exportedAt), '날짜만이 아니라 시각까지(이 PC 시간)');
  for (const bad of ['null', '[]', '{"hello":1}', '{"members":[],"schedules":[]}']) assert.equal(a.run(`OnAirAdmin._test.backupFileSummary(${bad}).ok`), false, bad);
});
test('importing a backup asks with a preview and saves with a server-side snapshot', async () => {
  const a = app();
  a.run('answers.push(true)');
  const ok = await a.run(`OnAirAdmin.importBackupFile({name:'smart_duty_backup_20261014.json',text:async()=>${JSON.stringify(JSON.stringify(BACKUP))}})`);
  assert.equal(ok, true);
  const [payload] = a.plain('saved');
  assert.equal(payload.snapshotKind, 'import');
  assert.equal(payload.members.length, 1);
  assert.match(a.run('asked[0].title'), /백업 파일 불러오기\(JSON\)/);
  assert.ok(a.run('asked[0].html').includes(`파일을 만든 시각: <b>${localStamp(BACKUP.exportedAt)}</b>`));
  assert.match(a.run('asked[0].html'), /2026-11-01 ~ 2026-11-30/);
});
test('a non-backup file gets a friendly message and nothing is saved', async () => {
  const a = app();
  a.run('answers.push(true)');
  const event = `{target:{value:'x',files:[{name:'사진.json',text:async()=>'{"photo":true}'}]}}`;
  assert.equal(await a.run(`restoreSystem(${event})`), false);
  assert.equal(a.run('saved.length'), 0);
  assert.equal(a.run('asked[0].title'), '백업 파일이 아닙니다');
  assert.match(a.run('asked[0].html'), /서버에 백업 저장\(JSON\)/);
  assert.match(a.run('asked[0].html'), /back-up/);
  assert.match(a.run('asked[0].html'), /기존 PC에 저장한 백업 파일도 사용할 수 있습니다/);
  a.run('answers.push(true)');
  assert.equal(await a.run(`OnAirAdmin.importBackupFile({name:'x.json',text:async()=>'not json'})`), false);
});
test('cancelling the import preview leaves data untouched', async () => {
  const a = app();
  const before = a.run('JSON.stringify(memberList)');
  a.run('answers.push(null)');
  assert.equal(await a.run(`OnAirAdmin.importBackupFile({name:'b.json',text:async()=>${JSON.stringify(JSON.stringify(BACKUP))}})`), false);
  assert.equal(a.run('JSON.stringify(memberList)'), before);
  assert.equal(a.run('saved.length'), 0);
});

// ── 5. 마감한 달 잠금 ───────────────────────────────────────────
const CLOSED = `closedMonths=['2026-09']; scheduleMap={'2026-09-10':[{name:'김지은',role:'평일(여)'}],'2026-09-11':[{name:'이서연',role:'평일(여)'}]}; rememberCommittedState();`;
test('closed month blocks date-modal edits, drag and auto-assign before any save', async () => {
  const a = app({beforeAdmin: CLOSED + `generateScheduleByRules=async()=>{globalThis.generated=true;};`});
  assert.equal(a.run(`OnAirAdmin._test.closedBannerText('2026-09')`), '2026년 9월은 마감되었습니다 — 고치려면 [마감 해제]');
  openDate(a, '2026-09-10', '메모');
  a.element('modal-date-comment').value = '바뀐 메모';
  assert.equal(await a.run('saveDateModalChanges()'), false);
  a.run("selectManualWorker(0,'최유진')");
  assert.equal(a.run('tempSelectedWorkers[0].name'), '김지은');
  a.run(`draggedData={sourceDate:'2026-09-10',workerName:'김지은',gender:'F',workerIndex:0}`);
  await a.run(`handleDrop({preventDefault(){},currentTarget:{classList:{remove(){}}}},'2026-09-11')`);
  a.run('curMonth=9');
  await a.run('generateScheduleByRules()');
  assert.equal(a.run('typeof generated'), 'undefined');
  assert.equal(a.run('saved.length'), 0);
  assert.match(a.run('OnAirAdmin._test.state.lastActionToast.text'), /2026년 9월은 마감되어/);
  assert.equal(a.run('OnAirAdmin._test.state.lastActionToast.actions[0].label'), '마감 해제');
});
test('open months are not blocked', async () => {
  const a = app({beforeAdmin: CLOSED});
  openDate(a, '2026-11-10');
  a.element('modal-date-comment').value = '메모';
  await a.run('saveDateModalChanges()');
  assert.equal(a.run('saved.length'), 1);
});

// ── 6. 사원 삭제 ────────────────────────────────────────────────
const MEMBER = `scheduleMap={'2026-09-05':[{name:'김민수',role:'주말/휴일(남)'}],'2026-10-17':[{name:'김민수',role:'주말/휴일(남)'},{name:'김지은',role:'주말/휴일(여)'}],'2026-11-07':[{name:'김민수',role:'주말/휴일(남)'}]}; rememberCommittedState();`;
test('member delete shows remaining future duties and keeps gender via tombstone', async () => {
  const a = app({beforeAdmin: MEMBER});
  a.run("answers.push('delete')");
  assert.equal(await a.run('removeMember(1)'), true);
  assert.match(a.run('asked[0].html'), /남은 당직이 <b>2일<\/b>/);
  assert.deepEqual(a.plain('asked[0].buttons.map(b=>b.label)'), ['삭제하고 남은 당직 다시 배정(미리보기)', '삭제만 하기 (당직은 그대로)', '취소']);
  const [payload] = a.plain('saved');
  assert.equal(payload.snapshotKind, 'member', '삭제 직전 서버 사본을 요청한다');
  assert.ok(!payload.members.some(m => m.name === '김민수'));
  assert.equal(payload.rules.memberTombstones['김민수'].gender, 'M');
  assert.match(payload.historyLog.details, /남은 당직 2일/);
  assert.equal(a.run(`OnAirAdmin._test.displayGenderOf('김민수')`), 'M');
  assert.equal(a.run(`genderOf('김민수')`), 'F', '배정 계산용 genderOf 는 그대로');
});
test('deleted member stays 남 in statistics and calendar display only', () => {
  const a = app({beforeAdmin: MEMBER});
  a.run("memberList=memberList.filter(m=>m.name!=='김민수')");
  a.element('stat-year').value = '2026';
  a.element('stat-month').value = 'ALL';
  a.run('renderStats()');
  assert.match(a.element('stats-tbody').innerHTML, /<b>김민수<\/b> \(남\)/, '역할 표식으로 남 표시');
  assert.equal(a.run(`genderOf('김민수')`), 'F', '표시가 끝나면 원래 함수로 돌아온다');
});
test('"남은 당직 다시 배정" deletes first and opens the auto-assign preview from the first remaining month', async () => {
  const a = app({beforeAdmin: MEMBER + `generateScheduleByRules=async()=>{globalThis.generatedAt={y:curYear,m:curMonth,scope:await openScopeModal()};};
    openScopeModal=async()=>OnAirAdmin._test.state.preferredScope;`});
  a.run("answers.push('reassign')");
  await a.run('removeMember(1)');
  assert.deepEqual(a.plain('generatedAt'), {y: 2026, m: 10, scope: 'FROM_MONTH'});
  assert.equal(a.run('saved.length'), 1);
});
test('member delete with no future duty offers a simple delete', async () => {
  const a = app({beforeAdmin: MEMBER});
  a.run("answers.push(null)");
  assert.equal(await a.run('removeMember(5)'), false);
  assert.deepEqual(a.plain('asked[0].buttons.map(b=>b.label)'), ['삭제', '취소']);
  assert.equal(a.run('saved.length'), 0);
});

// ── 7. 입력 창 닫기 전 확인 ──────────────────────────────────────
function ruleForm(a) {
  a.element('rule-modal').classList.add('active');
  a.element('rule-algo').value = 'FIXED_ORDER';
  a.element('rule-sub-zero').value = 'true';
  a.element('rule-21-fri-off').value = 'true';
  a.element('rule-balance-dow').value = 'true';
  a.run(`dutyRule.algoType='FIXED_ORDER'; dutyRule.substituteScoreZero=true; dutyRule.rule21FridayOff=true; dutyRule.balanceDayOfWeek=true;
    dutyRule.fixedOrders={weekday:['김지은','이서연','최유진'],weekendM:['김민수','박지훈'],weekendF:['김지은','이서연'],holidayM:[],holidayF:[],majorM:['김민수','박지훈'],majorF:['김지은','이서연']};`);
  const fo = a.plain('dutyRule.fixedOrders');
  for (const [key, id] of Object.entries({weekday: 'ro-wd', weekendM: 'ro-we-m', weekendF: 'ro-we-f', holidayM: 'ro-hol-m', holidayF: 'ro-hol-f', majorM: 'ro-maj-m', majorF: 'ro-maj-f'}))
    a.element(id).value = fo[key].join(', ');
}
test('rule modal asks before discarding edits on Esc/✕ and closes when unchanged', async () => {
  const a = app();
  ruleForm(a);
  a.run('closeRuleModal()');
  assert.equal(a.element('rule-modal').classList.contains('active'), false, '바뀐 것이 없으면 바로 닫힌다');
  ruleForm(a);
  a.element('ro-we-m').value = '박지훈, 김민수';
  a.run('answers.push(false)');
  a.run('MODAL_CLOSERS.find(e=>e[0]==="rule-modal")[1]()');
  await new Promise(r => setTimeout(r, 0));
  assert.equal(a.element('rule-modal').classList.contains('active'), true, '계속 작성하기');
  assert.equal(a.run('asked[0].title'), '저장하지 않은 내용이 있습니다');
  a.run('answers.push(true)');
  a.run('closeRuleModal()');
  await new Promise(r => setTimeout(r, 0));
  assert.equal(a.element('rule-modal').classList.contains('active'), false, '닫기(저장 안 함)');
});
test('reject reason, date modal, holiday and member inputs share the discard question', async () => {
  const a = app();
  const p = a.run(`askRejectReason({requester_name:'김지은',request_type:'SWAP'})`);
  a.element('reject-reason-input').value = '인원 부족';
  a.run('answers.push(false)');
  await a.element('reject-cancel').onclick();
  assert.equal(a.element('reject-modal').classList.contains('active'), true, '적은 사유는 지키고 창을 유지');
  a.run('answers.push(true)');
  await a.element('reject-cancel').onclick();
  assert.equal(await p, null);
  openDate(a, '2026-11-24');
  a.element('modal-date-comment').value = '적던 메모';
  a.element('date-modal').classList.add('active');
  a.run('answers.push(false)');
  a.run('closeDateModal()');
  await new Promise(r => setTimeout(r, 0));
  assert.equal(a.element('date-modal').classList.contains('active'), true);
  a.element('holiday-modal').classList.add('active');
  a.element('hol-name').value = '창립일';
  a.run('answers.push(false)');
  a.run('closeHolidayModal()');
  await new Promise(r => setTimeout(r, 0));
  assert.equal(a.element('holiday-modal').classList.contains('active'), true);
  a.element('member-modal').classList.add('active');
  a.element('new-member-name').value = '신입';
  a.run('answers.push(true)');
  a.run('closeMemberModal()');
  await new Promise(r => setTimeout(r, 0));
  assert.equal(a.element('member-modal').classList.contains('active'), false);
  assert.equal(a.run('asked.length'), 5);
});

// ── 8. 고정 순번 편집: 명단 대조로 저장을 막고 '그래도 저장' 허용 ───────
test('roster problems list unknown, wrong-gender and duplicate names', () => {
  const a = app();
  ruleForm(a);
  a.element('ro-we-m').value = '김민수, 홍길동, 김지은, 김민수';
  const problems = a.plain('OnAirAdmin._test.rosterProblems()');
  assert.deepEqual(problems.map(p => [p.label, p.names.join(','), p.why]), [
    ['주말(남)', '홍길동', '사원 명단에 없는 이름'], ['주말(남)', '김지은', '남사원 순번에 맞지 않는 성별'], ['주말(남)', '김민수', '두 번 들어간 이름']]);
});
test('saving rules with a non-roster name is blocked until "그래도 저장"; history records before/after', async () => {
  const a = app();
  ruleForm(a);
  a.element('ro-we-m').value = '박지훈, 홍길동';
  a.run('answers.push(null)');
  assert.equal(await a.run('saveRulesOnly()'), false);
  assert.equal(a.run('saved.length'), 0);
  assert.match(a.run('asked[0].html'), /홍길동/);
  a.run('answers.push(true)');
  await a.run('saveRulesOnly()');
  const [payload] = a.plain('saved');
  assert.equal(payload.historyLog.type, '근무 규칙 변경');
  assert.match(payload.historyLog.details, /주말\(남\) 순번: 김민수, 박지훈 → 박지훈, 홍길동 \(추가: 홍길동 \/ 빠짐: 김민수\)/);
});
test('rule diff text names option changes and pure reorders', () => {
  const a = app();
  const text = a.run(`OnAirAdmin._test.ruleDiffText({algoType:'FAIR_WEIGHT',rule21FridayOff:true,fixedOrders:{weekendF:['A','B']},familyDayOffMonths:[]},
    {algoType:'FIXED_ORDER',rule21FridayOff:false,fixedOrders:{weekendF:['B','A']},familyDayOffMonths:['2026-10']})`);
  assert.match(text, /배정 방식: 누적 가중치 균등 → 고정 순환제/);
  assert.match(text, /패밀리데이: 사용 → 사용 안 함/);
  assert.match(text, /주말\(여\) 순번: A, B → B, A \(순서만 바뀜\)/);
  assert.match(text, /패밀리데이 없는 달: 없음 → 2026-10/);
  assert.equal(a.run('OnAirAdmin._test.ruleDiffText({fixedOrders:{}}, {fixedOrders:{}})'), '바뀐 설정 없음 (같은 설정으로 다시 저장)');
});

// ── 9. 확인 버튼이 화면 안에 남도록(스타일) ─────────────────────────
test('modal footers are sticky for scope, rotation-start, preview and rule modals', () => {
  assert.match(adminJs, /\.oa-sticky-foot\{position:sticky;bottom:/);
  for (const id of ['scope-modal', 'rotation-start-modal', 'assign-preview-modal', 'rule-modal']) assert.ok(adminJs.includes(`'${id}'`), id);
});

// ── 10. 자동 배정 미리보기 ───────────────────────────────────────
test('preview model groups by month, classifies 당직 없음 추가 and computes per-person deltas', () => {
  const a = app();
  const before = {schedules: {'2026-11-02': [{name: '김지은'}], '2026-11-03': [{name: '이서연'}], '2026-11-04': [{name: '최유진'}], '2026-12-01': [{name: '김지은'}]},
    noDuty: {'2026-11-05': 'RULE21'}, comments: {}};
  const after = {schedules: {'2026-11-02': [{name: '김지은'}], '2026-11-03': [{name: '김지은'}], '2026-11-05': [{name: '최유진'}], '2026-12-01': [{name: '김지은'}]},
    noDuty: {'2026-11-04': 'RULE21'}, comments: {}};
  const model = a.plain(`OnAirAdmin._test.buildPreviewModel({before:${JSON.stringify(before)},after:${JSON.stringify(after)},year:2026,startM:11,endM:12})`);
  const kind = k => model.rows.find(r => r.k === k).kind;
  assert.equal(kind('2026-11-02'), '그대로');
  assert.equal(kind('2026-11-03'), '변경');
  assert.equal(kind('2026-11-04'), '당직 없음 추가', '근무하던 날이 당직 없는 날이 되면 해제가 아니라 당직 없음 추가');
  assert.equal(kind('2026-11-05'), '당직 없음 해제');
  assert.equal(model.counts.newOff, 1);
  assert.deepEqual(model.months.map(m => [m.key, m.changed, m.newOff]), [['2026-11', 3, 1], ['2026-12', 0, 0]]);
  assert.deepEqual(model.deltas, [{name: '김지은', before: 2, after: 3, delta: 1}, {name: '이서연', before: 1, after: 0, delta: -1}]);
  const html = a.run(`OnAirAdmin._test.previewHtml(OnAirAdmin._test.buildPreviewModel({before:${JSON.stringify(before)},after:${JSON.stringify(after)},year:2026,startM:11,endM:12}))`);
  assert.match(html, /<mark class="oa-diff-add">김지은<\/mark>/);
  assert.match(html, /<mark class="oa-diff-del">이서연<\/mark>/);
  assert.doesNotMatch(html, /<mark class="oa-diff-add">최유진<\/mark>.*2026-11-02/);
  assert.match(html, /id="oa-pv-only-changed" checked> 바뀐 날만/);
  assert.match(html, /<details class="oa-pv-month" data-month="2026-11" open>/);
  assert.match(html, /<details class="oa-pv-month" data-month="2026-12">/, '바뀐 날이 없는 달은 접혀 있다');
  assert.match(html, /김지은 2→3 \(\+1\)/);
  assert.match(html, /이서연 1→0 \(−1\)/);
  assert.match(html, /data-oa-changed="0" hidden/);
});
test('confirming the real preview marks the next save for an "assign" snapshot', async () => {
  const a = app({beforeAdmin: `openScopeModal=async()=>'MONTH'; dutyRule.algoType='FAIR_WEIGHT'; dutyRule.rule21FridayOff=false;`});
  const pending = a.run('generateScheduleByRules()');
  for (let i = 0; i < 20 && !a.element('preview-apply').onclick; i++) await new Promise(r => setTimeout(r, 0));
  assert.equal(typeof a.element('preview-apply').onclick, 'function', '미리보기 창이 열린다');
  assert.match(a.element('oa-preview-groups').innerHTML, /2026년 11월/);
  a.element('preview-apply').onclick();
  await pending;
  assert.equal(a.run('saved.length'), 1);
  assert.equal(a.run('saved[0].snapshotKind'), 'assign', '확정 저장에 자동 배정 직전 사본을 요청한다');
  assert.equal(a.run('saved[0].historyLog.type'), '자동 배정');
  assert.equal(a.run('OnAirAdmin._test.state.pendingSnapshotKind'), null, '쓰고 나면 비운다');
});

// ── 12. 드래그: 이름이 든 알림, 30초 되돌리기 ───────────────────────
test('drag swap toast names both people with a 30 second undo and history names the partner', async () => {
  const a = app({beforeAdmin: SWAP_SETUP});
  a.run(`draggedData={sourceDate:'2026-11-24',workerName:'김지은',gender:'F',workerIndex:0}`);
  await a.run(`handleDrop({preventDefault(){},currentTarget:{classList:{remove(){}}}},'2026-11-25')`);
  const info = a.run('OnAirAdmin._test.state.lastActionToast');
  assert.equal(info.text, '11/24 김지은 ↔ 11/25 이서연 교환');
  assert.equal(info.seconds, 30);
  assert.equal(info.actions[0].countdown, true);
  assert.equal(a.run('saved[0].historyLog.details'), "[2026-11-24] '김지은' ↔ [2026-11-25] '이서연' 교환");
  assert.equal(a.element('undo-count').textContent, 30, '되돌리기 보관 30초');
  assert.equal(a.run('OnAirAdmin._test.state.undoSeconds'), 10, '다른 변경은 원래 10초');
  assert.ok(!a.plain('toasts').some(t => /교환 완료/.test(t.m)), '같은 내용의 기본 알림은 겹쳐 띄우지 않는다');
  // 3.18.0 QA: 본 화면의 '🔀 …맞교환했습니다' 알림이 [되돌리기] 알림과 함께 두 개 떴다. 정상 알림은 하나도 더 띄우지 않는다.
  assert.deepEqual(a.plain('toasts').filter(t => !t.e).map(t => t.m), []);
  // 되돌리기 이름도 '드래그 이동'이 아니라 '드래그 맞교환'
  assert.equal(a.run('undoSnapshot.label'), '드래그 맞교환');
});
test('3.18.0 QA: a plain drag move keeps the "드래그 이동" undo name', async () => {
  const a = app({beforeAdmin: SWAP_SETUP + "scheduleMap['2026-11-26']=[];"});
  a.run(`draggedData={sourceDate:'2026-11-24',workerName:'김지은',gender:'F',workerIndex:0}`);
  await a.run(`handleDrop({preventDefault(){},currentTarget:{classList:{remove(){}}}},'2026-11-26')`);
  assert.equal(a.run('saved.length'), 1);
  assert.equal(a.run('undoSnapshot.label'), '드래그 이동');
});
test('3.18.0 QA: action toasts never take clicks or drops away from calendar cells', () => {
  const style = adminJs;
  assert.match(style, /\.oa-action-toast\{[^}]*pointer-events:none/);
  assert.match(style, /\.oa-action-toast button\{[^}]*pointer-events:auto/);
  assert.match(style, /body\.oa-dragging \.oa-action-toast[^{]*#toast[^{]*\{pointer-events:none !important\}/);
});
test('dropping onto a 당직 없는 날 uses the custom question, not native confirm', async () => {
  const a = app({beforeAdmin: SWAP_SETUP + `noDutyMap={'2026-11-26':true};`});
  a.run(`draggedData={sourceDate:'2026-11-24',workerName:'김지은',gender:'F',workerIndex:0}; answers.push(true);`);
  await a.run(`handleDrop({preventDefault(){},currentTarget:{classList:{remove(){}}}},'2026-11-26')`);
  assert.equal(a.run('asked[0].title'), '당직 없는 날입니다');
  assert.equal(a.run('saved[0].schedules["2026-11-26"][0].name'), '김지은');
  assert.equal(a.run('OnAirAdmin._test.state.lastActionToast.text'), '김지은: 11/24 → 11/26 이동');
});

// ── 13. 기본 입력 창 대신 쓰는 창 ─────────────────────────────────
test('month close: memo optional, cancel cancels; reopen requires a reason with inline validation', async () => {
  const a = app();
  a.run(`globalThis.respond=()=>({status:'success'}); curMonth=11; answers.push(null);`);
  assert.equal(await a.run('toggleMonthClose()'), false);
  assert.equal(a.run('fetches.length'), 0, '취소는 취소');
  assert.equal(a.run('asked[0].title'), '2026년 11월 마감하기');
  a.run(`answers.push('')`);
  assert.equal(await a.run('toggleMonthClose()'), true);
  assert.deepEqual(JSON.parse(a.run('fetches[0].options.body')), {month: '2026-11', closed: true, reason: '', baseVersion: 1});
  a.run(`closedMonths=['2026-11']; answers.push('11/24 맞교환 반영');`);
  await a.run('toggleMonthClose()');
  const reopen = a.run('asked[2]');
  assert.equal(reopen.title, '2026년 11월 마감 해제');
  const field = {value: ''};
  assert.equal(reopen.validate(true, {querySelector: () => field}), '해제 사유를 적어 주세요.');
  field.value = '정정';
  assert.equal(reopen.validate(true, {querySelector: () => field}), '');
  assert.equal(reopen.dirty({querySelector: () => field}), true, '적은 사유가 있으면 닫기 전에 묻는다');
  assert.equal(JSON.parse(a.run('fetches[1].options.body')).reason, '11/24 맞교환 반영');
});
test('history CSV range uses two dates with presets and validates them', async () => {
  const a = app();
  assert.deepEqual(a.plain(`OnAirAdmin._test.presetRange('lastMonth', new Date(2026,0,15))`), {start: '2025-12-01', end: '2025-12-31'});
  assert.deepEqual(a.plain(`OnAirAdmin._test.presetRange('year', new Date(2026,5,1))`), {start: '2026-01-01', end: '2026-12-31'});
  assert.deepEqual(a.plain(`OnAirAdmin._test.presetRange('all')`), {start: '', end: ''});
  assert.equal(a.run(`OnAirAdmin._test.historyRangeError('2026-03-01','2026-02-01')`), '시작일이 종료일보다 늦습니다.');
  assert.match(a.run(`OnAirAdmin._test.historyRangeError('2026-03-01','')`), /모두 고르세요/);
  assert.equal(a.run(`OnAirAdmin._test.historyRangeError('','')`), '');
  a.run(`answers.push({start:'2026-01-01',end:'2026-03-31'})`);
  assert.equal(await a.run('downloadHistoryCsv()'), true);
  const link = a.created.filter(n => n.tag === 'a').pop();
  assert.equal(link.href, '/api/duty/history-export?start=2026-01-01&end=2026-03-31');
  assert.equal(link.clicked, 1);
  assert.equal(a.run('asked[0].title'), '변경 이력 CSV 내려받기');
  a.run('answers.push(null)');
  assert.equal(await a.run('downloadHistoryCsv()'), false);
});
test('my-name chooser is a select list instead of a numbered prompt', async () => {
  const a = app({beforeAdmin: `setMyName=n=>{globalThis.myNameSet=n;};`});
  a.run(`answers.push('이서연')`);
  await a.run('promptMyName()');
  assert.equal(a.run('myNameSet'), '이서연');
  assert.match(a.run('asked[0].html'), /<select id="oa-pick-name">/);
  assert.match(a.run('asked[0].html'), /<option value="이서연">이서연<\/option>/);
});
test('date modal close confirmation is custom, not native confirm', async () => {
  const a = app();
  openDate(a, '2026-11-24');
  a.element('modal-date-comment').value = '바뀜';
  a.element('date-modal').classList.add('active');
  a.run('answers.push(true)');
  a.run('closeDateModal()');
  await new Promise(r => setTimeout(r, 0));
  assert.equal(a.element('date-modal').classList.contains('active'), false);
});

// ── 14. 공휴일을 바꾸면 그 달 다시 배정 미리보기 ─────────────────────
test('adding a holiday offers "이 달 다시 배정 미리보기" which opens the month preview directly', async () => {
  const a = app({beforeAdmin: `generateScheduleByRules=async()=>{globalThis.gen={y:curYear,m:curMonth,scope:await openScopeModal()};}; openScopeModal=async()=>'SHOULD_NOT_OPEN';`});
  a.element('hol-date').value = '2027-02-19';
  a.element('hol-name').value = '창립일';
  await a.run('addCustomHoliday()');
  const info = a.run('OnAirAdmin._test.state.lastActionToast');
  assert.equal(info.actions[0].label, '이 달 다시 배정 미리보기');
  await info.actions[0].onClick();
  assert.deepEqual(a.plain('gen'), {y: 2027, m: 2, scope: {scope: 'MONTH', fresh: false, familyReset: false}});
  a.run(`OnAirAdmin._test.state.lastActionToast=null`);
  await a.run(`removeBaseHoliday('2026-12-25')`);
  assert.match(a.run('OnAirAdmin._test.state.lastActionToast.text'), /12\/25 공휴일 설정을 바꿨습니다/);
});

// ── 15·16. 문구 ────────────────────────────────────────────────
test('ordinary approval wording, search clear and scope details read plainly', () => {
  // Button labels are exercised using rendered rows in approval_list.test.cjs.
  assert.ok(!html.includes("'ABSENCE' ? '반영' : '승인'"));
  assert.ok(html.includes('신청을 승인할까요?'));
  assert.ok(html.includes('>검색 지우기</button>'));
  assert.ok(!/onclick="clearSearch\(\)"[^>]*>초기화</.test(html));
  assert.equal((html.match(/<details class="scope-detail"><summary>자세히<\/summary>/g) || []).length, 2);
  assert.ok(html.includes('<em>규칙이 만든 당직 없는 날 표시를 지우고 순번대로 새로 짭니다.</em>'));
});

// ── 17. 미배정 표시 ─────────────────────────────────────────────
test('unassigned days are listed only for months that have assignments', () => {
  const a = app({beforeAdmin: `scheduleMap={'2026-11-02':[{name:'김지은',role:'평일(여)'}],'2026-11-07':[{name:'김민수',role:'주말/휴일(남)'}]}; noDutyMap={'2026-11-03':true};`});
  const days = a.plain('OnAirAdmin._test.unassignedDays(2026,11)');
  assert.ok(!days.includes('2026-11-02'), '채운 평일');
  assert.ok(!days.includes('2026-11-03'), '당직 없는 날');
  assert.ok(days.includes('2026-11-04'), '빈 평일');
  assert.ok(days.includes('2026-11-07'), '남만 있고 여가 빈 주말');
  assert.deepEqual(a.plain('OnAirAdmin._test.unassignedDays(2026,12)'), [], '아직 배정하지 않은 달은 표시하지 않는다');
  a.run(`absences=[{id:9,name:'김지은',type:'연차',start:'2026-11-02',end:'2026-11-02'}]`);
  assert.ok(a.plain('OnAirAdmin._test.unassignedDays(2026,11)').includes('2026-11-02'), '휴가로 빠진 사람은 채운 것으로 보지 않는다');
});
