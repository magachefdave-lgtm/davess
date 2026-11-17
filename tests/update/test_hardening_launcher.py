"""런처 보강(E-2/3/4/9/10/12) 시험. 각 시험 위의 [위험] 주석은 막으려는 실제 사고다."""
import copy
import json
import logging
import os
from pathlib import Path
import time
from unittest.mock import Mock

import pytest

from updater import core, launcher
from test_launcher import INSTALLED_VERSION, Process, candidate, fake_spawn, set_state, site  # noqa: F401  (픽스처 재사용)


def read_state(site):
    return json.loads((site['profile'] / 'state.json').read_text(encoding='utf-8'))


def ini(site, text):
    site['ini'].write_text('[NETWORK]\nbind_host = 127.0.0.1\nshared_folder_path = Z:/original\n' + text,
                           encoding='utf-8')


# ------------------------------------------------------------------ E-2 시작 확인 시간

# [위험] 느린 PC 에서 30초를 넘기면 정상 업데이트가 영구 차단되어 그 PC 만 영원히 구버전에 머문다.
@pytest.mark.parametrize('text,expected', [
    ('', 120.0), ('[UPDATE]\n', 120.0), ('[UPDATE]\nstartup_timeout_seconds = 200\n', 200.0),
    ('[UPDATE]\nstartup_timeout_seconds = 5\n', 30.0), ('[UPDATE]\nstartup_timeout_seconds = 99999\n', 600.0),
    ('[UPDATE]\nstartup_timeout_seconds = abc\n', 120.0), ('[UPDATE]\nstartup_timeout_seconds = nan\n', 120.0),
    ('[UPDATE]\nstartup_timeout_seconds = inf\n', 120.0), ('[UPDATE]\nstartup_timeout_seconds = -3\n', 30.0),
])
def test_startup_timeout_default_clamp_and_invalid(site, text, expected):
    ini(site, text)
    assert launcher._startup_timeout(launcher._read_update_settings(site['config'], 'ADMIN')) == expected


# [위험] 한글 주석이 든 cp949 INI 를 못 읽어 설정이 무시되거나 시작 자체가 실패한다.
def test_startup_timeout_reads_cp949_ini(site):
    site['ini'].write_bytes('[NETWORK]\n; 한글 주석\nbind_host = 127.0.0.1\n[UPDATE]\nstartup_timeout_seconds = 77\n'.encode('cp949'))
    assert launcher._startup_timeout(launcher._read_update_settings(site['config'], 'ADMIN')) == 77.0


# [위험] 설정한 시간이 실제 대기에 반영되지 않으면(상수 30초 그대로) 설정이 무의미해진다.
def test_spawn_waits_for_configured_timeout(site, monkeypatch):
    ini(site, '[UPDATE]\nstartup_timeout_seconds = 222\n')
    value = candidate(site)
    monkeypatch.setattr(launcher.subprocess, 'Popen', Mock(return_value=Process()))
    wait = Mock()
    monkeypatch.setattr(launcher, '_await_health', wait)
    launcher._spawn(value, site['config'], site['profile'], Mock())
    assert wait.call_args.args[5] == 222.0


def timeout_spawn(update, calls):
    def spawn(value, config, profile, logger):
        calls.append(value)
        if value == update:
            raise launcher.StartupTimeoutError('timeout')
        return Process(), profile / 'health' / 't.json'
    return spawn


# [위험] 첫 시간 초과에서 바로 차단하면 일시적으로 느렸던 정상 버전이 영구히 막힌다.
def test_first_timeout_keeps_update_retryable_second_blocks(site, monkeypatch):
    active = candidate(site)
    update = candidate(site, '3.15.1', 1)
    set_state(site, active=active)
    stage = Mock(side_effect=[update, None, None])
    monkeypatch.setattr(core, 'stage_update', stage)
    calls = []
    monkeypatch.setattr(launcher, '_spawn', timeout_spawn(update, calls))
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION) == 0
    state = read_state(site)
    assert state['failed_sequences'] == [] and state['pending'] == update
    assert state['timeout_failures'] == {'1': 1} and state['active'] == active
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION) == 0          # 두 번째: 같은 sequence 연속 시간 초과
    state = read_state(site)
    assert state['failed_sequences'] == [1] and state['pending'] is None
    assert 'timeout_failures' not in state
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION) == 0          # 세 번째: 더 시도하지 않음
    assert calls == [update, active, update, active, active]


