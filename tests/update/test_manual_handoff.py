"""Signed packages + actual legacy supervisor, helper, application and TCP port."""
import base64
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.request import Request, build_opener, ProxyHandler
import zipfile
import pytest
from updater import core, launcher, manual_update, process_identity
from test_core import release

ROOT = Path(__file__).resolve().parents[2]
ACTOR = Path(__file__).parent / 'fixtures' / 'manual_actor.py'
LEGACY = ROOT.parent / 'WLR_OnAir_v3.18.26_Source_Python311-314_r4'


def wait(fn, seconds=12):
    end = time.monotonic() + seconds
    last = None
    while time.monotonic() < end:
        try:
            result = fn()
            if result: return result
        except (OSError, ValueError, core.UpdateError) as exc: last = exc
        time.sleep(.05)
    raise AssertionError(f'timeout: {last}')


def request(port, path='/api/system/health', data=None):
    req = Request(f'http://127.0.0.1:{port}{path}', data=None if data is None else json.dumps(data).encode(),
                  headers={'Content-Type': 'application/json'})
    with build_opener(ProxyHandler({})).open(req, timeout=.5) as response:
        return json.load(response)


def binary(version, fail=False):
    return (f'#!{sys.executable}\nimport sys\nsys.dont_write_bytecode=True\nsys.path.insert(0,{str(ACTOR.parent)!r})\n'
            f'from manual_actor import app\nraise SystemExit(app({version!r},{fail!r}))\n').encode()


def site(tmp, role):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    profile = tmp / 'profiles' / role; config = profile / 'config'; config.mkdir(parents=True)
    db = config / 'smart_duty.db'
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE sentinel (value TEXT)'); connection.execute("INSERT INTO sentinel VALUES ('preserve')")
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    ini = config / f'config_{role.lower()}.ini'
    ini.write_text(f'[NETWORK]\nshared_folder_path={config}\nbind_host=127.0.0.1\nserver_port={port}\n')
    installed = tmp / 'installed'; installed.mkdir()
    exe = installed / core.EXECUTABLES[role]; exe.write_bytes(binary('3.18.27')); exe.chmod(0o700)
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    repo = tmp / 'repository'; (repo / 'channels').mkdir(parents=True)
    core.atomic_json(installed / 'onair_update.json', dict(schema=1, product='WLR_OnAir', role=role, repository=str(repo), public_key=public))
    return dict(root=tmp, profile=profile, config=config, role=role, repo=repo, key=key, public=public, port=port, exe=exe, db=db, ini=ini)


def publish(s, version, seq, fail=False):
    package = s['repo'] / 'packages' / s['role'] / f'{version}.zip'; package.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package, 'w') as archive: archive.writestr(core.EXECUTABLES[s['role']], binary(version, fail))
    target = package.with_name(f'{version}-{core.sha256_file(package)[:12]}.zip')
    package.rename(target); package = target
    payload = dict(schema=1, product='WLR_OnAir', role=s['role'], version=version, sequence=seq,
                   package=str(package.relative_to(s['repo'])), sha256=core.sha256_file(package), size=package.stat().st_size,
                   executable=core.EXECUTABLES[s['role']], min_launcher='1.0.0', data_schema=1)
    signature = base64.b64encode(s['key'].sign(core.canonical_json(payload))).decode()
    core.atomic_json(s['repo'] / 'channels' / (s['role'] + '.json'), dict(payload=payload, signature=signature))


def start(s, hold=False):
    env = dict(os.environ, WLR_PROFILE_ROOT=str(s['root'] / 'profiles'), SMART_DUTY_MODE=s['role'], PYTHONDONTWRITEBYTECODE='1')
    if hold: env['TEST_HOLD_OLD'] = '1'
    with (s['root'] / 'process.log').open('ab') as output:
        supervisor = ROOT if s['role'] == 'MASTER' else LEGACY
        return subprocess.Popen([sys.executable, str(ACTOR), str(supervisor), s['role'], str(s['exe'])], env=env,
                                stdout=output, stderr=output, start_new_session=True)


def begin(s):
    request(s['port'], '/check', {})
    status = wait(lambda: (v if (v := request(s['port'], '/status'))['result'] == 'available' else None))
    return request(s['port'], '/prepare', dict(offer_id=status['offer_id'], instance_id=status['instance_id']))['operation']


