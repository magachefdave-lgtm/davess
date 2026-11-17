"""Publisher integrity/security failure cases using local temporary repositories."""
import base64
import hashlib
import json
import multiprocessing
import os
import stat
import zipfile
from pathlib import Path

import pytest

from updater import publisher
from updater.core import UpdateError, canonical_json, load_json, verify_envelope


EXES = {'ADMIN': b'MZadmin executable fixture', 'VIEWER': b'MZviewer executable fixture'}


def release_zip(tmp_path, version='3.15.0', *, edit=None, extra=None, duplicate=None, symlink=None):
    files = {role: {'path': f'{role}/{publisher.EXECUTABLES[role]}',
                    'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
             for role, data in EXES.items()}
    manifest = {'schema': 1, 'product': 'WLR_OnAir', 'version': version, 'data_schema': 1, 'files': files}
    if edit:
        edit(manifest)
    path = tmp_path / ('release-' + str(version).replace('/', '_') + '.zip')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('release.json', canonical_json(manifest))
        for role, data in EXES.items():
            member = f'{role}/{publisher.EXECUTABLES[role]}'
            if symlink == role:
                info = zipfile.ZipInfo(member)
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, data)
            else:
                archive.writestr(member, data)
        if extra:
            archive.writestr(extra, b'unexpected')
        if duplicate:
            archive.writestr(duplicate, b'duplicate')
    return path


def test_initialize_keeps_pin_and_key_private(tmp_path):
    repo = tmp_path / 'repo'
    key = tmp_path / 'private' / 'publisher-key.json'
    pub = publisher.initialize_repository(repo, key)
    assert len(base64.b64decode(pub)) == 32
    assert publisher.initialize_repository(repo, key) == pub
    assert load_json(repo / publisher.PUBLIC_PIN)['public_key'] == pub
    key_doc = load_json(key)
    assert key_doc['public_key'] == pub
    if os.name == 'nt':
        assert key_doc['protection'] == 'windows-dpapi-user'
    else:
        assert stat.S_IMODE(key.stat().st_mode) == 0o600
        assert key_doc['protection'] == 'posix-user-only'
    assert not any('private_key' in p.read_text(errors='replace') for p in repo.rglob('*.json'))


def test_existing_pin_mismatch_and_lost_key_refused(tmp_path):
    repo, key = tmp_path / 'repo', tmp_path / 'key.json'
    pin = publisher.initialize_repository(repo, key)
    other = tmp_path / 'other-key.json'
    publisher.initialize_repository(tmp_path / 'other-repo', other)
    before = (repo / publisher.PUBLIC_PIN).read_bytes()
    with pytest.raises(UpdateError):
        publisher.initialize_repository(repo, other)
    assert (repo / publisher.PUBLIC_PIN).read_bytes() == before
    key.unlink()
    with pytest.raises(UpdateError):
        publisher.initialize_repository(repo, key)
    assert not key.exists()
    assert load_json(repo / publisher.PUBLIC_PIN)['public_key'] == pin


def test_key_forbidden_in_repository_or_installer_output(tmp_path):
    repo = tmp_path / 'repo'
    with pytest.raises(UpdateError):
        publisher.initialize_repository(repo, repo / 'key.json')
    assert not repo.exists()
    package = release_zip(tmp_path)
    with pytest.raises(UpdateError):
        publisher.publish_release(repo, package, tmp_path / 'out' / 'key.json', tmp_path / 'out')
    assert not repo.exists()


def test_publish_signed_packages_and_role_installers(tmp_path):
    repo, key, output = tmp_path / 'repo', tmp_path / 'key.json', tmp_path / 'installers'
    pin = publisher.initialize_repository(repo, key)
    result = publisher.publish_release(repo, release_zip(tmp_path), key, output)
    assert result['version'] == '3.15.0'
    for role in EXES:
        envelope = load_json(repo / 'channels' / f'{role}.json')
        payload = verify_envelope(envelope, pin, role)
        assert payload == result['channels'][role]
        assert payload['sequence'] == 1
        archive_path = repo / payload['package']
        assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == payload['sha256']
        assert archive_path.stat().st_size == payload['size']
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.namelist() == [publisher.EXECUTABLES[role]]
            assert archive.read(publisher.EXECUTABLES[role]) == EXES[role]
        with zipfile.ZipFile(result['installers'][role]) as archive:
            assert set(archive.namelist()) == {publisher.EXECUTABLES[role], 'onair_update.json', 'README.txt'}
            assert archive.read(publisher.EXECUTABLES[role]) == EXES[role]
            bootstrap = json.loads(archive.read('onair_update.json'))
            assert bootstrap == {'schema': 1, 'product': 'WLR_OnAir', 'role': role,
                                 'repository': str(repo), 'public_key': pin}
            assert 'private_key' not in archive.read('onair_update.json').decode()
            assert key.name not in archive.namelist()


