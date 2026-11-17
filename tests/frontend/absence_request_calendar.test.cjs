'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=process.env.ONAIR_REQUEST_CALENDAR_TEST_SOURCE||path.resolve(__dirname,'../..');
const html=fs.readFileSync(path.join(root,'frontend/dist/index.html'),'utf8');
const base=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m=>m[1]).find(s=>s.includes('function renderTodayCard()'));
function app(){
 const nodes=new Map(),local=new Map(),session=new Map();let ctx;const snapshots=[];let hit=null;
 const node=id=>{if(!nodes.has(id)){const classes=new Set();nodes.set(id,{id,isConnected:true,events:{},value:'',checked:false,disabled:false,hidden:false,innerHTML:'',textContent:'',style:{},dataset:{},options:[],children:[],scrollTop:0,scrollLeft:0,classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle(x,v){v?classes.add(x):classes.delete(x)}},setAttribute(k,v){this[k]=v},removeAttribute(k){delete this[k]},appendChild(o){this.children.push(o);this.options.push(o);o.parentNode=this;return o},insertAdjacentHTML(){},append(){},insertBefore(o){this.appendChild(o)},addEventListener(type,fn){(this.events[type]??=[]).push(fn)},dispatchEvent(event){event.target??=this;if(event.type==='change'&&this.id==='absence-req-start')vm.runInContext('onAbsenceRequestStartChange()',ctx);for(const fn of this.events[event.type]||[])fn(event);if(event.bubbles){snapshots.push([node('absence-req-start').value,node('absence-req-end').value]);for(const fn of node('absence-request-modal').events[event.type]||[])fn(event);}},querySelector(selector){const key=/data-arc-date="([^"]+)"/.exec(selector||'');return key?day(key[1]):null;},querySelectorAll(){return []},focus(){document.activeElement?.blurHandler?.();document.activeElement=this},scrollIntoView(){},remove(){},closest(selector){return selector==='[data-arc-date]'&&this.dataset.arcDate?this:null},contains(el){return !!el&&el.id.startsWith('day-')},setPointerCapture(id){this.captured=id},hasPointerCapture(id){return this.captured===id},releasePointerCapture(){this.captured=null}});}return nodes.get(id)};
 function day(key){const d=node('day-'+key);d.dataset.arcDate=key;return d;}
 const storage=map=>({getItem:k=>map.has(k)?map.get(k):null,setItem:(k,v)=>map.set(k,String(v)),removeItem:k=>map.delete(k)});
 const document={getElementById:node,querySelectorAll:()=>[],querySelector:()=>null,events:{},addEventListener(type,fn){(this.events[type]??=[]).push(fn)},dispatchEvent(event){event.target??=this;if(event.type==='change'&&this.id==='absence-req-start')vm.runInContext('onAbsenceRequestStartChange()',ctx);for(const fn of this.events[event.type]||[])fn(event);if(event.bubbles){snapshots.push([node('absence-req-start').value,node('absence-req-end').value]);for(const fn of node('absence-request-modal').events[event.type]||[])fn(event);}},createElement:tag=>({...node('new-'+nodes.size),tagName:tag}),head:node('head'),body:node('body'),activeElement:null};
 ctx=vm.createContext({console,Date,Event:class{constructor(type,options={}){this.type=type;Object.assign(this,options)}},document,window:{addEventListener(){}},localStorage:storage(local),sessionStorage:storage(session),setTimeout:fn=>{fn();return 0},clearTimeout(){},setInterval:()=>0,clearInterval(){},requestAnimationFrame:fn=>fn(),confirm:()=>true,alert:()=>{throw Error('generic alert forbidden')}});
 vm.runInContext(base,ctx);const run=s=>vm.runInContext(s,ctx);
 run(`SYSTEM_MODE='VIEWER';SERVER_CONNECTED=true;dataLoaded=true;todayStr='2026-10-13';myName='A';
 memberList=[{name:'A',gender:'F'},{name:'B',gender:'F'},{name:'C',gender:'M'}];absenceTypes=['연차','교육'];
 closedMonths=[];absences=[];scheduleMap={'2026-10-14':[{name:'A'},{name:'B'}],'2026-10-15':[{name:'A'}]};
 showToast=()=>{};addNotif=()=>{};checkRequestOutcomes=()=>{};renderCalendar=()=>{};renderAbsenceList=()=>{};renderAbsenceTypeUI=()=>{};updateAbsenceDateLabels=()=>{};openVacancyModal=()=>{};offerUndo=()=>{};
 globalThis.sent=[];globalThis.response={ok:true,status:200,body:{receiptToken:'own-token',requestId:12}};
 fetch=async(url,options={})=>{sent.push({url,...options});return {ok:response.ok,status:response.status,json:async()=>response.body}};
 fetchWithAdminToken=async(url,options={})=>{sent.push({url,...options});return {ok:response.ok,status:response.status,json:async()=>response.body}};
 syncFullDataToBackend=async p=>{sent.push(JSON.parse(JSON.stringify(p)));return true};
 window.OnAirUI={escape:esc,dateLabel:x=>x,open:(id)=>{document.getElementById(id).classList.add('active')},close:id=>document.getElementById(id).classList.remove('active')};`);
 vm.runInContext(fs.readFileSync(path.join(root,'frontend/dist/usability_requests.js'),'utf8'),ctx);
 vm.runInContext(fs.readFileSync(path.join(root,'frontend/dist/absence_request_calendar.js'),'utf8'),ctx);document.elementFromPoint=()=>hit;
 const api=ctx.window.OnAirAbsenceRequestCalendar;return {run,node,ctx,local,session,api,day,snapshots,setHit:key=>{hit=key?day(key):null},pointer(type,key,more={}){hit=key?day(key):null;const event={type,target:hit||node('arc-calendar-grid'),pointerId:1,isPrimary:true,button:0,clientX:10,clientY:10,preventDefault(){},...more};node('arc-calendar-grid').dispatchEvent(event);return event;},json:s=>JSON.parse(run('JSON.stringify('+s+')'))};
}
const values=a=>[a.node('absence-req-start').value,a.node('absence-req-end').value];
const open=a=>a.run('openAbsenceRequestModal()');
test('viewer drag excludes weekends and custom holidays for vacation but preserves education',()=>{
 const a=app();open(a);a.node('absence-req-type').value='연차';a.run("finalHolidays={'2026-10-14':{name:'검수 공휴일'}}");
 a.pointer('pointerdown','2026-10-09');a.pointer('pointermove','2026-10-16');a.pointer('pointerup','2026-10-16');
 assert.deepEqual(values(a),['2026-10-09','2026-10-16']);assert.match(a.node('arc-selection-caption').textContent,/휴가 5일 \/ 휴일 3일 제외/);
 for(const key of ['2026-10-10','2026-10-11','2026-10-14'])assert.match(a.node('arc-calendar-grid').innerHTML,new RegExp(`class="[^"]*excluded[^\"]*" data-arc-date="${key}"[^>]+aria-pressed="false"`));
 a.node('absence-req-type').value='교육';a.node('absence-request-modal').dispatchEvent({type:'change',target:a.node('absence-req-type')});assert.doesNotMatch(a.node('arc-calendar-grid').innerHTML,/excluded/);assert.equal(a.node('arc-selection-caption').textContent,'신청 기간');
});
test('viewer weekend-only vacation never submits and keeps its selected draft',async()=>{
 const a=app();open(a);a.node('absence-req-type').value='연차';a.pointer('pointerdown','2026-10-10');a.pointer('pointermove','2026-10-11');a.pointer('pointerup','2026-10-11');await a.run('submitAbsenceRequest()');assert.equal(a.node('absence-req-start')['aria-invalid'],'true');assert.equal(a.json('sent').filter(x=>x.method==='POST').length,0);assert.deepEqual(values(a),['2026-10-10','2026-10-11']);
});
test('opening uses the final default dates and does not change main state or source data',()=>{
 const a=app(),before=a.json('[curYear,curMonth,scheduleMap,absences]');open(a);
 assert.equal(a.api.state.year,2026);assert.equal(a.api.state.month,10);assert.deepEqual(values(a),['2026-10-16','2026-10-16']);
 assert.equal(a.api.state.start,'2026-10-16');assert.deepEqual(a.json('[curYear,curMonth,scheduleMap,absences]'),before);assert.equal(a.json('sent').length,0);
});
test('first click sets a single day, second click normalizes a reversed interval, third starts again',()=>{
 const a=app();open(a);a.api.selectDate('2026-11-12');assert.deepEqual(values(a),['2026-11-12','2026-11-12']);assert.equal(a.api.state.anchor,'2026-11-12');
 a.api.selectDate('2026-11-09');assert.deepEqual(values(a),['2026-11-09','2026-11-12']);assert.equal(a.api.state.anchor,'');
 a.api.selectDate('2026-11-20');assert.deepEqual(values(a),['2026-11-20','2026-11-20']);
});
test('click range keeps its anchor while crossing months and years',()=>{
 const a=app();open(a);a.api.selectDate('2026-12-30');a.api.moveMonth(1);assert.equal(a.api.state.year,2027);assert.equal(a.api.state.anchor,'2026-12-30');a.api.selectDate('2027-01-03');
 assert.deepEqual(values(a),['2026-12-30','2027-01-03']);assert.equal(a.node('arc-selection-label').innerHTML.includes('2027'),true);
});
test('pointer dragging previews without mutation and commits both fields atomically on release',()=>{
 const a=app();open(a);const before=values(a);a.pointer('pointerdown','2026-10-18');a.pointer('pointermove','2026-10-23');assert.deepEqual(values(a),before);assert.equal(a.api.state.drag.end,'2026-10-23');
 const n=a.snapshots.length;a.pointer('pointerup','2026-10-23');assert.deepEqual(values(a),['2026-10-18','2026-10-23']);assert.equal(a.api.state.drag,null);assert.equal(a.api.state.anchor,'');
 assert.deepEqual(a.snapshots.slice(n),[['2026-10-18','2026-10-23'],['2026-10-18','2026-10-23']]);
});
test('reverse pointer dragging and release outside preserve the last valid calendar cell',()=>{
 const a=app();open(a);a.pointer('pointerdown','2026-10-23');a.pointer('pointermove','2026-10-18');a.pointer('pointermove',null);a.pointer('pointerup',null);
 assert.deepEqual(values(a),['2026-10-18','2026-10-23']);assert.equal(a.node('arc-calendar-grid').captured,null);
});
test('an outside release without a range cancels, while a dragged return to the anchor is a single-day range',()=>{
 const a=app();open(a);const before=values(a);a.pointer('pointerdown','2026-10-18');a.pointer('pointerup',null);assert.deepEqual(values(a),before);
 a.api.selectDate('2026-10-15');a.pointer('pointerdown','2026-10-18');a.pointer('pointermove','2026-10-20');a.pointer('pointermove','2026-10-18');a.pointer('pointerup','2026-10-18');assert.deepEqual(values(a),['2026-10-18','2026-10-18']);assert.equal(a.api.state.anchor,'');
});
test('pointer cancellation and explicit cancellation leave original dates and saved draft intact',()=>{
 const a=app();open(a);const before=values(a),draft=JSON.stringify([...a.session]);a.pointer('pointerdown','2026-10-18');a.pointer('pointermove','2026-10-23');a.pointer('pointercancel',null);
 assert.deepEqual(values(a),before);assert.equal(JSON.stringify([...a.session]),draft);assert.equal(a.api.state.drag,null);
 a.api.selectDate('2026-10-20');assert.equal(a.api.cancel(),true);assert.equal(a.api.state.anchor,'');assert.deepEqual(values(a),['2026-10-20','2026-10-20']);
});
test('secondary pointers are ignored and a synthesized pointer click is not applied twice',()=>{
 const a=app();open(a);const before=values(a);a.pointer('pointerdown','2026-10-18',{button:2});a.pointer('pointerup','2026-10-18',{button:2});assert.deepEqual(values(a),before);
 a.pointer('pointerdown','2026-10-18',{isPrimary:false});assert.equal(a.api.state.drag,null);
 a.pointer('pointerdown','2026-10-18');a.pointer('pointerup','2026-10-18');a.node('arc-calendar-grid').dispatchEvent({type:'click',detail:1,target:a.day('2026-10-18')});assert.equal(a.api.state.anchor,'2026-10-18');
 a.node('arc-calendar-grid').dispatchEvent({type:'click',detail:0,target:a.day('2026-10-21')});assert.deepEqual(values(a),['2026-10-18','2026-10-21']);
});
test('typed dates reset pending selection and update the highlight without rewriting invalid input',()=>{
 const a=app();open(a);a.api.selectDate('2026-10-18');a.node('absence-req-start').value='2028-02-29';a.node('absence-req-end').value='2028-03-02';a.api.sync('absence-req-start');
 assert.equal(a.api.state.anchor,'');assert.equal(a.api.state.year,2028);assert.equal(a.api.state.month,2);assert.equal(a.api.state.start,'2028-02-29');
 a.node('absence-req-start').value='';a.api.sync('absence-req-start');assert.equal(a.node('absence-req-start').value,'');assert.equal(a.api.state.start,'');
});
test('restored edit or amendment seeds retain all other fields and calendar opens at their range',()=>{
 const a=app();a.run(`window.OnAirRequests.openAbsence({requester_name:'B',absence_type:'교육',target_date:'2027-12-30',absence_end_date:'2028-01-03',reason:'원래 사유',absence_details:{title:'강의'}},{amendToken:'owner-token',original:{name:'B',type:'교육',start:'2027-12-30',end:'2028-01-03',education:{}}})`);
 assert.deepEqual(values(a),['2027-12-30','2028-01-03']);assert.equal(a.api.state.year,2027);assert.equal(a.api.state.month,12);a.api.selectDate('2028-01-04');a.api.selectDate('2028-01-05');
 assert.equal(a.node('absence-req-name').value,'B');assert.equal(a.node('absence-req-name').disabled,true);assert.equal(a.node('absence-req-type').value,'교육');assert.equal(a.node('absence-req-reason').value,'원래 사유');assert.equal(a.node('ur-req-edu-title').value,'강의');
 assert.equal(a.json('sent').length,0);
});
test('closing cancels transient selection and reopening restores committed draft dates',()=>{
 const a=app();open(a);a.api.selectDate('2026-11-09');a.api.selectDate('2026-11-12');a.pointer('pointerdown','2026-11-16');a.pointer('pointermove','2026-11-20');a.run('closeAbsenceRequestModal()');assert.equal(a.api.state.drag,null);assert.equal(a.api.state.anchor,'');open(a);
 assert.deepEqual(values(a),['2026-11-09','2026-11-12']);assert.equal(a.api.state.start,'2026-11-09');
});
test('changing applicant defaults syncs dates only before the user chooses their own range',()=>{
 const a=app();open(a);a.node('absence-req-name').value='C';a.node('absence-request-modal').dispatchEvent({type:'change',target:a.node('absence-req-name')});assert.deepEqual(values(a),['2026-10-14','2026-10-14']);assert.equal(a.api.state.start,'2026-10-14');
 a.api.selectDate('2026-10-20');a.node('absence-req-name').value='B';a.node('absence-request-modal').dispatchEvent({type:'change',target:a.node('absence-req-name')});assert.deepEqual(values(a),['2026-10-20','2026-10-20']);assert.equal(a.api.state.anchor,'');
});
test('date calculations include leap day, reject impossible dates and bound month navigation',()=>{
 const a=app();open(a);assert.equal(a.api._test.dateOf('2027-02-29'),null);assert.equal(a.api._test.dateOf('2028-02-29').getUTCDate(),29);
 const cells=a.api._test.calendarCells(2028,2);assert.equal(cells.length,42);assert.equal(cells.filter(x=>x.key==='2028-02-29').length,1);
 a.api.selectDate('1000-01-01');assert.equal(a.api.moveMonth(-1),false);a.api.close();a.api.selectDate('9999-12-31');assert.equal(a.api.moveMonth(1),false);
});
test('administrative mode and disabled fields cannot mutate request dates',()=>{
 const a=app();open(a);const before=values(a);a.run("SYSTEM_MODE='ADMIN'");assert.equal(a.api.selectDate('2026-11-09'),false);assert.deepEqual(values(a),before);a.run("SYSTEM_MODE='VIEWER'");a.node('absence-req-start').disabled=true;assert.equal(a.api.selectDate('2026-11-09'),false);
});
