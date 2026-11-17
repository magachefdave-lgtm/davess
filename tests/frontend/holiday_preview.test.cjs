'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=process.env.ONAIR_HOLIDAY_PREVIEW_TEST_SOURCE||path.resolve(__dirname,'../..');
const html=fs.readFileSync(path.join(root,'frontend/dist/index.html'),'utf8');
const main=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m=>m[1]).find(s=>s.includes('function renderTodayCard()'));
function app(){
 const nodes=new Map(),doc={activeElement:null};
 function node(id){
  if(nodes.has(id))return nodes.get(id);
  const classes=new Set();const n={id,value:'',innerHTML:'',textContent:'',dataset:{},style:{},disabled:false,hidden:false,isConnected:true,tabIndex:0,attributes:{},events:{},options:[],
   classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle(x,v){v?classes.add(x):classes.delete(x)}},
   setAttribute(k,v){this.attributes[k]=String(v)},getAttribute(k){return this.attributes[k]},addEventListener(k,f){(this.events[k]??=[]).push(f)},querySelectorAll(){return []},querySelector(){return null},getClientRects(){return [1]},focus(){doc.activeElement=this},contains(x){return x?.id.startsWith('hp-')||x?.id==='preview-year'},appendChild(){}};
  nodes.set(id,n);return n;
 }
 Object.assign(doc,{getElementById:node,querySelectorAll:()=>[],querySelector:()=>null,addEventListener(){},body:node('body')});doc.activeElement=doc.body;
 const storage={getItem:()=>null,setItem(){},removeItem(){}};
 const ctx=vm.createContext({console,Date,document:doc,window:{innerHeight:1080,addEventListener(){},OnAirUI:{raise(){}}},localStorage:storage,sessionStorage:storage,setTimeout:()=>0,clearTimeout(){},setInterval:()=>0,clearInterval(){},requestAnimationFrame(){},confirm:()=>true,alert(){}});
 vm.runInContext(main,ctx);const run=s=>vm.runInContext(s,ctx);
 run(`SYSTEM_MODE='ADMIN';SERVER_CONNECTED=true;dataLoaded=true;dataLoadError='';todayStr='2026-11-05';curYear=2026;curMonth=11;
 memberList=[{name:'김민수',gender:'M'},{name:'이서연',gender:'F'}];
 globalThis.testHolidays={'2026-01-01':{name:'신정'},'2026-02-17':{name:'설날',isMajor:true},'2026-11-09':{name:'시험 공휴일'},'2026-11-10':{name:'휴무일'},'2026-12-25':{name:'성탄절'},'2027-01-01':{name:'신정'}};
 mergeHolidays=()=>{finalHolidays={...testHolidays}};mergeHolidays();
 scheduleMap={'2026-01-01':[{name:'김민수'}],'2026-02-17':[{name:'김민수'}],'2026-11-09':[{name:'김민수'},{name:'이서연'}],'2026-11-10':[{name:'이서연'}]};noDutyMap={'2026-11-10':true};
 globalThis.edits=[];openPreviewDate=k=>{edits.push(k)};applyModeUI=()=>{};renderConnState=()=>{};fetchAllData=async()=>true;`);
 node('hp-month').value='all';node('preview-year').value='2026';
 vm.runInContext(fs.readFileSync(path.join(root,'frontend/dist/holiday_preview.js'),'utf8'),ctx);
 return {node,doc,run,ctx,api:ctx.window.OnAirHolidayPreview,json:s=>JSON.parse(run('JSON.stringify('+s+')'))};
}
test('opening selects an upcoming holiday and only changes local preview state',()=>{
 const a=app(),snapshot=a.json('[curYear,curMonth,scheduleMap,noDutyMap,customHolidays]');a.run('openPreviewModal()');
 assert.equal(a.api.state.selected,'2026-11-09');assert.equal(a.api.state.month,11);assert.equal(a.doc.activeElement.id,'hp-search');
 assert.deepEqual(a.json('[curYear,curMonth,scheduleMap,noDutyMap,customHolidays]'),snapshot);assert.match(a.node('hp-selected-name').textContent,/시험 공휴일/);
});
test('explicit no-duty and main festival days are off even when stale workers exist',()=>{
 const a=app();a.run('openPreviewModal()');const rows=a.api.rows();
 assert.equal(rows.find(r=>r.key==='2026-02-17').status,'off');assert.equal(rows.find(r=>r.key==='2026-11-10').status,'off');
 assert.equal(rows.find(r=>r.key==='2026-11-09').status,'assigned');assert.equal(rows.find(r=>r.key==='2026-12-25').status,'missing');
 a.api.select('2026-11-10');assert.doesNotMatch(a.node('hp-selected-workers').innerHTML,/이서연/);
});
test('search, month and status filters compose without hiding inaccessible pages',()=>{
 const a=app();a.run('openPreviewModal()');a.node('hp-search').value='김민수';a.api.filterChanged();
 assert.deepEqual(Array.from(a.api.filtered(),r=>r.key),['2026-01-01','2026-11-09']);
 a.node('hp-search').value='';a.node('hp-month').value='11';a.api.filterChanged('month');a.api.setFilter('off');
 assert.deepEqual(Array.from(a.api.filtered(),r=>r.key),['2026-11-10']);assert.equal(a.node('hp-count-all').textContent,'2');
});
test('calendar selection reveals a filtered-out holiday and explains the reset',()=>{
 const a=app();a.run('openPreviewModal()');a.node('hp-search').value='없는 이름';a.api.filterChanged();a.api.setFilter('missing');
 a.api.select('2026-11-09','calendar');assert.equal(a.node('hp-search').value,'');assert.equal(a.api.state.filter,'all');
 assert.match(a.node('hp-selection-note').textContent,/검색·필터/);assert.match(a.node('prev-tbody').innerHTML,/data-hp-row="2026-11-09"/);
});
test('selecting an ordinary calendar day gives a read-only nonholiday detail',()=>{
 const a=app();a.run('openPreviewModal()');const before=a.json('[scheduleMap,noDutyMap,customHolidays,curYear,curMonth]');
 a.api.select('2026-11-12','calendar');assert.equal(a.node('hp-selected-name').textContent,'공휴일이 아닙니다.');
 assert.deepEqual(a.json('[scheduleMap,noDutyMap,customHolidays,curYear,curMonth]'),before);
});
test('calendar navigation crosses years locally and honors supported year limits',()=>{
 const a=app();a.run('openPreviewModal()');a.api.select('2026-12-25');a.api.moveMonth(1);
 assert.equal(a.api.state.year,2027);assert.equal(a.api.state.month,1);assert.equal(a.node('preview-year').value,'2027');assert.equal(a.run('curYear'),2026);
 a.api.moveMonth(-1);assert.equal(a.api.state.year,2026);assert.equal(a.api.state.month,12);
 assert.equal(a.api.select('0999-01-01','calendar'),false);assert.equal(a.api.select('2026-02-30','calendar'),false);
});
test('calendar produces complete leap-day grid with selected and today labels',()=>{
 const a=app();const cells=a.api._test.calendarCells(2028,2);assert.equal(cells.length,42);assert.equal(cells.filter(c=>c.key==='2028-02-29').length,1);
 a.run('openPreviewModal()');assert.match(a.node('hp-calendar-grid').innerHTML,/aria-current="date"/);assert.match(a.node('hp-calendar-grid').innerHTML,/data-hp-date="2026-11-09"[^>]*aria-pressed="true"/);
});
test('pagination covers every annual row and clamps after filtering',()=>{
 const a=app();a.run(`testHolidays={};for(let i=1;i<=23;i++)testHolidays['2026-12-'+String(i).padStart(2,'0')]={name:'공휴일 '+i}`);a.run('openPreviewModal()');
 const seen=new Set();for(let i=0;i<3;i++){a.api.setPage(i);for(const m of a.node('prev-tbody').innerHTML.matchAll(/data-hp-row="([^"]+)"/g))seen.add(m[1])}assert.equal(seen.size,23);
 a.node('hp-search').value='공휴일 23';a.api.filterChanged();assert.equal(a.api.state.page,0);assert.match(a.node('hp-range').textContent,/1.*1.*1/);
});
test('live fetch refreshes selected names and status without changing source records',async()=>{
 const a=app();a.run('openPreviewModal()');a.api.select('2026-11-09');
 a.run("testHolidays['2026-11-09'].name='변경된 휴일';noDutyMap['2026-11-09']=true");await a.run('fetchAllData()');
 assert.equal(a.node('hp-selected-name').textContent,'변경된 휴일');assert.equal(a.node('hp-selected-status').textContent,'휴무');
 a.run("delete testHolidays['2026-11-09']");await a.run('fetchAllData()');assert.equal(a.node('hp-selected-name').textContent,'공휴일이 아닙니다.');
});
test('HTML is escaped in holiday and person labels',()=>{
 const a=app();a.run(`testHolidays['2026-11-09'].name='<img src=x>';scheduleMap['2026-11-09']=[{name:'<script>x</script>'}]`);a.run('openPreviewModal()');
 assert.match(a.node('prev-tbody').innerHTML,/&lt;img/);assert.match(a.node('prev-tbody').innerHTML,/&lt;script/);assert.doesNotMatch(a.node('prev-tbody').innerHTML,/<img|<script/);
});
test('edit reuses legacy date editor only in admin mode and only for current holidays',()=>{
 const a=app();a.run('openPreviewModal()');assert.equal(a.api.edit('2026-11-09'),true);assert.deepEqual(a.json('edits'),['2026-11-09']);
 a.run("SYSTEM_MODE='VIEWER'");a.api.render();assert.equal(a.api.edit('2026-12-25'),false);assert.doesNotMatch(a.node('prev-tbody').innerHTML,/data-hp-edit=/);
 a.run("SYSTEM_MODE='ADMIN'");assert.equal(a.api.edit('2026-11-12'),false);
});
test('upcoming navigation has a defined no-future state and close returns focus',()=>{
 const a=app();const trigger=a.node('open-button');trigger.focus();a.run('openPreviewModal()');a.run("todayStr='2026-12-31'");a.api.render();
 assert.equal(a.node('hp-jump').disabled,true);const before=a.api.state.selected;assert.equal(a.api.jumpUpcoming(),false);assert.equal(a.api.state.selected,before);
 a.run('closePreviewModal()');assert.equal(a.doc.activeElement,trigger);
});
test('unloaded duty data shows a loading/error state instead of claiming unassigned',()=>{
 const a=app();a.run('dataLoaded=false;openPreviewModal()');assert.match(a.node('prev-tbody').innerHTML,/불러오는 중/);assert.doesNotMatch(a.node('prev-tbody').innerHTML,/data-hp-edit=/);
 a.run("dataLoadError='오류'");a.api.render();assert.match(a.node('prev-tbody').innerHTML,/불러오지 못/);
});
