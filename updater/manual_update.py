"""Signed manual update preparation and independent graceful supervisor handoff."""
from datetime import datetime
from pathlib import Path
import threading
import time
import copy
import hashlib
import os
import secrets
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from . import profiles, process_identity
from . import core, launcher


def check_release(profile: Path, role: str, running_version: str) -> dict:
    profile = Path(profile)
    current = core.parse_version(running_version)
    result = dict(current_version=running_version, available_version=None,
                  checked_at=datetime.now().astimezone().isoformat(timespec='seconds'))
    pin_path = profile / 'onair_update.json'
    if not pin_path.exists():
        return dict(result, result='no_repository', message='업데이트 저장소가 설정되지 않았습니다.')
    pin = core.validate_bootstrap(core.load_json(pin_path), role)
    if (profile / launcher.TRUST_RECOVERY_REQUEST).exists():
        raise core.UpdateError('업데이트 신뢰 복구를 먼저 완료해 주세요.')
    # Do not call _load_state: it can quarantine corrupt state. A check is read-only.
    state_path = profile / 'state.json'
    state = launcher._validate_state(core.load_json(state_path), role) if state_path.exists() else launcher._default_state(role)
    if launcher._is_paused(pin, role):
        return dict(result, result='paused', message='관리자가 업데이트를 일시 정지했습니다.')
    envelope = core.load_json(Path(pin['repository']) / 'channels' / f'{role}.json')
    payload = core.verify_envelope(envelope, pin['public_key'], role)
    result['available_version'] = payload['version']
    sequence = payload['sequence']
    if sequence < state['highest_sequence']:
        raise core.UpdateError('저장소의 게시 순서가 이전 기록보다 낮습니다. 관리자에게 알려 주세요.')
    if sequence in state['failed_sequences']:
        return dict(result, result='blocked', message='이 PC에서 시작에 실패해 차단한 버전입니다. 관리자에게 확인해 주세요.')
    if core.parse_version(payload['version']) <= current:
        return dict(result, result='up_to_date', message='현재 최신 버전을 사용하고 있습니다.')
    if core.parse_version(payload['version']) < core.parse_version(state['highest_version']):
        raise core.UpdateError('이 PC가 이미 확인한 버전보다 이전 게시입니다.')
    if sequence == state['highest_sequence']:
        pending = state['pending']
        if not pending or pending['sequence'] != sequence or pending['version'] != payload['version']:
            raise core.UpdateError('이미 처리한 게시입니다. 관리자에게 업데이트 기록을 확인해 주세요.')
        core.verify_candidate(pending, profile, role)
    offer_id = hashlib.sha256(core.canonical_json(envelope)).hexdigest()
    return dict(result, result='available', offer_id=offer_id,
                message='새 버전이 있습니다. 작업을 저장한 뒤 업데이트 후 다시 시작을 누르세요.')


def read_state(profile, role):
    path = Path(profile) / 'state.json'
    return (launcher._validate_state(core.load_json(path), role) if path.exists()
            else launcher._with_optional_defaults(launcher._default_state(role)))


def prepare_release(profile, role, offer_id, running_version=None, progress=None):
    profile = Path(profile)
    pin = core.validate_bootstrap(core.load_json(profile / 'onair_update.json'), role)
    state = read_state(profile, role)
    version = running_version or (state['active'] or {}).get('version', '0.0.0')
    check = check_release(profile, role, version)
    envelope = core.load_json(Path(pin['repository']) / 'channels' / f'{role}.json')
    if (check['result'] != 'available' or check.get('offer_id') != offer_id or
            hashlib.sha256(core.canonical_json(envelope)).hexdigest() != offer_id):
        raise core.UpdateError('게시가 변경되었습니다. 최신 버전을 다시 확인해 주세요.')
    payload = core.verify_envelope(envelope, pin['public_key'], role)
    pending = state['pending']
    same_pending = bool(pending and pending['sequence'] == payload['sequence'])
    # A local pending hash alone is not a binding to this signed ZIP. Re-stage
    # the fixed envelope, even for an equal sequence, to authenticate its bytes.
    candidate = core.stage_update(Path(pin['repository']), profile, role, pin['public_key'],
                                  payload['sequence'] - 1 if same_pending else state['highest_sequence'], version,
                                  expected_envelope=envelope, progress=progress)
    if same_pending and candidate != pending:
        raise core.UpdateError('기존 대기 버전과 게시 파일이 다릅니다. 관리자에게 확인해 주세요.')
    if candidate is None:
        raise core.UpdateError('준비할 새 버전이 없습니다.')
    return dict(candidate=candidate, envelope=envelope, package_sha256=payload['sha256'],
                expected_state=state, expected_pin=pin)