# [위험] 프로세스가 곧바로 죽는 명백한 불량 버전을 2번이나 사용자에게 띄우게 된다.
def test_obvious_failure_still_blocks_immediately(site, monkeypatch):
    active = candidate(site)
    update = candidate(site, '3.15.1', 1)
    set_state(site, active=active)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=update))
    calls = []

    def spawn(value, config, profile, logger):
        calls.append(value)
        if value == update:
            raise core.UpdateError('프로그램이 시작 확인 전에 종료되었습니다.')
        return Process(), profile / 'health' / 't.json'
    monkeypatch.setattr(launcher, '_spawn', spawn)
    launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    state = read_state(site)
    assert state['failed_sequences'] == [1] and 'timeout_failures' not in state


# [위험] 성공했는데 카운터가 남으면 다음 우연한 시간 초과 한 번에 정상 버전이 차단된다.
def test_success_removes_timeout_counter(site, monkeypatch):
    active = candidate(site)
    update = candidate(site, '3.15.1', 1)
    state = set_state(site, active=active, pending=update, highest=1)
    state['timeout_failures'] = {'1': 1}
    launcher._save_state(site['profile'], state)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=None))
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, []))
    launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    state = read_state(site)
    assert state['active'] == update and 'timeout_failures' not in state


# [위험] 서로 다른 sequence 의 시간 초과가 합쳐져 '연속 2회' 로 잘못 세어진다.
def test_timeouts_of_different_sequences_are_not_combined(site, monkeypatch):
    active = candidate(site)
    first = candidate(site, '3.15.1', 1)
    second = candidate(site, '3.15.2', 2)
    set_state(site, active=active)
    monkeypatch.setattr(core, 'stage_update', Mock(side_effect=[first, second]))
    calls = []

    def spawn(value, config, profile, logger):
        calls.append(value)
        if value in (first, second):
            raise launcher.StartupTimeoutError('timeout')
        return Process(), profile / 'health' / 't.json'
    monkeypatch.setattr(launcher, '_spawn', spawn)
    launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    state = read_state(site)
    assert state['failed_sequences'] == [] and state['timeout_failures'] == {'2': 1}


# [위험] 새 필드 때문에 이미 설치된 PC 의 옛 state.json 이 열리지 않아 프로그램이 안 뜬다.
def test_old_state_schema_loads_and_default_save_keeps_old_shape(site):
    old = {'schema': 1, 'role': 'ADMIN', 'active': None, 'previous': None, 'pending': None,
           'highest_sequence': 0, 'highest_version': '0.0.0', 'failed_sequences': []}
    core.atomic_json(site['profile'] / 'state.json', old)
    state = launcher._load_state(site['profile'], 'ADMIN')
    assert state['timeout_failures'] == {} and state['reset_applied'] is False
    launcher._save_state(site['profile'], state)
    assert read_state(site) == old          # 기본값이면 옛 런처가 읽는 모양 그대로 쓴다


# [위험] 손으로 고친 timeout_failures 가 이상해도(음수 등) 프로그램이 죽으면 안 된다(격리 후 재시작).
def test_bad_optional_state_values_are_quarantined(site):
    state = launcher._default_state('ADMIN')
    state['timeout_failures'] = {'x': 'y'}
    core.atomic_json(site['profile'] / 'state.json', state)
    loaded = launcher._load_state(site['profile'], 'ADMIN')
    assert loaded['timeout_failures'] == {} and list(site['profile'].glob('state.json.bad-*'))


