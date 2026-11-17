'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const {app,plain}=require('./helpers/scheduler.cjs');
function setup(token, failure){
 const run=app();
 run(`ADMIN_TOKEN=${JSON.stringify(token)};SYSTEM_MODE='ADMIN';SERVER_CONNECTED=true;CONN.server=true;CONN.shared=true;CONN.lastGoodAt=1;
  globalThis.calls=[];globalThis.notifications=[];globalThis.prompts=0;globalThis.down=0;
  document.getElementById=()=>({textContent:'0'});
  applyModeUI=()=>{};renderSysBanners=()=>{};updatePendingBadge=()=>{};markConnected=()=>{};markServerDown=()=>{down++};checkVersionSkew=()=>{};
  suspendAutoRefresh=true;window.OnAirRequestAccess={syncAdminNotifications:info=>notifications.push(info.requestRecovery)};
  prompt=()=>{prompts++;throw Error('unexpected prompt')};fetchWithAdminToken=()=>{prompts++;throw Error('unexpected auth flow')};
  fetch=async(url,options)=>{calls.push({url,options});${failure==='network'?"throw Error('offline');":failure==='http'?"return {ok:false,status:403,json:async()=>({detail:'denied'})};":`return {ok:true,json:async()=>({mode:'ADMIN',dbOk:true,pendingCount:0,requestRecovery:ADMIN_TOKEN?{datasetKey:'active:generation',pendingIds:['arrival-one']}:null})};`}}
 `);
 return run;
}
for(const method of ['fetchSystemInfo','pollSystemInfo']){
 for(const token of ['held-admin-token',''])test(`${method} sends only an existing token and forwards authorized recovery summary (${token?'authenticated':'anonymous'})`,async()=>{
  const run=setup(token);await run(`${method}()`);
  assert.deepEqual(plain(run('calls')),[{url:'/api/system/info',options:{headers:token?{'X-Admin-Token':token}:{}}}]);
  assert.deepEqual(plain(run('notifications')),token?[{datasetKey:'active:generation',pendingIds:['arrival-one']}]:[null]);
  assert.equal(run('prompts'),0);
 });
 for(const failure of ['network','http'])test(`${method} ${failure} failure never prompts or invents recovery notifications`,async()=>{
  const run=setup('held-admin-token',failure);await run(`${method}()`);
  assert.equal(run('calls.length'),1);assert.equal(run('prompts'),0);assert.deepEqual(plain(run('notifications')),[]);
  assert.equal(run('ADMIN_TOKEN'),'held-admin-token');
 });
}
