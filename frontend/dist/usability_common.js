/* Shared dialogs for OnAir usability extensions. Existing main layout stays intact. */
(() => {
  'use strict';
  // Advertise the data format only at the same-origin business write boundary.
  // Normalize with the original init first: injecting init.headers into an existing
  // Request would otherwise reset its referrer policy. No body stream is read or cloned.
  if(typeof window.fetch==='function'&&typeof Request==='function'&&typeof URL==='function'){
    const nativeFetch=window.fetch;
    window.fetch=function(input,init){
      let nextRequest;
      try{
        const request=input instanceof Request?input:null;
        const url=new URL(request?request.url:input,document.baseURI||window.location.href);
        const method=String(init?.method!==undefined?init.method:(request?.method||'GET')).toUpperCase();
        const business=url.pathname.startsWith('/api/duty/')||/^\/api\/system\/(restore|import-json)\/?$/.test(url.pathname);
        if(url.origin===window.location.origin&&business&&['POST','PUT','PATCH','DELETE'].includes(method)){
          nextRequest=new Request(request||url,init);
          nextRequest.headers.set('X-OnAir-Data-Contract','1');
        }
      }catch{}
      // Let fetch handle malformed arguments itself, without changing its failure behavior.
      return nextRequest?nativeFetch.call(this,nextRequest):nativeFetch.apply(this,arguments);
    };
  }
  const entries = new Map();
  const escapeText = value => String(value == null ? '' : value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  function dateLabel(key, full = false) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(key || ''));
    if (!match) return String(key || '');
    const [, ys, ms, ds] = match, y=Number(ys), m=Number(ms), d=Number(ds);
    const dt=new Date(Date.UTC(y,m-1,d));
    if(dt.getUTCFullYear()!==y||dt.getUTCMonth()!==m-1||dt.getUTCDate()!==d)return String(key||'');
    const dow=['일','월','화','수','목','금','토'][dt.getUTCDay()];
    // 연도까지 쓸 때는 '2026년 10월 14일(수)'로 쓴다('2026년 10/14'처럼 섞지 않는다). 짧은 목록은 '10/14(수)'.
    return full ? `${ys}년 ${m}월 ${d}일(${dow})` : `${m}/${d}(${dow})`;
  }
  // Legacy dialogs toggle .active directly; observe those same lifecycle transitions.
  // Content refreshes never count as a new opening and never move focus on their own.
  const modalStates = new Map(), inertStates = new Map();
  let modalStack = [], syncing = false, lastFocus = document.activeElement;
  const canManage = typeof document.querySelectorAll === 'function' && typeof getComputedStyle === 'function';
  function registerClose(id, callback) {
    if(typeof MODAL_CLOSERS==='undefined')return;
    for(let i=MODAL_CLOSERS.length-1;i>=0;i--)if(MODAL_CLOSERS[i][0]===id)MODAL_CLOSERS.splice(i,1);
    if(typeof callback==='function')MODAL_CLOSERS.push([id,callback]);
  }
  function moveCloser(id) {
    if(typeof MODAL_CLOSERS==='undefined')return;
    const index=MODAL_CLOSERS.findIndex(entry=>entry[0]===id);
    if(index>=0)MODAL_CLOSERS.push(MODAL_CLOSERS.splice(index,1)[0]);
  }
  function visible(node) {
    return node.isConnected && (node.classList.contains('active') || node.style.display==='flex') &&
      node.getClientRects().length>0 && getComputedStyle(node).visibility!=='hidden';
  }
  function focusables(node) {
    return [...node.querySelectorAll('button,input,select,textarea,a[href],summary,[tabindex]')].filter(el=>
      !el.disabled && !el.matches(':disabled') && el.tabIndex>=0 && !el.closest('[inert]') &&
      el.getClientRects().length && getComputedStyle(el).visibility!=='hidden');
  }
  function focusInside(node, backwards=false) {
    const fields=focusables(node), field=backwards?fields.at(-1):fields[0];
    if(field){field.focus({preventScroll:true});return;}
    if(!node.hasAttribute('tabindex'))node.setAttribute('tabindex','-1');
    node.focus({preventScroll:true});
  }
  function returnFocusTarget(trigger) {
    const usable=node=>node?.isConnected && !node.disabled && !node.closest('[inert]') &&
      node.getClientRects().length && getComputedStyle(node).visibility!=='hidden';
    if(usable(trigger))return trigger;
    // A menu command hides itself after opening a dialog; return to its visible toggle.
    const toggle=trigger?.closest?.('.menu-pop-wrap')?.querySelector('.menu-pop-btn');
    return usable(toggle)?toggle:null;
  }
  function layers() {
    const top=modalStack.at(-1), blocked=new Set();
    if(top){
      // Keep the top dialog's ancestor path usable even if a legacy modal is nested.
      for(let branch=top;branch&&branch!==document.body;branch=branch.parentElement){
        for(const sibling of branch.parentElement?.children||[]){
          if(sibling!==branch && !['SCRIPT','STYLE','LINK'].includes(sibling.tagName))blocked.add(sibling);
        }
      }
    }
    for(const [node,original] of inertStates)if(!blocked.has(node)){node.inert=original;inertStates.delete(node);}
    for(const node of blocked)if('inert' in node){
      if(!inertStates.has(node))inertStates.set(node,node.inert);
      if(!node.inert)node.inert=true;
    }
    modalStack.forEach((node,index)=>{
      const z=String(2100+index);
      if(node.style.zIndex!==z)node.style.zIndex=z;
    });
  }
  function sync(focusNew=true, opener=document.activeElement) {
    if(!canManage||syncing)return;
    syncing=true;
    try{
      const shown=[...document.querySelectorAll('.modal')].filter(visible), closed=[];
      for(const node of modalStack)if(!shown.includes(node)){
        const state=modalStates.get(node);closed.push(state);
        node.style.zIndex=state.zIndex;modalStates.delete(node);
      }
      modalStack=modalStack.filter(node=>shown.includes(node));
      const opened=shown.filter(node=>!modalStates.has(node));
      for(const node of opened){
        let trigger=opener;
        if(!trigger||node.contains(trigger))trigger=lastFocus;
        if(!trigger||node.contains(trigger))trigger=modalStack.at(-1)?.querySelector('button,input,select,textarea,a[href],[tabindex]')||null;
        modalStates.set(node,{trigger,zIndex:node.style.zIndex});modalStack.push(node);moveCloser(node.id);
      }
      layers();
      const top=modalStack.at(-1), active=document.activeElement;
      if(closed.length && !opened.length){
        const trigger=returnFocusTarget(closed.at(-1)?.trigger);
        if(trigger && (!top||top.contains(trigger)) && (active===document.body||!top||!top.contains(active))){
          trigger.focus({preventScroll:true});
        }
        if(top&&!top.contains(document.activeElement))focusInside(top);
      }
      if(focusNew&&opened.length&&top&&!top.contains(document.activeElement))focusInside(top);
    }finally{syncing=false;}
  }
  function top() { sync(false);return modalStack.at(-1)||null; }
  function closeTop() {
    const node=top();if(!node)return false;
    const entry=typeof MODAL_CLOSERS!=='undefined'?MODAL_CLOSERS.find(([id])=>id===node.id):null;
    try{
      if(entry)entry[1]();
      else node.querySelector('.oa-close,.modal-head > button')?.click();
    }catch(error){console.warn('ESC 닫기 실패:',node.id,error);}
    finally{sync();}
    return true;
  }
  function close(id) {
    const item=entries.get(id); if(!item)return;
    const wasOpen=item.node.classList.contains('active');
    item.node.classList.remove('active');
    if(!wasOpen)return;
    try { item.onClose?.(); } finally {
      sync();
      if(!canManage&&item.trigger?.isConnected && typeof item.trigger.focus==='function')item.trigger.focus();
      item.trigger=null;
    }
  }
  function raise(id) {
    const node=entries.get(id)?.node||document.getElementById(id);
    if(!node)return null;
    sync(false);
    const focused=document.activeElement, retainFocus=node.contains?.(focused);
    document.body.appendChild(node);moveCloser(id);
    if(modalStates.has(node)){
      modalStack=modalStack.filter(item=>item!==node);modalStack.push(node);layers();
    }
    // appendChild detaches an existing focused subtree, even within the same parent.
    if(retainFocus&&focused?.isConnected&&!focused.disabled)focused.focus({preventScroll:true});
    sync();return node;
  }
  if(canManage){
    document.addEventListener('focusin',event=>{
      if(syncing){lastFocus=event.target;return;}
      sync(false,lastFocus);
      const node=modalStack.at(-1);
      if(node&&!node.contains(event.target))focusInside(node);
      lastFocus=document.activeElement;
    },true);
    document.addEventListener('keydown',event=>{
      if(event.key==='Escape')return; // index.html owns the one guarded Escape route.
      const node=top();if(!node)return;
      if(event.key==='Tab'){
        const fields=focusables(node), index=fields.indexOf(document.activeElement);
        event.preventDefault();event.stopImmediatePropagation();
        if(!fields.length){focusInside(node);return;}
        const next=index<0?(event.shiftKey?fields.length-1:0):(index+(event.shiftKey?-1:1)+fields.length)%fields.length;
        fields[next].focus({preventScroll:true});
      }else if(!node.contains(event.target)){
        event.preventDefault();event.stopImmediatePropagation();focusInside(node);
      }
    },true);
    if(typeof MutationObserver==='function')new MutationObserver(()=>sync()).observe(document.body,
      {subtree:true,childList:true,attributes:true,attributeFilter:['class','style','hidden']});
    sync();
  }
  function open(id, title, html, options={}) {
    let item=entries.get(id);
    if(!item){
      const node=document.createElement('div');node.id=id;node.className='modal oa-modal';node.setAttribute('role','dialog');node.setAttribute('aria-modal','true');node.setAttribute('aria-labelledby',id+'-title');
      node.innerHTML=`<div class="modal-body"><div class="modal-head"><div class="modal-title" id="${escapeText(id)}-title"></div><button type="button" class="oa-close" aria-label="창 닫기">✕</button></div><div class="oa-body"></div></div>`;
      document.body.appendChild(node);
      item={node,trigger:null,onClose:null};entries.set(id,item);
      node.querySelector('.oa-close').addEventListener('click',()=>close(id));
      registerClose(id,()=>close(id));
    }
    const wasOpen=item.node.classList.contains('active');
    // Opening a closed dialog promotes it; refreshing an open parent preserves its child.
    if(!wasOpen)raise(id);
    if(!item.node.classList.contains('active'))item.trigger=document.activeElement;
    item.onClose=typeof options.onClose==='function'?options.onClose:null;
    item.node.querySelector('.modal-title').textContent=String(title||'');
    item.node.querySelector('.oa-body').innerHTML=String(html||'');
    item.node.querySelector('.modal-body').style.width=(Number(options.width)||680)+'px';
    item.node.classList.add('active');
    sync();
    if(!canManage&&!wasOpen)item.node.querySelector('.oa-close').focus({preventScroll:true});
    return item.node;
  }
  window.OnAirUI={open,close,raise,registerClose,top,closeTop,dateLabel,escape:escapeText};
})();