def test_stage_uses_exact_offer_even_channel_replaced(tmp_path, monkeypatch):
    repo, profile, key, public, payload, package, manifest = release(tmp_path)
    envelope = core.load_json(manifest)
    original = core.verify_envelope
    def verify(*args):
        value = original(*args)
        manifest.write_text('{}')
        return value
    monkeypatch.setattr(core, 'verify_envelope', verify)
    candidate = core.stage_update(repo, profile, 'ADMIN', public, expected_envelope=envelope)
    assert candidate['version'] == payload['version']
    assert not (profile / 'state.json').exists()


def test_candidate_root_exit_does_not_hide_child(tmp_path):
    marker = tmp_path / 'child'
    child = subprocess.Popen([sys.executable, '-c', f'import subprocess,sys; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(2)"]);open({str(marker)!r},"w").write(str(p.pid))'], start_new_session=True)
    tracker = process_identity.CandidateTracker(child)
    child.wait(timeout=3)
    assert not tracker.stopped()
    with pytest.raises(launcher.OwnedProcessStopError):
        child._onair_tracker = tracker
        launcher._stop_owned(child, __import__('logging').getLogger('test'))
    assert process_identity.wait_candidate_stopped(tracker, 4)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process fixture')
@pytest.mark.parametrize('role', ['ADMIN', 'VIEWER', 'MASTER'])
def test_manual_helper_three_updates_then_failed_release_rolls_back(tmp_path, role):
    if role != 'MASTER' and not LEGACY.exists():
        pytest.skip('Historical supervisor fixture requires immutable legacy source')
    s = site(tmp_path, role)
    protected = {key: s[key].read_bytes() for key in ('db', 'ini', 'exe')}
    old = start(s)
    try:
        health = wait(lambda: request(s['port']))
        wait(lambda: (s['profile'] / 'state.json').exists())
        for sequence in range(1, 5):
            version = f'3.18.{27 + sequence}'
            publish(s, version, sequence, fail=sequence == 4)
            prior = health
            operation = begin(s)
            result = wait(lambda: (v if (v := manual_update.operation_result(s['profile'], operation)) and v['result'] in ('updated', 'rolled_back', 'failed') else (request(s['port'], '/status') if request(s['port'], '/status')['result'] == 'failed' else None)), 20)
            assert result['result'] == ('rolled_back' if sequence == 4 else 'updated'), result
            health = request(s['port'])
            assert health['instance_id'] != prior['instance_id'] and health['pid'] != prior['pid']
            assert health['version'] == result['current_version'] and health['port'] == s['port']
            assert health['instance_id'] == result['new_instance_id']
            assert old.wait(timeout=5) == 0
            assert not Path(f'/proc/{prior["pid"]}').exists()
            with core.profile_lock(s['profile'] / 'manual-operation'): pass
            with pytest.raises(core.UpdateError):
                with core.profile_lock(s['profile']): pass
            assert {key: s[key].read_bytes() for key in protected} == protected
        request(s['port'], '/stop', {})
        wait(lambda: not process_identity.Identity(health['pid']).alive() if Path(f'/proc/{health["pid"]}').exists() else True)
        wait(lambda: _lock_available(s['profile']))
        # The unchanged shortcut launcher selects the final cached good version.
        launched = time.time_ns()
        old = start(s)
        restored = wait(lambda: request(s['port']) if (s['profile'] / 'update_status.json').stat().st_mtime_ns >= launched else None)
        assert restored['version'] == '3.18.30'
    finally:
        try: request(s['port'], '/stop', {})
        except OSError: pass
        old.wait(timeout=8)


def _lock_available(profile):
    try:
        with core.profile_lock(profile): return True
    except core.UpdateError: return False


@pytest.mark.skipif(os.name == 'nt' or not LEGACY.exists(), reason='POSIX fixture')
def test_old_app_refuses_exit_no_second_application(tmp_path):
    s = site(tmp_path, 'ADMIN'); old = start(s, hold=True)
    try:
        health = wait(lambda: request(s['port']))
        wait(lambda: (s['profile'] / 'state.json').exists())
        before = (s['profile'] / 'state.json').read_bytes()
        publish(s, '3.18.28', 1)
        operation = begin(s)
        result = wait(lambda: (v if (v := manual_update.operation_result(s['profile'], operation)) and v['result'] == 'failed' else None))
        assert '종료' in result['message']
        assert request(s['port']) == health and old.poll() is None
        assert (s['profile'] / 'state.json').read_bytes() == before
        with core.profile_lock(s['profile'] / 'manual-operation'): pass
    finally:
        request(s['port'], '/stop', {})
        old.wait(timeout=8)


