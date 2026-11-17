"""Real subprocess regressions for the formerly unbounded build stage 2."""
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('stage2_bootstrap', ROOT / 'build_windows.py')
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def child(script):
    return [sys.executable, '-I', '-u', '-c', script]


def test_silent_child_has_bounded_deadline_and_keeps_heartbeat(tmp_path):
    log = io.StringIO()
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        bootstrap.run_logged(child('import time; time.sleep(30)'), log, tmp_path,
                             timeout=0.3, heartbeat=0.1, label='[2/5-a] venv')
    assert time.monotonic() - started < 5
    assert '[2/5-a] venv' in log.getvalue()
    assert 'still running' in log.getvalue()
    assert 'TIMEOUT' in log.getvalue()


def test_partial_line_is_logged_before_timeout(tmp_path):
    log = io.StringIO()
    with pytest.raises(subprocess.TimeoutExpired):
        bootstrap.run_logged(child('import sys,time; sys.stdout.write("partial output"); '
                                   'sys.stdout.flush(); time.sleep(30)'), log, tmp_path,
                             timeout=0.4, heartbeat=0.1, label='ensurepip')
    assert 'partial output' in log.getvalue()


def test_silent_success_still_reports_heartbeat(tmp_path):
    log = io.StringIO()
    bootstrap.run_logged(child('import time; time.sleep(0.3)'), log, tmp_path,
                         timeout=3, heartbeat=0.1, label='bundled pip')
    assert 'still running' in log.getvalue()
    assert 'exit code 0' in log.getvalue()


def test_utf8_characters_split_across_output_chunks_are_preserved(tmp_path):
    log = io.StringIO()
    script = ('import os,time; data="한글 경로".encode("utf-8"); '
              'os.write(1, data[:1]); time.sleep(0.05); os.write(1, data[1:]); '
              'os.write(2, b"\\nerror stream\\n")')
    bootstrap.run_logged(child(script), log, tmp_path, timeout=3)
    assert '한글 경로\nerror stream' in log.getvalue()


def test_failed_child_preserves_output_and_original_exit_code(tmp_path):
    log = io.StringIO()
    with pytest.raises(subprocess.CalledProcessError) as error:
        bootstrap.run_logged(child('print("bundled pip failure"); raise SystemExit(19)'),
                             log, tmp_path, timeout=3, label='ensurepip')
    assert error.value.returncode == 19
    assert 'bundled pip failure' in log.getvalue()
    assert 'exit code 19' in log.getvalue()


def test_child_stdin_is_eof_and_polluting_environment_is_removed(tmp_path, monkeypatch):
    for key in ('PYTHONINSPECT', 'PYTHONPATH', 'PYTHONHOME', 'PIP_INDEX_URL', 'VIRTUAL_ENV'):
        monkeypatch.setenv(key, 'polluted')
    script = ('import json,os,sys; print(json.dumps({"stdin": sys.stdin.read(), '
              '"leaks": [k for k in ("PYTHONINSPECT", "PYTHONPATH", "PYTHONHOME", '
              '"PIP_INDEX_URL", "VIRTUAL_ENV") if k in os.environ]}))')
    log = io.StringIO()
    bootstrap.run_logged(child(script), log, tmp_path, timeout=3)
    result = next(json.loads(line) for line in log.getvalue().splitlines()
                  if line.startswith('{"stdin"'))
    assert result == {'stdin': '', 'leaks': []}


def test_stage2_uses_real_local_venv_and_bundled_pip_under_korean_space_path(tmp_path, monkeypatch):
    # This really creates a NEW disposable environment; it never invokes the host pip.
    for key in ('PYTHONINSPECT', 'PYTHONHOME', 'PYTHONPATH', 'PIP_INDEX_URL'):
        monkeypatch.setenv(key, 'polluted')
    env_dir = tmp_path / '한글 빌드 환경'
    log = io.StringIO()
    python = bootstrap.create_build_environment(env_dir, log, tmp_path)
    probe = subprocess.run([str(python), '-I', '-c',
                            'import sys,pip,json; print(json.dumps([sys.prefix, sys.base_prefix, pip.__file__]))'],
                           env=bootstrap.clean_environment(), capture_output=True, text=True,
                           check=True, timeout=10)
    prefix, base, pip_path = json.loads(probe.stdout)
    assert Path(prefix) == env_dir
    assert prefix != base
    assert Path(pip_path).is_relative_to(env_dir)
    text = log.getvalue()
    assert '[2/5-a]' in text and '[2/5-b]' in text
    assert '--without-pip' in text and '-m ensurepip --upgrade --default-pip' in text
    assert '-I -u -X utf8 -m venv' in text


