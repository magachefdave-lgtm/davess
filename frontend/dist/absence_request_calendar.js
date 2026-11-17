/* Date selection changes the request draft only; the existing request flow owns persistence. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const grid = $('arc-calendar-grid');
  if (!grid) return;
  const weekdays = ['일', '월', '화', '수', '목', '금', '토'];
  const state = {year: 0, month: 1, start: '', end: '', anchor: '', drag: null};
  let committing = false;
  const keyOf = date => `${String(date.getUTCFullYear()).padStart(4, '0')}-${String(date.getUTCMonth() + 1).padStart(2, '0')}-${String(date.getUTCDate()).padStart(2, '0')}`;
  function dateOf(key) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(key))) return null;
    const [year, month, day] = key.split('-').map(Number);
    const date = new Date(Date.UTC(year, month - 1, day));
    return year >= 1000 && year <= 9999 && keyOf(date) === key ? date : null;
  }
  const label = key => {
    const date = dateOf(key);
    return date ? `${key.replaceAll('-', '.')}(${weekdays[date.getUTCDay()]})` : '';
  };
  const canEdit = () => SYSTEM_MODE === 'VIEWER' && $('absence-request-modal')?.classList.contains('active') &&
    !$('absence-req-start').disabled && !$('absence-req-end').disabled && !$('btn-submit-absence-request').disabled;
  const range = (first, last) => first <= last ? [first, last] : [last, first];
  function calendarCells(year, month) {
    const first = new Date(Date.UTC(year, month - 1, 1));
    first.setUTCDate(1 - first.getUTCDay());
    return Array.from({length: 42}, (_, offset) => {
      const date = new Date(first); date.setUTCDate(first.getUTCDate() + offset);
      return {key: keyOf(date), day: date.getUTCDate(), weekday: date.getUTCDay(), outside: date.getUTCMonth() + 1 !== month,
        valid: date.getUTCFullYear() >= 1000 && date.getUTCFullYear() <= 9999};
    });
  }
  function viewDate(key) {
    const date = dateOf(key); if (!date) return false;
    state.year = date.getUTCFullYear(); state.month = date.getUTCMonth() + 1; return true;
  }
  function render() {
    if (!state.year) return;
    const focused = document.activeElement?.dataset?.arcDate;
    const [start, end] = state.drag ? range(state.drag.start, state.drag.end) : [state.start, state.end];
    const valid = dateOf(start) && dateOf(end) && start <= end;
    const vacation = window.OnAirRequests?.isVacationType($('absence-req-type')?.value);
    const plan = valid && window.OnAirRequests?.adminDatePlan($('absence-req-type')?.value, start, end);
    const excludedKeys = new Set(plan?.excluded || []);
    $('arc-month-label').textContent = `${state.year}년 ${state.month}월`;
    grid.innerHTML = calendarCells(state.year, state.month).map(cell => {
      const excluded = excludedKeys.has(cell.key);
      const inside = !!valid && cell.key >= start && cell.key <= end && !excluded;
      const holiday = typeof finalHolidays !== 'undefined' && finalHolidays[cell.key];
      const classes = ['arc-day', ...(cell.weekday === 0 || holiday ? ['sun'] : cell.weekday === 6 ? ['sat'] : []), ...(excluded ? ['excluded'] : []),
        ...(cell.outside ? ['outside'] : []), ...(cell.key === todayStr ? ['today'] : []), ...(inside ? ['in-range'] : []),
        ...(inside && cell.key === start ? ['range-start'] : []), ...(inside && cell.key === end ? ['range-end'] : []),
        ...(cell.key === state.anchor ? ['anchor'] : [])];
      return `<button type="button" class="${classes.join(' ')}" data-arc-date="${cell.key}" aria-label="${esc(label(cell.key))}${excluded ? ' 휴가 제외' : inside ? ' 선택한 기간' : ''}${holiday ? ' '+esc(holiday.name) : ''}" aria-pressed="${inside}"${cell.valid ? '' : ' disabled'}${cell.key === todayStr ? ' aria-current="date"' : ''}>${cell.day}</button>`;
    }).join('');
    $('arc-prev-month').disabled = state.year === 1000 && state.month === 1;
    $('arc-next-month').disabled = state.year === 9999 && state.month === 12;
    $('arc-jump-start').disabled = !dateOf(state.start);
    $('arc-jump-end').disabled = !dateOf(state.end);
    $('arc-today').disabled = !dateOf(todayStr);
    if ($('arc-selection-caption')) $('arc-selection-caption').textContent = vacation && plan
      ? `신청 기간 · 휴가 ${plan.days}일 / 휴일 ${plan.excluded.length}일 제외` : '신청 기간';
    $('arc-selection-label').innerHTML = valid ? `<span class="arc-date">${esc(label(start))}</span>` + (start === end ? '' : ` ~ <span class="arc-date">${esc(label(end))}</span>`) : '시작일과 종료일을 확인해 주세요.';
    $('arc-selection-hint').textContent = state.drag ? '버튼을 놓으면 선택한 기간이 반영됩니다.' : state.anchor
      ? '종료일을 선택하세요. 같은 날짜를 누르면 하루 일정입니다.'
      : '시작일과 종료일을 차례로 누르거나, 날짜를 드래그해 선택하세요.';
    grid.classList.toggle('arc-dragging', !!state.drag);
    if (focused && !state.drag) grid.querySelector(`[data-arc-date="${focused}"]`)?.focus({preventScroll: true});
  }
  function releasePointer() {
    const pointerId = state.drag?.pointerId;
    state.drag = null;
    if (pointerId !== undefined && grid.hasPointerCapture?.(pointerId)) grid.releasePointerCapture(pointerId);
  }
  function sync(changedField = '') {
    if (committing) return false;
    releasePointer(); state.anchor = '';
    state.start = $('absence-req-start').value;
    state.end = $('absence-req-end').value;
    const preferred = changedField === 'absence-req-end' ? state.end : state.start;
    viewDate(preferred) || viewDate(state.start) || viewDate(state.end) || viewDate(todayStr);
    if (!state.year) { state.year = new Date().getFullYear(); state.month = new Date().getMonth() + 1; }
    render(); return true;
  }
  function commit(first, last) {
    if (!canEdit() || !dateOf(first) || !dateOf(last)) return false;
    [state.start, state.end] = range(first, last);
    // Set both values before emitting either change so existing draft and impact handlers see one valid range.
    $('absence-req-start').value = state.start;
    $('absence-req-end').value = state.end;
    committing = true;
    try {
      $('absence-req-start').dispatchEvent(new Event('change', {bubbles: true}));
      $('absence-req-end').dispatchEvent(new Event('change', {bubbles: true}));
    } finally { committing = false; }
    render(); return true;
  }
  function selectDate(key) {
    if (!canEdit() || !dateOf(key)) return false;
    releasePointer();
    const first = state.anchor || key;
    state.anchor = state.anchor ? '' : key;
    viewDate(key);
    return commit(first, key);
  }
  function moveMonth(delta) {
    if (state.drag) return false;
    const date = new Date(Date.UTC(state.year, state.month - 1 + Number(delta), 1));
    if (!Number.isFinite(date.getTime()) || date.getUTCFullYear() < 1000 || date.getUTCFullYear() > 9999) return false;
    state.year = date.getUTCFullYear(); state.month = date.getUTCMonth() + 1; render(); return true;
  }
  function cancel() {
    if (!state.drag && !state.anchor) return false;
    releasePointer(); state.anchor = ''; render(); return true;
  }
  function close() { releasePointer(); state.anchor = ''; }
  function open() { close(); sync(); }
  function dayAt(event) {
    const hit = document.elementFromPoint?.(event.clientX, event.clientY);
    const button = hit?.closest?.('[data-arc-date]');
    return button && grid.contains(button) && !button.disabled && dateOf(button.dataset.arcDate) ? button : null;
  }
  grid.addEventListener('pointerdown', event => {
    if (!canEdit() || event.button !== 0 || event.isPrimary === false || state.drag) return;
    const button = event.target.closest('[data-arc-date]');
    if (!button || button.disabled || !dateOf(button.dataset.arcDate)) return;
    event.preventDefault();
    state.drag = {pointerId: event.pointerId, start: button.dataset.arcDate, end: button.dataset.arcDate, moved: false};
    grid.setPointerCapture?.(event.pointerId); render();
  });
  grid.addEventListener('pointermove', event => {
    if (!state.drag || event.pointerId !== state.drag.pointerId) return;
    const button = dayAt(event);
    if (!button || button.dataset.arcDate === state.drag.end) return;
    state.drag.end = button.dataset.arcDate;
    state.drag.moved = state.drag.moved || state.drag.end !== state.drag.start;
    render();
  });
  grid.addEventListener('pointerup', event => {
    if (!state.drag || event.pointerId !== state.drag.pointerId) return;
    const button = dayAt(event), selected = state.drag;
    if (button) selected.end = button.dataset.arcDate;
    const dragged = selected.moved || selected.end !== selected.start;
    releasePointer();
    if (!canEdit()) { render(); return; }
    if (!button && !dragged) { render(); return; }
    if (dragged) { state.anchor = ''; commit(selected.start, selected.end); }
    else selectDate(selected.start);
  });
  grid.addEventListener('pointercancel', event => {
    if (state.drag?.pointerId === event.pointerId) { releasePointer(); render(); }
  });
  grid.addEventListener('lostpointercapture', event => {
    if (state.drag?.pointerId === event.pointerId) { state.drag = null; render(); }
  });
  grid.addEventListener('click', event => {
    // Pointer selections are already handled on release; detail 0 is keyboard/programmatic activation.
    if (event.detail > 0) return;
    const button = event.target.closest('[data-arc-date]');
    if (button && !button.disabled) selectDate(button.dataset.arcDate);
  });
  $('arc-prev-month').addEventListener('click', () => moveMonth(-1));
  $('arc-next-month').addEventListener('click', () => moveMonth(1));
  $('arc-today').addEventListener('click', () => { if (viewDate(todayStr)) render(); });
  $('arc-jump-start').addEventListener('click', () => { if (viewDate(state.start)) render(); });
  $('arc-jump-end').addEventListener('click', () => { if (viewDate(state.end)) render(); });
  $('absence-request-modal').addEventListener('input', event => {
    if (event.target.id === 'absence-req-start' || event.target.id === 'absence-req-end') sync(event.target.id);
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !$('absence-request-modal').classList.contains('active')) return;
    const top = typeof MODAL_CLOSERS !== 'undefined' ? [...MODAL_CLOSERS].reverse().find(([id]) => isModalOpen(id)) : null;
    if (top && top[0] !== 'absence-request-modal') return;
    if (cancel()) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  window.OnAirAbsenceRequestCalendar = {open, sync, close, render, selectDate, moveMonth, cancel,
    get state() { return {...state, drag: state.drag ? {...state.drag} : null}; }, _test: {dateOf, calendarCells}};
})();
