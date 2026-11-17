'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const code = fs.readFileSync(path.resolve(__dirname, '../../frontend/dist/update_status.js'), 'utf8');
function harness({supported=true, confirm=true, healthVersions=[], reconnectUnauthorized=null}={}) {
  const actions = {children:[], appendChild(el){this.children.push(el);}, parentNode:{insertBefore(){}}};
  const out = {style:{}, textContent:''};
  const box = {isConnected:true, querySelector(id){return id==='#update-actions'?actions:id==='#update-action-result'?out:null;}};
  const data = {local:true, managed:true, mode:'ADMIN', version:'3.18.27', status:null,
    manual:{result:'available', offer_id:'offer', instance_id:'old', available_version:'3.18.28', restart_supported:supported}};
  const calls=[]; let restarted=false, reloads=0, healthCalls=0, clock=0;
  const window={confirm:()=>confirm, location:{reload(){reloads++;box.isConnected=false;}}};
  const context = vm.createContext({window, document:{getElementById(id){return id==='serverinfo-update'?box:null;},
    createElement(){return {style:{},setAttribute(){}};}}, localStorage:{getItem(){return null;}},
    Date:class extends Date {static now(){return clock;}},
    setTimeout(fn,ms){clock+=ms;queueMicrotask(fn);},
    fetch:async(url,options={})=>{
      calls.push({url,options});
      if(options.method==='POST') {restarted=true;return {ok:true,json:async()=>({operation:'operation',message:'preparing'})};}
      if(restarted && ((reconnectUnauthorized==='status' && url.includes('?operation=')) || (reconnectUnauthorized==='health' && url==='/api/system/health'))) {
        if(url==='/api/system/health') healthCalls++;
        return {ok:false,status:401,json:async()=>({detail:'관리자 암호가 필요합니다.'})};
      }
      if(url==='/api/system/health') {healthCalls++;return {ok:true,json:async()=>({mode:'ADMIN',version:healthVersions.shift()||'3.18.28',instance_id:'new',port:8000})};}
      return {ok:true,json:async()=>restarted?{...data,manual:{operation:'operation',result:'updated',current_version:'3.18.28',new_instance_id:'new',port:8000}}:data};
    }});
  vm.runInContext(code,context);
  return {window,actions,out,calls,get reloads(){return reloads;},get healthCalls(){return healthCalls;},
    render:()=>window.renderUpdateStatus(),button:label=>actions.children.find(b=>b.textContent===label)};
}
test('both controls appear; unsupported source execution disables only restart', async()=>{
  const h=harness({supported:false});await h.render();
  assert.equal(h.button('최신 버전 확인').disabled,false);
  assert.equal(h.button('업데이트 후 다시 시작').disabled,true);
});
test('cancel confirmation sends no restart request',async()=>{
  const h=harness({confirm:false});await h.render();
  await h.button('업데이트 후 다시 시작').onclick();
  assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);
  assert.equal(h.button('업데이트 후 다시 시작').disabled,false);
});
test('double click submits once and reload requires actual version/new instance',async()=>{
  const h=harness({healthVersions:['3.18.27','3.18.28']});await h.render();
  const b=h.button('업데이트 후 다시 시작');
  await Promise.all([b.onclick(),b.onclick()]);
  const posts=h.calls.filter(c=>c.options.method==='POST');
  assert.equal(posts.length,1);
  assert.deepEqual(JSON.parse(posts[0].options.body),{action:'prepare_restart',offer_id:'offer',instance_id:'old'});
  assert.equal(h.healthCalls,2);assert.equal(h.reloads,1);
  assert.ok(h.calls.some(c=>c.url.includes('?operation=operation')));
});

for (const endpoint of ['status','health']) {
  test('changed password during '+endpoint+' reconnect stops polling with reauthentication guidance',async()=>{
    const h=harness({reconnectUnauthorized:endpoint});await h.render();
    await h.button('업데이트 후 다시 시작').onclick();
    assert.match(h.out.textContent,/관리자 암호/);
    assert.match(h.out.textContent,/연결 정보를 다시 열어 인증/);
    assert.equal(h.calls.filter(c=>c.url.includes('?operation=')).length,1);
    assert.equal(h.healthCalls,endpoint==='health'?1:0);
    assert.equal(h.reloads,0);
    assert.equal(h.button('업데이트 후 다시 시작').disabled,true);
  });
}
