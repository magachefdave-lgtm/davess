'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=process.env.ONAIR_ABSENCE_TEST_SOURCE||path.resolve(__dirname,'../..');
const html=fs.readFileSync(path.join(root,'frontend/dist/index.html'),'utf8');
const main=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m=>m[1]).find(s=>s.includes('function renderTodayCard()'));

function app(){
 const nodes=new Map();let rows=[],buttons=[],longRows=[],longButtons=[],generation=0;
 const doc={activeElement:null};
 function node(id){
  if(nodes.has(id))return nodes.get(id);
  const classes=new Set(),el={id,value:'',textContent:'',dataset:{},disabled:false,hidden:false,isConnected:true,style:{},options:[],attributes:{},events:{},tabIndex:0,
   classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle(x,on){on?classes.add(x):classes.delete(x)}},
   setAttribute(k,v){this.attributes[k]=String(v)},getAttribute(k){return this.attributes[k]},addEventListener(k,f){(this.events[k]??=[]).push(f)},
   appendChild(x){this.options.push(x);return x},querySelectorAll(){return []},querySelector(){return null},focus(){doc.activeElement=this},getClientRects(){return [1]},scrollIntoView(){}};
  let markup='';Object.defineProperty(el,'innerHTML',{get:()=>markup,set(value){markup=value;if(!['abs-tbody','abs-long-list'].includes(id))return;
   const isLong=id==='abs-long-list';
   for(const old of isLong?longButtons:buttons)old.isConnected=false;
   const nextRows=[...markup.matchAll(/<(?:tr|article)[^>]*data-absence-id="(\d+)"/g)].map(m=>{const row=node(id+'-row-'+m[1]);row.dataset.absenceId=m[1];return row});
   generation++;
   const nextButtons=[...markup.matchAll(/<button[^>]*data-absence-preview="(\d+)"[^>]*data-preview-field="([^"]+)"/g)].map(m=>{const button=node('button-'+m[1]+'-'+m[2]+'-'+generation);button.dataset.absencePreview=m[1];button.dataset.previewField=m[2];return button});
   if(isLong){longRows=nextRows;longButtons=nextButtons;}else{rows=nextRows;buttons=nextButtons;}
  }});nodes.set(id,el);return el;
 }
 Object.assign(doc,{getElementById:node,querySelectorAll:selector=>{
  if(selector.includes('[data-absence-id]'))return [...(selector.includes('#abs-tbody')?rows:[]),...(selector.includes('#abs-long-list')?longRows:[])];
  if(selector.includes('[data-absence-preview]'))return [...(selector.includes('#abs-tbody')?buttons:[]),...(selector.includes('#abs-long-list')?longButtons:[])];
  return [];
 },querySelector:()=>null,addEventListener(){},createElement:()=>node('new-'+nodes.size),body:node('body')});doc.activeElement=doc.body;
 const storage={getItem:()=>null,setItem(){},removeItem(){}};
 const ctx=vm.createContext({console,Date,document:doc,window:{innerHeight:1080,addEventListener(){},OnAirUI:{raise(){}}},localStorage:storage,sessionStorage:storage,setTimeout:()=>0,clearTimeout(){},setInterval:()=>0,clearInterval(){},requestAnimationFrame(){},confirm:()=>true,alert(){}});
 vm.runInContext(main,ctx);const run=s=>vm.runInContext(s,ctx);
 run(`SYSTEM_MODE='ADMIN';SERVER_CONNECTED=true;dataLoaded=true;todayStr='2026-11-05';curYear=2026;curMonth=11;
 memberList=[{name:'A',gender:'M'},{name:'B',gender:'F'}];absenceTypes=['연차','교육'];
 absences=[{id:0,name:'A',type:'연차',start:'2026-11-09',end:'2026-11-10'},{id:2,name:'B',type:'교육',start:'2026-12-30',end:'2027-01-03'}];
 fetchAllData=async()=>{if(globalThis.nextAbsences)absences=nextAbsences;return true};
 renderCalendar=()=>{};showToast=()=>{};
 `);
 vm.runInContext(fs.readFileSync(path.join(root,'frontend/dist/absence_management.js'),'utf8'),ctx);
 const api=ctx.window.OnAirAbsenceManagement;
 node('absence-modal').classList.add('active');api.renderCalendarPreview();
 return {node,doc,run,api,ctx,buttons:()=>buttons,rows:()=>rows,longRows:()=>longRows,longButtons:()=>longButtons,json:s=>JSON.parse(run('JSON.stringify('+s+')'))};
}

