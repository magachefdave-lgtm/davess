"""게시 화면 피드백: 이미 게시된 ZIP 재게시, 같은 버전 재빌드, Update ZIP 기본 경로."""
import os
import time

import pytest

from test_hardening_ui import confirmed
from updater import core, publisher, setup_ui as ui
from test_publisher import EXES, release_zip

SAME_FILE = '이미 게시된 것과 같은 파일입니다'


def _channels(repo):
    return {role: (repo / 'channels' / f'{role}.json').read_bytes()
            for role in EXES if (repo / 'channels' / f'{role}.json').exists()}


# ------------------------------------------------------------ 2. 같은 ZIP 재게시

def test_first_publish_is_not_reported_as_already_published(tmp_path):
    result = publisher.publish_release(tmp_path / 'repo', release_zip(tmp_path), tmp_path / 'k' / 'key.json')
    assert result['already_published'] is False
    assert result['unchanged'] == []
    assert not any(SAME_FILE in w for w in result['warnings'])


def test_republishing_identical_zip_is_flagged_and_changes_nothing(tmp_path):
    repo, key = tmp_path / 'repo', tmp_path / 'k' / 'key.json'
    publisher.publish_release(repo, release_zip(tmp_path), key)
    before = _channels(repo)
    again = publisher.publish_release(repo, release_zip(tmp_path), key)
    assert _channels(repo) == before
    assert again['already_published'] is True
    assert again['unchanged'] == ['ADMIN', 'VIEWER']
    assert any(SAME_FILE in w and 'Update.zip' in w for w in again['warnings'])


def test_partial_retry_publishes_new_role_and_notes_unchanged_one(tmp_path):
    repo, key = tmp_path / 'repo', tmp_path / 'k' / 'key.json'
    release = release_zip(tmp_path)
    publisher.publish_release(repo, release, key, roles=('ADMIN',))
    result = publisher.publish_release(repo, release, key)          # 관리자용은 이미 게시됨
    assert result['already_published'] is False
    assert result['unchanged'] == ['ADMIN']
    assert (repo / 'channels' / 'VIEWER.json').exists()
    assert any('관리자용' in w and '그대로' in w for w in result['warnings'])
    assert not any(SAME_FILE in w for w in result['warnings'])


@pytest.fixture
def actions(tmp_path):
    root = tmp_path / 'Publisher'
    root.mkdir()
    return ui.PublisherActions(root), tmp_path


def _form(tmp_path, release, **extra):
    value = {'repository': str(tmp_path / 'repo'), 'release': str(release),
             'installers': str(tmp_path / 'inst'), 'roles': 'BOTH'}
    value.update(extra)
    return value


def test_ui_does_not_claim_success_for_already_published_zip(actions):
    act, tmp = actions
    release = release_zip(tmp, '3.17.11')
    first = confirmed(act.publish, _form(tmp, release, new_repo_ok=True))
    assert '게시를 완료' in first['message']
    with pytest.raises(ValueError) as caught:
        confirmed(act.publish, _form(tmp, release))
    message = str(caught.value)
    assert SAME_FILE in message and '새로 만든 Update.zip' in message
    assert '3.17.11' in message
    assert '게시를 완료' not in message


def test_ui_partial_retry_still_reports_completion_with_note(actions):
    act, tmp = actions
    release = release_zip(tmp)
    confirmed(act.publish, _form(tmp, release, new_repo_ok=True, roles='ADMIN'))
    result = confirmed(act.publish, _form(tmp, release))
    assert '게시를 완료' in result['message'] and '관리자용' in result['message']


# ------------------------------------------------------------ 3. 같은 버전 재빌드

def test_rebuilt_same_version_message_explains_cause_and_remedy(tmp_path, monkeypatch):
    repo, key = tmp_path / 'repo', tmp_path / 'k' / 'key.json'
    publisher.publish_release(repo, release_zip(tmp_path, '3.17.13'), key)
    before = _channels(repo)
    monkeypatch.setitem(EXES, 'ADMIN', b'MZadmin rebuilt bytes')
    (tmp_path / 'b').mkdir()
    with pytest.raises(core.UpdateError) as caught:
        publisher.publish_release(repo, release_zip(tmp_path / 'b', '3.17.13'), key)
    message = str(caught.value)
    assert '같은 버전 번호(3.17.13)로 이미 다른 파일이 게시되어 있습니다' in message
    assert 'VERSION' in message and '올려' in message and '다시 빌드' in message
    assert '3.17.14' in message                      # 다음 번호 예시
    assert '관리자용' in message
    assert _channels(repo) == before


