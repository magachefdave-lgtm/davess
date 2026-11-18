"""Signed, role-separated WLR OnAir release publication.

Private keys remain on the publishing user's PC.  Importing this module never
creates keys, opens a repository, or publishes a release.
"""
from __future__ import annotations

import base64
import binascii
import contextlib
import ctypes
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import core
from .core import (
    EXECUTABLES, LAUNCHER_VERSION, ROLE_FOLDERS, ROLE_LABELS, UpdateError, atomic_json,
    canonical_json, load_json, parse_version, public_key_fingerprint, sha256_file, verify_envelope,
)

PRODUCT = 'WLR_OnAir'
MAX_PACKAGE = 268435456
MAX_JSON = 65536
STANDARD_ROLES = ('ADMIN', 'VIEWER')
PUBLIC_PIN = 'public_key.json'
_KEY_ENTROPY = b'WLR_OnAir publisher private key v1'
KEY_FILE_NAME = 'signing_key.json'
KEY_BACKUP_MARK = 'key_backup.json'          # 백업을 만들었다는 표식(시각·지문만, 키 자료 없음)
LAST_PUBLISHED = 'last_published.json'       # 역할별 마지막으로 게시한 sequence (게시 PC 로컬)
LAST_PATHS = 'last_paths.json'               # 마지막으로 쓴 경로(다음에 채워 주기)
BACKUP_FORMAT = 'wlr-onair-key-backup'
BACKUP_SCRYPT = {'n': 2 ** 15, 'r': 8, 'p': 1}
MIN_PASSPHRASE = 12
_HASH_RE = re.compile(r'^[0-9a-f]{64}$')
# 게시 화면의 확인란 이름. 오류 문장이 화면의 실제 이름을 그대로 가리키도록 여기 한 곳에서 정한다.
FORCE_RESET_LABEL = '(경고가 떴을 때만) 저장소를 복원한 것이 맞고 그래도 게시합니다.'
NEW_REPOSITORY_LABEL = '이 폴더에 새 업데이트 저장소를 만드는 것이 맞습니다.'
KEY_REPLACE_LABEL = '이 PC에 이미 있는 키를 보관하고 교체합니다(보통은 체크하지 않음).'
NO_KEY_YET = ('이 PC에는 아직 서명 키가 없습니다. 서명 키는 처음 게시할 때 이 PC에 자동으로 만들어지며, '
              '게시가 끝나면 바로 키 백업 화면이 나옵니다. 첫 게시를 먼저 진행하세요. '
              '(다른 PC에서 게시해 왔다면 그 PC에서 백업 파일을 만든 뒤 아래 "서명 키 복구"로 가져오세요.)')


def _label(role):
    return ROLE_LABELS.get(role, role)


def _fail(message):
    raise UpdateError(message)


def _safe_path(path: Path, *, absolute=False) -> Path:
    path = Path(path)
    if absolute and not path.is_absolute():
        _fail('저장소와 개인 키 경로는 절대 경로여야 합니다.')
    path = Path(os.path.abspath(path))
    for entry in (path, *path.parents):
        if entry.is_symlink():
            _fail(f'심볼릭 링크 경로는 사용할 수 없습니다: {entry}')
        # Windows junctions are distinct from symbolic links on recent Python.
        if hasattr(entry, 'is_junction') and entry.is_junction():
            _fail(f'연결된 디렉터리 경로는 사용할 수 없습니다: {entry}')
    return path


