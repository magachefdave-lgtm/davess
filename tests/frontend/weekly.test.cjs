'use strict';
// 이번 주 휴가·부재 안내판과 '이번 주 전체 일정' 창(usability_weekly.js) 회귀.
// index.html 의 실제 스크립트와 보조 스크립트를 Node VM 에서 실행한다.
// 작은 가짜 배치는 한 열에서의 남은 공간만 재고, 실제 열 너비·폰트는 브라우저에서 검증한다.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'frontend/dist/index.html'), 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m => m[1])
  .find(s => s.includes('function renderWeekAbsenceBoard()'));
if (!script) { console.error('main script not found'); process.exit(2); }
const weeklyPath = path.join(root, 'frontend/dist/usability_weekly.js');
const ID = 'oa-weekly-absence';
const ROW_H = 30, TAG_H = 20;

function app({today = '2026-10-14', innerHeight = 1080, members = [], absences = [], listHeight = 400} = {}) {
  const nodes = new Map();
  const docListeners = new Map();
  let created = 0;
  function el(id, tag = 'div') {
    const classes = new Set();
    const e = {id, tagName: tag.toUpperCase(), style: {setProperty() {}}, dataset: {}, attrs: {}, children: [], parent: null, textContent: '',
      clientHeight: 0, isConnected: true, disabled: false,
      classList: {add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c), toggle(c, v) { (v ?? !classes.has(c)) ? classes.add(c) : classes.delete(c); }},
      get className() { return [...classes].join(' '); }, set className(v) { classes.clear(); String(v).split(/\s+/).filter(Boolean).forEach(c => classes.add(c)); },
      setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k] ?? null; },
      hasAttribute(k) { return k in this.attrs; }, removeAttribute(k) { delete this.attrs[k]; },
      appendChild(c) { c.parent = this; this.children.push(c); return c; },
      remove() { if (this.parent) this.parent.children = this.parent.children.filter(x => x !== this); this.parent = null; },
      focus() { ctx.document.activeElement = this; }, matches: sel => sel === '.abs-more' && classes.has('abs-more'),
      contains(n) { for (let x = n; x; x = x.parent) if (x === this) return true; return false; },
      closest() { return null; },
      querySelectorAll(sel) { const c = sel.replace(/^\./, ''); return this.children.filter(x => x.classList.contains(c)); },
      querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
      getBoundingClientRect() {
        if (!this.parent) return {top: 0, bottom: this.clientHeight, left: 0, width: 100, height: this.clientHeight};
        let top = 0;
        for (const s of this.parent.children) { if (s === this) break; if (s.style.display !== 'none') top += s.h; }
        return {top, bottom: top + this.h, left: 0, width: 100, height: this.h};
      }};
    return e;
  }
  // 안내판 목록 칸: innerHTML 로 그린 abs-row 버튼을 실제 자식으로 만든다.
  function listNode(id) {
    const list = el(id);
    list.clientHeight = listHeight;
    let markup = '';
    Object.defineProperty(list, 'innerHTML', {get: () => markup, set(v) {
      markup = v; list.children = [];
      for (const m of v.matchAll(/<button type="button" class="abs-row ([a-z]+)" data-oa-person="([^"]*)" data-date-key="([^"]*)"(?: data-record-count="([^"]*)")?/g)) {
        const row = el('', 'button'); row.className = 'abs-row ' + m[1]; row.dataset.oaPerson = m[2]; row.dataset.dateKey = m[3]; if (m[4]) row.dataset.recordCount = m[4]; row.h = ROW_H;
        list.appendChild(row);
      }
    }});
    return list;
  }
  const modal = el(ID);
  const title = el('', 'div'), body = el('', 'div');
  Object.assign(modal, {querySelector: sel => sel === '.modal-title' ? title : sel === '.oa-body' ? body : null});
  nodes.set(ID, modal);
  nodes.set('week-vacation-container', listNode('week-vacation-container'));
  nodes.set('week-dispatch-container', listNode('week-dispatch-container'));
  const board = el('desk-absences');
  board.clientHeight = listHeight;
  Object.defineProperty(board, 'scrollHeight', {get: () =>
    ['vacation', 'dispatch'].flatMap(kind => nodes.get(`week-${kind}-container`).children)
      .filter(row => row.style.display !== 'none').length * ROW_H});
  nodes.set(board.id, board);
  const getElementById = id => { if (!nodes.has(id)) nodes.set(id, el(id)); return nodes.get(id); };
  const opened = [];
  const ctx = vm.createContext({console, Date, setTimeout, clearTimeout, setInterval, clearInterval, queueMicrotask,
    requestAnimationFrame: () => {}, MutationObserver: class { observe() {} disconnect() {} },
    sessionStorage: {getItem: () => null, setItem() {}, removeItem() {}}, localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
    innerHeight, innerWidth: 1920,
    document: {readyState: 'complete', activeElement: null, getElementById,
      addEventListener(type, fn) { (docListeners.get(type) || docListeners.set(type, []).get(type)).push(fn); },
      createElement: tag => { const e = el('new-' + (++created), tag); e.h = TAG_H; return e; },
      querySelector: () => null, querySelectorAll: () => [], head: el('head'), body: el('body')}});
  ctx.window = ctx; ctx.addEventListener = () => {};
  vm.runInContext(script, ctx, {filename: 'index.html'});
  const run = code => vm.runInContext(code, ctx);
  run(`todayStr=${JSON.stringify(today)};memberList=${JSON.stringify(members.map(name => ({name, gender: 'F'})))};
    absences=${JSON.stringify(absences)};syncDeskWidths=()=>{};fitTodayVacations=()=>{};`);
  ctx.OnAirUI = {escape: s => run('esc')(s), dateLabel: k => k,
    open(id, t, h, options) { opened.push({id, title: t, html: h, options}); modal.classList.add('active'); title.textContent = t; body.innerHTML = h; },
    close(id) { if (id === ID) modal.classList.remove('active'); }};
  vm.runInContext(fs.readFileSync(weeklyPath, 'utf8'), ctx, {filename: weeklyPath});
  const click = (action) => {
    const button = {dataset: {weeklyAction: action}};
    for (const fn of docListeners.get('click') || []) fn({target: {closest: sel => sel.includes('[data-weekly-action]') ? button : null}});
  };
  const pageKeys = () => [...body.innerHTML.matchAll(/data-weekly-name="([^"]*)" data-weekly-date="([^"]*)"/g)].map(m => `${m[1]}|${m[2]}`);
  return {run, ctx, opened, modal, body, click, pageKeys, list: kind => nodes.get(`week-${kind}-container`),
    weekly: ctx.OnAirWeekly};
}
const rec = (name, type, start, end = start, id) => ({id, name, type, start, end});
const plain = x => JSON.parse(JSON.stringify(x));

