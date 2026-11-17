'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const html = fs.readFileSync(path.resolve(__dirname,'../../frontend/dist/index.html'),'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(m=>m[1]).find(s=>s.includes('function renderTodayCard()'));
function app({absences=[], members=['김민수','이서연','박지훈'], mode='ADMIN', off=false}={}) {
  const nodes = new Map();
  const element = id => {
    if(!nodes.has(id)) nodes.set(id,{innerHTML:'',textContent:'',style:{setProperty:()=>{}},dataset:{},querySelectorAll:()=>[],clientWidth:220,clientHeight:100});
    return nodes.get(id);
  };
  const storage={getItem:()=>null,setItem:()=>{},removeItem:()=>{}};
  const context=vm.createContext({Date,console,sessionStorage:storage,localStorage:storage,
    document:{addEventListener:()=>{},getElementById:element},window:{addEventListener:()=>{}},
    setTimeout,clearTimeout,setInterval,clearInterval,requestAnimationFrame:()=>{}});
  vm.runInContext(script,context);
  const run=s=>vm.runInContext(s,context);
  run(`todayStr='2026-09-23';SYSTEM_MODE=${JSON.stringify(mode)};
    memberList=${JSON.stringify(members.map(name=>({name,gender:'F'})))};
    absences=${JSON.stringify(absences)};scheduleMap={'2026-09-23':[{name:'당직자',role:'당직'}]};
    noDutyMap=${off?"{'2026-09-23':true}":'{}'};
    renderWeekAbsenceBoard=()=>{};renderWeekMemoBoard=()=>{};scheduleDeskReflow=()=>{};`);
  return {run,element};
}
const record=(name,type,start='2026-09-23',end=start)=>({name,type,start,end});

// v3.18.0 '금일 휴가자' → '금일 휴가·부재'. 장기(14일 이상)만 빠지고, 단기 부재는 '부재' 꼬리표로 들어온다.
test('today card renders current registered members; long (>=14 days) absences stay out',()=>{
  const a=app({absences:[record('김민수','연차','2026-09-22','2026-09-23'),record('이서연','반차'),
    record('박지훈','파견','2026-09-01','2026-12-31'),record('김민수','휴가','2026-09-24'),record('퇴사자','연차')]});
  a.run('renderTodayCard()');
  assert.equal(a.element('today-vacation-count').textContent,'2명');
  const body=a.element('today-vacation-container').innerHTML;
  assert.match(body,/김민수/);assert.match(body,/이서연/);
  assert.doesNotMatch(body,/박지훈|퇴사자|파견/);
});
test('same person appears once with distinct current vacation types',()=>{
  const a=app({absences:[record('김민수','오전 반차'),record('김민수','오후 반차'),record('김민수','오전 반차')]});
  a.run('renderTodayCard()');
  assert.equal(a.element('today-vacation-count').textContent,'1명');
  assert.equal((a.element('today-vacation-container').innerHTML.match(/class="today-vacation-person[ "]/g)||[]).length,1);
  const rows=JSON.parse(a.run('JSON.stringify(todayVacationRows())'));
  assert.deepEqual(rows,[{name:'김민수',types:['오전 반차','오후 반차'],kind:'vacation'}]);
});
test('vacation panel remains visible on no-duty days',()=>{
  const a=app({off:true,absences:[record('김민수','연차')]});
  a.run('renderTodayCard()');
  assert.match(a.element('today-person-container').innerHTML,/당직이 없습니다/);
  assert.match(a.element('today-vacation-container').innerHTML,/김민수/);
});
test('empty vacation panel explicitly shows zero and no vacations',()=>{
  const a=app();a.run('renderTodayCard()');
  assert.equal(a.element('today-vacation-count').textContent,'0명');
  assert.match(a.element('today-vacation-container').innerHTML,/없습니다/);
});
test('viewer and admin show the same vacations without changing source records',()=>{
  const absences=[record('김민수','연차'),record('이서연','반차','2026-09-22','2026-09-24')];
  const renders=[];
  for(const mode of ['ADMIN','VIEWER']) {
    const a=app({mode,absences});const before=a.run('JSON.stringify(absences)');
    a.run('renderTodayCard()');
    assert.equal(a.run('JSON.stringify(absences)'),before);
    renders.push(a.element('today-vacation-container').innerHTML);
  }
  assert.equal(renders[0],renders[1]);
  assert.match(renders[0],/김민수/);
});
test('vacation names and types are HTML escaped',()=>{
  const name='<img src=x onerror=alert(1)>';
  const a=app({members:[name],absences:[record(name,'연차<script>')]});
  a.run('renderTodayCard()');
  const body=a.element('today-vacation-container').innerHTML;
  assert.doesNotMatch(body,/<img|<script>/);
  assert.match(body,/&lt;img/);
});
test('twenty to twenty-four names fit compact card geometry without hidden people',()=>{
  for(const count of [20,24]) for(const [width,height] of [[160,55],[180,70],[250,65],[300,110]]) {
    const a=app(); const list=a.element('today-vacation-container'), styles={};
    list.clientWidth=width;list.clientHeight=height;
    list.querySelectorAll=()=>Array(count).fill({});
    list.style.setProperty=(name,value)=>styles[name]=value;
    a.run('fitTodayVacations()');
    const cols=Number(styles['--vac-cols']),rows=Number(styles['--vac-rows']);
    const font=parseFloat(styles['--vac-font']),gap=parseFloat(styles['--vac-gap']);
    assert.ok(cols*rows>=count);
    assert.ok(font>=8,`${count} at ${width}x${height}: font ${font}`);
    assert.ok(font*1.1<=((height-(rows-1)*gap)/rows)+0.01);
    assert.ok(font*3+6<=((width-(cols-1)*gap)/cols)+0.01);
  }
});

