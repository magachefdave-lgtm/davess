import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('clean_builder', ROOT / 'build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def valid_runtime(**changes):
    info = dict(implementation='cpython', version=[3, 14, 8], releaselevel='final',
                platform='win32', machine='AMD64', bits=64, gil_disabled=False,
                sysconfig_platform='win-amd64')
    return info | changes


def fixture_source(tmp_path):
    root = tmp_path / '한글 소스 folder'
    root.mkdir()
    for name in ('backend', 'updater', 'frontend'):
        shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns('__pycache__'))
    for name in builder.SOURCE_FILES:
        if name == 'backend/main.py':
            continue
        shutil.copyfile(ROOT / name, root / name)
    (root / 'build_selftest.py').write_text('value = 1\n')
    return root


def test_runtime_accepts_current_and_patch_update():
    builder.validate_runtime(valid_runtime())
    builder.validate_runtime(valid_runtime(version=[3, 14, 9]))


@pytest.mark.parametrize('changes', [
    {'version': [3, 10, 20]}, {'version': [3, 15, 0]}, {'version': [2, 7, 18]},
    {'releaselevel': 'candidate'}, {'implementation': 'pypy'}, {'bits': 32},
    {'gil_disabled': True}, {'platform': 'linux'}, {'sysconfig_platform': 'win-arm64'},
])
def test_runtime_rejects_incompatible_build_host(changes):
    with pytest.raises(ValueError):
        builder.validate_runtime(valid_runtime(**changes))


def test_source_check_detects_changed_bytes_and_version(tmp_path):
    root = fixture_source(tmp_path)
    manifest = builder.create_source_manifest(root)
    builder.write_json(root / 'SOURCE_MANIFEST.json', manifest)
    assert builder.verify_source_manifest(root)['version'] == (root / 'VERSION').read_text(encoding='utf-8').strip()  # 버전올리기 후에도 맞도록 VERSION 파일 기준
    p = root / 'frontend/dist/index.html'
    p.write_bytes(p.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='frontend/dist/index.html') as caught:
        builder.verify_source_manifest(root)
    assert '버전올리기.cmd' in str(caught.value) and '다릅니다' in str(caught.value)
    # 매니페스트와 다른 버전이어야 불일치가 난다. 실제 버전을 적으면 이 시험이 조용히 죽는다.
    (root / 'VERSION').write_text('9.9.9')
    with pytest.raises(ValueError, match='version|버전'):
        builder.create_source_manifest(root)


def test_source_inventory_rejects_symlink(tmp_path):
    root = fixture_source(tmp_path)
    (root / 'frontend/dist/leak.txt').symlink_to(root / 'VERSION')
    with pytest.raises(ValueError, match='link|연결'):
        builder.create_source_manifest(root)


def test_role_staging_is_allowlisted_and_viewer_has_no_publisher(tmp_path):
    root = fixture_source(tmp_path)
    (root / 'updater/signing_key.json').write_text('do not ship')
    (root / 'backend/smart_duty.db').write_text('do not ship')
    for role in ('ADMIN', 'VIEWER'):
        stage = tmp_path / role
        info = builder.prepare_role(root, stage, role, '3.16.3', 'a' * 64)
        assert (stage / 'onair_backend.py').read_bytes() == (root / 'backend/main.py').read_bytes()
        assert f"BUILD_ROLE = '{role}'" in (stage / 'entrypoint.py').read_text()
        assert (stage / 'updater/publisher.py').exists() == (role == 'ADMIN')
        assert not (stage / 'updater/signing_key.json').exists()
        assert not list(stage.rglob('*.db'))
        assert info['role'] == role and info['build_method'] == 'clean-source-pyinstaller'
        assert (stage / 'frontend/dist/index.html').read_bytes() == (root / 'frontend/dist/index.html').read_bytes()


