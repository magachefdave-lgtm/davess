from pathlib import Path
import pytest
from updater import setup_ui as ui, publisher


def test_save_paths_without_publication_and_restore(tmp_path):
    actions = ui.PublisherActions(tmp_path / 'local')
    data = {'repository': str(tmp_path / '공유 폴더'), 'installers': str(tmp_path / '설치'),
            'release': '', 'backup_pass': 'never store me', 'status_ok': True}
    result = actions.save_paths(data)
    assert '저장' in result['message']
    assert publisher.load_last_paths(actions.key) == {k: data[k] for k in ('repository', 'installers')}
    assert not (tmp_path / '공유 폴더').exists()
    assert not actions.key.exists()
    assert 'never store me' not in (actions.root / publisher.LAST_PATHS).read_text()


def test_save_paths_rejects_relative_input(tmp_path):
    with pytest.raises(ValueError):
        ui.PublisherActions(tmp_path).save_paths({'repository': 'relative'})


def test_browse_result_and_cancel(tmp_path, monkeypatch):
    actions = ui.PublisherActions(tmp_path)
    monkeypatch.setattr(ui, 'choose_publisher_path', lambda kind, current: str(tmp_path / '새 파일.zip'))
    assert actions.browse_release({'release': ''})['values']['release'].endswith('새 파일.zip')
    monkeypatch.setattr(ui, 'choose_publisher_path', lambda kind, current: '')
    assert actions.browse_release({'release': 'keep'})['values'] == {}


def test_strict_save_reports_disk_failure(tmp_path, monkeypatch):
    def fail(*args): raise OSError('disk full')
    monkeypatch.setattr(publisher, 'atomic_json', fail)
    with pytest.raises(OSError):
        ui.PublisherActions(tmp_path).save_paths({'repository': str(tmp_path / 'repo')})
