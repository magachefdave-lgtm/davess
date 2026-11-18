"""Server-approved Master devices and short-lived local browser sessions."""
import argparse
from contextlib import closing, contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import threading
import time
from urllib.parse import quote
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

from .request_access_local import CredentialLogFilter
from .request_access_schema import AccessError, read_dataset_identity

COOKIE = 'onair_master'
SESSION_SECONDS = 15 * 60
ENROLLMENT_SECONDS = 24 * 60 * 60
LOCKOUT_SECONDS = 5 * 60
FAILURE_LIMIT = 5
SIDECAR = 'smart_duty.master.sqlite3'


class PrivateAccessLogFilter(CredentialLogFilter):
    def filter(self, record):
        super().filter(record)
        # The UI posts secrets in JSON. Also discard stray credential query strings
        # and credential fields if an HTTP client or diagnostic logs them.
        record.msg = re.sub(r"(/api/master/[a-z-]+)\?[^\s\"']+", r'\1?[REDACTED]',
                            record.msg, flags=re.I)
        record.msg = re.sub(r"""((?:password|enrollmentCode|onair_master)["']?\s*[:=]\s*)"""
                            r"""(?:"[^"]*"|'[^']*'|[^\s,;\"'&}]+)""", r'\1[REDACTED]',
                            record.msg, flags=re.I)
        return True


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _password_digest(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600_000)


def _denied():
    return AccessError('MASTER_DENIED', '최고 관리자 PC 승인 상태와 전용 암호 또는 등록 코드를 확인해 주세요.', 403)


def _owner_database(database):
    path = Path(database).resolve(strict=True)
    if path.name != 'smart_duty.db' or not path.is_file():
        raise ValueError('운영 중인 smart_duty.db 파일을 선택해 주세요.')
    if os.name == 'nt':
        import ctypes
        if (str(path).startswith(('\\\\', '//')) or
                ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) != 3):
            raise ValueError('자료가 있는 서버 PC의 로컬 디스크 경로를 사용하세요.')
        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise ValueError('자료가 있는 서버 PC에서 Windows 관리자 권한으로 실행하세요.')
    else:
        # Development/maintenance on Linux also rejects mounted network shares.
        mounts = []
        for line in Path('/proc/self/mounts').read_text().splitlines():
            fields = line.split()
            if len(fields) > 2:
                mount = Path(fields[1].replace(r'\040', ' '))
                if path.is_relative_to(mount):
                    mounts.append((len(mount.parts), fields[2]))
        if mounts and max(mounts)[1] in {'cifs', 'smb3', 'smbfs', 'nfs', 'nfs4', '9p', 'fuse.sshfs'}:
            raise ValueError('자료가 있는 서버 PC의 로컬 디스크 경로를 사용하세요.')
    return path


