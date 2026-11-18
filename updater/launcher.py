"""Per-role update supervisor; application data never enters a release folder.

The installed executable is the initial local trust anchor. Repository packages
are accepted only by updater.core's pinned-signature verification. Activation
requires a health response from the exact child process started here.
"""
from __future__ import annotations

import copy
import json
import configparser
import ipaddress
import logging
import os
from pathlib import Path
import secrets
import shutil
import signal
import stat
import subprocess
import time
from datetime import datetime
from urllib.request import ProxyHandler, build_opener

from . import core, profiles
from .core import DEFAULT_PORTS, EXECUTABLES


STARTUP_TIMEOUT = 120.0          # 기본값(초). 프로필 INI [UPDATE] startup_timeout_seconds 로 조정
STARTUP_TIMEOUT_MIN = 30.0
STARTUP_TIMEOUT_MAX = 600.0
TIMEOUT_BLOCK_COUNT = 2          # 같은 순서 번호가 연속으로 이 횟수만큼 시간 초과여야 차단
STATE_BAD_KEEP = 3               # 격리된 state.json.bad-* 보관 개수
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUPS = 3
HEALTH_ORPHAN_SECONDS = 24 * 3600
VERSIONS_KEEP_RECENT = 3
HEALTH_POLL_INTERVAL = 0.15
HEALTH_MAX_BYTES = 4096
STATE_MAX_BYTES = 65536
IS_WINDOWS = os.name == 'nt'
STATUS_FILE = 'update_status.json'      # 프로그램 화면 [연결 정보] 가 읽는 마지막 업데이트 확인 결과
RESET_REQUEST = 'reset_blocked.request'  # 화면의 '차단된 업데이트 다시 허용' 버튼이 남기는 요청 표식
TRUST_RECOVERY_REQUEST = 'trust_recovery.pending.json'

# v3.18.0 직원 PC 화면·메시지 상자에 그대로 보이는 문장. 원인(영어·내부 용어)은 launcher.log 에만 남긴다.
CAPTURE_HINT = '이 창을 캡처해 관리자에게 보내 주세요.'
FALLBACK_PREVIOUS = '프로그램을 업데이트하지 못해 이전 버전으로 실행합니다. ' + CAPTURE_HINT
FALLBACK_CURRENT = '프로그램을 업데이트하지 못해 지금 버전으로 실행합니다. ' + CAPTURE_HINT

# 결과 코드 -> 화면 배너를 띄울지(실패여서 사용자가 알아야 하는지)
STATUS_NOTICE = {'updated': False, 'up_to_date': False, 'paused': False, 'no_repository': False,
                 'check_failed': True, 'blocked': True, 'start_failed': True, 'save_failed': True}


def write_update_status(profile: Path, result: str, message: str, *, current: str | None = None,
                        available: str | None = None, logger: logging.Logger | None = None) -> None:
    """마지막 업데이트 확인 결과를 프로필에 남긴다(실패해도 실행은 계속)."""
    value = {'schema': 1, 'checked_at': datetime.now().astimezone().isoformat(timespec='seconds'),
             'result': result, 'message': message, 'notice': STATUS_NOTICE.get(result, True),
             'current_version': current, 'available_version': available,
             'log_path': str(Path(profile) / 'launcher.log')}
    try:
        core.atomic_json(Path(profile) / STATUS_FILE, value)
    except (core.UpdateError, OSError, ValueError) as exc:
        if logger is not None:
            logger.warning('업데이트 상태 표시 파일을 저장하지 못했습니다: %s', exc)


class OwnedProcessStopError(core.UpdateError):
    """No fallback is safe until the failed owned process tree has stopped."""


class StartupTimeoutError(core.UpdateError):
    """시작 확인 시간 초과. 느린 PC 일 수 있어 즉시 차단하지 않고 횟수를 센다."""


def _default_state(role: str) -> dict:
    return {'schema': 1, 'role': role, 'active': None, 'previous': None,
            'pending': None, 'highest_sequence': 0, 'highest_version': '0.0.0',
            'failed_sequences': []}


# 나중에 추가된 선택 키. 옛 state.json 에는 없으므로 없으면 기본값으로 읽고,
# 기본값일 때는 파일에 쓰지 않아 옛 런처와도 같은 모양을 유지한다.
_OPTIONAL_STATE_KEYS = {'timeout_failures': dict, 'reset_applied': bool}


def _with_optional_defaults(state: dict) -> dict:
    state.setdefault('timeout_failures', {})
    state.setdefault('reset_applied', False)
    return state


def _validate_state(state: dict, role: str) -> dict:
    required = set(_default_state(role))
    keys = set(state)
    if not required <= keys or not keys <= required | set(_OPTIONAL_STATE_KEYS):
        raise core.UpdateError('업데이트 상태 파일의 형식 또는 프로그램 구분이 잘못되었습니다.')
    if type(state.get('schema')) is not int or state.get('schema') != 1 or state.get('role') != role:
        raise core.UpdateError('업데이트 상태 파일의 형식 또는 프로그램 구분이 잘못되었습니다.')
    highest = state['highest_sequence']
    failed = state['failed_sequences']
    if type(highest) is not int or highest < 0:
        raise core.UpdateError('업데이트 상태의 배포 차수 기록이 잘못되었습니다.')
    if not isinstance(failed, list) or any(type(n) is not int or n < 1 or n > highest for n in failed):
        raise core.UpdateError('실패한 업데이트 기록이 잘못되었습니다.')
    if len(failed) != len(set(failed)):
        raise core.UpdateError('실패한 업데이트 기록이 중복되었습니다.')
    core.parse_version(state['highest_version'])
    for key in ('active', 'previous', 'pending'):
        candidate = state[key]
        if candidate is not None and not isinstance(candidate, dict):
            raise core.UpdateError('설치된 버전 정보가 잘못되었습니다.')
    for key, kind in _OPTIONAL_STATE_KEYS.items():
        if key in state and type(state[key]) is not kind:
            raise core.UpdateError('업데이트 상태의 부가 기록이 잘못되었습니다.')
    for name, count in state.get('timeout_failures', {}).items():
        if not (isinstance(name, str) and name.isdigit() and int(name) >= 1 and type(count) is int and count >= 1):
            raise core.UpdateError('업데이트 상태의 시간 초과 기록이 잘못되었습니다.')
    return _with_optional_defaults(state)


