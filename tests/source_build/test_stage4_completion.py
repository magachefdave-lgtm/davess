"""Build completion follows the owned process, not inherited stdout handles."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_bootstrap():
    spec = importlib.util.spec_from_file_location('onair_stage4_bootstrap', ROOT / 'build_windows.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('exit_code', [0, 17])
def test_completed_process_does_not_wait_for_descendant_stdout(tmp_path, exit_code):
    bootstrap = load_bootstrap()
    log = io.StringIO()
    script = ('import subprocess,sys; '
              'subprocess.Popen([sys.executable,"-c","import time; time.sleep(1.5)"]); '
              'print("Viewer self-test complete",flush=True); '
              f'raise SystemExit({exit_code})')
    start = time.monotonic()
    if exit_code:
        with pytest.raises(subprocess.CalledProcessError) as caught:
            bootstrap.run_logged([sys.executable, '-c', script], log, tmp_path,
                                 timeout=.7, heartbeat=.05, label='[4/5] viewer')
        assert caught.value.returncode == 17
        assert 'Viewer self-test complete' in caught.value.output
    else:
        bootstrap.run_logged([sys.executable, '-c', script], log, tmp_path,
                             timeout=.7, heartbeat=.05, label='[4/5] viewer')
    assert time.monotonic() - start < .7
    assert 'Viewer self-test complete' in log.getvalue()


def test_quiet_output_relays_only_explicit_build_progress(tmp_path, capsys):
    bootstrap = load_bootstrap()
    log = io.StringIO()
    script = ('import sys,time; '
              'print("dependency internal noise",flush=True); '
              'sys.stdout.buffer.write("ONAIR_BUILD_PROGRESS 조회용: 자체점검 완료\\n".encode()); '
              'sys.stdout.buffer.flush()')
    bootstrap.run_logged([sys.executable, '-X', 'utf8', '-c', script], log, tmp_path, echo=False)
    out = capsys.readouterr().out
    assert '조회용: 자체점검 완료' in out
    assert 'dependency internal noise' not in out
    assert 'dependency internal noise' in log.getvalue()
    assert '조회용: 자체점검 완료' in log.getvalue()


def test_stage4_launcher_has_finite_deadline_and_explicit_utf8(tmp_path, monkeypatch):
    bootstrap = load_bootstrap()
    # Exercise the existing bootstrap entrypoint with the slow/external operations
    # replaced at their boundary; inspect the actual emitted compiler invocation.
    monkeypatch.setattr(bootstrap, 'ROOT', tmp_path)
    (tmp_path / 'build_logs').mkdir()
    (tmp_path / 'requirements-build-py312.txt').write_text('')
    class Builder:
        def validate_runtime(self): return {'version': [3, 12, 14]}
        def verify_source_manifest(self, root): return {'version': '3.18.27', 'source_fingerprint': 'f'*64}
    monkeypatch.setattr(bootstrap, 'builder_module', lambda: Builder())
    monkeypatch.setattr(bootstrap, 'requirements_for_runtime', lambda info: tmp_path/'requirements-build-py312.txt')
    monkeypatch.setattr(bootstrap, 'create_build_environment', lambda *a: Path(sys.executable))
    monkeypatch.setattr(bootstrap, 'prepare_wheelhouse', lambda *a: tmp_path)
    observed = []
    def run(command, log, cwd, **options):
        if '--output' in command:
            observed.append((command, options))
            output = Path(command[command.index('--output')+1]);output.mkdir(parents=True)
            (output/'BUILD_SUCCESS.txt').write_text('pass')
    monkeypatch.setattr(bootstrap, 'run_logged', run)
    assert bootstrap.main(opener=lambda path: True) == 0
    command, options = observed[0]
    assert 0 < options.get('timeout', 0) <= 3600
    assert '-u' in command and command[command.index('-X')+1] == 'utf8'


def load_builder():
    spec = importlib.util.spec_from_file_location('onair_stage4_builder', ROOT / 'build.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_role_compile_timeout_preserves_diagnostics(tmp_path, monkeypatch):
    builder = load_builder()
    command = [sys.executable, '-c', 'import time; print("compiler last line",flush=True); time.sleep(20)']
    monkeypatch.setattr(builder, 'pyinstaller_command', lambda *args: command)
    logfile = tmp_path / 'Viewer_BUILD.log'
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        builder.compile_role(tmp_path, tmp_path/'work', tmp_path/'dist', 'VIEWER', logfile, timeout=.3)
    assert 'compiler last line' in logfile.read_text(encoding='utf-8')
    assert 'compiler last line' in caught.value.output
    assert 'VIEWER' in caught.value.stage or '조회용' in caught.value.stage


def test_build_exposes_post_exe_checks_and_zip_completion(tmp_path, monkeypatch, capsys):
    builder = load_builder()
    version = (ROOT/'VERSION').read_text().strip()
    monkeypatch.setattr(builder, 'validate_runtime', lambda: {'platform': 'win32'})
    monkeypatch.setattr(builder, 'verify_source_manifest', lambda root: builder.create_source_manifest(root))
    events = []
    def compile_role(stage, work, dist, role, logfile, **options):
        events.append(('compile',role))
        dist.mkdir(parents=True)
        (dist/f'WLR_OnAir_{builder.ROLES[role]}.exe').write_bytes(b'INERT TEST '+role.encode())
    monkeypatch.setattr(builder, 'compile_role', compile_role, raising=False)
    def archive(executable, role, ver, info):
        events.append(('archive', role));return {'archive_verified': True}
    def selftest(executable, role, ver, fingerprint, report_path):
        events.append(('selftest',role));value={'ok':True,'role':role,'version':ver,'source_fingerprint':fingerprint}
        report_path.write_text(json.dumps(value));return value
    monkeypatch.setattr(builder, 'verify_archive', archive)
    monkeypatch.setattr(builder, 'frozen_self_test', selftest)
    monkeypatch.setattr(builder, 'installed_packages', lambda: {})
    # If the old direct subprocess.run path is still used, fail before invoking
    # the real platform-specific PyInstaller, rather than testing a fake build.
    monkeypatch.setattr(builder.subprocess, 'run', lambda *a,**kw: pytest.fail('unbounded compiler invocation'))
    output = tmp_path/'output'
    builder.build(ROOT, output)
    assert events == [('compile','ADMIN'),('archive','ADMIN'),('selftest','ADMIN'),
                      ('compile','VIEWER'),('archive','VIEWER'),('selftest','VIEWER'),
                      ('compile','MASTER'),('archive','MASTER'),('selftest','MASTER')]
    text = capsys.readouterr().out
    assert text.count('ONAIR_BUILD_PROGRESS ') >= 8
    assert '관리자용' in text and '조회용' in text and 'ZIP' in text
    assert '자체점검 완료' in text and '전체 빌드 완료' in text
    assert (output/'BUILD_SUCCESS.txt').is_file() and not (output/'BUILD_INCOMPLETE.txt').exists()
    assert (output/f'WLR_OnAir_v{version}_Update.zip').is_file()


@pytest.mark.skipif(os.name == 'nt' or not Path('/proc').exists(), reason='Linux nested-session cleanup')
def test_timeout_also_stops_owned_nested_session(tmp_path):
    import signal
    bootstrap = load_bootstrap()
    marker = tmp_path/'nested.pid'
    nested = ('import os,time; from pathlib import Path; '
              f'Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(20)')
    script = ('import subprocess,sys,time; '
              f'subprocess.Popen([sys.executable,"-c",{nested!r}],start_new_session=True); time.sleep(20)')
    unrelated = subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'])
    def alive(pid):
        p=Path(f'/proc/{pid}/stat')
        return p.exists() and p.read_text().rsplit(')',1)[1].split()[0]!='Z'
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            bootstrap.run_logged([sys.executable,'-c',script],io.StringIO(),tmp_path,timeout=.5,heartbeat=.1)
        pid=int(marker.read_text())
        end=time.monotonic()+.5
        while alive(pid) and time.monotonic()<end:time.sleep(.02)
        assert not alive(pid)
        assert unrelated.poll() is None
    finally:
        unrelated.kill();unrelated.wait(timeout=3)
        if marker.exists() and alive(int(marker.read_text())):
            os.killpg(int(marker.read_text()),signal.SIGKILL)


def test_cleanup_does_not_target_an_already_exited_process(monkeypatch):
    from types import SimpleNamespace
    bootstrap = load_bootstrap()
    calls=[]
    monkeypatch.setattr(bootstrap.os, 'killpg', lambda *args: calls.append(args), raising=False)
    monkeypatch.setattr(bootstrap.subprocess, 'run', lambda *a,**kw: calls.append(a) or SimpleNamespace(returncode=0,stdout=''))
    process=SimpleNamespace(pid=4242,poll=lambda:0,wait=lambda timeout:0,
                            kill=lambda:pytest.fail('already exited process must not be killed'))
    bootstrap._terminate_owned_process_tree(process,io.StringIO())
    assert not calls


@pytest.mark.skipif(os.name == 'nt', reason='POSIX ownership guard')
def test_cleanup_never_targets_a_root_it_has_already_reaped(monkeypatch):
    from types import SimpleNamespace
    bootstrap=load_bootstrap();observed={'polls':0,'reaped':False,'killed':False}
    def poll():
        observed['polls']+=1
        if observed['polls']==1:return None
        observed['reaped']=True
        return 0
    process=SimpleNamespace(pid=424242,poll=poll,wait=lambda timeout:0,
                            kill=lambda:pytest.fail('already exited'))
    monkeypatch.setattr(bootstrap,'_linux_descendants',lambda pid:[])
    def stop_group(pid, signum):
        assert not observed['reaped'], 'must not target a reaped/reusable process ID'
        observed['killed']=True
    monkeypatch.setattr(bootstrap.os,'killpg',stop_group)
    bootstrap._terminate_owned_process_tree(process,io.StringIO())
    assert observed['killed'] or observed['reaped']
