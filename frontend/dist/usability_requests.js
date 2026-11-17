/* Request drafts, receipt ownership, approval controls and optional education. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const ui=()=>window.OnAirUI;
  const escape=value=>ui().escape(value);
  const clone=value=>JSON.parse(JSON.stringify(value));
  const educationFields={title:'교육명',location:'장소',provider:'교육기관',preparation:'준비물',content:'교육 내용',notes:'안내사항'};
  const education=value=>Object.fromEntries(Object.keys(educationFields).map(key=>[key,String(value?.[key]??'')]));
  const isEducation=type=>/교육/i.test(String(type||''));
  const draftKey=kind=>'sd_request_draft_'+kind+'_v370';
  const readDraft=kind=>{try{return JSON.parse(sessionStorage.getItem(draftKey(kind))||'null')}catch{return null}};
  function writeDraft(kind,value){try{sessionStorage.setItem(draftKey(kind),JSON.stringify(value))}catch{}}
  function clearDraft(kind){try{sessionStorage.removeItem(draftKey(kind))}catch{}}
  let absenceContext=null,absenceOpener=null,changeContext=null,changeOpening=false;
  let absenceImpactArmed=false,absenceDatesTouched=false,zeroTouched=false;
  const owned=new Map();
  let myRequestsRows=[],myRequestsGeneration=0;
  let approvalRestore=null,approvalActionSnapshot=null;
  const validDate=value=>validAbsenceDate(String(value||''));
  const member=name=>memberList.some(m=>m.name===name);
  const personalName=()=>typeof myName==='string'&&member(myName)?myName:'';
  const value=id=>$(id)?.value||'';
  const canRequest=()=>SERVER_CONNECTED&&SYSTEM_MODE==='VIEWER';
  const REQUEST_ONLY='조회용 WLR OnAir가 켜져 있고 연결되어 있을 때만 신청할 수 있습니다.';
  // Never mention the receipt token: to an employee it is simply "the PC that sent the request".
  const OTHER_PC='신청한 PC에서만 수정·철회할 수 있습니다. 신청 권한 복구로 연결할 수 있습니다.';
  const TYPE_LABEL={SWAP:'맞교환',SUBSTITUTE:'대체 근무',ABSENCE:'휴가·부재',ABSENCE_CANCEL:'휴가·부재 취소'};
  const typeLabel=type=>TYPE_LABEL[type]||'신청';
  const isAmendment=r=>r?.request_type==='ABSENCE'&&Number(r.amends_request_id)>0;
  const amendmentNotice='관리자가 변경을 승인하기 전까지 기존 승인 일정이 유지됩니다.';
  const noDutyRestore='종전에 해제된 당직은 자동 복원되지 않습니다.';
  const absenceSummary=a=>a?`${a.start} ~ ${a.end||a.start} · ${a.type||''}${a.segments?.length>1?' · 실제 '+a.segments.map(r=>r.start===r.end?r.start:r.start+' ~ '+r.end).join(', '):''}`:'';
  const requestLabel=r=>isCancellation(r)?cancellationType(r)+' 취소':isAmendment(r)?'휴가·부재 변경':typeLabel(r?.request_type);
  const label=key=>ui().dateLabel(key);
  const md=key=>{const m=/^(\d{4})-(\d{2})-(\d{2})$/.exec(String(key||''));return m?`${Number(m[2])}/${Number(m[3])}`:String(key||'');};
  const addDays=(key,days)=>{const [y,m,d]=String(key).split('-').map(Number);return new Date(Date.UTC(y,m-1,d+days)).toISOString().slice(0,10);};
  const hasDuty=(name,key)=>!noDutyMap[key]&&(scheduleMap[key]||[]).some(w=>w.name===name);
  const offDay=key=>typeof isOffDayKey==='function'?!!isOffDayKey(key):false;
  const genderOfName=name=>typeof genderOf==='function'?genderOf(name):memberList.find(m=>m.name===name)?.gender;
  const absentOn=(name,key)=>typeof isAbsent==='function'&&validDate(key)?!!isAbsent(name,key):false;
  const majorDay=key=>(typeof majorDutyBlockStart==='function'&&!!majorDutyBlockStart(key))||(typeof isMajorMainDay==='function'&&!!isMajorMainDay(key));
  function educationInputs(prefix){return `<fieldset class="ur-education" id="${prefix}-group"><legend>교육 안내 <span>모두 선택 입력</span></legend><p class="ur-sub">신청 사유와 별도로 일정에 공개되는 안내입니다. 비워 두어도 저장할 수 있습니다.</p><div class="ur-education-grid">${Object.entries(educationFields).map(([key,label])=>`<label for="${prefix}-${key}">${label}${['content','notes'].includes(key)?`<textarea id="${prefix}-${key}" rows="2" maxlength="2000"></textarea>`:`<input id="${prefix}-${key}" maxlength="500">`}</label>`).join('')}</div></fieldset>`;}
  function fillEducation(prefix,details){for(const[key,text]of Object.entries(education(details)))if($(prefix+'-'+key))$(prefix+'-'+key).value=text;}
  function readEducation(prefix){return Object.fromEntries(Object.keys(educationFields).map(key=>[key,value(prefix+'-'+key)]));}
  function showEducation(prefix,type){if($(prefix+'-group'))$(prefix+'-group').hidden=!isEducation(type);}
  function educationDetailsHtml(details){const entries=Object.entries(education(details)).filter(([,text])=>text.trim());return entries.length?'<dl class="ur-education-details">'+entries.map(([key,text])=>`<dt>${escape(educationFields[key])}</dt><dd>${escape(text)}</dd>`).join('')+'</dl>':'<p class="ur-sub">등록된 교육 안내가 없습니다.</p>';}
  function showErrors(fields,errors){let first=null;for(const id of fields){const field=$(id);if(!field)continue;let error=$(id+'-error');if(!error){error=document.createElement('span');error.id=id+'-error';error.className='ur-field-error';error.setAttribute('role','status');field.insertAdjacentElement?.('afterend',error);}const text=errors[id]||'';if(error){error.textContent=text;error.hidden=!text;}if(text){field.setAttribute('aria-invalid','true');field.setAttribute('aria-describedby',id+'-error');first??=field;}else{field.removeAttribute('aria-invalid');field.removeAttribute('aria-describedby');}}first?.focus();return !first;}
  // v3.18.0 실패 문장은 화면 공통 도우미로 만든다(영어 오류·숫자 코드를 그대로 보이지 않는다).
  function friendly(error,response,action,body){if(typeof friendlyFetchError==='function')return friendlyFetchError(error,response,action,body);const d=body?.detail;return typeof d==='string'&&/[가-힣]/.test(d)?d:`${action} 중 오류가 났습니다. 잠시 후 다시 시도하고, 계속되면 관리자에게 알려 주세요.`;}
  async function readBody(res){try{return await res.json();}catch{return {};}}
  function failure(res,body){const error=new Error('요청이 처리되지 않았습니다.');error.status=res.status;error.detail=body?.detail;return error;}
  function confirmBox(title,message,ok){return typeof askConfirm==='function'?askConfirm({title,message,ok}):Promise.resolve(confirm(message));}
  function offlineText(){return typeof connectionLost==='function'&&connectionLost()?(typeof CONN!=='undefined'&&!CONN.server?'프로그램과 연결이 끊겨 보낼 수 없습니다. 연결되면 다시 보낼 수 있습니다. 입력한 내용은 그대로 남아 있습니다.':'공유 폴더에 연결할 수 없어 보낼 수 없습니다. 연결되면 다시 보낼 수 있습니다. 입력한 내용은 그대로 남아 있습니다.'):'';}
  function requestErrors(dto,ids){const errors={},change=dto.request_type!=='ABSENCE';if(!member(dto.requester_name))errors[ids.name]='신청자 이름을 선택하세요.';if(!validDate(dto.target_date))errors[ids.start]=change?'바꿀 내 당직 날짜를 선택하세요.':'시작일을 선택하세요.';if(String(dto.reason||'').length>500)errors[ids.reason]='사유는 500자 이내로 입력하세요.';
    if(dto.request_type==='ABSENCE'){if(!absenceTypes.includes(dto.absence_type))errors[ids.type]='휴가·부재 구분을 선택하세요.';if(!validDate(dto.absence_end_date))errors[ids.end]='종료일을 선택하세요.';else if(validDate(dto.target_date)&&dto.target_date>dto.absence_end_date)errors[ids.end]='종료일은 시작일 이후여야 합니다.';else if(validDate(dto.target_date)&&!adminDatePlan(dto.absence_type,dto.target_date,dto.absence_end_date).ranges.length)errors[ids.start]='선택한 기간이 모두 토·일요일 또는 공휴일입니다. 휴가 날짜를 다시 선택하세요.';}
    else{const swap=dto.request_type==='SWAP',partner=swap?dto.swap_worker:dto.substitute_name;if(!member(partner))errors[ids.partner]=swap?'맞교환할 사람을 선택하세요.':'대신 근무할 사람을 선택하세요.';else if(partner===dto.requester_name)errors[ids.partner]='신청자와 다른 사람을 선택하세요.';if(swap){if(!validDate(dto.swap_date))errors[ids.end]='교환할 날짜를 선택하세요.';else if(dto.swap_date===dto.target_date)errors[ids.end]='서로 다른 당직 날짜를 선택하세요.';}}
    return errors;
  }
  function absenceDTO(){const type=value('absence-req-type'),end=value('absence-req-end');return {requester_name:value('absence-req-name'),target_date:value('absence-req-start'),request_type:'ABSENCE',absence_type:type,absence_end_date:end,substitute_name:type,swap_date:end,reason:value('absence-req-reason').trim(),absence_details:readEducation('ur-req-edu')};}
  function changeEligibilityErrors(dto){const errors={},source=dto.target_date,partner=dto.request_type==='SWAP'?dto.swap_worker:dto.substitute_name,target=dto.swap_date;const sourceNames=(scheduleMap[source]||[]).map(w=>w.name);if(!validDate(source)||!member(dto.requester_name)||!member(partner))return errors;
    if(source<todayStr)errors['ur-change-date']='지난 당직은 바꿀 수 없습니다. 오늘 이후 당직을 선택하세요.';
    else if(noDutyMap[source]||!sourceNames.includes(dto.requester_name))errors['ur-change-date']='신청자의 당직이 있는 날짜를 선택하세요.';
    if(closedMonths.includes(source.slice(0,7)))errors['ur-change-date']='마감된 달의 당직은 변경할 수 없습니다.';
    if(majorDay(source))errors['ur-change-date']='명절 당직은 관리자에게 변경을 문의하세요.';
    if(genderOfName(dto.requester_name)!==genderOfName(partner))errors['ur-change-partner']='같은 성별인 사람을 선택하세요.';
    if(sourceNames.includes(partner)||absentOn(partner,source))errors['ur-change-partner']='이 날짜에 이미 당직이 있거나 휴가·부재인 사람은 선택할 수 없습니다.';
    if(dto.request_type==='SWAP'&&validDate(target)){const targetNames=(scheduleMap[target]||[]).map(w=>w.name);
      if(target<todayStr)errors['ur-change-otherdate']='지난 날짜는 교환할 수 없습니다. 오늘 이후 날짜를 선택하세요.';
      else if(noDutyMap[target]||!targetNames.includes(partner))errors['ur-change-otherdate']='상대의 당직이 있는 날짜를 선택하세요.';
      if(closedMonths.includes(target.slice(0,7)))errors['ur-change-otherdate']='마감된 달의 당직은 변경할 수 없습니다.';
      if(offDay(source)!==offDay(target))errors['ur-change-otherdate']='평일은 평일끼리, 주말·공휴일은 서로 교환하세요.';
      if(majorDay(target))errors['ur-change-otherdate']='명절 당직은 관리자에게 변경을 문의하세요.';
      if(targetNames.includes(dto.requester_name)||absentOn(dto.requester_name,target))errors['ur-change-otherdate']='교환할 날짜에 이미 내 당직이 있거나 휴가·부재입니다.';
      if(window.OnAirPersonal?.swapEligibility&&!window.OnAirPersonal.swapEligibility(dto.requester_name,source,partner,target)&&!Object.keys(errors).length)errors['ur-change-otherdate']='일정이 바뀌어 이 교환을 신청할 수 없습니다. 다른 날짜를 선택하세요.';
    }return errors;
  }
  // 3.18.0 QA: 교육을 '휴가 기간'으로 적던 확인 창. 구분에 맞춰 교육 기간·휴가 기간·부재 기간으로 쓴다.
  function periodWord(type){const t=String(type||'');return t.includes('교육')?'교육 기간':(/휴가|연차/.test(t)?'휴가 기간':'부재 기간');}
  function requestSummary(dto){const lines=[`신청자: ${dto.requester_name}`,`구분: ${typeLabel(dto.request_type)}`];
    if(dto.request_type==='SWAP')lines.push(`내 당직: ${label(dto.target_date)}`,`상대: ${dto.swap_worker} · 교환일: ${label(dto.swap_date)}`);
    else if(dto.request_type==='SUBSTITUTE')lines.push(`내 당직: ${label(dto.target_date)}`,`대신 근무할 사람: ${dto.substitute_name}`,`대체 근무 0점 처리: ${dto.is_zero_score?'예 (점수 제외)':'아니오 (점수 반영)'}`);
    else{const end=dto.absence_end_date||dto.target_date,plan=adminDatePlan(dto.absence_type,dto.target_date,end);lines.push(`${periodWord(dto.absence_type)}: ${label(dto.target_date)} ~ ${label(end)}${plan.days?` (${plan.days}일)`:''}`,`휴가·부재 구분: ${dto.absence_type}`);if(plan.excluded.length)lines.push(`실제 휴가 ${plan.days}일 · 토·일·공휴일 ${plan.excluded.length}일 제외`,'신청 구간: '+plan.ranges.map(r=>r.start===r.end?r.start:r.start+' ~ '+r.end).join(', '));}
    lines.push(`사유: ${String(dto.reason||'').trim()||'(적지 않음)'}`);return lines.join('\n');}
  function describeRequest(r){if(r.request_type==='SWAP')return `${label(r.target_date)} ↔ ${label(r.swap_date)} · ${r.swap_worker||''}`;if(r.request_type==='SUBSTITUTE')return `${label(r.target_date)} → ${r.substitute_name||''}`;return `${label(r.target_date)} ~ ${label(r.absence_end_date||r.swap_date||r.target_date)} · ${r.absence_type||r.substitute_name||''}`;}
  function amendmentComparison(r,withEducation=false,approval=false){
    if(!isAmendment(r)||!r.amendment_original)return '';
    const old=r.amendment_original,next={start:r.target_date,end:r.absence_end_date||r.swap_date||r.target_date,type:r.absence_type||r.substitute_name,education:r.absence_details};
    const summary=value=>approval
      ? approvalPeriodHtml(value.start,value.end||value.start)+(old.type!==next.type?' · '+escape(value.type||''):'')+
        (value.segments?.length>1?`<span class="ur-sub approval-segments">실제 구간 ${value.segments.map(span=>approvalPeriodHtml(span.start,span.end)).join(', ')}</span>`:'')
      : `<span style="display:inline-block;white-space:nowrap;">${escape(value.start)}</span> ~ <span style="display:inline-block;white-space:nowrap;">${escape(value.end||value.start)}</span> · ${escape(value.type||'')}${value.segments?.length>1?`<span class="ur-sub">실제 ${escape(value.segments.map(r=>r.start===r.end?r.start:r.start+' ~ '+r.end).join(', '))}</span>`:''}`;
    const hasEducation=[old,next].some(value=>isEducation(value.type)&&Object.values(education(value.education)).some(text=>text.trim()));
    const educationBlock=(value,title)=>withEducation&&isEducation(value.type)&&(!approval||hasEducation)?`<details class="ur-amend-education"><summary>${title} 교육 안내</summary>${educationDetailsHtml(value.education)}</details>`:'';
    return `<div class="ur-amend-comparison${approval?' approval-comparison':''}"><div class="before"><b>${approval?'기존 승인':'변경 전'}</b><span>${summary(old)}</span></div><div class="after"><b>변경 요청</b><span>${summary(next)}</span></div></div>`+educationBlock(old,'기존')+educationBlock(next,'변경 요청');
  }
  function showAmendmentContext(){
    const box=$('ur-amendment-context'),context=absenceContext;
    if(!box)return;box.hidden=!context?.original;
    box.innerHTML=context?.original?`<b>기존 승인 일정</b><p>${escape(absenceSummary(context.original))}</p><p>${amendmentNotice}</p><p>${noDutyRestore}</p>`:'';
  }

  // ── 휴가·부재 신청 ───────────────────────────────────────────
  const absenceFieldIds=['absence-req-name','absence-req-type','absence-req-start','absence-req-end','absence-req-reason'];
  // v3.18.1 신청 사유는 휴가·부재·맞교환·대체 근무 모두 선택 입력이다. 필수 표시(*)는 사유 칸에 붙이지 않는다.
  const absenceRequiredIds=absenceFieldIds.filter(id=>id!=='absence-req-reason');
  function saveAbsenceDraft(){writeDraft('absence',{request:absenceDTO(),context:absenceContext});}
  function firstFreeDay(name){let key=addDays(todayStr,1);for(let i=0;i<90;i++,key=addDays(key,1))if(!name||!hasDuty(name,key))return key;return addDays(todayStr,1);}
  function setDefaultAbsenceDates(){const day=firstFreeDay(value('absence-req-name'));if($('absence-req-start'))$('absence-req-start').value=day;if($('absence-req-end'))$('absence-req-end').value=day;}
  function markAbsenceRequired(modal){for(const id of absenceRequiredIds){const field=modal.querySelector?.(`label[for="${id}"]`);if(field&&!field.querySelector?.('.ur-req'))field.insertAdjacentHTML?.('beforeend',' <b class="ur-req" aria-hidden="true">*</b>');$(id)?.setAttribute('aria-required','true');}
    const reason=$('absence-req-reason');if(reason){reason.placeholder='';reason.removeAttribute?.('aria-required');}
    if(!modal.querySelector?.('#ur-absence-required'))modal.querySelector?.('.modal-hint')?.insertAdjacentHTML('afterend','<p id="ur-absence-required" class="ur-sub ur-required-note">* 표시는 꼭 입력하세요. 날짜를 고르면 해제될 내 당직을 바로 보여 드립니다.</p>');}
  function connectAbsenceForm(){const modal=$('absence-request-modal');if(!modal)return;if(!modal.querySelector('#ur-req-edu-group'))modal.querySelector('#absence-impact')?.insertAdjacentHTML('beforebegin',educationInputs('ur-req-edu'));markAbsenceRequired(modal);
    if(!modal.querySelector('#ur-absence-newdraft')){const button=document.createElement('button');button.id='ur-absence-newdraft';button.type='button';button.textContent='새로 작성';button.addEventListener('click',()=>newAbsenceDraft());$('btn-submit-absence-request')?.parentNode?.insertBefore(button,$('btn-submit-absence-request'));}
    if(!modal.dataset.urDraft){modal.dataset.urDraft='1';modal.addEventListener('input',()=>{saveAbsenceDraft();});modal.addEventListener('change',event=>{const id=event?.target?.id;if(id==='absence-req-start'||id==='absence-req-end'){absenceDatesTouched=true;absenceImpactArmed=true;}else if(id==='absence-req-name'&&!absenceDatesTouched)setDefaultAbsenceDates();showEducation('ur-req-edu',value('absence-req-type'));saveAbsenceDraft();renderAbsenceImpact();if(id==='absence-req-start'||id==='absence-req-end'||id==='absence-req-name')window.OnAirAbsenceRequestCalendar?.sync(id);else if(id==='absence-req-type')window.OnAirAbsenceRequestCalendar?.render();});}
  }
  // The duty-release warning appears only once dates are the user's own choice (typed, drafted or edited).
  const legacyImpact=renderAbsenceImpact;
  renderAbsenceImpact=function(){
    const box=$('absence-impact');if(!box)return;
    if(!absenceImpactArmed){if(box.style)box.style.display='none';return;}
    if(!isVacationType(value('absence-req-type')))return legacyImpact.apply(this,arguments);
    const name=value('absence-req-name'),plan=adminDatePlan(value('absence-req-type'),value('absence-req-start'),value('absence-req-end'));
    const hits=Object.keys(scheduleMap).filter(key=>plan.ranges.some(r=>key>=r.start&&key<=r.end)&&!noDutyMap[key]&&(scheduleMap[key]||[]).some(w=>w.name===name)).sort();
    box.style.display=hits.length||plan.excluded.length?'':'none';
    box.innerHTML=(plan.excluded.length?`실제 휴가 <b>${plan.days}일</b> · 토·일·공휴일 ${plan.excluded.length}일 제외`:'')+(hits.length?`${plan.excluded.length?'<br>':''}승인 시 <b>내 당직 ${hits.length}건</b>(${escape(hits.join(', '))})이 해제됩니다.`:'');
  };
  const legacyOpenAbsenceRequest=openAbsenceRequestModal;
  const legacyCloseAbsenceRequest=closeAbsenceRequestModal;
  function openAbsence(seed,options={}){if(!canRequest()){showToast(REQUEST_ONLY,true);return;}const draft=seed?null:readDraft('absence');if(!$('absence-request-modal').classList.contains('active'))absenceOpener=document.activeElement;legacyOpenAbsenceRequest();if(!$('absence-request-modal').classList.contains('active'))return;ui().raise?.('absence-request-modal');connectAbsenceForm();const dto=seed||draft?.request;absenceContext=options.editToken||options.amendToken?{...options,requester:seed?.requester_name}:draft?.context||null;
    if(dto){for(const[id,text]of Object.entries({'absence-req-name':dto.requester_name,'absence-req-type':dto.absence_type||dto.substitute_name,'absence-req-start':dto.target_date,'absence-req-end':dto.absence_end_date||dto.swap_date||dto.target_date,'absence-req-reason':dto.reason}))$(id).value=text||'';fillEducation('ur-req-edu',dto.absence_details);absenceDatesTouched=!!(dto.target_date||dto.absence_end_date);}
    else{$('absence-req-name').value=personalName();fillEducation('ur-req-edu',{});absenceDatesTouched=false;setDefaultAbsenceDates();}
    absenceImpactArmed=absenceDatesTouched;
    const fixed=!!(absenceContext?.editToken||absenceContext?.amendToken);
    $('absence-req-name').disabled=fixed;$('absence-request-title').textContent=absenceContext?.amendToken?'승인된 휴가·부재 변경 요청':absenceContext?.editToken?(absenceContext.original?'승인 대기 변경 요청 수정':'승인 대기 신청 수정'):'휴가·부재 신청';$('btn-submit-absence-request').textContent=absenceContext?.amendToken?'변경 요청하기':'신청하기';showAmendmentContext();showEducation('ur-req-edu',value('absence-req-type'));showErrors(absenceFieldIds,{});renderAbsenceImpact();saveAbsenceDraft();window.OnAirAbsenceRequestCalendar?.open();$(fixed?'absence-req-type':'absence-req-name')?.focus();
  }
  openAbsenceRequestModal=()=>openAbsence();
  function closeAbsenceEditor(){window.OnAirAbsenceRequestCalendar?.close();legacyCloseAbsenceRequest();if(absenceOpener?.isConnected&&!absenceOpener.disabled)absenceOpener.focus();absenceOpener=null;}
  closeAbsenceRequestModal=()=>{saveAbsenceDraft();closeAbsenceEditor();};
  // Only the topmost absence editor owns Tab; Escape keeps the shared close routing.
  document.addEventListener('keydown',event=>{
    const modal=$('absence-request-modal');
    if(event.key!=='Tab'||!modal?.classList.contains('active'))return;
    const top=typeof MODAL_CLOSERS!=='undefined'?[...MODAL_CLOSERS].reverse().find(([id])=>isModalOpen(id)):null;
    if(top&&top[0]!==modal.id)return;
    const fields=[...modal.querySelectorAll('button,input,select,textarea,a[href],summary,[tabindex]')].filter(field=>!field.disabled&&field.tabIndex>=0&&field.getClientRects().length);
    if(!fields.length){event.preventDefault();return;}
    const first=fields[0],last=fields.at(-1),active=document.activeElement;
    if(event.shiftKey&&(active===first||!modal.contains(active))){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&(active===last||!modal.contains(active))){event.preventDefault();first.focus();}
  },true);
  function newAbsenceDraft(){clearDraft('absence');absenceContext=null;openAbsence();}

  // ── 전송 (신청·수정 공통) ─────────────────────────────────────
  async function sendRequest(dto,context,kind,button){if(!canRequest()){showToast(REQUEST_ONLY,true);return false;}if(offlineText()){showToast(offlineText(),true);return false;}if(button?.disabled)return false;
    const selectedPlan=()=>dto.request_type==='ABSENCE'?JSON.stringify(adminDatePlan(dto.absence_type,dto.target_date,dto.absence_end_date||dto.target_date)):'';
    const confirmedPlan=selectedPlan();
    const ownerToken=context?.editToken||context?.amendToken,amending=!!context?.amendToken;
    if(ownerToken&&(!Object.hasOwn(myRequestStatuses,ownerToken)||dto.requester_name!==context.requester)){showToast(OTHER_PC,true);return false;}
    const extra=context?.original?`\n\n기존 승인 일정: ${absenceSummary(context.original)}\n${amendmentNotice}\n${noDutyRestore}`:'\n\n신청자 이름이 본인인지 확인하세요.';
    if(!await confirmBox(amending?'승인 일정 변경 요청':context?.editToken?'신청 수정':'신청 확인',`${amending?'아래 내용으로 일정 변경을 요청할까요?':context?.editToken?'수정한 내용으로 다시 신청할까요?':'아래 내용으로 신청할까요?'}\n\n${requestSummary(dto)}${extra}`,amending?'변경 요청하기':context?.editToken?'수정해서 신청하기':'신청하기'))return false;
    if(confirmedPlan!==selectedPlan()){renderAbsenceImpact();window.OnAirAbsenceRequestCalendar?.render();showToast('확인 중 공휴일이 변경되었습니다. 휴가 날짜를 확인한 뒤 다시 신청하세요.',true);return false;}
    const text=button?.textContent;if(button){button.disabled=true;button.textContent='전송 중…';}
    try{const url=ownerToken?'/api/duty/request/manage':'/api/duty/request-change';const payload=ownerToken?{receipt_token:ownerToken,action:amending?'AMEND':'EDIT',request:dto}:dto;const res=window.OnAirRequestAccess?await window.OnAirRequestAccess.submitDurable({action:amending?'AMEND':ownerToken?'EDIT':'SUBMIT',request:dto,receiptToken:ownerToken},()=>fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})):await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const body=await readBody(res);
      if(!res.ok){if(context?.editToken&&res.status===409&&await showProcessed(context.editToken,dto))return 'processed';showToast(friendly(null,res,'신청',body),true);return false;}
      const token=context?.editToken||body.receiptToken;if(token){trackMyRequest(token);if(typeof rememberMyRequestCopy==='function')rememberMyRequestCopy(token,{...dto,...(context?.original?{amends_request_id:context.amendsRequestId,amendment_original:context.original}:{})});}clearDraft(kind);addNotif(`${dto.requester_name} 님의 ${typeLabel(dto.request_type)} ${amending?'변경 요청을 접수':context?.editToken?'수정 내용을 전달':'신청을 접수'}했습니다.${context?.original?' '+amendmentNotice:''}`,{kind:'request',requestId:body.requestId||body.request?.id,receiptToken:token,requesterName:dto.requester_name,targetDate:dto.target_date,requestType:dto.request_type,status:'PENDING'});showToast(body.message||'신청 내용을 전달했습니다.');if(token)checkRequestOutcomes();refreshPending();if($('myreq-modal')?.classList.contains('active'))loadMyRequests();return true;}
    catch(error){if(typeof isNetworkError==='function'&&isNetworkError(error)&&typeof markServerDown==='function')markServerDown();showToast(friendly(error,null,'신청'),true);return false;}finally{if(button){button.disabled=false;button.textContent=text;}}
  }
  submitAbsenceRequest=async()=>{const dto=absenceDTO();saveAbsenceDraft();const errors=requestErrors(dto,{name:'absence-req-name',type:'absence-req-type',start:'absence-req-start',end:'absence-req-end',reason:'absence-req-reason'});if(!showErrors(absenceFieldIds,errors))return;const result=await sendRequest(dto,absenceContext,'absence',$('btn-submit-absence-request'));if(result){absenceContext=null;closeAbsenceEditor();}};

  // ── 당직 변경 신청 (맞교환·대체 근무) — 하나의 신청서, 한 번의 제출 ──────
  function futureDuties(name,options={}){return Object.keys(scheduleMap).filter(k=>validDate(k)&&k>=todayStr&&k!==options.except&&hasDuty(name,k)&&!closedMonths.includes(k.slice(0,7))&&(options.off===undefined||offDay(k)===options.off)).sort();}
  function swapDatesFor(name,source,partner){const P=window.OnAirPersonal;return futureDuties(partner,{except:source,off:validDate(source)?offDay(source):undefined}).filter(d=>validDate(source)&&P?.swapEligibility?P.swapEligibility(name,source,partner,d):!hasDuty(name,d)&&!majorDay(d));}
  // 3.18.0 QA: 해가 바뀌는 목록에서 날짜가 몇 년도인지 모르던 문제. 올해가 아니면 연도까지 쓴다('2027년 12월 20일(월)').
  function yearAwareLabel(key){return validDate(key)&&String(key).slice(0,4)!==String(todayStr||'').slice(0,4)?ui().dateLabel(key,true):label(key);}
  function dutyOptionLabel(key){return `${yearAwareLabel(key)}${offDay(key)?' · 주말·공휴일':''}`;}
  function optionsHtml(values,current,labelOf,empty){let html=`<option value="">${escape(empty)}</option>`+values.map(v=>`<option value="${escape(v)}">${escape(labelOf(v))}</option>`).join('');if(current&&!values.includes(current))html+=`<option value="${escape(current)}">${escape(labelOf(current))} · ${validDate(current)&&current<todayStr?'지난 날짜':'선택할 수 없음'}</option>`;return html;}
  function setSelect(id,html,current){const el=$(id);if(!el)return;el.innerHTML=html;el.value=current||'';}
  function zeroDefault(date){return validDate(date)&&offDay(date)&&!!(typeof dutyRule!=='undefined'&&dutyRule.substituteScoreZero);}
  function zeroNote(date){if(typeof dutyRule!=='undefined'&&!dutyRule.substituteScoreZero)return '지금 규칙은 대체 근무도 점수에 넣습니다. 0점 처리 여부는 관리자가 승인할 때 확정합니다.';if(validDate(date)&&offDay(date))return '주말·공휴일 대체 근무는 규칙에 따라 0점 처리가 기본입니다. 점수에 넣으려면 선택을 해제하세요.';return '평일 대체 근무는 점수(1점)에 그대로 넣는 것이 기본입니다.';}
  // changed: 'init' keeps seeded values (shown as "선택할 수 없음" when stale); other changes drop values that no longer fit.
  // A <select> drops a value it has no option for, so seeded values are passed in rather than read back.
  function refreshChangeOptions(changed,seed){const type=value('ur-change-type')==='SUBSTITUTE'?'SUBSTITUTE':'SWAP',name=value('ur-change-name'),keep=changed==='init',pick=(key,id)=>seed?String(seed[key]||''):value(id);
    const dates=member(name)?futureDuties(name):[];let date=pick('date','ur-change-date');if(!keep&&changed==='name'&&!dates.includes(date))date=dates[0]||'';if(!date&&dates.length&&(changed==='init'||changed==='name'))date=dates[0];
    setSelect('ur-change-date',optionsHtml(dates,date,dutyOptionLabel,!member(name)?'신청자를 먼저 선택하세요':dates.length?'내 당직 날짜 선택':'예정된 내 당직이 없습니다'),date);
    const gender=genderOfName(name),roster=(scheduleMap[date]||[]).map(w=>w.name);let partner=pick('partner','ur-change-partner'),partners;
    if(type==='SWAP')partners=member(name)?memberList.filter(m=>m.name!==name&&m.gender===gender&&(validDate(date)?swapDatesFor(name,date,m.name).length:futureDuties(m.name).length)).map(m=>m.name):[];
    else partners=member(name)?memberList.filter(m=>m.name!==name&&m.gender===gender&&!roster.includes(m.name)&&!absentOn(m.name,date)).map(m=>m.name):[];
    if(!keep&&!partners.includes(partner))partner='';
    setSelect('ur-change-partner',optionsHtml(partners,partner,x=>x,!member(name)?'신청자를 먼저 선택하세요':partners.length?(type==='SWAP'?'맞교환할 사람 선택':'대신 근무할 사람 선택'):'조건에 맞는 사람이 없습니다'),partner);
    const partnerLabel=$('ur-change-partner-label');if(partnerLabel)partnerLabel.innerHTML=`${type==='SWAP'?'맞교환할 사람':'대신 근무할 사람'} <b class="ur-req" aria-hidden="true">*</b>`;
    const others=type==='SWAP'&&member(partner)?swapDatesFor(name,date,partner):[];let other=pick('other','ur-change-otherdate');if(!keep&&!others.includes(other))other='';if(!other&&others.length)other=others[0];
    setSelect('ur-change-otherdate',optionsHtml(others,other,dutyOptionLabel,!member(partner)?'상대를 먼저 선택하세요':others.length?'교환할 날짜 선택':'교환할 수 있는 날짜가 없습니다'),type==='SWAP'?other:'');
    if($('ur-change-otherdate-wrap'))$('ur-change-otherdate-wrap').hidden=type!=='SWAP';if($('ur-change-zero-wrap'))$('ur-change-zero-wrap').hidden=type!=='SUBSTITUTE';
    if(!zeroTouched&&$('ur-change-zero'))$('ur-change-zero').checked=zeroDefault(date);if($('ur-change-zero-note'))$('ur-change-zero-note').textContent=zeroNote(date);
  }
  function changeDTO(){const type=value('ur-change-type')==='SUBSTITUTE'?'SUBSTITUTE':'SWAP';return {requester_name:value('ur-change-name'),request_type:type,target_date:value('ur-change-date'),swap_date:type==='SWAP'?value('ur-change-otherdate'):null,swap_worker:type==='SWAP'?value('ur-change-partner'):null,substitute_name:type==='SUBSTITUTE'?value('ur-change-partner'):null,is_zero_score:type==='SUBSTITUTE'&&!!$('ur-change-zero')?.checked,reason:value('ur-change-reason').trim()};}
  function saveChangeDraft(){if(!changeOpening&&$('ur-change-modal')?.classList.contains('active'))writeDraft('change',{request:changeDTO(),context:changeContext});}
  const changeFields=['ur-change-name','ur-change-type','ur-change-date','ur-change-partner','ur-change-otherdate','ur-change-reason'];
  const req=' <b class="ur-req" aria-hidden="true">*</b>';
  function openChange(seed,options={}){if(!canRequest())return showToast(REQUEST_ONLY,true);const saved=seed?null:readDraft('change'),dto=seed||saved?.request||{requester_name:personalName(),request_type:'SWAP',target_date:''};changeContext=options.editToken?{editToken:options.editToken,requester:dto.requester_name}:saved?.context||null;
    // An explicit 0점 choice (edit, retry, draft) is kept; otherwise the rule decides per date.
    zeroTouched=dto.request_type==='SUBSTITUTE'&&(typeof dto.is_zero_score==='boolean'||dto.is_zero_score===0||dto.is_zero_score===1);
    const names=`<option value="">이름 선택</option>`+memberList.map(m=>`<option value="${escape(m.name)}">${escape(m.name)}</option>`).join('');changeOpening=true;
    const modal=ui().open('ur-change-modal',changeContext?.editToken?'승인 대기 신청 수정':'당직 변경 신청',`<p class="ur-sub ur-required-note">* 표시는 꼭 입력하세요. 내 이름 설정은 본인 확인이 아니므로 신청자 이름을 확인하세요.</p><div class="ur-form"><label for="ur-change-name">신청자${req}</label><select id="ur-change-name" aria-required="true">${names}</select><label for="ur-change-type">신청 구분${req}</label><select id="ur-change-type"><option value="SWAP">맞교환</option><option value="SUBSTITUTE">대체 근무</option></select><label for="ur-change-date">내 당직 날짜${req}</label><select id="ur-change-date" aria-required="true"></select><label for="ur-change-partner" id="ur-change-partner-label">맞교환할 사람${req}</label><select id="ur-change-partner" aria-required="true"></select><div id="ur-change-otherdate-wrap" class="ur-form-row"><label for="ur-change-otherdate">교환할 날짜${req}</label><select id="ur-change-otherdate" aria-required="true"></select></div><div id="ur-change-zero-wrap" class="ur-form-row"><span class="ur-form-gap"></span><div><label class="ur-check"><input id="ur-change-zero" type="checkbox">대체 근무 0점 처리</label><p id="ur-change-zero-note" class="ur-sub ur-zero-note"></p></div></div><label for="ur-change-reason">사유 (선택)</label><div class="ur-field"><textarea id="ur-change-reason" rows="3" maxlength="500" placeholder=""></textarea><p id="ur-change-reason-hint" class="ur-hint">필요하면 적어 주세요. (500자 이내)</p></div></div><div class="ur-footer"><button type="button" id="ur-change-new">새로 작성</button><button type="button" onclick="OnAirUI.close('ur-change-modal')">닫기</button><button type="button" id="ur-change-submit" class="btn-main">신청하기</button></div>`,{width:620,onClose:()=>{if(!changeOpening)writeDraft('change',{request:changeDTO(),context:changeContext});}});
    for(const[id,text]of Object.entries({'ur-change-name':dto.requester_name,'ur-change-type':dto.request_type==='SUBSTITUTE'?'SUBSTITUTE':'SWAP','ur-change-reason':dto.reason}))$(id).value=text||'';
    $('ur-change-name').disabled=!!changeContext?.editToken;if(zeroTouched)$('ur-change-zero').checked=dto.is_zero_score!==false&&dto.is_zero_score!==0;
    refreshChangeOptions('init',{date:dto.target_date,partner:dto.request_type==='SUBSTITUTE'?dto.substitute_name:dto.swap_worker,other:dto.request_type==='SUBSTITUTE'?'':dto.swap_date});changeOpening=false;
    for(const[id,kind]of [['ur-change-name','name'],['ur-change-type','type'],['ur-change-date','date'],['ur-change-partner','partner']])$(id).addEventListener('change',()=>{refreshChangeOptions(kind);saveChangeDraft();});
    $('ur-change-zero').addEventListener('change',()=>{zeroTouched=true;saveChangeDraft();});
    if(modal&&!modal.dataset?.urChange){if(modal.dataset)modal.dataset.urChange='1';modal.addEventListener('input',saveChangeDraft);modal.addEventListener('change',saveChangeDraft);}
    $('ur-change-submit').onclick=submitChange;$('ur-change-new').onclick=()=>{clearDraft('change');changeContext=null;openChange({requester_name:personalName(),request_type:'SWAP',target_date:''});};saveChangeDraft();
  }
  async function submitChange(){const dto=changeDTO();saveChangeDraft();if(!showErrors(changeFields,{...requestErrors(dto,{name:'ur-change-name',start:'ur-change-date',end:'ur-change-otherdate',partner:'ur-change-partner',reason:'ur-change-reason'}),...changeEligibilityErrors(dto)}))return;const partner=dto.request_type==='SWAP'?dto.swap_worker:dto.substitute_name;
    const checks=dto.request_type==='SWAP'?[{name:dto.requester_name,dateKey:dto.swap_date},{name:partner,dateKey:dto.target_date}]:[{name:partner,dateKey:dto.target_date}];const override={[dto.target_date]:(scheduleMap[dto.target_date]||[]).map(w=>w.name===dto.requester_name?partner:w.name)};if(dto.request_type==='SWAP')override[dto.swap_date]=(scheduleMap[dto.swap_date]||[]).map(w=>w.name===partner?dto.requester_name:w.name);if(!await confirmConsecutiveRisk(checks,override))return;
    const result=await sendRequest(dto,changeContext,'change',$('ur-change-submit'));if(result){changeOpening=true;ui().close('ur-change-modal');changeOpening=false;changeContext=null;}}
  // Legacy date-form buttons (hidden for VIEWER) still hand over to the one composer instead of submitting.
  const legacySwap=handleSwapAction,legacySub=handleSubAction,legacyCloseDate=closeDateModal;
  handleSwapAction=async()=>{if(SYSTEM_MODE==='ADMIN')return legacySwap();const dto={requester_name:value('swap-my-worker'),request_type:'SWAP',target_date:activeModalDate,swap_date:value('swap-target-date'),swap_worker:value('swap-target-worker'),reason:value('req-reason-input')};legacyCloseDate(true);openChange(dto);};
  handleSubAction=async()=>{if(SYSTEM_MODE==='ADMIN')return legacySub();const dto={requester_name:value('sub-origin-worker'),request_type:'SUBSTITUTE',target_date:activeModalDate,substitute_name:value('sub-target-worker'),reason:value('req-reason-input')};legacyCloseDate(true);openChange(dto);};

  // ── 조회용 날짜 창: 정보 + 신청 시작 버튼만 (두 번째 입력 양식 없음) ──────────
  function ownSwapWindow(){
    if(!validDate(todayStr))return null;
    const [year,month,day]=todayStr.split('-').map(Number),targetMonth=year*12+month-1+3;
    const endYear=Math.floor(targetMonth/12),endMonth=targetMonth%12;
    // Calendar months, not 90 days: November 30 ends on February 28/29.
    const end=endYear>9999?'9999-12-31':new Date(Date.UTC(endYear,endMonth,Math.min(day,new Date(Date.UTC(endYear,endMonth+1,0)).getUTCDate()))).toISOString().slice(0,10);
    return {start:todayStr,end};
  }
  function ownSwapCandidates(name,target,partner){
    const range=ownSwapWindow();
    if(!range||!member(name)||!member(partner)||!validDate(target))return {range,rows:[]};
    // This shortcut lists all actual own assignments, including rows whose existing
    // exchange rules explain why they cannot be requested. Shared form options stay unchanged.
    const rows=Object.keys(scheduleMap).filter(date=>validDate(date)&&date>=range.start&&date<=range.end&&hasDuty(name,date)).sort().map(date=>{
      const dto={requester_name:name,request_type:'SWAP',target_date:date,swap_worker:partner,swap_date:target};
      const errors={...requestErrors(dto,{name:'ur-change-name',start:'ur-change-date',end:'ur-change-otherdate',partner:'ur-change-partner',reason:'ur-change-reason'}),...changeEligibilityErrors(dto)};
      return {date,reason:Object.values(errors)[0]||''};
    });
    return {range,rows};
  }
  function dateActions(date,workers){
    if(!canRequest()||noDutyMap[date]||!workers.length)return '';
    if(date<todayStr)return '<p class="ur-sub">지난 날짜는 신청할 수 없습니다.</p>';
    if(closedMonths.includes(date.slice(0,7)))return '<p class="ur-sub">마감된 달이라 신청할 수 없습니다. 관리자에게 문의하세요.</p>';
    if(majorDay(date))return '<p class="ur-sub">명절 당직은 관리자에게 변경을 문의하세요.</p>';
    const me=personalName(),button=(action,text,data={},reason='')=>`<button type="button" class="${action==='swap'||action==='swap-with'?'btn-main':''}" data-ur-date-action="${action}" data-date="${escape(date)}"${Object.entries(data).map(([k,v])=>` data-${k}="${escape(v)}"`).join('')}${reason?` disabled aria-disabled="true" title="${escape(reason)}"`:''}>${escape(text)}</button>`;
    if(me&&workers.includes(me))return `<div class="ur-date-actions">${button('swap','맞교환 신청')}${button('sub','대체 근무 신청')}</div>`;
    if(me){const partner=workers.find(n=>n!==me&&genderOfName(n)===genderOfName(me));if(!partner)return '';const {range,rows}=ownSwapCandidates(me,date,partner);
      if(!range)return '<p class="ur-sub">오늘 날짜를 확인한 뒤 다시 열어 주세요.</p>';
      const available=rows.filter(row=>!row.reason).length;
      return `<section class="ur-own-swaps" data-range-start="${escape(range.start)}" data-range-end="${escape(range.end)}" aria-label="맞교환할 내 당직"><div class="ur-own-swaps-heading"><b>맞교환할 내 당직</b><span>내 당직 ${rows.length}건 · 신청 가능 ${available}건</span></div><p class="ur-own-swaps-range">오늘부터 3개월 · ${escape(ui().dateLabel(range.start,true))} ~ ${escape(ui().dateLabel(range.end,true))}</p>${rows.length?`<ul class="ur-own-swaps-list" tabindex="0" aria-label="내 당직 날짜 목록">${rows.map(row=>`<li class="ur-own-swap-row${row.reason?' unavailable':''}"><div class="ur-own-swap-info"><span class="ur-own-swap-date">${escape(ui().dateLabel(row.date,true))}</span>${row.reason?`<span class="ur-own-swap-reason">${escape(row.reason)}</span>`:''}</div>${button('swap-with','맞교환 신청',{mine:row.date,partner,name:me},row.reason)}</li>`).join('')}</ul>`:'<p class="ur-sub">이 기간에 배정된 내 당직이 없습니다.</p>'}</section>`;}
    return `<p class="ur-sub">당직자 본인이면 아래에서 신청하세요. [내 이름 설정]을 하면 더 간단합니다.</p><div class="ur-date-actions">${workers.map(n=>button('as-worker',`${n} 님 당직 변경 신청`,{name:n})).join('')}</div>`;
  }
  function renderViewerDate(date){
    const panel=$('change-panel-title')?.parentNode?.parentNode;if(panel?.style)panel.style.display='none';
    const memo=$('modal-date-comment');if(memo){if(memo.style)memo.style.display='none';if(!document.querySelector('#ur-date-memo'))memo.insertAdjacentHTML?.('afterend','<div id="ur-date-memo" class="ur-date-memo"></div>');const text=String(commentMap[date]||'').trim(),box=$('ur-date-memo');box.textContent=text||'메모가 없습니다.';box.classList?.toggle('empty',!text);}
    if(!document.querySelector('#ur-date-viewer'))(panel||memo?.parentNode)?.insertAdjacentHTML?.('beforebegin','<div id="ur-date-viewer" class="ur-date-viewer"></div>');
    const workers=noDutyMap[date]?[]:(scheduleMap[date]||[]).map(w=>w.name),pending=pendingFor(null,date);
    $('ur-date-viewer').innerHTML=(workers.length?`<p class="ur-date-line"><span class="ur-date-label">당직</span>${workers.map(n=>`<b>${escape(n)}</b>`).join(', ')}</p>`:`<p class="ur-date-line">${noDutyMap[date]?'당직 없는 날입니다.':'배정된 당직자가 없습니다.'}</p>`)+
      pending.map(p=>`<p class="ur-date-line"><span class="ur-pending-badge">신청 대기</span>${escape(p.text)}</p>`).join('')+dateActions(date,workers);
  }
  const legacyOpenDate=openDateModal;
  openDateModal=date=>{legacyOpenDate(date);if(SYSTEM_MODE!=='ADMIN')renderViewerDate(date);};
  document.addEventListener('click',event=>{const node=event.target?.closest?.('[data-ur-date-action]');if(!node)return;event.preventDefault?.();const d=node.dataset,me=personalName();
    if(d.urDateAction==='swap-with'){
      if(!canRequest()){showToast(REQUEST_ONLY,true);return;}
      const row=d.name===me?ownSwapCandidates(me,d.date,d.partner).rows.find(row=>row.date===d.mine):null;
      if(node.disabled||!row||row.reason){showToast(row?.reason||'이름이나 일정이 바뀌었습니다. 내 당직 목록을 다시 확인하세요.',true);if(activeModalDate===d.date)renderViewerDate(d.date);return;}
    }
    const seed={swap:{requester_name:me,request_type:'SWAP',target_date:d.date},sub:{requester_name:me,request_type:'SUBSTITUTE',target_date:d.date},'swap-with':{requester_name:me,request_type:'SWAP',target_date:d.mine,swap_worker:d.partner,swap_date:d.date},'as-worker':{requester_name:d.name,request_type:'SWAP',target_date:d.date}}[d.urDateAction];
    if(!seed)return;legacyCloseDate(true);clearDraft('change');changeContext=null;openChange(seed);});

  // ── 신청 대기 표시 (내 일정·달력·내 신청 내역 버튼) ─────────────────────
  let pendingState={name:'',rows:[]};
  function requestDates(r,name){const out=[];if(isCancellation(r))return out;if(r.request_type==='SWAP'){if(name==null||r.requester_name===name)out.push(r.target_date);if(name==null||r.swap_worker===name)out.push(r.swap_date);}else if(r.request_type==='SUBSTITUTE'){if(name==null||r.requester_name===name)out.push(r.target_date);}else if(name==null||r.requester_name===name){const end=r.absence_end_date||r.swap_date||r.target_date;for(const k of Object.keys(scheduleMap))if(k>=r.target_date&&k<=end&&hasDuty(r.requester_name,k)&&(!r.absence_date_plan?.ranges||r.absence_date_plan.ranges.some(span=>k>=span.start&&k<=span.end)))out.push(k);}return out;}
  function pendingText(r){if(r.request_type==='SWAP')return `${r.requester_name} ${md(r.target_date)} ↔ ${r.swap_worker} ${md(r.swap_date)} 맞교환`;if(r.request_type==='SUBSTITUTE')return `${r.requester_name} ${md(r.target_date)} → ${r.substitute_name} 대체 근무`;const end=r.absence_end_date||r.swap_date||r.target_date;return `${r.requester_name} ${r.absence_type||'휴가·부재'} ${md(r.target_date)}${end!==r.target_date?' ~ '+md(end):''}`;}
  function pendingFor(name,date){return pendingState.rows.filter(r=>requestDates(r,name).includes(date)&&(name==null||[r.requester_name,r.swap_worker].includes(name))).map(r=>({id:Number(r.id),text:pendingText(r)}));}
  async function refreshPending(){if(!canRequest())return pendingState;const name=personalName();if(!name){pendingState={name:'',rows:[]};decoratePending();return pendingState;}
    try{const res=await fetch('/api/duty/my-requests?name='+encodeURIComponent(name)+'&involving=1');if(res.ok){const body=await res.json();if(personalName()===name)pendingState={name,rows:(body.requests||[]).filter(r=>r.status==='PENDING')};}}catch{}
    decoratePending();return pendingState;}
  function decoratePending(){if(SYSTEM_MODE!=='VIEWER')return;
    for(const node of document.querySelectorAll('#calendar-tbody .ur-pending-badge'))node.remove?.();
    if(pendingState.rows.length)for(const cell of document.querySelectorAll('#calendar-tbody td[data-date-key]')){const date=cell.dataset?.dateKey;for(const tag of cell.querySelectorAll?.('.worker-tag[data-worker-name]')||[]){const items=pendingFor(tag.dataset.workerName,date);if(items.length){tag.insertAdjacentHTML('beforeend',`<span class="ur-pending-badge" title="${escape(items.map(i=>i.text+' 신청 대기').join('\n'))}">신청 대기</span>`);tag.setAttribute('aria-label',`${tag.dataset.workerName} 개인 일정 보기 · 신청 대기`);}}}
    const count=pendingState.rows.filter(r=>r.requester_name===pendingState.name).length;
    const quick=document.querySelector('#ur-myreq-quick');if(quick){quick.textContent=count?`내 신청 내역 · 대기 ${count}건`:'내 신청 내역';quick.title='내 신청과 결과·반려 사유를 봅니다';}
    const line=document.querySelector('#my-next-duty .duty-line-label');if(line){line.querySelector?.('.ur-line-pending')?.remove?.();if(count)line.insertAdjacentHTML('beforeend',`<span class="ur-line-pending" title="승인을 기다리는 내 신청 ${count}건">대기 ${count}건</span>`);}
  }
  function ensureQuickButton(){if(SYSTEM_MODE!=='VIEWER'||document.querySelector('#ur-myreq-quick'))return;const anchor=[...document.querySelectorAll('#menu-row > button.viewer-only')].at(-1);if(!anchor?.insertAdjacentHTML)return;anchor.insertAdjacentHTML('afterend','<button type="button" id="ur-myreq-quick" class="viewer-only ur-myreq-quick"><svg class="ico" aria-hidden="true"><use href="#i-inbox"/></svg><span>내 신청 내역</span></button>');const quick=document.querySelector('#ur-myreq-quick');quick?.addEventListener('click',()=>openMyRequestsModal());if(quick?.style)quick.style.display=SERVER_CONNECTED?'':'none';}
  const legacyRenderCalendar=renderCalendar;renderCalendar=function(){const result=legacyRenderCalendar.apply(this,arguments);decoratePending();return result;};
  const legacyRenderName=renderMyNameUI;renderMyNameUI=function(){const result=legacyRenderName.apply(this,arguments);decoratePending();return result;};
  const legacyFetchAll=fetchAllData;fetchAllData=async function(...args){const result=await legacyFetchAll.apply(this,args);ensureQuickButton();await refreshPending();if(result&&$('absence-request-modal')?.classList.contains('active')){renderAbsenceImpact();window.OnAirAbsenceRequestCalendar?.render();}return result;};
  const legacySetName=setMyName;setMyName=function(...args){const result=legacySetName.apply(this,args);refreshPending();return result;};

  // ── 내 신청 내역 · 수정 · 철회 ──────────────────────────────────
  function recoverLinked(request){
    const linked=request?.linked_amendment;
    if(!linked||!Number.isSafeInteger(Number(linked.id))||Number(linked.id)<=0||typeof linked.receiptToken!=='string'||!linked.receiptToken||!['PENDING','APPROVED','REJECTED','CANCELLED'].includes(linked.status))return null;
    // Only the original receipt endpoint can disclose this capability. Name queries never enter here.
    if(!Object.hasOwn(myRequestStatuses,linked.receiptToken))trackMyRequest(linked.receiptToken);
    return linked;
  }
  let ownershipSnapshot={state:'ready',owned,unresolvedTokens:new Set(),errors:[]};
  async function refreshOwned(){
    mergeLatestMyRequestStatuses();owned.clear();const seen=new Set(),unresolvedTokens=new Set(),errors=[];
    async function visit(token){
      if(seen.has(token)||!Object.hasOwn(myRequestStatuses,token))return;seen.add(token);
      try{const res=await fetch('/api/duty/request-status/'+encodeURIComponent(token));if(!res.ok)throw Object.assign(Error('신청 권한을 확인하지 못했습니다'),{status:res.status});
        const body=await res.json(),request=body.request;if(!request?.id)throw Error('현재 자료에서 신청을 확인할 수 없습니다');
        owned.set(Number(request.id),{token,request});const linked=recoverLinked(request);if(linked)await visit(linked.receiptToken);
      }catch(error){unresolvedTokens.add(token);errors.push({status:error.status||0,message:error.message});}
    }
    await Promise.all(Object.keys(myRequestStatuses).map(visit));
    ownershipSnapshot={state:unresolvedTokens.size?(owned.size?'partial':'unavailable'):'ready',owned,unresolvedTokens,errors};
    window.OnAirRequestAccess?.renderBanner(ownershipSnapshot);return ownershipSnapshot;
  }
  // Cancellation uses the exact approved rows, never today's recalculated holiday plan.
  const isCancellation=r=>r?.request_type==='ABSENCE_CANCEL';
  const cancelNotice='관리자가 취소를 승인하기 전까지 기존 일정이 유지됩니다.';
  function cancellationType(r){const kind=String(r?.absence_type||r?.cancellable_absence?.type||r?.amendment_original?.type||'휴가·부재');return /연차|휴가/.test(kind)?'휴가':kind;}
  function cancellationInfo(r){
    const original=r?.cancellable_absence||r?.amendment_original;
    const ranges=original?(original.segments||[original]):(r?.absence_date_plan?.ranges||[]);
    const valid=ranges.filter(span=>validDate(span.start)&&validDate(span.end)&&span.start<=span.end);
    const days=valid.reduce((sum,span)=>sum+Math.round((Date.parse(span.end)-Date.parse(span.start))/86400000)+1,0);
    return {ranges:valid,days};
  }
  function cancellationText(r){const plan=cancellationInfo(r);return `${r.requester_name} · ${cancellationType(r)}${plan.days?` · 실제 ${plan.days}일`:''}\n`+plan.ranges.map(span=>span.start===span.end?span.start:span.start+' ~ '+span.end).join(', ');}
  function cancellationDetails(r,approval=false){
    const plan=cancellationInfo(r),showYear=plan.ranges.some(span=>span.start.slice(0,4)!==todayStr.slice(0,4)||span.end.slice(0,4)!==todayStr.slice(0,4));
    if(approval)return `<div class="approval-cancellation"><div class="ur-cancel-ranges">${plan.ranges.length?plan.ranges.map(span=>approvalPeriodHtml(span.start,span.end)).join(''):approvalPeriodHtml(r.target_date,r.absence_end_date||r.target_date)}</div>`+(plan.days?`<span class="ur-sub">실제 ${plan.days}일</span>`:'')+'</div>';
    const day=key=>`<time datetime="${escape(key)}">${escape(ui().dateLabel(key,showYear))}</time>`;
    return `<div class="ur-cancel-summary"><strong>${escape(r.requester_name)} · ${escape(cancellationType(r))}${plan.days?` · 실제 ${plan.days}일`:''}</strong><div class="ur-cancel-ranges">${plan.ranges.map(span=>`<span>${day(span.start)}${span.end!==span.start?' ~ '+day(span.end):''}</span>`).join('')}</div></div>`;
  }

  let cancellationContext=null,cancellationOpening=false,cancellationSaving=false;
  async function openCancellation(token,reason=''){
    if(!canRequest())return showToast(REQUEST_ONLY,true);
    if(!Object.hasOwn(myRequestStatuses,token))return showToast(OTHER_PC,true);
    if(cancellationOpening||cancellationSaving)return;
    cancellationOpening=true;
    try{
      const res=await fetch('/api/duty/request-status/'+encodeURIComponent(token)),body=await readBody(res);
      if(!res.ok)throw failure(res,body);const request=body.request;recoverLinked(request);
      if(request?.status!=='APPROVED'||request.request_type!=='ABSENCE'||!request.can_cancel||!request.cancellable_absence)
        return showToast(request?.cancellation_blocker||'현재 이 승인 일정은 취소를 요청할 수 없습니다. 내 신청 내역을 다시 확인하세요.',true);
      cancellationContext={token,request:clone(request)};
      const node=ui().open('ur-absence-cancel',cancellationType(request)+' 취소 요청',
        cancellationDetails(request)+`<p class="ur-amend-policy">${cancelNotice}</p><p class="ur-sub">선택한 신청의 모든 구간을 함께 취소합니다. ${noDutyRestore}</p><label class="ur-cancel-label" for="ur-cancel-reason">취소 사유 <span>(선택)</span></label><textarea id="ur-cancel-reason" maxlength="500" rows="3">${escape(reason)}</textarea><div class="ur-footer"><button type="button" data-ur-cancel-close>닫기</button><button type="button" id="ur-cancel-submit" class="btn-main">취소 요청하기</button></div>`,
        {width:560,onClose:()=>{cancellationContext=null;}});
      node?.querySelector?.('[data-ur-cancel-close]')?.addEventListener('click',()=>ui().close('ur-absence-cancel'));
      $('ur-cancel-submit')?.addEventListener('click',submitCancellation);
    }catch(error){showToast(friendly(error,null,'취소할 승인 일정 확인'),true);}finally{cancellationOpening=false;}
  }
  async function submitCancellation(){
    const context=cancellationContext;if(!context||cancellationSaving)return false;
    if(!canRequest())return showToast(REQUEST_ONLY,true);
    if(!Object.hasOwn(myRequestStatuses,context.token))return showToast(OTHER_PC,true);
    if(offlineText())return showToast(offlineText(),true);
    const reason=value('ur-cancel-reason').trim();if(!showErrors(['ur-cancel-reason'],reason.length>500?{'ur-cancel-reason':'취소 사유는 500자 이내로 입력하세요.'}:{}))return false;
    const button=$('ur-cancel-submit');cancellationSaving=true;if(button){button.disabled=true;button.textContent='전송 중…';}
    try{
      const legacy=()=>fetch('/api/duty/request/manage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({receipt_token:context.token,action:'CANCEL_ABSENCE',reason})});const res=window.OnAirRequestAccess?await window.OnAirRequestAccess.submitDurable({action:'CANCEL_ABSENCE',receiptToken:context.token,reason},legacy):await legacy(),body=await readBody(res);
      if(!res.ok){showToast(friendly(null,res,'취소 요청',body),true);return false;}
      if(body.receiptToken){trackMyRequest(body.receiptToken);rememberMyRequestCopy?.(body.receiptToken,{...context.request,request_type:'ABSENCE_CANCEL',amends_request_id:context.request.id,amendment_original:context.request.cancellable_absence,reason,status:'PENDING'});}
      addNotif(`${context.request.requester_name} 님의 ${cancellationType(context.request)} 취소 요청을 접수했습니다. ${cancelNotice}`,{kind:'request',requestId:body.requestId,receiptToken:body.receiptToken,requesterName:context.request.requester_name,targetDate:context.request.target_date,requestType:'ABSENCE_CANCEL',status:'PENDING'});
      if(cancellationContext===context)ui().close('ur-absence-cancel');
      showToast(body.message||'취소 요청을 접수했습니다.');checkRequestOutcomes();await refreshPending();if($('myreq-modal')?.classList.contains('active'))await loadMyRequests();return true;
    }catch(error){showToast(friendly(error,null,'취소 요청'),true);return false;}
    finally{cancellationSaving=false;if(button){button.disabled=false;button.textContent='취소 요청하기';}}
  }
  async function retryCancellation(r){await refreshOwned();const parent=owned.get(Number(r.amends_request_id));if(!parent)return showToast('원래 신청한 PC에서 승인 일정을 확인한 뒤 취소를 요청해 주세요.',true);return openCancellation(parent.token,r.reason||'');}
  function showCancellationState(token,r){
    const terminal=r.status!=='PENDING',retry=canRequest()&&['REJECTED','CANCELLED'].includes(r.status);
    const word={PENDING:'취소 승인 대기',APPROVED:'취소 완료',REJECTED:'취소 요청 반려',CANCELLED:'취소 요청 철회'}[r.status]||'취소 요청';
    const policy=r.status==='APPROVED'?`승인된 일정이 취소되었습니다. ${noDutyRestore}`:r.status==='PENDING'?cancelNotice:'취소 요청이 반영되지 않아 기존 승인 일정이 유지됩니다.';
    const node=ui().open('ur-cancellation-state',word,cancellationDetails(r)+
      (r.reason?`<div class="ur-cancel-reason"><b>취소 사유</b><p>${escape(r.reason)}</p></div>`:'')+
      (r.status==='REJECTED'?`<div class="ur-cancel-reason"><b>반려 사유</b><p>${escape(r.reject_reason||'관리자가 사유를 남기지 않았습니다.')}</p></div>`:'')+
      `<p class="ur-amend-policy">${policy}</p><div class="ur-footer"><button type="button" data-ur-cancel-state="close">닫기</button>${!terminal&&canRequest()?'<button type="button" data-ur-cancel-state="withdraw">취소 요청 철회</button>':''}${retry?'<button type="button" class="btn-main" data-ur-cancel-state="retry">다시 취소 요청</button>':''}</div>`,{width:560});
    node?.querySelector?.('[data-ur-cancel-state="close"]')?.addEventListener('click',()=>ui().close('ur-cancellation-state'));
    node?.querySelector?.('[data-ur-cancel-state="withdraw"]')?.addEventListener('click',()=>withdraw(token));
    node?.querySelector?.('[data-ur-cancel-state="retry"]')?.addEventListener('click',()=>{ui().close('ur-cancellation-state');retryCancellation(r);});
  }

  function approvedActions(own){
    if(!canRequest())return '';
    if(!own)return `<span class="ur-sub ur-other-pc">${ownershipSnapshot.state==='ready'?'이 신청의 관리 권한을 연결해야 합니다.':'신청 권한을 확인하지 못했습니다. 다시 확인해 주세요.'}</span>`;
    const r=own.request,id=Number(r.id),linked=r.linked_amendment;
    const actions=[r.can_amend?`<button type="button" onclick="OnAirRequests.amendById(${id})">변경 요청</button>`:'',r.can_cancel?`<button type="button" class="ur-action-danger" title="${escape(cancellationType(r))} 취소 요청" aria-label="${escape(cancellationType(r))} 취소 요청" onclick="OnAirRequests.cancelById(${id})">취소 요청</button>`:''].join('');
    if(actions)return actions+(r.can_amend&&!r.can_cancel&&r.cancellation_blocker?`<span class="ur-sub">${escape(r.cancellation_blocker)}</span>`:'');
    if(linked&&['PENDING','APPROVED'].includes(linked.status)&&owned.has(Number(linked.id))){
      const cancel=isCancellation(linked),message=cancel?(linked.status==='PENDING'?'취소 승인 대기':'취소 완료'):(linked.status==='PENDING'?'변경 승인 대기':'다른 승인 일정으로 변경됨');
      const label=cancel?'취소 요청 보기':linked.status==='PENDING'?'변경 요청 보기':'최신 승인 보기';
      return (cancel?'':`<span class="ur-sub">${message}</span>`)+`<button type="button" title="${label}" aria-label="${label}" onclick="OnAirRequests.editById(${Number(linked.id)})">${cancel?(linked.status==='PENDING'?'요청 보기':'취소 내역'):linked.status==='PENDING'?'요청 보기':'최신 승인 보기'}</button>`;
    }
    return `<span class="ur-sub">${escape(r.cancelled_by_id?'취소 완료':r.amendment_blocker||r.cancellation_blocker||'이 승인 일정은 현재 변경을 요청할 수 없습니다.')}</span>`;
  }

  function myRequestTime(value){
    const text=String(value||'—'),parts=text.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)$/);
    return `<span class="ur-myreq-time">${parts?`<span>${escape(parts[1])}</span><span class="ur-myreq-clock">${escape(parts[2])}</span>`:escape(text)}</span>`;
  }
  function myRequestContents(r,own){
    // Name lookup omits private reasons. Only an already-owned receipt may supply them.
    const privateRequest=own?.request?.requester_name===r.requester_name?own.request:null;
    const details=privateRequest?.absence_details??r.absence_details;
    const date=key=>`<span class="ur-myreq-date">${escape(label(key))}</span>`;
    let summary;
    if(isCancellation(r))summary=cancellationDetails({...r,amendment_original:privateRequest?.amendment_original||r.amendment_original});
    else if(isAmendment(r))summary=amendmentComparison(r);
    else if(r.request_type==='SWAP')summary=`<div class="ur-myreq-dates">${date(r.target_date)}<span>↔</span>${date(r.swap_date)}</div><div class="ur-myreq-kind">${escape(r.swap_worker||'')}</div>`;
    else if(r.request_type==='SUBSTITUTE')summary=`<div class="ur-myreq-dates">${date(r.target_date)}<span>→</span><span>${escape(r.substitute_name||'')}</span></div>`;
    else {const end=r.absence_end_date||r.swap_date||r.target_date;summary=`<div class="ur-myreq-dates">${date(r.target_date)}${end!==r.target_date?`<span>~</span>${date(end)}`:''}</div><div class="ur-myreq-kind">${escape(r.absence_type||r.substitute_name||'')}</div>`;}
    const reason=typeof privateRequest?.reason==='string'?privateRequest.reason.trim():'';
    const plan=r.absence_date_plan||privateRequest?.absence_date_plan;
    return `<div class="ur-myreq-summary">${summary}</div>`+
      (r.request_type==='ABSENCE'&&plan?.excluded?.length?`<p class="ur-sub">실제 휴가 ${Number(plan.days)}일 · 토·일·공휴일 ${plan.excluded.length}일 제외</p>`:'')+
      (reason?`<div class="ur-myreq-reason"><span class="ur-myreq-caption">신청 사유</span><div>${escape(reason)}</div></div>`:'')+
      (!isCancellation(r)&&isEducation(r.absence_type)&&details&&typeof details==='object'?educationDetailsHtml(details):'')+
      (isCancellation(r)&&r.status==='PENDING'?`<p class="ur-sub">${cancelNotice}</p>`:'')+
      (isAmendment(r)&&r.status==='PENDING'?`<p class="ur-sub">${amendmentNotice}</p>`:'');
  }

  let myRequestGroups=[],expandedRequestGroups=new Set(),myRequestGroupName='';
  function groupMyRequests(rows){
    const records=new Map(rows.map(r=>[Number(r.id),r])),groups=new Map();
    for(const r of records.values()){
      const parent=isCancellation(r)?records.get(Number(r.amends_request_id)):null;
      const root=parent?.request_type==='ABSENCE'&&parent.requester_name===r.requester_name?parent:r;
      const id=Number(root.id);
      if(!groups.has(id))groups.set(id,{root,cancellations:[],latestId:id});
      const group=groups.get(id);group.latestId=Math.max(group.latestId,Number(r.id));
      if(root!==r)group.cancellations.push(r);
    }
    // The API orders events newest first; the first event also orders its group.
    return [...groups.values()];
  }
  function myRequestActions(r){
    const own=owned.get(Number(r.id)),id=Number(r.id);
    if(r.status==='PENDING')return own?.request.status==='PENDING'
      ?`<button type="button" title="${isCancellation(r)?'취소 요청 보기':'수정'}" onclick="OnAirRequests.editById(${id})">${isCancellation(r)?'요청 보기':'수정'}</button><button type="button" class="ur-action-danger" onclick="OnAirRequests.withdrawById(${id})">철회</button>`
      :`<span class="ur-sub ur-other-pc">${ownershipSnapshot.state==='ready'?OTHER_PC:'신청 권한을 확인하지 못했습니다. 다시 확인해 주세요.'}</span>`;
    if(r.status==='APPROVED'&&r.request_type==='ABSENCE')return approvedActions(own);
    if(r.status==='APPROVED'&&isCancellation(r)&&own&&canRequest())return `<button type="button" onclick="OnAirRequests.editById(${id})">취소 내역</button>`;
    if(r.status==='REJECTED'||r.status==='CANCELLED')return (isAmendment(r)||isCancellation(r))&&!own
      ?`<span class="ur-sub ur-other-pc">${ownershipSnapshot.state==='ready'?OTHER_PC:'신청 권한을 확인하지 못했습니다. 다시 확인해 주세요.'}</span>`
      :`<button type="button" title="${isCancellation(r)?'다시 취소 요청':'다시 작성'}" onclick="OnAirRequests.resubmitById(${id})">다시 요청</button>`;
    return '';
  }
  function requestGroupState(group){
    const r=group.root,privateRequest=owned.get(Number(r.id))?.request;
    const status={PENDING:['pending','승인 대기'],APPROVED:['approved','승인'],REJECTED:['rejected','반려'],CANCELLED:['cancelled','철회']};
    let result=Object.hasOwn(status,r.status)?status[r.status]:['unknown',r.status||'—'];
    let hint='',tone='',actionRequest=r,processed=r.processed_at;
    const children=[...group.cancellations].sort((a,b)=>Number(b.id)-Number(a.id)),latest=children[0];
    if(isCancellation(r))result=[result[0],{PENDING:'취소 승인 대기',APPROVED:'취소 완료',REJECTED:'취소 반려',CANCELLED:'취소 요청 철회'}[r.status]||result[1]];
    else if(r.request_type==='ABSENCE'&&r.status==='APPROVED'){
      const completed=r.cancelled_by_id||privateRequest?.cancelled_by_id||children.find(c=>c.status==='APPROVED')?.id;
      const pending=children.find(c=>c.status==='PENDING');
      const amendment=myRequestsRows.find(c=>c.request_type==='ABSENCE'&&c.status==='APPROVED'&&Number(c.amends_request_id)===Number(r.id)&&c.requester_name===r.requester_name);
      const superseded=privateRequest?.superseded_by_id||amendment?.id;
      if(completed){result=['cancelled','취소 완료'];hint='승인된 일정 취소됨';actionRequest=null;processed=r.cancellation_processed_at||privateRequest?.cancellation_processed_at||children.find(c=>Number(c.id)===Number(completed))?.processed_at;}
      else if(superseded){hint='다른 승인 일정으로 변경됨';processed=amendment?.processed_at||processed;}
      else if(pending||r.pending_cancellation_id||privateRequest?.pending_cancellation_id){result=['pending','취소 승인 대기'];hint='승인 전까지 일정 유지';actionRequest=pending||r;processed=pending?.created_at||null;}
      else if(latest?.status==='REJECTED'){hint='취소 반려 · 기존 일정 유지';tone='rejected';processed=latest.processed_at;}
      else if(latest?.status==='CANCELLED'){hint='취소 요청 철회 · 기존 일정 유지';processed=latest.processed_at;}
    }
    return {result,hint,tone,processed,actions:actionRequest?myRequestActions(actionRequest):''};
  }
  function myRequestHistory(group){
    const events=[];
    for(const r of [group.root,...group.cancellations]){
      const own=owned.get(Number(r.id))?.request;
      const privateRequest=own?.requester_name===r.requester_name?own:null;
      const cancel=isCancellation(r),kind=cancel?'취소 요청':isAmendment(r)?'일정 변경 신청':r.request_type==='ABSENCE'?(isEducation(r.absence_type)?'교육 신청':'휴가·부재 신청'):requestLabel(r);
      const reason=privateRequest?.reason||'';
      const description=reason||(!cancel&&r.request_type==='ABSENCE'?label(r.target_date)+(r.absence_end_date&&r.absence_end_date!==r.target_date?' ~ '+label(r.absence_end_date):''):'');
      events.push({id:Number(r.id),time:r.created_at,title:kind,description,tone:''});
      if(r.status!=='PENDING'){
        const title=cancel?{APPROVED:'취소 승인',REJECTED:'취소 반려',CANCELLED:'취소 요청 철회'}[r.status]:{APPROVED:'신청 승인',REJECTED:'신청 반려',CANCELLED:'신청 철회'}[r.status];
        const rejection=cancel?privateRequest?.reject_reason:r.reject_reason;
        const description=r.status==='REJECTED'?(rejection?'반려 사유: '+rejection:'반려 사유는 신청한 PC에서 확인할 수 있습니다.'):cancel?(r.status==='APPROVED'?'기존 일정 취소 완료':'기존 일정 유지'):'';
        events.push({id:Number(r.id),time:r.processed_at,title:title||r.status,description,tone:r.status==='APPROVED'&&!cancel?'approved':r.status==='REJECTED'?'rejected':''});
      }
    }
    events.sort((a,b)=>String(a.time||'9999').localeCompare(String(b.time||'9999'))||a.id-b.id);
    return events.map(e=>`<li class="ur-history-event ${e.tone?'ur-history-'+e.tone:''}" data-ur-history-request-id="${e.id}"><time>${myRequestTime(e.time)}</time><span class="ur-history-event-title">${escape(e.title)}</span><span class="ur-history-description">${escape(e.description)}</span></li>`).join('');
  }
  function toggleRequestHistory(id,force){
    id=Number(id);const button=$('ur-history-toggle-'+id),panel=$('ur-history-'+id),row=$('ur-group-'+id);
    if(!button||!panel||!row)return;
    const open=typeof force==='boolean'?force:!expandedRequestGroups.has(id);
    if(open)expandedRequestGroups.add(id);else expandedRequestGroups.delete(id);
    button.setAttribute('aria-expanded',String(open));panel.hidden=!open;row.classList.toggle('ur-group-expanded',open);
  }
  function renderRequestGroup(group){
    const r=group.root,id=Number(r.id),own=owned.get(id),state=requestGroupState(group),open=expandedRequestGroups.has(id);
    const cell=(label,content,className='')=>`<td data-label="${label}"><div class="ur-myreq-value ${className}">${content}</div></td>`;
    const kind=r.request_type==='ABSENCE'&&!isAmendment(r)?r.absence_type||requestLabel(r):requestLabel(r);
    const kindClass=r.request_type==='ABSENCE'&&!isAmendment(r)?(isEducation(r.absence_type)?'ur-kind-education':isVacationType(r.absence_type)?'ur-kind-vacation':''):'';
    const count=[r,...group.cancellations].reduce((sum,item)=>sum+1+(item.status==='PENDING'?0:1),0);
    const disclosure=`<button type="button" class="ur-history-toggle" id="ur-history-toggle-${id}" aria-expanded="${open}" aria-controls="ur-history-${id}" aria-label="${escape(kind)} 처리 이력 ${count}건 보기" onclick="OnAirRequests.toggleHistory(${id})">이력 ${count}<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 7.5 5 5 5-5"/></svg></button>`;
    const result=`<span class="ur-result-badge ur-result-${state.result[0]}">${escape(state.result[1])}</span>`+(state.hint?`<span class="ur-group-hint ${state.tone?'ur-group-'+state.tone:''}">${escape(state.hint)}</span>`:'');
    const rejection=isCancellation(r)?own?.request?.reject_reason:r.reject_reason;
    const rootRejection=rejection?`<p class="ur-myreq-rejection">반려 사유: ${escape(rejection)}</p>`:'';
    return `<tr data-ur-request-id="${id}" id="ur-group-${id}" class="ur-group-primary${open?' ur-group-expanded':''}${r.request_type==='ABSENCE'&&!isAmendment(r)?' ur-plain-absence':''}">`+
      cell('신청 일시',myRequestTime(r.created_at))+cell('구분',escape(kind),'ur-myreq-type '+kindClass)+
      cell('내용',`<div class="ur-group-content">${myRequestContents(r,own)}${disclosure}</div>${rootRejection}`,'ur-myreq-content')+
      cell('결과',result)+cell('처리 일시',myRequestTime(state.processed))+
      cell('작업',state.actions?`<div class="ur-row-actions">${state.actions}</div>`:'<span class="ur-sub">—</span>','ur-myreq-actions')+'</tr>'+
      `<tr class="ur-history-row" id="ur-history-${id}"${open?'':' hidden'}><td colspan="6"><section class="ur-history-panel" aria-label="${escape(kind)} 처리 이력"><h3>처리 이력</h3><ol>${myRequestHistory(group)}</ol></section></td></tr>`;
  }
  loadMyRequests=async()=>{
    const name=value('myreq-name'),tb=$('myreq-tbody'),generation=++myRequestsGeneration;
    if(name!==myRequestGroupName){expandedRequestGroups.clear();owned.clear();myRequestsRows=[];myRequestGroups=[];myRequestGroupName=name;}
    window.OnAirRequestAccess?.renderBanner(ownershipSnapshot);
    if($('myreq-count'))$('myreq-count').textContent='';
    if(!name){tb.innerHTML='<tr><td colspan="6">이름을 선택하세요.</td></tr>';return;}
    tb.innerHTML='<tr><td colspan="6">불러오는 중…</td></tr>';
    try{
      const [res]=await Promise.all([fetch('/api/duty/my-requests?name='+encodeURIComponent(name)+'&grouped=1'),refreshOwned()]);
      const body=await res.json();if(!res.ok)throw Error(serverMessage(res,body));if(generation!==myRequestsGeneration)return;
      myRequestsRows=body.requests||[];myRequestGroups=groupMyRequests(myRequestsRows);
      tb.innerHTML=myRequestGroups.length?myRequestGroups.map(renderRequestGroup).join(''):'<tr><td colspan="6">신청 내역이 없습니다.</td></tr>';
      if($('myreq-count'))$('myreq-count').textContent='신청 일정 '+myRequestGroups.length+'건';
    }catch(error){if(generation===myRequestsGeneration)tb.innerHTML=`<tr><td colspan="6">${escape(friendly(error,null,'신청 내역 불러오기'))}</td></tr>`;}
  };
  openMyRequestsModal=(name,requestId)=>{
    const selected=typeof name==='string'&&member(name)?name:personalName();
    $('myreq-name').innerHTML='<option value="">이름 선택</option>'+memberList.map(m=>`<option value="${escape(m.name)}">${escape(m.name)}</option>`).join('');
    $('myreq-name').value=selected;$('myreq-modal').classList.add('active');ui().raise?.('myreq-modal');
    const task=loadMyRequests();task.then(()=>{
      if(!requestId)return;
      const group=myRequestGroups.find(g=>Number(g.root.id)===Number(requestId)||g.cancellations.some(r=>Number(r.id)===Number(requestId)));
      if(!group)return;
      if(Number(group.root.id)!==Number(requestId))toggleRequestHistory(group.root.id,true);
      const row=$('myreq-tbody').querySelector(`[data-ur-request-id="${Number(group.root.id)}"]`);
      row?.scrollIntoView({block:'center'});row?.classList.add('ur-highlight');
    });return task;
  };
  async function openAmendment(token,proposed){
    if(!canRequest())return showToast(REQUEST_ONLY,true);
    if(!Object.hasOwn(myRequestStatuses,token))return showToast(OTHER_PC,true);
    try{const res=await fetch('/api/duty/request-status/'+encodeURIComponent(token)),body=await readBody(res);if(!res.ok)throw failure(res,body);const request=body.request;recoverLinked(request);
      if(request?.status!=='APPROVED'||request.request_type!=='ABSENCE'||!request.can_amend||!request.amendable_absence)return showToast(request?.amendment_blocker||'현재 이 승인 일정은 변경을 요청할 수 없습니다. 내 신청 내역을 새로 확인하세요.',true);
      const original=request.amendable_absence,seed=proposed?{...proposed}:{...request,target_date:original.start,absence_end_date:original.end,absence_type:original.type,absence_details:original.education};
      seed.requester_name=request.requester_name;seed.request_type='ABSENCE';openAbsence(seed,{amendToken:token,amendsRequestId:Number(request.id),original:clone(original)});
    }catch(error){showToast(friendly(error,null,'승인 일정 확인'),true);}
  }
  function resubmit(r){
    if(isCancellation(r))return retryCancellation(r);
    if(isAmendment(r))return retryAmendment(r);
    const seed={...r};for(const key of ['id','status','created_at','processed_at','reject_reason','revision','role','requestLabel'])delete seed[key];if(seed.request_type==='ABSENCE')openAbsence(seed);else openChange(seed);
  }
  async function retryAmendment(r){
    await refreshOwned();const parent=owned.get(Number(r.amends_request_id));
    if(!parent)return showToast('기존 승인 신청을 확인할 수 없습니다. 원래 신청한 PC에서 변경을 요청하거나 관리자에게 문의하세요.',true);
    return openAmendment(parent.token,r);
  }
  // A request decided in the meantime keeps its result and its linked amendment path.
  async function showProcessed(token,draft){let request=null;try{const res=await fetch('/api/duty/request-status/'+encodeURIComponent(token));if(res.ok)request=(await res.json()).request;}catch{}
    if(!request||!request.status||request.status==='PENDING')return false;
    if(isCancellation(request)){showCancellationState(token,request);return true;}
    const linked=recoverLinked(request),amendable=canRequest()&&request.status==='APPROVED'&&request.request_type==='ABSENCE'&&request.can_amend;
    const word=request.cancelled_by_id?'취소된 승인 일정입니다.':{APPROVED:'이미 승인된 신청입니다.',REJECTED:'이미 반려된 신청입니다.',CANCELLED:'이미 철회한 신청입니다.'}[request.status]||'대기 중인 신청이 아닙니다.';
    const detail=request.cancelled_by_id?`<p class="ur-state-line">관리자가 취소를 승인해 이 일정이 취소되었습니다. 최초 승인 기록은 유지됩니다.</p>`:request.status==='REJECTED'?`<p class="ur-state-line"><b>반려 사유</b> ${escape(request.reject_reason||'관리자가 사유를 남기지 않았습니다.')}</p>`:request.status==='APPROVED'?`<p class="ur-state-line">승인된 내용은 일정에 반영되었습니다.${amendable?' 변경 요청을 보내면 관리자가 다시 승인한 뒤 반영합니다.':''}</p>`:'';
    const blocked=request.status==='APPROVED'&&request.request_type==='ABSENCE'&&!amendable;
    const action=request.request_type==='ABSENCE'&&!canRequest()?'':amendable?'변경 요청':linked?'연결된 신청 보기':blocked?'':'다시 작성';
    const hint=isAmendment(request)&&request.status!=='APPROVED'?'기존 승인 일정은 유지됩니다.':blocked?(request.amendment_blocker||'현재 이 승인 일정은 변경을 요청할 수 없습니다.'):amendable?amendmentNotice:'처리된 신청의 결과는 유지됩니다. 다시 작성하면 새 신청서를 엽니다.';
    ui().close('ur-change-modal');if($('absence-request-modal')?.classList.contains('active'))legacyCloseAbsenceRequest();
    const node=ui().open('ur-request-state','신청 상태 안내',`<p class="ur-state-title">${escape(word)}</p><p class="ur-state-line">${escape(requestLabel(request))} · ${escape(describeRequest(request))}</p>${isAmendment(request)?amendmentComparison(request):''}${detail}<p class="ur-sub">${escape(hint)}</p><div class="ur-footer"><button type="button" data-ur-state="close">닫기</button>${action?`<button type="button" class="btn-main" data-ur-state="retry">${action}</button>`:''}</div>`,{width:560});
    const base={...request,...(draft||{})};node?.querySelector?.('[data-ur-state="close"]')?.addEventListener('click',()=>ui().close('ur-request-state'));node?.querySelector?.('[data-ur-state="retry"]')?.addEventListener('click',()=>{ui().close('ur-request-state');if(amendable)return openAmendment(token,draft?base:undefined);if(linked)return openOwnedRequest(linked.receiptToken);return resubmit(base);});
    checkRequestOutcomes();refreshPending();return true;
  }
  async function openOwnedRequest(token){if(!Object.hasOwn(myRequestStatuses,token))return showToast(OTHER_PC,true);try{const res=await fetch('/api/duty/request-status/'+encodeURIComponent(token));const body=await readBody(res);if(!res.ok)throw failure(res,body);const request=body.request;recoverLinked(request);if(isCancellation(request)){showCancellationState(token,request);return;}if(request.status==='PENDING'){if(request.request_type==='ABSENCE')openAbsence(request,{editToken:token,...(isAmendment(request)?{amendsRequestId:Number(request.amends_request_id),original:request.amendment_original}:{})});else openChange(request,{editToken:token});}else await showProcessed(token);}catch(error){showToast(friendly(error,null,'신청 확인'),true);}}
  async function withdraw(token){if(!Object.hasOwn(myRequestStatuses,token))return showToast(OTHER_PC,true);if(!await confirmBox('신청 철회','승인 대기 중인 신청을 철회할까요?\n\n철회하면 관리자 결재함에서 빠집니다.','철회하기'))return;try{const res=await fetch('/api/duty/request/manage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({receipt_token:token,action:'WITHDRAW'})});const body=await readBody(res);if(!res.ok){if(res.status===409&&await showProcessed(token))return;throw failure(res,body);}myRequestStatuses[token]='CANCELLED';saveMyRequestStatuses();showToast(body.message||'신청을 철회했습니다.');ui().close('ur-cancellation-state');refreshPending();await loadMyRequests();}catch(error){showToast(friendly(error,null,'철회'),true);}}

  // ── 결재함 (관리자) ─────────────────────────────────────────────
  // Preserve the existing row content and decisions; filtering only moves/hides rows.
  const approvalState={checks:new Map(),rows:new Map(),query:'',type:'ALL',sort:'newest'};
  function requestSignature(r){return JSON.stringify([r.request_type,r.requester_name,r.target_date,r.swap_date,r.swap_worker,r.substitute_name,r.is_zero_score,r.absence_type,r.absence_end_date,r.reason,r.absence_details,r.amends_request_id,r.amendment_original]);}
  function approvalSearchText(r){
    // Use the row's period renderers so original/changed/cancelled ranges keep the same date and weekday format.
    const periods=(isCancellation(r)?cancellationDetails(r,true):isAmendment(r)?amendmentComparison(r,false,true):'')||approvalPeriodHtml(r.target_date,r.absence_end_date||r.swap_date||r.target_date);
    return [r.requester_name,r.reason,r.swap_worker,r.substitute_name,r.absence_type,periods.replace(/<[^>]*>/g,' '),...Object.values(education(r.absence_details)),...(isAmendment(r)?Object.values(education(r.amendment_original?.education)):[])].join(' ').toLocaleLowerCase();
  }
  function filterApproval(rows,options={}){const query=String(options.query||'').trim().toLocaleLowerCase();const type=options.type||'ALL';const sort=options.sort||'oldest';return rows.filter(r=>r.status==='PENDING'&&(type==='ALL'||r.request_type===type||(type==='BLOCKED'&&r.blocker))&&(!query||approvalSearchText(r).includes(query))).slice().sort((a,b)=>{if(sort==='name')return String(a.requester_name).localeCompare(String(b.requester_name),'ko')||Number(a.id)-Number(b.id);if(sort==='date')return String(a.target_date).localeCompare(String(b.target_date))||Number(a.id)-Number(b.id);const result=String(a.created_at||'').localeCompare(String(b.created_at||''))||Number(a.id)-Number(b.id);return sort==='newest'?-result:result;});}
  function captureApproval(){for(const r of approvalCache||[]){if(r.status==='PENDING'&&r.request_type==='SUBSTITUTE'){const box=$('req-zero-'+Number(r.id));if(box)approvalState.checks.set(Number(r.id),{signature:requestSignature(r),checked:!!box.checked});}}const scroll=$('approval-modal')?.querySelector('.approval-scroll');const active=document.activeElement;return {top:scroll?.scrollTop||0,left:scroll?.scrollLeft||0,focusId:active?.id,requestId:active?.closest?.('[data-ur-request-id]')?.dataset.urRequestId,action:active?.getAttribute?.('onclick')};}
  function applyApprovalFilter(){const tb=$('approval-tbody');if(!tb)return;const matching=filterApproval(approvalCache,approvalState),ids=new Set(matching.map(r=>Number(r.id)));for(const[id,row]of approvalState.rows)row.hidden=!ids.has(id);for(const r of matching){const row=approvalState.rows.get(Number(r.id));if(row)tb.appendChild(row);}let message=tb.querySelector('.ur-no-approval');if(!message&&approvalState.rows.size){message=document.createElement('tr');message.className='ur-no-approval';message.innerHTML='<td colspan="3" class="approval-message">조건에 맞는 대기 신청이 없습니다.</td>';tb.appendChild(message);}if(message)message.hidden=matching.length>0;$('approval-count').textContent=`대기 ${approvalCache.filter(r=>r.status==='PENDING').length}건${approvalState.query||approvalState.type!=='ALL'?` · 표시 ${matching.length}건`:''}`;}
  function approvalToolbar(){const modal=$('approval-modal');if(!modal||modal.querySelector('#ur-approval-toolbar'))return;modal.querySelector('.approval-scroll')?.insertAdjacentHTML('beforebegin','<div class="ur-approval-toolbar" id="ur-approval-toolbar"><input id="ur-approval-search" type="search" aria-label="결재 신청 검색" placeholder="이름·날짜·사유 검색"><select id="ur-approval-filter" aria-label="신청 종류 필터"><option value="ALL">전체 종류</option><option value="SWAP">맞교환</option><option value="SUBSTITUTE">대체 근무</option><option value="ABSENCE">휴가·부재</option><option value="ABSENCE_CANCEL">휴가·부재 취소</option><option value="BLOCKED">승인 전 확인 필요</option></select><select id="ur-approval-sort" aria-label="결재 정렬"><option value="oldest">오래된 신청부터</option><option value="newest">최근 신청부터</option><option value="date">당직 날짜순</option><option value="name">이름순</option></select></div>');for(const[id,key]of [['ur-approval-search','query'],['ur-approval-filter','type'],['ur-approval-sort','sort']]){$(id).value=approvalState[key];$(id).addEventListener(id==='ur-approval-search'?'input':'change',()=>{captureApproval();approvalState[key]=$(id).value;applyApprovalFilter();});}}
  const legacyApproval=openApprovalModal;
  // The 0점 box starts from the requester's own choice; an approver's unsaved change survives refreshes of the same request revision.
  openApprovalModal=async()=>{const snapshot=approvalRestore||captureApproval();approvalToolbar();await legacyApproval();const pending=new Set();approvalState.rows.clear();for(const r of approvalCache||[]){if(r.status!=='PENDING')continue;const id=Number(r.id);pending.add(id);const prior=approvalState.checks.get(id),box=$('req-zero-'+id);if(box&&r.request_type==='SUBSTITUTE'){box.checked=prior?.signature===requestSignature(r)?prior.checked:requestZeroScore(r);approvalState.checks.set(id,{signature:requestSignature(r),checked:box.checked});}const button=$('approval-tbody')?.querySelector(`button[onclick="processApproval(${id}, 'APPROVE')"]`);const row=button?.closest('tr');if(row){row.dataset.urRequestId=String(id);approvalState.rows.set(id,row);if(r.request_type==='ABSENCE'&&!isAmendment(r)&&isEducation(r.absence_type)&&r.absence_details)row.children[1]?.insertAdjacentHTML('beforeend',educationDetailsHtml(r.absence_details));}}
    for(const id of approvalState.checks.keys())if(!pending.has(id))approvalState.checks.delete(id);if(approvalState.rows.size)applyApprovalFilter();const scroll=$('approval-modal')?.querySelector('.approval-scroll');if(scroll){scroll.scrollTop=snapshot.top;scroll.scrollLeft=snapshot.left;}let focus=snapshot.focusId&&$(snapshot.focusId);if(!focus&&snapshot.requestId){const row=approvalState.rows.get(Number(snapshot.requestId));focus=snapshot.action?Array.from(row?.querySelectorAll('button')||[]).find(b=>b.getAttribute('onclick')===snapshot.action):row?.querySelector('button');if(!focus)focus=Array.from(approvalState.rows.values()).find(r=>!r.hidden)?.querySelector('button');}if(focus?.isConnected&&!focus.closest?.('[hidden]'))focus.focus({preventScroll:true});};

  const legacyProcessApproval=processApproval;
  const legacyAdminFetch=fetchWithAdminToken;
  fetchWithAdminToken=async(url,options={},...args)=>{
    const snapshot=approvalActionSnapshot;let approving=false;
    if(url==='/api/duty/request/action'&&options.body&&snapshot){
      try{
        const body=JSON.parse(options.body);
        if(Number(body.request_id)===snapshot.id){
          if(Number.isInteger(snapshot.revision))body.request_revision=snapshot.revision;
          approving=body.action==='APPROVE'&&snapshot.action==='APPROVE';
          if(approving&&typeof snapshot.impactDigest==='string')body.impact_digest=snapshot.impactDigest;
          options={...options,body:JSON.stringify(body)};
        }
      }catch{}
    }
    const response=await legacyAdminFetch(url,options,...args);
    // The legacy failure path reloads the queue and shows the server's Korean explanation.
    // Refresh calendar conditions as well; the next approval still needs an explicit click.
    if(approving&&response.status===409)await fetchAllData();
    return response;
  };
  processApproval=async(id,action)=>{if(approvalProcessing||approvalActionSnapshot)return;approvalRestore=captureApproval();const displayed=approvalCache.find(r=>Number(r.id)===Number(id));approvalActionSnapshot={id:Number(id),action,revision:displayed?.revision,impactDigest:displayed?.impact_digest};try{return await legacyProcessApproval(id,action);}finally{approvalRestore=null;approvalActionSnapshot=null;}};

  // Shared editor fields remain single-person by default; bulk saves one payload.
  const isVacationType=type=>/연차|휴가/.test(String(type||''));
  function adminDatePlan(type,start,end){
    const ranges=[],excluded=[];
    if(!validDate(start)||!validDate(end)||start>end)return {ranges,excluded,days:0};
    if(!isVacationType(type))return {ranges:[{start,end}],excluded,days:Math.round((Date.parse(end)-Date.parse(start))/86400000)+1};
    const cursor=new Date(start+'T00:00:00Z'),last=new Date(end+'T00:00:00Z');let days=0,range=null;
    for(;cursor<=last;cursor.setUTCDate(cursor.getUTCDate()+1)){
      const key=cursor.toISOString().slice(0,10);
      if(isOffDayKey(key)){excluded.push(key);range=null;continue;}
      days++;
      if(range)range.end=key;else{range={start:key,end:key};ranges.push(range);}
    }
    return {ranges,excluded,days};
  }
  function bulkPlan(names,type,start,end,ignoreId=null){
    const datePlan=adminDatePlan(type,start,end),unique=[...new Set(names)].filter(member),duplicates=[],addNames=[];
    const overlaps=a=>datePlan.ranges.some(r=>normalizeDate(a.start)<=r.end&&normalizeDate(a.end)>=r.start);
    for(const name of unique){if(absences.some(a=>(ignoreId===null||Number(a.id)!==Number(ignoreId))&&a.name===name&&overlaps(a)))duplicates.push(name);else addNames.push(name);}
    const freedDates=Object.keys(scheduleMap).filter(key=>datePlan.ranges.some(r=>key>=r.start&&key<=r.end)&&(scheduleMap[key]||[]).some(w=>addNames.includes(w.name))).sort();
    const months=closedMonths.filter(month=>datePlan.ranges.some(r=>month>=r.start.slice(0,7)&&month<=r.end.slice(0,7)));
    return {...datePlan,addNames,duplicates,freedDates,closedMonths:months,freedAssignments:freedDates.reduce((sum,key)=>sum+(scheduleMap[key]||[]).filter(w=>addNames.includes(w.name)).length,0)};
  }
  function adminNames(){return $('ur-bulk-toggle')?.checked&&editingAbsenceId===null?Array.from(document.querySelectorAll('[data-ur-bulk-name]:checked')).map(el=>el.value):[value('abs-name')];}
  function updateBulkMode(){const bulk=!!$('ur-bulk-toggle')?.checked&&editingAbsenceId===null;if($('ur-bulk-names'))$('ur-bulk-names').hidden=!bulk;if($('abs-name'))$('abs-name').disabled=bulk||editingAbsenceId!==null;updateAbsenceEditPreview();}
  function adminEducation(){return isEducation(value('abs-type'))?readEducation('ur-admin-edu'):education(editingAbsenceOriginal?.education);}
  function connectAdminForm(){const editor=$('abs-editor');if(!editor)return;if(!$('ur-bulk-control')){($('abs-bulk-host')||editor).insertAdjacentHTML('afterbegin','<div id="ur-bulk-control" class="ur-bulk-control"><label class="ur-check"><input id="ur-bulk-toggle" type="checkbox">여러 명 함께 등록</label><div id="ur-bulk-names" class="ur-bulk-names" hidden></div></div>');$('ur-bulk-toggle').addEventListener('change',updateBulkMode);}if(!$('ur-admin-edu-group')){if($('abs-education-storage'))$('abs-education-storage').insertAdjacentHTML('beforeend',educationInputs('ur-admin-edu'));else $('abs-edit-preview')?.insertAdjacentHTML('beforebegin',educationInputs('ur-admin-edu'));}$('ur-bulk-names').innerHTML=memberList.map(m=>`<label><input type="checkbox" data-ur-bulk-name value="${escape(m.name)}">${escape(m.name)}</label>`).join('');if(!editor.dataset.urFields){editor.dataset.urFields='1';editor.addEventListener('change',()=>{showEducation('ur-admin-edu',value('abs-type'));updateAbsenceEditPreview();});editor.addEventListener('input',()=>updateAbsenceEditPreview());}showEducation('ur-admin-edu',value('abs-type'));window.OnAirAbsenceManagement?.connectForm();}
  const legacyAdminOpen=openAbsenceModal,legacyBeginEdit=beginAbsenceEdit,legacyCancelEdit=cancelAbsenceEdit,legacyDirty=absenceEditDirty,legacyPreview=updateAbsenceEditPreview;
  let adminDraftBaseline=null;
  const adminDraftSnapshot=()=>JSON.stringify({name:value('abs-name'),type:value('abs-type'),start:value('abs-start'),end:value('abs-end'),bulk:!!$('ur-bulk-toggle')?.checked,names:adminNames(),education:readEducation('ur-admin-edu')});
  function clearAdminEducation(){fillEducation('ur-admin-edu',{});if($('ur-bulk-toggle'))$('ur-bulk-toggle').checked=false;document.querySelectorAll('[data-ur-bulk-name]').forEach(el=>el.checked=false);if($('ur-bulk-control'))$('ur-bulk-control').hidden=false;updateBulkMode();showEducation('ur-admin-edu',value('abs-type'));}
  openAbsenceModal=()=>{legacyAdminOpen();if(SYSTEM_MODE!=='ADMIN')return;connectAdminForm();clearAdminEducation();adminDraftBaseline=adminDraftSnapshot();};
  beginAbsenceEdit=id=>{const prior=editingAbsenceOriginal;legacyBeginEdit(id);if(editingAbsenceOriginal===prior||Number(editingAbsenceId)!==Number(id))return;editingAbsenceOriginal=clone(absences.find(a=>Number(a.id)===Number(id)));connectAdminForm();fillEducation('ur-admin-edu',editingAbsenceOriginal.education);$('ur-bulk-toggle').checked=false;$('ur-bulk-control').hidden=true;showEducation('ur-admin-edu',value('abs-type'));};
  absenceEditDirty=()=>legacyDirty()||!!editingAbsenceOriginal&&JSON.stringify(education(editingAbsenceOriginal.education))!==JSON.stringify(adminEducation())||!editingAbsenceOriginal&&adminDraftBaseline!==null&&adminDraftSnapshot()!==adminDraftBaseline;
  cancelAbsenceEdit=(force=false)=>{const prior=editingAbsenceId;if(!force&&(absenceFormSaving||!confirmDiscardAbsenceEdit()))return prior;legacyCancelEdit(true);if(editingAbsenceId===null){clearAdminEducation();showErrors(['abs-name','abs-type','abs-start','abs-end','ur-bulk-toggle'],{});adminDraftBaseline=adminDraftSnapshot();}return prior;};
  startNewAbsence=()=>{
    if(SYSTEM_MODE!=='ADMIN'||absenceFormSaving||isSaving||!confirmDiscardAbsenceEdit())return;
    const query=value('abs-search').trim().toLocaleLowerCase();
    const matches=query?memberList.filter(m=>m.name.toLocaleLowerCase().includes(query)):[];
    const match=matches.find(m=>m.name.toLocaleLowerCase()===query)||(matches.length===1?matches[0]:null);
    cancelAbsenceEdit(true);
    const select=$('abs-name');
    if(!Array.from(select.options).some(o=>o.value==='')){
      const option=document.createElement('option');option.value='';option.textContent='사원을 선택하세요';select.insertBefore(option,select.firstChild);
    }
    select.value=match?.name||'';$('abs-type').value=absenceTypes[0]||'';
    clearAdminEducation();updateAbsenceDateLabels();window.OnAirAbsenceManagement?.beginDraft();
    adminDraftBaseline=adminDraftSnapshot();select.focus({preventScroll:true});
    if(!match&&matches.length>1)showToast(`검색된 사원이 ${matches.length}명입니다. 대상 사원을 선택하세요.`);
  };
  updateAbsenceEditPreview=()=>{
    legacyPreview();showEducation('ur-admin-edu',value('abs-type'));
    const start=value('abs-start'),end=value('abs-end');if(!validDate(start)||!validDate(end)||start>end)return;
    const plan=bulkPlan(adminNames(),value('abs-type'),start,end,editingAbsenceId);
    const excluded=plan.excluded.length?` · 실제 휴가 ${plan.days}일 / 토·일·공휴일 ${plan.excluded.length}일 제외`:'';
    if(editingAbsenceId!==null){
      const freed=plan.freedDates.filter(date=>date<editingAbsenceOriginal.start||date>editingAbsenceOriginal.end);
      $('abs-edit-preview').textContent=`기존: ${editingAbsenceOriginal.type} · ${editingAbsenceOriginal.start} ~ ${editingAbsenceOriginal.end}\n변경: ${value('abs-type')} · ${start} ~ ${end}${excluded} · 당직 해제 ${freed.length}일`;
    }else $('abs-edit-preview').textContent=`등록 대상 ${plan.addNames.length}명${plan.duplicates.length?' · 중복 기간 제외: '+plan.duplicates.join(', '):''}${excluded} · 당직 해제 ${plan.freedAssignments}건${plan.freedDates.length?' ('+plan.freedDates.map(date=>ui().dateLabel(date)).join(', ')+')':''}${plan.closedMonths.length?' · 마감된 달: '+plan.closedMonths.join(', '):''}`;
  };
  saveAbsenceForm=async()=>{
    if(SYSTEM_MODE!=='ADMIN'||absenceFormSaving||isSaving)return;
    if(!SERVER_CONNECTED||!dataLoaded)return showToast('당직표를 아직 불러오지 못했습니다. 연결 상태를 확인한 뒤 새로고침(F5)하세요.',true);
    const start=value('abs-start'),end=value('abs-end'),type=value('abs-type'),names=adminNames(),errors={};
    if(!names.length||names.some(n=>!member(n)))errors[$('ur-bulk-toggle')?.checked?'ur-bulk-toggle':'abs-name']='등록할 사원을 선택하세요.';
    if(!type)errors['abs-type']='부재 구분을 선택하세요.';
    if(!validDate(start))errors['abs-start']='올바른 시작일을 입력하세요.';
    if(!validDate(end)||validDate(start)&&start>end)errors['abs-end']='시작일 이후의 올바른 종료일을 입력하세요.';
    const plan=bulkPlan(names,type,start,end,editingAbsenceId);
    if(validDate(start)&&validDate(end)&&start<=end&&!plan.ranges.length)errors['abs-start']='선택한 기간이 모두 토·일요일 또는 공휴일입니다. 휴가 날짜를 다시 선택하세요.';
    if(plan.closedMonths.length)errors['abs-start']='마감된 달을 포함할 수 없습니다: '+plan.closedMonths.join(', ');
    if(!showErrors(['abs-name','ur-bulk-toggle','abs-type','abs-start','abs-end'],errors))return;
    const old=editingAbsenceOriginal,editingId=editingAbsenceId;
    let target=editingId===null?null:absences.find(a=>Number(a.id)===Number(editingId));
    if(old&&(!target||JSON.stringify(target)!==JSON.stringify(old)))return showToast('이 기록이 다른 곳에서 변경되었습니다. 다시 선택하세요.',true);
    if(old&&!absenceEditDirty())return showToast('변경된 내용이 없습니다.');
    if(!old&&!plan.addNames.length)return showToast('모든 대상에게 겹치는 부재 기록이 있습니다. 기존 기록을 수정하세요.',true);
    if(old&&plan.duplicates.length)return showErrors(['abs-end'],{'abs-end':'같은 사원의 다른 부재 기록과 기간이 겹칩니다.'});
    const freed=old?plan.freedDates.filter(date=>date<old.start||date>old.end):plan.freedDates,details=adminEducation();
    const label=old?`${old.name} 부재 변경`:`${plan.addNames.join(', ')} 부재 등록`;
    const stateSnapshot=()=>JSON.stringify([dataVersion,absences,scheduleMap,closedMonths,memberList,finalHolidays]);
    const confirmedState=stateSnapshot(),confirmedDraft=adminDraftSnapshot();
    let captured=false;
    // The confirmation is part of this operation: no second request or editor close may pass it.
    absenceFormSaving=true;const unfreeze=freezeAbsenceEditor();$('abs-submit-btn').disabled=true;
    try{
      if(!await confirmBox(old?'부재 변경 저장':'부재 등록',`${label}을 저장할까요?\n\n${type} · ${ui().dateLabel(start)} ~ ${ui().dateLabel(end)}${plan.excluded.length?'\n실제 휴가 '+plan.days+'일 · 토·일·공휴일 '+plan.excluded.length+'일 제외\n저장 구간: '+plan.ranges.map(r=>r.start===r.end?r.start:r.start+' ~ '+r.end).join(', '):''}\n${freed.length?'당직 해제 날짜: '+freed.map(date=>ui().dateLabel(date)).join(', '):'해제할 당직 없음'}${plan.duplicates.length?'\n기간이 겹쳐 뺀 사람: '+plan.duplicates.join(', '):''}`,'저장'))return;
      if(SYSTEM_MODE!=='ADMIN'||!SERVER_CONNECTED||!dataLoaded)return showToast('연결 상태가 바뀌었습니다. 입력 내용은 유지됩니다. 연결을 확인하고 다시 저장하세요.',true);
      if(confirmedState!==stateSnapshot()||editingId!==editingAbsenceId||confirmedDraft!==adminDraftSnapshot())return showToast('확인 중 일정이나 입력 내용이 바뀌었습니다. 입력 내용은 유지됩니다. 변경 내용을 확인하고 다시 저장하세요.',true);
      // A refresh can replace array objects with equal values while the dialog is open.
      target=editingId===null?null:absences.find(a=>Number(a.id)===Number(editingId));
      captureUndo(old?'부재 수정':'여러 명 부재 등록');captured=true;
      let nextId=Math.max(Date.now(),...absences.map(a=>Number(a.id)||0));
      if(old){
        target.type=type;target.start=plan.ranges[0].start;target.end=plan.ranges[0].end;target.education=details;
        for(const range of plan.ranges.slice(1))absences.push({id:++nextId,name:old.name,type,...range,education:clone(details)});
        for(const date of freed)scheduleMap[date]=(scheduleMap[date]||[]).filter(w=>w.name!==old.name);
      }else{
        for(const name of plan.addNames)for(const range of plan.ranges)absences.push({id:++nextId,name,type,...range,education:clone(details)});
        for(const date of freed)scheduleMap[date]=(scheduleMap[date]||[]).filter(w=>!plan.addNames.includes(w.name));
      }
      const history=makeHistory(old?'부재 기간·구분·교육 수정':'부재 등록',`${start}~${end}`,label+`; ${type} ${start}~${end}; 당직 해제 ${freed.length}일`+(plan.excluded.length?`; 휴가 ${plan.days}일, 토·일·공휴일 ${plan.excluded.length}일 제외; 저장 구간 ${plan.ranges.map(r=>r.start+'~'+r.end).join(', ')}`:'')),payload={absences,historyLog:history};
      if(freed.length)payload.schedules=scheduleMap;
      const ok=await syncFullDataToBackend(payload);
      if(ok){cancelAbsenceEdit(true);renderAbsenceList();renderCalendar();offerUndo();showToast('부재 일정을 저장했습니다.');if(freed.length)openVacancyModal(freed,'부재 등록·수정으로 비워진 당직을 확인하세요.');}
      else{restoreUndoAfterFailedSave();updateAbsenceEditPreview();}
    }catch(error){if(captured)restoreUndoAfterFailedSave();showToast(friendly(error,null,'부재 일정 저장'),true);}
    finally{absenceFormSaving=false;unfreeze();$('abs-submit-btn').disabled=false;}
  };
  const style=document.createElement('style');style.textContent=`#ur-amendment-context{padding:12px 14px;border:1px solid var(--border-strong);border-radius:10px;background:var(--surface);font-size:12px;line-height:1.6;overflow-wrap:anywhere}#ur-amendment-context[hidden]{display:none}#ur-amendment-context p{margin:4px 0}.ur-amend-comparison{font-size:12px;font-weight:400;line-height:1.6;overflow-wrap:anywhere}.ur-amend-comparison>div{display:grid;grid-template-columns:64px minmax(0,1fr);gap:6px;margin:3px 0}.ur-amend-comparison b{color:var(--text-muted);font-size:11.5px;font-weight:600}.ur-amend-education{font-size:12px;font-weight:400;margin-top:6px}.ur-amend-education summary{cursor:pointer;color:var(--text-sub)}.ur-amend-policy{margin:8px 0 0;line-height:1.5}#absence-request-modal .ur-education,#absence-modal .ur-education,#ur-change-modal .ur-form{margin-top:14px;border:1px solid var(--border);border-radius:10px;padding:14px;font-size:14px}.ur-education legend{font-weight:700;padding:0 5px}.ur-education legend span,.ur-sub{font-size:12px;color:var(--text-muted);font-weight:400}.ur-education-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.ur-education-grid label{display:flex;flex-direction:column;gap:5px;font-size:12px}.ur-education-grid input,.ur-education-grid textarea,#ur-change-modal input,#ur-change-modal select,#ur-change-modal textarea{box-sizing:border-box;min-width:0;width:100%;padding:8px;border:1px solid var(--border-strong);border-radius:6px;background:var(--card);color:var(--text-main)}.ur-field-error{display:block;font-size:12px;color:var(--holiday-text);margin:4px 0;grid-column:2}[aria-invalid="true"]{outline:2px solid var(--holiday-text);outline-offset:1px}.ur-form{display:grid;grid-template-columns:100px 1fr;gap:10px;align-items:center}.ur-form-row{display:contents}.ur-form-row[hidden],.ur-check[hidden],.ur-education[hidden],.ur-bulk-names[hidden]{display:none!important}.ur-check{display:flex;align-items:center;gap:7px;font-size:12px}.ur-check input,#ur-change-modal .ur-check input{width:16px;height:16px}.ur-footer{display:flex;justify-content:flex-end;gap:8px;margin-top:16px}.ur-approval-toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 12px}.ur-approval-toolbar input{flex:1;min-width:170px}.ur-approval-toolbar input,.ur-approval-toolbar select{padding:7px;border:1px solid var(--border-strong);border-radius:6px;background:var(--card);color:var(--text-main)}.ur-bulk-control{margin-bottom:12px}.ur-bulk-names{display:flex;flex-wrap:wrap;gap:8px 14px;margin-top:10px;max-height:160px;overflow:auto;font-size:12px}.ur-bulk-names label{display:flex;gap:5px;align-items:center}.ur-education-details{display:grid;grid-template-columns:80px 1fr;gap:6px;margin:10px 0;font-size:12px}.ur-education-details dt{color:var(--text-muted)}.ur-education-details dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}.ur-row-actions{display:flex;flex-wrap:wrap;gap:5px;margin-top:7px}.ur-highlight{outline:2px solid var(--primary);outline-offset:-2px}@media(max-width:600px){.ur-education-grid{grid-template-columns:1fr}.ur-form{grid-template-columns:80px 1fr}.ur-footer{flex-wrap:wrap}}.ur-req{color:var(--holiday-text);font-weight:700}.ur-required-note{margin:0 0 10px}.ur-hint{font-size:12px;color:var(--text-muted);margin:4px 0 0}.ur-field{min-width:0}.ur-zero-note{margin:4px 0 0}.ur-form-gap{display:block}#ur-change-modal .ur-form select{min-height:36px}.ur-date-viewer{display:flex;flex-direction:column;gap:8px;padding:12px 14px;border:1px solid var(--border);border-radius:10px;background:var(--surface);font-size:13px}.ur-date-line{margin:0;display:flex;flex-wrap:wrap;align-items:center;gap:6px}.ur-date-label{font-size:12px;color:var(--text-muted);font-weight:700;min-width:36px}.ur-date-actions{display:flex;flex-wrap:wrap;gap:8px}.ur-date-actions button{min-height:34px;padding:5px 14px;font-weight:700}.ur-date-memo{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;line-height:1.6;padding:8px 2px;color:var(--text-main)}.ur-date-memo.empty{color:var(--text-muted)}.ur-pending-badge{display:inline-block;padding:0 6px;border-radius:5px;background:var(--primary);color:var(--card);font-size:11px;font-weight:700;line-height:18px;white-space:nowrap}#calendar-tbody .worker-tag .ur-pending-badge{margin-left:3px;padding:0 4px;font-size:9.5px;line-height:14px;vertical-align:1px;flex:none}.ur-line-pending{display:inline-block;margin-left:6px;padding:0 5px;border-radius:4px;background:var(--primary-light);color:var(--primary);font-weight:800;white-space:nowrap}.ur-myreq-quick{display:inline-flex;align-items:center;gap:5px}.ur-other-pc{display:block;font-size:11.5px;color:var(--text-muted);margin-top:4px}.ur-state-title{font-size:15px;font-weight:700;margin:0 0 8px}.ur-state-line{margin:6px 0;font-size:13px;overflow-wrap:anywhere}.ur-state-line b{color:var(--text-muted);font-size:12px;margin-right:6px}#approval-modal .req-zero-choice{display:block;font-size:11.5px;color:var(--text-muted);line-height:1.4;margin-top:2px;white-space:nowrap}`;document.head.appendChild(style);
  style.textContent+=`
    #date-modal .modal-body:has(#ur-date-viewer .ur-own-swaps){width:700px;max-width:calc(100vw - 32px)}
    #ur-date-viewer .ur-own-swaps{min-width:0;border-top:1px solid var(--border);padding-top:12px}
    #ur-date-viewer .ur-own-swaps-heading{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:3px 10px;font-size:13px;line-height:1.5}
    #ur-date-viewer .ur-own-swaps-heading>span{font-size:11.5px;font-weight:400;color:var(--text-muted);white-space:nowrap}
    #ur-date-viewer .ur-own-swaps-range{margin:4px 0 10px;font-size:11.5px;line-height:1.5;color:var(--text-sub);overflow-wrap:anywhere}
    #ur-date-viewer .ur-own-swaps-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;list-style:none;margin:0;padding:1px 2px 2px;max-height:clamp(120px,calc(90vh - 360px),420px);overflow:auto;overscroll-behavior:contain;scrollbar-gutter:stable}
    #ur-date-viewer .ur-own-swap-row{display:grid;grid-template-columns:minmax(0,1fr) auto;grid-template-rows:auto;align-items:center;align-content:start;gap:6px 8px;min-width:0;min-height:54px;box-sizing:border-box;padding:10px;border:1px solid var(--border);border-radius:9px;background:var(--card)}
    #ur-date-viewer .ur-own-swap-info{display:contents}
    #ur-date-viewer .ur-own-swap-date{grid-column:1;grid-row:1;min-width:0;font-size:12px;font-weight:600;line-height:1.5;white-space:nowrap;font-variant-numeric:tabular-nums}
    #ur-date-viewer .ur-own-swap-reason{grid-column:1 / -1;grid-row:2;display:block;margin:0;font-size:11.5px;line-height:1.45;color:var(--text-muted);word-break:keep-all;overflow-wrap:anywhere}
    #ur-date-viewer .ur-own-swap-row>button{grid-column:2;grid-row:1;min-height:30px;padding:4px 8px;border:1px solid var(--border-strong);border-radius:7px;background:var(--card);color:var(--text-main);box-shadow:none;font-size:11.5px;font-weight:600;line-height:1.4;white-space:nowrap}
    #ur-date-viewer .ur-own-swap-row>button:hover:not(:disabled){background:var(--primary-light);border-color:var(--primary)}
    #ur-date-viewer .ur-own-swap-row>button:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
    #ur-date-viewer .ur-own-swap-row>button:disabled{opacity:1;background:var(--surface);border-color:var(--border);color:var(--text-muted);cursor:not-allowed}
    @media(max-width:680px){#ur-date-viewer .ur-own-swaps-list{grid-template-columns:1fr}#ur-date-viewer .ur-own-swap-row{padding:9px 10px}#ur-date-viewer .ur-own-swap-date{font-size:12px}}
    #myreq-modal .modal-body{box-sizing:border-box;max-width:calc(100vw - 32px)}
    #myreq-modal .table-scroll{min-width:0;overflow-x:hidden}
    #myreq-modal .data-table{table-layout:fixed;width:100%;font-size:12.5px}
    #myreq-modal .data-table th,#myreq-modal .data-table td{box-sizing:border-box;vertical-align:middle;white-space:normal;overflow-wrap:anywhere;word-break:keep-all;line-height:1.55;padding:10px}
    #myreq-modal .data-table th{font-size:11.5px}
    #myreq-modal .data-table th:nth-child(1){width:10%}
    #myreq-modal .data-table th:nth-child(2){width:9%}
    #myreq-modal .data-table th:nth-child(3){width:31%}
    #myreq-modal .data-table th:nth-child(4){width:17%}
    #myreq-modal .data-table th:nth-child(5){width:13%}
    #myreq-modal .data-table th:nth-child(6){width:20%}
    #myreq-modal .data-table th:nth-child(4),#myreq-modal .data-table th:nth-child(6),#myreq-modal td[data-label="결과"],#myreq-modal td[data-label="작업"]{text-align:center}
    #myreq-modal .ur-myreq-value{min-width:0;max-width:100%}
    #myreq-modal .ur-myreq-time{display:flex;flex-direction:column;gap:1px;font-size:12px;font-variant-numeric:tabular-nums;color:var(--text-sub);line-height:1.6}
    #myreq-modal .ur-myreq-clock{font-size:11.5px;color:var(--text-muted)}
    #myreq-modal .ur-myreq-type{font-weight:600}
    #myreq-modal .ur-myreq-content{font-size:13px;color:var(--text-main)}
    #myreq-modal .ur-myreq-dates{display:flex;flex-wrap:wrap;gap:2px 6px;align-items:baseline}
    .ur-cancel-summary{padding:14px;border:1px solid var(--border-strong);border-radius:8px;background:var(--noduty-bg);line-height:1.7}
    .ur-cancel-ranges{display:flex;flex-wrap:wrap;gap:3px 12px;margin-top:6px;font-size:13px}
    .ur-cancel-ranges span{white-space:normal}
    .ur-cancel-ranges time{white-space:nowrap}
    .ur-cancel-label{display:block;margin:16px 0 6px;font-size:13px;font-weight:600}
    .ur-cancel-label span{color:var(--text-muted);font-weight:400}
    #ur-cancel-reason{box-sizing:border-box;width:100%;font:inherit;font-size:13px;line-height:1.6;min-height:84px;resize:vertical}
    .ur-cancel-reason{margin-top:14px;font-size:13px;line-height:1.6;overflow-wrap:anywhere}
    .ur-cancel-reason p{white-space:pre-wrap;margin:5px 0}
    #myreq-modal .ur-cancel-summary{padding:0;border:0;background:none}
    #myreq-modal .ur-myreq-date{display:inline-block;white-space:nowrap;font-variant-numeric:tabular-nums}
    #myreq-modal .ur-myreq-kind{font-size:12px;color:var(--text-sub);margin-top:3px}
    #myreq-modal .ur-myreq-reason{margin-top:9px;padding-left:9px;border-left:2px solid var(--border-strong);white-space:pre-wrap;line-height:1.6}
    #myreq-modal .ur-myreq-caption{display:block;margin-bottom:2px;font-size:11.5px;color:var(--text-muted)}
    #myreq-modal .ur-myreq-rejection{white-space:pre-wrap;line-height:1.65}
    #myreq-modal .ur-education-details{grid-template-columns:64px minmax(0,1fr);gap:4px 8px;margin:9px 0 0;line-height:1.6}
    #myreq-modal .ur-education-details dt,#myreq-modal .ur-education-details dd{min-width:0}
    #myreq-modal .ur-result-badge{display:inline-block;max-width:100%;box-sizing:border-box;padding:3px 9px;border:1px solid var(--border-strong);border-radius:6px;background:var(--noduty-bg);color:var(--text-sub);font-size:12px;font-weight:700;line-height:1.5;text-align:center;white-space:nowrap}
    #myreq-modal .ur-result-approved{background:var(--male-bg);border-color:var(--male-border);color:var(--sat-blue)}
    #myreq-modal .ur-result-rejected{background:var(--holiday-bg);border-color:var(--holiday-border);color:var(--holiday-text)}
    #myreq-modal .ur-row-actions{margin:0;display:flex;flex-wrap:wrap;gap:6px;align-items:center;justify-content:center}
    #myreq-modal .ur-row-actions button{flex:none;max-width:100%;min-height:30px;padding:5px 9px;font-size:12px;font-weight:600;line-height:18px;white-space:nowrap;border-radius:6px;background:var(--card);border:1px solid var(--border-strong);color:var(--text-main)}
    #myreq-modal .ur-row-actions .ur-action-danger{color:var(--holiday-text);border-color:var(--holiday-border);background:var(--holiday-bg)}
    #myreq-modal .ur-row-actions .ur-sub{flex-basis:100%;width:100%;font-size:11.5px;line-height:1.5;margin:0}
    @media(max-width:760px){
      #myreq-modal .modal-body{padding:16px}
      #myreq-modal .data-table,#myreq-modal .data-table tbody{display:block;width:100%}
      #myreq-modal .data-table thead{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
      #myreq-modal .data-table tr{display:block;padding:10px 0;border-bottom:1px solid var(--border-strong)}
      #myreq-modal .data-table td{display:grid;grid-template-columns:74px minmax(0,1fr);gap:8px;padding:5px 6px;border:0;text-align:left}
      #myreq-modal .data-table td::before{content:attr(data-label);font-size:11.5px;font-weight:600;color:var(--text-muted)}
      #myreq-modal .data-table td[colspan]{display:block}
      #myreq-modal .data-table td[colspan]::before{content:none}
      #myreq-modal .ur-myreq-time{flex-direction:row;flex-wrap:wrap;gap:3px 8px}
      #myreq-modal .ur-row-actions{justify-content:flex-start}
    }

    #myreq-modal .ur-group-primary>td{padding:14px 10px}
    #myreq-modal .ur-kind-vacation{color:var(--vacation-text)}
    #myreq-modal .ur-kind-education{color:var(--dispatch-text)}
    #myreq-modal .ur-plain-absence .ur-myreq-kind{display:none}
    #myreq-modal .ur-group-content{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 8px;align-items:start}
    #myreq-modal .ur-plain-absence .ur-myreq-reason{margin-top:3px;padding-left:0;border:0;font-size:12px;color:var(--text-sub)}
    #myreq-modal .ur-plain-absence .ur-myreq-caption{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
    #myreq-modal .ur-history-toggle{display:inline-flex;align-items:center;gap:4px;padding:3px 4px;min-height:28px;border:0;border-radius:5px;white-space:nowrap;background:transparent;color:var(--primary-hover);font-size:11px}
    #myreq-modal .ur-history-toggle svg{width:14px;height:14px;fill:none;stroke:currentColor;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}
    #myreq-modal .ur-history-toggle[aria-expanded=true] svg{transform:rotate(180deg)}
    #myreq-modal .ur-history-toggle:focus-visible{outline:2px solid var(--primary);outline-offset:2px}
    #myreq-modal .ur-group-hint{display:block;margin-top:5px;color:var(--text-sub);font-size:11px;line-height:1.5}
    #myreq-modal .ur-group-rejected{color:var(--holiday-text)}
    #myreq-modal .ur-myreq-rejection{margin:7px 0 0;color:var(--holiday-text);font-size:12px}
    #myreq-modal .ur-group-expanded>td,#myreq-modal .ur-history-row>td{background:color-mix(in srgb,var(--primary) 4%,var(--card));border-bottom:0}
    #myreq-modal .ur-group-expanded>td:first-child,#myreq-modal .ur-history-row>td{box-shadow:inset 3px 0 var(--primary)}
    #myreq-modal .ur-history-row>td{padding:0 16px 16px;border-bottom:1px solid var(--border)}
    #myreq-modal .ur-history-row[hidden]{display:none!important}
    #myreq-modal .ur-history-panel{border-top:1px solid var(--border-strong);padding:12px 8px 0;text-align:left}
    #myreq-modal .ur-history-panel h3{font-size:12px;font-weight:600;margin:0 0 8px;color:var(--text-sub)}
    #myreq-modal .ur-history-panel ol{list-style:none;margin:0;padding:0}
    #myreq-modal .ur-history-event{display:grid;grid-template-columns:112px 106px minmax(0,1fr);gap:12px;position:relative;padding:5px 0 6px 17px;font-size:12px;line-height:1.65}
    #myreq-modal .ur-history-event::before{content:'';position:absolute;left:1px;top:13px;width:6px;height:6px;border-radius:50%;background:var(--text-muted);z-index:1}
    #myreq-modal .ur-history-event:not(:last-child)::after{content:'';position:absolute;left:3px;top:18px;bottom:-9px;border-left:1px solid var(--border-strong)}
    #myreq-modal .ur-history-event .ur-myreq-time{font-size:11px}
    #myreq-modal .ur-history-event-title{font-weight:600;color:var(--text-sub)}
    #myreq-modal .ur-history-description{color:var(--text-sub);white-space:pre-wrap;overflow-wrap:anywhere;min-width:0}
    #myreq-modal .ur-history-approved .ur-history-event-title{color:var(--sat-blue)}
    #myreq-modal .ur-history-approved::before{background:var(--sat-blue)}
    #myreq-modal .ur-history-rejected .ur-history-event-title,#myreq-modal .ur-history-rejected .ur-history-description{color:var(--holiday-text)}
    #myreq-modal .ur-history-rejected::before{background:var(--holiday-text)}
    #myreq-modal .ur-group-content>.ur-myreq-reason,#myreq-modal .ur-group-content>.ur-education-details,#myreq-modal .ur-group-content>.ur-sub{grid-column:1 / -1}
    #myreq-modal .ur-history-toggle{grid-column:2;grid-row:1}
    #myreq-modal .ur-myreq-dates{gap:2px 4px}
    @media(min-width:761px) and (max-width:1100px){
      #myreq-modal .modal-body{padding:18px}
      #myreq-modal .ur-group-primary>td{padding:12px 6px}
      #myreq-modal .ur-group-content{grid-template-columns:minmax(0,1fr)}
      #myreq-modal .ur-history-toggle{grid-column:1;grid-row:auto;justify-self:start}
      #myreq-modal .ur-row-actions{gap:4px}
      #myreq-modal .ur-row-actions button{padding:4px 7px}
    }
    @media(max-width:760px){
      #myreq-modal .ur-group-primary>td{padding:5px 6px}
      #myreq-modal .ur-history-row{padding:0;border:0}
      #myreq-modal .ur-history-row>td{display:block;padding:0 10px 12px}
      #myreq-modal .ur-group-expanded{background:color-mix(in srgb,var(--primary) 4%,var(--card));box-shadow:inset 3px 0 var(--primary)}
      #myreq-modal .ur-group-expanded>td:first-child{box-shadow:none}
      #myreq-modal .ur-history-event{grid-template-columns:88px minmax(0,1fr);gap:2px 9px;font-size:11.5px}
      #myreq-modal .ur-history-event>time{grid-row:span 2}
      #myreq-modal .ur-history-event .ur-myreq-time{flex-direction:column;font-size:10.5px}
      #myreq-modal .ur-history-description{grid-column:2}
    }
  `;
  window.OnAirRequests={refreshOwned,ownership:()=>ownershipSnapshot,reload:()=>loadMyRequests(),toggleHistory:toggleRequestHistory,cancellationText,cancellationDetails,submitCancellation,cancelById:id=>{const own=owned.get(Number(id));return own?openCancellation(own.token):showToast(OTHER_PC,true);},adminDatePlan,isVacationType,amendmentComparison,openAbsence,openChange,openMyRequests:openMyRequestsModal,openOwnedRequest,resubmit,withdraw,showProcessed,educationDetailsHtml,bulkPlan,filterApproval,submitChange,refreshPending,pendingFor,requestSummary,amendById:id=>{const own=owned.get(Number(id));return own?openAmendment(own.token):showToast(OTHER_PC,true);},editById:id=>{const own=owned.get(Number(id));return own?openOwnedRequest(own.token):showToast(OTHER_PC,true);},withdrawById:id=>{const own=owned.get(Number(id));if(own)withdraw(own.token);else showToast(OTHER_PC,true);},resubmitById:id=>{const record=owned.get(Number(id));if(record&&['REJECTED','CANCELLED'].includes(record.request.status))return resubmit(record.request);const row=myRequestsRows.find(r=>Number(r.id)===Number(id));if(row&&!isAmendment(row)&&!isCancellation(row))return resubmit({...row,reason:''});return showToast(OTHER_PC,true);}};
})();
