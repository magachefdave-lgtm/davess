'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const html = fs.readFileSync(path.resolve(__dirname, '../../frontend/dist/index.html'), 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m => m[1]).find(s => s.includes('function renderTodayCard()'));
function app({mode = 'ADMIN', token = ''} = {}) {
  const calls = [], downloads = [], notices = [], toasts = [], attrs = new Map();
  const button = {innerHTML: '<svg></svg>서버에 백업 저장(JSON)', disabled: false,
    setAttribute: (k,v) => attrs.set(k,String(v)), getAttribute: k => attrs.get(k) ?? null, removeAttribute: k => attrs.delete(k)};
  const storage = {getItem: () => null, setItem() {}, removeItem() {}};
  const ctx = vm.createContext({console, Date, setTimeout, clearTimeout, setInterval, clearInterval,
    localStorage: storage, sessionStorage: storage, document: {addEventListener() {}, querySelector: () => button,
      getElementById: () => ({style:{}, classList:{add(){},remove(){},contains(){return false;}}})}});
  ctx.window = ctx;ctx.addEventListener = () => {};
  vm.runInContext(script,ctx);
  const run = code => vm.runInContext(code,ctx);
  run(`SYSTEM_MODE=${JSON.stringify(mode)};ADMIN_TOKEN=${JSON.stringify(token)};renderAuthStatus=()=>{};`);
  ctx.fetch = async (url, options) => { calls.push({url,options});return ctx.response; };
  ctx.downloadBlob = (...args) => downloads.push(args);
  ctx.showToast = (...args) => toasts.push(args);
  ctx.OnAirUI = {open: (id,title,body,options) => notices.push({id,title,body,options})};
  ctx.response = {ok:true,status:200,json:async()=>({ok:true,name:'smart_duty_backup_20261105_120000.json',path:'\\\\server\\공유\\back-up\\smart_duty_backup_20261105_120000.json',exportedAt:'2026-11-05T03:00:00Z',dataVersion:3,byteSize:123})};
  return {ctx,run,button,calls,downloads,notices,toasts,attrs};
}
test('backup posts only an authenticated request and shows the server path without downloading cached data', async () => {
  const a = app({token:'secret'});
  a.run("memberList=[{name:'오래된 브라우저 데이터'}];scheduleMap={};");
  assert.equal(await a.run('backupSystem()'), true);
  assert.equal(a.calls.length,1);assert.equal(a.calls[0].url,'/api/system/backup');
  assert.equal(a.calls[0].options.method,'POST');assert.equal(a.calls[0].options.headers['X-Admin-Token'],'secret');
  assert.equal(a.calls[0].options.body,undefined);assert.equal(a.downloads.length,0);
  assert.equal(a.notices.length,1);assert.match(a.notices[0].body,/back-up/);
  assert.match(a.notices[0].body,/readonly/);assert.match(a.notices[0].body,/smart_duty_backup_20261105_120000\.json/);
  assert.equal(a.button.disabled,false);assert.equal(a.attrs.has('aria-busy'),false);
});
test('duplicate clicks issue one request; all busy state is restored after completion', async () => {
  const a = app();let finish;
  a.ctx.fetch = async (url,options) => {a.calls.push({url,options});return await new Promise(r=>finish=r);};
  const pending=a.run('backupSystem()');assert.equal(a.button.disabled,true);assert.equal(a.attrs.get('aria-busy'),'true');
  assert.equal(await a.run('backupSystem()'),false);assert.equal(a.calls.length,1);
  finish(a.ctx.response);assert.equal(await pending,true);assert.equal(a.button.disabled,false);assert.equal(a.attrs.has('aria-busy'),false);
});
test('a viewer cannot start a backup even by invoking the function directly', async () => {
  const a=app({mode:'VIEWER'});assert.equal(await a.run('backupSystem()'),false);
  assert.equal(a.calls.length,0);assert.equal(a.downloads.length,0);assert.equal(a.notices.length,0);
});
test('authentication cancellation creates no success notice, restores the button and allows retry', async () => {
  const a=app();a.ctx.requestAdminPassword=async()=>null;
  a.ctx.response={ok:false,status:401,json:async()=>({detail:'관리자 인증 필요'})};
  assert.equal(await a.run('backupSystem()'),false);assert.equal(a.notices.length,0);assert.equal(a.button.disabled,false);
  a.ctx.response={ok:true,status:200,json:async()=>({ok:true,name:'retry.json',path:'/server/back-up/retry.json'})};
  assert.equal(await a.run('backupSystem()'),true);assert.equal(a.calls.length,2);assert.equal(a.downloads.length,0);
});
for(const status of [403,503])test(`server error ${status} shows the actual detail and never falls back to local download`,async()=>{
  const a=app();a.ctx.response={ok:false,status,json:async()=>({detail:'공유 폴더에 백업을 저장할 수 없습니다. 권한을 확인하세요.'})};
  assert.equal(await a.run('backupSystem()'),false);assert.equal(a.downloads.length,0);assert.equal(a.notices.length,0);
  assert.ok(a.toasts.some(x=>String(x[0]).includes('권한을 확인하세요')));assert.equal(a.button.disabled,false);assert.equal(a.attrs.has('aria-busy'),false);
});
test('network failure restores the button without claiming a successful save',async()=>{
  const a=app();a.ctx.fetch=async()=>{throw new TypeError('Failed to fetch');};
  assert.equal(await a.run('backupSystem()'),false);assert.equal(a.downloads.length,0);assert.equal(a.notices.length,0);assert.equal(a.button.disabled,false);
  assert.ok(a.toasts.some(x=>x[1]===true));
});
test('malformed success is not shown as a completed backup',async()=>{
  const a=app();a.ctx.response={ok:true,status:200,json:async()=>({ok:true})};
  assert.equal(await a.run('backupSystem()'),false);assert.equal(a.notices.length,0);assert.equal(a.downloads.length,0);assert.equal(a.button.disabled,false);
});
test('server path is displayed as inert selectable text',async()=>{
  const a=app();a.ctx.response={ok:true,status:200,json:async()=>({ok:true,name:'safe.json',path:'\\\\server\\공유 & <경로>\\back-up\\</textarea><img src=x onerror=alert(1)>.json'})};
  assert.equal(await a.run('backupSystem()'),true);const body=a.notices[0].body;
  assert.ok(body.includes('&amp;'));assert.ok(body.includes('&lt;경로&gt;'));assert.ok(body.includes('&lt;/textarea&gt;'));assert.ok(!body.includes('<img'));
});
