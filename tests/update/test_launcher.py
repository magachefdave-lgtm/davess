import base64
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from updater import core, launcher


class Process:
    def __init__(self, pid=301, exit_code=0):
        self.pid = pid
        self.exit_code = exit_code
        self.dead = False
        self.terminated = 0
        self.killed = 0
        self.on_wait = None

    def poll(self):
        return self.exit_code if self.dead else None

    def wait(self, timeout=None):
        if self.on_wait:
            self.on_wait()
        self.dead = True
        return self.exit_code

    def terminate(self):
        self.terminated += 1
        self.dead = True

    def kill(self):
        self.killed += 1
        self.dead = True


@pytest.fixture
def site(tmp_path, monkeypatch):
    role = 'ADMIN'
    profile = tmp_path / 'profile' / role
    config = profile / 'config'
    config.mkdir(parents=True)
    ini = config / 'config_admin.ini'
    ini.write_text('[NETWORK]\nbind_host = 127.0.0.1\nshared_folder_path = Z:/original\n', encoding='utf-8')
    database = config / 'smart_duty.db'
    database.write_bytes(b'ORIGINAL USER DATABASE - DO NOT COPY OR RESTORE')
    installed = tmp_path / 'installed'
    installed.mkdir()
    exe = installed / 'WLR_OnAir_Admin.exe'
    exe.write_bytes(b'MZ initial trusted admin')
    bootstrap = {'schema': 1, 'product': 'WLR_OnAir', 'role': role,
                 'repository': str(tmp_path / 'repository'),
                 'public_key': base64.b64encode(bytes(range(32))).decode()}
    core.atomic_json(installed / 'onair_update.json', bootstrap)
    monkeypatch.setattr(launcher.profiles, 'profile_dir', lambda requested: profile)
    monkeypatch.setattr(launcher.profiles, 'prepare_profile', lambda requested: config)
    monkeypatch.setattr(launcher.profiles, 'validate_profile_storage', lambda requested, folder: None, raising=False)
    monkeypatch.setattr(launcher, '_read_system_info', lambda host, port: None)
    return {'profile': profile, 'config': config, 'ini': ini, 'db': database,
            'exe': exe, 'bootstrap': bootstrap, 'role': role}


# 설치된 exe 의 버전. launcher.run 은 이 값을 캐시된 버전과 비교해 실행 순서를 정한다.
# 이 파일의 테스트들은 '캐시/저장소 버전이 설치본보다 새롭거나 같다'는 전제로 쓰였으므로
# 가장 낮은 후보 버전(3.15.0)과 같게 둔다. 설치본이 더 새 경우는 test_installed_exe_priority.py.
INSTALLED_VERSION = '3.15.0'


def candidate(site, version='3.15.0', sequence=0, role='ADMIN'):
    directory = site['profile'] / 'versions' / f'{version}-{sequence}-{role}'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / core.EXECUTABLES[role]
    path.write_bytes(f'MZ {version} {role}'.encode())
    return {'role': role, 'version': version, 'sequence': sequence,
            'path': str(path), 'sha256': core.sha256_file(path), 'data_schema': 1}


def set_state(site, active=None, previous=None, pending=None, highest=0, failed=None):
    state = launcher._default_state(site['role'])
    state.update(active=active, previous=previous, pending=pending,
                 highest_sequence=highest, failed_sequences=failed or [])
    state['highest_version'] = max([c['version'] for c in (active, previous, pending) if c] or ['0.0.0'],
                                   key=core.parse_version)
    launcher._save_state(site['profile'], state)
    return state


def fake_spawn(site, records):
    def spawn(value, config, profile, logger):
        records.append(value)
        return Process(), profile / 'health' / 'test.json'
    return spawn


