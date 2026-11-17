/* Presentation-only companions for the existing absence editor and save workflow. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const educationKeys = ['title', 'location', 'provider', 'preparation', 'content', 'notes'];
  const educationInputs = () => educationKeys.map(key => $('ur-admin-edu-' + key)).filter(Boolean);
  const canEdit = () => SYSTEM_MODE === 'ADMIN' && !absenceFormSaving && !isSaving;
  const checkedNames = () => [...document.querySelectorAll('[data-ur-bulk-name]:checked')].map(input => input.value);
  const calendar = {selectedId: null, year: 0, month: 0, draft: false, anchor: ''};
  let pickingDate = false;
  let drag = null;
  const longList = {page: 0, query: '', filter: ''};
  const previewSelector = '#abs-tbody [data-absence-preview], #abs-long-list [data-absence-preview]';
  const dayNames = ['일', '월', '화', '수', '목', '금', '토'];
  const dateKey = date => `${String(date.getUTCFullYear()).padStart(4, '0')}-${String(date.getUTCMonth() + 1).padStart(2, '0')}-${String(date.getUTCDate()).padStart(2, '0')}`;
  function calendarDate(key) {
    if (!validAbsenceDate(String(key || ''))) return null;
    const [year, month, day] = key.split('-').map(Number);
    return new Date(Date.UTC(year, month - 1, day));
  }
  function selectedRecord() {
    if (calendar.selectedId === null) return null;
    return absences.find(record => Number(record.id) === calendar.selectedId) || null;
  }
  function displayedRange() {
    if (!calendar.draft && !drag) return selectedRecord();
    const [start, end] = drag ? [drag.start, drag.end].sort() : [$('abs-start').value, $('abs-end').value];
    return {name: $('abs-name').value || '새 일정', type: $('abs-type').value, start, end};
  }
  function beginDraft() {
    calendar.selectedId = null; calendar.draft = true; calendar.anchor = '';
    const date = calendarDate($('abs-start').value);
    if (date) { calendar.year = date.getUTCFullYear(); calendar.month = date.getUTCMonth() + 1; }
    renderCalendarPreview();
  }
  function canPickDate() {
    return canEdit() && $('absence-modal')?.classList.contains('active') && !$('abs-start').disabled && !$('abs-end').disabled;
  }
  function commitDates(start, end, anchor = '') {
    if (!canPickDate() || !calendarDate(start) || !calendarDate(end)) return false;
    [start, end] = [start, end].sort();
    calendar.selectedId = null; calendar.draft = true; calendar.anchor = anchor;
    $('abs-start').value = start; $('abs-end').value = end;
    pickingDate = true;
    try { updateAbsenceDateLabels(); } finally { pickingDate = false; }
    renderCalendarPreview();
    return true;
  }
  function pickCalendarDate(key) {
    const anchor = calendar.draft && calendar.anchor;
    return commitDates(anchor || key, key, anchor ? '' : key);
  }
  function calendarCells(year, month, record) {
    const first = new Date(Date.UTC(year, month - 1, 1));
    first.setUTCDate(1 - first.getUTCDay());
    const validRange = record && calendarDate(record.start) && calendarDate(record.end) && record.start <= record.end;
    return Array.from({length: 42}, (_, offset) => {
      const date = new Date(first); date.setUTCDate(first.getUTCDate() + offset);
      const key = dateKey(date);
      return {key, day: date.getUTCDate(), weekday: date.getUTCDay(), outside: date.getUTCMonth() + 1 !== month,
        inRange: !!validRange && key >= record.start && key <= record.end,
        start: !!validRange && key === record.start, end: !!validRange && key === record.end, today: key === todayStr};
    });
  }
  function markPreviewRows() {
    document.querySelectorAll('#abs-tbody [data-absence-id], #abs-long-list [data-absence-id]').forEach(row => {
      row.classList.toggle('abs-calendar-selected', calendar.selectedId !== null && Number(row.dataset.absenceId) === calendar.selectedId);
    });
    document.querySelectorAll(previewSelector).forEach(button => {
      button.setAttribute('aria-pressed', String(calendar.selectedId !== null && Number(button.dataset.absencePreview) === calendar.selectedId));
    });
  }
  function renderCalendarPreview() {
    if (!$('abs-calendar-grid') || !calendar.year) return;
    let record = displayedRange();
    if (!record && calendar.selectedId !== null) calendar.selectedId = null;
    $('abs-calendar-month').textContent = `${calendar.year}년 ${calendar.month}월`;
    const cells = calendarCells(calendar.year, calendar.month, record);
    const focused = document.activeElement?.dataset?.date;
    const focusKey = [focused, record?.start, todayStr].find(key => cells.some(c => c.key === key)) || cells.find(c => !c.outside).key;
    const cellHtml = cell => {
      const excluded = !!(calendar.draft || drag) && cell.inRange && window.OnAirRequests?.isVacationType(record?.type) && isOffDayKey(cell.key);
      const selected = cell.inRange && !excluded;
      const classes = ['abs-calendar-day', ...(cell.outside ? ['outside'] : []), ...(selected ? ['in-range'] : []), ...(excluded ? ['excluded'] : []),
        ...(cell.start && selected ? ['range-start'] : []), ...(cell.end && selected ? ['range-end'] : []), ...(cell.today ? ['today'] : []),
        ...(cell.weekday === 0 || finalHolidays[cell.key] ? ['sun'] : cell.weekday === 6 ? ['sat'] : [])];
      const label = `${cell.key.replaceAll('-', '.')}(${dayNames[cell.weekday]})${excluded ? ' 휴가 제외' : selected ? ' 선택 일정' : ''}${cell.start ? ' 시작' : ''}${cell.end ? ' 종료' : ''}${finalHolidays[cell.key] ? ' '+finalHolidays[cell.key].name : ''}`;
      return `<button type="button" class="${classes.join(' ')}" role="gridcell" data-date="${cell.key}" tabindex="${cell.key === focusKey ? 0 : -1}" aria-selected="${selected}" aria-label="${esc(label)}"${calendarDate(cell.key) ? '' : ' disabled'}${cell.today ? ' aria-current="date"' : ''}>${cell.day}</button>`;
    };
    $('abs-calendar-grid').innerHTML = Array.from({length: 6}, (_, row) =>
      '<div role="row" class="abs-calendar-row" style="display:contents">' + cells.slice(row * 7, row * 7 + 7).map(cellHtml).join('') + '</div>').join('');
    $('abs-calendar-prev').disabled = calendar.year === 100 && calendar.month === 1;
    $('abs-calendar-next').disabled = calendar.year === 9999 && calendar.month === 12;
    $('abs-calendar-start').disabled = !calendarDate(record?.start);
    $('abs-calendar-end').disabled = !calendarDate(record?.end);
    $('abs-calendar-selection').innerHTML = record
      ? `<strong class="abs-calendar-person">${esc(record.name)}</strong><span class="abs-kind abs-kind-${absenceKind(record)}">${esc(record.type)}</span>` +
        `<span class="abs-calendar-period">${esc(absenceManagementPeriod(record.start, record.end, false))}</span>`
      : '<strong>선택한 일정이 없습니다.</strong><span>달력에서 날짜를 선택하세요.</span>';
    const inCurrentList = record && [...document.querySelectorAll(previewSelector)].some(button => Number(button.dataset.absencePreview) === calendar.selectedId);
    $('abs-calendar-note').textContent = drag ? '마우스를 놓으면 선택한 기간이 오른쪽에 반영됩니다.' : calendar.draft
      ? (calendar.anchor ? '종료일을 선택하세요. 같은 날짜는 하루 일정입니다.' : '시작일·종료일을 차례로 선택하면 오른쪽에 반영됩니다.')
      : !record ? '달력은 날짜 입력, 목록의 이름·기간은 일정 조회입니다.'
      : !inCurrentList ? '선택한 일정은 현재 목록 페이지·검색 조건 밖에 있습니다.'
      : '저장된 일정입니다. 수정하려면 목록의 [수정]을 누르세요.';
    markPreviewRows();
    if (focused) $('abs-calendar-grid').querySelector(`[data-date="${focused}"]`)?.focus({preventScroll: true});
  }

  function longPeriod(record) {
    const start = calendarDate(record.start), end = calendarDate(record.end);
    if (!start || !end) return absenceManagementPeriod(record.start, record.end, false);
    const showYear = start.getUTCFullYear() !== end.getUTCFullYear();
    const part = date => `${showYear ? String(date.getUTCFullYear()).slice(-2) + '.' : ''}${String(date.getUTCMonth() + 1).padStart(2, '0')}${showYear ? '.' : '/'}${String(date.getUTCDate()).padStart(2, '0')}(${dayNames[date.getUTCDay()]})`;
    return `${part(start)}~${part(end)}`;
  }
  function renderLongList() {
    const section = $('abs-long-section');
    if (!section) return;
    const query = ($('abs-search')?.value || '').trim().toLocaleLowerCase();
    if (query !== longList.query || absenceListFilter !== longList.filter) longList.page = 0;
    longList.query = query; longList.filter = absenceListFilter;
    const rows = absenceListFilter === 'active' ? absences.filter(record => record.end >= todayStr && isLongAbsence(record) &&
      (!query || `${record.name} ${record.type}`.toLocaleLowerCase().includes(query))).sort(compareUpcomingAbsences) : [];
    section.hidden = !rows.length;
    $('abs-calendar-panel')?.classList.toggle('abs-has-long', !!rows.length);
    const size = window.innerHeight <= 850 ? 1 : 2;
    const pages = Math.max(1, Math.ceil(rows.length / size));
    longList.page = Math.min(Math.max(0, longList.page), pages - 1);
    $('abs-long-count').textContent = `${rows.length}건`;
    $('abs-long-page').textContent = `${longList.page + 1} / ${pages}`;
    $('abs-long-prev').disabled = longList.page === 0;
    $('abs-long-next').disabled = longList.page + 1 >= pages;
    $('abs-long-list').innerHTML = rows.slice(longList.page * size, (longList.page + 1) * size).map(record => {
      const id = Number(record.id);
      const fullPeriod = absenceManagementPeriod(record.start, record.end, false);
      const label = esc(`${record.name} ${record.type} ${fullPeriod} 달력에서 보기`);
      return `<article class="abs-long-item${Number(editingAbsenceId) === id && editingAbsenceId !== null ? ' abs-long-editing' : ''}" data-absence-id="${id}">` +
        `<button type="button" class="abs-long-preview" data-absence-preview="${id}" data-preview-field="long" aria-label="${label}" aria-pressed="false" onclick="OnAirAbsenceManagement.preview(${id})">` +
        `<span class="abs-long-line"><strong class="abs-long-person">${esc(record.name)}</strong><span class="abs-long-period" title="${esc(fullPeriod)}">${esc(longPeriod(record))}</span></span></button>` +
        `<div class="abs-long-actions"><span class="abs-long-meta"><span class="abs-kind abs-kind-${absenceKind(record)}">${esc(record.type)}</span>${absenceUpcomingBadge(record)}</span><span><button type="button" data-abs-long-action="edit" data-abs-long-id="${id}" aria-label="${esc(record.name)} ${esc(record.type)} 일정 수정" onclick="beginAbsenceEdit(${id})">수정</button>` +
        `<button type="button" class="abs-delete" data-abs-long-action="delete" data-abs-long-id="${id}" aria-label="${esc(record.name)} ${esc(record.type)} 일정 삭제" onclick="delAbsence(${id})">삭제</button></span></div></article>`;
    }).join('');
  }

  function changeLongPage(delta) {
    longList.page += delta;
    renderLongList(); renderCalendarPreview();
  }
  function preview(id) {
    if (SYSTEM_MODE !== 'ADMIN') return false;
    const record = absences.find(row => Number(row.id) === Number(id));
    const date = record && calendarDate(record.start);
    if (!record || !date) return false;
    calendar.selectedId = Number(record.id);
    calendar.draft = false; calendar.anchor = '';
    calendar.year = date.getUTCFullYear(); calendar.month = date.getUTCMonth() + 1;
    renderCalendarPreview();
    return true;
  }
  function moveCalendarMonth(delta) {
    if (drag || !Number.isInteger(delta) || !calendar.year) return;
    const date = new Date(Date.UTC(calendar.year, calendar.month - 1 + delta, 1));
    if (date.getUTCFullYear() < 100 || date.getUTCFullYear() > 9999) return;
    calendar.year = date.getUTCFullYear(); calendar.month = date.getUTCMonth() + 1;
    renderCalendarPreview();
  }
  function jumpCalendar(edge) {
    const record = displayedRange();
    const date = record && calendarDate(edge === 'end' ? record.end : record.start);
    if (!date) return;
    calendar.year = date.getUTCFullYear(); calendar.month = date.getUTCMonth() + 1;
    renderCalendarPreview();
  }
  function resetCalendarPreview() {
    calendar.selectedId = null; calendar.draft = false; calendar.anchor = '';
    calendar.year = Number(curYear); calendar.month = Number(curMonth);
    if (!Number.isInteger(calendar.year) || !Number.isInteger(calendar.month) || calendar.year < 100 || calendar.year > 9999 || calendar.month < 1 || calendar.month > 12) {
      const date = calendarDate(todayStr) || new Date();
      calendar.year = date.getUTCFullYear(); calendar.month = date.getUTCMonth() + 1;
    }
    renderCalendarPreview();
  }

  function refreshSummary() {
    const educationButton = $('abs-education-button');
    if (educationButton) educationButton.hidden = !/교육/i.test($('abs-type')?.value || '');
    if ($('abs-education-summary')) {
      const fields = educationInputs(), filled = fields.filter(input => input.value.trim()).length;
      $('abs-education-summary').textContent = $('ur-admin-edu-title')?.value.trim() ||
        (filled ? `${filled}개 항목 입력됨 · 눌러서 확인` : '교육명·장소·내용 등을 입력할 수 있습니다.');
    }
    const summary = $('abs-bulk-summary');
    if (summary) {
      const names = checkedNames(), bulk = !!$('ur-bulk-toggle')?.checked && editingAbsenceId === null;
      summary.hidden = !bulk;
      summary.textContent = bulk ? `함께 등록 ${names.length}명: ${names.join(', ') || '대상을 선택하세요.'}` : '';
    }
    if ($('abs-bulk-button')) $('abs-bulk-button').textContent = $('ur-bulk-toggle')?.checked ? '대상 변경' : '여러 명 등록';
  }

  function connectForm() {
    const control = $('ur-bulk-control');
    if (control && !$('abs-bulk-button')) {
      const button = document.createElement('button');
      button.id = 'abs-bulk-button'; button.type = 'button';
      button.textContent = '여러 명 등록'; button.addEventListener('click', openBulk);
      control.appendChild(button);
    }
    refreshSummary();
  }

  function openEducation() {
    if (!canEdit() || !/교육/i.test($('abs-type')?.value || '')) return;
    const fields = $('ur-admin-edu-group');
    if (!fields) return;
    const saved = educationInputs().map(input => input.value);
    let applied = false;
    const modal = window.OnAirUI.open('abs-education-modal', '교육 상세 정보',
      '<div id="abs-education-fields"></div><p class="oa-sub">입력 내용은 오른쪽 편집창의 [변경사항 저장] 또는 [새 부재 등록]을 눌러야 저장됩니다.</p>' +
      '<div class="oa-footer"><button type="button" id="abs-education-cancel">취소</button><button type="button" id="abs-education-apply" class="btn-main">입력 내용 반영</button></div>', {
        width: 690, onClose: () => {
          if (!applied) educationInputs().forEach((input, index) => { input.value = saved[index]; });
          $('abs-education-storage').appendChild(fields);
          updateAbsenceEditPreview(); refreshSummary();
        }
      });
    $('abs-education-fields').appendChild(fields); fields.hidden = false;
    $('abs-education-cancel').onclick = () => window.OnAirUI.close(modal.id);
    $('abs-education-apply').onclick = () => { applied = true; window.OnAirUI.close(modal.id); };
    $('ur-admin-edu-title')?.focus();
  }

  function openBulk() {
    if (!canEdit() || editingAbsenceId !== null) return;
    const fields = $('ur-bulk-names'), toggle = $('ur-bulk-toggle');
    if (!fields || !toggle) return;
    const wasBulk = toggle.checked, selected = new Set(checkedNames());
    let applied = false;
    const modal = window.OnAirUI.open('abs-bulk-modal', '여러 명 함께 등록',
      '<p class="oa-sub">같은 기간과 구분으로 등록할 사원을 선택하세요. 겹치는 일정이 있는 사원은 저장 전에 알려 드립니다.</p><div id="abs-bulk-fields"></div><p id="abs-bulk-error" class="oa-error" role="status"></p>' +
      '<div class="oa-footer"><button type="button" id="abs-bulk-single">한 명 등록으로 전환</button><button type="button" id="abs-bulk-cancel">취소</button><button type="button" id="abs-bulk-apply" class="btn-main">선택한 사원 적용</button></div>', {
        width: 650, onClose: () => {
          if (!applied) {
            toggle.checked = wasBulk;
            fields.querySelectorAll('[data-ur-bulk-name]').forEach(input => { input.checked = selected.has(input.value); });
          }
          $('ur-bulk-control').appendChild(fields);
          toggle.dispatchEvent(new Event('change', {bubbles: true}));
          refreshSummary();
        }
      });
    $('abs-bulk-fields').appendChild(fields); fields.hidden = false;
    $('abs-bulk-single').onclick = () => {
      const first = checkedNames()[0];
      if (first) $('abs-name').value = first;
      fields.querySelectorAll('[data-ur-bulk-name]').forEach(input => { input.checked = false; });
      toggle.checked = false; applied = true; window.OnAirUI.close(modal.id);
    };
    $('abs-bulk-cancel').onclick = () => window.OnAirUI.close(modal.id);
    $('abs-bulk-apply').onclick = () => {
      if (!checkedNames().length) { $('abs-bulk-error').textContent = '등록할 사원을 한 명 이상 선택하세요.'; return; }
      toggle.checked = true; applied = true; window.OnAirUI.close(modal.id);
    };
    fields.querySelector('input')?.focus();
  }

  function openTypes() {
    if (!canEdit()) return;
    const controls = $('abs-types-controls');
    renderAbsenceTypeUI();
    const modal = window.OnAirUI.open('abs-types-modal', '휴가·부재 구분 관리',
      '<p class="oa-sub">구분을 추가하거나 사용하지 않는 구분을 삭제합니다. 기존 일정의 구분은 바뀌지 않습니다.</p><div id="abs-types-fields"></div>' +
      '<div class="oa-footer"><button type="button" id="abs-types-close">닫기</button></div>', {
        width: 620, onClose: () => { $('abs-types-storage').appendChild(controls); updateAbsenceEditPreview(); }
      });
    $('abs-types-fields').appendChild(controls);
    $('abs-types-close').onclick = () => window.OnAirUI.close(modal.id);
    $('new-abs-type-input').focus();
  }

  const originalPreview = updateAbsenceEditPreview;
  updateAbsenceEditPreview = function() {
    const result = originalPreview.apply(this, arguments); refreshSummary();
    if (calendar.draft) renderCalendarPreview();
    return result;
  };
  const originalDateLabels = updateAbsenceDateLabels;
  updateAbsenceDateLabels = function() {
    if (!pickingDate) calendar.anchor = '';
    return originalDateLabels.apply(this, arguments);
  };
  const originalBegin = beginAbsenceEdit;
  beginAbsenceEdit = function(id) {
    const before = editingAbsenceOriginal;
    const result = originalBegin.apply(this, arguments);
    refreshSummary();
    if (editingAbsenceOriginal !== before && Number(editingAbsenceId) === Number(id)) preview(id);
    return result;
  };
  const originalList = renderAbsenceList;
  renderAbsenceList = function() {
    const active = document.activeElement;
    const focusId = active?.dataset?.absencePreview, focusField = active?.dataset?.previewField;
    const actionId = active?.dataset?.absLongId, action = active?.dataset?.absLongAction;
    const result = originalList.apply(this, arguments);
    renderLongList();
    renderCalendarPreview();
    if (focusId !== undefined && !active.isConnected) {
      const replacement = [...document.querySelectorAll(previewSelector)]
        .find(button => button.dataset.absencePreview === focusId && button.dataset.previewField === focusField);
      (replacement || $('abs-search'))?.focus({preventScroll: true});
    } else if (actionId !== undefined && !active.isConnected) {
      const replacement = [...document.querySelectorAll('#abs-long-list [data-abs-long-action]')]
        .find(button => button.dataset.absLongId === actionId && button.dataset.absLongAction === action);
      (replacement || $('abs-search'))?.focus({preventScroll: true});
    }
    return result;
  };
  const originalFetch = fetchAllData;
  fetchAllData = async function() {
    const result = await originalFetch.apply(this, arguments);
    if (result && $('absence-modal')?.classList.contains('active')) renderAbsenceList();
    return result;
  };
  const originalOpen = openAbsenceModal;
  openAbsenceModal = function() {
    longList.page = 0;
    const result = originalOpen.apply(this, arguments);
    if (SYSTEM_MODE === 'ADMIN' && $('absence-modal')?.classList.contains('active')) {
      window.OnAirUI.raise('absence-modal'); connectForm(); resetCalendarPreview();
      $('abs-search')?.focus({preventScroll: true});
    }
    return result;
  };
  window.addEventListener('resize', () => {
    if ($('absence-modal')?.classList.contains('active')) renderAbsenceList();
  });
  $('absence-modal')?.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const modal = $('absence-modal');
    const top = typeof MODAL_CLOSERS !== 'undefined' ? [...MODAL_CLOSERS].reverse().find(([id]) => isModalOpen(id)) : null;
    if (top && top[0] !== modal.id) return;
    const fields = [...modal.querySelectorAll('button,input,select,textarea,a[href],summary,[tabindex]')]
      .filter(field => !field.disabled && field.tabIndex >= 0 && field.getClientRects().length);
    if (!fields.length) return;
    if (event.shiftKey && (document.activeElement === fields[0] || !modal.contains(document.activeElement))) {
      event.preventDefault(); fields.at(-1).focus();
    } else if (!event.shiftKey && (document.activeElement === fields.at(-1) || !modal.contains(document.activeElement))) {
      event.preventDefault(); fields[0].focus();
    }
  });
  $('abs-calendar-prev')?.addEventListener('click', () => moveCalendarMonth(-1));
  $('abs-calendar-next')?.addEventListener('click', () => moveCalendarMonth(1));
  $('abs-calendar-start')?.addEventListener('click', () => jumpCalendar('start'));
  $('abs-calendar-end')?.addEventListener('click', () => jumpCalendar('end'));
  const grid = $('abs-calendar-grid');
  function releaseDrag() {
    const pointerId = drag?.pointerId; drag = null;
    if (pointerId !== undefined && grid.hasPointerCapture?.(pointerId)) grid.releasePointerCapture(pointerId);
  }
  function dayAt(event) {
    const day = document.elementFromPoint(event.clientX, event.clientY)?.closest('[data-date]');
    return day && grid.contains(day) && !day.disabled ? day : null;
  }
  grid?.addEventListener('pointerdown', event => {
    if (!canPickDate() || event.button !== 0 || event.isPrimary === false || drag) return;
    const day = event.target.closest('[data-date]');
    if (!day || day.disabled || !calendarDate(day.dataset.date)) return;
    event.preventDefault();
    drag = {pointerId: event.pointerId, start: day.dataset.date, end: day.dataset.date, moved: false};
    grid.setPointerCapture?.(event.pointerId); renderCalendarPreview();
  });
  grid?.addEventListener('pointermove', event => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const day = dayAt(event);
    if (day && day.dataset.date !== drag.end) {
      drag.end = day.dataset.date; drag.moved = drag.moved || drag.end !== drag.start; renderCalendarPreview();
    }
  });
  grid?.addEventListener('pointerup', event => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    const day = dayAt(event), selected = drag;
    if (day) selected.end = day.dataset.date;
    releaseDrag();
    if (selected.moved || selected.start !== selected.end) commitDates(selected.start, selected.end);
    else if (day) pickCalendarDate(selected.start);
    renderCalendarPreview();
    if (day) grid.querySelector(`[data-date="${selected.end}"]`)?.focus({preventScroll: true});
  });
  for (const type of ['pointercancel', 'lostpointercapture']) grid?.addEventListener(type, event => {
    if (drag?.pointerId === event.pointerId) { releaseDrag(); renderCalendarPreview(); }
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && drag) { releaseDrag(); renderCalendarPreview(); event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  $('abs-calendar-grid')?.addEventListener('click', event => {
    if (event.detail > 0) return;
    const day = event.target.closest('[data-date]');
    if (day) pickCalendarDate(day.dataset.date);
  });
  $('abs-calendar-grid')?.addEventListener('keydown', event => {
    const key = event.target.dataset.date, date = calendarDate(key);
    const offset = {ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7}[event.key];
    if (!date || offset === undefined) return;
    event.preventDefault(); date.setUTCDate(date.getUTCDate() + offset);
    if (!calendarDate(dateKey(date))) return;
    calendar.year = date.getUTCFullYear(); calendar.month = date.getUTCMonth() + 1;
    renderCalendarPreview();
    const next = $('abs-calendar-grid').querySelector(`[data-date="${dateKey(date)}"]`);
    if (next) {
      $('abs-calendar-grid').querySelectorAll('[data-date]').forEach(day => { day.tabIndex = day === next ? 0 : -1; });
      next.focus({preventScroll: true});
    }
  });
  $('abs-long-prev')?.addEventListener('click', () => changeLongPage(-1));
  $('abs-long-next')?.addEventListener('click', () => changeLongPage(1));
  window.OnAirAbsenceManagement = {connectForm, refreshSummary, openEducation, openBulk, openTypes,
    preview, moveCalendarMonth, jumpCalendar, renderCalendarPreview, renderLongList, beginDraft, pickCalendarDate,
    get calendarState() { return {...calendar}; }, _test: {calendarCells, calendarDate}};
})();
