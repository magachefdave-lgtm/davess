'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'../..');
function app(){
 const nodes=new Map(),notifications=[],context={console,Date,Map,Set,JSON,Object,Number,String,Array,Math,URL,
  myName:'이서연',memberList:[{name:'이서연'}],SYSTEM_MODE:'VIEWER',notifList:notifications,myRequestStatuses:{},suspendAutoRefresh:false,
  saveNotifs(){},updateNotifBadge(){},getNowTimeStr:()=> '2026-11-17 10:00',fetchAllData:async()=>true,setMyName(){},notifyRemoteRefresh(){},checkRequestOutcomes(){},addNotif(){},showToast(){},renderNotifList(){},closeNotifModal(){},
  localStorage:{getItem:()=>null,setItem(){}},setTimeout(){},clearTimeout(){},setInterval(){},clearInterval(){},
  document:{head:{appendChild(){}},createElement:()=>({}),getElementById:id=>{if(!nodes.has(id))nodes.set(id,{classList:{contains:()=>false,remove(){}},addEventListener(){},querySelector:()=>null,innerHTML:''});return nodes.get(id);}},
  OnAirUI:{close(){}},OnAirDinner:{open:id=>context.opened=id}};
 context.window=context;vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(root,'frontend/dist/usability_notifications.js'),'utf8'),context);return context;
}
test('dinner notification preserves only safe routing data and opens that poll',()=>{
 const a=app();a.addNotif('새 회식 취합',{kind:'dinner',pollId:'poll-123',person:'이서연',key:'private-key'});
 assert.equal(a.notifList[0].meta.pollId,'poll-123');assert.equal(a.notifList[0].meta.key,undefined);a.OnAirNotifications.goTo(0);assert.equal(a.opened,'poll-123');assert.equal(a.notifList[0].read,true);
});
test('role and connection dinner notifications without a poll navigate to the dinner list',()=>{
 const a=app();a.addNotif('CA 사용 연결이 승인되었습니다.',{kind:'dinner'});a.OnAirNotifications.goTo(0);assert.equal(a.notifList[0].read,true);assert.ok(Object.hasOwn(a,'opened'));assert.equal(a.opened,undefined);
});