test('short periods keep first year, cross-year keeps both, and same day appears once',()=>{
 const a=app();
 assert.equal(a.run("absenceManagementPeriod('2026-11-09','2026-11-10')"),'2026.11.09(월) ~ 11.10(화)');
 assert.equal(a.run("absenceManagementPeriod('2026-12-31','2027-01-02')"),'2026.12.31(목) ~ 2027.01.02(토)');
 assert.equal(a.run("absenceManagementPeriod('2026-11-09','2026-11-09')"),'2026.11.09(월)');
});
test('calendar picks a one-day draft then an inclusive range without changing stored records',()=>{
 const a=app();a.run('openAbsenceModal()');const before=a.json('[absences,scheduleMap]');
 assert.equal(a.api.pickCalendarDate('2026-11-16'),true);assert.equal(a.node('abs-start').value,'2026-11-16');assert.equal(a.node('abs-end').value,'2026-11-16');
 a.api.pickCalendarDate('2026-11-19');assert.equal(a.node('abs-start').value,'2026-11-16');assert.equal(a.node('abs-end').value,'2026-11-19');assert.deepEqual(a.json('[absences,scheduleMap]'),before);
 a.api.pickCalendarDate('2026-11-24');a.api.pickCalendarDate('2026-11-22');assert.equal(a.node('abs-start').value,'2026-11-22');assert.equal(a.node('abs-end').value,'2026-11-24');
});
test('calendar range survives month navigation and handles cross-year selection',()=>{
 const a=app();a.run('openAbsenceModal()');a.api.moveCalendarMonth(1);a.api.pickCalendarDate('2026-12-30');a.api.moveCalendarMonth(1);a.api.pickCalendarDate('2027-01-03');
 assert.equal(a.node('abs-start').value,'2026-12-30');assert.equal(a.node('abs-end').value,'2027-01-03');assert.equal(a.api.calendarState.month,1);assert.equal(a.run('curMonth'),11);
});
test('calendar ignores invalid, disabled, saving, hidden and viewer selections',()=>{
 const a=app();a.run('openAbsenceModal()');const before=a.node('abs-start').value;
 assert.equal(a.api.pickCalendarDate('2026-02-30'),false);
 for(const state of ['absenceFormSaving=true','absenceFormSaving=false;isSaving=true',"isSaving=false;SYSTEM_MODE='VIEWER'"]){a.run(state);assert.equal(a.api.pickCalendarDate('2026-11-20'),false);}
 a.run("SYSTEM_MODE='ADMIN'");a.node('abs-start').disabled=true;assert.equal(a.api.pickCalendarDate('2026-11-20'),false);a.node('abs-start').disabled=false;a.node('absence-modal').classList.remove('active');assert.equal(a.api.pickCalendarDate('2026-11-20'),false);assert.equal(a.node('abs-start').value,before);
});
test('each name/type/period is an independent keyboard preview control, with safe labels',()=>{
 const a=app();a.run("absences[0].name='<img>';renderAbsenceList()");
 assert.equal(a.buttons().length,6);assert.deepEqual(a.buttons().slice(0,3).map(b=>b.dataset.previewField),['name','type','period']);
 assert.match(a.node('abs-tbody').innerHTML,/&lt;img&gt;/);assert.doesNotMatch(a.node('abs-tbody').innerHTML,/<img>/);
 assert.match(a.node('abs-tbody').innerHTML,/aria-label="&lt;img&gt; 연차 .* 달력에서 보기"/);
});
test('opening begins with no selected record and does not move the main month',()=>{
 const a=app();a.run('openAbsenceModal()');assert.equal(a.api.calendarState.selectedId,null);assert.equal(a.api.calendarState.year,2026);assert.equal(a.api.calendarState.month,11);
 assert.match(a.node('abs-calendar-selection').innerHTML,/선택한 일정이 없습니다/);assert.equal(a.run('curMonth'),11);
 assert.equal(a.doc.activeElement.id,'abs-search');
});
test('preview changes only the selected range and leaves a dirty editor untouched',()=>{
 const a=app();a.run('renderAbsenceList();beginAbsenceEdit(2)');a.node('abs-end').value='2027-01-08';
 const before=a.json('[absences,scheduleMap,editingAbsenceOriginal]');a.api.preview(0);
 assert.equal(a.node('abs-end').value,'2027-01-08');assert.equal(a.run('editingAbsenceId'),2);
 assert.deepEqual(a.json('[absences,scheduleMap,editingAbsenceOriginal]'),before);assert.equal(a.api.calendarState.selectedId,0);
 assert.equal(a.buttons()[0].attributes['aria-pressed'],'true');assert.equal(a.run('curMonth'),11);
});
test('calendar highlights inclusive month/year range and start/end month navigation',()=>{
 const a=app();a.api.preview(2);assert.equal(a.api.calendarState.month,12);
 let cells=a.api._test.calendarCells(2026,12,a.json('absences[1]'));
 assert.deepEqual(Array.from(cells.filter(c=>c.inRange),c=>c.key),['2026-12-30','2026-12-31','2027-01-01','2027-01-02','2027-01-03']);
 assert.equal(cells.find(c=>c.key==='2026-12-30').start,true);assert.equal(cells.find(c=>c.key==='2027-01-03').end,true);
 a.api.jumpCalendar('end');assert.equal(a.api.calendarState.year,2027);assert.equal(a.api.calendarState.month,1);
 a.api.moveCalendarMonth(-1);assert.equal(a.api.calendarState.year,2026);assert.equal(a.api.calendarState.month,12);
 assert.equal(a.run('curYear'),2026);assert.equal(a.run('curMonth'),11);
});
test('leap day and single-day ranges are inclusive without adding neighboring dates',()=>{
 const a=app();const cells=a.api._test.calendarCells(2028,2,{start:'2028-02-29',end:'2028-02-29'});
 assert.equal(cells.filter(c=>c.inRange).length,1);const day=cells.find(c=>c.inRange);assert.equal(day.key,'2028-02-29');assert.equal(day.start,true);assert.equal(day.end,true);
 assert.equal(a.api._test.calendarDate('2027-02-29'),null);
});
test('filtered preview remains explicitly labeled, and a deleted selected record clears stale highlight',()=>{
 const a=app();a.run('renderAbsenceList()');a.api.preview(0);a.node('abs-search').value='B';a.run('renderAbsenceList()');
 assert.equal(a.api.calendarState.selectedId,0);assert.match(a.node('abs-calendar-note').textContent,/목록 페이지·검색 조건 밖/);
 a.run('absences=absences.filter(a=>a.id!==0);renderAbsenceList()');assert.equal(a.api.calendarState.selectedId,null);
 assert.doesNotMatch(a.node('abs-calendar-grid').innerHTML,/class="[^"]*in-range/);assert.equal(a.node('abs-calendar-start').disabled,true);
});
test('server refresh updates displayed range while preserving the unsaved editor',async()=>{
 const a=app();a.api.preview(0);a.node('abs-end').value='2026-12-12';
 a.run("globalThis.nextAbsences=[{id:0,name:'A',type:'연차',start:'2026-11-11',end:'2026-11-12'}]");await a.run('fetchAllData()');
 assert.match(a.node('abs-calendar-selection').innerHTML,/2026\.11\.11\(수\) ~ 2026\.11\.12\(목\)/);assert.equal(a.node('abs-end').value,'2026-12-12');
});
test('successful explicit editing syncs preview, but declined edit keeps the previous preview',()=>{
 const a=app();a.run('beginAbsenceEdit(2)');assert.equal(a.api.calendarState.selectedId,2);
 a.node('abs-end').value='2027-01-08';a.api.preview(0);a.run('confirm=()=>false;beginAbsenceEdit(2)');
 assert.equal(a.api.calendarState.selectedId,0);assert.equal(a.node('abs-end').value,'2027-01-08');
});
test('list rerender restores a preview button focus; removed buttons fall back to search',()=>{
 const a=app();a.run('renderAbsenceList()');a.buttons()[1].focus();a.run('renderAbsenceList()');
 assert.equal(a.doc.activeElement.dataset.absencePreview,'0');assert.equal(a.doc.activeElement.dataset.previewField,'type');assert.equal(a.doc.activeElement.isConnected,true);
 a.run('absences=absences.filter(a=>a.id!==0);renderAbsenceList()');assert.equal(a.doc.activeElement.id,'abs-search');
});
test('viewer cannot select an administrative preview and invalid IDs do nothing',()=>{
 const a=app();a.api.preview(0);a.run("SYSTEM_MODE='VIEWER'");assert.equal(a.api.preview(2),false);assert.equal(a.api.calendarState.selectedId,0);
 a.run("SYSTEM_MODE='ADMIN'");assert.equal(a.api.preview(999),false);assert.equal(a.api.calendarState.selectedId,0);
});

