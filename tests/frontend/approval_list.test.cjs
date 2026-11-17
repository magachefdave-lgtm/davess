'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const htmlPath = process.env.ONAIR_FRONTEND || path.resolve(__dirname, '../../frontend/dist/index.html');
const html = fs.readFileSync(htmlPath, 'utf8');
const script = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(m => m[1]).find(s => s.includes('async function openApprovalModal()'));

function app(response) {
  const nodes = new Map();
  const element = id => {
    if (!nodes.has(id)) {
      const classes=new Set();
      nodes.set(id,{innerHTML:'',textContent:'',style:{},classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)}});
    }
    return nodes.get(id);
  };
  const storage={getItem:()=>null,setItem:()=>{},removeItem:()=>{}};
  const context=vm.createContext({Date,console,sessionStorage:storage,localStorage:storage,
    document:{addEventListener:()=>{},getElementById:element},window:{addEventListener:()=>{}},
    setTimeout,clearTimeout,setInterval,clearInterval});
  vm.runInContext(script,context,{filename:htmlPath});
  const run=code=>vm.runInContext(code,context);
  context.reply=response;
  run(`SYSTEM_MODE='ADMIN'; updatePendingBadge=()=>{};
    fetchWithAdminToken=async()=>({status:reply.status||200,ok:!reply.status||reply.status===200,json:async()=>reply});`);
  return {run,element};
}
const request=(id,type,extra={})=>({id,request_type:type,status:'PENDING',requester_name:'김민수',
  created_at:'2026-10-09 09:17:00',target_date:'2026-10-12',reason:'가족 병원 진료',
  requester_host:'OFFICE-PC-01',requester_account:'staff01',...extra});