def test_first_start_seeds_local_executable_and_keeps_data(site, monkeypatch):
    before = (site['ini'].read_bytes(), site['db'].read_bytes())
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=None))
    records = []
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, records))
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION) == 0
    state = core.load_json(site['profile'] / 'state.json')
    assert state['active']['sequence'] == 0
    assert Path(state['active']['path']).parent.parent == site['profile'] / 'versions'
    assert Path(state['active']['path']).read_bytes() == site['exe'].read_bytes()
    assert core.load_json(site['profile'] / 'onair_update.json') == site['bootstrap']
    assert (site['ini'].read_bytes(), site['db'].read_bytes()) == before


def test_offline_starts_cached_active(site, monkeypatch):
    active = candidate(site)
    set_state(site, active=active)
    monkeypatch.setattr(core, 'stage_update', Mock(side_effect=OSError('SMB disconnected')))
    records = []
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, records))
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION) == 0
    assert records == [active]


def test_activation_is_after_health_and_lock_lives_until_exit(site, monkeypatch):
    active = candidate(site)
    update = candidate(site, '3.15.1', 1)
    set_state(site, active=active)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=update))
    child = Process(exit_code=7)

    def on_wait():
        assert core.load_json(site['profile'] / 'state.json')['active'] == update
        with pytest.raises(core.UpdateError):
            with core.profile_lock(site['profile']):
                pass
    child.on_wait = on_wait

    def spawn(value, config, profile, logger):
        assert value == update
        state = core.load_json(profile / 'state.json')
        assert state['active'] == active
        assert state['pending'] == update
        assert state['highest_sequence'] == 1
        return child, profile / 'health' / 'test.json'
    monkeypatch.setattr(launcher, '_spawn', spawn)
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION) == 7
    assert child.terminated == 0 and child.killed == 0
    state = core.load_json(site['profile'] / 'state.json')
    assert state['previous'] == active and state['pending'] is None
    assert core.stage_update.call_count == 1


def test_failed_update_rolls_back_and_is_not_retried(site, monkeypatch):
    active = candidate(site)
    update = candidate(site, '3.15.1', 1)
    set_state(site, active=active)
    calls = []
    monkeypatch.setattr(core, 'stage_update', Mock(side_effect=[update, None]))

    def spawn(value, config, profile, logger):
        calls.append(value)
        if value == update:
            raise core.UpdateError('startup failed')
        return Process(), profile / 'health' / 'test.json'
    monkeypatch.setattr(launcher, '_spawn', spawn)
    original_db = site['db'].read_bytes()
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION) == 0
    state = core.load_json(site['profile'] / 'state.json')
    assert state['active'] == active and state['pending'] is None
    assert state['highest_sequence'] == 1 and state['failed_sequences'] == [1]
    assert state['highest_version'] == '3.15.1'
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION) == 0
    assert calls == [update, active, active]
    assert core.stage_update.call_args.kwargs['highest_sequence'] == 1
    assert core.stage_update.call_args.kwargs['current_version'] == '3.15.1'
    assert site['db'].read_bytes() == original_db


def test_pending_after_interrupted_start_is_used_offline(site, monkeypatch):
    active = candidate(site)
    pending = candidate(site, '3.15.1', 1)
    set_state(site, active=active, pending=pending, highest=1)
    monkeypatch.setattr(core, 'stage_update', Mock(side_effect=OSError('offline')))
    calls = []
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, calls))
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION) == 0
    assert calls == [pending]


def test_corrupt_active_falls_back_to_verified_previous(site, monkeypatch):
    previous = candidate(site)
    active = candidate(site, '3.15.1', 1)
    set_state(site, active=active, previous=previous, highest=1)
    Path(active['path']).write_bytes(b'tampered')
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=None))
    calls = []
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, calls))
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION) == 0
    assert calls == [previous]


def test_changed_bootstrap_never_replaces_pin_or_launches(site, monkeypatch):
    pin = site['profile'] / 'onair_update.json'
    core.atomic_json(pin, site['bootstrap'])
    changed = dict(site['bootstrap'], public_key=base64.b64encode(b'x' * 32).decode())
    core.atomic_json(site['exe'].parent / 'onair_update.json', changed)
    stage = Mock()
    spawn = Mock()
    monkeypatch.setattr(core, 'stage_update', stage)
    monkeypatch.setattr(launcher, '_spawn', spawn)
    with pytest.raises(core.UpdateError, match='업데이트 설정과 새 설치 파일의 설정이 다릅니다'):
        launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    assert core.load_json(pin) == site['bootstrap']
    stage.assert_not_called()
    spawn.assert_not_called()