test('long sidebar follows 14-day threshold, upcoming ordering, paging and the active filter',()=>{
 const a=app();a.ctx.window.innerHeight=768;
 a.run(`absences=[
 {id:4,name:'D',type:'파견',start:'2027-01-01',end:'2027-01-14'},
 {id:1,name:'A',type:'연차',start:'2026-11-01',end:'2026-11-14'},
 {id:2,name:'B',type:'파견',start:'2026-11-06',end:'2026-11-18'},
 {id:3,name:'C',type:'교육',start:'2026-11-06',end:'2026-11-19'},
 {id:5,name:'E',type:'파견',start:'2026-10-01',end:'2026-10-30'}
 ];globalThis.beforeLong=JSON.stringify(absences);renderAbsenceList()`);
 assert.equal(a.node('abs-long-section').hidden,false);assert.equal(a.node('abs-long-count').textContent,'3건');
 assert.deepEqual(a.longRows().map(r=>r.dataset.absenceId),['1']);assert.deepEqual(a.rows().map(r=>r.dataset.absenceId),['2']);
 a.node('abs-long-next').events.click[0]();assert.deepEqual(a.longRows().map(r=>r.dataset.absenceId),['3']);
 a.node('abs-long-next').events.click[0]();assert.deepEqual(a.longRows().map(r=>r.dataset.absenceId),['4']);assert.equal(a.node('abs-long-next').disabled,true);
 a.ctx.window.innerHeight=1080;a.run('renderAbsenceList()');assert.equal(a.node('abs-long-page').textContent,'2 / 2');
 a.run("setAbsenceListFilter('all')");assert.equal(a.node('abs-long-section').hidden,true);assert.equal(a.rows().length,5);
 a.run("setAbsenceListFilter('active')");assert.deepEqual(a.longRows().map(r=>r.dataset.absenceId),['1','3']);
 assert.equal(a.run('JSON.stringify(absences)===beforeLong'),true);
});

