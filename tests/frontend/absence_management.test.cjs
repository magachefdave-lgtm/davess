'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {test}=require('node:test');
const source=process.env.ONAIR_ABSENCE_TEST_SOURCE || path.resolve(__dirname,'../..');
const html=fs.readFileSync(path.join(source,'frontend/dist/index.html'),'utf8');
const script=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m=>m[1]).find(s=>s.includes('function renderTodayCard()'));
function app(height=1080){
  const nodes=new Map();
  const el=id=>{if(!nodes.has(id)){const classes=new Set();nodes.set(id,{value:'',textContent:'',innerHTML:'',disabled:false,style:{},dataset:{},options:[],classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)},focus(){},scrollIntoView(){},appendChild(o){this.options.push(o);},querySelectorAll(){return [];}});}return nodes.get(id);};
  const filters=['all','active','past'].map(filter=>({dataset:{absenceFilter:filter},attrs:{},setAttribute(key,value){this.attrs[key]=value;}}));
  const storage={getItem:()=>null,setItem(){},removeItem(){}};
  const ctx=vm.createContext({console,Date,document:{getElementById:el,querySelectorAll:s=>s==='[data-absence-filter]'?filters:[],addEventListener(){},createElement:()=>({})},window:{innerHeight:height,addEventListener(){}},localStorage:storage,sessionStorage:storage,setTimeout:()=>0,clearTimeout(){},setInterval:()=>0,clearInterval(){},requestAnimationFrame(){},confirm:()=>true,alert(){}});
  vm.runInContext(script,ctx);const run=s=>vm.runInContext(s,ctx);
  run(`todayStr='2026-11-05';absences=Array.from({length:19},(_,i)=>({id:i+1,name:'사원'+(i+1),type:i%2?'교육':'연차',start:i<2?'2026-10-26':'2026-11-09',end:i<2?'2026-10-27':'2026-11-10'}));`);
  return {el,run,ctx,filters,rows:()=>[...el('abs-tbody').innerHTML.matchAll(/data-absence-id="(\d+)"/g)].map(m=>Number(m[1]))};
}
test('all pages expose upcoming rows before past records once and clamp both ends',()=>{
  const a=app();a.run("setAbsenceListFilter('all')");assert.deepEqual(a.rows(),[10,11,12,13,14,15,16,17]);
  assert.equal(a.el('abs-page-prev').disabled,true);assert.equal(a.el('abs-page-next').disabled,false);
  a.run('changeAbsenceListPage(1)');assert.deepEqual(a.rows(),[18,19,3,4,5,6,7,8]);
  a.run('changeAbsenceListPage(1)');assert.deepEqual(a.rows(),[9,1,2]);assert.equal(a.el('abs-page-next').disabled,true);
  a.run('changeAbsenceListPage(999)');assert.deepEqual(a.rows(),[9,1,2]);
  a.run('changeAbsenceListPage(-999)');assert.equal(a.el('abs-page-label').textContent,'1 / 3');
});
test('compact desktop page has seven records and resize retains accessible pages',()=>{
  const a=app(768);a.run("setAbsenceListFilter('all')");assert.deepEqual(a.rows(),[10,11,12,13,14,15,16]);
  a.run('changeAbsenceListPage(2)');assert.deepEqual(a.rows(),[7,8,9,1,2]);
  a.ctx.window.innerHeight=1080;a.run('renderAbsenceList()');assert.deepEqual(a.rows(),[9,1,2]);
});
test('search resets page while status filters combine with search without changing records',()=>{
  const a=app();a.run("setAbsenceListFilter('all');changeAbsenceListPage(2)");a.el('abs-search').value='교육';a.run('renderAbsenceList()');
  assert.deepEqual(a.rows(),[10,12,14,16,18,4,6,8]);assert.equal(a.el('abs-page-label').textContent,'1 / 2');
  a.run("setAbsenceListFilter('past')");assert.deepEqual(a.rows(),[2]);assert.equal(a.filters[2].attrs['aria-pressed'],'true');
  a.run("setAbsenceListFilter('active')");assert.deepEqual(a.rows(),[10,12,14,16,18,4,6,8]);
  assert.equal(a.run('absences.length'),19);
});
test('today is included in current/upcoming and yesterday in past; empty search has accurate controls',()=>{
  const a=app();a.run("absences=[{id:1,name:'오늘',type:'연차',start:todayStr,end:todayStr},{id:2,name:'어제',type:'연차',start:'2026-11-04',end:'2026-11-04'}];setAbsenceListFilter('active')");
  assert.deepEqual(a.rows(),[1]);a.run("setAbsenceListFilter('past')");assert.deepEqual(a.rows(),[2]);
  a.el('abs-search').value='없는 이름';a.run('renderAbsenceList()');assert.deepEqual(a.rows(),[]);assert.match(a.el('abs-tbody').innerHTML,/검색 결과가 없습니다/);
  assert.equal(a.el('abs-page-next').disabled,true);assert.equal(a.el('abs-page-prev').disabled,true);assert.equal(a.el('abs-page-label').textContent,'1 / 1');
});
test('record deletion on final page clamps to remaining records without losing selected highlight',()=>{
  const a=app();a.run("setAbsenceListFilter('all');changeAbsenceListPage(2);absences=absences.slice(0,16);editingAbsenceId=8;renderAbsenceList()");
  assert.deepEqual(a.rows(),[4,5,6,7,8,9,1,2]);assert.match(a.el('abs-tbody').innerHTML,/data-absence-id="8" class="abs-selected" aria-selected="true"/);
});
test('combined periods keep full years and weekdays; user values are escaped',()=>{
  const a=app();a.run("absences=[{id:1,name:'<img src=x>',type:'교육<script>',start:'2026-12-31',end:'2027-01-02'}];renderAbsenceList()");
  const rendered=a.el('abs-tbody').innerHTML;assert.match(rendered,/2026\.12\.31\(목\)/);assert.match(rendered,/2027\.01\.02\(토\)/);assert.match(rendered,/3일/);
  assert.match(rendered,/&lt;img src=x&gt;/);assert.match(rendered,/교육&lt;script&gt;/);assert.doesNotMatch(rendered,/<img|<script/);
});
test('selected id zero is highlighted only when explicitly editing it',()=>{
  const a=app();a.run("absences=[{id:0,name:'A',type:'연차',start:todayStr,end:todayStr}];editingAbsenceId=null;renderAbsenceList()");assert.doesNotMatch(a.el('abs-tbody').innerHTML,/aria-selected/);
  a.run('editingAbsenceId=0;renderAbsenceList()');assert.match(a.el('abs-tbody').innerHTML,/aria-selected="true"/);
});