# [위험] 관리자가 풀 방법이 없어 차단된 PC 를 파일을 직접 지워야 한다(비개발자에게 불가능).
def test_reset_failed_sequences_applies_once_and_only_to_installed_floor(site, monkeypatch):
    active = candidate(site, '3.16.0', 2)
    set_state(site, active=active, highest=3, failed=[3])
    state = read_state(site)
    state['highest_version'] = '3.16.1'
    launcher._save_state(site['profile'], state)
    ini(site, '[UPDATE]\nreset_failed_sequences = true\n')
    stage = Mock(return_value=None)
    monkeypatch.setattr(core, 'stage_update', stage)
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, []))
    launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    state = read_state(site)
    assert state['failed_sequences'] == [] and state['reset_applied'] is True
    # 하한은 설치된 active 까지만 낮아지고 그보다 낮아지지 않는다.
    assert stage.call_args.kwargs['highest_sequence'] == 2
    assert stage.call_args.kwargs['current_version'] == '3.16.0'
    # INI 가 true 로 남아 있어도 다음 실행에서 다시 적용되지 않는다.
    state['failed_sequences'] = [3]
    state['highest_sequence'] = 3
    launcher._save_state(site['profile'], state)
    launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    assert read_state(site)['failed_sequences'] == [3]
    # 값을 false 로 돌렸다가 다시 true 로 하면 다시 적용된다.
    ini(site, '[UPDATE]\nreset_failed_sequences = false\n')
    launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    assert read_state(site).get('reset_applied', False) is False
    ini(site, '[UPDATE]\nreset_failed_sequences = true\n')
    launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    assert read_state(site)['failed_sequences'] == []


# ------------------------------------------------------------------ E-3 상태 저장 실패

# [위험] 저장 실패인데 메모리에서만 sequence 가 올라가 같은 업데이트가 영영 다시 시도되지 않는다.
def test_state_save_failure_does_not_consume_sequence(site, monkeypatch, caplog):
    active = candidate(site)
    update = candidate(site, '3.15.1', 1)
    set_state(site, active=active)
    before = (site['profile'] / 'state.json').read_bytes()
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=update))
    calls = []
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, calls))
    real = launcher._save_state

    def failing(profile, state):
        if state.get('pending') == update:
            raise core.UpdateError('disk full')
        return real(profile, state)
    monkeypatch.setattr(launcher, '_save_state', failing)
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION) == 0
    assert calls == [active]                                   # 이번 실행은 로컬 정상 버전
    state = read_state(site)
    assert state['highest_sequence'] == 0 and state['pending'] is None and state['active'] == active
    monkeypatch.setattr(launcher, '_save_state', real)         # 다음 실행: 정상 저장 -> 재시도 가능
    launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    assert calls[-1] == update


# [위험] 실패 기록 저장이 안 된다고 프로그램이 아예 안 뜬다.
def test_failure_record_save_error_does_not_block_startup(site, monkeypatch):
    active = candidate(site)
    update = candidate(site, '3.15.1', 1)
    set_state(site, active=active)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=update))
    calls = []

    def spawn(value, config, profile, logger):
        calls.append(value)
        if value == update:
            raise core.UpdateError('died')
        return Process(), profile / 'health' / 't.json'
    monkeypatch.setattr(launcher, '_spawn', spawn)
    real = launcher._save_state
    failures = []

    def flaky(profile, state):
        if state.get('failed_sequences') and not failures:      # 실패 기록 저장만 한 번 실패
            failures.append(1)
            raise core.UpdateError('disk full')
        return real(profile, state)
    monkeypatch.setattr(launcher, '_save_state', flaky)
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION) == 0
    assert calls == [update, active]


# ------------------------------------------------------------------ E-4 깨진 state.json

