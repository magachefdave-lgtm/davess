'use strict';
// v3.18.0 문구·연결 상태 회귀.
//   · friendlyFetchError: 네트워크 끊김·500·서버 안내문(detail)을 한국어 한 문장으로 (영어·숫자 코드 노출 금지)
//   · josa: 받침에 맞는 조사
//   · 실제 흐름(관리자 저장·직원 신청·내 신청 내역)에서 fetch 실패·500 이 영어로 새지 않음
//   · 연결 상태: 프로그램 꺼짐 → 10초 안에 표시, 복구되면 배너가 사라지고 안내는 한 번,
//                공유 폴더 끊김 → 배너 문구(경로 없음), 끊긴 동안 '다른 PC에서 바뀜' 거짓 알림 없음
//   · 정적 검사: 화면 문구에 개발 용어가 없고(허용 목록 제외), 예시 자리표시자가 없음
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const dist = path.join(root, 'frontend/dist');
const html = fs.readFileSync(path.join(dist, 'index.html'), 'utf8');
const base = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m => m[1]).find(s => s.includes('function renderTodayCard()'));
const sidecar = name => fs.readFileSync(path.join(dist, name), 'utf8');

function app({mode = 'VIEWER', sidecars = []} = {}) {
  const nodes = new Map(), local = new Map(), session = new Map();
  const node = id => {
    if (!nodes.has(id)) {
      const classes = new Set();
      nodes.set(id, {id, isConnected: true, value: '', checked: false, disabled: false, hidden: false, innerHTML: '', textContent: '',
        style: {}, dataset: {}, options: [], children: [], title: '',
        classList: {add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x),
          toggle(x, v) { (v === undefined ? !classes.has(x) : v) ? classes.add(x) : classes.delete(x); }},
        _classes: classes, setAttribute(k, v) { this[k] = String(v); }, removeAttribute(k) { delete this[k]; }, getAttribute(k) { return this[k]; },
        addEventListener() {}, appendChild() {}, insertAdjacentElement() {}, insertAdjacentHTML() {}, querySelector: () => null, querySelectorAll: () => [],
        focus() {}, closest: () => null, remove() {}});
    }
    return nodes.get(id);
  };
  const storage = map => ({getItem: k => map.has(k) ? map.get(k) : null, setItem: (k, v) => map.set(k, String(v)), removeItem: k => map.delete(k)});
  const timers = [];
  let clock = Date.UTC(2026, 9, 14, 0, 5);   // 09:05 KST
  const FakeDate = class extends Date { constructor(...a) { if (a.length) super(...a); else super(clock); } static now() { return clock; } };
  const document = {getElementById: node, querySelectorAll: () => [], querySelector: () => null, events: {},
    addEventListener(type, fn) { (this.events[type] ??= []).push(fn); }, createElement: tag => ({...node('new-' + nodes.size), tagName: tag}),
    head: node('head'), body: node('body'), activeElement: null, readyState: 'complete'};
  const ctx = vm.createContext({console, Date: FakeDate, document, window: {addEventListener() {}}, localStorage: storage(local), sessionStorage: storage(session),
    setTimeout: (fn, ms) => { timers.push({fn, ms, at: clock + (ms || 0), kind: 'timeout'}); return timers.length; },
    clearTimeout: id => { if (timers[id - 1]) timers[id - 1].fn = null; },
    setInterval: (fn, ms) => { timers.push({fn, ms, at: clock + ms, kind: 'interval'}); return timers.length; },
    clearInterval: id => { if (timers[id - 1]) timers[id - 1].fn = null; },
    requestAnimationFrame: fn => fn(), confirm: () => true, alert: () => { throw Error('native alert'); }, AbortController});
  ctx.window = ctx; ctx.addEventListener = () => {};
  vm.runInContext(base, ctx);
  const run = s => vm.runInContext(s, ctx);
  run(`SYSTEM_MODE='${mode}';SERVER_CONNECTED=true;dataLoaded=true;todayStr='2026-10-14';myName='A';curYear=2026;curMonth=10;
    memberList=[{name:'A',gender:'F'},{name:'B',gender:'F'},{name:'C',gender:'M'}];absenceTypes=['연차','교육'];closedMonths=[];absences=[];
    scheduleMap={'2026-10-15':[{name:'A'},{name:'B'}],'2026-10-16':[{name:'A'}]};dataVersion=5;
    globalThis.toasts=[];globalThis.notifs=[];showToast=(m,e)=>toasts.push({m:String(m),e:!!e});addNotif=(t)=>notifs.push(String(t));
    checkRequestOutcomes=async()=>{};renderCalendar=()=>{};renderMemberModalList=()=>{};renderAbsenceTypeUI=()=>{};renderAbsenceList=()=>{};
    updateAbsenceDateLabels=()=>{};openVacancyModal=()=>{};offerUndo=()=>{};initWebSocket=()=>{};updateYearMonthUI=()=>{};
    window.OnAirUI={escape:esc,dateLabel:x=>x,open:(id)=>{document.getElementById(id).classList.add('active')},close:id=>document.getElementById(id).classList.remove('active')};`);
  for (const name of sidecars) vm.runInContext(sidecar(name), ctx, {filename: name});
  const advance = ms => { clock += ms; };
  const fireDue = async () => {
    for (let guard = 0; guard < 50; guard++) {
      const due = timers.filter(t => t.fn && t.at <= clock);
      if (!due.length) break;
      for (const t of due) {
        const fn = t.fn;
        if (t.kind === 'interval') t.at = clock + t.ms; else t.fn = null;
        await fn();
      }
    }
  };
  return {run, node, ctx, timers, advance, fireDue, json: s => JSON.parse(run('JSON.stringify(' + s + ')'))};
}
const ENGLISH = /\b(?!WLR\b|OnAir\b|F5\b|JSON\b)[A-Za-z]{3,}\b|HTTP|\b[45]\d\d\b/;

