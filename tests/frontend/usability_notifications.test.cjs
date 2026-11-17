'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'frontend/dist/index.html'), 'utf8');
const original = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(x => x[1]).find(s => s.includes('function addNotif('));
const extensionPath = path.join(root, 'frontend/dist/usability_notifications.js');
const decode = s => s.replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&amp;/g,'&');
class Node {
  constructor(tag='div') { this.tagName=tag.toUpperCase(); this.children=[]; this.style={}; this.dataset={}; this.attrs={}; this.events={}; this.className=''; this.value=''; this._text=''; this._html=''; this.isConnected=true; this.tabIndex=0;
    this.classList={add:(...xs)=>{this.className=[...new Set(this.className.split(/\s+/).filter(Boolean).concat(xs))].join(' ');},remove:(...xs)=>{this.className=this.className.split(/\s+/).filter(x=>!xs.includes(x)).join(' ');},contains:x=>this.className.split(/\s+/).includes(x)};
  }
  appendChild(n) { n.parentNode=this; this.children.push(n); return n; }
  remove() { if(this.parentNode) this.parentNode.children=this.parentNode.children.filter(n=>n!==this); this.isConnected=false; }
  setAttribute(k,v) { this.attrs[k]=String(v); if(k==='class')this.className=String(v); if(k==='id')this.id=String(v); if(k.startsWith('data-'))this.dataset[k.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(k,f) { (this.events[k] ||= []).push(f); }
  dispatch(k,e={}) { e.target ||= this; e.preventDefault ||= ()=>{}; e.stopPropagation ||= ()=>{e.stopped=true;}; for(const f of this.events[k]||[])f(e); if(!e.stopped && this.parentNode)this.parentNode.dispatch(k,e); }
  click() { this.dispatch('click'); if(this.onclick)this.onclick({target:this}); }
  focus() { this.focused=true; }
  matches(s) { if(s.startsWith('#'))return this.id===s.slice(1); if(s.startsWith('.'))return this.classList.contains(s.slice(1)); const a=/^\[([^=\]]+)(?:="?([^"\]]+)"?)?\]$/.exec(s); if(a)return a[2]===undefined ? this.getAttribute(a[1])!==null : this.getAttribute(a[1])===a[2]; return this.tagName===s.toUpperCase(); }
  closest(s) { return this.matches(s)?this:this.parentNode?.closest(s); }
  querySelectorAll(s) { const parts=s.split(',').map(x=>x.trim()); return this.children.flatMap(n=>(parts.some(x=>n.matches(x))?[n]:[]).concat(n.querySelectorAll(s))); }
  querySelector(s) { return this.querySelectorAll(s)[0]||null; }
  contains(n) { return n===this || this.children.some(c=>c.contains(n)); }
  getClientRects() { return [{}]; }
  set textContent(v) { this.children=[]; this._text=String(v); }
  get textContent() { return this._text+this.children.map(n=>n.textContent).join(''); }
  set innerHTML(v) { this._html=String(v); this._text=''; this.children=[]; const stack=[this]; for(const token of this._html.match(/<[^>]+>|[^<]+/g)||[]) { if(token.startsWith('</')){if(stack.length>1)stack.pop();} else if(token.startsWith('<')) { const m=/^<([\w-]+)/.exec(token); if(!m)continue; const n=new Node(m[1]); for(const a of token.matchAll(/([\w-]+)=(?:"([^"]*)"|'([^']*)')/g))n.setAttribute(a[1],decode(a[2]??a[3])); stack.at(-1).appendChild(n); if(!/\/$/.test(token.slice(0,-1))&&!/^(input|br|hr)$/.test(m[1]))stack.push(n); } else { const n=new Node('#text'); n.textContent=decode(token); stack.at(-1).appendChild(n); } } }
  get innerHTML() { return this._html; }
}
function app(saved={}, beforeExtension) {
  const values=new Map(Object.entries(saved)); const storage={getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,String(v)),removeItem:k=>values.delete(k)};
  const body=new Node('body'), head=new Node('head'), nodes=new Map();
  const element=id=>body.querySelector('#'+id)||nodes.get(id)||(()=>{const n=new Node();n.id=id;nodes.set(id,n);body.appendChild(n);return n;})();
  const doc={body,head,activeElement:null,readyState:'loading',createElement:t=>new Node(t),getElementById:element,querySelector:s=>body.querySelector(s),querySelectorAll:s=>body.querySelectorAll(s),addEventListener:()=>{}};
  let now=0, seq=0; const timers=new Map();
  class ClockDate extends Date { constructor(...args){super(...(args.length?args:[now]));} static now(){return now;} }
  const ctx=vm.createContext({Date:ClockDate,console,sessionStorage:storage,localStorage:storage,document:doc,
    setTimeout:(f,delay)=>{const id=++seq;timers.set(id,{at:now+Number(delay),f});return id;},clearTimeout:id=>timers.delete(id),setInterval:()=>1,clearInterval:()=>{},addEventListener:()=>{},confirm:()=>true});
  ctx.window=ctx;
  vm.runInContext(original,ctx); const run=code=>vm.runInContext(code,ctx);
  run(`memberList=[{name:'김민수'},{name:'이서연'}]; myName='김민수'; SERVER_CONNECTED=true; SYSTEM_MODE='VIEWER'; todayStr='2026-10-13';
    renderCalendar=()=>{};renderMemberModalList=()=>{};renderAbsenceTypeUI=()=>{};updateYearMonthUI=()=>{};renderMyNameUI=()=>{};selectMemberDuty=()=>{};updateMemberSelectionUI=()=>{};
    goToDate=k=>{globalThis.jumpedTo=k;};openPersonAbsenceModal=(n,d)=>{globalThis.absenceOpened={n,d};};`);
  // Shared modal code runs unmodified; DOM, storage, timer and HTTP boundaries are the only doubles.
  vm.runInContext(fs.readFileSync(path.join(root,'frontend/dist/usability_common.js'),'utf8'),ctx);
  if(beforeExtension)beforeExtension(run,ctx);
  if(fs.existsSync(extensionPath))vm.runInContext(fs.readFileSync(extensionPath,'utf8'),ctx);
  const advance=ms=>{const end=now+ms; while(true){const next=[...timers].filter(([,x])=>x.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;now=next[1].at;timers.delete(next[0]);next[1].f();}now=end;};
  const payload=p=>{ctx.fetch=async()=>({ok:true,json:async()=>p});};
  return {run,ctx,element,body,values,advance,payload};
}
const plain=x=>JSON.parse(JSON.stringify(x));
test('recovery notifications navigate to the dedicated admin review without changing normal approval counts',()=>{
  const a=app();a.run("SYSTEM_MODE='ADMIN';addNotif('신청 권한 복구 요청 1건',{kind:'recovery'})");
  a.ctx.OnAirRequestAccess={openAdminRecovery:()=>{a.ctx.recoveryOpened=true;}};
  a.run('OnAirNotifications.goTo(0)');assert.equal(a.ctx.recoveryOpened,true);
  assert.equal(a.run('notifList[0].read'),true);
  a.run("SYSTEM_MODE='VIEWER';addNotif('신청 권한 복구 요청',{kind:'recovery'})");
  a.ctx.recoveryOpened=false;a.run('OnAirNotifications.goTo(0)');assert.equal(a.ctx.recoveryOpened,false);
});
test('closing notification history preserves unread records and explicit read-all still works',()=>{
  const a=app({sd_notifs:JSON.stringify([{t:'2026-10-13 12:00',text:'알림',read:false}])});
  a.run('openNotifModal();closeNotifModal()'); assert.equal(a.run('notifList[0].read'),false);
  a.run('markAllNotifsRead()'); assert.equal(a.run('notifList[0].read'),true);
});
test('each notification opens details and marks only that record read with separate timestamp',()=>{
  const a=app();a.run("addNotif('첫 알림');addNotif('둘째 알림');renderNotifList()");
  const row=a.element('notif-list').querySelector('[data-notif-index]'); assert.ok(row);row.click();
  assert.deepEqual(plain(a.run('notifList.map(x=>x.read)')),[true,false]);
  assert.ok(a.element('notif-list').querySelector('time'));assert.ok(a.element('oa-notif-detail').classList.contains('active'));
});
test('toast messages queue rather than overwriting and close advances the queue',()=>{
  const a=app();a.run("showToast('첫 안내');showToast('둘째 안내')");
  assert.match(a.element('toast').textContent,/첫 안내/);assert.doesNotMatch(a.element('toast').textContent,/둘째 안내/);
  a.element('toast').querySelector('[data-toast-close]').click();assert.match(a.element('toast').textContent,/둘째 안내/);
});
test('long toast gets a longer reading duration, hover pauses remaining time, and details keep full text',()=>{
  const a=app(), text='읽어야 할 긴 안내 문장 '.repeat(40);a.ctx.message=text;a.run('showToast(message)');
  a.advance(4000);assert.equal(a.element('toast').style.display,'block');
  a.element('toast').dispatch('mouseenter');a.advance(60000);assert.equal(a.element('toast').style.display,'block');
  a.element('toast').querySelector('[data-toast-details]').click();assert.ok(a.element('oa-toast-detail').textContent.includes(text));
  a.element('toast').dispatch('mouseleave');a.advance(60000);assert.equal(a.element('toast').style.display,'block');
  a.run("OnAirUI.close('oa-toast-detail')");a.advance(60000);assert.equal(a.element('toast').style.display,'none');
});
test('owned request metadata persists opaquely and navigates to its actual receipt detail',()=>{
  const a=app();a.run("trackMyRequest('opaque/$?=secret');addNotif('승인 결과',{kind:'request',receiptToken:'opaque/$?=secret',requestId:7,requesterName:'김민수',targetDate:'2026-10-20'})");
  assert.equal(JSON.parse(a.values.get('sd_notifs'))[0].meta.receiptToken,'opaque/$?=secret');
  a.ctx.OnAirRequests={openOwnedRequest:token=>{a.ctx.openedToken=token;}};
  a.run('OnAirNotifications.goTo(0)');assert.equal(a.ctx.openedToken,'opaque/$?=secret');
});
test('unowned receipt token is never stored or used and safe legacy records reach a known person/date',()=>{
  const a=app();a.run("addNotif('알림',{kind:'request',receiptToken:'someone-else',requesterName:'김민수'});addNotif(\"김민수 당직 안내 [2026-10-20]\");addNotif(\"✅ '김민수' 님의 [2026-10-21] 대체 근무 신청이 승인되었습니다.\")");
  assert.equal(a.run('notifList[2].meta.receiptToken'),undefined);
  a.ctx.OnAirRequests={openMyRequests:(name,id)=>{a.ctx.openedRequest={name,id};}};
  a.run('OnAirNotifications.goTo(0)');assert.equal(a.ctx.openedRequest.name,'김민수');
  a.run('OnAirNotifications.goTo(1)');assert.equal(a.ctx.jumpedTo,'2026-10-20');
  a.run("addNotif('파일 이름 2026-10-99 혹은 2026-10-20');OnAirNotifications.goTo(0)");assert.equal(a.ctx.jumpedTo,'2026-10-20');
});
test('first successful personal fetch establishes a baseline without false changes',async()=>{
  const a=app();a.payload({schedules:{'2026-10-20':[{name:'김민수',role:'당직'}]},absences:[]});await a.run('fetchAllData()');
  assert.equal(a.run('notifList.length'),0);assert.ok(a.values.get('sd_personal_schedule_snapshot_v1'));
});
test('actual personal duty and absence changes produce before/after details with relevant navigation',async()=>{
  const a=app();a.payload({schedules:{'2026-10-20':[{name:'김민수',role:'당직'},{name:'이서연'}]},absences:[]});await a.run('fetchAllData()');
  a.payload({schedules:{'2026-10-22':[{name:'김민수',role:'대체당직',isSubstitute:true}]},absences:[{id:11,name:'김민수',start:'2026-10-25',end:'2026-10-26',type:'교육',education:{title:'소방 교육'}}]});await a.run('fetchAllData()');
  const list=plain(a.run('notifList'));assert.equal(list.length,3);assert.ok(list.some(n=>n.meta.kind==='absence'));assert.ok(list.every(n=>n.meta.before!==undefined&&n.meta.after!==undefined));
  const absence=list.findIndex(n=>n.meta.kind==='absence');a.run(`OnAirNotifications.goTo(${absence})`);assert.deepEqual(plain(a.ctx.absenceOpened),{n:'김민수',d:'2026-10-25'});
});
test('metadata noise, other people and record order do not create personal change notifications',async()=>{
  const a=app();const base={schedules:{'2026-10-20':[{name:'김민수',role:'당직',gender:'M'},{name:'이서연'}]},absences:[{id:1,name:'김민수',start:'2026-10-25',end:'2026-10-26',type:'휴가'}]};a.payload(base);await a.run('fetchAllData()');
  a.payload({schedules:{'2026-10-20':[{name:'다른사람'},{name:'김민수',role:'당직',gender:'F',updated_at:'changed'}]},absences:[{id:999,name:'다른사람',start:'2026-10-14',end:'2026-10-14',type:'교육'},{...base.absences[0],id:777,created_at:'changed',reason:'비공개'}]});await a.run('fetchAllData()');assert.equal(a.run('notifList.length'),0);
});
test('persistent baseline detects changes made while the app was closed and name changes reset comparison',async()=>{
  const first=app();first.payload({schedules:{'2026-10-20':[{name:'김민수'}]},absences:[]});await first.run('fetchAllData()');
  const next=app(Object.fromEntries(first.values));next.payload({schedules:{'2026-10-21':[{name:'김민수'}]},absences:[]});await next.run('fetchAllData()');assert.equal(next.run('notifList.length'),2);
  next.run("setMyName('이서연')");next.payload({schedules:{'2026-10-21':[{name:'김민수'}]},absences:[]});await next.run('fetchAllData()');assert.equal(next.run('notifList.length'),2);
});
test('failed fetches and suspended refresh do not consume or fabricate personal snapshots',async()=>{
  const a=app();a.payload({schedules:{'2026-10-20':[{name:'김민수'}]},absences:[]});await a.run('fetchAllData()');const before=a.values.get('sd_personal_schedule_snapshot_v1');
  a.ctx.fetch=async()=>({ok:false,status:500,json:async()=>({})});a.run('setSysAlert=()=>{}');await a.run('fetchAllData()');assert.equal(a.values.get('sd_personal_schedule_snapshot_v1'),before);
  a.run('suspendAutoRefresh=true');a.payload({schedules:{},absences:[]});await a.run('fetchAllData()');assert.equal(a.values.get('sd_personal_schedule_snapshot_v1'),before);assert.equal(a.run('notifList.length'),0);
});
test('token-only request outcome polling handles cancellation and keeps private reasons out of general alerts',async()=>{
  const a=app();a.run("trackMyRequest('mine');trackMyRequest('cancelled')");const urls=[];
  a.ctx.fetch=async url=>{urls.push(url);return {ok:true,json:async()=>({request:{id:url.endsWith('cancelled')?4:3,requester_name:'김민수',request_type:'ABSENCE',target_date:'2026-10-20',absence_end_date:'2026-10-22',absence_type:'휴가',status:url.endsWith('cancelled')?'CANCELLED':'REJECTED',reason:'개인정보 신청 사유',reject_reason:'개인정보 반려 사유'}})};};
  await a.run('checkRequestOutcomes()');assert.ok(urls.every(u=>u.startsWith('/api/duty/request-status/')));
  assert.equal(a.run("myRequestStatuses.cancelled"),'CANCELLED');assert.ok(plain(a.run('notifList')).some(n=>/철회/.test(n.text)));
  // v3.18.0 신청 사유는 알림에 남기지 않고, 관리자의 반려 사유는 신청자의 결과 알림 한 건에 함께 보인다.
  assert.doesNotMatch(a.run('JSON.stringify(notifList)'),/개인정보 신청 사유/);
});
test('detail navigation continues to target the opened record when another notification arrives',()=>{
  const a=app();a.run("addNotif('당직 안내 [2026-10-20]',{kind:'schedule',targetDate:'2026-10-20'});OnAirNotifications.openDetail(0);addNotif('당직 안내 [2026-10-21]',{kind:'schedule',targetDate:'2026-10-21'})");
  a.element('oa-notif-detail').querySelector('[data-notif-action]').click();assert.equal(a.ctx.jumpedTo,'2026-10-20');assert.deepEqual(plain(a.run('notifList.map(x=>x.read)')),[false,true]);
});
test('a queued toast remains paused when close is clicked while hovering',()=>{
  const a=app();a.run("showToast('첫 안내');showToast('둘째 안내')");a.element('toast').dispatch('mouseenter');
  a.element('toast').querySelector('[data-toast-close]').click();a.advance(60000);assert.match(a.element('toast').textContent,/둘째 안내/);
  a.element('toast').dispatch('mouseleave');a.advance(60000);assert.equal(a.element('toast').style.display,'none');
});
test('failed-save errors become immediately visible while a long normal queue is paused, without losing its messages',()=>{
  const a=app();a.run("showToast('읽는 중인 안내');showToast('다음 안내')");a.element('toast').dispatch('mouseenter');
  a.run("showToast('저장 실패: 다시 확인하세요',true)");assert.match(a.element('oa-toast-errors').textContent,/저장 실패/);assert.match(a.element('toast').textContent,/읽는 중인 안내/);
  a.element('oa-toast-errors').querySelector('[data-toast-error-close]').click();a.element('toast').querySelector('[data-toast-close]').click();assert.match(a.element('toast').textContent,/다음 안내/);
});
test('recognized old receipt messages navigate safely by their existing requester name',()=>{
  const a=app();a.ctx.OnAirRequests={openMyRequests:name=>{a.ctx.openedName=name;}};
  a.run("addNotif(\"📤 [2026-10-20] '김민수' ↔ [2026-10-21] '이서연' 맞교환 신청을 접수했습니다.\");OnAirNotifications.goTo(0)");assert.equal(a.ctx.openedName,'김민수');
  a.ctx.openedName='';a.run("addNotif(\"📤 '김민수' 님의 [2026-10-20 ~ 2026-10-22] 휴가 반영 요청을 접수했습니다.\");OnAirNotifications.goTo(0)");assert.equal(a.ctx.openedName,'김민수');
});
test('changing public education details emits one before/after absence notification and never includes private reasons',async()=>{
  const a=app();a.payload({schedules:{},absences:[{id:1,name:'김민수',start:'2026-10-20',end:'2026-10-20',type:'교육',education:{title:'기초교육',location:'본관'},reason:'개인 사유'}]});await a.run('fetchAllData()');
  a.payload({schedules:{},absences:[{id:2,name:'김민수',start:'2026-10-20',end:'2026-10-20',type:'교육',education:{location:'별관',title:'기초교육'},reason:'다른 개인 사유'}]});await a.run('fetchAllData()');
  assert.equal(a.run('notifList.length'),1);assert.match(a.run('notifList[0].meta.before'),/본관/);assert.match(a.run('notifList[0].meta.after'),/별관/);assert.doesNotMatch(a.values.get('sd_notifs')+a.values.get('sd_personal_schedule_snapshot_v1'),/개인 사유/);
});
test('error cards keep independent hover timing and never replace other error messages',()=>{
  const a=app();a.run("showToast('실패 첫 안내',true);showToast('실패 둘째 안내',true)");assert.match(a.element('oa-toast-errors').textContent,/실패 첫 안내/);assert.match(a.element('oa-toast-errors').textContent,/실패 둘째 안내/);
  const first=a.element('oa-toast-errors').children[0];a.advance(2000);first.dispatch('mouseenter');a.advance(60000);assert.match(a.element('oa-toast-errors').textContent,/실패 첫 안내/);assert.doesNotMatch(a.element('oa-toast-errors').textContent,/실패 둘째 안내/);
  first.dispatch('mouseleave');a.advance(60000);assert.equal(a.element('oa-toast-errors').textContent,'');
});
test('notification baseline wrapper preserves personal name options for the preceding sidecar',()=>{
  const a=app({},(_run,ctx)=>vm.runInContext(fs.readFileSync(path.join(root,'frontend/dist/usability_personal.js'),'utf8'),ctx));
  a.run("setMyName('이서연',{remember:true})");assert.equal(a.values.get('sd_my_name'),'이서연');assert.equal(a.values.get('sd_my_name_remember'),'1');
});
test('damaged local personal snapshots become a fresh baseline rather than disrupting a successful fetch',async()=>{
  const a=app({sd_personal_schedule_snapshot_v1:JSON.stringify({version:1,names:{'김민수':{duties:{},absences:[null]}}})});
  a.payload({schedules:{'2026-10-20':[{name:'김민수'}]},absences:[]});await a.run('fetchAllData()');assert.equal(a.run('dataLoaded'),true);assert.equal(a.run('notifList.length'),0);
});

// ── v3.18.0 결재 한 건 = 결과 알림 한 건 ─────────────────────────────
// [위험] 감사에서 맞교환 승인 한 번에 알림이 4개(결과·'당직표 갱신 (vm)'·날짜별 일정 변경 2건) 쌓이고,
//   결과 안내가 '당직표 갱신 (vm)' 안내에 가려졌다. 상대방은 무엇이 왜 바뀌었는지 알 수 없었다.
function routed(a,{data,requests=[],status=null}){
  a.ctx.fetch=async url=>{
    if(String(url).includes('/api/duty/my-requests'))return {ok:true,json:async()=>({requests})};
    if(String(url).includes('/api/duty/request-status/'))return {ok:true,json:async()=>({request:status})};
    return {ok:true,json:async()=>data};
  };
}
const swapApproved={id:9,status:'APPROVED',request_type:'SWAP',requester_name:'이서연',target_date:'2026-10-26',swap_date:'2026-10-20',swap_worker:'김민수',processed_at:'',reject_reason:''};
test('swap partner gets one result notification naming the requester and both dates',async()=>{
  const a=app();a.payload({schedules:{'2026-10-20':[{name:'김민수'}],'2026-10-26':[{name:'이서연'}]},absences:[]});await a.run('fetchAllData()');
  routed(a,{data:{schedules:{'2026-10-20':[{name:'이서연'}],'2026-10-26':[{name:'김민수'}]},absences:[]},requests:[swapApproved]});
  await a.run('fetchAllData()');a.run(`notifyRemoteRefresh({lastWrite:{host:'vm',source:'request:9:APPROVED'}})`);await a.run('checkRequestOutcomes()');
  const list=plain(a.run('notifList'));
  assert.equal(list.length,1,JSON.stringify(list.map(n=>n.text)));
  assert.match(list[0].text,/이서연 님과 맞교환 승인 \(10\/20 ↔ 10\/26\)/);
  assert.match(list[0].text,/내 당직이 10\/20\(화\)에서 10\/26\(월\)로 바뀌었습니다/);
  assert.equal(list[0].meta.targetDate,'2026-10-26');
  assert.match(a.element('toast').textContent,/이서연 님과 맞교환 승인/);
  assert.doesNotMatch(JSON.stringify(list)+a.element('toast').textContent,/\(vm\)|vm\)/);
});
test('requester PC with its receipt gets the same request announced exactly once',async()=>{
  const a=app();a.run(`myName='이서연';memberList.push({name:'김민수'});trackMyRequest('mine-receipt-token-000000')`);
  a.payload({schedules:{'2026-10-20':[{name:'김민수'}],'2026-10-26':[{name:'이서연'}]},absences:[]});await a.run('fetchAllData()');
  routed(a,{data:{schedules:{'2026-10-20':[{name:'이서연'}],'2026-10-26':[{name:'김민수'}]},absences:[]},requests:[{...swapApproved}],status:{...swapApproved,reason:'개인 신청 사유'}});
  await a.run('fetchAllData()');a.run(`notifyRemoteRefresh({lastWrite:{host:'vm',source:'request:9:APPROVED'}})`);await a.run('checkRequestOutcomes()');await a.run('checkRequestOutcomes()');
  const list=plain(a.run('notifList'));
  assert.equal(list.length,1,JSON.stringify(list.map(n=>n.text)));
  assert.match(list[0].text,/김민수 님과 맞교환 승인 \(10\/26 ↔ 10\/20\)/);
  assert.equal(a.run(`myRequestStatuses['mine-receipt-token-000000']`),'APPROVED');
  assert.doesNotMatch(JSON.stringify(list),/개인 신청 사유|토큰/);
});
test('a rejected request notifies once with the reject reason and no schedule noise',async()=>{
  const a=app();a.run(`trackMyRequest('rejected-receipt-token-0000')`);
  routed(a,{data:{schedules:{},absences:[]},status:{id:12,status:'REJECTED',request_type:'SUBSTITUTE',requester_name:'김민수',target_date:'2026-10-22',substitute_name:'박지훈',reject_reason:'그날 인원 부족'}});
  await a.run('checkRequestOutcomes()');await a.run('checkRequestOutcomes()');
  const list=plain(a.run('notifList'));assert.equal(list.length,1);
  assert.match(list[0].text,/대체 근무 반려 \(10\/22 → 박지훈 님\)\n반려 사유: 그날 인원 부족/);
});
test('ordinary remote refresh never shows the PC name and yields to a personal change notice',async()=>{
  const a=app();a.run(`notifyRemoteRefresh({lastWrite:{host:'vm',source:''}})`);
  assert.equal(a.run('notifList.length'),1);assert.doesNotMatch(a.run('notifList[0].text')+a.element('toast').textContent,/vm/);
  a.payload({schedules:{'2026-10-20':[{name:'김민수'}]},absences:[]});await a.run('fetchAllData()');
  a.payload({schedules:{'2026-10-21':[{name:'김민수'}]},absences:[]});await a.run('fetchAllData()');
  const before=a.run('notifList.length');a.run(`notifyRemoteRefresh({lastWrite:{host:'vm',source:''}})`);assert.equal(a.run('notifList.length'),before);
});
test('an old approval does not swallow a later unrelated change of the same date',async()=>{
  const a=app();a.payload({schedules:{'2026-10-20':[{name:'김민수'}]},absences:[]});await a.run('fetchAllData()');
  a.run(`localStorage.setItem('sd_announced_requests_v1',JSON.stringify({9:-3600000}))`);
  routed(a,{data:{schedules:{'2026-10-20':[{name:'이서연'}]},absences:[]},requests:[swapApproved]});await a.run('fetchAllData()');
  const list=plain(a.run('notifList'));assert.equal(list.length,1);assert.match(list[0].text,/\[2026-10-20\] 당직 일정이 변경되었습니다/);
});
test('a recent approval cannot swallow a later removal of the newly substituted duty',async()=>{
  const a=app();a.payload({schedules:{},absences:[]});await a.run('fetchAllData()');
  const approval={id:71,status:'APPROVED',request_type:'SUBSTITUTE',requester_name:'이서연',
    target_date:'2026-10-20',substitute_name:'김민수',is_zero_score:true,processed_at:''};
  routed(a,{data:{schedules:{'2026-10-20':[{name:'김민수',role:'대체당직',isSubstitute:true,zeroScore:true}]},absences:[]},requests:[approval]});
  await a.run('fetchAllData()');assert.equal(a.run('notifList.length'),1);
  a.advance(1000);routed(a,{data:{schedules:{'2026-10-20':[{name:'이서연'}]},absences:[]},requests:[approval]});
  await a.run('fetchAllData()');
  assert.equal(a.run('notifList.length'),2);
  assert.equal(a.run('notifList[0].meta.targetDate'),'2026-10-20');
  assert.doesNotMatch(a.run('notifList[0].text'),/승인/);
});
test('one approval may fold its matching change once but not a later same-shaped reassignment',async()=>{
  const a=app();a.payload({schedules:{},absences:[]});await a.run('fetchAllData()');
  const approval={id:72,status:'APPROVED',request_type:'SUBSTITUTE',requester_name:'이서연',
    target_date:'2026-10-20',substitute_name:'김민수',is_zero_score:true,processed_at:''};
  const assigned={schedules:{'2026-10-20':[{name:'김민수',role:'대체당직',isSubstitute:true,zeroScore:true}]},absences:[]};
  routed(a,{data:assigned,requests:[approval]});await a.run('fetchAllData()');
  routed(a,{data:{schedules:{},absences:[]},requests:[approval]});await a.run('fetchAllData()');
  routed(a,{data:assigned,requests:[approval]});await a.run('fetchAllData()');
  assert.equal(a.run('notifList.length'),3);
  assert.match(a.run('notifList[0].text'),/일정이 변경/);
});
test('an unobserved approval does not explain a normal assignment with different score flags',async()=>{
  const a=app();a.payload({schedules:{},absences:[]});await a.run('fetchAllData()');
  const approval={id:73,status:'APPROVED',request_type:'SUBSTITUTE',requester_name:'이서연',
    target_date:'2026-10-20',substitute_name:'김민수',is_zero_score:true,processed_at:''};
  routed(a,{data:{schedules:{'2026-10-20':[{name:'김민수',role:'평일(여)',isSubstitute:false,zeroScore:false}]},absences:[]},requests:[approval]});
  await a.run('fetchAllData()');
  assert.match(a.run('notifList[0].text'),/일정이 변경/);
  assert.equal(a.run('notifList[0].meta.requestId'),undefined);
});
for(const changeName of [false,true])test(`post-approval baseline preserves later remove/readd notifications (name selection=${changeName})`,async()=>{
  const a=app();const assigned={schedules:{'2026-10-20':[{name:'김민수',role:'대체당직',isSubstitute:true,zeroScore:true}]},absences:[]};
  const approval={id:74,status:'APPROVED',request_type:'SUBSTITUTE',requester_name:'이서연',
    target_date:'2026-10-20',substitute_name:'김민수',is_zero_score:true,processed_at:''};
  a.run(`localStorage.setItem('sd_announced_requests_v1',JSON.stringify({74:0}))`);
  a.payload(assigned);await a.run('fetchAllData()');
  if(changeName)a.run("myName='';setMyName('김민수')");
  routed(a,{data:{schedules:{},absences:[]},requests:[approval]});await a.run('fetchAllData()');
  routed(a,{data:assigned,requests:[approval]});await a.run('fetchAllData()');
  assert.equal(a.run('notifList.length'),2);
  assert.equal(a.run('notifList[0].meta.requestId'),undefined);
});
test('closing the base notification box does not mark records read',()=>{
  const body=original.match(/function closeNotifModal\(\) \{[\s\S]*?\n    \}/)[0];assert.doesNotMatch(body,/read\s*=\s*true/);
  assert.doesNotMatch(html,/창을 닫으면 모두 읽음/);
});
test('toast moves into the free gutter beside an open form instead of covering its fields',()=>{
  const a=app();a.ctx.innerWidth=1366;a.ctx.innerHeight=768;
  const modal=a.element('absence-request-modal');modal.className='modal active';const panel=new (a.element('x').constructor)('div');panel.className='modal-body';panel.getBoundingClientRect=()=>({left:373,right:993,top:40,bottom:728,width:620,height:688});modal.appendChild(panel);
  a.run("showToast('장소 칸을 가리면 안 되는 안내')");const style=a.element('toast').style;
  assert.equal(style.right,'12px');assert.equal(style.transform,'none');assert.ok(parseInt(style.maxWidth,10)<=1366-993-24,style.maxWidth);
  a.run("showToast('오류도 옆으로',true)");assert.ok(parseInt(a.element('oa-toast-errors').style.maxWidth,10)<=349);
  modal.className='modal';a.element('toast').querySelector('[data-toast-close]').click();a.run("showToast('창이 없으면 가운데')");assert.equal(a.element('toast').style.transform,'');
});

// ── 3.18.0 QA ───────────────────────────────────────────────────────
// 자동 백업으로 되돌린 뒤 사라진 신청: 이 PC 는 열 때마다 같은 토큰으로 404 를 받고 신청자는 아무것도 몰랐다.
test('3.18.0 QA: a request that vanished after a backup restore retains its receipt and is announced once with its summary',async()=>{
  const a=app();a.ctx.OnAirRequests={resubmit:r=>{a.ctx.resubmitted=r;}};
  a.run(`trackMyRequest('gone-token-0000000000000');rememberMyRequestCopy('gone-token-0000000000000',{requester_name:'김민수',target_date:'2026-10-20',request_type:'ABSENCE',absence_type:'교육',absence_end_date:'2026-10-22',reason:'사내 교육'});trackMyRequest('kept-token-0000000000000')`);
  const urls=[];
  a.ctx.fetch=async url=>{urls.push(url);return url.includes('gone')?{ok:false,status:404,json:async()=>({detail:'신청 결과를 찾을 수 없습니다.'})}:{ok:false,status:404,json:async()=>({detail:'Not Found'})};};
  await a.run('checkRequestOutcomes()');
  assert.equal(a.run(`Object.prototype.hasOwnProperty.call(myRequestStatuses,'gone-token-0000000000000')`),true,'현재 자료에서 확인하지 못해도 접수 키는 보존한다');
  assert.equal(a.run(`myRequestStatuses['kept-token-0000000000000']`),'PENDING','경로가 없을 때(옛 서버 등)의 404 는 지우지 않는다');
  assert.match(a.values.get('sd_my_request_statuses_v2'),/gone-token/);
  const list=plain(a.run('notifList'));
  assert.equal(list.length,1);
  assert.equal(list[0].text,'↩ 관리자가 이전 상태로 되돌려 10/20(화)~10/22(목) 교육 신청이 취소되었습니다. 필요하면 다시 신청하세요.');
  const dialog=a.element('oa-request-vanished');assert.ok(dialog.classList.contains('active'));
  dialog.querySelector('[data-oa-vanished-retry]').click();
  assert.equal(a.ctx.resubmitted.absence_type,'교육');assert.equal(a.ctx.resubmitted.reason,'사내 교육');
  urls.length=0;await a.run('checkRequestOutcomes()');
  assert.ok(urls.some(u=>u.includes('gone')),'자료 복원 이후 유효한지 다시 확인한다');
  assert.equal(plain(a.run('notifList')).length,1,'안내는 한 번만');
});
test('3.18.0 QA: without a local copy the vanished notice still says what happened',async()=>{
  const a=app();a.run(`trackMyRequest('old-token-00000000000000')`);
  a.ctx.fetch=async()=>({ok:false,status:404,json:async()=>({detail:'신청 결과를 찾을 수 없습니다.'})});
  await a.run('checkRequestOutcomes()');
  assert.match(plain(a.run('notifList'))[0].text,/관리자가 이전 상태로 되돌려 이 PC에서 보낸 신청 1건이 취소되었습니다/);
  assert.equal(a.element('oa-request-vanished').querySelector('[data-oa-vanished-retry]'),null,'내용이 없으면 [다시 작성]을 내지 않는다');
});
// 교육처럼 당직이 없던 기간에도 '이 기간의 내 당직은 해제됩니다'라고 했다.
test('3.18.0 QA: approved absence result mentions released duties only when there were some',()=>{
  const a=app();
  const base={id:31,status:'APPROVED',request_type:'ABSENCE',requester_name:'김민수',target_date:'2026-10-20',absence_end_date:'2026-10-22',absence_type:'교육'};
  const none=a.ctx.OnAirNotifications.resultRecord({...base,released_dates:[]},'김민수').text;
  assert.doesNotMatch(none,/해제/);assert.match(none,/휴가·부재 일정에 반영되었습니다\.$/);
  const unknown=a.ctx.OnAirNotifications.resultRecord({...base},'김민수').text;
  assert.doesNotMatch(unknown,/해제/);
  const two=a.ctx.OnAirNotifications.resultRecord({...base,released_dates:['2026-10-20','2026-10-22']},'김민수').text;
  assert.match(two,/이 기간의 당직 2건이 해제되었습니다: 10\/20\(화\), 10\/22\(목\)/);
});

const approvedAmendment={id:81,status:'APPROVED',request_type:'ABSENCE',requester_name:'김민수',
  target_date:'2027-01-04',absence_end_date:'2027-01-05',absence_type:'교육',absence_details:{location:'별관'},
  amends_request_id:31,amendment_original:{id:17,name:'김민수',type:'연차',start:'2026-12-28',end:'2026-12-29',education:{}},released_dates:[]};
test('approved absence amendment describes old and new dates/types without claiming duties were restored or released',()=>{
  const a=app(),r=a.ctx.OnAirNotifications.resultRecord(approvedAmendment,'김민수');
  assert.match(r.text,/교육 변경 요청 승인/);assert.match(r.text,/2026-12-28 ~ 2026-12-29 · 연차/);
  assert.match(r.text,/2027-01-04 ~ 2027-01-05 · 교육/);assert.doesNotMatch(r.text,/당직.*(?:복원|해제)/);
  assert.equal(r.meta.before,'2026-12-28 ~ 2026-12-29 · 연차');assert.equal(r.meta.after,'2027-01-04 ~ 2027-01-05 · 교육');
  assert.equal(r.meta.requestId,81);assert.equal(r.meta.kind,'request');
});
test('amendment approval mentions only server-reported valid released duties',()=>{
  const a=app(),r=a.ctx.OnAirNotifications.resultRecord({...approvedAmendment,released_dates:['2027-01-04','2027-01-05','invalid']},'김민수');
  assert.match(r.text,/당직 2건이 해제되었습니다/);assert.doesNotMatch(r.text,/invalid/);
});
for(const [status,word] of [['REJECTED','반려'],['CANCELLED','철회']])test(`absence amendment ${status} says the existing approved schedule remains`,()=>{
  const a=app(),r=a.ctx.OnAirNotifications.resultRecord({...approvedAmendment,status,reject_reason:'일정 확인 필요'},'김민수');
  assert.match(r.text,new RegExp('변경 요청 '+word));assert.match(r.text,/기존 승인 일정은 유지됩니다/);
  assert.equal(r.meta.before,undefined);assert.equal(r.meta.after,undefined);
  if(status==='REJECTED')assert.match(r.text,/반려 사유: 일정 확인 필요/);
});
test('an approved amendment changing both type and dates produces one result rather than an extra deletion alert',async()=>{
  const a=app();a.payload({schedules:{},absences:[approvedAmendment.amendment_original]});await a.run('fetchAllData()');
  routed(a,{data:{schedules:{},absences:[{id:17,name:'김민수',type:'교육',start:'2027-01-04',end:'2027-01-05',education:{location:'별관'}}]},requests:[approvedAmendment]});
  await a.run('fetchAllData()');const list=plain(a.run('notifList'));
  assert.equal(list.length,1,JSON.stringify(list.map(n=>n.text)));assert.equal(list[0].meta.requestId,81);
  assert.match(list[0].text,/변경 요청 승인/);
});

const cancellationSegments=[
  {id:51,name:'김민수',start:'2026-12-24',end:'2026-12-24',type:'휴가'},
  {id:52,name:'김민수',start:'2026-12-28',end:'2026-12-29',type:'휴가'}
];
const approvedCancellation={id:91,status:'APPROVED',request_type:'ABSENCE_CANCEL',requester_name:'김민수',
  target_date:'2026-12-24',absence_end_date:'2026-12-29',absence_type:'휴가',amends_request_id:31,
  amendment_original:{...cancellationSegments[0],end:'2026-12-29',segments:cancellationSegments},
  released_dates:[],reason:'비공개 취소 사유'};
const cancelledParent={id:31,status:'APPROVED',request_type:'ABSENCE',requester_name:'김민수',
  target_date:'2026-12-24',absence_end_date:'2026-12-29',absence_type:'휴가',cancelled_by_id:91,released_dates:[]};
for(const category of ['휴가','교육','출장','파견'])test(`approved ${category} cancellation has cancellation wording and actual historical spans`,()=>{
  const a=app(),r=a.ctx.OnAirNotifications.resultRecord({...approvedCancellation,absence_type:category,
    amendment_original:{...approvedCancellation.amendment_original,type:category,segments:cancellationSegments.map(s=>({...s,type:category}))},
    // Cancellation must not infer release effects even if a legacy payload carries these dates.
    released_dates:['2026-12-24']},'김민수');
  assert.match(r.text,new RegExp(category+' 취소 요청 승인 \\(12/24 ~ 12/29\\)'));
  assert.match(r.text,/취소 구간: 2026-12-24, 2026-12-28 ~ 2026-12-29/);
  assert.match(r.text,/승인된 .* 일정이 취소되었습니다/);
  assert.doesNotMatch(r.text,/등록|반영되었습니다|해제되었습니다|비공개 취소 사유/);
  assert.equal(r.meta.kind,'request');assert.equal(r.meta.requestId,91);assert.equal(r.meta.requestType,'ABSENCE_CANCEL');
  assert.equal(r.meta.targetDate,'2026-12-24');assert.equal(r.meta.endDate,'2026-12-29');
  assert.equal(r.meta.before,`2026-12-24 · ${category}\n2026-12-28 ~ 2026-12-29 · ${category}`);
  assert.equal(r.meta.after,'해당 승인 일정 없음');
});
for(const [status,word] of [['REJECTED','반려'],['CANCELLED','철회']])test(`absence cancellation ${status} preserves original and never claims absence was cancelled`,()=>{
  const a=app(),r=a.ctx.OnAirNotifications.resultRecord({...approvedCancellation,status,reject_reason:'휴가 일정 재확인'},'김민수');
  assert.match(r.text,new RegExp('휴가 취소 요청 '+word));assert.match(r.text,/기존 승인 일정은 유지됩니다/);
  assert.doesNotMatch(r.text,/일정이 취소되었습니다|등록|해제되었습니다|비공개 취소 사유/);
  assert.equal(r.meta.before,undefined);assert.equal(r.meta.after,undefined);
  if(status==='REJECTED')assert.match(r.text,/반려 사유: 휴가 일정 재확인/);
});
test('single-segment legacy cancellation keeps end date and local opaque receipt navigation',async()=>{
  const a=app(),request={...approvedCancellation,amendment_original:{...cancellationSegments[1]},target_date:'2026-12-28'};
  a.run("trackMyRequest('cancel/$?=opaque')");routed(a,{data:{schedules:{},absences:[]},status:request});
  await a.run('checkRequestOutcomes()');
  const saved=JSON.parse(a.values.get('sd_notifs'))[0];assert.equal(saved.meta.requestType,'ABSENCE_CANCEL');assert.equal(saved.meta.receiptToken,'cancel/$?=opaque');
  assert.equal(saved.meta.endDate,'2026-12-29');assert.match(saved.text,/12\/28 ~ 12\/29/);
  assert.doesNotMatch(saved.text,/비공개 취소 사유|opaque/);
  a.ctx.OnAirRequests={openOwnedRequest:token=>{a.ctx.openedToken=token;}};a.run('OnAirNotifications.goTo(0)');assert.equal(a.ctx.openedToken,'cancel/$?=opaque');
});
for(const order of ['personal-first','receipt-first','reload'])test(`approved cancellation emits once across ${order}, folds all removed segments, and keeps parent history`,async()=>{
  let a=app();a.run("trackMyRequest('parent-token');myRequestStatuses['parent-token']='APPROVED';trackMyRequest('cancel-token');saveMyRequestStatuses()");
  a.payload({schedules:{},absences:cancellationSegments});await a.run('fetchAllData()');
  const route=()=>routed(a,{data:{schedules:{},absences:[]},requests:[cancelledParent,approvedCancellation],status:approvedCancellation});route();
  if(order!=='personal-first')await a.run('checkRequestOutcomes()');
  if(order==='reload'){a=app(Object.fromEntries(a.values));route();}
  await a.run('fetchAllData()');await a.run('checkRequestOutcomes()');await a.run('fetchAllData()');
  a.run("notifyRemoteRefresh({lastWrite:{source:'request:91:APPROVED',host:'private-host'}})");
  const list=plain(a.run('notifList'));assert.equal(list.length,1,JSON.stringify(list.map(n=>n.text)));
  assert.match(list[0].text,/휴가 취소 요청 승인/);assert.equal(list[0].meta.requestType,'ABSENCE_CANCEL');assert.equal(list[0].meta.requestId,91);
  assert.equal(a.run("myRequestStatuses['parent-token']"),'APPROVED');assert.equal(a.run("myRequestStatuses['cancel-token']"),'APPROVED');
  assert.doesNotMatch(JSON.stringify(list),/비공개 취소 사유|private-host/);
});
test('cancellation only folds its historical absence removals, never a simultaneous duty change',async()=>{
  const a=app();a.payload({schedules:{'2026-12-24':[{name:'김민수'}]},absences:cancellationSegments});await a.run('fetchAllData()');
  routed(a,{data:{schedules:{},absences:[]},requests:[approvedCancellation]});await a.run('fetchAllData()');
  const list=plain(a.run('notifList'));assert.equal(list.length,2,JSON.stringify(list.map(n=>n.text)));
  assert.equal(list.filter(n=>n.meta.requestId===91).length,1);assert.equal(list.filter(n=>n.meta.kind==='schedule').length,1);
});
test('historical parent approval never absorbs a new duty removal after cancellation',async()=>{
  const a=app();a.payload({schedules:{'2026-12-24':[{name:'김민수'}]},absences:cancellationSegments});await a.run('fetchAllData()');
  routed(a,{data:{schedules:{},absences:[]},requests:[{...cancelledParent,released_dates:['2026-12-24']},approvedCancellation]});await a.run('fetchAllData()');
  const list=plain(a.run('notifList'));assert.equal(list.length,2);
  assert.equal(list.filter(n=>n.meta.requestId===91).length,1);assert.equal(list.filter(n=>n.meta.kind==='schedule').length,1);
  assert.ok(list.every(n=>n.meta.requestId!==31));
});
test('a stale pending receipt for a cancelled original becomes approved without reannouncing registration',async()=>{
  const a=app();a.run("trackMyRequest('parent-token')");routed(a,{data:{schedules:{},absences:[]},status:cancelledParent});
  await a.run('checkRequestOutcomes()');assert.equal(a.run("myRequestStatuses['parent-token']"),'APPROVED');assert.equal(a.run('notifList.length'),0);
});
test('an observed cancellation cannot swallow a later independent removal of the same dates',async()=>{
  const a=app();a.payload({schedules:{},absences:cancellationSegments});await a.run('fetchAllData()');
  routed(a,{data:{schedules:{},absences:[]},requests:[approvedCancellation]});await a.run('fetchAllData()');
  routed(a,{data:{schedules:{},absences:cancellationSegments},requests:[approvedCancellation]});await a.run('fetchAllData()');
  routed(a,{data:{schedules:{},absences:[]},requests:[approvedCancellation]});await a.run('fetchAllData()');
  const list=plain(a.run('notifList'));assert.equal(list.length,5);assert.equal(list.filter(n=>n.meta.requestId===91).length,1);
  assert.match(list[0].text,/일정이 삭제되었습니다/);
});
test('partial or mismatched original removal cannot be attributed to a cancellation approval',async()=>{
  for(const variant of ['partial','education']){
    const a=app(),segments=variant==='education'?cancellationSegments.map(s=>({...s,type:'교육',education:{location:'본관'}})):cancellationSegments;
    const request=variant==='education'?{...approvedCancellation,absence_type:'교육',amendment_original:{...approvedCancellation.amendment_original,type:'교육',segments:segments.map(s=>({...s,education:{location:'별관'}}))}}:approvedCancellation;
    a.payload({schedules:{},absences:segments});await a.run('fetchAllData()');
    routed(a,{data:{schedules:{},absences:variant==='partial'?[segments[1]]:[]},requests:[request]});await a.run('fetchAllData()');
    const list=plain(a.run('notifList'));assert.ok(list.length);assert.ok(list.every(n=>n.meta.kind==='absence'&&n.meta.requestId===undefined),variant);
  }
});
test('cancellation for another person cannot produce a personal notification',async()=>{
  const a=app();a.payload({schedules:{},absences:[]});await a.run('fetchAllData()');
  routed(a,{data:{schedules:{},absences:[]},requests:[{...approvedCancellation,requester_name:'이서연'}]});await a.run('fetchAllData()');
  assert.equal(a.run('notifList.length'),0);
});
test('withdrawal from another tab wins over an in-flight cancellation outcome',async()=>{
  const a=app();a.run("trackMyRequest('cancel-token')");let resolve;
  a.ctx.fetch=()=>new Promise(done=>{resolve=done;});const pending=a.run('checkRequestOutcomes()');
  a.values.set('sd_my_request_statuses_v2',JSON.stringify({'cancel-token':'CANCELLED'}));
  resolve({ok:true,json:async()=>({request:approvedCancellation})});await pending;
  assert.equal(a.run('notifList.length'),0);assert.equal(a.run("myRequestStatuses['cancel-token']"),'CANCELLED');
});
test('restored-away cancellation is labelled as a cancellation request rather than a new absence',()=>{
  const a=app(),text=a.ctx.OnAirNotifications.vanishedText(approvedCancellation);
  assert.match(text,/12\/24.*12\/29.*휴가 취소 요청/);assert.doesNotMatch(text,/변경 신청|비공개 취소 사유/);
});
