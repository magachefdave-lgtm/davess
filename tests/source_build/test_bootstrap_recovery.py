"""Long extraction paths and stage-2 diagnostics; no Windows emulation claimed."""
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location('build_recovery', ROOT / 'build_windows.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare_main(m, root, monkeypatch):
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(m, 'ROOT', root)
    monkeypatch.setattr(m, 'builder_module', lambda: SimpleNamespace(
        validate_runtime=lambda: {'version': [3, 12, 0]},
        verify_source_manifest=lambda _: {'version': '3.18.11', 'source_fingerprint': 'a' * 64}))
    monkeypatch.setattr(m, 'requirements_for_runtime', lambda _: root / 'requirements-build-py312.txt')
    monkeypatch.setattr(m, 'prepare_wheelhouse', lambda *a: m.wheelhouse_dir(root) or root / 'test-wheels')


def test_stage2_substeps_visible_even_with_quiet_children(tmp_path, monkeypatch, capsys):
    m = load()
    monkeypatch.setattr(m, 'run_logged', lambda *a, **kw: None)
    m.create_build_environment(tmp_path / 'env', io.StringIO(), tmp_path)
    out = capsys.readouterr().out
    assert '[2/5-a]' in out and '[2/5-b]' in out


def test_failed_child_exposes_bounded_last_error_in_addition_to_full_log(tmp_path):
    m = load()
    log = io.StringIO()
    command = [sys.executable, '-I', '-u', '-c',
               'print("noise" * 10000); print("OSError: path too long"); raise SystemExit(19)']
    with pytest.raises(subprocess.CalledProcessError) as caught:
        m.run_logged(command, log, tmp_path, label='[2/5-b] bundled pip', echo=False)
    assert caught.value.returncode == 19
    assert caught.value.stage == '[2/5-b] bundled pip'
    assert 'OSError: path too long' in caught.value.output
    assert len(caught.value.output) <= 8192
    assert 'noise' * 10000 in log.getvalue()


def test_timeout_keeps_last_output_and_exact_substep(tmp_path):
    m = load()
    command = [sys.executable, '-I', '-u', '-c', 'import time; print("waiting for pip"); time.sleep(30)']
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        m.run_logged(command, io.StringIO(), tmp_path, label='[2/5-b] bundled pip', timeout=.4, heartbeat=.1)
    assert caught.value.stage == '[2/5-b] bundled pip'
    assert 'waiting for pip' in caught.value.output


def test_main_failure_prints_substep_exit_code_and_last_error(tmp_path, monkeypatch, capsys):
    m = load(); prepare_main(m, tmp_path, monkeypatch)
    def failed_env(env, log, cwd):
        m.run_logged([sys.executable, '-I', '-c', 'print("No module named ensurepip"); raise SystemExit(23)'],
                     log, cwd, label='[2/5-b] bundled pip', echo=False)
    monkeypatch.setattr(m, 'create_build_environment', failed_env)
    assert m.main(opener=lambda _: False) == 1
    err = capsys.readouterr().err
    assert '[2/5-b]' in err and '23' in err and 'No module named ensurepip' in err


@pytest.mark.parametrize('fail_after_prepare', [False, True])
def test_long_source_path_uses_real_short_isolated_env_and_cleans_only_own_work(tmp_path, monkeypatch,
                                                                            fail_after_prepare):
    m = load()
    root = tmp_path / ('압축 풀기 ' + 'x' * 75) / ('OnAir_' + 'y' * 75)
    prepare_main(m, root, monkeypatch)
    old_env = root / '.build-envs' / 'old'; old_env.mkdir(parents=True)
    sentinel = old_env / 'keep.txt'; sentinel.write_text('previous build')
    actual_create = m.create_build_environment
    created = []
    def create(env, log, cwd):
        created.append(Path(env))
        python = actual_create(env, log, cwd)
        result = subprocess.run([str(python), '-I', '-c',
                                 'import sys,pip; print(sys.prefix); print(pip.__file__)'],
                                env=m.clean_environment(), capture_output=True, text=True, check=True)
        assert str(env) in result.stdout
        return python
    monkeypatch.setattr(m, 'create_build_environment', create)
    actual_run = m.run_logged
    def run(command, log, cwd, **kw):
        if kw.get('label', '').startswith('[2/5'):
            return actual_run(command, log, cwd, **kw)
        if fail_after_prepare:
            raise subprocess.CalledProcessError(17, command)
        if '--output' in command:
            out = Path(command[command.index('--output') + 1]); out.mkdir(parents=True)
            (out / 'BUILD_SUCCESS.txt').write_text('synthetic compiler success')
    monkeypatch.setattr(m, 'run_logged', run)
    assert m.main(opener=lambda _: False) == int(fail_after_prepare)
    env = created[0]
    assert not env.is_relative_to(root), 'venv must not inherit the extraction path'
    assert len(str(env)) < 100
    assert not env.exists(), 'only the freshly owned environment is removed after exit'
    assert sentinel.read_text() == 'previous build'
    assert list((root / 'build_logs').glob('*.log'))
    if not fail_after_prepare:
        assert list((root / 'dist').glob('*/BUILD_SUCCESS.txt'))


def test_empty_wheelhouse_is_reported_after_stage3_heading(tmp_path, monkeypatch, capsys):
    m = load(); prepare_main(m, tmp_path, monkeypatch)
    (tmp_path / 'wheels').mkdir()
    monkeypatch.setattr(m, 'create_build_environment', lambda env, *_: env / 'python')
    assert m.main(opener=lambda _: False) == 1
    captured = capsys.readouterr()
    assert '[3/5]' in captured.out
    assert 'wheels' in captured.err and '[4/5]' not in captured.out


def test_busy_temporary_file_does_not_turn_completed_build_into_failure(tmp_path, monkeypatch, capsys):
    m = load(); prepare_main(m, tmp_path, monkeypatch)
    work = tmp_path / 'owned-work'; work.mkdir()
    monkeypatch.setattr(m.tempfile, 'mkdtemp', lambda **_: str(work))
    monkeypatch.setattr(m, 'create_build_environment', lambda env, *_: env / 'python')
    def fake_run(command, *a, **kw):
        if '--output' in command:
            out = Path(command[command.index('--output') + 1]); out.mkdir(parents=True)
            (out / 'BUILD_SUCCESS.txt').write_text('synthetic compiler success')
    monkeypatch.setattr(m, 'run_logged', fake_run)
    removed = []
    def busy(path):
        removed.append(path)
        raise PermissionError('file in use')
    monkeypatch.setattr(m.shutil, 'rmtree', busy)
    assert m.main(opener=lambda _: False) == 0
    out = capsys.readouterr().out
    assert '빌드 성공' in out and '자동 정리하지 못했습니다' in out
    assert removed == [work]
    assert list((tmp_path / 'dist').glob('*/BUILD_SUCCESS.txt'))


def test_no_writable_temp_never_installs_into_existing_python(tmp_path, monkeypatch, capsys):
    m = load(); prepare_main(m, tmp_path, monkeypatch)
    def unavailable(**_):
        raise OSError('No usable temporary directory')
    monkeypatch.setattr(m.tempfile, 'mkdtemp', unavailable)
    monkeypatch.setattr(m, 'create_build_environment', lambda *a: pytest.fail('must not create or reuse an environment'))
    monkeypatch.setattr(m, 'run_logged', lambda *a, **kw: pytest.fail('must not invoke host pip'))
    assert m.main(opener=lambda _: False) == 1
    assert 'No usable temporary directory' in capsys.readouterr().err
    assert not (tmp_path / 'dist').exists()
