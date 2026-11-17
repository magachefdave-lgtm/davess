'use strict';
// [위험] 장기 파견·휴직은 몇 달씩 이어져 달력 모든 칸에 같은 이름이 반복됐다.
//   v3.15.7 에서 달력 칸에서 뺐다. 이 테스트가 지키는 것은 두 가지다.
//   1) 장기 부재 이름이 달력 칸에 다시 나타나지 않는다
//   2) 휴가·단기 부재는 그대로 보인다 (표시를 통째로 지운 것이 아니다)
//   그리고 표시를 뺀 것이 배정에 영향을 주지 않아야 한다 — isAbsent 는 그대로다.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const html = fs.readFileSync(path.resolve(__dirname, '../../frontend/dist/index.html'), 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(m => m[1]).find(s => s.includes('function renderCalendar()'));
if (!script) { console.error('renderCalendar 를 찾지 못했습니다 — 함수 이름이 바뀌었는지 확인하세요.'); process.exit(2); }

function app(absences) {
  const nodes = new Map();
  const element = id => {
    if (!nodes.has(id)) nodes.set(id, {
      innerHTML: '', textContent: '', value: '', rows: [], dataset: {},
      style: {setProperty: () => {}}, classList: {toggle: () => {}, add: () => {}, remove: () => {}, contains: () => false},
      querySelectorAll: () => [], appendChild: () => {}, clientWidth: 900, clientHeight: 600
    });
    return nodes.get(id);
  };
  const storage = {getItem: () => null, setItem: () => {}, removeItem: () => {}};
  const context = vm.createContext({
    Date, console, JSON, Math, Set, Map, sessionStorage: storage, localStorage: storage,
    document: {addEventListener: () => {}, getElementById: element, querySelector: () => null, querySelectorAll: () => []},
    window: {addEventListener: () => {}},
    setTimeout, clearTimeout, setInterval, clearInterval, requestAnimationFrame: () => {}
  });
  vm.runInContext(script, context);
  const run = s => vm.runInContext(s, context);
  run(`
    curYear=2026; curMonth=11; todayStr='2026-11-10'; SYSTEM_MODE='ADMIN';
    memberList=[{id:1,name:'김파견',gender:'M'},{id:2,name:'이휴가',gender:'F'},{id:3,name:'박부재',gender:'F'}];
    absences=${JSON.stringify(absences)};
    scheduleMap={}; noDutyMap={}; commentMap={}; customHolidays={};
    renderTodayCard=()=>{}; renderMyNameUI=()=>{}; renderMemberDutyButtons=()=>{};
    updateMemberCalendarHighlights=()=>{}; executeMemberSearch=()=>{};
    balanceCalendarWeekRows=()=>{}; renderWeekAbsenceBoard=()=>{}; renderWeekMemoBoard=()=>{};
    renderCalendar();
  `);
  return {run, cells: element('calendar-tbody').innerHTML, tbody: () => element('calendar-tbody').innerHTML};
}

const span = (name, type, start, end) => ({name, type, start, end});

test('long dispatch absence no longer appears in any calendar cell', () => {
  const {cells} = app([span('김파견', '파견', '2026-10-01', '2026-12-31')]);
  assert.ok(cells.includes('2026-11-10'), '달력이 실제로 그려졌는지 먼저 확인');
  assert.ok(!cells.includes('김파견'), '장기 파견자 이름이 달력 칸에 남아 있습니다');
  assert.ok(!cells.includes('v-mini-chip dispatch'), '장기 부재 칩이 달력에 남아 있습니다');
});

