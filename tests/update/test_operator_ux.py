"""3.18.0 운영자 사용성: 게시 화면(키 백업·확인 창·진행 단계·창 닫힘), 첫 설정 화면, 업데이트 상태 파일,
화면에서 바꾸는 설정. [위험] 주석은 막으려는 실제 사고다. 개인 키·운영 DB 는 쓰지 않는다(전부 임시 폴더).
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
from unittest.mock import Mock

import pytest

from updater import core, launcher, profiles, publisher, setup_ui as ui
from test_hardening_ui import PASS, confirmed, post, start, stop
from test_launcher import INSTALLED_VERSION, Process, candidate, fake_spawn, set_state, site  # noqa: F401
from test_publisher import release_zip

REAL_FORM = ui.LocalForm


@pytest.fixture
def actions(tmp_path):
    root = tmp_path / 'Publisher'
    root.mkdir()
    opened = []
    act = ui.PublisherActions(root, opener=lambda path: opened.append(Path(path)) or True)
    return act, tmp_path, opened


def form_data(tmp, **extra):
    value = {'repository': str(tmp / 'repo'), 'release': str(release_zip(tmp)),
             'installers': str(tmp / 'inst'), 'roles': 'BOTH'}
    value.update(extra)
    return value


def page_of(monkeypatch, tmp_path, executable=None, backup=None):
    monkeypatch.setattr(publisher, 'default_publisher_dir', lambda: tmp_path / 'publisher')
    if backup is not None:
        monkeypatch.setattr(publisher, 'key_backup_status', lambda key: backup)
    seen = {}

    def capture(form):
        seen['form'] = form
        form.server.server_close()
    monkeypatch.setattr(ui.LocalForm, 'run', capture)
    ui.publisher_ui(executable or tmp_path / 'x' / 'WLR_OnAir_Admin.exe')
    return seen['form'].page


# ------------------------------------------------------------------ 2. 첫 게시 직후 키 백업

# [위험] 첫 게시 때 키가 말없이 만들어지고, 백업은 창을 다시 열어야만 가능했다.
def test_first_publish_reports_key_created_and_keeps_window_for_backup(actions):
    act, tmp, _ = actions
    result = confirmed(act.publish, form_data(tmp, new_repo_ok=True))
    assert result['keep_open'] is True and result['after'] == 'backup'
    default = Path(result['backup_default'])
    assert default.is_absolute() and not publisher._is_within(default, act.root)   # 키 폴더와 다른 곳
    assert not publisher._is_within(default, tmp / 'repo')                          # 공유 폴더 아님
    done = act.backup({'backup_path': str(tmp / 'usb' / 'k.json'), 'backup_pass': PASS, 'backup_pass2': PASS,
                       'repository': str(tmp / 'repo')})
    assert '키 백업을 만들었습니다' in done['message']
    # 백업을 만든 뒤의 게시는 창을 붙잡지 않는다.
    second = confirmed(act.publish, form_data(tmp, release=str(release_zip(tmp, '3.15.1'))))
    assert 'keep_open' not in second


def test_publish_release_reports_key_creation_once(tmp_path):
    key = tmp_path / 'k' / publisher.KEY_FILE_NAME
    first = publisher.publish_release(tmp_path / 'repo', release_zip(tmp_path), key)
    second = publisher.publish_release(tmp_path / 'repo', release_zip(tmp_path, '3.15.1'), key)
    assert first['key_created'] is True and second['key_created'] is False


def test_master_publish_opens_dedicated_installer_folder(actions, monkeypatch):
    act, tmp, opened = actions
    page = page_of(monkeypatch, tmp)
    assert '<select name="roles"><option value="BOTH">' in page
    assert '<option value="MASTER">최고 관리자용만</option>' in page
    data = form_data(tmp, roles='MASTER', new_repo_ok=True)
    data['release'] = str(release_zip(tmp, include_master=True))
    result = confirmed(act.publish, data)
    assert opened == [tmp / 'inst' / 'MasterAdmin']
    assert str(opened[0]) in result['message']
    assert not (tmp / 'repo' / 'channels' / 'ADMIN.json').exists()
    assert not (tmp / 'repo' / 'channels' / 'VIEWER.json').exists()


# [위험] 첫 게시 전에 '키 백업'을 누르면 '원래 게시 PC의 키를 사용하세요'가 나와 처음 쓰는 사람이 헤맸다.
def test_backup_before_first_publish_explains_when_key_is_created(actions):
    act, tmp, _ = actions
    with pytest.raises(core.UpdateError) as caught:
        act.backup({'backup_path': str(tmp / 'b.json'), 'backup_pass': '', 'backup_pass2': ''})
    message = str(caught.value)
    assert '처음 게시할 때' in message and '원래 게시 PC' not in message


def test_keep_open_result_waits_for_finish_over_http(monkeypatch):
    form = ui.LocalForm('t', [], lambda d: {'message': 'published', 'keep_open': True, 'after': 'backup'},
                        actions={'backup': lambda d: {'message': 'ok'}})
    monkeypatch.setattr(ui.webbrowser, 'open', Mock(return_value=True))
    start(form)
    try:
        assert post(form, '/submit', {})[1]['result']['keep_open'] is True
        assert not form.done.is_set() and form.result['message'] == 'published'
        assert post(form, '/action/backup', {})[0] == 200
        assert post(form, '/finish', {})[0] == 200 and form.done.is_set()
    finally:
        stop(form)


def test_done_is_set_before_the_reply_is_written(monkeypatch):
    """3.18.0 QA: /finish·/submit 응답이 done 보다 먼저 나가 시험이 5번에 1번꼴로 실패했다.

    응답 머리를 쓰는 순간 done 이 이미 켜져 있어야 한다(응답을 받은 쪽이 바로 확인해도 맞는 값)."""
    import http.server
    seen = []
    real = http.server.BaseHTTPRequestHandler.end_headers

    def spy(handler):
        seen.append((handler.path, form.done.is_set()))
        return real(handler)
    for path in ('/finish', '/cancel', '/submit'):
        form = ui.LocalForm('t', [], lambda d: {'message': 'ok'})
        start(form)
        monkeypatch.setattr(http.server.BaseHTTPRequestHandler, 'end_headers', spy)
        try:
            assert post(form, path, {})[0] == 200 and form.done.is_set()
        finally:
            monkeypatch.setattr(http.server.BaseHTTPRequestHandler, 'end_headers', real)
            stop(form)
    assert seen == [('/finish', True), ('/cancel', True), ('/submit', True)]


def test_run_waits_for_the_closing_reply_before_shutting_down(monkeypatch):
    """done 을 응답보다 먼저 켜도, run() 은 그 응답이 다 나간 뒤에 서버를 닫는다."""
    import threading
    import time
    form = ui.LocalForm('t', [], lambda d: {'message': 'ok'})
    released = threading.Event()
    with form._inflight_lock:
        form._inflight += 1                     # 응답을 쓰는 중인 요청 하나를 흉내 낸다

    def finish_later():
        time.sleep(0.3)
        with form._inflight_lock:
            form._inflight -= 1
            form._inflight_lock.notify_all()
        released.set()
    monkeypatch.setattr(ui.webbrowser, 'open', Mock(side_effect=lambda url: form.done.set() or True))
    threading.Thread(target=finish_later, daemon=True).start()
    assert form.run(timeout=5) is None
    assert released.is_set()


def test_keep_open_result_is_returned_even_if_window_times_out(monkeypatch):
    monkeypatch.setattr(ui, 'KEEP_OPEN_EXTRA', 0.3)          # 실제로는 키 백업을 위해 10분 더 열어 둔다
    form = ui.LocalForm('t', [], lambda d: {'keep_open': True})
    monkeypatch.setattr(ui.webbrowser, 'open', Mock(side_effect=lambda url: post(form, '/submit', {}) and True))
    assert form.run(timeout=0.5) == {'keep_open': True}       # 게시는 이미 끝났으므로 결과를 잃지 않는다


def test_publisher_page_has_after_publish_backup_panel(monkeypatch, tmp_path):
    page = page_of(monkeypatch, tmp_path)
    for element in ('id="after"', 'id="after-path"', 'id="after-pass"', 'id="after-pass2"', 'id="after-backup"',
                    'id="after-later"', 'id="after-close"', 'id="after-browse"'):
        assert element in page
    assert 'WLR_OnAir_키백업' in page


# ------------------------------------------------------------------ 6. 진행 단계와 창 닫힘

def test_publish_progress_steps_are_reported_in_order(tmp_path):
    steps = []
    publisher.publish_release(tmp_path / 'repo', release_zip(tmp_path), tmp_path / 'k' / 'key.json',
                              installer_output=tmp_path / 'inst', progress=steps.append)
    assert steps == list(publisher.PROGRESS_STEPS)
    assert all(re.search(r'\(.*(초|분).*\)', step) for step in steps)       # 대략의 시간이 붙어 있다


def test_progress_callback_failure_does_not_break_publication(tmp_path):
    def broken(_):
        raise RuntimeError('screen gone')
    result = publisher.publish_release(tmp_path / 'repo', release_zip(tmp_path), tmp_path / 'k' / 'key.json',
                                       progress=broken)
    assert result['version'] == '3.15.0'


def test_progress_action_and_ping_answer_while_publishing(monkeypatch):
    import threading
    gate = threading.Event()
    state = {'step': '1/5 Update ZIP 검사'}

    def slow(data):
        gate.wait(5)
        return {'message': 'done'}
    form = ui.LocalForm('t', [], slow, actions={'progress': lambda d: {'message': state['step']}})
    form.deadline = __import__('time').monotonic() + 100
    start(form)
    try:
        worker = threading.Thread(target=post, args=(form, '/submit', {}))
        worker.start()
        assert post(form, '/action/progress', {}) == (200, {'ok': True, 'result': {'message': '1/5 Update ZIP 검사'}})
        status, body = post(form, '/ping', {})
        assert status == 200 and 90 <= body['remaining'] <= 100
        gate.set()
        worker.join(5)
        assert form.done.is_set()
    finally:
        gate.set()
        stop(form)
    assert 'PROGRESS = true' not in form.page and 'HAS_PROGRESS=true' in form.page


NODE_CLOSED = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const input=JSON.parse(process.argv[1]);
const nodes=new Map();
function node(id){if(!nodes.has(id))nodes.set(id,{id,textContent:'',className:'',hidden:true,value:''});return nodes.get(id);}
const form={hidden:false,elements:{namedItem:()=>null},querySelectorAll:()=>[],querySelector:()=>null};
const doc={getElementById:id=>id==='form'?form:node(id)};
const ctx=vm.createContext({document:doc,FormData:function(){return [];},console,window:{},
  fetch:async()=>{throw new TypeError('Failed to fetch');}});
vm.runInContext(input.script,ctx);
(async()=>{
  await form.onsubmit({preventDefault(){}});
  assert.equal(node('closed').hidden,false);
  assert.equal(node('closed').textContent,input.closed);
  assert.equal(form.hidden,true);
  assert.ok(!node('result').textContent.includes('Failed to fetch'));
})().catch(error=>{console.error(error);process.exitCode=1;});
'''


# [위험] 15분이 지나 창이 닫힌 뒤 버튼을 누르면 'Failed to fetch' 만 나와 무엇을 할지 몰랐다.
def test_page_reports_closed_window_instead_of_failed_to_fetch(monkeypatch, tmp_path):
    page = page_of(monkeypatch, tmp_path)
    script = re.search(r'<script>(.*?)</script>', page, re.S).group(1)
    closed = '창이 닫혔습니다(15분이 지났거나 프로그램이 종료됨). 배포관리.cmd 를 다시 실행하세요.'
    assert json.dumps(closed, ensure_ascii=False) in script or json.dumps(closed) in script
    run = subprocess.run([shutil.which('node') or 'node', '-e', NODE_CLOSED, json.dumps({'script': script, 'closed': closed})],
                         capture_output=True, text=True, timeout=15)
    assert run.returncode == 0, run.stderr


# ------------------------------------------------------------------ 7. 내부 용어

def test_sequence_warning_names_the_real_checkbox_and_avoids_internal_terms(monkeypatch, tmp_path):
    message = publisher._sequence_problem({'ADMIN': 1}, {'ADMIN': 3})
    assert publisher.FORCE_RESET_LABEL in message and '관리자용' in message
    for term in ('ADMIN', '순서 번호', 'last_published', 'channel', 'sequence'):
        assert term not in message
    page = page_of(monkeypatch, tmp_path)
    for label in (publisher.FORCE_RESET_LABEL, publisher.NEW_REPOSITORY_LABEL, publisher.KEY_REPLACE_LABEL):
        assert label in page


@pytest.mark.parametrize('call, needle', [
    (lambda tmp: publisher.publish_release(tmp / 'r', release_zip(tmp), tmp / 'k.json', roles=('X',)), '관리자용'),
    (lambda tmp: publisher.publish_release(tmp / 'r', release_zip(tmp), tmp / 'k.json', min_launcher='9.0.0'), '업데이트 실행기'),
    (lambda tmp: publisher.set_paused(tmp / 'r', ['X'], True), '관리자용'),
])
def test_publisher_errors_use_plain_korean(tmp_path, call, needle):
    with pytest.raises(core.UpdateError) as caught:
        call(tmp_path)
    text = str(caught.value)
    assert needle in text
    assert not re.search(r'\b(ADMIN|VIEWER|min_launcher|channel|last_published)\b', text)


def test_lower_version_message_names_role_and_bump_tool(tmp_path):
    key = tmp_path / 'k' / 'key.json'
    publisher.publish_release(tmp_path / 'repo', release_zip(tmp_path, '3.15.2'), key)
    with pytest.raises(core.UpdateError) as caught:
        publisher.publish_release(tmp_path / 'repo', release_zip(tmp_path, '3.15.1'), key)
    assert '관리자용' in str(caught.value) and '버전올리기.cmd' in str(caught.value)


def test_last_published_warning_has_no_file_name(tmp_path):
    key = tmp_path / 'key.json'
    (tmp_path / publisher.LAST_PUBLISHED).write_text('broken')
    _, warning = publisher._read_last_published(key)
    assert 'last_published' not in warning and '마지막 게시 기록' in warning


# ------------------------------------------------------------------ 8. 위험한 버튼의 확인 창

# [위험] 비상 정지 대상이 기본값 '둘 다'라서 의도와 달리 관리자용까지 멈췄다.
def test_pause_needs_explicit_target_and_confirmation_with_path(actions, monkeypatch, tmp_path):
    act, tmp, _ = actions
    confirmed(act.publish, form_data(tmp, new_repo_ok=True))
    with pytest.raises(ValueError, match='대상을 먼저 골라'):
        act.pause(form_data(tmp))                                    # 기본값(빈 선택)으로는 진행하지 않는다
    ask = act.pause(form_data(tmp, pause_roles='VIEWER'))
    text = ask['confirm']['text']
    assert '조회용' in text and '관리자용' not in text and str(tmp / 'repo') in text and '설치된 버전' in text
    assert not list((tmp / 'repo' / 'channels').glob('*.paused'))  # 확인 전에는 아무것도 바뀌지 않는다
    # 확인 토큰은 같은 대상에만, 한 번만 쓰인다.
    other = act.pause(form_data(tmp, pause_roles='BOTH', confirmed=ask['confirm']['token']))
    assert 'confirm' in other and not list((tmp / 'repo' / 'channels').glob('*.paused'))
    act.pause(form_data(tmp, pause_roles='BOTH', confirmed=other['confirm']['token']))
    assert {p.name for p in (tmp / 'repo' / 'channels').glob('*.paused')} == {'ADMIN.paused', 'VIEWER.paused'}
    assert 'confirm' in act.pause(form_data(tmp, pause_roles='BOTH', confirmed=other['confirm']['token']))
    resume = act.resume(form_data(tmp, pause_roles='ADMIN'))
    assert '[일시 정지 해제]' in resume['confirm']['text']
    page = page_of(monkeypatch, tmp_path)
    assert re.search(r'<select name="pause_roles"><option value="" selected>', page)


def test_new_repository_needs_checkbox_then_confirmation(actions):
    act, tmp, _ = actions
    with pytest.raises(ui.NeedsInput) as caught:
        act.publish(form_data(tmp))
    assert caught.value.show == {'new_repo_ok': True} and publisher.NEW_REPOSITORY_LABEL in str(caught.value)
    ask = act.publish(form_data(tmp, new_repo_ok=True))
    assert str(tmp / 'repo') in ask['confirm']['text'] and '새 서명 키' in ask['confirm']['text']
    assert not (tmp / 'repo').exists()


def test_key_replacement_asks_for_confirmation(actions):
    act, tmp, _ = actions
    confirmed(act.publish, form_data(tmp, new_repo_ok=True))
    act.backup({'backup_path': str(tmp / 'usb' / 'b.json'), 'backup_pass': PASS, 'backup_pass2': PASS})
    request = {'restore_path': str(tmp / 'usb' / 'b.json'), 'restore_pass': PASS,
               'repository': str(tmp / 'repo'), 'restore_replace': True}
    ask = act.restore(request)
    assert '[서명 키 교체]' in ask['confirm']['text'] and str(act.key) in ask['confirm']['text']
    assert not list(act.key.parent.glob('*.replaced-*'))
    done = confirmed(act.restore, request)
    assert '복구했습니다' in done['message'] and list(act.key.parent.glob('*.replaced-*'))


def test_new_repository_checkbox_only_visible_for_new_folder(actions, monkeypatch, tmp_path):
    act, tmp, _ = actions
    assert act.check_repository({'repository': str(tmp / 'new')})['show'] == {'new_repo_ok': True}
    confirmed(act.publish, form_data(tmp, new_repo_ok=True))
    assert act.check_repository({'repository': str(tmp / 'repo')})['show'] == {'new_repo_ok': False}
    page = page_of(monkeypatch, tmp_path)
    assert re.search(r'<label class="check" id="wrap-new_repo_ok" hidden>', page)
    assert 'data-check="check_repository"' in page


def test_confirmation_over_http_does_not_finish_form(monkeypatch):
    form = ui.LocalForm('t', [], lambda d: {'confirm': {'token': 't', 'text': 'sure?'}})
    start(form)
    try:
        assert post(form, '/submit', {})[1]['result']['confirm']['text'] == 'sure?'
        assert not form.done.is_set() and form.result is None
    finally:
        stop(form)


def test_needs_input_error_carries_fields_to_show():
    def fail(_):
        raise ui.NeedsInput('check the box', show={'new_repo_ok': True})
    form = ui.LocalForm('t', [], fail)
    start(form)
    try:
        assert post(form, '/submit', {}) == (400, {'ok': False, 'error': 'check the box', 'show': {'new_repo_ok': True}})
    finally:
        stop(form)


# ------------------------------------------------------------------ 11·12. 설치 ZIP 위치·지문

# [위험] 설치 ZIP 이 숨김 폴더(%LOCALAPPDATA%)에 저장되어 운영자가 찾지 못했다.
def test_installers_default_next_to_build_and_folder_opened_after_publish(actions, monkeypatch, tmp_path):
    act, tmp, opened = actions
    result = confirmed(act.publish, form_data(tmp, new_repo_ok=True))
    assert opened == [tmp / 'inst'] and '탐색기로 열었습니다' in result['message']
    build = tmp_path / 'build'
    exe = build / 'Admin' / 'WLR_OnAir_Admin.exe'
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'MZ')
    shutil.copy(release_zip(tmp_path, '3.18.0'), build / 'WLR_OnAir_v3.18.0_Update.zip')
    page = page_of(monkeypatch, tmp_path, exe)
    assert f'name="installers" value="{build / ui.INSTALLERS_FOLDER}"' in page
    assert 'AppData' not in ui.default_installers_dir(None, None)


def test_publish_result_offers_fingerprint_copy_with_hint(actions, monkeypatch, tmp_path):
    act, tmp, _ = actions
    result = confirmed(act.publish, form_data(tmp, new_repo_ok=True))
    row = result['copy'][0]
    assert re.fullmatch(r'[0-9A-F]{4}(-[0-9A-F]{4}){3}', row['value']) and '첫 설정' in row['hint']
    page = page_of(monkeypatch, tmp_path, backup={'warning': '', 'fingerprint': 'ABCD-EF01-2345-6789'})
    assert 'data-copy="ABCD-EF01-2345-6789"' in page and '복사' in page


# ------------------------------------------------------------------ 10. 첫 설정 화면

def setup_fields(monkeypatch, tmp_path, role='ADMIN', legacy=False):
    monkeypatch.setenv('WLR_PROFILE_ROOT', str(tmp_path / 'profiles'))
    exe = tmp_path / 'inst' / ('WLR_OnAir_Admin.exe' if role == 'ADMIN' else 'WLR_OnAir_Viewer.exe')
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b'MZ')
    if legacy:
        (exe.parent / 'config.ini').write_text('[NETWORK]\n')
    seen = {}
    real = REAL_FORM

    class Fake:
        timed_out = False

        def __init__(self, title, fields, action, description='', **kwargs):
            seen.update(fields=fields, action=action, kwargs=kwargs, form=self)
            self.page = real(title, fields, action, description, **kwargs)
            self.page.server.server_close()

        def run(self):
            return None
    monkeypatch.setattr(ui, 'LocalForm', Fake)
    with pytest.raises(RuntimeError):
        ui.ensure_setup(role, exe)
    return seen


def test_setup_page_uses_radio_choice_and_browse_buttons(monkeypatch, tmp_path):
    seen = setup_fields(monkeypatch, tmp_path)
    page = seen['form'].page.page
    for value in ('legacy', 'shared', 'new'):
        assert f'name="choice" value="{value}"' in page
    for words in ('기존 공유 폴더 사용', '이 PC를 새로 연결', '새 데이터 만들기'):
        assert words in page
    assert 'data-action="browse_legacy"' in page and 'data-action="browse_shared"' in page
    assert set(seen['kwargs']['actions']) == {'browse_legacy', 'browse_shared'}
    assert 'name="choice" value="shared" checked' in page          # 예전 설정이 없으면 '새로 연결'이 기본
    viewer = setup_fields(monkeypatch, tmp_path / 'v', role='VIEWER', legacy=True)
    viewer_page = viewer['form'].page.page
    assert 'value="new"' not in viewer_page and 'name="choice" value="legacy" checked' in viewer_page


# [위험] 새 PC 인데도 '기존 프로그램을 종료했습니다' 체크를 요구해 설정을 마칠 수 없었다.
def test_fresh_install_does_not_require_closed_checkbox(monkeypatch, tmp_path):
    seen = setup_fields(monkeypatch, tmp_path)
    apply = seen['action']
    result = apply({'choice': 'new'})
    assert Path(result['config_dir']).is_dir()


def test_shared_choice_needs_folder_and_legacy_needs_closed(monkeypatch, tmp_path):
    seen = setup_fields(monkeypatch, tmp_path)
    apply = seen['action']
    with pytest.raises(ValueError, match='공유 데이터 폴더'):
        apply({'choice': 'shared', 'shared_dir': ''})
    with pytest.raises(ValueError, match='종료'):
        apply({'choice': 'legacy', 'legacy_dir': str(tmp_path)})
    with pytest.raises(ValueError, match='하나를 골라'):
        apply({})
    data = tmp_path / 'data'
    data.mkdir()
    import sqlite3
    sqlite3.connect(data / profiles.DB_NAME).close()
    result = apply({'choice': 'shared', 'shared_dir': f'"{data}"', 'legacy_dir': 'ignored'})
    assert Path(result['config_dir']).is_dir()


def test_choose_path_supports_save_and_backup_open_kinds(monkeypatch):
    monkeypatch.setattr(ui.os, 'name', 'posix')
    with pytest.raises(ValueError, match='Windows'):
        ui.choose_publisher_path('save')
    assert set(ui._PICK_KINDS) == {'folder', 'zip', 'save', 'json'}


# ------------------------------------------------------------------ 3. 직원 PC 업데이트 상태

def status_of(site):
    return core.load_json(site['profile'] / launcher.STATUS_FILE)


def test_status_file_up_to_date(site, monkeypatch):
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=None))
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, []))
    launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    status = status_of(site)
    assert status['result'] == 'up_to_date' and status['notice'] is False
    assert status['current_version'] == INSTALLED_VERSION and status['log_path'].endswith('launcher.log')
    assert status['checked_at'] and '최신' in status['message']


# [위험] 직원 PC 에서 업데이트 확인이 실패해도 로그에만 남아 아무도 몰랐다.
def test_status_file_reports_check_failure_in_plain_korean(site, monkeypatch):
    set_state(site, active=candidate(site))
    monkeypatch.setattr(core, 'stage_update', Mock(side_effect=OSError('SMB disconnected')))
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, []))
    launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    status = status_of(site)
    assert status['result'] == 'check_failed' and status['notice'] is True
    assert '공유 폴더 연결' in status['message']


def test_status_file_reports_updated_and_start_failure(site, monkeypatch):
    active = candidate(site)
    set_state(site, active=active)
    new = candidate(site, '3.16.0', 1)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=new))
    monkeypatch.setattr(core, 'verify_candidate', lambda *a: None)
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, []))
    launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    status = status_of(site)
    assert status['result'] == 'updated' and status['current_version'] == '3.16.0'
    # 다음 새 버전은 시작에 실패 -> 이전 버전으로 실행, 화면 안내 대상
    newer = candidate(site, '3.17.0', 2)
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=newer))

    def spawn(value, config, profile, logger):
        if value['version'] == '3.17.0':
            raise core.UpdateError('프로그램이 시작 확인 전에 종료되었습니다.')
        return Process(), profile / 'health' / 'x.json'
    monkeypatch.setattr(launcher, '_spawn', spawn)
    launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    status = status_of(site)
    assert status['result'] == 'start_failed' and status['notice'] is True
    assert status['current_version'] == '3.16.0' and status['available_version'] == '3.17.0'
    assert '이전 버전으로 실행' in status['message']


def test_status_write_failure_never_blocks_start(site, monkeypatch):
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=None))
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, []))
    real = core.atomic_json

    def fail_status(path, value):
        if Path(path).name == launcher.STATUS_FILE:
            raise OSError('read-only')
        return real(path, value)
    monkeypatch.setattr(core, 'atomic_json', fail_status)
    assert launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION) == 0


# [위험] 차단된 업데이트를 다시 허용하려면 INI 에 false→true 를 번갈아 써야 했다.
def test_reset_request_file_clears_blocked_updates_once(site, monkeypatch):
    active = candidate(site)
    set_state(site, active=active, highest=4, failed=[3, 4])
    (site['profile'] / launcher.RESET_REQUEST).write_text('now')
    monkeypatch.setattr(core, 'stage_update', Mock(return_value=None))
    monkeypatch.setattr(launcher, '_spawn', fake_spawn(site, []))
    launcher.run('ADMIN', site['exe'], version=INSTALLED_VERSION)
    state = core.load_json(site['profile'] / 'state.json')
    assert state['failed_sequences'] == [] and state['highest_sequence'] == 0
    assert 'reset_applied' not in state                               # INI 방식과 달리 되돌릴 필요가 없다
    assert not (site['profile'] / launcher.RESET_REQUEST).exists()


# ------------------------------------------------------------------ 9. 화면에서 바꾸는 설정

def test_ini_settings_update_preserves_comments_and_keeps_backup(tmp_path):
    ini = tmp_path / 'config_admin.ini'
    shutil.copy(Path(__file__).resolve().parents[2] / 'examples' / 'config_admin.example.ini', ini)
    original = ini.read_bytes()
    backup = profiles.update_ini_settings(ini, {'admin_password': 'pw 한글', 'server_port': '8010',
                                                'allowed_hosts': 'onair, onair.corp', 'backup_mirror_dir': r'D:\Backup'})
    assert backup.read_bytes() == original
    text = ini.read_text(encoding='utf-8')
    assert '; (선택) 일일 백업을 한 벌 더 둘 폴더' in text
    assert profiles.read_ini_settings(ini) == {'admin_password_set': True, 'server_port': '8010',
                                               'allowed_hosts': 'onair, onair.corp', 'backup_mirror_dir': r'D:\Backup'}
    profiles.update_ini_settings(ini, {'admin_password': ''})
    assert profiles.read_ini_settings(ini)['admin_password_set'] is False


def test_ini_settings_reads_cp949_and_rejects_bad_values(tmp_path):
    ini = tmp_path / 'config_viewer.ini'
    ini.write_bytes('[NETWORK]\nshared_folder_path = \\\\공유\\데이터\n'.encode('cp949'))
    for bad in ({'server_port': '80'}, {'server_port': 'abc'}, {'admin_password': 'a\nb'},
                {'admin_password': ' x'}, {'allowed_hosts': 'bad host!'}, {'backup_mirror_dir': 'relative'},
                {'shared_folder_path': 'x'}, {}):
        with pytest.raises(core.UpdateError):
            profiles.update_ini_settings(ini, bad)
    assert ini.read_bytes().decode('cp949').startswith('[NETWORK]')
    assert not list(tmp_path.glob('*.bak-*'))
    profiles.update_ini_settings(ini, {'server_port': '8011'})
    text = ini.read_text(encoding='utf-8')                            # 저장은 UTF-8
    assert '\\\\공유\\데이터' in text and 'server_port = 8011' in text


def test_example_ini_points_to_buttons_instead_of_reset_trick():
    root = Path(__file__).resolve().parents[2]
    for name in ('config_admin.example.ini', 'config_viewer.example.ini'):
        text = (root / 'examples' / name).read_text(encoding='utf-8')
        assert '자주 바꾸는 설정' in text and '차단된 업데이트 다시 허용' in text
        assert not re.search(r'^\s*reset_failed_sequences', text, re.M)