// ── friendlyFetchError ─────────────────────────────────────────
test('friendlyFetchError: network failure, 5xx without detail, Korean detail, English detail and parse errors', () => {
  const a = app();
  const f = (code) => a.run(code);
  assert.equal(f(`friendlyFetchError(new TypeError('Failed to fetch'), null, '저장')`),
    '서버와 연결이 끊겨 저장하지 못했습니다. 프로그램이 켜져 있는지 확인한 뒤 다시 시도하세요.');
  assert.equal(f(`friendlyFetchError(new TypeError('NetworkError when attempting to fetch resource.'), null, '신청')`),
    '서버와 연결이 끊겨 신청하지 못했습니다. 프로그램이 켜져 있는지 확인한 뒤 다시 시도하세요.');
  assert.equal(f(`friendlyFetchError(null, {status:500}, '저장', {})`),
    '저장 중 오류가 났습니다. 잠시 후 다시 시도하고, 계속되면 관리자에게 알려 주세요.');
  assert.equal(f(`friendlyFetchError(null, {status:502}, '결재', {detail:'Bad Gateway'})`),
    '결재 중 오류가 났습니다. 잠시 후 다시 시도하고, 계속되면 관리자에게 알려 주세요.');
  assert.equal(f(`friendlyFetchError(null, {status:409}, '신청', {detail:'같은 날짜의 맞교환 신청이 이미 결재 대기 중입니다.'})`),
    '같은 날짜의 맞교환 신청이 이미 결재 대기 중입니다.');
  assert.equal(f(`friendlyFetchError(apiError({status:400}, {detail:[{msg:'field required'}]}), null, '신청')`),
    '신청하지 못했습니다. 화면을 새로고침(F5)한 뒤 다시 시도하고, 계속되면 관리자에게 알려 주세요.');
  assert.equal(f(`friendlyFetchError(new SyntaxError('Unexpected token < in JSON at position 0'), null, '불러오기')`),
    '불러오기 중 오류가 났습니다. 잠시 후 다시 시도하고, 계속되면 관리자에게 알려 주세요.');
  assert.equal(f(`friendlyFetchError(new TypeError('Failed to fetch'), null, '신청 내역 불러오기')`),
    '서버와 연결이 끊겨 신청 내역을 불러오지 못했습니다. 프로그램이 켜져 있는지 확인한 뒤 다시 시도하세요.');
  assert.match(f(`friendlyFetchError(null, {status:503}, '저장', {})`), /^공유 폴더에 연결할 수 없어 저장하지 못했습니다/);
  assert.match(f(`friendlyFetchError(null, {status:401}, '복원', {})`), /관리자 암호/);
  for (const status of [400, 401, 403, 404, 409, 423, 428, 500, 503]) {
    const text = f(`friendlyFetchError(null, {status:${status}}, '저장', {detail:'Internal Server Error'})`);
    assert.doesNotMatch(text, ENGLISH, `${status}: ${text}`);
  }
});