def revalidate_prepared(profile, role, prepared):
    pin = core.validate_bootstrap(core.load_json(Path(profile) / 'onair_update.json'), role)
    if pin != prepared['expected_pin'] or read_state(profile, role) != prepared['expected_state']:
        raise core.UpdateError('업데이트 설정이나 기록이 변경되었습니다. 다시 확인해 주세요.')
    if (Path(profile) / launcher.TRUST_RECOVERY_REQUEST).exists() or launcher._is_paused(pin, role):
        raise core.UpdateError('업데이트가 중지되었습니다. 현재 프로그램을 유지합니다.')
    envelope = core.load_json(Path(pin['repository']) / 'channels' / f'{role}.json')
    if envelope != prepared['envelope']:
        raise core.UpdateError('게시가 변경되었습니다. 최신 버전을 다시 확인해 주세요.')
    core.verify_envelope(envelope, pin['public_key'], role)
    core.verify_candidate(prepared['candidate'], Path(profile), role)


def _operation_path(profile, operation, suffix):
    if not isinstance(operation, str) or len(operation) != 64 or any(c not in '0123456789abcdef' for c in operation):
        raise core.UpdateError('업데이트 작업 번호가 올바르지 않습니다.')
    directory = Path(profile) / 'manual-update'
    core._mkdir(directory)
    path = directory / (operation + suffix)
    core._no_links(path)
    return path


def operation_result(profile, operation):
    path = _operation_path(profile, operation, '.result.json')
    return core.load_json(path) if path.exists() else None


