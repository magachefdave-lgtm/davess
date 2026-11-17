'use strict';
// v3.17.13 화면 견고성 회귀 — 저장소 차단, 서버 백업 파일명, CSV 수식 무력화, WebSocket 단일 연결.
// index.html 의 실제 스크립트를 Node VM 에서 실행한다. 화면 DOM·네트워크만 대역이다.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'frontend/dist/index.html'), 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m => m[1])
  .find(s => s.includes('function renderTodayCard()'));
if (!script) { console.error('main script not found'); process.exit(2); }

function app({blocked = false, session = {}, DateClass = Date, extra = {}} = {}) {
  const nodes = new Map();
  const element = id => {
    if (!nodes.has(id)) nodes.set(id, {id, value: '', innerHTML: '', textContent: '', style: {}, dataset: {},
      classList: {add() {}, remove() {}, contains() { return false; }, toggle() {}},
      querySelectorAll: () => [], querySelector: () => null, setAttribute() {}, appendChild() {}, focus() {}});
    return nodes.get(id);
  };
  const values = new Map(Object.entries(session));
  const store = {getItem: k => values.has(k) ? values.get(k) : null, setItem: (k, v) => values.set(k, String(v)), removeItem: k => values.delete(k)};
  const ctx = vm.createContext({console, Date: DateClass, Blob, URL, setTimeout, clearTimeout, setInterval, clearInterval,
    requestAnimationFrame: () => {}, location: {protocol: 'http:', host: '127.0.0.1:8000'},
    document: {addEventListener() {}, getElementById: element, querySelectorAll: () => [], querySelector: () => null,
      createElement: () => ({style: {}, click() {}}), body: {appendChild() {}, removeChild() {}}, head: {appendChild() {}}},
    ...extra});
  ctx.window = ctx; ctx.addEventListener = () => {};
  // A blocked browser throws on the property read itself. The accessor must be defined inside the context —
  // a getter put on the sandbox object from outside is not honoured by node:vm.
  if (blocked) vm.runInContext(`for (const k of ['localStorage','sessionStorage']) Object.defineProperty(globalThis, k,
    {configurable: true, get() { throw new Error('SecurityError: The operation is insecure.'); }});`, ctx);
  else { ctx.localStorage = store; ctx.sessionStorage = store; }
  vm.runInContext(script, ctx, {filename: 'index.html'});
  const run = code => vm.runInContext(code, ctx);
  return {run, ctx, values, element};
}

// [위험] v3.17.12 저장소가 막힌 브라우저(보안 정책, 사생활 보호 창)에서는 최상위의
//   sessionStorage.getItem 이 예외를 던져 화면 스크립트 전체가 멈췄다 — 달력이 아예 안 그려졌다.
test('blocked storage: main script loads and admin token falls back to memory only', async () => {
  const a = app({blocked: true});
  assert.throws(() => a.run('window.sessionStorage'), /SecurityError/, 'fixture blocks property reads');
  assert.equal(a.run('ADMIN_TOKEN'), '');
  a.run(`globalThis.calls=[];renderAuthStatus=()=>{};requestAdminPassword=async()=>'new-pin';
    fetch=async(url,o)=>{calls.push(o.headers['X-Admin-Token']||'');return {status:calls.length===1?401:200,ok:calls.length!==1};};`);
  const res = await a.run(`fetchWithAdminToken('/api/x',{})`);
  assert.equal(res.ok, true);
  assert.equal(a.run('ADMIN_TOKEN'), 'new-pin');
  assert.deepEqual(JSON.parse(a.run('JSON.stringify(calls)')), ['', 'new-pin']);
});
test('available storage: admin token is restored, replaced and cleared through sessionStorage', async () => {
  const a = app({session: {sd_admin_token: 'old-pin'}});
  assert.equal(a.run('ADMIN_TOKEN'), 'old-pin');
  a.run(`globalThis.n=0;renderAuthStatus=()=>{};requestAdminPassword=async()=>'new-pin';
    fetch=async()=>{n++;return {status:n===1?401:200,ok:n!==1};};`);
  await a.run(`fetchWithAdminToken('/api/x',{})`);
  assert.equal(a.values.get('sd_admin_token'), 'new-pin');
  a.run(`n=0;requestAdminPassword=async()=>null;`);
  await a.run(`fetchWithAdminToken('/api/x',{})`);
  assert.equal(a.values.has('sd_admin_token'), false);
});

