"""Missing package marker must not stop supported builds; integrity stays enforced."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
MARKER = b'"""WLR OnAir automatic update components."""\n'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def stage1(tmp_path, monkeypatch):
    root = tmp_path / '한글 소스 (새 폴더)'
    root.mkdir()
    builder = load(ROOT / 'build.py', 'marker_builder')
    for name in builder.SOURCE_FILES + tuple('backend/' + name for name in builder.BACKEND_FILES):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    shutil.copytree(ROOT / 'updater', root / 'updater', ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT / 'frontend', root / 'frontend')
    builder.write_json(root / 'SOURCE_MANIFEST.json', builder.create_source_manifest(root))
    bootstrap = load(ROOT / 'build_windows.py', 'marker_bootstrap')
    monkeypatch.setattr(bootstrap, 'ROOT', root)
    monkeypatch.setattr(bootstrap, 'builder_module', lambda: builder)
    select_lock = bootstrap.requirements_for_runtime
    monkeypatch.setattr(bootstrap, 'requirements_for_runtime', lambda info: select_lock(info, root))
    runtime = dict(implementation='cpython', version=[3, 12, 0], releaselevel='final',
                   platform='win32', machine='AMD64', bits=64, gil_disabled=False,
                   sysconfig_platform='win-amd64')
    monkeypatch.setattr(builder, 'runtime_info', lambda: runtime)
    reached = []

    def stop_before_environment(*args):
        # Native Windows process creation is the external boundary, after real stage1.
        reached.append('environment')
        raise RuntimeError('TEST_BOUNDARY: environment creation deliberately not run')

    monkeypatch.setattr(bootstrap, 'create_build_environment', stop_before_environment)
    monkeypatch.setattr(bootstrap, 'run_logged', lambda *a, **kw: pytest.fail('must not download or build'))
    return root, builder, bootstrap, runtime, reached


@pytest.mark.parametrize('minor', [11, 12, 13, 14])
def test_missing_updater_marker_is_restored_before_build_environment(stage1, minor, capsys):
    root, builder, bootstrap, runtime, reached = stage1
    runtime['version'][1] = minor
    manifest_bytes = (root / 'SOURCE_MANIFEST.json').read_bytes()
    marker = root / 'updater/__init__.py'
    marker.unlink()
    # The reported ValueError is independently reproduced by the original read-only check.
    with pytest.raises(ValueError, match='누락된 소스'):
        builder.verify_source_manifest(root)
    assert not marker.exists()
    bootstrap.main(opener=lambda _: None)
    assert reached == ['environment']
    assert marker.read_bytes() == MARKER
    assert (root / 'SOURCE_MANIFEST.json').read_bytes() == manifest_bytes
    assert builder.verify_source_manifest(root)['version'] == (root / 'VERSION').read_text().strip()
    assert '__init__.py' in capsys.readouterr().out


@pytest.mark.parametrize('case', ['edited_marker', 'directory_marker', 'wrong_expected_hash',
                                'other_missing', 'other_changed', 'missing_updater_directory'])
def test_marker_recovery_never_approves_invalid_source(stage1, case):
    root, builder, bootstrap, runtime, reached = stage1
    marker = root / 'updater/__init__.py'
    if case == 'edited_marker':
        marker.write_bytes(b'raise RuntimeError("changed")\n')
    elif case == 'directory_marker':
        marker.unlink(); marker.mkdir()
    elif case == 'missing_updater_directory':
        shutil.rmtree(root / 'updater')
    else:
        marker.unlink()
        if case == 'wrong_expected_hash':
            manifest = json.loads((root / 'SOURCE_MANIFEST.json').read_text())
            manifest['files']['updater/__init__.py'] = '0' * 64
            builder.write_json(root / 'SOURCE_MANIFEST.json', manifest)
        elif case == 'other_missing':
            (root / 'updater/core.py').unlink()
        else:
            (root / 'frontend/dist/index.html').write_bytes(b'changed frontend')
    manifest_bytes = (root / 'SOURCE_MANIFEST.json').read_bytes()
    assert bootstrap.main(opener=lambda _: None) == 1
    assert not reached
    assert (root / 'SOURCE_MANIFEST.json').read_bytes() == manifest_bytes
    if case == 'edited_marker':
        assert marker.read_bytes() == b'raise RuntimeError("changed")\n'
    if case == 'wrong_expected_hash':
        assert not marker.exists()


@pytest.mark.parametrize('case', ['file_link', 'dangling_link', 'directory_link'])
def test_marker_recovery_never_writes_through_link(stage1, tmp_path, case):
    root, builder, bootstrap, runtime, reached = stage1
    marker = root / 'updater/__init__.py'
    outside = tmp_path / 'outside'
    outside.mkdir()
    if case == 'directory_link':
        shutil.rmtree(root / 'updater')
        (root / 'updater').symlink_to(outside, target_is_directory=True)
    else:
        marker.unlink()
        if case == 'file_link':
            (outside / 'marker.py').write_bytes(MARKER)
        marker.symlink_to(outside / 'marker.py')
    before = {p.name: p.read_bytes() for p in outside.iterdir()}
    assert bootstrap.main(opener=lambda _: None) == 1
    assert not reached
    assert {p.name: p.read_bytes() for p in outside.iterdir()} == before


def test_intact_source_is_read_only_during_preparation(stage1):
    root, builder, bootstrap, runtime, reached = stage1
    before = {p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
              for p in root.rglob('*') if p.is_file()}
    bootstrap.main(opener=lambda _: None)
    assert reached == ['environment']
    for name, content_and_mtime in before.items():
        assert ((root / name).read_bytes(), (root / name).stat().st_mtime_ns) == content_and_mtime