// ── josa ───────────────────────────────────────────────────────
test('josa picks the particle from the final consonant, ignoring a trailing parenthesis', () => {
  const a = app();
  const j = (w, p) => a.run(`josa(${JSON.stringify(w)}, ${JSON.stringify(p)})`);
  assert.equal(j('김민수', '을/를'), '김민수를');
  assert.equal(j('홍길동', '을/를'), '홍길동을');
  assert.equal(j('김민수', '은/는'), '김민수는');
  assert.equal(j('박정민', '이/가'), '박정민이');
  assert.equal(j('맞교환', '과/와'), '맞교환과');
  assert.equal(j('대체 근무', '과/와'), '대체 근무와');
  assert.equal(j('2026년 10월', '으로/로'), '2026년 10월로', 'ㄹ 받침은 로');
  assert.equal(j('부산', '으로/로'), '부산으로');
  assert.equal(j('서울', '으로/로'), '서울로');
  assert.equal(j('바다', '으로/로'), '바다로');
  assert.equal(j('10월 14일(수)', '을/를'), '10월 14일(수)을', '괄호 앞 낱말(14일)에 맞춘다');
  assert.equal(j('3', '이/가'), '3이');
  assert.equal(j('2', '이/가'), '2가');
  assert.equal(j('', '을/를'), '를');
});