def test_stage2_refuses_reuse_of_an_existing_environment(tmp_path):
    env_dir = tmp_path / 'existing'
    env_dir.mkdir()
    sentinel = env_dir / 'keep.txt'
    sentinel.write_text('previous build must stay intact')
    with pytest.raises(FileExistsError):
        bootstrap.create_build_environment(env_dir, io.StringIO(), tmp_path)
    assert sentinel.read_text() == 'previous build must stay intact'


def process_running(pid):
    status = Path(f'/proc/{pid}/stat')
    if status.exists():
        return status.read_text().split(') ', 1)[1][0] != 'Z'
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process-group check; Windows tree needs Windows acceptance')
def test_timeout_kills_owned_descendant_but_not_unrelated_process(tmp_path):
    pid_file = tmp_path / 'grandchild.pid'
    script = ('import subprocess,sys,time,pathlib; '
              'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
              f'pathlib.Path({str(pid_file)!r}).write_text(str(p.pid)); time.sleep(30)')
    unrelated = subprocess.Popen(child('import time; time.sleep(30)'),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            bootstrap.run_logged(child(script), io.StringIO(), tmp_path,
                                 timeout=0.5, heartbeat=0.1)
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 2
        while process_running(pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not process_running(pid)
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait(timeout=3)
        if pid_file.exists() and process_running(int(pid_file.read_text())):
            os.kill(int(pid_file.read_text()), signal.SIGKILL)


@pytest.mark.skipif(os.name == 'nt', reason='SIGINT injection uses POSIX; actual Ctrl+C requires Windows acceptance')
def test_interrupt_kills_owned_child_and_preserves_keyboard_interrupt(tmp_path):
    pid_file = tmp_path / 'interrupt-child.pid'
    log_file = tmp_path / 'interrupt.log'
    script = ('import os,time,pathlib; '
              f'pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(30)')
    # -I does not imply -B; never leave __pycache__/build_windows*.pyc in the source folder.
    wrapper = ('import importlib.util,sys; sys.dont_write_bytecode=True; '
               f's=importlib.util.spec_from_file_location("bootstrap", {str(ROOT / "build_windows.py")!r}); '
               'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
               f'log=open({str(log_file)!r}, "w", encoding="utf-8"); '
               f'm.run_logged({child(script)!r}, log, {str(tmp_path)!r}, timeout=10, heartbeat=0.1)')
    process = subprocess.Popen(child(wrapper), stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 4
        while not pid_file.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.02)
        assert pid_file.exists(), 'supervised child did not start'
        process.send_signal(signal.SIGINT)
        assert process.wait(timeout=5) != 0
        pid = int(pid_file.read_text())
        assert not process_running(pid)
        assert 'interrupted' in log_file.read_text()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        if pid_file.exists() and process_running(int(pid_file.read_text())):
            os.kill(int(pid_file.read_text()), signal.SIGKILL)


@pytest.mark.parametrize('taskkill_failure', ['missing', 'denied', 'timeout'])
def test_windows_tree_cleanup_failure_falls_back_without_claiming_descendants_stopped(monkeypatch,
                                                                                    taskkill_failure):
    # Only the Windows OS boundary is replaced. This is NOT a Windows tree proof.
    calls = []
    state = {'alive': True, 'killed': False}

    def kill():
        state.update(alive=False, killed=True)

    def taskkill(command, **options):
        calls.append((command, options))
        if taskkill_failure == 'missing':
            raise FileNotFoundError('taskkill not found')
        if taskkill_failure == 'timeout':
            raise subprocess.TimeoutExpired(command, options['timeout'])
        return SimpleNamespace(returncode=5, stdout='Access is denied.\n')

    process = SimpleNamespace(pid=43210, poll=lambda: None if state['alive'] else -9,
                              kill=kill, wait=lambda timeout: -9)
    monkeypatch.setattr(bootstrap, 'os', SimpleNamespace(name='nt', environ=os.environ, devnull=os.devnull))
    monkeypatch.setattr(bootstrap.subprocess, 'run', taskkill)
    log = io.StringIO()
    bootstrap._terminate_owned_process_tree(process, log)
    assert state['killed']
    assert calls[0][0][1:] == ['/PID', '43210', '/T', '/F']
    assert '/IM' not in calls[0][0]
    assert calls[0][1]['stdin'] == subprocess.DEVNULL
    assert calls[0][1]['timeout'] == 5
    assert 'Descendant cleanup is not confirmed' in log.getvalue()