class LiveCheck:
    """One bounded worker; blocking SMB cannot create unbounded worker threads."""
    RESTART_STATES = frozenset(('downloading', 'waiting', 'ready', 'restarting'))
    AUTOMATIC_CHECK_INTERVAL = 300

    def __init__(self):
        self.lock = threading.RLock()
        self.worker = None
        self.started = 0
        self.last_check_started = None
        self.last_progress = 0
        self.runtime = None
        self.token = None
        self.prepared = None
        self.operation = None
        self.cancelled = False
        self.value = dict(result='idle', message='최신 버전 확인을 눌러 주세요.')

    def configure(self, *, profile, role, version, instance_id, port, host, executable):
        self.runtime = dict(profile=Path(profile), role=role, version=version,
                            instance_id=instance_id, port=port, host=host, executable=str(executable))

    def status(self, profile=None, operation=None):
        with self.lock:
            if operation and profile:
                value = operation_result(profile, operation)
                if value is not None:
                    return dict(value, supported=bool(self.runtime), restart_supported=bool(self.runtime),
                                busy=value['result'] not in ('updated', 'rolled_back', 'failed', 'cancelled'))
            now = time.monotonic()
            if self.value['result'] == 'checking' and now - self.started > 15:
                self.cancelled = True
                self.value = dict(result='check_failed', message='저장소 확인 시간이 초과되었습니다. 공유 폴더 연결을 확인해 주세요.')
            elif self.value['result'] == 'downloading' and (now - self.started > 300 or now - self.last_progress > 60):
                self.cancelled = True
                self.value = dict(result='failed', message='다운로드 시간이 초과되었습니다. 현재 프로그램을 유지합니다.')
            value = dict(self.value, operation=self.operation, state=self.value['result'],
                         busy=bool(self.worker and self.worker.is_alive()) or self.value['result'] in self.RESTART_STATES,
                         supported=bool(self.runtime), restart_supported=bool(self.runtime))
            if self.runtime:
                value.update(instance_id=self.runtime['instance_id'], current_version=self.runtime['version'])
            return value

    def start(self, profile, role, version, *, automatic=False):
        with self.lock:
            # The supervisor may still own a restart after the preparer exits.
            # A header poll (or another explicit check) must keep its capability.
            if ((self.worker and self.worker.is_alive()) or
                    self.value['result'] in self.RESTART_STATES):
                return self.operation
            now = time.monotonic()
            if (automatic and self.last_check_started is not None and
                    now - self.last_check_started < self.AUTOMATIC_CHECK_INTERVAL):
                return self.operation
            self.started = self.last_check_started = now
            self.cancelled = False
            self.operation = secrets.token_hex(32)
            self.value = dict(result='checking', current_version=version, available_version=None,
                              checked_at=None, message='게시된 버전을 확인하고 있습니다…')
            def work():
                try:
                    value = check_release(profile, role, version)
                except (core.UpdateError, OSError, ValueError, TypeError, KeyError):
                    value = dict(result='check_failed', current_version=version, available_version=None,
                                 checked_at=datetime.now().astimezone().isoformat(timespec='seconds'),
                                 message='게시 정보를 확인하지 못했습니다. 공유 폴더 연결 또는 업데이트 설정을 관리자에게 확인해 주세요.')
                with self.lock:
                    if time.monotonic() - self.started <= 15 and not self.cancelled: self.value = value
            self.worker = threading.Thread(target=work, name='onair-live-update-check', daemon=True)
            self.worker.start()
            return self.operation

    def prepare(self, offer_id, instance_id):
        with self.lock:
            if not self.runtime:
                raise core.UpdateError('업데이트 실행 파일로 시작한 프로그램에서만 자동 재시작할 수 있습니다.')
            if instance_id != self.runtime['instance_id']:
                raise core.UpdateError('프로그램이 바뀌었습니다. 화면을 새로고침해 주세요.')
            if self.worker and self.worker.is_alive():
                raise core.UpdateError('이미 업데이트 작업을 진행하고 있습니다.')
            if self.value.get('offer_id') != offer_id or self.value['result'] != 'available':
                raise core.UpdateError('최신 버전을 먼저 확인해 주세요.')
            runtime = dict(self.runtime)
            self.operation = operation = secrets.token_hex(32)
            self.token = secrets.token_hex(32)
            self.started = self.last_progress = time.monotonic(); self.cancelled = False
            self.value = dict(result='downloading', available_version=self.value['available_version'],
                              checked_at=self.value.get('checked_at'),
                              message='새 버전을 내려받고 확인하고 있습니다…')
            self.worker = threading.Thread(target=self._prepare_worker, args=(runtime, offer_id, operation),
                                           name='onair-manual-prepare', daemon=True)
            self.worker.start()
            return operation

    def _prepare_worker(self, runtime, offer_id, operation):
        profile, role = runtime['profile'], runtime['role']
        identities = []
        try:
            # Reserve preparation; helper acquires the same lock after spawn.
            with core.profile_lock(profile / 'manual-operation'):
                state = read_state(profile, role)
                active = state['active']
                path = core.verify_candidate(active, profile, role)
                if Path(runtime['executable']).resolve() != path.resolve():
                    raise core.UpdateError('현재 실행 파일이 검증된 버전과 다릅니다.')
                settings = launcher._read_update_settings(profile / 'config', role)
                configured = settings.getint('NETWORK', 'server_port', fallback=core.DEFAULT_PORTS[role])
                if configured != runtime['port'] or launcher._expected_host(profile / 'config', role) != runtime['host']:
                    raise core.UpdateError('접속 설정이 변경되었습니다. 트레이에서 종료하고 다시 실행한 뒤 업데이트해 주세요.')
                def progress(count, total):
                    with self.lock:
                        self.status()
                        if self.cancelled: raise core.UpdateError('업데이트 준비 시간이 초과되었습니다.')
                        self.last_progress = time.monotonic()
                        self.value.update(received_bytes=count, total_bytes=total)
                prepared = prepare_release(profile, role, offer_id, runtime['version'], progress)
                revalidate_prepared(profile, role, prepared)
                self.prepared = prepared
                self.status()
                if self.cancelled: raise core.UpdateError('업데이트 준비 시간이 초과되었습니다.')
                identities = process_identity.capture_old_chain(role, os.getpid(), path)
                ticket = dict(schema=1, role=role, operation=operation, token_sha256=hashlib.sha256(self.token.encode()).hexdigest(),
                              created_at=time.time(), instance_id=runtime['instance_id'], old_chain=[i.document() for i in identities],
                              executable=str(path), version=runtime['version'], host=runtime['host'], port=runtime['port'], prepared=prepared)
                ticket_path = _operation_path(profile, operation, '.ticket.json')
                core.atomic_json(ticket_path, ticket)
                env = os.environ.copy()
                env.update(PYINSTALLER_RESET_ENVIRONMENT='1', WLR_MANUAL_UPDATE_TOKEN=self.token)
                with (profile / 'manual-update.log').open('ab') as log:
                    child = subprocess.Popen([str(path), '--onair-manual-update', operation],
                        cwd=str(profile / 'config'), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                        close_fds=True, start_new_session=os.name != 'nt',
                        creationflags=(getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0) | (0x01000000 if os.environ.get('WLR_MANUAL_RESTART') == '1' else 0)) if os.name == 'nt' else 0)
                with self.lock:
                    self.value.update(result='waiting', message='저장을 마친 뒤 프로그램을 다시 시작합니다…')
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                result = operation_result(profile, operation)
                if result:
                    with self.lock: self.value.update(result)
                    if result['result'] in ('failed', 'cancelled', 'updated', 'rolled_back'): return
                if child.poll() is not None: raise core.UpdateError('업데이트 도우미가 종료되었습니다. 현재 프로그램을 유지합니다.')
                time.sleep(.2)
            raise core.UpdateError('재시작 준비 시간이 초과되었습니다. 현재 프로그램을 유지합니다.')
        except Exception as exc:
            with self.lock:
                self.cancelled = True; self.token = None
                self.value.update(result='failed', message=str(exc) if isinstance(exc, core.UpdateError) else '업데이트를 준비하지 못했습니다. 현재 프로그램을 유지합니다.')
        finally:
            for item in identities: item.close()

    def validate_shutdown(self):
        if self.cancelled or not self.runtime or not self.prepared:
            raise core.UpdateError('업데이트 준비가 취소되었습니다.')
        runtime = self.runtime
        revalidate_prepared(runtime['profile'], runtime['role'], self.prepared)
        settings = launcher._read_update_settings(runtime['profile'] / 'config', runtime['role'])
        port = settings.getint('NETWORK', 'server_port', fallback=core.DEFAULT_PORTS[runtime['role']])
        if port != runtime['port'] or launcher._expected_host(runtime['profile'] / 'config', runtime['role']) != runtime['host']:
            raise core.UpdateError('접속 설정이 변경되어 재시작을 취소했습니다.')

    def authorize_shutdown(self, operation, token):
        with self.lock:
            if (self.cancelled or self.operation != operation or not self.token or
                    not secrets.compare_digest(self.token, token) or
                    self.value['result'] not in ('waiting', 'ready')):
                return False
            self.token = None
            self.value.update(result='restarting', message='프로그램을 다시 시작하고 있습니다…')
            return True


