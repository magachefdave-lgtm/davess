"""Updater end-to-end tests. All repositories, keys and DBs are synthetic.

No executable is launched; startup/rollback process behavior is tested separately.
This verifies publisher -> pinned installer -> authenticated stage -> local file
validation while preserving initialized profile configuration and SQLite bytes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import zipfile

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[2]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from updater import core, publisher

ROLES = ('ADMIN', 'VIEWER')
EXECUTABLES = {'ADMIN': 'WLR_OnAir_Admin.exe', 'VIEWER': 'WLR_OnAir_Viewer.exe'}


def bytes_hash(value):
    return hashlib.sha256(value).hexdigest()


def make_release(root, version='3.15.1', corrupt_hash=False, extra_entry=False):
    """Developer release container with inert synthetic MZ-like test data."""
    path = root / f'release-{version}.zip'
    files, executables = {}, {}
    for role in ROLES:
        name = f'{role}/{EXECUTABLES[role]}'
        data = b'MZ\0SYNTHETIC_NOT_EXECUTABLE\0' + (role + ':' + version).encode()
        executables[role] = data
        files[role] = {'path': name, 'sha256': bytes_hash(data), 'size': len(data)}
    if corrupt_hash:
        files['VIEWER']['sha256'] = '0' * 64
    metadata = {'schema': 1, 'product': 'WLR_OnAir', 'version': version,
                'data_schema': 1, 'files': files}
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('release.json', core.canonical_json(metadata))
        for role in ROLES:
            archive.writestr(files[role]['path'], executables[role])
        if extra_entry:
            archive.writestr('ADMIN/../unsafe.txt', 'reject')
    return path, executables


def load_manifest(repository, role):
    return json.loads((repository / 'channels' / f'{role}.json').read_text(encoding='utf-8'))


def install_pin(publication, role):
    with zipfile.ZipFile(publication['installers'][role]) as archive:
        assert EXECUTABLES[role] in archive.namelist()
        assert 'onair_update.json' in archive.namelist()
        assert not any('private' in name.lower() or 'secret' in name.lower() for name in archive.namelist())
        pin = json.loads(archive.read('onair_update.json'))
    return core.validate_bootstrap(pin, role)


def protect_profile(profile, role):
    """Prior application files with a real SQLite sentinel and stale temp debris."""
    config = profile / 'config'
    config.mkdir(parents=True, exist_ok=True)
    (config / f'config_{role.lower()}.ini').write_text(
        '[NETWORK]\nshared_folder_path=Z:/original-share\n'
        'admin_password=synthetic-do-not-log\n', encoding='utf-8')
    with sqlite3.connect(config / 'smart_duty.db') as db:
        db.execute('CREATE TABLE retained (id INTEGER PRIMARY KEY, content TEXT NOT NULL)')
        db.execute('INSERT INTO retained VALUES (?, ?)', (1, '사용자 보존 데이터'))
    (profile / 'state.json').write_text('{"sentinel":"do-not-change"}', encoding='utf-8')
    (config / 'backups').mkdir()
    (config / 'backups' / 'prior.db').write_bytes(b'original-backup-unchanged')
    return snapshot_protected(profile)


def snapshot_protected(profile):
    result = {}
    for path in (profile / 'config').rglob('*'):
        if path.is_file():
            result[str(path.relative_to(profile))] = bytes_hash(path.read_bytes())
    state = profile / 'state.json'
    if state.exists():
        result['state.json'] = bytes_hash(state.read_bytes())
    return result


def repository_payload_snapshot(repository):
    return {str(path.relative_to(repository)): bytes_hash(path.read_bytes())
            for folder in ('channels', 'packages')
            for path in (repository / folder).rglob('*') if path.is_file()}


@pytest.fixture
def release_setup(tmp_path):
    repo = tmp_path / '공유 repo with spaces'
    key = tmp_path / 'publishing-profile' / 'private-key.json'
    installers = tmp_path / 'installers'
    key.parent.mkdir()
    public_key = publisher.initialize_repository(repo, key)
    release, binaries = make_release(tmp_path)
    publication = publisher.publish_release(repo, release, key, installer_output=installers)
    return {'root': tmp_path, 'repository': repo, 'key': key, 'public_key': public_key,
            'publication': publication, 'binaries': binaries}


def stage(setup, profile, role, **kwargs):
    return core.stage_update(setup['repository'], profile, role, setup['public_key'], **kwargs)


@pytest.mark.parametrize('role', ROLES)
def test_publisher_installer_pin_stage_and_verify_end_to_end(release_setup, role):
    setup = release_setup
    profile = setup['root'] / f'profile-{role}'
    before = protect_profile(profile, role)
    pin = install_pin(setup['publication'], role)
    assert pin['public_key'] == setup['public_key']
    assert Path(pin['repository']) == setup['repository'].resolve()
    manifest = load_manifest(setup['repository'], role)
    payload = core.verify_envelope(manifest, pin['public_key'], role)
    assert payload['version'] == '3.15.1' and payload['role'] == role
    candidate = core.stage_update(Path(pin['repository']), profile, role, pin['public_key'])
    executable = core.verify_candidate(candidate, profile, role)
    assert executable.read_bytes() == setup['binaries'][role]
    assert executable.is_relative_to(profile / 'versions')
    assert not executable.is_relative_to(setup['repository'])
    assert candidate['sha256'] == bytes_hash(setup['binaries'][role])
    assert snapshot_protected(profile) == before


@pytest.mark.parametrize('role', ROLES)
def test_role_mixup_rejected_at_pin_manifest_and_candidate(release_setup, role):
    setup = release_setup
    other = 'VIEWER' if role == 'ADMIN' else 'ADMIN'
    pin = install_pin(setup['publication'], other)
    with pytest.raises(core.UpdateError):
        core.validate_bootstrap(pin, role)
    envelope = load_manifest(setup['repository'], other)
    with pytest.raises(core.UpdateError):
        core.verify_envelope(envelope, setup['public_key'], role)
    profile = setup['root'] / f'profile-{role}'
    protect_profile(profile, role)
    candidate = stage(setup, profile, role)
    with pytest.raises(core.UpdateError):
        core.verify_candidate(candidate, profile, other)


def test_signature_tampering_cannot_mutate_prior_profile(release_setup):
    setup, role = release_setup, 'VIEWER'
    profile = setup['root'] / 'prior-viewer'
    before = protect_profile(profile, role)
    envelope = load_manifest(setup['repository'], role)
    envelope['payload']['version'] = '9.99.99'
    core.atomic_json(setup['repository'] / 'channels' / f'{role}.json', envelope)
    with pytest.raises(core.UpdateError):
        stage(setup, profile, role)
    assert snapshot_protected(profile) == before
    assert not list((profile / 'versions').rglob('*.exe'))


def test_changed_repository_signer_is_rejected_by_pinned_installer(release_setup):
    setup, role = release_setup, 'ADMIN'
    pin = install_pin(setup['publication'], role)
    attacker_repository = setup['root'] / 'different-publisher'
    other_key = setup['root'] / 'other-private-key.json'
    other_public_key = publisher.initialize_repository(attacker_repository, other_key)
    assert other_public_key != pin['public_key']
    release, _ = make_release(setup['root'], '3.15.9')
    publisher.publish_release(attacker_repository, release, other_key)
    # Replacement manifest correctly signed by another publisher must not acquire trust.
    foreign_manifest = load_manifest(attacker_repository, role)
    core.atomic_json(setup['repository'] / 'channels' / f'{role}.json', foreign_manifest)
    profile = setup['root'] / 'prior-admin'
    before = protect_profile(profile, role)
    with pytest.raises(core.UpdateError):
        stage(setup, profile, role)
    assert snapshot_protected(profile) == before
    with pytest.raises((core.UpdateError, publisher.PublishError) if hasattr(publisher, 'PublishError') else core.UpdateError):
        publisher.initialize_repository(setup['repository'], other_key)


@pytest.mark.parametrize('damage', ['truncate', 'replace', 'missing'])
def test_interrupted_or_tampered_packages_never_replace_prior_active(release_setup, damage):
    setup, role = release_setup, 'VIEWER'
    profile = setup['root'] / 'existing-profile'
    protect_profile(profile, role)
    active = stage(setup, profile, role)
    core.atomic_json(profile / 'state.json', {'active': active, 'highest_sequence': active['sequence']})
    before = snapshot_protected(profile)
    active_bytes = core.verify_candidate(active, profile, role).read_bytes()
    release, _ = make_release(setup['root'], '3.15.2')
    publisher.publish_release(setup['repository'], release, setup['key'])
    package = setup['repository'] / load_manifest(setup['repository'], role)['payload']['package']
    if damage == 'missing':
        package.unlink()
    elif damage == 'truncate':
        package.write_bytes(package.read_bytes()[:16])
    else:
        altered = bytearray(package.read_bytes())
        altered[len(altered) // 2] ^= 1
        package.write_bytes(altered)
    with pytest.raises(core.UpdateError):
        stage(setup, profile, role, highest_sequence=active['sequence'], current_version=active['version'])
    assert core.verify_candidate(active, profile, role).read_bytes() == active_bytes
    assert snapshot_protected(profile) == before


def test_partial_repository_debris_is_ignored_and_staging_repeat_is_idempotent(release_setup):
    setup, role = release_setup, 'ADMIN'
    profile = setup['root'] / 'repeat-profile'
    before = protect_profile(profile, role)
    (setup['repository'] / 'channels' / 'ADMIN.json.partial').write_text('{incomplete')
    (setup['repository'] / 'packages' / 'ADMIN' / 'broken.zip.partial').write_bytes(b'PK\x03')
    first = stage(setup, profile, role)
    second = stage(setup, profile, role)
    assert first == second
    assert core.verify_candidate(first, profile, role).read_bytes() == setup['binaries'][role]
    assert snapshot_protected(profile) == before
    assert stage(setup, profile, role, highest_sequence=first['sequence'], current_version=first['version']) is None


def test_new_release_stages_without_state_activation_and_replay_refused(release_setup):
    setup, role = release_setup, 'ADMIN'
    profile = setup['root'] / 'rollback-profile'
    protect_profile(profile, role)
    first_envelope = load_manifest(setup['repository'], role)
    first = stage(setup, profile, role)
    core.atomic_json(profile / 'state.json', {'active': first, 'highest_sequence': first['sequence']})
    before = snapshot_protected(profile)
    release, new_bytes = make_release(setup['root'], '3.15.2')
    publisher.publish_release(setup['repository'], release, setup['key'])
    second = stage(setup, profile, role, highest_sequence=first['sequence'], current_version=first['version'])
    assert second['sequence'] > first['sequence']
    assert second['version'] == '3.15.2'
    assert core.verify_candidate(second, profile, role).read_bytes() == new_bytes[role]
    assert core.verify_candidate(first, profile, role).read_bytes() == setup['binaries'][role]
    assert snapshot_protected(profile) == before
    # A valid old signature does not authorize a remote downgrade.
    core.atomic_json(setup['repository'] / 'channels' / f'{role}.json', first_envelope)
    with pytest.raises(core.UpdateError):
        stage(setup, profile, role, highest_sequence=second['sequence'], current_version=second['version'])
    assert snapshot_protected(profile) == before


@pytest.mark.parametrize('corrupt_hash,extra_entry', [(True, False), (False, True)])
def test_invalid_developer_release_preserves_published_channels(release_setup, corrupt_hash, extra_entry):
    setup = release_setup
    before = repository_payload_snapshot(setup['repository'])
    release, _ = make_release(setup['root'], '3.15.2', corrupt_hash, extra_entry)
    expected_error = (core.UpdateError, publisher.PublishError) if hasattr(publisher, 'PublishError') else core.UpdateError
    with pytest.raises(expected_error):
        publisher.publish_release(setup['repository'], release, setup['key'])
    assert repository_payload_snapshot(setup['repository']) == before


def test_cached_candidate_detects_local_tamper_without_touching_database(release_setup):
    setup, role = release_setup, 'VIEWER'
    profile = setup['root'] / 'local-tamper'
    before = protect_profile(profile, role)
    candidate = stage(setup, profile, role)
    local = core.verify_candidate(candidate, profile, role)
    local.write_bytes(local.read_bytes() + b'tamper')
    with pytest.raises(core.UpdateError):
        core.verify_candidate(candidate, profile, role)
    assert snapshot_protected(profile) == before


def test_relocated_candidate_or_symlink_never_executes_external_path(release_setup):
    setup, role = release_setup, 'ADMIN'
    profile = setup['root'] / 'containment-profile'
    before = protect_profile(profile, role)
    candidate = stage(setup, profile, role)
    outside = setup['root'] / EXECUTABLES[role]
    outside.write_bytes(setup['binaries'][role])
    escaped = dict(candidate, path=str(outside.resolve()))
    with pytest.raises(core.UpdateError):
        core.verify_candidate(escaped, profile, role)
    original_path = Path(candidate['path'])
    original_path.unlink()
    try:
        original_path.symlink_to(outside)
    except OSError:
        pytest.skip('Symlink creation unavailable on test host')
    with pytest.raises(core.UpdateError):
        core.verify_candidate(candidate, profile, role)
    assert snapshot_protected(profile) == before


def test_private_signing_material_is_not_distributed(release_setup):
    setup = release_setup
    # The key file itself must stay outside publication and initial-install ZIPs.
    secret_file = setup['key'].read_bytes()
    assert secret_file
    assert not setup['key'].is_relative_to(setup['repository'])
    for path in setup['repository'].rglob('*'):
        if path.is_file():
            assert path.read_bytes() != secret_file
    for installer in setup['publication']['installers'].values():
        with zipfile.ZipFile(installer) as archive:
            for name in archive.namelist():
                assert archive.read(name) != secret_file
    if sys.platform != 'win32':
        assert setup['key'].stat().st_mode & 0o077 == 0


def test_network_read_failure_mid_copy_cleans_staging_and_preserves_active(release_setup, monkeypatch):
    setup, role = release_setup, 'ADMIN'
    profile = setup['root'] / 'interrupted-transfer'
    protect_profile(profile, role)
    active = stage(setup, profile, role)
    core.atomic_json(profile / 'state.json', {'active': active, 'highest_sequence': active['sequence']})
    before = snapshot_protected(profile)
    release, _ = make_release(setup['root'], '3.15.2')
    publisher.publish_release(setup['repository'], release, setup['key'])
    package = setup['repository'] / load_manifest(setup['repository'], role)['payload']['package']
    real_open = core._open_regular

    class InterruptedRead:
        def __init__(self, stream):
            self.stream, self.read_once = stream, False
        def fileno(self):
            return self.stream.fileno()
        def read(self, count):
            if self.read_once:
                raise OSError('synthetic SMB connection lost')
            self.read_once = True
            return self.stream.read(min(count, 16))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()

    def interrupt_package(path):
        stream = real_open(path)
        return InterruptedRead(stream) if Path(path) == package else stream

    monkeypatch.setattr(core, '_open_regular', interrupt_package)
    with pytest.raises(core.UpdateError):
        stage(setup, profile, role, highest_sequence=active['sequence'], current_version=active['version'])
    assert core.verify_candidate(active, profile, role).read_bytes() == setup['binaries'][role]
    assert snapshot_protected(profile) == before
    assert not list((profile / 'versions').glob('.stage-*'))
