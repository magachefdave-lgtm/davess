"""설치된 exe(base) 와 캐시된 known_good 사이의 우선순위.

사용자가 더 새 버전의 설치 ZIP 을 풀었는데도 예전에 캐시된 known_good 이 계속
실행되던 문제(설치본은 known_good 이 없을 때만 준비되고 맨 마지막에 시도됨)의 회귀 테스트.
"""
from pathlib import Path
from unittest.mock import Mock

from updater import core, launcher
from test_launcher import Process, candidate, fake_spawn, set_state, site  # noqa: F401

INSTALLED = '3.17.12'


def _state(site):
    return core.load_json(site['profile'] / 'state.json')


def _is_base(value, site):
    return (value['sequence'] == 0 and value['version'] == INSTALLED and
            value['sha256'] == core.sha256_file(site['exe']))


def test_newer_installed_exe_runs_before_older_cached(site, monkeypatch):
    old = candidate(site, '3.15.6', 5)
    set_state(site, active=old, highest=5)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=None))
    records = []
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, records))
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED) == 0
    assert len(records) == 1 and _is_base(records[0], site)
    state = _state(site)
    assert _is_base(state['active'], site)
    assert state['previous'] == old              # 되돌림 대상은 예전 known_good
    assert state['highest_version'] == INSTALLED
    assert state['highest_sequence'] == 5         # 순서 번호 하한은 그대로


def test_repository_update_older_than_installed_exe_is_refused(site, monkeypatch):
    old = candidate(site, '3.15.6', 5)
    set_state(site, active=old, highest=5)
    stale = candidate(site, '3.16.0', 6)          # 설치본(3.17.12)보다 오래된 저장소 업데이트
    stage = Mock(return_value=stale)
    monkeypatch.setattr(core, 'stage_update', stage)
    records = []
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, records))
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED) == 0
    assert stage.call_args.kwargs['current_version'] == INSTALLED
    assert [r['version'] for r in records] == [INSTALLED]
    state = _state(site)
    assert state['pending'] is None and state['highest_sequence'] == 5


def test_older_installed_exe_does_not_downgrade(site, monkeypatch):
    cached = candidate(site, '3.17.12', 5)
    set_state(site, active=cached, highest=5)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=None))
    records = []
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, records))
    assert launcher.run('ADMIN', site['exe'], version='3.15.0') == 0
    assert records == [cached]
    state = _state(site)
    assert state['active'] == cached and state['previous'] is None
    assert not list((site['profile'] / 'versions').glob('bundled-*'))


def test_same_version_different_hash_keeps_known_good(site, monkeypatch):
    cached = candidate(site, INSTALLED, 5)
    assert cached['sha256'] != core.sha256_file(site['exe'])
    set_state(site, active=cached, highest=5)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=None))
    records = []
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, records))
    for _ in range(2):                             # 실행할 때마다 오가지 않는다
        assert launcher.run('ADMIN', site['exe'], version=INSTALLED) == 0
    assert records == [cached, cached]
    assert _state(site)['active'] == cached
    assert not list((site['profile'] / 'versions').glob('bundled-*'))


def test_pending_newer_than_installed_still_runs_first(site, monkeypatch):
    old = candidate(site, '3.15.6', 5)
    set_state(site, active=old, highest=5)
    update = candidate(site, '3.18.0', 6)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=update))
    records = []
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, records))
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED) == 0
    assert records == [update]
    state = _state(site)
    assert state['active'] == update and state['pending'] is None
    assert _is_base(state['previous'], site)       # 되돌림은 설치본(더 새 것)으로


def test_failed_newer_pending_falls_back_to_installed_before_cached(site, monkeypatch):
    old = candidate(site, '3.15.6', 5)
    set_state(site, active=old, highest=5)
    update = candidate(site, '3.18.0', 6)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=update))
    records = []

    def spawn(value, config, profile, logger):
        records.append(value)
        if value == update:
            raise core.UpdateError('crashed')
        return Process(), profile / 'health' / 'test.json'
    monkeypatch.setattr(launcher, '_spawn', spawn)
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED) == 0
    assert records[0] == update and _is_base(records[1], site) and len(records) == 2
    state = _state(site)
    assert 6 in state['failed_sequences']
    assert _is_base(state['active'], site) and state['previous'] == old


