'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'frontend/dist/index.html'), 'utf8');
const main = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m => m[1]).find(s => s.includes('function friday21Of('));
const clone = value => JSON.parse(JSON.stringify(value));
const tick = async () => { for (let i = 0; i < 14; i++) await Promise.resolve(); };
function app() {
  const nodes = new Map(), doc = {activeElement: null};
  function node(initial = '') {
    let id = initial, content = ''; const classes = new Set();
    const n = {value: '', checked: false, disabled: false, hidden: false, textContent: '', style: {}, dataset: {}, listeners: {}, options: [], children: [], tabIndex: 0, isConnected: true,
      classList: {add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x), toggle(x, yes) {yes ??= !classes.has(x); yes ? classes.add(x) : classes.delete(x); return yes;}},
      add(option) {this.options.push(option);}, setAttribute(k, v) {this[k] = String(v);}, getAttribute(k) {return this[k];}, removeAttribute(k) {delete this[k];},
      addEventListener(k, f) {(this.listeners[k] ??= []).push(f);}, appendChild(x) {this.children.push(x); return x;}, insertBefore(x) {this.children.push(x);}, remove() {},
      querySelector(s) {if(s[0] === '#') return get(s.slice(1)); return this.children.find(x => String(x.className || '').split(' ').includes(s.slice(1))) || null;},
      querySelectorAll() {return this.children;}, contains(x) {return this === x || this.children.includes(x);}, getClientRects() {return [1];}, focus() {if (!this.disabled) doc.activeElement = this;}};
    Object.defineProperty(n, 'id', {get: () => id, set(v) {id = v; nodes.set(v, n);}});
    Object.defineProperty(n, 'innerHTML', {get: () => content, set(v) {content = v; n.children = [...v.matchAll(/id="([^"]+)"/g)].map(m => get(m[1]));}});
    if (id) nodes.set(id, n); return n;
  }
  const get = id => nodes.get(id) || node(id);
  Object.assign(doc, {getElementById: get, querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, createElement: () => node(), createTextNode: t => ({textContent: t}), body: node('body')});
  doc.activeElement = doc.body;
  const storage = {getItem: () => null, setItem() {}, removeItem() {}};
  const ctx = vm.createContext({Date, JSON, Math, Promise, console, document: doc, localStorage: storage, sessionStorage: storage, navigator: {},
    crypto: require('node:crypto').webcrypto, Option: function(text, value) {this.text = text; this.value = value;},
    setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {}, requestAnimationFrame: f => f(), addEventListener() {}, scrollTo() {}, alert() {}});
  const run = s => vm.runInContext(s, ctx);
  run('window=globalThis'); vm.runInContext(main, ctx);
  run(`SYSTEM_MODE='ADMIN';SERVER_CONNECTED=true;dataLoaded=true;todayStr='2026-11-09';curYear=2026;curMonth=11;dataVersion=7;
    memberList=[{name:'여성',gender:'F'},{name:'남성',gender:'M'}];scheduleMap={'2026-11-20':[{name:'여성',role:'평일',isSubstitute:false,zeroScore:false}]};
    dutyRule.algoType='FIXED_ORDER';customHolidays={};mergeHolidays();
    globalThis.saved=[];globalThis.toasts=[];globalThis.requests=[];globalThis.responses=[];globalThis.asked=[];globalThis.answers=[];globalThis.refreshCount=0;
    globalThis.supplementData={schema:1,events:[],bases:[],requiredContract:0};
    getHolidaySupplementState=()=>supplementData;
    renderCalendar=()=>{};renderConnState=()=>{};applyModeUI=()=>{};renderMemberModalList=()=>{};renderAbsenceTypeUI=()=>{};
    showToast=(message,error)=>toasts.push({message,error});
    syncFullDataToBackend=async body=>{saved.push(body);dataVersion++;return true;};
    fetchAllData=async()=>{refreshCount++;return true};refreshHolidaySupplementData=async()=>{await fetchAllData();return supplementData};
    fetchWithAdminToken=async(url,opts={})=>{requests.push({url,body:opts.body?JSON.parse(opts.body):null});const item=responses.shift();if(item instanceof Error)throw item;if(!item)throw new Error('No response');return {ok:item.status<400,status:item.status,json:async()=>item.body};};`);
  // Real application wrappers run against the DOM and transport boundary above.
  const module = path.join(root, 'frontend/dist/holiday_supplement.js');
  if (fs.existsSync(module)) vm.runInContext(fs.readFileSync(module, 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'frontend/dist/usability_admin.js'), 'utf8'), ctx);
  run('OnAirAdmin.askHook=opts=>{asked.push(opts);return answers.length?answers.shift():null}');
  vm.runInContext(fs.readFileSync(path.join(root, 'frontend/dist/holiday_management.js'), 'utf8'), ctx);
  return {get, doc, ctx, run, plain: s => clone(run(s)), api: ctx.HolidaySupplement, reply(body, status = 200) {ctx.responses.push({status, body});}};
}
function plan(overrides = {}) {
  return {schema: 1, effect: 'ADD', eligible: true, blockers: [], warnings: [], baseVersion: 7, reviewDigest: 'digest-1',
    before: {date_key: '2026-11-20', holiday: null, workers: [{name: '여성', role: '평일', zeroScore: false}]},
    after: {date_key: '2026-11-20', holiday: {name: '추가 공휴일'}, workers: [{name: '여성', role: '공휴일(여)'}, {name: '남성', role: '공휴일(남)'}]},
    candidate: {name: '남성', position: 1, cycle: 1, ordinal: 2},
    rotationBasis: {year: 2026, pool_key: 'holiday|M', revision: 1, sequence_snapshot: ['앞순번', '남성'], ordinary_tail_position: 1,
      lastReservation: {date_key: '2026-12-25', name: '앞순번', position: 0, cycle: 1, ordinal: 1}, skipped_candidates: [{name: '부재자', code: 'ABSENT', message: '승인된 교육'}]},
    impacts: [{code: 'STATISTICS_CHANGE', message: '여성: 평일 1점 → 공휴일 2점'}, {code: 'PENDING_REQUEST', requestId: 4, message: '맞교환 신청 #4는 다시 확인해야 합니다.'}],
    affectedDates: ['2026-11-20'], preservedDatesCount: 365, requiredContract: 1, ...overrides};
}
const draft = {holidayIntent: 'SET_CUSTOM', date_key: '2026-11-20', name: '추가 공휴일', is_major: false};

test('weekday create opens a review before any legacy save; cancel preserves input and every business row', async () => {
  const a = app(); a.run('openHolidayModal()'); await a.run("OnAirHolidayManagement.select('2026-11-20')"); a.get('hol-name').value = '추가 공휴일';
  const before = a.plain('[scheduleMap,customHolidays]'); a.reply(plan());
  const pending = a.run('addCustomHoliday()'); await tick();
  assert.equal(a.run('saved.length'), 0, 'legacy mutation must not run before review');
  assert.equal(a.run('requests[0].url'), '/api/duty/holiday-supplement/preview');
  a.api.close(); assert.equal(await pending, false);
  assert.deepEqual(a.plain('[scheduleMap,customHolidays]'), before); assert.equal(a.get('hol-name').value, '추가 공휴일');
  assert.equal(a.run('requests.length'), 1);
});
test('review explains preserved female, proposed male, annual tail, skipped absence and request/statistic effects', async () => {
  const a = app(); assert.ok(a.api, 'supplement review module is available'); a.reply(plan()); const pending = a.api.open(draft); await tick();
  const rendered = a.get('hs-content').innerHTML;
  for (const text of ['여성', '남성', '2026', '2026-12-25', '앞순번', '부재자', '승인된 교육', '평일 1점', '신청 #4', '365']) assert.ok(rendered.includes(text), text);
  assert.match(rendered, /기존 여성 유지/); assert.equal(a.get('hs-apply').disabled, false); a.api.close(); await pending;
});
test('apply double clicks write once, reload server state, and never offer month regeneration', async () => {
  const a = app(); assert.ok(a.api); a.reply(plan()); const pending = a.api.open(draft); await tick();
  a.run('fetchWithAdminToken=async(url,opts)=>{requests.push({url,body:JSON.parse(opts.body)});return new Promise(resolve=>globalThis.finishApply=resolve)}');
  const applying = a.api.apply(); assert.equal(await a.api.apply(), false); assert.equal(a.api.close(), false, 'cannot dismiss an in-flight commit');
  a.run("finishApply({ok:true,status:200,json:async()=>({status:'APPLIED',dataVersion:8})})");
  assert.equal(await applying, true); assert.equal(await pending, true); assert.equal(a.run('refreshCount'), 1);
  assert.equal(a.run('requests.filter(r=>r.url.endsWith("/apply")).length'), 1);
  assert.match(a.run('toasts.at(-1).message'), /해당 날짜 보완 완료/); assert.doesNotMatch(a.run('toasts.at(-1).message'), /다시 배정/);
});
test('uncertain response retries the identical operation and draft instead of consuming another credit', async () => {
  const a = app(); assert.ok(a.api); a.reply(plan()); const pending = a.api.open(draft); await tick();
  a.run("responses.push(new Error('connection lost'))"); assert.equal(await a.api.apply(), false);
  assert.equal(a.get('hs-apply').disabled, false); a.reply({status: 'APPLIED', historicalResult: {status: 'APPLIED'}, currentState: {state: 'CANCELLED'}});
  assert.equal(await a.api.apply(), true); await pending;
  const calls = a.plain('requests.filter(r=>r.url.endsWith("/apply"))'); assert.deepEqual(calls[1].body, calls[0].body);
  assert.match(a.run('toasts.at(-1).message'), /현재 상태/);
});
test('a 409 invalidates the review and requires new preview plus new operation id', async () => {
  const a = app(); assert.ok(a.api); a.reply(plan()); const pending = a.api.open(draft); await tick();
  a.reply({detail: '다른 PC에서 변경되었습니다.'}, 409); assert.equal(await a.api.apply(), false);
  assert.equal(a.get('hs-apply').disabled, true); assert.equal(await a.api.apply(), false);
  a.reply(plan({baseVersion: 8, reviewDigest: 'digest-2'})); await a.api.refresh();
  a.reply({status: 'APPLIED'}); await a.api.apply(); await pending;
  const calls = a.plain('requests.filter(r=>r.url.endsWith("/apply"))'); assert.notEqual(calls[1].body.operationId, calls[0].body.operationId);
  assert.equal(calls[1].body.baseVersion, 8); assert.equal(calls[1].body.reviewDigest, 'digest-2');
});
test('server blockers disable apply and explain unsupported basis without silently accepting it', async () => {
  const a = app(); assert.ok(a.api); a.reply(plan({eligible: false, effect: 'BLOCKED', blockers: [{code: 'BASIS_REBASE_REQUIRED', message: '같은 해 순번 또는 정규 예약이 바뀌었습니다.'}]}));
  const pending = a.api.open(draft); await tick(); assert.equal(a.get('hs-apply').disabled, true);
  assert.match(a.get('hs-content').innerHTML, /기존 표.*보존/); assert.match(a.get('hs-content').innerHTML, /이번 버전/);
  assert.equal(await a.api.apply(), false); assert.equal(a.run('requests.length'), 1); a.api.close(); await pending;
});
test('restoring a removed weekday base holiday and removing an active supplement use typed previews', async () => {
  const a = app(); assert.ok(a.api); a.run("customHolidays['2026-12-25']={name:'성탄절',isRemoved:true};scheduleMap['2026-12-25']=[{name:'여성',role:'평일'}];mergeHolidays()");
  a.reply(plan()); let pending = a.run("restoreBaseHoliday('2026-12-25')"); await tick();
  assert.equal(a.run('requests.at(-1).body.holidayIntent'), 'RESTORE_BASE'); a.api.close(); await pending;
  a.run("supplementData.events=[{date_key:'2026-11-20',state:'ACTIVE'}];customHolidays['2026-11-20']={name:'추가',isRemoved:false};mergeHolidays()");
  a.reply(plan({effect: 'REMOVE'})); pending = a.run("delCustomHoliday('2026-11-20')"); await tick();
  assert.equal(a.run('requests.at(-1).body.holidayIntent'), 'DELETE_CUSTOM'); assert.equal(a.run('saved.length'), 0); a.api.close(); await pending;
});
test('ordinary weekend CRUD and existing non-supplement labels retain their legacy save path', async () => {
  const a = app(); a.run('openHolidayModal()'); await a.run("OnAirHolidayManagement.select('2026-11-21')"); a.get('hol-name').value = '주말 휴일';
  assert.equal(await a.run('addCustomHoliday()'), true); assert.equal(a.run('saved.length'), 1); assert.equal(a.run('requests.length'), 0);
});
test('female-only holiday is counted as missing male while its assigned female remains visible', () => {
  const a = app(); a.run("customHolidays['2026-11-20']={name:'추가',isRemoved:false};mergeHolidays()");
  vm.runInContext(fs.readFileSync(path.join(root, 'frontend/dist/holiday_preview.js'), 'utf8'), a.ctx);
  a.get('hp-month').value = 'all'; a.run('openPreviewModal()');
  const row = a.run("OnAirHolidayPreview.rows().find(row=>row.key==='2026-11-20')"); assert.equal(row.status, 'missing');
  assert.match(a.get('prev-tbody').innerHTML, /여성/); assert.match(a.get('prev-tbody').innerHTML, /남성 미배정/);
});
test('preview labels and API errors escape HTML', async () => {
  const a = app(); assert.ok(a.api); const p = plan(); p.candidate.name = '<img src=x>'; p.impacts[0].message = '<script>bad</script>'; a.reply(p);
  const pending = a.api.open(draft); await tick(); assert.match(a.get('hs-content').innerHTML, /&lt;img/); assert.doesNotMatch(a.get('hs-content').innerHTML, /<img|<script/);
  a.api.close(); await pending;
});
test('reconcile uses the selected year/pool and a fresh review before preserving existing reservations', async () => {
  const a = app(); assert.ok(a.api); a.reply(plan({effect: 'RECONCILE', affectedDates: [], candidate: null}));
  const pending = a.api.openReconcile({year: 2027, pool_key: 'weekend|M', expected_basis_revision: 2}); await tick();
  const sent = a.plain('requests[0].body'); assert.equal(sent.holidayIntent, 'RECONCILE'); assert.equal(sent.year, 2027); assert.equal(sent.reconcilePolicy, 'PRESERVE_EXISTING');
  assert.match(a.get('hs-content').innerHTML, /1월 1일/); assert.match(a.get('hs-content').innerHTML, /자동 연속/); a.api.close(); await pending;
});
test('reconcile strips local blocker codes and requires another review after choosing a position', async () => {
  const a = app(); a.reply(plan({effect: 'RECONCILE', affectedDates: []}));
  const pending = a.api.openReconcile({year: 2027, pool_key: 'weekend|M', expected_basis_revision: 2, code: 'BASIS_RECONCILE_REQUIRED'}); await tick();
  assert.equal(a.run('requests[0].body.code'), undefined, 'UI diagnostics must not enter the strict API draft');
  a.get('hs-anchor').value = '1'; a.get('hs-anchor').listeners.change.at(-1)();
  assert.equal(await a.api.apply(), false); a.reply(plan({effect: 'RECONCILE', reviewDigest: 'anchor-digest'}));
  a.get('hs-anchor-preview').listeners.click.at(-1)(); await tick();
  assert.equal(a.run('requests.at(-1).body.anchor'), 1); assert.equal(a.get('hs-apply').disabled, false); a.api.close(); await pending;
});
test('cancel during preview ignores a late response and returns keyboard focus', async () => {
  const a = app(); const trigger = a.get('hol-submit'); trigger.focus();
  a.run('fetchWithAdminToken=()=>new Promise(resolve=>globalThis.finishPreview=resolve)');
  const pending = a.api.open(draft); assert.equal(a.api.close(), true); assert.equal(await pending, false); assert.equal(a.doc.activeElement, trigger);
  a.ctx.previewResult = plan(); a.run('finishPreview({ok:true,status:200,json:async()=>previewResult})'); await tick();
  assert.equal(a.get('holiday-supplement-modal').classList.contains('active'), false); assert.equal(a.run('saved.length'), 0);
});
test('successful commit with failed reload only reloads again, never submits another apply', async () => {
  const a = app(); a.reply(plan()); const pending = a.api.open(draft); await tick(); a.reply({status: 'APPLIED'});
  a.run('refreshHolidaySupplementData=async()=>false'); assert.equal(await a.api.apply(), false);
  assert.match(a.get('hs-feedback').textContent, /저장은 완료/); a.run('refreshHolidaySupplementData=async()=>{refreshCount++;return supplementData}');
  assert.equal(await a.api.apply(), true); await pending; assert.equal(a.run('requests.filter(r=>r.url.endsWith("/apply")).length'), 1);
});
test('metadata-aware JSON import uses a dedicated atomic server restore and never optimistic local credit writes', async () => {
  const a = app(); const incoming = {formatVersion: 2, members: [{name: '새 명단', gender: 'F'}], schedules: {}, holidaySupplements: {schema: 1, events: [], bases: [], requiredContract: 1}};
  a.run('answers.push(true)'); a.reply({status: 'restored', dataVersion: 8}); const before = a.plain('[memberList,scheduleMap,supplementData]');
  assert.equal(await a.ctx.OnAirAdmin.importBackupFile({name: 'backup.json', text: async () => JSON.stringify(incoming)}), true);
  assert.equal(a.run('requests[0].url'), '/api/system/import-json'); assert.equal(a.run('requests[0].body.restoreLegacy'), false);
  assert.deepEqual(a.plain('requests[0].body.data'), incoming); assert.equal(a.run('saved.length'), 0); assert.deepEqual(a.plain('[memberList,scheduleMap,supplementData]'), before);
});
test('legacy JSON in an activated database explicitly confirms complete pre-supplement restoration', async () => {
  const a = app(); a.run('supplementData.requiredContract=1;answers.push(true)'); a.reply({status: 'restored'});
  const legacy = {formatVersion: 2, members: [], schedules: {}};
  assert.equal(await a.ctx.OnAirAdmin.importBackupFile({name: 'old.json', text: async () => JSON.stringify(legacy)}), true);
  assert.match(a.run('asked[0].html'), /보완 이전 상태로 전체 복원/);
  assert.match(a.run('asked[0].buttons[0].label'), /보완 이전 상태/); assert.equal(a.run('requests[0].body.restoreLegacy'), true);
});
test('future JSON schemas are rejected and failed full restore leaves all local business state unchanged', async () => {
  const a = app(); const before = a.plain('[memberList,scheduleMap,customHolidays,supplementData]');
  const future = {formatVersion: 3, members: [], schedules: {}, holidaySupplements: {schema: 2, events: [], bases: []}};
  assert.equal(await a.ctx.OnAirAdmin.importBackupFile({name: 'future.json', text: async () => JSON.stringify(future)}), false); assert.equal(a.run('requests.length'), 0);
  a.run('answers.push(true)'); a.reply({detail: '마감월이 포함되어 있습니다.'}, 400);
  const file = {...future, formatVersion: 2, holidaySupplements: {schema: 1, events: [], bases: []}};
  assert.equal(await a.ctx.OnAirAdmin.importBackupFile({name: 'valid.json', text: async () => JSON.stringify(file)}), false);
  assert.deepEqual(a.plain('[memberList,scheduleMap,customHolidays,supplementData]'), before); assert.equal(a.run('saved.length'), 0);
});
test('database snapshot undo shows supplement counts and explicitly forwards legacy-restore intent', async () => {
  const a = app(); a.run('supplementData.requiredContract=1;answers.push(true)');
  a.reply({ok: true, current: {dataVersion: 7, counts: {}, holidaySupplementCount: 2, holidaySupplementSchema: 1}, backup: {counts: {}, holidaySupplementCount: 0, holidaySupplementSchema: 0}});
  a.reply({status: 'restored'}); assert.equal(await a.ctx.OnAirAdmin.undoToSnapshot('old.db', '이전 사본'), true);
  assert.match(a.run('asked[0].html'), /보완 기록/); assert.match(a.run('asked[0].html'), /보완 이전 상태로 전체 복원/);
  assert.equal(a.run('requests.at(-1).body.restoreLegacy'), true);
});
test('unsaved JSON keeps the private server export intact and carries pending changes separately', async () => {
  const a = app(); a.run("supplementData.requiredContract=1;unsavedList=[{at:'2026-11-09 10:00:00',reason:'연결 오류',payload:{schedules:{'2026-11-20':[]}}}];globalThis.downloads=[];downloadBlob=(name,text)=>downloads.push({name,data:JSON.parse(text)})");
  const exported = {formatVersion: 2, members: [], schedules: {'2026-11-20': [{name: '여성'}]}, holidaySupplements: {schema: 1, events: [{original_holiday: null, state: 'ACTIVE'}], bases: [], requiredContract: 1}};
  a.reply(exported); assert.equal(await a.run('downloadUnsaved()'), true);
  const file = a.plain('downloads[0].data'); assert.deepEqual(file.schedules, exported.schedules); assert.deepEqual(file.holidaySupplements, exported.holidaySupplements);
  assert.deepEqual(file.pendingChanges.payload.schedules, {'2026-11-20': []}); assert.match(a.run('toasts.at(-1).message'), /초안.*별도/);
});
test('reconcile repeated weekend names show the correct expanded cycle and preserve a zero anchor', async () => {
  const a = app(); const p = plan({effect: 'RECONCILE'}); p.rotationBasis.pool_key = 'weekend|M'; p.rotationBasis.sequence_snapshot = ['남성1', '남성2', '남성2', '남성1']; a.reply(p);
  const pending = a.api.openReconcile({year: 2027, pool_key: 'weekend|M', expected_basis_revision: 1, anchor: 0}); await tick();
  assert.equal(a.run('requests[0].body.anchor'), 0);
  assert.match(a.get('hs-content').innerHTML, /value="1"[^>]*>남성2 · 위치 2 · 1회차/);
  assert.match(a.get('hs-content').innerHTML, /value="2"[^>]*>남성2 · 위치 3 · 2회차/); a.api.close(); await pending;
});
test('known validation rejection can regenerate the review instead of being stuck in an uncertain retry', async () => {
  const a = app(); a.reply(plan()); const pending = a.api.open(draft); await tick(); a.reply({detail: '현재 기준을 다시 확인하세요.'}, 400);
  assert.equal(await a.api.apply(), false); assert.equal(a.get('hs-apply').disabled, true); assert.equal(a.get('hs-refresh').disabled, false);
  a.reply(plan()); assert.equal(await a.api.refresh(), true); a.api.close(); await pending;
});
test('supported basis drift can be reviewed first then returns to an unsaved holiday preview', async () => {
  const a = app(); const blocked = plan({effect: 'BLOCKED', eligible: false, blockers: [{code: 'BASIS_RECONCILE_REQUIRED', message: '이전 연도 소비가 바뀌었습니다.'}]});
  a.reply(blocked); const pending = a.api.open(draft); await tick(); assert.match(a.get('hs-content').innerHTML, /id="hs-reconcile"/);
  a.reply(plan({effect: 'RECONCILE'})); a.get('hs-reconcile').listeners.click.at(-1)(); await tick();
  assert.equal(a.run('requests.at(-1).body.holidayIntent'), 'RECONCILE'); a.reply({status: 'APPLIED'}); a.reply(plan());
  assert.equal(await a.api.apply(), true); await tick();
  assert.equal(a.run('requests.at(-1).body.holidayIntent'), 'SET_CUSTOM'); assert.equal(a.get('holiday-supplement-modal').classList.contains('active'), true);
  assert.equal(a.run('requests.filter(r=>r.url.endsWith("/apply")).length'), 1); a.api.close(); assert.equal(await pending, false);
});
test('network errors are explained in Korean and the draft stays reviewable', async () => {
  const a = app(); a.run("responses.push(new TypeError('Failed to fetch'))"); const pending = a.api.open(draft); await tick();
  assert.match(a.get('hs-feedback').textContent, /연결|불러오/); assert.doesNotMatch(a.get('hs-feedback').textContent, /Failed to fetch/);
  a.reply(plan()); await a.api.refresh(); a.run("responses.push(new TypeError('Failed to fetch'))"); await a.api.apply();
  assert.doesNotMatch(a.get('hs-feedback').textContent, /Failed to fetch/); a.api.close(); await pending;
});
test('major, fair, empty, past and unsupported-year calendar edits require explicit holiday-only confirmation', async () => {
  assert.equal(typeof app().api.calendarOnlyReason, 'function', 'holiday-only edits remain available');
  for (const scenario of ['major', 'fair', 'empty', 'past', 'unsupported']) {
    const a = app(); a.get('hol-date').value = scenario === 'past' ? '2026-11-06' : scenario === 'unsupported' ? '2030-06-17' : '2026-11-20'; a.get('hol-name').value = '달력 관리';
    if (scenario === 'major') a.get('hol-major').checked = true;
    if (scenario === 'fair') a.run("dutyRule.algoType='FAIR_WEIGHT'");
    if (scenario === 'empty') a.run('scheduleMap={}');
    const before = a.plain('scheduleMap');
    a.run('answers.push(false)'); assert.equal(await a.run('addCustomHoliday()'), false, scenario);
    assert.equal(a.run('saved.length'), 0); assert.equal(a.run('requests.length'), 0);
    assert.match(a.run('asked.at(-1).title'), /공휴일 설정만/);
    a.run('answers.push(true)'); assert.equal(await a.run('addCustomHoliday()'), true, scenario);
    assert.equal(a.run('saved.length'), 1); assert.deepEqual(a.plain('scheduleMap'), before);
    assert.equal(a.run('requests.length'), 0, 'calendar-only edit does not request supplementation');
  }
});
test('calendar-only routing never bypasses an active supplement or an assigned fixed-order review', async () => {
  const a = app(); a.run("supplementData.events=[{date_key:'2026-11-20',state:'ACTIVE'}];dutyRule.algoType='FAIR_WEIGHT';scheduleMap={}");
  assert.equal(a.api.shouldPreview('SET_CUSTOM', '2026-11-20', {is_major:true}), true);
  assert.equal(a.api.calendarOnlyReason('SET_CUSTOM', '2026-11-20', {is_major:true}), '');
  a.run("supplementData.events=[];dutyRule.algoType='FIXED_ORDER';scheduleMap={'2026-11-20':[{name:'남성',role:'평일'}]}");
  assert.equal(a.api.shouldPreview('SET_CUSTOM', '2026-11-20'), true, 'invalid assigned slot still requires server review');
});
test('a fresh-year explicit-anchor requirement retains the picker and cannot apply implicitly', async () => {
  const a=app(); const p=plan({effect:'BLOCKED',eligible:false,blockers:[{code:'ANCHOR_REQUIRED',message:'시작 위치를 직접 확인하세요.'}]});
  p.rotationBasis.revision=0; a.reply(p);
  const pending=a.api.openReconcile({year:2027,pool_key:'holiday|M',expected_basis_revision:0}); await tick();
  assert.match(a.get('hs-content').innerHTML,/id="hs-anchor"/); assert.equal(a.get('hs-apply').disabled,true);
  assert.equal(a.run('requests[0].body.expected_basis_revision'),0); a.api.close(); await pending;
});
test('holiday management limits names to the server field and restores editor focus on review cancel', async () => {
  const a = app(); a.run('openHolidayModal()'); await a.run("OnAirHolidayManagement.select('2026-11-20')");
  a.get('hol-name').value = '가'.repeat(101); const rejected = a.run('addCustomHoliday()'); await tick();
  assert.equal(a.run('requests.length'), 0); assert.equal(await rejected, false);
  assert.match(a.run('toasts.at(-1).message'), /100/);
  a.get('hol-name').value = '추가 공휴일'; const trigger = a.get('hol-submit'); trigger.focus();
  a.get('holiday-modal').children.push(trigger, a.get('hol-name'));
  a.reply(plan()); const pending = a.run('addCustomHoliday()'); await tick(); a.api.close(); await pending; assert.equal(a.doc.activeElement, trigger);
});