def test_cross_role_candidate_cannot_be_executed(site, monkeypatch):
    other = candidate(site, role='VIEWER')
    set_state(site, active=other)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=None))
    calls = []
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, calls))
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION) == 0
    assert len(calls) == 1 and calls[0]['role'] == 'ADMIN'
    assert calls[0] != other


def test_role_executable_mismatch_is_rejected_and_corrupt_state_is_quarantined(site, monkeypatch):
    with pytest.raises(core.UpdateError):
        launcher.run('VIEWER', site['exe'], version=INSTALLED_VERSION)
    # E-4: state.json 은 다시 만들 수 있는 캐시이므로 이제 격리 후 기본 상태로 시작한다
    # (예전에는 '상태 파일' 오류로 프로그램이 뜨지 않았다). 상세 시험은 test_update_hardening.py.
    core.atomic_json(site['profile'] / 'state.json', {'schema': 1, 'role': 'VIEWER'})
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=None))
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, []))
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION) == 0
    assert list(site['profile'].glob('state.json.bad-*'))


class Clock:
    def __init__(self):
        self.now = 0
    def monotonic(self):
        return self.now
    def sleep(self, delay):
        self.now += delay


def health(value, pid=301, nonce='own-instance', host='127.0.0.1'):
    return {'pid': pid, 'instance_id': nonce, 'version': value['version'],
            'mode': value['role'], 'port': 8000, 'host': host}


@pytest.mark.parametrize('field,bad', [('pid', 999), ('instance_id', 'other-process'),
                                     ('version', '3.14.6'), ('mode', 'VIEWER'),
                                     ('host', 'attacker.example'), ('port', True)])
def test_health_never_accepts_unrelated_server(site, monkeypatch, field, bad):
    value = candidate(site)
    local = health(value)
    path = site['profile'] / 'health.json'
    core.atomic_json(path, local)
    remote = dict(local, **{field: bad})
    monkeypatch.setattr(launcher, '_read_http_health', lambda host, port: remote)
    clock = Clock()
    monkeypatch.setattr(launcher.time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(launcher.time, 'sleep', clock.sleep)
    monkeypatch.setattr(launcher, 'STARTUP_TIMEOUT', 0.3)
    with pytest.raises(core.UpdateError, match='시간이 초과'):
        launcher._await_health(Process(), value, 'own-instance', path, '127.0.0.1')


def test_spawn_uses_stable_config_and_only_terminates_own_failed_child(site, monkeypatch):
    value = candidate(site)
    child = Process()
    unrelated = Process(pid=999)
    popen = Mock(return_value=child)
    monkeypatch.setattr(launcher.subprocess, 'Popen', popen)
    monkeypatch.setattr(launcher, '_terminate_posix_tree', lambda process, force=False: process.kill() if force else process.terminate())
    monkeypatch.setattr(launcher, '_await_health', Mock(side_effect=core.UpdateError('wrong nonce')))
    with pytest.raises(core.UpdateError):
        launcher._spawn(value, site['config'], site['profile'], Mock())
    args, kwargs = popen.call_args
    assert args[0] == [value['path'], '--onair-app']
    assert kwargs['shell'] is False and kwargs['cwd'] == str(site['config'])
    assert kwargs['env']['WLR_CONFIG_DIR'] == str(site['config'])
    assert kwargs['env']['SMART_DUTY_MODE'] == 'ADMIN'
    assert kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'] == '1'
    assert len(kwargs['env']['WLR_UPDATE_INSTANCE_ID']) == 64
    assert kwargs['stdout'] is not launcher.subprocess.PIPE
    assert child.terminated == 1 and unrelated.terminated == 0


def test_legacy_lan_host_is_preserved_in_health_probe(site, monkeypatch):
    site['ini'].write_text('[NETWORK]\nbind_host = 12.80.127.170\n', encoding='utf-8')
    assert launcher._expected_host(site['config'], 'ADMIN') == '12.80.127.170'
    value = candidate(site)
    local = health(value, host='12.80.127.170')
    path = site['profile'] / 'health.json'
    core.atomic_json(path, local)
    request = Mock(return_value=local)
    monkeypatch.setattr(launcher, '_read_http_health', request)
    launcher._await_health(Process(), value, 'own-instance', path, '12.80.127.170')
    request.assert_called_once_with('12.80.127.170', 8000)


def test_missing_bootstrap_allows_only_trusted_local_base(site, monkeypatch):
    (site['exe'].parent / 'onair_update.json').unlink()
    stage = Mock()
    monkeypatch.setattr(core, 'stage_update', stage)
    calls = []
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, calls))
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION) == 0
    assert calls[0]['sequence'] == 0
    stage.assert_not_called()