def test_stored_pending_older_than_installed_is_skipped(site, monkeypatch):
    old = candidate(site, '3.15.6', 5)
    stale = candidate(site, '3.16.0', 6)
    set_state(site, active=old, pending=stale, highest=6)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=None))
    records = []
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, records))
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED) == 0
    assert len(records) == 1 and _is_base(records[0], site)
    state = _state(site)
    assert state['pending'] is None and state['previous'] == old


def test_rollback_to_previous_after_switching_to_installed(site, monkeypatch):
    old = candidate(site, '3.15.6', 5)
    set_state(site, active=old, highest=5)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=None))
    records = []
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, records))
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED) == 0
    base = _state(site)['active']
    assert _is_base(base, site)

    # 다음 실행에서 설치본이 깨졌다(시작 실패) -> previous(예전 캐시)로 되돌린다
    second = []

    def spawn(value, config, profile, logger):
        second.append(value)
        if value == base:
            raise core.UpdateError('broken')
        return Process(), profile / 'health' / 'test.json'
    monkeypatch.setattr(launcher, '_spawn', spawn)
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED) == 0
    assert second == [base, old]
    state = _state(site)
    assert state['active'] == old


def test_installed_startup_timeout_falls_back_to_cached(site, monkeypatch):
    old = candidate(site, '3.15.6', 5)
    set_state(site, active=old, highest=5)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=None))
    records = []

    def spawn(value, config, profile, logger):
        records.append(value)
        if value['sequence'] == 0:
            raise launcher.StartupTimeoutError('timeout')
        return Process(), profile / 'health' / 'test.json'
    monkeypatch.setattr(launcher, '_spawn', spawn)
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED) == 0
    assert _is_base(records[0], site) and records[1:] == [old]
    state = _state(site)
    assert state['active'] == old and state['failed_sequences'] == []


def test_pending_timeout_with_newer_installed_keeps_pending_retryable(site, monkeypatch):
    old = candidate(site, '3.15.6', 5)
    set_state(site, active=old, highest=5)
    update = candidate(site, '3.18.0', 6)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=update))
    records = []

    def spawn(value, config, profile, logger):
        records.append(value)
        if value == update:
            raise launcher.StartupTimeoutError('timeout')
        return Process(), profile / 'health' / 'test.json'
    monkeypatch.setattr(launcher, '_spawn', spawn)
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED) == 0
    assert records[0] == update and _is_base(records[1], site) and len(records) == 2
    state = _state(site)
    assert state['pending'] == update and state['timeout_failures'] == {'6': 1}
    assert state['failed_sequences'] == []


def test_seed_failure_with_newer_installed_still_runs_cached(site, monkeypatch):
    old = candidate(site, '3.15.6', 5)
    set_state(site, active=old, highest=5)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=None))
    monkeypatch.setattr(launcher, '_seed_bundled', Mock(side_effect=OSError('disk full')))
    records = []
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, records))
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED) == 0
    assert records == [old]


def test_run_requires_explicit_version(site):
    import inspect
    parameter = inspect.signature(launcher.run).parameters['version']
    assert parameter.default is inspect.Parameter.empty


def test_deep_fallback_reaches_older_previous_and_never_records_failed_as_rollback(site, monkeypatch):
    older = candidate(site, '3.15.1', 3)
    old = candidate(site, '3.15.6', 5)
    set_state(site, active=old, previous=older, highest=5)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=None))
    records = []

    def spawn(value, config, profile, logger):
        records.append(value)
        if value is not None and value != older:
            raise core.UpdateError('broken')         # 설치본과 예전 캐시 모두 시작 실패
        return Process(), profile / 'health' / 'test.json'
    monkeypatch.setattr(launcher, '_spawn', spawn)
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED) == 0
    assert _is_base(records[0], site) and records[1:] == [old, older]
    state = _state(site)
    assert state['active'] == older
    assert state['previous'] is None                # 방금 실패한 버전을 되돌림 대상으로 남기지 않는다