// [위험] v3.18.0 '금일 휴가·부재' = 오늘 걸친 기록 중 장기(isLongAbsence: 기간 14일 이상)가 아닌 것 전부.
//   (3.17.x 의 '금일 휴가자'는 휴가 구분만 셌다 — 오늘 교육·출장·외근인 사람이 자리에 있는 것처럼 보였다.
//    이 파일의 예전 테스트 'short absences (교육/외근) are not vacations' 는 그 규칙을 고정하던 것이라
//    의도적으로 새 규칙으로 바꿨다.)
//   휴가 기록이 있으면 '휴가'(보라), 없으면 '부재'(주황). 기간이 긴 연차도 14일 이상이면 장기 칸으로 간다.
test('short absences (교육/외근/출장) are listed with the 부재 tag; a 92-day 연차 is long and excluded', ()=>{
  const a=app({absences:[record('김민수','교육'),record('이서연','외근'),record('정우진','출장','2026-09-22','2026-09-25'),
    record('박지훈','연차','2026-08-01','2026-10-31')],members:['김민수','이서연','박지훈','정우진']});
  a.run('renderTodayCard()');
  assert.deepEqual(JSON.parse(a.run('JSON.stringify(todayVacationRows())')),[
    {name:'김민수',types:['교육'],kind:'short'},{name:'이서연',types:['외근'],kind:'short'},{name:'정우진',types:['출장'],kind:'short'}]);
  assert.equal(a.element('today-vacation-count').textContent,'3명');
  const body=a.element('today-vacation-container').innerHTML;
  assert.match(body,/class="today-vacation-person kind-short"/);
  assert.match(body,/<b class="tv-tag">부재<\/b> 교육/);
  assert.doesNotMatch(body,/박지훈/);
});
test('vacation rows come first with the 휴가 tag; mixed vacation+absence person counts as 휴가', ()=>{
  const a=app({absences:[record('김민수','교육'),record('이서연','연차'),record('박지훈','교육'),record('박지훈','오후 반차')]});
  const rows=JSON.parse(a.run('JSON.stringify(todayVacationRows())'));
  assert.deepEqual(rows.map(r=>[r.name,r.kind]),[['박지훈','vacation'],['이서연','vacation'],['김민수','short']]);
  a.run('renderTodayCard()');
  const body=a.element('today-vacation-container').innerHTML;
  assert.match(body,/<b class="tv-tag">휴가<\/b> 연차/);
  assert.match(a.element('today-vacation-count').title,/휴가 2명 · 부재 1명/);
});
test('long-type names (육아휴가, 연수 휴가) are tagged 부재 when short and excluded when >= 14 days', ()=>{
  const a=app({absences:[record('김민수','육아휴가','2026-01-01','2026-12-31'),record('이서연','연수 휴가'),record('박지훈','병가')]});
  assert.equal(a.run("isVacationType('육아휴가')&&isLongAbsenceType('육아휴가')"),true,'fixture really exercises both clauses');
  assert.deepEqual(JSON.parse(a.run('JSON.stringify(todayVacationRows())')),
    [{name:'박지훈',types:['병가'],kind:'vacation'},{name:'이서연',types:['연수 휴가'],kind:'short'}]);
});
test('14-day boundary: 13 days is short (listed), 14 days is long (not listed)', ()=>{
  const a=app({absences:[record('김민수','파견','2026-09-11','2026-09-23'),record('이서연','파견','2026-09-10','2026-09-23'),
    record('박지훈','교육','2026-09-23','2026-10-06')]});
  assert.equal(a.run("absenceSpanDays({start:'2026-09-11',end:'2026-09-23'})"),13);
  assert.equal(a.run("absenceSpanDays({start:'2026-09-23',end:'2026-10-06'})"),14);
  assert.deepEqual(JSON.parse(a.run('JSON.stringify(todayVacationRows().map(r=>r.name))')),['김민수']);
});
test('the panel heading says 금일 휴가·부재 and the empty text matches', ()=>{
  const a=app();a.run('renderTodayCard()');
  assert.match(a.element('today-vacation-container').innerHTML,/금일 휴가·부재자가 없습니다/);
  assert.match(html,/<div class="today-vacation-heading"><span class="tv-title"><span class="tt-week">금일 <\/span>휴가·부재<\/span>/);
  assert.doesNotMatch(html,/<span>금일 휴가자<\/span>/);
});
test('vacation boundaries: starts today and ends today are included, yesterday/tomorrow are not', ()=>{
  const a=app({absences:[record('김민수','연차','2026-09-23','2026-09-25'),record('이서연','반차','2026-09-20','2026-09-23'),
    record('박지훈','연차','2026-09-22','2026-09-22'),record('박지훈','연차','2026-09-24','2026-09-24')]});
  assert.deepEqual(JSON.parse(a.run('JSON.stringify(todayVacationRows().map(r=>r.name))')).sort(),['김민수','이서연'].sort());
});
