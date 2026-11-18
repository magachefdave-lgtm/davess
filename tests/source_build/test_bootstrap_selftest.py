import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    path = ROOT / f'{name}.py'
    assert path.is_file(), f'{name} module is not implemented'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_dependency_command_uses_pinned_wheels_and_public_registry(tmp_path):
    m = load('build_windows')
    cmd = m.install_command(tmp_path / 'Scripts/python.exe', ROOT / 'requirements-build.txt')
    assert '--require-hashes' in cmd and '--only-binary=:all:' in cmd
    assert cmd[cmd.index('--index-url') + 1] == 'https://pypi.org/simple'
    assert '-r' in cmd and '--no-input' in cmd
    assert '--upgrade' not in cmd


def test_bootstrap_environment_cannot_inherit_python_or_pip_config(monkeypatch):
    m = load('build_windows')
    monkeypatch.setenv('PIP_EXTRA_INDEX_URL', 'https://unwanted.invalid/simple')
    monkeypatch.setenv('PYTHONPATH', 'unwanted')
    env = m.clean_environment()
    assert 'PIP_EXTRA_INDEX_URL' not in env and 'PYTHONPATH' not in env
    import os
    assert env['PIP_CONFIG_FILE'] == os.devnull


def test_logged_subprocess_preserves_failure_and_log(tmp_path):
    m = load('build_windows')
    import subprocess
    path = tmp_path / 'build.log'
    with path.open('w', encoding='utf-8') as log:
        with pytest.raises(subprocess.CalledProcessError) as result:
            m.run_logged([sys.executable, '-c', 'print("test failure"); raise SystemExit(7)'], log, tmp_path)
    assert result.value.returncode == 7
    assert 'test failure' in path.read_text()


def test_selftest_rejects_role_version_asset_and_module_errors(tmp_path):
    m = load('build_selftest')
    import hashlib
    p = tmp_path / 'frontend/dist/index.html'
    p.parent.mkdir(parents=True)
    p.write_text('test only')
    data = {'role': 'VIEWER', 'version': '3.16.3', 'build_method': 'clean-source-pyinstaller',
            'source_fingerprint': 'a' * 64, 'assets': {'frontend/dist/index.html': hashlib.sha256(p.read_bytes()).hexdigest()}}
    (tmp_path / 'onair_build_info.json').write_text(json.dumps(data))
    assert m.verify_payload(tmp_path, 'VIEWER', '3.16.3') == data
    with pytest.raises(ValueError, match='role|역할'):
        m.verify_payload(tmp_path, 'ADMIN', '3.16.3')
    p.write_text('corrupt')
    with pytest.raises(ValueError, match='hash'):
        m.verify_payload(tmp_path, 'VIEWER', '3.16.3')


def test_selftest_error_is_machine_readable_without_opening_setup(tmp_path):
    m = load('build_selftest')
    result = tmp_path / 'self-test.json'
    assert m.run('ADMIN', '3.16.3', str(result)) == 1
    data = json.loads(result.read_text())
    assert data['ok'] is False and 'frozen' in data['error']


def test_cmd_prefers_requested_series_and_never_auto_installs():
    text = (ROOT / 'build_windows.cmd').read_text(encoding='utf-8')
    assert 'py -0p' in text and 'build_windows.py' in text and 'DisableDelayedExpansion' in text
    assert 'py -3.14' not in text and 'where python.exe' in text
    assert 'WindowsApps' in text and 'if not exist' in text
    assert 'winget' not in text and 'powershell' not in text


@pytest.mark.parametrize('role', ['ADMIN', 'VIEWER', 'MASTER'])
def test_staged_dependency_probe_does_not_execute_backend(role, tmp_path):
    import subprocess
    builder = load('build')
    stage = tmp_path / '한글 self test'
    builder.prepare_role(ROOT, stage, role, '3.16.3', 'a' * 64)
    # Only the OS DLL loader is replaced: this Linux check does not simulate Windows.
    script = ('import ctypes,sys,json; calls=[]; '
              'ctypes.WinDLL=lambda name,**kwargs: calls.append(name); '
              'import build_selftest; '
              f'build_selftest.check_modules({role!r}); '
              'assert "onair_backend" not in sys.modules; '
              'print(json.dumps(calls))')
    result = subprocess.run([sys.executable, '-c', script], cwd=stage,
                            capture_output=True, text=True, check=True)
    calls = json.loads(result.stdout)
    assert calls == ['kernel32', 'user32', 'shell32'] + (['crypt32'] if role == 'ADMIN' else [])
    assert not list(stage.rglob('*.db'))
    assert not list(stage.rglob('signing_key.json'))


def test_cmd_unsets_legacy_install_opt_in_flags():
    text = (ROOT / 'build_windows.cmd').read_text(encoding='utf-8')
    assert 'set "PYLAUNCHER_ALLOW_INSTALL="' in text
    assert 'set "PYLAUNCHER_ALWAYS_INSTALL="' in text
    assert 'set "PYLAUNCHER_DRYRUN="' in text


