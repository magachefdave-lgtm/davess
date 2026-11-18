"""HTTP dinner contract and authorization against isolated real SQLite databases."""
import copy
import importlib.util
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from functools import partial

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Mount

from test_backend_regression import AUTH, SEED, load_app


PREFIX = '/api/dinner'
CONTRACT = {'X-OnAir-Data-Contract': '1'}
KST = timezone(timedelta(hours=9))


@pytest.fixture
def dinner_site(load_app):
    site = load_app('source')
    # Keep this fixture usable before and after main.py's additive integration.
    if importlib.util.find_spec('backend.dinner_api'):
        from backend import dinner_api, dinner_schema
        if not any(getattr(r, 'path', None) == PREFIX + '/summary' for r in site.module.app.routes):
            dinner_api.install(site.module.app, site.module.__dict__)
            routes = site.module.app.router.routes
            routes[:] = [r for r in routes if not isinstance(r, Mount)] + [r for r in routes if isinstance(r, Mount)]
        with site.module.get_db_connection(readonly=False) as conn:
            conn.execute('BEGIN IMMEDIATE')
            dinner_schema.ensure_schema(conn.cursor())
            conn.commit()
    site.save(copy.deepcopy(SEED))
    return site


def op(**values):
    return dict(operationId=str(uuid.uuid4()), **values)


def key_headers(key):
    return dict(CONTRACT, **{'X-OnAir-Dinner-Key': key})


def send(site, path, body, *, key=None, admin=True, method='post'):
    headers = dict(CONTRACT)
    if admin:
        headers.update(AUTH)
    if key:
        headers.update(key_headers(key))
    return getattr(site.client, method)(PREFIX + path, json=body, headers=headers)


def dates():
    today = datetime.now(KST).date()
    return [(today + timedelta(days=n)).isoformat() for n in (1, 10, 11, 30)]


def assign(site):
    days = dates()
    response = send(site, '/role', op(memberName='A',
        startDate=datetime.now(KST).date().isoformat(),
        endDate=days[3], expectedRevision=0))
    assert response.status_code == 200, response.text
    return response.json()['role']


def assign_year(site, year, name='A', revision=0):
    response = send(site, '/role', op(roleId=year, memberName=name,
        startDate=f'{year}-01-01', endDate=f'{year}-12-31', expectedRevision=revision))
    assert response.status_code == 200, response.text
    return response.json()['role']


def test_annual_roles_and_future_connection_use_separate_http_bindings(dinner_site, monkeypatch):
    from backend import dinner_service

    site = dinner_site
    year = datetime.now(KST).year
    monkeypatch.setattr(dinner_service, 'DinnerService',
                        partial(dinner_service.DinnerService, now=datetime(year, 11, 18, tzinfo=KST)))
    assign_year(site, year)
    assign_year(site, year + 1, 'B')
    key = secrets.token_urlsafe(32)
    connection = send(site, '/connections', op(memberName='B'), key=key, admin=False).json()['connection']
    assert connection['roleId'] == year + 1
    summary = site.client.get(PREFIX + '/summary?name=B', headers=AUTH).json()
    assert len(summary['roles']) == 2 and summary['role']['roleId'] == year
    assert summary['assignedRole']['roleId'] == year + 1
    assert summary['pendingConnections'][0]['id'] == connection['id']
    approved = send(site, '/connections/' + connection['id'] + '/decision', op(decision='APPROVE'))
    assert approved.status_code == 200, approved.text
    assert approved.json()['connection']['active'] is False
    assert site.client.get(PREFIX + '/manage', headers=key_headers(key)).status_code == 403
    overlap = send(site, '/role', op(roleId=year + 1, memberName='B', startDate=f'{year}-12-31',
                                    endDate=f'{year+1}-12-31', expectedRevision=1))
    assert overlap.status_code == 409
    managed = site.client.get(PREFIX + '/manage', headers=AUTH).json()
    assert managed['roles'][1]['revision'] == 1
    assert managed['connections'][0]['status'] == 'APPROVED'