@pytest.mark.parametrize('edit', [
    lambda m: m.update(schema=True),
    lambda m: m.update(schema=2),
    lambda m: m.update(product='Another'),
    lambda m: m.update(data_schema=2),
    lambda m: m.update(version='3.15.0/evil'),
    lambda m: m['files'].pop('VIEWER'),
    lambda m: m['files']['ADMIN'].update(path='../WLR_OnAir_Admin.exe'),
    lambda m: m['files']['ADMIN'].update(sha256='0' * 64),
    lambda m: m['files']['ADMIN'].update(size=True),
    lambda m: m['files']['ADMIN'].update(size=0),
    lambda m: m['files']['ADMIN'].update(size=publisher.MAX_PACKAGE + 1),
    lambda m: m['files']['VIEWER'].update(size=1),
])
def test_malformed_release_rejected_before_repository_or_key_creation(tmp_path, edit):
    package = release_zip(tmp_path, edit=edit)
    repo, key = tmp_path / 'repo', tmp_path / 'key.json'
    with pytest.raises(UpdateError):
        publisher.publish_release(repo, package, key)
    assert not repo.exists()
    assert not key.exists()


@pytest.mark.parametrize('extra', ['../escape.exe', '/absolute.exe', 'ADMIN/extra.exe', 'ADMIN\\evil.exe'])
def test_zip_extra_or_traversal_rejected(tmp_path, extra):
    with pytest.raises(UpdateError):
        publisher.publish_release(tmp_path / 'repo', release_zip(tmp_path, extra=extra), tmp_path / 'key.json')
    assert not (tmp_path / 'repo').exists()


def test_duplicate_and_symlink_archive_entries_rejected(tmp_path):
    with pytest.warns(UserWarning):
        package = release_zip(tmp_path, duplicate='release.json')
    with pytest.raises(UpdateError):
        publisher.publish_release(tmp_path / 'repo', package, tmp_path / 'key.json')
    package = release_zip(tmp_path, symlink='ADMIN')
    with pytest.raises(UpdateError):
        publisher.publish_release(tmp_path / 'repo', package, tmp_path / 'key.json')
    assert not (tmp_path / 'repo').exists()


def test_old_versions_and_modified_channel_refused_same_release_retry_stable(tmp_path):
    repo, key = tmp_path / 'repo', tmp_path / 'key.json'
    publisher.publish_release(repo, release_zip(tmp_path), key)
    previous = (repo / 'channels' / 'ADMIN.json').read_bytes()
    same = publisher.publish_release(repo, release_zip(tmp_path), key)
    assert same['channels']['ADMIN']['sequence'] == 1
    assert (repo / 'channels' / 'ADMIN.json').read_bytes() == previous
    with pytest.raises(UpdateError):
        publisher.publish_release(repo, release_zip(tmp_path, '3.14.9'), key)
    assert (repo / 'channels' / 'ADMIN.json').read_bytes() == previous
    channel = load_json(repo / 'channels' / 'ADMIN.json')
    channel['payload']['sequence'] = 99
    (repo / 'channels' / 'ADMIN.json').write_bytes(canonical_json(channel))
    with pytest.raises(UpdateError):
        publisher.publish_release(repo, release_zip(tmp_path, '3.15.1'), key)


def test_roles_publish_independently_and_sequences_increase(tmp_path):
    repo, key = tmp_path / 'repo', tmp_path / 'key.json'
    release = release_zip(tmp_path)
    first = publisher.publish_release(repo, release, key, roles=('ADMIN',))
    assert set(first['channels']) == {'ADMIN'}
    assert not (repo / 'channels' / 'VIEWER.json').exists()
    before = (repo / 'channels' / 'ADMIN.json').read_bytes()
    second = publisher.publish_release(repo, release, key, roles=('VIEWER',))
    assert second['channels']['VIEWER']['sequence'] == 2
    assert (repo / 'channels' / 'ADMIN.json').read_bytes() == before
    third = publisher.publish_release(repo, release_zip(tmp_path, '3.15.1'), key)
    assert {p['sequence'] for p in third['channels'].values()} == {3}


