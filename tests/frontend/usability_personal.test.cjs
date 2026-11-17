'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, 'frontend/dist/index.html'), 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m=>m[1]).find(s=>s.includes('function consecutiveRun('));
const sidecarPath = path.join(root, 'frontend/dist/usability_personal.js');
function app(saved={}) {
  const nodes = new Map(), listeners = new Map();
  const element=id=>{if(!nodes.has(id))nodes.set(id,{id,value:'',textContent:'',innerHTML:'',style:{setProperty(k,v){this[k]=v;}},dataset:{},clientWidth:120,clientHeight:48,classList:{add(){},remove(){},toggle(){},contains(){return false;}},querySelectorAll(){return [];},querySelector(){return null;},setAttribute(){},appendChild(){},focus(){ctx.document.activeElement=this;}});return nodes.get(id);};
  const makeStorage=initial=>{const values=new Map(Object.entries(initial));return {getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,String(v)),removeItem:k=>values.delete(k),values};};
  const local=makeStorage(saved.local||{}),session=makeStorage(saved.session||{});
  const ctx=vm.createContext({Date,console,TextEncoder,Blob,URL,setTimeout,clearTimeout,setInterval,clearInterval,requestAnimationFrame:()=>{},document:{readyState:'loading',addEventListener(type,fn){if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(fn);},getElementById:element,querySelectorAll(){return [];},querySelector(){return null;},createElement:element,head:{appendChild(){}},body:{appendChild(){},removeChild(){}}},window:{addEventListener(){}},OnAirUI:{open:(id,title,body,options)=>{ctx.opened={id,title,body,options};},close:id=>{ctx.closed=id;},escape:s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),dateLabel:k=>{const [y,m,d]=k.split('-').map(Number);return `${m}/${d}(${'일월화수목금토'[new Date(y,m-1,d).getDay()]})`;}}});
  ctx.window=ctx;ctx.addEventListener=()=>{};
  // Accessor properties must be defined inside the context; a getter set on the sandbox object from outside is not honoured.
  if(saved.blocked)vm.runInContext("for(const k of ['localStorage','sessionStorage'])Object.defineProperty(globalThis,k,{configurable:true,get(){throw new Error('SecurityError: storage is blocked');}});",ctx);
  else{ctx.localStorage=local;ctx.sessionStorage=session;}
  vm.runInContext(script,ctx,{filename:'index.html'});
  const run=s=>vm.runInContext(s,ctx);
  run(`todayStr='2026-10-13';curYear=2026;curMonth=10;memberList=[{id:1,name:'은지',gender:'F'},{id:2,name:'은서',gender:'F'},{id:3,name:'민수',gender:'M'},{id:4,name:'수진',gender:'F'}];finalHolidays={};scheduleMap={};absences=[];noDutyMap={};closedMonths=[];renderMyNameUI=()=>{};updateMemberSelectionUI=()=>{};showToast=(message)=>{globalThis.toast=message;};renderCalendar=()=>{if(document.getElementById('member-search-input').value.trim())executeMemberSearch();};`);
  assert.ok(fs.existsSync(sidecarPath),'personal usability sidecar is implemented');
  vm.runInContext(fs.readFileSync(sidecarPath,'utf8'),ctx,{filename:sidecarPath});
  const clickEducation=()=>{const tag=ctx.opened.body.match(/<button[^>]*data-oa-action="education"[^>]*>/)?.[0];assert.ok(tag,'education detail button exists');const dataset=Object.fromEntries([...tag.matchAll(/data-([a-z-]+)="([^"]*)"/g)].map(([,k,v])=>[k.replace(/-([a-z])/g,(_,c)=>c.toUpperCase()),v]));return ()=>{const node={dataset};for(const fn of listeners.get('click')||[])fn({target:{closest:()=>node},preventDefault(){},stopPropagation(){}});};};
  const key=(overrides={})=>{const event={key:'Enter',prevented:false,preventDefault(){this.prevented=true;},...overrides};ctx.handleMemberSearchKeydown(event);return event;};
  return {run,element,ctx,local,session,key,clickEducation,listeners,p:ctx.OnAirPersonal};
}
const plain=x=>JSON.parse(JSON.stringify(x));
test('name search returns zero, multiple, and exact results from real roster',()=>{const a=app();assert.deepEqual(plain(a.p.searchNames('없는이름')),[]);assert.equal(a.p.searchNames('은').length,2);assert.equal(a.p.searchNames('은지')[0].name,'은지');});
test('ambiguous search keeps typing focus and only opens results on Enter',()=>{const a=app(),input=a.element('member-search-input');input.focus();input.value='은';a.run("selectedQuickMember='민수';executeMemberSearch()");assert.equal(a.run('selectedQuickMember'),'');assert.equal(a.ctx.opened,undefined);assert.equal(a.ctx.document.activeElement,input);assert.equal(a.key().prevented,true);assert.match(a.ctx.opened.body,/은지/);assert.match(a.ctx.opened.body,/은서/);assert.equal(a.ctx.opened.id,'oa-personal-search');});
test('missing search displays explicit empty results after Enter',()=>{const a=app();a.element('member-search-input').value='없는이름';a.run('executeMemberSearch()');assert.equal(a.ctx.opened,undefined);a.key();assert.match(a.ctx.opened.body,/일치하는 이름이 없습니다/);});
test('closing search results then rendering does not reopen them; explicit Enter does',()=>{const a=app();a.element('member-search-input').value='은';a.key();assert.equal(a.ctx.opened.id,'oa-personal-search');a.ctx.OnAirUI.close('oa-personal-search');a.ctx.opened=undefined;a.run('renderCalendar()');assert.equal(a.ctx.opened,undefined);a.key();assert.equal(a.ctx.opened.id,'oa-personal-search');});
test('composition Enter and legacy IME keyCode 229 do not submit or cancel composition',()=>{const a=app();a.element('member-search-input').value='은';for(const event of [{isComposing:true},{keyCode:229},{key:'ArrowDown'}]){assert.equal(a.key(event).prevented,false);assert.equal(a.ctx.opened,undefined);}a.key();assert.equal(a.ctx.opened.id,'oa-personal-search');});
test('exact and unique names retain calendar highlighting without a results dialog',()=>{const a=app();for(const [query,name] of [['은지','은지'],['민','민수'],['','']]){a.element('member-search-input').value=query;a.run('executeMemberSearch()');assert.equal(a.run('selectedQuickMember'),name);a.key();assert.equal(a.ctx.opened,undefined);}});
test('default my name applies to session and does not write local identity',()=>{const a=app();a.run("setMyName('은지')");assert.equal(a.run('myName'),'은지');assert.equal(a.session.getItem('sd_my_name_session'),'은지');assert.equal(a.local.getItem('sd_my_name'),null);});
test('remember is explicit and clearing removes both identity stores',()=>{const a=app();a.run("setMyName('은지',{remember:true})");assert.equal(a.local.getItem('sd_my_name'),'은지');assert.equal(a.local.getItem('sd_my_name_remember'),'1');a.run("setMyName('')");assert.equal(a.session.getItem('sd_my_name_session'),null);assert.equal(a.local.getItem('sd_my_name'),null);});
test('legacy remembered identity is migrated without changing the existing preference',()=>{const a=app({local:{sd_my_name:'민수'}});assert.equal(a.run('myName'),'민수');assert.equal(a.local.getItem('sd_my_name_remember'),'1');});
test('an explicit session-only marker prevents restoration of a legacy local name',()=>{const a=app({local:{sd_my_name:'민수',sd_my_name_remember:'0'}});assert.equal(a.run('myName'),'');});
test('remembered and session identity restore using the existing myName variable',()=>{const a=app({local:{sd_my_name:'민수',sd_my_name_remember:'1'},session:{sd_my_name_session:'은지'}});assert.equal(a.run('myName'),'은지');});
test('absence sections prioritize ongoing and upcoming and keep past records',()=>{const a=app();a.run(`absences=[{id:3,name:'은지',type:'교육',start:'2026-09-01',end:'2026-09-02'},{id:2,name:'은지',type:'연차',start:'2026-10-21',end:'2026-10-23'},{id:1,name:'은지',type:'교육',start:'2026-10-12',end:'2026-10-16'}]`);const s=a.p.absenceSections('은지');assert.equal(s.ongoing[0].id,1);assert.equal(s.upcoming[0].id,2);assert.equal(s.past[0].id,3);a.p.openAbsence('은지','2026-10-13');assert.match(a.ctx.opened.body,/<details[^>]*><summary>지난 일정 1건/);assert.ok(a.ctx.opened.body.indexOf('진행 중')<a.ctx.opened.body.indexOf('예정된 일정'));assert.match(a.ctx.opened.body,/10\/12\(월\)/);});
test('education fields hide blanks and never expose request reasons',()=>{const a=app();const f=a.p.educationFields({title:' 실무 교육 ',location:'',provider:'사내',preparation:'노트북',content:'실습',notes:'안내',reason:'PRIVATE'});assert.deepEqual(plain(f.map(x=>x.key)),['title','provider','preparation','content','notes']);a.run(`absences=[{name:'은지',type:'교육',start:'2026-10-12',end:'2026-10-16',reason:'PRIVATE',education:{title:'실무',content:'<실습>',notes:'안내'}}]`);a.p.openEducation(a.run('absences[0]'));assert.match(a.ctx.opened.body,/&lt;실습&gt;/);assert.doesNotMatch(a.ctx.opened.body,/PRIVATE|장소/);});
function educationRecords(a){a.run(`absences=[{id:101,name:'은지',type:'교육',start:'2026-11-01',end:'2026-11-03',education:{title:'원래 교육'}},{id:102,name:'민수',type:'교육',start:'2026-12-01',end:'2026-12-03',education:{title:'다른 사람 교육'}}]`);a.p.openAbsence('은지');return a.clickEducation();}
test('education detail follows the stable ID after API replacement and reordering',()=>{const a=app(),click=educationRecords(a);a.run(`absences=JSON.parse(JSON.stringify(absences)).reverse();absences[1].id='101';absences[1].education.title='갱신된 원래 교육'`);click();assert.equal(a.ctx.opened.id,'oa-personal-education');assert.match(a.ctx.opened.title,/은지/);assert.match(a.ctx.opened.body,/갱신된 원래 교육/);assert.doesNotMatch(a.ctx.opened.body,/다른 사람 교육/);});
test('deleted education record shows notice and refreshes the original persons absence list',()=>{const a=app(),click=educationRecords(a);a.run('absences=absences.filter(a=>a.id!==101)');click();assert.equal(a.ctx.opened.id,'oa-personal-absence');assert.match(a.ctx.opened.title,/은지/);assert.match(a.ctx.toast,/삭제|변경/);assert.doesNotMatch(a.ctx.opened.body,/다른 사람 교육|원래 교육/);});
test('education detail refuses a record whose ID now belongs to another person',()=>{const a=app(),click=educationRecords(a);a.run(`absences=[{id:101,name:'민수',type:'교육',start:'2026-11-01',end:'2026-11-03',education:{title:'다른 사람 교육'}}]`);click();assert.equal(a.ctx.opened.id,'oa-personal-absence');assert.match(a.ctx.opened.title,/은지/);assert.doesNotMatch(a.ctx.opened.body,/다른 사람 교육/);});
test('education records without stable IDs do not create unsafe detail links',()=>{const a=app();a.run(`absences=[{name:'은지',type:'교육',start:'2026-11-01',end:'2026-11-03',education:{title:'교육'}}]`);a.p.openAbsence('은지');assert.doesNotMatch(a.ctx.opened.body,/data-oa-action="education"/);});
test('unified agenda merges duties and absence with inclusive absence state',()=>{const a=app();a.run(`scheduleMap={'2026-10-18':[{name:'은지'},{name:'민수'}],'2026-10-10':[{name:'은지'}],'2026-10-19':[{name:'은지'}]};noDutyMap={'2026-10-19':true};absences=[{name:'은지',type:'교육',start:'2026-10-12',end:'2026-10-13',education:{title:'실무'}}]`);const rows=a.p.agendaRows('은지');assert.equal(rows.length,3);assert.equal(rows[0].state,'ongoing');assert.equal(rows[1].start,'2026-10-18');assert.equal(rows[2].state,'past');assert.deepEqual(plain(rows[1].mates),['민수']);assert.equal(a.p.agendaRows('은지','duty').length,2);});
test('agenda offers the existing full duty list and ICS without deleting it',()=>{const a=app();a.run("myName='은지'");a.p.openAgenda('은지');assert.match(a.ctx.opened.body,/전체 당직표/);assert.match(a.ctx.opened.body,/ICS 내려받기/);assert.match(a.ctx.opened.body,/등록된 일정이 없습니다/);});
test('agenda duty countdown uses padded right badges and keeps coworkers, notes, and past state',()=>{
  const a=app();a.run(`myName='은지';scheduleMap={'2026-10-12':[{name:'은지'}],'2026-10-13':[{name:'은지'},{name:'민수'}],'2026-10-16':[{name:'은지'}],'2026-10-25':[{name:'은지'}],'2027-01-21':[{name:'은지'}]};commentMap={'2026-10-16':'인수인계 확인'}`);a.p.openAgenda('은지','duty');
  const body=a.ctx.opened.body;
  assert.deepEqual([...body.matchAll(/class="op-dday[^\"]*"[^>]*>([^<]+)<\/span>/g)].map(m=>m[1]),['D-DAY','D-03','D-12','D-100']);
  assert.match(body,/함께 근무 민수/);assert.match(body,/인수인계 확인/);assert.match(body,/지난 일정<\/div>/);
  assert.doesNotMatch(body,/<div class="op-sub">\d+일 뒤 당직|까지 · 진행 중|D--/);
  assert.match(body,/aria-label="3일 뒤 당직">D-03<\/span><div class="op-actions">/);
});
test('both absence lists show countdown badges and preserve ongoing and past information',()=>{
  const a=app();a.run(`absences=[{id:1,name:'은지',type:'교육',start:'2026-10-10',end:'2026-10-16'},{id:2,name:'은지',type:'연차',start:'2026-10-13',end:'2026-10-15'},{id:3,name:'은지',type:'교육',start:'2026-10-16',end:'2026-10-18',education:{title:'실무 교육'}},{id:4,name:'은지',type:'연차',start:'2026-10-25',end:'2026-10-25'},{id:5,name:'은지',type:'연차',start:'2026-10-01',end:'2026-10-02'}]`);
  for(const open of [()=>a.p.openAbsence('은지'),()=>a.p.openAgenda('은지','absence')]){
    open();const body=a.ctx.opened.body;
    assert.deepEqual([...body.matchAll(/class="op-dday[^\"]*"[^>]*>([^<]+)<\/span>/g)].map(m=>m[1]),['D-DAY','D-03','D-12']);
    assert.match(body,/10\/16\(금\)까지 · 진행 중/);assert.match(body,/10\/15\(목\)까지 · 진행 중/);assert.match(body,/지난 일정<\/div>/);
    assert.match(body,/aria-label="오늘 시작">D-DAY/);assert.match(body,/aria-label="3일 뒤 시작">D-03/);
    assert.doesNotMatch(body,/<div class="op-sub">\d+일 뒤 시작|D--/);assert.match(body,/실무 교육/);
  }
});
test('vacation fit reserves one readable overflow slot and never shrinks below 12px',()=>{const a=app();const x=a.p.vacationLayout(120,48,8);assert.equal(x.slots,4);assert.equal(x.visible,3);assert.equal(x.hidden,5);assert.equal(x.font,12);assert.equal(a.p.vacationLayout(120,48,4).hidden,0);});
test('cross-month candidates respect sex, absence, no duty, closure, duplicate and day class',()=>{const a=app();a.run(`scheduleMap={'2026-10-18':[{name:'은지'}],'2026-11-01':[{name:'수진'}],'2026-11-08':[{name:'민수'}],'2026-11-15':[{name:'수진'},{name:'은지'}],'2026-11-22':[{name:'은서'}],'2026-10-25':[{name:'수진'}],'2026-11-02':[{name:'수진'}]};noDutyMap={'2026-10-25':true};absences=[{name:'은서',type:'연차',start:'2026-10-18',end:'2026-10-18'}]`);assert.deepEqual(plain(a.p.swapCandidates('은지','2026-10-18',60).map(x=>[x.name,x.date])),[['수진','2026-11-01']]);a.run("closedMonths=['2026-11']");assert.equal(a.p.swapCandidates('은지','2026-10-18',60).length,0);});
test('major block dates are not presented as single-day swaps',()=>{const a=app();a.run(`scheduleMap={'2026-10-18':[{name:'은지'}],'2026-10-25':[{name:'수진'}]};majorDutyBlockStart=k=>k==='2026-10-25'?'2026-10-24':null`);assert.equal(a.p.swapCandidates('은지','2026-10-18',30).length,0);});
test('candidate interval comparison is calculated after both replacement assignments',()=>{const a=app();a.run(`scheduleMap={'2026-10-04':[{name:'은지'}],'2026-10-18':[{name:'은지'}],'2026-11-01':[{name:'수진'}],'2026-11-08':[{name:'은지'}],'2026-10-11':[{name:'수진'}],'2026-10-25':[{name:'수진'}]}`);const x=a.p.postSwapIntervals('은지','2026-10-18','수진','2026-11-01');assert.deepEqual(plain(x.mine),{previous:28,next:7});assert.deepEqual(plain(x.partner),{previous:7,next:7});});
test('consecutive work warning is based on existing logic with post-swap overrides',()=>{const a=app();a.run(`scheduleMap={'2026-10-18':[{name:'은지'}],'2026-10-25':[{name:'수진'}],'2026-10-24':[{name:'은지'}]}`);const x=a.p.swapCandidates('은지','2026-10-18',30)[0];assert.ok(x.risks.some(r=>r.name==='은지'&&r.len>=7));});
test('selecting a candidate fills request DTO without submitting or changing assignments',()=>{const a=app();a.run(`SERVER_CONNECTED=true;SYSTEM_MODE='VIEWER';scheduleMap={'2026-10-18':[{name:'은지'}],'2026-11-01':[{name:'수진'}]};OnAirRequests={openChange:d=>{globalThis.requestSeed=d;}}`);const before=a.run('JSON.stringify(scheduleMap)');assert.equal(a.p.selectSwapCandidate('은지','2026-10-18','수진','2026-11-01'),true);assert.deepEqual(plain(a.ctx.requestSeed),{requester_name:'은지',target_date:'2026-10-18',request_type:'SWAP',swap_date:'2026-11-01',swap_worker:'수진'});assert.equal(a.run('JSON.stringify(scheduleMap)'),before);});
test('candidate is rechecked against changes immediately before prefilling',()=>{const a=app();a.run(`SERVER_CONNECTED=true;SYSTEM_MODE='VIEWER';scheduleMap={'2026-10-18':[{name:'은지'}],'2026-11-01':[{name:'수진'}]};closedMonths=['2026-11'];OnAirRequests={openChange:d=>{globalThis.requestSeed=d;}}`);assert.equal(a.p.selectSwapCandidate('은지','2026-10-18','수진','2026-11-01'),false);assert.equal(a.ctx.requestSeed,undefined);});
test('ICS uses all-day dates, exclusive ends, stable UIDs and CRLF',()=>{const a=app();a.run(`scheduleMap={'2026-10-18':[{name:'은지'}],'2026-10-19':[{name:'은지'}]};noDutyMap={'2026-10-19':true};absences=[{id:9,name:'은지',type:'교육',start:'2026-10-30',end:'2026-11-02',education:{title:'실무',location:'본관'}}]`);const text=a.p.buildICS('은지');assert.match(text,/DTSTART;VALUE=DATE:20261030\r\nDTEND;VALUE=DATE:20261103/);assert.match(text,/DTSTART;VALUE=DATE:20261018\r\nDTEND;VALUE=DATE:20261019/);assert.doesNotMatch(text,/20261019\r\nDTEND|TZID|T090000/);assert.equal((text.match(/BEGIN:VEVENT/g)||[]).length,2);assert.deepEqual(text.match(/UID:[^\r]+/g),a.p.buildICS('은지').match(/UID:[^\r]+/g));assert.equal(text.replace(/\r\n/g,'').includes('\n'),false);});
test('ICS escapes CRLF, delimiters, and backslashes without exposing private reasons',()=>{const a=app();a.run(`absences=[{id:9,name:'은지',type:'교육',start:'2026-10-30',end:'2026-11-02',reason:'PRIVATE',education:{title:'교육,실무;A',content:'첫줄\\r\\n둘째줄\\\\장비'}}]`);const text=a.p.buildICS('은지');assert.match(text,/교육\\,실무\\;A/);assert.match(text.replace(/\r\n[ \t]/g,''),/첫줄\\n둘째줄\\\\장비/);assert.doesNotMatch(text,/PRIVATE/);});
test('ICS folding keeps every UTF-8 content line within 75 octets',()=>{const a=app();a.run(`absences=[{name:'은지',type:'교육',start:'2026-10-30',end:'2026-11-02',education:{content:'가'.repeat(120)}}]`);const text=a.p.buildICS('은지');for(const line of text.split('\r\n'))assert.ok(Buffer.byteLength(line)<=75,line);});
test('long displayed names fit their centered slot without shrinking the font or replacing identity',()=>{const a=app();assert.equal(typeof a.p.fitNameLabel,'function');const full='김가람가장긴이름테스트';const display=a.p.fitNameLabel(full,62,s=>Array.from(s).length*10);assert.ok(display.endsWith('…'));assert.ok(Array.from(display).length*10<=58);assert.equal(a.p.fitNameLabel('김민수',62,s=>Array.from(s).length*10),'김민수');assert.equal(full,'김가람가장긴이름테스트');});

// [위험] v3.17.12 관리자 모드의 '내 일정'에 [맞교환 후보]가 보였고, 후보를 고르면 일정 창이 모두 닫힌 뒤
//   "조회용 프로그램에서 서버에 연결된 경우에만 신청할 수 있습니다" 로 끝났다(입력이 사라진 막다른 길).
//   신청은 서버에 연결된 조회용 프로그램만 받으므로 그때만 후보 버튼을 보이고, 아니면 창을 닫지 않는다.
function swapAgenda(a,mode,connected){a.run(`myName='은지';SYSTEM_MODE='${mode}';SERVER_CONNECTED=${connected};scheduleMap={'2026-10-18':[{name:'은지'}],'2026-11-01':[{name:'수진'}]};OnAirRequests={openChange:d=>{globalThis.requestSeed=d;}}`);a.p.openAgenda('은지','duty');return a.ctx.opened.body;}
for(const [mode,connected,shown] of [['ADMIN',true,false],['ADMIN',false,false],['VIEWER',false,false],['VIEWER',true,true]]){
  test(`swap candidate button in own agenda: ${mode} connected=${connected} -> ${shown?'shown':'hidden'}`,()=>{
    const a=app(),body=swapAgenda(a,mode,connected);
    assert.match(body,/data-oa-action="date"/,'date button is always available');
    assert.equal(/data-oa-action="swap"/.test(body),shown);
  });
}
test('ADMIN: choosing a swap candidate keeps dialogs open and does not open the request form',()=>{
  const a=app();swapAgenda(a,'ADMIN',true);a.ctx.closed=undefined;
  assert.equal(a.p.selectSwapCandidate('은지','2026-10-18','수진','2026-11-01'),false);
  assert.equal(a.ctx.closed,undefined,'no dialog was closed');
  assert.equal(a.ctx.requestSeed,undefined,'request form was not opened');
  assert.match(a.ctx.toast,/조회용 WLR OnAir가 켜져 있고 연결되어 있을 때만/);
});
test('VIEWER disconnected: a stale swap button shows the reason instead of the candidate dialog',()=>{
  const a=app();swapAgenda(a,'VIEWER',false);a.ctx.opened=undefined;
  const node={dataset:{oaAction:'swap',name:'은지',date:'2026-10-18'}};
  for(const fn of a.listeners.get('click')||[])fn({target:{closest:()=>node},preventDefault(){},stopPropagation(){}});
  assert.equal(a.ctx.opened,undefined);assert.match(a.ctx.toast,/연결되어 있을 때만 신청할 수 있습니다/);
});
test('VIEWER connected: the candidate prefills the request and then closes the dialogs',()=>{
  const a=app();swapAgenda(a,'VIEWER',true);
  assert.equal(a.p.selectSwapCandidate('은지','2026-10-18','수진','2026-11-01'),true);
  assert.equal(a.ctx.requestSeed.swap_worker,'수진');assert.ok(a.ctx.closed);
});
// [위험] 저장소가 막힌 브라우저에서는 window.localStorage 를 읽는 것만으로 예외가 난다.
//   보조 스크립트가 시작하다 멈추면 이름 선택·개인 일정이 통째로 사라진다.
test('blocked browser storage: personal sidecar installs and name selection still works for this run',()=>{
  const a=app({blocked:true});
  assert.throws(()=>a.run('window.localStorage'),/SecurityError/,'fixture blocks property reads');
  assert.equal(a.run('myName'),'');
  a.run("setMyName('은지',{remember:true})");assert.equal(a.run('myName'),'은지');
  a.p.openPicker();assert.match(a.ctx.opened.body,/이 PC에 내 이름 기억/);
});

// ── v3.18.0 ──────────────────────────────────────────────────────
// [위험] 맞교환 후보에서 두 번째 후보를 골라도 선택이 바뀌지 않고, 신청서에는 첫 후보가 채워졌다.
//   선택 버튼이 {name,date} 만 저장해 risks 를 읽다 예외가 나고 화면이 다시 그려지지 않았다.
const fire=(a,dataset)=>{for(const fn of a.listeners.get('click')||[])fn({target:{closest:()=>({dataset})},preventDefault(){},stopPropagation(){}});};
test('choosing the second swap candidate changes the selection and pre-fills that partner and date',()=>{
  const a=app();a.run(`myName='은지';SYSTEM_MODE='VIEWER';SERVER_CONNECTED=true;scheduleMap={'2026-10-18':[{name:'은지'}],'2026-10-25':[{name:'수진'}],'2026-11-01':[{name:'은서'}]};OnAirRequests={openChange:d=>{globalThis.requestSeed=d;}}`);
  fire(a,{oaAction:'swap',name:'은지',date:'2026-10-18'});assert.match(a.ctx.opened.body,/data-partner="수진"[^>]*data-target="2026-10-25"[^>]*>이 후보로/);
  fire(a,{oaAction:'choose-swap',name:'은지',source:'2026-10-18',partner:'은서',target:'2026-11-01',window:'30'});
  const body=a.ctx.opened.body;assert.equal(a.ctx.opened.id,'oa-personal-swap');
  assert.match(body,/data-oa-action="prefill-swap"[^>]*data-partner="은서"[^>]*data-target="2026-11-01"/);
  assert.match(body,/<tr class="selected"><td><b>은서<\/b>/);assert.match(body,/<b>은서 11\/1\(일\)<\/b>/);
  fire(a,{oaAction:'prefill-swap',name:'은지',source:'2026-10-18',partner:'은서',target:'2026-11-01'});
  assert.equal(a.ctx.requestSeed.swap_worker,'은서');assert.equal(a.ctx.requestSeed.swap_date,'2026-11-01');assert.equal(a.ctx.requestSeed.target_date,'2026-10-18');
});
test('agenda marks duties with a pending request and offers 내 신청 내역 next to 내 일정',()=>{
  const a=app();a.run(`myName='은지';SYSTEM_MODE='VIEWER';SERVER_CONNECTED=true;scheduleMap={'2026-10-18':[{name:'은지'}],'2026-10-25':[{name:'은지'}]};OnAirRequests={pendingFor:(n,d)=>n==='은지'&&d==='2026-10-18'?[{id:1,text:'은지 10/18 ↔ 수진 10/25 맞교환'}]:[],openMyRequests:n=>{globalThis.myRequestsFor=n;}}`);
  a.p.openAgenda('은지');const body=a.ctx.opened.body;
  assert.equal((body.match(/>신청 대기<\/span>/g)||[]).length,1);assert.match(body,/title="은지 10\/18 ↔ 수진 10\/25 맞교환">신청 대기/);
  assert.match(body,/data-oa-action="my-requests"[^>]*>내 신청 내역/);fire(a,{oaAction:'my-requests',name:'은지'});assert.equal(a.ctx.myRequestsFor,'은지');
  a.run(`SYSTEM_MODE='ADMIN'`);a.p.openAgenda('은지');assert.doesNotMatch(a.ctx.opened.body,/data-oa-action="my-requests"/);
});
test('personal popup offers ICS for any person; ICS holds today and later unless past is requested',()=>{
  const a=app();a.run(`myName='은지';scheduleMap={'2026-10-01':[{name:'수진'}],'2026-10-13':[{name:'수진'}],'2026-10-20':[{name:'수진'}]};absences=[{id:4,name:'수진',type:'연차',start:'2026-10-05',end:'2026-10-06'},{id:5,name:'수진',type:'연차',start:'2026-10-12',end:'2026-10-14'}]`);
  a.p.openAgenda('수진');assert.match(a.ctx.opened.body,/data-oa-action="ics" data-name="수진"/);assert.match(a.ctx.opened.body,/id="oa-ics-past"/);assert.doesNotMatch(a.ctx.opened.body,/전체 당직표/);
  const future=a.p.buildICS('수진'),all=a.p.buildICS('수진',{includePast:true});
  assert.equal((future.match(/BEGIN:VEVENT/g)||[]).length,3);assert.doesNotMatch(future,/20261001|20261005/);assert.match(future,/DTSTART;VALUE=DATE:20261012/);
  assert.equal((all.match(/BEGIN:VEVENT/g)||[]).length,5);
});
test('downloading ICS shows how to import it and how to refresh it',()=>{
  const a=app();a.run(`scheduleMap={'2026-10-20':[{name:'수진'}]}`);a.ctx.document.createElement=()=>({click(){},remove(){}});a.ctx.URL={createObjectURL:()=>'blob:x',revokeObjectURL(){}};
  a.p.openAgenda('수진');a.p.downloadICS('수진');
  const hint='Outlook에서 파일을 열면 일정이 추가됩니다. 바뀐 일정은 다시 내려받아 가져오세요.';
  assert.equal(a.element('oa-ics-hint').textContent,hint);assert.equal(a.element('oa-ics-hint').hidden,false);assert.equal(a.ctx.toast,hint);
});

// Restoring an identity must restore the real search/selection flow after data arrives.
test('remembered identity restores duty selection only after the roster is loaded',()=>{
  const a=app({local:{sd_my_name:'민수',sd_my_name_remember:'1'}});
  a.run('memberList=[];renderMyNameUI()');assert.equal(a.run('selectedQuickMember'),'');
  a.run("memberList=[{id:3,name:'민수',gender:'M'}];dataLoaded=true;renderMyNameUI()");
  assert.equal(a.run('selectedQuickMember'),'민수');assert.equal(a.element('member-search-input').value,'민수');
  assert.equal(a.run('memberPickClass()'),'pick-male');
});
test('session identity restores female duty selection without remembering the PC',()=>{
  const a=app({session:{sd_my_name_session:'은지'}});a.run('dataLoaded=true;renderMyNameUI()');
  assert.equal(a.run('selectedQuickMember'),'은지');assert.equal(a.run('memberPickClass()'),'pick-female');
  assert.equal(a.local.getItem('sd_my_name'),null);
});
test('restoration never replaces an explicit search and never repeats on later refreshes',()=>{
  const a=app({local:{sd_my_name:'민수',sd_my_name_remember:'1'}});
  a.element('member-search-input').value='은';a.run('executeMemberSearch();dataLoaded=true;renderMyNameUI()');
  assert.equal(a.element('member-search-input').value,'은');assert.equal(a.run('selectedQuickMember'),'');
  a.element('member-search-input').value='';a.run('executeMemberSearch();renderMyNameUI()');
  assert.equal(a.run('selectedQuickMember'),'');
});
test('a removed remembered member is not selected after loading the roster',()=>{
  const a=app({local:{sd_my_name:'퇴사자',sd_my_name_remember:'1'}});a.run('dataLoaded=true;renderMyNameUI()');
  assert.equal(a.run('selectedQuickMember'),'');assert.equal(a.element('member-search-input').value,'');
});
test('clearing personal identity keeps calendar selection cleared on the next render',()=>{
  const a=app();a.run("dataLoaded=true;setMyName('은지',{remember:true});setMyName('');renderCalendar();renderMyNameUI()");
  assert.equal(a.run('selectedQuickMember'),'');assert.equal(a.element('member-search-input').value,'');
  assert.equal(a.session.getItem('sd_my_name_session'),null);assert.equal(a.local.getItem('sd_my_name'),null);
});
