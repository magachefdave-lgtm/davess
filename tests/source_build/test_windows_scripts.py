"""Windows batch-file rules and build-script robustness, checked portably.

The .cmd checks model cmd.exe decisions from the real file text (token splitting,
labels, substring tests). They are not a substitute for running cmd.exe on Korean
Windows; the Windows-only test in test_python_preflight.py covers the normalizer there.
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import types

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {'.build-envs', 'node_modules', '.git', 'build_logs'}


def load(name):
    spec = importlib.util.spec_from_file_location(f'winscripts_{name}', ROOT / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_cmd(name):
    return (ROOT / name).read_bytes().decode('cp949').replace('\r\n', '\n')


def cmd_files():
    return sorted(p for p in ROOT.rglob('*')
                  if p.suffix.lower() in {'.cmd', '.bat'} and p.is_file()
                  and not SKIP_DIRS.intersection(p.relative_to(ROOT).parts))


def label_block(text, label):
    """Lines of one label up to the next label (cmd falls through, so stop at any label)."""
    body = text.split(f'\n:{label}\n', 1)[1]
    lines = []
    for line in body.split('\n'):
        if line.startswith(':'):
            break
        lines.append(line)
    return lines


# ---------------------------------------------------------------- house rules

def test_every_batch_file_is_found():
    names = {p.name for p in cmd_files()}
    assert {'build_windows.cmd', 'collect_wheels.cmd', '배포관리.cmd'} <= names


@pytest.mark.parametrize('path', cmd_files(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_batch_file_is_cp949_crlf_with_ascii_commands(path):
    raw = path.read_bytes()
    assert not raw.startswith(b'\xef\xbb\xbf'), 'BOM breaks the first command'
    assert raw.endswith(b'\r\n')
    assert b'\n' not in raw.replace(b'\r\n', b''), 'bare LF'
    assert b'\r' not in raw.replace(b'\r\n', b''), 'bare CR'
    text = raw.decode('cp949')  # strict: UTF-8 Korean text fails here
    non_ascii_lines = [line for line in text.split('\r\n') if not line.isascii()]
    for line in non_ascii_lines:
        # Korean is allowed only as echo text; labels/commands/variables stay ASCII.
        assert re.match(r'(?i)echo[ .]', line.lstrip()), line
    if re.search(r'(?im)^\s*(@?chcp|.*[&|]\s*chcp)\b', text):
        # cmd re-reads the batch file line by line in the active code page, so a code page
        # switch is only safe in a pure-ASCII file (see build_windows.cmd for why it has one).
        assert raw.isascii(), 'chcp in a file with Korean text garbles the following lines'


@pytest.mark.parametrize('path', cmd_files(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_folder_change_failure_is_reported_not_silent(path):
    text = path.read_bytes().decode('cp949').replace('\r\n', '\n')
    lines = text.split('\n')
    for index, line in enumerate(lines):
        if line.strip().lower().startswith('cd /d'):
            follow = lines[index + 1].strip()
            match = re.fullmatch(r'if errorlevel 1 goto :(\w+)', follow)
            assert match, f'{path.name}: cd /d must be followed by an error branch, got {follow!r}'
            block = [x.strip() for x in label_block(text, match.group(1))]
            assert any(x.startswith('echo ') for x in block)
            assert 'pause' in block and 'exit /b 1' in block
            assert block.index('pause') < block.index('exit /b 1')


def test_deploy_manager_is_cp949_without_chcp_and_explains_folder_error():
    raw = (ROOT / '배포관리.cmd').read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode('utf-8')  # it must not be the old UTF-8 file
    text = raw.decode('cp949').replace('\r\n', '\n')
    assert 'chcp' not in text.lower()
    block = label_block(text, 'folder_error')
    assert any('폴더' in line for line in block if line.startswith('echo '))
    # Unchanged launch contract.
    assert 'start "" "%~dp0Admin\\WLR_OnAir_Admin.exe" --publish' in text
    assert 'if exist "BUILD_INCOMPLETE.txt" goto :incomplete' in text


def test_build_windows_keeps_utf8_codepage_only_because_it_is_ascii():
    # py -0p (launcher2.c) writes UTF-8 and FOR /F decodes with the active code page.
    # Without chcp 65001 a C:\Users\<한글 이름>\...\python.exe from py -0p is garbled.
    raw = (ROOT / 'build_windows.cmd').read_bytes()
    assert raw.isascii()
    text = raw.decode('ascii')
    assert text.index('chcp 65001 >nul') < text.index("('py -0p 2^>nul')")


# ------------------------------------------------- collect_wheels.cmd: py -0p

def test_collect_wheels_reuses_build_windows_launcher_parsing_verbatim():
    build = read_cmd('build_windows.cmd')
    collect = read_cmd('collect_wheels.cmd')
    for label in ('from_launcher', 'normalize_candidate'):
        assert label_block(collect, label) == label_block(build, label), label
    assert 'for /f "tokens=1,*" %%A in (\'py -0p 2^>nul\') do (' in collect
    assert 'set "ONAIR_PY=%%B"' not in collect  # the original defect
    assert 'set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"' in collect
    assert 'py -3' not in collect and 'winget' not in collect and 'powershell' not in collect


class CmdSelectionModel:
    """Executes the reviewed statements of collect_wheels.cmd's Python search.

    Any statement the model does not know fails the test, so a script change
    forces a review of this model instead of silently passing.
    """

    def __init__(self, text, py_lines=(), where_lines=(), existing=(), runnable=(), env=None):
        self.text = text
        self.py_lines, self.where_lines = list(py_lines), list(where_lines)
        self.existing, self.runnable = set(existing), set(runnable)
        self.env = env or {}
        self.selected = None
        self.store_alias = False
        self.candidates = 0
        self.tried = []

    # FOR /F "tokens=1,*": leading delimiters skipped; %%B gets the untouched remainder.
    @staticmethod
    def for_f_tokens(line):
        parts = line.lstrip(' \t').split(None, 1)
        return (parts + ['', ''])[:2] if parts else None

    def normalize(self, candidate):
        for line in label_block(self.text, 'normalize_candidate'):
            line = line.strip()
            if not line or line.startswith('rem '):
                continue
            if line == 'set "ONAIR_PYTHON_CANDIDATE=%ONAIR_PYTHON_CANDIDATE:"=%"':
                candidate = candidate.replace('"', '')
            elif line.startswith('for /f "tokens=*" %%P in '):
                candidate = candidate.lstrip(' \t')
            elif line == 'if "%ONAIR_PYTHON_CANDIDATE:~0,1%"=="*" set "ONAIR_PYTHON_CANDIDATE=%ONAIR_PYTHON_CANDIDATE:~1%"':
                candidate = candidate[1:] if candidate.startswith('*') else candidate
            elif line == 'if "%ONAIR_PYTHON_CANDIDATE:~-2%"==" *" set "ONAIR_PYTHON_CANDIDATE=%ONAIR_PYTHON_CANDIDATE:~0,-2%"':
                candidate = candidate[:-2] if candidate.endswith(' *') else candidate
            elif line == 'exit /b 0':
                return candidate
            else:
                pytest.fail('normalize model needs review for: ' + line)
        return candidate

    def from_launcher(self, tag, candidate):
        for line in label_block(self.text, 'from_launcher'):
            line = line.strip()
            if not line or line.startswith('rem '):
                continue
            match = re.fullmatch(r'if "%ONAIR_LAUNCHER_TAG(:~0,1)?%"=="(.*)" goto :try_candidate', line)
            if match:
                value = tag[:1] if match.group(1) else tag
                if value == match.group(2):
                    return self.try_candidate(candidate)
            elif line == 'exit /b 0':
                return
            else:
                pytest.fail('from_launcher model needs review for: ' + line)

    def try_candidate(self, candidate):
        rc = 0
        for line in label_block(self.text, 'try_candidate'):
            line = line.strip()
            if not line or line.startswith('rem '):
                continue
            if line == 'call :normalize_candidate':
                candidate = self.normalize(candidate)
            elif line == 'if not defined ONAIR_PYTHON_CANDIDATE exit /b 0':
                if not candidate:
                    return
            elif line == 'if /i not "%ONAIR_PYTHON_CANDIDATE:WindowsApps=%"=="%ONAIR_PYTHON_CANDIDATE%" goto :store_alias':
                if 'windowsapps' in candidate.lower():
                    self.store_alias = True  # :store_alias only sets the flag and returns
                    return
            elif line == 'if not exist "%ONAIR_PYTHON_CANDIDATE%" exit /b 0':
                if candidate not in self.existing:
                    return
            elif line == 'set /a ONAIR_CANDIDATES+=1 >nul':
                self.candidates += 1
            elif line == '"%ONAIR_PYTHON_CANDIDATE%" -I -c "import sys" >nul 2>&1':
                self.tried.append(candidate)
                rc = 0 if candidate in self.runnable else 1
            elif line == 'if errorlevel 1 exit /b 0':
                if rc:
                    return
            elif line == 'set "ONAIR_PY=%ONAIR_PYTHON_CANDIDATE%"':
                self.selected = candidate
            elif line == 'exit /b 0':
                return
            else:
                pytest.fail('try_candidate model needs review for: ' + line)

    def expand(self, value, loop_value):
        value = value.replace('%%V', loop_value)
        return re.sub(r'%(\w+)%', lambda m: self.env.get(m.group(1), ''), value)

    def run(self):
        """Interpret the search section; returns the label it jumps to."""
        lines = self.text.split('\n')
        start = next(i for i, line in enumerate(lines) if line.startswith('echo ') and not line.isascii())
        i = start + 1
        while i < len(lines):
            line = lines[i].strip()
            i += 1
            if not line or line.startswith('rem '):
                continue
            if line == 'if defined ONAIR_PY goto :have_python':
                if self.selected:
                    return 'have_python'
                continue
            if line == 'goto :no_python':
                return 'no_python'
            loop = re.fullmatch(r"for /f \"tokens=1,\*\" %%A in \('py -0p 2\^>nul'\) do \(", line)
            where = re.fullmatch(r"for /f \"delims=\" %%P in \('where python\.exe 2\^>nul'\) do \(", line)
            plain = re.fullmatch(r'for %%V in \(([^)]*)\) do \(', line)
            if not (loop or where or plain):
                pytest.fail('search model needs review for: ' + line)
            body = []
            while lines[i].strip() != ')':
                body.append(lines[i].strip())
                i += 1
            i += 1
            if loop:
                assert body == ['set "ONAIR_LAUNCHER_TAG=%%A"', 'set "ONAIR_PYTHON_CANDIDATE=%%B"',
                                'if not defined ONAIR_PY call :from_launcher']
                for raw in self.py_lines:
                    tokens = self.for_f_tokens(raw)
                    if tokens and not self.selected:
                        self.from_launcher(*tokens)
            elif where:
                assert body == ['set "ONAIR_PYTHON_CANDIDATE=%%P"', 'if not defined ONAIR_PY call :try_candidate']
                for raw in self.where_lines:
                    if raw and not self.selected:
                        self.try_candidate(raw)
            else:
                for value in plain.group(1).split():
                    candidate = None
                    for statement in body:
                        set_match = re.fullmatch(r'set "ONAIR_PYTHON_CANDIDATE=(.*)"', statement)
                        if set_match:
                            candidate = self.expand(set_match.group(1), value)
                        elif statement == 'if not defined ONAIR_PY call :try_candidate':
                            if not self.selected:
                                self.try_candidate(candidate)
                        else:
                            pytest.fail('install-folder model needs review for: ' + statement)
        pytest.fail('search section did not end')


DEFAULT_313 = r'C:\Users\kim\AppData\Local\Programs\Python\Python313\python.exe'
STORE_312 = (r'C:\Users\kim\AppData\Local\Microsoft\WindowsApps'
             r'\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\python.exe')


@pytest.mark.parametrize('py_lines,existing,expected', [
    # The reported defect: the default interpreter is marked with '*'.
    ([' -V:3.13 *        ' + DEFAULT_313], {DEFAULT_313}, DEFAULT_313),
    ([' -V:3.14 *   C:\\Program Files\\Python314\\python.exe'],
     {r'C:\Program Files\Python314\python.exe'}, r'C:\Program Files\Python314\python.exe'),
    # Legacy launcher heading and marker style.
    (['Installed Pythons found by py Launcher for Windows', ' -3.11-64 *      C:\\Python311\\python.exe'],
     {r'C:\Python311\python.exe'}, r'C:\Python311\python.exe'),
    ([' -3.11-64        C:\\Python311\\python.exe *'], {r'C:\Python311\python.exe'}, r'C:\Python311\python.exe'),
    # Store alias is skipped even when it is the default; the next real install is used.
    ([' -V:3.12 *       ' + STORE_312, ' -V:3.13         ' + DEFAULT_313], {STORE_312, DEFAULT_313}, DEFAULT_313),
    # A path that does not exist (e.g. garbled by the code page) is skipped.
    ([' -V:3.13 *  C:\\Users\\ê¹\x80\\python.exe', ' -V:3.12  C:\\Python312\\python.exe'],
     {r'C:\Python312\python.exe'}, r'C:\Python312\python.exe'),
])
def test_collect_wheels_launcher_listing_selects_clean_runnable_path(py_lines, existing, expected):
    model = CmdSelectionModel(read_cmd('collect_wheels.cmd'), py_lines=py_lines,
                              existing=existing, runnable=existing - {STORE_312})
    assert model.run() == 'have_python'
    assert model.selected == expected
    assert not model.selected.startswith(('*', ' ')) and not model.selected.endswith(' ')


def test_collect_wheels_skips_installed_python_that_does_not_run():
    broken, good = r'C:\Broken\python.exe', r'C:\Python314\python.exe'
    model = CmdSelectionModel(read_cmd('collect_wheels.cmd'),
                              py_lines=[' -V:3.13 *  ' + broken, ' -V:3.14     ' + good],
                              existing={broken, good}, runnable={good})
    assert model.run() == 'have_python' and model.selected == good
    assert model.tried == [broken, good]


def test_collect_wheels_store_alias_only_reports_python_not_found():
    model = CmdSelectionModel(read_cmd('collect_wheels.cmd'),
                              py_lines=['No installed Pythons found!'], where_lines=[STORE_312],
                              existing={STORE_312}, runnable={STORE_312})
    assert model.run() == 'no_python'
    assert model.store_alias and model.tried == [] and model.candidates == 0


def test_collect_wheels_finds_korean_profile_install_without_launcher_output():
    # py -0p output is UTF-8; under CP949 a Korean profile path arrives garbled and is
    # rejected, so the environment-variable based install-folder search must find it.
    local = 'C:\\Users\\홍길동\\AppData\\Local'
    target = local + '\\Programs\\Python\\Python313\\python.exe'
    model = CmdSelectionModel(read_cmd('collect_wheels.cmd'),
                              py_lines=[' -V:3.13 *  C:\\Users\\í\x99\x8d\\python.exe'],
                              existing={target}, runnable={target},
                              env={'LOCALAPPDATA': local, 'ProgramFiles': r'C:\Program Files'})
    assert model.run() == 'have_python' and model.selected == target


def test_collect_wheels_finds_python_install_manager_runtime():
    local = r'C:\Users\kim\AppData\Local'
    target = local + r'\Python\pythoncore-3.14-64\python.exe'
    model = CmdSelectionModel(read_cmd('collect_wheels.cmd'), existing={target}, runnable={target},
                              env={'LOCALAPPDATA': local})
    assert model.run() == 'have_python' and model.selected == target


def test_collect_wheels_error_messages_distinguish_missing_python_from_download_failure():
    text = read_cmd('collect_wheels.cmd')
    module = load('collect_wheels')
    routes = dict(re.findall(r'if "%ONAIR_EXIT%"=="(\d+)" goto :(\w+)', text))
    assert routes == {str(module.EXIT_OK): 'done',
                      str(module.EXIT_DOWNLOAD_FAILED): 'download_failed',
                      str(module.EXIT_NO_PIP): 'no_pip',
                      str(module.EXIT_MISSING_LOCK): 'source_incomplete'}
    assert len({module.EXIT_OK, module.EXIT_DOWNLOAD_FAILED, module.EXIT_NO_PIP,
                module.EXIT_MISSING_LOCK, 1, 2}) == 6  # never collides with Python's own codes
    no_python = '\n'.join(label_block(text, 'no_python'))
    download = '\n'.join(label_block(text, 'download_failed'))
    assert 'Python을 찾지 못했습니다' in no_python and 'PyPI' not in no_python
    assert '다운로드 실패' in download and 'PyPI' in download and 'Python은 정상적으로 찾았습니다' in download
    assert 'goto :failed' in text and 'IT 담당자' not in '\n'.join(label_block(text, 'failed'))
    for label in ('done', 'no_python_hint', 'download_failed', 'no_pip', 'source_incomplete',
                  'failed', 'folder_error'):
        block = [x.strip() for x in label_block(text, label)]
        assert 'pause' in block, label
    assert '"%ONAIR_PY%" -I "%~dp0collect_wheels.py"' in text


def test_collect_wheels_exit_codes(monkeypatch, tmp_path, capsys):
    m = load('collect_wheels')
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    monkeypatch.setattr(m, 'WHEELHOUSE', tmp_path / 'wheels')
    assert m.main() == m.EXIT_MISSING_LOCK
    for name, _, _ in m.TARGETS:
        (tmp_path / name).write_text('test only')
    real_find_spec = m.importlib.util.find_spec
    monkeypatch.setattr(m.importlib.util, 'find_spec', lambda name, *a: None if name == 'pip' else real_find_spec(name, *a))
    assert m.main() == m.EXIT_NO_PIP
    monkeypatch.setattr(m.importlib.util, 'find_spec', real_find_spec)
    calls = []

    def fake_pip(code, output):
        def run(command, log):
            calls.append(command)
            log.write(output)
            return code
        return run
    monkeypatch.setattr(m, 'run_logged', fake_pip(1, 'ERROR: HTTPSConnectionPool pypi blocked\n'))
    assert m.main() == m.EXIT_DOWNLOAD_FAILED and len(calls) == 1
    # pip 출력은 build_logs 의 로그 파일에 남고, 실패 안내가 그 파일 경로를 알려 준다.
    logs = sorted((tmp_path / 'build_logs').glob('collect_wheels_*.log'))
    assert logs and 'pypi blocked' in logs[-1].read_text(encoding='utf-8')
    assert str(logs[-1]) in capsys.readouterr().err
    monkeypatch.setattr(m, 'run_logged', fake_pip(0, 'ok\n'))
    assert m.main() == m.EXIT_OK


# ------------------------------------------------------ no bytecode in source

def test_source_check_and_bootstrap_loader_do_not_write_bytecode(tmp_path):
    builder = load('build')
    root = tmp_path / '한글 source copy'
    root.mkdir()
    for name in ('backend', 'updater', 'frontend'):
        shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns('__pycache__'))
    for name in builder.SOURCE_FILES:
        if not (root / name).exists():
            shutil.copyfile(ROOT / name, root / name)
    builder.write_json(root / 'SOURCE_MANIFEST.json', builder.create_source_manifest(root))
    env = {k: v for k, v in os.environ.items() if k != 'PYTHONDONTWRITEBYTECODE'}
    check = subprocess.run([sys.executable, '-I', str(root / 'build.py'), '--check'], cwd=root, env=env,
                           capture_output=True, text=True, encoding='utf-8')
    assert check.returncode == 0, check.stderr
    # build_windows.py runs as __main__ (never cached itself) and loads build.py by path.
    loader = subprocess.run([sys.executable, '-I', '-c',
                             'import runpy, sys; g = runpy.run_path(sys.argv[1], run_name="bootstrap"); '
                             'm = g["builder_module"](); assert m._runtime_policy.REQUIREMENT; '
                             'assert sys.dont_write_bytecode is False',
                             str(root / 'build_windows.py')], cwd=root, env=env,
                            capture_output=True, text=True, encoding='utf-8')
    assert loader.returncode == 0, loader.stderr
    assert not (root / '__pycache__').exists()
    assert not list(root.rglob('*.pyc'))


def test_builder_restores_bytecode_flag_for_importers(tmp_path):
    # Load a copy: with the flag off, loading the real file would cache build.pyc in the source.
    for name in ('build.py', 'build_preflight.py'):
        shutil.copyfile(ROOT / name, tmp_path / name)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = False
        spec = importlib.util.spec_from_file_location('winscripts_build_copy', tmp_path / 'build.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # this outer load may cache; only the inner one must not
        assert sys.dont_write_bytecode is False
        assert not list(tmp_path.glob('__pycache__/build_preflight*.pyc'))
    finally:
        sys.dont_write_bytecode = previous


# ------------------------------------------- frozen self-test timeout cleanup

class FakeSelfTestProcess:
    instances = []

    def __init__(self, args, **kwargs):
        self.args, self.kwargs = args, kwargs
        self.pid = 4242
        self.killed = False
        self.waits = 0
        self.timeout_first = True
        FakeSelfTestProcess.instances.append(self)

    def wait(self, timeout=None):
        self.waits += 1
        if self.waits == 1 and self.timeout_first:
            raise subprocess.TimeoutExpired(self.args, timeout)
        return 1

    def poll(self):
        return None if not self.killed else 1

    def kill(self):
        self.killed = True


@pytest.fixture
def fake_windows_selftest(monkeypatch, tmp_path):
    builder = load('build')
    FakeSelfTestProcess.instances = []
    runs, temps = [], []
    monkeypatch.setattr(builder, '_WINDOWS', True)
    monkeypatch.setattr(builder.subprocess, 'Popen', FakeSelfTestProcess)
    monkeypatch.setattr(builder.subprocess, 'run',
                        lambda command, **kw: runs.append((command, kw)) or types.SimpleNamespace(returncode=0))
    real_mkdtemp = builder.tempfile.mkdtemp

    def mkdtemp(**kwargs):
        temps.append(real_mkdtemp(dir=tmp_path, **kwargs))
        return temps[-1]
    monkeypatch.setattr(builder.tempfile, 'mkdtemp', mkdtemp)
    return builder, runs, temps


def test_frozen_self_test_timeout_kills_whole_tree_and_reports_timeout(fake_windows_selftest, tmp_path):
    builder, runs, temps = fake_windows_selftest
    with pytest.raises(ValueError, match='시간 초과') as info:
        builder.frozen_self_test(tmp_path / 'WLR_OnAir_Admin.exe', 'ADMIN', '1.0.0', 'f' * 64,
                                 tmp_path / 'Admin_SELFTEST.json', timeout=7)
    assert '7초' in str(info.value)
    command, options = runs[0]
    assert Path(command[0]).name.lower() == 'taskkill.exe'
    assert command[1:] == ['/F', '/T', '/PID', '4242']
    assert options.get('timeout') and options.get('stdin') == subprocess.DEVNULL
    process = FakeSelfTestProcess.instances[0]
    assert process.kwargs.get('creationflags') and process.killed
    assert process.kwargs['env']['LOCALAPPDATA'] == temps[0]
    assert not os.path.exists(temps[0])


def test_locked_temp_folder_does_not_mask_the_timeout(fake_windows_selftest, monkeypatch, tmp_path, capsys):
    builder, runs, temps = fake_windows_selftest
    attempts = []

    def locked(path, *args, **kwargs):
        attempts.append(path)
        raise PermissionError(32, 'The process cannot access the file', str(path))
    monkeypatch.setattr(builder.shutil, 'rmtree', locked)
    monkeypatch.setattr(builder.time, 'sleep', lambda seconds: None)
    with pytest.raises(ValueError, match='시간 초과'):
        builder.frozen_self_test(tmp_path / 'x.exe', 'VIEWER', '1.0.0', 'f' * 64, tmp_path / 'r.json', timeout=1)
    assert len(attempts) > 1  # retried briefly
    assert 'WARNING' in capsys.readouterr().err
    monkeypatch.undo()
    shutil.rmtree(temps[0], ignore_errors=True)


def test_frozen_self_test_success_path_is_unchanged(fake_windows_selftest, tmp_path):
    builder, runs, temps = fake_windows_selftest
    report = tmp_path / 'Admin_SELFTEST.json'

    class Passing(FakeSelfTestProcess):
        def __init__(self, args, **kwargs):
            super().__init__(args, **kwargs)
            self.timeout_first = False
            Path(args[2]).write_text(json.dumps({'ok': True, 'role': 'ADMIN', 'version': '1.0.0',
                                                 'source_fingerprint': 'f' * 64}), encoding='utf-8')

        def wait(self, timeout=None):
            return 0
    builder.subprocess.Popen = Passing
    data = builder.frozen_self_test(tmp_path / 'x.exe', 'ADMIN', '1.0.0', 'f' * 64, report)
    assert data['ok'] is True and runs == []
    assert not os.path.exists(temps[0])


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX process-group variant of the tree kill')
def test_frozen_self_test_timeout_stops_grandchild_on_posix(tmp_path):
    builder = load('build')
    exe = tmp_path / 'fake_onefile.sh'
    exe.write_text('#!/bin/sh\nsleep 60 &\necho $! > "$2.child"\nwait\n', encoding='ascii')
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    report = tmp_path / 'report.json'
    with pytest.raises(ValueError, match='시간 초과'):
        builder.frozen_self_test(exe, 'ADMIN', '1.0.0', 'f' * 64, report, timeout=2)
    child = int((tmp_path / 'report.json.child').read_text().strip())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            state = Path(f'/proc/{child}/stat').read_text().rsplit(')', 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            state = 'gone'
        if state in {'gone', 'Z', 'X'}:
            break
        time.sleep(0.1)
    assert state in {'gone', 'Z', 'X'}, f'grandchild still running ({state})'
