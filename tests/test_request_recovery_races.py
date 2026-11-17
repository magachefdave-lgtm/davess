"""Deterministic interleavings; production serializes these with BEGIN IMMEDIATE."""
import pytest
from tests.test_request_recovery import setup, approve
from backend.request_access_schema import AccessError, ensure_request_uid, after_access_restore
from backend.request_recovery import *


def add(cur,id,name='Kim',parent=None,kind='ABSENCE',token='key'):
    cur.execute('INSERT INTO change_requests(id,requester_name,request_type,status,receipt_token,amends_request_id,target_date) VALUES(?,?,?,?,?,?,?)',(id,name,kind,'PENDING',token,parent,'2027-02-01'))
    return ensure_request_uid(cur,id)


def test_only_selected_same_person_lineages_are_claimed():
    c,cur,r,uid=setup(); child=add(cur,2,parent=1,kind='ABSENCE_AMEND',token='child'); add(cur,3,name='Lee'); add(cur,4)
    approve(cur,r,uid); bundle=claim_recovery(cur,r.id,'proof','claim',1002)
    assert {x['request_uid'] for x in bundle.receipts}=={uid,child}


def test_corrupt_other_person_link_blocks_whole_group():
    c,cur,r,uid=setup(); add(cur,2,name='Lee',parent=1,kind='ABSENCE_CANCEL')
    scope=preview_recovery(cur,r.id,[uid]); assert scope.blockers and not scope.items
    with pytest.raises(AccessError): decide_recovery(cur,r.id,'APPROVE',[uid],scope.digest,'op','',1001)


def test_cycle_and_missing_parent_are_not_candidates():
    c,cur,r,uid=setup(); a=add(cur,2,parent=3); b=add(cur,3,parent=2); orphan=add(cur,4,parent=999)
    assert preview_recovery(cur,r.id,[a,b,orphan]).blockers
    assert {x['request_uid'] for x in preview_recovery(cur,r.id,[]).items}=={uid}


def test_claim_does_not_mutate_schedules_or_business_status():
    c,cur,r,uid=setup(); c.execute('CREATE TABLE schedules(value TEXT)'); c.execute("INSERT INTO schedules VALUES('unchanged')")
    before=dict(cur.execute('SELECT * FROM change_requests').fetchone()); approve(cur,r,uid); claim_recovery(cur,r.id,'proof','claim',1002)
    assert before==dict(cur.execute('SELECT * FROM change_requests').fetchone())
    assert cur.execute('SELECT value FROM schedules').fetchone()[0]=='unchanged'


def test_status_change_after_approval_is_allowed_but_review_must_be_current():
    c,cur,r,uid=setup(); scope=preview_recovery(cur,r.id,[uid]); cur.execute("UPDATE change_requests SET status='REJECTED'")
    with pytest.raises(AccessError,match='SCOPE_CHANGED'): decide_recovery(cur,r.id,'APPROVE',[uid],scope.digest,'bad','',1001)
    approve(cur,r,uid); cur.execute("UPDATE change_requests SET status='CANCELLED'")
    bundle=claim_recovery(cur,r.id,'proof','claim',1002); assert bundle.receipts[0]['status']=='CANCELLED'


def test_uid_reuse_and_receipt_rotation_detected():
    c,cur,r,uid=setup(); approve(cur,r,uid); cur.execute("UPDATE change_requests SET request_uid='different'")
    with pytest.raises(AccessError,match='SCOPE_CHANGED'): claim_recovery(cur,r.id,'proof','claim',1002)


def test_claim_retry_deadline_and_different_claim_id():
    c,cur,r,uid=setup(); approve(cur,r,uid); bundle=claim_recovery(cur,r.id,'proof','claim',87399)
    assert claim_recovery(cur,r.id,'proof','claim',87401)==bundle
    with pytest.raises(AccessError,match='OPERATION_CONFLICT'): claim_recovery(cur,r.id,'proof','other',87401)
    with pytest.raises(AccessError,match='EXPIRED'): claim_recovery(cur,r.id,'proof','claim',87399+86400)


def test_completion_requires_matching_digest_and_is_invalidated_by_restore():
    c,cur,r,uid=setup(); approve(cur,r,uid); bundle=claim_recovery(cur,r.id,'proof','claim',1002)
    with pytest.raises(AccessError,match='RECEIPT_MISMATCH'): complete_recovery(cur,r.id,'proof','claim','wrong',1003)
    complete_recovery(cur,r.id,'proof','claim',receipt_bundle_digest(bundle),1003); after_access_restore(cur)
    assert cur.execute('SELECT state FROM request_recoveries').fetchone()[0]=='COMPLETED'
    with pytest.raises(AccessError,match='DATASET_CHANGED'): complete_recovery(cur,r.id,'proof','claim',receipt_bundle_digest(bundle),1004)


def test_decision_operation_conflict_and_empty_selection():
    c,cur,r,uid=setup()
    with pytest.raises(AccessError,match='INVALID_SCOPE'): decide_recovery(cur,r.id,'APPROVE',[],'','op','',1001)
    approve(cur,r,uid)
    with pytest.raises(AccessError,match='OPERATION_CONFLICT'): decide_recovery(cur,r.id,'REJECT',[],'','approve','',1001)


def test_duplicate_create_and_no_secret_in_audit():
    c,cur,r,uid=setup(); identity=read_dataset_identity(cur)
    assert create_recovery(cur,identity,'Kim','device',hashlib.sha256(b'proof').hexdigest(),'create',1001).id==r.id
    assert create_recovery(cur,identity,'Kim','device',hashlib.sha256(b'proof').hexdigest(),'other',1001).id==r.id
    approve(cur,r,uid); claim_recovery(cur,r.id,'proof','claim',1002)
    for event in cur.execute('SELECT safe_result_json FROM request_recovery_events'):
        assert 'receipt_token' not in event[0] and 'proof' not in event[0]
