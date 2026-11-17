"""Security and transactional tests for the public updater.core contract."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import stat
import zipfile

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from updater import core


def keypair():
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    return key, public


def envelope(payload, key):
    return {'payload': payload, 'signature': base64.b64encode(key.sign(core.canonical_json(payload))).decode()}


def release(tmp_path, *, role='ADMIN', version='3.15.1', sequence=1, entries=None):
    repository = tmp_path / 'repository'
    profile = tmp_path / 'profile'
    repository.mkdir(exist_ok=True)
    profile.mkdir(exist_ok=True)
    key, public = keypair()
    exe = core.EXECUTABLES[role]
    package = tmp_path / 'package.zip'
    with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries or [(exe, b'MZ' + b'approved-build' * 20)]:
            archive.writestr(name, data)
    digest = core.sha256_file(package)
    relative = f'packages/{role}/{version}-{digest[:12]}.zip'
    target = repository / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(package.read_bytes())
    payload = dict(schema=1, product='WLR_OnAir', role=role, version=version,
                   sequence=sequence, package=relative, sha256=digest,
                   size=target.stat().st_size, executable=exe,
                   min_launcher='1.0.0', data_schema=1)
    manifest = repository / 'channels' / f'{role}.json'
    core.atomic_json(manifest, envelope(payload, key))
    return repository, profile, key, public, payload, target, manifest


def update(args, **kwargs):
    repository, profile, _, public, payload, _, _ = args
    return core.stage_update(repository, profile, payload['role'], public, **kwargs)


def test_stage_local_immutable_candidate_and_no_state_or_config_writes(tmp_path):
    args = release(tmp_path)
    _, profile, *_ = args
    (profile / 'config').mkdir()
    (profile / 'config' / 'database.sqlite').write_bytes(b'never touch data')
    (profile / 'state.json').write_text('{"old":true}')
    candidate = update(args)
    actual = core.verify_candidate(candidate, profile, 'ADMIN')
    assert actual.is_file() and actual.is_absolute()
    assert actual.name == 'WLR_OnAir_Admin.exe'
    assert (profile / 'state.json').read_text() == '{"old":true}'
    assert (profile / 'config' / 'database.sqlite').read_bytes() == b'never touch data'
    assert update(args) == candidate
    assert len(list((profile / 'versions').iterdir())) == 1


def test_manifest_signature_and_trust_pin_are_required(tmp_path):
    args = release(tmp_path)
    repository, profile, _, public, payload, _, manifest = args
    wrong_key, wrong_public = keypair()
    for supplied in [wrong_public, 'bad-key']:
        with pytest.raises(core.UpdateError):
            core.stage_update(repository, profile, 'ADMIN', supplied)
    altered = envelope(payload, wrong_key)
    core.atomic_json(manifest, altered)
    with pytest.raises(core.UpdateError):
        update(args)
    assert not (profile / 'versions').exists()


def test_correct_signature_does_not_override_role_or_protocol_checks(tmp_path):
    args = release(tmp_path)
    _, _, key, public, payload, *_ = args
    for changes in [
        {'role': 'VIEWER'}, {'schema': 2}, {'product': 'Different'}, {'sequence': True},
        {'sequence': 0}, {'size': 0}, {'size': core.MAX_PACKAGE_BYTES + 1},
        {'min_launcher': '2.0.0'}, {'data_schema': 2}, {'version': '3.15.01'},
        {'package': '../payload.zip'}, {'executable': 'WLR_OnAir_Viewer.exe'},
        {'extra': 'untrusted'}, {'sha256': 'A' * 64},
    ]:
        with pytest.raises(core.UpdateError):
            core.verify_envelope(envelope(payload | changes, key), public, 'ADMIN')
    with pytest.raises(core.UpdateError):
        core.verify_envelope(envelope(payload, key), public, 'VIEWER')


def test_sequence_and_version_prevent_replay_and_downgrade(tmp_path):
    args = release(tmp_path, sequence=3)
    assert update(args, highest_sequence=3) is None
    with pytest.raises(core.UpdateError):
        update(args, highest_sequence=4)
    assert update(args, current_version='3.15.1') is None
    for version in ['3.15.2', '3.16.0']:
        with pytest.raises(core.UpdateError):
            update(args, current_version=version)


def test_tampered_truncated_and_oversize_packages_never_install(tmp_path):
    args = release(tmp_path)
    package = args[-2]
    original = package.read_bytes()
    for data in [original[:-5], b'X' + original[1:], original + b'extra']:
        package.write_bytes(data)
        with pytest.raises(core.UpdateError):
            update(args)
        versions = args[1] / 'versions'
        assert not versions.exists() or not list(versions.iterdir())


def test_offline_storage_preserves_existing_candidate(tmp_path):
    args = release(tmp_path)
    candidate = update(args)
    args[-1].unlink()
    with pytest.raises(core.UpdateError):
        update(args)
    assert core.verify_candidate(candidate, args[1], 'ADMIN').is_file()


@pytest.mark.parametrize('names', [
    ['../WLR_OnAir_Admin.exe'], ['/WLR_OnAir_Admin.exe'], ['dir/WLR_OnAir_Admin.exe'],
    ['dir\\WLR_OnAir_Admin.exe'], ['WLR_OnAir_Viewer.exe'],
    ['WLR_OnAir_Admin.exe', 'extra.txt'], ['WLR_OnAir_Admin.exe', 'WLR_OnAir_Admin.exe'],
])
def test_archive_member_attacks_rejected(tmp_path, names):
    args = release(tmp_path, entries=[(name, b'MZuntrusted') for name in names])
    with pytest.raises(core.UpdateError):
        update(args)
    assert not list(args[1].rglob('*.exe'))


def test_archive_symlink_and_bomb_metadata_rejected(tmp_path):
    info = zipfile.ZipInfo('WLR_OnAir_Admin.exe')
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    args = release(tmp_path, entries=[(info, b'/untrusted/target')])
    with pytest.raises(core.UpdateError):
        update(args)


def test_declared_extraction_size_is_bounded(tmp_path, monkeypatch):
    args = release(tmp_path, entries=[('WLR_OnAir_Admin.exe', b'A' * 4096)])
    # Archive is small, but its uncompressed output is larger than the test cap.
    monkeypatch.setattr(core, 'MAX_EXECUTABLE_BYTES', 1024)
    with pytest.raises(core.UpdateError):
        update(args)


def test_strict_json_bounds_duplicate_keys_and_nonfinite(tmp_path):
    p = tmp_path / 'manifest.json'
    for data in [b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'{' + b' ' * 100]:
        p.write_bytes(data)
        with pytest.raises(core.UpdateError):
            core.load_json(p, max_bytes=64)
    with pytest.raises(core.UpdateError):
        core.canonical_json({'x': float('nan')})
    core.atomic_json(p, {'한국어': '보존', 'a': 1})
    assert core.load_json(p) == {'한국어': '보존', 'a': 1}
    assert core.canonical_json({'b': 2, 'a': '한'}) == '{"a":"한","b":2}'.encode()


def test_bootstrap_role_absolute_repository_and_key_validation(tmp_path):
    _, public = keypair()
    value = dict(schema=1, product='WLR_OnAir', role='ADMIN', repository=str(tmp_path), public_key=public)
    assert core.validate_bootstrap(value, 'ADMIN') == value
    for changes in [{'role': 'VIEWER'}, {'repository': 'relative/repository'}, {'public_key': 'wrong'}, {'schema': True}]:
        with pytest.raises(core.UpdateError):
            core.validate_bootstrap(value | changes, 'ADMIN')


def test_candidate_tampering_wrong_role_and_symlink_rejected(tmp_path):
    args = release(tmp_path)
    candidate = update(args)
    path = Path(candidate['path'])
    with pytest.raises(core.UpdateError):
        core.verify_candidate(candidate, args[1], 'VIEWER')
    with pytest.raises(core.UpdateError):
        core.verify_candidate(candidate | {'path': str(tmp_path / path.name)}, args[1], 'ADMIN')
    outside = tmp_path / 'outside.exe'
    outside.write_bytes(path.read_bytes())
    path.unlink()
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip('symlink creation unavailable on this platform')
    with pytest.raises(core.UpdateError):
        core.verify_candidate(candidate, args[1], 'ADMIN')
    path.unlink()
    path.write_bytes(b'MZtampered')
    with pytest.raises(core.UpdateError):
        core.verify_candidate(candidate, args[1], 'ADMIN')


def test_bundled_sequence_zero_candidate_allowed(tmp_path):
    args = release(tmp_path)
    candidate = update(args)
    assert core.verify_candidate(candidate | {'sequence': 0}, args[1], 'ADMIN')


def test_profile_lock_is_nonblocking_and_releases(tmp_path):
    profile = tmp_path / 'profile'
    with core.profile_lock(profile):
        with pytest.raises(core.UpdateError):
            with core.profile_lock(profile):
                pytest.fail('second owner must not acquire lock')
    with core.profile_lock(profile):
        pass


def test_repository_disconnect_mid_copy_keeps_old_binary_and_removes_stage(tmp_path, monkeypatch):
    args = release(tmp_path)
    candidate = update(args)
    original_open = core._open_regular
    package_path = args[-2]

    class DisconnectedStream:
        def __init__(self, stream):
            self.stream = stream
            self.reads = 0
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            self.stream.close()
        def fileno(self):
            return self.stream.fileno()
        def read(self, size):
            self.reads += 1
            if self.reads > 1:
                raise OSError('simulated SMB disconnect')
            return self.stream.read(min(size, 16))

    def interrupted_open(path):
        stream = original_open(path)
        return DisconnectedStream(stream) if Path(path) == package_path else stream

    monkeypatch.setattr(core, '_open_regular', interrupted_open)
    with pytest.raises(core.UpdateError):
        update(args)
    assert core.verify_candidate(candidate, args[1], 'ADMIN').is_file()
    assert len(list((args[1] / 'versions').iterdir())) == 1


def test_install_rename_failure_leaves_no_partial_candidate(tmp_path, monkeypatch):
    args = release(tmp_path)
    original_rename = Path.rename

    def blocked_rename(path, target):
        if path.name.startswith('.stage-'):
            raise OSError('simulated local filesystem failure')
        return original_rename(path, target)

    monkeypatch.setattr(Path, 'rename', blocked_rename)
    with pytest.raises(core.UpdateError):
        update(args)
    assert list((args[1] / 'versions').iterdir()) == []


def test_payload_role_channel_swap_does_not_install(tmp_path):
    args = release(tmp_path, role='VIEWER')
    repo, profile, _, public, _, _, manifest = args
    (repo / 'channels' / 'ADMIN.json').write_bytes(manifest.read_bytes())
    with pytest.raises(core.UpdateError):
        core.stage_update(repo, profile, 'ADMIN', public)
    assert not (profile / 'versions').exists()
