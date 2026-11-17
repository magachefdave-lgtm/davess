"""Windows flush semantics at the OS boundary, with real profile/DB files."""
import configparser
import errno
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from updater import profiles
from updater.core import UpdateError


@pytest.fixture
def profile_root(tmp_path, monkeypatch):
    root = tmp_path / '프로필'
    monkeypatch.setenv('WLR_PROFILE_ROOT', str(root))
    return root


@pytest.fixture
def windows_flush(monkeypatch):
    real_fsync = os.fsync

    def require_write_access(fd):
        # Zero-byte write verifies write access without modifying file content.
        # Models Windows _commit/FlushFileBuffers, which rejects read handles.
        os.write(fd, b'')
        return real_fsync(fd)

    monkeypatch.setattr(profiles, 'os', SimpleNamespace(**(vars(os) | {'fsync': require_write_access})))


def make_existing(folder):
    folder.mkdir()
    db = folder / 'smart_duty.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE original(value TEXT)')
        conn.execute('INSERT INTO original VALUES (?)', ('보존할 원본',))
    return db


@pytest.mark.parametrize('role', ['ADMIN', 'VIEWER'])
@pytest.mark.parametrize('kind', ['legacy', 'shared'])
def test_existing_profile_saves_under_windows_flush_rules(tmp_path, profile_root, windows_flush, role, kind):
    legacy = tmp_path / '기존 설치'
    db = make_existing(legacy)
    ini = legacy / f'config_{role.lower()}.ini'
    ini.write_text('[NETWORK]\nshared_folder_path=\nserver_port=8123\n', encoding='utf-8')
    before = (hashlib.sha256(db.read_bytes()).hexdigest(), ini.read_bytes())
    args = {'legacy_dir': legacy} if kind == 'legacy' else {'shared_dir': legacy}
    config_dir = profiles.prepare_profile(role, **args)
    config = configparser.ConfigParser(interpolation=None)
    config.read(config_dir / ini.name, encoding='utf-8')
    assert config['NETWORK']['shared_folder_path'] == str(legacy)
    assert (hashlib.sha256(db.read_bytes()).hexdigest(), ini.read_bytes()) == before
    assert not (config_dir / 'smart_duty.db').exists()
    assert not list(config_dir.glob('*.tmp'))
    other = 'VIEWER' if role == 'ADMIN' else 'ADMIN'
    assert not (profile_root / other).exists()


def test_fresh_admin_creates_valid_db_under_windows_flush_rules(profile_root, windows_flush):
    config_dir = profiles.prepare_profile('ADMIN', allow_new=True)
    with sqlite3.connect(config_dir / 'smart_duty.db') as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone() == ('ok',)
    assert (config_dir / 'config_admin.ini').is_file()


def test_flush_failure_reports_stage_and_os_code_without_publishing(tmp_path, monkeypatch):
    target = tmp_path / 'config_admin.ini'
    def fail(fd):
        error = OSError(errno.EACCES, 'Access is denied')
        error.winerror = 5
        raise error
    monkeypatch.setattr(profiles, 'os', SimpleNamespace(**(vars(os) | {'fsync': fail})))
    with pytest.raises(UpdateError) as caught:
        profiles._publish_new_file(target, lambda path: path.write_bytes(b'[NETWORK]\n'))
    assert 'WinError 5' in str(caught.value)
    assert '동기화' in str(caught.value)
    assert str(target) in str(caught.value)
    assert not target.exists()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('race', [False, True])
def test_windows_publish_needs_no_hardlinks_and_never_overwrites(tmp_path, monkeypatch, race):
    target = tmp_path / 'config_admin.ini'
    real_link = os.link
    def unsupported(*args):
        raise OSError(errno.ENOTSUP, 'Hard links unavailable')
    def windows_rename(source, destination):
        if race:
            destination.write_bytes(b'existing-profile')
        # POSIX emulation of Windows rename's atomic no-replace contract.
        real_link(source, destination)
        source.unlink()
    boundary = vars(os) | {'name': 'nt', 'link': unsupported, 'rename': windows_rename}
    monkeypatch.setattr(profiles, 'os', SimpleNamespace(**boundary))
    if race:
        with pytest.raises(UpdateError, match='덮어쓰지'):
            profiles._publish_new_file(target, lambda path: path.write_bytes(b'new-profile'))
        assert target.read_bytes() == b'existing-profile'
    else:
        profiles._publish_new_file(target, lambda path: path.write_bytes(b'new-profile'))
        assert target.read_bytes() == b'new-profile'
    assert list(tmp_path.iterdir()) == [target]
