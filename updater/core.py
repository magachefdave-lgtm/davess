"""Pinned, signed shared-folder updates. This module never activates a release.

Remote files are authenticated then copied to an immutable local version folder.
Configuration, activation state and user databases belong to other modules.
"""
from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import tempfile
import zipfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PRODUCT = 'WLR_OnAir'
LAUNCHER_VERSION = '1.1.0'
MAX_PACKAGE_BYTES = 268435456
MAX_EXECUTABLE_BYTES = 268435456
EXECUTABLES = {'ADMIN': 'WLR_OnAir_Admin.exe', 'VIEWER': 'WLR_OnAir_Viewer.exe'}
_PAYLOAD_KEYS = {'schema', 'product', 'role', 'version', 'sequence', 'package', 'sha256',
                 'size', 'executable', 'min_launcher', 'data_schema'}
_CANDIDATE_KEYS = {'role', 'version', 'sequence', 'path', 'sha256', 'data_schema'}
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_VERSION = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z')


class UpdateError(Exception):
    """A rejected update, unavailable repository, or incompatible local cache."""


def _role(role: str) -> str:
    if not isinstance(role, str) or role not in EXECUTABLES:
        raise UpdateError('유효하지 않은 프로그램 역할입니다.')
    return role


def _integer(value, minimum: int, maximum: int | None = None) -> bool:
    return type(value) is int and value >= minimum and (maximum is None or value <= maximum)


def canonical_json(value) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                          allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise UpdateError('표준 JSON으로 표현할 수 없는 값입니다.') from exc


def parse_version(text: str) -> tuple[int, int, int]:
    if not isinstance(text, str) or len(text) > 64 or not _VERSION.fullmatch(text):
        raise UpdateError('버전은 major.minor.patch 형식이어야 합니다.')
    return tuple(int(part) for part in text.split('.'))


def public_key_fingerprint(public_key: str) -> str:
    """사람이 눈으로 대조할 공개 키 지문. 예: 'A1B2-C3D4-E5F6-0718' (SHA-256 앞 8바이트)."""
    raw = _base64(public_key, 32, '공개 키')
    digest = hashlib.sha256(raw).hexdigest()[:16].upper()
    return '-'.join(digest[i:i + 4] for i in range(0, 16, 4))


def _absolute(path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _no_links(path: Path) -> None:
    """Reject all existing symlinks/junctions, including profile ancestors."""
    path = _absolute(path)
    for part in reversed([path, *path.parents]):
        try:
            mode = part.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode) or (hasattr(part, 'is_junction') and part.is_junction()):
            raise UpdateError('업데이트 경로에 심볼릭 링크 또는 정션을 사용할 수 없습니다.')


def _mkdir(path: Path) -> None:
    _no_links(path)
    path.mkdir(parents=True, exist_ok=True)
    _no_links(path)


def _open_regular(path: Path):
    _no_links(path)
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    flags |= getattr(os, 'O_NONBLOCK', 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise UpdateError('일반 파일만 읽을 수 있습니다.')
        return os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    try:
        with _open_regular(Path(path)) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
    except OSError as exc:
        raise UpdateError(f'파일을 읽지 못했습니다: {Path(path).name}') from exc
    return digest.hexdigest()


def _unique_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise UpdateError('JSON에 중복된 키가 있습니다.')
        result[key] = value
    return result


def _reject_constant(value):
    raise UpdateError('JSON에 비정상 숫자 값이 있습니다.')


def load_json(path: Path, max_bytes=65536) -> dict:
    if not _integer(max_bytes, 1):
        raise UpdateError('JSON 최대 크기가 올바르지 않습니다.')
    try:
        with _open_regular(Path(path)) as stream:
            if os.fstat(stream.fileno()).st_size > max_bytes:
                raise UpdateError('JSON 파일이 허용 크기를 초과합니다.')
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise UpdateError('JSON 파일이 허용 크기를 초과합니다.')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs,
                           parse_constant=_reject_constant)
        if type(value) is not dict:
            raise UpdateError('JSON 최상위 값은 객체여야 합니다.')
        return value
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        raise UpdateError(f'JSON 파일을 읽지 못했습니다: {Path(path).name}') from exc