def _is_within(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _storage_paths(repository, key_path, installer_output=None):
    repository = _safe_path(repository, absolute=True)
    key_path = _safe_path(key_path, absolute=True)
    if _is_within(key_path, repository):
        _fail('개인 서명 키는 업데이트 저장소 밖의 게시 PC 사용자 폴더에 보관해야 합니다.')
    if installer_output is not None:
        installer_output = _safe_path(installer_output, absolute=True)
        if _is_within(key_path, installer_output):
            _fail('개인 서명 키가 있는 폴더에는 설치 ZIP을 내보낼 수 없습니다.')
    return repository, key_path, installer_output


@contextlib.contextmanager
def _publisher_lock(repository):
    repository.mkdir(parents=True, exist_ok=True)
    path = _safe_path(repository / '.publisher.lock')
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags, 0o600)
    acquired = False
    try:
        if os.name == 'nt':
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b'\0')
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise UpdateError('다른 게시 작업이 실행 중입니다. 완료 후 다시 시도하세요.') from exc
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise UpdateError('다른 게시 작업이 실행 중입니다. 완료 후 다시 시도하세요.') from exc
        acquired = True
        yield
    finally:
        if acquired:
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class _DataBlob(ctypes.Structure):
    _fields_ = [('cbData', ctypes.c_ulong), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(raw, *, decrypt=False):
    """Current-user DPAPI; never CRYPTPROTECT_LOCAL_MACHINE."""
    if os.name != 'nt':
        _fail('이 서명 키는 키를 만든 Windows PC의 같은 사용자 계정에서만 열 수 있습니다. 다른 PC라면 키 백업에서 복구하세요.')
    from ctypes import wintypes
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    protect = crypt32.CryptProtectData
    unprotect = crypt32.CryptUnprotectData
    protect.argtypes = [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    unprotect.argtypes = [ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.POINTER(_DataBlob),
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    protect.restype = unprotect.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    incoming = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    entropy_buffer = (ctypes.c_ubyte * len(_KEY_ENTROPY)).from_buffer_copy(_KEY_ENTROPY)
    data = _DataBlob(len(raw), incoming)
    entropy = _DataBlob(len(_KEY_ENTROPY), entropy_buffer)
    outgoing = _DataBlob()
    if decrypt:
        ok = unprotect(ctypes.byref(data), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(outgoing))
    else:
        ok = protect(ctypes.byref(data), 'WLR OnAir publisher key', ctypes.byref(entropy),
                     None, None, 1, ctypes.byref(outgoing))
    if not ok:
        _fail('Windows 사용자 키 보호를 처리하지 못했습니다. 원래 게시 PC와 사용자 계정을 확인하세요.')
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(outgoing.pbData, ctypes.c_void_p))


def _public_key(private_key):
    raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode('ascii')


def _decode64(value, description, expected_length=None):
    if not isinstance(value, str):
        _fail(f'{description} 형식이 올바르지 않습니다.')
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise UpdateError(f'{description} 형식이 올바르지 않습니다.') from exc
    if expected_length is not None and len(raw) != expected_length:
        _fail(f'{description} 길이가 올바르지 않습니다.')
    return raw


def _read_private_key(key_path):
    key_path = _safe_path(key_path)
    if not key_path.is_file():
        _fail('개인 서명 키를 찾을 수 없습니다. 원래 게시 PC의 키를 사용하세요.')
    if os.name != 'nt' and stat.S_IMODE(key_path.stat().st_mode) & 0o077:
        _fail('개인 서명 키 권한이 너무 넓습니다. 사용자 전용 0600 권한으로 설정하세요.')
    document = load_json(key_path, max_bytes=MAX_JSON)
    if (set(document) != {'schema', 'product', 'protection', 'private_key', 'public_key'}
            or type(document.get('schema')) is not int or document['schema'] != 1
            or document.get('product') != PRODUCT):
        _fail('개인 서명 키 파일 형식이 올바르지 않습니다.')
    protected = _decode64(document['private_key'], '개인 키')
    protection = document['protection']
    if protection == 'windows-dpapi-user':
        raw = _dpapi(protected, decrypt=True)
    elif protection == 'posix-user-only' and os.name != 'nt':
        raw = protected
    else:
        _fail('이 운영체제에서 사용할 수 없는 개인 키 보호 방식입니다.')
    if len(raw) != 32:
        _fail('개인 서명 키 길이가 올바르지 않습니다.')
    key = Ed25519PrivateKey.from_private_bytes(raw)
    if _public_key(key) != document['public_key']:
        _fail('개인 서명 키와 공개 키가 일치하지 않습니다.')
    return key


def _key_document(key):
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                            serialization.NoEncryption())
    protected = _dpapi(raw) if os.name == 'nt' else raw
    return {'schema': 1, 'product': PRODUCT,
            'protection': 'windows-dpapi-user' if os.name == 'nt' else 'posix-user-only',
            'private_key': base64.b64encode(protected).decode('ascii'),
            'public_key': _public_key(key)}


def _write_key_file(key_path, key):
    """기존 DPAPI(현재 사용자) 보호 경로로 저장한다. 파일이 이미 있으면 FileExistsError."""
    key_path.parent.mkdir(parents=True, exist_ok=True)
    document = _key_document(key)
    # O_EXCL protects against two repositories attempting to create the same key.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(key_path, flags, 0o600)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(canonical_json(document) + b'\n')
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        key_path.unlink(missing_ok=True)
        raise


def _write_new_key(key_path):
    key = Ed25519PrivateKey.generate()
    try:
        _write_key_file(key_path, key)
    except FileExistsError:
        return _read_private_key(key_path)
    return key


def _initialize_locked(repository, key_path):
    pin_path = _safe_path(repository / PUBLIC_PIN)
    existing_pin = None
    if pin_path.exists():
        existing_pin = load_json(pin_path, max_bytes=MAX_JSON)
        if (set(existing_pin) != {'schema', 'product', 'public_key'}
                or type(existing_pin.get('schema')) is not int or existing_pin['schema'] != 1
                or existing_pin.get('product') != PRODUCT):
            _fail('저장소 공개 키 파일이 올바르지 않습니다.')
        _decode64(existing_pin['public_key'], '저장소 공개 키', 32)
        if not key_path.exists():
            _fail('기존 저장소의 개인 키가 없습니다. 새 키로 기존 공개 키를 바꿀 수 없습니다.')
    else:
        for name in ('channels', 'packages'):
            folder = _safe_path(repository / name)
            if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
                _fail('게시 데이터가 있는 저장소에 공개 키가 없습니다. 원래 공개 키를 복구하세요.')
    key = _read_private_key(key_path) if key_path.exists() else _write_new_key(key_path)
    public_key = _public_key(key)
    if existing_pin is not None and existing_pin['public_key'] != public_key:
        _fail('게시 PC의 개인 키와 저장소 공개 키가 일치하지 않습니다. 공개 키는 변경되지 않았습니다.')
    for name in ('packages', 'channels'):
        _safe_path(repository / name).mkdir(parents=True, exist_ok=True)
    if existing_pin is None:
        atomic_json(pin_path, {'schema': 1, 'product': PRODUCT, 'public_key': public_key})
    return key, public_key


def initialize_repository(repository: Path, key_path: Path) -> str:
    """Explicitly initialize or reopen a repository using the publishing user's key."""
    try:
        repository, key_path, _ = _storage_paths(repository, key_path)
        with _publisher_lock(repository):
            _, public_key = _initialize_locked(repository, key_path)
            return public_key
    except UpdateError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise UpdateError(f'업데이트 저장소를 초기화하지 못했습니다: {exc}') from exc


# ------------------------------------------------------------------ 게시 PC 로컬 파일 위치

def default_publisher_dir() -> Path:
    """게시 PC 사용자 폴더 (개인 키·백업 표식·마지막 게시 기록이 있는 곳)."""
    base = os.environ.get('LOCALAPPDATA') or str(Path.home() / '.local' / 'share')
    return Path(base) / 'WLR_OnAir' / 'Publisher'


def _local_key_path(key_path=None) -> Path:
    return _safe_path(key_path if key_path is not None else default_publisher_dir() / KEY_FILE_NAME,
                      absolute=True)


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def _read_pin(repository: Path):
    """저장소 공개 키 핀을 읽기 전용으로 검증해 돌려준다(없으면 None)."""
    pin_path = _safe_path(repository / PUBLIC_PIN)
    if not pin_path.exists():
        return None
    pin = load_json(pin_path, max_bytes=MAX_JSON)
    if (set(pin) != {'schema', 'product', 'public_key'}
            or type(pin.get('schema')) is not int or pin['schema'] != 1 or pin.get('product') != PRODUCT):
        _fail('저장소 공개 키 파일이 올바르지 않습니다.')
    _decode64(pin['public_key'], '저장소 공개 키', 32)
    return pin['public_key']


# ------------------------------------------------------------------ E-1 개인키 백업·복구

def _check_passphrase(passphrase):
    if not isinstance(passphrase, str) or len(passphrase) < MIN_PASSPHRASE:
        _fail(f'백업 암호는 {MIN_PASSPHRASE}자 이상이어야 합니다. 쉽게 추측되지 않는 문장을 정하고 '
              '키 백업 파일과 따로 안전한 곳에 적어 두세요. 암호를 잃어버리면 백업을 열 수 없습니다.')


def _derive(passphrase: str, salt: bytes) -> bytes:
    import unicodedata
    secret = unicodedata.normalize('NFC', passphrase).encode('utf-8')
    return Scrypt(salt=salt, length=32, **BACKUP_SCRYPT).derive(secret)


def _backup_aad(public_key: str) -> bytes:
    return canonical_json({'schema': 1, 'product': PRODUCT, 'format': BACKUP_FORMAT, 'kdf': 'scrypt',
                           'kdf_params': BACKUP_SCRYPT, 'public_key': public_key})


def _backup_document(key, passphrase: str) -> dict:
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                            serialization.NoEncryption())
    public_key = _public_key(key)
    salt, nonce = os.urandom(16), os.urandom(12)
    cipher = ChaCha20Poly1305(_derive(passphrase, salt)).encrypt(nonce, raw, _backup_aad(public_key))
    return {'schema': 1, 'product': PRODUCT, 'format': BACKUP_FORMAT, 'kdf': 'scrypt',
            'kdf_params': dict(BACKUP_SCRYPT),
            'salt': base64.b64encode(salt).decode('ascii'),
            'nonce': base64.b64encode(nonce).decode('ascii'),
            'ciphertext': base64.b64encode(cipher).decode('ascii'),
            'public_key': public_key, 'fingerprint': public_key_fingerprint(public_key),
            'created': _now_iso()}


