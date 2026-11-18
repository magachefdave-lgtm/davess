"""Request-access code must survive source inventory, staging and freezing."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('request_access_builder', ROOT / 'build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
FILES = ('__init__.py', 'request_access_schema.py', 'request_access_store.py',
         'request_access_local.py', 'request_recovery.py', 'request_access_api.py',
         'dinner_schema.py', 'dinner_service.py', 'dinner_api.py', 'master_auth.py')
MODULES = ('backend',) + tuple('backend.' + Path(name).stem for name in FILES[1:])


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'source'
    shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns('.git', '__pycache__', '.pytest_cache'))
    for name in FILES:
        (root / 'backend' / name).write_text('MARKER = ' + repr(name) + '\n', encoding='utf-8')
    (root / 'backend' / 'private.dat').write_text('must not ship')
    return root


@pytest.mark.parametrize('role', ['ADMIN', 'VIEWER', 'MASTER'])
def test_staged_request_access_imports_are_complete_and_allowlisted(source, tmp_path, role):
    stage = tmp_path / role
    builder.prepare_role(source, stage, role, '3.18.35', 'a' * 64)
    assert {p.name for p in (stage / 'backend').iterdir()} == set(FILES)
    script = 'import importlib; ' + '; '.join(
        f'assert importlib.import_module({module!r}).MARKER == {name!r}'
        for module, name in zip(MODULES, FILES))
    subprocess.run([sys.executable, '-B', '-c', script], cwd=stage, check=True)
    command = builder.pyinstaller_command('python.exe', stage, tmp_path / 'work', tmp_path / 'dist', role)
    hidden = [command[i + 1] for i, value in enumerate(command[:-1]) if value == '--hidden-import']
    assert set(MODULES) <= set(hidden)


def test_request_access_inventory_is_hashed_and_missing_files_fail(source):
    manifest = builder.create_source_manifest(source)
    for name in FILES:
        key = 'backend/' + name
        assert manifest['files'][key] == builder.sha256_file(source / key)
    original = manifest['source_fingerprint']
    (source / 'backend/request_access_store.py').write_text('MARKER = "changed"\n')
    assert builder.create_source_manifest(source)['source_fingerprint'] != original
    (source / 'backend/request_access_local.py').unlink()
    with pytest.raises(ValueError, match='request_access_local'):
        builder.source_paths(source)


@pytest.mark.parametrize('role', ['ADMIN', 'VIEWER', 'MASTER'])
def test_runtime_selftest_imports_support_modules_without_application(source, tmp_path, role):
    stage = tmp_path / role
    builder.prepare_role(source, stage, role, '3.18.35', 'a' * 64)
    script = ('import ctypes,sys; ctypes.WinDLL=lambda *args,**kwargs: None; '
              'import build_selftest; '
              f'checked=build_selftest.check_modules({role!r}); '
              f'assert set({MODULES!r}) <= set(checked); '
              f'assert all(name in sys.modules for name in {MODULES!r}); '
              'assert "onair_backend" not in sys.modules; assert "backend.main" not in sys.modules')
    subprocess.run([sys.executable, '-B', '-c', script], cwd=stage, check=True)
