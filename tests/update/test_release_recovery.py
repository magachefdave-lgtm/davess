"""Fixed-behavior regressions; WLR_RELEASE_SOURCE selects immutable baseline."""
import base64
import os
from pathlib import Path
import sqlite3
import sys

import pytest

SOURCE = Path(os.environ.get('WLR_RELEASE_SOURCE', str(Path(__file__).resolve().parents[2])))
sys.path.insert(0, str(SOURCE))
from updater import core, launcher, profiles


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setenv('WLR_PROFILE_ROOT', str(tmp_path / 'profiles'))
    config = profiles.prepare_profile('ADMIN', allow_new=True)
    profile = config.parent
    exe = tmp_path / 'installed' / 'WLR_OnAir_Admin.exe'
    exe.parent.mkdir()
    exe.write_bytes(b'MZ synthetic ADMIN')
    bootstrap = {'schema': 1, 'product': core.PRODUCT, 'role': 'ADMIN',
                 'repository': str(tmp_path / 'old-repository'),
                 'public_key': base64.b64encode(bytes(range(32))).decode()}
    core.atomic_json(exe.parent / 'onair_update.json', bootstrap)
    launcher._pin_bootstrap(profile, exe, 'ADMIN')
    active = launcher._seed_bundled(exe, profile, 'ADMIN', '3.18.4')
    state = launcher._default_state('ADMIN')
    state.update(active=active, highest_version='3.18.4', highest_sequence=42)
    launcher._save_state(profile, state)
    monkeypatch.setattr(launcher, '_read_system_info', lambda *_: None)
    return {'profile': profile, 'config': config, 'exe': exe, 'active': active, 'state': state,
            'bootstrap': bootstrap}


class HealthyChild:
    pid = 999999999
    stopped = False
    def poll(self): return 0 if self.stopped else None
    def terminate(self): self.stopped = True
    def wait(self, timeout=None): return 0


def test_persisted_active_survives_persistent_state_save_failure_and_keeps_lock(site, monkeypatch):
    profile = site['profile']
    before = (profile / 'state.json').read_bytes()
    child = HealthyChild()
    monkeypatch.setattr(launcher, '_spawn', lambda *_: (child, profile / 'health' / 'synthetic.json'))
    monkeypatch.setattr(core, 'stage_update', lambda *_args, **_kwargs: None)
    actual_atomic = core.atomic_json
    def full_disk(path, data):
        if Path(path).name == 'state.json':
            raise core.UpdateError('synthetic persistent ENOSPC')
        return actual_atomic(path, data)
    monkeypatch.setattr(core, 'atomic_json', full_disk)
    def supervised(process, _logger):
        assert process is child and not child.stopped
        with pytest.raises(core.UpdateError):
            with core.profile_lock(profile):
                pytest.fail('supervisor lock released before app exit')
        return 17
    monkeypatch.setattr(launcher, '_wait_for_exit', supervised)
    assert launcher.run('ADMIN', site['exe'], version='3.18.4') == 17
    assert not child.stopped
    assert (profile / 'state.json').read_bytes() == before
    assert core.load_json(profile / launcher.STATUS_FILE)['result'] == 'save_failed'
    with core.profile_lock(profile):
        pass


def test_first_setup_ini_failure_removes_only_owned_db_and_allows_retry(tmp_path, monkeypatch):
    monkeypatch.setenv('WLR_PROFILE_ROOT', str(tmp_path / 'profiles'))
    original = profiles._publish_new_file
    def fail_ini(target, writer):
        if Path(target).suffix == '.ini':
            raise core.UpdateError('synthetic transient INI publication error')
        return original(target, writer)
    monkeypatch.setattr(profiles, '_publish_new_file', fail_ini)
    with pytest.raises(core.UpdateError, match='synthetic transient'):
        profiles.prepare_profile('ADMIN', allow_new=True)
    config = profiles.profile_dir('ADMIN') / 'config'
    assert not (config / profiles.DB_NAME).exists()
    assert not (config / 'config_admin.ini').exists()
    monkeypatch.setattr(profiles, '_publish_new_file', original)
    assert profiles.prepare_profile('ADMIN', allow_new=True) == config
    with sqlite3.connect(config / profiles.DB_NAME) as db:
        assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)