def test_removed_and_readded_member_does_not_reactivate_annual_ca_key(dinner_site):
    site = dinner_site
    assign_year(site, datetime.now(KST).year)
    key = secrets.token_urlsafe(32)
    connection = send(site, '/connections', op(memberName='A'), key=key, admin=False).json()['connection']
    assert send(site, '/connections/' + connection['id'] + '/decision', op(decision='APPROVE')).status_code == 200
    members = site.data()['members']
    site.save({'members': [m for m in members if m['name'] != 'A']})
    site.save({'members': members})
    assert site.client.get(PREFIX + '/manage', headers=key_headers(key)).status_code == 403


def test_summary_exposes_only_own_selected_member_connections_for_both_terms(dinner_site):
    site = dinner_site
    year = datetime.now(KST).year
    assign_year(site, year)
    assign_year(site, year + 1)
    key = secrets.token_urlsafe(32)
    own = {}
    for role_id in (year, year + 1):
        response = send(site, '/connections', op(roleId=role_id, memberName='A'), key=key, admin=False)
        assert response.status_code == 200, response.text
        own[str(role_id)] = response.json()['connection']
    summary = site.client.get(PREFIX + '/summary?name=A', headers=key_headers(key)).json()
    assert summary['ownConnections'] == own
    assert summary['assignedRole']['roleId'] == year
    assert site.client.get(PREFIX + '/summary?name=B', headers=key_headers(key)).json()['ownConnections'] == {}
    foreign = site.client.get(PREFIX + '/summary?name=A', headers=key_headers(secrets.token_urlsafe(32))).json()
    assert foreign['ownConnections'] == {} and foreign['connection'] is None
    assert 'verificationCode' not in str(foreign)


def draft(site):
    days = dates()
    response = send(site, '/polls', op(title='11월 회식 날짜', description='모두 가능한 날을 고릅니다.', deadline=days[0] + 'T18:00',
        candidates=[{'id':'first', 'date':days[1], 'time':'18:00'}, {'id':'second', 'date':days[2], 'time':'18:30'}], targets=['A','B','C']))
    assert response.status_code == 200, response.text
    return response.json()['poll']


def publish(site):
    poll = draft(site)
    response = send(site, '/polls/' + poll['id'] + '/action', op(action='PUBLISH', expectedRevision=poll['revision']))
    assert response.status_code == 200, response.text
    return response.json()['poll']


def test_summary_is_available_without_credentials_and_never_cacheable(dinner_site):
    response = dinner_site.client.get(PREFIX + '/summary?name=A')
    assert response.status_code == 200, response.text
    assert set(('datasetKey','role','connection','polls','events','pendingConnections')) <= response.json().keys()
    assert response.headers['cache-control'] == 'no-store'


def test_admin_password_and_viewer_mode_cannot_be_bypassed(dinner_site, monkeypatch):
    site = dinner_site
    assert site.client.get(PREFIX + '/manage').status_code in (401,403)
    response = send(site, '/role', op(memberName='', expectedRevision=0), admin=False)
    assert response.status_code == 401
    monkeypatch.setattr(site.module, 'APP_MODE', 'VIEWER')
    assert send(site, '/role', op(memberName='', expectedRevision=0)).status_code == 403


def test_remote_passwordless_admin_is_denied_but_local_admin_is_allowed(dinner_site, monkeypatch):
    site = dinner_site
    monkeypatch.setattr(site.module, 'ADMIN_PASSWORD', '')
    remote = TestClient(site.module.app, client=('192.0.2.31', 5000))
    response = remote.post(PREFIX + '/role', json=op(memberName='', expectedRevision=0), headers=CONTRACT)
    assert response.status_code == 403
    assert remote.get(PREFIX + '/manage').status_code in (401,403)
    assert site.client.get(PREFIX + '/manage').status_code == 200


