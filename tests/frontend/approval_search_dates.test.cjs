'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {app}=require('./helpers/requests_app.cjs');
const request=(id,extra={})=>({id,status:'PENDING',request_type:'ABSENCE',requester_name:'A',absence_type:'교육',target_date:'2026-12-29',absence_end_date:'2027-01-04',...extra});
function search(a,rows,query){a.ctx.queue=rows;return a.json(`window.OnAirRequests.filterApproval(queue,{query:${JSON.stringify(query)}}).map(r=>r.id)`);}

test('new absence searches every visible period endpoint using ISO dates and displayed weekdays',()=>{
 const a=app(),rows=[request(1)];
 for(const query of ['2026-12-29','2027-01-04','2026-12-29(화)','2027-01-04(월)'])assert.deepEqual(search(a,rows,query),[1],query);
});

test('amendment searches original dates, actual original segments, and requested dates',()=>{
 const a=app(),rows=[request(2,{amends_request_id:20,amendment_original:{type:'연차',start:'2026-11-02',end:'2026-11-13',segments:[{start:'2026-11-02',end:'2026-11-06'},{start:'2026-11-09',end:'2026-11-13'}]}})];
 for(const query of ['2026-11-02','2026-11-13','2026-11-06(금)','2026-11-09(월)','2026-12-29(화)','2027-01-04(월)'])assert.deepEqual(search(a,rows,query),[2],query);
});

test('legacy amendment without an original snapshot searches its displayed requested period',()=>{
 const a=app(),rows=[request(7,{amends_request_id:20})];
 assert.deepEqual(search(a,rows,'2027-01-04(월)'),[7]);
});

test('cancellation searches the ranges actually shown and falls back to request endpoints',()=>{
 const a=app(),rows=[request(3,{request_type:'ABSENCE_CANCEL',amendment_original:{type:'교육',start:'2026-11-02',end:'2026-11-13',segments:[{start:'2026-11-02',end:'2026-11-06'},{start:'2026-11-09',end:'2026-11-13'}]}}),request(4,{request_type:'ABSENCE_CANCEL'})];
 for(const query of ['2026-11-02(월)','2026-11-06(금)','2026-11-09(월)','2026-11-13(금)'])assert.deepEqual(search(a,rows,query),[3],query);
 assert.deepEqual(search(a,rows,'2027-01-04(월)'),[4]);
});

test('search includes education text without exposing JSON keys or mutating filtered rows',()=>{
 const a=app(),rows=[request(5,{absence_details:{title:'안전 연수',notes:'회의 준비'}}),request(6,{status:'APPROVED'})],before=JSON.stringify(rows);
 assert.deepEqual(search(a,rows,'안전 연수'),[5]);assert.deepEqual(search(a,rows,'회의 준비'),[5]);assert.deepEqual(search(a,rows,'"title"'),[]);assert.equal(JSON.stringify(rows),before);
 assert.deepEqual(search(a,rows,'2027-01-04'),[5]);
});
