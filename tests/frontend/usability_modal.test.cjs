'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const code=fs.readFileSync(path.resolve(__dirname,'../../frontend/dist/usability_common.js'),'utf8');
function app(){
 const order=[],listeners=new Map();
 const document={activeElement:null,addEventListener(type,fn){listeners.set(type,fn);},body:{appendChild(node){const i=order.indexOf(node);if(i>=0){order.splice(i,1);if(node.contains(document.activeElement))document.activeElement=null;}order.push(node);node.isConnected=true;}},getElementById:id=>order.find(n=>n.id===id)};
 document.createElement=()=>{const classes=new Set(),children=new Map();const node={style:{},isConnected:true,setAttribute(){},addEventListener(){},classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)},contains(el){return el===this||[...children.values()].includes(el);},querySelector(sel){if(!children.has(sel))children.set(sel,{style:{},isConnected:true,textContent:'',innerHTML:'',addEventListener(){},focus(){document.activeElement=this;}});return children.get(sel);}};return node;};
 const context=vm.createContext({window:{},document,Date,MODAL_CLOSERS:[['legacy',()=>{}]]});vm.runInContext(code,context);
 return {ui:context.window.OnAirUI,document,order,closers:context.MODAL_CLOSERS};
}
test('registered legacy lifecycle replaces duplicate close entries without bypassing its guard',()=>{
 const a=app();let attempts=0;const guard=()=>{attempts++;};a.ui.registerClose('legacy',guard);a.ui.registerClose('legacy',guard);
 assert.equal(a.closers.filter(([id])=>id==='legacy').length,1);
 a.closers.find(([id])=>id==='legacy')[1]();assert.equal(attempts,1);
});
test('refreshing an already open shared dialog preserves its existing focused control',()=>{
 const a=app();const node=a.ui.open('refresh','갱신','처음');const field=node.querySelector('.oa-body');field.focus();
 a.ui.open('refresh','갱신','다음');assert.equal(a.document.activeElement,field);
});
test('refreshing an open parent preserves a nested child as the foremost dialog',()=>{
 const a=app();a.ui.open('parent','부모','처음');a.ui.open('child','자식','확인');
 a.ui.open('parent','부모','갱신');assert.equal(a.order.at(-1).id,'child');
 assert.equal(a.closers.at(-1)[0],'child');
});