BAD_STATES = {
    'invalid_json': b'{"schema": 1, "role": "ADM',
    'not_utf8': b'\xff\xfe\x00bad',
    'list': b'[1,2,3]',
    'wrong_role': json.dumps({'schema': 1, 'role': 'VIEWER', 'active': None, 'previous': None, 'pending': None,
                              'highest_sequence': 0, 'highest_version': '0.0.0', 'failed_sequences': []}).encode(),
    'missing_keys': b'{"schema":1,"role":"ADMIN"}',
    'bad_failed': json.dumps({'schema': 1, 'role': 'ADMIN', 'active': None, 'previous': None, 'pending': None,
                              'highest_sequence': 1, 'highest_version': '0.0.0', 'failed_sequences': [9]}).encode(),
    'bad_version': json.dumps({'schema': 1, 'role': 'ADMIN', 'active': None, 'previous': None, 'pending': None,
                               'highest_sequence': 0, 'highest_version': 'x', 'failed_sequences': []}).encode(),
    'too_big': b'{"a":"' + b'x' * 70000 + b'"}',
    'empty': b'',
}


# [위험] state.json 한 글자만 깨져도 프로그램이 영구히 안 뜬다(사용자는 파일을 고칠 수 없다).
@pytest.mark.parametrize('name', sorted(BAD_STATES))
def test_corrupt_state_is_quarantined_and_program_starts(site, monkeypatch, caplog, name):
    path = site['profile'] / 'state.json'
    path.write_bytes(BAD_STATES[name])
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=None))
    calls = []
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, calls))
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION) == 0
    bad = list(site['profile'].glob('state.json.bad-*'))
    assert len(bad) == 1 and bad[0].read_bytes() == BAD_STATES[name]      # 원본은 지우지 않고 보존
    assert read_state(site)['active']['sequence'] == 0                     # 기본 상태로 재출발
    assert 'state.json.bad-' in (site['profile'] / 'launcher.log').read_text(encoding='utf-8')
    assert len(calls) == 1


# [위험] 격리 파일이 계속 쌓여 프로필 폴더가 어지러워진다.
def test_only_three_quarantined_files_are_kept(site):
    for number in range(5):
        (site['profile'] / f'state.json.bad-20200101-00000{number}').write_text('old')
    (site['profile'] / 'state.json').write_bytes(b'garbage')
    launcher._load_state(site['profile'], 'ADMIN')
    names = sorted(p.name for p in site['profile'].glob('state.json.bad-*'))
    assert len(names) == 3
    assert not any(n.endswith(('000000', '000001', '000002')) for n in names)   # 가장 오래된 것부터 삭제
    assert any(n.startswith('state.json.bad-2') and not n.startswith('state.json.bad-2020') for n in names)


# [위험] 신뢰 핀(onair_update.json)까지 관용하면 깨진/조작된 핀 대신 저장소를 믿게 된다.
def test_bootstrap_pin_corruption_stays_strict(site, monkeypatch):
    pin = site['profile'] / 'onair_update.json'
    pin.write_bytes(b'{broken')
    stage, spawn = Mock(), Mock()
    monkeypatch.setattr(core, 'stage_update', stage)
    monkeypatch.setattr(launcher, '_spawn', spawn)
    with pytest.raises(core.UpdateError):
        launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    assert pin.read_bytes() == b'{broken'
    assert not list(site['profile'].glob('onair_update.json.bad*'))
    stage.assert_not_called()
    spawn.assert_not_called()


# [위험] state.json 이 연결(심볼릭) 파일이면 따라가서 엉뚱한 파일을 읽고 쓴다.
def test_symlink_state_is_still_rejected(site, tmp_path):
    target = tmp_path / 'elsewhere.json'
    target.write_text('{}')
    (site['profile'] / 'state.json').symlink_to(target)
    with pytest.raises(core.UpdateError, match='업데이트 기록 파일에 문제가 있어'):
        launcher._load_state(site['profile'], 'ADMIN')
    assert target.read_text() == '{}'


# [위험] 이름 바꾸기가 실패했는데 조용히 넘어가거나, 사용자가 뭘 해야 할지 모르는 오류가 나온다.
def test_quarantine_rename_failure_gives_actionable_korean_error(site, monkeypatch):
    (site['profile'] / 'state.json').write_bytes(b'garbage')
    monkeypatch.setattr(launcher.os, 'replace', Mock(side_effect=PermissionError('locked')))
    with pytest.raises(core.UpdateError) as caught:
        launcher._load_state(site['profile'], 'ADMIN')
    message = str(caught.value)
    assert str(site['profile']) in message and 'state.json' in message and '삭제하거나' in message


