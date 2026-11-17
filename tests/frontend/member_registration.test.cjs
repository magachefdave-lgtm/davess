'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(require('node:path').resolve(__dirname,'../../frontend/dist/index.html'),'utf8');
const code=html.match(/    async function addMember\(\) \{[\s\S]*?\n    \}/)[0];
function app(name,saving=async()=>false){
 const input={value:name,focus(){this.focused=true;}},notices=[],calls=[];
 const ctx={document:{getElementById:id=>id==='member-modal'?{classList:{contains:()=>true}}:input,querySelector:()=>({value:'M'})},memberList:[],Date,Array,showToast:m=>notices.push(m),josa:n=>n,todayStr:'2026-11-17',makeHistory:()=>({}),renderMemberModalList(){},syncFullDataToBackend:async payload=>{calls.push(payload);return saving();}};
 vm.createContext(ctx);vm.runInContext(code,ctx);return {ctx,input,notices,calls};
}
test('cancelled member authentication retains the typed name',async()=>{const a=app('남궁민수');await a.ctx.addMember();assert.equal(a.input.value,'남궁민수');});
test('a programmatic five-character name is rejected before a write',async()=>{const a=app('홍길동사원');await a.ctx.addMember();assert.equal(a.calls.length,0);assert.equal(a.ctx.memberList.length,0);assert.match(a.notices[0],/4자/);});
test('successful registration clears its input after the save completes',async()=>{let resolve;const a=app('남궁민수',()=>new Promise(r=>resolve=r));const task=a.ctx.addMember();assert.equal(a.input.value,'남궁민수');resolve(true);await task;assert.equal(a.input.value,'');});
test('save completion preserves a different next name typed while waiting',async()=>{let resolve;const a=app('남궁민수',()=>new Promise(r=>resolve=r));const task=a.ctx.addMember();a.input.value='이서연';resolve(true);await task;assert.equal(a.input.value,'이서연');});