_BACKUP_KEYS = {'schema', 'product', 'format', 'kdf', 'kdf_params', 'salt', 'nonce', 'ciphertext',
                'public_key', 'fingerprint', 'created'}


def _open_backup(src_path, passphrase):
    """백업 파일을 풀어 (개인키, 문서)를 돌려준다. 개인키·암호는 메시지에 넣지 않는다."""
    src = _safe_path(src_path, absolute=True)
    if not src.is_file():
        _fail('키 백업 파일을 찾을 수 없습니다. 파일 경로와 USB/네트워크 연결을 확인하세요.')
    damaged = '키 백업 파일이 손상되었거나 WLR OnAir 키 백업 파일이 아닙니다. 다른 사본이 있는지 확인하세요.'
    try:
        document = load_json(src, max_bytes=MAX_JSON)
    except UpdateError as exc:
        raise UpdateError(damaged) from exc
    if (set(document) != _BACKUP_KEYS or document.get('schema') != 1 or type(document.get('schema')) is not int
            or document.get('product') != PRODUCT or document.get('format') != BACKUP_FORMAT
            or document.get('kdf') != 'scrypt' or document.get('kdf_params') != BACKUP_SCRYPT):
        _fail(damaged)
    try:
        salt = _decode64(document['salt'], '백업 salt', 16)
        nonce = _decode64(document['nonce'], '백업 nonce', 12)
        cipher = _decode64(document['ciphertext'], '백업 본문', 48)
        _decode64(document['public_key'], '백업 공개 키', 32)
    except UpdateError as exc:
        raise UpdateError(damaged) from exc
    if document['fingerprint'] != public_key_fingerprint(document['public_key']):
        _fail(damaged)
    _check_passphrase(passphrase)
    try:
        raw = ChaCha20Poly1305(_derive(passphrase, salt)).decrypt(nonce, cipher, _backup_aad(document['public_key']))
    except InvalidTag:
        _fail('백업 암호가 틀렸거나 백업 파일이 변조되었습니다. 암호를 다시 확인해 주세요.')
    key = Ed25519PrivateKey.from_private_bytes(raw)
    if _public_key(key) != document['public_key']:
        _fail(damaged)
    return key, document


def _write_backup_mark(key_path: Path, fingerprint: str, created: str) -> None:
    atomic_json(key_path.parent / KEY_BACKUP_MARK,
                {'schema': 1, 'fingerprint': fingerprint, 'created': created})


def export_key_backup(dest_path, passphrase, key_path=None) -> dict:
    """개인키를 암호(scrypt + ChaCha20-Poly1305)로 잠가 파일로 내보낸다. 기존 파일은 덮어쓰지 않는다."""
    try:
        key_path = _local_key_path(key_path)
        if not os.path.lexists(key_path):
            _fail(NO_KEY_YET)          # 첫 게시 전: '원래 게시 PC의 키' 안내 대신 키가 언제 생기는지 알려 준다
        _check_passphrase(passphrase)
        dest = _safe_path(dest_path, absolute=True)
        if _is_within(dest, key_path.parent):
            _fail('백업 파일을 개인 키가 있는 폴더 안에 저장할 수 없습니다. PC가 고장나면 함께 사라집니다. '
                  'USB 등 다른 곳에 저장하세요.')
        if os.path.lexists(dest):
            _fail('같은 이름의 파일이 이미 있습니다. 다른 파일 이름을 입력해 주세요(기존 파일은 덮어쓰지 않습니다).')
        key = _read_private_key(key_path)
        document = _backup_document(key, passphrase)
        dest.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(dest, flags, 0o600)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(canonical_json(document) + b'\n')
                handle.flush()
                os.fsync(handle.fileno())
            # 쓴 파일을 바로 다시 열어 복구 가능한지 확인한 뒤에만 '백업함' 표식을 남긴다.
            reopened, _ = _open_backup(dest, passphrase)
            if _public_key(reopened) != document['public_key']:
                _fail('백업 파일 확인에 실패했습니다.')
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        _write_backup_mark(key_path, document['fingerprint'], document['created'])
        return {'path': str(dest), 'fingerprint': document['fingerprint'], 'created': document['created']}
    except UpdateError:
        raise
    except OSError as exc:
        raise UpdateError(f'키 백업 파일을 저장하지 못했습니다. 저장 위치를 확인해 주세요: {exc}') from exc


