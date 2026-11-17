"""Authorization, bounded write drain, and callback contracts at actual ASGI API."""
import asyncio
import pytest
from updater import manual_update
from test_operator_backend import backend


@pytest.fixture
def manager(monkeypatch):
    value = manual_update.LiveCheck()
    monkeypatch.setattr(manual_update, 'live_check', value)
    return value


def test_restart_reuses_local_password_origin_and_instance_checks(backend, manager, monkeypatch):
    module, client, profile = backend(password='secret')
    dto = {'action': 'prepare_restart', 'offer_id': 'offer', 'instance_id': 'old'}
    assert client.post('/api/system/update-status', json=dto).status_code == 401
    headers = {'X-Admin-Token': 'secret'}
    assert client.post('/api/system/update-status', json=dto, headers=dict(headers, Origin='https://foreign.example')).status_code == 403
    assert client.post('/api/system/update-status', json=dto, headers=headers).status_code == 409
    manager.configure(profile=profile, role='ADMIN', version='3.18.27', instance_id='new', port=8000, host='127.0.0.1', executable='unused')
    manager.value = {'result': 'available', 'offer_id': 'offer'}
    assert client.post('/api/system/update-status', json=dto, headers=headers).status_code == 409
    monkeypatch.setattr(module, 'is_loopback_client', lambda request: False)
    assert client.post('/api/system/update-status', json=dto, headers=headers).status_code == 403
    assert not manager.worker


def test_browser_token_cannot_shutdown_and_helper_capability_is_one_use(backend, manager, monkeypatch):
    module, client, profile = backend(password='secret')
    stopped = []
    monkeypatch.setattr(manager, 'validate_shutdown', lambda: None)
    monkeypatch.setattr(module, 'manual_shutdown_callback', lambda: stopped.append(True))
    manager.operation, manager.token = 'operation', 'secret-once'
    manager.value = {'result': 'waiting'}
    dto = {'operation': 'operation'}
    assert client.post('/api/system/manual-update-ready', json=dto, headers={'X-Admin-Token': 'secret'}).status_code == 403
    assert not stopped
    assert client.post('/api/system/manual-update-ready', json=dto, headers={'X-OnAir-Update-Token': 'secret-once'}).status_code == 200
    assert stopped == [True]
    assert client.post('/api/system/manual-update-ready', json=dto, headers={'X-OnAir-Update-Token': 'secret-once'}).status_code == 403


def test_busy_writer_cancels_shutdown_and_reopens_gate(backend, manager, monkeypatch):
    module, client, profile = backend()
    stopped = []
    monkeypatch.setattr(module, 'manual_shutdown_callback', lambda: stopped.append(True))
    manager.operation, manager.token = 'operation', 'one-use'
    manager.value = {'result': 'waiting'}
    gate = module.update_write_gate
    gate.active = 1
    original = gate.drain
    monkeypatch.setattr(gate, 'drain', lambda: original(.03))
    response = client.post('/api/system/manual-update-ready', json={'operation': 'operation'}, headers={'X-OnAir-Update-Token': 'one-use'})
    assert response.status_code == 409 and not stopped and not gate.draining
    gate.active = 0
    assert client.post('/api/system/update-status', json={'action': 'reset_blocked'}).status_code == 200


def test_write_gate_waits_for_response_background_work_and_rejects_new_writes(backend):
    module, _, _ = backend()
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        async def writing(scope, receive, send):
            started.set(); await release.wait()
        middleware = module.UpdateWriteMiddleware(writing)
        async def unused(*args): pass
        task = asyncio.create_task(middleware({'type': 'http', 'method': 'POST', 'path': '/api/save'}, unused, unused))
        await started.wait()
        drain = asyncio.create_task(module.update_write_gate.drain(.2))
        await asyncio.sleep(.01)
        responses = []
        async def send(message): responses.append(message)
        await middleware({'type': 'http', 'method': 'POST', 'path': '/api/save'}, unused, send)
        assert responses[0]['status'] == 503 and not drain.done()
        release.set(); await task
        assert await drain and module.update_write_gate.active == 0
    asyncio.run(scenario())


def test_durable_operation_is_local_and_readable_after_restart(backend, manager):
    from updater import core
    module, client, profile = backend()
    operation = 'a' * 64
    core.atomic_json(manual_update._operation_path(profile, operation, '.result.json'),
                     dict(operation=operation, result='rolled_back', current_version='3.18.27', new_instance_id='new', role='ADMIN', port=8000))
    response = client.get('/api/system/update-status?operation=' + operation)
    assert response.status_code == 200
    assert response.json()['manual']['new_instance_id'] == 'new'
    assert response.json()['manual']['result'] == 'rolled_back'
    assert client.get('/api/system/update-status?operation=../outside').status_code == 400


def test_publication_change_during_drain_cancels_and_unblocks_writes(backend, manager, monkeypatch):
    from updater import core
    module, client, profile = backend()
    stopped = []
    monkeypatch.setattr(module, 'manual_shutdown_callback', lambda: stopped.append(True))
    manager.operation, manager.token = 'operation', 'capability'
    manager.value = {'result': 'waiting'}
    def changed(): raise core.UpdateError('publication changed')
    monkeypatch.setattr(manager, 'validate_shutdown', changed)
    response = client.post('/api/system/manual-update-ready', json={'operation': 'operation'}, headers={'X-OnAir-Update-Token': 'capability'})
    assert response.status_code == 409 and not stopped
    assert not module.update_write_gate.draining
    assert client.post('/api/system/update-status', json={'action': 'reset_blocked'}).status_code == 200


def test_windows_tray_shutdown_uses_pointer_sized_handles(backend, monkeypatch):
    import ctypes
    from ctypes import wintypes
    module, _, _ = backend()
    class Function:
        def __call__(self, *args): return 1
    class DLL:
        def __getattr__(self, name):
            function = Function(); setattr(self, name, function); return function
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **k: DLL(), raising=False)
    monkeypatch.setattr(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE, raising=False)
    tray = module.TrayIcon('test', lambda: None, lambda: None, lambda: None)
    assert tray.user32.PostMessageW.argtypes == [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    assert tray.user32.DestroyWindow.argtypes == [wintypes.HWND]