def _fsync_dir(path: Path) -> None:
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_json(path: Path, value: dict) -> None:
    if type(value) is not dict:
        raise UpdateError('저장할 JSON은 객체여야 합니다.')
    path = _absolute(path)
    temporary = None
    try:
        raw = canonical_json(value)
        _mkdir(path.parent)
        _no_links(path)
        fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        _no_links(path)
        os.replace(temporary, path)
        temporary = None
        _fsync_dir(path.parent)
    except OSError as exc:
        raise UpdateError(f'JSON을 안전하게 저장하지 못했습니다: {path.name}') from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _base64(text, length: int, label: str) -> bytes:
    if not isinstance(text, str) or len(text) > 256:
        raise UpdateError(f'{label} 형식이 올바르지 않습니다.')
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise UpdateError(f'{label} 형식이 올바르지 않습니다.') from exc
    if len(raw) != length or base64.b64encode(raw).decode('ascii') != text:
        raise UpdateError(f'{label} 길이나 인코딩이 올바르지 않습니다.')
    return raw


def validate_bootstrap(value: dict, role: str) -> dict:
    _role(role)
    if type(value) is not dict or set(value) != {'schema', 'product', 'role', 'repository', 'public_key'}:
        raise UpdateError('초기 업데이트 설정의 필드가 올바르지 않습니다.')
    if type(value['schema']) is not int or value['schema'] != 1 or value['product'] != PRODUCT or value['role'] != role:
        raise UpdateError('초기 업데이트 설정의 제품·역할·형식이 일치하지 않습니다.')
    repository = value['repository']
    if (not isinstance(repository, str) or not repository.strip() or len(repository) > 4096
            or '\x00' in repository
            or not (Path(repository).is_absolute() or PureWindowsPath(repository).is_absolute())):
        raise UpdateError('업데이트 저장소는 절대 경로여야 합니다.')
    _base64(value['public_key'], 32, '공개 키')
    return dict(value)


def verify_envelope(envelope: dict, public_key: str, role: str) -> dict:
    _role(role)
    if type(envelope) is not dict or set(envelope) != {'payload', 'signature'}:
        raise UpdateError('서명된 업데이트 선언의 필드가 올바르지 않습니다.')
    payload = envelope['payload']
    if type(payload) is not dict or set(payload) != _PAYLOAD_KEYS:
        raise UpdateError('업데이트 선언의 필드가 올바르지 않습니다.')
    try:
        Ed25519PublicKey.from_public_bytes(_base64(public_key, 32, '공개 키')).verify(
            _base64(envelope['signature'], 64, '서명'), canonical_json(payload))
    except InvalidSignature as exc:
        raise UpdateError('업데이트 서명 확인에 실패했습니다.') from exc
    if (type(payload['schema']) is not int or payload['schema'] != 1
            or type(payload['data_schema']) is not int or payload['data_schema'] != 1
            or payload['product'] != PRODUCT or payload['role'] != role):
        raise UpdateError('업데이트 제품·역할·데이터 형식이 일치하지 않습니다.')
    parse_version(payload['version'])
    if parse_version(payload['min_launcher']) > parse_version(LAUNCHER_VERSION):
        raise UpdateError('이 업데이트는 새 설치 ZIP이 필요합니다. 관리자에게 알려 주세요. '
                          f"(필요한 업데이트 실행기 {payload['min_launcher']} 이상, 이 PC는 {LAUNCHER_VERSION})")
    if not _integer(payload['sequence'], 1) or not _integer(payload['size'], 1, MAX_PACKAGE_BYTES):
        raise UpdateError('업데이트 순서 또는 파일 크기가 올바르지 않습니다.')
    digest = payload['sha256']
    if not isinstance(digest, str) or not _HEX.fullmatch(digest):
        raise UpdateError('업데이트 파일 해시가 올바르지 않습니다.')
    expected_package = f"packages/{role}/{payload['version']}-{digest[:12]}.zip"
    if payload['package'] != expected_package or payload['executable'] != EXECUTABLES[role]:
        raise UpdateError('업데이트 경로 또는 실행파일 역할이 올바르지 않습니다.')
    return dict(payload)


