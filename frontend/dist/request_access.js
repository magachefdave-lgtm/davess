/* Local launch credentials stay in this browser session. Receipt values never enter recovery markup. */
(()=>{
'use strict';
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const base='/api/local/request-access',admin='/api/duty/request-recoveries';
let session=null,boot=null,active=null,adminMode=false,selected=new Set(),digest=null,offset=0,query='',candidateGeneration=0,timer=null,claimBusy=false,refreshBusy=false,returnFocus=null;
const operations=new Map(),claims=new Map();
const uuid=()=>crypto.randomUUID();
async function body(res){let data;try{data=await res.json();}catch{throw Error('응답을 확인하지 못했습니다. 같은 내용으로 다시 확인해 주세요.');}if(!res.ok)throw Object.assign(Error(data.detail||'처리하지 못했습니다. 다시 확인해 주세요.'),data,{status:res.status});return data;}
async function bootstrap(){if(boot)return boot;boot=(async()=>{const ticket=window.__onairAccessTicket;delete window.__onairAccessTicket;try{session=JSON.parse(sessionStorage.getItem('onair.local.session')||'null');}catch{}if(ticket){session=await body(await fetch(base+'/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticket}),cache:'no-store',referrerPolicy:'no-referrer'}));sessionStorage.setItem('onair.local.session',JSON.stringify(session));}if(session&&Date.now()>=(Number.isFinite(Number(session.expiresAt))?Number(session.expiresAt)*1000:Date.parse(session.expiresAt))){session=null;sessionStorage.removeItem('onair.local.session');}if(session)await refreshReceipts();return !!session;})().catch(error=>{boot=null;throw error;});return boot;}
async function local(path,data,method='POST'){if(!session)throw Error('OnAir 트레이에서 화면을 다시 열어 주세요.');return body(await fetch(base+path,{method,headers:{'Content-Type':'application/json','X-OnAir-Local-Session':session.sessionToken,'X-OnAir-Data-Contract':'1'},...(method==='GET'?{}:{body:JSON.stringify(data||{})}),cache:'no-store',referrerPolicy:'no-referrer'}));}
async function dinnerCredentials(name){
  const profile=await body(await fetch(base+'/dinner-profile',{cache:'no-store',referrerPolicy:'no-referrer'}));
  if(profile.managed===false)return null;
  if(profile.managed!==true)throw Error('이 PC의 회식 응답 보관 설정을 확인하지 못했습니다. 다시 확인해 주세요.');
  if(!await bootstrap())throw Error('OnAir 트레이에서 화면을 다시 열어 주세요. 회식 응답 권한을 안전하게 연결해야 합니다.');
  return local('/dinner-credentials',{name});
}
async function adminCall(path,data){return body(await fetchWithAdminToken(admin+path,{method:data?'POST':'GET',headers:{'Content-Type':'application/json','X-OnAir-Data-Contract':'1'},...(data?{body:JSON.stringify(data)}:{}),cache:'no-store'}));}
async function refreshReceipts(){if(!session)return;const tokens=typeof readMyRequestStatuses==='function'?Object.keys(readMyRequestStatuses()):[];if(tokens.length)await local('/receipts/import',{receipts:tokens});const data=await local('/receipts/read',{});for(const record of data.receipts||[])if(record.receipt_token&&typeof trackMyRequest==='function'&&!Object.hasOwn(readMyRequestStatuses(),record.receipt_token))trackMyRequest(record.receipt_token);return data;}
async function submitDurable(input,legacy){
  if(!['SUBMIT','AMEND','CANCEL_ABSENCE'].includes(input.action))return legacy();
  const pending=JSON.parse(sessionStorage.getItem('onair.access.submissions')||'{}');
  // Persist only a digest and opaque operation ID; reasons and receipts stay out of retry metadata.
  let key=null;
  if(crypto.subtle){
    const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(input)));
    key=Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join('');
  }
  const unresolved=key?(pending[key]||operations.get(key)):Object.keys(pending).length;
  try{await bootstrap();}catch(error){if(error.code==='DISABLED'&&!unresolved)return legacy();throw error;}
  if(!session){
    if(unresolved)throw Error('처리 중인 신청의 결과를 먼저 확인해야 합니다. OnAir 트레이에서 화면을 다시 열어 같은 내용으로 확인해 주세요.');
    return legacy();
  }
  if(!key)throw Error('이 브라우저에서 안전한 신청 처리를 시작할 수 없습니다. OnAir 트레이에서 화면을 다시 열어 주세요.');
  const operationId=input.operationId||pending[key]||operations.get(key)||uuid();
  operations.set(key,operationId);pending[key]=operationId;
  sessionStorage.setItem('onair.access.submissions',JSON.stringify(pending));
  function clearPending(){
    const latest=JSON.parse(sessionStorage.getItem('onair.access.submissions')||'{}');
    if(latest[key]===operationId)delete latest[key];
    sessionStorage.setItem('onair.access.submissions',JSON.stringify(latest));operations.delete(key);
  }
  let data;
  try{data=await local('/submit',{...input,operationId});}
  catch(error){if(error.code==='DISABLED'&&!unresolved){clearPending();return legacy();}throw error;}
  clearPending();return {ok:true,status:200,json:async()=>data};
}
function renderBanner(snapshot){const modal=$('myreq-modal');if(!modal)return;let banner=$('ra-banner');if(!banner){banner=document.createElement('div');banner.id='ra-banner';banner.className='ra-banner';modal.querySelector('.modal-body')?.insertBefore(banner,modal.querySelector('.modal-body').children[1]);}banner.innerHTML=`<span>${snapshot?.state&&snapshot.state!=='ready'?(snapshot.errors?.some(e=>e.status===404)?'현재 자료에서 신청을 확인할 수 없습니다. 연결 자료를 확인하고 다시 확인해 주세요.':'신청 권한을 확인하지 못했습니다. 저장된 권한 정보는 보존됩니다.'):snapshot?.owned?.size?'신청 권한이 연결되었습니다. 필요한 경우 이 PC의 복구 상태를 확인할 수 있습니다.':'이 신청의 관리 권한을 연결해야 합니다. 신청 권한 복구를 요청할 수 있습니다.'}</span><button type="button" onclick="OnAirRequests.reload()">다시 확인</button><button type="button" onclick="OnAirRequestAccess.openRecovery()">신청 권한 복구</button>`;}
function shell(title){returnFocus=document.activeElement;let panel=$('ra-modal');if(!panel){panel=document.createElement('div');panel.id='ra-modal';panel.className='modal';document.body.appendChild(panel);}panel.innerHTML=`<section class="modal-body ra-panel" role="dialog" aria-modal="true" aria-labelledby="ra-title"><div class="modal-head"><h2 id="ra-title">${title}</h2><button id="ra-close" type="button" onclick="OnAirRequestAccess.close()" aria-label="닫기">×</button></div><div id="ra-content" aria-live="polite"></div><p id="ra-error" role="alert"></p></section>`;panel.classList.add('active');window.OnAirUI?.raise?.('ra-modal');$('ra-close')?.focus?.();startPolling();}
function error(e){$('ra-error').textContent=e.message;}
const labels={PENDING:'관리자 확인 대기',APPROVED:'권한 연결 승인됨',CLAIMED:'권한을 이 PC에 저장하고 있습니다',COMPLETED:'선택한 신청의 권한이 연결되었습니다',REJECTED:'반려',CANCELLED:'철회',EXPIRED:'만료',INVALIDATED:'자료가 변경되어 재확인이 필요합니다'};
function recoveryTime(value){if(!value)return '—';const numeric=Number(value);const date=new Date(Number.isFinite(numeric)?numeric*1000:value);return Number.isNaN(date.getTime())?'—':date.toLocaleString('ko-KR');}
function status(r){return `<strong>${esc(labels[r.state]||r.state)}</strong><p>복구 확인 번호 ${esc(r.id)}</p><p>요청 ${esc(recoveryTime(r.created_at))} · 유효기간 ${esc(recoveryTime(r.claim_deadline||r.expires_at||r.expiresAt))}</p>${r.reason?`<p>${esc(r.reason)}</p>`:''}`;}
async function openRecovery(){adminMode=false;active=null;shell('신청 권한 복구');try{await bootstrap();if(!session)throw Error('OnAir 트레이에서 화면을 다시 열어 주세요.');await refreshEmployee();}catch(e){error(e);}}
async function refreshEmployee(){const data=await local('/recoveries',null,'GET');const name=$('myreq-name')?.value||'';const rows=(data.recoveries||[]).filter(r=>(r.requested_name||r.requestedName)===name);active=rows.find(r=>['PENDING','APPROVED','CLAIMED'].includes(r.state))||rows[0]||null;$('ra-content').innerHTML=`<p>선택한 이름: <b>${esc(name)}</b></p><p>이 PC에서 요청합니다. 관리자가 직원과 대상 신청을 확인한 뒤 권한 연결을 승인합니다.</p>${active?status(active):''}<div class="ra-actions"><button type="button" onclick="OnAirRequestAccess.refresh()">다시 확인</button>${!active||!['PENDING','APPROVED','CLAIMED'].includes(active.state)?'<button type="button" onclick="OnAirRequestAccess.create()">복구 요청 등록</button>':''}${active&&['APPROVED','CLAIMED'].includes(active.state)?'<button type="button" onclick="OnAirRequestAccess.claim()">이 PC에 저장 / 재시도</button>':''}${active&&['PENDING','APPROVED'].includes(active.state)?'<button type="button" onclick="OnAirRequestAccess.cancel()">복구 요청 철회</button>':''}</div>`;if(active?.state==='APPROVED')await claim();}
async function create(){try{const requestedName=$('myreq-name')?.value;if(!requestedName)throw Error('이름을 먼저 선택해 주세요.');const key='recovery:'+requestedName;if(!operations.has(key))operations.set(key,uuid());await local('/recoveries',{requestedName,operationId:operations.get(key)});operations.delete(key);await refreshEmployee();}catch(e){error(e);}}
async function claim(){
  if(claimBusy||!active)return;
  claimBusy=true;
  try{const id=active.id;if(!claims.has(id))claims.set(id,uuid());
    active=await local('/recoveries/'+encodeURIComponent(id)+'/claim',{operationId:claims.get(id)});
    if(active.state==='COMPLETED'){await refreshReceipts();await window.OnAirRequests?.reload();}
    await refreshEmployee();
  }catch(e){error(e);}finally{claimBusy=false;}
}
async function cancel(){try{await local('/recoveries/'+encodeURIComponent(active.id)+'/cancel',{operationId:uuid()});await refreshEmployee();}catch(e){error(e);}}
// Separate from ordinary approvals: a replacement request must alert even when
// the pending total stays the same. Persist opaque IDs only, scoped to the dataset.
const notificationKey='onair.access.admin-notifications.v1';
let notificationSnapshot=null;
function syncAdminNotifications(info){
  if(typeof SYSTEM_MODE==='undefined'||SYSTEM_MODE!=='ADMIN'||info?.dbOk===false)return;
  const summary=info?.requestRecovery;
  if(!summary||typeof summary.datasetKey!=='string'||!Array.isArray(summary.pendingIds))return;
  const ids=[...new Set(summary.pendingIds.filter(id=>typeof id==='string'&&id.length<=128))];
  if(typeof updateRecoveryPendingBadge==='function')updateRecoveryPendingBadge(ids.length);
  let previous=notificationSnapshot;
  try{const saved=JSON.parse(localStorage.getItem(notificationKey)||'null');if(saved&&typeof saved==='object'&&Array.isArray(saved.pendingIds))previous=saved;}catch{}
  const seen=new Set(previous?.datasetKey===summary.datasetKey?previous.pendingIds:[]);
  const arrived=ids.filter(id=>!seen.has(id)).length;
  notificationSnapshot={datasetKey:summary.datasetKey,pendingIds:ids};
  try{localStorage.setItem(notificationKey,JSON.stringify(notificationSnapshot));}catch{}
  if(arrived){
    const text=`신청 권한 복구 요청 ${arrived}건이 도착했습니다. 대기 중인 ${ids.length}건을 확인해 주세요.`;
    if(typeof addNotif==='function')addNotif(text,{kind:'recovery'});
    if(typeof showToast==='function')showToast(text);
  }
}
async function openAdminRecovery(){adminMode=true;active=null;selected.clear();shell('신청 권한 복구 · 관리자 확인');await refreshAdmin();}
async function refreshAdmin(){try{const data=await adminCall('');syncAdminNotifications({requestRecovery:data.notifications});if(active){const current=(data.recoveries||[]).find(r=>r.id===active.id);if(current&&current.state!=='PENDING'){active=current;digest=null;$('ra-content').innerHTML=status(current)+'<button type="button" onclick="OnAirRequestAccess.adminList()">복구 요청 목록</button>';}return;}$('ra-content').innerHTML='<p>권한 연결 승인은 휴가 승인과 별개이며 기존 일정을 변경하지 않습니다.</p>'+((data.recoveries||[]).map((r,i)=>`<article>${status(r)}<p>${esc(r.requested_name||r.requestedName)} · ${esc(r.host_label)} · ${esc(r.account_label)}</p>${r.state==='PENDING'?`<button type="button" data-ra-review="${i}">대상 신청 확인</button>`:''}</article>`).join('')||'<p>복구 요청이 없습니다.</p>');document.querySelectorAll('[data-ra-review]').forEach(el=>el.onclick=()=>review(data.recoveries[Number(el.dataset.raReview)]));}catch(e){error(e);}}
async function review(r){active=r;selected.clear();digest=null;offset=0;query='';await candidates();}
function businessState(value){return {PENDING:'승인 대기',APPROVED:'승인',REJECTED:'반려',CANCELLED:'철회'}[value]||'상태 확인 필요';}
function requestKind(value){return {ABSENCE:'휴가·교육·부재',ABSENCE_CANCEL:'취소 요청',SWAP:'맞교환',SUBSTITUTE:'대체 근무'}[value]||'신청';}
function exclusions(blockers){
  if(!blockers?.length)return '';
  const details=blockers.map(item=>typeof item==='string'?item:item.detail||'신청 원본 또는 연결 관계를 확인해 주세요.');
  return '<aside class="ra-exclusions"><h3>복구 대상에서 제외된 신청</h3><ul>'+details.map(detail=>'<li>'+esc(detail)+'</li>').join('')+'</ul></aside>';
}
async function candidates(){const generation=++candidateGeneration;try{const data=await adminCall('/'+encodeURIComponent(active.id)+'/candidates?offset='+offset+'&limit=50&q='+encodeURIComponent(query));if(generation!==candidateGeneration)return;$('ra-error').textContent='';$('ra-content').innerHTML=status(active)+'<button type="button" onclick="OnAirRequestAccess.adminList()">복구 요청 목록</button>'+ `<label>과거 신청 검색 <input id="ra-search" type="search" value="${esc(query)}" placeholder="이름·기간"></label><button type="button" onclick="OnAirRequestAccess.search()">검색</button><p>대상 신청 그룹을 직접 선택하세요. 선택 ${selected.size}건 / 검색 결과 ${Number(data.total)}건</p><div class="ra-candidates">${(data.groups||[]).map((g,i)=>`<label><input type="checkbox" data-ra-group="${i}" ${selected.has(g.rootUid)?'checked':''}><span>${esc(g.requester_name)} · ${esc(g.target_date)} ~ ${esc(g.absence_end_date)} · ${esc(g.absence_type||'휴가·교육·부재')} · ${esc(businessState(g.status))} · 연결 ${Number(g.count)}건<small class="ra-candidate-time">신청 ${esc(recoveryTime(g.created_at))}</small></span></label>`).join('')}</div>${exclusions(data.blockers)}<div class="ra-actions"><button type="button" id="ra-prev" ${offset?'':'disabled'}>이전</button><button type="button" id="ra-next" ${offset+50<Number(data.total)?'':'disabled'}>다음</button><button type="button" onclick="OnAirRequestAccess.preview()" ${selected.size?'':'disabled'}>선택 범위 미리보기</button><button type="button" onclick="OnAirRequestAccess.reject()">반려</button></div><div id="ra-preview"></div>`;$('ra-prev').onclick=()=>{offset=Math.max(0,offset-50);return candidates();};$('ra-next').onclick=()=>{offset+=50;return candidates();};document.querySelectorAll('[data-ra-group]').forEach(el=>el.onchange=()=>{const id=data.groups[Number(el.dataset.raGroup)].rootUid;el.checked?selected.add(id):selected.delete(id);digest=null;return candidates();});}catch(e){error(e);}}
async function preview(){try{digest=null;const roots=[...selected],scope=JSON.stringify(roots),id=active.id;const data=await adminCall('/'+encodeURIComponent(id)+'/preview',{rootUids:roots});if(active?.id!==id||JSON.stringify([...selected])!==scope)return;digest=data.blockers?.length?null:data.digest;$('ra-preview').innerHTML=`<h3>선택한 신청의 연결 범위 · ${Number(data.items?.length||0)}건</h3><ul>${(data.items||[]).map(i=>`<li>#${esc(i.request_id||i.request_uid||i.id)} · ${esc(i.requester_name)} · ${esc(i.target_date)} ~ ${esc(i.absence_end_date||i.target_date)} · ${esc(i.absence_type||requestKind(i.request_type))} · ${esc(businessState(i.status))} · 신청 ${esc(recoveryTime(i.created_at))}</li>`).join('')}</ul>${exclusions(data.blockers)}<button type="button" onclick="OnAirRequestAccess.approve()" ${!selected.size||data.blockers?.length?'disabled':''}>권한 연결 승인</button>`;}catch(e){error(e);}}
async function decision(action){try{if(action==='APPROVE'&&(!digest||!selected.size))throw Error('대상 신청을 선택하고 미리보기를 다시 확인해 주세요.');const reason=action==='REJECT'?prompt('반려 사유를 입력하세요.'):'';if(reason===null)return;const input={action,rootUids:[...selected],expectedDigest:digest,reason};const key=active.id+JSON.stringify(input);if(!operations.has(key))operations.set(key,uuid());await adminCall('/'+encodeURIComponent(active.id)+'/decision',{...input,operationId:operations.get(key)});operations.delete(key);active=null;await refreshAdmin();}catch(e){if(e.code==='SCOPE_CHANGED'){digest=null;await candidates();}error(e);}}
async function refresh(){if(document.hidden||refreshBusy)return;refreshBusy=true;try{if(adminMode)await refreshAdmin();else await refreshEmployee();}catch(e){error(e);}finally{refreshBusy=false;}}
function startPolling(){clearInterval(timer);timer=setInterval(()=>{if(!document.hidden&&$('ra-modal')?.classList.contains('active'))refresh();},10000);}
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&$('ra-modal')?.classList.contains('active'))refresh();});
document.addEventListener('keydown',event=>{const panel=$('ra-modal');if(!panel?.classList.contains('active'))return;if(event.key==='Escape'){event.preventDefault();window.OnAirRequestAccess.close();}if(event.key==='Tab'){const nodes=Array.from(panel.querySelectorAll('button:not(:disabled),input:not(:disabled),[tabindex="0"]'));const first=nodes[0],last=nodes[nodes.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}});
window.OnAirRequestAccess={syncAdminNotifications,bootstrap,dinnerCredentials,refreshReceipts,renderBanner,submitDurable,openRecovery,openAdminRecovery,refresh,create,claim,cancel,preview,adminList:()=>{active=null;selected.clear();digest=null;return refreshAdmin();},search:()=>{query=$('ra-search').value;offset=0;digest=null;return candidates();},approve:()=>decision('APPROVE'),reject:()=>decision('REJECT'),close:()=>{clearInterval(timer);$('ra-modal')?.classList.remove('active');returnFocus?.focus?.();}};
bootstrap().catch(()=>{});
})();