// [위험] 이번 주는 일~토다(달력의 한 줄과 같다). 월~일로 바꾸면 안내판과 달력이 서로 다른 주를 가리킨다.
test('week range is Sunday..Saturday of the week containing today', () => {
  for (const [today, start, end] of [['2026-10-14', '2026-10-11', '2026-10-17'], ['2026-10-11', '2026-10-11', '2026-10-17'],
    ['2026-10-17', '2026-10-11', '2026-10-17'], ['2026-10-18', '2026-10-18', '2026-10-24'], ['2026-12-30', '2026-12-27', '2027-01-02']]) {
    const a = app({today});
    const keys = plain(a.run(`weekKeysOf(${JSON.stringify(today)})`));
    assert.equal(keys.length, 7);
    assert.equal(keys[0], start, today); assert.equal(keys[6], end, today);
    assert.equal(a.run(`dowOf(${JSON.stringify(start)})`), 0); assert.equal(a.run(`dowOf(${JSON.stringify(end)})`), 6);
    a.weekly.open('vacation');
    assert.match(a.opened.at(-1).html, new RegExp(`오늘 기준 ${start}\\(일\\) ~ ${end}\\(토\\)`));
  }
});

// [위험] 주 경계에 걸친 기록(일요일에 끝나는 휴가, 토요일에 시작하는 파견)이 빠지면 그 주 부재자가 안 보인다.
test('records overlapping the week edges are included; outside and unregistered are not', () => {
  const members = ['가', '나', '다', '라', '마', '바', '사'];
  const absences = [rec('가', '연차', '2026-10-05', '2026-10-11'),    // ends Sunday
    rec('나', '교육', '2026-10-17', '2026-10-20'),                    // starts Saturday
    rec('다', '파견', '2026-09-01', '2026-12-31'),                    // spans the week
    rec('라', '연차', '2026-10-01', '2026-10-10'),                    // ends Saturday before
    rec('마', '출장', '2026-10-18', '2026-10-19'),                    // starts Sunday after
    rec('바', '외근', '2026-10-14'),                                  // inside
    rec('퇴사자', '연차', '2026-10-14'),                               // not in member list
    rec('사', '연수', '2026-10-11', '2026-10-11')];                   // Sunday only, long-type name but 1 day
  const a = app({members, absences});
  const names = kind => plain(a.weekly.rows(kind)).map(r => r.name);
  // v3.18.0 장기 여부는 기간(14일 이상)으로 정한다 — 하루짜리 '연수'는 단기 부재(주황)로 왼쪽 칸에 온다.
  assert.deepEqual(names('vacation'), ['가', '사', '바', '나'], 'vacation types first, then short absences, by start');
  assert.deepEqual(names('dispatch'), ['다']);
  a.run('renderWeekAbsenceBoard()');
  assert.deepEqual(a.list('vacation').children.map(r => r.dataset.oaPerson), ['가', '사', '바', '나'], 'board and dialog agree');
  assert.deepEqual(a.list('dispatch').children.map(r => r.dataset.oaPerson), ['다']);
  assert.equal(a.run("document.getElementById('week-vacation-count').textContent"), '4명');
  assert.equal(a.run("document.getElementById('week-vacation-period').textContent"), '10.11 ~ 10.17');
  assert.equal(a.run("document.getElementById('week-dispatch-count').textContent"), '1명');
});