# [위험] state 가 초기화되면 downgrade 방지가 사라진다. 하한은 '현재 설치된 exe 버전'(base)으로 유지돼야 한다.
def test_after_reset_state_current_version_floor_is_installed_exe(site, monkeypatch):
    (site['profile'] / 'state.json').write_bytes(b'garbage')
    stage = Mock(return_value=None)
    monkeypatch.setattr(core, 'stage_update', stage)
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, []))
    launcher.run('ADMIN', site['exe'], version='3.15.7')
    assert stage.call_args.kwargs['current_version'] == '3.15.7'
    assert stage.call_args.kwargs['highest_sequence'] == 0


# ------------------------------------------------------------------ E-9 비상 정지

# [위험] 불량 버전을 뿌린 뒤 정지 수단이 없어 직원 PC 가 계속 불량 버전을 받아 간다.
def test_paused_marker_skips_update_check_and_runs_local_version(site, monkeypatch):
    active = candidate(site)
    pending = candidate(site, '3.15.1', 1)
    set_state(site, active=active, pending=pending, highest=1)
    channels = Path(site['bootstrap']['repository']) / 'channels'
    channels.mkdir(parents=True)
    (channels / 'ADMIN.paused').write_text('stop')
    stage = Mock()
    monkeypatch.setattr(core, 'stage_update', stage)
    calls = []
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, calls))
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION) == 0
    stage.assert_not_called()
    assert calls == [active]                                  # 이미 받아 둔 pending 도 정지 중에는 실행하지 않음
    state = read_state(site)
    assert state['pending'] == pending and state['active'] == active    # 설치된 버전·기록은 지우지 않음
    assert 'paused' in (site['profile'] / 'launcher.log').read_text(encoding='utf-8') or '일시 정지' in (site['profile'] / 'launcher.log').read_text(encoding='utf-8')


# [위험] 한 역할의 정지가 다른 역할(조회용)까지 막는다 / 표식을 지워도 계속 정지 상태다.
def test_pause_is_per_role_and_resumes_when_marker_removed(site, monkeypatch):
    active = candidate(site)
    set_state(site, active=active)
    channels = Path(site['bootstrap']['repository']) / 'channels'
    channels.mkdir(parents=True)
    (channels / 'VIEWER.paused').write_text('x')
    stage = Mock(return_value=None)
    monkeypatch.setattr(core, 'stage_update', stage)
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, []))
    launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    assert stage.call_count == 1
    (channels / 'ADMIN.paused').write_text('x')
    launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    assert stage.call_count == 1
    (channels / 'ADMIN.paused').unlink()
    launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    assert stage.call_count == 2


# [위험] 공유 폴더 접근 오류(권한 등)가 예외로 올라가 프로그램이 안 뜬다.
def test_paused_check_error_is_treated_as_not_paused(monkeypatch):
    monkeypatch.setattr(Path, 'exists', Mock(side_effect=PermissionError('denied')))
    assert launcher._is_paused({'repository': '/x'}, 'ADMIN') is False


# ------------------------------------------------------------------ E-10 정리·회전

def entry(name, age, is_dir=True, link=False, now=1_000_000.0):
    return (name, now - age, is_dir, link)


# [위험] 실행 중/되돌릴 버전 폴더를 정리하다 지워 프로그램이 못 뜬다. (가장 중요)
def test_select_never_returns_protected_even_if_old():
    now = 1_000_000.0
    entries = [entry(f'v{i}', i * 100, now=now) for i in range(30)]
    protected = {'v29', 'v28', 'v27', 'v0'}
    result = launcher.select_versions_to_delete(entries, protected, recent=3, now=now)
    assert not set(result) & protected
    for name in protected:
        assert name not in result