// ── 실제 흐름에서 영어가 새지 않는다 ───────────────────────────
test('admin save: fetch rejection and HTTP 500 show Korean only and keep the unsaved change', async () => {
  const a = app({mode: 'ADMIN'});
  a.run(`CONN.lastGoodAt=new Date(); restoreCommittedState=()=>{}; keepUnsaved=(p,r)=>{globalThis.kept=r;};
    fetchWithAdminToken=async()=>{throw new TypeError('Failed to fetch');};`);
  assert.equal(await a.run(`syncFullDataToBackend({schedules:{}})`), false);
  let last = a.json('toasts').at(-1);
  assert.equal(last.e, true);
  assert.equal(last.m, '⛔ 서버와 연결이 끊겨 저장하지 못했습니다. 프로그램이 켜져 있는지 확인한 뒤 다시 시도하세요.');
  assert.doesNotMatch(a.run('kept'), ENGLISH);
  a.run(`CONN.server=true; fetchWithAdminToken=async()=>({ok:false,status:500,json:async()=>{throw new SyntaxError('Unexpected token');}});`);
  assert.equal(await a.run(`syncFullDataToBackend({schedules:{}})`), false);
  last = a.json('toasts').at(-1);
  assert.equal(last.m, '⛔ 저장 중 오류가 났습니다. 잠시 후 다시 시도하고, 계속되면 관리자에게 알려 주세요.');
  assert.doesNotMatch(last.m, ENGLISH);
});
test('employee request: rejection, 500 and a non-JSON error body never surface English or a JSON parse error', async () => {
  const a = app({sidecars: ['usability_requests.js']});
  a.run(`CONN.lastGoodAt=new Date(); askConfirm=async()=>true;`);
  // 실제 버튼 흐름(submitAbsenceRequest)으로 보낸다.
  for (const [id, v] of Object.entries({'absence-req-name': 'A', 'absence-req-type': '연차', 'absence-req-start': '2026-10-20', 'absence-req-end': '2026-10-21', 'absence-req-reason': '개인 사유'})) a.node(id).value = v;
  a.run(`fetch=async()=>{throw new TypeError('Failed to fetch');};`);
  await a.run('submitAbsenceRequest()');
  let last = a.json('toasts').at(-1);
  assert.equal(last.m, '서버와 연결이 끊겨 신청하지 못했습니다. 프로그램이 켜져 있는지 확인한 뒤 다시 시도하세요.');
  a.run(`CONN.server=true; fetch=async()=>({ok:false,status:500,json:async()=>{throw new SyntaxError('Unexpected token < in JSON');}});`);
  await a.run('submitAbsenceRequest()');
  last = a.json('toasts').at(-1);
  assert.equal(last.m, '신청 중 오류가 났습니다. 잠시 후 다시 시도하고, 계속되면 관리자에게 알려 주세요.');
  a.run(`fetch=async()=>({ok:false,status:423,json:async()=>({detail:'마감된 달(2026-10)은 변경 신청을 받을 수 없습니다. 관리자에게 문의해 주세요.'})});`);
  await a.run('submitAbsenceRequest()');
  assert.match(a.json('toasts').at(-1).m, /^마감된 달/);
  for (const t of a.json('toasts')) assert.doesNotMatch(t.m, ENGLISH, t.m);
  assert.equal(a.node('absence-req-reason').value, '개인 사유', '입력한 사유는 그대로');
});
test('my-request list: a failed load reads in Korean', async () => {
  const a = app({sidecars: ['usability_requests.js']});
  a.node('myreq-name').value = 'A';
  a.run(`fetch=async()=>{throw new TypeError('Failed to fetch');};`);
  await a.run('loadMyRequests()');
  assert.match(a.node('myreq-tbody').innerHTML, /서버와 연결이 끊겨 신청 내역을 불러오지 못했습니다/);
  assert.doesNotMatch(a.node('myreq-tbody').innerHTML.replace(/<[^>]*>/g, ''), ENGLISH);
});