// The new count must follow the displayed pending rows after refresh, not include processed requests.
test('approval list shows pending count and retains each request action identity',async()=>{
  const a=app({requests:[request(41,'SWAP',{swap_date:'2026-10-14',swap_worker:'박준호'}),
    request(52,'SUBSTITUTE',{substitute_name:'이서연',is_zero_score:true}),
    request(63,'ABSENCE',{absence_type:'연차',absence_end_date:'2026-10-16',affectedDates:['2026-10-13','2026-10-15']}),
    request(74,'SWAP',{status:'APPROVED'})]});
  await a.run('openApprovalModal()');
  assert.equal(a.element('approval-count').textContent,'대기 3건');
  assert.ok(a.element('approval-modal').classList.contains('active'));
  const body=a.element('approval-tbody').innerHTML;
  for(const id of [41,52,63]) for(const action of ['APPROVE','REJECT']) {
    assert.ok(body.includes(`processApproval(${id}, '${action}')`));
  }
  assert.doesNotMatch(body,/processApproval\(74/);
  assert.match(body,/박준호/);assert.match(body,/이서연/);
  assert.match(body,/OFFICE-PC-01 · staff01/);
  assert.match(body,/2026-10-09 09:17:00/);
  assert.match(body,/가족 병원 진료/);
  assert.match(body,/승인 시 당직 2건 해제/);
  assert.match(body,/2026-10-13/);assert.match(body,/2026-10-15/);
});
// v3.18.0 [위험] 예전에는 신청자가 '점수 반영'(is_zero_score=false)을 골라도 결재함에 체크된 채 나와,
//   그대로 승인하면 신청자 선택과 반대로 0점 처리됐다. 처음 상태는 신청 값을 따른다.
test('only substitute requests expose zero-score and the box starts from the requester choice',async()=>{
  const a=app({requests:[request(1,'SWAP',{swap_date:'2026-10-14',swap_worker:'박준호'}),
    request(2,'SUBSTITUTE',{substitute_name:'이서연',is_zero_score:1}),
    request(3,'SUBSTITUTE',{substitute_name:'이서연',is_zero_score:0}),
    request(5,'SUBSTITUTE',{substitute_name:'이서연'}),
    request(4,'ABSENCE',{absence_type:'연차',absence_end_date:'2026-10-16'})]});
  await a.run('openApprovalModal()');
  const body=a.element('approval-tbody').innerHTML;
  const box=id=>body.match(new RegExp(`<input[^>]*id="req-zero-${id}"[^>]*>`))?.[0]||'';
  assert.doesNotMatch(body,/id="req-zero-1"/);
  assert.match(box(2),/checked/);
  assert.doesNotMatch(box(3),/checked/);
  assert.match(box(5),/checked/,'old requests without a value keep the server default (0점)');
  assert.doesNotMatch(body,/id="req-zero-4"/);
  assert.equal((body.match(/대체 근무 0점 처리/g)||[]).length,3);
  assert.equal((body.match(/신청자 선택: 0점/g)||[]).length,2);
  assert.equal((body.match(/신청자 선택: 점수 반영/g)||[]).length,1);
  assert.doesNotMatch(body,/주말 0점/);
});
test('server blockers and all user-entered fields stay visible and HTML-escaped',async()=>{
  const a=app({requests:[request(8,'SUBSTITUTE',{requester_name:'<b>김민수</b>',substitute_name:'<img src=x>',
    reason:'<script>bad()</script>',requester_host:'<host>',requester_account:'<account>',
    blocker:'<em>배정이 없습니다</em>'})]});
  await a.run('openApprovalModal()');
  const body=a.element('approval-tbody').innerHTML;
  for(const value of ['&lt;b&gt;김민수&lt;/b&gt;','&lt;img src=x&gt;','&lt;script&gt;bad()&lt;/script&gt;',
    '&lt;host&gt;','&lt;account&gt;','&lt;em&gt;배정이 없습니다&lt;/em&gt;'])assert.ok(body.includes(value),value);
  assert.doesNotMatch(body,/<script>|<img src=x>|<em>배정/);
  assert.ok(body.includes("processApproval(8, 'REJECT')"));
});
test('empty requests refresh the count and show the empty message',async()=>{
  const a=app({requests:[]});a.element('approval-count').textContent='대기 4건';
  await a.run('openApprovalModal()');
  assert.equal(a.element('approval-count').textContent,'대기 0건');
  assert.match(a.element('approval-tbody').innerHTML,/결재 대기 중인 신청이 없습니다/);
});
test('cancelled authentication does not leave an old pending count visible',async()=>{
  const a=app({status:401});a.element('approval-count').textContent='대기 4건';
  await a.run('openApprovalModal()');
  assert.equal(a.element('approval-count').textContent,'확인 필요');
  assert.match(a.element('approval-tbody').innerHTML,/관리자 암호 입력을 취소했습니다/);
});
test('load failures show an error and clear any stale count',async()=>{
  const a=app({status:500});a.element('approval-count').textContent='대기 4건';
  await a.run('openApprovalModal()');
  assert.equal(a.element('approval-count').textContent,'확인 필요');
  // v3.18.0 'HTTP 500' 같은 숫자 코드 대신 할 일을 알려 주는 문장
  assert.match(a.element('approval-tbody').innerHTML,/결재 목록 불러오기 중 오류가 났습니다\. 잠시 후 다시 시도하고, 계속되면 관리자에게 알려 주세요\./);
  assert.doesNotMatch(a.element('approval-tbody').innerHTML,/HTTP|500/);
});

// Cancellation is a separate decision while the existing three types keep 승인.
test('cancellation approval is labelled distinctly and never offers substitute scoring or duty release',async()=>{
 const a=app({requests:[request(91,'ABSENCE_CANCEL',{absence_type:'연차',absence_end_date:'2026-10-16',amends_request_id:20,affectedDates:['2026-10-13']}),request(92,'ABSENCE',{absence_type:'교육'}),request(93,'SWAP',{swap_date:'2026-10-14',swap_worker:'이서연'}),request(94,'SUBSTITUTE',{substitute_name:'박준호'})]});
 await a.run('openApprovalModal()');const body=a.element('approval-tbody').innerHTML;const rows=body.match(/<tr[\s\S]*?<\/tr>/g);
 assert.match(rows[0],/요청 취소 승인/);assert.match(rows[0],/기존 일정 전체가 취소/);assert.doesNotMatch(rows[0],/req-zero|req-affect/);
 for(const row of rows.slice(1)){assert.match(row,/요청 승인/);assert.doesNotMatch(row,/요청 취소 승인/);}
});

test('pending absence kinds keep a distinct badge above the applicant and correct decision labels',async()=>{
 const a=app({requests:[request(101,'ABSENCE',{absence_type:'연차'}),request(102,'ABSENCE',{amends_request_id:90,absence_type:'교육',amendment_original:{start:'2026-10-01',end:'2026-10-02',type:'교육'}}),request(103,'ABSENCE_CANCEL',{amends_request_id:91,absence_type:'연차'})]});
 await a.run('openApprovalModal()');const rows=a.element('approval-tbody').innerHTML.match(/<tr[\s\S]*?<\/tr>/g);
 for(const [i,kind,button,reason]of [[0,'신규 신청','승인','사유'],[1,'일정 변경','변경 승인','변경 사유'],[2,'취소 요청','취소 승인','취소 사유']]){
  assert.ok(rows[i].indexOf(kind)<rows[i].indexOf('<strong>김민수</strong>'));
  assert.match(rows[i],new RegExp(`aria-label="김민수 요청 ${button}"`));
  assert.match(rows[i],new RegExp(`approval-reason-label">${reason}<`));
  assert.ok(rows[i].includes(`processApproval(${101+i}, 'APPROVE')`));
  assert.equal((rows[i].match(/<td>/g)||[]).length,3);
 }
 assert.match(rows[1],/기존 일정 유지/);
});

test('approval periods show weekdays, both years across New Year, and escape invalid date input',async()=>{
 const a=app({requests:[request(101,'ABSENCE',{absence_type:'출장',target_date:'2026-12-31',absence_end_date:'2027-01-04'})]});
 await a.run('openApprovalModal()');const body=a.element('approval-tbody').innerHTML;
 assert.match(body,/2026-12-31\(목\)/);assert.match(body,/2027-01-04\(월\)/);
 assert.match(a.run('approvalPeriodHtml("<img src=x>","<img src=x>")'),/&lt;img src=x&gt;/);
 assert.doesNotMatch(a.run('approvalPeriodHtml("<img src=x>","<img src=x>")'),/<img/);
});