test('upcoming sorts all matching rows before pagination and leaves stored order unchanged',()=>{
  const a=app(768);
  a.run(`absences=[
    {id:8,name:'아',type:'연차',start:'2026-11-20',end:'2026-11-20'},
    {id:5,name:'마',type:'연차',start:'2026-11-07',end:'2026-11-07'},
    {id:3,name:'다',type:'교육',start:'2026-11-05',end:'2026-11-06'},
    {id:9,name:'자',type:'교육',start:'2026-10-30',end:'2026-11-04'},
    {id:2,name:'나',type:'교육',start:'2026-11-03',end:'2026-11-08'},
    {id:7,name:'사',type:'연차',start:'2026-11-10',end:'2026-11-10'},
    {id:4,name:'라',type:'연차',start:'2026-11-06',end:'2026-11-06'},
    {id:1,name:'가',type:'교육',start:'2026-11-01',end:'2026-11-05'},
    {id:6,name:'바',type:'연차',start:'2026-11-08',end:'2026-11-08'}
  ];globalThis.beforeOrder=JSON.stringify(absences);setAbsenceListFilter('active')`);
  assert.deepEqual(a.rows(),[1,2,3,4,5,6,7]);
  assert.deepEqual([...a.el('abs-tbody').innerHTML.matchAll(/class="abs-dday abs-dday-[^"]+"[^>]*>([^<]+)</g)].map(m=>m[1]),['진행 중','진행 중','D-DAY','D-1','D-2','D-3','D-5']);
  a.run('changeAbsenceListPage(1)');assert.deepEqual(a.rows(),[8]);
  assert.equal(a.run('JSON.stringify(absences)===beforeOrder'),true);
  a.run("setAbsenceListFilter('all')");assert.deepEqual(a.rows(),[1,2,3,4,5,6,7]);
  a.run('changeAbsenceListPage(1)');assert.deepEqual(a.rows(),[8,9]);
  a.run("setAbsenceListFilter('past')");assert.deepEqual(a.rows(),[9]);
});

test('same start sorts by end, Korean name and record id consistently',()=>{
  const a=app();
  a.run(`absences=[
    {id:8,name:'김민수',type:'연차',start:'2026-11-06',end:'2026-11-07'},
    {id:2,name:'김민수',type:'연차',start:'2026-11-06',end:'2026-11-09'},
    {id:7,name:'이서연',type:'교육',start:'2026-11-06',end:'2026-11-07'},
    {id:3,name:'김민수',type:'연차',start:'2026-11-06',end:'2026-11-07'}
  ];setAbsenceListFilter('active')`);
  assert.deepEqual(a.rows(),[3,8,7,2]);
});

test('expected-start header, badges and empty span follow filter without changing day totals',()=>{
  const a=app();a.run("absences=[{id:1,name:'A',type:'교육',start:'2026-11-06',end:'2026-11-08'}];setAbsenceListFilter('active')");
  assert.equal(a.el('abs-col-upcoming').hidden,false);assert.equal(a.el('abs-upcoming-heading').hidden,false);
  assert.equal(a.el('absence-modal').classList.contains('abs-list-upcoming'),true);
  assert.match(a.el('abs-tbody').innerHTML,/<tr[^>]+><td class="abs-upcoming">/);
  assert.match(a.el('abs-tbody').innerHTML,/<td class="abs-days">3일<\/td>/);
  assert.equal(a.el('abs-list-sort-note').textContent,'오늘 기준 · 가까운 일정순');
  a.el('abs-search').value='없음';a.run('renderAbsenceList()');assert.match(a.el('abs-tbody').innerHTML,/colspan="6"/);
  a.run("setAbsenceListFilter('past')");assert.match(a.el('abs-tbody').innerHTML,/colspan="5"/);
  assert.equal(a.el('abs-col-upcoming').hidden,true);assert.equal(a.el('abs-upcoming-heading').hidden,true);
  a.el('abs-search').value='';a.run('renderAbsenceList()');assert.doesNotMatch(a.el('abs-tbody').innerHTML,/abs-dday|abs-upcoming/);
  a.run("setAbsenceListFilter('all')");assert.equal(a.el('abs-col-upcoming').hidden,false);assert.match(a.el('abs-tbody').innerHTML,/>D-1</);
  assert.match(html,/<th[^>]+class="abs-days-heading">총 일수<\/th>/);
});

test('countdown covers year, leap-day and daylight-saving calendar boundaries',()=>{
  const a=app();
  for(const [today,start,expected] of [['2026-12-31','2027-01-02','D-2'],['2028-02-28','2028-03-01','D-2'],['2027-03-13','2027-03-15','D-2'],['2026-11-05','2026-11-05','D-DAY']]){
    a.ctx.testToday=today;a.ctx.testStart=start;
    a.run('todayStr=testToday;absences=[{id:1,name:"A",type:"연차",start:testStart,end:testStart}];setAbsenceListFilter("active")');
    assert.match(a.el('abs-tbody').innerHTML,new RegExp('>'+expected+'<'));
  }
});

test('clock date change refreshes an open upcoming list without touching an editor draft',()=>{
  const a=app();a.el('absence-modal').classList.add('active');a.el('abs-end').value='2026-12-12';
  a.run(`absences=[{id:1,name:'A',type:'연차',start:'2026-11-05',end:'2026-11-05'},{id:2,name:'B',type:'교육',start:'2026-11-06',end:'2026-11-07'}];setAbsenceListFilter('active');renderCalendar=()=>{}`);
  assert.deepEqual(a.rows(),[1,2]);
  a.ctx.Date=class extends Date{constructor(...args){super(...(args.length?args:['2026-11-06T12:00:00Z']));}};
  a.run('startClock()');assert.deepEqual(a.rows(),[2]);assert.match(a.el('abs-tbody').innerHTML,/>D-DAY</);
  assert.equal(a.el('abs-end').value,'2026-12-12');
});

test('opening and reopening absence management always selects upcoming with cleared query and page',()=>{
  const a=app();assert.equal(a.run('absenceListFilter'),'active');
  a.el('abs-search').value='교육';
  a.run("SYSTEM_MODE='ADMIN';memberList=[];renderAbsenceTypeUI=()=>{};cancelAbsenceEdit=()=>{};updateAbsenceDateLabels=()=>{};absenceListFilter='all';absenceListPage=2;absenceListQuery='교육';openAbsenceModal()");
  assert.equal(a.run('absenceListFilter'),'active');assert.equal(a.run('absenceListPage'),0);
  assert.equal(a.el('abs-search').value,'');assert.equal(a.run('absenceListQuery'),'');
  assert.equal(a.filters.find(f=>f.dataset.absenceFilter==='active').attrs['aria-pressed'],'true');
  assert.deepEqual(a.rows(),[10,11,12,13,14,15,16,17]);
  a.run("setAbsenceListFilter('past');openAbsenceModal()");assert.equal(a.run('absenceListFilter'),'active');
});

test('all includes ongoing and today before future and past sorted by recent end and stable ties',()=>{
  const a=app();
  a.run(`absences=[
    {id:4,name:'가',type:'연차',start:'2026-11-01',end:'2026-11-04'},
    {id:8,name:'가',type:'교육',start:'2027-01-02',end:'2027-01-03'},
    {id:6,name:'가',type:'연차',start:'2026-10-28',end:'2026-10-30'},
    {id:3,name:'가',type:'교육',start:'2026-11-06',end:'2026-11-06'},
    {id:5,name:'가',type:'연차',start:'2026-11-03',end:'2026-11-04'},
    {id:2,name:'가',type:'교육',start:'2026-11-05',end:'2026-11-05'},
    {id:1,name:'가',type:'연차',start:'2026-11-01',end:'2026-11-05'},
    {id:7,name:'나',type:'연차',start:'2026-11-03',end:'2026-11-04'}
  ];globalThis.raw=JSON.stringify(absences);setAbsenceListFilter('all')`);
  assert.deepEqual(a.rows(),[1,2,3,8,5,7,4,6]);
  assert.equal(a.run('JSON.stringify(absences)===raw'),true);
  a.el('abs-search').value='연차';a.run('renderAbsenceList()');assert.deepEqual(a.rows(),[1,5,7,4,6]);
  a.run("setAbsenceListFilter('past')");assert.deepEqual(a.rows(),[4,6,5,7]);
  a.run("setAbsenceListFilter('all');absences=absences.filter(a=>a.end<todayStr);renderAbsenceList()");assert.deepEqual(a.rows(),[5,7,4,6]);
  a.el('abs-search').value='';a.run("setAbsenceListFilter('active')");assert.deepEqual(a.rows(),[]);assert.match(a.el('abs-tbody').innerHTML,/해당하는 부재 내역이 없습니다/);
});

test('all tab moves a finished row behind upcoming rows at midnight and retains the editor draft',()=>{
  const a=app();a.el('absence-modal').classList.add('active');a.el('abs-end').value='2026-12-12';
  a.run(`absences=[{id:3,name:'C',type:'연차',start:'2026-10-01',end:'2026-10-02'},{id:1,name:'A',type:'연차',start:'2026-11-05',end:'2026-11-05'},{id:2,name:'B',type:'교육',start:'2026-11-06',end:'2026-11-07'}];setAbsenceListFilter('all');renderCalendar=()=>{}`);
  assert.deepEqual(a.rows(),[1,2,3]);
  a.ctx.Date=class extends Date{constructor(...args){super(...(args.length?args:['2026-11-06T12:00:00Z']));}};
  a.run('startClock()');assert.deepEqual(a.rows(),[2,1,3]);
  assert.equal(a.el('abs-end').value,'2026-12-12');assert.equal(a.el('abs-col-upcoming').hidden,false);assert.match(a.el('abs-tbody').innerHTML,/>종료</);
});

test('active moves 14-day records out of the table while all retains long records and ended badges',()=>{
  const a=app();
  a.run(`absences=[
    {id:3,name:'지난',type:'파견',start:'2026-10-01',end:'2026-10-14'},
    {id:4,name:'미래',type:'교육',start:'2026-11-20',end:'2026-12-10'},
    {id:1,name:'단기',type:'파견',start:'2026-11-06',end:'2026-11-18'},
    {id:2,name:'장기',type:'연차',start:'2026-11-02',end:'2026-11-15'}
  ];globalThis.original=JSON.stringify(absences);setAbsenceListFilter('active')`);
  assert.deepEqual(a.rows(),[1]);assert.match(a.el('abs-list-count').textContent,/장기 2건/);
  a.run("setAbsenceListFilter('all')");assert.deepEqual(a.rows(),[2,1,4,3]);
  assert.match(a.el('abs-tbody').innerHTML,/<td class="abs-days">14일<\/td>/);
  assert.match(a.el('abs-tbody').innerHTML,/abs-dday-past[^>]*>종료</);
  assert.equal(a.run('JSON.stringify(absences)===original'),true);
  a.run("setAbsenceListFilter('past')");assert.deepEqual(a.rows(),[3]);assert.equal(a.el('abs-col-upcoming').hidden,true);
});

test('a long-only search points to the left list and does not hide results behind a misleading empty message',()=>{
  const a=app();
  a.run(`absences=[{id:1,name:'장기',type:'파견',start:'2026-11-06',end:'2026-11-19'}];setAbsenceListFilter('active')`);
  a.el('abs-search').value='파견';a.run('renderAbsenceList()');assert.deepEqual(a.rows(),[]);
  assert.match(a.el('abs-tbody').innerHTML,/장기 일정 1건은 왼쪽 목록/);
  assert.doesNotMatch(a.el('abs-tbody').innerHTML,/검색 결과가 없습니다/);
  a.el('abs-search').value='교육';a.run('renderAbsenceList()');assert.match(a.el('abs-tbody').innerHTML,/검색 결과가 없습니다/);
});