def test_freeze_command_keeps_onefile_contract_and_role_isolation(tmp_path):
    stage = tmp_path / '한글 stage'
    command = builder.pyinstaller_command('python.exe', stage, tmp_path / 'work', tmp_path / 'dist', 'VIEWER')
    assert command[:3] == ['python.exe', '-m', 'PyInstaller']
    assert '--onefile' in command and '--windowed' in command and '--noupx' in command
    assert command[command.index('--exclude-module') + 1] == 'updater.publisher'
    assert 'onair_backend' in command and 'uvicorn' in command and 'websockets' in command
    assert '--uac-admin' not in command
    assert not any('build_runtime' in c for c in command)
    assert str(stage / 'entrypoint.py') == command[-1]


def test_release_zip_contract_hashes_actual_artifacts(tmp_path):
    out = tmp_path / 'output'
    files = {}
    for role, label in [('ADMIN', 'Admin'), ('VIEWER', 'Viewer')]:
        p = out / label / f'WLR_OnAir_{label}.exe'
        p.parent.mkdir(parents=True)
        p.write_bytes(b'INERT TEST ' + role.encode())
        files[role] = p
    result = builder.package_release(out, '3.16.3', files)
    assert (out / '배포관리.cmd').read_bytes() == (builder.ROOT / '배포관리.cmd').read_bytes()
    import zipfile
    with zipfile.ZipFile(result) as z:
        metadata = json.loads(z.read('release.json'))
        assert set(z.namelist()) == {'release.json', 'ADMIN/WLR_OnAir_Admin.exe', 'VIEWER/WLR_OnAir_Viewer.exe'}
        for role in files:
            item = metadata['files'][role]
            assert item['sha256'] == builder.sha256_file(files[role])
            assert z.read(item['path']) == files[role].read_bytes()


def test_entry_selftest_does_not_start_setup_or_backend(monkeypatch, tmp_path):
    entry_spec = importlib.util.spec_from_file_location('test_entry', ROOT / 'entrypoint.py')
    entry = importlib.util.module_from_spec(entry_spec)
    entry_spec.loader.exec_module(entry)
    import types
    calls = []
    monkeypatch.setitem(sys.modules, 'build_selftest', types.SimpleNamespace(run=lambda role, version, path: calls.append((role, version, path)) or 0))
    target = str(tmp_path / 'result.json')
    monkeypatch.setattr(sys, 'argv', ['app.exe', '--onair-self-test', target])
    assert entry.main() == 0
    assert calls == [('ADMIN', (ROOT / 'VERSION').read_text(encoding='utf-8').strip(), target)]


def test_missing_build_inputs_and_private_static_assets_rejected(tmp_path):
    root = fixture_source(tmp_path)
    (root / 'build_selftest.py').unlink()
    with pytest.raises(ValueError, match='누락|missing'):
        builder.create_source_manifest(root)
    (root / 'build_selftest.py').write_text('value=1')
    (root / 'frontend/dist/smart_duty.db').write_text('private')
    with pytest.raises(ValueError, match='private|운영|개인'):
        builder.create_source_manifest(root)


def test_freeze_requires_windows_and_never_creates_outputs_on_linux(tmp_path):
    if sys.platform == 'win32':
        pytest.skip('Linux guard test')
    out = tmp_path / 'not-created'
    with pytest.raises(ValueError, match='Windows'):
        builder.build(ROOT, out)
    assert not out.exists()


def test_source_manifest_distinguishes_build_revision(tmp_path):
    root = fixture_source(tmp_path)
    info = builder.create_source_manifest(root)
    assert info['build_revision'] == 'python311-314-source-r4'
    stage_info = builder.prepare_role(root, tmp_path / 'staged', 'ADMIN', '3.16.3', info['source_fingerprint'])
    assert stage_info['build_revision'] == info['build_revision']


def test_dynamic_asyncio_backend_is_explicitly_collected(tmp_path):
    command = builder.pyinstaller_command('python.exe', tmp_path, tmp_path / 'w', tmp_path / 'o', 'ADMIN')
    assert 'anyio._backends._asyncio' in command


def test_static_directory_links_are_rejected_instead_of_silently_omitted(tmp_path):
    root = fixture_source(tmp_path)
    other = tmp_path / 'other assets'
    other.mkdir()
    (root / 'frontend/dist/linked').symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match='연결|link'):
        builder.create_source_manifest(root)


