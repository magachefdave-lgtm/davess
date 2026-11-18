"""Dinner capability keys remain private and durable across browser launches."""
from concurrent.futures import ThreadPoolExecutor
import base64
import copy
import os
import uuid
import logging

import pytest
from fastapi.testclient import TestClient

from backend.request_access_store import ReceiptVault
from backend.request_access_schema import AccessError
from test_backend_regression import load_app, SEED
from test_dinner_api import assign, dates, key_headers, op, send
from test_request_access_api import site, session, PREFIX


def test_diagnostic_log_masks_dinner_header_capability_but_retains_failure_context():
    from backend.request_access_local import CredentialLogFilter
    record = logging.LogRecord('onair-test', logging.WARNING, __file__, 1,
                               'POST /api/dinner/polls/7/response failed: headers=%s',
                               ({'X-OnAir-Dinner-Key': 'private-capability-xyz', 'Other': 'keep'},), None)
    CredentialLogFilter().filter(record)
    result = record.getMessage()
    assert 'private-capability-xyz' not in result
    assert '[REDACTED]' in result
    assert '/api/dinner/polls/7/response failed' in result
    assert 'keep' in result


def credentials(site, headers, name='A'):
    return site.client.post(PREFIX + '/dinner-credentials', json={'name': name}, headers=headers)


def test_key_survives_vault_reopen_and_keeps_names_and_datasets_separate(tmp_path):
    vault = ReceiptVault(tmp_path)
    assert hasattr(vault, 'dinner_key'), 'Dinner keys need durable local storage before submission.'
    first = vault.dinner_key('dataset-one', 'A')
    assert len(base64.urlsafe_b64decode(first + '=' * (-len(first) % 4))) >= 32
    reopened = ReceiptVault(tmp_path)
    assert reopened.dinner_key('dataset-one', 'A') == first
    assert reopened.dinner_key('dataset-one', 'B') != first
    assert reopened.dinner_key('dataset-two', 'A') != first


def test_concurrent_first_access_returns_one_durable_key(tmp_path):
    assert hasattr(ReceiptVault, 'dinner_key'), 'Concurrent first access must not create competing keys.'
    with ThreadPoolExecutor(max_workers=8) as pool:
        keys = list(pool.map(lambda _: ReceiptVault(tmp_path).dinner_key('dataset', 'A'), range(20)))
    assert len(set(keys)) == 1
    assert ReceiptVault(tmp_path).dinner_key('dataset', 'A') == keys[0]