def replacement(site):
    new = dict(site['bootstrap'], repository=str(site['exe'].parent / 'new-repository'),
               public_key=base64.b64encode(bytes(reversed(range(32)))).decode())
    core.atomic_json(site['exe'].parent / 'onair_update.json', new)
    return new


def test_explicit_verified_reenrollment_preserves_data_archives_old_trust_and_version_floor(site):
    profile = site['profile']
    old_pin, old_state = (profile / 'onair_update.json').read_bytes(), (profile / 'state.json').read_bytes()
    ini = site['config'] / 'config_admin.ini'
    db = site['config'] / profiles.DB_NAME
    before = ini.read_bytes(), db.read_bytes()
    (profile / launcher.RESET_REQUEST).write_bytes(b'old-request')
    core.atomic_json(profile / launcher.STATUS_FILE, {'old': True})
    new = replacement(site)
    with pytest.raises(core.UpdateError):
        launcher._pin_bootstrap(profile, site['exe'], 'ADMIN')
    archive = launcher.reenroll_update_trust('ADMIN', site['exe'], version='3.18.5',
                      fingerprint=core.public_key_fingerprint(new['public_key']), confirm=True)
    assert (ini.read_bytes(), db.read_bytes()) == before
    assert (archive / 'onair_update.json').read_bytes() == old_pin
    assert (archive / 'state.json').read_bytes() == old_state
    assert (archive / launcher.RESET_REQUEST).read_bytes() == b'old-request'
    assert not (profile / launcher.RESET_REQUEST).exists()
    state = launcher._load_state(profile, 'ADMIN')
    assert state['highest_sequence'] == 0
    assert state['highest_version'] == '3.18.5'
    assert state['pending'] is None
    assert state['active']['sha256'] == site['active']['sha256']
    assert state['active']['sequence'] == 0
    assert launcher._pin_bootstrap(profile, site['exe'], 'ADMIN') == new
    assert not (profile / launcher.TRUST_RECOVERY_REQUEST).exists()


@pytest.mark.parametrize('new_candidate', ['bundled', 'pending'])
def test_unpersisted_activation_save_failure_still_stops_owned_child(site, monkeypatch, new_candidate):
    profile = site['profile']
    child = HealthyChild()
    monkeypatch.setattr(launcher, '_spawn', lambda *_: (child, profile / 'health' / 'synthetic.json'))
    if new_candidate == 'pending':
        update = dict(site['active'], version='3.18.6', sequence=43)
        state = dict(site['state'], pending=update, highest_sequence=43, highest_version='3.18.6')
        launcher._save_state(profile, state)
    before = (profile / 'state.json').read_bytes()
    monkeypatch.setattr(core, 'stage_update', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, '_save_state', lambda *_: (_ for _ in ()).throw(core.UpdateError('disk full')))
    with pytest.raises(core.UpdateError, match='disk full'):
        launcher.run('ADMIN', site['exe'], version='3.18.5' if new_candidate == 'bundled' else '3.18.4')
    assert child.stopped
    assert (profile / 'state.json').read_bytes() == before
    with core.profile_lock(profile):
        pass


@pytest.mark.parametrize('mode', ['existing', 'modified', 'replaced', 'published', 'cancel'])
def test_failed_setup_preserves_unowned_or_adopted_data(tmp_path, monkeypatch, mode):
    monkeypatch.setenv('WLR_PROFILE_ROOT', str(tmp_path / 'profiles'))
    config = profiles.profile_dir('ADMIN') / 'config'
    db = config / profiles.DB_NAME
    if mode == 'existing':
        config.mkdir(parents=True)
        db.write_bytes(b'preexisting-data')
    original = profiles._publish_new_file
    def fail_ini(target, writer):
        if Path(target).suffix != '.ini':
            return original(target, writer)
        if mode == 'modified':
            with sqlite3.connect(db) as connection:
                connection.execute('create table sentinel(value text)')
        elif mode == 'replaced':
            saved = db.with_suffix('.original')
            db.rename(saved)
            db.write_bytes(b'replacement-data')
        elif mode == 'published':
            original(target, writer)
        if mode == 'cancel':
            raise KeyboardInterrupt('cancel setup')
        raise core.UpdateError('INI failed')
    monkeypatch.setattr(profiles, '_publish_new_file', fail_ini)
    with pytest.raises((core.UpdateError, KeyboardInterrupt)):
        profiles.prepare_profile('ADMIN', allow_new=True)
    if mode == 'cancel':
        assert not db.exists()
    else:
        assert db.exists()
        if mode == 'existing': assert db.read_bytes() == b'preexisting-data'
        if mode == 'replaced': assert db.read_bytes() == b'replacement-data'
        if mode == 'modified':
            with sqlite3.connect(db) as connection:
                assert connection.execute("select name from sqlite_master where name='sentinel'").fetchone()