test('long sidebar search, preview, keyboard focus and data refresh preserve drafts',async()=>{
 const a=app();a.ctx.window.innerHeight=768;
 a.run(`absences=[{id:0,name:'<A>',type:'파견',start:'2026-12-28',end:'2027-01-10'},{id:2,name:'B',type:'교육',start:'2026-11-06',end:'2026-11-19'}];renderAbsenceList()`);
 assert.deepEqual(a.longRows().map(r=>r.dataset.absenceId),['2']);
 a.node('abs-long-next').events.click[0]();a.longButtons()[0].focus();a.run('renderAbsenceList()');
 assert.equal(a.doc.activeElement.dataset.previewField,'long');assert.equal(a.doc.activeElement.isConnected,true);
 a.node('abs-end').value='2028-12-12';a.api.preview(0);
 assert.equal(a.api.calendarState.selectedId,0);assert.equal(a.longButtons()[0].attributes['aria-pressed'],'true');assert.equal(a.node('abs-end').value,'2028-12-12');
 assert.match(a.node('abs-long-list').innerHTML,/2026\.12\.28\(월\)/);assert.match(a.node('abs-long-list').innerHTML,/2027\.01\.10\(일\)/);assert.match(a.node('abs-long-list').innerHTML,/&lt;A&gt;/);
 assert.match(a.node('abs-long-list').innerHTML,/class="abs-long-line"><strong class="abs-long-person">&lt;A&gt;<\/strong><span class="abs-long-period" title="2026\.12\.28\(월\) ~ 2027\.01\.10\(일\)">26\.12\.28\(월\)~27\.01\.10\(일\)<\/span>/);
 a.node('abs-search').value='교육';a.run('renderAbsenceList()');assert.equal(a.node('abs-long-page').textContent,'1 / 1');assert.deepEqual(a.longRows().map(r=>r.dataset.absenceId),['2']);
 assert.match(a.node('abs-long-list').innerHTML,/class="abs-long-period" title="2026\.11\.06\(금\) ~ 2026\.11\.19\(목\)">11\/06\(금\)~11\/19\(목\)<\/span>/);
 a.node('abs-search').value='';a.run("globalThis.nextAbsences=[{id:0,name:'<A>',type:'파견',start:'2026-12-28',end:'2027-01-09'}]");await a.run('fetchAllData()');
 assert.equal(a.node('abs-long-section').hidden,true);assert.deepEqual(a.rows().map(r=>r.dataset.absenceId),['0']);assert.equal(a.node('abs-end').value,'2028-12-12');
});
