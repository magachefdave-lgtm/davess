'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const code=fs.readFileSync(path.resolve(__dirname,'../../frontend/dist/update_status.js'),'utf8');

function app(){
 const nodes=new Map(Object.entries({'set-port':{value:'18230'},'set-hosts':{value:''},'set-mirror':{value:''},'set-pass':{value:''},'set-pass-clear':{checked:false},'set-save':{disabled:false},'update-action-result':{style:{},textContent:''},'update-actions':{appendChild(){}}}));
 const box={isConnected:true,querySelector:selector=>nodes.get(selector.slice(1))||null};
 const data={mode:'ADMIN',version:'3.18.40',managed:false,status:null,settings:{server_port:18230,allowed_hosts:'',backup_mirror_dir:'',admin_password_set:true}};
 const posts=[],pending=[];
 const context=vm.createContext({window:{},document:{getElementById:id=>id==='serverinfo-update'?box:null,createElement:()=>({style:{},setAttribute(){}})},localStorage:{getItem:()=>null},fetch:async(url,options={})=>{
   if(options.method!=='POST')return {ok:true,json:async()=>structuredClone(data)};
   posts.push(JSON.parse(options.body));return new Promise(resolve=>pending.push((ok=true)=>resolve({ok,status:ok?200:503,json:async()=>ok?{message:'저장했습니다.'}:{detail:'저장하지 못했습니다.'}})));
 }});
 vm.runInContext(code,context);
 return {nodes,posts,pending,render:()=>context.window.renderUpdateStatus(),save:()=>nodes.get('set-save').onclick()};
}

test('same dialog saves A to B and back to A against the last successful submission',async()=>{
 const a=app();await a.render();a.nodes.get('set-port').value='18234';const first=a.save();a.pending.shift()();await first;
 a.nodes.get('set-port').value='18230';const second=a.save();
 assert.equal(a.posts.length,2,'returning to the initial value must send another save');
 assert.deepEqual(a.posts[1].settings,{server_port:'18230'});a.pending.shift()();await second;
 await a.save();assert.equal(a.posts.length,2);assert.equal(a.nodes.get('update-action-result').textContent,'바뀐 값이 없습니다.');
});

test('save locks concurrent submissions and preserves edits made while awaiting success',async()=>{
 const a=app();await a.render();a.nodes.get('set-port').value='18234';const first=a.save();
 assert.equal(a.nodes.get('set-save').disabled,true);
 a.nodes.get('set-port').value='18235';a.nodes.get('set-hosts').value='new-office';await a.save();
 assert.equal(a.posts.length,1);a.pending.shift()();await first;
 assert.equal(a.nodes.get('set-save').disabled,false);assert.equal(a.nodes.get('set-port').value,'18235');assert.equal(a.nodes.get('set-hosts').value,'new-office');
 const next=a.save();assert.deepEqual(a.posts[1].settings,{server_port:'18235',allowed_hosts:'new-office'});a.pending.shift()();await next;
});

test('failed save keeps the draft and comparison baseline available for retry',async()=>{
 const a=app();await a.render();a.nodes.get('set-port').value='18234';a.nodes.get('set-pass').value='draft-password';a.nodes.get('set-pass-clear').checked=true;
 const first=a.save();a.pending.shift()(false);await first;
 assert.equal(a.nodes.get('set-save').disabled,false);assert.equal(a.nodes.get('set-port').value,'18234');assert.equal(a.nodes.get('set-pass').value,'draft-password');assert.equal(a.nodes.get('set-pass-clear').checked,true);
 const retry=a.save();assert.deepEqual(a.posts[1].settings,a.posts[0].settings);a.pending.shift()();await retry;
});