def _extract_package(archive_path: Path, destination: Path, executable: str) -> str:
    with zipfile.ZipFile(archive_path, 'r') as archive:
        entries = archive.infolist()
        if len(entries) != 1:
            raise UpdateError('업데이트 ZIP에는 지정된 실행파일 하나만 있어야 합니다.')
        info = entries[0]
        mode = info.external_attr >> 16
        if (info.filename != executable or info.orig_filename != executable or info.is_dir()
                or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                or info.external_attr & 0x10 or info.flag_bits & 1
                or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                or not 1 <= info.file_size <= MAX_EXECUTABLE_BYTES):
            raise UpdateError('업데이트 ZIP의 파일·경로·압축 형식이 허용되지 않습니다.')
        digest = hashlib.sha256()
        count = 0
        with archive.open(info, 'r') as source, destination.open('xb') as output:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                count += len(block)
                if count > info.file_size or count > MAX_EXECUTABLE_BYTES:
                    raise UpdateError('압축 해제 결과가 허용 크기를 초과합니다.')
                digest.update(block)
                output.write(block)
            if count != info.file_size:
                raise UpdateError('압축 해제 결과의 크기가 일치하지 않습니다.')
            output.flush()
            os.fsync(output.fileno())
        destination.chmod(0o700)
        return digest.hexdigest()


def stage_update(repository: Path, profile: Path, role: str, public_key: str,
                 highest_sequence: int = 0, current_version: str = '0.0.0', *,
                 expected_envelope: dict | None = None, progress=None) -> dict | None:
    """Authenticate, locally stage and atomically install; never activate/write state."""
    _role(role)
    if not _integer(highest_sequence, 0):
        raise UpdateError('기존 업데이트 배포 차수가 올바르지 않습니다.')
    current = parse_version(current_version)
    repository, profile = _absolute(repository), _absolute(profile)
    stage = None
    try:
        payload = verify_envelope(expected_envelope if expected_envelope is not None else
                                  load_json(repository / 'channels' / f'{role}.json'), public_key, role)
        sequence = payload['sequence']
        if sequence < highest_sequence:
            raise UpdateError('저장소가 교체·복구된 것 같습니다. 관리자에게 알려 주세요. '
                              f'(저장소의 배포 차수 {sequence}차가 이 PC가 이미 확인한 {highest_sequence}차보다 낮습니다. '
                              '보안을 위해 이 업데이트는 받지 않고 설치된 버전으로 실행합니다.)')
        if sequence == highest_sequence:
            return None
        published = parse_version(payload['version'])
        if published == current:
            return None  # A fresh install may not have consumed this publication sequence.
        if published < current:
            raise UpdateError('현재 버전보다 오래된 업데이트는 설치하지 않습니다.')
        versions = profile / 'versions'
        _mkdir(versions)
        stage = Path(tempfile.mkdtemp(prefix='.stage-', dir=versions))
        archive_path = stage / 'download.zip'
        digest = hashlib.sha256()
        count = 0
        with _open_regular(repository / payload['package']) as source, archive_path.open('xb') as output:
            if os.fstat(source.fileno()).st_size != payload['size']:
                raise UpdateError('업데이트 ZIP 크기가 서명된 값과 일치하지 않습니다.')
            for block in iter(lambda: source.read(1024 * 1024), b''):
                count += len(block)
                if progress is not None:
                    progress(count, payload['size'])
                if count > payload['size'] or count > MAX_PACKAGE_BYTES:
                    raise UpdateError('업데이트 ZIP이 허용 크기를 초과합니다.')
                digest.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if count != payload['size'] or digest.hexdigest() != payload['sha256']:
            raise UpdateError('업데이트 ZIP이 누락되거나 해시가 일치하지 않습니다.')
        exe_hash = _extract_package(archive_path, stage / EXECUTABLES[role], EXECUTABLES[role])
        archive_path.unlink()
        final = versions / f"{payload['version']}-{sequence}-{exe_hash[:12]}"
        candidate = {'role': role, 'version': payload['version'], 'sequence': sequence,
                     'path': str(final / EXECUTABLES[role]), 'sha256': exe_hash, 'data_schema': 1}
        _no_links(final)
        if final.exists():
            verify_candidate(candidate, profile, role)
        else:
            try:
                # The nonempty destination cannot be replaced by a concurrent installer.
                stage.rename(final)
                stage = None
                _fsync_dir(versions)
            except FileExistsError:
                verify_candidate(candidate, profile, role)
        verify_candidate(candidate, profile, role)
        return candidate
    except UpdateError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError, NotImplementedError) as exc:
        raise UpdateError('업데이트를 안전하게 내려받거나 설치하지 못했습니다. 기존 버전을 유지합니다.') from exc
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)


