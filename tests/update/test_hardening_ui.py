"""설정·게시 화면 보강(E-1/6/7/11) 시험. [위험] 주석은 막으려는 실제 사고다."""
import json
import logging
from pathlib import Path
import threading
import time
from unittest.mock import Mock

import pytest

from updater import core, publisher, setup_ui as ui
from test_publisher import release_zip
from test_setup_ui import request

PASS = 'correct horse battery staple'
UpdateError_or_value = (core.UpdateError, ValueError)


def start(form):
    thread = threading.Thread(target=form.server.serve_forever, daemon=True)
    thread.start()
    return thread


def stop(form):
    form.server.shutdown()
    form.server.server_close()


def confirmed(function, body):
    """위험한 동작은 먼저 확인 창 내용을 돌려준다. 시험에서는 사용자가 '확인'을 누른 것으로 이어 간다."""
    result = function(body)
    if isinstance(result, dict) and result.get('confirm'):
        result = function(dict(body, confirmed=result['confirm']['token']))
    assert not (isinstance(result, dict) and result.get('confirm'))
    return result


def post(form, path, body, token=True):
    import urllib.request
    import urllib.error
    headers = {'Content-Type': 'application/json', 'Origin': form.url}
    if token:
        headers['X-OnAir-Token'] = form.token
    req = urllib.request.Request(form.url + path, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


# ------------------------------------------------------------------ E-11 브라우저 실패

# [위험] 브라우저가 안 열렸는데 화면 없이 영원히 대기해 프로그램이 '먹통'처럼 보인다.
def test_browser_failure_shows_full_url_and_cancel_returns_without_waiting(monkeypatch):
    form = ui.LocalForm('t', [], lambda d: {'ok': 1})
    monkeypatch.setattr(ui.webbrowser, 'open', Mock(return_value=False))
    box = Mock(return_value=False)
    monkeypatch.setattr(ui, '_message_box', box)
    started = time.monotonic()
    assert form.run(timeout=30) is None
    assert time.monotonic() - started < 5                  # 취소하면 제한 시간(30초)을 기다리지 않는다
    text = box.call_args.args[1]
    assert f'{form.url}/?token={form.token}' in text and 'Ctrl+C' in text
    assert box.call_args.kwargs['ok_cancel'] is True


# [위험] webbrowser.open 이 예외를 던져도 같은 안내 없이 프로그램이 죽는다.
def test_browser_exception_is_handled_like_failure(monkeypatch):
    form = ui.LocalForm('t', [], lambda d: {'ok': 1})
    monkeypatch.setattr(ui.webbrowser, 'open', Mock(side_effect=OSError('no browser')))
    box = Mock(return_value=False)
    monkeypatch.setattr(ui, '_message_box', box)
    assert form.run(timeout=30) is None and box.called


# [위험] 안내 후 사용자가 주소를 직접 열어 제출하는 경로가 막힌다(서버가 이미 떠 있어야 함).
def test_manual_url_path_still_completes(monkeypatch):
    form = ui.LocalForm('t', [], lambda d: {'message': 'done'})
    monkeypatch.setattr(ui.webbrowser, 'open', Mock(return_value=False))

    def box(title, text, ok_cancel=False):
        assert post(form, '/submit', {})[0] == 200          # 사용자가 주소를 열어 제출했다고 가정
        return True
    monkeypatch.setattr(ui, '_message_box', box)
    assert form.run(timeout=30) == {'message': 'done'}


# [위험] 사용자가 창을 닫고 방치하면 서버가 영원히 남는다 -> 전체 제한 시간.
def test_form_times_out(monkeypatch):
    form = ui.LocalForm('t', [], lambda d: {})
    monkeypatch.setattr(ui.webbrowser, 'open', Mock(return_value=True))
    started = time.monotonic()
    assert form.run(timeout=0.2) is None and form.timed_out is True
    assert time.monotonic() - started < 2                  # 지정한 제한 시간에 끝난다
    assert ui.FORM_TIMEOUT == 15 * 60


# [위험] 토큰이 든 URL 이 로그에 남아 다른 사용자가 같은 PC 에서 설정 창을 열 수 있다.
def test_url_with_token_is_never_logged(monkeypatch, caplog):
    form = ui.LocalForm('t', [], lambda d: {})
    monkeypatch.setattr(ui.webbrowser, 'open', Mock(return_value=False))
    monkeypatch.setattr(ui, '_message_box', Mock(return_value=False))
    with caplog.at_level(logging.DEBUG):
        form.run(timeout=5)
    assert form.token not in caplog.text


# [위험] 시간 초과/취소 때 첫 설정이 '취소했습니다' 로만 나와 원인을 모른다.
def test_ensure_setup_timeout_message(monkeypatch, tmp_path):
    monkeypatch.setenv('WLR_PROFILE_ROOT', str(tmp_path / 'profiles'))

    class Fake:
        def __init__(self, *args, **kwargs):
            self.timed_out = True
            self.description = args[3]
        def run(self):
            return None
    monkeypatch.setattr(ui, 'LocalForm', Fake)
    with pytest.raises(RuntimeError, match='15분'):
        ui.ensure_setup('ADMIN', tmp_path / 'WLR_OnAir_Admin.exe')


# ------------------------------------------------------------------ E-7 첫 설정 화면 지문

# [위험] 사용자가 설치본의 공개키가 맞는지 대조할 방법이 없다.
def test_first_setup_description_shows_public_key_fingerprint(monkeypatch, tmp_path):
    monkeypatch.setenv('WLR_PROFILE_ROOT', str(tmp_path / 'profiles'))
    repo, key = tmp_path / 'repo', tmp_path / 'k' / 'signing_key.json'
    public = publisher.initialize_repository(repo, key)
    exe = tmp_path / 'inst' / 'WLR_OnAir_Admin.exe'
    exe.parent.mkdir()
    exe.write_bytes(b'MZ')
    core.atomic_json(exe.parent / 'onair_update.json', {'schema': 1, 'product': 'WLR_OnAir', 'role': 'ADMIN',
                                                       'repository': str(repo), 'public_key': public})
    seen = {}

    class Fake:
        timed_out = False
        def __init__(self, title, fields, action, description='', **kwargs):
            seen['description'] = description
            seen['fields'] = fields
        def run(self):
            return {'config_dir': str(tmp_path)}
    monkeypatch.setattr(ui, 'LocalForm', Fake)
    ui.ensure_setup('ADMIN', exe)
    assert core.public_key_fingerprint(public) in seen['description']
    # 지문은 복사 버튼이 있는 칸으로도 보여 준다(비교 안내 포함).
    copy = [f for f in seen['fields'] if f.get('type') == 'copy']
    assert copy and copy[0]['value'] == core.public_key_fingerprint(public) and '비교' in copy[0]['help']
    assert ui.bootstrap_fingerprint_text('VIEWER', exe) == ''          # 역할이 다르면 신뢰하지 않고 표시도 안 함
    assert ui.bootstrap_fingerprint_text('ADMIN', tmp_path / 'none.exe') == ''


# ------------------------------------------------------------------ 보조 동작 엔드포인트

# [위험] 보조 동작(/action)이 토큰·Origin 검사를 우회하거나 창을 닫아 버린다.
def test_action_endpoint_requires_token_and_does_not_finish_form():
    form = ui.LocalForm('t', [{'type': 'button', 'name': 'ping', 'label': 'P'}], lambda d: {'x': 1},
                        actions={'ping': lambda d: {'message': 'pong ' + str(d.get('a'))}})
    start(form)
    try:
        assert post(form, '/action/ping', {'a': 1}, token=False)[0] == 403
        assert post(form, '/action/nope', {})[0] == 404
        assert post(form, '/action/ping', {'a': 1}) == (200, {'ok': True, 'result': {'message': 'pong 1'}})
        assert not form.done.is_set()
        assert 'data-action="ping"' in form.page
    finally:
        stop(form)


def test_password_field_is_password_type():
    form = ui.LocalForm('t', [{'type': 'password', 'name': 'p', 'label': 'P'}], lambda d: {})
    try:
        assert 'type="password" name="p"' in form.page
    finally:
        form.server.server_close()


# ------------------------------------------------------------------ E-6 게시 화면 동작

@pytest.fixture
def actions(tmp_path):
    root = tmp_path / 'Publisher'
    root.mkdir()
    return ui.PublisherActions(root), tmp_path


def data(tmp_path, **extra):
    base = {'repository': str(tmp_path / 'repo'), 'release': str(release_zip(tmp_path)),
            'installers': str(tmp_path / 'inst'), 'roles': 'BOTH'}
    base.update(extra)
    return base


# Status is now checked automatically when publishing.
def test_publish_checks_status_without_manual_view_or_checkbox(actions):
    act, tmp = actions
    result = confirmed(act.publish, data(tmp, new_repo_ok=True))
    assert '게시를 완료' in result['message']
    assert (tmp / 'repo' / 'channels' / 'ADMIN.json').exists()


# [위험] 경로를 잘못 입력해도(처음 보는 빈 폴더) 조용히 새 저장소가 만들어진다.
def test_new_empty_folder_needs_explicit_new_repository_confirmation(actions):
    act, tmp = actions
    (tmp / 'repo').mkdir()
    act.status(data(tmp))
    with pytest.raises(ValueError, match='새 저장소를 만듭니다. 맞습니까'):
        confirmed(act.publish, data(tmp, status_ok=True))
    assert list((tmp / 'repo').iterdir()) == []
    result = confirmed(act.publish, data(tmp, status_ok=True, new_repo_ok=True))
    assert '게시를 완료' in result['message']
    assert (tmp / 'repo' / 'channels' / 'ADMIN.json').exists()


# [위험] 상태를 본 뒤 다른 사람이 게시해 내용이 바뀌었는데 예전 확인으로 게시된다.
def test_automatic_publish_rejects_stale_lower_version(actions):
    act, tmp = actions
    act.status(data(tmp))
    confirmed(act.publish, data(tmp, status_ok=True, new_repo_ok=True))
    second = data(tmp, release=str(release_zip(tmp, '3.15.1')))
    act.status(second)
    publisher.publish_release(tmp / 'repo', release_zip(tmp, '3.15.2'), act.key)       # 다른 게시자가 그 사이 게시
    with pytest.raises(publisher.UpdateError, match='보다 높아야'):
        confirmed(act.publish, dict(second, status_ok=True))


# [위험] 기존 저장소에도 '새 저장소' 체크가 필요하다고 하거나, 게시 후 경로 기억이 안 된다.
def test_existing_repository_publishes_with_status_check_and_remembers_paths(actions):
    act, tmp = actions
    act.status(data(tmp))
    confirmed(act.publish, data(tmp, status_ok=True, new_repo_ok=True))
    request2 = data(tmp, release=str(release_zip(tmp, '3.15.1')))
    message = act.status(request2)['message']
    assert '현재 버전 3.15.0' in message and '배포 차수 1' in message and '공개 키 지문' in message
    assert '순서 번호' not in message
    result = confirmed(act.publish, dict(request2, status_ok=True))
    assert '3.15.1' in result['message']
    assert publisher.load_last_paths(act.key)['repository'] == str(tmp / 'repo')


# [위험] 복원된 저장소에서 확인 체크만으로 sequence 가드를 통과한다.
def test_sequence_guard_needs_its_own_confirmation_in_ui(actions):
    act, tmp = actions
    act.status(data(tmp))
    confirmed(act.publish, data(tmp, status_ok=True, new_repo_ok=True))
    import shutil
    saved = tmp / 'saved'
    shutil.copytree(tmp / 'repo', saved)
    act.status(data(tmp, release=str(release_zip(tmp, '3.15.1'))))
    confirmed(act.publish, data(tmp, release=str(release_zip(tmp, '3.15.1')), status_ok=True))
    shutil.rmtree(tmp / 'repo')
    shutil.copytree(saved, tmp / 'repo')
    req = data(tmp, release=str(release_zip(tmp, '3.15.2')))
    status = act.status(req)
    assert '복원되었거나 지워진' in status['message']
    with pytest.raises(UpdateError_or_value, match='복원되었거나 지워진'):
        confirmed(act.publish, dict(req, status_ok=True))
    assert '3.15.2' in confirmed(act.publish, dict(req, status_ok=True, force_sequence_reset=True))['message']


# [위험] 키 백업 경고가 화면에 안 떠서 백업 없이 계속 운영한다.
def test_backup_warning_shown_in_status_and_publish_result(actions):
    act, tmp = actions
    assert '키 백업을 아직 만들지 않았습니다' not in act.status(data(tmp))['message']   # 키가 아직 없으면 경고 대상 아님
    result = confirmed(act.publish, data(tmp, status_ok=True, new_repo_ok=True))
    # 첫 게시에서 키를 만들었으면 결과 화면이 닫히지 않고 바로 키 백업을 요구한다.
    assert result['keep_open'] is True and result['after'] == 'backup'
    assert '서명 키를 새로 만들었습니다' in result['message'] and result['backup_default'].endswith('.json')
    assert '키 백업을 아직 만들지 않았습니다' in act.status(data(tmp))['message']
    act.backup({'backup_path': str(tmp / 'usb' / 'b.json'), 'backup_pass': PASS, 'backup_pass2': PASS})
    assert '키 백업: 만들었음' in act.status(data(tmp))['message']


# [위험] 암호 확인이 다른데 백업이 만들어지거나, 공유 폴더 안에 백업이 저장된다.
def test_backup_action_validations_and_restore_action(actions):
    act, tmp = actions
    act.status(data(tmp))
    confirmed(act.publish, data(tmp, status_ok=True, new_repo_ok=True))
    with pytest.raises(ValueError, match='서로 다릅니다'):
        act.backup({'backup_path': str(tmp / 'u.json'), 'backup_pass': PASS, 'backup_pass2': PASS + 'x'})
    with pytest.raises(ValueError, match='공유 업데이트 폴더 안'):
        act.backup({'backup_path': str(tmp / 'repo' / 'b.json'), 'backup_pass': PASS, 'backup_pass2': PASS,
                    'repository': str(tmp / 'repo')})
    with pytest.raises(ValueError, match='전체 경로'):
        act.backup({'backup_path': 'rel.json', 'backup_pass': PASS, 'backup_pass2': PASS})
    done = act.backup({'backup_path': str(tmp / 'usb' / 'b.json'), 'backup_pass': PASS, 'backup_pass2': PASS})
    assert PASS not in done['message']
    act.key.unlink()
    out = act.restore({'restore_path': str(tmp / 'usb' / 'b.json'), 'restore_pass': PASS,
                       'repository': str(tmp / 'repo')})
    assert '지문' in out['message'] and act.key.exists()


# [위험] 화면의 정지·해제가 선택한 역할만 처리하지 않는다.
def test_pause_resume_actions(actions):
    act, tmp = actions
    act.status(data(tmp))
    confirmed(act.publish, data(tmp, status_ok=True, new_repo_ok=True))
    confirmed(act.pause, dict(data(tmp), pause_roles='VIEWER'))
    assert (tmp / 'repo' / 'channels' / 'VIEWER.paused').exists() and not (tmp / 'repo' / 'channels' / 'ADMIN.paused').exists()
    assert '일시 정지 중' in act.status(data(tmp))['message']
    confirmed(act.resume, dict(data(tmp), pause_roles='BOTH'))
    assert not list((tmp / 'repo' / 'channels').glob('*.paused'))


# [위험] 경로에 따옴표가 붙은 채 복사해 붙여 넣으면 실패한다 / 상대경로가 통과한다.
def test_repository_path_validation(actions):
    act, tmp = actions
    with pytest.raises(ValueError, match='전체 경로'):
        act.status({'repository': 'relative/path'})
    with pytest.raises(ValueError, match='전체 경로'):
        act.status({'repository': ''})
    assert act.status({'repository': '"' + str(tmp / 'q') + '"'})['status']['exists'] is False