# [위험] 최근 버전까지 지워 최근에 쓰던 버전으로의 복귀가 불가능해진다.
def test_select_keeps_recent_n_beyond_protected():
    now = 1_000_000.0
    entries = [entry(f'v{i}', i * 100, now=now) for i in range(10)]     # v0 가장 최근
    result = launcher.select_versions_to_delete(entries, {'v9'}, recent=3, now=now)
    assert set(result) == {'v3', 'v4', 'v5', 'v6', 'v7', 'v8'}   # v0~v2(최근3) + v9(보호) 유지


# [위험] 파일·심볼릭 링크·최근 임시 폴더를 지우려다 엉뚱한 곳을 건드린다.
def test_select_ignores_files_links_and_recent_temp_dirs():
    now = 1_000_000.0
    entries = [entry('a', 1, now=now), entry('file.txt', 9999, is_dir=False, now=now),
               entry('link', 9999, link=True, now=now), entry('.stage-new', 60, now=now),
               entry('.stage-old', 3 * 86400, now=now), entry('b', 2, now=now), entry('c', 3, now=now),
               entry('d', 9000, now=now)]
    assert sorted(launcher.select_versions_to_delete(entries, set(), recent=3, now=now)) == ['.stage-old', 'd']


def make_versions(site, names, base=1_000_000):
    versions = site['profile'] / 'versions'
    versions.mkdir(parents=True, exist_ok=True)
    for index, name in enumerate(names):
        folder = versions / name
        folder.mkdir()
        (folder / 'WLR_OnAir_Admin.exe').write_bytes(b'x')
        os.utime(folder, (base + index, base + index))
    return versions


# [위험] 실제 폴더에서 active/previous/pending/known_good 가 삭제된다.
def test_cleanup_versions_removes_old_but_keeps_state_candidates(site):
    names = [f'3.0.{i}-{i}-aaa' for i in range(10)]
    versions = make_versions(site, names)

    def cand(name):
        return {'role': 'ADMIN', 'version': '3.0.0', 'sequence': 1, 'sha256': 'a' * 64, 'data_schema': 1,
                'path': str(versions / name / 'WLR_OnAir_Admin.exe')}
    active, previous, pending, good = cand(names[0]), cand(names[1]), cand(names[2]), cand(names[3])
    removed = launcher._cleanup_versions(site['profile'], [active, previous, pending, good], Mock())
    left = {p.name for p in versions.iterdir()}
    assert set(names[:4]) <= left
    assert set(names[-3:]) <= left                       # 그 밖의 가장 최근 3개
    assert left == set(names[:4]) | set(names[-3:])
    assert sorted(removed) == sorted(names[4:7])


# [위험] 보호할 폴더를 알 수 없는데 일단 지워 버린다.
def test_cleanup_aborts_when_a_candidate_path_cannot_be_resolved(site):
    names = [f'3.0.{i}-{i}-aaa' for i in range(8)]
    versions = make_versions(site, names)
    weird = {'path': str(site['profile'] / 'elsewhere' / 'x.exe')}
    assert launcher._cleanup_versions(site['profile'], [weird], Mock()) == []
    assert len(list(versions.iterdir())) == 8


# [위험] 잠긴(실행 중) 폴더 때문에 정리가 예외로 시작을 막는다.
def test_cleanup_skips_locked_folder_quietly(site, monkeypatch):
    names = [f'3.0.{i}-{i}-aaa' for i in range(8)]
    versions = make_versions(site, names)
    keep = {'path': str(versions / names[-1] / 'WLR_OnAir_Admin.exe')}
    real = launcher.shutil.rmtree

    def locked(path, *args, **kwargs):
        if Path(path).name == names[0]:
            raise PermissionError('in use')
        return real(path, *args, **kwargs)
    monkeypatch.setattr(launcher.shutil, 'rmtree', locked)
    removed = launcher._cleanup_versions(site['profile'], [keep], Mock())
    assert names[0] not in removed and (versions / names[0]).exists()