test('vacation and short absence still show in the calendar cell', () => {
  const {cells} = app([
    span('이휴가', '연차', '2026-11-10', '2026-11-10'),
    span('박부재', '교육', '2026-11-10', '2026-11-10'),
    span('김파견', '휴직', '2026-10-01', '2026-12-31')
  ]);
  assert.ok(cells.includes('이휴가'), '휴가자는 달력에 보여야 합니다');
  assert.ok(cells.includes('박부재'), '단기 부재자는 달력에 보여야 합니다');
  assert.ok(/class="[^"]*\bv-mini-chip\b[^"]*\bshort-absence\b[^"]*"/.test(cells), '단기 부재 색 구분이 사라졌습니다');
  assert.ok(!cells.includes('김파견'), '장기 부재자는 달력에 나오지 않아야 합니다');
});

test('long absence is still excluded from duty by isAbsent', () => {
  const {run} = app([span('김파견', '파견', '2026-10-01', '2026-12-31')]);
  // 표시만 뺀 것이고 배정 제외는 그대로여야 한다.
  assert.equal(run(`isAbsent('김파견','2026-11-10')`), true);
  assert.equal(run(`isLongAbsenceType('파견')`), true);
  assert.equal(run(`isAbsent('이휴가','2026-11-10')`), false);
});

test('cell with only long absences renders no absence row at all', () => {
  const {cells} = app([span('김파견', '주재', '2026-11-01', '2026-11-30')]);
  assert.ok(!cells.includes('cell-absence-list'), '빈 부재 줄이 그려지면 칸 높이만 잡아먹습니다');
});

// ── v3.18.0 달력 칸 표시 ────────────────────────────────────────────
// 날짜 칸 하나의 HTML 만 잘라 본다.
function cellOf(html, key) {
  const start = html.indexOf(`data-date-key="${key}"`);
  assert.ok(start >= 0, `${key} 칸이 없습니다`);
  const end = html.indexOf('</td>', start);
  return html.slice(start, end);
}

// [위험] 장기 여부를 구분 이름으로 정하면 4일짜리 '출장'이 달력에서 사라진다. 기간 14일이 경계다.
test('short trips show on the calendar; >=14-day records of any type do not (13/14 boundary)', () => {
  const {cells} = app([
    span('김파견', '출장', '2026-11-09', '2026-11-12'),     // 4 days → shown
    span('이휴가', '파견', '2026-11-01', '2026-11-13'),     // 13 days → shown (short)
    span('박부재', '출장', '2026-11-01', '2026-11-14')      // 14 days → hidden (long)
  ]);
  const c = cellOf(cells, '2026-11-10');
  assert.match(c, /<span class="chip-tag" aria-hidden="true">출<\/span>김파견/);
  assert.match(c, /<span class="chip-tag" aria-hidden="true">부<\/span>이휴가/);
  assert.ok(!c.includes('박부재'), '14일 이상은 장기라 달력 칸에 나오지 않아야 합니다');
});

test('chips carry a one-letter tag: 휴 vacation, 교 education, 출 trip, 부 other; vacation keeps the purple chip', () => {
  const {cells} = app([
    span('김파견', '교육', '2026-11-10', '2026-11-10'),
    span('이휴가', '연차/휴가', '2026-11-10', '2026-11-10'),
    span('박부재', '예비군', '2026-11-10', '2026-11-10')
  ]);
  const c = cellOf(cells, '2026-11-10');
  assert.match(c, /class="v-mini-chip absence-detail-trigger "[^>]*>.{0,60}휴<\/span>이휴가/);
  assert.match(c, /short-absence[^>]*>.{0,60}교<\/span>김파견/);
  assert.match(c, /short-absence[^>]*>.{0,60}부<\/span>박부재/);
});

test('a person on long dispatch with a one-day course that day shows the course chip', () => {
  const {cells} = app([
    span('김파견', '파견', '2026-10-01', '2026-12-31'),
    span('김파견', '교육', '2026-11-10', '2026-11-10')
  ]);
  assert.match(cellOf(cells, '2026-11-10'), /교<\/span>김파견/);
  assert.ok(!cellOf(cells, '2026-11-11').includes('김파견'));
});