// ── 연결 상태 ──────────────────────────────────────────────────
function connected(mode = 'VIEWER') {
  const a = app({mode});
  a.run(`globalThis.serverUp=true; globalThis.info={mode:'${mode}',version:'3.18.0',dbOk:true,dataVersion:5,pendingCount:0,lastWrite:{}};
    globalThis.loads=0;
    fetch=async(url)=>{
      if(!serverUp) throw new TypeError('Failed to fetch');
      if(url==='/api/system/ping') return {ok:true,status:200,json:async()=>({ok:true})};
      if(url==='/api/system/info') return {ok:true,status:200,json:async()=>JSON.parse(JSON.stringify(info))};
      if(url==='/api/duty/all-data'){ if(!info.dbOk) return {ok:false,status:503,json:async()=>({detail:'공유 폴더에 연결할 수 없습니다.'})};
        loads++; return {ok:true,status:200,json:async()=>({members:memberList,schedules:scheduleMap,dataVersion:info.dataVersion,closedMonths:[]})}; }
      return {ok:true,status:200,json:async()=>({})};
    };`);
  return a;
}
const alertText = a => a.node('conn-alert').innerHTML.replace(/<[^>]*>/g, '');
test('connection: the heartbeat is frequent enough to show a stopped program within 10 seconds', () => {
  const a = connected();
  assert.ok(a.run('HEARTBEAT_MS + HEARTBEAT_TIMEOUT_MS') < 10000);
  a.run('startHeartbeat()');
  assert.ok(a.timers.some(t => t.kind === 'interval' && t.ms <= 5000), 'heartbeat interval ≤ 5s');
});
test('connection: program stops → indicator and banner within 10s; recovery clears both and announces once', async () => {
  const a = connected();
  await a.run('fetchAllData()');
  a.run('startHeartbeat(); renderConnState();');
  assert.equal(a.node('conn-indicator').textContent, '연결됨');
  assert.equal(a.node('conn-alert').innerHTML, '');
  a.run('serverUp=false');
  a.advance(4000); await a.fireDue();
  assert.equal(a.run('CONN.server'), false);
  assert.match(a.node('conn-indicator').textContent, /^연결 끊김 · \d\d:\d\d 기준 표시 중$/);
  assert.match(alertText(a), /프로그램과 연결이 끊겼습니다 — 프로그램이 켜져 있는지 확인하세요/);
  assert.match(a.node('conn-alert').innerHTML, />다시 연결</);
  assert.equal(a.run('SYSTEM_MODE'), 'VIEWER');
  assert.equal(a.run('dataLoaded'), true, '마지막 당직표는 그대로 보여 준다');
  a.advance(4000); await a.fireDue();   // 계속 끊겨 있어도 알림이 쌓이지 않는다
  const toastsDuring = a.json('toasts').length;
  assert.equal(toastsDuring, 0);
  a.run('serverUp=true');
  a.advance(4000); await a.fireDue();
  assert.equal(a.run('CONN.server'), true);
  assert.equal(a.node('conn-indicator').textContent, '연결됨');
  assert.equal(a.node('conn-alert').innerHTML, '');
  const toasts = a.json('toasts').map(t => t.m);
  assert.deepEqual(toasts.filter(m => /다시 연결되어 최신 당직표를 불러왔습니다/.test(m)).length, 1);
  assert.equal(a.json('notifs').filter(n => /다른 PC/.test(n)).length, 0);
});
test('connection: a send while the program is down is blocked with a hint and the typed text stays', async () => {
  const a = connected();
  await a.run('fetchAllData()');
  a.run('serverUp=false'); await a.run('checkHeartbeat()');
  const blocked = [];
  const target = {closest: () => ({})};
  for (const fn of a.ctx.document.events.click || []) fn({target, preventDefault() { blocked.push('pd'); }, stopImmediatePropagation() { blocked.push('stop'); }});
  assert.deepEqual(blocked, ['pd', 'stop']);
  assert.match(a.json('toasts').at(-1).m, /프로그램과 연결이 끊겨 보낼 수 없습니다\. 연결되면 다시 보낼 수 있습니다\. 입력한 내용은 그대로 남아 있습니다\./);
});
test('connection: shared folder outage → banner without a path, no false refresh notices, recovery clears and announces once', async () => {
  for (const mode of ['VIEWER', 'ADMIN']) {
    const a = connected(mode);
    await a.run('fetchAllData()');
    a.run('startPendingPoll(); renderConnState();');
    a.run(`info={...info,dbOk:false,dataVersion:0,detail:'공유 폴더에 연결할 수 없습니다.',dbPath:'\\\\\\\\fs01\\\\duty\\\\smart_duty.db'}`);
    a.advance(30000); await a.fireDue();
    assert.equal(a.run('CONN.shared'), false, mode);
    const text = alertText(a);
    assert.match(text, /^⚠️ 공유 폴더에 연결할 수 없습니다 · 표시 중인 표는 \d\d:\d\d 기준 · 연결되면 자동으로 다시 불러옵니다\./, mode);
    assert.doesNotMatch(text, /fs01|smart_duty|\\\\|F5|저장할 수 없습니다/, mode);
    if (mode === 'VIEWER') assert.doesNotMatch(text, /연결 정보/);
    else assert.match(text, /\[연결 정보\]/);
    assert.match(a.node('conn-indicator').textContent, /^연결 끊김 · \d\d:\d\d 기준 표시 중$/);
    for (let i = 0; i < 6; i++) { a.advance(10000); await a.fireDue(); }   // 1분 동안 10초마다 확인
    assert.equal(a.json('notifs').length, 0, `${mode}: 끊긴 동안 거짓 알림 없음`);
    assert.equal(a.json('toasts').length, 0, mode);
    a.run(`info={...info,dbOk:true,dataVersion:5,detail:''}`);
    a.advance(10000); await a.fireDue();
    assert.equal(a.run('CONN.shared'), true, mode);
    assert.equal(a.node('conn-alert').innerHTML, '', `${mode}: 배너가 바로 사라진다`);
    assert.equal(a.node('conn-indicator').textContent, '연결됨');
    assert.equal(a.json('toasts').filter(t => /다시 연결되어 최신 당직표를 불러왔습니다/.test(t.m)).length, 1, mode);
    assert.equal(a.json('notifs').filter(n => /다른 PC/.test(n)).length, 0, mode);
    a.advance(30000); await a.fireDue();   // 다음 정상 확인에서도 다시 알리지 않는다
    assert.equal(a.json('toasts').filter(t => /다시 연결/.test(t.m)).length, 1, mode);
  }
});
test('connection: never connected → plain start instructions with the product name, not an exe name', async () => {
  const a = app();
  a.run(`SERVER_CONNECTED=false; dataLoaded=false; fetch=async()=>{throw new TypeError('Failed to fetch');}; applyModeUI=()=>{};`);
  await a.run('fetchSystemInfo()');
  const text = alertText(a);
  assert.match(text, /WLR OnAir\(관리자용 또는 조회용\)를 실행한 뒤 \[다시 연결\]을 누르세요/);
  assert.doesNotMatch(text, /SmartDuty|Viewer|\.exe/);
});