def test_copy_failure_keeps_channels_unchanged(tmp_path, monkeypatch):
    repo, key = tmp_path / 'repo', tmp_path / 'key.json'
    publisher.publish_release(repo, release_zip(tmp_path), key)
    before = {role: (repo / 'channels' / f'{role}.json').read_bytes() for role in EXES}
    original_commit = publisher._commit_immutable
    copied = []
    def fail_second(source, destination):
        if copied:
            raise OSError('simulated lost SMB connection')
        original_commit(source, destination)
        copied.append(destination)
    monkeypatch.setattr(publisher, '_commit_immutable', fail_second)
    with pytest.raises(UpdateError):
        publisher.publish_release(repo, release_zip(tmp_path, '3.15.1'), key)
    assert len(copied) == 1
    for role in EXES:
        assert (repo / 'channels' / f'{role}.json').read_bytes() == before[role]


def test_existing_package_is_never_overwritten(tmp_path):
    source, destination = tmp_path / 'source', tmp_path / 'existing'
    source.write_bytes(b'new release')
    destination.write_bytes(b'old bytes')
    with pytest.raises(UpdateError):
        publisher._commit_immutable(source, destination)
    assert destination.read_bytes() == b'old bytes'


def _hold_lock(repository, ready, release):
    with publisher._publisher_lock(Path(repository)):
        ready.set()
        release.wait(20)


def test_lock_excludes_another_process(tmp_path):
    context = multiprocessing.get_context('spawn')
    ready, release = context.Event(), context.Event()
    repository = tmp_path / 'repo'
    process = context.Process(target=_hold_lock, args=(str(repository), ready, release))
    process.start()
    try:
        assert ready.wait(10)
        with pytest.raises(UpdateError):
            publisher.initialize_repository(repository, tmp_path / 'key.json')
        assert not (tmp_path / 'key.json').exists()
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0


@pytest.mark.skipif(os.name == 'nt', reason='POSIX permission and symlink test')
def test_unsafe_key_permissions_and_symlink_repository_refused(tmp_path):
    repo, key = tmp_path / 'repo', tmp_path / 'key.json'
    publisher.initialize_repository(repo, key)
    key.chmod(0o644)
    with pytest.raises(UpdateError):
        publisher.initialize_repository(repo, key)
    linked = tmp_path / 'linked'
    linked.symlink_to(repo, target_is_directory=True)
    with pytest.raises(UpdateError):
        publisher.initialize_repository(linked, key)


def test_nul_truncation_and_dos_directory_zip_metadata_rejected():
    nul = zipfile.ZipInfo('ADMIN/WLR_OnAir_Admin.exe\x00evil')
    assert nul.filename == 'ADMIN/WLR_OnAir_Admin.exe'
    with pytest.raises(UpdateError):
        publisher._valid_zip_member(nul)
    directory = zipfile.ZipInfo('ADMIN/WLR_OnAir_Admin.exe')
    directory.external_attr = 0x10
    with pytest.raises(UpdateError):
        publisher._valid_zip_member(directory)


def test_partial_channel_failure_can_resume_same_release(tmp_path, monkeypatch):
    repo, key = tmp_path / 'repo', tmp_path / 'key.json'
    publisher.publish_release(repo, release_zip(tmp_path), key)
    new_release = release_zip(tmp_path, '3.15.1')
    original_atomic_json = publisher.atomic_json
    def fail_viewer(path, value):
        if Path(path).name == 'VIEWER.json':
            raise OSError('SMB unavailable while committing Viewer channel')
        return original_atomic_json(path, value)
    monkeypatch.setattr(publisher, 'atomic_json', fail_viewer)
    with pytest.raises(UpdateError):
        publisher.publish_release(repo, new_release, key)
    admin_before = (repo / 'channels' / 'ADMIN.json').read_bytes()
    assert load_json(repo / 'channels' / 'ADMIN.json')['payload']['version'] == '3.15.1'
    assert load_json(repo / 'channels' / 'VIEWER.json')['payload']['version'] == '3.15.0'
    monkeypatch.setattr(publisher, 'atomic_json', original_atomic_json)
    resumed = publisher.publish_release(repo, new_release, key)
    assert {payload['version'] for payload in resumed['channels'].values()} == {'3.15.1'}
    assert {payload['sequence'] for payload in resumed['channels'].values()} == {2}
    assert (repo / 'channels' / 'ADMIN.json').read_bytes() == admin_before
    viewer_before = (repo / 'channels' / 'VIEWER.json').read_bytes()
    repeated = publisher.publish_release(repo, new_release, key)
    assert {payload['sequence'] for payload in repeated['channels'].values()} == {2}
    assert (repo / 'channels' / 'VIEWER.json').read_bytes() == viewer_before


