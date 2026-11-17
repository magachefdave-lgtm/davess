"""Grouped name lookups preserve cancellation lineage without receipt disclosure."""
import json
from test_backend_cancellations import seeded, child
from test_backend_regression import load_app
from test_backend_amendments import approve_original, own


def test_grouped_lookup_keeps_old_parent_and_all_cancellations_beyond_fifty_rows(seeded):
    original = approve_original(seeded)
    parent_id = original['requestId']
    conn = seeded.module.get_db_connection()
    try:
        for _ in range(52):
            conn.execute("INSERT INTO change_requests (requester_name,target_date,request_type,status,created_at,absence_type,absence_end_date) "
                         "VALUES ('A','2030-07-01','ABSENCE','REJECTED','2030-01-01 10:00:00','교육','2030-07-02')")
        conn.commit()
    finally:
        conn.close()
    cancellation = child(seeded, original, reason='private cancel reason')
    ordinary = seeded.get('/api/duty/my-requests?name=A').json()['requests']
    assert len(ordinary) == 50 and parent_id not in [r['id'] for r in ordinary]
    grouped = seeded.get('/api/duty/my-requests?name=A&grouped=1').json()['requests']
    assert parent_id in [r['id'] for r in grouped]
    assert cancellation['requestId'] in [r['id'] for r in grouped]
    assert len([r for r in grouped if r['request_type'] != 'ABSENCE_CANCEL']) == 50


def test_grouped_lookup_includes_retries_but_never_discloses_private_cancellation_fields(seeded):
    original = approve_original(seeded, reason='private original reason')
    rejected = child(seeded, original, reason='private cancellation reason')
    assert seeded.action(rejected['requestId'], 'REJECT', reject_reason='private rejection reason').status_code == 200
    pending = child(seeded, original)
    response = seeded.get('/api/duty/my-requests?name=A&grouped=1')
    assert response.status_code == 200
    rows = response.json()['requests']
    assert {r['id'] for r in rows} == {original['requestId'], rejected['requestId'], pending['requestId']}
    for secret in ['private original reason','private cancellation reason','private rejection reason',original['receiptToken'],rejected['receiptToken']]:
        assert secret not in json.dumps(rows)
    assert own(seeded,rejected)['reject_reason'] == 'private rejection reason'
    assert seeded.get('/api/duty/my-requests?name=B&grouped=1').json()['requests'] == []