// [위험] v3.18.0 4일짜리 '출장'이 이름만으로 장기 칸에 가고 달력에서 사라졌다. 기간 14일이 경계다.
test('long/short split is by duration: 4-day 출장 short, 13 days short, 14 days long, 파견 3 days short', () => {
  const members = ['가', '나', '다', '라', '마'];
  const absences = [rec('가', '출장', '2026-10-14', '2026-10-17'),     // 4 days
    rec('나', '출장', '2026-10-01', '2026-10-13'),                      // 13 days, ends inside the week
    rec('다', '출장', '2026-10-01', '2026-10-14'),                      // 14 days
    rec('라', '파견', '2026-10-12', '2026-10-14'),                      // inherently long type, 3 days
    rec('마', '연차', '2026-10-05', '2026-10-18')];                     // 14-day vacation → long
  const a = app({members, absences});
  const names = kind => plain(a.weekly.rows(kind)).map(r => r.name);
  assert.deepEqual(names('vacation'), ['나', '라', '가']);
  assert.deepEqual(names('dispatch'), ['다', '마']);
  a.run('renderWeekAbsenceBoard()');
  assert.deepEqual(a.list('vacation').children.map(r => r.className), ['abs-row short', 'abs-row short', 'abs-row short']);
  assert.deepEqual(a.list('dispatch').children.map(r => r.className), ['abs-row dis', 'abs-row dis']);
  a.weekly.open('dispatch');
  assert.match(a.opened.at(-1).html, /ow-record ow-dispatch/);
  assert.doesNotMatch(a.opened.at(-1).html, /ow-record ow-(vacation|short)/);
  a.weekly.open('vacation');
  assert.equal((a.opened.at(-1).html.match(/ow-record ow-short/g) || []).length, 3);
});