def test_same_executable_with_different_min_launcher_gets_specific_message(tmp_path):
    repo, key = tmp_path / 'repo', tmp_path / 'k' / 'key.json'
    publisher.publish_release(repo, release_zip(tmp_path, '3.17.13'), key)
    with pytest.raises(core.UpdateError) as caught:
        publisher.publish_release(repo, release_zip(tmp_path, '3.17.13'), key, min_launcher='1.0.1')
    message = str(caught.value)
    assert '같은 버전 번호(3.17.13)' in message and '설정' in message and 'VERSION' in message


# ------------------------------------------------------------ 2. Update ZIP 기본 경로

def _touch(path, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'zip')
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_finds_newest_update_zip_in_build_output_folder(tmp_path):
    build = tmp_path / 'build'
    exe = _touch(build / 'Admin' / 'WLR_OnAir_Admin.exe')
    now = time.time()
    _touch(build / 'WLR_OnAir_v3.17.9_Update.zip', now)            # 더 최근 파일이지만 낮은 버전
    newest = _touch(build / 'WLR_OnAir_v3.17.13_Update.zip', now - 100)
    _touch(build / 'WLR_OnAir_vbad_Update.zip', now)
    assert ui.find_latest_update_zip(exe) == str(newest)


def test_finds_update_zip_next_to_exe(tmp_path):
    exe = _touch(tmp_path / 'dist' / 'WLR_OnAir_Admin.exe')
    found = _touch(tmp_path / 'dist' / 'WLR_OnAir_v3.18.0_Update.zip')
    assert ui.find_latest_update_zip(exe) == str(found)


def test_no_update_zip_found_returns_none(tmp_path):
    exe = _touch(tmp_path / 'x' / 'Admin' / 'WLR_OnAir_Admin.exe')
    assert ui.find_latest_update_zip(exe) is None
    assert ui.find_latest_update_zip(tmp_path / 'missing' / 'WLR_OnAir_Admin.exe') is None


def _page(monkeypatch, tmp_path, executable, last):
    monkeypatch.setattr(publisher, 'default_publisher_dir', lambda: tmp_path / 'publisher')
    monkeypatch.setattr(publisher, 'key_backup_status', lambda key: {'warning': ''})
    monkeypatch.setattr(publisher, 'load_last_paths', lambda key=None: dict(last))
    seen = {}

    def capture(form):
        seen['page'] = form.page
        form.server.server_close()
    monkeypatch.setattr(ui.LocalForm, 'run', capture)
    ui.publisher_ui(executable)
    return seen['page']


def test_publish_form_prefers_new_build_zip_over_last_published(monkeypatch, tmp_path):
    build = tmp_path / 'build'
    exe = _touch(build / 'Admin' / 'WLR_OnAir_Admin.exe')
    fresh = _touch(build / 'WLR_OnAir_v3.17.13_Update.zip')
    old = str(tmp_path / 'old' / 'WLR_OnAir_v3.17.12_Update.zip')
    page = _page(monkeypatch, tmp_path, exe, {'release': old})
    assert f'name="release" value="{fresh}"' in page
    assert old not in page


def test_publish_form_falls_back_to_last_release_and_placeholder_is_version_neutral(monkeypatch, tmp_path):
    exe = _touch(tmp_path / 'nozip' / 'Admin' / 'WLR_OnAir_Admin.exe')
    old = str(tmp_path / 'old' / 'WLR_OnAir_v3.17.12_Update.zip')
    page = _page(monkeypatch, tmp_path, exe, {'release': old})
    assert f'name="release" value="{old}"' in page
    assert '3.15.4' not in page
    # v3.18.0 자리표시자에 예시 경로를 넣지 않는다. 버전 중립 안내는 칸 아래 도움말에 있다.
    assert 'vX.Y.Z' not in page and 'WLR_OnAir_v(버전)_Update.zip' in page