def test_no_valid_local_or_repository_has_clear_failure(site, monkeypatch):
    site['exe'].unlink()
    monkeypatch.setattr(core, 'stage_update', Mock(side_effect=OSError('offline')))
    with pytest.raises(core.UpdateError, match='실행할 수 있는 프로그램을 찾지 못했습니다'):
        launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)


def test_version_or_sequence_downgrade_cannot_be_activated(site, monkeypatch):
    active = candidate(site, '3.16.0', 2)
    old = candidate(site, '3.15.1', 3)
    set_state(site, active=active, highest=2)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=old))
    calls = []
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, calls))
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION) == 0
    assert calls == [active]
    state = core.load_json(site['profile'] / 'state.json')
    assert state['highest_sequence'] == 2


def test_preexisting_server_is_not_quarantined_or_killed(site, monkeypatch):
    active = candidate(site)
    pending = candidate(site, '3.15.1', 1)
    before = set_state(site, active=active, pending=pending, highest=1)
    existing = Process(pid=999)
    monkeypatch.setattr(launcher, '_read_system_info', lambda host, port: {'mode': 'ADMIN', 'version': '3.14.6'})
    stage, spawn = Mock(), Mock()
    monkeypatch.setattr(core, 'stage_update', stage)
    monkeypatch.setattr(launcher, '_spawn', spawn)
    with pytest.raises(core.UpdateError, match='기존 WLR OnAir'):
        launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    assert core.load_json(site['profile'] / 'state.json') == before
    assert existing.terminated == 0
    stage.assert_not_called()
    spawn.assert_not_called()


def test_legacy_server_appearing_during_start_keeps_pending_retryable(site, monkeypatch):
    active = candidate(site)
    update = candidate(site, '3.15.1', 1)
    set_state(site, active=active)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=update))
    monkeypatch.setattr(launcher, '_spawn', Mock(side_effect=core.UpdateError('duplicate server')))
    check = Mock(side_effect=[None, core.UpdateError('기존 WLR OnAir')])
    monkeypatch.setattr(launcher, '_ensure_no_running_app', check)
    with pytest.raises(core.UpdateError, match='기존 WLR OnAir'):
        launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    state = core.load_json(site['profile'] / 'state.json')
    assert state['pending'] == update and state['failed_sequences'] == []
    assert state['active'] == active


def test_storage_loss_during_start_does_not_blacklist_release(site, monkeypatch):
    active = candidate(site)
    update = candidate(site, '3.15.1', 1)
    set_state(site, active=active)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=update))
    monkeypatch.setattr(launcher, '_spawn', Mock(side_effect=core.UpdateError('DB offline')))
    monkeypatch.setattr(launcher.profiles, 'validate_profile_storage', Mock(side_effect=[None, core.UpdateError('공유 폴더 연결 실패')]))
    with pytest.raises(core.UpdateError, match='공유 폴더'):
        launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    state = core.load_json(site['profile'] / 'state.json')
    assert state['pending'] == update and state['failed_sequences'] == []
    assert state['active'] == active