// [위험] v3.18.1 사용자 결정 — 칸의 인원 표시(1/1, 1/2, 옛 1인/2인)는 필요 없다. 다시 생기면 안 된다.
test('calendar cells show no duty count pill (1/1, 1/2) on any day', () => {
  const a = app([]);
  a.run(`scheduleMap={'2026-11-10':[{name:'이휴가'}],'2026-11-14':[{name:'김파견'}]};
    noDutyMap={'2026-11-12':true}; renderCalendar();`);
  const html = a.tbody();
  for (const k of ['2026-11-10', '2026-11-11', '2026-11-12', '2026-11-14']) {
    const cell = cellOf(html, k);
    assert.ok(!cell.includes('duty-fill'), k + ' 에 인원 표시가 남아 있습니다');
    assert.ok(!/>\d\/\d</.test(cell), k + ' 에 1/2 같은 표기가 남아 있습니다');
  }
  assert.ok(!/[12]인</.test(html), "옛 '1인/2인' 표기가 남아 있습니다");
});

// [위험] 자동 배정이 붙인 '패밀리데이' 줄이 기존 메모 뒤에 이어 붙어 한 덩어리 메모로 보였다.
test('family day label replaces 당직 없음 and the memo stays separate; source memo is not modified', () => {
  const a = app([]);
  a.run(`noDutyMap={'2026-11-20':'RULE21','2026-11-13':true,'2026-11-06':true};
    commentMap={'2026-11-20':'분기 감사 대비 자료 제출\\n패밀리데이','2026-11-13':'패밀리데이','2026-11-06':'정전'};
    renderCalendar();`);
  const html = a.tbody();
  const fri = cellOf(html, '2026-11-20');
  assert.match(fri, /<div class="noduty-banner noduty-family">패밀리데이<\/div>/);
  assert.match(fri, /<span>분기 감사 대비 자료 제출<\/span>/);
  assert.ok(!/<span>[^<]*패밀리데이[^<]*<\/span>/.test(fri), '메모 줄에 패밀리데이가 남아 있습니다');
  const manual = cellOf(html, '2026-11-13');
  assert.match(manual, /noduty-family">패밀리데이</);
  assert.ok(!manual.includes('cell-memo'), '패밀리데이 한 줄뿐인 메모는 메모 칸을 만들지 않습니다');
  assert.match(cellOf(html, '2026-11-06'), /<div class="noduty-banner">당직 없음<\/div>/);
  assert.equal(a.run(`commentMap['2026-11-20']`), '분기 감사 대비 자료 제출\n패밀리데이', '원본 메모는 그대로여야 합니다');
});

test('major holiday no-duty day is labelled 명절', () => {
  const a = app([]);
  a.run(`customHolidays={'2026-11-17':{name:'테스트명절',isMajor:true}}; noDutyMap={'2026-11-17':'RULE_MAJOR_DAY','2026-11-18':'RULE_MAJOR_DAY'}; renderCalendar();`);
  const html = a.tbody();
  assert.match(cellOf(html, '2026-11-17'), /noduty-major">명절</);
  assert.match(cellOf(html, '2026-11-18'), /noduty-major">명절</);
});

test('viewer mode renders the same tags, counts and labels', () => {
  const out = [];
  for (const mode of ['ADMIN', 'VIEWER']) {
    const a = app([span('김파견', '출장', '2026-11-09', '2026-11-12')]);
    a.run(`SYSTEM_MODE='${mode}'; scheduleMap={'2026-11-10':[{name:'이휴가'}]}; noDutyMap={'2026-11-20':'RULE21'};
      commentMap={'2026-11-20':'메모\\n패밀리데이'}; renderCalendar();`);
    out.push(a.tbody().replace(/draggable="(true|false)"/g, ''));
  }
  assert.equal(out[0], out[1]);
  assert.match(out[1], /출<\/span>김파견/);
  assert.ok(!out[1].includes('duty-fill'), '조회용에도 인원 표시가 없어야 합니다');
  assert.match(out[1], /noduty-family">패밀리데이</);
});
