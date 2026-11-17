'use strict';
// v3.16.0 화면 쪽 규칙. 각 시험 위의 [위험] 은 그 시험이 막는 실제 사고다.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const htmlPath = process.env.ONAIR_FRONTEND || path.resolve(__dirname, '../../frontend/dist/index.html');
const html = fs.readFileSync(htmlPath, 'utf8');
const main = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(x => x[1]).find(s => s.includes('function generateScheduleByRules('));
for (const name of ['toggleMonthClose', 'loadMyRequests', 'downloadHistoryCsv', 'loadDiagClients', 'previewBackup', 'restoreBackup']) {
  if (!main.includes(`function ${name}(`)) { console.error(`${name} 를 찾지 못했습니다 — 함수 이름이 바뀌었는지 확인하세요.`); process.exit(2); }
}

function app() {
  const nodes = new Map();
  const element = id => {
    if (!nodes.has(id)) nodes.set(id, {
      id, innerHTML: '', textContent: '', value: '', checked: false, title: '', className: '',
      style: {setProperty: () => {}}, dataset: {}, children: [],
      classList: {add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false},
      appendChild(child) { this.children.push(child); }, querySelectorAll: () => [], click: () => {}
    });
    return nodes.get(id);
  };
  const storage = {getItem: () => null, setItem: () => {}, removeItem: () => {}};
  const created = [];
  const context = vm.createContext({
    Date, console, JSON, Math, Set, Map, Promise, encodeURIComponent,
    sessionStorage: storage, localStorage: storage,
    document: {addEventListener: () => {}, getElementById: element, querySelector: () => null, querySelectorAll: () => [],
               createElement: tag => { const e = {tag, className: '', textContent: '', title: '', style: {}, click() { created.push(this); }}; return e; },
               body: {appendChild: () => {}, removeChild: () => {}}},
    window: {addEventListener: () => {}},
    setTimeout, clearTimeout, setInterval, clearInterval, requestAnimationFrame: () => {}
  });
  vm.runInContext(main, context, {filename: htmlPath});
  const run = code => vm.runInContext(code, context);
  run(`SYSTEM_MODE='ADMIN'; renderCalendar=()=>{}; globalThis.toasts=[]; showToast=(m,e)=>{toasts.push({m,e});};`);
  return {run, element, created};
}

// [위험] 마감한 달을 자동 배정하면 미리보기까지 다 보여 준 뒤 확정에서 서버가 거부해 헛수고가 된다.
test('auto-assign stops before computing when a month in scope is closed', async () => {
  const {run} = app();
  run(`curYear=2026; curMonth=11; closedMonths=['2026-11']; memberList=[{name:'F1',gender:'F'}];
       scheduleMap={}; noDutyMap={}; commentMap={}; customHolidays={};
       globalThis.saved=0; syncFullDataToBackend=async()=>{saved++; return true;};`);
  await run(`generateScheduleByRules({silent:true, scope:'MONTH'})`);
  assert.equal(run('saved'), 0);
  assert.match(run('toasts[0].m'), /마감/);
  run(`closedMonths=['2026-10']`);
  await run(`generateScheduleByRules({silent:true, scope:'MONTH'})`);
  assert.equal(run('saved'), 1, '다른 달이 마감됐을 때는 그대로 배정한다');
});

test('closed months are marked on the month tabs', () => {
  const {run, element} = app();
  run(`curYear=2026; curMonth=11; closedMonths=['2026-10']; updateYearMonthUI();`);
  const tabs = element('month-tabs-container').children;
  assert.equal(tabs.length, 12);
  assert.match(tabs[9].className, /m-tab-closed/);
  assert.doesNotMatch(tabs[10].className, /m-tab-closed/);
});

test('month close requires a reason to reopen and sends baseVersion', async () => {
  const {run} = app();
  run(`curYear=2026; curMonth=9; closedMonths=['2026-09']; dataVersion=7; globalThis.calls=[];
       fetchAllData=async()=>{}; prompt=()=> '  '; confirm=()=>true;
       fetchWithAdminToken=async(url,opt)=>{calls.push({url, body: JSON.parse(opt.body)}); return {ok:true,status:200,json:async()=>({})};};`);
  await run('toggleMonthClose()');
  assert.equal(run('calls.length'), 0, '사유 없이 해제 요청을 보내면 안 된다');
  run(`prompt=()=> '수당 정정';`);
  await run('toggleMonthClose()');
  const sent = run('calls[0]');
  assert.equal(sent.url, '/api/duty/month-close');
  assert.deepEqual({...sent.body}, {month: '2026-09', closed: false, reason: '수당 정정', baseVersion: 7});
});

// [위험] 공용 관리자 암호라 누가 바꿨는지 화면에서 알 수 없었다. 서버가 기록한 PC·계정을 보여 준다.
test('history table shows server-recorded PC and account', () => {
  const {run, element} = app();
  run(`curYear=2026; curMonth=10; changeHistory=[{timestamp:'2026-10-06 09:00:00', type:'수동 변경', dateKey:'2026-10-07',
        details:'<b>x</b>', host:'LAB-PC1', actor:'hong'}]; openHistoryModal('ALL');`);
  const body = element('history-tbody').innerHTML;
  assert.match(body, /LAB-PC1 · hong/);
  assert.match(body, /&lt;b&gt;x&lt;\/b&gt;/, '내용은 이스케이프되어야 한다');
});