def test_setup_failure_serializes_concurrent_setup_and_retry(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    monkeypatch.setenv('WLR_PROFILE_ROOT', str(tmp_path / 'profiles'))
    reached, proceed = Event(), Event()
    original = profiles._publish_new_file
    def pause_ini(target, writer):
        if Path(target).suffix == '.ini':
            reached.set()
            assert proceed.wait(5)
            raise core.UpdateError('first INI failure')
        return original(target, writer)
    monkeypatch.setattr(profiles, '_publish_new_file', pause_ini)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(profiles.prepare_profile, 'ADMIN', allow_new=True)
        assert reached.wait(5)
        try:
            with pytest.raises(core.UpdateError):
                profiles.prepare_profile('ADMIN', allow_new=True)
        finally:
            proceed.set()
        with pytest.raises(core.UpdateError, match='first INI failure'):
            first.result(timeout=5)
    monkeypatch.setattr(profiles, '_publish_new_file', original)
    config = profiles.prepare_profile('ADMIN', allow_new=True)
    assert (config / 'config_admin.ini').is_file()


@pytest.mark.parametrize('mode', ['wrong', 'unconfirmed', 'locked', 'running', 'older', 'invalid-role'])
def test_reenrollment_rejects_without_changing_trust_or_data(site, monkeypatch, mode):
    new = replacement(site)
    profile = site['profile']
    fingerprint = core.public_key_fingerprint(new['public_key'])
    if mode == 'running':
        monkeypatch.setattr(launcher, '_read_system_info', lambda *_: {'mode': 'ADMIN', 'version': '3.18.4'})
    before = {p.relative_to(profile): p.read_bytes() for p in profile.rglob('*') if p.is_file()}
    def attempt():
        return launcher.reenroll_update_trust('VIEWER' if mode == 'invalid-role' else 'ADMIN', site['exe'],
                version='3.18.3' if mode == 'older' else '3.18.5',
                fingerprint='0000-0000-0000-0000' if mode == 'wrong' else fingerprint,
                confirm=mode != 'unconfirmed')
    if mode == 'locked':
        with core.profile_lock(profile):
            with pytest.raises(core.UpdateError): attempt()
    else:
        with pytest.raises(core.UpdateError): attempt()
    after = {p.relative_to(profile): p.read_bytes() for p in profile.rglob('*') if p.is_file()}
    # Acquiring the shared supervisor lock may create only its stable lock file.
    before.pop(Path('launcher.lock'), None)
    after.pop(Path('launcher.lock'), None)
    assert after == before


@pytest.mark.parametrize('fail_name', ['state.json', 'onair_update.json', 'reset_blocked.request'])
def test_interrupted_recovery_blocks_launcher_and_retry_completes_from_archive(site, monkeypatch, fail_name):
    new = replacement(site)
    profile = site['profile']
    old_pin, old_state = (profile / 'onair_update.json').read_bytes(), (profile / 'state.json').read_bytes()
    request = profile / launcher.RESET_REQUEST
    request.write_bytes(b'original-reset-request')
    real_atomic, real_unlink = core.atomic_json, Path.unlink
    if fail_name == 'reset_blocked.request':
        def fail_unlink(path, *args, **kwargs):
            if path == request: raise PermissionError('synthetic locked reset request')
            return real_unlink(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'unlink', fail_unlink)
    else:
        def fail_write(path, value):
            if Path(path) == profile / fail_name:
                raise core.UpdateError('synthetic write failure')
            return real_atomic(path, value)
        monkeypatch.setattr(core, 'atomic_json', fail_write)
    with pytest.raises((core.UpdateError, OSError)):
        launcher.reenroll_update_trust('ADMIN', site['exe'], version='3.18.5',
                fingerprint=core.public_key_fingerprint(new['public_key']), confirm=True)
    assert (profile / launcher.TRUST_RECOVERY_REQUEST).exists()
    with pytest.raises(core.UpdateError, match='복구'):
        launcher._pin_bootstrap(profile, site['exe'], 'ADMIN')
    monkeypatch.setattr(core, 'atomic_json', real_atomic)
    monkeypatch.setattr(Path, 'unlink', real_unlink)
    archive = launcher.reenroll_update_trust('ADMIN', site['exe'], version='3.18.5',
                fingerprint=core.public_key_fingerprint(new['public_key']), confirm=True)
    assert (archive / 'onair_update.json').read_bytes() == old_pin
    assert (archive / 'state.json').read_bytes() == old_state
    assert launcher._pin_bootstrap(profile, site['exe'], 'ADMIN') == new
    assert not (profile / launcher.TRUST_RECOVERY_REQUEST).exists()


@pytest.mark.parametrize('role', ['ADMIN', 'VIEWER'])
def test_frozen_entrypoint_recovery_uses_build_role_and_installed_exe(tmp_path, monkeypatch, role):
    import importlib.util
    spec = importlib.util.spec_from_file_location('recovery_entrypoint', SOURCE / 'entrypoint.py')
    entrypoint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entrypoint)
    exe = tmp_path / core.EXECUTABLES[role]
    monkeypatch.setattr(entrypoint, 'BUILD_ROLE', role)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(exe))
    monkeypatch.setattr(sys, 'argv', [str(exe), '--recover-update-trust', '--fingerprint', '1234-5678-9ABC-DEF0', '--confirm'])
    monkeypatch.setenv('SMART_DUTY_MODE', 'OTHER')
    calls = []
    def recover(*args, **kwargs):
        calls.append((args, kwargs))
        return tmp_path / 'archive'
    monkeypatch.setattr(launcher, 'reenroll_update_trust', recover, raising=False)
    assert entrypoint.main() == 0
    assert calls == [((role, exe), {'version': entrypoint.APP_VERSION, 'fingerprint': '1234-5678-9ABC-DEF0', 'confirm': True})]