// ── 정적 검사 ──────────────────────────────────────────────────
function jsStrings(src) {
  const out = []; let i = 0, prev = '';
  while (i < src.length) {
    const c = src[i];
    if (c === '/' && src[i + 1] === '/') { const j = src.indexOf('\n', i); i = j < 0 ? src.length : j; continue; }
    if (c === '/' && src[i + 1] === '*') { const j = src.indexOf('*/', i + 2); i = j < 0 ? src.length : j + 2; continue; }
    if (c === '/' && '(,=:[!&|?{};+-*%<>~^'.includes(prev)) { let j = i + 1, cls = false; while (j < src.length && src[j] !== '\n') { if (src[j] === '\\') { j += 2; continue; } if (src[j] === '[') cls = true; else if (src[j] === ']') cls = false; else if (src[j] === '/' && !cls) break; j++; } i = j + 1; prev = 'r'; continue; }
    if (c === "'" || c === '"' || c === '`') {
      let j = i + 1, depth = 0;
      while (j < src.length) { const d = src[j]; if (d === '\\') { j += 2; continue; } if (c === '`' && d === '$' && src[j + 1] === '{') { depth++; j += 2; continue; } if (c === '`' && depth && d === '}') { depth--; j++; continue; } if (d === c && !depth) break; if (c !== '`' && d === '\n') break; j++; }
      out.push(src.slice(i + 1, j)); i = j + 1; prev = 's'; continue;
    }
    if (!/\s/.test(c)) prev = c;
    i++;
  }
  return out;
}
function visibleTexts() {
  const texts = [];
  const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m => m[1]);
  const markup = html.replace(/<script[^>]*>[\s\S]*?<\/script>|<style[^>]*>[\s\S]*?<\/style>|<!--[\s\S]*?-->/g, '');
  for (const m of markup.matchAll(/>([^<>]+)</g)) texts.push({where: 'index.html', text: m[1]});
  for (const m of markup.matchAll(/\b(?:placeholder|title|aria-label)="([^"]*)"/g)) texts.push({where: 'index.html attr', text: m[1]});
  for (const s of scripts) for (const t of jsStrings(s)) texts.push({where: 'index.html script', text: t});
  for (const f of fs.readdirSync(dist).filter(f => f.endsWith('.js'))) for (const t of jsStrings(sidecar(f))) texts.push({where: f, text: t});
  return texts.map(x => ({...x, text: x.text.replace(/\$\{(?:[^{}]|\{[^{}]*\})*\}/g, ' ').replace(/<[^>]*>/g, ' ')})).filter(x => /[가-힣]/.test(x.text));
}
const BANNED = [/\bDB\b/, /\bAPI\b/, /baseVersion/, /\b(?:409|423|428)\b/, /[Tt]oken/, /토큰/, /\bJSON\b/, /동기화/, /스냅샷/, /known_good/, /[Ss]equence/,
  /순서 번호/, /[Cc]hannel/, /\bViewer\b/, /\bVIEWER\b/, /\bADMIN\b/, /관리자 모드/, /조회 모드/, /히스토리/, /\bRule\b/, /룰/, /데이터베이스/, /HTTP/,
  /Failed to fetch/, /휴가\/부재/, /근무 없는 날/, /당직 휴무/, /SmartDuty/, /런처/, /실행 관리자/, /사원 풀/, /bind_host/, /admin_password/, /config_admin\.ini/,
  /\[자가 진단\]/, /\[월 마감 해제\]/, /\[요청 승인 대기\]/, /2026년 \d+\/\d+/, /\} (?:를|을|의|은|는|으로)\b/];