def verify_candidate(candidate: dict, profile: Path, role: str) -> Path:
    _role(role)
    if type(candidate) is not dict or set(candidate) != _CANDIDATE_KEYS:
        raise UpdateError('설치된 버전 정보의 필드가 올바르지 않습니다.')
    if (candidate['role'] != role or type(candidate['data_schema']) is not int
            or candidate['data_schema'] != 1 or not _integer(candidate['sequence'], 0)):
        raise UpdateError('설치된 버전의 역할·형식·순서가 일치하지 않습니다.')
    parse_version(candidate['version'])
    digest = candidate['sha256']
    if not isinstance(digest, str) or not _HEX.fullmatch(digest):
        raise UpdateError('설치된 실행파일 해시가 올바르지 않습니다.')
    value = candidate['path']
    if not isinstance(value, str) or '\x00' in value:
        raise UpdateError('설치된 실행파일 경로가 올바르지 않습니다.')
    path = Path(value)
    versions = _absolute(profile) / 'versions'
    if not path.is_absolute() or '..' in path.parts or path.name != EXECUTABLES[role]:
        raise UpdateError('설치된 실행파일 경로·역할이 일치하지 않습니다.')
    try:
        relative = path.relative_to(versions)
        if len(relative.parts) < 2:
            raise ValueError('version directory required')
        _no_links(path)
        size = path.stat().st_size
        if not 1 <= size <= MAX_EXECUTABLE_BYTES or sha256_file(path) != digest:
            raise UpdateError('설치된 실행파일이 변경되었거나 손상되었습니다.')
    except (ValueError, OSError) as exc:
        raise UpdateError('설치된 실행파일이 로컬 버전 폴더 안에 존재하지 않습니다.') from exc
    return path


@contextmanager
def profile_lock(profile: Path):
    """One supervisor per role/profile. The stable lock file is never unlinked."""
    profile = _absolute(profile)
    handle = None
    locked = False
    try:
        _mkdir(profile)
        path = profile / 'launcher.lock'
        _no_links(path)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_BINARY', 0)
                     | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        handle = os.fdopen(fd, 'r+b')
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise UpdateError('실행 잠금 파일이 올바르지 않습니다.')
        if os.name == 'nt':
            import msvcrt
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b'\0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
    except OSError as exc:
        if handle is not None:
            handle.close()
        raise UpdateError('WLR OnAir가 이미 켜져 있거나 시작하는 중입니다. 화면 오른쪽 아래 트레이의 WLR OnAir 아이콘에서 [열기]를 누르세요. 아이콘이 없으면 잠시 뒤 다시 실행해 주세요.') from exc
    except BaseException:
        if handle is not None:
            handle.close()
        raise
    try:
        yield
    finally:
        if locked:
            try:
                if os.name == 'nt':
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
