'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.resolve(__dirname,'../../frontend/dist/request_access.js'),'utf8');
function app({managed=true,ticket,credentialError,profileError}={}) {
 const sent=[],storage=new Map();
 const context={window:{__onairAccessTicket:ticket},document:{getElementById:()=>null,addEventListener(){}},sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},Date,JSON,Object,Map,Set,Number,encodeURIComponent,crypto:require('node:crypto').webcrypto,TextEncoder,Uint8Array,setInterval(){},clearInterval(){},fetch:async(url,options={})=>{
  sent.push({url,...options});
  let value={},status=200;
  if(url.endsWith('/dinner-profile')){value=profileError||{managed};if(profileError)status=503;}
  if(url.endsWith('/session'))value={sessionToken:'private-session',expiresAt:Math.floor(Date.now()/1000)+1000};
  if(url.endsWith('/receipts/read'))value={receipts:[]};
  if(url.endsWith('/dinner-credentials')){value=credentialError||{key:'private-dinner-key',datasetKey:'dataset',deviceLabel:'PC'};if(credentialError)status=503;}
  return {ok:status===200,status,json:async()=>value};
 }};
 vm.createContext(context);vm.runInContext(source,context);
 return {api:context.window.OnAirRequestAccess,sent,context};
}
test('only explicitly unmanaged server permits browser fallback',async()=>{
 const a=app({managed:false});assert.equal(typeof a.api.dinnerCredentials,'function');
 assert.equal(await a.api.dinnerCredentials('A'),null);
 assert.equal(a.sent.filter(r=>r.url.endsWith('/dinner-credentials')).length,0);
});
test('managed missing launch session blocks instead of returning fallback',async()=>{
 const a=app();assert.equal(typeof a.api.dinnerCredentials,'function');
 await assert.rejects(a.api.dinnerCredentials('A'),/트레이|프로그램/);
 assert.equal(a.sent.filter(r=>r.url.endsWith('/dinner-credentials')).length,0);
});
test('managed local bridge sends session privately and returns dinner key',async()=>{
 const a=app({ticket:'launch-ticket'});assert.equal(typeof a.api.dinnerCredentials,'function');
 const result=await a.api.dinnerCredentials('A');
 assert.equal(result.key,'private-dinner-key');assert.equal(result.datasetKey,'dataset');
 const call=a.sent.find(r=>r.url.endsWith('/dinner-credentials'));
 assert.deepEqual(JSON.parse(call.body),{name:'A'});
 assert.equal(call.headers['X-OnAir-Local-Session'],'private-session');
 assert.equal(call.headers['X-OnAir-Data-Contract'],'1');
 assert.equal(a.api.session,undefined);assert.equal(a.context.window.__onairAccessTicket,undefined);
});
test('managed disk failure is propagated and never downgraded to browser fallback',async()=>{
 const a=app({ticket:'launch-ticket',credentialError:{code:'LOCAL_PERSIST_FAILED',detail:'저장 실패'}});
 assert.equal(typeof a.api.dinnerCredentials,'function');
 await assert.rejects(a.api.dinnerCredentials('A'),e=>e.code==='LOCAL_PERSIST_FAILED');
});
test('profile check failure does not assume unmanaged browser',async()=>{
 const a=app({profileError:{detail:'연결 실패'}});assert.equal(typeof a.api.dinnerCredentials,'function');
 await assert.rejects(a.api.dinnerCredentials('A'),/연결 실패/);
 assert.equal(a.sent.filter(r=>r.url.endsWith('/dinner-credentials')).length,0);
});
