"""Public header version information stays read-only, bounded, and capability-free."""
import threading
from types import SimpleNamespace

import pytest

from updater import core, launcher, manual_update
from test_core import envelope, release
from test_operator_backend import backend as operator_backend


PUBLIC_KEYS = {
    'mode', 'current_version', 'latest_version', 'result', 'checked_at',
    'update_available', 'can_update', 'local', 'managed', 'busy',
}


@pytest.fixture
def backend(operator_backend, monkeypatch):
    """Keep this synthetic version matrix independent of the release version."""
    def load(*args, **kwargs):
        module, client, profile = operator_backend(*args, **kwargs)
        monkeypatch.setattr(module, 'APP_VERSION', '3.18.28')
        return module, client, profile
    return load


@pytest.fixture
def manager(monkeypatch):
    checker = manual_update.LiveCheck()
    monkeypatch.setattr(manual_update, 'live_check', checker)
    return checker


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    actual = manual_update.time
    monkeypatch.setattr(manual_update, 'time', SimpleNamespace(
        monotonic=lambda: now[0], time=actual.time, sleep=actual.sleep))
    return now


def signed_repository(tmp_path, profile, version='3.18.29', role='ADMIN'):
    repository, _, key, public, payload, _, manifest = release(
        tmp_path, role=role, version=version)
    core.atomic_json(profile / 'onair_update.json', dict(
        schema=1, product='WLR_OnAir', role=role,
        repository=str(repository), public_key=public))
    return key, payload, manifest


def restart_runtime(manager, profile, version='3.18.28', role='ADMIN'):
    manager.configure(profile=profile, role=role, version=version,
                      instance_id='private-instance', port=8000,
                      host='127.0.0.1', executable='packaged-app.exe')


def republish(key, payload, manifest):
    updated = dict(payload, version='3.18.30', sequence=2,
                   package=f"packages/ADMIN/3.18.30-{payload['sha256'][:12]}.zip")
    repository = manifest.parent.parent
    (repository / updated['package']).write_bytes((repository / payload['package']).read_bytes())
    core.atomic_json(manifest, envelope(updated, key))


def checked(client, manager):
    first = client.get('/api/system/version-status')
    assert first.status_code == 200
    if manager.worker:
        manager.worker.join(3)
        assert not manager.worker.is_alive()
    return client.get('/api/system/version-status')


def test_first_header_read_checks_signed_feed_and_caches_offer_until_ttl(
        backend, manager, clock, tmp_path):
    module, client, profile = backend()
    key, payload, manifest = signed_repository(tmp_path, profile)
    restart_runtime(manager, profile)
    before = {p.relative_to(profile): p.read_bytes()
              for p in profile.rglob('*') if p.is_file()}

    response = checked(client, manager)
    body = response.json()
    assert set(body) == PUBLIC_KEYS
    assert response.headers['cache-control'] == 'no-store'
    assert body == dict(mode='ADMIN', current_version='3.18.28',
                       latest_version='3.18.29', result='available',
                       checked_at=body['checked_at'], update_available=True,
                       can_update=True, local=True, managed=True, busy=False)
    assert body['checked_at']
    operation, worker, offer = manager.operation, manager.worker, manager.value['offer_id']
    republish(key, payload, manifest)
    for elapsed in (0, 1, 60, 299):
        clock[0] = 100 + elapsed
        cached = client.get('/api/system/version-status').json()
        assert cached['latest_version'] == '3.18.29' and cached['can_update']
        assert (manager.operation, manager.worker, manager.value['offer_id']) == (operation, worker, offer)
    assert {p.relative_to(profile): p.read_bytes()
            for p in profile.rglob('*') if p.is_file()} == before
    clock[0] = 400
    refreshed = checked(client, manager).json()
    assert refreshed['latest_version'] == '3.18.30'
    assert manager.operation != operation


@pytest.mark.parametrize('result', ['downloading', 'waiting', 'ready', 'restarting'])
def test_header_and_explicit_check_preserve_active_restart_capability(
        backend, manager, clock, result):
    module, client, profile = backend()
    restart_runtime(manager, profile)
    manager.operation, manager.token = 'private-operation', 'one-use-secret'
    prepared = manager.prepared = {'private': 'verified preparation'}
    manager.started = manager.last_progress = clock[0]
    manager.value = dict(result=result, available_version='3.18.29',
                         checked_at='2026-11-06T00:00:00+00:00')

    body = client.get('/api/system/version-status').json()
    assert body['result'] == result and body['busy'] is True
    assert body['latest_version'] == '3.18.29' and body['update_available']
    assert body['can_update'] is False
    response = client.post('/api/system/update-status', json={'action': 'check_now'})
    assert response.status_code == 200
    assert response.json()['operation'] == 'private-operation'
    assert manager.value['result'] == result and manager.prepared is prepared
    assert manager.operation == 'private-operation' and manager.token == 'one-use-secret'
    assert manager.worker is None


