/* Server-owned holiday changes. A review is read-only; only Apply can commit it. */
(() => {
  'use strict';
  const W = window, $ = id => document.getElementById(id), ID = 'holiday-supplement-modal';
  const escape = value => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const copy = value => JSON.parse(JSON.stringify(value));
  const poolLabel = key => key === 'weekend|M' ? '주말 공유 순번 · 남성' : '일반 공휴일 순번 · 남성';
  const reconcileCodes = ['BASIS_RECONCILE_REQUIRED', 'BASIS_REVIEW_REQUIRED', 'BASIS_RECONFIRM_REQUIRED'];
  let modal = null, active = null, serial = 0;
  function metadata() { return typeof W.getHolidaySupplementState === 'function' ? W.getHolidaySupplementState() : {events: [], bases: []}; }
  function hasActiveSupplement(key) { return (metadata().events || []).some(event => event.date_key === key && ['ACTIVE', 'NEEDS_REVIEW'].includes(event.state)); }
  function weekday(key) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(key))) return false;
    const date = new Date(key + 'T00:00:00Z');
    return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === key && date.getUTCDay() > 0 && date.getUTCDay() < 6;
  }
  function calendarOnlyReason(intent, key, options = {}) {
    if (hasActiveSupplement(key) || !['SET_CUSTOM', 'RESTORE_BASE'].includes(intent) || !weekday(key) ||
        (typeof finalHolidays !== 'undefined' && finalHolidays[key])) return '';
    if (options.is_major || (intent === 'RESTORE_BASE' && typeof BASE_HOLIDAYS !== 'undefined' && BASE_HOLIDAYS[key]?.isMajor)) return '명절은 기존 명절 근무표에서 배정합니다.';
    if (typeof dutyRule !== 'undefined' && dutyRule.algoType !== 'FIXED_ORDER') return '현재 배정 방식에서는 고정 순번 보완을 사용하지 않습니다.';
    if (typeof todayStr !== 'undefined' && key <= todayStr) return '오늘과 지난 날짜에는 자동 보완을 적용하지 않습니다.';
    if (Number(key.slice(0, 4)) < 2023 || Number(key.slice(0, 4)) > 2029) return '이 연도는 자동 보완의 공휴일 검증 범위 밖입니다.';
    if (typeof scheduleMap !== 'undefined' && !(scheduleMap[key] || []).length) return '이 날짜에는 아직 유지할 당직 배정이 없습니다.';
    return '';
  }
  function shouldPreview(intent, key, options = {}) {
    if (hasActiveSupplement(key)) return true;
    return ['SET_CUSTOM', 'RESTORE_BASE'].includes(intent) && weekday(key) && !(typeof finalHolidays !== 'undefined' && finalHolidays[key]) && !calendarOnlyReason(intent, key, options);
  }
  function uuid() {
    if (W.crypto?.randomUUID) return W.crypto.randomUUID();
    const bytes = new Uint8Array(16); W.crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  function busy() { return active && ['applying', 'reloading'].includes(active.phase); }
  function close() {
    if (!active || busy()) return false;
    finish(false); return true;
  }
  function finish(result) {
    const session = active;
    if (!session) return;
    active = null; serial++;
    modal.classList.remove('active');
    if (session.trigger?.isConnected) session.trigger.focus({preventScroll: true});
    session.resolve(result);
  }
  function ensureModal() {
    if (modal) return;
    modal = document.createElement('div'); modal.id = ID; modal.className = 'modal oa-modal hs-modal';
    modal.setAttribute('role', 'dialog'); modal.setAttribute('aria-modal', 'true'); modal.setAttribute('aria-labelledby', 'hs-title');
    modal.innerHTML = '<div class="modal-body hs-dialog"><div class="modal-head"><div><div class="hs-eyebrow">공휴일 관리</div><h2 class="modal-title" id="hs-title">추가 공휴일 미리보기</h2></div>' +
      '<button type="button" class="oa-close" id="hs-close" aria-label="미리보기 닫기">✕</button></div>' +
      '<div id="hs-content" class="hs-content"></div><p id="hs-feedback" class="hs-feedback" role="status" aria-live="polite"></p>' +
      '<div class="hs-footer"><button type="button" id="hs-cancel">취소</button><button type="button" id="hs-refresh">최신 내용 다시 미리보기</button>' +
      '<button type="button" id="hs-apply" class="btn-main" disabled>확인 후 적용</button></div></div>';
    document.body.appendChild(modal);
    $('hs-close').addEventListener('click', close); $('hs-cancel').addEventListener('click', close);
    $('hs-refresh').addEventListener('click', () => refresh()); $('hs-apply').addEventListener('click', apply);
    modal.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); return; }
      if (event.key !== 'Tab') return;
      const fields = [...modal.querySelectorAll('button,select,input,a[href],[tabindex]')].filter(field => !field.disabled && field.tabIndex >= 0 && field.getClientRects().length);
      if (!fields.length) { event.preventDefault(); return; }
      if (event.shiftKey && (document.activeElement === fields[0] || !modal.contains(document.activeElement))) { event.preventDefault(); fields.at(-1).focus(); }
      else if (!event.shiftKey && (document.activeElement === fields.at(-1) || !modal.contains(document.activeElement))) { event.preventDefault(); fields[0].focus(); }
    });
    if (typeof MODAL_CLOSERS !== 'undefined') MODAL_CLOSERS.push([ID, close]);
  }
  function slotText(slot) {
    return [slot.name, slot.role, slot.isSubstitute ? '대체 근무' : '', slot.zeroScore ? '0점 유지' : ''].filter(Boolean).join(' · ');
  }
  function slotsHtml(slots) { return (slots || []).length ? `<ul class="hs-workers">${slots.map(slot => `<li>${escape(slotText(slot))}</li>`).join('')}</ul>` : '<p class="hs-muted">배정 없음</p>'; }
  function occurrence(value) {
    if (!value) return '확인 자료 없음';
    return [value.name, value.date_key, Number.isFinite(value.cycle) ? `${value.cycle}회차` : '', Number.isFinite(value.ordinal) ? `${value.ordinal}번째` : Number.isFinite(value.position) ? `위치 ${value.position + 1}` : ''].filter(Boolean).join(' · ');
  }
  function notices(list, kind) {
    if (!Array.isArray(list) || !list.length) return '';
    return `<section class="hs-notice ${kind}"><h3>${kind === 'blockers' ? '적용 전 확인 필요' : '함께 확인해 주세요'}</h3><ul>${list.map(item => `<li>${escape(item.message || item.code || item)}</li>`).join('')}</ul></section>`;
  }
  function planHtml(plan) {
    const basis = plan.rotationBasis || {}, before = plan.before || {}, after = plan.after || {};
    const key = after.date_key || before.date_key || active.draft.date_key || '';
    const year = basis.year || active.draft.year || key.slice(0, 4);
    const reconcile = active.draft.holidayIntent === 'RECONCILE';
    const blocked = (plan.blockers || []).some(item => item.code === 'BASIS_REBASE_REQUIRED');
    const skip = basis.skipped_candidates || [];
    const changes = (plan.affectedDates || []).length;
    let html = `<p class="hs-summary"><b>${escape(reconcile ? `${year}년 순번 기준 재확인` : key)}</b>${after.holiday?.name ? ` · ${escape(after.holiday.name)}` : ''}</p>`;
    if (reconcile) html += '<div class="hs-notice"><b>기존 배정은 유지합니다.</b><p>선택한 연도의 1월 1일 시작점을 고정해 기존 예약을 채택합니다. 이 경계에서는 전년도 추가 소비와의 자동 연속 순번을 적용하지 않습니다. 실제 근무표는 바뀌지 않습니다.</p></div>';
    else {
      const caption = plan.effect === 'ADD' ? '기존 여성 유지 · 남성 1명 보완' : plan.effect === 'REMOVE' ? '현재 배정 확인 · 보완 해제' : '공휴일 설정 변경';
      html += `<h3>${caption}</h3><div class="hs-comparison"><section><h4>변경 전</h4>${slotsHtml(before.workers)}</section><section><h4>변경 후</h4>${slotsHtml(after.workers)}</section></div>`;
      if (plan.candidate) html += `<p class="hs-candidate"><span>추가 남성</span><strong>${escape(plan.candidate.name)}</strong><small>${escape(occurrence({...plan.candidate, name: ''}))}</small></p>`;
      html += `<p class="hs-preserved">변경 날짜 <b>${changes}개</b> · 그 외 배정 <b>${escape(plan.preservedDatesCount ?? '—')}일 유지</b></p>`;
    }
    html += notices(plan.blockers, 'blockers');
    if (!reconcile && basis.pool_key && (plan.blockers || []).some(item => reconcileCodes.includes(item.code))) html += '<button type="button" id="hs-reconcile">순번 기준 먼저 재확인</button>';
    if (blocked) html += '<p class="hs-notice blockers">기존 표와 보완 기록은 보존됩니다. 같은 해 순번 명단이나 정규 예약이 바뀐 기준으로 보완을 옮기는 기능은 이번 버전에서 지원하지 않습니다. 이전 기준으로 되돌린 뒤 다시 확인하거나, 별도 관리 절차로 처리해 주세요.</p>';
    if (basis.pool_key) {
      html += `<section class="hs-basis"><h3>${escape(year)}년 연간 예약 기준</h3><dl><div><dt>순번 묶음</dt><dd>${escape(poolLabel(basis.pool_key))}</dd></div><div><dt>마지막 정규 예약</dt><dd>${escape(occurrence(basis.lastReservation))}</dd></div>` +
        `<div><dt>기준 상태</dt><dd>${plan.eligible ? '서버 검증 완료' : '미확정 · 차단 사유 확인'}${basis.revision ? ` · ${escape(basis.revision)}판` : ''}</dd></div></dl>`;
      if (skip.length) html += `<details open><summary>건너뛴 후보 ${skip.length}명</summary><ul>${skip.map(item => `<li><b>${escape(occurrence(item))}</b> — ${escape(item.message || item.code)}</li>`).join('')}</ul></details>`;
      html += '</section>';
    }
    if ((plan.impacts || []).length) html += `<section class="hs-impacts"><h3>통계·신청 영향</h3><ul>${plan.impacts.map(item => `<li>${escape(item.message || item.code)}</li>`).join('')}</ul><p class="hs-muted">순번 예약과 실제 근무 통계는 별도로 계산합니다. 대기 신청은 삭제하지 않고 승인 전에 다시 확인합니다.</p></section>`;
    html += notices(plan.warnings, 'warnings');
    if (reconcile && Array.isArray(basis.sequence_snapshot) && basis.sequence_snapshot.length && !blocked) {
      const chosen = active.draft.anchor, order = basis.sequence_snapshot, half = order.length / 2;
      const paired = basis.pool_key === 'weekend|M' && half > 0 && half % 2 === 0 && order.slice(0, half).every((name, i) => order[i ^ 1] === order[i + half]);
      const options = order.map((name, position) => {
        const cycle = paired ? Math.floor(position / half) + 1 : 1;
        return `<option value="${position}"${chosen === position ? ' selected' : ''}>${escape(name)} · 위치 ${position + 1} · ${cycle}회차</option>`;
      }).join('');
      html += `<details class="hs-anchor"><summary>1월 1일 시작점 직접 확인</summary><label for="hs-anchor">시작 위치 · 같은 이름도 회차와 위치로 구분합니다</label><select id="hs-anchor"><option value="">서버가 확인한 기존 시작점 유지</option>${options}</select><button type="button" id="hs-anchor-preview">선택한 시작점 미리보기</button></details>`;
    }
    return html;
  }
  function render() {
    if (!active) return;
    const loading = active.phase === 'previewing', saving = busy(), plan = active.plan;
    $('hs-title').textContent = active.draft.holidayIntent === 'RECONCILE' ? '순번 기준 재확인' : plan?.effect === 'REMOVE' ? '공휴일 보완 해제 미리보기' : '추가 공휴일 미리보기';
    $('hs-content').innerHTML = loading ? '<p class="hs-loading" role="status">현재 근무표와 연간 순번을 확인하고 있습니다…</p>' : plan ? planHtml(plan) : '<p class="hs-muted">공휴일과 근무표는 저장하지 않았습니다. 다시 미리보기를 눌러 확인해 주세요.</p>';
    $('hs-feedback').textContent = active.message || '';
    $('hs-feedback').className = 'hs-feedback' + (['error', 'conflict'].includes(active.phase) ? ' hs-error' : '');
    $('hs-apply').disabled = loading || saving || active.phase === 'conflict' || !plan?.eligible || !!plan.blockers?.length || !plan.reviewDigest;
    $('hs-apply').textContent = active.committed ? '최신 상태 다시 불러오기' : saving ? '저장 중…' : active.retry ? '동일 작업 다시 확인' : plan?.effect === 'RECONCILE' ? '기존 배정 유지로 기준 적용' : plan?.effect === 'REMOVE' ? '확인 후 보완 해제' : plan?.effect === 'NOOP' ? '확인' : '확인 후 적용';
    if (active.committed && !saving) $('hs-apply').disabled = false;
    $('hs-refresh').disabled = loading || saving || !!active.retry || !!active.committed;
    $('hs-cancel').disabled = saving; $('hs-close').disabled = saving;
    $('hs-cancel').textContent = active.retry || active.committed ? '닫기' : '취소';
    modal.setAttribute('aria-busy', String(loading || saving));
    if (active.draft.holidayIntent === 'RECONCILE' && plan?.rotationBasis?.sequence_snapshot?.length) {
      const select = $('hs-anchor'), button = $('hs-anchor-preview');
      if (select && button) {
        select.disabled = saving || !!active.retry; button.disabled = saving || !!active.retry;
        select.addEventListener('change', () => { $('hs-apply').disabled = true; active.anchorDirty = true; });
        button.addEventListener('click', () => {
          if (select.value === '') delete active.draft.anchor;
          else active.draft.anchor = Number(select.value);
          refresh();
        });
      }
    }
    if (active.draft.holidayIntent !== 'RECONCILE' && plan?.rotationBasis?.pool_key && (plan.blockers || []).some(item => reconcileCodes.includes(item.code))) {
      $('hs-reconcile')?.addEventListener('click', () => {
        if (!active || busy()) return;
        const basis = active.plan.rotationBasis;
        active.returnDraft = copy(active.draft);
        active.draft = {holidayIntent: 'RECONCILE', year: basis.year, pool_key: basis.pool_key, expected_basis_revision: basis.revision, reconcilePolicy: 'PRESERVE_EXISTING'};
        refresh();
      });
    }
  }
  function errorText(body, fallback) {
    const detail = body?.detail;
    return typeof detail === 'string' ? detail : detail?.message || body?.message || fallback;
  }
  function friendlyError(error) {
    if (/[가-힣]/.test(String(error?.message || ''))) return error.message;
    return typeof friendlyFetchError === 'function' ? friendlyFetchError(error, null, '공휴일 미리보기') : '서버 응답을 확인하지 못했습니다. 연결 상태를 확인해 주세요.';
  }
  async function request(path, body) {
    const response = await fetchWithAdminToken('/api/duty/holiday-supplement/' + path,
      {method: 'POST', headers: {'Content-Type': 'application/json', 'X-OnAir-Data-Contract': '1'}, body: JSON.stringify(body)}, '공휴일 변경을 확인하려면 관리자 암호를 입력하세요.');
    let result = {}; try { result = await response.json(); } catch (_) { /* handled below */ }
    if (!response.ok) { const error = new Error(errorText(result, response.status === 401 ? '관리자 인증을 취소했습니다. 저장하지 않았습니다.' : '서버 응답을 확인하지 못했습니다.')); error.status = response.status; throw error; }
    return result;
  }
  async function refresh() {
    if (!active || busy() || active.retry || active.committed) return false;
    const session = active, token = ++serial;
    session.phase = 'previewing'; session.plan = null; session.message = ''; session.operationId = null; session.anchorDirty = false;
    session.reviewedDraft = {...copy(session.draft), baseVersion: typeof dataVersion === 'number' ? dataVersion : 0};
    render();
    try {
      const plan = await request('preview', session.reviewedDraft);
      if (active !== session || serial !== token) return false;
      if (plan.schema !== 1 || !plan.reviewDigest || !Number.isFinite(plan.baseVersion)) throw new Error('지원하지 않는 미리보기 응답입니다. 프로그램 버전을 확인해 주세요.');
      session.plan = plan; session.phase = 'ready'; session.operationId = uuid();
      render(); $('hs-cancel').focus({preventScroll: true}); return true;
    } catch (error) {
      if (active !== session || serial !== token) return false;
      session.phase = 'error'; session.message = friendlyError(error); render(); return false;
    }
  }
  async function reload() {
    if (typeof W.refreshHolidaySupplementData === 'function') {
      const result = await W.refreshHolidaySupplementData(); if (result === false) throw new Error('최신 자료를 불러오지 못했습니다.');
    } else if (typeof fetchAllData !== 'function' || await fetchAllData() === false) throw new Error('최신 자료를 불러오지 못했습니다.');
  }
  async function apply() {
    if (!active || busy()) return false;
    const session = active, plan = session.plan;
    if (!session.committed && (session.phase === 'conflict' || session.anchorDirty || !plan?.eligible || plan.blockers?.length || !plan.reviewDigest)) return false;
    session.phase = session.committed ? 'reloading' : 'applying'; session.message = ''; render();
    try {
      if (!session.committed) {
        session.receipt = plan.effect === 'NOOP' ? {status: 'NOOP'} : await request('apply', {operationId: session.operationId, draft: session.reviewedDraft, baseVersion: plan.baseVersion, reviewDigest: plan.reviewDigest});
        session.committed = true;
      }
      session.phase = 'reloading'; render(); await reload();
      if (typeof discardUndo === 'function') discardUndo('공휴일 변경 후 최신 상태 확인');
      if (session.returnDraft) {
        session.draft = session.returnDraft; delete session.returnDraft;
        session.committed = false; session.retry = false; session.receipt = null; session.phase = 'ready';
        await refresh();
        session.message = '순번 기준을 확인했습니다. 공휴일 보완 내용을 다시 확인한 뒤 적용해 주세요.'; render(); return true;
      }
      if (typeof session.options.onCommitted === 'function') await session.options.onCommitted(session.receipt);
      const historical = session.receipt?.historicalResult;
      const message = historical ? '이전에 처리한 작업입니다. 현재 상태를 다시 불러왔습니다.' : plan.effect === 'RECONCILE' ? '기존 배정을 유지하고 순번 기준을 확인했습니다.' : plan.effect === 'REMOVE' ? '해당 날짜 보완 해제 완료. 최신 근무표를 불러왔습니다.' : plan.effect === 'NOOP' ? '이미 반영된 상태입니다. 최신 근무표를 불러왔습니다.' : plan.effect === 'LABEL_ONLY' ? '공휴일 설정을 반영했습니다. 기존 배정은 유지했습니다.' : '해당 날짜 보완 완료. 다른 날짜의 배정은 유지했습니다.';
      if (typeof showToast === 'function') showToast(message);
      finish(true); return true;
    } catch (error) {
      if (error.status === 409 && !session.committed) {
        session.phase = 'conflict'; session.retry = false; session.operationId = null;
        session.message = `${friendlyError(error)} 최신 내용 다시 미리보기로 변경 사항을 확인하세요.`;
      } else if (error.status >= 400 && error.status < 500 && error.status !== 401 && !session.committed) {
        session.phase = 'error'; session.retry = false; session.plan = null;
        session.message = `${friendlyError(error)} 저장하지 않았습니다. 최신 내용 다시 미리보기로 확인해 주세요.`;
      } else {
        session.phase = 'error'; session.retry = !session.committed && error.status !== 401;
        session.message = session.committed ? '저장은 완료됐지만 최신 화면을 불러오지 못했습니다. 최신 상태 다시 불러오기를 눌러 주세요.' : error.status === 401 ? friendlyError(error) : `${friendlyError(error)} 처리 결과가 불확실할 수 있습니다. 동일 작업 다시 확인을 누르면 중복 저장 없이 결과를 확인합니다.`;
      }
      render(); return false;
    }
  }
  function open(draft, options = {}) {
    if (typeof SYSTEM_MODE !== 'undefined' && SYSTEM_MODE !== 'ADMIN') return Promise.resolve(false);
    if (active) { W.OnAirUI?.raise(ID); return Promise.resolve(false); }
    ensureModal();
    const done = new Promise(resolve => { active = {draft: copy(draft), options, resolve, trigger: options.restoreFocus || document.activeElement, phase: 'previewing', plan: null, message: '', retry: false}; });
    modal.classList.add('active'); W.OnAirUI?.raise(ID); $('hs-close').focus({preventScroll: true});
    refresh(); return done;
  }
  function openReconcile(input, options = {}) {
    const draft = {holidayIntent: 'RECONCILE', year: input.year, pool_key: input.pool_key,
      expected_basis_revision: input.expected_basis_revision, reconcilePolicy: 'PRESERVE_EXISTING'};
    if (input.anchor != null) draft.anchor = typeof input.anchor === 'object' ? input.anchor.position : input.anchor;
    return open(draft, options);
  }
  W.HolidaySupplement = {open, openReconcile, close, refresh, apply, shouldPreview, calendarOnlyReason, hasActiveSupplement};
})();