def test_pyinstaller_descendant_health_is_accepted_but_foreign_pid_is_not(site, monkeypatch):
    value = candidate(site)
    root = Process(pid=100)
    monkeypatch.setattr(launcher, '_process_parent_map', lambda: {200: 100, 201: 200, 999: 888})
    assert launcher._owns_pid(root, 100)
    assert launcher._owns_pid(root, 200)
    assert launcher._owns_pid(root, 201)
    assert not launcher._owns_pid(root, 999)
    local = health(value, pid=200)
    path = site['profile'] / 'health.json'
    core.atomic_json(path, local)
    monkeypatch.setattr(launcher, '_read_http_health', lambda host, port: local)
    launcher._await_health(root, value, 'own-instance', path, '127.0.0.1')
    root.dead = True
    assert not launcher._owns_pid(root, 200)


def test_process_ancestry_cycles_are_rejected(monkeypatch):
    monkeypatch.setattr(launcher, '_process_parent_map', lambda: {200: 201, 201: 200})
    assert not launcher._owns_pid(Process(pid=100), 200)


def test_windows_stop_targets_only_live_owned_bootloader_tree(monkeypatch):
    monkeypatch.setattr(launcher, 'IS_WINDOWS', True)
    monkeypatch.setattr(launcher, '_windows_taskkill_path', lambda: 'C:/Windows/System32/taskkill.exe')
    command = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(launcher.subprocess, 'run', command)
    owned, unrelated = Process(pid=100), Process(pid=999)
    launcher._stop_owned(owned, Mock())
    assert command.call_args.args[0] == ['C:/Windows/System32/taskkill.exe', '/PID', '100', '/T', '/F']
    assert command.call_args.kwargs['shell'] is False
    assert unrelated.terminated == 0 and unrelated.killed == 0
    command.reset_mock()
    launcher._stop_owned(owned, Mock())
    command.assert_not_called()


def test_failure_to_stop_tree_blocks_fallback(site, monkeypatch):
    active = candidate(site)
    pending = candidate(site, '3.15.1', 1)
    set_state(site, active=active)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=pending))
    spawn = Mock(side_effect=launcher.OwnedProcessStopError('owned tree still alive'))
    monkeypatch.setattr(launcher, '_spawn', spawn)
    with pytest.raises(core.UpdateError, match='still alive'):
        launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    assert spawn.call_count == 1
    state = core.load_json(site['profile'] / 'state.json')
    assert state['pending'] == pending and state['failed_sequences'] == []


def test_posix_termination_uses_only_new_session_group(monkeypatch):
    process = Process(pid=100)
    process._onair_own_group = True
    monkeypatch.setattr(launcher.os, 'getpgid', lambda pid: pid)
    kill = Mock()
    monkeypatch.setattr(launcher.os, 'killpg', kill)
    launcher._terminate_posix_tree(process)
    kill.assert_called_once_with(100, launcher.signal.SIGTERM)
    assert process.terminated == 0


def test_real_profile_start_preserves_ini_and_database(tmp_path, monkeypatch):
    monkeypatch.setenv('WLR_PROFILE_ROOT', str(tmp_path / 'profiles'))
    config = launcher.profiles.prepare_profile('ADMIN', allow_new=True)
    profile = launcher.profiles.profile_dir('ADMIN')
    ini = config / 'config_admin.ini'
    db = config / 'smart_duty.db'
    before = ini.read_bytes(), db.read_bytes()
    exe = tmp_path / 'WLR_OnAir_Admin.exe'
    exe.write_bytes(b'MZ trusted local installation')
    monkeypatch.setattr(launcher, '_read_system_info', lambda host, port: None)
    monkeypatch.setattr(launcher, '_spawn', lambda *args: (Process(), profile / 'health' / 'test.json'))
    assert launcher.run('ADMIN', exe, version=INSTALLED_VERSION) == 0
    assert (ini.read_bytes(), db.read_bytes()) == before