// [위험] 대체 근무 0점 여부를 관리자가 바로잡을 길이 없었다.
test('approving a substitute sends the admin-chosen zero score', async () => {
  const {run, element} = app();
  run(`approvalCache=[{id:5, request_type:'SUBSTITUTE', requester_name:'A', substitute_name:'B', target_date:'2026-10-10', is_zero_score:1}];
       scheduleMap={'2026-10-10':[{name:'A'}]}; confirmConsecutiveRisk=()=>true; globalThis.bodies=[];
       fetchAllData=async()=>{}; openApprovalModal=async()=>{}; openVacancyModal=()=>{};
       fetchWithAdminToken=async(url,opt)=>{bodies.push(JSON.parse(opt.body)); return {ok:true,status:200,json:async()=>({})};};`);
  element('req-zero-5').checked = false;
  await run(`processApproval(5, 'APPROVE')`);
  assert.equal(run('bodies[0].zero_score'), false);
  // 실제 화면에서는 맞교환 행에 0점 칸이 없다(getElementById 가 null).
  run(`approvalCache=[{id:6, request_type:'SWAP', requester_name:'A', swap_worker:'B', target_date:'2026-10-10', swap_date:'2026-10-11'}];
       { const realGet = document.getElementById; document.getElementById = id => id === 'req-zero-6' ? null : realGet(id); }`);
  await run(`processApproval(6, 'APPROVE')`);
  assert.equal(run(`'zero_score' in bodies[1]`), false, '맞교환에는 0점 값을 보내지 않는다');
});

// [위험] 반려 사유가 브라우저 저장소에만 있어 다른 PC 에서는 결과를 볼 수 없었다.
test('my requests lists results with reject reason, escaped', async () => {
  const {run, element} = app();
  element('myreq-name').value = '홍길동';
  run(`fetch=async(url)=>({ok:true, json:async()=>({requests:[
        {id:1, request_type:'SUBSTITUTE', requestLabel:'대체 근무', target_date:'2026-10-10', substitute_name:'<i>B</i>',
         status:'REJECTED', created_at:'2026-10-01 10:00:00', processed_at:'2026-10-02 10:00:00', reject_reason:'인원 부족'}]})});`);
  await run('loadMyRequests()');
  const body = element('myreq-tbody').innerHTML;
  assert.match(body, /반려/);
  assert.match(body, /인원 부족/);
  assert.match(body, /&lt;i&gt;B&lt;\/i&gt;/);
});

test('history CSV link carries the chosen range and rejects a bad one', () => {
  const {run, created} = app();
  run(`prompt=()=> '2026-01-01~2026-03-31'; downloadHistoryCsv();`);
  assert.equal(created.at(-1).href, '/api/duty/history-export?start=2026-01-01&end=2026-03-31');
  run(`prompt=()=> '올해'; downloadHistoryCsv();`);
  assert.equal(created.length, 1, '형식이 틀리면 내려받지 않는다');
  run(`prompt=()=> ''; downloadHistoryCsv();`);
  assert.equal(created.at(-1).href, '/api/duty/history-export');
});

test('banner warns when exposed without password and when built-in holidays are about to end', () => {
  const {run} = app();
  run(`globalThis.alertText=''; setSysAlert=t=>{alertText=t;}; renderAuthStatus=()=>{};`);
  run(`renderSysBanners({dbOk:true, version:'3.16.0', exposedWithoutPassword:true})`);
  assert.match(run('alertText'), /암호 없이 사내망/);
  const expectHorizon = run('DATA_MAX_YEAR - THIS_YEAR <= 1');
  assert.equal(/내장 공휴일 자료가/.test(run('alertText')), expectHorizon);
});

test('restore asks for confirmation and sends the preview version', async () => {
  const {run} = app();
  run(`globalThis.sent=[]; confirm=()=>false; fetchAllData=async()=>{}; openDiagModal=async()=>{};
       fetchWithAdminToken=async(url,opt)=>{sent.push({url, body: opt && opt.body}); return {ok:true,status:200,json:async()=>({safetyCopy:'prerestore_x.db'})};};`);
  await run(`restoreBackup('smart_duty_2026-10-01.db', 12)`);
  assert.equal(run('sent.length'), 0, '확인을 취소하면 아무것도 보내지 않는다');
  run(`confirm=()=>true;`);
  await run(`restoreBackup('smart_duty_2026-10-01.db', 12)`);
  assert.equal(run('sent[0].url'), '/api/system/restore');
  assert.deepEqual({...JSON.parse(run('sent[0].body'))}, {name: 'smart_duty_2026-10-01.db', baseVersion: 12});
  // v3.18.0 파일 이름(prerestore_x.db) 대신 사람이 읽는 이름으로 알린다.
  assert.doesNotMatch(run('toasts.at(-1).m'), /prerestore_x\.db/);
  assert.match(run('toasts.at(-1).m'), /되돌렸습니다\. 되돌리기 직전 상태도 따로 보관했습니다\./);
});