def test_windows_identity_waits_handle_not_reused_pid(monkeypatch):
    class Kernel:
        def WaitForSingleObject(self, handle, timeout):
            assert handle == 456
            return 0
        def CloseHandle(self, handle): pass
    monkeypatch.setattr(process_identity, 'IS_WINDOWS', True)
    monkeypatch.setattr(process_identity, '_kernel', lambda: Kernel())
    identity = object.__new__(process_identity.Identity)
    identity.pid, identity.created, identity.path, identity.handle = 123, 1, 'old.exe', 456
    assert not identity.alive()  # PID 123 may now belong to an unrelated process.
    identity.close()


def test_windows_job_active_child_blocks_fallback_after_parent_exit(monkeypatch):
    class Kernel:
        def QueryInformationJobObject(self, job, info, pointer, size, returned):
            assert job == 42 and info == 1
            pointer._obj.active = 1
            return True
    monkeypatch.setattr(process_identity, 'IS_WINDOWS', True)
    monkeypatch.setattr(process_identity, '_kernel', lambda: Kernel())
    tracker = object.__new__(process_identity.CandidateTracker)
    tracker.job, tracker.ownership_complete = 42, True
    assert not tracker.stopped()
    tracker.ownership_complete = False
    assert not process_identity.wait_candidate_stopped(tracker, 0)


def test_prepared_restart_rejects_port_takeover_without_state_mutation(tmp_path, monkeypatch):
    s = site(tmp_path, 'ADMIN')
    from updater import profiles
    monkeypatch.setattr(profiles, 'profile_dir', lambda role: s['profile'])
    core.atomic_json(s['profile'] / 'onair_update.json', core.load_json(s['exe'].parent / 'onair_update.json'))
    publish(s, '3.18.28', 1)
    offer = manual_update.check_release(s['profile'], 'ADMIN', '3.18.27')['offer_id']
    prepared = manual_update.prepare_release(s['profile'], 'ADMIN', offer, '3.18.27')
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', s['port'])); occupied.listen()
        with pytest.raises(core.UpdateError, match='포트'):
            launcher.run_prepared_restart('ADMIN', s['exe'], '3.18.27', prepared,
                prepared['expected_state'], s['port'], expected_pin=prepared['expected_pin'])
    assert not (s['profile'] / 'state.json').exists()


def test_windows_job_attached_before_resume_and_helper_can_break_away(monkeypatch):
    import ctypes
    events = []
    class Kernel:
        def CreateJobObjectW(self, *args): events.append('create'); return 99
        def SetInformationJobObject(self, job, info, pointer, size):
            assert pointer._obj.basic.flags == 0x800  # explicit breakaway only, no kill-on-close
            events.append('limits'); return True
        def AssignProcessToJobObject(self, job, process): events.append('assign'); return True
        def CloseHandle(self, job): events.append('close')
    class Function:
        def __call__(self, handle):
            assert handle == 77
            events.append('resume'); return 0
    class Ntdll:
        NtResumeProcess = Function()
    class Process:
        _handle = 77
        def kill(self): pytest.fail('must not kill a successfully contained candidate')
    monkeypatch.setattr(process_identity, 'IS_WINDOWS', True)
    monkeypatch.setattr(process_identity, '_kernel', lambda: Kernel())
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *args, **kwargs: Ntdll(), raising=False)
    tracker = process_identity.CandidateTracker(Process())
    assert tracker.ownership_complete
    assert events == ['create', 'limits', 'assign', 'resume']
    tracker.close()


def test_helper_waits_for_parent_to_release_operation_lock(tmp_path):
    import threading
    acquired = threading.Event()
    failures = []
    def helper():
        try:
            with manual_update._helper_operation_lock(tmp_path, timeout=1): acquired.set()
        except Exception as exc: failures.append(exc)
    with core.profile_lock(tmp_path / 'manual-operation'):
        thread = threading.Thread(target=helper); thread.start()
        assert not acquired.wait(.1)
    thread.join(2)
    assert not failures and acquired.is_set()


def test_successful_candidate_root_exit_retains_profile_lock_until_child_exit(tmp_path):
    import logging
    import threading
    child = subprocess.Popen([sys.executable, '-c', 'import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(.8)"])'], start_new_session=True)
    child._onair_tracker = process_identity.CandidateTracker(child)
    entered = threading.Event()
    def supervise():
        with core.profile_lock(tmp_path):
            entered.set()
            launcher._wait_for_exit(child, logging.getLogger('test-supervise'))
    thread = threading.Thread(target=supervise); thread.start(); assert entered.wait(1)
    wait(lambda: child.poll() is not None, 2)
    try:
        with pytest.raises(core.UpdateError):
            with core.profile_lock(tmp_path): pass
    finally:
        thread.join(3)
    assert not thread.is_alive()
    with core.profile_lock(tmp_path): pass