def import_key_backup(src_path, passphrase, key_path=None, repository=None, *,
                      replace_existing=False) -> dict:
    """백업에서 개인키를 복구한다. 이 PC에 키가 없을 때만, 저장소가 있으면 그 공개 키와 같을 때만 성공."""
    try:
        key, document = _open_backup(src_path, passphrase)
        public_key, fingerprint = document['public_key'], document['fingerprint']
        key_path = _local_key_path(key_path)
        if repository is not None:
            pinned = _read_pin(_safe_path(repository, absolute=True))
            if pinned is not None and pinned != public_key:
                _fail('이 백업의 키(지문 %s)는 업데이트 저장소의 공개 키(지문 %s)와 다릅니다. '
                      '다른 저장소의 백업이거나 오래된 백업입니다. 올바른 백업 파일을 찾아 주세요. '
                      '저장소와 키는 변경하지 않았습니다.' % (fingerprint, public_key_fingerprint(pinned)))
        replaced = None
        if os.path.lexists(key_path):
            existing = None
            try:
                existing = load_json(key_path, max_bytes=MAX_JSON).get('public_key')
            except UpdateError:
                pass
            if not replace_existing:
                if existing == public_key:
                    _fail('이 PC에는 이미 같은 서명 키가 있습니다. 복구할 필요가 없습니다.')
                _fail('이 PC에는 이미 서명 키가 있어 덮어쓰지 않았습니다. 정말 교체해야 한다면 '
                      '"기존 키를 보관하고 교체"를 확인하고 다시 시도하세요(기존 키 파일은 이름만 바꿔 보관합니다).')
            replaced = key_path.with_name(f'{key_path.name}.replaced-{datetime.datetime.now():%Y%m%d-%H%M%S}')
            n = 1
            while os.path.lexists(replaced):
                n += 1
                replaced = key_path.with_name(f'{key_path.name}.replaced-{datetime.datetime.now():%Y%m%d-%H%M%S}-{n}')
            os.replace(key_path, replaced)
        try:
            _write_key_file(key_path, key)
        except BaseException:
            if replaced is not None and not os.path.lexists(key_path):
                os.replace(replaced, key_path)
            raise
        _write_backup_mark(key_path, fingerprint, document['created'])
        return {'fingerprint': fingerprint, 'public_key': public_key,
                'replaced_backup': str(replaced) if replaced else None}
    except UpdateError:
        raise
    except OSError as exc:
        raise UpdateError(f'키를 복구하지 못했습니다. 저장 위치와 권한을 확인해 주세요: {exc}') from exc


def key_backup_status(key_path=None) -> dict:
    """키 백업 여부 조회(읽기 전용). warning 이 있으면 게시 화면이 경고로 띄운다."""
    key_path = _local_key_path(key_path)
    status = {'key_exists': False, 'fingerprint': None, 'backed_up': False,
              'backup_created': None, 'warning': None}
    try:
        if not key_path.is_file():
            return status
        public_key = load_json(key_path, max_bytes=MAX_JSON).get('public_key')
        status['fingerprint'] = public_key_fingerprint(public_key)
        status['key_exists'] = True
    except (UpdateError, OSError, TypeError):
        status['warning'] = '이 PC의 서명 키 파일을 읽지 못했습니다. 키 파일을 확인하세요.'
        return status
    mark = None
    try:
        mark_path = key_path.parent / KEY_BACKUP_MARK
        if mark_path.is_file():
            mark = load_json(mark_path, max_bytes=MAX_JSON)
    except (UpdateError, OSError):
        mark = None
    if isinstance(mark, dict) and mark.get('fingerprint') == status['fingerprint']:
        status['backed_up'] = True
        status['backup_created'] = mark.get('created')
    else:
        status['warning'] = ('키 백업을 아직 만들지 않았습니다. 이 PC가 고장나거나 윈도우를 다시 설치하면 '
                             '키를 복구할 수 없어 모든 PC에 설치 ZIP을 다시 배포해야 합니다. '
                             '지금 "키 백업 파일 만들기"를 실행해 USB 등에 보관하세요.')
    return status


# ------------------------------------------------------------------ E-5 sequence 단조 가드 / 마지막 경로

def _read_last_published(key_path: Path):
    """(역할별 마지막 sequence, 경고문). 파일이 깨져 있으면 빈 기록과 경고를 돌려준다."""
    path = key_path.parent / LAST_PUBLISHED
    if not os.path.lexists(path):
        return {}, None
    try:
        document = load_json(path, max_bytes=MAX_JSON)
        roles = document.get('roles')
        if (set(document) != {'schema', 'roles'} or document['schema'] != 1 or not isinstance(roles, dict)
                or any(r not in EXECUTABLES or type(v) is not int or v < 1 for r, v in roles.items())):
            raise UpdateError('형식 오류')
        return dict(roles), None
    except (UpdateError, OSError):
        return {}, ('이 PC의 마지막 게시 기록 파일을 읽지 못해 저장소 복원 확인을 건너뛰었습니다. '
                    '게시는 계속할 수 있습니다.')


def _write_last_published(key_path: Path, sequences: dict) -> None:
    current, _ = _read_last_published(key_path)
    for role, value in sequences.items():
        current[role] = max(current.get(role, 0), value)
    atomic_json(key_path.parent / LAST_PUBLISHED, {'schema': 1, 'roles': current})


def _sequence_problem(repo_sequences: dict, local: dict):
    stale = {role: (repo_sequences.get(role, 0), seq) for role, seq in local.items()
             if repo_sequences.get(role, 0) < seq}
    if not stale:
        return None
    detail = ', '.join(f'{_label(role)}: 저장소 {have}차 / 이 PC의 마지막 게시 기록 {want}차'
                       for role, (have, want) in sorted(stale.items()))
    return ('저장소가 복원되었거나 지워진 것 같습니다. (%s)\n'
            '저장소 내용이 이 PC가 마지막으로 게시한 것보다 오래된 상태입니다. 최신 백업에서 다시 복원했는지 '
            '확인하세요. 의도한 복원이 맞다면 [4. 비상 정지 / 저장소 복원]을 펼쳐 '
            f'"{FORCE_RESET_LABEL}"에 체크하고 다시 게시하세요. '
            '그 경우 새 배포 차수는 기록된 차수 다음부터 이어집니다.' % detail)


