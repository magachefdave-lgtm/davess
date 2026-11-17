/* CA and administrator dinner workflows. Secrets and transport belong to dinner_polls.js. */
(()=>{
'use strict';
const ID='onair-dinner-admin',CONFIRM='onair-dinner-confirm';
const D=()=>window.OnAirDinner;
const $=id=>document.getElementById(id);
const esc=value=>D().esc(value);
const uuid=()=>{if(typeof crypto.randomUUID==='function')return crypto.randomUUID();const bytes=crypto.getRandomValues(new Uint8Array(16));bytes[6]=(bytes[6]&15)|64;bytes[8]=(bytes[8]&63)|128;const h=Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20);};
const nowKST=()=>new Date(Date.now()+9*3600000).toISOString().slice(0,16);
const dateOnly=()=>nowKST().slice(0,10);
const iso=d=>d.toISOString().slice(0,10);
const shortDate=value=>{const day=String(value||'').slice(0,10);return window.OnAirUI.dateLabel(day,day.slice(0,4)!==dateOnly().slice(0,4));};
const stamp=value=>value?shortDate(value)+' '+String(value).slice(11,16):'미정';
const administrator=()=>typeof SYSTEM_MODE!=='undefined'&&SYSTEM_MODE==='ADMIN';
const M={
  roles(data){return Array.isArray(data.roles)?data.roles:(data.role?[data.role]:[]);},
  roleId(role){return Number(role?.roleId)||1;},
  yearRole(data,year){const roles=M.roles(data),start=year+'-01-01',end=year+'-12-31';const matching=roles.filter(r=>r.memberName&&r.startDate<=end&&r.endDate>=start);return matching.find(r=>M.termState(r)==='임기중')||matching.find(r=>M.termState(r)==='예정')||matching[0]||roles.find(r=>Number(r.roleId)===year)||roles.find(r=>!r.memberName&&M.roleId(r)===1&&year===Number(dateOnly().slice(0,4)))||null;},
  termState(role,today=dateOnly()){return !role?.memberName?'미지정':today<role.startDate?'예정':today>role.endDate?'종료':'임기중';},
  roleConnections(data,role){return role?(data.connections||[]).filter(c=>(Number(c.roleId)||1)===M.roleId(role)):[];},
  connectionState(role,connections,today=dateOnly()){if(connections.some(c=>c.status==='APPROVED'))return M.termState(role,today)==='예정'?'승인완료·임기대기':M.termState(role,today)==='임기중'?'사용중':'임기 종료';return connections.some(c=>c.status==='PENDING')?'승인대기':'연결미요청';},
  validDate(value){if(!/^\d{4}-\d{2}-\d{2}$/.test(value||''))return false;const d=new Date(value+'T00:00:00Z');return Number.isFinite(d.getTime())&&iso(d)===value;},
  termEnd(start,years){if(!M.validDate(start))throw Error('시작일을 먼저 선택해 주세요.');const [y,m,d]=start.split('-').map(Number);return iso(new Date(Date.UTC(y+Number(years),m-1,d-1)));},
  monthDays(month){const [y,m]=month.split('-').map(Number),first=new Date(Date.UTC(y,m-1,1)),offset=first.getUTCDay();return Array.from({length:42},(_,i)=>iso(new Date(Date.UTC(y,m-1,1-offset+i))));},
  validateDraft(input,now=nowKST()){
    const title=String(input.title||'').trim(),description=String(input.description||'').trim(),deadline=String(input.deadline||'').slice(0,16);
    if(!title||title.length>100)throw Error('제목은 1~100자로 입력해 주세요.');
    if(description.length>2000)throw Error('안내 내용은 2,000자 이내로 입력해 주세요.');
    if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(deadline)||!M.validDate(deadline.slice(0,10))||!/^([01]\d|2[0-3]):[0-5]\d$/.test(deadline.slice(11))||deadline<=now)throw Error('응답 마감은 현재 시각 이후로 선택해 주세요.');
    if(!Array.isArray(input.candidates)||input.candidates.length<2||input.candidates.length>5)throw Error('후보 날짜는 2개 이상, 5개 이하로 선택해 주세요.');
    const candidates=input.candidates.map(c=>({id:String(c.id||uuid()),date:String(c.date||''),time:String(c.time||'')}));
    if(candidates.some(c=>!M.validDate(c.date)||!/^([01]\d|2[0-3]):[0-5]\d$/.test(c.time)))throw Error('후보 날짜와 시간을 확인해 주세요.');
    if(new Set(candidates.map(c=>c.date)).size!==candidates.length)throw Error('같은 날짜를 후보에 중복해서 넣을 수 없습니다.');
    if(new Set(candidates.map(c=>c.id)).size!==candidates.length)throw Error('후보 식별정보가 겹칩니다. 목록에서 다시 열어 주세요.');
    if(candidates.some(c=>c.date+'T'+c.time<=deadline))throw Error('응답 마감은 모든 후보 날짜·시간보다 앞서야 합니다.');
    const targets=[...new Set((input.targets||[]).map(String).filter(Boolean))];
    if(!targets.length)throw Error('응답 대상 직원을 한 명 이상 선택해 주세요.');
    return {title,description,deadline,candidates,targets};
  },
  operationBook(){const ids=new Map();return {id(path,body){const key=path+'\n'+JSON.stringify(body);if(!ids.has(key))ids.set(key,uuid());return ids.get(key);},done(path,body){ids.delete(path+'\n'+JSON.stringify(body));}};},
  completed(poll){return (poll.results?.responders||[]).filter(r=>r.complete).length;},
  outstanding(poll){const result=new Map(),r=poll.results||{};for(const row of r.nonresponders||[]){const name=typeof row==='string'?row:row.name;if(name)result.set(name,{name,needsConfirmation:false});}for(const row of [...(r.confirmationRequired||[]),...(r.responders||[]).filter(r=>!r.complete)]){const name=typeof row==='string'?row:row.name;if(name)result.set(name,{name,needsConfirmation:true});}return [...result.values()];}
};
const operations=M.operationBook();
let state={view:'list',data:null,poll:null,draft:null,filter:'active',month:dateOnly().slice(0,7)},busy=false,loadGeneration=0;
const statusNames={DRAFT:'작성 중',OPEN:'응답 중',CLOSED:'마감 · 집계 중',CONFIRMED:'날짜 확정',CANCELLED:'취소'};
const connectionNames={PENDING:'관리자 확인 대기',APPROVED:'승인됨',REJECTED:'반려',REVOKED:'연결 해제',INVALIDATED:'연결 무효',EXPIRED:'임기 종료'};
const pill=(text,type='')=>`<span class="da-pill ${esc(type)}">${esc(text)}</span>`;
const status=poll=>pill(statusNames[poll.status]||poll.status,({OPEN:'good',CONFIRMED:'blue',CANCELLED:'danger'})[poll.status]||'');
const button=(action,text,extra='',cls='')=>`<button type="button" data-da="${action}" class="${cls}" ${extra}>${text}</button>`;
const icon=id=>`<svg aria-hidden="true" class="da-icon"><use href="#i-${id}"></use></svg>`;
const notice=text=>`<div class="da-note">${esc(text)}</div>`;
const dutyApplied='확정 시 이 날짜에 석식간담회 메모와 당직 없음을 반영했습니다.';
const dutyCancel='취소해도 확정 시 반영한 달력의 석식간담회 메모와 당직 없음 처리는 유지됩니다. 근무자는 자동으로 복원하지 않으며, 필요한 변경은 관리자가 달력에서 직접 조정할 수 있습니다.';
const footer=html=>`<div class="da-footer">${html}</div>`;
function error(error,container='da-error'){const node=$(container);if(node){node.hidden=false;node.textContent=error?.message||String(error);node.focus({preventScroll:true});}}
function clearError(){const node=$('da-error');if(node){node.hidden=true;node.textContent='';}}
function notify(text){if(typeof showToast==='function')showToast(text);}
function shell(title,html,width=980){
  const node=OnAirUI.open(ID,title,`<section class="da-app"><div id="da-error" class="da-error" role="alert" tabindex="-1" hidden></div>${html}</section>`,{width,onClose:()=>{loadGeneration++;}});
  node.onclick=onClick;node.onchange=onChange;node.oninput=onInput;
  node.onkeydown=e=>{if(e.key==='Enter'&&e.target.matches('input:not([type=checkbox]):not([type=radio])'))e.preventDefault();};
  return node;
}
function hasOpen(){return $(ID)?.classList.contains('active');}
async function request(path,body,method='POST'){
  const result=await D().api(path,{method,body:{...body,operationId:operations.id(path,body)}});
  operations.done(path,body);D().refresh().catch(()=>{});return result;
}
async function run(work){if(busy)return;busy=true;clearError();const nodes=[...($(ID)?.querySelectorAll('button[data-da]')||[])];nodes.forEach(n=>{n.dataset.daWasDisabled=String(n.disabled);n.disabled=true;});try{await work();}catch(e){error(e);}finally{busy=false;nodes.filter(n=>n.isConnected).forEach(n=>{n.disabled=n.dataset.daWasDisabled==='true';delete n.dataset.daWasDisabled;});}}
async function open(){
  const generation=++loadGeneration;shell('회식 날짜 취합',notice('회식 일정을 불러오고 있습니다.'));
  try{const data=await D().api('/manage');if(generation!==loadGeneration||!hasOpen())return;state.data=data;state.view='list';state.poll=null;renderList();}
  catch(e){if(generation!==loadGeneration||!hasOpen())return;if(e.status===403){await openRole();}else{shell('회식 날짜 취합',notice('자료를 불러오지 못했습니다.')+footer(button('reload','다시 불러오기')));error(e);}}
}
async function openRole(){
  const generation=++loadGeneration;shell(administrator()?'CA 담당자와 사용 연결':'CA 사용 연결',notice('담당자 정보를 확인하고 있습니다.'),860);
  try{const data=await D().api(administrator()?'/manage':'/summary?name='+encodeURIComponent(D().getName()||''));if(generation!==loadGeneration||!hasOpen())return;state.data=data;state.view='role';state.roleYear=Number(dateOnly().slice(0,4));state.roleDrafts={};state.roleSelections={};state.connectionTab='pending';state.connectionPage=0;renderRole();}
  catch(e){if(generation===loadGeneration&&hasOpen())error(e);}
}
function roleCaption(role){if(!role?.memberName)return '담당자 미지정 · 관리자가 회식을 운영합니다.';const range=role.startDate+' ~ '+role.endDate;return role.active?range:dateOnly()<role.startDate?range+' · 임기 시작 전':range+' · 임기 종료';}
function renderList(){
  const data=state.data,all=data.polls||[];
  const visible=all.filter(p=>state.filter==='all'||(state.filter==='active'?['DRAFT','OPEN','CLOSED'].includes(p.status):['CONFIRMED','CANCELLED'].includes(p.status))).sort((a,b)=>String(b.createdAt||'').localeCompare(String(a.createdAt||'')));
  shell('회식 날짜 취합',`<div class="da-toolbar"><div><h2>회식 일정</h2><p class="da-muted">${esc(data.role?.active?'CA '+data.role.memberName:'관리자 운영')} · ${esc(roleCaption(data.role))}</p></div><div class="da-actions">${button('role',administrator()?'CA 담당자 관리':'CA 사용 연결')}${button('new',icon('calendar')+'새 날짜 취합','','da-primary')}</div></div>
    <div class="da-tabs" aria-label="회식 목록 필터">${[['active','진행 중'],['past','확정 · 취소'],['all','전체']].map(([key,label])=>button('filter',label,`data-filter="${key}" aria-pressed="${state.filter===key}"`,state.filter===key?'selected':'')).join('')}</div>
    <div class="da-poll-list">${visible.length?visible.map(p=>`<article class="da-poll-card"><div class="da-card-main"><div class="da-row">${status(p)}<h3>${esc(p.title)}</h3></div><p class="da-muted">후보 ${p.candidates?.length||0}개 · 대상 ${p.targets?.length||0}명 · 응답 마감 ${esc(stamp(p.deadline))}</p><div class="da-card-dates">${(p.candidates||[]).map(c=>`<span>${esc(shortDate(c.date))} ${esc(c.time)}</span>`).join('')}</div></div><div class="da-card-side"><strong>${M.completed(p)}<small> / ${p.targets?.length||0}명 응답</small></strong>${button('detail',p.status==='DRAFT'?'작성 이어가기':'응답 현황',`data-id="${esc(p.id)}"`)}</div></article>`).join(''):`<div class="da-empty">${icon('calendar')}<h3>${state.filter==='past'?'확정·취소된 회식이 없습니다.':'등록된 취합이 없습니다.'}</h3><p>새 날짜 취합에서 후보와 대상 직원을 선택하세요.</p></div>`}</div>`);
}
function selectedRole(){const year=state.roleYear||Number(dateOnly().slice(0,4));state.roleSelections=state.roleSelections||{};if(!state.roleSelections[year]){const initial=M.yearRole(state.data,year);state.roleSelections[year]=initial?M.roleId(initial):year;}return M.roles(state.data).find(r=>M.roleId(r)===state.roleSelections[year])||null;}
function roleDraftKey(){const role=selectedRole();return 'role-'+(role?M.roleId(role):state.roleSelections[state.roleYear]||state.roleYear);}
function roleDraft(){state.roleDrafts=state.roleDrafts||{};const role=selectedRole(),year=state.roleYear;return state.roleDrafts[roleDraftKey()]||(state.roleDrafts[roleDraftKey()]={roleId:role?M.roleId(role):year,expectedRevision:role?.revision||0,memberName:role?.memberName||'',startDate:role?.startDate||year+'-01-01',endDate:role?.endDate||year+'-12-31'});}
function captureRole(){if(!$('da-ca-name'))return;Object.assign(roleDraft(),{memberName:$('da-ca-name').value,startDate:$('da-term-start').value,endDate:$('da-term-end').value});}
function viewerRoles(){return M.roles(state.data).filter(r=>r.memberName===D().getName()&&['임기중','예정'].includes(M.termState(r)));}
function viewerRole(){return viewerRoles().find(r=>M.roleId(r)===state.viewerRoleId)||state.data.assignedRole||state.data.role;}
function viewerConnection(){const role=viewerRole(),data=state.data;if(!role)return null;if(data.ownConnections)return data.ownConnections[String(M.roleId(role))]||null;return data.connection&&(Number(data.connection.roleId)||1)===M.roleId(role)?data.connection:null;}
function updateRole(result){const role=result.role,roles=[...M.roles(state.data).filter(r=>M.roleId(r)!==M.roleId(role)),role];state.data.roles=roles;state.data.role=roles.find(r=>M.termState(r)==='임기중')||null;if(state.roleDrafts)delete state.roleDrafts['role-'+M.roleId(role)];}
function renderRole(){
  const data=state.data;
  if(!administrator()){
    const role=viewerRole(),connection=viewerConnection(),mine=!!role?.memberName&&role.memberName===D().getName(),term=M.termState(role),enabled=mine&&['임기중','예정'].includes(term),approved=connection?.status==='APPROVED';
    const connectionLabel=approved&&term==='예정'?'승인완료·임기시작대기':approved&&term==='임기중'?'사용중':connectionNames[connection?.status];
    shell('CA 사용 연결',`${viewerRoles().length>1?`<div class="da-tabs da-viewer-roles" aria-label="사용 연결할 CA 임기">${viewerRoles().map(r=>button('viewer-role',esc(r.startDate+' ~ '+r.endDate)+' · '+esc(M.termState(r)),`data-role-id="${M.roleId(r)}" aria-pressed="${M.roleId(r)===M.roleId(role)}"`,M.roleId(r)===M.roleId(role)?'selected':'')).join('')}</div>`:''}<div class="da-role-card"><span class="da-eyebrow">회식 담당자</span><h2>${esc(role?.memberName||'담당자 미지정')}</h2><p>${esc(roleCaption(role))}</p>${pill(term)} ${connectionLabel?pill(connectionLabel,approved?'good':''):''}</div>${enabled?notice(term==='예정'?'임기 전에 이 PC의 사용 연결을 요청하고 승인받을 수 있습니다. 회식 관리는 임기 시작일부터 사용할 수 있습니다.':'관리자가 이 PC의 사용 연결을 승인하면 날짜 취합과 결과 관리를 시작할 수 있습니다.'):notice(mine?'현재 임기가 종료되었습니다. 담당자나 임기를 관리자에게 확인해 주세요.':'CA 운영 권한은 관리자가 지정한 담당자에게 부여됩니다.')}${connection?.verificationCode?`<div class="da-verification"><span>관리자에게 알려줄 확인 코드</span><strong>${esc(connection.verificationCode)}</strong><small>${esc(connection.deviceLabel||'')}</small></div>`:''}${connection?.status==='REJECTED'?notice(connection.reason||'연결 요청이 반려되었습니다. 관리자에게 확인 후 다시 요청해 주세요.'):''}${footer(enabled&&approved&&term==='임기중'?button('reload','회식 관리 열기','','da-primary'):enabled&&approved?button('role','승인 상태 새로 확인'):enabled?button('connect',connection?.status==='PENDING'?'승인 상태 새로 확인':'이 PC 사용 연결 요청','', 'da-primary'):button('close','닫기'))}`,680);
    return;
  }
  const thisYear=Number(dateOnly().slice(0,4)),year=state.roleYear||thisYear,role=selectedRole(),assigned=!!role?.memberName,term=M.termState(role),connections=M.roleConnections(data,role);
  const appointments=M.roles(data).filter(r=>r.memberName&&r.startDate<=year+'-12-31'&&r.endDate>=year+'-01-01');
  const draft=roleDraft(),stale=draft.conflict||draft.expectedRevision!==(role?.revision||0)||draft.roleId!==(role?M.roleId(role):year);
  const members=(data.members||[]).map(m=>typeof m==='string'?m:m.name),names=[...new Set([...M.roles(data).map(r=>r.memberName).filter(Boolean),...members])];
  const groups={pending:connections.filter(c=>c.status==='PENDING'),approved:connections.filter(c=>c.status==='APPROVED'),history:connections.filter(c=>!['PENDING','APPROVED'].includes(c.status))};
  const tab=state.connectionTab||'pending',rows=groups[tab],pageCount=Math.max(1,Math.ceil(rows.length/2)),page=Math.min(state.connectionPage||0,pageCount-1);state.connectionPage=page;
  const cards=[thisYear,thisYear+1].map(y=>{const r=M.yearRole(data,y),cs=M.roleConnections(data,r),ts=M.termState(r);return button('role-year',`<span class="da-year-label">${y===thisYear?'올해':'내년'} · ${y}년</span><span class="da-year-person">${esc(r?.memberName||'담당자 미지정')}${pill(ts,ts==='임기중'?'good':'')}</span><span class="da-year-term">${esc(r?.memberName?r.startDate+' ~ '+r.endDate:y+'-01-01 ~ '+y+'-12-31')}</span><span class="da-year-progress">${esc(M.connectionState(r,cs))}${cs.some(c=>c.status==='PENDING')?' · '+cs.filter(c=>c.status==='PENDING').length+'건 대기':''}</span>`,`data-year="${y}" aria-pressed="${year===y}"`, 'da-year-card'+(year===y?' selected':''));}).join('');
  shell('CA 담당자와 사용 연결',`<div class="da-role-workspace"><div class="da-role-intro"><p>올해와 내년 담당자를 지정하고, 담당자별 PC 사용 연결을 승인하세요.</p><span class="da-muted">교체 후에도 기존 취합과 응답은 이어집니다.</span></div><div class="da-role-grid"><section class="da-panel da-appointment-panel"><div class="da-year-cards">${cards}</div><div class="da-section-head"><h2>${year}년 담당자 지정</h2>${appointments.length>1?`<select id="da-role-appointment" aria-label="이 해의 담당자 임기 선택">${appointments.map(r=>`<option value="${M.roleId(r)}" ${M.roleId(r)===M.roleId(role)?'selected':''}>${esc(r.memberName+' · '+r.startDate+' ~ '+r.endDate)}</option>`).join('')}</select>`:pill(term,term==='임기중'?'good':'')}</div>${stale?`<div class="da-draft-conflict" role="status">담당자 지정이 변경되었습니다. 입력 내용은 보존했습니다. ${button('role-refresh','최신 지정 불러오기 (입력 초기화)')}</div>`:''}<label class="da-field">담당 직원<select id="da-ca-name"><option value="">직원 선택</option>${names.map(n=>`<option ${n===draft.memberName?'selected':''} value="${esc(n)}">${esc(n)}</option>`).join('')}</select></label><div class="da-two"><label class="da-field">임기 시작<input id="da-term-start" type="date" value="${esc(draft.startDate)}"></label><label class="da-field">임기 종료<input id="da-term-end" type="date" value="${esc(draft.endDate)}"></label></div><div class="da-actions da-term-presets">${button('term','1년', 'data-years="1"')}${button('term','2년','data-years="2"')}<small class="da-muted">시작일부터 · 날짜 직접 지정 가능</small></div>${notice('담당자를 바꾸면 해당 임기의 이전 PC 연결이 해제됩니다. 두 해에 걸친 임기는 같은 지정 내용을 공유합니다.')}<div class="da-actions da-role-save">${button('save-role',assigned?'지정 내용 저장':'CA 지정','','da-primary')}${assigned?button('remove-role','CA 권한 해제','','da-danger'):''}</div></section><section class="da-panel da-approval-panel"><div class="da-section-head"><h2>사용 연결 승인</h2>${pill(groups.pending.length+'건 대기',groups.pending.length?'blue':'')}</div><div class="da-selected-identity"><div class="da-row"><strong>${esc(role?.memberName||'담당자 미지정')}</strong>${pill(term,term==='임기중'?'good':'')}${pill(M.connectionState(role,connections))}</div><p>${assigned?esc(role.startDate+' ~ '+role.endDate):year+'년 담당자를 먼저 지정해 주세요.'}</p></div><p class="da-muted">선택한 담당자의 PC입니다. 확인 코드를 대조한 뒤 승인하세요.</p><div class="da-tabs da-connection-tabs" aria-label="사용 연결 상태">${[['pending','승인대기'],['approved','승인된 PC'],['history','이전 기록']].map(([key,label])=>button('connection-tab',label+' '+groups[key].length,`data-tab="${key}" aria-pressed="${tab===key}"`,tab===key?'selected':'')).join('')}</div><div class="da-connections">${rows.length?rows.slice(page*2,page*2+2).map(c=>`<article class="da-connection ${c.status==='APPROVED'?'da-connected':''}"><div class="da-row"><strong>${esc(c.deviceLabel||'PC 이름 없음')}</strong>${pill(c.status==='APPROVED'?M.connectionState(role,[c]):connectionNames[c.status]||c.status,c.status==='APPROVED'?'good':'')}</div><p>${esc(c.memberName||role?.memberName||'CA')}${c.reason?' · '+esc(c.reason):''}</p>${c.status==='PENDING'?`<div class="da-connection-bottom"><div class="da-code">${esc(c.verificationCode||'코드 없음')}</div>${button('connection-review','연결 확인',`data-id="${esc(c.id)}"`,'da-primary')}</div>`:''}</article>`).join(''):`<div class="da-empty-small">${tab==='pending'?'새 사용 연결 요청이 없습니다.':tab==='approved'?'승인된 PC가 없습니다.':'이전 연결 기록이 없습니다.'}</div>`}</div>${pageCount>1?`<div class="da-connection-pages">${button('connection-page','이전',`data-page="${page-1}" ${page===0?'disabled':''}`)}<span>${page+1} / ${pageCount}</span>${button('connection-page','다음',`data-page="${page+1}" ${page===pageCount-1?'disabled':''}`)}</div>`:''}</section></div>${footer(button('reload','회식 일정으로'))}</div>`,1280);
}
function defaultDraft(){const base=new Date(dateOnly()+'T00:00:00Z'),day=n=>iso(new Date(base.getTime()+n*86400000));return {title:'',description:'',deadline:day(3)+'T17:00',candidates:[{id:uuid(),date:day(7),time:'18:30'},{id:uuid(),date:day(8),time:'18:30'}],targets:(state.data.members||[]).map(m=>typeof m==='string'?m:m.name)};}
function startEditor(poll=null){state.view='edit';state.poll=poll;state.draft=poll?JSON.parse(JSON.stringify({title:poll.title,description:poll.description||'',deadline:String(poll.deadline).slice(0,16),candidates:poll.candidates,targets:poll.targets})):defaultDraft();state.month=state.draft.candidates[0]?.date.slice(0,7)||dateOnly().slice(0,7);renderEditor();}
function captureEditor(){if(state.view!=='edit'||!state.draft)return;const d=state.draft;for(const [key,id] of [['title','da-title'],['description','da-description'],['deadline','da-deadline']])if($(id))d[key]=$(id).value;document.querySelectorAll('#'+ID+' [data-da-date]').forEach(n=>{const c=d.candidates.find(c=>c.id===n.dataset.daDate);if(c)c.date=n.value;});document.querySelectorAll('#'+ID+' [data-da-time]').forEach(n=>{const c=d.candidates.find(c=>c.id===n.dataset.daTime);if(c)c.time=n.value;});}
function calendar(){const d=state.draft,month=state.month;return `<div class="da-calendar-head">${button('month',icon('chev-l'),'data-delta="-1" aria-label="이전 달"')}<strong>${Number(month.slice(0,4))}년 ${Number(month.slice(5))}월</strong>${button('month',icon('chev-r'),'data-delta="1" aria-label="다음 달"')}</div><div class="da-weekdays">${['일','월','화','수','목','금','토'].map(n=>`<span>${n}</span>`).join('')}</div><div class="da-calendar-grid">${M.monthDays(month).map(date=>{const selected=d.candidates.some(c=>c.date===date);return button('day',Number(date.slice(8)),`data-date="${date}" aria-label="${esc(shortDate(date))}${selected?' 후보 선택됨':''}" aria-pressed="${selected}"`,(date.slice(0,7)!==month?'outside ':'')+(selected?'is-candidate ':'')+(date===dateOnly()?'is-today':''));}).join('')}</div><p class="da-calendar-help">달력에서 2~5개 후보를 선택하세요.<br>선택한 날짜를 다시 누르면 제외됩니다.</p>`;}
function candidates(){return state.draft.candidates.map((c,i)=>`<div class="da-candidate"><span class="da-candidate-order">${i+1}</span><label><span class="sr-only">후보 ${i+1} 날짜</span><input type="date" data-da-date="${esc(c.id)}" value="${esc(c.date)}"></label><label><span class="sr-only">후보 ${i+1} 시간</span><input type="time" data-da-time="${esc(c.id)}" value="${esc(c.time)}"></label>${button('remove-candidate','×',`data-id="${esc(c.id)}" aria-label="후보 ${i+1} 삭제" ${state.draft.candidates.length<=2?'disabled':''}`,'da-icon-button')}</div>`).join('');}
function renderEditor(){
  const d=state.draft,p=state.poll,published=p&&p.status!=='DRAFT';
  shell(p?'날짜 취합 수정':'새 날짜 취합',`<div class="da-editor-grid"><aside class="da-panel da-calendar-panel"><div id="da-calendar">${calendar()}</div>${published?notice('변경한 후보의 응답만 재확인합니다. 다른 후보의 기존 응답은 유지됩니다.'):''}</aside><section><label class="da-field">제목<input id="da-title" maxlength="100" value="${esc(d.title)}" placeholder="예: 12월 부서 회식"></label><label class="da-field">안내 내용 <span class="da-optional">선택</span><textarea id="da-description" maxlength="2000" rows="2" placeholder="회식 취합 안내를 적어 주세요.">${esc(d.description)}</textarea></label><div class="da-section-head"><h3>후보 날짜 · 시간</h3><span class="da-muted" id="da-candidate-count">${d.candidates.length} / 5개</span></div><div id="da-candidates" class="da-candidates">${candidates()}</div><div class="da-two da-editor-bottom"><label class="da-field">응답 마감 <span class="da-optional">한국 시간</span><input id="da-deadline" type="datetime-local" value="${esc(d.deadline)}"></label><div class="da-field">응답 대상<strong id="da-target-count">${d.targets.length}명</strong>${published?'<small class="da-muted">게시된 취합의 대상은 바꿀 수 없습니다.</small>':button('targets','참여 대상 선택')}</div></div></section></div>${footer(button('editor-back','돌아가기')+button('save-draft',published?'수정 내용 확인':'초안 저장','','da-primary'))}`);
}
function updateCalendar(source){const focus=source?.dataset||{};const el=$('da-calendar');if(el)el.innerHTML=calendar();const list=$('da-candidates');if(list)list.innerHTML=candidates();if($('da-candidate-count'))$('da-candidate-count').textContent=state.draft.candidates.length+' / 5개';let target;if(focus.da==='month')target=Array.from(el.querySelectorAll('[data-delta]')).find(n=>n.dataset.delta===focus.delta);else if(focus.da==='day')target=Array.from(el.querySelectorAll('[data-date]')).find(n=>n.dataset.date===focus.date);else if(focus.da==='remove-candidate')target=list.querySelector('input');target?.focus({preventScroll:true});}
function chooseTargets(){captureEditor();const selected=new Set(state.draft.targets),members=(state.data.members||[]).map(m=>typeof m==='string'?m:m.name);const node=OnAirUI.open(CONFIRM,'응답 대상 선택',`<section class="da-app"><p class="da-muted">게시한 뒤에는 대상을 변경할 수 없습니다.</p><label class="da-field">이름 검색<input id="da-target-search" type="search" placeholder="이름으로 찾기"></label><div class="da-actions">${button('target-all','전체 선택')}${button('target-none','선택 해제')}<span id="da-selected-count" class="da-muted">${selected.size}명 선택</span></div><div class="da-targets">${members.map((n,i)=>`<label data-da-person="${esc(n)}"><input type="checkbox" value="${esc(n)}" ${selected.has(n)?'checked':''}><span>${esc(n)}</span></label>`).join('')}</div><div id="da-confirm-error" class="da-error" role="alert" tabindex="-1" hidden></div>${footer(button('target-cancel','돌아가기')+button('target-apply','선택 적용','','da-primary'))}</section>`,{width:620});
  const count=()=>{$('da-selected-count').textContent=selected.size+'명 선택';};
  node.onchange=e=>{if(e.target.matches('input[type=checkbox]')){e.target.checked?selected.add(e.target.value):selected.delete(e.target.value);count();}};
  node.oninput=e=>{if(e.target.id==='da-target-search')node.querySelectorAll('[data-da-person]').forEach(n=>n.hidden=!n.dataset.daPerson.includes(e.target.value.trim()));};
  node.onclick=e=>{const action=e.target.closest('[data-da]')?.dataset.da;if(action==='target-all'||action==='target-none'){members.forEach(n=>action==='target-all'?selected.add(n):selected.delete(n));node.querySelectorAll('input[type=checkbox]').forEach(n=>n.checked=selected.has(n.value));count();}else if(action==='target-cancel')OnAirUI.close(CONFIRM);else if(action==='target-apply'){if(!selected.size)return error(Error('한 명 이상 선택해 주세요.'),'da-confirm-error');state.draft.targets=[...selected];$('da-target-count').textContent=selected.size+'명';OnAirUI.close(CONFIRM);}};
}
function confirmDialog(title,html,submit,{label='확인',danger=false,requiredCheck='',fields=false,onDismiss=null}={}){
  const node=OnAirUI.open(CONFIRM,title,`<section class="da-app">${html}${requiredCheck?`<label class="da-check"><input id="da-confirm-check" type="checkbox"><span>${esc(requiredCheck)}</span></label>`:''}<div id="da-confirm-error" class="da-error" role="alert" tabindex="-1" hidden></div>${footer(button('dismiss','돌아가기')+button('accept',esc(label),'',danger?'da-danger-solid':'da-primary'))}</section>`,{width:620});
  node.oninput=null;node.onchange=null;let pending=false;node.onclick=async e=>{const btn=e.target.closest('[data-da]');if(!btn||pending)return;if(btn.dataset.da==='dismiss'){OnAirUI.close(CONFIRM);if(onDismiss)onDismiss();return;}if(btn.dataset.da!=='accept')return;$('da-confirm-error').hidden=true;$('da-confirm-error').textContent='';if(requiredCheck&&!$('da-confirm-check').checked)return error(Error('안내 내용을 확인한 뒤 체크해 주세요.'),'da-confirm-error');pending=true;node.querySelectorAll('button[data-da]').forEach(n=>n.disabled=true);try{await submit();OnAirUI.close(CONFIRM);}catch(err){error(err,'da-confirm-error');}finally{pending=false;node.querySelectorAll('button[data-da]').forEach(n=>n.disabled=false);}};
}
async function reloadData(optional=false){try{state.data=await D().api('/manage');}catch(error){if(!optional)throw error;notify('변경은 저장했습니다. 최신 집계는 목록을 다시 열어 확인해 주세요.');}return state.data;}
async function saveDraft(){
  captureEditor();const body=M.validateDraft(state.draft),p=state.poll;
  const save=async()=>{const result=await request(p?'/polls/'+encodeURIComponent(p.id):'/polls',p?{...body,expectedRevision:p.revision}:body,p?'PATCH':'POST');state.poll=result.poll;state.view='detail';state.data.polls=[...(state.data.polls||[]).filter(x=>x.id!==result.poll.id),result.poll];await reloadData(true);state.poll=state.data.polls.find(x=>x.id===result.poll.id)||result.poll;renderDetail();notify(p?'취합 내용을 저장했습니다.':'초안을 저장했습니다. 게시하면 직원에게 새 투표가 표시됩니다.');};
  if(p&&p.status!=='DRAFT')confirmDialog('변경된 후보 재확인',notice('날짜나 시간이 달라진 후보의 응답만 다시 받습니다. 제목·안내·마감만 바꾸면 기존 응답은 유지됩니다.'),save,{label:'수정 적용',requiredCheck:'변경 후보의 재확인 처리를 확인했습니다.'});else await save();
}
function renderDetail(){
  const p=state.poll,r=p.results||{},candidates=r.candidates||[],responders=r.responders||[],outstanding=M.outstanding(p);
  const total=p.targets?.length||0,done=M.completed(p),editable=['DRAFT','OPEN','CLOSED'].includes(p.status),active=p.status==='OPEN',draft=p.status==='DRAFT';
  const final=(p.candidates||[]).find(c=>c.id===p.selectedCandidateId);
  shell('회식 응답 현황',`<div class="da-toolbar"><div><div class="da-row">${status(p)}<h2>${esc(p.title)}</h2></div><p class="da-muted">응답 마감 ${esc(stamp(p.deadline))} · 대상 ${total}명</p></div>${button('list','목록')}</div>${p.description?`<p class="da-description">${esc(p.description)}</p>`:''}${final?`<div class="da-final"><strong>${esc(shortDate(final.date))} ${esc(final.time)} ${p.status==='CANCELLED'?'취소된 일정':'확정'}</strong><span>${esc(p.venue||'장소 추후 안내')}</span>${p.notice?`<p>${esc(p.notice)}</p>`:''}</div>`:p.notice?notice(p.notice):''}${p.dutyEffect&&['CONFIRMED','CANCELLED'].includes(p.status)?notice(p.status==='CANCELLED'?dutyCancel:dutyApplied):''}<div class="da-progress-summary"><strong>${done}<small> / ${total}명 응답 완료</small></strong><span class="da-muted">미응답·재확인 ${Math.max(0,total-done)}명</span><div class="da-progress-track" aria-label="응답 완료 ${done}명 / ${total}명"><span style="width:${total?Math.min(100,done/total*100):0}%"></span></div></div>
    <div class="da-result-grid">${(p.candidates||[]).map(c=>{const s=candidates.find(r=>r.id===c.id)||{possible:0,maybe:0,difficult:0,unanswered:total};return `<article class="da-result-card ${c.id===p.selectedCandidateId?'is-final':''}"><div class="da-row"><h3>${esc(shortDate(c.date))}</h3><span>${esc(c.time)}</span></div><div class="da-result-counts"><span class="possible"><strong>${Number(s.possible)||0}</strong>가능</span><span class="maybe"><strong>${Number(s.maybe)||0}</strong>미정</span><span class="difficult"><strong>${Number(s.difficult)||0}</strong>어려움</span><span><strong>${Number(s.unanswered)||0}</strong>미응답</span></div>${!draft&&editable?button('finalize','이 날짜로 확정',`data-id="${esc(c.id)}"`):c.id===p.selectedCandidateId?pill(p.status==='CANCELLED'?'기존 확정 날짜':'확정 날짜',p.status==='CANCELLED'?'':'good'):''}</article>`;}).join('')}</div>
    <div class="da-actions da-management-actions">${draft?button('publish','직원에게 게시','','da-primary'):''}${['DRAFT','OPEN'].includes(p.status)?button('edit','후보 · 내용 수정'):''}${active?button('remind','미응답 안내',outstanding.length?'':'disabled'):''}${active?button('close-poll','응답 마감'):''}${['OPEN','CLOSED'].includes(p.status)?button('extend',active?'마감 연장':'응답 다시 열기'):''}${p.status!=='CANCELLED'?button('cancel-poll','취합 취소','','da-danger'):''}</div>
    <details class="da-detail-section" ${outstanding.length?'open':''}><summary>미응답 · 재확인 ${outstanding.length}명</summary><div class="da-name-list">${outstanding.length?outstanding.map(n=>`<span>${esc(n.name)}${n.needsConfirmation?'<small>재확인</small>':''}</span>`).join(''):'<p class="da-muted">모든 대상 직원이 응답했습니다.</p>'}</div></details><details class="da-detail-section"><summary>직원별 응답과 제안 ${responders.length}명</summary><div class="da-person-responses">${responders.map(row=>`<article><div class="da-row"><strong>${esc(row.name)}</strong>${pill(row.complete?'응답 완료':'일부 후보 재확인',row.complete?'good':'')}</div><div class="da-person-choices">${(p.candidates||[]).map(c=>`<span class="${esc(['possible','maybe','difficult'].includes(row.answers?.[c.id])?row.answers[c.id]:'')}">${esc(shortDate(c.date))} · ${esc(({possible:'가능',maybe:'미정',difficult:'어려움'})[row.answers?.[c.id]]||'미응답')}</span>`).join('')}</div>${row.suggestion?`<p class="da-suggestion">${esc(row.suggestion)}</p>`:''}</article>`).join('')||'<p class="da-muted">아직 제출된 응답이 없습니다.</p>'}</div></details>`);
}
async function doAction(action,extra={}){
  const p=state.poll,result=await request('/polls/'+encodeURIComponent(p.id)+'/action',{action,expectedRevision:p.revision,...extra});
  state.poll=result.poll;state.data.polls=(state.data.polls||[]).map(x=>x.id===p.id?result.poll:x);
  if(action==='CONFIRM'){
    // Render the commit receipt immediately; neither follow-up read may hold the success dialog open.
    state.view='detail';renderDetail();refreshAfterConfirmation(result.poll);return;
  }
  await reloadData(true);state.poll=state.data.polls.find(x=>x.id===p.id)||result.poll;state.view='detail';renderDetail();
}
function refreshAfterConfirmation(poll){
  const generation=loadGeneration,current=()=>generation===loadGeneration&&state.view==='detail'&&state.poll===poll&&hasOpen();
  (async()=>{
    try{if(typeof fetchAllData!=='function'||await fetchAllData()===false)throw Error('calendar refresh pending');}
    catch(_){notify('회식 확정은 저장했습니다. 달력을 다시 불러오지 못했으니 잠시 후 새로고침해 주세요.');}
  })();
  D().api('/manage').then(data=>{
    // The committed poll is already visible. Refresh only its cache so later reads cannot steal focus or reopen another view.
    if(current())state.data=data;
  }).catch(()=>{if(current())notify('회식 확정은 저장했습니다. 최신 집계는 목록을 다시 열어 확인해 주세요.');});
}
async function actionDialog(action,candidateId){
  let p=state.poll;
  if(action==='finalize'){
    const id=p.id;await reloadData();p=state.data.polls.find(p=>p.id===id);
    if(!p||!['OPEN','CLOSED'].includes(p.status))throw Error('취합 상태가 변경되었습니다. 목록에서 다시 확인해 주세요.');
    if(!Number.isInteger(state.data.dutyDataVersion)||state.data.dutyDataVersion<0)throw Error('최신 근무 자료를 확인하지 못했습니다. 목록을 다시 열어 주세요.');
    if(!p.candidates.some(c=>c.id===candidateId))throw Error('후보 날짜가 변경되었습니다. 목록에서 다시 확인해 주세요.');
    state.poll=p;
  }
  if(action==='publish')confirmDialog('직원에게 새 투표 게시',`<h3>${esc(p.title)}</h3><p class="da-muted">대상 ${p.targets.length}명 · 응답 마감 ${esc(stamp(p.deadline))}</p>${notice('게시하면 대상 직원의 알림 오른쪽에 새 투표가 표시됩니다. 게시 후 참여 대상은 변경할 수 없습니다.')}`,()=>doAction('PUBLISH'),{label:'게시하기'});
  if(action==='close-poll')confirmDialog('응답 마감',notice('지금부터 추가 응답을 받지 않고 결과 집계를 유지합니다. 필요하면 마감을 연장해 다시 열 수 있습니다.'),()=>doAction('CLOSE'),{label:'응답 마감'});
  if(action==='remind')confirmDialog('미응답 직원에게 안내',`<p>미응답·재확인 대상 <strong>${M.outstanding(p).length}명</strong>에게 OnAir 알림을 보냅니다.</p><label class="da-field">안내 내용<textarea id="da-action-notice" rows="3" maxlength="2000">${esc(p.title)}의 참석 가능 날짜를 확인해 주세요.</textarea></label>`,()=>doAction('REMIND',{notice:$('da-action-notice').value.trim()}),{label:'안내 알림 보내기'});
  if(action==='extend')confirmDialog(p.status==='CLOSED'?'응답 다시 열기':'응답 마감 연장',notice('기존 응답은 유지합니다. 새 마감은 모든 후보 날짜·시간보다 앞서야 합니다.')+`<label class="da-field">새 응답 마감 · 한국 시간<input id="da-new-deadline" type="datetime-local" value="${esc(String(p.deadline).slice(0,16))}"></label>`,()=>{const deadline=$('da-new-deadline').value;if(!deadline||deadline<=nowKST()||deadline<=String(p.deadline).slice(0,16)||p.candidates.some(c=>deadline>=c.date+'T'+c.time))throw Error('기존 마감 이후이면서 모든 후보 일시보다 이른 새 마감을 선택해 주세요.');return doAction('EXTEND',{deadline});},{label:p.status==='CLOSED'?'응답 다시 열기':'마감 연장'});
  if(action==='finalize'){
    const c=p.candidates.find(c=>c.id===candidateId),counts=p.results?.candidates?.find(r=>r.id===candidateId)||{},baseVersion=state.data.dutyDataVersion;
    confirmDialog('회식 날짜 확정',`<div class="da-final"><strong>${esc(shortDate(c.date))} ${esc(c.time)}</strong><p>가능 ${Number(counts.possible)||0}명 · 미정 ${Number(counts.maybe)||0}명 · 어려움 ${Number(counts.difficult)||0}명 · 미응답 ${Number(counts.unanswered)||0}명</p></div>${notice(shortDate(c.date)+' 하루에만 기존 메모를 보존하면서 석식간담회 메모를 추가하고, 배정된 근무자를 제거해 당직 없음으로 표시합니다. 다른 날짜의 배정과 순번은 변경하지 않습니다.')}<label class="da-field">장소 <span class="da-optional">선택</span><input id="da-venue" maxlength="200" value="${esc(p.venue||'')}"></label><label class="da-field">확정 안내 <span class="da-optional">선택</span><textarea id="da-action-notice" maxlength="2000" rows="3">${esc(p.notice||'')}</textarea></label>`,()=>doAction('CONFIRM',{expectedRevision:p.revision,baseVersion,selectedCandidateId:c.id,venue:$('da-venue').value.trim(),notice:$('da-action-notice').value.trim()}),{label:'이 날짜로 확정',requiredCheck:'미정·참석 어려움·미응답 인원과 선택 날짜의 달력 변경을 확인했습니다.'});
  }
  if(action==='cancel-poll'&&p.status==='DRAFT'){confirmDialog('게시 전 초안 취소',notice('게시하지 않은 초안을 취소합니다. 직원에게 표시하거나 알리지 않습니다.'),()=>doAction('CANCEL',{notice:''}),{label:'초안 취소',danger:true});return;}
  if(action==='cancel-poll')confirmDialog('회식 취합 취소',notice('응답 접수를 종료하고 대상 직원에게 취소 안내를 표시합니다. 기존 응답 기록은 보관됩니다.')+(p.dutyEffect?notice(dutyCancel):'')+`<label class="da-field">취소 안내<textarea id="da-action-notice" maxlength="2000" rows="3" placeholder="직원에게 전달할 내용을 입력하세요."></textarea></label>`,()=>{const notice=$('da-action-notice').value.trim();if(!notice)throw Error('취소 안내를 입력해 주세요.');return doAction('CANCEL',{notice});},{label:'취합 취소',danger:true,requiredCheck:'대상 직원에게 취소 안내를 표시하는 것을 확인했습니다.'});
}
function reviewConnection(id){captureRole();const c=(state.data.connections||[]).find(c=>String(c.id)===id);if(!c)return;confirmDialog('CA 사용 연결 확인',`<p><strong>${esc(c.memberName||state.data.role?.memberName)}</strong> · ${esc(c.deviceLabel||'')}</p><div class="da-verification"><span>담당자 PC의 코드와 대조해 주세요.</span><strong>${esc(c.verificationCode||'')}</strong></div><label class="da-field">처리<select id="da-connection-decision"><option value="APPROVE">사용 연결 승인</option><option value="REJECT">연결 요청 반려</option></select></label><label class="da-field">안내 사유 <span class="da-optional">선택</span><input id="da-connection-reason" maxlength="500"></label>`,async()=>{const result=await request('/connections/'+encodeURIComponent(id)+'/decision',{decision:$('da-connection-decision').value,reason:$('da-connection-reason').value.trim()});state.data.connections=state.data.connections.map(c=>String(c.id)===id?result.connection:c);await reloadData(true);renderRole();},{label:'선택한 처리 적용',requiredCheck:'담당자와 PC 확인 코드를 확인했습니다.'});}
function saveRole(){captureRole();const draft=roleDraft(),memberName=$('da-ca-name').value,startDate=$('da-term-start').value,endDate=$('da-term-end').value,role=selectedRole();if(!memberName)throw Error('CA 담당 직원을 선택해 주세요.');if(!M.validDate(startDate)||!M.validDate(endDate)||endDate<startDate)throw Error('임기 시작일과 종료일을 확인해 주세요.');const body={roleId:draft.roleId,memberName,startDate,endDate,expectedRevision:draft.expectedRevision};confirmDialog(role?.memberName&&role.memberName!==memberName?'CA 담당자 교체':'CA 지정 내용 확인',`<div class="da-role-card"><h3>${esc(memberName)}</h3><p>${esc(startDate)} ~ ${esc(endDate)}</p></div>${notice(role?.memberName&&role.memberName!==memberName?'이전 담당자의 사용 연결은 해제됩니다. 새 담당자는 사용 연결 승인 후 기존 취합을 이어서 관리합니다.':'담당자가 사용할 PC에서 연결 요청을 보내면 관리자가 승인할 수 있습니다.')}`,async()=>{let result;try{result=await request('/role',body);}catch(e){if(e.code==='REVISION_CONFLICT'||/revision|다른.*변경|최신.*지정/.test(e.message||'')){draft.conflict=true;e.message+=' 입력 내용은 보존했습니다. 돌아가기 후 최신 지정을 불러와 확인해 주세요.';}throw e;}updateRole(result);await reloadData(true);renderRole();},{label:'지정 내용 저장',onDismiss:()=>renderRole()});}
async function onClick(event){
  const btn=event.target.closest('[data-da]');if(!btn||btn.disabled||!$(ID)?.contains(btn))return;const a=btn.dataset.da;
  await run(async()=>{
    if(a==='reload')return open();if(a==='role')return openRole();if(a==='close')return OnAirUI.close(ID);
    if(a==='filter'){state.filter=btn.dataset.filter;return renderList();}
    if(a==='new')return startEditor();
    if(a==='detail'){const p=state.data.polls.find(p=>String(p.id)===btn.dataset.id);if(!p)return;state.poll=p;if(p.status==='DRAFT')return startEditor(p);state.view='detail';return renderDetail();}
    if(a==='list'){await reloadData();state.view='list';return renderList();}
    if(a==='edit')return startEditor(state.poll);
    if(a==='editor-back')return confirmDialog('작성 화면 나가기',notice('아직 저장하지 않은 내용은 닫으면 사라집니다.'),async()=>{state.view='list';renderList();},{label:'목록으로 돌아가기'});
    if(a==='month'){captureEditor();const [y,m]=state.month.split('-').map(Number);state.month=iso(new Date(Date.UTC(y,m-1+Number(btn.dataset.delta),1))).slice(0,7);return updateCalendar(btn);}
    if(a==='day'||a==='remove-candidate'){captureEditor();const d=state.draft,at=d.candidates.findIndex(c=>a==='day'?c.date===btn.dataset.date:c.id===btn.dataset.id);if(at>=0){if(d.candidates.length<=2)throw Error('후보 날짜는 2개 이상 유지해 주세요.');d.candidates.splice(at,1);}else{if(d.candidates.length>=5)throw Error('후보 날짜는 최대 5개까지 선택할 수 있습니다.');d.candidates.push({id:uuid(),date:btn.dataset.date,time:'18:30'});}return updateCalendar(btn);}
    if(a==='targets')return chooseTargets();if(a==='save-draft')return saveDraft();
    if(a==='term'){$('da-term-end').value=M.termEnd($('da-term-start').value,Number(btn.dataset.years));return;}
    if(a==='viewer-role'){state.viewerRoleId=Number(btn.dataset.roleId);return renderRole();}
    if(a==='role-refresh'){const key=roleDraftKey();await reloadData();delete state.roleDrafts[key];delete state.roleDrafts[roleDraftKey()];return renderRole();}
    if(a==='role-year'){captureRole();state.roleYear=Number(btn.dataset.year);state.connectionTab='pending';state.connectionPage=0;return renderRole();}
    if(a==='connection-tab'){captureRole();state.connectionTab=btn.dataset.tab;state.connectionPage=0;return renderRole();}
    if(a==='connection-page'){captureRole();state.connectionPage=Number(btn.dataset.page);return renderRole();}
    if(a==='save-role')return saveRole();
    if(a==='remove-role'){const role=selectedRole();return confirmDialog('CA 권한 해제',notice('CA 사용 연결을 해제합니다. 취합과 응답은 보관되며 관리자가 계속 운영할 수 있습니다.'),async()=>{const result=await request('/role',{roleId:M.roleId(role),memberName:'',startDate:role.startDate,endDate:role.endDate,expectedRevision:role.revision});updateRole(result);await reloadData(true);renderRole();},{label:'권한 해제',danger:true});}
    if(a==='connection-review')return reviewConnection(btn.dataset.id);
    if(a==='connect'){if(viewerConnection()?.status==='PENDING')return openRole();const local=await D().credentials();await request('/connections',{roleId:M.roleId(viewerRole()),memberName:D().getName(),deviceLabel:local.deviceLabel||'OnAir 직원 PC'});return openRole();}
    return actionDialog(a,btn.dataset.id);
  });
}
function onChange(event){if(state.view==='role'&&event.target.id==='da-role-appointment'){captureRole();state.roleSelections[state.roleYear]=Number(event.target.value);state.connectionPage=0;state.connectionTab='pending';renderRole();return;}if(state.view!=='edit')return;if(event.target.matches('[data-da-date],[data-da-time]')){captureEditor();if(event.target.matches('[data-da-date]')){const value=event.target.value;if(M.validDate(value))state.month=value.slice(0,7);$('da-calendar').innerHTML=calendar();}}}
function onInput(event){if(state.view==='edit'&&['da-title','da-description','da-deadline'].includes(event.target.id)){const field={ 'da-title':'title','da-description':'description','da-deadline':'deadline'}[event.target.id];state.draft[field]=event.target.value;}}
window.OnAirDinnerAdmin={open,openRole};
})();
