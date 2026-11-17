"""Real shared SQLite + separate installed profiles, with synthetic staff only."""
from pathlib import Path
import configparser
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import httpx

ROOT = Path(__file__).resolve().parents[2]
NAMES = ['김도윤', '이서연', '이준호', '김하늘', '박민수', '박지은',
         '최현우', '최수빈', '정우진', '정유나', '한지훈', '한나래']


def fixture():
    return dict(members=[dict(id=i+1, name=n, gender='M' if i % 2 == 0 else 'F') for i, n in enumerate(NAMES)],
                schedules={}, comments={}, noDuty={}, absences=[],
                absenceTypes=['연차/휴가', '교육', '파견'], customHolidays={},
                globalMemo='회식 날짜 취합 검증용 예시 자료')


class Runtime:
    def __init__(self, base, label, mode, shared, source=ROOT):
        self.profile = Path(base) / label
        self.profile.mkdir(mode=0o700, exist_ok=True)
        config = self.profile / 'config'
        config.mkdir(mode=0o700, exist_ok=True)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}'
        cfg = configparser.ConfigParser()
        cfg['NETWORK'] = dict(shared_folder_path=str(shared), server_port=str(port), bind_host='127.0.0.1',
                              admin_password='', open_browser='false', tray_icon='false')
        with (config / f'config_{mode.lower()}.ini').open('w') as stream:
            cfg.write(stream)
        ticket_path = self.profile / 'launch.json'
        activate = "conn=m.get_db_connection();m.activate_holiday_contract(conn.cursor());conn.commit();conn.close();" if mode == 'ADMIN' else ''
        code = ("import backend.main as m,pathlib,json,uvicorn; " + activate +
                "pathlib.Path(" + repr(str(ticket_path)) + ").write_text(json.dumps([m.REQUEST_ACCESS.issue_ticket(" + repr(self.url) +
                ") for _ in range(20)]));uvicorn.run(m.app,host='127.0.0.1',port=" + str(port) + ",log_level='warning',access_log=False)")
        self.log = (self.profile / 'server.log').open('w')
        server_python = os.environ.get('ONAIR_SERVER_PYTHON', '/workspace/audit-31712/backend/venv/bin/python')
        self.process = subprocess.Popen([server_python, '-B', '-c', code], cwd=source,
            env=dict(os.environ, WLR_CONFIG_DIR=str(config), SMART_DUTY_MODE=mode, PYTHONDONTWRITEBYTECODE='1'),
            stdout=self.log, stderr=self.log)
        for _ in range(150):
            if self.process.poll() is not None:
                raise RuntimeError((self.profile / 'server.log').read_text())
            try:
                if self.get('/api/duty/all-data').status_code == 200:
                    self.tickets = json.loads(ticket_path.read_text())
                    return
            except httpx.RequestError:
                pass
            time.sleep(.1)
        raise RuntimeError('Synthetic server startup timeout')

    def get(self, path, **kwargs):
        return httpx.get(self.url + path, timeout=15, **kwargs)

    def send(self, path, body=None, method='POST', headers=None):
        return httpx.request(method, self.url + path, json=body,
            headers={'X-OnAir-Data-Contract': '1', **(headers or {})}, timeout=15)

    def seed(self):
        response = self.send('/api/duty/save-all', dict(baseVersion=self.get('/api/duty/all-data').json()['dataVersion'], **fixture()))
        response.raise_for_status()

    def open(self, browser, name='', width=1440, height=1000, dark=False, reduced=False):
        context = browser.new_context(viewport=dict(width=width, height=height), locale='ko-KR',
                                      timezone_id='Asia/Seoul', reduced_motion='reduce' if reduced else 'no-preference')
        page = context.new_page()
        page.goto(self.url + '/#onair_access=' + self.tickets.pop())
        page.wait_for_function('dataLoaded && memberList.length === 12')
        page.evaluate('document.fonts.ready')
        if dark:
            page.evaluate("document.documentElement.setAttribute('data-theme','dark')")
        if name:
            page.evaluate('(name)=>setMyName(name,{remember:true})', name)
        return context, page

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(5)
        self.log.close()
