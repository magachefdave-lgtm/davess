'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {test}=require('node:test');
const html=fs.readFileSync(path.resolve(__dirname,'../../frontend/dist/index.html'),'utf8');
const script=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m=>m[1]).find(s=>s.includes('function renderTodayCard()'));
function app(type='파견') {
  const nodes=new Map();
  const el=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',innerHTML:'',disabled:false,style:{},dataset:{},options:[],classList:{add(){},remove(){}},focus(){},scrollIntoView(){},appendChild(o){this.options.push(o);},querySelectorAll(){return [];}});return nodes.get(id);};
  const storage={getItem:()=>null,setItem(){},removeItem(){}};
  const ctx=vm.createContext({console,Date,document:{getElementById:el,querySelectorAll:()=>[],addEventListener(){},createElement:()=>({})},window:{addEventListener(){}},localStorage:storage,sessionStorage:storage,setTimeout:()=>0,clearTimeout(){},setInterval:()=>0,clearInterval(){},requestAnimationFrame(){},confirm:()=>true,alert(){}});
  vm.runInContext(script,ctx);const run=s=>vm.runInContext(s,ctx);
  run(`SYSTEM_MODE='ADMIN';SERVER_CONNECTED=true;dataLoaded=true;todayStr='2026-09-28';
    absences=[{id:7,name:'A',type:${JSON.stringify(type)},start:'2026-09-01',end:'2026-09-30'},{id:8,name:'B',type:'연차',start:'2026-10-01',end:'2026-10-01'}];
    memberList=[{name:'A',gender:'M'},{name:'B',gender:'F'}];absenceTypes=['파견','모성보호','장기부재','연차'];
    scheduleMap={'2026-10-01':[{name:'A',role:'당직'},{name:'B',role:'당직'}],'2026-10-10':[{name:'A',role:'당직'}]};
    renderCalendar=()=>{};showToast=()=>{};openVacancyModal=()=>{};
    globalThis.sent=[];syncFullDataToBackend=async p=>{sent.push(JSON.parse(JSON.stringify(p)));return true;};`);
  run('beginAbsenceEdit(7)');
  return {el,run,json:s=>JSON.parse(run('JSON.stringify('+s+')'))};
}
test('editing existing absence unlocks both dates but keeps identity fixed',()=>{
  const a=app();assert.equal(a.el('abs-start').disabled,false);assert.equal(a.el('abs-end').disabled,false);assert.equal(a.el('abs-name').disabled,true);
});
for(const type of ['파견','모성보호','장기부재'])test(type+' extension saves same record and frees only newly covered duty',async()=>{
  const a=app(type);a.el('abs-end').value='2026-10-03';await a.run('saveAbsenceForm()');
  assert.equal(a.json('absences')[0].end,'2026-10-03');assert.equal(a.json('absences')[0].id,7);
  assert.deepEqual(a.json('scheduleMap["2026-10-01"]'),[{name:'B',role:'당직'}]);
  assert.deepEqual(a.json('scheduleMap["2026-10-10"]'),[{name:'A',role:'당직'}]);
  assert.equal(a.json('sent')[0].absences[1].id,8);assert.ok(a.json('sent')[0].schedules);
});
test('shortening a period does not invent or restore past duty',async()=>{
  const a=app();const before=a.json('scheduleMap');a.el('abs-start').value='2026-09-05';a.el('abs-end').value='2026-09-20';await a.run('saveAbsenceForm()');
  assert.equal(a.json('absences')[0].start,'2026-09-05');assert.deepEqual(a.json('scheduleMap'),before);
});
test('type-only correction preserves existing duty even inside old absence',async()=>{
  const a=app();a.run("scheduleMap['2026-09-15']=[{name:'A',role:'당직'}]");const before=a.json('scheduleMap');a.el('abs-type').value='모성보호';await a.run('saveAbsenceForm()');assert.deepEqual(a.json('scheduleMap'),before);assert.equal(a.json('absences')[0].type,'모성보호');
});
test('invalid or reversed dates do not mutate or save',async()=>{
  for(const end of ['', '2026-08-31','2026-02-30']) {const a=app();const before=a.json('absences');a.el('abs-end').value=end;await a.run('saveAbsenceForm()');assert.deepEqual(a.json('absences'),before);assert.equal(a.json('sent').length,0);}
});
test('cancel confirmation leaves both records and duties intact',async()=>{
  const a=app();a.run('confirm=()=>false');a.el('abs-end').value='2026-10-03';await a.run('saveAbsenceForm()');assert.equal(a.json('absences')[0].end,'2026-09-30');assert.equal(a.json('sent').length,0);
});
test('failed save restores period and freed duty together',async()=>{
  const a=app();const records=a.json('absences'),duties=a.json('scheduleMap');a.run('syncFullDataToBackend=async()=>false');a.el('abs-end').value='2026-10-03';await a.run('saveAbsenceForm()');assert.deepEqual(a.json('absences'),records);assert.deepEqual(a.json('scheduleMap'),duties);
});
test('stale editor cannot overwrite a record refreshed from another PC',async()=>{
  const a=app();a.run("absences[0].end='2026-12-31'");a.el('abs-end').value='2026-10-03';await a.run('saveAbsenceForm()');assert.equal(a.json('absences')[0].end,'2026-12-31');assert.equal(a.json('sent').length,0);
});
test('viewer cannot mutate absence from editor',async()=>{
  const a=app();a.run("SYSTEM_MODE='VIEWER'");a.el('abs-type').value='모성보호';a.el('abs-end').value='2026-10-03';await a.run('saveAbsenceForm()');assert.equal(a.json('absences')[0].type,'파견');assert.equal(a.json('sent').length,0);
});
test('pending save blocks repeated submission',async()=>{
  const a=app();a.run('syncFullDataToBackend=p=>new Promise(resolve=>{sent.push(p);globalThis.finishSave=resolve;})');a.el('abs-end').value='2026-10-03';const first=a.run('saveAbsenceForm()');await a.run('saveAbsenceForm()');a.run('finishSave(true)');await first;assert.equal(a.json('sent').length,1);
});
