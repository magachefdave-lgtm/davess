"""Launch all three frozen Windows applications on a temporary database and profiles."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes as w
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[2]
LABELS = {'ADMIN': 'Admin', 'VIEWER': 'Viewer', 'MASTER': 'MasterAdmin'}
STATUS = '/api/master/session'
UNLOCK = '/api/master/unlock'
MANAGE = '/api/dinner/manage'
EXCHANGE = '/api/local/request-access/session'


class Windows:
    def __init__(self):
        self.api = ctypes.WinDLL('user32', use_last_error=True)
        for name, result, args in (
            ('FindWindowExW', w.HWND, [w.HWND, w.HWND, w.LPCWSTR, w.LPCWSTR]),
            ('GetWindowThreadProcessId', w.DWORD, [w.HWND, ctypes.POINTER(w.DWORD)]),
            ('PostMessageW', w.BOOL, [w.HWND, w.UINT, w.WPARAM, w.LPARAM]),
            ('OpenInputDesktop', w.HANDLE, [w.DWORD, w.BOOL, w.DWORD]),
            ('GetUserObjectInformationW', w.BOOL,
             [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD)]),
            ('CloseDesktop', w.BOOL, [w.HANDLE]),
        ):
            fn = getattr(self.api, name)
            fn.restype, fn.argtypes = result, args

    def window(self, pid):
        handle = None
        while handle := self.api.FindWindowExW(None, handle, 'WLROnAirTrayWindow', None):
            owner = w.DWORD()
            self.api.GetWindowThreadProcessId(handle, ctypes.byref(owner))
            if owner.value == pid:
                return handle
        return None

    def interactive(self):
        desktop = self.api.OpenInputDesktop(0, False, 1)
        if not desktop:
            return False
        try:
            name, size = ctypes.create_unicode_buffer(256), w.DWORD()
            return (self.api.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name), ctypes.byref(size))
                    and name.value.casefold() == 'default')
        finally:
            self.api.CloseDesktop(desktop)


class LocalAPI:
    def __init__(self, port):
        self.origin = f'http://localhost:{port}'
        self.address = f'http://127.0.0.1:{port}'
        self.headers = {'Host': f'localhost:{port}', 'Origin': self.origin, 'Content-Type': 'application/json',
                        'X-OnAir-Data-Contract': '1'}
        self.opener = build_opener(ProxyHandler({}))

    def request(self, path, data=None, expected=200):
        request = Request(self.address + path, headers=self.headers,
                          data=None if data is None else json.dumps(data).encode())
        try:
            response = self.opener.open(request, timeout=2)
        except HTTPError as error:
            response = error
        with response:
            assert response.status == expected, (path, response.status, expected)
            return json.load(response), response.headers


@contextmanager
def launch(role, dist, base, win, deadline):
    from build import _kill_process_tree
    label = LABELS[role]
    profile = base / role
    config = profile / 'config'
    config.mkdir(parents=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    api = LocalAPI(port)
    capture, health = profile / 'launch-url', profile / 'health.json'
    nonce = secrets.token_hex(16)
    config.joinpath(f'config_{role.lower()}.ini').write_text(
        f'[NETWORK]\nshared_folder_path={base / "data"}\nserver_port={port}\nbind_host=127.0.0.1\n'
        f'open_browser={str(role == "MASTER").lower()}\ntray_icon={str(role != "MASTER").lower()}\n',
        encoding='utf-8')
    # Python webbrowser passes the real launch URL to this local browser stand-in.
    # Forward slashes and quotes survive shlex then CreateProcess on Windows.
    browser = f'"{Path(sys.executable).as_posix()}" -I -B "{Path(__file__).resolve().as_posix()}" --capture "%s"'
    env = {k: v for k, v in os.environ.items() if not k.startswith(('WLR_', 'SMART_DUTY_', '_PYI_'))}
    env.update(WLR_REQUIRED_PORT=str(port), WLR_CONFIG_DIR=str(config), WLR_HEALTH_FILE=str(health),
               WLR_UPDATE_INSTANCE_ID=nonce, PYINSTALLER_RESET_ENVIRONMENT='1',
               BROWSER=browser, ONAIR_SMOKE_CAPTURE=str(capture))
    exe = dist / label / f'WLR_OnAir_{label}.exe'
    process = subprocess.Popen([str(exe), '--onair-app'], env=env, cwd=config, stdin=subprocess.DEVNULL)
    pid = None
    try:
        while time.monotonic() < deadline and process.poll() is None:
            if health.exists():
                state = json.loads(health.read_text(encoding='utf-8'))
                assert (state['instance_id'], state['mode'], state['port']) == (nonce, role, port)
                pid = state['pid']
                try:
                    info, _ = api.request('/api/system/info')
                except (URLError, TimeoutError):
                    continue
                mode = 'ADMIN' if role == 'MASTER' else role
                assert (info['distributionRole'], info['mode'], info['effectiveMode']) == (role, role, mode)
                assert info['dbOk'] and Path(info['dbPath']) == base / 'data/smart_duty.db'
                assert info['version'] == state['version'] == (ROOT / 'VERSION').read_text().strip()
                if role != 'MASTER' or (capture.exists() and capture.stat().st_size):
                    yield api, capture, pid
                    return
            time.sleep(.1)
        raise RuntimeError(f'{role} failed to start within the smoke deadline (exit {process.poll()})')
    finally:
        if process.poll() is None:
            hwnd = win.window(pid) if pid else None
            if hwnd:
                win.api.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE, no confirmation
                try:
                    process.wait(3)
                except subprocess.TimeoutExpired:
                    pass
            if process.poll() is None:
                _kill_process_tree(process)


def master_checks(api, capture, pid, db, win):
    from backend.master_auth import provision
    url = urlsplit(capture.read_text(encoding='utf-8'))
    capture.unlink()
    assert url.scheme + '://' + url.netloc == api.origin and not url.query
    ticket, = parse_qs(url.fragment)['onair_access']
    session, _ = api.request(EXCHANGE, {'ticket': ticket})
    api.request(EXCHANGE, {'ticket': ticket}, 403)
    api.headers['X-OnAir-Local-Session'] = session['sessionToken']
    status, _ = api.request(STATUS)
    assert status['available'] and not status['active'] and not status['grantPending'] and not status['enrolled']
    device = str(uuid.UUID(status['deviceId']))
    password = secrets.token_urlsafe(24)
    api.request(UNLOCK, {'password': password, 'enrollmentCode': 'unissued'}, 403)
    # Provision this synthetic dataset through the same local server-owner operation used by the CLI.
    code = provision(db, device)
    assert api.request(STATUS)[0]['grantPending']
    auth = {'password': password, 'enrollmentCode': code}
    if not win.interactive():
        assert api.request(UNLOCK, auth, 403)[0]['code'] == 'MASTER_WORKSTATION_LOCKED'
        print('PASS Master launch ticket, enrollment and locked desktop denial. Interactive unlock unavailable.')
        return
    active, headers = api.request(UNLOCK, auth)
    cookie = SimpleCookie(headers['Set-Cookie'])['onair_master']
    assert active['active'] and cookie['secure'] and cookie['httponly']
    # Match Chromium's secure localhost cookie handling over the loopback HTTP server.
    api.headers['Cookie'] = 'onair_master=' + cookie.value
    assert api.request(STATUS)[0]['active']
    assert 'polls' in api.request(MANAGE)[0]
    hwnd = win.window(pid)
    assert hwnd and win.api.PostMessageW(hwnd, 0x02B1, 7, 0)  # WTS_SESSION_LOCK to our Master window only
    deadline = time.monotonic() + 3
    while api.request(STATUS)[0]['active']:
        assert time.monotonic() < deadline, 'Master session remained active after synthetic lock message'
        time.sleep(.05)
    api.request(MANAGE, expected=403)
    print('PASS Master enrollment, unlock, aggregate access and revocation on a synthetic window lock message.')


def main():
    if sys.platform != 'win32':
        raise SystemExit('Run with Windows and the frozen EXEs.')
    sys.path.insert(0, str(ROOT))
    dist = Path(sys.argv[1]).resolve()
    win = Windows()
    deadline = time.monotonic() + 95
    with tempfile.TemporaryDirectory(prefix='onair-frozen-roles-') as work:
        base = Path(work)
        (base / 'data').mkdir()
        db = base / 'data/smart_duty.db'
        sqlite3.connect(db).close()  # ADMIN initializes the fresh schema before either other role opens it.
        for role in LABELS:
            with launch(role, dist, base, win, deadline) as (api, capture, pid):
                assert api.request(STATUS)[0]['available'] == (role == 'MASTER')
                api.request(MANAGE, expected=403)
                if role == 'MASTER':
                    master_checks(api, capture, pid, db, win)
                print(f'PASS frozen {role} health, distribution/effective roles and default aggregate denial.')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--capture':
        Path(os.environ['ONAIR_SMOKE_CAPTURE']).write_text(sys.argv[2], encoding='utf-8')
    else:
        main()