def test_ca_name_and_pending_connection_do_not_authorize_management(dinner_site):
    site = dinner_site
    assign(site)
    key = secrets.token_urlsafe(32)
    assert site.client.get(PREFIX + '/manage?name=A', headers=key_headers(key)).status_code in (401,403)
    connection = send(site, '/connections', op(memberName='A', deviceLabel='합성 PC'), key=key, admin=False)
    assert connection.status_code == 200, connection.text
    assert connection.json()['connection']['status'] == 'PENDING'
    assert site.client.get(PREFIX + '/manage', headers=key_headers(key)).status_code in (401,403)
    approved = send(site, '/connections/' + connection.json()['connection']['id'] + '/decision', op(decision='APPROVE', reason='확인'))
    assert approved.status_code == 200, approved.text
    assert site.client.get(PREFIX + '/manage', headers=key_headers(key)).status_code == 200
    current = site.client.get(PREFIX + '/manage', headers=AUTH).json()['role']
    revoked = send(site, '/role', op(memberName='', expectedRevision=current['revision']))
    assert revoked.status_code == 200, revoked.text
    assert site.client.get(PREFIX + '/manage', headers=key_headers(key)).status_code in (401,403)


def test_response_owner_is_required_even_when_member_name_matches(dinner_site):
    site = dinner_site
    poll = publish(site)
    owner = secrets.token_urlsafe(32)
    body = op(memberName='A', answers={'first':'difficult','second':'difficult'}, suggestion='다음 달도 좋습니다.', expectedRevision=poll['revision'])
    first = send(site, '/polls/' + poll['id'] + '/response', body, key=owner, admin=False)
    assert first.status_code == 200, first.text
    replay = send(site, '/polls/' + poll['id'] + '/response', body, key=owner, admin=False)
    assert replay.status_code == 200, replay.text
    thief = secrets.token_urlsafe(32)
    detail = site.client.get(PREFIX + '/polls/' + poll['id'] + '?name=A', headers=key_headers(thief))
    assert detail.status_code == 200, detail.text
    assert detail.json()['poll']['responded'] is True
    assert detail.json()['poll']['ownResponse'] is None
    wrong = send(site, '/polls/' + poll['id'] + '/response', op(memberName='A', answers={'first':'possible','second':'possible'}, suggestion='', expectedRevision=detail.json()['poll']['revision']), key=thief, admin=False)
    assert wrong.status_code in (403,409), wrong.text
    own = site.client.get(PREFIX + '/polls/' + poll['id'] + '?name=A', headers=key_headers(owner)).json()['poll']['ownResponse']
    assert own['answers'] == {'first':'difficult','second':'difficult'}
    public = site.client.get(PREFIX + '/summary?name=A').text
    assert owner not in public and thief not in public


def test_stale_poll_edit_does_not_overwrite_newer_management_change(dinner_site):
    site = dinner_site
    poll = draft(site)
    first = send(site, '/polls/' + poll['id'], op(title='변경한 제목', expectedRevision=poll['revision']), method='patch')
    assert first.status_code == 200, first.text
    stale = send(site, '/polls/' + poll['id'], op(title='오래된 화면의 제목', expectedRevision=poll['revision']), method='patch')
    assert stale.status_code == 409, stale.text
    current = site.client.get(PREFIX + '/manage', headers=AUTH).json()['polls'][0]
    assert current['title'] == '변경한 제목'


def test_shared_database_write_contract_is_enforced_before_dinner_write(dinner_site):
    site = dinner_site
    with site.module.get_db_connection(readonly=False) as conn:
        site.module.activate_holiday_contract(conn.cursor())
        conn.commit()
    rejected = site.client.post(PREFIX + '/role', json=op(memberName='', expectedRevision=0), headers=AUTH)
    assert rejected.status_code == 428, rejected.text
    allowed = send(site, '/role', op(memberName='', expectedRevision=0))
    assert allowed.status_code == 200, allowed.text


def test_database_busy_is_retryable_and_does_not_disclose_key(dinner_site):
    site = dinner_site
    key = secrets.token_urlsafe(32)
    connection = sqlite3.connect(site.module.DB_PATH)
    try:
        connection.execute('BEGIN IMMEDIATE')
        failed = send(site, '/connections', op(memberName='A', deviceLabel='PC'), key=key, admin=False)
        assert failed.status_code == 503, failed.text
        assert failed.json()['retryable'] is True
        assert key not in failed.text
    finally:
        connection.rollback()
        connection.close()