def _quarantine_state(profile: Path, path: Path, reason: str, logger: logging.Logger) -> None:
    """깨진 state.json 을 지우지 않고 이름을 바꿔 격리한다(최근 STATE_BAD_KEEP 개만 보관)."""
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    target = path.with_name(f'{path.name}.bad-{stamp}')
    number = 1
    while os.path.lexists(target):
        number += 1
        target = path.with_name(f'{path.name}.bad-{stamp}-{number}')
    try:
        os.replace(path, target)
    except OSError as exc:
        raise core.UpdateError(
            '업데이트 상태 파일(state.json)이 손상되었고 자동으로 정리하지 못했습니다.\n'
            f'프로그램을 모두 종료한 뒤, 폴더 "{profile}" 안의 state.json 파일을 삭제하거나 '
            '다른 이름으로 바꾸고 다시 실행해 주세요. (설정과 데이터 파일은 건드리지 마세요.)') from exc
    logger.warning('state.json 이 올바르지 않아 %s 로 격리하고 기본 상태로 다시 시작합니다. 사유: %s',
                   target.name, reason)
    try:
        olds = sorted(p for p in profile.glob('state.json.bad-*') if p.is_file() and not p.is_symlink())
        for old in olds[:-STATE_BAD_KEEP] if len(olds) > STATE_BAD_KEEP else []:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _load_state(profile: Path, role: str, logger: logging.Logger | None = None) -> dict:
    """state.json 은 다시 만들 수 있는 캐시이므로 이상이 있으면 격리 후 기본값으로 재출발한다.

    (신뢰 핀인 onair_update.json 은 여기와 달리 엄격하게 유지된다: _pin_bootstrap)
    """
    logger = logger or logging.getLogger('onair.launcher')
    path = profile / 'state.json'
    if not os.path.lexists(path):
        return _with_optional_defaults(_default_state(role))
    if path.is_symlink():
        raise core.UpdateError('업데이트 기록 파일에 문제가 있어 시작하지 않았습니다.')
    try:
        return _validate_state(core.load_json(path, max_bytes=STATE_MAX_BYTES), role)
    except (core.UpdateError, OSError, ValueError, TypeError, KeyError) as exc:
        _quarantine_state(profile, path, str(exc), logger)
        return _with_optional_defaults(_default_state(role))


def _save_state(profile: Path, state: dict) -> None:
    value = dict(state)
    if not value.get('timeout_failures'):
        value.pop('timeout_failures', None)
    if not value.get('reset_applied'):
        value.pop('reset_applied', None)
    core.atomic_json(profile / 'state.json', value)


def _try_save_state(profile: Path, state: dict, logger: logging.Logger) -> bool:
    """저장을 시도하고 실패해도 예외를 던지지 않는다(상태 파일 문제로 프로그램이 안 뜨면 안 됨)."""
    try:
        _save_state(profile, state)
        return True
    except (core.UpdateError, OSError, ValueError) as exc:
        logger.warning('업데이트 상태를 저장하지 못했습니다: %s', exc)
        return False


def _pin_bootstrap(profile: Path, executable: Path, role: str) -> dict | None:
    """An adjacent changed bootstrap cannot silently change an established pin."""
    if os.path.lexists(profile / TRUST_RECOVERY_REQUEST):
        raise core.UpdateError('업데이트 신뢰 복구가 끝나지 않았습니다. 관리자가 지문을 확인한 뒤 같은 복구 명령을 다시 실행해 주세요.')
    stable_path = profile / 'onair_update.json'
    installed_path = executable.parent / 'onair_update.json'
    stable = None
    if stable_path.is_symlink():
        raise core.UpdateError('업데이트 설정 파일에 문제가 있어 시작하지 않았습니다.')
    if stable_path.exists():
        stable = core.validate_bootstrap(core.load_json(stable_path), role)
    installed = None
    if installed_path.is_symlink():
        raise core.UpdateError('설치 파일의 업데이트 설정에 문제가 있어 시작하지 않았습니다.')
    if installed_path != stable_path and installed_path.exists():
        installed = core.validate_bootstrap(core.load_json(installed_path), role)
    if stable is not None:
        if installed is not None and installed != stable:
            raise core.UpdateError('이 PC에 저장된 업데이트 설정과 새 설치 파일의 설정이 다릅니다. 기존 설정을 그대로 두고 시작하지 않았습니다.')
        return stable
    if installed is not None:
        core.atomic_json(stable_path, installed)
    return installed