def test_hung_check_returns_unknown_and_never_queues_another_worker(
        backend, manager, clock, monkeypatch):
    module, client, profile = backend()
    release_worker, entered = threading.Event(), threading.Event()

    def slow_repository(*args):
        entered.set()
        release_worker.wait(5)
        return dict(result='available', available_version='3.18.29', offer_id='late-offer')

    monkeypatch.setattr(manual_update, 'check_release', slow_repository)
    try:
        initial = client.get('/api/system/version-status')
        assert initial.status_code == 200 and entered.wait(1)
        assert initial.json()['current_version'] == '3.18.28'
        assert initial.json()['latest_version'] is None
        worker, operation = manager.worker, manager.operation
        clock[0] = 116
        timed_out = client.get('/api/system/version-status').json()
        assert timed_out['result'] == 'check_failed'
        assert timed_out['latest_version'] is None
        assert not timed_out['update_available'] and not timed_out['can_update']
        clock[0] = 500
        for _ in range(5):
            assert client.get('/api/system/version-status').json()['result'] == 'check_failed'
            assert manager.worker is worker and manager.operation == operation
    finally:
        release_worker.set()
        if manager.worker:
            manager.worker.join(3)
    assert manager.status()['result'] == 'check_failed'


def test_password_and_remote_clients_get_minimal_info_without_action_access(
        backend, manager, monkeypatch, tmp_path):
    module, client, profile = backend(password='private-password')
    signed_repository(tmp_path, profile)
    restart_runtime(manager, profile)
    monkeypatch.setattr(module, 'is_loopback_client', lambda request: False)
    response = checked(client, manager)
    body = response.json()
    assert set(body) == PUBLIC_KEYS
    assert body['latest_version'] == '3.18.29' and body['update_available']
    assert not body['local'] and not body['can_update']
    text = response.text
    for private in ('private-password', 'private-instance', str(profile), 'offer_id', 'operation', 'settings', 'log_path'):
        assert private not in text
    assert client.get('/api/system/update-status').status_code == 401
    assert client.post('/api/system/update-status', json={'action': 'check_now'}).status_code == 401
    assert client.post('/api/system/update-status', json={'action': 'prepare_restart'},
                       headers={'X-Admin-Token': 'private-password'}).status_code == 403
    assert client.post('/api/system/manual-update-ready', json={},
                       headers={'X-Admin-Token': 'private-password'}).status_code == 403
    assert client.post('/api/system/version-status', json={'action': 'prepare_restart'}).status_code == 405


@pytest.mark.parametrize('latest,expected_result,available', [
    ('3.18.9', 'up_to_date', False),
    ('3.18.28', 'up_to_date', False),
    ('3.18.29', 'available', True),
    ('3.19.0', 'available', True),
])
@pytest.mark.parametrize('packaged_restart', [False, True])
def test_signed_versions_use_numeric_comparison_and_require_restart_support(
        backend, manager, tmp_path, latest, expected_result, available, packaged_restart):
    module, client, profile = backend()
    signed_repository(tmp_path, profile, latest)
    if packaged_restart:
        restart_runtime(manager, profile)
    body = checked(client, manager).json()
    assert body['current_version'] == '3.18.28'
    assert body['latest_version'] == latest and body['result'] == expected_result
    assert body['update_available'] is available
    assert body['can_update'] is (available and packaged_restart)


def test_blocked_signed_newer_version_is_visible_without_update_action(
        backend, manager, tmp_path):
    module, client, profile = backend()
    signed_repository(tmp_path, profile)
    restart_runtime(manager, profile)
    state = launcher._default_state('ADMIN')
    state.update(highest_sequence=1, highest_version='3.18.29', failed_sequences=[1])
    core.atomic_json(profile / 'state.json', state)
    before = (profile / 'state.json').read_bytes()
    body = checked(client, manager).json()
    assert body['result'] == 'blocked' and body['latest_version'] == '3.18.29'
    assert body['update_available'] and not body['can_update']
    assert (profile / 'state.json').read_bytes() == before


def test_unverified_feed_never_becomes_latest(backend, manager, tmp_path):
    module, client, profile = backend()
    _, _, manifest = signed_repository(tmp_path, profile)
    restart_runtime(manager, profile)
    data = core.load_json(manifest)
    data['payload']['version'] = '3.99.0'
    core.atomic_json(manifest, data)
    body = checked(client, manager).json()
    assert body['result'] == 'check_failed' and body['latest_version'] is None
    assert not body['update_available'] and not body['can_update']


def test_no_pin_and_unmanaged_source_report_unknown_latest(backend, manager, monkeypatch):
    module, client, profile = backend()
    body = checked(client, manager).json()
    assert body['result'] == 'no_repository' and body['latest_version'] is None
    assert body['current_version'] == '3.18.28'
    assert not body['update_available'] and not body['can_update']
    worker = manager.worker
    monkeypatch.setattr(module, 'MANAGED_CONFIG', False)
    body = client.get('/api/system/version-status').json()
    assert body['result'] == 'unmanaged' and body['latest_version'] is None
    assert body['current_version'] == '3.18.28' and not body['managed']
    assert not body['update_available'] and not body['can_update']
    assert manager.worker is worker


def test_explicit_check_bypasses_automatic_cache(backend, manager, clock, tmp_path):
    module, client, profile = backend(password='operator-password')
    key, payload, manifest = signed_repository(tmp_path, profile)
    assert checked(client, manager).json()['latest_version'] == '3.18.29'
    operation = manager.operation
    republish(key, payload, manifest)
    response = client.post('/api/system/update-status', json={'action': 'check_now'},
                           headers={'X-Admin-Token': 'operator-password'})
    assert response.status_code == 200 and response.json()['operation'] != operation
    manager.worker.join(3)
    assert client.get('/api/system/version-status').json()['latest_version'] == '3.18.30'
