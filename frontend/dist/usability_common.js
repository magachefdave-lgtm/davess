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
  function close(id) {
    const item=entries.get(id); if(!item)return;
    const wasOpen=item.node.classList.contains('active');
    item.node.classList.remove('active');
    if(!wasOpen)return;
    try { item.onClose?.(); } finally {
      if(item.trigger?.isConnected && typeof item.trigger.focus==='function')item.trigger.focus();
      item.trigger=null;
    }
  }
  function raise(id) {
    const node=entries.get(id)?.node||document.getElementById(id);
    if(!node)return null;
    document.body.appendChild(node);
    if(typeof MODAL_CLOSERS!=='undefined'){
      const index=MODAL_CLOSERS.findIndex(entry=>entry[0]===id);
      if(index>=0)MODAL_CLOSERS.push(MODAL_CLOSERS.splice(index,1)[0]);
    }
    return node;
  }
  function open(id, title, html, options={}) {
    let item=entries.get(id);
    if(!item){
      const node=document.createElement('div');node.id=id;node.className='modal oa-modal';node.setAttribute('role','dialog');node.setAttribute('aria-modal','true');node.setAttribute('aria-labelledby',id+'-title');
      node.innerHTML=`<div class="modal-body"><div class="modal-head"><div class="modal-title" id="${escapeText(id)}-title"></div><button type="button" class="oa-close" aria-label="창 닫기">✕</button></div><div class="oa-body"></div></div>`;
      document.body.appendChild(node);
      item={node,trigger:null,onClose:null};entries.set(id,item);
      node.querySelector('.oa-close').addEventListener('click',()=>close(id));
      let pressedBackground=false;
      node.addEventListener('mousedown',e=>{pressedBackground=e.target===node;});
      node.addEventListener('click',e=>{if(e.target===node&&pressedBackground)close(id);pressedBackground=false;});
      node.addEventListener('keydown',e=>{
        if(e.key!=='Tab')return;
        const focusable=[...node.querySelectorAll('button,input,select,textarea,a[href],summary,[tabindex]')].filter(el=>!el.disabled && el.tabIndex>=0 && el.getClientRects().length);
        if(!focusable.length){e.preventDefault();return;}
        const first=focusable[0],last=focusable.at(-1);
        if(e.shiftKey&&(document.activeElement===first||!node.contains(document.activeElement))){e.preventDefault();last.focus();}
        else if(!e.shiftKey&&(document.activeElement===last||!node.contains(document.activeElement))){e.preventDefault();first.focus();}
      });
      if(typeof MODAL_CLOSERS!=='undefined')MODAL_CLOSERS.push([id,()=>close(id)]);
    }
    // Raising reused dialogs keeps visual and Escape order aligned.
    document.body.appendChild(item.node);
    if(typeof MODAL_CLOSERS!=='undefined'){
      const previous=MODAL_CLOSERS.findIndex(entry=>entry[0]===id);
      if(previous>=0)MODAL_CLOSERS.splice(previous,1);
      MODAL_CLOSERS.push([id,()=>close(id)]);
    }
    if(!item.node.classList.contains('active'))item.trigger=document.activeElement;
    item.onClose=typeof options.onClose==='function'?options.onClose:null;
    item.node.querySelector('.modal-title').textContent=String(title||'');
    item.node.querySelector('.oa-body').innerHTML=String(html||'');
    item.node.querySelector('.modal-body').style.width=(Number(options.width)||680)+'px';
    item.node.classList.add('active');
    item.node.querySelector('.oa-close').focus({preventScroll:true});
    return item.node;
  }
  window.OnAirUI={open,close,raise,dateLabel,escape:escapeText};
})();
