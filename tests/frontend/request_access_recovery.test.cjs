'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path'),{webcrypto}=require('node:crypto');
const source=fs.readFileSync(path.resolve(__dirname,'../../frontend/dist/request_access.js'),'utf8');
function app({storage=new Map(),handler=()=>({}),ticket='launch',name='A'}={}){
 const nodes=new Map(),events={},ticks=[],sent=[],closers=new Map();let sequence=0;
 class Element{
  constructor(tag='div'){this.tagName=tag;this.id='';this.dataset={};this.children=[];this.value='';this.textContent='';this.checked=false;this.disabled=false;this.attrs={};this.scrollTop=0;this.scrollLeft=0;const classes=new Set();this.classList={add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)};}
  set innerHTML(html){this.html=html;for(const node of this.children){if(node.contains(document.activeElement))document.activeElement=document.body;if(node.id)nodes.delete(node.id);}this.children=[];for(const match of html.matchAll(/<([a-z][\w-]*)\b([^>]*)>/gi)){const node=new Element(match[1]);for(const a of match[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)){node.attrs[a[1]]=a[2]??'';if(a[1]==='id'){node.id=a[2];nodes.set(node.id,node);}if(a[1]==='value')node.value=a[2];if(a[1]==='checked')node.checked=true;if(a[1]==='disabled')node.disabled=true;if(a[1].startsWith('data-'))node.dataset[a[1].slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=a[2];}node.parentNode=this;this.children.push(node);}}
  get innerHTML(){return this.html||'';}
  appendChild(node){node.parentNode=this;this.children.push(node);if(node.id)nodes.set(node.id,node);return node;}
  insertBefore(node){return this.appendChild(node);}
  focus(){document.activeElement=this;}
  contains(node){for(let current=node;current;current=current.parentNode)if(current===this)return true;return false;}
  getAttribute(name){return this.attrs[name]??null;}
  querySelectorAll(selector){const descendants=this.children.flatMap(node=>[node,...node.querySelectorAll('*')]);return descendants.filter(node=>selector==='*'||selector.split(',').some(s=>{s=s.trim();if(s[0]==='.')return (node.attrs.class||node.className||'').split(' ').includes(s.slice(1));if(s[0]==='#')return node.id===s.slice(1);const attr=s.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);if(attr)return Object.hasOwn(node.attrs,attr[1])&&(attr[2]===undefined||node.attrs[attr[1]]===attr[2]);return !node.disabled&&s.startsWith(node.tagName);}));}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
 }
 const document={hidden:false,activeElement:null,body:new Element(),getElementById:id=>nodes.get(id)||null,createElement:tag=>new Element(tag),querySelectorAll:selector=>nodes.get('ra-content')?.querySelectorAll(selector)||[],addEventListener:(type,fn)=>(events[type]??=[]).push(fn)};
 const nameEl=new Element('select');nameEl.id='myreq-name';nameEl.value=name;nodes.set(nameEl.id,nameEl);
 const fetch=async(url,options={})=>{const call={url,...options,input:options.body?JSON.parse(options.body):null};sent.push(call);const data=await handler(call);return {ok:!data?.error,status:data?.error?503:200,json:async()=>!data?.error&&url.endsWith('/session')?{sessionToken:'session-value',expiresAt:Math.floor(Date.now()/1000)+1000}:data};};
 const context={window:{__onairAccessTicket:ticket,OnAirUI:Object.freeze({registerClose:(id,close)=>closers.set(id,close),raise(){}})},document,sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k)},crypto:{randomUUID:()=>`operation-${++sequence}`,subtle:webcrypto.subtle},TextEncoder,Uint8Array,URLSearchParams,Date,JSON,Object,Map,Set,Number,Array,encodeURIComponent,setInterval:fn=>(ticks.push(fn),ticks.length),clearInterval(){},fetch,fetchWithAdminToken:fetch,prompt:()=> 'review reason'};
 vm.createContext(context);vm.runInContext(source,context);
 return {api:context.window.OnAirRequestAccess,document,nodes,sent,storage,ticks,events,closers,context,flush:()=>new Promise(resolve=>setImmediate(resolve))};
}
const recovery=(state='PENDING',name='A')=>({id:'recovery-one',state,requested_name:name,host_label:'PC',account_label:'user',created_at:123,expires_at:456});
const action=(a,name)=>a.nodes.get('ra-content').children.find(node=>node.attrs.onclick===`OnAirRequestAccess.${name}()`);
const employeeHandler=call=>call.url.endsWith('/recoveries')?{recoveries:[recovery()]}:{receipts:[]};
test('recovery registers one common close callback and restores its opener',async()=>{
 const a=app({handler:employeeHandler}),opener=a.nodes.get('myreq-name');opener.focus();await a.api.openRecovery();
 assert.equal(typeof a.closers.get('ra-modal'),'function');
 let prevented=false;for(const handler of a.events.keydown||[])handler({key:'Escape',preventDefault(){prevented=true;}});
 assert.equal(prevented,false,'common modal manager owns Escape');assert.equal(a.nodes.get('ra-modal').classList.contains('active'),true);
 a.closers.get('ra-modal')();assert.equal(a.nodes.get('ra-modal').classList.contains('active'),false);assert.ok(a.document.activeElement===opener);
});
test('employee manual and polling refresh preserve the corresponding focused control and panel scroll',async()=>{
 const a=app({handler:employeeHandler});await a.api.openRecovery();action(a,'refresh').focus();const panel=a.nodes.get('ra-modal').querySelector('.ra-panel');panel.scrollTop=37;
 await a.api.refresh();assert.ok(a.document.activeElement===action(a,'refresh'));assert.equal(panel.scrollTop,37);
 action(a,'cancel').focus();a.ticks.at(-1)();await a.flush();assert.ok(a.document.activeElement===action(a,'cancel'));
});
test('successful latest employee refresh clears a previous read failure without clearing it on failure',async()=>{
 let fail=false;const a=app({handler:call=>call.url.endsWith('/recoveries')&&fail?{error:true,detail:'temporary recovery failure'}:employeeHandler(call)});await a.api.openRecovery();
 const content=a.nodes.get('ra-content').innerHTML;action(a,'refresh').focus();fail=true;await a.api.refresh();assert.equal(a.nodes.get('ra-content').innerHTML,content);assert.ok(a.document.activeElement===action(a,'refresh'));assert.match(a.nodes.get('ra-error').textContent,/temporary recovery failure/);
 fail=false;await a.api.refresh();assert.equal(a.nodes.get('ra-error').textContent,'');
});
test('candidate checkbox selection keeps its DOM focus and list scroll while invalidating approval',async()=>{
 const a=app({handler:call=>call.url.includes('/candidates')?{groups:[{rootUid:'stable-root'},{rootUid:'second-root'}],total:2}:call.url.endsWith('/request-recoveries')?{recoveries:[recovery()]}:{receipts:[]}});await a.api.openAdminRecovery();await a.document.querySelectorAll('[data-ra-review]')[0].onclick();
 const content=a.nodes.get('ra-content'),checkbox=a.document.querySelectorAll('[data-ra-group]')[0],list=content.querySelector('.ra-candidates'),reads=a.sent.length;checkbox.focus();list.scrollTop=1800;checkbox.checked=true;await checkbox.onchange();
 assert.ok(a.document.activeElement===checkbox);assert.ok(content.querySelector('.ra-candidates')===list);assert.equal(list.scrollTop,1800);assert.equal(a.sent.length,reads,'selection is a local update');assert.equal(checkbox.checked,true);
 await a.api.approve();assert.equal(a.sent.filter(call=>call.url.endsWith('/decision')).length,0);
});
test('admin refresh preserves an edited search and clears its previous read error on success',async()=>{
 let fail=false;const a=app({handler:call=>call.url.endsWith('/request-recoveries')?(fail?{error:true,detail:'admin temporary failure'}:{recoveries:[recovery()]}):call.url.includes('/candidates')?{groups:[],total:0}:{receipts:[]}});await a.api.openAdminRecovery();await a.document.querySelectorAll('[data-ra-review]')[0].onclick();
 const search=a.nodes.get('ra-search');search.value='unsent search';search.focus();fail=true;await a.api.refresh();assert.ok(a.nodes.get('ra-search')===search);assert.equal(search.value,'unsent search');assert.ok(a.document.activeElement===search);assert.match(a.nodes.get('ra-error').textContent,/admin temporary failure/);
 fail=false;await a.api.refresh();assert.equal(a.nodes.get('ra-error').textContent,'');assert.ok(a.document.activeElement===search);assert.equal(search.value,'unsent search');
});
test('successful latest preview retry clears its failed read while retaining the selected scope',async()=>{
 let fail=true;const a=app({handler:call=>call.url.endsWith('/preview')?(fail?{error:true,detail:'temporary preview failure'}:{items:[{request_id:1}],digest:'latest-digest',blockers:[]}):call.url.includes('/candidates')?{groups:[{rootUid:'root'}],total:1}:call.url.endsWith('/request-recoveries')?{recoveries:[recovery()]}:{receipts:[]}});await a.api.openAdminRecovery();await a.document.querySelectorAll('[data-ra-review]')[0].onclick();
 const checkbox=a.document.querySelectorAll('[data-ra-group]')[0];checkbox.checked=true;await checkbox.onchange();await a.api.preview();assert.match(a.nodes.get('ra-error').textContent,/temporary preview failure/);assert.equal(checkbox.checked,true);
 fail=false;await a.api.preview();assert.equal(a.nodes.get('ra-error').textContent,'');assert.match(a.nodes.get('ra-preview').innerHTML,/권한 연결 승인/);assert.equal(checkbox.checked,true);
});
test('an earlier preview failure cannot overwrite a newer successful preview',async()=>{
 let resolveOld,calls=0;const a=app({handler:call=>call.url.endsWith('/preview')?(++calls===1?new Promise(resolve=>{resolveOld=resolve;}):{items:[],digest:'new-digest',blockers:[]}):call.url.includes('/candidates')?{groups:[{rootUid:'root'}],total:1}:call.url.endsWith('/request-recoveries')?{recoveries:[recovery()]}:{receipts:[]}});await a.api.openAdminRecovery();await a.document.querySelectorAll('[data-ra-review]')[0].onclick();const checkbox=a.document.querySelectorAll('[data-ra-group]')[0];checkbox.checked=true;await checkbox.onchange();
 const old=a.api.preview();await a.flush();await a.api.preview();resolveOld({error:true,detail:'obsolete preview failure'});await old;assert.equal(a.nodes.get('ra-error').textContent,'');
});
test('a delayed employee response cannot render or claim after the selected name changes',async()=>{
 let resolveRead;const a=app({handler:call=>call.url.endsWith('/recoveries')?new Promise(resolve=>{resolveRead=resolve;}):{receipts:[]}});await a.api.bootstrap();const pending=a.api.openRecovery();await a.flush();a.nodes.get('myreq-name').value='B';resolveRead({recoveries:[recovery('APPROVED','A'),recovery('PENDING','B')]});await pending;
 assert.equal(a.nodes.get('ra-content').innerHTML,'');assert.equal(a.sent.filter(call=>call.url.endsWith('/claim')).length,0);
});
test('a delayed admin candidate response cannot replace the list after navigating back',async()=>{
 let resolveRead;const a=app({handler:call=>call.url.includes('/candidates')?new Promise(resolve=>{resolveRead=resolve;}):call.url.endsWith('/request-recoveries')?{recoveries:[recovery()]}:{receipts:[]}});await a.api.openAdminRecovery();const pending=a.document.querySelectorAll('[data-ra-review]')[0].onclick();await a.flush();await a.api.adminList();const list=a.nodes.get('ra-content').innerHTML;resolveRead({groups:[{rootUid:'old-root'}],total:1});await pending;
 assert.equal(a.nodes.get('ra-content').innerHTML,list);assert.equal(a.document.querySelectorAll('[data-ra-group]').length,0);assert.equal(a.nodes.get('ra-error').textContent,'');
});
test('closing invalidates pending recovery reads and their errors across reopening',async()=>{
 let resolveRead,defer=false;const a=app({handler:call=>call.url.endsWith('/recoveries')?(defer?new Promise(resolve=>{resolveRead=resolve;}):{recoveries:[]}):{receipts:[]}});await a.api.openRecovery();defer=true;const pending=a.api.refresh();await a.flush();a.api.close();defer=false;await a.api.openRecovery();const fresh=a.nodes.get('ra-content').innerHTML,focus=a.document.activeElement,reads=a.sent.length;await a.api.refresh();assert.equal(a.sent.length,reads+1,'an old dialog request must not block a fresh manual read');resolveRead({error:true,detail:'stale failure'});await pending;
 assert.equal(a.nodes.get('ra-error').textContent,'');assert.equal(a.nodes.get('ra-content').innerHTML,fresh);assert.ok(a.document.activeElement===focus);
});
test('admin recovery arrivals survive reload, detect equal-count replacements, and clear only on successful reads',()=>{
 const storage=new Map();let notices=[],counts=[];
 function setup(){const a=app({ticket:null});Object.assign(a.context,{SYSTEM_MODE:'ADMIN',localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},updateRecoveryPendingBadge:n=>counts.push(n),addNotif:(text,meta)=>notices.push({text,meta}),showToast:()=>{}});return a;}
 const first=setup(),info=ids=>({requestRecovery:{datasetKey:'data:generation',pendingIds:ids}});
 first.api.syncAdminNotifications(info(['one']));assert.equal(notices.length,1);assert.equal(counts.at(-1),1);assert.equal(notices[0].meta.kind,'recovery');
 first.api.syncAdminNotifications(info(['one']));assert.equal(notices.length,1);
 const next=setup();next.api.syncAdminNotifications(info(['one']));assert.equal(notices.length,1,'reload must not repeat the alert');
 next.api.syncAdminNotifications(info(['two']));assert.equal(notices.length,2,'new request replaces processed one at the same total');
 next.api.syncAdminNotifications({dbOk:false,...info([])});next.api.syncAdminNotifications({requestRecovery:null});assert.equal(counts.at(-1),1,'failed lookup is not zero pending');
 next.api.syncAdminNotifications(info([]));assert.equal(counts.at(-1),0);assert.equal(notices.length,2);
 next.context.SYSTEM_MODE='VIEWER';next.api.syncAdminNotifications(info(['three']));assert.equal(notices.length,2);
});
test('admin searches historical pages, explicitly selects roots, previews digest and approves only selected scope',async()=>{
 const a=app({handler:call=>{
  if(call.url.includes('/candidates'))return {groups:[{rootUid:call.url.includes('offset=50')?'historical-root':'recent-root',requester_name:'A',target_date:'2020-01-01',absence_end_date:'2020-01-02',absence_type:'교육',status:'APPROVED',count:3}],total:80};
  if(call.url.endsWith('/preview'))return {items:[{request_id:7}],digest:'review-digest',blockers:[]};
  if(call.url.endsWith('/decision'))return recovery('APPROVED');
  return call.url.endsWith('/request-recoveries')?{recoveries:[recovery()]}:{receipts:[]};
 }});await a.api.bootstrap();await a.api.openAdminRecovery();await a.document.querySelectorAll('[data-ra-review]')[0].onclick();
 assert.equal(a.document.querySelectorAll('[data-ra-group]')[0].checked,false,'initial selection must be empty');
 await a.api.approve();assert.equal(a.sent.filter(c=>c.url.endsWith('/decision')).length,0);
 await a.nodes.get('ra-next').onclick();assert.ok(a.sent.at(-1).url.includes('offset=50'));
 const checkbox=a.document.querySelectorAll('[data-ra-group]')[0];checkbox.checked=true;await checkbox.onchange();await a.flush();
 a.nodes.get('ra-search').value='2020';await a.api.search();await a.flush();assert.match(a.sent.at(-1).url,/q=2020/);
 await a.api.preview();const preview=a.sent.find(c=>c.url.endsWith('/preview'));assert.deepEqual(preview.input.rootUids,['historical-root']);
 await a.api.approve();const decision=a.sent.find(c=>c.url.endsWith('/decision'));assert.equal(decision.input.expectedDigest,'review-digest');assert.deepEqual(decision.input.rootUids,['historical-root']);assert.equal(decision.input.action,'APPROVE');
});
test('claimed recovery stays storing until verified completion and retries same claim operation',async()=>{
 let state='CLAIMED';const a=app({handler:call=>call.url.endsWith('/claim')?recovery(state):call.url.endsWith('/recoveries')?{recoveries:[recovery(state)]}:{receipts:[]}});
 await a.api.bootstrap();await a.api.openRecovery();assert.match(a.nodes.get('ra-content').innerHTML,/권한을 이 PC에 저장하고 있습니다/);assert.doesNotMatch(a.nodes.get('ra-content').innerHTML,/선택한 신청의 권한이 연결되었습니다/);
 await a.api.claim();await a.api.claim();const calls=a.sent.filter(c=>c.url.endsWith('/claim'));assert.equal(calls.length,2);assert.equal(calls[0].input.operationId,calls[1].input.operationId);
 state='COMPLETED';await a.api.claim();assert.match(a.nodes.get('ra-content').innerHTML,/선택한 신청의 권한이 연결되었습니다/);
});
test('polling pauses hidden documents and refreshes immediately when visible',async()=>{
 const a=app({handler:call=>call.url.endsWith('/recoveries')?{recoveries:[recovery()]}:{receipts:[]}});await a.api.bootstrap();await a.api.openRecovery();const before=a.sent.length;
 a.document.hidden=true;a.ticks.at(-1)();await a.flush();assert.equal(a.sent.length,before);
 a.document.hidden=false;a.events.visibilitychange[0]();await a.flush();assert.equal(a.sent.length,before+1);
});
test('changing employee never claims or renders another employee recovery',async()=>{
 const a=app({handler:call=>call.url.endsWith('/recoveries')?{recoveries:[recovery('APPROVED','B'),recovery('PENDING','A')]}:{receipts:[]}});await a.api.bootstrap();await a.api.openRecovery();assert.match(a.nodes.get('ra-content').innerHTML,/관리자 확인 대기/);assert.equal(a.sent.filter(c=>c.url.endsWith('/claim')).length,0);
 a.nodes.get('myreq-name').value='C';await a.api.refresh();assert.doesNotMatch(a.nodes.get('ra-content').innerHTML,/recovery-one|관리자 확인 대기/);assert.match(a.nodes.get('ra-content').innerHTML,/복구 요청 등록/);
});
test('durable retry survives reload with only digest and operation id in metadata',async()=>{
 const storage=new Map(),request={action:'AMEND',receiptToken:'private-receipt-value',request:{reason:'private-reason-text'}};
 const first=app({storage,handler:call=>{if(call.url.endsWith('/submit'))throw Error('lost response');return {receipts:[]};}});await first.api.bootstrap();await assert.rejects(first.api.submitDurable(request,()=>{throw Error('fallback forbidden');}),/lost response/);
 const metadata=storage.get('onair.access.submissions');assert.doesNotMatch(metadata,/private-receipt|private-reason|receiptToken|reason/);assert.match(metadata,/[a-f0-9]{64}/);
 const second=app({storage,ticket:undefined,handler:call=>call.url.endsWith('/submit')?{receiptToken:'success-receipt'}:{receipts:[]}});await second.api.bootstrap();await second.api.submitDurable(request,()=>{throw Error('fallback forbidden');});
 assert.equal(first.sent.find(c=>c.url.endsWith('/submit')).input.operationId,second.sent.find(c=>c.url.endsWith('/submit')).input.operationId);assert.deepEqual(JSON.parse(storage.get('onair.access.submissions')),{});
});
test('blocked admin preview cannot be approved even by direct handler call',async()=>{
 const a=app({handler:call=>call.url.includes('/candidates')?{groups:[{rootUid:'root'}],total:1}:call.url.endsWith('/preview')?{items:[],digest:'blocked-digest',blockers:['원본 누락']}:call.url.endsWith('/request-recoveries')?{recoveries:[recovery()]}:{receipts:[]}});await a.api.bootstrap();await a.api.openAdminRecovery();await a.document.querySelectorAll('[data-ra-review]')[0].onclick();const checkbox=a.document.querySelectorAll('[data-ra-group]')[0];checkbox.checked=true;await checkbox.onchange();await a.flush();await a.api.preview();await a.api.approve();assert.equal(a.sent.filter(c=>c.url.endsWith('/decision')).length,0);
});
test('changing selection during preview invalidates its approval digest',async()=>{
 let resolvePreview;const a=app({handler:call=>call.url.includes('/candidates')?{groups:[{rootUid:'root'}],total:1}:call.url.endsWith('/preview')?new Promise(resolve=>{resolvePreview=resolve;}):call.url.endsWith('/request-recoveries')?{recoveries:[recovery()]}:{receipts:[]}});await a.api.bootstrap();await a.api.openAdminRecovery();await a.document.querySelectorAll('[data-ra-review]')[0].onclick();let checkbox=a.document.querySelectorAll('[data-ra-group]')[0];checkbox.checked=true;await checkbox.onchange();const pending=a.api.preview();await a.flush();checkbox=a.document.querySelectorAll('[data-ra-group]')[0];checkbox.checked=false;await checkbox.onchange();resolvePreview({items:[],digest:'old-digest',blockers:[]});await pending;await a.api.approve();assert.equal(a.sent.filter(c=>c.url.endsWith('/decision')).length,0);
});
test('approved claim failures remain retryable without false completion',async()=>{
 const a=app({handler:call=>{if(call.url.endsWith('/claim'))return {error:true,code:'LOCAL_PERSIST_FAILED',detail:'개인 저장 실패',retryable:true};return call.url.endsWith('/recoveries')?{recoveries:[recovery('APPROVED')]}:{receipts:[]};}});await a.api.bootstrap();await a.api.openRecovery();assert.match(a.nodes.get('ra-error').textContent,/개인 저장 실패/);assert.doesNotMatch(a.nodes.get('ra-content').innerHTML,/선택한 신청의 권한이 연결되었습니다/);await a.api.claim();const calls=a.sent.filter(c=>c.url.endsWith('/claim'));assert.equal(calls.length,2);assert.equal(calls[0].input.operationId,calls[1].input.operationId);
});
test('transient bootstrap receipt failure can retry with the valid session and write contract',async()=>{
 let fail=true;const a=app({handler:call=>{if(call.url.endsWith('/receipts/read')&&fail){fail=false;throw Error('temporary vault unavailable');}return {receipts:[]};}});await assert.rejects(a.api.bootstrap(),/temporary vault unavailable/);assert.equal(await a.api.bootstrap(),true);const requests=a.sent.filter(c=>c.url.endsWith('/receipts/read'));assert.equal(requests.length,2);for(const call of requests){assert.equal(call.headers['X-OnAir-Data-Contract'],'1');assert.equal(call.headers['X-OnAir-Local-Session'],'session-value');}assert.equal(a.sent.filter(c=>c.url.endsWith('/session')).length,1);
});
test('disabled recovery permits a safe legacy amendment without a pending durable operation',async()=>{
 const a=app({handler:()=>({error:true,code:'DISABLED',detail:'복구 기능 중지',retryable:false})});let fallback=0;await a.api.submitDurable({action:'AMEND',receiptToken:'existing-receipt',request:{reason:'edit'}},async()=>++fallback);assert.equal(fallback,1);assert.equal(a.sent.filter(c=>c.url.endsWith('/submit')).length,0);
});
test('disabled feature never falls back for an unresolved durable operation after reload',async()=>{
 const storage=new Map(),input={action:'CANCEL_ABSENCE',receiptToken:'existing-receipt',reason:'cancel'};const first=app({storage,handler:call=>{if(call.url.endsWith('/submit'))throw Error('lost response');return {receipts:[]};}});await first.api.bootstrap();await assert.rejects(first.api.submitDurable(input,()=>{throw Error('unsafe fallback');}),/lost response/);
 const second=app({storage,handler:()=>({error:true,code:'DISABLED',detail:'복구 기능 중지',retryable:false})});let fallback=0;await assert.rejects(second.api.submitDurable(input,async()=>++fallback),/복구 기능 중지/);assert.equal(fallback,0);assert.notEqual(storage.get('onair.access.submissions'),'{}');
});
test('feature disabled after bootstrap safely falls back only for a fresh durable operation',async()=>{
 const a=app({handler:call=>call.url.endsWith('/submit')?{error:true,code:'DISABLED',detail:'복구 기능 중지',retryable:false}:{receipts:[]}});await a.api.bootstrap();let fallback=0;await a.api.submitDurable({action:'CANCEL_ABSENCE',receiptToken:'receipt',reason:'cancel'},async()=>++fallback);assert.equal(fallback,1);assert.deepEqual(JSON.parse(a.storage.get('onair.access.submissions')),{});
});
test('candidate metadata and exclusions are readable and guarded GET requests carry the write contract',async()=>{
 const created=1577923200,requested=1794268800;
 const a=app({handler:call=>call.url.includes('/candidates')?{groups:[{rootUid:'root',requester_name:'A',target_date:'2020-01-02',absence_end_date:'2020-01-03',absence_type:'외부 교육',status:'APPROVED',count:2,created_at:created}],total:1,blockers:[{rootUid:'bad-root',code:'BROKEN_LINK',detail:'원본 누락 <script>위험</script>'}]}:call.url.endsWith('/request-recoveries')?{recoveries:[{...recovery(),created_at:requested}]}:{receipts:[]}});
 await a.api.bootstrap();await a.api.openAdminRecovery();assert.match(a.nodes.get('ra-content').innerHTML,new RegExp(new Date(requested*1000).toLocaleString('ko-KR').replace(/[.*+?^${}()|[\]\\]/g,'\\$&')));
 await a.document.querySelectorAll('[data-ra-review]')[0].onclick();const html=a.nodes.get('ra-content').innerHTML;
 assert.match(html,/외부 교육/);assert.match(html,/승인/);assert.doesNotMatch(html,/APPROVED/);assert.ok(html.includes(new Date(created*1000).toLocaleString('ko-KR')));
 assert.match(html,/복구 대상에서 제외된 신청/);assert.match(html,/원본 누락 &lt;script&gt;위험&lt;\/script&gt;/);assert.doesNotMatch(html,/BROKEN_LINK|<script>/);
 const calls=a.sent.filter(c=>c.url.includes('/api/duty/request-recoveries'));assert.equal(calls.length,2);for(const call of calls){assert.equal(call.method,'GET');assert.equal(call.headers['X-OnAir-Data-Contract'],'1');}
});