@pytest.mark.parametrize('minor', [11, 12, 13, 14])
@pytest.mark.parametrize('patch', [0, 6, 8])
def test_builder_accepts_each_supported_stable_series_and_patch(minor, patch):
    assert builder.validate_runtime(valid_runtime(version=[3, minor, patch]))['version'] == [3, minor, patch]


def test_role_stages_shared_runtime_policy_and_build_identity(tmp_path):
    stage = tmp_path / 'staged'
    info = builder.prepare_role(ROOT, stage, 'ADMIN', '3.16.3', 'a' * 64)
    assert (stage / 'build_preflight.py').read_bytes() == (ROOT / 'build_preflight.py').read_bytes()
    assert info['python_runtime'] == builder.runtime_info()


def test_each_abi_lock_is_in_the_source_fingerprint():
    manifest = builder.create_source_manifest(ROOT)
    for minor in (11, 12, 13, 14):
        assert f'requirements-build-py3{minor}.txt' in manifest['files']
    assert 'build_preflight.py' in manifest['files']


@pytest.mark.parametrize('minor', [11, 12, 13, 14])
def test_archive_requires_exact_selected_python_dll(minor, monkeypatch, tmp_path):
    import types
    info = {'python_runtime': valid_runtime(version=[3, minor, 6]), 'assets': {}}
    toc = {'frontend/dist/index.html': None, 'onair_build_info.json': None,
           f'python3{minor}.dll': None, 'PYZ.pyz': None}
    names = ('onair_backend', 'build_preflight', 'build_selftest', 'updater.core',
             'updater.launcher', 'updater.profiles', 'updater.setup_ui', 'updater.publisher',
             'updater.manual_update', 'updater.process_identity',
             'uvicorn', 'websockets', 'backend', 'backend.request_access_schema',
             'backend.request_access_store', 'backend.request_access_local',
             'backend.request_recovery', 'backend.request_access_api',
             'backend.dinner_schema', 'backend.dinner_service', 'backend.dinner_api')
    archive = types.SimpleNamespace(toc=toc,
        extract=lambda name: json.dumps(info).encode(),
        open_embedded_archive=lambda name: types.SimpleNamespace(toc=dict.fromkeys(names)))
    monkeypatch.setitem(sys.modules, 'PyInstaller', types.ModuleType('PyInstaller'))
    monkeypatch.setitem(sys.modules, 'PyInstaller.archive', types.ModuleType('PyInstaller.archive'))
    readers = types.ModuleType('PyInstaller.archive.readers')
    readers.CArchiveReader = lambda filename: archive
    monkeypatch.setitem(sys.modules, 'PyInstaller.archive.readers', readers)
    class FakePE:
        FILE_HEADER = types.SimpleNamespace(Machine=0x8664)
        OPTIONAL_HEADER = types.SimpleNamespace(Subsystem=2)
        FileInfo = [[types.SimpleNamespace(StringTable=[types.SimpleNamespace(entries={
            b'FileVersion': b'3.16.3', b'ProductVersion': b'3.16.3'})])]]
        lang = types.SimpleNamespace(data=types.SimpleNamespace(struct=types.SimpleNamespace(OffsetToData=0, Size=1)))
        DIRECTORY_ENTRY_RESOURCE = types.SimpleNamespace(entries=[types.SimpleNamespace(id=24,
            directory=types.SimpleNamespace(entries=[types.SimpleNamespace(directory=types.SimpleNamespace(entries=[lang]))]))])
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get_data(self, *args): return b'<requestedExecutionLevel level="asInvoker"/>'
    pefile = types.ModuleType('pefile')
    pefile.PE = lambda filename: FakePE()
    monkeypatch.setitem(sys.modules, 'pefile', pefile)
    assert builder.verify_archive(tmp_path / 'inert.exe', 'ADMIN', '3.16.3', info)['archive_verified']
    del toc[f'python3{minor}.dll']
    toc['python310.dll'] = None
    with pytest.raises(ValueError, match=f'python3{minor}.dll'):
        builder.verify_archive(tmp_path / 'inert.exe', 'ADMIN', '3.16.3', info)