@pytest.mark.parametrize('minor', [11, 12, 13, 14])
def test_bootstrap_selects_actual_python_abi_lock(minor, tmp_path):
    m = load('build_windows')
    info = dict(implementation='cpython', version=[3, minor, 6], releaselevel='final',
                platform='win32', bits=64, gil_disabled=False, sysconfig_platform='win-amd64')
    target = tmp_path / f'requirements-build-py3{minor}.txt'
    target.write_text('test only')
    assert m.requirements_for_runtime(info, tmp_path) == target
    target.unlink()
    with pytest.raises(FileNotFoundError, match='requirements-build'):
        m.requirements_for_runtime(info, tmp_path)


@pytest.mark.parametrize('minor', [11, 12, 13, 14])
def test_frozen_selftest_binds_supported_runtime_to_build_metadata(minor):
    m = load('build_selftest')
    info = dict(implementation='cpython', version=[3, minor, 6], releaselevel='final',
                platform='win32', bits=64, gil_disabled=False, sysconfig_platform='win-amd64')
    m.verify_runtime({'python_runtime': info}, actual=info)
    with pytest.raises(ValueError, match='mismatch'):
        m.verify_runtime({'python_runtime': info}, actual=dict(info, version=[3, minor, 7]))
    with pytest.raises(ValueError, match='mismatch'):
        m.verify_runtime({'python_runtime': info}, actual=dict(info, gil_disabled=True))


def test_frozen_selftest_rejects_missing_runtime_identity():
    m = load('build_selftest')
    with pytest.raises(ValueError, match='runtime'):
        m.verify_runtime({}, actual={})


# [위험] 사내망에는 PyPI 가 없다. wheels/ 가 있는데도 --index-url 로 나가면
#   빌드가 3단계에서 멈춘다. 반대로 wheels/ 가 없을 때 --no-index 로 가면
#   인터넷 PC 에서도 아무것도 설치되지 않는다. 두 분기를 모두 못박는다.
def test_offline_wheelhouse_install_command_never_touches_network(tmp_path):
    m = load('build_windows')
    house = tmp_path / 'wheels'
    house.mkdir()
    cmd = m.install_command(tmp_path / 'Scripts/python.exe', ROOT / 'requirements-build.txt', house)
    assert '--no-index' in cmd
    assert cmd[cmd.index('--find-links') + 1] == str(house)
    assert '--index-url' not in cmd
    # 해시·바이너리 고정은 오프라인에서도 풀지 않는다
    assert '--require-hashes' in cmd and '--only-binary=:all:' in cmd
    assert '--upgrade' not in cmd and '--trusted-host' not in cmd


def test_wheelhouse_absent_keeps_public_registry_path(tmp_path):
    m = load('build_windows')
    assert m.wheelhouse_dir(tmp_path) is None
    cmd = m.install_command(tmp_path / 'Scripts/python.exe', ROOT / 'requirements-build.txt',
                            m.wheelhouse_dir(tmp_path))
    assert cmd[cmd.index('--index-url') + 1] == 'https://pypi.org/simple'
    assert '--no-index' not in cmd


def test_empty_wheelhouse_stops_instead_of_silently_going_online(tmp_path):
    m = load('build_windows')
    (tmp_path / 'wheels').mkdir()
    with pytest.raises(FileNotFoundError):
        m.wheelhouse_dir(tmp_path)
    (tmp_path / 'wheels' / 'anything-1.0-py3-none-any.whl').write_bytes(b'')
    assert m.wheelhouse_dir(tmp_path) == tmp_path / 'wheels'


# [위험] 네 개 minor 중 하나라도 빠지면 그 Python 이 깔린 PC 에서만 빌드가
#   실패한다. 수집 대상이 락 파일과 1:1 로 맞는지 고정한다.
def test_wheel_collector_covers_every_abi_lock_with_hash_pinning():
    m = load('collect_wheels')
    locks = {name for name, _, _ in m.TARGETS}
    assert locks == {f'requirements-build-py31{n}.txt' for n in (1, 2, 3, 4)}
    for name, python_version, abi in m.TARGETS:
        assert (ROOT / name).is_file(), name
        assert abi == 'cp' + python_version.replace('.', '')
        cmd = m.download_command(ROOT / name, python_version, abi)
        assert '--require-hashes' in cmd and '--only-binary=:all:' in cmd
        assert cmd[cmd.index('--platform') + 1] == 'win_amd64'
        assert cmd[cmd.index('--abi') + 1] == abi
        assert cmd[cmd.index('--index-url') + 1] == 'https://pypi.org/simple'


def test_wheel_collector_environment_cannot_inherit_pip_config(monkeypatch):
    m = load('collect_wheels')
    monkeypatch.setenv('PIP_INDEX_URL', 'https://unwanted.invalid/simple')
    env = m.clean_environment()
    assert 'PIP_INDEX_URL' not in env