// [위험] 서버에 만든 파일의 이름을 브라우저 시계로 다시 만들면 실제 파일을 찾을 수 없다.
// 서버가 반환한 경로를 그대로 보여 주고, 브라우저 날짜나 캐시로 백업을 만들지 않는다.
async function serverBackupNotice(instant, name) {
  const Fixed = class extends Date { constructor(...args) { if (args.length) super(...args); else super(instant); } static now() { return instant; } };
  const a = app({DateClass: Fixed});
  a.ctx.result = {ok: true, name, path: 'server/back-up/' + name};
  a.run(`SYSTEM_MODE='ADMIN';globalThis.downloads=[];downloadBlob=(...args)=>downloads.push(args);showToast=()=>{};
    fetchWithAdminToken=async()=>({ok:true,status:200,json:async()=>result});
    OnAirUI={open:(id,title,body)=>{globalThis.backupNotice=body;}};`);
  assert.equal(await a.run('backupSystem()'), true);
  assert.equal(a.run('downloads.length'), 0);
  return a.run('backupNotice');
}
test('server backup filename is preserved around the Korea/UTC date boundary', async () => {
  const name = 'smart_duty_backup_20261014_083000_abcdef.json';
  assert.match(await serverBackupNotice(Date.UTC(2026, 9, 13, 23, 30), name), new RegExp(name.replace('.', '\\.')));
});
test('incorrect browser date never changes the server backup filename', async () => {
  const name = 'smart_duty_backup_20261105_120000_abcdef.json';
  const notice = await serverBackupNotice(Date.UTC(2000, 0, 1), name);
  assert.ok(notice.includes(name));
  assert.ok(!notice.includes('20000101'));
});

