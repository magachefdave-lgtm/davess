'use strict';
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const htmlPath = process.env.ONAIR_FRONTEND || path.resolve(__dirname, '../../../frontend/dist/index.html');
const html = fs.readFileSync(htmlPath, 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(x => x[1]).find(s => s.includes('function friday21Of('));
function app() {
  const storage = {getItem: () => null, setItem: () => {}, removeItem: () => {}};
  const context = vm.createContext({Date, console, sessionStorage:storage, localStorage:storage,
    document:{addEventListener:()=>{}}, window:{addEventListener:()=>{}},
    setTimeout, clearTimeout, setInterval, clearInterval});
  vm.runInContext(script, context, {filename:htmlPath});
  const run = code => vm.runInContext(code, context);
  run(`SYSTEM_MODE='ADMIN'; memberList=[{id:1,name:'M1',gender:'M'},{id:2,name:'M2',gender:'M'},{id:3,name:'M3',gender:'M'},{id:4,name:'F1',gender:'F'},{id:5,name:'F2',gender:'F'},{id:6,name:'F3',gender:'F'}];
    dutyRule.rule21FridayOff=false; dutyRule.algoType='FIXED_ORDER';
    dutyRule.fixedOrders={weekday:['F1','F2','F3'],weekendM:['M1','M2','M3'],weekendF:['F1','F2','F3'],holidayM:['M3','M2','M1'],holidayF:['F3','F2','F1'],majorM:['M1','M2','M3'],majorF:['F1','F2','F3']};
    renderCalendar=()=>{}; showToast=(message,error)=>{globalThis.lastToast={message,error};};
    syncFullDataToBackend=async payload=>{globalThis.savedPayload=cloneJson(payload); return true;};
    mergeHolidays();`);
  return run;
}
const plain = x => JSON.parse(JSON.stringify(x));
async function generate(run, year, month, options={}) {
  run(`curYear=${year};curMonth=${month};rememberCommittedState();`);
  await run(`generateScheduleByRules(${JSON.stringify({silent:true,scope:'MONTH',...options})})`);
  return plain(run('scheduleMap'));
}
module.exports={app,plain,generate};