def load_last_paths(key_path=None) -> dict:
    """마지막으로 쓴 경로(저장소·릴리스·설치 ZIP 폴더). 실패해도 빈 값을 돌려준다."""
    try:
        path = _local_key_path(key_path).parent / LAST_PATHS
        document = load_json(path, max_bytes=MAX_JSON) if path.is_file() else {}
        return {k: document[k] for k in ('repository', 'release', 'installers')
                if isinstance(document.get(k), str) and len(document[k]) < 4096}
    except (UpdateError, OSError):
        return {}


def save_last_paths(paths: dict, key_path=None, *, strict=False) -> None:
    try:
        value = {k: str(v) for k, v in paths.items() if k in ('repository', 'release', 'installers') and v}
        atomic_json(_local_key_path(key_path).parent / LAST_PATHS, value)
    except (UpdateError, OSError):
        if strict:
            raise


# ------------------------------------------------------------------ E-6 읽기 전용 저장소 상태

def repository_status(repository, key_path=None) -> dict:
    """저장소의 현재 상태를 읽기만 한다. 폴더·잠금·핀·키를 만들거나 쓰지 않는다."""
    repo = _safe_path(repository, absolute=True)
    key_path = _local_key_path(key_path)
    status = {'repository': str(repo), 'exists': repo.exists(), 'empty': False, 'initialized': False,
              'new_repository': False, 'public_key': None, 'fingerprint': None, 'roles': {},
              'local_key_fingerprint': None, 'warnings': []}
    if status['exists']:
        if not repo.is_dir():
            _fail('업데이트 저장소 경로가 폴더가 아닙니다.')
        try:
            status['empty'] = not any(repo.iterdir())
        except OSError as exc:
            raise UpdateError(f'업데이트 저장소 폴더를 읽지 못했습니다. 공유 폴더 연결을 확인하세요: {exc}') from exc
    public_key = _read_pin(repo) if status['exists'] else None
    if public_key is not None:
        status.update(initialized=True, public_key=public_key, fingerprint=public_key_fingerprint(public_key))
    for role in EXECUTABLES:
        info = {'version': None, 'sequence': 0, 'published': False, 'error': None,
                'paused': (repo / 'channels' / f'{role}.paused').exists()}
        channel = repo / 'channels' / f'{role}.json'
        if status['exists'] and channel.exists():
            info['published'] = True
            if public_key is None:
                info['error'] = '공개 키 없이 게시 파일만 있습니다.'
            else:
                try:
                    payload = verify_envelope(load_json(channel, max_bytes=MAX_JSON), public_key, role)
                    info.update(version=payload['version'], sequence=payload['sequence'])
                except UpdateError as exc:
                    info['error'] = str(exc)
        status['roles'][role] = info
    status['new_repository'] = (not status['initialized']
                                and not any(i['published'] for i in status['roles'].values()))
    if not status['initialized'] and any(i['published'] for i in status['roles'].values()):
        status['warnings'].append('공개 키 파일이 없는 저장소입니다. 게시할 수 없습니다. 원래 공개 키를 복구하세요.')
    if status['exists'] and not status['empty'] and status['new_repository']:
        status['warnings'].append('비어 있지 않은 폴더입니다. 업데이트 저장소가 맞는 경로인지 다시 확인하세요.')
    local = key_backup_status(key_path)
    status['local_key_fingerprint'] = local['fingerprint']
    if status['initialized']:
        if not local['key_exists']:
            status['warnings'].append('이 PC에는 이 저장소의 서명 키가 없습니다. 키 백업에서 복구하세요.')
        elif local['fingerprint'] != status['fingerprint']:
            status['warnings'].append('이 PC의 서명 키(지문 %s)가 저장소 공개 키와 다릅니다. 게시할 수 없습니다.'
                                      % local['fingerprint'])
    problem = _sequence_problem({r: i['sequence'] for r, i in status['roles'].items()},
                                _read_last_published(key_path)[0])
    if problem:
        status['warnings'].append(problem.split('\n')[0])
    for role, info in status['roles'].items():
        if info['error']:
            status["warnings"].append(f"{_label(role)} 게시 정보를 확인하지 못했습니다: {info['error']}")
    return status


# ------------------------------------------------------------------ E-9 비상 정지

def set_paused(repository, roles, paused: bool) -> dict:
    """channels/<역할>.paused 표식을 만들거나 지운다(서명 형식 밖). 설치된 버전은 지우지 않는다."""
    try:
        if (not isinstance(roles, (tuple, list)) or not roles
                or any(role not in EXECUTABLES for role in roles)):
            _fail('일시 정지할 대상(관리자용 / 조회용 / 최고 관리자용)을 골라 주세요.')
        repo = _safe_path(repository, absolute=True)
        if _read_pin(repo) is None:
            _fail('게시된 적이 없는 저장소입니다. 경로를 확인하세요.')
        with _publisher_lock(repo):
            channels = _safe_path(repo / 'channels')
            channels.mkdir(parents=True, exist_ok=True)
            for role in dict.fromkeys(roles):
                marker = _safe_path(channels / f'{role}.paused')
                if paused:
                    if not os.path.lexists(marker):
                        marker.write_text(f'paused {_now_iso()}\n', encoding='utf-8')
                else:
                    marker.unlink(missing_ok=True)
        return {role: paused for role in dict.fromkeys(roles)}
    except UpdateError:
        raise
    except OSError as exc:
        raise UpdateError(f'정지 표식을 처리하지 못했습니다. 공유 폴더 쓰기 권한을 확인하세요: {exc}') from exc


def _json_document(raw):
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail('Update ZIP 안의 버전 정보가 손상되었습니다. 빌드 완료 폴더에서 다시 복사하세요.')
            result[key] = value
        return result
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=no_duplicates,
                           parse_constant=lambda _: _fail('허용되지 않는 JSON 숫자입니다.'))
    except (ValueError, UnicodeError) as exc:
        raise UpdateError('Update ZIP 안의 버전 정보를 읽을 수 없습니다. 빌드 완료 폴더에서 다시 복사하세요.') from exc
    if not isinstance(value, dict):
        _fail('Update ZIP 안의 버전 정보가 손상되었습니다. 빌드 완료 폴더에서 다시 복사하세요.')
    return value


