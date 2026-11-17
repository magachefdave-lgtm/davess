import hashlib
import pytest
from tests.test_request_access_schema import database
from backend.request_access_schema import *
from backend.request_recovery import *


def setup():
    c=database(); cur=c.cursor(); identity=read_dataset_identity(cur)
    r=create_recovery(cur,identity,'Kim','device',hashlib.sha256(b'proof').hexdigest(),'create',1000)
    uid=ensure_request_uid(cur,1); return c,cur,r,uid


def approve(cur,r,uid):
    scope=preview_recovery(cur,r.id,[uid]); assert not scope.blockers
    return decide_recovery(cur,r.id,'APPROVE',[uid],scope.digest,'approve','checked',1001)


def test_same_claim_retries_after_lost_response():
    c,cur,r,uid=setup(); approve(cur,r,uid)
    a=claim_recovery(cur,r.id,'proof','claim',1002); b=claim_recovery(cur,r.id,'proof','claim',1003)
    assert a==b and a.receipts[0]['receipt_token']=='old'
    assert complete_recovery(cur,r.id,'proof','claim',receipt_bundle_digest(a),1004).state=='COMPLETED'
    assert complete_recovery(cur,r.id,'proof','claim',receipt_bundle_digest(a),1005).state=='COMPLETED'


def test_scope_growth_requires_new_review():
    c,cur,r,uid=setup(); approve(cur,r,uid)
    c.execute("INSERT INTO change_requests VALUES(2,'Kim','ABSENCE_AMEND','PENDING','new',1,'2027-02-01','child')")
    with pytest.raises(AccessError,match='SCOPE_CHANGED'): claim_recovery(cur,r.id,'proof','claim',1002)


def test_other_device_cannot_claim():
    c,cur,r,uid=setup(); approve(cur,r,uid)
    with pytest.raises(AccessError) as e: claim_recovery(cur,r.id,'wrong','claim',1002)
    assert e.value.status==403


def test_two_admins_decide_once():
    c,cur,r,uid=setup(); a=approve(cur,r,uid); assert approve(cur,r,uid)==a
    with pytest.raises(AccessError): decide_recovery(cur,r.id,'REJECT',[],'','other','no',1002)


def test_restore_invalidates_recovery_and_reused_id():
    c,cur,r,uid=setup(); approve(cur,r,uid); after_access_restore(cur)
    assert cur.execute('SELECT state FROM request_recoveries').fetchone()[0]=='INVALIDATED'
    with pytest.raises(AccessError): claim_recovery(cur,r.id,'proof','claim',1002)


def test_legacy_null_receipt_requires_approval():
    c,cur,r,uid=setup(); cur.execute('UPDATE change_requests SET receipt_token=NULL')
    with pytest.raises(AccessError): claim_recovery(cur,r.id,'proof','claim',1001)
    assert cur.execute('SELECT receipt_token FROM change_requests').fetchone()[0] is None
    approve(cur,r,uid); assert claim_recovery(cur,r.id,'proof','claim',1002).receipts[0]['receipt_token']


def test_expiry_and_clock_skew():
    c,cur,r,uid=setup()
    with pytest.raises(AccessError,match='CLOCK_SKEW'): cancel_recovery(cur,r.id,'proof',699)
    with pytest.raises(AccessError,match='EXPIRED'): cancel_recovery(cur,r.id,'proof',87400)