def test_same_version_different_content_cannot_replace_published_release(tmp_path, monkeypatch):
    repo, key = tmp_path / 'repo', tmp_path / 'key.json'
    publisher.publish_release(repo, release_zip(tmp_path), key)
    before = {role: (repo / 'channels' / f'{role}.json').read_bytes() for role in EXES}
    monkeypatch.setitem(EXES, 'VIEWER', b'MZ changed Viewer')
    different = release_zip(tmp_path)
    with pytest.raises(UpdateError, match='같은 버전'):
        publisher.publish_release(repo, different, key)
    for role in EXES:
        assert (repo / 'channels' / f'{role}.json').read_bytes() == before[role]


def test_immutable_staging_copy_interruption_never_exposes_partial_final(tmp_path, monkeypatch):
    source, destination = tmp_path / 'source', tmp_path / 'repo' / 'release.zip'
    source.write_bytes(b'complete immutable package' * 100)
    original_copy = publisher.shutil.copyfileobj
    def interrupted_copy(input_file, output_file, length):
        output_file.write(input_file.read(9))
        raise KeyboardInterrupt('simulated abrupt copy interruption')
    monkeypatch.setattr(publisher.shutil, 'copyfileobj', interrupted_copy)
    with pytest.raises(KeyboardInterrupt):
        publisher._commit_immutable(source, destination)
    assert not destination.exists()
    monkeypatch.setattr(publisher.shutil, 'copyfileobj', original_copy)
    # A real process kill can leave an unreferenced staging file. It must not
    # prevent the retry or be mistaken for the final immutable release.
    orphan = destination.parent / f'.{destination.name}.abandoned.tmp'
    orphan.write_bytes(b'partial')
    publisher._commit_immutable(source, destination)
    assert destination.read_bytes() == source.read_bytes()


def test_atomic_publish_failure_keeps_final_absent_and_allows_retry(tmp_path, monkeypatch):
    source, destination = tmp_path / 'source', tmp_path / 'repo' / 'release.zip'
    source.write_bytes(b'complete immutable package')
    operation = 'rename' if os.name == 'nt' else 'link'
    original = getattr(publisher.os, operation)
    def unavailable(source_path, final_path):
        assert Path(source_path).read_bytes() == source.read_bytes()
        assert not Path(final_path).exists()
        raise OSError('simulated network loss at atomic publish')
    monkeypatch.setattr(publisher.os, operation, unavailable)
    with pytest.raises(OSError):
        publisher._commit_immutable(source, destination)
    assert not destination.exists()
    monkeypatch.setattr(publisher.os, operation, original)
    publisher._commit_immutable(source, destination)
    assert destination.read_bytes() == source.read_bytes()


def test_atomic_package_publish_failure_keeps_previous_channels(tmp_path, monkeypatch):
    repo, key = tmp_path / 'repo', tmp_path / 'key.json'
    publisher.publish_release(repo, release_zip(tmp_path), key)
    before = {role: (repo / 'channels' / f'{role}.json').read_bytes() for role in EXES}
    operation = 'rename' if os.name == 'nt' else 'link'
    original = getattr(publisher.os, operation)
    def unavailable(*args):
        raise OSError('simulated SMB atomic package publish failure')
    monkeypatch.setattr(publisher.os, operation, unavailable)
    new_release = release_zip(tmp_path, '3.15.1')
    with pytest.raises(UpdateError):
        publisher.publish_release(repo, new_release, key)
    for role in EXES:
        assert (repo / 'channels' / f'{role}.json').read_bytes() == before[role]
    assert not list((repo / 'packages').rglob('3.15.1-*.zip'))
    monkeypatch.setattr(publisher.os, operation, original)
    result = publisher.publish_release(repo, new_release, key)
    assert {payload['version'] for payload in result['channels'].values()} == {'3.15.1'}


def _crash_during_immutable_copy(source, destination):
    def die_mid_copy(input_file, output_file, length):
        output_file.write(input_file.read(9))
        output_file.flush()
        os.fsync(output_file.fileno())
        os._exit(77)
    publisher.shutil.copyfileobj = die_mid_copy
    publisher._commit_immutable(Path(source), Path(destination))


def test_real_process_exit_mid_copy_leaves_only_unreferenced_temp(tmp_path):
    source, destination = tmp_path / 'source', tmp_path / 'repo' / 'release.zip'
    source.write_bytes(b'complete immutable package' * 100)
    context = multiprocessing.get_context('spawn')
    process = context.Process(target=_crash_during_immutable_copy, args=(str(source), str(destination)))
    process.start()
    process.join(10)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail('crash-injection child did not terminate')
    assert process.exitcode == 77
    assert not destination.exists()
    assert len(list(destination.parent.glob(f'.{destination.name}.*.tmp'))) == 1
    publisher._commit_immutable(source, destination)
    assert destination.read_bytes() == source.read_bytes()