// [위험] 쪽 넘김이 기록을 건너뛰거나 두 번 보이면 '이번 주 전체 일정'이 전체가 아니다.
function manyRecords(n) {
  const members = [], absences = [];
  for (let i = 0; i < n; i++) {
    const name = `사원${String(i).padStart(2, '0')}`; members.push(name);
    absences.push(rec(name, i % 3 ? '연차' : '교육', `2026-10-${String(11 + (i % 7)).padStart(2, '0')}`));
  }
  absences.push(rec('사원00', '교육', '2026-10-11'));   // exact duplicate of a record (no id) — both must be listed
  return {members, absences};
}
for (const [innerHeight, size] of [[1080, 5], [900, 5], [768, 3], [500, 2]]) {
  test(`paging at innerHeight ${innerHeight}: page size ${size}, every record exactly once`, () => {
    const data = manyRecords(12);
    const a = app({...data, innerHeight});
    a.weekly.open('vacation');
    const total = a.weekly.rows('vacation').length;
    assert.equal(total, 13);
    assert.equal(a.weekly.state().pageSize, size);
    const pages = Math.ceil(total / size);
    const seen = [], allKeys = [];
    for (let p = 0; p < pages; p++) {
      assert.match(a.body.innerHTML, new RegExp(`${p + 1} / ${pages} 페이지`));
      const keys = a.pageKeys();
      assert.equal(keys.length, p < pages - 1 ? size : total - size * (pages - 1));
      seen.push(...keys);
      allKeys.push(...[...a.body.innerHTML.matchAll(/data-weekly-key="([^"]*)"/g)].map(m => m[1]));
      assert.equal(/data-weekly-action="previous" disabled/.test(a.body.innerHTML), p === 0);
      assert.equal(/data-weekly-action="next" disabled/.test(a.body.innerHTML), p === pages - 1);
      a.click('next');
    }
    assert.equal(a.weekly.state().page, pages - 1, 'next on the last page stays');
    const expected = plain(a.weekly.rows('vacation')).map(r => `${r.name}|${r.start}`);
    assert.deepEqual(seen, expected);
    assert.equal(seen.filter(k => k === '사원00|2026-10-11').length, 2, 'duplicate records are both listed');
    // focus restoration after a data refresh finds a record by its key, so keys must be unique across pages
    assert.equal(new Set(allKeys).size, total);
    a.click('previous');
    assert.equal(a.weekly.state().page, pages - 2);
  });
}

// 좁은 목록은 항목부터 조이고 별도 더보기 버튼을 만들지 않는다. 전체 목록은 제목에서 연다.
test('weekly vacation overflow keeps records and full-list title without a bottom More button', () => {
  const a = app({...manyRecords(12), listHeight: 130});
  a.run('renderWeekAbsenceBoard()');
  const list = a.list('vacation'), rows = list.children.filter(c => c.classList.contains('abs-row'));
  assert.equal(list.querySelector('.abs-more'), null);
  assert.equal(rows.length, 13);
  assert.ok(rows.some(r => r.style.display !== 'none'));
  assert.ok(rows.some(r => r.style.display === 'none'));
  assert.equal(a.run("document.getElementById('week-vacation-hidden').hidden"), false);
  a.weekly.open('vacation');
  assert.equal(a.opened.at(-1).title, '이번 주 휴가·부재');
  assert.match(a.opened.at(-1).html, /일정 <strong>13건<\/strong>/);
});
test('everything fits: no "더" button and nothing hidden', () => {
  const a = app({...manyRecords(3), listHeight: 400});
  a.run('renderWeekAbsenceBoard()');
  const list = a.list('vacation');
  assert.equal(list.querySelector('.abs-more'), null);
  assert.ok(list.children.every(r => r.style.display !== 'none'));
});
test('a very short vacation column keeps the title path without a More button', () => {
  const a = app({...manyRecords(4), listHeight: 41});
  a.run('renderWeekAbsenceBoard()');
  const list = a.list('vacation');
  assert.equal(list.querySelector('.abs-more'), null);
  a.weekly.open('vacation');
  assert.match(a.opened.at(-1).html, /일정 <strong>5건<\/strong>/);
});

// [위험] 보조 스크립트는 본 스크립트의 함수를 감싸서 바꾼다. 순서가 바뀌면 감싸기가 엉뚱한 함수에 걸린다.
test('index.html loads usability and management sidecars after the main script, in order, same-origin', () => {
  const tags = [...html.matchAll(/<script\b[^>]*\bsrc="([^"]+)"[^>]*><\/script>/g)].map(m => m[1]);
  assert.deepEqual(tags, ['/design_picker.js', '/usability_common.js', '/holiday_supplement.js', '/usability_personal.js', '/usability_weekly.js',
    '/request_access.js', '/usability_requests.js', '/usability_notifications.js', '/usability_admin.js', '/assignment_explanations.js',
    '/holiday_management.js', '/absence_management.js', '/holiday_preview.js', '/absence_request_calendar.js', '/update_status.js', '/master_session.js', '/dinner_polls.js', '/dinner_admin.js']);
  const mainEnd = html.indexOf(script) + script.length;
  assert.ok(html.indexOf('<script src="/usability_common.js">') > mainEnd, 'sidecars come after the main script');
  for (const src of tags) assert.ok(fs.existsSync(path.join(root, 'frontend/dist', src)), src);
  assert.doesNotMatch(html, /<(?:script|link)\b[^>]*(?:src|href)="(?:https?:)?\/\//i, 'no external resources');
});


// Separate approved requests stay intact; only the weekly board coalesces dates.
function vacationBoard(records, today = '2026-11-05', listHeight = 400) {
  const a = app({today, members: [...new Set(records.map(r => r.name))], absences: records, listHeight});
  a.run('renderWeekAbsenceBoard()');
  return a;
}
function labels(a) {
  return [...a.list('vacation').innerHTML.matchAll(/aria-label="([^"]*)"/g)].map(m => m[1]);
}
test('same-person adjacent annual leave is one card, with weekdays and original requests retained', () => {
  const records = [rec('김민수', '연차', '2026-11-06'), rec('김민수', '연차', '2026-11-05')];
  const a = vacationBoard(records);
  assert.equal(a.list('vacation').children.length, 1);
  assert.equal(a.list('vacation').children[0].dataset.dateKey, '2026-11-05');
  assert.equal(a.list('vacation').children[0].dataset.recordCount, '2');
  assert.match(labels(a)[0], /11\/5\(목\) ~ 11\/6\(금\)/);
  assert.deepEqual(plain(a.run('absences')), plain(records));
  assert.equal(a.weekly.rows('vacation').length, 2);
  a.weekly.open('vacation');
  assert.match(a.body.innerHTML, /일정 <strong>2건<\/strong>/);
  assert.equal(a.run("document.getElementById('week-vacation-count').textContent"), '1명');
});
test('separated leave days share a card but never become one continuous range', () => {
  const a = vacationBoard([rec('가', '연차', '2026-11-02'), rec('가', '연차', '2026-11-05')]);
  assert.equal(a.list('vacation').children.length, 1);
  assert.match(labels(a)[0], /11\/2\(월\), 11\/5\(목\)/);
  assert.doesNotMatch(labels(a)[0], /~/);
});
test('overlaps and duplicate requests display the date union without modifying the records', () => {
  const records = [rec('가', '연차', '2026-11-04', '2026-11-06'), rec('가', '연차', '2026-11-02', '2026-11-04'), rec('가', '연차', '2026-11-02', '2026-11-04')];
  const a = vacationBoard(records);
  assert.equal(a.list('vacation').children.length, 1);
  assert.match(labels(a)[0], /11\/2\(월\) ~ 11\/6\(금\)/);
  assert.equal(a.list('vacation').children[0].dataset.recordCount, '3');
  assert.deepEqual(plain(a.run('absences')), plain(records));
  assert.equal(a.weekly.rows('vacation').length, 3);
});
test('different people/types and separate education or long absence remain separate', () => {
  const a = vacationBoard([rec('가', '연차', '2026-11-02'), rec('가', '연차', '2026-11-03'), rec('나', '연차', '2026-11-03'), rec('가', '오전반차', '2026-11-04'), rec('가', '교육', '2026-11-05'), rec('가', '교육', '2026-11-06'), rec('가', '파견', '2026-10-01', '2026-11-15'), rec('가', '파견', '2026-11-01', '2026-12-31')]);
  assert.equal(a.list('vacation').children.length, 5);
  assert.equal(a.list('dispatch').children.length, 2);
  assert.equal(a.weekly.rows('vacation').length, 6);
});
test('week filtering happens before grouping; overlapping original ranges are not clipped', () => {
  const a = vacationBoard([rec('가', '연차', '2026-10-31'), rec('가', '연차', '2026-11-01'), rec('가', '연차', '2026-11-06', '2026-11-09'), rec('가', '연차', '2026-11-10')]);
  assert.equal(a.list('vacation').children.length, 1);
  assert.match(labels(a)[0], /11\/1\(일\), 11\/6\(금\) ~ 11\/9\(월\)/);
  assert.equal(a.list('vacation').children[0].dataset.recordCount, '2');
});
for (const [today, first, second, expected] of [
  ['2026-12-30', '2026-12-31', '2027-01-01', '2026/12/31(목) ~ 2027/1/1(금)'],
  ['2028-02-29', '2028-02-29', '2028-03-01', '2/29(화) ~ 3/1(수)']
]) test(`calendar adjacency and year labels: ${first} / ${second}`, () => {
  const a = vacationBoard([rec('가', '연차', second), rec('가', '연차', first)], today);
  assert.equal(a.list('vacation').children.length, 1);
  assert.ok(labels(a)[0].includes(expected), labels(a)[0]);
});
test('overflow title counts underlying requests, not the number of grouped cards', () => {
  const a = vacationBoard([rec('가', '연차', '2026-11-02'), rec('가', '연차', '2026-11-03'), rec('나', '연차', '2026-11-04')], '2026-11-05', 41);
  assert.equal(a.list('vacation').children.length, 2);
  assert.match(a.run("document.getElementById('week-vacation-title').title"), /전체 일정 3건 \/ 현재 2건 표시/);
  assert.equal(a.list('vacation').querySelector('.abs-more'), null);
});
