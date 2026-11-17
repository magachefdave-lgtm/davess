"""Manual fallback must not start pre-feature binaries that can spawn browsers."""
import logging
import pytest
from updater import core, launcher
from test_launcher import site, candidate, set_state, Process


def test_manual_failure_does_not_launch_pre_feature_fallback(site, monkeypatch):
    active = candidate(site, '3.18.27', 2)
    previous = candidate(site, '3.18.26', 1)
    pending = candidate(site, '3.18.28', 3)
    set_state(site, active=active, previous=previous, pending=pending, highest=3)
    calls = []
    def spawn(value, *args):
        calls.append(value['version'])
        if value != previous: raise core.UpdateError('synthetic failure')
        return Process(), site['profile'] / 'health' / 'test.json'
    monkeypatch.setattr(launcher, '_spawn', spawn)
    with pytest.raises(core.UpdateError, match='모두 시작'):
        launcher._run_locked('ADMIN', site['exe'], '3.18.27', site['profile'], logging.getLogger('test-manual'), prepared=pending)
    assert calls == ['3.18.28', '3.18.27']


def test_helper_ack_timeout_has_margin_after_twenty_second_backend_boundary(site, monkeypatch):
    import hashlib
    import time
    from urllib import request
    from updater import manual_update as m
    active = candidate(site, '3.18.27', 2)
    operation, token = 'a' * 64, 'test-capability'
    ticket = dict(schema=1, role='ADMIN', operation=operation, token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                  created_at=time.time(), instance_id='old', old_chain=[], executable=active['path'], version=active['version'],
                  host='127.0.0.1', port=8000, prepared={'expected_state': {'active': active}, 'expected_pin': {}})
    core.atomic_json(m._operation_path(site['profile'], operation, '.ticket.json'), ticket)
    monkeypatch.setenv('WLR_MANUAL_UPDATE_TOKEN', token)
    monkeypatch.setattr(m.process_identity, 'reopen_chain', lambda docs: [])
    monkeypatch.setattr(m.process_identity, 'wait_old_chain', lambda *a: True)
    monkeypatch.setattr(m, 'revalidate_prepared', lambda *a: None)
    monkeypatch.setattr(launcher, 'run_prepared_restart', lambda *a, **kw: 0)
    calls = []
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass
    class Opener:
        def open(self, req, timeout):
            calls.append(timeout)
            # 15s drain + 5s validation + scheduler/response delivery overhead.
            # Model this boundary without sleeping in the test suite.
            if timeout <= 21: raise TimeoutError('ack delivered after client timeout')
            return Response()
    monkeypatch.setattr(request, 'build_opener', lambda *a: Opener())
    assert m.run_helper('ADMIN', operation) == 0
    assert calls[0] >= 30
