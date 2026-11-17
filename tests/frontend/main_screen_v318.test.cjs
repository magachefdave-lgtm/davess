'use strict';
// v3.18.0 메인 화면 — 이번 주 메모 보드의 공지(globalMemo)·패밀리데이 분리, 분류 경계.
// index.html 의 실제 스크립트를 Node VM 에서 실행한다. DOM 은 innerHTML 만 보는 대역이다.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const html = fs.readFileSync(path.resolve(__dirname, '../../frontend/dist/index.html'), 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(m => m[1]).find(s => s.includes('function renderWeekMemoBoard()'));

function app() {
  const nodes = new Map();
  const element = id => {
    if (!nodes.has(id)) nodes.set(id, {innerHTML: '', textContent: '', style: {setProperty: () => {}}, dataset: {},
      attrs: {}, removeAttribute(k) { delete this.attrs[k]; }, setAttribute(k, v) { this.attrs[k] = v; },
      querySelectorAll: () => [], children: [], clientWidth: 220, clientHeight: 0});
    return nodes.get(id);
  };
  const storage = {getItem: () => null, setItem: () => {}, removeItem: () => {}};
  const context = vm.createContext({Date, console, sessionStorage: storage, localStorage: storage,
    document: {addEventListener: () => {}, getElementById: element, querySelector: () => null, querySelectorAll: () => []},
    window: {addEventListener: () => {}}, setTimeout, clearTimeout, setInterval, clearInterval, requestAnimationFrame: () => {}});
  vm.runInContext(script, context);
  const run = s => vm.runInContext(s, context);
  run(`todayStr='2026-10-14'; memberList=[]; absences=[]; scheduleMap={}; customHolidays={};`);
  return {run, element};
}