// [위험] 이름·메모·이력은 누구나 입력할 수 있다. '=HYPERLINK(...)' 같은 값이 CSV 에 그대로 들어가면
//   관리자가 엑셀로 여는 순간 수식으로 실행된다.
test('csvCell neutralizes formula prefixes and quotes separators including CR', () => {
  const a = app();
  const cell = v => a.run(`csvCell(${JSON.stringify(v)})`);
  for (const lead of ['=', '+', '-', '@']) assert.equal(cell(lead + 'SUM(A1)'), "'" + lead + 'SUM(A1)', lead);
  assert.equal(cell('\tcmd'), "'\tcmd");
  assert.equal(cell('\rcmd'), '"\'\rcmd"');
  assert.equal(cell('a\rb'), '"a\rb"');
  assert.equal(cell('a,b'), '"a,b"');
  assert.equal(cell('say "hi"'), '"say ""hi"""');
  assert.equal(cell('a\nb'), '"a\nb"');
  assert.equal(cell('김민수'), '김민수');
  assert.equal(cell('2026-10-14'), '2026-10-14');
  assert.equal(a.run('csvCell(-3)'), '-3', 'numbers are values, not formulas');
  assert.equal(a.run('csvCell(null)'), '');
});
test('monthly and yearly CSV exports neutralize user-entered names and notes', () => {
  const a = app();
  a.run(`curYear=2026;curMonth=10;memberList=[{name:'=HYPERLINK("x")',gender:'M'},{name:'정상',gender:'F'}];
    scheduleMap={'2026-10-05':[{name:'=HYPERLINK("x")'},{name:'정상'}]};commentMap={'2026-10-05':'@SUM(1)'};noDutyMap={};
    changeHistory=[{timestamp:'2026-10-01 09:00:00',type:'+수정',dateKey:'2026-10-05',details:'-2+3'}];
    finalHolidays={};absences=[];globalThis.files=[];downloadBlob=(name,body)=>files.push(body);showToast=()=>{};`);
  a.run('exportMonthlyCsv()');
  const monthly = a.run('files[0]');
  assert.match(monthly, /2026-10-05,월,[^,]*,"'=HYPERLINK\(""x""\)",정상,'@SUM\(1\)/);
  assert.doesNotMatch(monthly, /(^|,)"?=HYPERLINK/m);
  a.run('exportFullExcelWithHistory()');
  const yearly = a.run('files[1]');
  assert.match(yearly, /"'\+수정"/);
  assert.match(yearly, /"'-2\+3"/);
  assert.doesNotMatch(yearly, /(^|,)"=HYPERLINK/m);
});

// [위험] [연결 다시 시도]를 10초 재연결 루프와 겹쳐 누르면 initWebSocket 이 두 번 불려
//   소켓이 둘 생기고, 같은 알림을 두 번 처리했다(알림 중복·데이터 이중 로드).
function socketApp() {
  const timers = [];
  const sockets = [];
  class FakeSocket { constructor(url) { this.url = url; this.readyState = 0; sockets.push(this); } close() { this.readyState = 3; } }
  const a = app({extra: {WebSocket: FakeSocket,
    setTimeout: (fn, ms) => { timers.push({fn, ms}); return timers.length; }, clearTimeout: id => { if (timers[id - 1]) timers[id - 1].fn = null; }}});
  a.run(`SERVER_CONNECTED=true;SYSTEM_MODE='VIEWER';globalThis.loads=0;fetchAllData=async()=>{loads++;};showToast=()=>{};`);
  const fire = () => { const pending = timers.splice(0); for (const t of pending) if (t.fn) t.fn(); };
  return {a, sockets, timers, fire};
}
test('initWebSocket keeps a single open or connecting socket', () => {
  const {a, sockets} = socketApp();
  a.run('initWebSocket(); initWebSocket();');
  assert.equal(sockets.length, 1);
  sockets[0].readyState = 1; sockets[0].onopen();
  a.run('initWebSocket()');
  assert.equal(sockets.length, 1);
  assert.equal(sockets[0].url, 'ws://127.0.0.1:8000/ws/duty');
});
test('after a close, retry and manual reconnect together create exactly one new socket', () => {
  const {a, sockets, timers, fire} = socketApp();
  a.run('initWebSocket()'); sockets[0].readyState = 3; sockets[0].onclose();
  assert.equal(timers.filter(t => t.fn).length, 1, 'one retry is scheduled');
  sockets[0].onclose();   // a duplicate close event must not schedule a second retry
  assert.equal(timers.filter(t => t.fn).length, 1);
  a.run('initWebSocket()');            // manual reconnect while the 5s retry is pending
  fire();                               // the pending retry must not add another socket
  assert.equal(sockets.length, 2);
  // the replaced socket's late events are ignored
  const before = a.run('loads');
  sockets[0].onmessage({data: JSON.stringify({event: 'DATA_SYNCED'})});
  assert.equal(a.run('loads'), before);
  sockets[1].onmessage({data: JSON.stringify({event: 'DATA_SYNCED'})});
  assert.equal(a.run('loads'), before + 1);
  // a late close event from the replaced socket must not drop the live one or schedule another retry
  sockets[1].readyState = 1;
  sockets[0].onclose();
  a.run('initWebSocket()');
  assert.equal(sockets.length, 2);
  assert.equal(timers.filter(t => t.fn).length, 0);
});
test('concurrent retryConnection calls (button + reconnect loop) load data and open a socket once', async () => {
  const {a, sockets} = socketApp();
  a.run(`SERVER_CONNECTED=false;fetchSystemInfo=async()=>{await null;SERVER_CONNECTED=true;};`);
  await a.run('Promise.all([retryConnection(), retryConnection(true)])');
  assert.equal(a.run('loads'), 1);
  assert.equal(sockets.length, 1);
});
test('websocket is not opened while the server is disconnected', () => {
  const {a, sockets} = socketApp();
  a.run('SERVER_CONNECTED=false; initWebSocket()');
  assert.equal(sockets.length, 0);
});
