/* Read-only holiday browsing. Existing date editor remains responsible for mutations. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const weekdays = ['일', '월', '화', '수', '목', '금', '토'];
  const statusLabels = {assigned: '배정됨', missing: '미배정', off: '휴무'};
  const state = {year: 0, month: 1, selected: '', filter: 'all', page: 0, size: 8, hint: ''};
  let trigger = null;
  const keyOf = date => `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}-${String(date.getUTCDate()).padStart(2, '0')}`;
  function dateOf(key) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(key))) return null;
    const [year, month, day] = key.split('-').map(Number);
    const date = new Date(Date.UTC(year, month - 1, day));
    return year >= 1000 && year <= 9999 && keyOf(date) === key ? date : null;
  }
  function dateLabel(key, full = false) {
    const date = dateOf(key);
    return date ? `${full ? date.getUTCFullYear() + '.' : ''}${String(date.getUTCMonth() + 1).padStart(2, '0')}.${String(date.getUTCDate()).padStart(2, '0')}(${weekdays[date.getUTCDay()]})` : '';
  }
  const inRange = year => YEAR_LIST.includes(year);
  function workers(key) {
    return (scheduleMap[key] || []).filter(worker => worker && typeof worker.name === 'string' && worker.name.trim());
  }
  function info(key) {
    const holiday = finalHolidays[key];
    if (!holiday || !dateOf(key)) return null;
    const off = !!noDutyMap[key] || isMajorMainDay(key);
    const assigned = off ? [] : workers(key);
    const genders = assigned.map(worker => memberList.find(member => member.name === worker.name)?.gender || worker.gender);
    const missing = off ? [] : ['M', 'F'].filter(gender => !genders.includes(gender));
    return {key, name: String(holiday.name || ''), major: !!holiday.isMajor, workers: assigned,
      missing, status: off ? 'off' : missing.length ? 'missing' : 'assigned'};
  }
  function rows() {
    return Object.keys(finalHolidays).filter(key => key.startsWith(state.year + '-') && dateOf(key))
      .sort().map(info).filter(Boolean);
  }
  function baseRows() {
    const month = $('hp-month').value;
    const terms = $('hp-search').value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    return rows().filter(row => (month === 'all' || Number(row.key.slice(5, 7)) === Number(month)) &&
      terms.every(term => `${row.key} ${row.name} ${row.workers.map(worker => worker.name).join(' ')}`.toLocaleLowerCase().includes(term)));
  }
  function filtered() { return baseRows().filter(row => state.filter === 'all' || row.status === state.filter); }
  function peopleHtml(assigned) {
    return assigned.map(worker => {
      const member = memberList.find(item => item.name === worker.name);
      const gender = (member || worker).gender;
      return `<span class="hp-person${gender === 'M' ? ' male' : gender === 'F' ? ' female' : ''}">${esc(worker.name)}</span>`;
    }).join('');
  }
  function assignmentHtml(row) {
    if (row.status === 'off') return '<span class="hp-muted">당직 없음</span>';
    if (row.status === 'missing') return `<div class="hp-workers">${peopleHtml(row.workers)}<span class="hp-missing">${(row.missing || []).map(gender => gender === 'M' ? '남성' : '여성').join('·')} 미배정</span></div>`;
    return `<div class="hp-workers">${peopleHtml(row.workers)}</div>`;
  }
  function calendarCells(year, month) {
    const first = new Date(Date.UTC(year, month - 1, 1));
    first.setUTCDate(1 - first.getUTCDay());
    return Array.from({length: 42}, (_, offset) => {
      const date = new Date(first); date.setUTCDate(first.getUTCDate() + offset);
      return {key: keyOf(date), day: date.getUTCDate(), weekday: date.getUTCDay(), outside: date.getUTCMonth() + 1 !== month,
        supported: inRange(date.getUTCFullYear())};
    });
  }
  function renderCalendar(visible) {
    $('hp-calendar-month').textContent = `${state.year}년 ${state.month}월`;
    $('hp-calendar-grid').innerHTML = calendarCells(state.year, state.month).map(cell => {
      const row = info(cell.key), selected = cell.key === state.selected;
      const classes = ['hp-day', ...(cell.weekday === 0 ? ['sun'] : cell.weekday === 6 ? ['sat'] : []),
        ...(cell.outside ? ['out'] : []), ...(row ? ['holiday'] : []), ...(selected ? ['selected'] : []), ...(cell.key === todayStr ? ['today'] : [])];
      const label = `${dateLabel(cell.key, true)}${row ? ' ' + row.name : ''}`;
      return `<button type="button" class="${classes.join(' ')}" data-hp-date="${cell.key}" aria-pressed="${selected}" aria-label="${esc(label)}" title="${esc(label)}"${cell.supported ? '' : ' disabled'}${cell.key === todayStr ? ' aria-current="date"' : ''}><span class="hp-day-num">${cell.day}</span><span class="hp-day-dot${row ? '' : ' empty'}" aria-hidden="true"></span></button>`;
    }).join('');
    $('hp-calendar-prev').disabled = state.year === MIN_YEAR && state.month === 1;
    $('hp-calendar-next').disabled = state.year === MAX_YEAR && state.month === 12;
    $('hp-calendar-today').disabled = !dateOf(todayStr) || !inRange(Number(todayStr.slice(0, 4)));
    const selected = info(state.selected);
    $('hp-selected-date').textContent = dateLabel(state.selected, true) || '선택한 날짜가 없습니다.';
    $('hp-selected-name').textContent = selected ? selected.name : state.selected ? '공휴일이 아닙니다.' : '달력이나 목록에서 날짜를 선택하세요.';
    $('hp-selected-status').textContent = selected && dataLoaded ? statusLabels[selected.status] : selected ? '확인 필요' : '공휴일 아님';
    $('hp-selected-status').className = 'hp-status' + (selected && dataLoaded ? ' ' + selected.status : '');
    $('hp-selected-workers').innerHTML = !dataLoaded ? '<span class="hp-muted">당직 자료를 확인하고 있습니다.</span>' : selected ? assignmentHtml(selected) : '';
    const inCalendar = state.selected.startsWith(`${state.year}-${String(state.month).padStart(2, '0')}-`);
    $('hp-selection-note').textContent = state.hint || (selected && !visible.some(row => row.key === state.selected)
      ? '선택한 공휴일은 현재 목록 페이지·검색 조건 밖에 있습니다.'
      : state.selected && !inCalendar ? '선택한 날짜는 현재 달력 월 밖에 있습니다.' : '날짜나 공휴일명을 누르면 달력에서 확인할 수 있습니다.');
  }
  function focusToken() {
    const active = document.activeElement, data = active?.dataset;
    return data?.hpDate ? {attribute: 'data-hp-date', value: data.hpDate}
      : data?.hpSelect ? {attribute: 'data-hp-select', value: data.hpSelect, field: data.hpField}
      : data?.hpEdit ? {attribute: 'data-hp-edit', value: data.hpEdit} : null;
  }
  function restoreFocus(token) {
    if (!token) return;
    const selector = `#preview-modal [${token.attribute}="${token.value}"]${token.field ? `[data-hp-field="${token.field}"]` : ''}`;
    (document.querySelector(selector) || $('hp-search'))?.focus({preventScroll: true});
  }
  function render() {
    if (!state.year) return;
    const token = focusToken();
    mergeHolidays();
    state.size = window.innerHeight <= 850 ? 7 : 8;
    const base = baseRows(), all = filtered(), pages = Math.max(1, Math.ceil(all.length / state.size));
    state.page = Math.max(0, Math.min(pages - 1, state.page));
    const start = state.page * state.size, shown = all.slice(start, start + state.size), admin = SYSTEM_MODE === 'ADMIN' && dataLoaded;
    for (const status of ['all', 'assigned', 'missing', 'off']) {
      $('hp-count-' + status).textContent = String(status === 'all' ? base.length : dataLoaded ? base.filter(row => row.status === status).length : 0);
    }
    document.querySelectorAll('#preview-modal [data-hp-filter]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.hpFilter === state.filter)));
    const colspan = admin ? 5 : 4;
    $('prev-tbody').innerHTML = !dataLoaded ? `<tr><td colspan="${colspan}" class="hp-empty">${dataLoadError ? '당직 자료를 불러오지 못했습니다. 연결 상태를 확인해 주세요.' : '당직 자료를 불러오는 중입니다.'}</td></tr>`
      : shown.length ? shown.map(row => `<tr data-hp-row="${row.key}" class="${row.key === state.selected ? 'hp-selected' : ''}" aria-selected="${row.key === state.selected}">` +
        `<td><button type="button" class="hp-select hp-date" data-hp-select="${row.key}" data-hp-field="date" aria-label="${esc(dateLabel(row.key, true) + ' ' + row.name + ' 달력에서 보기')}" aria-pressed="${row.key === state.selected}">${esc(dateLabel(row.key))}</button></td>` +
        `<td><button type="button" class="hp-select hp-holiday-name" data-hp-select="${row.key}" data-hp-field="name" aria-pressed="${row.key === state.selected}">${esc(row.name)}${row.major ? ' <span class="hp-major">명절</span>' : ''}</button></td>` +
        `<td>${assignmentHtml(row)}</td><td><span class="hp-status ${row.status}">${statusLabels[row.status]}</span></td>` +
        (admin ? `<td class="admin-only"><button type="button" class="hp-edit" data-hp-edit="${row.key}" data-needs-server aria-label="${esc(dateLabel(row.key, true) + ' ' + row.name + ' 당직 수정')}">수정</button></td>` : '') + '</tr>').join('')
      : `<tr><td colspan="${colspan}" class="hp-empty">${rows().length ? '조건에 맞는 공휴일이 없습니다.' : '이 연도에 표시할 공휴일이 없습니다.'}</td></tr>`;
    $('hp-range').textContent = dataLoaded && all.length ? `${start + 1}–${Math.min(start + state.size, all.length)} / ${all.length}일` : '0일';
    $('hp-page-label').textContent = `${state.page + 1} / ${pages}`;
    $('hp-prev-page').disabled = !dataLoaded || state.page === 0;
    $('hp-next-page').disabled = !dataLoaded || state.page + 1 >= pages;
    const upcoming = rows().find(row => row.key >= todayStr);
    $('hp-jump').disabled = !upcoming;
    $('hp-jump').title = upcoming ? `${dateLabel(upcoming.key, true)} ${upcoming.name}` : '선택한 연도에 다가오는 공휴일이 없습니다.';
    renderCalendar(shown);
    applyModeUI();
    if (typeof renderConnState === 'function') renderConnState();
    restoreFocus(token);
  }
  function revealSelected() {
    const at = filtered().findIndex(row => row.key === state.selected);
    if (at >= 0) state.page = Math.floor(at / state.size);
  }
  function select(key, source = 'list') {
    const date = dateOf(key);
    if (!date || !inRange(date.getUTCFullYear())) return false;
    mergeHolidays();
    state.selected = key; state.year = date.getUTCFullYear(); state.month = date.getUTCMonth() + 1; state.hint = '';
    $('preview-year').value = String(state.year);
    if (source === 'calendar' && info(key) && !filtered().some(row => row.key === key)) {
      $('hp-month').value = 'all'; $('hp-search').value = ''; state.filter = 'all';
      state.hint = '선택한 공휴일을 표시하기 위해 검색·필터를 해제했습니다.';
    }
    revealSelected(); render(); return true;
  }
  function moveMonth(delta) {
    const date = new Date(Date.UTC(state.year, state.month - 1 + Number(delta), 1));
    if (!inRange(date.getUTCFullYear())) return false;
    const yearChanged = state.year !== date.getUTCFullYear();
    state.year = date.getUTCFullYear(); state.month = date.getUTCMonth() + 1; state.hint = '';
    $('preview-year').value = String(state.year);
    if ($('hp-month').value !== 'all') { $('hp-month').value = String(state.month); state.page = 0; }
    if (yearChanged) state.page = 0;
    render(); return true;
  }
  function filterChanged(kind = '') {
    state.page = 0; state.hint = '';
    const month = Number($('hp-month').value);
    if (kind === 'month' && month >= 1 && month <= 12) state.month = month;
    render();
  }
  function setFilter(value) {
    if (!['all', 'assigned', 'missing', 'off'].includes(value)) return false;
    state.filter = value; filterChanged(); return true;
  }
  function setPage(value) { state.page = Number.isFinite(Number(value)) ? Math.trunc(Number(value)) : 0; state.hint = ''; render(); }
  function jumpUpcoming() {
    mergeHolidays();
    const upcoming = rows().find(row => row.key >= todayStr);
    if (!upcoming) return false;
    $('hp-month').value = 'all'; $('hp-search').value = ''; state.filter = 'all';
    return select(upcoming.key);
  }
  function edit(key) {
    if (SYSTEM_MODE !== 'ADMIN' || !dataLoaded || !info(key)) return false;
    openPreviewDate(key); return true;
  }
  const originalClose = closePreviewModal;
  closePreviewModal = function() {
    const open = $('preview-modal').classList.contains('active');
    const result = originalClose.apply(this, arguments);
    if (open && trigger?.isConnected) trigger.focus({preventScroll: true});
    trigger = null;
    return result;
  };
  openPreviewModal = function() {
    if (!$('preview-modal').classList.contains('active')) trigger = document.activeElement;
    state.year = inRange(curYear) ? curYear : MIN_YEAR;
    state.month = Number(curMonth) >= 1 && Number(curMonth) <= 12 ? Number(curMonth) : 1;
    state.filter = 'all'; state.page = 0; state.selected = ''; state.hint = '';
    state.size = window.innerHeight <= 850 ? 7 : 8;
    $('preview-year').value = String(state.year); $('hp-month').value = 'all'; $('hp-search').value = '';
    mergeHolidays();
    const available = rows(), first = available.find(row => row.key >= todayStr) || available[0];
    if (first) { state.selected = first.key; state.month = Number(first.key.slice(5, 7)); revealSelected(); }
    render(); $('preview-modal').classList.add('active');
    window.OnAirUI?.raise('preview-modal'); $('hp-search').focus({preventScroll: true});
  };
  renderPreview = function(year) {
    if (!inRange(Number(year))) return false;
    if (state.year !== Number(year)) {
      state.year = Number(year); state.page = 0; state.selected = ''; state.hint = '';
      $('preview-year').value = String(state.year);
      mergeHolidays();
      const available = filtered(), first = available.find(row => row.key >= todayStr) || available[0];
      if (first) { state.selected = first.key; state.month = Number(first.key.slice(5, 7)); revealSelected(); }
    }
    render(); return true;
  };
  const originalFetch = fetchAllData;
  fetchAllData = async function() {
    const result = await originalFetch.apply(this, arguments);
    if ($('preview-modal')?.classList.contains('active')) render();
    return result;
  };
  $('hp-search').addEventListener('input', () => filterChanged());
  $('preview-year').addEventListener('change', event => renderPreview(Number(event.target.value)));
  $('hp-month').addEventListener('change', () => filterChanged('month'));
  $('hp-jump').addEventListener('click', jumpUpcoming);
  $('hp-prev-page').addEventListener('click', () => setPage(state.page - 1));
  $('hp-next-page').addEventListener('click', () => setPage(state.page + 1));
  $('hp-calendar-prev').addEventListener('click', () => moveMonth(-1));
  $('hp-calendar-next').addEventListener('click', () => moveMonth(1));
  $('hp-calendar-today').addEventListener('click', () => select(todayStr, 'calendar'));
  $('hp-calendar-grid').addEventListener('click', event => { const button = event.target.closest('[data-hp-date]'); if (button && !button.disabled) select(button.dataset.hpDate, 'calendar'); });
  $('prev-tbody').addEventListener('click', event => {
    const selection = event.target.closest('[data-hp-select]'), editing = event.target.closest('[data-hp-edit]');
    if (selection) select(selection.dataset.hpSelect);
    else if (editing && !editing.disabled) edit(editing.dataset.hpEdit);
  });
  document.querySelectorAll('#preview-modal [data-hp-filter]').forEach(button => button.addEventListener('click', () => setFilter(button.dataset.hpFilter)));
  $('preview-modal').addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const modal = $('preview-modal');
    const top = typeof MODAL_CLOSERS !== 'undefined' ? [...MODAL_CLOSERS].reverse().find(([id]) => isModalOpen(id)) : null;
    if (top && top[0] !== modal.id) return;
    const fields = [...modal.querySelectorAll('button,input,select,textarea,a[href],[tabindex]')]
      .filter(field => !field.disabled && field.tabIndex >= 0 && field.getClientRects().length);
    if (!fields.length) return;
    if (event.shiftKey && (document.activeElement === fields[0] || !modal.contains(document.activeElement))) { event.preventDefault(); fields.at(-1).focus(); }
    else if (!event.shiftKey && (document.activeElement === fields.at(-1) || !modal.contains(document.activeElement))) { event.preventDefault(); fields[0].focus(); }
  });
  window.addEventListener('resize', () => { if ($('preview-modal')?.classList.contains('active')) render(); });
  window.OnAirHolidayPreview = {render, rows, filtered, select, moveMonth, filterChanged, setFilter, setPage, jumpUpcoming, edit,
    get state() { return {...state}; }, _test: {dateOf, calendarCells, dateLabel}};
})();
