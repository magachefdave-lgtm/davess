'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'../../..');
const html=fs.readFileSync(path.join(root,'frontend/dist/index.html'),'utf8');
const base=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(m=>m[1]).find(s=>s.includes('function renderTodayCard()'));
function app(){
 const nodes=new Map(),local=new Map(),session=new Map();
 const node=id=>{if(!nodes.has(id)){const classes=new Set();nodes.set(id,{id,isConnected:true,events:{},value:'',checked:false,disabled:false,hidden:false,innerHTML:'',textContent:'',style:{},dataset:{},options:[],children:[],scrollTop:0,scrollLeft:0,classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),toggle(x,v){v?classes.add(x):classes.delete(x)}},setAttribute(k,v){this[k]=v},removeAttribute(k){delete this[k]},appendChild(o){this.children.push(o);this.options.push(o);o.parentNode=this;return o},insertAdjacentHTML(){},append(){},insertBefore(o){this.appendChild(o)},addEventListener(type,fn){(this.events[type]??=[]).push(fn)},dispatchEvent(event){for(const fn of this.events[event.type]||[])fn(event)},querySelector(){return null},querySelectorAll(){return []},focus(){document.activeElement?.blurHandler?.();document.activeElement=this},scrollIntoView(){},remove(){},closest(){return null}});}return nodes.get(id)};
 const storage=map=>({getItem:k=>map.has(k)?map.get(k):null,setItem:(k,v)=>map.set(k,String(v)),removeItem:k=>map.delete(k)});
 const document={getElementById:node,querySelectorAll:()=>[],querySelector:()=>null,events:{},addEventListener(type,fn){(this.events[type]??=[]).push(fn)},dispatchEvent(event){for(const fn of this.events[event.type]||[])fn(event)},createElement:tag=>({...node('new-'+nodes.size),tagName:tag}),head:node('head'),body:node('body'),activeElement:null};
 const ctx=vm.createContext({console,Date,document,window:{addEventListener(){}},localStorage:storage(local),sessionStorage:storage(session),setTimeout:fn=>{fn();return 0},clearTimeout(){},setInterval:()=>0,clearInterval(){},requestAnimationFrame:fn=>fn(),confirm:()=>true,alert:()=>{throw Error('generic alert forbidden')}});
 vm.runInContext(base,ctx);const run=s=>vm.runInContext(s,ctx);
 run(`SYSTEM_MODE='VIEWER';SERVER_CONNECTED=true;dataLoaded=true;todayStr='2026-10-13';myName='A';
 memberList=[{name:'A',gender:'F'},{name:'B',gender:'F'},{name:'C',gender:'M'}];absenceTypes=['연차','교육'];
 closedMonths=[];absences=[];scheduleMap={'2026-10-14':[{name:'A'},{name:'B'}],'2026-10-15':[{name:'A'}]};
 showToast=()=>{};addNotif=()=>{};checkRequestOutcomes=()=>{};renderCalendar=()=>{};renderAbsenceList=()=>{};renderAbsenceTypeUI=()=>{};updateAbsenceDateLabels=()=>{};openVacancyModal=()=>{};offerUndo=()=>{};
 globalThis.sent=[];globalThis.response={ok:true,status:200,body:{receiptToken:'own-token',requestId:12}};
 fetch=async(url,options={})=>{sent.push({url,...options});return {ok:response.ok,status:response.status,json:async()=>response.body}};
 fetchWithAdminToken=async(url,options={})=>{sent.push({url,...options});return {ok:response.ok,status:response.status,json:async()=>response.body}};
 syncFullDataToBackend=async p=>{sent.push(JSON.parse(JSON.stringify(p)));return true};
 window.OnAirUI={escape:esc,dateLabel:x=>x,open:(id)=>{document.getElementById(id).classList.add('active')},close:id=>document.getElementById(id).classList.remove('active')};`);
 vm.runInContext(fs.readFileSync(path.join(root,'frontend/dist/usability_requests.js'),'utf8'),ctx);
 return {run,node,ctx,local,session,json:s=>JSON.parse(run('JSON.stringify('+s+')'))};
}
module.exports={app};
