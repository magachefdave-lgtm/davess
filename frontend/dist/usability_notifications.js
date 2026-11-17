/* Notification history, readable toast queue and this PC's personal schedule comparison. */
(() => {
  'use strict';
  const SNAPSHOT_KEY = 'sd_personal_schedule_snapshot_v1';
  const detailId = 'oa-notif-detail', toastDetailId = 'oa-toast-detail';
  const escapeText = value => typeof esc === 'function' ? esc(value) : String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const own = (object, key) => Object.prototype.hasOwnProperty.call(object || {}, key);
  const date = value => {
    const s = String(value || '').trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return '';
    const dt = new Date(s + 'T00:00:00Z');
    return Number.isFinite(dt.getTime()) && dt.toISOString().slice(0, 10) === s ? s : '';
  };
  const nameValue = value => typeof value === 'string' && value.trim().length <= 100 ? value.trim() : '';
  const isKnownName = name => !!name && (name === myName || memberList.some(m => m.name === name));
  function ownedToken(token) {
    if (typeof token !== 'string' || !token) return '';
    if (typeof mergeLatestMyRequestStatuses === 'function') mergeLatestMyRequestStatuses();
    return own(myRequestStatuses, token) ? token : '';
  }
  function cleanMeta(input) {
    const m = input && typeof input === 'object' && !Array.isArray(input) ? input : {};
    const result = {};
    if (['schedule','absence','request','approval','recovery','dinner'].includes(m.kind)) result.kind = m.kind;
    if (m.kind === 'dinner' && typeof m.pollId === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(m.pollId)) result.pollId = m.pollId;
    if (m.kind === 'dinner' && m.dinnerAction === 'role') result.dinnerAction = 'role';
    const targetDate = date(m.targetDate || m.date), endDate = date(m.endDate);
    if (targetDate) result.targetDate = targetDate;
    if (endDate) result.endDate = endDate;
    const person = nameValue(m.person || m.requesterName || m.name);
    if (person) result.person = person;
    const requestId = Number(m.requestId);
    if (Number.isSafeInteger(requestId) && requestId > 0) result.requestId = requestId;
    const token = ownedToken(m.receiptToken);
    if (token) result.receiptToken = token; // The token is an opaque capability; never derive one from a name or ID.
    if (['ABSENCE','ABSENCE_CANCEL','SWAP','SUBSTITUTE'].includes(m.requestType)) result.requestType = m.requestType;
    if (['PENDING','APPROVED','REJECTED','CANCELLED'].includes(m.status)) result.status = m.status;
    if (typeof m.before === 'string') result.before = m.before.slice(0, 4000);
    if (typeof m.after === 'string') result.after = m.after.slice(0, 4000);
    return result;
  }
  function legacyMeta(text) {
    const result = {};
    // Only existing, recognizable notification formats produce a destination. Arbitrary dates in prose do not.
    const range = /\[(\d{4}-\d{2}-\d{2})(?:\s*~\s*(\d{4}-\d{2}-\d{2}))?\]/.exec(text);
    // v3.18.0 새 알림은 이름을 따옴표 없이 'X 님'으로 쓴다. 옛 알림('X' 님/사원)도 그대로 읽는다. 명단에 있는 이름만 인정한다.
    const personMatch = /'([^'\n]{1,100})'\s*(?:님|사원)/.exec(text) || /^📤\s*\[\d{4}-\d{2}-\d{2}\]\s*'([^'\n]{1,100})'\s*(?:↔|→)/.exec(text)
      || /(?:^|[\s\]])([^\s'\[\]]{1,30}) 님/u.exec(text);
    if (personMatch && isKnownName(personMatch[1])) result.person = personMatch[1];
    if (/\[요청 승인 대기\]|\[신청 결재함\]|신청이 도착했습니다/.test(text)) result.kind = 'approval';
    else if (/신청|반영 요청/.test(text) && range) result.kind = 'request';
    else if (/당직|근무|휴가|부재|교육/.test(text) && range) result.kind = /휴가|부재|교육/.test(text) ? 'absence' : 'schedule';
    if (result.kind && range && date(range[1])) {
      result.targetDate = date(range[1]);
      if (date(range[2])) result.endDate = date(range[2]);
    }
    return result;
  }
  const metadata = record => Object.assign(legacyMeta(String(record.text || '')), cleanMeta(record.meta));
  function persist() { saveNotifs(); updateNotifBadge(); }
  if (!Array.isArray(notifList)) notifList = [];
  notifList = notifList.filter(n => n && typeof n === 'object' && typeof n.text === 'string').slice(0, 100);
  // Retain legacy records and read state. Do not manufacture request IDs or tokens during migration.
  notifList.forEach(n => { if (n.meta) n.meta = cleanMeta(n.meta); });
  addNotif = function (text, meta) {
    const record = {t:getNowTimeStr(), text:String(text), read:false, meta:Object.assign(legacyMeta(String(text)), cleanMeta(meta))};
    notifList.unshift(record);
    if (notifList.length > 100) notifList.length = 100;
    persist();
    const modal = document.getElementById('notif-modal');
    if (modal?.classList.contains('active')) renderNotifList();
    return record;
  };
  function markRead(index) {
    const record = notifList[index];
    if (!record) return;
    record.read = true; persist(); renderNotifList();
  }
  closeNotifModal = function () {
    document.getElementById('notif-modal')?.classList.remove('active');
  };
  function destination(meta) {
    if (meta.kind === 'dinner') return meta.dinnerAction === 'role' ? 'CA 사용 연결 확인' : meta.pollId ? '회식 일정·내 응답 보기' : '회식 일정 보기';
    if (meta.kind === 'request' && (ownedToken(meta.receiptToken) || meta.person || meta.requestId)) return '신청 내역 보기';
    if (meta.kind === 'approval' && SYSTEM_MODE === 'ADMIN') return '신청 결재함 보기';
    if (meta.kind === 'recovery' && SYSTEM_MODE === 'ADMIN') return '신청 권한 복구 확인';
    if (meta.kind === 'absence' && meta.person && meta.targetDate) return '휴가·교육·부재 일정 보기';
    if (meta.targetDate && meta.kind !== 'request') return '해당 날짜 보기';
    return '';
  }
  function goToRecord(record) {
    if (!record) return false;
    const meta = metadata(record);
    if (!destination(meta)) return false;
    const index = notifList.indexOf(record);
    if(index >= 0)markRead(index);
    window.OnAirUI?.close(detailId);
    closeNotifModal();
    if (meta.kind === 'dinner') {
      if(meta.dinnerAction === 'role')window.OnAirDinnerAdmin?.openRole();
      else window.OnAirDinner?.open(meta.pollId);
    }
    else if (meta.kind === 'request') {
      const token = ownedToken(meta.receiptToken);
      if (token && window.OnAirRequests?.openOwnedRequest) window.OnAirRequests.openOwnedRequest(token);
      else if (window.OnAirRequests?.openMyRequests) window.OnAirRequests.openMyRequests(meta.person, meta.requestId);
      else {
        openMyRequestsModal();
        const sel = document.getElementById('myreq-name');
        if (meta.person && sel) { sel.value = meta.person; loadMyRequests(); }
      }
    } else if (meta.kind === 'approval' && SYSTEM_MODE === 'ADMIN') openApprovalModal();
    else if (meta.kind === 'recovery' && SYSTEM_MODE === 'ADMIN') window.OnAirRequestAccess?.openAdminRecovery();
    else if (meta.kind === 'absence' && meta.person && meta.targetDate) {
      if (window.OnAirPersonal?.openAbsence) window.OnAirPersonal.openAbsence(meta.person, meta.targetDate);
      else openPersonAbsenceModal(meta.person, meta.targetDate);
    } else goToDate(meta.targetDate);
    return true;
  }
  function goTo(index) { return goToRecord(notifList[index]); }
  function openDetail(index) {
    const record = notifList[index];
    if (!record) return;
    markRead(index);
    const meta = metadata(record), action = destination(meta);
    if (!window.OnAirUI) { if (action) goTo(index); return; }
    const comparison = typeof meta.before === 'string' || typeof meta.after === 'string'
      ? `<div class="oa-notif-comparison"><div><span>변경 전</span><p>${escapeText(meta.before || '없음')}</p></div><div><span>변경 후</span><p>${escapeText(meta.after || '없음')}</p></div></div>` : '';
    const node = window.OnAirUI.open(detailId, '알림 상세',
      `<time class="oa-notif-detail-time">${escapeText(record.t || '')}</time><p class="oa-notif-detail-text">${escapeText(record.text)}</p>${comparison}` +
      (action ? `<div class="oa-actions"><button type="button" class="btn-main" data-notif-action="go">${escapeText(action)}</button></div>` : '<p class="modal-hint">이 알림에 연결된 특정 일정이 없습니다.</p>'), {width:640});
    node.querySelector('[data-notif-action]')?.addEventListener('click', () => goToRecord(record));
  }
  renderNotifList = function () {
    const box = document.getElementById('notif-list');
    if (!box) return;
    if (!box._oaNotifsBound) {
      box.addEventListener('click', e => { const row=e.target.closest?.('[data-notif-index]'); if(row)openDetail(Number(row.dataset.notifIndex)); });
      box._oaNotifsBound = true;
    }
    box.innerHTML = !notifList.length ? '<p class="oa-notif-empty">보관된 알림이 없습니다.</p>' : notifList.map((record,index) =>
      `<button type="button" class="oa-notif-row${record.read ? ' is-read' : ' is-unread'}" data-notif-index="${index}" aria-label="${record.read ? '' : '미확인 · '}${escapeText(record.text)}">` +
      `<span class="oa-notif-marker" aria-hidden="true"></span><span class="oa-notif-copy"><span class="oa-notif-text">${escapeText(record.text)}</span>` +
      `<time class="oa-notif-time">${escapeText(record.t || '')}</time></span></button>`).join('');
    const hint = document.getElementById('notif-modal')?.querySelector('.modal-hint');
    if (hint) hint.textContent = '알림은 이 PC 브라우저에 최근 100건까지 보관됩니다. 개별 알림을 열거나 모두 읽음을 누르면 읽음으로 표시됩니다.';
  };

  const toastQueue = [];
  let activeToast = null, toastTimer = null, toastStarted = 0, toastRemaining = 0;
  let toastPaused = false, toastHovered = false, toastFocused = false, toastDetailOpen = false;
  function pauseToast() {
    if (!activeToast || toastPaused) return;
    clearTimeout(toastTimer); toastTimer = null;
    toastRemaining = Math.max(0, toastRemaining - (Date.now() - toastStarted));
    toastPaused = true;
  }
  function resumeToast() {
    if (!activeToast || toastHovered || toastFocused || toastDetailOpen) return;
    if (toastTimer !== null && !toastPaused) return;
    clearTimeout(toastTimer); toastPaused = false; toastStarted = Date.now();
    toastTimer = setTimeout(dismissToast, toastRemaining);
  }
  function dismissToast() {
    clearTimeout(toastTimer); toastTimer = null; activeToast = null;
    toastPaused = false; if(!toastQueue.length)toastHovered = false; toastFocused = false;
    const node = document.getElementById('toast');
    if (node) { node.style.display = 'none'; node.textContent = ''; }
    showNextToast();
  }
  function showToastDetails() {
    if (!activeToast || !window.OnAirUI) return;
    const text = activeToast.text;
    window.OnAirUI.close(toastDetailId);
    pauseToast(); toastDetailOpen = true;
    window.OnAirUI.open(toastDetailId, '안내 전체 보기', `<p class="oa-notif-detail-text">${escapeText(text)}</p>`,
      {width:640,onClose:()=>{toastDetailOpen=false;resumeToast();}});
  }
  function showNextToast() {
    if (activeToast || !toastQueue.length) return;
    const node = document.getElementById('toast');
    if (!node) return;
    activeToast = toastQueue.shift();
    clearTimeout(node._timer); // Cancel any timer created before the extension loaded.
    node.className = 'toast-msg oa-toast' + (activeToast.isError ? ' err' : '');
    node.textContent = '';
    const text = document.createElement('div'); text.className = 'oa-toast-text'; text.textContent = activeToast.text; node.appendChild(text);
    const actions = document.createElement('div'); actions.className = 'oa-toast-actions';
    if (activeToast.text.length > 120 || activeToast.text.includes('\n')) {
      const details = document.createElement('button'); details.type='button';details.textContent='전체 보기';details.setAttribute('data-toast-details','');
      details.addEventListener('click',showToastDetails);actions.appendChild(details);
    }
    const close = document.createElement('button');close.type='button';close.textContent='닫기';close.setAttribute('data-toast-close','');close.setAttribute('aria-label','안내 닫기');
    close.addEventListener('click',dismissToast);actions.appendChild(close);node.appendChild(actions);
    node.style.display = 'block';
    placeToasts();
    if (!node._oaToastBound) {
      node.addEventListener('mouseenter',()=>{toastHovered=true;pauseToast();});
      node.addEventListener('mouseleave',()=>{toastHovered=false;resumeToast();});
      node.addEventListener('focusin',()=>{toastFocused=true;pauseToast();});
      node.addEventListener('focusout',e=>{if(!node.contains(e.relatedTarget)){toastFocused=false;resumeToast();}});
      node._oaToastBound=true;
    }
    toastRemaining = Math.max(activeToast.isError ? 6500 : 4500, Math.min(30000, 2500 + activeToast.text.length * 65));
    resumeToast();
  }
  // Toasts never sit on top of an open dialog's fields: use the free gutter beside it, else above/below it.
  function freeSide() {
    try {
      const width=window.innerWidth||document.documentElement?.clientWidth||0;if(!width)return null;
      const rects=[...document.querySelectorAll('.modal')].filter(n=>n.classList?.contains('active')).map(n=>n.querySelector?.('.modal-body')||n).filter(n=>typeof n?.getBoundingClientRect==='function').map(n=>n.getBoundingClientRect()).filter(r=>r.width>0&&r.height>0);
      if(!rects.length)return null;
      const left=Math.min(...rects.map(r=>r.left)),right=Math.max(...rects.map(r=>r.right)),top=Math.min(...rects.map(r=>r.top)),bottom=Math.max(...rects.map(r=>r.bottom));
      const gutterRight=width-right-24,gutterLeft=left-24;
      if(Math.max(gutterRight,gutterLeft)>=220)return {side:gutterRight>=gutterLeft?'right':'left',width:Math.floor(Math.min(400,Math.max(gutterRight,gutterLeft)))};
      const height=window.innerHeight||0;return {side:height-bottom>=top?'bottom':'top'};
    } catch { return null; }
  }
  function placeToasts() {
    const free=freeSide(),node=document.getElementById('toast'),errors=document.getElementById('oa-toast-errors');
    if(node?.style){
      Object.assign(node.style,{left:'',right:'',top:'',bottom:'',transform:'',maxWidth:''});
      if(free?.side==='left'||free?.side==='right')Object.assign(node.style,{left:free.side==='left'?'12px':'auto',right:free.side==='right'?'12px':'auto',transform:'none',maxWidth:free.width+'px',bottom:'16px'});
      else if(free?.side==='top')Object.assign(node.style,{top:'8px',bottom:'auto'});
    }
    if(errors?.style){
      Object.assign(errors.style,{left:'',right:'',maxWidth:''});
      if(free?.side==='right')errors.style.maxWidth=free.width+'px';
      else if(free?.side==='left')Object.assign(errors.style,{left:'12px',right:'auto',maxWidth:free.width+'px'});
    }
  }
  window.addEventListener?.('resize',()=>{if(activeToast||document.getElementById('oa-toast-errors')?.children?.length)placeToasts();});
  function showUrgentToast(message) {
    let box=document.getElementById('oa-toast-errors');
    if(!box){box=document.createElement('div');box.id='oa-toast-errors';box.setAttribute('role','alert');box.setAttribute('aria-live','assertive');document.body.appendChild(box);}
    const node=document.createElement('div');node.className='oa-toast-error';
    const text=document.createElement('div');text.className='oa-toast-text';text.textContent=message;node.appendChild(text);
    const actions=document.createElement('div');actions.className='oa-toast-actions';node.appendChild(actions);
    const state={remaining:Math.max(6500,Math.min(30000,2500+message.length*65)),started:0,timer:null,paused:false,hovered:false,focused:false,details:false};
    function pause(){if(state.paused)return;clearTimeout(state.timer);state.timer=null;state.remaining=Math.max(0,state.remaining-(Date.now()-state.started));state.paused=true;}
    function dismiss(){clearTimeout(state.timer);state.timer=null;node.remove();}
    function resume(){if(state.hovered||state.focused||state.details)return;if(state.timer!==null&&!state.paused)return;state.started=Date.now();state.paused=false;state.timer=setTimeout(dismiss,state.remaining);}
    if(message.length>120||message.includes('\n')){
      const details=document.createElement('button');details.type='button';details.textContent='전체 보기';details.setAttribute('data-toast-details','');
      details.addEventListener('click',()=>{if(!window.OnAirUI)return;window.OnAirUI.close(toastDetailId);pause();state.details=true;window.OnAirUI.open(toastDetailId,'안내 전체 보기',`<p class="oa-notif-detail-text">${escapeText(message)}</p>`,{width:640,onClose:()=>{state.details=false;if(node.isConnected)resume();}});});actions.appendChild(details);
    }
    const close=document.createElement('button');close.type='button';close.textContent='닫기';close.setAttribute('data-toast-error-close','');close.setAttribute('aria-label','오류 안내 닫기');close.addEventListener('click',dismiss);actions.appendChild(close);
    node.addEventListener('mouseenter',()=>{state.hovered=true;pause();});node.addEventListener('mouseleave',()=>{state.hovered=false;resume();});
    node.addEventListener('focusin',()=>{state.focused=true;pause();});node.addEventListener('focusout',e=>{if(!node.contains(e.relatedTarget)){state.focused=false;resume();}});
    box.appendChild(node);placeToasts();resume();
  }
  showToast = function (msg, isError) { if(isError){showUrgentToast(String(msg));return;}toastQueue.push({text:String(msg),isError:false});showNextToast(); };

  function snapshot(name) {
    const duties = {};
    Object.keys(scheduleMap).sort().forEach(key => {
      if (!date(key) || noDutyMap[key] || !Array.isArray(scheduleMap[key])) return;
      const mine = scheduleMap[key].filter(w=>w?.name===name).map(w=>({role:String(w.role || '당직').trim() || '당직',isSubstitute:!!w.isSubstitute,zeroScore:!!w.zeroScore}));
      if (mine.length) duties[key] = mine.sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b)));
    });
    const educationFields = ['title','location','provider','preparation','content','notes'];
    const personalAbsences = absences.filter(a=>a?.name===name).map(a=>{
      const item={start:date(normalizeDate(a.start)),end:date(normalizeDate(a.end)),type:String(a.type || '부재').trim() || '부재'};
      const education={};educationFields.forEach(key=>{if(typeof a.education?.[key]==='string'&&a.education[key].trim())education[key]=a.education[key].trim();});
      if(Object.keys(education).length)item.education=education;
      return item;
    }).filter(a=>a.start&&a.end).sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b)));
    return {duties,absences:personalAbsences};
  }
  function readSnapshots() {
    try { const data=JSON.parse(localStorage.getItem(SNAPSHOT_KEY)||'{}');return data?.version===1 && data.names && typeof data.names==='object' && !Array.isArray(data.names) ? {version:1,names:Object.assign(Object.create(null),data.names)} : {version:1,names:Object.create(null)}; }
    catch { return {version:1,names:Object.create(null)}; }
  }
  function writeSnapshot(name,current) {
    const data=readSnapshots();data.names[name]=current;
    try { localStorage.setItem(SNAPSHOT_KEY,JSON.stringify(data)); } catch {}
  }
  const dutyText = value => value?.length ? value.map(w=>`${w.role}${w.isSubstitute?' · 대체 근무':''}${w.zeroScore?' · 0점':''}`).join(', ') : '당직 없음';
  const absenceText = value => value ? `${value.start} ~ ${value.end} · ${value.type}` + (value.education ? '\n' + Object.values(value.education).join(' · ') : '') : '등록된 부재 없음';
  function validSnapshot(value) {
    if(!value || !value.duties || typeof value.duties!=='object' || Array.isArray(value.duties) || !Array.isArray(value.absences))return false;
    if(!Object.entries(value.duties).every(([key,rows])=>date(key)&&Array.isArray(rows)&&rows.every(w=>w&&typeof w.role==='string'&&typeof w.isSubstitute==='boolean'&&typeof w.zeroScore==='boolean')))return false;
    return value.absences.every(a=>a&&date(a.start)&&date(a.end)&&typeof a.type==='string'&&(!a.education||(typeof a.education==='object'&&!Array.isArray(a.education)&&Object.entries(a.education).every(([key,text])=>['title','location','provider','preparation','content','notes'].includes(key)&&typeof text==='string'))));
  }
  // ── 결재 결과 알림: 신청 한 건 = 알림 한 건 ─────────────────────────
  // One approval used to produce a result, a '당직표 갱신 (PC)' notice and one schedule-change notice per date.
  // The result is announced once per request on this PC; schedule changes it explains are folded into it.
  const ANNOUNCED_KEY='sd_announced_requests_v1', FOLD_WINDOW_MS=15*60*1000;
  const announcedMemory=Object.create(null);
  function readAnnounced(){try{const v=JSON.parse(localStorage.getItem(ANNOUNCED_KEY)||'{}');return v&&typeof v==='object'&&!Array.isArray(v)?Object.assign(Object.create(null),announcedMemory,v):Object.assign(Object.create(null),announcedMemory);}catch{return Object.assign(Object.create(null),announcedMemory);}}
  function markAnnounced(id){const key=String(Number(id));announcedMemory[key]=Date.now();const data=readAnnounced();data[key]=Date.now();const kept=Object.keys(data).sort((a,b)=>data[b]-data[a]).slice(0,300);try{localStorage.setItem(ANNOUNCED_KEY,JSON.stringify(Object.fromEntries(kept.map(k=>[k,data[k]]))));}catch{}}
  const isAnnounced=id=>String(Number(id)) in readAnnounced();
  const announcedAt=id=>Number(readAnnounced()[String(Number(id))])||0;
  const FOLDED_KEY='sd_folded_request_changes_v1';
  let foldedMemory=[];
  function foldedIds(){
    let saved=[];try{const value=JSON.parse(localStorage.getItem(FOLDED_KEY)||'[]');if(Array.isArray(value))saved=value;}catch{}
    return [...new Set(saved.concat(foldedMemory).map(Number).filter(id=>Number.isSafeInteger(id)&&id>0))].slice(-300);
  }
  function markFolded(id){
    foldedMemory=foldedIds().filter(value=>value!==Number(id)).concat(Number(id)).slice(-300);
    try{localStorage.setItem(FOLDED_KEY,JSON.stringify(foldedMemory));}catch{}
  }
  const md=key=>{const m=/^(\d{4})-(\d{2})-(\d{2})$/.exec(String(key||''));return m?`${Number(m[2])}/${Number(m[3])}`:String(key||'');};
  const dl=key=>window.OnAirUI?.dateLabel?window.OnAirUI.dateLabel(key):String(key||'');
  // Cancellation describes the stored approval, including holiday-separated spans. Never recompute it from today's holidays.
  function cancellationSegments(r){
    const original=r.amendment_original;
    if(!original||typeof original!=='object')return [];
    const rows=Array.isArray(original.segments)&&original.segments.length?original.segments:[original];
    if(!rows.every(s=>s&&date(s.start)&&date(s.end)&&s.start<=s.end))return [];
    return rows.map(s=>({start:s.start,end:s.end,type:String(s.type||original.type||absenceRequestType(r)),education:s.education||original.education||{}}))
      .sort((a,b)=>a.start.localeCompare(b.start)||a.end.localeCompare(b.end));
  }
  const spanText=s=>s.start===s.end?s.start:`${s.start} ~ ${s.end}`;
  const matchesCancellationSegment=(a,s)=>matchesRequestAbsence({target_date:s.start,absence_end_date:s.end,absence_type:s.type,absence_details:s.education},a);
  function resultRecord(r,viewer){
    const status=r.status,word={APPROVED:'승인',REJECTED:'반려',CANCELLED:'철회'}[status]||'처리',icon={APPROVED:'✅',REJECTED:'❌',CANCELLED:'↩'}[status]||'ℹ';
    const requester=nameValue(r.requester_name),type=r.request_type,start=date(normalizeDate(r.target_date)),swap=date(r.swap_date),end=['ABSENCE','ABSENCE_CANCEL'].includes(type)?date(absenceRequestEnd(r)):'';
    const partner=!!viewer&&viewer!==requester&&((type==='SWAP'&&r.swap_worker===viewer)||(type==='SUBSTITUTE'&&r.substitute_name===viewer));
    const approved=status==='APPROVED',lines=[];let head,before,after,targetDate=start;
    if(type==='SWAP'){
      const mine=partner?swap:start,theirs=partner?start:swap;
      head=`${partner?requester:nameValue(r.swap_worker)} 님과 맞교환 ${word} (${md(mine)} ↔ ${md(theirs)})`;
      if(approved){before=`${dl(mine)} 당직`;after=`${dl(theirs)} 당직`;lines.push(`내 당직이 ${dl(mine)}에서 ${dl(theirs)}로 바뀌었습니다.`);targetDate=theirs;}
    }else if(type==='SUBSTITUTE'){
      if(partner){head=`${requester} 님 대신 근무 ${word} (${md(start)})`;if(approved){before='당직 없음';after=`${dl(start)} 당직 · 대체 근무${r.is_zero_score?' · 0점':''}`;lines.push(`${dl(start)} 당직이 추가되었습니다.`);}}
      else{head=`대체 근무 ${word} (${md(start)} → ${nameValue(r.substitute_name)} 님)`;if(approved){before=`${dl(start)} 당직`;after='당직 없음';lines.push(`${dl(start)} 당직을 ${nameValue(r.substitute_name)} 님이 대신 근무합니다.`);}}
    }else if(type==='ABSENCE_CANCEL'){
      const category=absenceRequestType(r),segments=cancellationSegments(r);
      head=`${category} 취소 요청 ${word} (${md(start)}${end&&end!==start?' ~ '+md(end):''})`;
      if(segments.length)lines.push(`${approved?'취소':'대상'} 구간: ${segments.map(spanText).join(', ')}`);
      if(approved){
        before=segments.length?segments.map(s=>`${spanText(s)} · ${s.type}`).join('\n'):`${start} ~ ${end} · ${category}`;
        after='해당 승인 일정 없음';
        lines.push(`승인된 ${category} 일정이 취소되었습니다. 당직 일정은 변경되지 않았습니다.`);
      }else if(['REJECTED','CANCELLED'].includes(status))lines.push('기존 승인 일정은 유지됩니다.');
    }else{
      const amendment=type==='ABSENCE'&&Number(r.amends_request_id)>0;
      head=`${absenceRequestType(r)} ${amendment?'변경 요청':'신청'} ${word} (${md(start)}${end&&end!==start?' ~ '+md(end):''})`;
      // 3.18.0 QA: 당직이 없던 기간(예: 교육)에도 '내 당직은 해제됩니다'라고 했다. 서버가 알려 준, 실제로 풀린 날짜만 적는다.
      if(approved){
        const original=r.amendment_original;
        if(amendment&&original&&date(original.start)&&date(original.end)){
          before=`${original.start} ~ ${original.end} · ${String(original.type||'휴가·부재')}`;
          after=`${start} ~ ${end} · ${absenceRequestType(r)}`;
          lines.push(`변경 전: ${before}\n변경 후: ${after}`);
        }
        const released=Array.isArray(r.released_dates)?r.released_dates.map(date).filter(Boolean):[];
        lines.push('휴가·부재 일정에 반영되었습니다.'+(released.length?` 이 기간의 당직 ${released.length}건이 해제되었습니다: ${released.map(dl).join(', ')}`:''));
      }else if(amendment&&['REJECTED','CANCELLED'].includes(status)){
        lines.push('기존 승인 일정은 유지됩니다.');
      }
    }
    if(status==='REJECTED'&&!partner)lines.push(`반려 사유: ${String(r.reject_reason||'').trim()||'관리자가 사유를 남기지 않았습니다.'}`);
    const prefix=!partner&&requester&&requester!==myName?`${requester} 님 · `:'';
    const text=`${icon} ${prefix}${head}`+(lines.length?'\n'+lines.join('\n'):'');
    const meta=partner?{kind:'schedule',person:viewer,targetDate,requestId:r.id,requestType:type,status}
      :{kind:'request',requestId:r.id,requesterName:requester,targetDate:start,endDate:end,requestType:type,status};
    if(before!==undefined){meta.before=before;meta.after=after;}
    return {text,meta};
  }
  // Returns true when this call announced the request (false if it was already announced on this PC).
  function announceResult(r,viewer,receiptToken){
    if(!r||!Number(r.id)||isAnnounced(r.id)||(r.request_type==='ABSENCE'&&Number(r.cancelled_by_id)>0))return false;
    const record=resultRecord(r,viewer);markAnnounced(r.id);
    addNotif(record.text,receiptToken?{...record.meta,receiptToken}:record.meta);showToast(record.text);return true;
  }
  function requestDates(r,viewer){
    if(r.request_type==='ABSENCE_CANCEL')return [];
    if(r.request_type==='SWAP')return [r.target_date,r.swap_date].map(date).filter(Boolean);
    if(r.request_type==='SUBSTITUTE')return [date(r.target_date)].filter(Boolean);
    const start=date(normalizeDate(r.target_date)),end=date(absenceRequestEnd(r));return start&&end&&r.requester_name===viewer?[{start,end}]:[];
  }
  function matchesRequestAbsence(r,a){
    return a.start===date(normalizeDate(r.target_date))&&a.end===date(absenceRequestEnd(r))&&a.type===absenceRequestType(r)&&
      ['title','location','provider','preparation','content','notes'].every(key=>String(a.education?.[key]||'').trim()===String(r.absence_details?.[key]||'').trim());
  }
  function reflectedDecision(r,viewer,state){
    const start=date(r.target_date);
    if(r.request_type==='ABSENCE_CANCEL'){
      const segments=cancellationSegments(r);
      return r.requester_name===viewer&&segments.length>0&&segments.every(s=>!state.absences.some(a=>matchesCancellationSegment(a,s)));
    }
    if(r.request_type==='ABSENCE')return r.requester_name===viewer&&state.absences.some(a=>matchesRequestAbsence(r,a));
    if(r.request_type==='SUBSTITUTE')return viewer===r.requester_name?!(state.duties[start]||[]).length:
      (state.duties[start]||[]).some(w=>w.isSubstitute&&w.zeroScore===!!r.is_zero_score);
    const mine=viewer===r.requester_name?start:date(r.swap_date),theirs=viewer===r.requester_name?date(r.swap_date):start;
    return !(state.duties[mine]||[]).length&&(state.duties[theirs]||[]).length>0;
  }
  function explains(r,change,viewer,before,current){
    if(r.request_type==='ABSENCE_CANCEL'){
      if(r.requester_name!==viewer||change.meta.kind!=='absence'||change.meta.after!==absenceText(null))return false;
      const segments=cancellationSegments(r);
      // A cancellation can explain only the complete, exact removal of its original spans, never a duty change.
      return segments.length>0&&segments.every(s=>before.absences.some(a=>matchesCancellationSegment(a,s))&&!current.absences.some(a=>matchesCancellationSegment(a,s)))&&
        segments.some(s=>change.meta.targetDate===s.start&&change.meta.endDate===s.end&&before.absences.some(a=>matchesCancellationSegment(a,s)&&change.meta.before===absenceText(a)));
    }
    // Moving an approved absence can look like a removal plus an addition. Both belong to this one amendment.
    const original=r.request_type==='ABSENCE'&&Number(r.amends_request_id)>0?r.amendment_original:null;
    if(original&&r.requester_name===viewer&&change.meta.kind==='absence'&&
       change.meta.targetDate===original.start&&change.meta.endDate===original.end&&change.meta.after===absenceText(null)){
      const oldRequest={target_date:original.start,absence_end_date:original.end,absence_type:original.type,absence_details:original.education};
      if(before.absences.some(a=>matchesRequestAbsence(oldRequest,a))&&!current.absences.some(a=>matchesRequestAbsence(oldRequest,a))&&
         current.absences.some(a=>matchesRequestAbsence(r,a))&&!before.absences.some(a=>matchesRequestAbsence(r,a)))return true;
    }
    const inRange=requestDates(r,viewer).some(d=>typeof d==='string'?change.meta.kind==='schedule'&&change.meta.targetDate===d:(change.meta.kind==='absence'?change.meta.targetDate===d.start&&change.meta.endDate===d.end:change.meta.targetDate>=d.start&&change.meta.targetDate<=d.end));
    if(!inRange)return false;
    if(change.meta.kind==='absence'){
      if(r.request_type!=='ABSENCE'||r.requester_name!==viewer)return false;
      return current.absences.some(a=>matchesRequestAbsence(r,a))&&!before.absences.some(a=>matchesRequestAbsence(r,a));
    }
    const key=change.meta.targetDate,old=before.duties[key]||[],now=current.duties[key]||[];
    if(r.request_type==='ABSENCE')return old.length>0&&!now.length&&(!Array.isArray(r.released_dates)||r.released_dates.includes(key));
    const removed=r.request_type==='SUBSTITUTE'?viewer===r.requester_name:
      key===(viewer===r.requester_name?date(r.target_date):date(r.swap_date));
    if(removed)return old.length>0&&!now.length;
    if(old.length||!now.length)return false;
    return r.request_type==='SWAP'||now.some(w=>w.isSubstitute&&w.zeroScore===!!r.is_zero_score);
  }
  async function decidedRequestsFor(name){
    try{const res=await fetch('/api/duty/my-requests?name='+encodeURIComponent(name)+'&involving=1');if(!res.ok)return [];const body=await res.json();return Array.isArray(body?.requests)?body.requests.filter(r=>r&&r.status==='APPROVED'&&Number(r.id)>0&&!(r.request_type==='ABSENCE'&&Number(r.cancelled_by_id)>0)):[];}catch{return [];}
  }
  let lastPersonalNotice=-Infinity;
  async function comparePersonal() {
    if (!myName || !dataLoaded || !SERVER_CONNECTED || suspendAutoRefresh) return [];
    const name=myName,current=snapshot(name),before=readSnapshots().names[name];
    if (!validSnapshot(before)) { writeSnapshot(name,current);return []; }
    let changes=[];
    [...new Set(Object.keys(before.duties).concat(Object.keys(current.duties)))].sort().forEach(key=>{
      if (JSON.stringify(before.duties[key]||[])===JSON.stringify(current.duties[key]||[])) return;
      if (!date(key)) return;
      changes.push({text:`${name} 님의 [${key}] 당직 일정이 변경되었습니다.`,meta:{kind:'schedule',person:name,targetDate:key,before:dutyText(before.duties[key]),after:dutyText(current.duties[key])}});
    });
    const removed=before.absences.slice(),added=[];
    current.absences.forEach(item=>{const i=removed.findIndex(old=>JSON.stringify(old)===JSON.stringify(item));if(i>=0)removed.splice(i,1);else added.push(item);});
    added.forEach(item=>{
      let i=removed.findIndex(old=>old.start===item.start&&old.end===item.end);
      if(i<0)i=removed.findIndex(old=>old.type===item.type);
      const old=i>=0?removed.splice(i,1)[0]:null;
      changes.push({text:`${name} 님의 [${item.start} ~ ${item.end}] ${item.type} 일정이 ${old?'변경':'등록'}되었습니다.`,meta:{kind:'absence',person:name,targetDate:item.start,endDate:item.end,before:absenceText(old),after:absenceText(item)}});
    });
    removed.forEach(item=>{if(!date(item.start))return;changes.push({text:`${name} 님의 [${item.start} ~ ${item.end}] ${item.type} 일정이 삭제되었습니다.`,meta:{kind:'absence',person:name,targetDate:item.start,endDate:item.end,before:absenceText(item),after:absenceText(null)}});});
    // Persist before publishing so another successful fetch of the same version cannot repeat the notification.
    writeSnapshot(name,current);
    if(!changes.length)return [];
    // Changes explained by an approved request become that request's single result notification.
    // A request already announced long ago does not swallow a later, unrelated edit of the same date.
    const now=Date.now();
    for(const r of await decidedRequestsFor(name)){
      if(foldedIds().includes(Number(r.id)))continue;
      const seen=isAnnounced(r.id);if(seen&&now-announcedAt(r.id)>FOLD_WINDOW_MS)continue;
      const processed=Date.parse(String(r.processed_at||'').replace(' ','T'));if(!seen&&Number.isFinite(processed)&&now-processed>36*3600*1000)continue;
      // If the saved baseline already contains this result, a later edit is independent.
      if(reflectedDecision(r,name,before)){markFolded(r.id);continue;}
      const folded=changes.filter(change=>explains(r,change,name,before,current));if(!folded.length)continue;
      changes=changes.filter(change=>!folded.includes(change));
      markFolded(r.id);
      announceResult(r,name);
    }
    changes.forEach(change=>addNotif(change.text,change.meta));
    if(changes.length){lastPersonalNotice=Date.now();showToast(`${name} 님의 개인 일정 ${changes.length}건이 변경되었습니다. 알림 보관함에서 변경 전후를 확인하세요.`);}
    return changes;
  }
  const fetchOriginal=fetchAllData;
  fetchAllData=async function (...args) {
    const suspended=suspendAutoRefresh;
    const result=await fetchOriginal.apply(this,args);
    if(!suspended && !suspendAutoRefresh)await comparePersonal();
    return result;
  };
  // Another PC's save: no host names or internal tags. A save caused by one decided request is announced by its result
  // notification; a personal-change notice from the same refresh already says what changed.
  notifyRemoteRefresh=function (info) {
    const source=String(info?.lastWrite?.source||'');
    if(source.startsWith('request:'))return;
    if(Date.now()-lastPersonalNotice<5000)return;
    showToast('🔄 당직표가 갱신되어 새로 불러왔습니다.');
    addNotif('🔄 다른 PC에서 당직표가 바뀌어 새로 불러왔습니다.');
  };
  const setNameOriginal=setMyName;
  setMyName=function (...args) {
    const previous=myName,result=setNameOriginal.apply(this,args);
    if(previous!==myName && myName) {
      if(dataLoaded && SERVER_CONNECTED && !suspendAutoRefresh)writeSnapshot(myName,snapshot(myName));
      else { const data=readSnapshots();delete data.names[myName];try{localStorage.setItem(SNAPSHOT_KEY,JSON.stringify(data));}catch{} }
    }
    return result;
  };
  // ── 3.18.0 QA: 관리자가 자동 백업으로 되돌려 서버에서 사라진 신청 ─────────────
  // 예전에는 이 PC가 열 때마다 같은 토큰을 물어 404 를 받기만 하고, 신청자는 신청이 없어진 것을 몰랐다.
  const VANISHED_DETAIL='신청 결과를 찾을 수 없습니다.';
  async function isVanished(res){if(!res||res.status!==404)return false;try{const body=await (typeof res.clone==='function'?res.clone():res).json();return body?.detail===VANISHED_DETAIL;}catch{return false;}}
  function vanishedLabel(r){
    if(!r)return '';
    const kind=['ABSENCE','ABSENCE_CANCEL'].includes(r.request_type)?(String(r.absence_type||'').trim()||'휴가·부재')+(r.request_type==='ABSENCE_CANCEL'?' 취소 요청':''):(r.request_type==='SWAP'?'맞교환':(r.request_type==='SUBSTITUTE'?'대체 근무':'변경'));
    const start=date(normalizeDate(r.target_date)),end=['ABSENCE','ABSENCE_CANCEL'].includes(r.request_type)?date(normalizeDate(r.absence_end_date||r.target_date)):'';
    const when=start?(end&&end!==start?`${dl(start)}~${dl(end)}`:dl(start)):'';
    return `${when?when+' ':''}${kind}`;
  }
  function vanishedText(r){return r?`관리자가 이전 상태로 되돌려 ${vanishedLabel(r)}${r.request_type==='ABSENCE_CANCEL'?'':' 신청'}이 취소되었습니다. 필요하면 다시 신청하세요.`
    :'관리자가 이전 상태로 되돌려 이 PC에서 보낸 신청 1건이 취소되었습니다. 필요하면 다시 신청하세요.';}
  // Archive the unresolved record without erasing its receipt or rewrite copy.
  const archivedReceipts=new Set();
  function forgetVanished(receiptToken){archivedReceipts.add(receiptToken);return typeof readMyRequestCopies==='function'?readMyRequestCopies()[receiptToken]?.request||null:null;}
  function showVanished(list){
    if(!list.length)return;
    list.forEach(item=>addNotif('↩ '+vanishedText(item.request)));
    if(!window.OnAirUI)return showToast(list.map(item=>vanishedText(item.request)).join('\n'));
    const rows=list.map((item,i)=>`<li><p class="oa-notif-detail-text">${escapeText(vanishedText(item.request))}</p>`+
      (item.request&&window.OnAirRequests?.resubmit?`<button type="button" class="btn-main" data-oa-vanished-retry="${i}">다시 작성</button>`:'')+'</li>').join('');
    const node=window.OnAirUI.open('oa-request-vanished','신청이 취소되었습니다',`<ul style="list-style:none;padding:0;margin:0;display:grid;gap:12px;">${rows}</ul>`+
      '<p class="oa-sub" style="margin-top:12px;">[다시 작성]을 누르면 이 PC에 남아 있던 신청 내용으로 새 신청서를 엽니다. 보내기 전에 날짜와 내용을 다시 확인하세요.</p>'+
      '<div class="ur-footer" style="display:flex;justify-content:flex-end;margin-top:12px;"><button type="button" data-oa-vanished-close>닫기</button></div>',{width:560});
    node?.querySelectorAll?.('[data-oa-vanished-retry]').forEach(button=>button.addEventListener('click',()=>{
      const item=list[Number(button.getAttribute('data-oa-vanished-retry'))];window.OnAirUI.close('oa-request-vanished');
      window.OnAirRequests.resubmit({...item.request});
    }));
    node?.querySelector?.('[data-oa-vanished-close]')?.addEventListener('click',()=>window.OnAirUI.close('oa-request-vanished'));
  }
  let checkingOutcomes=null;
  checkRequestOutcomes=function () {
    if(checkingOutcomes)return checkingOutcomes;
    checkingOutcomes=(async()=>{
      if(!SERVER_CONNECTED)return;
      mergeLatestMyRequestStatuses();let changed=false;const vanished=[];
      for(const [receiptToken,previous] of Object.entries(myRequestStatuses)) {
        if(previous!=='PENDING')continue;
        try {
          const res=await fetch(`/api/duty/request-status/${encodeURIComponent(receiptToken)}`);
          if(await isVanished(res)){if(!archivedReceipts.has(receiptToken))vanished.push({token:receiptToken,request:forgetVanished(receiptToken)});continue;}
          if(!res.ok)continue;
          const r=(await res.json()).request || {};
          if(!['APPROVED','REJECTED','CANCELLED'].includes(r.status))continue;
          // A withdrawal made in another tab while this request was in flight wins over the stale response.
          mergeLatestMyRequestStatuses();if(myRequestStatuses[receiptToken]!=='PENDING')continue;
          // The applicant's own reason never goes into notifications; the admin's reject reason is for the applicant.
          announceResult({...r,reason:undefined},null,receiptToken);
          myRequestStatuses[receiptToken]=r.status;changed=true;
        } catch {}
      }
      if(changed)saveMyRequestStatuses();
      showVanished(vanished);
    })().finally(()=>{checkingOutcomes=null;});
    return checkingOutcomes;
  };
  const style=document.createElement('style');style.id='oa-notifications-style';
  style.textContent=`
    #notif-list .oa-notif-row{display:block;width:100%;position:relative;text-align:left;padding:12px 12px 12px 25px;border:0;border-bottom:1px solid var(--border);border-radius:0;background:transparent;color:var(--text-main);font:inherit;cursor:pointer;box-shadow:none}
    #notif-list .oa-notif-row.is-unread{background:var(--surface-2,#f5f7fa)}
    #notif-list .oa-notif-row.is-read{color:var(--text-muted)}
    #notif-list .oa-notif-row:hover{background:var(--surface-hover,var(--surface-2,#f5f7fa))}
    #notif-list .oa-notif-row:focus-visible{outline:2px solid var(--accent,#2563eb);outline-offset:-2px}
    #notif-list .oa-notif-marker{position:absolute;left:12px;top:20px;width:5px;height:5px;border-radius:50%;background:var(--accent,#2563eb)}
    #notif-list .is-read .oa-notif-marker{visibility:hidden}
    #notif-list .oa-notif-copy{display:grid;gap:5px;min-width:0}
    #notif-list .oa-notif-text{font-size:13px;font-weight:500;line-height:1.65;overflow-wrap:anywhere;white-space:pre-wrap}
    #notif-list .oa-notif-time{display:block;font-size:11px;font-weight:400;line-height:1.4;color:var(--text-muted);white-space:nowrap}
    #notif-list .oa-notif-empty{text-align:center;padding:24px;color:var(--text-muted);font-weight:500}
    #oa-notif-detail .oa-notif-detail-time{display:block;color:var(--text-muted);font-size:12px;white-space:nowrap}
    #oa-notif-detail .oa-notif-detail-text,#oa-toast-detail .oa-notif-detail-text{font-weight:500;font-size:14px;line-height:1.7;white-space:pre-wrap;overflow-wrap:anywhere}
    #oa-notif-detail .oa-notif-comparison{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:18px 0}
    #oa-notif-detail .oa-notif-comparison>div{padding:12px;background:var(--surface-2,#f5f7fa);border:1px solid var(--border);border-radius:6px;min-width:0}
    #oa-notif-detail .oa-notif-comparison span{color:var(--text-muted);font-size:12px}
    #oa-notif-detail .oa-notif-comparison p{font-size:13px;font-weight:500;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere;margin:6px 0 0}
    #toast.oa-toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);width:max-content;max-width:min(680px,calc(100vw - 32px));box-sizing:border-box;padding:14px 18px;z-index:15000;text-align:left;font-weight:500;white-space:normal;overflow-wrap:anywhere}
    #toast .oa-toast-text{font-size:13px;line-height:1.65;font-weight:500;white-space:pre-wrap;max-height:min(240px,35vh);overflow-y:auto}
    #toast .oa-toast-actions{display:flex;justify-content:flex-end;gap:12px;margin-top:8px}
    #toast .oa-toast-actions button{border:1px solid currentColor;background:transparent;color:inherit;padding:3px 9px;font-size:12px;font-weight:500;box-shadow:none}
    #oa-toast-errors{position:fixed;top:16px;right:16px;max-width:min(560px,calc(100vw - 32px));max-height:45vh;overflow-y:auto;display:flex;flex-direction:column;gap:8px;z-index:15001}
    #oa-toast-errors .oa-toast-error{padding:14px 18px;border-radius:var(--radius,6px);background:var(--holiday-text,#b91c1c);color:var(--bg,#fff);box-shadow:0 4px 18px #0002;overflow-wrap:anywhere}
    #oa-toast-errors .oa-toast-text{font-size:13px;font-weight:500;line-height:1.65;white-space:pre-wrap}
    #oa-toast-errors .oa-toast-actions{display:flex;justify-content:flex-end;gap:12px;margin-top:8px}
    #oa-toast-errors .oa-toast-actions button{border:1px solid currentColor;background:transparent;color:inherit;padding:3px 9px;font-size:12px;font-weight:500;box-shadow:none}
    @media(max-width:540px){#oa-notif-detail .oa-notif-comparison{grid-template-columns:1fr}}
    @media print{#oa-toast-errors{display:none!important}}
  `;
  document.head.appendChild(style);
  window.OnAirNotifications={vanishedText,forgetVanished,isVanished,add:(text,meta)=>addNotif(text,meta),activate:openDetail,openDetail,goTo,markRead,snapshot,comparePersonal,resultRecord,announceResult,placeToasts};
  persist();
})();