# [위험] 실행 경로 전체에서 정리가 실제 active 를 지운다(통합).
def test_run_cleanup_keeps_active_and_previous(site, monkeypatch):
    previous = candidate(site, '3.15.0', 0)
    active = candidate(site, '3.15.1', 1)
    set_state(site, active=active, previous=previous, highest=1)
    versions = site['profile'] / 'versions'
    for index in range(8):
        extra = versions / f'old-{index}'
        extra.mkdir()
        os.utime(extra, (1000 + index, 1000 + index))
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=None))
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, []))
    launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    left = {p.name for p in versions.iterdir()}
    assert Path(active['path']).parent.name in left and Path(previous['path']).parent.name in left
    assert {'old-5', 'old-6', 'old-7'} <= left and 'old-0' not in left


# [위험] 로그가 무한히 커져 디스크를 채운다.
def test_rotate_log_by_size_keeps_three_backups(tmp_path):
    log = tmp_path / 'launcher.log'
    for round_ in range(6):
        log.write_bytes(bytes([65 + round_]) * 50)
        assert launcher._rotate_log(log, max_bytes=10, backups=3)
    assert not log.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ['launcher.log.1', 'launcher.log.2', 'launcher.log.3']
    assert (tmp_path / 'launcher.log.1').read_bytes()[:1] == b'F'
    assert (tmp_path / 'launcher.log.3').read_bytes()[:1] == b'D'


# [위험] 작은 로그까지 회전해 최근 기록이 사라진다.
def test_rotate_log_leaves_small_file(tmp_path):
    log = tmp_path / 'application.log'
    log.write_bytes(b'x' * 5)
    assert launcher._rotate_log(log, max_bytes=10) is None and log.read_bytes() == b'x' * 5


# [위험] 다른 프로세스가 열고 있어 이름을 못 바꾸는 로그 때문에 시작이 실패한다.
def test_rotate_log_failure_is_silent(tmp_path, monkeypatch):
    log = tmp_path / 'application.log'
    log.write_bytes(b'x' * 50)
    monkeypatch.setattr(launcher.os, 'replace', Mock(side_effect=PermissionError('open')))
    assert 'application.log' in launcher._rotate_log(log, max_bytes=10)
    assert log.read_bytes() == b'x' * 50


# [위험] 실행 중인 자식이 쓰는 application.log 를 실행 도중에 회전한다 -> 시작 시점 한 번만 해야 한다.
def test_logs_rotate_at_start_only(site, monkeypatch):
    (site['profile'] / 'application.log').write_bytes(b'x' * (launcher.LOG_MAX_BYTES + 1))
    (site['profile'] / 'launcher.log').write_bytes(b'y' * (launcher.LOG_MAX_BYTES + 1))
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=None))
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, []))
    launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    assert (site['profile'] / 'application.log.1').exists() and (site['profile'] / 'launcher.log.1').exists()
    assert (site['profile'] / 'launcher.log').stat().st_size < 4096


# [위험] 살아 있는 프로그램의 health nonce 파일을 지운다 / 고아 파일이 영원히 남는다.
def test_orphan_health_files_removed_only_when_old(site):
    health = site['profile'] / 'health'
    health.mkdir()
    old, new = health / 'old.json', health / 'new.json'
    old.write_text('{}')
    new.write_text('{}')
    os.utime(old, (time.time() - 2 * 86400,) * 2)
    launcher._cleanup_health(site['profile'], Mock())
    assert not old.exists() and new.exists()


# ------------------------------------------------------------------ E-12 안내문

# [위험] 트레이 아이콘이 없는 상태에서 사용자가 어떻게 종료해야 할지 모른다.
def test_running_app_message_mentions_task_manager(site, monkeypatch):
    monkeypatch.setattr(launcher, '_read_system_info', lambda host, port: {'mode': 'ADMIN', 'version': '3.14.6'})
    with pytest.raises(core.UpdateError) as caught:
        launcher._ensure_no_running_app(site['config'], 'ADMIN')
    assert '작업 관리자' in str(caught.value) and 'WLR_OnAir_*.exe' in str(caught.value)
