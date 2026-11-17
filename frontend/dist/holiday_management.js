/* Keep holiday selection local to this dialog; existing mutations own persistence. */
(function () {
  'use strict';
  const W = window, $ = id => document.getElementById(id);
  const weekdays = ['일', '월', '화', '수', '목', '금', '토'];
  const state = {year: 0, month: 0, selected: '', page: 0, size: 5, baseline: '', source: '', saving: false, selecting: false};
  let trigger = null;
  const keyOf = date => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
  function dateOf(key) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(key))) return null;
    const [y, m, d] = key.split('-').map(Number);
    const date = new Date(y, m - 1, d);
    return y >= 1000 && keyOf(date) === key ? date : null;
  }
  const label = key => { const d = dateOf(key); return d ? `${key.replaceAll('-', '.')} (${weekdays[d.getDay()]})` : ''; };
  function info(key) {
    const base = BASE_HOLIDAYS[key], custom = customHolidays[key];
    if (!base && !custom) return null;
    const removed = !!(custom && custom.isRemoved), added = !!(custom && !custom.isRemoved);
    const value = removed ? (base || custom) : (finalHolidays[key] || base || custom);
    return value ? {name: String(value.name || ''), major: !!value.isMajor, removed, custom: added} : null;
  }
  const sourceStamp = key => JSON.stringify([BASE_HOLIDAYS[key] || null, customHolidays[key] || null]);
  const form = () => ({date: $('hol-date').value, name: $('hol-name').value, major: $('hol-major').checked});
  const dirty = () => !!state.baseline && JSON.stringify(form()) !== state.baseline;
  function remember() { state.baseline = JSON.stringify(form()); state.source = sourceStamp(state.selected); }
  function ensureYear(year) {
    const select = $('hol-year');
    if (!Array.from(select.options).some(option => option.value === String(year))) select.add(new Option(`${year}년`, String(year)));
    select.value = String(year);
  }
  function filtered() {
    const year = $('hol-year').value, terms = $('hol-search').value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    return [...new Set([...Object.keys(BASE_HOLIDAYS), ...Object.keys(customHolidays)])].sort()
      .filter(key => key.startsWith(year + '-') && dateOf(key))
      .map(key => ({key, value: info(key)})).filter(row => row.value && terms.every(term => `${row.key} ${row.value.name}`.toLocaleLowerCase().includes(term)));
  }
  function revealSelected() {
    const at = filtered().findIndex(row => row.key === state.selected);
    if (at >= 0) state.page = Math.floor(at / state.size);
  }
  function renderCalendar() {
    if (!state.year) return;
    const focusedDate = document.activeElement?.getAttribute?.('data-hol-date');
    $('hol-month-label').textContent = `${state.year}년 ${state.month + 1}월`;
    const start = new Date(state.year, state.month, 1);
    start.setDate(1 - start.getDay());
    let html = '';
    for (let i = 0; i < 42; i++) {
      const date = new Date(start); date.setDate(start.getDate() + i);
      const key = keyOf(date), value = info(key), classes = ['hol-day'];
      if (!date.getDay()) classes.push('sun'); else if (date.getDay() === 6) classes.push('sat');
      if (date.getMonth() !== state.month) classes.push('out');
      if (value) classes.push(value.removed ? 'removed' : value.custom ? 'custom' : 'holiday');
      if (key === state.selected) classes.push('selected');
      if (key === todayStr) classes.push('today');
      const description = `${label(key)}${value ? ` ${value.name}${value.removed ? ' 해제됨' : ''}` : ''}`;
      html += `<button type="button" class="${classes.join(' ')}" data-hol-date="${key}" aria-label="${esc(description)}" aria-pressed="${key === state.selected}"${state.saving ? ' disabled' : ''}${key === todayStr ? ' aria-current="date"' : ''} title="${esc(description)}"><span class="hol-day-num">${date.getDate()}</span><span class="hol-day-dot${value ? '' : ' empty'}" aria-hidden="true"></span></button>`;
    }
    $('hol-calendar-grid').innerHTML = html;
    if (dateOf(focusedDate)) $('hol-calendar-grid').querySelector(`[data-hol-date="${focusedDate}"]`)?.focus({preventScroll: true});
    const selected = info(state.selected);
    $('hol-selected-date').textContent = label(state.selected);
    $('hol-selected-name').textContent = selected ? selected.name : '등록된 공휴일이 없습니다.';
    const kind = selected ? selected.removed ? '해제됨' : selected.custom ? '직접 지정' : '기본 제공' : '공휴일 아님';
    $('hol-selected-kind').textContent = kind;
    $('hol-selected-kind').className = 'hol-kind' + (selected ? selected.removed ? ' removed' : selected.custom ? ' custom' : '' : '');
    $('hol-prev-month').disabled = state.saving || (state.year === 1000 && state.month === 0);
    $('hol-next-month').disabled = state.saving || (state.year === 9999 && state.month === 11);
  }
  function renderList() {
    const rows = filtered(), pages = Math.max(1, Math.ceil(rows.length / state.size));
    state.page = Math.max(0, Math.min(state.page, pages - 1));
    const visible = rows.slice(state.page * state.size, (state.page + 1) * state.size);
    $('hol-list-count').textContent = `${rows.length}건`;
    $('hol-tbody').innerHTML = visible.length ? visible.map(({key, value}) => {
      const action = value.custom ? 'delCustomHoliday' : value.removed ? 'restoreBaseHoliday' : 'removeBaseHoliday';
      const verb = value.custom ? '삭제' : value.removed ? '복구' : '해제';
      return `<tr data-hol-row="${key}" class="${key === state.selected ? 'selected-row ' : ''}${value.removed ? 'removed-row' : ''}" aria-selected="${key === state.selected}">` +
        `<td>${esc(label(key))}</td><td class="hol-row-name">${esc(value.name)}${value.removed ? ' <span class="hol-kind removed">해제</span>' : ''}</td>` +
        `<td>${value.major ? '명절' : '—'}</td><td><span class="hol-kind${value.custom ? ' custom' : ''}">${value.custom ? '직접 지정' : '기본 제공'}</span></td>` +
        `<td><div class="hol-row-actions"><button type="button" data-hol-edit="${key}"${state.saving ? ' disabled' : ''} aria-label="${esc(label(key) + ' ' + value.name)} 수정">수정</button>` +
        `<button type="button"${value.custom ? ' class="btn-danger"' : ''}${state.saving ? ' disabled' : ''} data-needs-server onclick="${action}('${key}')" aria-label="${esc(label(key) + ' ' + value.name + ' ' + verb)}">${verb}</button></div></td></tr>`;
    }).join('') : '<tr><td colspan="5" class="hol-empty">표시할 공휴일이 없습니다.</td></tr>';
    $('hol-range').textContent = rows.length ? `${state.page * state.size + 1}–${Math.min((state.page + 1) * state.size, rows.length)} / ${rows.length}건` : '0건';
    $('hol-page-label').textContent = `${state.page + 1} / ${pages}`;
    $('hol-prev-page').disabled = state.saving || state.page === 0;
    $('hol-next-page').disabled = state.saving || state.page + 1 >= pages;
    renderCalendar();
    if (typeof renderConnState === 'function') renderConnState();
  }
  function renderHistory() {
    const rows = changeHistory.filter(row => String(row.type).startsWith('공휴일')).slice(0, 50);
    $('hol-history').innerHTML = rows.length ? rows.map(row => `<div class="hol-history-row"><time>${esc(row.timestamp || '')}</time><span><b>${esc(row.type)}</b> · ${esc(row.details || '')}</span></div>`).join('') : '<span>변경 이력이 없습니다.</span>';
  }
  function fill(key) {
    const date = dateOf(key); if (!date) return false;
    state.selected = key; state.year = date.getFullYear(); state.month = date.getMonth();
    ensureYear(state.year);
    const value = info(key);
    $('hol-date').value = key;
    $('hol-date').removeAttribute('aria-invalid');
    $('hol-name').value = value ? value.name : '';
    $('hol-major').checked = !!(value && value.major);
    $('hol-editor-title').textContent = value ? '공휴일 수정' : '새 공휴일 등록';
    $('hol-editor-mode').textContent = value ? value.removed ? '해제됨' : value.custom ? '직접 지정' : '기본 제공' : '새 등록';
    $('hol-editor-mode').className = 'hol-kind' + (value && value.custom ? ' custom' : '');
    $('hol-submit').textContent = value ? '변경 반영' : '공휴일 추가';
    remember(); revealSelected(); renderList(); return true;
  }
  async function discardDraft() {
    return !dirty() || await OnAirAdmin.confirmDiscard('공휴일에서 바꾼 내용이 저장되지 않았습니다. 입력을 바꿀까요?');
  }
  async function select(key, focusName = false) {
    if (state.saving || state.selecting || !dateOf(key)) return false;
    state.selecting = true;
    try {
      if (!(await discardDraft())) return false;
      fill(key);
      if (focusName) $('hol-name').focus();
      return true;
    } finally { state.selecting = false; }
  }
  function fitPage() {
    const wrap = document.querySelector('#holiday-modal .hol-table-wrap');
    if (!wrap || !$('holiday-modal').classList.contains('active') || wrap.clientHeight < 100) return;
    const size = Math.max(2, Math.min(8, Math.floor((wrap.clientHeight - 36) / 41)));
    if (size === state.size) return;
    const first = state.page * state.size;
    state.size = size; state.page = Math.floor(first / size); renderList();
  }
  function open() {
    if (SYSTEM_MODE !== 'ADMIN') return false;
    if ($('holiday-modal').classList.contains('active')) return true;
    trigger = document.activeElement;
    mergeHolidays(); state.page = 0; $('hol-search').value = '';
    $('holiday-modal').classList.add('active');
    if (W.OnAirUI) W.OnAirUI.raise('holiday-modal');
    const key = todayStr.startsWith(`${curYear}-${String(curMonth).padStart(2, '0')}-`) ? todayStr : `${curYear}-${String(curMonth).padStart(2, '0')}-01`;
    fill(key); renderHistory(); requestAnimationFrame(() => { fitPage(); revealSelected(); renderList(); });
    document.querySelector('#holiday-modal .hol-head button')?.focus({preventScroll: true});
    return true;
  }
  function moveMonth(step) {
    if (state.saving || state.selecting) return;
    const date = new Date(state.year, state.month + step, 1);
    if (date.getFullYear() < 1000 || date.getFullYear() > 9999) return;
    state.year = date.getFullYear(); state.month = date.getMonth(); ensureYear(state.year); state.page = 0; renderList();
  }
  async function changeYear() {
    const year = Number($('hol-year').value);
    if (!Number.isInteger(year) || year < 1000 || year > 9999 || state.saving || state.selecting) return;
    state.year = year; state.page = 0; renderList();
  }
  const oldClose = W.closeHolidayModal;
  function finishClose() {
    const result = oldClose(true);
    if (trigger?.isConnected && typeof trigger.focus === 'function') trigger.focus({preventScroll: true});
    trigger = null;
    return result;
  }
  W.closeHolidayModal = function (force) {
    if (state.saving || state.selecting) return false;
    if (force !== true && dirty()) {
      state.selecting = true;
      OnAirAdmin.confirmDiscard('공휴일에서 바꾼 내용이 저장되지 않았습니다. 닫을까요?')
        .then(ok => { if (ok) finishClose(); }).finally(() => { state.selecting = false; });
      return false;
    }
    return finishClose();
  };
  // Preserve the existing authorization, month-close, version checks and assignment-preview wrappers.
  ['addCustomHoliday', 'delCustomHoliday', 'removeBaseHoliday', 'restoreBaseHoliday'].forEach(name => {
    const previous = W[name];
    W[name] = async function (...args) {
      if (SYSTEM_MODE !== 'ADMIN' || state.saving || state.selecting) return false;
      if (!SERVER_CONNECTED || !dataLoaded || (typeof connectionLost === 'function' && connectionLost())) {
        showToast('⚠️ 연결 상태와 자료 불러오기를 확인한 뒤 다시 시도하세요. 입력한 내용은 유지됩니다.', true); return false;
      }
      const key = name === 'addCustomHoliday' ? $('hol-date').value : args[0];
      if (!dateOf(key)) { showToast('⚠️ 올바른 날짜를 입력하세요.', true); return false; }
      if (name === 'addCustomHoliday' && !$('hol-name').value.trim()) { showToast('⚠️ 날짜와 공휴일 이름을 입력하세요.', true); return false; }
      if (name === 'addCustomHoliday' && $('hol-name').value.trim().length > 100) { showToast('⚠️ 공휴일 이름은 100자 이내로 입력하세요.', true); $('hol-name').focus(); return false; }
      const sourceBeforeConfirm = sourceStamp(key);
      if (state.selected === key && state.source !== sourceBeforeConfirm) {
        showToast('⚠️ 이 날짜의 공휴일이 다른 PC에서 변경되었습니다. 날짜를 다시 선택해 최신 내용을 확인하세요.', true); return false;
      }
      if (name !== 'addCustomHoliday' && state.selected === key) {
        state.selecting = true;
        try { if (!(await discardDraft())) return false; }
        finally { state.selecting = false; }
      }
      if (sourceBeforeConfirm !== sourceStamp(key)) {
        showToast('⚠️ 확인하는 동안 이 날짜의 공휴일이 다른 PC에서 변경되었습니다. 날짜를 다시 선택해 최신 내용을 확인하세요.', true); return false;
      }
      const draft = form();
      const focused = document.activeElement;
      state.saving = true;
      const controls = Array.from($('holiday-modal').querySelectorAll('button, input, select'));
      const previousDisabled = controls.map(control => control.disabled);
      controls.forEach(control => { control.disabled = true; });
      try {
        const ok = await previous.apply(this, args);
        if (ok === true) {
          if (name === 'addCustomHoliday' || state.selected === key) fill(key);
          else { renderList(); renderHistory(); }
        } else {
          $('hol-date').value = draft.date; $('hol-name').value = draft.name; $('hol-major').checked = draft.major;
        }
        return ok;
      } finally {
        state.saving = false;
        controls.forEach((control, i) => { control.disabled = previousDisabled[i]; });
        renderList(); renderHistory();
        if (focused?.isConnected && typeof focused.focus === 'function') focused.focus({preventScroll: true});
        else if ($('holiday-modal').classList.contains('active')) $('hol-submit').focus({preventScroll: true});
      }
    };
  });
  $('hol-calendar-grid').addEventListener('click', event => {
    const button = event.target.closest('[data-hol-date]'); if (button) select(button.dataset.holDate);
  });
  $('hol-name').setAttribute('maxlength', '100');
  $('hol-tbody').addEventListener('click', event => {
    const edit = event.target.closest('[data-hol-edit]');
    if (edit) { select(edit.dataset.holEdit, true); return; }
    if (event.target.closest('button')) return;
    const row = event.target.closest('[data-hol-row]'); if (row) select(row.dataset.holRow);
  });
  $('hol-prev-month').addEventListener('click', () => moveMonth(-1));
  $('hol-next-month').addEventListener('click', () => moveMonth(1));
  $('hol-today').addEventListener('click', () => select(todayStr));
  async function commitDate() {
    const next = $('hol-date').value, previous = state.selected;
    if (!dateOf(next)) { $('hol-date').setAttribute('aria-invalid', 'true'); return false; }
    $('hol-date').removeAttribute('aria-invalid');
    if (next === previous) return true;
    $('hol-date').value = previous;
    return select(next);
  }
  // Native date inputs emit intermediate change events while a year is typed.
  // Keep that input intact until the user leaves the field or presses Enter.
  $('hol-date').addEventListener('blur', commitDate);
  $('hol-date').addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); commitDate(); }
  });
  $('hol-new').addEventListener('click', async () => {
    if (state.saving || state.selecting) return;
    state.selecting = true;
    try {
      if (!(await discardDraft())) return;
      $('hol-name').value = ''; $('hol-major').checked = false;
      $('hol-editor-title').textContent = '새 공휴일 등록'; $('hol-editor-mode').textContent = '새 등록'; $('hol-submit').textContent = '공휴일 추가';
      remember(); $('hol-name').focus();
    } finally { state.selecting = false; }
  });
  $('hol-search').addEventListener('input', () => { state.page = 0; renderList(); });
  $('hol-prev-page').addEventListener('click', () => { state.page--; renderList(); });
  $('hol-next-page').addEventListener('click', () => { state.page++; renderList(); });
  $('hol-history-toggle').addEventListener('click', () => {
    const expanded = $('hol-history-section').classList.toggle('expanded');
    $('hol-history-toggle').setAttribute('aria-expanded', String(expanded));
    $('hol-history-toggle').textContent = expanded ? '접기' : '펼치기';
    requestAnimationFrame(fitPage);
  });
  if (typeof ResizeObserver === 'function') new ResizeObserver(fitPage).observe(document.querySelector('#holiday-modal .hol-table-wrap'));
  W.addEventListener('resize', fitPage);
  $('holiday-modal').addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const modal = $('holiday-modal');
    const top = typeof MODAL_CLOSERS !== 'undefined' ? [...MODAL_CLOSERS].reverse().find(([id]) => isModalOpen(id)) : null;
    if (top && top[0] !== modal.id) return;
    const fields = [...modal.querySelectorAll('button,input,select,textarea,a[href],summary,[tabindex]')]
      .filter(field => !field.disabled && field.tabIndex >= 0 && field.getClientRects().length);
    if (!fields.length) { event.preventDefault(); return; }
    if (event.shiftKey && (document.activeElement === fields[0] || !modal.contains(document.activeElement))) {
      event.preventDefault(); fields.at(-1).focus();
    } else if (!event.shiftKey && (document.activeElement === fields.at(-1) || !modal.contains(document.activeElement))) {
      event.preventDefault(); fields[0].focus();
    }
  });
  W.OnAirHolidayManagement = {open, renderList, renderHistory, changeYear, select, moveMonth, fitPage, get state() { return {...state}; }, _test: {dateOf, label, info, filtered, dirty, commitDate}};
})();