@pytest.mark.parametrize('args', [[], ['--fingerprint', '1234'], ['--fingerprint', '1234', '--confirm', '--role', 'VIEWER'], ['--fingerprint', '1234', '--confirm', '--publish'], ['--fingerprint', '1234', '--confirm', '--onair-app']])
def test_recovery_entrypoint_requires_exact_explicit_arguments(tmp_path, monkeypatch, args):
    monkeypatch.setenv('WLR_PROFILE_ROOT', str(tmp_path / 'profiles'))
    import importlib.util
    spec = importlib.util.spec_from_file_location('recovery_entrypoint', SOURCE / 'entrypoint.py')
    entrypoint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entrypoint)
    monkeypatch.setattr(sys, 'argv', ['program.exe', '--recover-update-trust', *args])
    monkeypatch.setenv('SMART_DUTY_MODE', 'ADMIN')
    with pytest.raises(RuntimeError):
        entrypoint.main()


def test_new_repository_sequence_starts_at_one_but_signature_and_version_guards_remain(site, tmp_path):
    import zipfile
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    key = Ed25519PrivateKey.generate()  # Test-only ephemeral key; never an operational key.
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    new = dict(site['bootstrap'], repository=str(tmp_path / 'new-repository'), public_key=public)
    core.atomic_json(site['exe'].parent / 'onair_update.json', new)
    launcher.reenroll_update_trust('ADMIN', site['exe'], version='3.18.5',
                fingerprint=core.public_key_fingerprint(public), confirm=True)
    profile = site['profile']
    state = launcher._load_state(profile, 'ADMIN')
    repo = Path(new['repository'])
    package = tmp_path / 'synthetic.zip'
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('WLR_OnAir_Admin.exe', b'MZ inert synthetic new-key release')
    digest = core.sha256_file(package)
    def publish(version):
        relative = f'packages/ADMIN/{version}-{digest[:12]}.zip'
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(package.read_bytes())
        payload = dict(schema=1, product=core.PRODUCT, role='ADMIN', version=version, sequence=1,
                       package=relative, sha256=digest, size=destination.stat().st_size,
                       executable='WLR_OnAir_Admin.exe', min_launcher='1.1.0', data_schema=1)
        envelope = {'payload': payload, 'signature': base64.b64encode(key.sign(core.canonical_json(payload))).decode()}
        core.atomic_json(repo / 'channels' / 'ADMIN.json', envelope)
    publish('3.18.4')
    with pytest.raises(core.UpdateError, match='오래된'):
        core.stage_update(repo, profile, 'ADMIN', public, highest_sequence=state['highest_sequence'], current_version=state['highest_version'])
    publish('3.18.6')
    with pytest.raises(core.UpdateError, match='서명'):
        core.stage_update(repo, profile, 'ADMIN', site['bootstrap']['public_key'], highest_sequence=0, current_version=state['highest_version'])
    update = core.stage_update(repo, profile, 'ADMIN', public, highest_sequence=0, current_version=state['highest_version'])
    assert update['sequence'] == 1 and update['version'] == '3.18.6'
    assert core.verify_candidate(update, profile, 'ADMIN').read_bytes() == b'MZ inert synthetic new-key release'