def test_atomic_persist_failure_never_returns_new_key_or_overwrites_vault(tmp_path, monkeypatch):
    vault = ReceiptVault(tmp_path)
    assert hasattr(vault, 'dinner_key'), 'A key must not escape before durable storage succeeds.'
    first = vault.dinner_key('dataset', 'A')
    before = vault.path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(os, 'replace', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
        with pytest.raises(AccessError) as error:
            vault.dinner_key('dataset', 'B')
        assert error.value.code == 'LOCAL_PERSIST_FAILED'
    assert vault.path.read_bytes() == before
    assert vault.dinner_key('dataset', 'A') == first


def test_profile_status_exposes_only_managed_flag(site, monkeypatch):
    value = site.client.get(PREFIX + '/dinner-profile')
    assert value.status_code == 200, value.text
    assert value.json() == {'managed': True}
    assert value.headers['cache-control'] == 'no-store'
    monkeypatch.setattr(site.module, 'MANAGED_CONFIG', False)
    assert site.client.get(PREFIX + '/dinner-profile').json() == {'managed': False}


def test_credential_endpoint_rejects_unauthorized_origin_and_remote_peer(site):
    denied = credentials(site, {'Origin': 'http://localhost'})
    assert denied.status_code == 403, denied.text
    headers = session(site)
    wrong = credentials(site, dict(headers, Origin='https://outside.invalid'))
    assert wrong.status_code == 403
    remote = TestClient(site.module.app, base_url='http://localhost', client=('192.0.2.8', 1234))
    result = remote.post(PREFIX + '/dinner-credentials', json={'name': 'A'}, headers=headers)
    assert result.status_code == 403
    assert 'key' not in result.json()


def test_same_profile_new_origin_keeps_key_and_public_data_excludes_it(site):
    headers = session(site)
    first = credentials(site, headers)
    assert first.status_code == 200, first.text
    value = first.json()
    assert set(value) == {'key', 'datasetKey', 'deviceLabel'}
    assert first.headers['cache-control'] == 'no-store'
    assert first.headers['referrer-policy'] == 'no-referrer'
    site.module.REQUEST_ACCESS._vault = None
    site.client = TestClient(site.module.app, base_url='http://127.0.0.1:8001')
    ticket = site.module.REQUEST_ACCESS.issue_ticket('http://127.0.0.1:8001')
    fresh = site.client.post(PREFIX + '/session', json={'ticket': ticket},
                             headers={'Origin': 'http://127.0.0.1:8001'})
    headers = {'Origin': 'http://127.0.0.1:8001', 'X-OnAir-Local-Session': fresh.json()['sessionToken']}
    assert credentials(site, headers).json() == value
    assert credentials(site, headers, 'B').json()['key'] != value['key']
    assert value['key'] not in site.client.get('/api/duty/all-data').text


def test_dataset_generation_change_keeps_response_key_but_new_dataset_does_not(site):
    headers = session(site)
    first = credentials(site, headers)
    assert first.status_code == 200, first.text
    with site.module.REQUEST_ACCESS.db(write=True) as cur:
        cur.execute("UPDATE app_settings SET value=? WHERE key='request_access_generation'", (str(uuid.uuid4()),))
    assert credentials(site, headers).json()['key'] == first.json()['key']
    with site.module.REQUEST_ACCESS.db(write=True) as cur:
        cur.execute("UPDATE app_settings SET value=? WHERE key='request_access_dataset_id'", (str(uuid.uuid4()),))
    second = credentials(site, headers).json()
    assert second['datasetKey'] != first.json()['datasetKey']
    assert second['key'] != first.json()['key']


def test_endpoint_disk_failure_and_invalid_member_return_no_key(site, monkeypatch):
    headers = session(site)
    invalid = credentials(site, headers, 'Unknown employee')
    assert invalid.status_code == 400, invalid.text
    with monkeypatch.context() as patch:
        patch.setattr(os, 'replace', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
        failed = credentials(site, headers)
    assert failed.status_code == 503
    assert failed.json()['code'] == 'LOCAL_PERSIST_FAILED'
    assert 'key' not in failed.json()
    assert credentials(site, headers).status_code == 200


def test_disabled_managed_profile_never_reports_unmanaged_fallback(site, monkeypatch):
    headers = session(site)
    original = site.module._cfg
    monkeypatch.setattr(site.module, '_cfg', lambda key, default='': 'false' if key == 'request_access_enabled' else original(key, default))
    profile = site.client.get(PREFIX + '/dinner-profile')
    assert profile.status_code == 200, profile.text
    assert profile.json()['managed'] is True
    denied = credentials(site, headers)
    assert denied.json()['code'] == 'DISABLED'
    assert 'key' not in denied.json()


def test_installed_roles_share_only_own_ballots_through_local_sessions(load_app, tmp_path, monkeypatch):
    from updater.profiles import profile_dir

    monkeypatch.setenv('WLR_PROFILE_ROOT', str(tmp_path / 'installed'))
    apps = {role: load_app('source', mode=role) for role in ('ADMIN', 'VIEWER', 'MASTER')}
    admin, viewer = apps['ADMIN'], apps['VIEWER']
    admin.save(copy.deepcopy(SEED))
    headers = {}
    for role, app in apps.items():
        profile = profile_dir(role)
        (profile / 'config').mkdir(parents=True)
        profile.chmod(0o700)
        monkeypatch.setattr(app.module, 'MANAGED_CONFIG', True)
        monkeypatch.setattr(app.module, 'CONFIG_DIR', str(profile / 'config'))
        monkeypatch.setattr(app.module, 'DB_PATH', admin.module.DB_PATH)
        app.client = TestClient(app.module.app, base_url='http://localhost')
        local = session(app)
        key = credentials(app, local).json()['key']
        headers[role] = {**local, **key_headers(key)}

    assign(admin)
    ca_key = headers['VIEWER']['X-OnAir-Dinner-Key']
    device = send(viewer, '/connections', op(memberName='A'), key=ca_key, admin=False).json()['connection']
    assert send(admin, '/connections/' + device['id'] + '/decision', op(decision='APPROVE')).status_code == 200
    deadline, first, second, _ = dates()
    draft = send(viewer, '/polls', op(title='날짜 선택', deadline=deadline+'T18:00',
        candidates=[dict(id='first', date=first, time='18:00'), dict(id='second', date=second, time='18:00')],
        targets=['A','B']), key=ca_key, admin=False).json()['poll']
    path = '/api/dinner/polls/' + draft['id']
    published = viewer.client.post(path+'/action', headers=headers['VIEWER'],
                                   json=op(action='PUBLISH', expectedRevision=draft['revision'])).json()['poll']
    previous = None
    for role in ('MASTER', 'VIEWER', 'MASTER', 'ADMIN', 'MASTER'):
        app, proof = apps[role], headers[role]
        own = app.client.get(path, params={'name':'A'}, headers=proof).json()['poll']['ownResponse']
        assert own == previous
        if own:
            key_only = key_headers(proof['X-OnAir-Dinner-Key'])
            assert app.client.get(path, params={'name':'A'}, headers=key_only).json()['poll']['ownResponse'] is None
        write = app.client.post(path+'/response', headers=proof, json=op(memberName='A',
            expectedRevision=published['revision'], expectedResponseVersion=own['version'] if own else None,
            answers={'first':'possible','second':'maybe'}, suggestion=role))
        assert write.status_code == 200, write.text
        previous = write.json()['poll']['ownResponse']
        assert previous['suggestion'] == role

    for role in ('ADMIN', 'MASTER'):
        assert apps[role].client.get('/api/dinner/manage', headers=headers[role]).status_code == 403
    wrong_key = credentials(admin, headers['ADMIN'], 'B').json()['key']
    wrong_person = {**headers['ADMIN'], **key_headers(wrong_key)}
    assert admin.client.get(path, params={'name':'A'}, headers=wrong_person).json()['poll']['ownResponse'] is None


def test_existing_peer_lookup_does_not_create_keys_files_or_profiles(tmp_path):
    profile = tmp_path / 'existing'
    vault = ReceiptVault(profile)
    key = vault.dinner_key('dataset', 'A')
    vault.lock_path.unlink()
    reader = ReceiptVault(profile, create=False)
    assert reader.existing_dinner_key('dataset', 'A') == key
    assert reader.existing_dinner_key('other-dataset', 'A') is None
    assert reader.existing_dinner_key('dataset', 'B') is None
    assert set(profile.iterdir()) == {vault.path}
    missing = tmp_path / 'missing'
    with pytest.raises(AccessError):
        ReceiptVault(missing, create=False)
    assert not missing.exists()
