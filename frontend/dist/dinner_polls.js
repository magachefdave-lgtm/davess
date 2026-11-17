/* Dinner polls: private response credentials, inbox integration and employee dialogs. */
(() => {
  'use strict';
  const modalId='dinner-modal', base='/api/dinner';
  const answerLabels={possible:'참석 가능',maybe:'미정',difficult:'참석 어려움'};
  const statusLabels={DRAFT:'작성 중',OPEN:'응답 받는 중',CLOSED:'마감 · 날짜 결정 중',CONFIRMED:'날짜 확정',CANCELLED:'취소'};
  const drafts=new Map(), seenMemory=new Map(), openedMemory=new Map(), calendarIntroductions=new Set();
  let summary=null, summaryName='', refreshGeneration=0, refreshing=null, activePoll=null, viewName='', view='list', calendarMonth='', linkedDate='', busy=false, requestGeneration=0;
  const getName=()=>typeof myName==='string'?myName:'';
  const isAdmin=()=>typeof SYSTEM_MODE!=='undefined'&&SYSTEM_MODE==='ADMIN';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icon=id=>`<svg class="ico" aria-hidden="true"><use href="#i-${id}"/></svg>`;
  const today=()=>new Date(Date.now()+9*3600000).toISOString().slice(0,10);
  const dateLabel=date=>window.OnAirUI?.dateLabel?.(date)||String(date||'');
  const deadlineLabel=value=>{const date=new Date(value);return Number.isFinite(date.getTime())?date.toLocaleString('ko-KR',{timeZone:'Asia/Seoul',month:'numeric',day:'numeric',weekday:'short',hour:'2-digit',minute:'2-digit',hour12:false}):String(value||'');};
  const btn=(label,action,classes='',extra='')=>`<button type="button" class="${classes}" data-dinner-action="${action}" ${extra}>${label}</button>`;
  const pill=(label,kind='')=>`<span class="dp-pill ${kind}">${esc(label)}</span>`;
  const isOpen=poll=>poll?.status==='OPEN'&&(!poll.deadline||new Date(poll.deadline).getTime()>Date.now());
  const pending=poll=>isOpen(poll)&&(!poll.responded||poll.needsConfirmation);
  const identityKey=(dataset,name)=>encodeURIComponent(String(dataset))+'|'+encodeURIComponent(name);
  const currentScope=()=>identityKey(summary?.datasetKey||'',getName());
  const draftKey=poll=>currentScope()+'|'+poll.id;
  const operationId=()=>{
    if(typeof crypto.randomUUID==='function')return crypto.randomUUID();
    const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;
    const value=Array.from(bytes,byte=>byte.toString(16).padStart(2,'0')).join('');return `${value.slice(0,8)}-${value.slice(8,12)}-${value.slice(12,16)}-${value.slice(16,20)}-${value.slice(20)}`;
  };
  async function responseJSON(response){
    let data;try{data=await response.json();}catch{throw Object.assign(Error('서버 응답을 확인하지 못했습니다. 입력한 내용은 유지됩니다. 다시 시도해 주세요.'),{status:response.status});}
    if(!response.ok)throw Object.assign(Error(typeof data.detail==='string'?data.detail:'처리하지 못했습니다. 다시 확인해 주세요.'),{status:response.status,code:data.code||''});
    return data;
  }
  async function credentials(){
    const name=getName();if(!name)throw Error('먼저 이 PC에서 사용할 내 이름을 선택해 주세요.');
    if(typeof window.OnAirRequestAccess?.dinnerCredentials!=='function')throw Error('회식 응답 권한 연결을 불러오지 못했습니다. 프로그램 화면을 다시 열어 주세요.');
    const managed=await window.OnAirRequestAccess.dinnerCredentials(name);
    // Only an explicit unmanaged profile permits browser storage. Expiry and disk failures must propagate.
    if(managed!==null){if(!managed?.key)throw Error('회식 응답 권한을 확인하지 못했습니다.');return managed;}
    const data=await responseJSON(await fetch(base+'/summary?name='+encodeURIComponent(name),{cache:'no-store'}));
    if(!data.datasetKey)throw Error('회식 자료를 확인하지 못했습니다. 다시 시도해 주세요.');
    const storageKey='onair.dinner.browser-key.v1.'+identityKey(data.datasetKey,name);
    let key;
    try{
      key=localStorage.getItem(storageKey);
      if(!/^[a-f0-9]{64}$/.test(key||'')){
        const bytes=crypto.getRandomValues(new Uint8Array(32));key=Array.from(bytes,value=>value.toString(16).padStart(2,'0')).join('');
        localStorage.setItem(storageKey,key);
        if(localStorage.getItem(storageKey)!==key)throw Error('not persisted');
      }
    }catch{throw Error('이 브라우저에 응답 권한을 저장하지 못했습니다. 저장소 사용을 허용한 뒤 다시 시도해 주세요.');}
    return {key,datasetKey:data.datasetKey,deviceLabel:'직접 연 브라우저'};
  }
  async function api(path,options={}){
    if(typeof path!=='string'||!path.startsWith('/')||path.startsWith('//'))throw Error('요청 경로를 확인해 주세요.');
    const method=String(options.method||'GET').toUpperCase(),headers={'Content-Type':'application/json','X-OnAir-Data-Contract':'1'};
    const needsKey=!isAdmin()||path.includes('/response')||(/^\/(summary|polls\/[^/]+)(?:\?|$)/.test(path)&&getName());
    if(needsKey&&getName())headers['X-OnAir-Dinner-Key']=(await credentials()).key;
    else if(needsKey&&method!=='GET')throw Error('먼저 내 이름을 선택해 주세요.');
    const init={method,headers,cache:'no-store',referrerPolicy:'no-referrer'};
    if(options.body!==undefined)init.body=JSON.stringify(options.body);
    let response;
    try{response=isAdmin()?await fetchWithAdminToken(base+path,init,'회식 운영을 계속하려면 관리자 암호를 입력하세요.'):await fetch(base+path,init);}
    catch{throw Error('프로그램 연결을 확인하지 못했습니다. 입력한 내용은 유지됩니다. 연결을 확인한 뒤 다시 시도해 주세요.');}
    return responseJSON(response);
  }
  function readSet(key,memory){
    if(memory.has(key))return memory.get(key);
    let values=[];try{const saved=JSON.parse(localStorage.getItem(key)||'[]');if(Array.isArray(saved))values=saved.filter(value=>typeof value==='string');}catch{}
    const set=new Set(values);memory.set(key,set);return set;
  }
  function rememberSet(key,set){try{localStorage.setItem(key,JSON.stringify([...set].slice(-500)));}catch{}}
  function openedSet(){return readSet('onair.dinner.opened.v1.'+currentScope(),openedMemory);}
  function markOpened(id){const key='onair.dinner.opened.v1.'+currentScope(),set=readSet(key,openedMemory);set.add(String(id));rememberSet(key,set);renderHeader();}
  function announce(data,name){
    if(isAdmin()){
      const key='onair.dinner.connections.v1.'+String(data.datasetKey),seen=readSet(key,seenMemory);
      const arrivals=(Array.isArray(data.pendingConnections)?data.pendingConnections:[]).filter(connection=>connection?.id&&!seen.has(String(connection.id)));
      if(arrivals.length){
        arrivals.forEach(connection=>seen.add(String(connection.id)));rememberSet(key,seen);
        const text=`CA 사용 연결 요청 ${arrivals.length}건이 도착했습니다. 담당자에게 확인한 뒤 연결을 승인해 주세요.`;
        if(typeof addNotif==='function')addNotif(text,{kind:'dinner',dinnerAction:'role'});
        if(typeof showToast==='function')showToast(text);
      }
    }
    if(!name)return;
    const key='onair.dinner.events.v1.'+identityKey(data.datasetKey,name),seen=readSet(key,seenMemory);let count=0;
    for(const event of data.events||[]){
      if(!event?.id||seen.has(String(event.id)))continue;
      seen.add(String(event.id));
      if(typeof addNotif==='function')addNotif(event.text||event.message||'회식 일정이 변경되었습니다.',{kind:'dinner',pollId:event.pollId?String(event.pollId):'',person:name});
      count++;
    }
    rememberSet(key,seen);
    if(count&&typeof showToast==='function')showToast(`회식 알림 ${count}건이 도착했습니다. 알림 또는 회식 일정에서 확인하세요.`);
  }
  function renderHeader(){
    const node=document.getElementById('header-dinner');if(!node)return;
    const polls=summaryName===getName()?(summary?.polls||[]):[],waiting=polls.filter(pending),fresh=waiting.filter(poll=>!openedSet().has(String(poll.id)));
    const label=waiting.some(poll=>poll.needsConfirmation)?'투표 재확인':fresh.length?'새 투표':waiting.length?'미응답':'회식 일정';
    const connections=isAdmin()&&Array.isArray(summary?.pendingConnections)?summary.pendingConnections.length:0,count=waiting.length||connections;
    node.innerHTML=icon('calendar')+`<span>${label}</span>`+(count?`<span class="dp-header-count">${count}</span>`:'');
    node.classList.toggle('is-pending',count>0);
    node.setAttribute('aria-label',waiting.length?`${label} ${waiting.length}건 · 회식 일정 열기`:'회식 일정 열기');
    node.setAttribute('title',waiting.length?'알림을 읽어도 응답 전까지 미응답으로 표시합니다.':'회식 일정과 내 응답을 확인합니다.');
  }
  async function refresh(){
    const name=getName();if(refreshing&&summaryName===name)return refreshing;
    const generation=++refreshGeneration;
    const task=(async()=>{
      const data=await api('/summary?name='+encodeURIComponent(name));
      if(generation!==refreshGeneration||name!==getName())return null;
      summary=data;summaryName=name;announce(data,name);renderHeader();
      // Keep a form and its focus untouched when another PC responds or changes a poll.
      return data;
    })();
    refreshing=task;
    try{return await task;}finally{if(refreshing===task)refreshing=null;}
  }
  function managerActions(){
    if(isAdmin())return btn('회식 관리','manage','btn-main')+btn('CA 지정·연결 승인','role');
    const role=summary?.role;
    if(role?.memberName===getName())return btn(summary.connection?.status==='APPROVED'?'회식 관리':'CA 사용 연결',summary.connection?.status==='APPROVED'?'manage':'role','btn-main');
    return '';
  }
  function shell(title,html,width=940){
    const node=window.OnAirUI.open(modalId,title,`<div class="dp-content">${html}</div>`,{width,onClose:()=>{requestGeneration++;activePoll=null;view='closed';}});
    node.onclick=click;node.onchange=input;node.oninput=input;node.onsubmit=submit;
    return node;
  }
  function errorText(error){return error?.message||'연결을 확인한 후 다시 시도해 주세요.';}
  function showError(error){const node=document.getElementById('dp-error');if(node){node.textContent=errorText(error);node.hidden=false;}else if(typeof showToast==='function')showToast(errorText(error),true);}
  async function open(id,listOnly=false){
    const generation=++requestGeneration;viewName=getName();
    shell('회식 일정','<p class="dp-empty" role="status">회식 일정을 불러오고 있습니다.</p>');
    try{
      await refresh();if(generation!==requestGeneration)return;
      if(!id&&!listOnly){const waiting=(summary?.polls||[]).filter(pending);if(waiting.length===1)id=waiting[0].id;}
      if(id){const data=await api('/polls/'+encodeURIComponent(id)+'?name='+encodeURIComponent(viewName));if(generation!==requestGeneration||viewName!==getName())return;activePoll=data.poll;markOpened(id);renderPoll();}
      else{activePoll=null;renderList();}
    }catch(error){if(generation!==requestGeneration)return;shell('회식 일정',`<div class="dp-empty"><p role="alert">${esc(errorText(error))}</p>${btn('다시 확인','retry','btn-main',id?`data-poll-id="${esc(id)}"`:'')}${isAdmin()?managerActions():''}</div>`);}
  }
  function renderList(){
    view='list';const name=getName(),polls=summary?.polls||[];
    const rows=polls.map(poll=>`<article class="dp-list-item"><div class="dp-list-copy"><div class="dp-row"><h3>${esc(poll.title)}</h3>${pill(statusLabels[poll.status]||poll.status,poll.status==='CONFIRMED'?'good':'')}</div><p>${(poll.candidates||[]).map(candidate=>esc(dateLabel(candidate.date)+' '+candidate.time)).join(' · ')}</p><p class="dp-caption">마감 ${esc(deadlineLabel(poll.deadline))}${poll.status==='OPEN'?' · '+(poll.needsConfirmation?'변경 후보 재확인 필요':poll.responded?'응답 완료':'미응답'):''}</p></div>${btn(pending(poll)?'투표 참여':'일정·내 응답 보기','poll',pending(poll)?'btn-main':'',`data-poll-id="${esc(poll.id)}"`)}</article>`).join('');
    shell('회식 일정',`<div class="dp-toolbar"><p class="dp-caption">${name?esc(name)+' 님의 회식 일정과 응답입니다.':'내 이름을 선택하면 대상 회식과 응답을 확인할 수 있습니다.'}</p><div class="dp-actions">${managerActions()}${btn('새로 확인','refresh')}</div></div>${!name?`<div class="dp-empty"><p>이 PC에서 사용할 내 이름을 먼저 선택해 주세요.</p>${btn('내 이름 선택','name','btn-main')}</div>`:rows||'<div class="dp-empty"><h3>현재 받은 회식 날짜 취합이 없습니다.</h3><p>대상 취합이 게시되면 이곳과 알림으로 알려드립니다.</p></div>'}<p id="dp-error" class="dp-error" role="alert" hidden></p>`);
  }
  function getDraft(poll){
    const key=draftKey(poll);let draft=drafts.get(key);
    if(!draft){draft={answers:{...(poll.ownResponse?.answers||{})},suggestion:poll.ownResponse?.suggestion||'',revision:poll.revision,candidateRevisions:Object.fromEntries(poll.candidates.map(candidate=>[candidate.id,candidate.revision])),operationId:null};drafts.set(key,draft);}
    if(draft.revision!==poll.revision){
      const answers={};for(const candidate of poll.candidates){if(draft.candidateRevisions[candidate.id]===candidate.revision&&answerLabels[draft.answers[candidate.id]])answers[candidate.id]=draft.answers[candidate.id];else if(poll.ownResponse?.answers?.[candidate.id])answers[candidate.id]=poll.ownResponse.answers[candidate.id];}
      draft.answers=answers;draft.revision=poll.revision;draft.operationId=null;draft.candidateRevisions=Object.fromEntries(poll.candidates.map(candidate=>[candidate.id,candidate.revision]));
    }
    return draft;
  }
  function canAnswer(poll){return !!viewName&&poll.targets?.includes(viewName)&&isOpen(poll)&&(!poll.responded||!!poll.ownResponse);}
  function renderPoll(edit=false){
    const poll=activePoll;if(!poll)return;
    if(canAnswer(poll)&&(!poll.responded||poll.needsConfirmation||edit)){view='vote';renderVote();return;}
    view='detail';const selected=poll.candidates.find(candidate=>candidate.id===poll.selectedCandidateId),owned=poll.ownResponse;
    let message='';
    if(poll.status==='CANCELLED')message=`<div class="dp-state"><h3>이번 회식이 취소되었습니다.</h3><p>${esc(poll.notice||'기존 취합과 응답은 기록으로 보관됩니다.')}</p>${poll.dutyEffect?'<p class="dp-note">취소해도 확정 시 반영한 달력의 석식간담회 메모와 당직 없음 처리는 유지됩니다. 근무자는 자동으로 복원하지 않으며, 필요한 변경은 관리자가 달력에서 직접 조정할 수 있습니다.</p>':''}</div>`;
    else if(poll.status==='CONFIRMED')message=`<div class="dp-state"><p class="dp-caption">확정된 회식 일시</p><h3 class="dp-final-date">${selected?esc(dateLabel(selected.date)+' '+selected.time):'확정 일정 확인 필요'}</h3><dl class="dp-meta"><dt>장소</dt><dd>${esc(poll.venue||'추후 안내')}</dd><dt>안내</dt><dd>${esc(poll.notice||'별도 안내 없음')}</dd></dl>${poll.dutyEffect?'<p class="dp-note">확정 시 이 날짜에 석식간담회 메모와 당직 없음을 반영했습니다.</p>':''}</div>`;
    else if(!poll.targets?.includes(viewName))message='<div class="dp-note">이번 취합의 응답 대상이 아닙니다. 대상 확인이 필요하면 CA 또는 관리자에게 문의해 주세요.</div>';
    else if(poll.responded&&!owned)message='<div class="dp-note">다른 PC에서 제출한 응답입니다. 개인 응답은 제출한 PC에서 확인하거나 수정해 주세요. 이름을 선택하는 것만으로 기존 응답을 변경할 수 없습니다.</div>';
    else if(!isOpen(poll))message='<div class="dp-note">응답이 마감되어 참석 여부를 변경할 수 없습니다. 회식 날짜가 확정되면 알림으로 알려드립니다.</div>';
    else message='<div class="dp-success">내 응답이 저장되었습니다. 마감 전까지 수정할 수 있습니다.</div>';
    shell(poll.title,`<div class="dp-toolbar"><p class="dp-caption">${esc(viewName)} 님 · 마감 ${esc(deadlineLabel(poll.deadline))}</p>${pill(statusLabels[poll.status]||poll.status,poll.status==='CONFIRMED'?'good':'')}</div>${message}<table class="dp-answer-table"><thead><tr><th>후보 날짜</th><th>내 응답</th></tr></thead><tbody>${poll.candidates.map(candidate=>`<tr><td>${esc(dateLabel(candidate.date)+' '+candidate.time)}</td><td>${owned?esc(answerLabels[owned.answers?.[candidate.id]]||'재확인 필요'):'—'}</td></tr>`).join('')}</tbody></table>${owned?.suggestion?`<p class="dp-note">내 다른 날짜 제안: ${esc(owned.suggestion)}</p>`:''}<div class="dp-footer">${btn('회식 목록','list')}${canAnswer(poll)?btn('내 응답 수정','edit','btn-main'):''}${btn('닫기','close')}</div><p id="dp-error" class="dp-error" role="alert" hidden></p>`);
  }
  function calendar(){
    const candidates=activePoll.candidates,months=[...new Set(candidates.map(candidate=>candidate.date.slice(0,7)))].sort();
    const [year,month]=calendarMonth.split('-').map(Number),offset=new Date(Date.UTC(year,month-1,1)).getUTCDay(),last=new Date(Date.UTC(year,month,0)).getUTCDate();
    const introKey=activePoll.id+'|'+calendarMonth,animate=!calendarIntroductions.has(introKey)&&months.includes(calendarMonth);if(animate)calendarIntroductions.add(introKey);
    let cells='';for(let index=0;index<42;index++){
      if(index%7===0)cells+='<tr>';
      const day=index-offset+1,date=`${year}-${String(month).padStart(2,'0')}-${String(day).padStart(2,'0')}`,candidate=candidates.find(value=>value.date===date),current=date===today();
      let content='<span class="dp-day" aria-hidden="true"></span>';
      if(day>0&&day<=last){const label=`${year}년 ${month}월 ${day}일 ${['일','월','화','수','목','금','토'][index%7]}요일`;
        content=candidate?`<button type="button" class="dp-day dp-candidate${animate?' is-intro':''}${current?' is-today':''}" data-dinner-action="calendar-date" data-date="${date}" aria-label="${label} · CA 제안일" aria-controls="dp-ballot-${esc(candidate.id)}" aria-pressed="${linkedDate===date}">${day}</button>`:`<span class="dp-day${current?' is-today':''}${index%7===0?' is-sunday':index%7===6?' is-saturday':''}" ${current?'aria-current="date"':''}>${day}</span>`;}
      cells+=`<td data-dinner-cell="${index}">${content}</td>`;if(index%7===6)cells+='</tr>';
    }
    return `<h3>후보 날짜</h3><div class="dp-calendar-nav">${btn('‹','month-prev','','aria-label="이전 달"')}<strong>${year}년 ${month}월</strong>${btn('›','month-next','','aria-label="다음 달"')}</div><table class="dp-calendar" aria-label="${year}년 ${month}월 후보 날짜 달력"><thead><tr>${['일','월','화','수','목','금','토'].map((day,index)=>`<th scope="col" class="${index===0?'is-sunday':index===6?'is-saturday':''}">${day}</th>`).join('')}</tr></thead><tbody>${cells}</tbody></table><div class="dp-calendar-legend"><span><i></i>CA 제안일</span><span><i class="is-today"></i>오늘</span></div><p class="dp-calendar-detail" id="dp-calendar-detail" aria-live="polite">${calendarDetail()}</p><label class="dp-calendar-picker">후보가 있는 달<select data-dinner-month aria-label="후보가 있는 달">${!months.includes(calendarMonth)?'<option value="" selected disabled>후보가 있는 달 선택</option>':''}${months.map(value=>`<option value="${value}" ${value===calendarMonth?'selected':''}>${Number(value.slice(0,4))}년 ${Number(value.slice(5))}월 · 후보 ${candidates.filter(candidate=>candidate.date.startsWith(value)).length}개</option>`).join('')}</select></label>`;
  }
  function calendarDetail(){
    const candidate=activePoll.candidates.find(value=>value.date===linkedDate&&value.date.startsWith(calendarMonth));
    return candidate?`<strong>${esc(dateLabel(candidate.date)+' '+candidate.time)} 후보 확인 중</strong>오른쪽에서 참석 여부를 선택해 주세요.`:'CA가 제안한 후보일을 누르면 오른쪽 응답 항목을 확인할 수 있습니다. 날짜 클릭으로 응답이 선택되지는 않습니다.';
  }
  function scheduleHint(candidate){
    const duty=typeof scheduleMap!=='undefined'&&(scheduleMap[candidate.date]||[]).some(person=>person.name===viewName);
    const absence=typeof absences!=='undefined'&&absences.find(item=>item.name===viewName&&item.start<=candidate.date&&(item.end||item.start)>=candidate.date);
    return duty?'당직 기록 있음':absence?'부재 기록 있음':'';
  }
  function renderVote(){
    const poll=activePoll,draft=getDraft(poll);calendarMonth=/^\d{4}-\d{2}$/.test(calendarMonth)&&poll.candidates.some(candidate=>candidate.date.startsWith(calendarMonth))?calendarMonth:poll.candidates[0].date.slice(0,7);
    const changed=new Set(poll.confirmationCandidateIds||[]);
    const rows=poll.candidates.map(candidate=>`<fieldset class="dp-ballot${linkedDate===candidate.date?' is-linked':''}" id="dp-ballot-${esc(candidate.id)}" data-ballot-date="${esc(candidate.date)}"><legend>${esc(dateLabel(candidate.date)+' '+candidate.time)}</legend>${changed.has(candidate.id)?'<p class="dp-caption dp-reconfirm">날짜 또는 시간이 변경되어 다시 확인해 주세요.</p>':scheduleHint(candidate)?`<p class="dp-caption">내 일정: ${scheduleHint(candidate)} · 참석 여부는 직접 선택</p>`:''}<div class="dp-choices">${Object.entries(answerLabels).map(([value,label])=>`<label class="dp-choice"><input type="radio" name="dp-answer-${esc(candidate.id)}" value="${value}" data-answer-candidate="${esc(candidate.id)}" ${draft.answers[candidate.id]===value?'checked':''}><span>${label}</span></label>`).join('')}</div></fieldset>`).join('');
    shell(poll.title,`<div class="dp-toolbar"><p class="dp-caption">${esc(viewName)} 님 · 응답 마감 <strong>${esc(deadlineLabel(poll.deadline))}</strong></p>${pill(poll.needsConfirmation?'재확인 필요':'응답 작성',poll.needsConfirmation?'warn':'')}</div>${poll.description?`<p class="dp-description">${esc(poll.description)}</p>`:''}<div class="dp-vote-layout"><aside class="dp-calendar-pane" id="dp-calendar-pane" aria-label="CA 제안일 확인">${calendar()}</aside><form id="dp-vote-form" class="dp-vote-form"><div class="dp-note">${poll.needsConfirmation?'변경된 후보만 다시 확인해 주세요. 다른 후보의 응답은 유지됩니다.':'가능한 날짜를 모두 알려주세요. 각 날짜에 참석 가능 · 미정 · 참석 어려움 중 하나를 선택합니다.'}</div>${rows}<label class="dp-suggestion">다른 날짜 제안 <span class="dp-caption">선택 입력 · 회식 운영 담당자에게 전달</span><textarea id="dp-suggestion" rows="2" maxlength="1000">${esc(draft.suggestion)}</textarea></label><p class="dp-caption">미정 또는 모든 날짜에 참석 어려움을 선택해도 저장하면 응답 완료입니다. 불참 사유는 입력하지 않습니다.</p><p id="dp-error" class="dp-error" role="alert" hidden></p><div class="dp-footer"><span class="dp-caption" id="dp-progress">${progressText()}</span>${btn('취소','list')}<button type="submit" class="btn-main" id="dp-submit">내 응답 저장</button></div></form></div>`);
  }
  function progressText(){const draft=getDraft(activePoll);return `${activePoll.candidates.length}개 후보 중 ${activePoll.candidates.filter(candidate=>answerLabels[draft.answers[candidate.id]]).length}개 선택`;}
  function renderCalendarOnly(focusAction){
    const pane=document.getElementById('dp-calendar-pane');if(!pane)return;pane.innerHTML=calendar();
    if(focusAction)pane.querySelector(`[data-dinner-action="${focusAction}"]`)?.focus({preventScroll:true});
  }
  function selectDate(date){
    if(!activePoll?.candidates.some(candidate=>candidate.date===date))return;linkedDate=date;
    const node=document.getElementById(modalId);
    node?.querySelectorAll('[data-dinner-action="calendar-date"]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.date===date)));
    node?.querySelectorAll('[data-ballot-date]').forEach(row=>row.classList.toggle('is-linked',row.dataset.ballotDate===date));
    const detail=document.getElementById('dp-calendar-detail');if(detail)detail.innerHTML=calendarDetail();
  }
  function input(event){
    const target=event.target;if(target?.hasAttribute?.('data-dinner-month')){calendarMonth=target.value;renderCalendarOnly();document.querySelector('[data-dinner-month]')?.focus({preventScroll:true});return;}
    if(!activePoll||view!=='vote'||busy)return;
    const draft=getDraft(activePoll),candidate=target?.dataset?.answerCandidate;
    if(candidate&&activePoll.candidates.some(value=>value.id===candidate)&&answerLabels[target.value]){if(draft.answers[candidate]!==target.value){draft.answers[candidate]=target.value;draft.operationId=null;}}
    else if(target?.id==='dp-suggestion'&&draft.suggestion!==target.value){draft.suggestion=target.value;draft.operationId=null;}
    const progress=document.getElementById('dp-progress');if(progress)progress.textContent=progressText();
  }
  async function submit(event){
    if(event.target?.id!=='dp-vote-form')return;event.preventDefault();
    if(busy||!activePoll)return;
    const poll=activePoll,name=viewName,draft=getDraft(poll),generation=requestGeneration;
    if(name!==getName())return showError(Error('내 이름이 변경되었습니다. 회식 목록을 다시 열어 주세요.'));
    if(!poll.candidates.every(candidate=>answerLabels[draft.answers[candidate.id]]))return showError(Error('모든 후보 날짜에 참석 여부를 선택해 주세요.'));
    if(!draft.operationId)draft.operationId=operationId();busy=true;
    const node=document.getElementById(modalId),controls=[...(node?.querySelectorAll('input,textarea,button[type="submit"]')||[])];controls.forEach(control=>control.disabled=true);
    try{
      const data=await api('/polls/'+encodeURIComponent(poll.id)+'/response',{method:'POST',body:{memberName:name,answers:{...draft.answers},suggestion:draft.suggestion,expectedRevision:poll.revision,operationId:draft.operationId}});
      drafts.delete(draftKey(poll));
      if(generation===requestGeneration&&name===getName()){activePoll=data.poll;renderPoll();}
      await refresh().catch(()=>{});
    }catch(error){
      if(generation!==requestGeneration||name!==getName())return;
      if([409,410,423].includes(error.status)){
        try{const latest=await api('/polls/'+encodeURIComponent(poll.id)+'?name='+encodeURIComponent(name));if(generation===requestGeneration){activePoll=latest.poll;renderPoll(true);}}catch{}
      }
      showError(error);
    }finally{busy=false;controls.forEach(control=>control.disabled=false);}
  }
  async function click(event){
    const button=event.target?.closest?.('[data-dinner-action]');if(!button||button.disabled)return;
    const action=button.dataset.dinnerAction;
    if(action==='close'){window.OnAirUI.close(modalId);return;}
    if(action==='manage'){window.OnAirDinnerAdmin?.open();return;}
    if(action==='role'){window.OnAirDinnerAdmin?.openRole();return;}
    if(action==='name'){window.OnAirUI.close(modalId);await promptMyName();return open();}
    if(action==='poll'||action==='retry')return open(button.dataset.pollId||undefined);
    if(action==='list'||action==='refresh')return open(undefined,true);
    if(action==='edit'){renderPoll(true);return;}
    if(action==='calendar-date'){selectDate(button.dataset.date);return;}
    if(action==='month-prev'||action==='month-next'){
      const [year,month]=calendarMonth.split('-').map(Number),date=new Date(Date.UTC(year,month-1+(action==='month-prev'?-1:1),1));
      if(date.getUTCFullYear()<1900||date.getUTCFullYear()>9999)return;
      calendarMonth=date.toISOString().slice(0,7);renderCalendarOnly(action);
    }
  }
  function start(){
    renderHeader();refresh().catch(()=>{});
    setInterval(()=>{if(!document.hidden)return refresh().catch(()=>{});},30000);
  }
  if(typeof setMyName==='function'){
    const previous=setMyName;setMyName=function(...args){const before=getName(),result=previous.apply(this,args);if(before!==getName()){refreshGeneration++;requestGeneration++;summary=null;summaryName='';activePoll=null;window.OnAirUI?.close(modalId);renderHeader();refresh().catch(()=>{});}return result;};
  }
  if(typeof fetchSystemInfo==='function'){
    const previous=fetchSystemInfo;fetchSystemInfo=async function(...args){const result=await previous.apply(this,args);refresh().catch(()=>{});return result;};
  }
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh().catch(()=>{});});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
  window.OnAirDinner={api,refresh,open,getName,esc,credentials};
})();
