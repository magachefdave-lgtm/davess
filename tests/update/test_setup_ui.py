import importlib
import json
from pathlib import Path
import sys
import threading
import urllib.request
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def request(server, token=None, origin=None, body=None):
    headers = {}
    if token is not None:
        headers['X-OnAir-Token'] = token
    if origin is not None:
        headers['Origin'] = origin
    req = urllib.request.Request(server.url + '/submit',
          data=json.dumps(body or {}).encode(), headers={**headers, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_local_form_rejects_unauthorized_post_and_foreign_origin():
    ui = importlib.import_module('updater.setup_ui')
    called = []
    s = ui.LocalForm('test', [], lambda data: called.append(data) or {'ok': True})
    t = threading.Thread(target=s.server.serve_forever, daemon=True)
    t.start()
    try:
        assert request(s)[0] == 403
        assert request(s, s.token, 'https://example.com')[0] == 403
        assert called == []
        assert request(s, s.token, s.url, {'a': 1}) == (200, {'ok': True, 'result': {'ok': True}})
        assert called == [{'a': 1}]
    finally:
        s.server.shutdown()
        s.server.server_close()


def test_local_form_error_does_not_report_success():
    ui = importlib.import_module('updater.setup_ui')
    def failed(_):
        raise ValueError('설정 경로를 확인하세요')
    s = ui.LocalForm('test', [], failed)
    t = threading.Thread(target=s.server.serve_forever, daemon=True)
    t.start()
    try:
        status, result = request(s, s.token, s.url)
        assert status == 400 and result['ok'] is False
        assert not s.done.is_set()
    finally:
        s.server.shutdown()
        s.server.server_close()