test('saved globalMemo (공지) is shown as a pinned first line of the weekly memo board, escaped', () => {
  const a = app();
  a.run(`globalMemo='출입카드 <b>수령</b> / 내선 1234'; commentMap={'2026-10-15':'정전 작업'}; noDutyMap={}; renderWeekMemoBoard();`);
  const body = a.element('week-memo-board').innerHTML;
  assert.ok(body.startsWith('<div class="week-notice" role="note"'), body.slice(0, 80));
  assert.match(body, /<b class="week-notice-tag">공지<\/b><span class="week-notice-text">출입카드 &lt;b&gt;수령&lt;\/b&gt; \/ 내선 1234<\/span>/);
  assert.match(body, /정전 작업/);
  assert.equal((body.match(/class="week-memo-item/g) || []).length, 1, '공지는 메모 건수에 들어가지 않습니다');
  assert.match(a.element('week-memo-range').textContent, /· 1건$/);
});

test('notice also shows when the week has no memo; empty notice renders nothing', () => {
  const a = app();
  a.run(`globalMemo='비상연락 내선 1234'; commentMap={}; noDutyMap={}; renderWeekMemoBoard();`);
  assert.match(a.element('week-memo-board').innerHTML, /week-notice[\s\S]*week-memo-empty">없음/);
  a.run(`globalMemo='   '; renderWeekMemoBoard();`);
  assert.doesNotMatch(a.element('week-memo-board').innerHTML, /week-notice/);
});

test('family-day line is shown as a tag, separate from the memo text, in the weekly memo board', () => {
  const a = app();
  a.run(`globalMemo=''; noDutyMap={'2026-10-16':'RULE21','2026-10-15':true};
    commentMap={'2026-10-16':'분기 감사 대비 자료 제출\\n패밀리데이','2026-10-15':'패밀리데이'}; renderWeekMemoBoard();`);
  const body = a.element('week-memo-board').innerHTML;
  assert.match(body, /<span class="week-memo-text"><b class="week-memo-tag">패밀리데이<\/b>분기 감사 대비 자료 제출<\/span>/);
  assert.match(body, /<span class="week-memo-text"><b class="week-memo-tag">패밀리데이<\/b><\/span>/);
  assert.doesNotMatch(body, /제출 패밀리데이|제출\n패밀리데이/);
});

// [위험] 예전 b 는 1줄, c 는 날짜만이라 1366 에서 메모 본문이 '14시 설…' 이거나 아예 사라졌다.
test('memo density levels: a/b two lines, c one line; no date-only level', () => {
  assert.match(script, /const LEVELS = \['a', 'b', 'c'\];\s*let lv = 0;\s*board\.dataset\.density/);
  const css = fs.readFileSync(path.resolve(__dirname, '../../frontend/dist/usability_common.css'), 'utf8');
  assert.match(css, /data-density="b"\] \.week-memo-text \{ -webkit-line-clamp: 2; \}/);
  assert.match(css, /data-density="c"\] \.week-memo-text \{ display: -webkit-box; -webkit-line-clamp: 1; \}/);
});

test('classification helpers: span, long boundary, kind and tag (any timezone)', () => {
  const a = app();
  const J = s => JSON.parse(a.run(`JSON.stringify(${s})`));
  assert.equal(a.run('LONG_ABSENCE_MIN_DAYS'), 14);
  assert.deepEqual(J(`[absenceSpanDays({start:'2026-10-14',end:'2026-10-14'}), absenceSpanDays({start:'2026-10-14'}),
    absenceSpanDays({start:'2026-02-20',end:'2026-03-05'}), absenceSpanDays({start:'2028-02-20',end:'2028-03-04'}),
    absenceSpanDays({start:'2026/3/1',end:'2026.3.14'}), absenceSpanDays({start:'',end:''})]`), [1, 1, 14, 14, 14, 0]);
  assert.deepEqual(J(`[isLongAbsence({type:'출장',start:'2026-10-14',end:'2026-10-17'}),
    isLongAbsence({type:'파견',start:'2026-10-01',end:'2026-10-13'}), isLongAbsence({type:'파견',start:'2026-10-01',end:'2026-10-14'}),
    isLongAbsence({type:'교육',start:'2026-10-01',end:'2026-10-14'}), isLongAbsence({type:'연차',start:'2026-10-01',end:'2026-10-14'})]`),
    [false, false, true, true, true]);
  assert.deepEqual(J(`['연차','반차','공가','교육','출장','외근','육아휴가','예비군',''].map(t=>absenceKind({type:t,start:'2026-10-14',end:'2026-10-14'}))`),
    ['vacation', 'vacation', 'vacation', 'short', 'short', 'short', 'short', 'short', 'short']);
  assert.deepEqual(J(`['연차/휴가','교육','연수','출장','외근','예비군','파견'].map(t=>absenceTagOf({type:t,start:'2026-10-14',end:'2026-10-14'}))`),
    ['휴', '교', '교', '출', '출', '부', '부']);
  // DST 가 있는 시간대에서도 일수가 하루 어긋나지 않아야 한다 (America/Los_Angeles 3월·11월)
  assert.deepEqual(J(`[absenceSpanDays({start:'2026-03-01',end:'2026-03-14'}), absenceSpanDays({start:'2026-10-25',end:'2026-11-07'})]`), [14, 14]);
});

test('calendar legend explains the chip letters (no count format since 3.18.1); it sits in the calendar header bar', () => {
  const bar = html.slice(html.indexOf('<div class="cal-header-bar">'), html.indexOf('<div class="month-tabs"'));
  for (const s of ['<i class="lg-chip">휴</i>휴가', '>교</i>교육', '>출</i>출장·외근', '>부</i>기타 부재'])
    assert.ok(bar.includes(s), s);
  assert.ok(!bar.includes('lg-fill'), '범례에 배정/필요 설명이 남아 있습니다');
});

test('pending approval label is one text run so the count reads 결재 대기 2건', () => {
  assert.match(html, /<span class="approval-label">결재 대기 <span id="pending-req-count">0<\/span>건<\/span>/);
});
