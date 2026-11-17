"""Selected-lineage recovery; caller owns BEGIN IMMEDIATE, write guard and commit.

No receipt, proof or full bundle is stored in the append-only audit events.
"""
from dataclasses import dataclass, asdict
import hashlib
import hmac
import json
import secrets
import uuid
from .request_access_schema import AccessError, read_dataset_identity, ensure_request_uid

TTL=86400
ACTIVE=('PENDING','APPROVED','CLAIMED')
KINDS=('ABSENCE','ABSENCE_AMEND','ABSENCE_CANCEL')

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

@dataclass(frozen=True)
class RecoveryResult:
    id: str
    state: str
    expires_at: int
    version: int
    reason: str=''
    def as_dict(self):
        return dict(id=self.id,state=self.state,expiresAt=self.expires_at,version=self.version,reason=self.reason)

@dataclass(frozen=True)
class RecoveryScope:
    items: list
    digest: str
    blockers: list
    def as_dict(self): return asdict(self)

@dataclass(frozen=True)
class ClaimBundle:
    recovery_id: str
    claim_id: str
    dataset_id: str
    generation: str
    receipts: list
    def as_dict(self): return asdict(self)


def receipt_bundle_digest(bundle):
    data=bundle.as_dict() if hasattr(bundle,'as_dict') else bundle
    receipts=data['receipts'] if isinstance(data,dict) else data
    return digest(sorted([{k:r[k] for k in ('request_uid','request_id','receipt_token')} for r in receipts],key=lambda r:r['request_uid']))


def _get(cur,rid):
    row=cur.execute('SELECT * FROM request_recoveries WHERE id=?',(rid,)).fetchone()
    if row is None: raise AccessError('REQUEST_UNRESOLVED','복구 요청을 확인할 수 없습니다.')
    return dict(row)


def _result(row):
    return RecoveryResult(row['id'],row['state'],row['expires_at'],row['version'],row.get('reason') or '')


def _check(cur,row,now):
    identity=read_dataset_identity(cur)
    if (row['dataset_id'],row['generation'])!=(identity.dataset_id,identity.generation) or row['state']=='INVALIDATED':
        raise AccessError('DATASET_CHANGED','자료가 변경되어 복구 요청을 다시 확인해야 합니다.')
    if row['created_at']>now+300 or (row.get('claimed_at') is not None and row['claimed_at']>now+300):
        raise AccessError('CLOCK_SKEW','PC 시간을 확인해 주세요.')
    deadline=row['claim_deadline'] if row['state'] in ('CLAIMED','COMPLETED') else row['expires_at']
    if row['state']=='EXPIRED' or now>=deadline:
        if row['state'] in ACTIVE:
            cur.execute("UPDATE request_recoveries SET state='EXPIRED',version=version+1 WHERE id=?",(row['id'],))
        raise AccessError('EXPIRED','복구 요청이 만료되었습니다.',410)


def _proof(row,proof):
    if not isinstance(proof,str) or not hmac.compare_digest(hashlib.sha256(proof.encode()).hexdigest(),row['device_proof_hash']):
        raise AccessError('FORBIDDEN','요청한 PC에서만 수령할 수 있습니다.',403)


