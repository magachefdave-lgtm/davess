/* Read-only weekly schedules. Pagination keeps the existing dashboard free of new scrolling. */
(() => {
  'use strict';
  const ID = 'oa-weekly-absence';
  const UI = () => window.OnAirUI;
  const esc = value => UI().escape(value);
  const get = () => document.getElementById(ID);
  const kindOf = kind => kind === 'dispatch' ? 'dispatch' : 'vacation';
  const titleOf = kind => kind === 'dispatch' ? '이번 주 장기 파견·부재' : '이번 주 휴가·부재';
  const state = {kind:'vacation', page:0, size:5, trigger:null, detailReturn:null, signature:''};
  let detailObserver = null;
  let resizeQueued = false;
  let tooltipOwner = null;
  let tooltipTitle = '';

  function week() {
    const keys = weekKeysOf(todayStr);
    return {start:keys[0], end:keys[6]};
  }
  function rows(kind = state.kind) {
    const range = week(), names = new Set(memberList.map(member => member.name));
    const all = absences.filter(a => names.has(a.name))
      .filter(a => normalizeDate(a.start) <= range.end && normalizeDate(a.end) >= range.start)
      .sort((a,b) => normalizeDate(a.start).localeCompare(normalizeDate(b.start)) || String(a.name).localeCompare(String(b.name)));
    // v3.18.0 장기 여부는 기간(isLongAbsence), 휴가·부재는 absenceKind — 안내판(renderWeekAbsenceBoard)과 같은 기준.
    if (kindOf(kind) === 'dispatch') return all.filter(a => isLongAbsence(a));
    const near = all.filter(a => !isLongAbsence(a));
    return near.filter(a => absenceKind(a) === 'vacation').concat(near.filter(a => absenceKind(a) !== 'vacation'));
  }
  function fullDate(value) {
    const key = normalizeDate(value);
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(key);
    if (!match) return key;
    const date = new Date(Date.UTC(Number(match[1]), Number(match[2])-1, Number(match[3])));
    return `${key}(${['일','월','화','수','목','금','토'][date.getUTCDay()]})`;
  }
  function period(record) {
    const start = normalizeDate(record.start), end = normalizeDate(record.end || record.start);
    return start === end ? fullDate(start) : `${fullDate(start)} ~ ${fullDate(end)}`;
  }
  function pageSize() {
    const preferred = window.innerHeight >= 900 ? 5 : 3;
    return Math.max(1, Math.min(preferred, Math.floor((window.innerHeight * .88 - 260) / 64)));
  }
  function recordKeys(records) {
    const counts = new Map();
    return records.map(record => {
      const base = JSON.stringify([record.id ?? '', record.name, record.type, normalizeDate(record.start), normalizeDate(record.end)]);
      const occurrence = counts.get(base) || 0;
      counts.set(base, occurrence + 1);
      return `${base}:${occurrence}`;
    });
  }
  function focusedToken(node = document.activeElement) {
    if (!get()?.contains(node)) return null;
    if (node.matches('.oa-close')) return {action:'header-close'};
    const action = node.closest('[data-weekly-action]');
    return action ? {action:action.dataset.weeklyAction, key:action.dataset.weeklyKey, index:Number(action.dataset.weeklyIndex || 0)} : null;
  }
  function restoreFocus(token) {
    const modal = get();
    if (!token || !modal?.classList.contains('active')) return;
    let target;
    if (token.action === 'record') {
      const buttons = [...modal.querySelectorAll('[data-weekly-action="record"]')];
      target = buttons.find(button => button.dataset.weeklyKey === token.key) || buttons[Math.min(token.index, buttons.length - 1)];
    } else if (token.action === 'header-close') target = modal.querySelector('.oa-close');
    else target = modal.querySelector(`[data-weekly-action="${token.action}"]`);
    if (!target || target.disabled) target = modal.querySelector('[data-weekly-action="next"]:not(:disabled),[data-weekly-action="previous"]:not(:disabled),[data-weekly-action="close"]');
    target?.focus({preventScroll:true});
  }
  function onClose() {
    detailObserver?.disconnect(); detailObserver = null;
    state.detailReturn = null;
    const trigger = state.trigger;
    // The board may have replaced the original button during a data refresh.
    queueMicrotask(() => {
      if (get()?.classList.contains('active')) return;
      const target = trigger?.isConnected ? trigger : document.getElementById(`week-${state.kind}-title`);
      target?.focus({preventScroll:true});
    });
  }
  function content(records, keys, totalPages) {
    const range = week(), start = state.page * state.size;
    const displayed = records.slice(start, start + state.size);
    const people = new Set(records.map(record => record.name)).size;
    return `<div class="ow-weekly">` +
      `<div class="ow-period">오늘 기준 ${esc(fullDate(range.start))} ~ ${esc(fullDate(range.end))}</div>` +
      `<div class="ow-context">달력에서 선택한 달과 관계없이 오늘이 속한 주를 표시합니다.</div>` +
      `<div class="ow-summary">대상 <strong>${people}명</strong> · 일정 <strong>${records.length}건</strong><span>항목을 누르면 개인 일정을 봅니다.</span></div>` +
      `<div class="ow-list" style="--ow-page-size:${state.size}">` +
      (displayed.length ? displayed.map((record,index) => {
        const kindOfRecord = absenceKind(record);
        const category = kindOfRecord === 'long' ? 'ow-dispatch' : kindOfRecord === 'vacation' ? 'ow-vacation' : 'ow-short';
        const title = `${record.name} · ${record.type || '부재'} · ${period(record)}`;
        return `<button type="button" class="ow-record ${category}" data-weekly-action="record" data-weekly-key="${esc(keys[start+index])}" data-weekly-index="${index}" data-weekly-name="${esc(record.name)}" data-weekly-date="${esc(normalizeDate(record.start))}" title="${esc(title)}" aria-label="${esc(title)} · 개인 일정 보기">` +
          `<span class="ow-record-top"><span class="ow-name">${esc(record.name)}</span><span class="ow-type">${esc(record.type || '부재')}</span></span>` +
          `<span class="ow-dates">${esc(period(record))}</span></button>`;
      }).join('') : '<div class="ow-empty">이번 주에 해당하는 일정이 없습니다.</div>') + `</div>` +
      `<div class="ow-footer"><div class="ow-pagination"><button type="button" data-weekly-action="previous"${state.page === 0 ? ' disabled' : ''} aria-label="이전 일정 페이지">이전</button>` +
      `<span class="ow-page" role="status" aria-live="polite" aria-atomic="true">${state.page+1} / ${totalPages} 페이지</span>` +
      `<button type="button" data-weekly-action="next"${state.page+1 >= totalPages ? ' disabled' : ''} aria-label="다음 일정 페이지">다음</button></div>` +
      `<button type="button" data-weekly-action="close" class="btn-main">닫기</button></div></div>`;
  }
  function render(initial = false) {
    const records = rows(), keys = recordKeys(records), size = pageSize();
    if (!initial && size !== state.size) state.page = Math.floor(state.page * state.size / size);
    state.size = size;
    const totalPages = Math.max(1, Math.ceil(records.length / size));
    state.page = Math.max(0, Math.min(state.page, totalPages - 1));
    const signature = JSON.stringify([state.kind, state.page, state.size, week(), keys]);
    if (!initial && signature === state.signature) return;
    state.signature = signature;
    const focus = focusedToken();
    const html = content(records, keys, totalPages);
    if (initial) UI().open(ID, titleOf(state.kind), html, {width:720, onClose});
    else {
      const modal = get();
      modal.querySelector('.modal-title').textContent = titleOf(state.kind);
      modal.querySelector('.oa-body').innerHTML = html;
      // Do not raise or steal focus from an open personal detail dialog.
      restoreFocus(focus);
    }
  }
  function open(kind = 'vacation', page = 0) {
    hideTooltip();
    const active = document.activeElement;
    if (!get()?.classList.contains('active')) state.trigger = active;
    state.kind = kindOf(kind);
    state.page = Math.max(0, Math.floor(Number(page) || 0));
    state.size = pageSize();
    render(true);
  }
  function refresh() {
    if (get()?.classList.contains('active')) render();
    if (tooltipOwner) showTooltip(tooltipOwner);
  }
  function openRecord(button) {
    state.detailReturn = focusedToken(button);
    // Mouse activation is not guaranteed to focus buttons on every platform.
    button.focus({preventScroll:true});
    window.OnAirPersonal?.openAbsence(button.dataset.weeklyName, button.dataset.weeklyDate);
    detailObserver?.disconnect();
    const detail = document.getElementById('oa-personal-absence');
    if (!detail) return;
    detailObserver = new MutationObserver(() => {
      if (detail.classList.contains('active')) return;
      detailObserver?.disconnect(); detailObserver = null;
      restoreFocus(state.detailReturn);
      state.detailReturn = null;
    });
    detailObserver.observe(detail, {attributes:true, attributeFilter:['class']});
  }
  function tooltipNode() {
    let node = document.getElementById('ow-week-tooltip');
    if (!node) {
      node = document.createElement('div');node.id='ow-week-tooltip';node.className='ow-week-tooltip';node.setAttribute('role','tooltip');node.hidden=true;document.body.appendChild(node);
    }
    return node;
  }
  function showTooltip(button) {
    if (!button.isConnected) return hideTooltip();
    if (tooltipOwner !== button) {hideTooltip();tooltipOwner=button;tooltipTitle=button.getAttribute('title') || '';}
    if (button.hasAttribute('title')) tooltipTitle=button.getAttribute('title');
    button.removeAttribute('title');
    const node = tooltipNode(), range = week();
    node.textContent=`오늘 기준 ${fullDate(range.start)} ~ ${fullDate(range.end)}\n달력에서 선택한 달과 관계없이 이번 주를 표시합니다.\n누르면 이번 주 전체 일정을 확인할 수 있습니다.`;
    node.hidden=false;button.setAttribute('aria-describedby',node.id);
    const rect=button.getBoundingClientRect(), box=node.getBoundingClientRect();
    node.style.left=`${Math.max(8, Math.min(rect.left, window.innerWidth-box.width-8))}px`;
    node.style.top=`${rect.bottom+box.height+14 <= window.innerHeight ? rect.bottom+6 : Math.max(8,rect.top-box.height-6)}px`;
  }
  function hideTooltip() {
    const node=document.getElementById('ow-week-tooltip');if(node)node.hidden=true;
    if (tooltipOwner) {tooltipOwner.removeAttribute('aria-describedby');if(tooltipTitle)tooltipOwner.setAttribute('title',tooltipTitle);}
    tooltipOwner=null;tooltipTitle='';
  }
  function install() {
    const style=document.createElement('style');style.id='oa-weekly-style';style.textContent=`
      #oa-weekly-absence .modal-body{overflow:hidden;box-sizing:border-box}
      #oa-weekly-absence .ow-period{font-size:13px;font-weight:600;color:var(--text-main);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      #oa-weekly-absence .ow-context{font-size:12px;color:var(--text-muted);margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      #oa-weekly-absence .ow-summary{display:flex;align-items:center;gap:4px;font-size:12px;color:var(--text-sub);margin:14px 0 8px;white-space:nowrap}
      #oa-weekly-absence .ow-summary span{margin-left:auto;font-size:11px;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis}
      #oa-weekly-absence .ow-list{display:flex;flex-direction:column;gap:6px;min-height:calc(var(--ow-page-size)*64px - 6px)}
      #oa-weekly-absence .ow-record{display:flex;flex-direction:column;align-items:stretch;justify-content:center;gap:3px;width:100%;height:58px;min-height:58px;box-sizing:border-box;padding:8px 12px;border:1px solid var(--border);border-radius:8px;background:var(--ow-bg);border-color:var(--ow-border);color:var(--text-main);text-align:left;cursor:pointer;overflow:hidden}
      #oa-weekly-absence .ow-vacation{--ow-bg:var(--vacation-bg);--ow-color:var(--vacation-text);--ow-border:var(--vacation-border)}
      #oa-weekly-absence .ow-short{--ow-bg:var(--short-absence-bg);--ow-color:var(--short-absence-text);--ow-border:var(--short-absence-border)}
      #oa-weekly-absence .ow-dispatch{--ow-bg:var(--dispatch-bg);--ow-color:var(--dispatch-text);--ow-border:var(--dispatch-border)}
      #oa-weekly-absence .ow-record:hover{background:var(--ow-bg);border-color:var(--ow-color)}
      #oa-weekly-absence .ow-record:focus-visible{outline-color:var(--ow-color)}
      #oa-weekly-absence .ow-record .ow-name,#oa-weekly-absence .ow-record .ow-type{color:var(--ow-color)}
      #oa-weekly-absence .ow-record-top{display:flex;align-items:center;gap:10px;width:100%;min-width:0;line-height:18px}
      #oa-weekly-absence .ow-name{font-size:14px;font-weight:600;max-width:55%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      #oa-weekly-absence .ow-type{font-size:12px;font-weight:500;color:var(--text-sub);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      #oa-weekly-absence .ow-dates{display:block;font-size:12px;font-weight:400;line-height:17px;color:var(--text-sub);max-width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      #oa-weekly-absence .ow-empty{padding:24px 0;color:var(--text-muted);font-size:13px}
      #oa-weekly-absence .ow-footer{display:flex;justify-content:space-between;align-items:center;gap:12px;border-top:1px solid var(--border);margin-top:14px;padding-top:12px}
      #oa-weekly-absence .ow-pagination{display:flex;align-items:center;gap:10px}
      #oa-weekly-absence .ow-page{min-width:9em;text-align:center;font-size:12px;color:var(--text-sub);font-variant-numeric:tabular-nums;white-space:nowrap}
      #oa-weekly-absence button:disabled{cursor:default;opacity:.45}
      .ow-week-tooltip{position:fixed;z-index:10050;max-width:min(430px,calc(100vw - 16px));padding:9px 12px;border:1px solid var(--border-strong);border-radius:8px;box-sizing:border-box;background:var(--card);color:var(--text-main);box-shadow:0 4px 16px rgba(0,0,0,.15);font-family:inherit;font-size:12px;line-height:1.7;white-space:pre-line;pointer-events:none}
      @media(max-width:600px){#oa-weekly-absence .ow-summary span{display:none}#oa-weekly-absence .ow-pagination{gap:5px}#oa-weekly-absence .ow-footer{gap:6px}#oa-weekly-absence .ow-period{font-size:11px}}
    `;document.head.appendChild(style);
    document.addEventListener('click', event => {
      hideTooltip();
      const button=event.target.closest?.(`#${ID} [data-weekly-action]`);if(!button)return;
      switch(button.dataset.weeklyAction){
        case 'close':UI().close(ID);break;
        case 'previous':if(state.page>0){state.page--;render();}break;
        case 'next':if((state.page+1)*state.size<rows().length){state.page++;render();}break;
        case 'record':openRecord(button);break;
      }
    });
    document.addEventListener('pointerover',event=>{const button=event.target.closest?.('.week-absence-title[data-weekly-open]');if(button)showTooltip(button);});
    document.addEventListener('pointerout',event=>{if(tooltipOwner?.contains(event.target)&&!tooltipOwner.contains(event.relatedTarget))hideTooltip();});
    document.addEventListener('focusin',event=>{const button=event.target.closest?.('.week-absence-title[data-weekly-open]');if(button)showTooltip(button);});
    document.addEventListener('focusout',event=>{if(tooltipOwner===event.target)hideTooltip();});
    document.addEventListener('keydown',event=>{if(event.key==='Escape')hideTooltip();},true);
    window.addEventListener('resize',()=>{if(resizeQueued)return;resizeQueued=true;requestAnimationFrame(()=>{resizeQueued=false;refresh();});});
  }
  window.OnAirWeekly={open,refresh,close:()=>UI().close(ID),rows,state:()=>({kind:state.kind,page:state.page,pageSize:state.size,total:rows().length})};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install);else install();
})();