def test_archive_failure_leaves_original_trust_and_state_intact(site, monkeypatch):
    new = replacement(site)
    profile = site['profile']
    before = (profile / 'onair_update.json').read_bytes(), (profile / 'state.json').read_bytes()
    real = profiles._publish_new_file
    def fail_archive(path, writer):
        if 'recovery' in Path(path).parts: raise core.UpdateError('archive unavailable')
        return real(path, writer)
    monkeypatch.setattr(profiles, '_publish_new_file', fail_archive)
    with pytest.raises(core.UpdateError, match='archive unavailable'):
        launcher.reenroll_update_trust('ADMIN', site['exe'], version='3.18.5',
                    fingerprint=core.public_key_fingerprint(new['public_key']), confirm=True)
    assert ((profile / 'onair_update.json').read_bytes(), (profile / 'state.json').read_bytes()) == before
    assert not (profile / launcher.TRUST_RECOVERY_REQUEST).exists()


@pytest.mark.parametrize('role', ['ADMIN', 'VIEWER'])
def test_recovery_handles_established_pin_without_state_and_preserves_existing_db(tmp_path, monkeypatch, role):
    monkeypatch.setenv('WLR_PROFILE_ROOT', str(tmp_path / 'profiles'))
    data = tmp_path / 'shared'
    data.mkdir()
    with sqlite3.connect(data / profiles.DB_NAME) as connection:
        connection.execute('create table sentinel(value text)')
        connection.execute("insert into sentinel values ('preserved')")
    config = profiles.prepare_profile(role, shared_dir=data)
    exe = tmp_path / 'new-installer' / core.EXECUTABLES[role]
    exe.parent.mkdir()
    exe.write_bytes(b'MZ synthetic')
    old = dict(schema=1, product=core.PRODUCT, role=role, repository=str(tmp_path / 'old'),
               public_key=base64.b64encode(bytes(range(32))).decode())
    new = dict(old, repository=str(tmp_path / 'new'), public_key=base64.b64encode(bytes(reversed(range(32)))).decode())
    core.atomic_json(config.parent / 'onair_update.json', old)
    core.atomic_json(exe.parent / 'onair_update.json', new)
    monkeypatch.setattr(launcher, '_read_system_info', lambda *_: None)
    before = (data / profiles.DB_NAME).read_bytes(), (config / f'config_{role.lower()}.ini').read_bytes()
    launcher.reenroll_update_trust(role, exe, version='3.18.5', fingerprint=core.public_key_fingerprint(new['public_key']), confirm=True)
    assert ((data / profiles.DB_NAME).read_bytes(), (config / f'config_{role.lower()}.ini').read_bytes()) == before
    assert launcher._pin_bootstrap(config.parent, exe, role) == new