def _event(cur,row,action,operation_id,input_digest,now,reason='',actor_label='',host_label=''):
    result=_result(row)
    cur.execute('INSERT INTO request_recovery_events(event_id,recovery_id,action,actor_label,host_label,at,reason,operation_id,input_digest,safe_result_json) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (str(uuid.uuid4()),row['id'],action,actor_label,host_label,now,reason,operation_id,input_digest,json.dumps(asdict(result))))
    return result


def _replay(cur,rid,operation_id,input_digest):
    event=cur.execute('SELECT input_digest,safe_result_json FROM request_recovery_events WHERE recovery_id=? AND operation_id=?',(rid,operation_id)).fetchone()
    if event:
        if event[0]!=input_digest: raise AccessError('OPERATION_CONFLICT','같은 처리 번호의 내용이 다릅니다.')
        return RecoveryResult(**json.loads(event[1]))


def create_recovery(cur,identity,requested_name,device_id,proof_hash,operation_id,now,*,host_label='',account_label=''):
    if identity!=read_dataset_identity(cur): raise AccessError('DATASET_CHANGED','연결 자료가 변경되었습니다.')
    if not requested_name or not requested_name.strip() or not device_id or not operation_id or len(proof_hash)!=64:
        raise AccessError('INVALID_INPUT','이름과 PC 확인정보가 필요합니다.')
    try: bytes.fromhex(proof_hash)
    except ValueError: raise AccessError('INVALID_INPUT','PC 확인정보가 올바르지 않습니다.')
    requested_name=requested_name.strip()
    inp=digest([identity.as_dict(),requested_name,device_id,proof_hash,host_label,account_label])
    # Creation operation IDs are scoped to the requesting device and dataset.
    for e in cur.execute("SELECT e.recovery_id,e.input_digest,e.safe_result_json FROM request_recovery_events e JOIN request_recoveries r ON r.id=e.recovery_id WHERE e.operation_id=? AND e.action='CREATE' AND r.dataset_id=? AND r.device_id=?",(operation_id,identity.dataset_id,device_id)).fetchall():
        if e[1]!=inp: raise AccessError('OPERATION_CONFLICT','같은 처리 번호의 내용이 다릅니다.')
        row=_get(cur,e[0]); _check(cur,row,now); return _result(row)
    rows=cur.execute("SELECT * FROM request_recoveries WHERE dataset_id=? AND generation=? AND device_id=? AND requested_name=? AND state IN ('PENDING','APPROVED','CLAIMED')",(identity.dataset_id,identity.generation,device_id,requested_name)).fetchall()
    for existing in rows:
        row=dict(existing)
        if row['created_at']>now+300: raise AccessError('CLOCK_SKEW','PC 시간을 확인해 주세요.')
        deadline=row['claim_deadline'] if row['state']=='CLAIMED' else row['expires_at']
        if now<deadline:
            if not hmac.compare_digest(row['device_proof_hash'],proof_hash): raise AccessError('RECOVERY_IN_PROGRESS','이 PC에서 진행 중인 복구 요청이 있습니다.')
            return _event(cur,row,'CREATE',operation_id,inp,now)
        cur.execute("UPDATE request_recoveries SET state='EXPIRED',version=version+1 WHERE id=?",(row['id'],))
    rid=str(uuid.uuid4())
    cur.execute('INSERT INTO request_recoveries(id,dataset_id,generation,requested_name,device_id,device_proof_hash,host_label,account_label,state,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(rid,identity.dataset_id,identity.generation,requested_name,device_id,proof_hash,host_label,account_label,'PENDING',now,now+TTL))
    return _event(cur,_get(cur,rid),'CREATE',operation_id,inp,now,actor_label=account_label,host_label=host_label)


def _identity(row):
    # Status and mutable request text may change after review; lineage identity may not.
    return digest([row['request_uid'],row['id'],row.get('requester_name'),row.get('request_type'),row.get('amends_request_id')])


def preview_recovery(cur,recovery_id,root_uids):
    recovery=_get(cur,recovery_id)
    identity=read_dataset_identity(cur)
    if (identity.dataset_id,identity.generation)!=(recovery['dataset_id'],recovery['generation']): raise AccessError('DATASET_CHANGED','연결 자료가 변경되었습니다.')
    for r in cur.execute("SELECT id FROM change_requests WHERE request_uid IS NULL OR request_uid=''").fetchall(): ensure_request_uid(cur,r[0])
    rows=[dict(r) for r in cur.execute('SELECT * FROM change_requests').fetchall()]
    byid={r['id']:r for r in rows}; byuid={r['request_uid']:r for r in rows}
    children={}
    for r in rows:
        if r.get('amends_request_id') is not None: children.setdefault(r['amends_request_id'],[]).append(r)
    blockers=[]; items=[]
    if not isinstance(root_uids,(list,tuple)) or len(set(root_uids))!=len(root_uids): raise AccessError('INVALID_SCOPE','신청 그룹을 명시적으로 선택해 주세요.')
    selected=bool(root_uids)
    roots=root_uids or [r['request_uid'] for r in rows if r.get('requester_name')==recovery['requested_name'] and r.get('request_type') in KINDS and r.get('amends_request_id') is None]
    for uid in roots:
        root=byuid.get(uid); group=[]; visiting=set(); seen=set(); problems=[]
        if not root or root.get('amends_request_id') is not None:
            blockers.append(dict(rootUid=uid,code='INVALID_ROOT',detail='신청 원본을 확인할 수 없습니다.')); continue
        def walk(row):
            if row['id'] in visiting: problems.append('CYCLE'); return
            if row['id'] in seen: return
            if row.get('requester_name')!=recovery['requested_name'] or row.get('request_type') not in KINDS:
                problems.append('INVALID_LINK'); return
            visiting.add(row['id']); group.append(row)
            for child in children.get(row['id'],[]): walk(child)
            visiting.remove(row['id']); seen.add(row['id'])
        walk(root)
        if problems:
            blockers.append(dict(rootUid=uid,code=problems[0],detail='다른 직원 또는 손상된 신청 연결을 확인해 주세요.')); continue
        for row in group:
            items.append(dict(request_uid=row['request_uid'],request_id=row['id'],root_uid=uid,identity_digest=_identity(row),requester_name=row.get('requester_name'),request_type=row.get('request_type'),status=row.get('status'),target_date=row.get('target_date'),absence_end_date=row.get('absence_end_date'),absence_type=row.get('absence_type'),created_at=row.get('created_at'),selected=selected))
    if not selected:
        # Malformed components with no root must remain visible as exclusions.
        for candidate in rows:
            if candidate.get('requester_name')!=recovery['requested_name'] or candidate.get('request_type') not in KINDS:
                continue
            current=candidate; ancestors=set(); problem=None
            while current.get('amends_request_id') is not None:
                if current['id'] in ancestors:
                    problem='CYCLE'; break
                ancestors.add(current['id'])
                parent=byid.get(current['amends_request_id'])
                if parent is None:
                    problem='ORPHAN'; break
                if parent.get('requester_name')!=recovery['requested_name'] or parent.get('request_type') not in KINDS:
                    problem='INVALID_LINK'; break
                current=parent
            if problem and not any(b['rootUid']==candidate['request_uid'] for b in blockers):
                blockers.append(dict(rootUid=candidate['request_uid'],code=problem,
                    detail=f"신청 {candidate['id']}: 원본이 없거나 신청 연결이 손상되어 자동 복구할 수 없습니다."))
    items.sort(key=lambda r:r['request_id'])
    return RecoveryScope(items,digest(items),blockers)


def decide_recovery(cur,recovery_id,action,root_uids,expected_digest,operation_id,reason,now,*,actor_label='',host_label=''):
    if action not in ('APPROVE','REJECT') or not operation_id: raise AccessError('INVALID_INPUT','처리 내용을 확인해 주세요.')
    inp=digest([action,sorted(root_uids),expected_digest,reason])
    row=_get(cur,recovery_id); _check(cur,row,now)
    replay=_replay(cur,recovery_id,operation_id,inp)
    if replay: return replay
    if row['state']!='PENDING': raise AccessError('STATE_CHANGED','이미 처리된 복구 요청입니다.')
    if action=='APPROVE':
        if not root_uids: raise AccessError('INVALID_SCOPE','복구할 신청 그룹을 선택해 주세요.')
        scope=preview_recovery(cur,recovery_id,root_uids)
        if scope.blockers or not scope.items or scope.digest!=expected_digest: raise AccessError('SCOPE_CHANGED','대상 신청이 변경되었습니다. 다시 확인해 주세요.')
        for item in scope.items:
            cur.execute('INSERT INTO request_recovery_items(recovery_id,request_uid,request_id,identity_digest,root_uid) VALUES(?,?,?,?,?)',(recovery_id,item['request_uid'],item['request_id'],item['identity_digest'],item['root_uid']))
    cur.execute('UPDATE request_recoveries SET state=?,review_digest=?,reason=?,version=version+1 WHERE id=? AND state=? AND version=?',('APPROVED' if action=='APPROVE' else 'REJECTED',expected_digest,reason,recovery_id,'PENDING',row['version']))
    if cur.rowcount!=1: raise AccessError('STATE_CHANGED','다른 관리자가 처리했습니다.')
    return _event(cur,_get(cur,recovery_id),action,operation_id,inp,now,reason,actor_label,host_label)


def _scope_valid(cur,row):
    approved=[dict(r) for r in cur.execute('SELECT * FROM request_recovery_items WHERE recovery_id=?',(row['id'],)).fetchall()]
    scope=preview_recovery(cur,row['id'],sorted({r['root_uid'] for r in approved}))
    actual={(r['request_uid'],r['request_id'],r['identity_digest'],r['root_uid']) for r in scope.items}
    expected={(r['request_uid'],r['request_id'],r['identity_digest'],r['root_uid']) for r in approved}
    if not expected or scope.blockers or actual!=expected: raise AccessError('SCOPE_CHANGED','선택한 신청 연결이 변경되었습니다. 관리자 재확인이 필요합니다.')
    return scope


def _bundle(cur,row):
    receipts=[]
    for item in cur.execute('SELECT i.request_uid,i.request_id,c.receipt_token,c.status FROM request_recovery_items i JOIN change_requests c ON c.id=i.request_id AND c.request_uid=i.request_uid WHERE i.recovery_id=? ORDER BY i.request_id',(row['id'],)).fetchall():
        receipts.append(dict(dataset_id=row['dataset_id'],request_uid=item[0],request_id=item[1],receipt_token=item[2],status=item[3]))
    return ClaimBundle(row['id'],row['claim_id'],row['dataset_id'],row['generation'],receipts)


def claim_recovery(cur,recovery_id,proof,claim_id,now):
    row=_get(cur,recovery_id); _proof(row,proof); _check(cur,row,now)
    if not claim_id: raise AccessError('INVALID_INPUT','수령 처리 번호가 필요합니다.')
    if row['state'] in ('CLAIMED','COMPLETED'):
        if row['claim_id']!=claim_id: raise AccessError('OPERATION_CONFLICT','다른 수령 처리 번호입니다.')
        _scope_valid(cur,row)
        bundle=_bundle(cur,row)
        if receipt_bundle_digest(bundle)!=row['receipt_digest']:
            raise AccessError('DATASET_CHANGED','수령 권한정보가 변경되었습니다.')
        return bundle
    if row['state']!='APPROVED': raise AccessError('STATE_CHANGED','관리자 승인이 필요합니다.')
    scope=_scope_valid(cur,row)
    for item in scope.items:
        cur.execute('UPDATE change_requests SET receipt_token=? WHERE id=? AND request_uid=? AND receipt_token IS NULL',(secrets.token_urlsafe(32),item['request_id'],item['request_uid']))
        token=cur.execute('SELECT receipt_token FROM change_requests WHERE id=?',(item['request_id'],)).fetchone()[0]
        if not token: raise AccessError('REQUEST_UNRESOLVED','접수 키를 확인할 수 없습니다.')
    cur.execute("UPDATE request_recoveries SET state='CLAIMED',claim_id=?,claimed_at=?,claim_deadline=?,version=version+1 WHERE id=?",(claim_id,now,now+TTL,recovery_id))
    row=_get(cur,recovery_id); bundle=_bundle(cur,row)
    cur.execute('UPDATE request_recoveries SET receipt_digest=? WHERE id=?',(receipt_bundle_digest(bundle),recovery_id))
    _event(cur,row,'CLAIM',None,None,now)

    return bundle


def complete_recovery(cur,recovery_id,proof,claim_id,receipt_digest,now):
    row=_get(cur,recovery_id); _proof(row,proof); _check(cur,row,now)
    if row['state'] not in ('CLAIMED','COMPLETED') or row['claim_id']!=claim_id: raise AccessError('STATE_CHANGED','수령 정보를 확인해 주세요.')
    if not hmac.compare_digest(row['receipt_digest'] or '',receipt_digest or ''): raise AccessError('RECEIPT_MISMATCH','개인 저장정보 검증이 일치하지 않습니다.')
    claim_recovery(cur,recovery_id,proof,claim_id,now)
    if row['state']=='COMPLETED': return _result(row)
    cur.execute("UPDATE request_recoveries SET state='COMPLETED',completed_at=?,version=version+1 WHERE id=?",(now,recovery_id))
    return _event(cur,_get(cur,recovery_id),'COMPLETE',None,None,now)


def cancel_recovery(cur,recovery_id,proof,now):
    row=_get(cur,recovery_id); _proof(row,proof); _check(cur,row,now)
    if row['state']=='CANCELLED': return _result(row)
    if row['state'] not in ('PENDING','APPROVED'): raise AccessError('STATE_CHANGED','이미 수령한 복구 요청은 철회할 수 없습니다.')
    cur.execute("UPDATE request_recoveries SET state='CANCELLED',version=version+1 WHERE id=?",(recovery_id,))
    return _event(cur,_get(cur,recovery_id),'CANCEL',None,None,now)
