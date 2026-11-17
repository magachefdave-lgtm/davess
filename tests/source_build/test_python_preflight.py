"""Portable preflight tests; subprocess/Windows facts are injected, not Windows QA."""
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load():
    path = ROOT / 'build_preflight.py'
    assert path.is_file(), 'The Python preflight diagnostic helper is missing'
    spec = importlib.util.spec_from_file_location('preflight_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runtime(**changes):
    return dict(implementation='cpython', version=[3, 14, 6], releaselevel='final',
                platform='win32', bits=64, gil_disabled=False,
                sysconfig_platform='win-amd64', **{}) | changes


@pytest.mark.parametrize('version', [[3, 11, 0], [3, 11, 15], [3, 12, 0],
                                     [3, 13, 0], [3, 14, 0], [3, 14, 6], [3, 14, 8]])
def test_supported_stable_series_accepts_existing_patch_versions(version):
    assert load().rejection_reasons(runtime(version=version)) == []


@pytest.mark.parametrize('changes,expected', [
    ({'version': [3, 10, 20]}, 'supported series are 3.11, 3.12, 3.13, 3.14'),
    ({'version': [3, 15, 0], 'releaselevel': 'candidate'}, 'pre-release'),
    ({'version': [3, 15, 0]}, 'supported series are 3.11, 3.12, 3.13, 3.14'),
    ({'implementation': 'pypy'}, 'CPython'),
    ({'bits': 32, 'sysconfig_platform': 'win32'}, '32-bit'),
    ({'sysconfig_platform': 'win-arm64'}, 'AMD64'),
    ({'gil_disabled': True}, 'free-threaded'),
    ({'platform': 'linux'}, 'Windows'),
])
def test_rejections_explain_observed_incompatibility(changes, expected):
    assert expected in '; '.join(load().rejection_reasons(runtime(**changes)))


def test_reports_all_rejections_instead_of_only_first_one():
    reasons = load().rejection_reasons(runtime(version=[3, 15, 0], releaselevel='alpha',
                                               bits=32, gil_disabled=True))
    text = '; '.join(reasons)
    assert all(word in text for word in ('3.15.0', 'pre-release', '32-bit', 'free-threaded'))


@pytest.mark.parametrize('minor', [11, 12, 13, 14])
def test_lock_and_dll_follow_the_selected_interpreter(minor):
    m = load()
    info = runtime(version=[3, minor, 0])
    assert m.requirements_name(info) == f'requirements-build-py3{minor}.txt'
    assert m.runtime_dll(info['version']) == f'python3{minor}.dll'


def test_diagnostic_prints_reason_and_never_copies_private_environment(tmp_path, monkeypatch, capsys):
    m = load()
    monkeypatch.setenv('API_TOKEN', 'DO_NOT_LOG_SECRET')
    monkeypatch.setenv('ONAIR_PYTHON_CANDIDATE', r'C:\Users\Private Person\Python\python.exe')
    log = tmp_path / 'preflight.log'
    code = m.check_candidate(log, runtime(version=[3, 15, 0], releaselevel='candidate'))
    assert code == 10
    output = capsys.readouterr().out
    assert '3.15.0' in output and 'pre-release' in output and 'REJECTED' in output
    content = log.read_text(encoding='utf-8')
    assert output == content
    assert 'DO_NOT_LOG_SECRET' not in content and 'Private Person' not in content


def test_accepted_diagnostic_reports_runtime_and_selected_lock(tmp_path, capsys):
    m = load()
    code = m.check_candidate(tmp_path / 'preflight.log', runtime())
    output = capsys.readouterr().out
    assert code == 0 and 'ACCEPTED' in output and '3.14.6' in output
    assert 'requirements-build-py314.txt' in output


def test_unwritable_log_reports_failure_without_changing_runtime_result(tmp_path, capsys):
    m = load()
    assert m.check_candidate(tmp_path, runtime()) == 0
    assert 'WARNING' in capsys.readouterr().out


@pytest.mark.parametrize('missing', ['venv', 'ensurepip'])
def test_missing_build_module_rejects_candidate_before_stage2(missing, tmp_path, monkeypatch, capsys):
    m = load()
    # None is Python's import sentinel for a module unavailable in this interpreter.
    monkeypatch.setitem(sys.modules, missing, None)
    log = tmp_path / 'missing-module.log'
    assert m.check_candidate(log, runtime()) == 10
    output = capsys.readouterr().out
    assert 'ACCEPTED' not in output
    assert 'REJECTED' in output and missing in output
    assert 'Modify' in output and 'Repair' in output
    assert log.read_text(encoding='utf-8') == output


def test_broken_build_module_reports_name_without_private_exception(tmp_path, monkeypatch, capsys):
    m = load()
    original = importlib.import_module

    def broken(name, *args, **kwargs):
        if name == 'ensurepip':
            raise OSError(r'C:\Users\Private Person\Secret Python\ensurepip cannot load')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(importlib, 'import_module', broken)
    assert m.check_candidate(tmp_path / 'broken-module.log', runtime()) == 10
    output = capsys.readouterr().out
    assert 'ensurepip' in output and 'REJECTED' in output
    assert 'Private Person' not in output and 'Secret Python' not in output


def test_build_module_probe_does_not_run_for_unsupported_runtime(monkeypatch, capsys):
    m = load()

    def unexpected(*args, **kwargs):
        pytest.fail('Unsupported runtimes must not import build modules')

    monkeypatch.setattr(importlib, 'import_module', unexpected)
    assert m.check_candidate(info=runtime(version=[3, 10, 0])) == 10
    assert 'supported series' in capsys.readouterr().out


def test_runtime_policy_does_not_require_build_modules(monkeypatch):
    m = load()
    monkeypatch.setitem(sys.modules, 'venv', None)
    monkeypatch.setitem(sys.modules, 'ensurepip', None)
    # Frozen applications intentionally omit build tools. Their ABI checks stay unchanged.
    assert m.rejection_reasons(runtime()) == []
    assert m.requirements_name(runtime()) == 'requirements-build-py314.txt'


def test_cmd_diagnostics_exist_without_python_and_do_not_hide_probe_stdout():
    raw = (ROOT / 'build_windows.cmd').read_bytes()
    text = raw.decode('ascii')
    assert b'\r\n' in raw and b'\n' not in raw.replace(b'\r\n', b'')
    assert 'No installed Python candidates were returned' in text
    assert 'PYTHON CHECK FAILED' in text and 'python_preflight_' in text
    assert 'PROBE COULD NOT RUN' in text and 'WindowsApps alias skipped' in text
    lines = [line for line in text.splitlines() if 'build_preflight.py' in line and not line.startswith('rem ')]
    assert len(lines) == 1 and ' -I ' in lines[0]
    assert ' >nul' not in lines[0] and ' 1>nul' not in lines[0]
    assert 'build_windows.py' in text and 'pause' in text
    assert 'where python.exe' in text and 'py -0p' in text
    assert 'py -3' not in text and 'winget' not in text and 'powershell' not in text


def test_probe_isolated_execution_ignores_poisoned_pythonpath(tmp_path):
    import subprocess
    m = load()
    (tmp_path / 'sysconfig.py').write_text('raise RuntimeError("POISONED PYTHONPATH")')
    import os
    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    result = subprocess.run([sys.executable, '-I', str(ROOT / 'build_preflight.py'),
                             str(tmp_path / 'preflight.log')], env=env,
                            capture_output=True, text=True, check=False)
    assert result.returncode == 10  # This is the Linux host, never an accepted Windows host.
    assert 'Windows' in result.stdout and 'POISONED' not in result.stderr


# Both launchers that parse `py -0p` share this normalization block byte-for-byte.
LAUNCHER_PARSING_CMDS = ('build_windows.cmd', 'collect_wheels.cmd')


def read_cmd(name):
    # House rule: CP949 + CRLF. build_windows.cmd is pure ASCII (a CP949 subset).
    return (ROOT / name).read_bytes().decode('cp949').replace('\r\n', '\n')


# This narrow model executes the actual CMD normalization statements below.
# It covers token/substring decisions only, not cmd.exe expansion/Unicode I/O.
def cmd_normalize_model(candidate, name='build_windows.cmd'):
    text = read_cmd(name)
    assert ':normalize_candidate\n' in text, 'Dedicated CMD normalization is missing'
    code = text.split(':normalize_candidate\n', 1)[1].split('exit /b 0', 1)[0]
    for line in code.splitlines():
        line = line.strip()
        if not line or line.startswith('rem '):
            continue
        if line == 'set "ONAIR_PYTHON_CANDIDATE=%ONAIR_PYTHON_CANDIDATE:"=%"':
            candidate = candidate.replace('"', '')
        elif line.startswith('for /f "tokens=*" %%P in '):
            candidate = candidate.lstrip(' \t')
        elif line == 'if "%ONAIR_PYTHON_CANDIDATE:~0,1%"=="*" set "ONAIR_PYTHON_CANDIDATE=%ONAIR_PYTHON_CANDIDATE:~1%"':
            if candidate.startswith('*'):
                candidate = candidate[1:]
        elif line == 'if "%ONAIR_PYTHON_CANDIDATE:~-2%"==" *" set "ONAIR_PYTHON_CANDIDATE=%ONAIR_PYTHON_CANDIDATE:~0,-2%"':
            if candidate.endswith(' *'):
                candidate = candidate[:-2]
        else:
            pytest.fail('CMD model needs a reviewed update for: ' + line)
    return candidate


@pytest.mark.parametrize('line,expected', [
    (' -V:3.14      *   C:\\Users\\A B\\Python314\\python.exe', r'C:\Users\A B\Python314\python.exe'),
    (' -V:3.13         C:\\Program Files\\Python313\\python.exe', r'C:\Program Files\Python313\python.exe'),
    (' -3.12-64        C:\\Python312\\python.exe', r'C:\Python312\python.exe'),
    (' -V:PythonCore/3.11  * "C:\\A B\\python.exe"', r'C:\A B\python.exe'),
    ('  *              C:\\venv\\Scripts\\python.exe', r'C:\venv\Scripts\python.exe'),
    (' -3.11-64        C:\\Python311\\python.exe *', r'C:\Python311\python.exe'),
    ('\t-V:3.14\t*\tC:\\한글! & (test)\\python.exe', 'C:\\한글! & (test)\\python.exe'),
])
@pytest.mark.parametrize('name', LAUNCHER_PARSING_CMDS)
def test_launcher_legacy_and_manager_normalization_model(line, expected, name):
    # FOR /F tokens=1,* uses spaces/tabs, leaving the entire path remainder.
    tokens = line.lstrip(' \t').split(None, 1)
    assert cmd_normalize_model(tokens[1], name) == expected


@pytest.mark.parametrize('path', [r'C:\Program Files\Python\python.exe',
                                 r'C:\A&B!(work)\python.exe',
                                 'C:\\한글 폴더\\python.exe'])
@pytest.mark.parametrize('name', LAUNCHER_PARSING_CMDS)
def test_path_candidate_normalization_preserves_filename_model(path, name):
    assert cmd_normalize_model(path, name) == path


@pytest.mark.skipif(sys.platform != 'win32', reason='Real cmd.exe verification requires Windows')
@pytest.mark.parametrize('candidate,expected', [
    (' *   C:\\Program Files\\Python\\python.exe', r'C:\Program Files\Python\python.exe'),
    ('\t*\t"C:\\한글! & (test)\\python.exe"', 'C:\\한글! & (test)\\python.exe'),
    ('C:\\Python311\\python.exe *', r'C:\Python311\python.exe'),
    (r'C:\A&B!(work)\python.exe', r'C:\A&B!(work)\python.exe'),
])
@pytest.mark.parametrize('name', LAUNCHER_PARSING_CMDS)
def test_normalizer_in_real_cmd_when_windows_is_available(candidate, expected, name, tmp_path):
    import os
    import subprocess
    text = read_cmd(name)
    body = ':normalize_candidate\n' + text.split(':normalize_candidate\n', 1)[1].split('exit /b 0', 1)[0] + 'exit /b 0\n'
    script = '@echo off\nsetlocal DisableDelayedExpansion\nchcp 65001 >nul\ncall :normalize_candidate\nset ONAIR_PYTHON_CANDIDATE\nexit /b 0\n' + body
    path = tmp_path / 'normalize.cmd'
    path.write_bytes(script.replace('\n', '\r\n').encode('ascii'))
    result = subprocess.run(['cmd.exe', '/d', '/c', str(path)],
        env=dict(os.environ, ONAIR_PYTHON_CANDIDATE=candidate),
        capture_output=True, encoding='utf-8', check=True)
    assert result.stdout.strip() == 'ONAIR_PYTHON_CANDIDATE=' + expected