def test_invalid_json_body_is_rejected_by_http_validation(dinner_site):
    response = dinner_site.client.post(PREFIX + '/role', json=['wrong'], headers=dict(AUTH, **CONTRACT))
    assert response.status_code == 422


def test_remote_valid_admin_password_can_manage_and_invalid_header_never_grants_admin(dinner_site):
    remote = TestClient(dinner_site.module.app, client=('192.0.2.32', 5001))
    assert remote.get(PREFIX + '/manage', headers=AUTH).status_code == 200
    denied = remote.get(PREFIX + '/manage', headers={'X-Admin-Token':'not-the-password'})
    assert denied.status_code == 401
    assert denied.headers['cache-control'] == 'no-store'


def test_drafts_and_management_results_are_not_exposed_to_staff(dinner_site):
    site = dinner_site
    poll = draft(site)
    key = secrets.token_urlsafe(32)
    summary = site.client.get(PREFIX + '/summary?name=A', headers=key_headers(key))
    assert summary.status_code == 200
    assert summary.json()['polls'] == []
    assert site.client.get(PREFIX + '/polls/' + poll['id'] + '?name=A', headers=key_headers(key)).status_code in (403,404)
    assert site.client.get(PREFIX + '/manage', headers=key_headers(key)).status_code in (401,403)


def test_http_lifecycle_remind_close_extend_confirm_and_cancel(dinner_site):
    site = dinner_site
    poll = publish(site)

    def act(action, **changes):
        nonlocal poll
        response = send(site, '/polls/' + poll['id'] + '/action',
                        op(action=action, expectedRevision=poll['revision'], **changes))
        assert response.status_code == 200, response.text
        poll = response.json()['poll']
        return poll

    act('REMIND')
    assert site.client.get(PREFIX + '/summary?name=B').json()['events']
    assert act('CLOSE')['status'] == 'CLOSED'
    closed = send(site, '/polls/' + poll['id'] + '/response',
                  op(memberName='A', answers={'first':'possible','second':'maybe'},
                     suggestion='', expectedRevision=poll['revision']),
                  key=secrets.token_urlsafe(32), admin=False)
    assert closed.status_code == 409, closed.text
    assert act('EXTEND', deadline=dates()[1] + 'T12:00')['status'] == 'OPEN'
    assert act('CLOSE')['status'] == 'CLOSED'
    assert act('CONFIRM', selectedCandidateId='second', venue='구내 식당', notice='18:30에 만나요.',
               baseVersion=site.data()['dataVersion'])['status'] == 'CONFIRMED'
    detail = site.client.get(PREFIX + '/polls/' + poll['id'] + '?name=A').json()['poll']
    assert detail['selectedCandidateId'] == 'second'
    assert detail['venue'] == '구내 식당'
    assert act('CANCEL', notice='일정 취소')['status'] == 'CANCELLED'


def test_published_candidate_edit_preserves_unchanged_answer_and_excludes_stale_count(dinner_site):
    site = dinner_site
    poll = publish(site)
    key = secrets.token_urlsafe(32)
    answered = send(site, '/polls/' + poll['id'] + '/response',
                    op(memberName='A', answers={'first':'possible','second':'maybe'},
                       suggestion='', expectedRevision=poll['revision']), key=key, admin=False)
    assert answered.status_code == 200, answered.text
    current = site.client.get(PREFIX + '/manage', headers=AUTH).json()['polls'][0]
    candidates = [{'id':c['id'],'date':c['date'],'time':c['time']} for c in current['candidates']]
    candidates[0]['time'] = '19:00'
    changed = send(site, '/polls/' + poll['id'],
                   op(candidates=candidates, expectedRevision=current['revision']), method='patch')
    assert changed.status_code == 200, changed.text
    detail = site.client.get(PREFIX + '/polls/' + poll['id'] + '?name=A', headers=key_headers(key)).json()['poll']
    assert detail['needsConfirmation']
    assert detail['ownResponse']['answers']['second'] == 'maybe'
    managed = site.client.get(PREFIX + '/manage', headers=AUTH).json()['polls'][0]
    counts = {row['id']:row for row in managed['results']['candidates']}
    assert counts['first']['possible'] == 0
    assert counts['second']['maybe'] == 1
