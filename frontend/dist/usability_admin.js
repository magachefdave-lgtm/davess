/* History search, recorded comparisons and local notice copying. */
(() => {
  'use strict';
  let period = 'MONTH', searchInput = null;
  const ui = () => window.OnAirUI;
  const escape = value => ui().escape(value);
  const normalize = value => String(value == null ? '' : value).normalize('NFKC').toLocaleLowerCase();

  function snapshotWords(value) {
    if (value == null) return [];
    if (Array.isArray(value)) return value.flatMap(snapshotWords);
    if (typeof value === 'object') return Object.entries(value).flatMap(([key, item]) =>
      [...(/^\d{4}-\d{2}-\d{2}$/.test(key) ? [key] : []), ...snapshotWords(item)]);
    return [String(value)];
  }

  function filterHistory(records, selectedPeriod = 'MONTH', query = '', year = curYear, month = curMonth) {
    const prefix = selectedPeriod === 'MONTH' ? `${year}-${String(month).padStart(2, '0')}` : String(year);
    const terms = normalize(query).trim().split(/\s+/).filter(Boolean);
    return (Array.isArray(records) ? records : []).filter(record => {
      if (!record || typeof record !== 'object') return false;
      if (selectedPeriod !== 'ALL' && !String(record.timestamp || '').startsWith(prefix) &&
          !String(record.dateKey || '').startsWith(prefix)) return false;
      const text = normalize([record.timestamp, record.type, record.dateKey, record.details, record.host, record.actor,
        ...snapshotWords(record.before), ...snapshotWords(record.after)].filter(value => value != null).join(' '));
      return terms.every(term => text.includes(term));
    });
  }

  function hasSnapshots(record) {
    return !!(record && record.before && record.after && typeof record.before === 'object' &&
      typeof record.after === 'object' && !Array.isArray(record.before) && !Array.isArray(record.after));
  }

  function workersText(workers) {
    return (Array.isArray(workers) ? workers : []).map(worker => {
      if (typeof worker === 'string') return worker;
      if (!worker || typeof worker !== 'object') return '';
      const labels=[worker.name,worker.role || '',worker.team ? `(${worker.team})` : ''];
      if(typeof worker.isSubstitute==='boolean')labels.push(worker.isSubstitute?'대체 근무':'일반 근무');
      if(typeof worker.zeroScore==='boolean')labels.push(worker.zeroScore?'0점':'점수 반영');
      return labels.filter(Boolean).join(' · ');
    }).filter(Boolean).join(', ') || '없음';
  }

  function absenceText(records) {
    return (Array.isArray(records) ? records : []).map(record => {
      if (!record || typeof record !== 'object') return '';
      const dates = [record.start, record.end && record.end !== record.start ? record.end : ''].filter(Boolean).join(' ~ ');
      const education = record.education && typeof record.education === 'object' ?
        Object.values(record.education).filter(value => typeof value === 'string' && value.trim()).join(' · ') : '';
      return [record.name, record.type, dates, education].filter(Boolean).join(' · ');
    }).filter(Boolean).join('\n') || '없음';
  }

  function comparisonRows(record) {
    if (!hasSnapshots(record)) return [];
    const before = record.before, after = record.after;
    const rows = [...new Set([...Object.keys(before.schedules || {}), ...Object.keys(after.schedules || {})])].sort()
      .map(date => ({label: `${ui().dateLabel(date, true)} 당직`, before: workersText((before.schedules || {})[date]),
        after: workersText((after.schedules || {})[date])}));
    if ((Array.isArray(before.absences) && before.absences.length) || (Array.isArray(after.absences) && after.absences.length)) {
      rows.push({label: '휴가 · 부재', before: absenceText(before.absences), after: absenceText(after.absences)});
    }
    return rows;
  }

  function renderComparison(record) {
    if (!hasSnapshots(record)) return '<p style="color:var(--text-muted);">전후 기록이 없는 과거 이력입니다. 아래 기존 상세 내용을 확인해 주세요.</p>';
    const rows = comparisonRows(record);
    if (!rows.length) return '<p style="color:var(--text-muted);">서버에 기록된 상세 변경 항목이 없습니다.</p>';
    return '<div class="table-scroll"><table class="data-table" style="table-layout:fixed;">' +
      '<thead><tr><th>항목</th><th>변경 전</th><th>변경 후</th></tr></thead><tbody>' + rows.map(row =>
        `<tr><td>${escape(row.label)}</td><td style="white-space:pre-wrap;overflow-wrap:anywhere;">${escape(row.before)}</td>` +
        `<td style="white-space:pre-wrap;overflow-wrap:anywhere;">${escape(row.after)}</td></tr>`).join('') + '</tbody></table></div>';
  }

  function historyNoticeText(record) {
    const lines = ['[OnAir 일정 변경]', `구분: ${record.type || ''}`];
    if (record.dateKey) lines.push(`대상일: ${ui().dateLabel(record.dateKey, true)}`);
    if (record.details) lines.push(`내용: ${record.details}`);
    for (const row of comparisonRows(record)) lines.push(`${row.label}\n변경 전: ${row.before}\n변경 후: ${row.after}`);
    if (record.timestamp) lines.push(`기록 일시: ${record.timestamp}`);
    return lines.join('\n');
  }

  async function copyNotice(record) {
    const text = historyNoticeText(record);
    try {
      if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (_) { /* Local browsers may deny Clipboard API; use their copy command. */ }
    const active = document.activeElement, x = window.scrollX, y = window.scrollY;
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('aria-label', '복사할 변경 공지');
    area.style.cssText = 'position:fixed;left:0;top:0;width:1px;height:1px;opacity:0;';
    document.body.appendChild(area);
    try {
      area.focus({preventScroll: true});
      area.select();
      return !!(document.execCommand && document.execCommand('copy'));
    } catch (_) {
      return false;
    } finally {
      area.remove();
      if (active && typeof active.focus === 'function') active.focus({preventScroll: true});
      window.scrollTo(x, y);
    }
  }

  function openRecord(record) {
    if (!record) return;
    const who = [record.host, record.actor].filter(Boolean).join(' · ');
    const node = ui().open('history-change-modal', '변경 상세',
      `<p>${escape(record.timestamp || '')} · ${escape(record.type || '')}${who ? ' · ' + escape(who) : ''}</p>` +
      (record.dateKey ? `<p style="font-weight:700;">${escape(ui().dateLabel(record.dateKey, true))}</p>` : '') +
      renderComparison(record) + `<p style="white-space:pre-wrap;overflow-wrap:anywhere;">${escape(record.details || '')}</p>` +
      '<label style="display:block;font-weight:700;margin-top:14px;">변경 공지 문구' +
      `<textarea readonly rows="6" style="display:block;width:100%;margin-top:6px;resize:vertical;">${escape(historyNoticeText(record))}</textarea></label>` +
      '<button type="button" class="btn-main" data-copy-change style="margin-top:10px;">공지 문구 복사</button>' +
      '<span data-copy-status role="status" aria-live="polite" style="margin-left:10px;"></span>', {width: 740});
    const button = node.querySelector('[data-copy-change]'), status = node.querySelector('[data-copy-status]');
    button.addEventListener('click', async () => {
      button.disabled = true;
      try { status.textContent = await copyNotice(record) ? '공지 문구를 복사했습니다.' : '복사하지 못했습니다. 위 문구를 선택해 직접 복사해 주세요.'; }
      finally { button.disabled = false; }
    });
  }

  function openChange(index) { openRecord(changeHistory[Number(index)]); }

  function ensureSearch() {
    if (searchInput) return;
    const body = document.getElementById('history-modal').querySelector('.modal-body');
    const wrap = document.createElement('label');
    wrap.id = 'history-search-wrap';
    wrap.style.cssText = 'display:block;margin:12px 0;font-size:12px;font-weight:700;';
    wrap.textContent = '이름 · 날짜 · 변경 종류 검색 (여러 낱말은 띄어 쓰면 모두 맞는 것만 보입니다)';
    searchInput = document.createElement('input');
    searchInput.id = 'history-search';
    searchInput.type = 'search';
    searchInput.placeholder = '';
    searchInput.style.cssText = 'display:block;width:100%;margin-top:6px;';
    searchInput.addEventListener('input', renderHistory);
    wrap.appendChild(searchInput);
    body.insertBefore(wrap, body.querySelector('.table-scroll'));
  }

  function renderHistory() {
    const records = Array.isArray(changeHistory) ? changeHistory : [];
    const query = searchInput ? searchInput.value : '';
    const filtered = filterHistory(records, period, query);
    const tbody = document.getElementById('history-tbody');
    if (!filtered.length) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:16px;color:var(--text-muted);">${query.trim() ? '검색 조건에 맞는 변경 이력이 없습니다.' : '변경 이력이 없습니다.'}</td></tr>`;
      return;
    }
    tbody.innerHTML = filtered.map(record => {
      const who = [record.host, record.actor].filter(Boolean).join(' · ');
      return `<tr data-history-index="${records.indexOf(record)}" tabindex="0" role="button" aria-label="${escape((record.type || '변경') + ' 상세 보기')}" style="cursor:pointer;">` +
        `<td class="hist-time" data-label="일시">${escape(record.timestamp || '')}</td><td class="hist-type" data-label="구분"><b>${escape(record.type || '')}</b></td>` +
        `<td class="hist-key" data-label="대상일"><span>${historyDateHtml(record.dateKey)}</span></td><td class="hist-detail" data-label="상세 내용">${escape(record.details || '')}</td>` +
        `<td class="hist-who" data-label="PC · 계정">${escape(who || '-')}</td></tr>`;
    }).join('');
    tbody.querySelectorAll('[data-history-index]').forEach(row => {
      const record = records[Number(row.dataset.historyIndex)];
      row.addEventListener('click', () => openRecord(record));
      row.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openRecord(record); }
      });
    });
  }

  openHistoryModal = function(selectedPeriod = 'MONTH') {
    period = ['MONTH', 'YEAR', 'ALL'].includes(selectedPeriod) ? selectedPeriod : 'MONTH';
    ensureSearch();
    renderHistory();
    document.getElementById('history-modal').classList.add('active');
  };
  window.OnAirAdmin = {filterHistory, historyNoticeText, renderComparison, copyNotice, openChange};
})();

/* v3.18.0 관리자 작업 흐름 정리.
   - 배정 계산(순번·패밀리데이 규칙)은 바꾸지 않는다. 계산 앞뒤의 확인 창·되돌리기·잠금만 다룬다.
   - 본 스크립트 함수는 이름으로 감싼다(다른 보조 스크립트의 감싸기 위에 한 겹 더). */