def reenroll_update_trust(role: str, executable: Path, *, version: str,
                          fingerprint: str, confirm: bool = False) -> Path:
    """Explicit operator recovery after independently verifying a replacement key.

    Archive the old trust and repository-specific state before changing either.
    A durable marker blocks normal launch after interruption; the same verified
    command resumes from the original archive, never from partially reset state.
    """
    if role not in EXECUTABLES or confirm is not True:
        raise core.UpdateError('신뢰 복구에는 프로그램 구분과 명시적인 --confirm 확인이 필요합니다.')
    core.parse_version(version)
    executable = Path(executable).absolute()
    core._no_links(executable)
    if executable.name != EXECUTABLES[role] or not executable.is_file():
        raise core.UpdateError('복구할 역할의 새 설치 실행파일을 사용하세요.')
    installed = executable.parent / 'onair_update.json'
    replacement = core.validate_bootstrap(core.load_json(installed), role)
    expected = core.public_key_fingerprint(replacement['public_key'])
    if not isinstance(fingerprint, str) or fingerprint.strip().upper() != expected:
        raise core.UpdateError('새 공개 키 지문이 일치하지 않습니다. 별도로 받은 지문의 16자리를 모두 확인하세요.')
    profile = profiles.profile_dir(role)
    with core.profile_lock(profile):
        config = profile / 'config'
        profiles.validate_profile_storage(role, config)
        _ensure_no_running_app(config, role)
        marker = profile / TRUST_RECOVERY_REQUEST
        core._no_links(marker)
        if os.path.lexists(marker):
            transaction = core.load_json(marker)
            if (set(transaction) != {'schema', 'role', 'archive', 'bootstrap', 'version'} or
                    transaction['schema'] != 1 or transaction['role'] != role or
                    transaction['bootstrap'] != replacement or transaction['version'] != version):
                raise core.UpdateError('진행 중인 신뢰 복구와 설치본이 다릅니다. 처음 복구에 쓴 설치본과 지문으로 다시 실행하세요.')
            name = transaction['archive']
            if (not isinstance(name, str) or not name.startswith('trust-') or
                    len(name) != len('trust-') + 32 or any(c not in '0123456789abcdef' for c in name[6:])):
                raise core.UpdateError('신뢰 복구 보관 위치가 올바르지 않습니다.')
            archive = profile / 'recovery' / name
            core._no_links(archive)
            old_pin = core.validate_bootstrap(core.load_json(archive / 'onair_update.json'), role)
            old_state = _validate_state(core.load_json(archive / 'state.json'), role)
        else:
            old_pin = core.validate_bootstrap(core.load_json(profile / 'onair_update.json'), role)
            if old_pin == replacement:
                raise core.UpdateError('이미 같은 업데이트 설정을 신뢰하고 있습니다. 신뢰 복구가 필요하지 않습니다.')
            # Refuse corrupt state here: recovery must not silently erase a
            # version floor that cannot be read. Normal launch remains separate.
            old_state = (_validate_state(core.load_json(profile / 'state.json'), role)
                         if os.path.lexists(profile / 'state.json') else _with_optional_defaults(_default_state(role)))
            archive = None
        floor = old_state['highest_version']
        for key in ('active', 'previous', 'pending'):
            candidate = old_state[key]
            if candidate is not None:
                core.verify_candidate(candidate, profile, role)
                floor = max((floor, candidate['version']), key=core.parse_version)
        if core.parse_version(version) < core.parse_version(floor):
            raise core.UpdateError(f'기존에 확인한 버전({floor})보다 낮은 설치본으로 신뢰를 복구할 수 없습니다. 같은 버전 이상을 받으세요.')
        fresh = _default_state(role)
        fresh['highest_version'] = max((floor, version), key=core.parse_version)
        fresh['reset_applied'] = True  # Do not reapply an old INI reset to the new trust domain.
        # Already verified local rollback programs may remain usable. Their old
        # repository sequences have no meaning in the newly enrolled repository.
        for key in ('active', 'previous'):
            if old_state[key] is not None:
                fresh[key] = dict(old_state[key], sequence=0)
        if archive is None:
            snapshots = {}
            for name in ('onair_update.json', 'state.json', STATUS_FILE, RESET_REQUEST):
                source = profile / name
                if os.path.lexists(source):
                    with core._open_regular(source) as stream:
                        contents = stream.read(STATE_MAX_BYTES + 1)
                    if len(contents) > STATE_MAX_BYTES:
                        raise core.UpdateError('복구 전에 보관할 업데이트 기록이 허용 크기를 초과합니다.')
                    snapshots[name] = contents
            # Even a PC with no state gets an explicit original-state snapshot.
            snapshots.setdefault('state.json', core.canonical_json(old_state))
            root = profile / 'recovery'
            core._mkdir(root)
            archive = root / ('trust-' + secrets.token_hex(16))
            archive.mkdir(mode=0o700)
            for name, contents in snapshots.items():
                profiles._publish_new_file(archive / name, lambda path, raw=contents: path.write_bytes(raw))
            core._fsync_dir(archive)
            core._fsync_dir(root)
            core.atomic_json(marker, {'schema': 1, 'role': role, 'archive': archive.name,
                                      'bootstrap': replacement, 'version': version})
        # Do not remove the marker on any failure: normal startup stays blocked
        # until the same explicit, verified recovery command has completed.
        _save_state(profile, fresh)
        core.atomic_json(profile / 'onair_update.json', replacement)
        for name in (STATUS_FILE, RESET_REQUEST):
            path = profile / name
            core._no_links(path)
            path.unlink(missing_ok=True)
        core._fsync_dir(profile)
        marker.unlink()
        core._fsync_dir(profile)
        return archive


def _valid_candidate(candidate: dict | None, profile: Path, role: str,
                     logger: logging.Logger) -> dict | None:
    if candidate is None:
        return None
    try:
        core.verify_candidate(candidate, profile, role)
        return candidate
    except (core.UpdateError, OSError, ValueError, TypeError) as exc:
        logger.warning('로컬 버전 검증 실패: %s', exc)
        return None


def _seed_bundled(executable: Path, profile: Path, role: str, version: str) -> dict:
    """Copy the trusted installed EXE, never an arbitrary repository executable."""
    core.parse_version(version)
    if executable.name != EXECUTABLES[role] or executable.is_symlink() or not executable.is_file():
        raise core.UpdateError('처음 실행할 배포용 프로그램을 찾을 수 없습니다. 해당 구분의 설치 ZIP을 다시 풀어 주세요.')
    digest = core.sha256_file(executable)
    versions = profile / 'versions'
    versions.mkdir(parents=True, exist_ok=True)
    if versions.is_symlink():
        raise core.UpdateError('프로그램 보관 폴더에 문제가 있어 시작하지 않았습니다.')
    target_dir = versions / f'bundled-{version}-{digest[:12]}'
    target = target_dir / EXECUTABLES[role]
    candidate = {'role': role, 'version': version, 'sequence': 0,
                 'path': str(target.absolute()), 'sha256': digest, 'data_schema': 1}
    if target_dir.exists():
        core.verify_candidate(candidate, profile, role)
        return candidate
    staging = versions / f'.bootstrap-{secrets.token_hex(16)}'
    staging.mkdir()
    try:
        local = staging / EXECUTABLES[role]
        shutil.copy2(executable, local)
        if core.sha256_file(local) != digest:
            raise core.UpdateError('초기 프로그램 복사 중 내용이 변경되었습니다. 다시 실행해 주세요.')
        os.replace(staging, target_dir)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    core.verify_candidate(candidate, profile, role)
    return candidate


def _read_update_settings(config: Path, role: str) -> configparser.ConfigParser:
    """[UPDATE] 설정 읽기. 어떤 문제가 있어도 빈 설정(=기본값)으로 처리한다(utf-8-sig -> cp949)."""
    path = config / f'config_{role.lower()}.ini'
    for encoding in ('utf-8-sig', 'cp949'):
        settings = configparser.ConfigParser(interpolation=None)
        try:
            settings.read(path, encoding=encoding)
            return settings
        except (UnicodeDecodeError, configparser.Error, OSError):
            continue
    return configparser.ConfigParser(interpolation=None)


def _startup_timeout(settings: configparser.ConfigParser) -> float:
    """시작 확인 제한 시간(초). 잘못된 값은 기본값, 범위는 30~600초로 제한한다."""
    raw = settings.get('UPDATE', 'startup_timeout_seconds', fallback='').strip()
    if not raw:
        return STARTUP_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return STARTUP_TIMEOUT
    if value != value or value in (float('inf'), float('-inf')):
        return STARTUP_TIMEOUT
    return min(max(value, STARTUP_TIMEOUT_MIN), STARTUP_TIMEOUT_MAX)


def _reset_requested(settings: configparser.ConfigParser) -> bool:
    return settings.get('UPDATE', 'reset_failed_sequences', fallback='').strip().lower() in ('1', 'true', 'yes', 'on')


def _expected_host(config: Path, role: str) -> str:
    settings = configparser.ConfigParser(interpolation=None)
    path = config / f'config_{role.lower()}.ini'
    try:
        settings.read(path, encoding='utf-8-sig')
    except (UnicodeDecodeError, configparser.MissingSectionHeaderError):
        settings.read(path, encoding='cp949')
    host = settings.get('NETWORK', 'bind_host', fallback='127.0.0.1').strip() or '127.0.0.1'
    if host in ('0.0.0.0', 'localhost'):
        return '127.0.0.1'
    return '::1' if host == '::' else host