def _connect(path, mode):
    path = Path(path).absolute()
    name = path.as_posix()
    # SQLite requires an empty URI authority even for a Windows UNC path.
    uri = 'file://' + quote(name) if name.startswith('//') else path.as_uri()
    conn = sqlite3.connect(uri + '?mode=' + mode, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def provision(database, device, *, revoke=False):
    """Offline owner operation. HTTP routes never create grants."""
    path = _owner_database(database)
    device = str(uuid.UUID(device))
    with closing(_connect(path, 'ro')) as data:
        identity = read_dataset_identity(data.cursor())
    grant_path = path.with_name(SIDECAR)
    if revoke and not grant_path.exists():
        return None
    with closing(_connect(grant_path, 'rw' if revoke else 'rwc')) as con, con:
        con.execute('BEGIN IMMEDIATE')
        con.execute('''CREATE TABLE IF NOT EXISTS master_devices (
                       dataset TEXT NOT NULL, generation TEXT NOT NULL, device TEXT NOT NULL,
                       grant_id TEXT NOT NULL, code_hash TEXT, code_expires INTEGER NOT NULL,
                       salt BLOB, verifier BLOB, failures INTEGER NOT NULL DEFAULT 0,
                       locked_until INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(dataset, device))''')
        con.execute('DELETE FROM master_devices WHERE dataset=? AND device=?',
                    (identity.dataset_id, device))
        if revoke:
            return None
        code = secrets.token_urlsafe(24)
        con.execute('''INSERT INTO master_devices
                       (dataset, generation, device, grant_id, code_hash, code_expires)
                       VALUES (?,?,?,?,?,?)''',
                    (identity.dataset_id, identity.generation, device, uuid.uuid4().hex,
                     _digest(code), int(time.time()) + ENROLLMENT_SECONDS))
        return code


def provision_cli(argv):
    class OwnerArguments(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError('승인 명령에 --database "서버 PC의 smart_duty.db 경로" '
                             '--device "대상 PC의 ID"를 입력하세요. 해제할 때는 --revoke를 추가하세요.')

    parser = OwnerArguments(prog='WLR OnAir Master --master-provision')
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--device', required=True, type=uuid.UUID)
    parser.add_argument('--revoke', action='store_true')
    args = parser.parse_args(argv)
    if os.name != 'nt' and (not sys.stdout or not sys.stdout.isatty()):
        raise ValueError('등록 코드는 서버 PC의 대화형 터미널에서 확인해 주세요.')
    code = provision(args.database, str(args.device), revoke=args.revoke)
    message = (f'승인할 PC: {args.device}\n\n일회용 등록 코드: {code}\n\n'
               '24시간 안에 해당 PC의 사용자에게 별도로 전달해 주세요.' if code else
               f'{args.device} PC의 마스터 승인을 해제했습니다.')
    if os.name == 'nt':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, 'WLR OnAir 최고 관리자 PC 승인', 0x40)
    else:
        print(message)
    return 0


@dataclass(frozen=True)
class MasterSession:
    cookie_hash: str
    local_session_id: str
    dataset: str
    generation: str
    device: str
    grant_id: str
    expires_at: int
    deadline: float


def _grant(con, identity, device):
    return con.execute('''SELECT * FROM master_devices
                          WHERE dataset=? AND generation=? AND device=?''',
                       (identity.dataset_id, identity.generation, device)).fetchone()


class MasterAuthority:
    def __init__(self, host):
        self.h = host
        self._lock = threading.RLock()
        self._session = None
        self._monitor_ready = os.name != 'nt'
        self._desktop_locked = False

    def monitor_ready(self, ready):
        with self._lock:
            self._monitor_ready = ready
            self._session = None

    def workstation_changed(self, locked):
        with self._lock:
            self._desktop_locked = locked
            self._session = None

    def clear(self):
        with self._lock:
            self._session = None

    def _desktop_available(self):
        if not self._monitor_ready or self._desktop_locked:
            return False
        if os.name != 'nt':
            return True
        # Covers startup on an already locked PC, before the first WTS event.
        import ctypes
        from ctypes import wintypes
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        user.OpenInputDesktop.restype = wintypes.HANDLE
        user.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                   wintypes.LPVOID, wintypes.DWORD,
                                                   ctypes.POINTER(wintypes.DWORD)]
        user.CloseDesktop.argtypes = [wintypes.HANDLE]
        desktop = user.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
        if not desktop:
            return False
        try:
            name = ctypes.create_unicode_buffer(256)
            used = wintypes.DWORD()
            return bool(user.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name),
                                                        ctypes.byref(used)) and
                        name.value.casefold() == 'default')
        finally:
            user.CloseDesktop(desktop)

    def available(self, request):
        return self.h.DISTRIBUTION_ROLE == 'MASTER' and self.h.is_loopback_client(request)

    def _local(self, request):
        if not self.available(request):
            raise _denied()
        return self.h.REQUEST_ACCESS.authorize(request)

    def _identity(self, cursor=None):
        if cursor is not None:
            return read_dataset_identity(cursor)
        with closing(self.h.get_db_connection(readonly=True)) as conn:
            return read_dataset_identity(conn.cursor())

    @contextmanager
    def _grants(self, write=False):
        path = Path(self.h.DB_PATH).with_name(SIDECAR)
        if not path.is_file():
            yield None
            return
        try:
            with closing(_connect(path, 'rw' if write else 'ro')) as conn, conn:
                if write:
                    conn.execute('BEGIN IMMEDIATE')
                yield conn
        except sqlite3.Error:
            raise AccessError('MASTER_STORE_UNAVAILABLE', '서버 PC 승인 자료를 열 수 없습니다.', 503, True) from None

    def _active(self, request, local, cursor=None):
        session = self._session
        if session is None:
            return False
        if (time.monotonic() >= session.deadline or time.time() >= session.expires_at
                or not self._desktop_available()):
            self.clear()
            return False
        cookie = request.cookies.get(COOKIE, '')
        if (not cookie or not hmac.compare_digest(session.cookie_hash, _digest(cookie))
                or local.session_id != session.local_session_id):
            return False
        identity = self._identity(cursor)
        with self._grants() as con:
            grant = _grant(con, identity, session.device) if con else None
        if (identity.dataset_id != session.dataset or identity.generation != session.generation
                or not grant or grant['grant_id'] != session.grant_id or not grant['verifier']):
            self.clear()
            return False
        return True

    def authorized(self, request, cursor=None):
        if not self.available(request) or not request.cookies.get(COOKIE):
            return False
        try:
            local = self._local(request)
            with self._lock:
                return self._active(request, local, cursor)
        except (AccessError, sqlite3.Error):
            return False

    def status(self, request):
        status = dict(available=self.available(request), active=False, enrolled=False,
                      grantPending=False, deviceId=None, expiresAt=None)
        if not status['available'] or not request.headers.get('x-onair-local-session'):
            return status
        local = self._local(request)
        device = self.h.REQUEST_ACCESS.vault().read()['device_id']
        with self._lock:
            with self._grants() as con:
                grant = _grant(con, self._identity(), device) if con else None
            active = self._active(request, local)
            status.update(deviceId=device, active=active, enrolled=bool(grant and grant['verifier']),
                          grantPending=bool(grant and grant['code_hash'] and
                                            grant['code_expires'] > time.time()),
                          expiresAt=self._session.expires_at if active else None)
        return status

    def unlock(self, request, body):
        local = self._local(request)
        device = self.h.REQUEST_ACCESS.vault().read()['device_id']
        password = body.get('password')
        code = body.get('enrollmentCode', '')
        if not isinstance(password, str) or not 12 <= len(password) <= 128:
            raise AccessError('INVALID_INPUT', '최고 관리자 전용 암호를 12~128자로 입력해 주세요.', 400)
        if not isinstance(code, str) or len(code) > 128:
            raise _denied()
        identity = self._identity()
        with self._lock:
            if not self._desktop_available():
                raise AccessError('MASTER_WORKSTATION_LOCKED',
                                  'Windows 잠금을 해제하고 마스터 프로그램을 다시 열어 주세요.', 403)
            now = int(time.time())
            with self._grants(write=True) as con:
                row = _grant(con, identity, device) if con else None
                if not row:
                    raise _denied()
                if row['locked_until'] > now:
                    raise AccessError('MASTER_THROTTLED', '잠시 후 다시 시도해 주세요.', 429)
                if row['verifier']:
                    valid = (not code and
                             hmac.compare_digest(row['verifier'], _password_digest(password, row['salt'])))
                else:
                    valid = bool(code and row['code_expires'] > now and row['code_hash'] and
                                 hmac.compare_digest(row['code_hash'], _digest(code)))
                    if valid:
                        if self.h.ADMIN_PASSWORD and hmac.compare_digest(password.encode(),
                                                                         self.h.ADMIN_PASSWORD.encode()):
                            raise AccessError('INVALID_INPUT', '일반 관리자 암호와 다른 전용 암호를 입력해 주세요.', 400)
                        salt = secrets.token_bytes(16)
                        con.execute('''UPDATE master_devices SET salt=?,verifier=?,code_hash=NULL
                                       WHERE grant_id=?''',
                                    (salt, _password_digest(password, salt), row['grant_id']))
                if valid:
                    con.execute('UPDATE master_devices SET failures=0,locked_until=0 WHERE grant_id=?',
                                (row['grant_id'],))
                else:
                    fails = row['failures'] + 1
                    block = fails >= FAILURE_LIMIT
                    con.execute('UPDATE master_devices SET failures=?,locked_until=? WHERE grant_id=?',
                                (0 if block else fails, now + LOCKOUT_SECONDS if block else 0, row['grant_id']))
            # Leave the transaction before reporting a failed attempt so its count persists.
            if not valid:
                raise _denied()
            token = secrets.token_urlsafe(32)
            expiry = min(now + SESSION_SECONDS, local.expires_at)
            self._session = MasterSession(_digest(token), local.session_id, identity.dataset_id,
                                          identity.generation, device, row['grant_id'], expiry,
                                          time.monotonic() + expiry - now)
            return token, dict(available=True, active=True, enrolled=True, grantPending=False,
                               deviceId=device, expiresAt=expiry)


def install(app, host):
    authority = MasterAuthority(host)

    def response(data):
        return JSONResponse(data, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})

    @app.get('/api/master/session')
    def session(request: Request):
        return response(authority.status(request))

    @app.post('/api/master/unlock')
    def unlock(request: Request, body: dict):
        token, data = authority.unlock(request, body)
        result = response(data)
        result.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, path='/api',
                          secure=True, httponly=True, samesite='strict')
        return result

    @app.post('/api/master/lock')
    def lock(request: Request):
        authority._local(request)
        authority.clear()
        result = response(authority.status(request))
        result.delete_cookie(COOKIE, path='/api', secure=True, httponly=True, samesite='strict')
        return result

    return authority


if __name__ == '__main__':
    raise SystemExit(provision_cli(sys.argv[1:]))