// 'JSON'은 파일 종류를 가리키는 메뉴 이름에서만 허용한다.
const ALLOW = [/백업 파일 (?:불러오기|저장)\(JSON\)/g, /서버에 백업 저장\(JSON\)/g, /smart_duty_backup_날짜\.json/g];
test('static: user-facing strings carry no developer jargon or stale names (allowlist: JSON backup menu names)', () => {
  const problems = [];
  for (const {where, text} of visibleTexts()) {
    let t = text;
    for (const a of ALLOW) t = t.replace(a, ' ');
    for (const b of BANNED) if (b.test(t)) problems.push(`${where}: ${b} :: ${text.trim().slice(0, 120)}`);
  }
  assert.deepEqual(problems, []);
});
test('static: no example placeholders that look like real data', () => {
  const sources = {index: html, setup: fs.readFileSync(path.join(root, 'updater/setup_ui.py'), 'utf8'),
    ...Object.fromEntries(fs.readdirSync(dist).filter(f => f.endsWith('.js')).map(f => [f, sidecar(f)]))};
  const bad = [/192\.168\.\d+\.\d+/, /예\)\s*그날은/, /예: 김민수/, /예\)\s*11\/24/, /\\\\공유PC/, /E:\\\\?WLR_키백업/, /C:\\\\?WLR_Build/, /vX\.Y\.Z/,
    /C:\\\\?WLR_OnAir\\\\?관리자용 또는 조회용/, /D:\\\\+OnAir_Backup/];
  for (const [name, src] of Object.entries(sources)) for (const b of bad) assert.doesNotMatch(src, b, `${name}: ${b}`);
  for (const m of html.matchAll(/placeholder="([^"]*)"/g)) assert.doesNotMatch(m[1], /^예[):]|\d{1,3}\.\d{1,3}\.\d/, m[1]);
  for (const m of sources.setup.matchAll(/'placeholder':\s*r?'([^']*)'/g)) assert.equal(m[1], '', `setup_ui placeholder: ${m[1]}`);
});
test('static: the server-disconnected text names the product, and confirm dialogs in decision flows are not native', () => {
  assert.doesNotMatch(html, /SmartDuty_Admin\.exe|\(또는 Viewer\)/);
  assert.match(html, /WLR OnAir\(관리자용 또는 조회용\)/);
  const requests = sidecar('usability_requests.js');
  assert.doesNotMatch(requests, /[^.\w]confirm\(`|!confirm\('/, 'request flow uses the app dialog');
  const processApproval = html.slice(html.indexOf('async function processApproval'), html.indexOf('async function processApproval') + 3000);
  assert.doesNotMatch(processApproval, /[^.\w]confirm\(/);
  assert.match(processApproval, /askConfirm\(/);
});
