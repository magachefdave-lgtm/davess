"""Real-process transport fixture around production manual updater (no UI/DB API)."""
import configparser
import json
import os
from pathlib import Path
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

SOURCE = Path(__file__).resolve().parents[3]


def app(version, fail=False):
    sys.path.insert(0, str(SOURCE))
    from updater import core, manual_update
    role = os.environ['SMART_DUTY_MODE']
    if '--onair-manual-update' in sys.argv:
        if os.environ.get('TEST_HOLD_OLD'):
            original = manual_update.process_identity.wait_old_chain
            manual_update.process_identity.wait_old_chain = lambda chain, timeout_seconds=30: original(chain, .5)
        return manual_update.run_helper(role, sys.argv[-1])
    if fail: return 23
    config = Path(os.environ['WLR_CONFIG_DIR'])
    ini = configparser.ConfigParser(); ini.read(config / f'config_{role.lower()}.ini')
    port = ini.getint('NETWORK', 'server_port')
    payload = dict(pid=os.getpid(), instance_id=os.environ['WLR_UPDATE_INSTANCE_ID'],
                   version=version, mode=role, port=port, host='127.0.0.1')
    # POSIX test boundary: the fixture has no onefile bootloader. Production
    # Windows captures both Python and bootloader identities by image ancestry.
    os.environ['WLR_SUPERVISOR_PID'] = str(os.getppid())
    manager = manual_update.live_check
    manager.configure(profile=config.parent, role=role, version=version,
                      instance_id=payload['instance_id'], port=port, host='127.0.0.1', executable=sys.argv[0])
    stopping = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def send(self, status, value):
            self.send_response(status); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(value).encode())
        def do_GET(self):
            if self.path.startswith('/status'):
                self.send(200, manager.status()); return
            self.send(200, payload)
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or b'{}')
            if self.path == '/check':
                self.send(200, dict(operation=manager.start(config.parent, role, version)))
            elif self.path == '/prepare':
                try: self.send(200, dict(operation=manager.prepare(data['offer_id'], data['instance_id'])))
                except core.UpdateError as exc: self.send(409, dict(detail=str(exc)))
            elif self.path == '/api/system/manual-update-ready':
                if manager.authorize_shutdown(data['operation'], self.headers.get('X-OnAir-Update-Token', '')):
                    self.send(200, {'ok': True})
                    if not os.environ.get('TEST_HOLD_OLD'): stopping.set()
                else: self.send(403, {})
            elif self.path == '/stop':
                self.send(200, {}); stopping.set()
            else: self.send(404, {})
        def log_message(self, *args): pass
    server = HTTPServer(('127.0.0.1', port), Handler); server.timeout = .05
    core.atomic_json(Path(os.environ['WLR_HEALTH_FILE']), payload)
    signal.signal(signal.SIGTERM, lambda *args: stopping.set())
    try:
        while not stopping.is_set(): server.handle_request()
    finally: server.server_close()
    return 0


if __name__ == '__main__':
    # Load the immutable old supervisor, which knows nothing of the helper.
    sys.path.insert(0, sys.argv[1])
    from updater import launcher
    raise SystemExit(launcher.run(sys.argv[2], Path(sys.argv[3]), version='3.18.27'))