def _valid_zip_member(info):
    name = info.filename
    if (not name or info.orig_filename != name or '\\' in name or '\x00' in name or ':' in name or name.startswith('/')
            or any(piece in ('', '.', '..') for piece in name.split('/'))
            or info.is_dir() or (info.external_attr & 0x10) or (info.flag_bits & 1)):
        _fail('허용되지 않는 ZIP 파일 이름 또는 암호화 항목입니다.')
    mode = info.external_attr >> 16
    if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
        _fail('ZIP에는 일반 파일만 포함할 수 있습니다.')
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        _fail('지원하지 않는 ZIP 압축 방식입니다.')


@contextlib.contextmanager
def _validated_release(release_zip):
    release_zip = _safe_path(release_zip, absolute=True)
    if not release_zip.is_file() or release_zip.stat().st_size > len(EXECUTABLES) * MAX_PACKAGE + MAX_JSON + 8192:
        _fail('Update ZIP 파일이 없거나 너무 큽니다. 경로를 다시 확인하세요.')
    try:
        with zipfile.ZipFile(release_zip) as archive, tempfile.TemporaryDirectory(prefix='onair-release-') as temporary:
            entries = archive.infolist()
            members = {info.filename for info in entries}
            if len(entries) not in (3, 4) or len(entries) != len(members) or 'release.json' not in members:
                _fail('Update ZIP 안의 파일 구성이 다릅니다. 빌드 완료 폴더의 WLR_OnAir_v버전_Update.zip 을 그대로 고르세요.')
            for info in entries:
                _valid_zip_member(info)
            manifest_info = archive.getinfo('release.json')
            if manifest_info.file_size < 1 or manifest_info.file_size > MAX_JSON:
                _fail('Update ZIP 안의 버전 정보가 손상되었습니다. 빌드 완료 폴더에서 다시 복사하세요.')
            manifest = _json_document(archive.read(manifest_info))
            if (set(manifest) != {'schema', 'product', 'version', 'data_schema', 'files'}
                    or type(manifest.get('schema')) is not int or manifest['schema'] != 1
                    or manifest.get('product') != PRODUCT
                    or type(manifest.get('data_schema')) is not int or manifest['data_schema'] != 1):
                _fail('이 게시 도구가 지원하지 않는 Update ZIP 형식입니다. 같은 빌드 폴더의 배포관리.cmd 로 게시하세요.')
            parse_version(manifest['version'])
            files = manifest['files']
            if not isinstance(files, dict) or set(files) not in (set(STANDARD_ROLES), set(EXECUTABLES)):
                _fail('관리자·조회용 파일 선언이 필요합니다. 최고 관리자용은 추가로 포함할 수 있습니다.')
            expected = {'release.json', *(f'{role}/{EXECUTABLES[role]}' for role in files)}
            if members != expected:
                _fail('Update ZIP의 파일과 버전 정보에 선언된 파일이 다릅니다. 빌드 완료 폴더에서 다시 복사하세요.')
            extracted = {}
            for role, declared in files.items():
                executable = EXECUTABLES[role]
                expected_path = f'{role}/{executable}'
                if not isinstance(declared, dict) or set(declared) != {'path', 'sha256', 'size'}:
                    _fail(f'{_label(role)} 파일 선언이 올바르지 않습니다.')
                if (declared['path'] != expected_path or not isinstance(declared['sha256'], str)
                        or not _HASH_RE.fullmatch(declared['sha256'])
                        or type(declared['size']) is not int or not 1 <= declared['size'] <= MAX_PACKAGE):
                    _fail(f'{_label(role)} 실행파일 경로·해시·크기가 올바르지 않습니다.')
                info = archive.getinfo(expected_path)
                if info.file_size != declared['size']:
                    _fail(f'{_label(role)} 실행파일 크기가 선언과 다릅니다.')
                target = Path(temporary) / executable
                digest = hashlib.sha256()
                count = 0
                with archive.open(info) as source, target.open('xb') as output:
                    while chunk := source.read(1024 * 1024):
                        count += len(chunk)
                        if count > declared['size']:
                            _fail(f'{_label(role)} 실행파일이 선언 크기를 초과했습니다.')
                        digest.update(chunk)
                        output.write(chunk)
                if count != declared['size'] or digest.hexdigest() != declared['sha256']:
                    _fail(f'{_label(role)} 실행파일 무결성 검증에 실패했습니다. Update ZIP 을 다시 복사하거나 다시 빌드하세요.')
                extracted[role] = target
            yield manifest, extracted
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as exc:
        raise UpdateError('Update ZIP 이 손상되었거나 다른 종류의 ZIP 입니다. 빌드 완료 폴더에서 다시 복사하세요.') from exc


def _zip_file(archive, name, source):
    info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    with archive.open(info, 'w') as target, source.open('rb') as handle:
        shutil.copyfileobj(handle, target, 1024 * 1024)


def _zip_bytes(archive, name, raw):
    info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    archive.writestr(info, raw)