def _url_host(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
        return f'[{host}]' if address.version == 6 else host
    except ValueError:
        if not host or any(c in host for c in '/?#@:[]\\') or any(c.isspace() for c in host):
            raise core.UpdateError('시작 확인용 접속 주소가 올바르지 않습니다.')
        return host


def _read_system_info(host: str, port: int) -> object:
    opener = build_opener(ProxyHandler({}))
    with opener.open(f'http://{_url_host(host)}:{port}/api/system/info', timeout=0.2) as response:
        if response.status != 200:
            return None
        raw = response.read(16385)
    return json.loads(raw.decode('utf-8')) if len(raw) <= 16384 else None


def _ensure_no_running_app(config: Path, role: str) -> None:
    """Detect a previous/orphaned server; never attach to or terminate it."""
    host = _expected_host(config, role)
    settings = configparser.ConfigParser(interpolation=None)
    path = config / f'config_{role.lower()}.ini'
    try:
        settings.read(path, encoding='utf-8-sig')
    except (UnicodeDecodeError, configparser.MissingSectionHeaderError):
        settings.read(path, encoding='cp949')
    try:
        port = settings.getint('NETWORK', 'server_port', fallback=DEFAULT_PORTS[role])
    except ValueError:
        port = DEFAULT_PORTS[role]
    if not 1 <= port <= 65535:
        raise core.UpdateError('설정된 프로그램 포트가 올바르지 않습니다.')
    for current in range(port, min(port + 21, 65536)):
        try:
            info = _read_system_info(host, current)
            running_role = info.get('distributionRole', info.get('mode')) if isinstance(info, dict) else None
            if not isinstance(running_role, str) or running_role not in EXECUTABLES:
                continue
            core.parse_version(info.get('version'))
        except (OSError, ValueError, core.UpdateError):
            continue
        # Older releases refuse any existing WLR instance on their configured
        # port. A role-aware server can share the PC using the next free port.
        if running_role == role or (current == port and 'distributionRole' not in info):
            raise core.UpdateError('기존 WLR OnAir 프로그램이 실행 중입니다. 작업을 저장하고 해당 프로그램의 트레이에서 종료한 뒤 다시 실행해 주세요. 트레이 아이콘이 없으면 작업 관리자에서 WLR_OnAir_*.exe 를 종료해 주세요. 기존 프로그램은 종료하지 않았습니다.')


def _health_matches(value: object, candidate: dict, nonce: str, host: str) -> bool:
    if not isinstance(value, dict) or set(value) != {'pid', 'instance_id', 'version', 'mode', 'port', 'host'}:
        return False
    return (type(value['pid']) is int and value['pid'] > 0 and
            value['instance_id'] == nonce and value['version'] == candidate['version'] and
            value['mode'] == candidate['role'] and value['host'] == host and type(value['port']) is int and
            1 <= value['port'] <= 65535)


def _process_parent_map() -> dict[int, int]:
    """Snapshot Windows process ancestry, including PyInstaller's Python child."""
    if not IS_WINDOWS:
        return {}
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise core.UpdateError('프로그램 시작 확인을 위한 Windows 프로세스 목록을 읽지 못했습니다.')
    parents = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return parents


def _owns_pid(process, pid: int) -> bool:
    if type(pid) is not int or pid < 1 or process.poll() is not None:
        return False
    if pid == process.pid:
        return True
    parents = _process_parent_map()
    seen = set()
    current = pid
    for _ in range(64):
        if current in seen or current not in parents:
            return False
        seen.add(current)
        current = parents[current]
        if current == process.pid:
            return process.poll() is None
    return False


def _read_http_health(host: str, port: int) -> object:
    # Never send a local health nonce through an environment-configured proxy.
    opener = build_opener(ProxyHandler({}))
    url_host = _url_host(host)
    with opener.open(f'http://{url_host}:{port}/api/system/health', timeout=1.0) as response:
        if response.status != 200:
            return None
        raw = response.read(HEALTH_MAX_BYTES + 1)
    if len(raw) > HEALTH_MAX_BYTES:
        return None
    return json.loads(raw.decode('utf-8'))


def _await_health(process, candidate: dict, nonce: str, health_path: Path, host: str,
                  timeout: float | None = None) -> None:
    deadline = time.monotonic() + (STARTUP_TIMEOUT if timeout is None else timeout)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise core.UpdateError('프로그램이 시작 확인 전에 종료되었습니다. 기존 프로그램이 실행 중인지 확인해 주세요.')
        try:
            if health_path.exists() and not health_path.is_symlink():
                local = core.load_json(health_path, max_bytes=HEALTH_MAX_BYTES)
                if _health_matches(local, candidate, nonce, host) and _owns_pid(process, local['pid']):
                    remote = _read_http_health(host, local['port'])
                    if remote == local and _health_matches(remote, candidate, nonce, host):
                        if _owns_pid(process, remote['pid']):
                            return
        except (OSError, ValueError, core.UpdateError):
            pass
        time.sleep(HEALTH_POLL_INTERVAL)
    raise StartupTimeoutError('프로그램 시작 확인 시간이 초과되었습니다. 기존 프로그램 종료 여부와 로컬 접속 설정을 확인해 주세요.')


def _windows_taskkill_path() -> str:
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    kernel.GetSystemDirectoryW.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel.GetSystemDirectoryW(buffer, len(buffer))
    if length == 0 or length >= len(buffer):
        raise OwnedProcessStopError('Windows 프로그램 종료 도구의 경로를 확인하지 못했습니다.')
    return str(Path(buffer.value) / 'taskkill.exe')


def _terminate_posix_tree(process, force=False) -> None:
    if getattr(process, '_onair_own_group', False):
        try:
            if os.getpgid(process.pid) != process.pid:
                raise OwnedProcessStopError('자체 프로그램 프로세스 그룹을 확인하지 못했습니다.')
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            return
        except ProcessLookupError:
            if process.poll() is not None:
                return
    process.kill() if force else process.terminate()


def _stop_owned(process, logger: logging.Logger) -> None:
    """Stop only our live Popen process tree; never kill a discovered server PID."""
    tracker = getattr(process, '_onair_tracker', None)
    if process.poll() is not None:
        if tracker is not None and not tracker.stopped():
            raise OwnedProcessStopError('새 프로그램의 자식이 남아 있어 이전 버전을 시작하지 않았습니다.')
        return
    if IS_WINDOWS:
        # A onefile EXE has a bootloader parent and Python child. Killing only
        # the bootloader would leave the application running during rollback.
        command = [_windows_taskkill_path(), '/PID', str(process.pid), '/T', '/F']
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, shell=False, timeout=10,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode != 0:
                raise OwnedProcessStopError('시작에 실패한 자체 프로그램의 종료를 확인하지 못했습니다. 이전 버전을 중복 실행하지 않았습니다.')
            process.wait(timeout=5)
            if tracker is not None:
                from .process_identity import wait_candidate_stopped
                if not wait_candidate_stopped(tracker):
                    raise OwnedProcessStopError('새 프로그램의 종료 확인이 불완전하여 복귀를 중단했습니다.')
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OwnedProcessStopError('자체 프로그램의 종료 확인에 실패했습니다. 트레이에서 종료한 후 다시 실행해 주세요.') from exc
        return
    _terminate_posix_tree(process)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        logger.warning('시작에 실패한 자체 자식 프로세스의 종료를 마무리합니다.')
        _terminate_posix_tree(process, force=True)
        process.wait(timeout=5)
    if tracker is not None:
        from .process_identity import wait_candidate_stopped
        if not wait_candidate_stopped(tracker):
            raise OwnedProcessStopError('새 프로그램의 자식이 남아 있어 복귀를 중단했습니다.')


def _spawn(candidate: dict, config: Path, profile: Path, logger: logging.Logger):
    executable = core.verify_candidate(candidate, profile, candidate['role'])
    health_dir = profile / 'health'
    health_dir.mkdir(mode=0o700, exist_ok=True)
    if health_dir.is_symlink():
        raise core.UpdateError('프로그램 시작 확인 폴더에 문제가 있어 시작하지 않았습니다.')
    nonce = secrets.token_hex(32)
    health_path = health_dir / f'{nonce}.json'
    environment = os.environ.copy()
    environment.update({'WLR_CONFIG_DIR': str(config.absolute()),
                        'WLR_UPDATE_INSTANCE_ID': nonce, 'WLR_HEALTH_FILE': str(health_path.absolute()),
                        'SMART_DUTY_MODE': candidate['role'],
                        'PYINSTALLER_RESET_ENVIRONMENT': '1', 'WLR_SUPERVISOR_PID': str(os.getpid())})
    context = vars(logger).get('manual_context')
    if not isinstance(context, dict):
        context = None
    if context:
        environment.update(WLR_MANUAL_RESTART='1', WLR_REQUIRED_PORT=str(context['port']))
    process = None
    try:
        # File output avoids full pipes blocking a long-running server.
        with open(profile / 'application.log', 'ab') as output:
            process = subprocess.Popen([str(executable), '--onair-app'], cwd=str(config),
                                       env=environment, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT,
                                       close_fds=True, shell=False,
                                       creationflags=(getattr(subprocess, 'CREATE_NO_WINDOW', 0) | (4 if context else 0)) if IS_WINDOWS else 0,
                                       start_new_session=not IS_WINDOWS)
        process._onair_own_group = not IS_WINDOWS
        if context:
            from .process_identity import CandidateTracker
            process._onair_tracker = CandidateTracker(process)
        _await_health(process, candidate, nonce, health_path, _expected_host(config, candidate['role']),
                      _startup_timeout(_read_update_settings(config, candidate['role'])))
        if context and core.load_json(health_path)['port'] != context['port']:
            raise core.UpdateError('기존 접속 포트와 달라 업데이트를 취소했습니다.')
        return process, health_path
    except BaseException:
        if process is not None:
            _stop_owned(process, logger)
        try:
            health_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _same(a: dict | None, b: dict | None) -> bool:
    return bool(a and b and a == b)


def _wait_for_exit(process, logger: logging.Logger) -> int:
    while True:
        try:
            result = process.wait()
            tracker = getattr(process, '_onair_tracker', None)
            if tracker is not None:
                # A onefile root can exit before its child. Keep the profile
                # owned until the complete captured Job/session has stopped.
                while not tracker.stopped():
                    time.sleep(HEALTH_POLL_INTERVAL)
                tracker.close()
            return result
        except KeyboardInterrupt:
            logger.info('사용 중인 프로그램을 유지합니다. 종료하려면 프로그램의 트레이 종료를 사용하세요.')


# ---------------------------------------------------------------- 정리·회전 (E-10)

def _rotate_log(path: Path, max_bytes: int = LOG_MAX_BYTES, backups: int = LOG_BACKUPS) -> str | None:
    """크기가 max_bytes 를 넘으면 path.1 ... path.N 으로 밀어낸다. 시작 시점에 한 번만 호출한다.

    다른 프로세스가 열고 있어 이름을 바꿀 수 없으면 조용히 건너뛴다(로그 때문에 시작이 막히면 안 됨).
    """
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size <= max_bytes:
            return None
        oldest = path.with_name(f'{path.name}.{backups}')
        if oldest.exists():
            oldest.unlink()
        for number in range(backups - 1, 0, -1):
            source = path.with_name(f'{path.name}.{number}')
            if source.exists():
                os.replace(source, path.with_name(f'{path.name}.{number + 1}'))
        os.replace(path, path.with_name(f'{path.name}.1'))
        return f'{path.name} 이(가) 커서 {path.name}.1 로 회전했습니다.'
    except OSError as exc:
        return f'{path.name} 회전을 건너뜁니다(다른 프로그램이 사용 중일 수 있음): {exc}'


def _cleanup_health(profile: Path, logger: logging.Logger, now: float | None = None,
                    max_age: float = HEALTH_ORPHAN_SECONDS) -> None:
    """종료 때 지워지지 못한 오래된 health/*.json (nonce 파일)만 정리한다."""
    health = profile / 'health'
    now = time.time() if now is None else now
    try:
        if not health.is_dir() or health.is_symlink():
            return
        for item in health.glob('*.json'):
            try:
                if item.is_file() and not item.is_symlink() and now - item.stat().st_mtime > max_age:
                    item.unlink()
            except OSError:
                continue
    except OSError as exc:
        logger.info('시작 확인 폴더 정리를 건너뜁니다: %s', exc)


def select_versions_to_delete(entries, protected, recent: int = VERSIONS_KEEP_RECENT,
                              now: float | None = None, stale_seconds: float = 24 * 3600) -> list[str]:
    """삭제할 versions/ 하위 폴더 이름을 고르는 순수 함수.

    entries: (이름, 수정시각, 폴더인가, 링크인가) 목록.
    protected: 절대 지우면 안 되는 이름(active/previous/known_good/pending/현재 실행).
    protected 를 뺀 나머지 중 가장 최근 recent 개는 남기고, 그 외를 돌려준다.
    점(.)으로 시작하는 임시 폴더는 stale_seconds 보다 오래된 것만 삭제 대상이다.
    """
    now = time.time() if now is None else now
    protected = set(protected)
    stale, normal = [], []
    for name, mtime, is_dir, is_link in entries:
        if not is_dir or is_link or name in protected:
            continue
        if name.startswith('.'):
            if now - mtime > stale_seconds:
                stale.append(name)
        else:
            normal.append((mtime, name))
    normal.sort(reverse=True)
    result = stale + [name for _, name in normal[max(recent, 0):]]
    assert not set(result) & protected
    return result


def _protected_version_dirs(profile: Path, candidates) -> set[str] | None:
    """후보들의 versions/ 하위 폴더 이름. 하나라도 해석할 수 없으면 None(=정리 중단)."""
    versions = Path(os.path.abspath(profile / 'versions'))
    names = set()
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            relative = Path(os.path.abspath(candidate['path'])).relative_to(versions)
            names.add(relative.parts[0])
        except (KeyError, TypeError, ValueError, IndexError):
            return None
    return names


def _cleanup_versions(profile: Path, candidates, logger: logging.Logger,
                      recent: int = VERSIONS_KEEP_RECENT, now: float | None = None) -> list[str]:
    protected = _protected_version_dirs(profile, candidates)
    if protected is None:
        logger.warning('버전 폴더 정리를 건너뜁니다: 보호할 버전 위치를 확인하지 못했습니다.')
        return []
    versions = profile / 'versions'
    try:
        if not versions.is_dir() or versions.is_symlink():
            return []
        entries = []
        for item in versions.iterdir():
            info = item.lstat()
            entries.append((item.name, info.st_mtime, stat.S_ISDIR(info.st_mode),
                            stat.S_ISLNK(info.st_mode)))
    except OSError as exc:
        logger.warning('버전 폴더 정리를 건너뜁니다: %s', exc)
        return []
    removed = []
    for name in select_versions_to_delete(entries, protected, recent, now):
        try:
            shutil.rmtree(versions / name)
            removed.append(name)
        except OSError as exc:
            logger.info('버전 폴더 %s 는 사용 중이라 정리하지 않았습니다: %s', name, exc)
    if removed:
        logger.info('오래된 버전 폴더 %d개를 정리했습니다.', len(removed))
    return removed


def _is_paused(bootstrap: dict, role: str) -> bool:
    """저장소의 channels/<역할>.paused 표식이 있으면 비상 정지 상태(서명 형식 밖의 신호)."""
    try:
        return (Path(bootstrap['repository']) / 'channels' / f'{role}.paused').exists()
    except OSError:
        return False


def _run_locked(role: str, executable: Path, version: str,
                profile: Path, logger: logging.Logger, *, prepared=None, on_activated=None) -> int:
    config = profiles.prepare_profile(role)
    profiles.validate_profile_storage(role, config)
    _ensure_no_running_app(config, role)
    settings = _read_update_settings(config, role)
    message = _rotate_log(profile / 'application.log')
    if message:
        logger.info(message)
    _cleanup_health(profile, logger)
    state = _load_state(profile, role, logger)
    bootstrap = _pin_bootstrap(profile, executable, role)
    active = _valid_candidate(state['active'], profile, role, logger)
    previous = _valid_candidate(state['previous'], profile, role, logger)
    known_good = active or previous
    # 관리자 수동 해제: 화면 버튼의 요청 표식(한 번), 또는 INI 의 reset_failed_sequences = true 를 한 번만 적용한다.
    # 순서 번호 하한은 '설치되어 검증된 active/previous' 까지만 낮춘다(그보다 낮추지 않는다).
    request = profile / RESET_REQUEST
    button_request = os.path.lexists(request)
    if prepared is None and (button_request or _reset_requested(settings)):
        if button_request or not state['reset_applied']:
            fresh = copy.deepcopy(state)
            fresh.update(failed_sequences=[], timeout_failures={}, pending=None, reset_applied=True,
                         highest_sequence=max([c['sequence'] for c in (active, previous) if c], default=0),
                         highest_version=max([c['version'] for c in (active, previous) if c] or ['0.0.0'],
                                             key=core.parse_version))
            if button_request and not _reset_requested(settings):
                fresh['reset_applied'] = False   # 버튼 요청은 INI 와 달리 '다시 false 로' 되돌릴 필요가 없다
            if _try_save_state(profile, fresh, logger):
                logger.warning('관리자 요청(%s)에 따라 차단된 업데이트 기록을 비웠습니다. '
                               '저장소의 업데이트를 다시 확인합니다.',
                               '화면의 차단된 업데이트 다시 허용' if button_request else 'reset_failed_sequences')
                state = fresh
                if button_request:
                    try:
                        request.unlink()
                    except OSError:
                        pass
    elif prepared is None and state['reset_applied']:
        fresh = copy.deepcopy(state)
        fresh['reset_applied'] = False
        if _try_save_state(profile, fresh, logger):
            state = fresh
    # 설치된 exe(base): 캐시된 버전이 없을 때, 또는 사용자가 더 새 버전의 설치 ZIP 을 풀어
    # 설치본이 캐시(known_good)보다 새 버전일 때 준비한다. 같은 버전(내용만 다름)이거나 더 오래된
    # 설치본은 캐시를 그대로 쓴다(실행할 때마다 오가거나 낮은 버전으로 내려가지 않도록).
    older_previous = None        # 설치본 우선 시 밀려난 예전 previous(마지막 대체 후보)
    installed_newer = (known_good is not None and
                       core.parse_version(version) > core.parse_version(known_good['version']))
    base = None
    if known_good is None or installed_newer:
        try:
            base = _seed_bundled(executable, profile, role, version)
        except (core.UpdateError, OSError) as exc:
            logger.warning('초기 로컬 프로그램 준비 실패: %s', exc)
    if installed_newer and base is not None:
        logger.info('설치된 프로그램(%s)이 저장된 버전(%s)보다 새 버전이라 설치된 프로그램을 우선 실행합니다.',
                    version, known_good['version'])
        # 설치본을 기준 버전으로, 예전 캐시는 되돌림(previous) 대상으로 삼는다.
        older_previous = previous
        previous, known_good = known_good, base
    pending = _valid_candidate(state['pending'], profile, role, logger)
    if pending is not None and (pending['sequence'] in state['failed_sequences'] or
                                pending['sequence'] != state['highest_sequence']):
        pending = None
    if (pending is not None and base is not None and
            core.parse_version(pending['version']) <= core.parse_version(base['version'])):
        logger.info('대기 중인 업데이트(%s)가 설치된 프로그램(%s)보다 새 버전이 아니라 적용하지 않습니다.',
                    pending['version'], base['version'])
        pending = None
    retain_pending = False       # 이번 실행에서 대체 버전이 떠도 pending 을 지우지 않고 다음에 재시도
    check = ('up_to_date', '업데이트를 확인했습니다. 지금 버전이 최신입니다.', None)
    versions = [state['highest_version']]
    versions.extend(c['version'] for c in (known_good, base, pending) if c is not None)
    current_version = max(versions, key=core.parse_version)
    if prepared is not None:
        if pending != prepared:
            raise core.UpdateError('확정한 업데이트와 대기 버전이 다릅니다.')
        check = ('up_to_date', '확정한 새 버전을 시작합니다.', prepared['version'])
    elif bootstrap is not None and _is_paused(bootstrap, role):
        logger.warning('관리자가 이 프로그램의 업데이트를 일시 정지했습니다(저장소의 %s.paused 표식). '
                       '업데이트 확인을 건너뛰고 설치된 로컬 버전으로 실행합니다.', role)
        check = ('paused', '관리자가 업데이트를 일시 정지했습니다. 지금 설치된 버전으로 실행합니다.', None)
        if pending is not None:
            retain_pending = True    # 정지 중에는 실행하지 않되, 해제 후를 위해 기록은 보존
            pending = None
    elif bootstrap is not None:
        try:
            updated = core.stage_update(Path(bootstrap['repository']), profile, role,
                                        bootstrap['public_key'],
                                        highest_sequence=state['highest_sequence'],
                                        current_version=current_version)
            if updated is not None:
                core.verify_candidate(updated, profile, role)
                if updated['sequence'] <= state['highest_sequence'] or updated['sequence'] in state['failed_sequences']:
                    check = ('blocked', f"새 버전({updated['version']})은 이 PC에서 전에 시작하지 못해 막아 두었습니다. "
                             + FALLBACK_CURRENT + ' 관리자가 해결했다고 알려 주면 [연결 정보 → 차단된 업데이트 다시 허용]을 누른 뒤 '
                             '프로그램을 다시 시작하세요.', updated['version'])
                    raise core.UpdateError('이미 처리했거나 차단된 업데이트 배포 차수입니다. 저장소가 교체·복구된 것 같으면 관리자에게 알려 주세요.')
                if core.parse_version(updated['version']) <= core.parse_version(current_version):
                    raise core.UpdateError('기존 버전보다 오래되거나 같은 업데이트입니다.')
                # 복사본에 변경 -> 저장 성공 시에만 원본 교체. 저장에 실패하면 순서 번호를 소모하지 않는다.
                proposed = copy.deepcopy(state)
                proposed['pending'] = updated
                proposed['highest_sequence'] = updated['sequence']
                proposed['highest_version'] = updated['version']
                proposed['timeout_failures'] = {k: v for k, v in proposed['timeout_failures'].items()
                                                if int(k) == updated['sequence']}
                try:
                    _save_state(profile, proposed)
                except (core.UpdateError, OSError, ValueError) as exc:
                    logger.warning('업데이트 상태를 저장하지 못해 새 버전(%s)을 이번 실행에는 적용하지 않습니다. '
                                   '설치된 로컬 버전으로 실행하며 다음 실행에서 다시 시도합니다: %s',
                                   updated['version'], exc)
                    check = ('save_failed', f"새 버전({updated['version']})을 받았지만 이 PC에 업데이트 기록을 저장하지 못해 "
                             '이번에는 적용하지 않았습니다. 다음에 프로그램을 시작할 때 다시 시도합니다.', updated['version'])
                else:
                    state = proposed
                    pending = updated
        except (core.UpdateError, OSError, ValueError) as exc:
            logger.warning('업데이트 확인 실패. 검증된 로컬 버전을 사용합니다: %s', exc)
            if check[0] != 'blocked':
                reason = ('공유 폴더 연결이 끊겨 업데이트 폴더를 확인하지 못했습니다. ' if isinstance(exc, OSError)
                          else '새 버전 파일을 확인하지 못했습니다. ')
                check = ('check_failed', reason + FALLBACK_CURRENT, None)
    else:
        logger.info('업데이트 저장소 설정이 없습니다. 설치된 로컬 버전으로 실행합니다.')
        check = ('no_repository', '자동 업데이트가 설정되지 않은 설치본입니다. 지금 버전으로 실행합니다.', None)
    choices = []
    failed_now = []              # 이번 실행에서 시작에 실패한 후보(되돌림 대상으로 기록하지 않음)
    for candidate in (pending, known_good, previous, base, older_previous):
        # Earlier binaries ignore WLR_MANUAL_RESTART and can open a browser
        # inside the candidate's process tree. Never launch those as manual
        # fallback: their safe containment cannot be established. Normal
        # shortcut startup retains its existing complete fallback policy.
        if (prepared is not None and candidate is not None and
                core.parse_version(candidate['version']) < (3, 18, 27)):
            logger.warning('수동 재시작을 지원하지 않는 이전 버전(%s)은 자동 복귀에서 제외합니다.', candidate['version'])
            continue
        if candidate is not None and candidate not in choices:
            choices.append(candidate)
    if not choices:
        raise core.UpdateError('이 PC에서 실행할 수 있는 프로그램을 찾지 못했습니다. 관리자에게 받은 설치 ZIP을 다시 풀어 실행해 주세요.')
    for candidate in choices:
        try:
            process, health_path = _spawn(candidate, config, profile, logger)
        except OwnedProcessStopError:
            raise
        except (core.UpdateError, OSError, ValueError) as exc:
            logger.error('버전 %s 시작 실패: %s', candidate['version'], exc)
            # A lost shared folder or a concurrently opened legacy app is an
            # environment failure, not evidence that the signed release is bad.
            profiles.validate_profile_storage(role, config)
            _ensure_no_running_app(config, role)
            if candidate['sequence'] > 0 and candidate is pending:
                sequence = candidate['sequence']
                failed_state = copy.deepcopy(state)
                # 시간 초과는 느린 PC 일 수 있어 연속 TIMEOUT_BLOCK_COUNT 회일 때만 차단하고,
                # 프로세스가 곧바로 죽는 등 명백한 실패는 즉시 차단한다.
                count = (failed_state['timeout_failures'].get(str(sequence), 0) + 1
                         if isinstance(exc, StartupTimeoutError) else TIMEOUT_BLOCK_COUNT)
                if count >= TIMEOUT_BLOCK_COUNT:
                    if sequence not in failed_state['failed_sequences']:
                        failed_state['failed_sequences'].append(sequence)
                    failed_state['pending'] = None
                    failed_state['timeout_failures'].pop(str(sequence), None)
                else:
                    failed_state['timeout_failures'][str(sequence)] = count
                    retain_pending = True
                    logger.warning('버전 %s 의 시작 확인이 시간 초과되었습니다(%d회). 이번에는 차단하지 않고 '
                                   '이전 버전으로 실행하며, 다음 실행에서 한 번 더 시도합니다. '
                                   '느린 PC 라면 설정 파일 [UPDATE] startup_timeout_seconds 를 늘려 주세요.',
                                   candidate['version'], count)
                _try_save_state(profile, failed_state, logger)
                state = failed_state
            failed_now.append(candidate)
            if candidate is pending:
                check = ('start_failed', f"새 버전({candidate['version']})이 시작되지 않았습니다. " + FALLBACK_PREVIOUS,
                         candidate['version'])
            if _same(candidate, known_good):
                known_good = previous if not _same(candidate, previous) else None
            continue
        try:
            activated = copy.deepcopy(state)
            if not _same(candidate, active):
                # 되돌림 대상: 지금 실행한 것과 다른, 가장 최근에 검증된 버전
                activated['previous'] = next((c for c in (known_good, previous)
                                              if c is not None and not _same(c, candidate)
                                              and c not in failed_now), None)
            activated['active'] = candidate
            if candidate is pending or not retain_pending:
                activated['pending'] = None
            activated['timeout_failures'].pop(str(candidate['sequence']), None)
            activated['highest_version'] = max((activated['highest_version'], candidate['version']), key=core.parse_version)
            _save_state(profile, activated)
            state = activated
        except BaseException as exc:
            if (isinstance(exc, (core.UpdateError, OSError, ValueError)) and
                    _same(candidate, active)):
                # active was verified from the persisted state before staging.
                # Keep its child supervised even when optional bookkeeping fails.
                logger.warning('기존 정상 버전의 업데이트 기록을 저장하지 못했지만 실행과 잠금을 유지합니다: %s', exc)
                check = ('save_failed', '이 PC에 업데이트 기록을 저장하지 못했습니다. 기존 정상 버전으로 실행합니다. '
                         + CAPTURE_HINT, check[2])
            else:
                _stop_owned(process, logger)
                raise
        logger.info('버전 %s 시작 확인 완료 (%s). 사용 중에는 업데이트로 재시작하지 않습니다.',
                    candidate['version'], role)
        if pending is not None and candidate is pending:
            check = ('updated', f"새 버전({candidate['version']})으로 업데이트했습니다.", candidate['version'])
        write_update_status(profile, check[0], check[1], current=candidate['version'], available=check[2], logger=logger)
        try:
            _cleanup_versions(profile, [state['active'], state['previous'], state['pending'],
                                        known_good, previous, base, pending, candidate,
                                        {'path': str(executable)} if prepared is not None else None], logger)
        except Exception as exc:  # 정리 실패가 실행을 막으면 안 된다
            logger.warning('버전 폴더 정리 중 문제가 생겨 건너뜁니다: %s', exc)
        if on_activated is not None:
            on_activated(candidate, core.load_json(health_path))
        try:
            return _wait_for_exit(process, logger)
        finally:
            try:
                health_path.unlink(missing_ok=True)
            except OSError:
                pass
    write_update_status(profile, 'start_failed', '새 버전과 이전 버전을 모두 시작하지 못했습니다. ' + CAPTURE_HINT,
                        available=check[2], logger=logger)
    raise core.UpdateError('새 버전과 이전 버전을 모두 시작하지 못했습니다. 당직 데이터는 지우거나 되돌리지 않았습니다. '
                           f'{CAPTURE_HINT}\n(관리자 전달용 기록 파일: {profile / "launcher.log"})')


def run(role: str, executable: Path, *, version: str) -> int:
    """Run one role until its owned app exits; the UI prepares the profile first.

    version 은 설치된 exe 의 버전(entrypoint.APP_VERSION)이다. 캐시된 버전과 비교해
    어느 쪽을 먼저 실행할지 정하므로 기본값을 두지 않는다(엉뚱한 기본값이 있으면
    설치본이 캐시보다 새 것/오래된 것으로 잘못 판단된다).
    """
    if role not in EXECUTABLES:
        raise core.UpdateError('프로그램 구분을 확인할 수 없습니다.')
    executable = Path(executable).absolute()
    if executable.name != EXECUTABLES[role]:
        raise core.UpdateError(f'{core.ROLE_LABELS[role]} 실행파일 이름은 {EXECUTABLES[role]}이어야 합니다.')
    core.parse_version(version)
    profile = profiles.profile_dir(role)
    profile.mkdir(parents=True, mode=0o700, exist_ok=True)
    logger = logging.getLogger(f'onair.launcher.{role}.{id(profile)}')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    # launcher.log 는 핸들러가 열기 전에(시작 시점 한 번) 회전한다.
    rotated = _rotate_log(profile / 'launcher.log')
    handler = logging.FileHandler(profile / 'launcher.log', encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    logger.addHandler(handler)
    if rotated:
        logger.info(rotated)
    try:
        with core.profile_lock(profile):
            return _run_locked(role, executable, version, profile, logger)
    except (core.UpdateError, OSError, ValueError) as exc:
        logger.error('실행 중단: %s', exc)
        # The GUI entrypoint can display this same precise error to the user.
        raise core.UpdateError(str(exc)) from exc
    finally:
        logger.removeHandler(handler)
        handler.close()


def _require_free_port(config, role, port):
    import socket
    host = _expected_host(config, role)
    family = socket.AF_INET6 if ':' in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        if IS_WINDOWS:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            raise core.UpdateError('기존 접속 포트를 다른 프로그램이 사용하고 있어 재시작하지 않았습니다.') from exc


def run_prepared_restart(role, executable, version, prepared, expected_state, expected_port,
                         *, expected_pin, on_activated=None):
    """Take over only after old handles exit; never reselect the repository offer."""
    profile = profiles.profile_dir(role)
    logger = logging.getLogger('onair.manual.' + role)
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(profile / 'launcher.log', encoding='utf-8')
    logger.addHandler(handler)
    logger.manual_context = {'port': expected_port}
    try:
        with core.profile_lock(profile):
            state = _load_state(profile, role, logger)
            pin = core.validate_bootstrap(core.load_json(profile / 'onair_update.json'), role)
            if state != expected_state or pin != expected_pin:
                raise core.UpdateError('준비 중 업데이트 설정이나 기록이 변경되어 재시작하지 않았습니다.')
            if (profile / TRUST_RECOVERY_REQUEST).exists():
                raise core.UpdateError('신뢰 복구가 진행 중입니다.')
            payload = core.verify_envelope(prepared['envelope'], pin['public_key'], role)
            candidate = prepared['candidate']
            core.verify_candidate(candidate, profile, role)
            if (candidate['version'] != payload['version'] or candidate['sequence'] != payload['sequence'] or
                    prepared['package_sha256'] != payload['sha256'] or
                    candidate['sequence'] in state['failed_sequences']):
                raise core.UpdateError('확정한 새 버전의 검증 정보가 다릅니다.')
            _require_free_port(profile / 'config', role, expected_port)
            proposed = copy.deepcopy(state)
            proposed.update(pending=candidate, highest_sequence=candidate['sequence'], highest_version=candidate['version'])
            _save_state(profile, proposed)
            return _run_locked(role, Path(executable), version, profile, logger,
                               prepared=candidate, on_activated=on_activated)
    finally:
        del logger.manual_context
        logger.removeHandler(handler)
        handler.close()