(() => {
  'use strict';
  const W = typeof window !== 'undefined' ? window : globalThis;
  const doc = W.document || null;
  const isFn = value => typeof value === 'function';
  const escapeHtml = value => String(value == null ? '' : value).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const $ = id => (doc && isFn(doc.getElementById) ? doc.getElementById(id) : null);
  const pad2 = n => String(n).padStart(2, '0');
  const admin = () => typeof SYSTEM_MODE !== 'undefined' && SYSTEM_MODE === 'ADMIN';
  const clone = value => JSON.parse(JSON.stringify(value == null ? null : value));
  const monthOf = key => String(key || '').slice(0, 7);
  const DAYS = ['일', '월', '화', '수', '목', '금', '토'];
  const KEY_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
  function validKey(key) {
    const m = KEY_RE.exec(String(key || ''));
    if (!m) return false;
    const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
    return d.getUTCFullYear() === +m[1] && d.getUTCMonth() === +m[2] - 1 && d.getUTCDate() === +m[3];
  }
  function weekday(key) { const m = KEY_RE.exec(key); return m ? DAYS[new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])).getUTCDay()] : ''; }
  function shortDate(key) { const m = KEY_RE.exec(String(key || '')); return m ? `${+m[2]}/${+m[3]}` : String(key || ''); }
  function ymLabel(monthKey) { const [y, m] = String(monthKey).split('-').map(Number); return `${y}년 ${m}월`; }
  function closedList() { return typeof closedMonths !== 'undefined' && Array.isArray(closedMonths) ? closedMonths : []; }
  function isClosedMonth(monthKey) { return closedList().includes(monthKey); }
  function isClosedDate(key) { return /^\d{4}-\d{2}/.test(String(key || '')) && isClosedMonth(monthOf(key)); }
  function currentMonthKey() { return `${curYear}-${pad2(curMonth)}`; }
  function closedBannerText(monthKey) { return `${ymLabel(monthKey)}은 마감되었습니다 — 고치려면 [마감 해제]`; }
  function todayKey(now = new Date()) { return `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`; }
  function localStamp(value) {
    const d = value instanceof Date ? value : new Date(value);
    if (!value || Number.isNaN(d.getTime())) return '';
    return `${todayKey(d)} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
  }
  // v3.18.0 실패 문장은 화면 공통 도우미(friendlyFetchError)로 만든다. 영어·숫자 코드가 그대로 보이지 않게.
  function friendly(error, response, action, body) {
    if (isFn(W.friendlyFetchError)) return W.friendlyFetchError(error, response, action, body);
    const detail = body && body.detail;
    if (typeof detail === 'string' && /[가-힣]/.test(detail)) return detail;
    return `${action} 중 오류가 났습니다. 잠시 후 다시 시도하고, 계속되면 관리자에게 알려 주세요.`;
  }
  function apiFailure(response, body) {
    const error = new Error('요청이 처리되지 않았습니다.');
    error.status = response ? response.status : 0;
    error.detail = body && body.detail;
    return error;
  }
  function josaOf(word, pair) {
    if (isFn(W.josa)) return W.josa(word, pair);
    const [a, b] = String(pair).split('/');
    const ch = String(word || '').slice(-1).charCodeAt(0);
    return String(word || '') + (ch >= 0xAC00 && ch <= 0xD7A3 && (ch - 0xAC00) % 28 ? a : b);
  }
  function toast(message, error) { try { if (isFn(W.showToast) || typeof showToast === 'function') showToast(message, error); } catch (_) { /* 알림 실패는 흐름을 막지 않는다 */ } }
  const state = {lastActionToast: null, pendingSnapshotKind: null, snapSeq: 0, lastSnapshot: null, augments: [],
    preferredScope: null, scopeAutoResolve: null, undoSeconds: 10, undoMode: 'bar', lastDialog: null};

  // ── 공통 확인 창 ───────────────────────────────────────────
  // 브라우저 기본 confirm/prompt 대신 쓴다. Esc·✕·바깥 클릭은 '닫기'(null)이고, 적던 내용이 있으면 먼저 묻는다.
  const dialogs = new Map();
  function registerCloser(id, closer) {
    if (typeof MODAL_CLOSERS === 'undefined') return;
    const index = MODAL_CLOSERS.findIndex(entry => entry[0] === id);
    if (index >= 0) MODAL_CLOSERS.splice(index, 1);
    MODAL_CLOSERS.push([id, closer]);
  }
  function unregisterCloser(id) {
    if (typeof MODAL_CLOSERS === 'undefined') return;
    const index = MODAL_CLOSERS.findIndex(entry => entry[0] === id);
    if (index >= 0) MODAL_CLOSERS.splice(index, 1);
  }
  function ask(opts = {}) {
    state.lastDialog = opts;
    if (isFn(W.OnAirAdmin && W.OnAirAdmin.askHook)) return Promise.resolve(W.OnAirAdmin.askHook(opts));
    return new Promise(resolve => {
      const id = opts.id || 'oa-admin-dialog';
      const previous = dialogs.get(id);
      if (previous) previous.finish(null);
      if (!doc || !doc.body || !isFn(doc.createElement)) { resolve(null); return; }
      const buttons = opts.buttons || [{label: '확인', value: true, primary: true}, {label: '취소', value: null}];
      const node = doc.createElement('div');
      node.id = id;
      node.className = 'modal oa-modal oa-admin-dialog active';
      node.setAttribute('role', 'dialog');
      node.setAttribute('aria-modal', 'true');
      node.setAttribute('aria-labelledby', id + '-title');
      node.innerHTML = `<div class="modal-body" style="width:${Number(opts.width) || 520}px">` +
        `<div class="modal-head"><div class="modal-title" id="${escapeHtml(id)}-title">${escapeHtml(opts.title || '')}</div>` +
        '<button type="button" class="oa-close" aria-label="창 닫기">✕</button></div>' +
        `<div class="oa-body">${opts.html || ''}<div class="oa-error" role="alert" data-oa-error hidden></div></div>` +
        `<div class="oa-footer oa-sticky-foot">${buttons.map((b, i) =>
          `<button type="button" data-oa-btn="${i}" class="${b.primary ? 'btn-main' : (b.danger ? 'btn-danger' : '')}">${escapeHtml(b.label)}</button>`).join('')}</div></div>`;
      doc.body.appendChild(node);
      const trigger = doc.activeElement;
      let finished = false;
      const finish = value => {
        if (finished) return;
        finished = true;
        dialogs.delete(id);
        unregisterCloser(id);
        node.classList.remove('active');
        if (node.parentNode) node.parentNode.removeChild(node);
        if (trigger && trigger.isConnected && isFn(trigger.focus)) { try { trigger.focus({preventScroll: true}); } catch (_) {} }
        resolve(value);
      };
      const tryClose = async () => {
        if (isFn(opts.dirty) && opts.dirty(node)) {
          if (!(await confirmDiscard())) return;
        }
        finish(null);
      };
      dialogs.set(id, {finish, tryClose, node});
      registerCloser(id, () => { tryClose(); });
      node.querySelector('.oa-close').addEventListener('click', tryClose);
      const error = node.querySelector('[data-oa-error]');
      node.addEventListener('input', event => {
        if (error && !error.hidden) { error.hidden = true; error.textContent = ''; }
        if (event.target && isFn(event.target.removeAttribute)) event.target.removeAttribute('aria-invalid');
      });
      buttons.forEach((b, i) => node.querySelector(`[data-oa-btn="${i}"]`).addEventListener('click', () => {
        if (b.value === null || b.value === undefined) { tryClose(); return; }
        const problem = isFn(opts.validate) ? opts.validate(b.value, node) : '';
        if (problem) {
          error.textContent = problem;
          error.hidden = false;
          const field = opts.invalidFocus ? node.querySelector(opts.invalidFocus) : null;
          if (field) { field.setAttribute('aria-invalid', 'true'); field.focus(); }
          return;
        }
        finish(isFn(opts.result) ? opts.result(b.value, node) : b.value);
      }));
      node.addEventListener('keydown', event => {
        if (event.key !== 'Tab') return;
        const focusable = [...node.querySelectorAll('button,input,select,textarea,a[href],summary,[tabindex]')]
          .filter(el => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length);
        if (!focusable.length) return;
        const first = focusable[0], last = focusable[focusable.length - 1];
        if (event.shiftKey && doc.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && doc.activeElement === last) { event.preventDefault(); first.focus(); }
      });
      if (isFn(opts.onOpen)) opts.onOpen(node);
      const focus = (opts.focus && node.querySelector(opts.focus)) || node.querySelector('.oa-footer .btn-main') || node.querySelector('.oa-close');
      if (focus) focus.focus({preventScroll: true});
    });
  }
  function confirmDiscard(message) {
    return ask({id: 'oa-admin-discard', title: '저장하지 않은 내용이 있습니다', width: 440,
      html: `<p>${escapeHtml(message || '적어 둔 내용이 아직 저장되지 않았습니다. 닫으면 사라집니다. 닫을까요?')}</p>`,
      buttons: [{label: '계속 작성하기', value: false, primary: true}, {label: '닫기 (저장 안 함)', value: true, danger: true}]})
      .then(value => value === true);
  }
  function notice(title, html) { return ask({title, html, buttons: [{label: '확인', value: true, primary: true}]}); }

  // ── 버튼이 달린 알림(되돌리기·다시 배정 미리보기) ───────────────
  let actionTimer = null;
  function hideActionToast() {
    if (actionTimer) { clearInterval(actionTimer); actionTimer = null; }
    const box = $('oa-action-toast');
    if (box) box.style.display = 'none';
  }
  function actionToast(text, actions = [], seconds = 30) {
    state.lastActionToast = {text, actions, seconds};
    if (!doc || !doc.body || !isFn(doc.createElement)) return null;
    let box = $('oa-action-toast');
    if (!box) {
      box = doc.createElement('div');
      box.id = 'oa-action-toast';
      box.className = 'oa-action-toast';
      box.setAttribute('role', 'status');
      box.setAttribute('aria-live', 'polite');
      doc.body.appendChild(box);
    }
    hideActionToast();
    let left = Math.max(1, Number(seconds) || 30);
    box.innerHTML = `<span class="oa-at-text">${escapeHtml(text)}</span>` + actions.map((a, i) =>
      `<button type="button" data-oa-act="${i}" class="${i === 0 ? 'btn-main' : ''}">${escapeHtml(a.label)}${a.countdown ? ` (<span data-oa-count>${left}</span>초)` : ''}</button>`).join('') +
      '<button type="button" class="oa-at-close" aria-label="알림 닫기">✕</button>';
    box.style.display = 'flex';
    actions.forEach((a, i) => {
      const button = box.querySelector(`[data-oa-act="${i}"]`);
      if (button) button.addEventListener('click', () => { hideActionToast(); a.onClick(); });
    });
    const closeButton = box.querySelector('.oa-at-close');
    if (closeButton) closeButton.addEventListener('click', hideActionToast);
    actionTimer = setInterval(() => {
      left -= 1;
      const count = box.querySelector('[data-oa-count]');
      if (count) count.textContent = String(left);
      if (left <= 0) hideActionToast();
    }, 1000);
    return box;
  }

  // ── 되돌리기용 서버 사본 ────────────────────────────────────
  const SNAPSHOT_MESSAGES = {assign: '자동 배정을 저장했습니다.', approve: '신청을 승인했습니다.',
    import: '백업 파일을 불러왔습니다.', bulk: '여러 명의 일정을 한꺼번에 저장했습니다.', member: '사원을 명단에서 뺐습니다.'};
  function noteSnapshot(name, label, kind) {
    if (!name) return null;
    state.snapSeq += 1;
    state.lastSnapshot = {name, label: label || name, kind: kind || '', seq: state.snapSeq};
    return state.lastSnapshot;
  }
  function offerSnapshotUndo(message, snapshot) {
    if (!snapshot) return;
    actionToast(`${message} 잘못했다면 바로 되돌릴 수 있습니다.`, [{label: '실행 취소(되돌리기)', onClick: () => undoToSnapshot(snapshot.name, snapshot.label)}], 60);
  }
  // 저장 한 번이 여러 명의 휴가를 한꺼번에 넣으면 '일괄 작업'으로 보고 사본을 만든다.
  function autoSnapshotKind(payload) {
    if (!payload || !Array.isArray(payload.absences)) return null;
    const before = typeof committedState !== 'undefined' && committedState && Array.isArray(committedState.absences)
      ? committedState.absences.length : (typeof absences !== 'undefined' ? absences.length : 0);
    return payload.absences.length - before >= 2 ? 'bulk' : null;
  }
  function queueAugment(fn) { state.augments.push(fn); }
  function clearAugments() { state.augments.length = 0; }

  if (typeof fetchWithAdminToken === 'function') {
    const previousFetch = fetchWithAdminToken;
    fetchWithAdminToken = async function(url, options = {}, ...rest) {
      const response = await previousFetch.call(this, url, options, ...rest);
      try {
        if (response && response.ok && isFn(response.clone) && (url === '/api/duty/save-all' || url === '/api/duty/request/action')) {
          const body = await response.clone().json();
          if (body && body.snapshot) {
            let kind = 'approve';
            if (url === '/api/duty/save-all') { try { kind = JSON.parse(options.body).snapshotKind || ''; } catch (_) { kind = ''; } }
            const snap = noteSnapshot(body.snapshot, body.snapshotLabel, kind);
            if (kind === 'approve') setTimeout(() => offerSnapshotUndo(SNAPSHOT_MESSAGES.approve, snap), 0);
          }
        }
      } catch (_) { /* 응답 본문을 못 읽어도 원래 흐름은 그대로 */ }
      return response;
    };
  }

  if (typeof syncFullDataToBackend === 'function') {
    const previousSync = syncFullDataToBackend;
    syncFullDataToBackend = async function(payload) {
      let body = payload || {};
      const kind = state.pendingSnapshotKind || autoSnapshotKind(body);
      state.pendingSnapshotKind = null;
      for (const fn of state.augments.splice(0)) body = fn(body) || body;
      if (kind) body = Object.assign({}, body, {snapshotKind: kind});
      const seq = state.snapSeq;
      const ok = await previousSync.call(this, body);
      if (ok && kind && state.snapSeq !== seq && state.lastSnapshot) offerSnapshotUndo(SNAPSHOT_MESSAGES[kind] || '저장했습니다.', state.lastSnapshot);
      return ok;
    };
  }

  function countRows(d) {
    const label = {members: '사원', schedules: '당직 칸', absences: '휴가·부재', custom_holidays: '공휴일 설정', change_history: '변경 이력', change_requests: '신청', dinner_polls: '회식 취합', dinner_responses: '회식 응답'};
    return Object.keys(label).map(key => {
      const now = d.current && d.current.counts ? d.current.counts[key] : null;
      const then = d.backup && d.backup.counts ? d.backup.counts[key] : null;
      return `<tr><th>${label[key]}</th><td>${now == null ? '-' : now}</td><td${now !== then ? ' class="oa-diff-cell"' : ''}>${then == null ? '-' : then}</td></tr>`;
    }).join('');
  }
  function restorePreviewHtml(label, d) {
    const currentCount = d.current?.holidaySupplementCount ?? d.current?.counts?.holiday_supplements;
    const backupCount = d.backup?.holidaySupplementCount ?? d.backup?.counts?.holiday_supplements;
    const supplements = currentCount != null || backupCount != null ? `<tr><th>공휴일 보완 기록</th><td>${escapeHtml(currentCount ?? '알 수 없음')}</td><td>${escapeHtml(backupCount ?? '없음')}</td></tr>` : '';
    return `<p><b>${escapeHtml(label)}</b> 상태로 되돌립니다. 당직표·사원 명단·휴가·근무 규칙이 그때로 돌아갑니다.</p>` +
      '<div class="table-scroll"><table><thead><tr><th></th><th>지금</th><th>되돌릴 상태</th></tr></thead><tbody>' + countRows(d) + supplements +
      `<tr><th>당직 기간</th><td>${escapeHtml(d.current.scheduleFrom || '-')} ~ ${escapeHtml(d.current.scheduleTo || '-')}</td><td>${escapeHtml(d.backup.scheduleFrom || '-')} ~ ${escapeHtml(d.backup.scheduleTo || '-')}</td></tr>` +
      `<tr><th>마지막 변경</th><td>${escapeHtml(d.current.lastChange || '-')}</td><td>${escapeHtml(d.backup.lastChange || '-')}</td></tr></tbody></table></div>` +
      (restoreNeedsLegacy(d) ? '<p class="hs-notice blockers"><b>보완 이전 상태로 전체 복원</b>: 이 사본에는 공휴일 보완 기록이 없습니다. 공휴일·근무표·순번 예약을 사본 시점으로 함께 되돌리고 현재 보완 기록을 제거합니다. 구형 프로그램의 쓰기 차단은 유지됩니다.</p>' : '') +
      '<p class="oa-sub">그 뒤에 다른 PC에서 바꾼 내용도 함께 되돌아갑니다. 변경 이력과 월 마감 상태는 그대로 둡니다. 지금 상태는 \'복원 직전\' 사본으로 따로 보관하므로 되돌린 것도 다시 되돌릴 수 있습니다.</p>';
  }
  function supplementMetadata() { return isFn(W.getHolidaySupplementState) ? W.getHolidaySupplementState() : {schema: 1, events: [], bases: [], requiredContract: 0}; }
  function supplementContractActive() { const data = supplementMetadata(); return !!(data.requiredContract || data.events?.length || data.bases?.length); }
  function restoreNeedsLegacy(data) { return (supplementContractActive() || data.current?.holidaySupplementSchema === 1 && data.current?.holidaySupplementCount > 0) && data.backup?.holidaySupplementSchema !== 1; }
  async function readJson(response) { try { return await response.json(); } catch (_) { return {}; } }
  async function undoToSnapshot(name, label, title) {
    if (!name) return false;
    let response;
    try {
      response = await fetchWithAdminToken(`/api/system/backup-preview?name=${encodeURIComponent(name)}`, {}, '되돌리려면 관리자 암호를 입력하세요.');
    } catch (error) { toast('⛔ ' + friendly(error, null, '자동 백업 확인'), true); return false; }
    if (response.status === 401) { toast('⛔ 관리자 암호 입력을 취소했습니다.', true); return false; }
    const d = await readJson(response);
    if (!response.ok || !d.ok) { toast('⛔ 이 자동 백업으로는 되돌릴 수 없습니다. ' + friendly(null, response.ok ? {status: 500} : response, '되돌리기', d), true); return false; }
    const go = await ask({title: title || '실행 취소(되돌리기)', width: 620, html: restorePreviewHtml(label || name, d),
      buttons: [{label: restoreNeedsLegacy(d) ? '보완 이전 상태로 전체 복원' : '이 상태로 되돌리기', value: true, danger: true}, {label: '취소', value: null}]});
    if (!go) return false;
    return performRestore(name, Number(d.current.dataVersion), label || name, restoreNeedsLegacy(d));
  }
  async function performRestore(name, baseVersion, label, restoreLegacy = false) {
    let response;
    try {
      response = await fetchWithAdminToken('/api/system/restore', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name, baseVersion, ...(restoreLegacy ? {restoreLegacy: true} : {})})}, '복원하려면 관리자 암호를 입력하세요.');
    } catch (error) { toast('⛔ ' + friendly(error, null, '되돌리기'), true); return false; }
    if (response.status === 401) { toast('⛔ 관리자 암호 입력을 취소했습니다.', true); return false; }
    const body = await readJson(response);
    if (response.status === 409) {
      await notice('되돌리지 않았습니다', '<p>그 사이 다른 PC에서 당직표를 저장했습니다. 최신 내용을 불러왔으니 [더보기 → 자동 백업으로 되돌리기]에서 다시 확인하세요.</p>');
      if (isFn(W.fetchAllData) || typeof fetchAllData === 'function') await fetchAllData();
      return false;
    }
    if (!response.ok) { toast('⛔ ' + friendly(null, response, '되돌리기', body), true); return false; }
    if (typeof discardUndo === 'function') discardUndo('자동 백업으로 되돌림');
    if (typeof fetchAllData === 'function') await fetchAllData();
    const safetyLabel = body.safetyLabel || '복원 직전';
    actionToast(`${body.restoredLabel || label} 백업으로 되돌렸습니다. 되돌리기 직전 상태도 따로 보관했습니다.`,
      body.safetyCopy ? [{label: '되돌린 것 취소', onClick: () => undoToSnapshot(body.safetyCopy, safetyLabel)}] : [], 60);
    return true;
  }

  // 진단 창과 '자동 백업으로 되돌리기' 창이 같은 목록을 쓴다.
  const BACKUP_KIND_TEXT = {daily: '하루 첫 저장 전', premigration: '구조 변경 직전', prerestore: '복원 직전', auto: '작업 직전'};
  function backupRowsHtml(list, action) {
    return '<div class="table-scroll oa-backup-list"><table><thead><tr><th>이름</th><th>종류</th><th>저장 시각</th><th>크기</th><th></th></tr></thead><tbody>' +
      list.map(b => `<tr><td><b>${escapeHtml(b.label || b.name)}</b><div class="oa-sub oa-mono">${escapeHtml(b.name)}</div></td>` +
        `<td>${escapeHtml(BACKUP_KIND_TEXT[b.kind] || b.kind)}</td><td>${escapeHtml(b.modified)}</td><td>${Math.round((b.size || 0) / 1024)}KB</td>` +
        `<td><button type="button" ${action}="${escapeHtml(b.name)}" data-label="${escapeHtml(b.label || b.name)}">미리 보기</button></td></tr>`).join('') + '</tbody></table></div>';
  }
  async function fetchBackupList() {
    const response = await fetchWithAdminToken('/api/system/backups', {}, '백업 목록을 보려면 관리자 암호를 입력하세요.');
    if (response.status === 401) throw new Error('관리자 암호 입력을 취소했습니다.');
    if (!response.ok) throw apiFailure(response, await readJson(response));
    return ((await response.json()).backups || []);
  }
  if (typeof loadDiagBackups === 'function') {
    loadDiagBackups = async function() {
      const wrap = $('diag-backups-wrap'), box = $('diag-backups');
      if (!wrap || !box) return;
      wrap.style.display = '';
      const heading = wrap.querySelector('h4');
      if (heading) heading.textContent = '자동 백업으로 되돌리기';
      const preview = $('diag-backup-preview');
      if (preview) preview.innerHTML = '';
      try {
        const list = await fetchBackupList();
        if (!list.length) { box.innerHTML = '<span class="fair-note">보관된 자동 백업이 없습니다.</span>'; return; }
        box.innerHTML = backupRowsHtml(list, 'data-oa-diag-preview');
        box.querySelectorAll('[data-oa-diag-preview]').forEach(button => button.addEventListener('click', () => previewBackup(button.getAttribute('data-oa-diag-preview'))));
      } catch (error) {
        box.innerHTML = `<span class="fair-note" style="color:var(--holiday-text);">${escapeHtml(friendly(error, null, '자동 백업 목록 불러오기'))}</span>`;
      }
    };
  }
  if (typeof restoreBackup === 'function') {
    restoreBackup = async function(name, baseVersion) {
      const list = await fetchBackupList().catch(() => []);
      const label = (list.find(b => b.name === name) || {}).label || name;
      const go = await ask({title: '자동 백업으로 되돌리기', html: `<p><b>${escapeHtml(label)}</b> 상태로 되돌릴까요?</p><p class="oa-sub">지금의 당직표·명단·휴가·설정이 그 시점으로 바뀝니다. 지금 상태는 '복원 직전' 사본으로 따로 보관합니다.</p>`,
        buttons: [{label: '되돌리기', value: true, danger: true}, {label: '취소', value: null}]});
      if (!go) return false;
      const ok = await performRestore(name, Number(baseVersion), label);
      if (ok && $('diag-modal') && $('diag-modal').classList.contains('active') && typeof openDiagModal === 'function') await openDiagModal();
      return ok;
    };
  }
  async function openAutoBackupDialog() {
    let list;
    try { list = await fetchBackupList(); } catch (error) { toast('⛔ ' + friendly(error, null, '자동 백업 목록 불러오기'), true); return; }
    await ask({title: '자동 백업으로 되돌리기', width: 760,
      html: '<p class="oa-sub">서버가 자동으로 남긴 사본입니다. <b>작업 직전</b> 사본(자동 배정·승인·불러오기 직전)은 최근 20개, 하루 첫 저장 전 백업은 30일치를 보관합니다. 서버의 back-up 폴더나 기존 PC에 저장한 백업 파일을 불러오려면 [백업 파일 불러오기(JSON)]를 쓰세요.</p>' +
        (list.length ? backupRowsHtml(list, 'data-oa-auto-preview') : '<p>보관된 자동 백업이 없습니다.</p>'),
      onOpen: node => node.querySelectorAll('[data-oa-auto-preview]').forEach(button => button.addEventListener('click', () => {
        const entry = dialogs.get('oa-admin-dialog');
        if (entry) entry.finish(null);
        // 3.18.0 QA: 목록의 [미리 보기]는 '실행 취소'가 아니라 그 백업을 미리 보는 창이다.
        const label = button.getAttribute('data-label');
        undoToSnapshot(button.getAttribute('data-oa-auto-preview'), label, `자동 백업 미리 보기 — ${label}`);
      })),
      buttons: [{label: '닫기', value: null}]});
  }

  // ── 마감한 달 잠금 ───────────────────────────────────────────
  function closedNotice(key) {
    const monthKey = monthOf(key);
    actionToast(`${ymLabel(monthKey)}은 마감되어 고칠 수 없습니다.`, admin() ? [{label: '마감 해제', onClick: () => setMonthClosed(monthKey, false)}] : [], 15);
    return false;
  }
  function guardClosed(name, keysOf) {
    const previous = W[name];
    if (!isFn(previous)) return;
    W[name] = function(...args) {
      if (admin()) {
        const keys = keysOf(...args).filter(Boolean);
        const locked = keys.find(isClosedDate);
        if (locked) return closedNotice(locked);
      }
      return previous.apply(this, args);
    };
  }
  const activeDate = () => (typeof activeModalDate !== 'undefined' ? activeModalDate : '');
  const fieldValue = id => { const el = $(id); return el ? el.value : ''; };

  async function setMonthClosed(monthKey, wantClosed) {
    if (!admin()) return false;
    monthKey = monthKey || currentMonthKey();
    const closed = isClosedMonth(monthKey);
    if (wantClosed === closed) return true;
    const label = ymLabel(monthKey);
    let reason;
    if (closed) {
      reason = await ask({title: `${label} 마감 해제`, width: 500, focus: '#oa-reopen-reason', invalidFocus: '#oa-reopen-reason',
        html: '<p>마감을 해제하면 이 달 당직표를 다시 고칠 수 있습니다. 해제 사유는 변경 이력에 남습니다.</p>' +
          '<label class="oa-field"><span>해제 사유 <span class="oa-req">(꼭 적어 주세요)</span></span><textarea id="oa-reopen-reason" maxlength="200" placeholder=""></textarea><span class="oa-sub">무엇을 고치려고 해제하는지 적습니다. 심사 때 근거가 됩니다.</span></label>',
        dirty: node => !!node.querySelector('#oa-reopen-reason').value.trim(),
        validate: (value, node) => value && !node.querySelector('#oa-reopen-reason').value.trim() ? '해제 사유를 적어 주세요.' : '',
        result: (value, node) => node.querySelector('#oa-reopen-reason').value.trim(),
        buttons: [{label: '마감 해제', value: true, primary: true}, {label: '취소', value: null}]});
    } else {
      reason = await ask({title: `${label} 마감하기`, width: 500, focus: '#oa-close-memo',
        html: '<p>마감하면 이 달의 당직·당직 없는 날·메모를 바꿀 수 없고, 이 달에 걸린 변경 신청도 받거나 승인할 수 없습니다. 나중에 해제할 수 있으며, 해제할 때는 사유가 필요합니다.</p>' +
          '<label class="oa-field"><span>마감 메모 <span class="oa-sub">(선택)</span></span><textarea id="oa-close-memo" maxlength="200" placeholder=""></textarea><span class="oa-sub">비워도 됩니다.</span></label>',
        dirty: node => !!node.querySelector('#oa-close-memo').value.trim(),
        result: (value, node) => node.querySelector('#oa-close-memo').value.trim(),
        buttons: [{label: '이 달 마감하기', value: true, primary: true}, {label: '취소', value: null}]});
    }
    if (reason === null || reason === undefined || reason === false) return false;
    try {
      const response = await fetchWithAdminToken('/api/duty/month-close', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({month: monthKey, closed: !closed, reason, baseVersion: dataVersion})}, '월 마감을 바꾸려면 관리자 암호를 입력하세요.');
      if (response.status === 401) { toast('⛔ 관리자 암호 입력을 취소했습니다.', true); return false; }
      if (!response.ok) {
        const body = await readJson(response);
        toast('⛔ ' + friendly(null, response, '월 마감 처리', body), true);
        await fetchAllData();
        return false;
      }
      await fetchAllData();
      toast(closed ? `✅ ${label} 마감을 해제했습니다.` : `✅ ${label}을 마감했습니다.`);
      return true;
    } catch (error) {
      toast('⛔ ' + friendly(error, null, '월 마감 처리'), true);
      return false;
    }
  }
  if (typeof toggleMonthClose === 'function') toggleMonthClose = () => setMonthClosed(currentMonthKey());

  // ── 날짜 창: 저장 안 한 변경, 패밀리데이 3갈래, 마감 잠금 ──────────
  function pendingDateEdits() {
    const out = {any: false, comment: false, workers: false, parts: []};
    if (typeof dateModalBaseline === 'undefined' || dateModalBaseline === null || typeof dateModalState !== 'function') return out;
    let base, now;
    try { base = JSON.parse(dateModalBaseline); now = JSON.parse(dateModalState()); } catch (_) { return out; }
    out.comment = base.comment !== now.comment;
    out.workers = JSON.stringify([base.noDuty, base.reason, base.workers]) !== JSON.stringify([now.noDuty, now.reason, now.workers]);
    if (out.workers) out.parts.push('근무자·당직 없는 날 지정');
    if (out.comment) out.parts.push('메모');
    out.any = out.comment || out.workers;
    return out;
  }
  function isDateModalOpen() { const m = $('date-modal'); return !!(m && m.classList && m.classList.contains('active')); }
  function restorePicks(picks, zero) {
    const set = (id, value) => { const el = $(id); if (el && value) el.value = value; };
    set('swap-my-worker', picks['swap-my-worker']);
    set('swap-target-year', picks['swap-target-year']);
    set('swap-target-month', picks['swap-target-month']);
    if (typeof updateSwapDateDropdown === 'function') updateSwapDateDropdown();
    set('swap-target-date', picks['swap-target-date']);
    if (typeof updateSwapWorkerDropdown === 'function') updateSwapWorkerDropdown();
    set('swap-target-worker', picks['swap-target-worker']);
    set('sub-origin-worker', picks['sub-origin-worker']);
    if (typeof updateSubstituteCandidateDropdown === 'function') updateSubstituteCandidateDropdown();
    set('sub-target-worker', picks['sub-target-worker']);
    if ($('sub-zero-check')) $('sub-zero-check').checked = zero;
  }
  function wrapDateAction(name, historyType) {
    const previous = W[name];
    if (!isFn(previous)) return;
    W[name] = async function(...args) {
      if (!admin()) return previous.apply(this, args);
      const k = activeDate();
      const other = name === 'handleSwapAction' ? fieldValue('swap-target-date') : '';
      const locked = [k, other].find(isClosedDate);
      if (locked) return closedNotice(locked);
      const pending = pendingDateEdits();
      if (!pending.any) return previous.apply(this, args);
      const actionLabel = name === 'handleSwapAction' ? '맞교환' : '대체 근무';
      const choice = await ask({title: '저장하지 않은 변경이 있습니다', width: 520,
        html: `<p>이 날짜 창에서 바꾼 <b>${escapeHtml(pending.parts.join('·'))}</b>${josaOf(pending.parts.join('·'), '이/가').slice(pending.parts.join('·').length)} 아직 저장되지 않았습니다.</p><p>${escapeHtml(josaOf(actionLabel, '과/와'))} 함께 저장할까요?</p>`,
        buttons: [{label: `함께 저장하고 ${actionLabel}`, value: 'together', primary: true},
          {label: `바꾼 내용은 버리고 ${actionLabel}`, value: 'discard'}, {label: '취소', value: null}]});
      if (!choice) return false;
      const sourceField = name === 'handleSwapAction' ? 'swap-my-worker' : 'sub-origin-worker';
      if (choice === 'discard') {
        // 버린 근무자 지정 속 사람을 골라 둔 상태면 저장된 당직표에는 그 사람이 없다. 창을 저장된 상태로 되돌리고 다시 고르게 한다.
        const chosen = fieldValue(sourceField);
        if (pending.workers && chosen && !(scheduleMap[k] || []).some(w => w.name === chosen)) {
          if (isFn(W.openDateModal)) W.openDateModal(k);
          toast(`⚠️ 바꾼 근무자 지정을 버렸습니다. ${chosen} 님은 이 날 저장된 당직자가 아니므로 ${actionLabel}할 근무자를 다시 고르세요.`, true);
          return false;
        }
        return previous.apply(this, args);
      }
      if (pending.workers) {
        // 근무자 지정을 먼저 저장하고(이력 1건), 같은 창의 선택값으로 이어서 실행한다.
        // 저장하면 날짜 창이 닫히므로, 고른 값을 기억해 두었다가 저장된 당직표 기준으로 다시 맞춘 뒤 실행한다.
        const picks = {};
        ['swap-my-worker', 'swap-target-year', 'swap-target-month', 'swap-target-date', 'swap-target-worker',
          'sub-origin-worker', 'sub-target-worker'].forEach(id => { picks[id] = fieldValue(id); });
        const zero = $('sub-zero-check') ? !!$('sub-zero-check').checked : false;
        const before = dataVersion;
        await saveDateModalChanges();
        if (dataVersion === before) return false;
        const chosen = picks[sourceField];
        if (!chosen || !(scheduleMap[k] || []).some(w => w.name === chosen)) {
          if (isFn(W.openDateModal)) W.openDateModal(k);
          toast(`⚠️ 근무자 지정은 저장했지만 ${actionLabel}하지 않았습니다. ${actionLabel}할 근무자를 다시 고르세요.`, true);
          return false;
        }
        const versionAfterSave = dataVersion;
        const result = await previous.apply(this, args);
        if (dataVersion === versionAfterSave && isFn(W.openDateModal) && !isDateModalOpen()) {
          // 맞교환·대체 근무가 취소되었거나 실패했다: 저장된 상태로 창을 다시 열고 고른 값을 되살린다.
          W.openDateModal(k);
          restorePicks(picks, zero);
        }
        return result;
      }
      const text = String(fieldValue('modal-date-comment') || '').trim();
      queueAugment(payload => {
        if (!payload.historyLog || payload.historyLog.type !== historyType) return payload;
        if (text) commentMap[k] = text; else delete commentMap[k];
        return Object.assign({}, payload, {comments: commentMap,
          historyLog: Object.assign({}, payload.historyLog, {details: payload.historyLog.details + ` / ${shortDate(k)} 메모도 함께 저장`})});
      });
      try { return await previous.apply(this, args); } finally { clearAugments(); }
    };
  }
  wrapDateAction('handleSwapAction', '1:1 맞교환');
  wrapDateAction('handleSubAction', '대체 근무');

  function dayNeedsWorker(k) {
    if (typeof requiredDutyGenders !== 'function') return false;
    const need = requiredDutyGenders(k);
    const list = (scheduleMap[k] || []);
    return need.some(g => !list.some(w => (typeof isRosterMember !== 'function' || isRosterMember(w.name)) &&
      (typeof workerGender === 'function' ? workerGender(w) : genderOf(w.name)) === g && !(typeof isAbsent === 'function' && isAbsent(w.name, k))));
  }
  function familyQuestion(k) {
    const [y, m] = k.split('-').map(Number);
    return ask({title: `${y}년 ${m}월 패밀리데이`, width: 560,
      html: `<p>${escapeHtml(k)}(${weekday(k)})는 패밀리데이였습니다. 이 날을 근무일로 바꾸면 이 달 패밀리데이는 어떻게 할까요?</p>` +
        '<ul class="oa-list"><li><b>이 달은 패밀리데이 없음</b> — 기억해 두고 자동 배정이 다시 만들지 않습니다.</li>' +
        '<li><b>다른 날로 옮길 예정</b> — 옮길 날을 \'당직 없는 날\'로 하고 메모에 \'패밀리데이\'를 적으면 그 날을 이 달 패밀리데이로 인정합니다.</li>' +
        '<li><b>취소</b> — 아무것도 저장하지 않고 이 창으로 돌아갑니다.</li></ul>',
      buttons: [{label: '이 달은 패밀리데이 없음', value: 'none', primary: true}, {label: '다른 날로 옮길 예정', value: 'move'}, {label: '취소(저장 안 함)', value: null}]});
  }
  if (typeof saveDateModalChanges === 'function') {
    const previousSave = saveDateModalChanges;
    saveDateModalChanges = async function(...args) {
      if (!admin()) return previousSave.apply(this, args);
      const k = activeDate();
      if (isClosedDate(k)) return closedNotice(k);
      const wasValue = noDutyMap[k];
      const oldMemo = commentMap[k] || '';
      const wasFamily = !!wasValue && (wasValue === 'RULE21' || (typeof hasFamilyMemo === 'function' && hasFamilyMemo(oldMemo)));
      const asksFamily = !!(dutyRule.rule21FridayOff && wasFamily && !tempIsNoDuty);
      let answer = null;
      if (asksFamily) {
        answer = await familyQuestion(k);
        if (!answer) return false;
      }
      const reopening = !!wasValue && !tempIsNoDuty;
      if (reopening) {
        queueAugment(payload => {
          const log = payload.historyLog;
          if (!log || log.dateKey !== k) return payload;
          const names = (scheduleMap[k] || []).map(w => w.name).join(', ');
          const what = wasFamily ? '패밀리데이 해제' : '당직 없는 날 해제';
          const familyText = answer === 'none' ? ` / ${ymLabel(monthOf(k))}은 패밀리데이 없음으로 기억`
            : (answer === 'move' ? ` / ${ymLabel(monthOf(k))} 패밀리데이는 다른 날로 옮길 예정` : '');
          return Object.assign({}, payload, {historyLog: Object.assign({}, log, {type: what,
            details: `[${k}] ${what} → 근무일로 복구 · ${names ? '당직자: ' + names : '당직자 아직 없음(빈 자리 채우기 필요)'}${familyText}`})});
        });
      }
      const before = dataVersion;
      const realConfirm = W.confirm;
      let pendingSave;
      if (asksFamily) W.confirm = () => answer === 'none';
      try { pendingSave = previousSave.apply(this, args); } finally { if (asksFamily) W.confirm = realConfirm; }
      let result;
      try { result = await pendingSave; } finally { clearAugments(); }
      if (reopening && dataVersion !== before && !noDutyMap[k] && dayNeedsWorker(k) && typeof openVacancyModal === 'function')
        openVacancyModal([k], wasFamily ? '패밀리데이를 해제해 당직자가 없는 날입니다. 지금 채우세요.' : '당직 없는 날을 해제해 당직자가 비었습니다. 지금 채우세요.');
      return result;
    };
  }
  if (typeof closeDateModal === 'function') {
    const previousClose = closeDateModal;
    closeDateModal = function(force) {
      if (!force && admin() && typeof isDateModalDirty === 'function' && isDateModalDirty()) {
        confirmDiscard('이 날짜 창에서 바꾼 내용이 저장되지 않았습니다. 닫으면 사라집니다. 닫을까요?').then(ok => { if (ok) previousClose(true); });
        return false;
      }
      return previousClose(force);
    };
  }
  if (typeof openDateModal === 'function') {
    const previousOpen = openDateModal;
    openDateModal = function(dateKey, ...rest) {
      const result = previousOpen.call(this, dateKey, ...rest);
      try { applyDateLock(dateKey); } catch (_) {}
      return result;
    };
  }
  function applyDateLock(k) {
    const modal = $('date-modal');
    if (!modal || !isFn(modal.querySelector)) return;
    const locked = admin() && isClosedDate(k);
    modal.classList.toggle('oa-locked', locked);
    let banner = modal.querySelector('#oa-date-lock');
    if (!banner && locked) {
      banner = doc.createElement('div');
      banner.id = 'oa-date-lock';
      banner.className = 'oa-closed-banner';
      const head = modal.querySelector('.modal-head');
      if (head && head.parentNode) head.parentNode.insertBefore(banner, head.nextSibling);
    }
    if (banner) {
      banner.hidden = !locked;
      if (locked) {
        banner.innerHTML = `<span>${escapeHtml(ymLabel(monthOf(k)))}은 마감되었습니다 — 보기만 할 수 있습니다.</span><button type="button" class="btn-main" data-oa-reopen>마감 해제</button>`;
        banner.querySelector('[data-oa-reopen]').addEventListener('click', async () => { if (await setMonthClosed(monthOf(k), false)) { closeDateModalForce(); openDateModal(k); } });
      }
    }
    const memo = $('modal-date-comment');
    if (memo) memo.readOnly = locked || !admin();
    modal.querySelectorAll('#manual-assign-container button, #modal-noduty-btn, #btn-exec-swap, #btn-exec-sub, button[onclick="saveDateModalChanges()"]').forEach(button => {
      if (locked) { button.dataset.oaLockDisabled = '1'; button.disabled = true; }
      else if (button.dataset.oaLockDisabled) { delete button.dataset.oaLockDisabled; button.disabled = false; }
    });
  }
  function closeDateModalForce() { if (typeof closeDateModal === 'function') closeDateModal(true); }
  guardClosed('selectManualWorker', () => [activeDate()]);
  guardClosed('clearManualSlot', () => [activeDate()]);
  guardClosed('toggleNoDutyStatus', () => [activeDate()]);

  // ── 드래그: 이름이 든 알림, 30초 되돌리기 ──────────────────────
  if (typeof offerUndo === 'function') {
    offerUndo = function() {
      if (!undoSnapshot || !admin()) return;
      undoSnapshot.baseVersion = dataVersion;
      const seconds = state.undoSeconds || 10;
      const bar = $('undo-bar');
      if (bar && state.undoMode !== 'toast') {
        const label = $('undo-label');
        if (label) label.textContent = undoSnapshot.label || '변경';
        bar.style.display = 'flex';
      }
      let left = seconds;
      const count = $('undo-count');
      if (count) count.textContent = left;
      if (undoTimer) clearTimeout(undoTimer);
      const tick = () => {
        left -= 1;
        const c = $('undo-count');
        if (c) c.textContent = left;
        if (left <= 0) { discardUndo(null); return; }
        undoTimer = setTimeout(tick, 1000);
      };
      undoTimer = setTimeout(tick, 1000);
    };
  }
  function dragDescription(sourceDate, workerName, targetDate, partner) {
    return partner ? `${shortDate(sourceDate)} ${workerName} ↔ ${shortDate(targetDate)} ${partner} 교환`
      : `${workerName}: ${shortDate(sourceDate)} → ${shortDate(targetDate)} 이동`;
  }
  // 3.18.0 QA: 끄는 동안에는 화면 아래·위의 알림이 달력 칸을 가로채지 않게 한다(놓을 칸 위에 알림이 있어도 놓인다).
  function setDragging(on) { try { if (doc && doc.body && doc.body.classList) doc.body.classList.toggle('oa-dragging', !!on); } catch (_) { /* 표시만의 문제 */ } }
  if (doc && isFn(doc.addEventListener)) {
    doc.addEventListener('dragend', () => setDragging(false), true);
    doc.addEventListener('drop', () => setDragging(false), true);
  }
  if (typeof handleDragStart === 'function') {
    const previousStart = handleDragStart;
    handleDragStart = function(event, dateKey, ...rest) {
      if (admin() && isClosedDate(dateKey)) { if (event && isFn(event.preventDefault)) event.preventDefault(); return closedNotice(dateKey); }
      if (admin()) setDragging(true);
      return previousStart.call(this, event, dateKey, ...rest);
    };
  }
  if (typeof handleDrop === 'function') {
    const previousDrop = handleDrop;
    handleDrop = async function(event, targetDate) {
      if (!admin() || !draggedData) return previousDrop.call(this, event, targetDate);
      const drag = draggedData;
      const cell = event && event.currentTarget;
      if (isClosedDate(drag.sourceDate) || isClosedDate(targetDate)) {
        if (event && isFn(event.preventDefault)) event.preventDefault();
        if (cell && cell.classList) cell.classList.remove('drag-over');
        draggedData = null;
        return closedNotice(isClosedDate(targetDate) ? targetDate : drag.sourceDate);
      }
      if (drag.sourceDate === targetDate) return previousDrop.call(this, event, targetDate);
      const partnerEntry = (scheduleMap[targetDate] || []).find(w => genderOf(w.name) === drag.gender);
      const partner = partnerEntry ? partnerEntry.name : '';
      const text = dragDescription(drag.sourceDate, drag.workerName, targetDate, partner);
      let dropEvent = event;
      const realConfirm = W.confirm;
      let forceConfirm = false;
      if (noDutyMap[targetDate]) {
        if (event && isFn(event.preventDefault)) event.preventDefault();
        if (cell && cell.classList) cell.classList.remove('drag-over');
        draggedData = null;
        const go = await ask({title: '당직 없는 날입니다', html: `<p>${escapeHtml(josaOf(isFn(W.dayText) ? W.dayText(targetDate) : `${targetDate}(${weekday(targetDate)})`, '은/는'))} '당직 없는 날'입니다. 당직 없는 날을 해제하고 ${escapeHtml(drag.workerName)} 님을 배정할까요?</p>`,
          buttons: [{label: '당직 없는 날 해제하고 배정', value: true, primary: true}, {label: '취소', value: null}]});
        if (!go) return false;
        draggedData = drag;
        dropEvent = {preventDefault() {}, currentTarget: cell || {classList: {remove() {}}}, dataTransfer: event && event.dataTransfer};
        forceConfirm = true;
      }
      queueAugment(payload => payload.historyLog && payload.historyLog.type === '드래그 교환'
        ? Object.assign({}, payload, {historyLog: Object.assign({}, payload.historyLog,
          {details: partner ? `[${drag.sourceDate}] '${drag.workerName}' ↔ [${targetDate}] '${partner}' 교환` : `[${drag.sourceDate}] '${drag.workerName}' → [${targetDate}] 이동`})}) : payload);
      const before = dataVersion;
      state.undoSeconds = 30;
      state.undoMode = 'toast';
      state.suppressDragToast = true;
      let pending;
      if (forceConfirm) W.confirm = () => true;
      try { pending = previousDrop.call(this, dropEvent, targetDate); } finally { if (forceConfirm) W.confirm = realConfirm; }
      try { await pending; } finally {
        clearAugments();
        state.undoSeconds = 10;
        state.undoMode = 'bar';
        state.suppressDragToast = false;
      }
      if (dataVersion !== before) actionToast(text, [{label: '되돌리기', countdown: true, onClick: () => applyUndo()}], 30);
      return true;
    };
  }
  if (typeof showToast === 'function') {
    const previousToast = showToast;
    showToast = function(message, isError) {
      // 3.18.0 QA: 드래그 결과는 [되돌리기]가 달린 알림 하나로만 알린다(본 화면의 '🔀 …맞교환했습니다' 알림을 함께 띄우지 않는다).
      if (state.suppressDragToast && !isError && /교환 완료|맞교환했습니다/.test(String(message))) return;
      return previousToast.call(this, message, isError);
    };
  }

  // ── 사원 삭제: 남은 당직 수 · 다시 배정 미리보기 · 성별 보존 ─────────
  const realGenderOf = typeof genderOf === 'function' ? genderOf : null;
  let roleGenderCache = null;
  function roleGenders() {
    if (roleGenderCache) return roleGenderCache;
    roleGenderCache = {};
    Object.values(typeof scheduleMap !== 'undefined' ? scheduleMap : {}).forEach(list => (list || []).forEach(w => {
      const m = /\((남|여)\)/.exec(String((w && w.role) || ''));
      if (m && !roleGenderCache[w.name]) roleGenderCache[w.name] = m[1] === '남' ? 'M' : 'F';
    }));
    return roleGenderCache;
  }
  function displayGenderOf(name) {
    const member = (memberList || []).find(m => m.name === name);
    if (member) return member.gender;
    const stone = ((typeof dutyRule !== 'undefined' && dutyRule && dutyRule.memberTombstones) || {})[name];
    if (stone && (stone.gender === 'M' || stone.gender === 'F')) return stone.gender;
    const fromRole = roleGenders()[name];
    if (fromRole) return fromRole;
    return realGenderOf ? realGenderOf(name) : 'F';
  }
  // 화면·집계 표시용으로만 바꾼다. 배정 계산이 쓰는 genderOf 는 그대로 둔다.
  function withDisplayGender(name) {
    const previous = W[name];
    if (!isFn(previous) || !realGenderOf) return;
    W[name] = function(...args) {
      const current = genderOf;
      roleGenderCache = null;
      genderOf = displayGenderOf;
      try { return previous.apply(this, args); } finally { genderOf = current; roleGenderCache = null; }
    };
  }
  function futureDuties(name, from) {
    const start = from || (typeof todayStr !== 'undefined' && todayStr) || todayKey();
    return Object.keys(scheduleMap).filter(k => k >= start && !noDutyMap[k] && (scheduleMap[k] || []).some(w => w.name === name)).sort();
  }
  if (typeof removeMember === 'function') {
    removeMember = async function(id) {
      const target = memberList.find(m => m.id === id);
      if (!target) return false;
      const name = target.name, genderText = target.gender === 'M' ? '남' : '여';
      const future = futureDuties(name);
      const shown = future.slice(0, 12).map(k => `${shortDate(k)}(${weekday(k)})`).join(', ') + (future.length > 12 ? ` 외 ${future.length - 12}일` : '');
      const otherYear = future.filter(k => k.slice(0, 4) !== future[0].slice(0, 4)).length;
      const choice = await ask({title: `사원 삭제: ${name}`, width: 560,
        html: `<p><b>${escapeHtml(name)}</b>(${genderText}) 사원을 명단에서 뺍니다.</p>` +
          (future.length ? `<p>오늘 이후 남은 당직이 <b>${future.length}일</b> 있습니다: ${escapeHtml(shown)}</p>` +
            `<p class="oa-sub">[남은 당직 다시 배정]을 고르면 삭제한 뒤 ${escapeHtml(ymLabel(monthOf(future[0])))}부터 연말까지 다시 계산한 결과를 <b>먼저 보여 드리고</b>, 확정해야 저장됩니다.${otherYear ? ` 다음 해 당직 ${otherYear}일은 그 해로 가서 따로 다시 배정하세요.` : ''}</p>`
            : '<p>오늘 이후 남은 당직은 없습니다.</p>') +
          '<p class="oa-sub">지난 당직 기록은 그대로 남고, 통계에는 이 사원의 성별이 그대로 표시됩니다.</p>',
        buttons: future.length
          ? [{label: '삭제하고 남은 당직 다시 배정(미리보기)', value: 'reassign', primary: true}, {label: '삭제만 하기 (당직은 그대로)', value: 'delete', danger: true}, {label: '취소', value: null}]
          : [{label: '삭제', value: 'delete', danger: true}, {label: '취소', value: null}]});
      if (!choice) return false;
      memberList = memberList.filter(m => m.id !== id);
      dutyRule.memberTombstones = Object.assign({}, dutyRule.memberTombstones || {}, {[name]: {gender: target.gender, removedAt: (typeof todayStr !== 'undefined' && todayStr) || todayKey()}});
      const h = makeHistory('사원 삭제', (typeof todayStr !== 'undefined' && todayStr) || todayKey(),
        `'${name}' (${genderText}) 삭제 · 오늘 이후 남은 당직 ${future.length}일 ${choice === 'reassign' ? '→ 다시 배정 미리보기로 이어감' : '그대로 둠'}`);
      state.pendingSnapshotKind = 'member';
      const ok = await syncFullDataToBackend({members: memberList, rules: dutyRule, historyLog: h});
      if (typeof renderMemberModalList === 'function') renderMemberModalList();
      if (!ok) return false;
      toast(`🗑️ ${name} 님을 사원 명단에서 뺐습니다.`);
      if (choice === 'reassign' && future.length) await reassignFrom(future[0]);
      return true;
    };
  }
  async function reassignFrom(dateKey) {
    const [y, m] = dateKey.split('-').map(Number);
    if (typeof closeMemberModal === 'function') closeMemberModal(true);
    curYear = y; curMonth = m;
    if (typeof updateYearMonthUI === 'function') updateYearMonthUI();
    renderCalendar();
    state.preferredScope = m === 12 ? 'MONTH' : 'FROM_MONTH';
    try { await generateScheduleByRules(); } finally { state.preferredScope = null; }
  }

  // ── 입력 창 닫기 전 확인(공통) ─────────────────────────────────
  function guardClose(closeName, isDirty, message) {
    const previous = W[closeName];
    if (!isFn(previous)) return;
    W[closeName] = function(force, ...rest) {
      if (force !== true && admin() && isDirty()) {
        confirmDiscard(message).then(ok => { if (ok) previous.call(this, true, ...rest); });
        return false;
      }
      return previous.call(this, force, ...rest);
    };
  }
  const RO_IDS = ['ro-wd', 'ro-we-m', 'ro-we-f', 'ro-hol-m', 'ro-hol-f', 'ro-maj-m', 'ro-maj-f'];
  const ORDER_KEYS = {weekday: 'ro-wd', weekendM: 'ro-we-m', weekendF: 'ro-we-f', holidayM: 'ro-hol-m', holidayF: 'ro-hol-f', majorM: 'ro-maj-m', majorF: 'ro-maj-f'};
  function splitNames(text) { return String(text || '').split(/[,，\s]+/).filter(Boolean); }
  function ruleFormDirty() {
    const modal = $('rule-modal');
    if (!modal || !modal.classList.contains('active') || typeof dutyRule === 'undefined') return false;
    const fo = typeof migrateFixedOrders === 'function' ? migrateFixedOrders(dutyRule.fixedOrders) : (dutyRule.fixedOrders || {});
    const same = (id, value) => !$(id) || $(id).value === value;
    if (!same('rule-algo', dutyRule.algoType) || !same('rule-sub-zero', String(dutyRule.substituteScoreZero)) ||
        !same('rule-21-fri-off', String(dutyRule.rule21FridayOff)) || !same('rule-balance-dow', String(dutyRule.balanceDayOfWeek !== false))) return true;
    return Object.entries(ORDER_KEYS).some(([key, id]) => $(id) && splitNames($(id).value).join(',') !== (fo[key] || []).join(','));
  }
  guardClose('closeRuleModal', ruleFormDirty, '근무 규칙에서 바꾼 내용이 저장되지 않았습니다. 닫으면 사라집니다. 닫을까요?');
  guardClose('closeHolidayModal', () => !!String(fieldValue('hol-name')).trim(), '적어 둔 공휴일 이름이 저장되지 않았습니다. 닫을까요?');
  guardClose('closeMemberModal', () => !!String(fieldValue('new-member-name')).trim(), '적어 둔 사원 이름이 아직 등록되지 않았습니다. 닫을까요?');
  if (typeof askRejectReason === 'function') {
    const previousReject = askRejectReason;
    askRejectReason = function(...args) {
      const promise = previousReject.apply(this, args);
      const cancel = $('reject-cancel');
      if (cancel && isFn(cancel.onclick)) {
        const close = cancel.onclick;
        cancel.onclick = async event => {
          if (String(fieldValue('reject-reason-input')).trim() && !(await confirmDiscard('적어 둔 반려 사유가 전달되지 않았습니다. 반려하지 않고 닫을까요?'))) return;
          close.call(cancel, event);
        };
      }
      return promise;
    };
  }

  // ── 근무 규칙: 이름 칩 편집기 · 명단 대조 · 이력 비교 ───────────────
  const ORDER_LABELS = {weekday: '평일 순환(여)', weekendM: '주말(남)', weekendF: '주말(여)', holidayM: '일반 공휴일(남)', holidayF: '일반 공휴일(여)', majorM: '명절(남)', majorF: '명절(여)'};
  function specFor(id) { return (typeof ROSTER_SPECS !== 'undefined' ? ROSTER_SPECS : []).find(s => s.id === id) || {id, label: id, gender: 'F'}; }
  function rosterProblems(readList = id => splitNames(fieldValue(id))) {
    const known = new Map((memberList || []).map(m => [m.name, m.gender]));
    const problems = [];
    RO_IDS.forEach(id => {
      const spec = specFor(id), names = readList(id);
      const unknown = names.filter(n => !known.has(n));
      const wrong = names.filter(n => known.has(n) && known.get(n) !== spec.gender);
      const dup = [...new Set(names.filter((n, i) => names.indexOf(n) !== i))];
      if (unknown.length) problems.push({id, label: spec.label, names: unknown, why: '사원 명단에 없는 이름'});
      if (wrong.length) problems.push({id, label: spec.label, names: wrong, why: `${spec.gender === 'M' ? '남' : '여'}사원 순번에 맞지 않는 성별`});
      if (dup.length) problems.push({id, label: spec.label, names: dup, why: '두 번 들어간 이름'});
    });
    return problems;
  }
  function listChange(before, after) {
    const b = before || [], a = after || [];
    if (b.join(',') === a.join(',')) return '';
    const added = a.filter(n => !b.includes(n)), removed = b.filter(n => !a.includes(n));
    const notes = [];
    if (added.length) notes.push('추가: ' + added.join(', '));
    if (removed.length) notes.push('빠짐: ' + removed.join(', '));
    if (!notes.length) notes.push('순서만 바뀜');
    return `${b.join(', ') || '(비어 있음)'} → ${a.join(', ') || '(비어 있음)'} (${notes.join(' / ')})`;
  }
  function ruleDiffText(before = {}, after = {}) {
    const parts = [];
    const algo = v => v === 'FIXED_ORDER' ? '고정 순환제' : '누적 가중치 균등';
    const onOff = (v, on, off) => v ? on : off;
    if ((before.algoType || 'FAIR_WEIGHT') !== (after.algoType || 'FAIR_WEIGHT')) parts.push(`배정 방식: ${algo(before.algoType)} → ${algo(after.algoType)}`);
    if (!!before.substituteScoreZero !== !!after.substituteScoreZero) parts.push(`대체 근무 점수: ${onOff(before.substituteScoreZero, '0점', '점수 가산')} → ${onOff(after.substituteScoreZero, '0점', '점수 가산')}`);
    if (!!before.rule21FridayOff !== !!after.rule21FridayOff) parts.push(`패밀리데이: ${onOff(before.rule21FridayOff, '사용', '사용 안 함')} → ${onOff(after.rule21FridayOff, '사용', '사용 안 함')}`);
    if ((before.balanceDayOfWeek !== false) !== (after.balanceDayOfWeek !== false)) parts.push(`주말 요일 편중 방지: ${onOff(before.balanceDayOfWeek !== false, '사용', '사용 안 함')} → ${onOff(after.balanceDayOfWeek !== false, '사용', '사용 안 함')}`);
    const fb = before.fixedOrders || {}, fa = after.fixedOrders || {};
    Object.keys(ORDER_LABELS).forEach(key => {
      const change = listChange(fb[key], fa[key]);
      if (change) parts.push(`${ORDER_LABELS[key]} 순번: ${change}`);
    });
    const mb = before.familyDayOffMonths || [], ma = after.familyDayOffMonths || [];
    if (mb.join(',') !== ma.join(',')) parts.push(`패밀리데이 없는 달: ${mb.join(', ') || '없음'} → ${ma.join(', ') || '없음'}`);
    return parts.length ? parts.join(' / ') : '바뀐 설정 없음 (같은 설정으로 다시 저장)';
  }
  function wrapRuleSave(name) {
    const previous = W[name];
    if (!isFn(previous)) return;
    W[name] = async function(...args) {
      const problems = rosterProblems();
      if (problems.length) {
        const go = await ask({title: '순번에 확인할 이름이 있습니다', width: 580,
          html: '<p>아래 이름은 사원 명단과 맞지 않습니다. 이대로 저장하면 이 사람들은 배정되지 않거나 잘못 배정될 수 있습니다.</p><ul class="oa-list">' +
            problems.map(p => `<li><b>${escapeHtml(p.label)}</b>: ${escapeHtml(p.names.join(', '))} — ${escapeHtml(p.why)}</li>`).join('') + '</ul>',
          buttons: [{label: '돌아가서 고치기', value: null, primary: true}, {label: '그래도 저장', value: true, danger: true}]});
        if (!go) return false;
      }
      const before = clone(dutyRule);
      queueAugment(payload => payload.rules && payload.historyLog && /규칙 변경$/.test(payload.historyLog.type)
        ? Object.assign({}, payload, {historyLog: Object.assign({}, payload.historyLog, {type: '근무 규칙 변경', details: ruleDiffText(before, payload.rules)})}) : payload);
      try { return await previous.apply(this, args); } finally { clearAugments(); }
    };
  }
  wrapRuleSave('saveRulesOnly');
  wrapRuleSave('saveRulesAndRegenerate');

  function setRosterNames(id, names) {
    const area = $(id);
    if (!area) return;
    area.value = names.join(', ');
    try { area.dispatchEvent(new Event('input', {bubbles: true})); } catch (_) {
      if (typeof renderRosterCheck === 'function') renderRosterCheck();
    }
    renderChips(id);
  }
  const chipSelection = Object.create(null);
  function chipEditingEnabled() { return fieldValue('rule-algo') === 'FIXED_ORDER'; }
  function chipHtml(id) {
    const spec = specFor(id), names = splitNames(fieldValue(id));
    const enabled = chipEditingEnabled(), selected = chipSelection[id];
    const known = new Map((memberList || []).map(m => [m.name, m.gender]));
    const candidates = (memberList || []).filter(m => m.gender === spec.gender && !names.includes(m.name));
    return `<div class="oa-roster-bar"><div class="oa-roster-status"></div>` +
      `<select data-add aria-label="${escapeHtml(spec.label)} 순번에 사람 추가"${enabled ? '' : ' disabled'}><option value="">+ 사람 추가</option>` +
      candidates.map(m => `<option value="${escapeHtml(m.name)}">${escapeHtml(m.name)}</option>`).join('') + '</select></div>' +
      `<ol class="oa-chip-list" aria-label="${escapeHtml(spec.label)} 순번">` + names.map((n, i) => {
      const bad = !known.has(n) ? '명단에 없음' : (known.get(n) !== spec.gender ? '성별 다름' : '');
      return `<li class="oa-chip${bad ? ' oa-chip-bad' : ''}${selected === i ? ' oa-chip-selected' : ''}" draggable="${enabled}" data-idx="${i}">` +
        `<button type="button" data-select aria-pressed="${selected === i}" aria-label="${i + 1}번 ${escapeHtml(n)} 선택"${enabled ? '' : ' disabled'}><span class="oa-chip-no">${i + 1}</span><span class="oa-chip-name">${escapeHtml(n)}</span>${bad ? `<em>${bad}</em>` : ''}</button></li>`;
    }).join('') + (names.length ? '' : '<li class="oa-chip-empty">비어 있음</li>') + '</ol>' +
      (enabled && Number.isInteger(selected) && names[selected] !== undefined ? `<div class="oa-chip-toolbar" role="group" aria-label="${escapeHtml(names[selected])} 순번 편집" data-idx="${selected}"><span>${escapeHtml(names[selected])}</span><button type="button" data-move="-1"${selected === 0 ? ' disabled' : ''}>더 앞 순서로</button><button type="button" data-move="1"${selected === names.length - 1 ? ' disabled' : ''}>더 뒤 순서로</button><button type="button" data-remove>제거</button></div>` : '');
  }
  function renderChips(id) {
    const box = $('oa-chips-' + id);
    if (box) {
      const status = $('rc-' + id);
      box.innerHTML = chipHtml(id);
      const slot = box.querySelector('.oa-roster-status');
      if (status && slot) slot.appendChild(status);
    }
  }
  function setupChipEditor(id) {
    const area = $(id);
    if (!area || !area.parentNode || $('oa-chips-' + id)) return;
    const box = doc.createElement('div');
    box.id = 'oa-chips-' + id;
    box.className = 'oa-chips';
    area.parentNode.insertBefore(box, area.nextSibling);
    area.style.display = 'none';
    area.setAttribute('aria-hidden', 'true');
    area.tabIndex = -1;
    let dragFrom = -1;
    box.addEventListener('click', event => {
      if (!chipEditingEnabled()) return;
      const button = event.target.closest && event.target.closest('button');
      if (!button || button.disabled) return;
      const chip = event.target.closest('.oa-chip');
      if (button.hasAttribute('data-select') && chip) {
        chipSelection[id] = Number(chip.dataset.idx);
        renderChips(id);
        const selected = box.querySelector('.oa-chip-selected button');
        if (selected) selected.focus();
        return;
      }
      const toolbar = event.target.closest('.oa-chip-toolbar');
      if (!toolbar) return;
      const names = splitNames(area.value), index = Number(toolbar.dataset.idx);
      if (button.hasAttribute('data-remove')) {
        names.splice(index, 1);
        chipSelection[id] = Math.min(index, names.length - 1);
      } else if (button.hasAttribute('data-move')) {
        const to = index + Number(button.dataset.move);
        if (to < 0 || to >= names.length) return;
        [names[index], names[to]] = [names[to], names[index]];
        chipSelection[id] = to;
      } else return;
      setRosterNames(id, names);
      const again = box.querySelector('.oa-chip-selected button') || box.querySelector('select[data-add]');
      if (again) again.focus();
    });
    box.addEventListener('change', event => {
      if (!chipEditingEnabled() || !event.target.matches || !event.target.matches('select[data-add]') || !event.target.value) return;
      const names = splitNames(area.value);
      const candidate = (memberList || []).find(m => m.name === event.target.value && m.gender === specFor(id).gender);
      if (!candidate || names.includes(candidate.name)) return;
      chipSelection[id] = names.length;
      setRosterNames(id, names.concat(candidate.name));
      const selected = box.querySelector('.oa-chip-selected button');
      if (selected) selected.focus();
    });
    box.addEventListener('dragstart', event => {
      const chip = event.target.closest && event.target.closest('.oa-chip');
      if (!chip || !chipEditingEnabled()) return;
      dragFrom = Number(chip.dataset.idx);
      try { event.dataTransfer.setData('text/plain', String(dragFrom)); event.dataTransfer.effectAllowed = 'move'; } catch (_) {}
      event.stopPropagation();
    });
    box.addEventListener('dragover', event => { if (dragFrom >= 0) { event.preventDefault(); event.stopPropagation(); } });
    box.addEventListener('drop', event => {
      const chip = event.target.closest && event.target.closest('.oa-chip');
      if (dragFrom < 0 || !chipEditingEnabled()) return;
      event.preventDefault();
      event.stopPropagation();
      const names = splitNames(area.value);
      const to = chip ? Number(chip.dataset.idx) : names.length - 1;
      const [moved] = names.splice(dragFrom, 1);
      names.splice(to, 0, moved);
      dragFrom = -1;
      chipSelection[id] = to;
      setRosterNames(id, names);
    });
    box.addEventListener('dragend', () => { dragFrom = -1; });
    area.addEventListener('input', () => renderChips(id));
    renderChips(id);
  }
  if (typeof updateFixedOrderState === 'function') {
    const previousFixedOrderState = updateFixedOrderState;
    updateFixedOrderState = function(...args) {
      const result = previousFixedOrderState.apply(this, args);
      const box = $('fixed-order-box');
      if (box) { box.style.opacity = '1'; box.dataset.active = String(chipEditingEnabled()); }
      RO_IDS.forEach(renderChips);
      return result;
    };
  }
  if (typeof openRuleModal === 'function') {
    const previousOpenRule = openRuleModal;
    openRuleModal = function(...args) {
      const result = previousOpenRule.apply(this, args);
      try { RO_IDS.forEach(id => { setupChipEditor(id); renderChips(id); }); } catch (_) {}
      return result;
    };
  }
  if (typeof appendToRoster === 'function') {
    const previousAppend = appendToRoster;
    appendToRoster = function(id, ...rest) { const result = previousAppend.call(this, id, ...rest); renderChips(id); return result; };
  }
  if (typeof unsetFamilyOffMonth === 'function') {
    const previousUnset = unsetFamilyOffMonth;
    unsetFamilyOffMonth = async function(monthKey) {
      const go = await ask({title: '패밀리데이 다시 적용', html: `<p>${escapeHtml(ymLabel(monthKey))}을 '패밀리데이 없음' 기억에서 뺄까요?</p><p class="oa-sub">다음 자동 배정 때 그 달에 규칙대로 패밀리데이가 다시 지정됩니다.</p>`,
        buttons: [{label: '다시 적용', value: true, primary: true}, {label: '취소', value: null}]});
      if (!go) return false;
      const realConfirm = W.confirm;
      W.confirm = () => true;
      let pending;
      try { pending = previousUnset.call(this, monthKey); } finally { W.confirm = realConfirm; }
      return pending;
    };
  }

  // ── 자동 배정: 범위 창 · 미리보기 · 사본 ─────────────────────────
  function scopeIncludesClosed(scope) {
    if (typeof scopeMonths !== 'function') return [];
    const {startM, endM} = scopeMonths(scope), out = [];
    for (let m = startM; m <= endM; m++) if (isClosedMonth(`${curYear}-${pad2(m)}`)) out.push(m);
    return out;
  }
  if (typeof openScopeModal === 'function') {
    const previousScope = openScopeModal;
    openScopeModal = function(...args) {
      if (state.scopeAutoResolve) {
        const value = state.scopeAutoResolve;
        state.scopeAutoResolve = null;
        return Promise.resolve(value);
      }
      const promise = previousScope.apply(this, args);
      try {
        const preferred = state.preferredScope && $({MONTH: 'scope-opt-month', FROM_MONTH: 'scope-opt-rest', YEAR: 'scope-opt-year'}[state.preferredScope]);
        if (preferred) preferred.checked = true;
        [['MONTH', 'scope-opt-month'], ['FROM_MONTH', 'scope-opt-rest'], ['YEAR', 'scope-opt-year']].forEach(([scope, id]) => {
          const input = $(id);
          if (!input) return;
          const months = scopeIncludesClosed(scope);
          input.disabled = months.length > 0;
          const row = input.closest ? input.closest('.scope-option') : null;
          let note = row ? row.querySelector('.oa-scope-closed') : null;
          if (row && months.length && !note) { note = doc.createElement('small'); note.className = 'oa-scope-closed'; (row.querySelector('span') || row).appendChild(note); }
          if (note) { note.textContent = months.length ? ` 마감된 달(${months.join(', ')}월)이 들어 있어 고를 수 없습니다.` : ''; note.hidden = !months.length; }
        });
        const checked = doc.querySelector('input[name="assign-scope"]:checked');
        if (checked && checked.disabled) {
          const free = [...doc.querySelectorAll('input[name="assign-scope"]')].find(input => !input.disabled);
          if (free) free.checked = true;
        }
      } catch (_) {}
      return promise;
    };
  }

  function namesOf(list) { return (list || []).map(w => w.name); }
  function workerMeta(list) { return JSON.stringify((list || []).map(w => [w.name, w.role || '', !!w.isSubstitute, !!w.zeroScore])); }
  function buildPreviewModel({before, after, year, startM, endM}) {
    const rows = [], counts = {kept: 0, added: 0, changed: 0, cleared: 0, newOff: 0, reopened: 0, manual: 0};
    const tally = {};
    const bump = (name, side) => { tally[name] = tally[name] || {before: 0, after: 0}; tally[name][side] += 1; };
    for (let m = startM; m <= endM; m++) {
      const dim = new Date(year, m, 0).getDate();
      for (let d = 1; d <= dim; d++) {
        const k = `${year}-${pad2(m)}-${pad2(d)}`;
        const beforeNo = !!(before.noDuty || {})[k], afterNo = !!(after.noDuty || {})[k];
        const bList = beforeNo ? [] : ((before.schedules || {})[k] || []), aList = afterNo ? [] : ((after.schedules || {})[k] || []);
        const bNames = namesOf(bList), aNames = namesOf(aList);
        bNames.forEach(n => bump(n, 'before'));
        aNames.forEach(n => bump(n, 'after'));
        const bMemo = (before.comments || {})[k] || '', aMemo = (after.comments || {})[k] || '';
        const metaChanged = workerMeta((before.schedules || {})[k]) !== workerMeta((after.schedules || {})[k]);
        const changed = bNames.join(',') !== aNames.join(',') || beforeNo !== afterNo || bMemo !== aMemo || metaChanged;
        const manual = (before.noDuty || {})[k] === true || ((before.schedules || {})[k] || []).some(w => w.isSubstitute || w.zeroScore || /수동/.test(w.role || ''));
        let kind = '그대로';
        if (changed) {
          if (!beforeNo && afterNo) { kind = '당직 없음 추가'; counts.newOff++; }
          else if (beforeNo && !afterNo) { kind = '당직 없음 해제'; counts.reopened++; }
          else if (!bNames.length && aNames.length) { kind = '신규'; counts.added++; }
          else if (bNames.length && !aNames.length) { kind = '해제'; counts.cleared++; }
          else { kind = '변경'; counts.changed++; }
          if (manual) counts.manual++;
        } else counts.kept++;
        // 같은 사람인데 대체·0점 표식만 바뀐 날은 표식까지 적어야 무엇이 바뀌었는지 보인다.
        const metaOnly = metaChanged && bNames.join(',') === aNames.join(',') && bNames.length > 0;
        rows.push({k, month: `${year}-${pad2(m)}`, kind, changed, manual, beforeNo, afterNo, bNames, aNames, bMemo, aMemo, metaChanged,
          bDetail: metaOnly ? describeWorkers((before.schedules || {})[k]) : '', aDetail: metaOnly ? describeWorkers((after.schedules || {})[k]) : ''});
      }
    }
    const deltas = Object.entries(tally).map(([name, t]) => ({name, before: t.before, after: t.after, delta: t.after - t.before}))
      .filter(x => x.delta !== 0).sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta) || a.name.localeCompare(b.name, 'ko'));
    const months = [];
    rows.forEach(row => {
      let group = months[months.length - 1];
      if (!group || group.key !== row.month) { group = {key: row.month, label: ymLabel(row.month), rows: [], changed: 0, newOff: 0}; months.push(group); }
      group.rows.push(row);
      if (row.changed) group.changed++;
      if (row.kind === '당직 없음 추가') group.newOff++;
    });
    return {rows, months, counts, deltas};
  }
  function describeWorkers(list) {
    return (list || []).map(w => `${w.name} [${w.role || '당직'}${w.isSubstitute ? ' · 대체' : ''}${w.zeroScore ? ' · 0점' : ''}]`).join(', ');
  }
  function namesHtml(names, other, mark, noDuty) {
    if (noDuty) return '<span class="oa-pv-off">당직 없음</span>';
    if (!names.length) return '<span class="oa-sub">—</span>';
    return names.map(n => other.includes(n) ? escapeHtml(n) : `<mark class="${mark}">${escapeHtml(n)}</mark>`).join(', ');
  }
  function previewRowHtml(row) {
    const memo = row.bMemo !== row.aMemo;
    const before = row.bDetail ? escapeHtml(row.bDetail) : namesHtml(row.bNames, row.aNames, 'oa-diff-del', row.beforeNo);
    const after = row.aDetail ? escapeHtml(row.aDetail) : namesHtml(row.aNames, row.bNames, 'oa-diff-add', row.afterNo);
    return `<tr class="${row.changed ? 'oa-pv-changed' : 'oa-pv-same'}${row.manual && row.changed ? ' pv-row-manual' : ''}" data-oa-changed="${row.changed ? 1 : 0}"${row.changed ? '' : ' hidden'}>` +
      `<td>${escapeHtml(shortDate(row.k))}(${weekday(row.k)})${row.manual && row.changed ? ' <span class="pv-tag">수동</span>' : ''}</td>` +
      `<td><span class="oa-pv-kind oa-kind-${escapeHtml(row.kind.replace(/\s/g, ''))}">${escapeHtml(row.kind)}</span></td>` +
      `<td>${before}${memo ? `<div class="oa-sub">메모: ${escapeHtml(row.bMemo || '—')}</div>` : ''}</td>` +
      `<td>${after}${memo ? `<div class="oa-sub">메모: ${escapeHtml(row.aMemo || '—')}</div>` : ''}</td></tr>`;
  }
  function previewHtml(model) {
    const delta = model.deltas.length
      ? '<div class="oa-pv-delta"><b>사람별 당직 수 변화</b> ' + model.deltas.map(x =>
        `<span class="${x.delta > 0 ? 'oa-up' : 'oa-down'}">${escapeHtml(x.name)} ${x.before}→${x.after} (${x.delta > 0 ? '+' : '−'}${Math.abs(x.delta)})</span>`).join(' · ') + '</div>'
      : '<div class="oa-pv-delta"><b>사람별 당직 수 변화</b> 없음 — 모두 같은 횟수입니다.</div>';
    const filter = '<label class="oa-pv-filter"><input type="checkbox" id="oa-pv-only-changed" checked> 바뀐 날만</label>';
    const groups = model.months.map(group => `<details class="oa-pv-month" data-month="${group.key}"${group.changed ? ' open' : ''}>` +
      `<summary><b>${escapeHtml(group.label)}</b> · 바뀌는 날 ${group.changed}일${group.newOff ? ` (당직 없음 추가 ${group.newOff}일)` : ''}</summary>` +
      '<table class="data-table"><thead><tr><th>날짜</th><th>구분</th><th>지금</th><th>바뀔 값</th></tr></thead><tbody>' +
      group.rows.map(previewRowHtml).join('') +
      `<tr class="oa-pv-none"${group.changed ? ' hidden' : ''}><td colspan="4">이 달에는 바뀌는 날이 없습니다.</td></tr></tbody></table></details>`).join('');
    return delta + filter + `<div class="oa-pv-groups">${groups}</div>`;
  }
  function applyPreviewFilter(container, onlyChanged) {
    container.querySelectorAll('tr[data-oa-changed]').forEach(row => { row.hidden = onlyChanged && row.dataset.oaChanged !== '1'; });
    container.querySelectorAll('.oa-pv-month').forEach(group => {
      const none = group.querySelector('.oa-pv-none');
      const anyChanged = !!group.querySelector('tr[data-oa-changed="1"]');
      if (none) none.hidden = anyChanged || !onlyChanged;
    });
  }
  if (typeof openAssignPreview === 'function') {
    openAssignPreview = function(before, scope, scopeLabel, noDutyInfo) {
      return new Promise(resolve => {
        noDutyInfo = noDutyInfo || {};
        const {startM, endM} = scopeMonths(scope);
        const model = buildPreviewModel({before, after: {schedules: scheduleMap, noDuty: noDutyMap, comments: commentMap}, year: curYear, startM, endM});
        const shortages = typeof assignmentShortages === 'function' ? assignmentShortages(scope) : [];
        const reviewIssues = typeof assignmentReviewIssues === 'function' ? assignmentReviewIssues(scope) : [];
        const c = model.counts;
        const notes = [];
        if (shortages.length) notes.push(`<div class="pv-manual"><b>미배정 ${shortages.length}자리</b> · 인원·부재·순번 확인 필요<br>${shortages.map(x => `${escapeHtml(x.k)} ${escapeHtml(x.reason)}`).join('<br>')}</div>`);
        if (reviewIssues.length) notes.push(`<div class="pv-manual"><b>규칙·공휴일 설정 확인 ${reviewIssues.length}건</b><br>${reviewIssues.map(x => `${escapeHtml(x.k)} ${escapeHtml(x.message)}`).join('<br>')}</div>`);
        if ((noDutyInfo.cleared || []).length) notes.push(`<div class="pv-manual"><b>해제되는 당직 없는 날 ${noDutyInfo.cleared.length}건</b> · 다시 근무일이 되어 순번대로 배정됩니다<br>${noDutyInfo.cleared.map(x => `${escapeHtml(x.k)} — ${escapeHtml(x.why)}`).join('<br>')}</div>`);
        else if (noDutyInfo.fresh) notes.push('<div class="pv-manual"><b>규칙으로 정한 당직 없는 날 중 해제할 날이 없습니다</b></div>');
        if ((noDutyInfo.familyUnremembered || []).length) notes.push(`<div class="pv-manual"><b>'패밀리데이 없음' 기억을 지우는 달</b> · ${noDutyInfo.familyUnremembered.map(escapeHtml).join(', ')}</div>`);
        if ((noDutyInfo.keptManual || []).length) notes.push(`<div class="pv-manual"><b>그대로 두는 당직 없는 날 ${noDutyInfo.keptManual.length}건</b> · 관리자가 지정한 날이라 자동 배정이 바꾸지 않습니다<br>${noDutyInfo.keptManual.map(x => `${escapeHtml(x.k)} — ${escapeHtml(x.why)}`).join('<br>')}</div>`);
        const scopeBox = $('preview-scope');
        if (scopeBox) scopeBox.textContent = scopeLabel;
        const summary = $('preview-summary');
        if (summary) summary.innerHTML =
          `<span class="pv-stat"><b>${c.kept}</b>일 그대로</span><span class="pv-stat"><b>${c.added}</b>일 신규</span>` +
          `<span class="pv-stat"><b>${c.changed}</b>일 변경</span><span class="pv-stat"><b>${c.cleared}</b>일 해제</span>` +
          (c.newOff ? `<span class="pv-stat"><b>${c.newOff}</b>일 당직 없음 추가</span>` : '') + (c.reopened ? `<span class="pv-stat"><b>${c.reopened}</b>일 당직 없음 해제</span>` : '') +
          (c.manual ? `<span class="pv-stat pv-manual"><b>${c.manual}</b>일은 손으로 맞췄던 날</span>` : '') +
          (notes.length ? `<details class="oa-pv-notes"${shortages.length ? ' open' : ''}><summary>확인할 점 ${notes.length}가지</summary>${notes.join('')}</details>` : '');
        const modal = $('assign-preview-modal');
        let groups = $('oa-preview-groups');
        if (modal && !groups && isFn(doc.createElement)) {
          groups = doc.createElement('div');
          groups.id = 'oa-preview-groups';
          const legacy = modal.querySelector('.table-scroll');
          if (legacy) { legacy.style.display = 'none'; legacy.parentNode.insertBefore(groups, legacy); }
          else if (summary && summary.parentNode) summary.parentNode.appendChild(groups);
        }
        if (groups) {
          groups.innerHTML = previewHtml(model);
          const only = groups.querySelector('#oa-pv-only-changed');
          if (only) only.addEventListener('change', () => applyPreviewFilter(groups, only.checked));
        }
        const tbody = $('preview-tbody');
        if (tbody) tbody.innerHTML = '';
        if (modal) modal.style.display = 'flex';
        const done = value => {
          if (modal) modal.style.display = 'none';
          const apply = $('preview-apply'), cancel = $('preview-cancel');
          if (apply) apply.onclick = null;
          if (cancel) cancel.onclick = null;
          if (value) state.pendingSnapshotKind = 'assign';
          resolve(value);
        };
        const apply = $('preview-apply'), cancel = $('preview-cancel');
        if (apply) { apply.onclick = () => done(true); try { apply.focus({preventScroll: true}); } catch (_) {} }
        if (cancel) cancel.onclick = () => done(false);
      });
    };
  }

  if (typeof generateScheduleByRules === 'function') {
    const previousGenerate = generateScheduleByRules;
    generateScheduleByRules = async function(opts, ...rest) {
      if (admin() && !(opts && opts.silent) && isClosedMonth(currentMonthKey()) && !state.scopeAutoResolve) {
        closedNotice(`${currentMonthKey()}-01`);
        return false;
      }
      try { return await previousGenerate.call(this, opts, ...rest); }
      finally { if (state.pendingSnapshotKind === 'assign') state.pendingSnapshotKind = null; }
    };
  }

  // ── 공휴일을 바꾸면 그 달 다시 배정 미리보기 ─────────────────────
  async function previewMonth(dateKey) {
    if (isClosedDate(dateKey)) return closedNotice(dateKey);
    const [y, m] = dateKey.split('-').map(Number);
    if (typeof closeHolidayModal === 'function') closeHolidayModal(true);
    curYear = y; curMonth = m;
    if (typeof updateYearMonthUI === 'function') updateYearMonthUI();
    renderCalendar();
    state.scopeAutoResolve = {scope: 'MONTH', fresh: false, familyReset: false};
    try { await generateScheduleByRules(); } finally { state.scopeAutoResolve = null; }
    return true;
  }
  ['addCustomHoliday', 'delCustomHoliday', 'removeBaseHoliday', 'restoreBaseHoliday'].forEach(name => {
    const previous = W[name];
    if (!isFn(previous)) return;
    W[name] = async function(...args) {
      const key = name === 'addCustomHoliday' ? (typeof normalizeDate === 'function' ? normalizeDate(fieldValue('hol-date')) : fieldValue('hol-date')) : args[0];
      if (admin() && isClosedDate(key)) return closedNotice(key);
      const holidayIntent = {addCustomHoliday: 'SET_CUSTOM', delCustomHoliday: 'DELETE_CUSTOM', removeBaseHoliday: 'REMOVE_BASE', restoreBaseHoliday: 'RESTORE_BASE'}[name];
      const draft = {holidayIntent, date_key: key};
      if (holidayIntent === 'SET_CUSTOM') { draft.name = String(fieldValue('hol-name')).trim(); draft.is_major = !!$('hol-major')?.checked; }
      if (admin() && W.HolidaySupplement?.shouldPreview(holidayIntent, key, draft)) {
        return W.HolidaySupplement.open(draft);
      }
      const calendarOnly = admin() && W.HolidaySupplement?.calendarOnlyReason(holidayIntent, key, draft);
      if (calendarOnly) {
        const reviewedVersion = dataVersion;
        const go = await ask({title: '공휴일 설정만 변경', html: `<p>${escapeHtml(calendarOnly)}</p><p><b>${escapeHtml(key)}</b>의 공휴일 설정만 바꾸며, 기존 근무자와 다른 날짜의 배정은 유지합니다. 남성 자동 보완은 실행하지 않습니다.</p>`,
          buttons: [{label: '공휴일 설정만 저장', value: true}, {label: '취소', value: null}]});
        if (!go) return false;
        if (reviewedVersion !== dataVersion) { toast('확인하는 동안 자료가 변경되었습니다. 최신 내용을 확인하고 다시 저장하세요.', true); return false; }
      }
      const before = dataVersion;
      const result = await previous.apply(this, args);
      if (admin() && validKey(key) && dataVersion !== before) {
        actionToast(`${shortDate(key)} 공휴일 설정을 바꿨습니다. 당직표에 반영하려면 ${ymLabel(monthOf(key))}을 다시 배정하세요.`,
          [{label: '이 달 다시 배정 미리보기', onClick: () => previewMonth(key)}], 30);
      }
      return result;
    };
  });

  // ── 백업 파일 불러오기(JSON) ─────────────────────────────────
  function backupFileSummary(data) {
    if (!data || typeof data !== 'object' || Array.isArray(data)) return {ok: false};
    if (data.formatVersion != null && (!Number.isInteger(data.formatVersion) || data.formatVersion > 2)) return {ok: false, unsupported: true};
    const hasSupplementMetadata = Object.prototype.hasOwnProperty.call(data, 'holidaySupplements');
    if (hasSupplementMetadata && (data.holidaySupplements?.schema !== 1 || !Array.isArray(data.holidaySupplements.events) || !Array.isArray(data.holidaySupplements.bases))) return {ok: false, unsupported: true};
    const members = data.members || data.memberList;
    const schedules = data.schedules || data.scheduleMap;
    if (!Array.isArray(members) || !schedules || typeof schedules !== 'object' || Array.isArray(schedules)) return {ok: false};
    const days = Object.keys(schedules).filter(validKey).sort();
    const history = Array.isArray(data.history) ? data.history : (Array.isArray(data.changeHistory) ? data.changeHistory : []);
    const last = history.map(h => String((h && h.timestamp) || '')).filter(Boolean).sort().pop() || '';
    return {ok: true, members: members.length, scheduleDays: days.filter(k => (schedules[k] || []).length).length,
      absences: Array.isArray(data.absences) ? data.absences.length : 0, holidays: Object.keys(data.customHolidays || {}).length,
      history: history.length, from: days[0] || '', to: days[days.length - 1] || '', lastChange: last,
      exportedAt: data.exportedAt ? (localStamp(data.exportedAt) || String(data.exportedAt)) : '',
      hasSupplementMetadata, supplements: hasSupplementMetadata ? data.holidaySupplements.events.length : 0, pendingChanges: !!data.pendingChanges};
  }
  function currentSummary() {
    const days = Object.keys(scheduleMap).filter(validKey).sort();
    return {members: memberList.length, scheduleDays: days.filter(k => (scheduleMap[k] || []).length).length, absences: absences.length,
      holidays: Object.keys(customHolidays || {}).length, history: changeHistory.length, from: days[0] || '', to: days[days.length - 1] || '',
      lastChange: (changeHistory[0] && changeHistory[0].timestamp) || '', supplements: supplementMetadata().events?.length || 0, contractActive: supplementContractActive()};
  }
  function importPreviewHtml(fileName, file, now) {
    const row = (label, a, b) => `<tr><th>${label}</th><td>${escapeHtml(a)}</td><td${String(a) !== String(b) ? ' class="oa-diff-cell"' : ''}>${escapeHtml(b)}</td></tr>`;
    return `<p>파일: <b>${escapeHtml(fileName)}</b><br>파일을 만든 시각: <b>${escapeHtml(file.exportedAt || '알 수 없음')}</b></p>` +
      '<div class="table-scroll"><table><thead><tr><th></th><th>지금</th><th>이 파일</th></tr></thead><tbody>' +
      row('사원', `${now.members}명`, `${file.members}명`) + row('당직이 있는 날', `${now.scheduleDays}일`, `${file.scheduleDays}일`) +
      row('휴가·부재', `${now.absences}건`, `${file.absences}건`) + row('공휴일 설정', `${now.holidays}건`, `${file.holidays}건`) +
      ((now.contractActive || file.hasSupplementMetadata) ? row('공휴일 보완 기록', `${now.supplements || 0}건`, file.hasSupplementMetadata ? `${file.supplements}건` : '기록 없는 이전 형식') : '') +
      row('당직 기간', `${now.from || '-'} ~ ${now.to || '-'}`, `${file.from || '-'} ~ ${file.to || '-'}`) +
      row('마지막 변경', now.lastChange || '-', file.lastChange || '-') + '</tbody></table></div>' +
      '<p>불러오면 <b>지금 당직표·명단·휴가·근무 규칙을 이 파일 내용으로 덮어씁니다.</b> 변경 이력은 지우지 않습니다.</p>' +
      (now.contractActive && !file.hasSupplementMetadata ? '<p class="hs-notice blockers"><b>보완 이전 상태로 전체 복원</b>: 이 파일에는 보완 기록이 없습니다. 공휴일·근무표·순번 예약을 함께 되돌리고 현재 보완 기록을 제거합니다. 구형 프로그램의 쓰기 차단은 유지됩니다.</p>' : '') +
      (file.pendingChanges ? '<p class="hs-notice">이 파일의 <b>미저장 초안은 별도 보관된 내용</b>입니다. 복원에는 저장된 서버 상태만 사용하며 초안을 자동 적용하지 않습니다.</p>' : '') +
      '<p class="oa-sub">불러오기 직전 상태는 서버에 자동 백업으로 먼저 보관합니다. 잘못 불러왔다면 [실행 취소(되돌리기)] 또는 [자동 백업으로 되돌리기]로 돌아갈 수 있습니다.</p>';
  }
  function readFileText(file) {
    if (file && isFn(file.text)) return file.text();
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = event => resolve(event.target.result);
      reader.onerror = () => reject(reader.error || new Error('파일을 읽지 못했습니다.'));
      reader.readAsText(file);
    });
  }
  async function importBackupFile(file) {
    if (!file) return false;
    let data = null;
    try { data = JSON.parse(await readFileText(file)); } catch (_) { data = null; }
    const summary = backupFileSummary(data);
    if (!summary.ok) {
      if (summary.unsupported) { await notice('지원하지 않는 백업 버전', '<p>이 파일의 형식 또는 공휴일 보완 기록 버전을 지원하지 않습니다. 파일을 만든 버전의 프로그램에서 확인해 주세요. 지금 데이터는 그대로입니다.</p>'); return false; }
      await notice('백업 파일이 아닙니다', `<p>고른 파일(<b>${escapeHtml(file.name || '')}</b>)은 OnAir 백업 파일이 아니라서 불러오지 않았습니다. 지금 데이터는 그대로입니다.</p>` +
        '<p>[더보기 → 서버에 백업 저장(JSON)]으로 만든 서버 <b>back-up</b> 폴더의 백업 파일을 고르세요. 기존 PC에 저장한 백업 파일도 사용할 수 있습니다. 서버가 자동으로 남긴 사본으로 돌아가려면 [자동 백업으로 되돌리기]를 쓰세요.</p>');
      return false;
    }
    const now = currentSummary(), baseVersion = dataVersion, restoreLegacy = now.contractActive && !summary.hasSupplementMetadata;
    const go = await ask({title: '백업 파일 불러오기(JSON)', width: 640, html: importPreviewHtml(file.name || '', summary, now),
      buttons: [{label: restoreLegacy ? '보완 이전 상태로 전체 복원' : '이 파일로 덮어쓰기', value: true, danger: true}, {label: '취소', value: null}]});
    if (!go) return false;
    if (summary.hasSupplementMetadata || now.contractActive) {
      try {
        const response = await fetchWithAdminToken('/api/system/import-json', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({data, baseVersion, restoreLegacy})}, '백업 파일을 복원하려면 관리자 암호를 입력하세요.');
        const body = await readJson(response);
        if (!response.ok) {
          if (response.status === 409 && typeof fetchAllData === 'function') await fetchAllData();
          toast('⛔ ' + friendly(null, response, '백업 파일 복원', body), true); return false;
        }
        if (typeof discardUndo === 'function') discardUndo('백업 파일로 전체 복원');
        const loaded = isFn(W.refreshHolidaySupplementData) ? await W.refreshHolidaySupplementData() : await fetchAllData();
        if (loaded === false) { toast('복원은 완료됐지만 최신 자료를 불러오지 못했습니다. 연결을 확인한 뒤 새로고침해 주세요.', true); return true; }
        toast('📂 공휴일 보완 기록과 순번 예약을 포함해 백업 파일을 복원했습니다.'); return true;
      } catch (error) { toast('⛔ ' + friendly(error, null, '백업 파일 복원'), true); return false; }
    }
    memberList = data.members || data.memberList;
    scheduleMap = data.schedules || data.scheduleMap;
    commentMap = data.comments || data.commentMap || {};
    noDutyMap = data.noDuty || data.noDutyMap || {};
    absences = data.absences || [];
    absenceTypes = data.absenceTypes || absenceTypes;
    customHolidays = data.customHolidays || {};
    dutyRule = Object.assign(dutyRule, data.rules || data.dutyRule || {});
    globalMemo = typeof data.globalMemo === 'string' ? data.globalMemo : globalMemo;
    const restoredHistory = Array.isArray(data.history) ? data.history : (Array.isArray(data.changeHistory) ? data.changeHistory : null);
    if (restoredHistory) changeHistory = restoredHistory;
    const h = makeHistory('데이터 복구', (typeof todayStr !== 'undefined' && todayStr) || todayKey(),
      `백업 파일(${file.name || ''}, ${summary.exportedAt || '만든 시각 모름'}) 불러오기`);
    state.pendingSnapshotKind = 'import';
    const ok = await syncFullDataToBackend({members: memberList, schedules: scheduleMap, comments: commentMap, noDuty: noDutyMap,
      absences, absenceTypes, customHolidays, rules: dutyRule, globalMemo, historyBulk: restoredHistory || undefined, historyLog: h});
    if (typeof mergeHolidays === 'function') mergeHolidays();
    renderCalendar();
    if (typeof renderMemberModalList === 'function') renderMemberModalList();
    if (typeof renderAbsenceTypeUI === 'function') renderAbsenceTypeUI();
    if (ok) { await fetchAllData(); toast('📂 백업 파일을 불러와 저장했습니다. 기존 변경 이력은 그대로 남아 있습니다.'); }
    return ok;
  }
  if (typeof restoreSystem === 'function') {
    restoreSystem = async function(event) {
      const input = event && event.target;
      const file = input && input.files && input.files[0];
      if (input) input.value = '';
      if (!file) return false;
      try { return await importBackupFile(file); } catch (error) { toast('⛔ ' + friendly(error, null, '백업 파일 불러오기'), true); return false; }
    };
  }
  if (typeof downloadUnsaved === 'function') {
    const previousDownloadUnsaved = downloadUnsaved;
    downloadUnsaved = async function() {
      if (!supplementContractActive()) return previousDownloadUnsaved.apply(this, arguments);
      if (!unsavedList.length) return false;
      const pending = JSON.parse(JSON.stringify(unsavedList[0]));
      try {
        const response = await fetchWithAdminToken('/api/system/export-json', {}, '보완 기록을 포함해 내보내려면 관리자 암호를 입력하세요.');
        const data = await readJson(response);
        if (!response.ok || data.formatVersion !== 2 || data.holidaySupplements?.schema !== 1) {
          toast('⛔ 보완 기록을 포함한 서버 백업을 읽지 못해 파일을 저장하지 않았습니다. 연결을 확인해 주세요.', true); return false;
        }
        data.pendingChanges = pending;
        downloadBlob(`smart_duty_저장실패_${String(pending.at).replace(/[: ]/g, '-')}.json`, JSON.stringify(data, null, 2), 'application/json');
        toast('💾 저장된 서버 상태와 미저장 초안을 별도로 보관했습니다. 복원 시 초안은 자동 적용하지 않습니다.'); return true;
      } catch (error) { toast('⛔ ' + friendly(error, null, '미저장 초안 내보내기'), true); return false; }
    };
  }

  // ── 이력 CSV 기간 ───────────────────────────────────────────
  function presetRange(kind, now = new Date()) {
    const y = now.getFullYear(), m = now.getMonth() + 1;
    if (kind === 'year') return {start: `${y}-01-01`, end: `${y}-12-31`};
    if (kind === 'thisMonth') return {start: `${y}-${pad2(m)}-01`, end: `${y}-${pad2(m)}-${pad2(new Date(y, m, 0).getDate())}`};
    if (kind === 'lastMonth') {
      const py = m === 1 ? y - 1 : y, pm = m === 1 ? 12 : m - 1;
      return {start: `${py}-${pad2(pm)}-01`, end: `${py}-${pad2(pm)}-${pad2(new Date(py, pm, 0).getDate())}`};
    }
    return {start: '', end: ''};
  }
  function historyRangeError(start, end) {
    if (!start && !end) return '';
    if (!start || !end) return '시작일과 종료일을 모두 고르세요. 전체를 받으려면 [전체]를 누르세요.';
    if (!validKey(start) || !validKey(end)) return '날짜를 달력에서 다시 골라 주세요.';
    if (start > end) return '시작일이 종료일보다 늦습니다.';
    return '';
  }
  function historyRangeQuery(start, end) { return start && end ? `?start=${start}&end=${end}` : ''; }
  if (typeof downloadHistoryCsv === 'function') {
    downloadHistoryCsv = async function() {
      const initial = presetRange('year');
      const range = await ask({title: '변경 이력 CSV 내려받기', width: 520, focus: '#oa-hist-start', invalidFocus: '#oa-hist-start',
        html: '<div class="oa-presets" role="group" aria-label="빠른 기간 선택"><button type="button" data-preset="year">올해</button><button type="button" data-preset="lastMonth">지난달</button><button type="button" data-preset="thisMonth">이번 달</button><button type="button" data-preset="all">전체</button></div>' +
          `<div class="oa-row2"><label class="oa-field">시작일<input type="date" id="oa-hist-start" value="${initial.start}"></label><label class="oa-field">종료일<input type="date" id="oa-hist-end" value="${initial.end}"></label></div>` +
          '<p class="oa-sub">저장된 변경 이력 전체에서 이 기간만 내려받습니다(심사 제출용). 두 칸을 비우면 전체입니다.</p>',
        onOpen: node => node.querySelectorAll('[data-preset]').forEach(button => button.addEventListener('click', () => {
          const r = presetRange(button.getAttribute('data-preset'));
          node.querySelector('#oa-hist-start').value = r.start;
          node.querySelector('#oa-hist-end').value = r.end;
        })),
        validate: (value, node) => historyRangeError(node.querySelector('#oa-hist-start').value, node.querySelector('#oa-hist-end').value),
        result: (value, node) => ({start: node.querySelector('#oa-hist-start').value, end: node.querySelector('#oa-hist-end').value}),
        buttons: [{label: '내려받기', value: true, primary: true}, {label: '취소', value: null}]});
      if (!range) return false;
      const link = doc.createElement('a');
      link.href = '/api/duty/history-export' + historyRangeQuery(range.start, range.end);
      link.download = '';
      doc.body.appendChild(link); link.click(); doc.body.removeChild(link);
      toast('📄 변경 이력 CSV를 내려받습니다.');
      return true;
    };
  }
  // 이름 고르기(번호 입력 대신 목록). 본 스크립트의 promptMyName 이 다른 보조 창이 없을 때 쓴다.
  function pickName({title, label, names, current}) {
    return ask({title, width: 420, focus: '#oa-pick-name',
      html: `<label class="oa-field">${escapeHtml(label || '이름')}<select id="oa-pick-name"><option value="">이름을 고르세요</option>` +
        (names || []).map(n => `<option value="${escapeHtml(n)}"${n === current ? ' selected' : ''}>${escapeHtml(n)}</option>`).join('') + '</select></label>',
      validate: (value, node) => node.querySelector('#oa-pick-name').value ? '' : '이름을 고르세요.', invalidFocus: '#oa-pick-name',
      result: (value, node) => node.querySelector('#oa-pick-name').value,
      buttons: [{label: '선택', value: true, primary: true}, {label: '취소', value: null}]});
  }

  // ── 달력 꾸미기: ✎ 버튼, 미배정 표시, 마감 안내, 메뉴 이름 ─────────
  function unassignedDays(year, month) {
    const prefix = `${year}-${pad2(month)}`;
    const keys = Object.keys(scheduleMap).filter(k => k.startsWith(prefix));
    if (!keys.some(k => (scheduleMap[k] || []).length)) return [];
    const out = [];
    const dim = new Date(year, month, 0).getDate();
    for (let d = 1; d <= dim; d++) {
      const k = `${prefix}-${pad2(d)}`;
      if (!noDutyMap[k] && dayNeedsWorker(k)) out.push(k);
    }
    return out;
  }
  function setLabel(button, text) {
    if (!button) return;
    const nodes = [...button.childNodes].filter(n => n.nodeType === 3 && n.textContent.trim());
    if (nodes.length) { nodes.forEach((n, i) => { n.textContent = i === nodes.length - 1 ? text : ''; }); }
    else button.appendChild(doc.createTextNode(text));
  }
  function decorateCalendar() {
    if (!doc || !isFn(doc.querySelector)) return;
    const monthKey = currentMonthKey();
    const closed = isClosedMonth(monthKey);
    const isAdminMode = admin();
    const closeButton = doc.querySelector('button[onclick="toggleMonthClose()"]');
    if (closeButton) {
      setLabel(closeButton, closed ? '이 달 마감 해제' : '이 달 마감하기');
      closeButton.title = closed ? `${ymLabel(monthKey)} 마감을 해제합니다. 해제 사유가 필요합니다.` : `${ymLabel(monthKey)} 당직표를 확정해 더 바꾸지 못하게 합니다.`;
    }
    const autoButton = doc.querySelector('button[onclick="generateScheduleByRules()"]');
    if (autoButton) {
      autoButton.disabled = isAdminMode && closed;
      autoButton.title = isAdminMode && closed ? `${ymLabel(monthKey)}은 마감되어 자동 배정할 수 없습니다. [더보기 → 이 달 마감 해제] 후 다시 시도하세요.` : '';
    }
    const table = doc.querySelector('.cal-table');
    let banner = $('oa-closed-banner');
    if (table && !banner) {
      banner = doc.createElement('div');
      banner.id = 'oa-closed-banner';
      banner.className = 'oa-closed-banner';
      banner.setAttribute('role', 'status');
      table.parentNode.insertBefore(banner, table);
    }
    if (banner) {
      banner.hidden = !(isAdminMode && closed);
      if (isAdminMode && closed) {
        banner.innerHTML = `<span>${escapeHtml(ymLabel(monthKey))}은 마감되었습니다 — 고치려면</span> <button type="button" class="btn-main" data-oa-reopen>마감 해제</button>`;
        banner.querySelector('[data-oa-reopen]').addEventListener('click', () => setMonthClosed(monthKey, false));
      } else banner.innerHTML = '';
    }
    const missing = new Set(isAdminMode ? unassignedDays(curYear, curMonth) : []);
    doc.querySelectorAll('#calendar-tbody td[data-date-key]').forEach(td => {
      const k = td.getAttribute('data-date-key');
      td.classList.toggle('oa-closed-cell', closed);
      if (!isAdminMode) return;
      td.title = closed ? `${ymLabel(monthKey)}은 마감되어 보기만 할 수 있습니다` : '칸을 누르면 이 날 당직을 고칠 수 있습니다';
      if (closed) td.querySelectorAll('.worker-tag[draggable]').forEach(tag => tag.setAttribute('draggable', 'false'));
      const dayNumber = td.querySelector('.day-number');
      if (!closed && dayNumber) {
        // Keep keyboard editing on the existing date number without a separate edit icon.
        dayNumber.tabIndex = 0;
        dayNumber.setAttribute('role', 'button');
        dayNumber.setAttribute('aria-label', `${shortDate(k)}(${weekday(k)}) 당직 고치기`);
        dayNumber.onkeydown = event => {
          if (event.key !== 'Enter' && event.key !== ' ') return;
          event.preventDefault(); event.stopPropagation(); openDateModal(k);
        };
      }
      const old = td.querySelector('.cell-unassigned');
      if (missing.has(k) && !old) {
        const mark = doc.createElement('span');
        mark.className = 'cell-unassigned';
        mark.textContent = '미배정';
        mark.title = '당직자가 비어 있습니다. 칸을 눌러 채우세요';
        (td.querySelector('.worker-box') || td).appendChild(mark);
      } else if (!missing.has(k) && old) old.remove();
    });
  }
  if (typeof renderCalendar === 'function') {
    const previousRender = renderCalendar;
    renderCalendar = function(...args) {
      const result = previousRender.apply(this, args);
      try { decorateCalendar(); } catch (error) { console.warn('달력 표시 보강 실패', error); }
      return result;
    };
  }
  withDisplayGender('renderCalendar');
  withDisplayGender('renderStats');
  withDisplayGender('exportRangeCsv');
  withDisplayGender('exportMonthlyCsv');

  // ── 화면 문구·스타일 ─────────────────────────────────────────
  const STYLE = `
.oa-admin-dialog{z-index:2050}
.oa-admin-dialog .modal-body{display:flex;flex-direction:column;max-height:calc(100vh - 32px);overflow:auto}
.oa-admin-dialog .oa-body p{margin:0 0 10px}
.oa-admin-dialog .oa-list{margin:6px 0 10px;padding-left:18px}
.oa-admin-dialog .oa-list li{margin:4px 0}
.oa-field{display:flex;flex-direction:column;gap:6px;font-size:13px;font-weight:700;margin-top:8px}
.oa-field textarea,.oa-field select,.oa-field input{width:100%}
.oa-req{color:var(--holiday-text);font-weight:600}
.oa-row2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.oa-presets{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px}
.oa-mono{font-family:monospace;font-size:11px}
.oa-diff-cell{color:var(--holiday-text);font-weight:700}
.oa-sticky-foot{position:sticky;bottom:-24px;z-index:3;background:var(--card);margin-bottom:-24px !important;padding:12px 0 18px !important;border-top:1px solid var(--border);display:flex;justify-content:flex-end;gap:8px;flex-wrap:wrap}
#scope-modal .modal-body,#rotation-start-modal .modal-body,#assign-preview-modal .modal-body,#rule-modal .modal-body{max-height:calc(100vh - 32px) !important;overflow-y:auto}
#assign-preview-modal .modal-body{max-height:calc(100vh - 32px) !important}
.oa-action-toast{position:fixed;left:50%;bottom:128px;transform:translateX(-50%);z-index:2100;display:none;pointer-events:none;align-items:center;gap:10px;flex-wrap:wrap;max-width:min(92vw,760px);padding:10px 12px 10px 16px;border-radius:14px;background:var(--text-main);color:var(--bg);box-shadow:var(--shadow-2,0 8px 24px rgba(0,0,0,.25));font-size:13px;font-weight:600}
.oa-action-toast button{font-size:12px;min-height:30px;padding:4px 12px;border-radius:999px;cursor:pointer;pointer-events:auto}
/* 3.18.0 QA: 알림 상자 자체는 클릭·끌어 놓기를 가로채지 않는다(버튼만 누를 수 있다). 끄는 동안에는 버튼까지 통과시킨다. */
body.oa-dragging .oa-action-toast,body.oa-dragging .oa-action-toast button,body.oa-dragging #toast,body.oa-dragging #toast *,body.oa-dragging #oa-toast-errors,body.oa-dragging #oa-toast-errors *{pointer-events:none !important}
.oa-action-toast .oa-at-close{background:transparent;color:inherit;border:0;min-height:26px;padding:2px 6px}
.oa-closed-banner{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:6px 0 8px;padding:8px 12px;border-radius:10px;background:var(--vacation-bg,#fff4e5);color:var(--holiday-text);font-weight:700;font-size:13px}
.oa-closed-banner[hidden]{display:none}
.oa-closed-banner button{font-size:12px;padding:3px 12px;min-height:28px}
#calendar-tbody td.oa-closed-cell{cursor:default}
#date-modal.oa-locked #manual-assign-container,#date-modal.oa-locked #modal-noduty-btn,#date-modal.oa-locked #btn-exec-swap,#date-modal.oa-locked #btn-exec-sub{opacity:.45;pointer-events:none}
#calendar-tbody .day-header-cell:has(.day-number[role="button"]){min-height:22px}
.day-number[role="button"]:focus-visible{outline:2px solid var(--primary);outline-offset:2px;border-radius:4px}
.cell-unassigned{display:inline-block;align-self:flex-start;font-size:10px;font-weight:700;line-height:1.4;padding:0 6px;border-radius:6px;border:1px dashed var(--holiday-text);color:var(--holiday-text)}
.oa-scope-closed{display:block;color:var(--holiday-text);font-style:normal;font-weight:700}
.scope-detail{margin:0 0 0 30px;font-size:11px;color:var(--text-muted)}
.scope-detail summary{cursor:pointer;color:var(--primary);font-weight:700;font-size:11px}
.oa-pv-delta{margin:10px 0;font-size:12px;line-height:1.8}
.oa-pv-delta .oa-up{color:var(--primary);font-weight:700}
.oa-pv-delta .oa-down{color:var(--holiday-text);font-weight:700}
.oa-pv-filter{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:700;margin:2px 0 8px}
.oa-pv-month{margin:0 0 8px;border:1px solid var(--border);border-radius:10px;padding:6px 10px}
.oa-pv-month>summary{cursor:pointer;font-size:13px;padding:4px 0}
.oa-pv-month table{width:100%;font-size:12px}
.oa-pv-month td,.oa-pv-month th{padding:6px 8px;text-align:left;border-bottom:1px solid var(--border)}
mark.oa-diff-add{background:color-mix(in srgb,var(--primary) 22%,transparent);color:inherit;font-weight:800;border-radius:4px;padding:0 3px}
mark.oa-diff-del{background:color-mix(in srgb,var(--holiday-text) 18%,transparent);color:inherit;text-decoration:line-through;border-radius:4px;padding:0 3px}
.oa-pv-kind{display:inline-block;font-size:11px;font-weight:700;padding:0 6px;border-radius:6px;background:var(--surface)}
.oa-kind-당직없음추가,.oa-kind-해제{color:var(--holiday-text)}
.oa-pv-off{color:var(--holiday-text);font-weight:700}
.oa-pv-notes{margin-top:8px;font-size:12px}
.oa-chips{display:flex;flex-direction:column;gap:6px;margin:2px 0 4px}
.oa-chip-list{display:flex;flex-wrap:wrap;gap:4px;margin:0;padding:0;list-style:none}
.oa-chip{display:inline-flex;align-items:center;gap:2px;padding:2px 4px 2px 6px;border-radius:999px;background:var(--surface);border:1px solid var(--border);font-size:12px;cursor:grab}
.oa-chip-no{font-size:10px;color:var(--text-muted);min-width:14px}
.oa-chip-name{font-weight:700}
.oa-chip button{border:0;background:transparent;font-size:10px;padding:0 3px;min-height:20px;cursor:pointer;color:var(--text-muted)}
.oa-chip button:disabled{opacity:.3;cursor:default}
.oa-chip-bad{border-color:var(--holiday-text);background:color-mix(in srgb,var(--holiday-text) 10%,transparent)}
.oa-chip-bad em{font-style:normal;font-size:10px;color:var(--holiday-text);font-weight:700;margin-left:2px}
.oa-chip-empty{font-size:12px;color:var(--text-muted)}
.oa-chips select{font-size:12px;padding:3px 6px;max-width:180px}
@media(max-width:600px){.oa-row2{grid-template-columns:1fr}.oa-action-toast{bottom:70px}}`;
  function injectStyle() {
    if (!doc || !isFn(doc.createElement) || $('oa-admin-style')) return;
    const style = doc.createElement('style');
    style.id = 'oa-admin-style';
    style.textContent = STYLE;
    (doc.head || doc.body).appendChild(style);
  }
  function stickyFooters() {
    ['scope-modal', 'rotation-start-modal', 'assign-preview-modal', 'rule-modal'].forEach(id => {
      const body = $(id) && $(id).querySelector('.modal-body');
      if (!body) return;
      const footer = [...body.children].reverse().find(el => /flex-end/.test(el.getAttribute('style') || '') && el.querySelector('button'));
      if (footer) footer.classList.add('oa-sticky-foot');
    });
  }
  function relabelMenus() {
    const restoreLabel = doc.querySelector('label[for="restore-file"]');
    if (restoreLabel) {
      setLabel(restoreLabel, '백업 파일 불러오기(JSON)');
      restoreLabel.title = '서버의 back-up 폴더 또는 기존 PC의 백업 파일을 불러옵니다. 먼저 미리 보여 드립니다.';
      if (!$('oa-auto-backup-menu')) {
        const button = doc.createElement('button');
        button.id = 'oa-auto-backup-menu';
        button.type = 'button';
        button.className = 'admin-only';
        button.title = '서버가 작업 직전·하루 첫 저장 전에 남긴 사본으로 되돌립니다. 먼저 미리 보여 드립니다.';
        button.innerHTML = '<svg class="ico"><use href="#i-refresh"/></svg>자동 백업으로 되돌리기';
        button.addEventListener('click', () => openAutoBackupDialog());
        restoreLabel.parentNode.insertBefore(button, restoreLabel.nextSibling);
        button.style.display = admin() ? '' : 'none';
      }
    }
    const backupButton = doc.querySelector('button[onclick="backupSystem()"]');
    if (backupButton) {
      setLabel(backupButton, '서버에 백업 저장(JSON)');
      backupButton.title = '공유 데이터 폴더의 back-up에 백업 파일을 저장합니다.';
    }
  }
  function init() {
    if (!doc || !isFn(doc.querySelector)) return;
    injectStyle();
    stickyFooters();
    relabelMenus();
  }
  try {
    if (doc && doc.readyState === 'loading' && isFn(doc.addEventListener)) doc.addEventListener('DOMContentLoaded', init);
    else init();
  } catch (error) { console.warn('관리자 화면 보강 실패', error); }

  W.OnAirAdmin = Object.assign(W.OnAirAdmin || {}, {
    ask, confirmDiscard, actionToast, pickName, setMonthClosed, openAutoBackupDialog, undoToSnapshot, performRestore,
    importBackupFile, previewMonth, reassignFrom,
    _test: {state, closedBannerText, isClosedDate, pendingDateEdits, buildPreviewModel, previewHtml, ruleDiffText, rosterProblems,
      backupFileSummary, currentSummary, importPreviewHtml, presetRange, historyRangeError, historyRangeQuery, futureDuties,
      displayGenderOf, unassignedDays, dragDescription, restorePreviewHtml, autoSnapshotKind, noteSnapshot, decorateCalendar, localStamp}
  });
})();
