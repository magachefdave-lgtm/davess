'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').resolve(__dirname,'../../frontend/dist/usability_admin.js'),'utf8');
const editor=source.slice(source.indexOf('  function setRosterNames(id, names)'),source.indexOf("  if (typeof updateFixedOrderState === 'function')"));
function harness(){
  const nodes=new Map();
  const make=()=>({style:{},listeners:{},setAttribute(){},addEventListener(k,v){this.listeners[k]=v;},querySelector(){return null;}});
  const get=id=>{if(!nodes.has(id))nodes.set(id,Object.assign(make(),{id,value:'',innerHTML:'',parentNode:{insertBefore(n){nodes.set(n.id,n);}}}));return nodes.get(id);};
  get('rule-algo').value='FIXED_ORDER'; get('a').value='A, B, C';get('b').value='X, Y';
  const ctx=vm.createContext({$:id=>id.startsWith('oa-chips-')?(nodes.get(id)||null):get(id),fieldValue:id=>get(id).value,doc:{createElement:make},splitNames:s=>s.split(/[,，\s]+/).filter(Boolean),escapeHtml:s=>s,specFor:id=>({label:id,gender:'F'}),memberList:['A','B','C','D','X','Y'].map(name=>({name,gender:'F'})),Event:class{},renderRosterCheck(){}});
  vm.runInContext(editor+";setupChipEditor('a');setupChipEditor('b');",ctx);
  const box=get('oa-chips-a');
  const click=(index,action)=>{const button={disabled:false,dataset:action==='move'?{move:'1'}:{},hasAttribute:k=>k===`data-${action}`};const chip={dataset:{idx:String(index)}};const toolbar={dataset:{idx:String(index)}};box.listeners.click({target:{closest:s=>s==='button'?button:s==='.oa-chip'?(action==='select'?chip:null):toolbar}});};
  return {get,box,click};
}
test('selected roster toolbar moves and removes actual values while preserving other rosters',()=>{
  const a=harness();a.click(1,'select');
  assert.match(a.box.innerHTML,/aria-pressed="true"/);assert.equal((a.box.innerHTML.match(/data-remove/g)||[]).length,1);
  a.click(1,'move');assert.equal(a.get('a').value,'A, C, B');assert.match(a.box.innerHTML,/data-idx="2"><span>B<\/span>/);
  a.click(2,'remove');assert.equal(a.get('a').value,'A, C');assert.match(a.box.innerHTML,/data-idx="1"><span>C<\/span>/);
  assert.equal(a.get('b').value,'X, Y');
});
test('add selects the added name; FAIR_WEIGHT preserves all values and disables mutation',()=>{
  const a=harness();const target={value:'D',matches:()=>true};a.box.listeners.change({target});assert.equal(a.get('a').value,'A, B, C, D');assert.match(a.box.innerHTML,/data-idx="3"><span>D<\/span>/);
  a.get('rule-algo').value='FAIR_WEIGHT';a.click(0,'remove');a.box.listeners.change({target:{value:'X',matches:()=>true}});assert.equal(a.get('a').value,'A, B, C, D');assert.equal(a.get('b').value,'X, Y');
});
test('drag from another roster cannot reorder this roster',()=>{
  const a=harness();const b=a.get('oa-chips-b');b.listeners.drop({target:{closest:()=>({dataset:{idx:'0'}})},preventDefault(){throw Error('external drop accepted');},stopPropagation(){}});assert.equal(a.get('b').value,'X, Y');
});
test('pair preview exposes all actual pairs and explains zero, one and odd counts',()=>{
  const html=fs.readFileSync(require('node:path').resolve(__dirname,'../../frontend/dist/index.html'),'utf8');
  const start=html.indexOf('    function renderWeekendPairPreview() {');const end=html.indexOf('    // ── [02]',start);
  const box={style:{},innerHTML:'',querySelector:()=>({open:true})};const lists={'ro-we-m':['A','B','C','D'],'ro-we-f':['E','F']};
  const ctx=vm.createContext({document:{getElementById:()=>box},parseRosterInput:id=>lists[id],esc:s=>String(s)});vm.runInContext(html.slice(start,end),ctx);vm.runInContext('renderWeekendPairPreview()',ctx);
  assert.match(box.innerHTML,/<details open>/);assert.match(box.innerHTML,/토 <b>C<\/b> · 일 <b>D<\/b>/);assert.match(box.innerHTML,/토 <b>D<\/b> · 일 <b>C<\/b>/);
  lists['ro-we-m']=[];lists['ro-we-f']=['E'];vm.runInContext('renderWeekendPairPreview()',ctx);assert.match(box.innerHTML,/0명/);assert.match(box.innerHTML,/1명/);
  lists['ro-we-f']=['E','F','G'];vm.runInContext('renderWeekendPairPreview()',ctx);assert.match(box.innerHTML,/홀수\(3명\)/);assert.match(box.innerHTML,/E → F → G/);
});
