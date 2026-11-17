import base64
from pathlib import Path
import pytest
from updater import core, launcher, manual_update
from test_core import release


def setup_site(tmp_path):
    repository, profile, key, public, payload, package, manifest = release(tmp_path)
    core.atomic_json(profile / 'onair_update.json', dict(schema=1, product='WLR_OnAir', role='ADMIN', repository=str(repository), public_key=public))
    return repository, profile, key, public, payload, package, manifest


def test_live_check_does_not_download_or_change_state(tmp_path):
    site = setup_site(tmp_path)
    profile = site[1]
    result = manual_update.check_release(profile, 'ADMIN', '3.15.0')
    assert result['result'] == 'available'
    assert result['available_version'] == '3.15.1'
    assert not (profile / 'versions').exists()
    assert not (profile / 'state.json').exists()


def test_paused_never_offers_restart(tmp_path):
    repo, profile, *_ = setup_site(tmp_path)
    (repo / 'channels' / 'ADMIN.paused').write_text('paused')
    assert manual_update.check_release(profile, 'ADMIN', '3.15.0')['result'] == 'paused'


def test_same_version_is_latest(tmp_path):
    _, profile, *_ = setup_site(tmp_path)
    assert manual_update.check_release(profile, 'ADMIN', '3.15.1')['result'] == 'up_to_date'


def test_failed_sequence_not_latest(tmp_path):
    _, profile, *_ = setup_site(tmp_path)
    state = launcher._default_state('ADMIN')
    state.update(highest_sequence=1, highest_version='3.15.1', failed_sequences=[1])
    core.atomic_json(profile / 'state.json', state)
    assert manual_update.check_release(profile, 'ADMIN', '3.15.0')['result'] == 'blocked'


def test_bad_signature_rejected(tmp_path):
    _, profile, _, _, _, _, manifest = setup_site(tmp_path)
    data = core.load_json(manifest)
    data['payload']['version'] = '3.15.2'
    core.atomic_json(manifest, data)
    with pytest.raises(core.UpdateError):
        manual_update.check_release(profile, 'ADMIN', '3.15.0')


def test_corrupt_state_not_quarantined_by_check(tmp_path):
    _, profile, *_ = setup_site(tmp_path)
    state = profile / 'state.json'
    state.write_bytes(b'corrupt')
    with pytest.raises(core.UpdateError):
        manual_update.check_release(profile, 'ADMIN', '3.15.0')
    assert state.read_bytes() == b'corrupt'
    assert not list(profile.glob('state.json.bad*'))


def test_repository_sequence_rollback_rejected(tmp_path):
    _, profile, *_ = setup_site(tmp_path)
    state = launcher._default_state('ADMIN')
    state.update(highest_sequence=2, highest_version='3.15.2')
    core.atomic_json(profile / 'state.json', state)
    with pytest.raises(core.UpdateError):
        manual_update.check_release(profile, 'ADMIN', '3.15.0')


def test_no_pin_no_repository(tmp_path):
    assert manual_update.check_release(tmp_path, 'VIEWER', '3.18.26')['result'] == 'no_repository'


def test_hung_check_bounded_and_late_result_ignored(tmp_path, monkeypatch):
    import threading
    from updater import manual_update as m
    release_worker = threading.Event()
    monkeypatch.setattr(m, 'check_release', lambda *args: (release_worker.wait(2), {'result': 'available'})[1])
    checker = m.LiveCheck()
    checker.start(tmp_path, 'ADMIN', '3.15.0')
    first = checker.worker
    checker.start(tmp_path, 'ADMIN', '3.15.0')
    assert checker.worker is first
    checker.started -= 20
    assert checker.status()['result'] == 'check_failed'
    release_worker.set()
    first.join(3)
    assert checker.status()['result'] == 'check_failed'
    assert not checker.status()['restart_supported']


def test_prepare_is_state_read_only_and_rejects_changed_offer(tmp_path):
    repo, profile, *_ = setup_site(tmp_path)
    offer = manual_update.check_release(profile, 'ADMIN', '3.15.0')['offer_id']
    prepared = manual_update.prepare_release(profile, 'ADMIN', offer, '3.15.0')
    assert prepared['candidate']['version'] == '3.15.1'
    assert not (profile / 'state.json').exists()
    (repo / 'channels' / 'ADMIN.paused').touch()
    with pytest.raises(core.UpdateError): manual_update.revalidate_prepared(profile, 'ADMIN', prepared)
    with pytest.raises(core.UpdateError): manual_update.prepare_release(profile, 'ADMIN', offer, '3.15.0')


def test_download_timeout_invalidates_shutdown_capability():
    m = manual_update.LiveCheck()
    m.value = {'result': 'downloading'}
    m.operation, m.token = 'op', 'token'
    m.started = m.last_progress = manual_update.time.monotonic() - 61
    assert m.status()['result'] == 'failed'
    assert m.cancelled and not m.authorize_shutdown('op', 'token')


def test_changed_port_never_spawns_helper(tmp_path, monkeypatch):
    repo, profile, *_ = setup_site(tmp_path)
    active = core.stage_update(repo, profile, 'ADMIN', core.load_json(profile / 'onair_update.json')['public_key'])
    state = launcher._with_optional_defaults(launcher._default_state('ADMIN')); state['active'] = active
    launcher._save_state(profile, state)
    config = profile / 'config'; config.mkdir()
    (config / 'config_admin.ini').write_text('[NETWORK]\nserver_port=8005\nbind_host=127.0.0.1\n')
    m = manual_update.LiveCheck()
    m.value = {'result': 'downloading'}
    m.configure(profile=profile, role='ADMIN', version=active['version'], instance_id='i', port=8000, host='127.0.0.1', executable=active['path'])
    monkeypatch.setattr(manual_update.subprocess, 'Popen', lambda *a, **k: pytest.fail('must not spawn'))
    m._prepare_worker(m.runtime, 'offer', 'a' * 64)
    assert m.value['result'] == 'failed' and '설정' in m.value['message']