def _commit_immutable(source, destination):
    """Stage locally, then atomically expose complete bytes without replacement.

    A killed publisher may leave an unreferenced staging file, but never a
    partial final package.  A later retry uses a new staging file.  Windows
    rename refuses an existing target; POSIX hard-link creation has the same
    no-replace property.  Unsupported filesystem operations fail closed.
    """
    destination = _safe_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if (destination.is_file() and destination.stat().st_size == source.stat().st_size
                and sha256_file(destination) == sha256_file(source)):
            return
        _fail(f'같은 이름의 기존 파일과 내용이 다릅니다: {destination.name}')
    fd, temporary_name = tempfile.mkstemp(
        prefix=f'.{destination.name}.', suffix='.tmp', dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, 'wb') as output, source.open('rb') as handle:
            shutil.copyfileobj(handle, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if (temporary.stat().st_size != source.stat().st_size
                or sha256_file(temporary) != sha256_file(source)):
            _fail('저장소 복사 후 파일 무결성이 일치하지 않습니다.')
        if os.name != 'nt':
            temporary.chmod(0o644)
        try:
            if os.name == 'nt':
                os.rename(temporary, destination)
            else:
                os.link(temporary, destination)
        except FileExistsError:
            # A non-cooperating publisher cannot overwrite an immutable file.
            if (destination.is_file() and not destination.is_symlink()
                    and destination.stat().st_size == source.stat().st_size
                    and sha256_file(destination) == sha256_file(source)):
                return
            _fail('다른 작업이 동일한 게시 파일을 생성했습니다. 기존 파일을 확인하세요.')
    finally:
        temporary.unlink(missing_ok=True)


def _make_installer(path, role, version, executable_path, repository, public_key):
    label = ROLE_LABELS[role]
    fingerprint = public_key_fingerprint(public_key)
    bootstrap = {'schema': 1, 'product': PRODUCT, 'role': role,
                 'repository': str(repository), 'public_key': public_key}
    guide = (
        f'WLR OnAir {version} {label} 설치 안내\n\n'
        f'1. 이 ZIP 을 PC 의 로컬 폴더(예: C:\\WLR_OnAir\\{label})에 모두 압축 해제하세요.\n'
        '   공유 폴더나 ZIP 안에서 바로 실행하지 마세요.\n'
        f'2. {EXECUTABLES[role]} 를 더블클릭합니다. 처음 한 번만 설정 화면이 브라우저로 열립니다.\n'
        '   - 이 PC에서 예전 WLR OnAir 를 쓰던 경우: "기존 공유 폴더 사용"을 고르고 예전 프로그램 폴더를 고르세요.\n'
        '   - 이 PC에서 처음 쓰는 경우: "이 PC를 새로 연결"을 고르고 관리자가 알려 준 데이터 폴더를 고르세요.\n'
        '3. 설정 화면에 나오는 지문이 아래 지문과 같은지 확인한 뒤 진행하세요.\n'
        '4. 새 버전은 프로그램을 다시 시작할 때 자동으로 적용됩니다. 실행 중에는 바꾸지 않습니다.\n'
        '   업데이트 결과는 프로그램 화면의 [연결 정보] → 업데이트 상태에서 볼 수 있습니다.\n\n'
        f'onair_update.json 은 실행파일 옆에 그대로 두세요. 신뢰 공개 키와 업데이트 폴더 위치가 들어 있습니다.\n'
        f'업데이트 폴더: {repository}\n'
        f'업데이트 서명 공개 키 지문: {fingerprint}\n'
        '(처음 실행할 때 나오는 화면의 지문이 위 값, 그리고 관리자가 알려 준 값과 같은지 눈으로 확인하세요.\n'
        ' 다르면 실행하지 말고 관리자에게 알려 주세요.)\n'
        '기존 당직 데이터 파일·설정·백업은 삭제하지 마세요. 이 설치본에는 개인 서명 키가 포함되지 않습니다.\n'
    )
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        _zip_file(archive, EXECUTABLES[role], executable_path)
        _zip_bytes(archive, 'onair_update.json', canonical_json(bootstrap) + b'\n')
        _zip_bytes(archive, 'README.txt', guide.encode('utf-8-sig'))


ALREADY_PUBLISHED_NOTICE = ('이미 게시된 것과 같은 파일입니다 — 새로 만든 Update.zip 을 선택했는지 확인하세요. '
                            '저장소는 바뀌지 않았습니다.')


def _same_version_message(role: str, version: str, payload: dict, published: dict) -> str:
    """같은 버전 번호에 다른 내용을 게시하려 할 때의 안내(원인과 해결 방법을 비개발자용으로)."""
    major, minor, patch = parse_version(version)
    label = ROLE_LABELS.get(role, role)
    if payload.get('sha256') == published.get('sha256'):
        cause = (f'{label}: 같은 버전 번호({version})의 같은 프로그램이 다른 설정(최소 업데이트 실행기 버전 등)으로 '
                 '이미 게시되어 있습니다. 설정을 바꾸려면')
    else:
        cause = (f'{label}: 같은 버전 번호({version})로 이미 다른 파일이 게시되어 있습니다. '
                 '소스를 다시 빌드했다면')
    return (f'{cause} VERSION 의 번호를 올려(예: {major}.{minor}.{patch + 1}, 소스 폴더의 버전올리기.cmd 사용) '
            '다시 빌드한 뒤, 새로 만든 Update ZIP 을 게시해야 합니다. '
            '(이미 설치한 PC 들이 같은 번호의 서로 다른 프로그램을 받지 않도록 막는 안전 장치입니다.)')


# 게시 진행 단계(화면 표시용, 대략의 소요 시간 포함). 공유 폴더가 느리면 마지막 단계가 가장 오래 걸린다.
PROGRESS_STEPS = (
    '1/5 Update ZIP 검사 (약 10~30초)',
    '2/5 업데이트 폴더 확인 (몇 초)',
    '3/5 업데이트 파일 만들고 서명 (약 30초~1분)',
    '4/5 직원 PC용 설치 ZIP 만들기 (약 30초~1분)',
    '5/5 공유 업데이트 폴더로 복사 (네트워크에 따라 1~5분)',
)


def publish_release(repository: Path, release_zip: Path, key_path: Path,
                    installer_output: Path | None = None, *, roles=STANDARD_ROLES,
                    min_launcher: str = '1.0.0', force_sequence_reset: bool = False,
                    progress=None) -> dict:
    """Validate the entire release, then explicitly publish selected role channels.

    Each role is independently signed and installed.  Immutable packages are
    copied and verified before any channel pointer is atomically replaced.

    min_launcher: 이 업데이트를 받을 수 있는 최소 업데이트 실행기 버전. 기본값 '1.0.0' 을 올리면
    그보다 오래된 설치본은 이 업데이트를 받지 못하므로(새 설치 ZIP 필요) 꼭 필요할 때만 올린다.
    force_sequence_reset: 저장소가 이 PC의 마지막 게시 기록보다 오래된 상태(복원/삭제)여도 게시를 승인.
    progress: 단계 이름(str)을 받는 함수. 게시 화면이 '지금 무엇을 하는 중인지' 보여 주는 데 쓴다.
    """
    def step(text):
        if progress is not None:
            try:
                progress(text)
            except Exception:  # 화면 표시 실패가 게시를 막으면 안 된다
                pass

    try:
        parse_version(min_launcher)
        if parse_version(min_launcher) > parse_version(LAUNCHER_VERSION):
            _fail(f'요구 업데이트 실행기 버전({min_launcher})이 이 게시 도구의 업데이트 실행기 버전({LAUNCHER_VERSION})보다 높습니다.')
        if (not isinstance(roles, (tuple, list)) or not roles or len(set(roles)) != len(roles)
                or any(role not in EXECUTABLES for role in roles)):
            _fail('게시 대상(관리자용 / 조회용 / 최고 관리자용)을 다시 골라 주세요.')
        repository, key_path, installer_output = _storage_paths(repository, key_path, installer_output)
        # A malformed release must not create repository, pin, or private key.
        step(PROGRESS_STEPS[0])
        with _validated_release(release_zip) as (manifest, extracted):
            if 'MASTER' in roles and 'MASTER' not in extracted:
                _fail('이 Update ZIP에는 최고 관리자용 실행파일이 없습니다. 최고 관리자용이 포함된 버전을 선택하세요.')
            step(PROGRESS_STEPS[1])
            with _publisher_lock(repository):
                key_created = not os.path.lexists(key_path)
                key, public_key = _initialize_locked(repository, key_path)
                previous = {}
                for role in EXECUTABLES:
                    channel = _safe_path(repository / 'channels' / f'{role}.json')
                    if channel.exists():
                        previous[role] = verify_envelope(load_json(channel, max_bytes=MAX_JSON), public_key, role)
                warnings = []
                local_sequences, local_warning = _read_last_published(key_path)
                if local_warning:
                    warnings.append(local_warning)
                problem = _sequence_problem({r: previous[r]['sequence'] for r in previous}, local_sequences)
                if problem and not force_sequence_reset:
                    _fail(problem)
                version = manifest['version']
                for role in roles:
                    if role in previous and parse_version(version) < parse_version(previous[role]['version']):
                        _fail(f'{_label(role)}: 새 버전({version})은 지금 게시된 버전({previous[role]["version"]})보다 '
                              '높아야 합니다. 버전올리기.cmd 로 번호를 올린 뒤 다시 빌드하세요.')
                sequence = max((entry['sequence'] for entry in previous.values()), default=0) + 1
                unchanged = {role for role in roles
                             if role in previous and version == previous[role]['version']}
                # Resume an interrupted two-role publication at its original
                # sequence when it remains newer for every incomplete role.
                if unchanged:
                    retry_sequence = max(previous[role]['sequence'] for role in unchanged)
                    if all(retry_sequence > previous.get(role, {}).get('sequence', 0)
                           for role in roles if role not in unchanged):
                        sequence = retry_sequence
                # 저장소가 복원되어 낮아졌더라도 새 순서 번호는 이 PC의 기록보다 항상 커야 한다.
                floor = max(local_sequences.values(), default=0)
                if sequence <= floor:
                    sequence = floor + 1
                payloads = {}
                envelopes = {}
                installers = {}
                step(PROGRESS_STEPS[2])
                with tempfile.TemporaryDirectory(prefix='onair-publish-') as temporary:
                    temporary = Path(temporary)
                    packages = {}
                    for role in roles:
                        package = temporary / f'{role}.zip'
                        with zipfile.ZipFile(package, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                            _zip_file(archive, EXECUTABLES[role], extracted[role])
                        digest = sha256_file(package)
                        size = package.stat().st_size
                        if not 1 <= size <= MAX_PACKAGE:
                            _fail(f'{_label(role)} 업데이트 패키지가 허용 크기를 초과했습니다.')
                        payload = {'schema': 1, 'product': PRODUCT, 'role': role, 'version': version,
                                   'sequence': sequence, 'package': f'packages/{role}/{version}-{digest[:12]}.zip',
                                   'sha256': digest, 'size': size, 'executable': EXECUTABLES[role],
                                   'min_launcher': min_launcher, 'data_schema': 1}
                        if role in unchanged:
                            payload['sequence'] = previous[role]['sequence']
                            if payload != previous[role]:
                                _fail(_same_version_message(role, version, payload, previous[role]))
                        envelope = {'payload': payload,
                                    'signature': base64.b64encode(key.sign(canonical_json(payload))).decode('ascii')}
                        # Use the exact client verifier before making a release visible.
                        verify_envelope(envelope, public_key, role)
                        packages[role] = package
                        payloads[role] = payload
                        envelopes[role] = envelope
                    if installer_output is not None:
                        step(PROGRESS_STEPS[3])
                        for role in roles:
                            label = ROLE_FOLDERS[role]
                            filename = f'WLR_OnAir_{version}_{label}.zip'
                            installer = temporary / filename
                            _make_installer(installer, role, version, extracted[role], repository, public_key)
                            folder = installer_output / label if role == 'MASTER' else installer_output
                            _commit_immutable(installer, folder / filename)
                            installers[role] = str(folder / filename)
                    step(PROGRESS_STEPS[4])
                    for role in roles:
                        _commit_immutable(packages[role], repository / payloads[role]['package'])
                    # Every channel refers only to a fully verified immutable package.
                    # A network failure can leave different current versions by role;
                    # each published channel remains independently valid. Repeating
                    # the same release completes only the unfinished channels.
                    for role in roles:
                        if role in unchanged:
                            continue
                        channel = _safe_path(repository / 'channels' / f'{role}.json')
                        atomic_json(channel, envelopes[role])
                try:
                    recorded = {r: previous[r]['sequence'] for r in previous}
                    recorded.update({r: payloads[r]['sequence'] for r in roles})
                    _write_last_published(key_path, recorded)
                except (UpdateError, OSError):
                    warnings.append('게시는 끝났지만 이 PC의 마지막 게시 기록을 저장하지 못했습니다.')
                # 선택한 역할이 모두 이미 같은 파일로 게시되어 있으면 저장소는 하나도 바뀌지 않았다.
                # (지난번 Update ZIP 을 다시 고른 경우가 대부분이라 '게시 완료'로 보이면 안 된다.)
                already_published = set(roles) == unchanged
                if already_published:
                    warnings.insert(0, f'버전 {version}: {ALREADY_PUBLISHED_NOTICE}')
                else:
                    for role in sorted(unchanged):
                        warnings.append(f'{ROLE_LABELS[role]}은(는) 버전 {version}이(가) 이미 같은 파일로 '
                                        '게시되어 있어 그대로 두었습니다.')
                return {'version': version, 'channels': payloads, 'installers': installers,
                        'fingerprint': public_key_fingerprint(public_key), 'warnings': warnings,
                        'min_launcher': min_launcher, 'unchanged': sorted(unchanged),
                        'already_published': already_published, 'key_created': key_created}
    except UpdateError:
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise UpdateError(f'릴리스를 게시하지 못했습니다: {exc}') from exc