live_check = LiveCheck()


@contextmanager
def _helper_operation_lock(profile, timeout=10):
    """Allow the spawning parent to finish its lock-protected preparation."""
    deadline = time.monotonic() + timeout
    while True:
        lock = core.profile_lock(Path(profile) / 'manual-operation')
        try:
            lock.__enter__()
            break
        except core.UpdateError:
            if time.monotonic() >= deadline:
                raise core.UpdateError('다른 업데이트 작업이 끝나지 않아 재시작하지 않았습니다.')
            time.sleep(.05)
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


def run_helper(role, operation_id):
    """Entry point in the current signed EXE; survives the unchanged old launcher."""
    from urllib.request import Request, build_opener, ProxyHandler
    profile = profiles.profile_dir(role)
    token = os.environ.pop('WLR_MANUAL_UPDATE_TOKEN', '')
    ticket_path = _operation_path(profile, operation_id, '.ticket.json')
    result_path = _operation_path(profile, operation_id, '.result.json')
    chain = []
    stack = ExitStack()
    ticket = None
    def report(result, message, **extra):
        value = dict(operation=operation_id, result=result, state=result, message=message,
                     instance_id=ticket['instance_id'] if ticket else None, **extra)
        core.atomic_json(result_path, value)
    try:
        stack.enter_context(_helper_operation_lock(profile))
        ticket = core.load_json(ticket_path)
        if (ticket.get('schema') != 1 or ticket.get('role') != role or ticket.get('operation') != operation_id or
                not token or not secrets.compare_digest(ticket.get('token_sha256', ''), hashlib.sha256(token.encode()).hexdigest()) or
                not 0 <= time.time() - ticket['created_at'] <= 60):
            raise core.UpdateError('업데이트 작업 확인이 만료되었거나 일치하지 않습니다.')
        ticket_path.unlink()  # one-shot; the secret never enters status or logs
        prepared = ticket['prepared']
        active = prepared['expected_state']['active']
        executable = core.verify_candidate(active, profile, role)
        if str(executable) != ticket['executable']:
            raise core.UpdateError('업데이트 실행 파일이 다릅니다.')
        if getattr(sys, 'frozen', False) and Path(sys.executable).resolve() != executable.resolve():
            raise core.UpdateError('업데이트 도우미 실행 파일이 다릅니다.')
        chain = process_identity.reopen_chain(ticket['old_chain'])
        revalidate_prepared(profile, role, prepared)
        report('ready', '저장 중인 작업이 완료되기를 기다립니다…')
        url_host = launcher._url_host(ticket['host'])
        request = Request(f"http://{url_host}:{ticket['port']}/api/system/manual-update-ready",
                          data=core.canonical_json({'operation': operation_id}), method='POST',
                          headers={'Content-Type': 'application/json', 'X-OnAir-Update-Token': token})
        # Drain (15s) + final validation (5s) + response scheduling margin.
        with build_opener(ProxyHandler({})).open(request, timeout=40) as response:
            if response.status != 200: raise core.UpdateError('프로그램이 재시작 준비를 취소했습니다.')
        report('restarting', '기존 프로그램이 종료되기를 기다립니다…')
        if not process_identity.wait_old_chain(chain, 30):
            raise core.UpdateError('기존 프로그램의 종료를 확인하지 못해 업데이트를 중단했습니다.')
        def activated(candidate, health):
            report('updated' if candidate == prepared['candidate'] else 'rolled_back',
                   '새 버전으로 업데이트했습니다.' if candidate == prepared['candidate'] else '새 버전을 시작하지 못해 이전 버전으로 복귀했습니다.',
                   available_version=prepared['candidate']['version'], current_version=candidate['version'],
                   new_instance_id=health['instance_id'], role=role, port=health['port'])
            stack.close()  # operation lock released; launcher.lock remains held
        return launcher.run_prepared_restart(role, executable, ticket['version'], prepared,
                    prepared['expected_state'], ticket['port'], expected_pin=prepared['expected_pin'], on_activated=activated)
    except Exception as exc:
        report('failed', str(exc) if isinstance(exc, core.UpdateError) else '업데이트를 완료하지 못했습니다. 프로그램을 다시 실행해 주세요.')
        return 1
    finally:
        stack.close()
        for item in chain: item.close()
