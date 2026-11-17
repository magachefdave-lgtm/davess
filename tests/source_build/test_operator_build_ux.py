"""3.18.0 운영자 사용성: 버전 올리기, 빌드 창 안내, 배포관리.cmd 함정, 시작하기.txt, wheels 로그.

[위험] 주석은 실제로 비개발자 운영자가 막혔던 지점이다. cmd.exe 를 실제로 돌리지는 않는다
(그 부분은 Windows 확인 목록에 남긴다).
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name, alias=None):
    spec = importlib.util.spec_from_file_location(alias or f'opux_{name}', ROOT / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load('build')


def read_cmd(name):
    return (ROOT / name).read_bytes().decode('cp949').replace('\r\n', '\n')


def label_block(text, label):
    body = text.split(f'\n:{label}\n', 1)[1]
    lines = []
    for line in body.split('\n'):
        if line.startswith(':'):
            break
        lines.append(line)
    return lines


@pytest.fixture
def source(tmp_path):
    """매니페스트가 맞는 작은 소스 폴더 사본(문서·시험 폴더 포함 - SHA256SUMS 범위 확인용)."""
    root = tmp_path / '한글 소스'
    root.mkdir()
    for name in ('backend', 'updater', 'frontend', 'examples'):
        shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns('__pycache__'))
    for name in builder.SOURCE_FILES:
        if not (root / name).exists():
            shutil.copyfile(ROOT / name, root / name)
    shutil.copyfile(ROOT / 'README.md', root / 'README.md')
    (root / 'build_logs').mkdir()
    (root / 'build_logs' / 'old.log').write_text('artifact')        # 산출물: SHA256SUMS 대상 아님
    (root / 'updater' / '__pycache__').mkdir()
    (root / 'updater' / '__pycache__' / 'x.pyc').write_bytes(b'cache')
    version = (root / 'VERSION').read_text(encoding='utf-8').strip()
    for name in ('backend/main.py', 'entrypoint.py'):           # 진행 중인 다른 작업과 무관하게 버전을 맞춘다
        path = root / name
        path.write_text(re.sub(r"^APP_VERSION = '[^']*'$", f"APP_VERSION = '{version}'",
                               path.read_text(encoding='utf-8'), flags=re.M), encoding='utf-8')
    builder.write_json(root / 'SOURCE_MANIFEST.json', builder.create_source_manifest(root))
    return root, version


def sums_by_walk(root):
    rows = []
    for path in sorted(root.rglob('*'), key=lambda p: p.relative_to(root).as_posix().encode()):
        rel = path.relative_to(root).as_posix()
        if path.is_file() and rel != 'SHA256SUMS.txt' and not rel.startswith('build_logs/') and '__pycache__' not in rel:
            rows.append(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {rel}\n')
    return ''.join(rows)


# ------------------------------------------------------------------ 1. 버전 올리기

# [위험] 버전을 올리려면 VERSION·main.py·entrypoint.py·매니페스트·SHA256SUMS 를 손으로 맞춰야 했다.
def test_bump_version_updates_every_place_and_manifests(source):
    root, old = source
    new = builder.next_patch_version(old)
    out = []
    result = builder.bump_version(root, new, out=out.append)
    assert result == {'old': old, 'new': new, 'changed': []}
    assert (root / 'VERSION').read_text(encoding='utf-8') == new + '\n'
    main = (root / 'backend/main.py').read_text(encoding='utf-8')
    assert f"APP_VERSION = '{new}'" in main and main.splitlines()[0].startswith(f'"""WLR OnAir {new} ')
    assert f"APP_VERSION = '{new}'" in (root / 'entrypoint.py').read_text(encoding='utf-8')
    assert builder.verify_source_manifest(root)['version'] == new
    assert (root / 'SHA256SUMS.txt').read_text(encoding='utf-8') == sums_by_walk(root)
    assert 'frontend/dist/index.html' in (root / 'SHA256SUMS.txt').read_text(encoding='utf-8')
    assert any('build_windows.cmd' in line for line in out)          # 다음 단계를 알려 준다


def test_bump_refuses_same_or_lower_and_bad_format_without_touching_files(source):
    root, old = source
    before = {p: p.read_bytes() for p in (root / 'VERSION', root / 'SOURCE_MANIFEST.json')}
    for bad in (old, '0.0.1', '3.18', 'v3.18.0', '3.18.0.1', '70000.0.0'):
        with pytest.raises(ValueError):
            builder.bump_version(root, bad, out=lambda *_: None)
    assert {p: p.read_bytes() for p in before} == before


# [위험] 누가 소스를 몰래 고쳤어도 버전만 올리면 그 수정이 조용히 '신뢰됨'으로 기록된다.
def test_bump_stops_on_other_edited_sources_unless_accepted(source):
    root, old = source
    target = root / 'updater/core.py'
    target.write_text(target.read_text(encoding='utf-8') + '\n# edited\n', encoding='utf-8')
    with pytest.raises(ValueError, match='updater/core.py'):
        builder.bump_version(root, builder.next_patch_version(old), out=lambda *_: None)
    assert (root / 'VERSION').read_text(encoding='utf-8').strip() == old
    result = builder.bump_version(root, builder.next_patch_version(old), accept_changed=True, out=lambda *_: None)
    assert result['changed'] == ['updater/core.py']
    builder.verify_source_manifest(root)


def test_bump_failure_restores_original_files(source, monkeypatch):
    root, old = source
    before = {name: (root / name).read_bytes() for name in builder.VERSION_FILES + ('SOURCE_MANIFEST.json',)}
    monkeypatch.setattr(builder, 'sha256sums_text', lambda root: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        builder.bump_version(root, builder.next_patch_version(old), out=lambda *_: None)
    assert {name: (root / name).read_bytes() for name in before} == before


def test_interactive_bump_defaults_to_next_patch_and_can_be_cancelled(source):
    root, old = source
    answers = iter([''])
    lines = []
    assert builder.bump_version_interactive(root, ask=lambda _: next(answers), out=lines.append) == 0
    assert (root / 'VERSION').read_text(encoding='utf-8').strip() == builder.next_patch_version(old)
    # 다른 수정이 있으면 y/N 을 묻고, N 이면 아무것도 바꾸지 않는다.
    (root / 'updater/core.py').write_text('# changed\n', encoding='utf-8')
    current = (root / 'VERSION').read_text(encoding='utf-8')
    answers = iter(['', 'n'])
    assert builder.bump_version_interactive(root, ask=lambda _: next(answers), out=lines.append) == 1
    assert (root / 'VERSION').read_text(encoding='utf-8') == current
    assert any('아무 파일도 바꾸지 않았습니다' in line for line in lines)


def test_command_line_bump_runs_isolated_and_reports_errors(source):
    root, old = source
    run = subprocess.run([sys.executable, '-I', '-B', str(root / 'build.py'), '--bump-version', old],
                         capture_output=True, text=True, encoding='utf-8')
    assert run.returncode == 1 and '[오류]' in run.stderr and 'Traceback' not in run.stderr
    new = builder.next_patch_version(old)
    run = subprocess.run([sys.executable, '-I', '-B', str(root / 'build.py'), '--bump-version', new],
                         capture_output=True, text=True, encoding='utf-8')
    assert run.returncode == 0, run.stderr
    check = subprocess.run([sys.executable, '-I', '-B', str(root / 'build.py'), '--check'],
                           capture_output=True, text=True, encoding='utf-8')
    assert check.returncode == 0 and f'"version": "{new}"' in check.stdout
    assert not list(root.rglob('build*.pyc'))


# [위험] 버전 불일치 오류가 '버전 불일치: VERSION / backend/main.py' 로만 나와 무엇을 할지 몰랐다.
def test_version_mismatch_messages_point_to_bump_tool(source):
    root, old = source
    (root / 'VERSION').write_text(builder.next_patch_version(old) + '\n', encoding='utf-8')
    with pytest.raises(ValueError, match='버전올리기.cmd'):
        builder.source_version(root)
    with pytest.raises(ValueError, match='버전올리기.cmd'):
        builder.verify_source_manifest(root)
    check = subprocess.run([sys.executable, '-I', '-B', str(root / 'build.py'), '--check'],
                           capture_output=True, text=True, encoding='utf-8')
    assert check.returncode == 1 and '버전올리기.cmd' in check.stderr and 'Traceback' not in check.stderr


def test_bump_cmd_finds_python_like_collect_wheels_and_calls_bump():
    raw = (ROOT / '버전올리기.cmd').read_bytes()
    assert raw.endswith(b'\r\n') and b'\n' not in raw.replace(b'\r\n', b'')
    text = read_cmd('버전올리기.cmd')
    collect = read_cmd('collect_wheels.cmd')
    for label in ('from_launcher', 'normalize_candidate', 'try_candidate', 'store_alias'):
        assert label_block(text, label) == label_block(collect, label), label
    assert '"%ONAIR_PY%" -I -B "%~dp0build.py" --bump-version' in text
    assert 'if not exist "build.py" goto :not_source' in text
    for label in ('failed', 'no_python', 'not_source', 'folder_error'):
        block = [x.strip() for x in label_block(text, label)]
        assert 'pause' in block and 'exit /b 1' in block, label
    assert not re.search(r'(?im)^\s*@?chcp\b', text)


# ------------------------------------------------------------------ 5. 빌드 창

class FakeBuilder:
    def __init__(self, output_ok=True):
        self.output_ok = output_ok

    def validate_runtime(self, info=None):
        return {'version': [3, 13, 5]}

    def verify_source_manifest(self, root):
        return {'version': '3.18.0', 'source_fingerprint': 'f' * 64}


@pytest.fixture
def windows_build(tmp_path, monkeypatch):
    bootstrap = load('build_windows', 'opux_build_windows')
    monkeypatch.setattr(bootstrap, 'ROOT', tmp_path)
    monkeypatch.setattr(bootstrap, 'builder_module', lambda: FakeBuilder())
    monkeypatch.setattr(bootstrap, 'requirements_for_runtime', lambda info: tmp_path / 'requirements-build-py313.txt')
    monkeypatch.setattr(bootstrap, 'create_build_environment', lambda env, log, cwd: env / 'python')
    monkeypatch.setattr(bootstrap, 'wheelhouse_dir', lambda root: None)
    monkeypatch.setattr(bootstrap, 'prepare_wheelhouse', lambda *a: tmp_path / 'wheels')
    return bootstrap


def test_build_window_shows_korean_steps_durations_and_opens_output(windows_build, monkeypatch, capsys, tmp_path):
    commands=[]
    def fake_run(command, log, cwd, **kw):
        commands.append([str(x) for x in command])
        log.write('Collecting fastapi (hash-pinned) GIL normal\n')
        if '--output' in [str(x) for x in command]:
            output = Path(command[command.index('--output') + 1])
            output.mkdir(parents=True)
            (output / 'BUILD_SUCCESS.txt').write_text('ok')
    monkeypatch.setattr(windows_build, 'run_logged', fake_run)
    opened = []
    assert windows_build.main(opener=lambda p: opened.append(Path(p)) or True) == 0
    out = capsys.readouterr().out
    for step in ('[1/5] 빌드 준비 확인', '[2/5]', '[3/5] 필요한 부품 설치', '[4/5]', '[5/5] 마무리'):
        assert step in out
    assert '예상: 약 5~15분' in out
    assert 'GIL' not in out and 'hash-pinned' not in out                 # 전문 용어는 로그에만
    assert opened and opened[0].name.startswith('source-build-')
    assert '배포관리.cmd' in out and '탐색기로 열었습니다' in out
    log = next((tmp_path / 'build_logs').glob('*.log')).read_text(encoding='utf-8')
    assert 'hash-pinned' in log
    assert any(command[-2:]==['pip','check'] for command in commands)
    assert not any('pip' in command and 'list' in command for command in commands)


def test_build_failure_says_where_the_log_is_and_what_to_send(windows_build, monkeypatch, capsys, tmp_path):
    def fail(command, log, cwd, **kw):
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(windows_build, 'run_logged', fail)
    opened = []
    assert windows_build.main(opener=lambda p: opened.append(Path(p)) or True) == 1
    err = capsys.readouterr().err
    log = next((tmp_path / 'build_logs').glob('*.log'))
    assert str(log) in err and '로그 파일 하나와' in err and '[빌드 실패]' in err
    assert opened == [tmp_path / 'build_logs']


def test_child_output_goes_to_log_only_unless_verbose(capsys, tmp_path):
    bootstrap = load('build_windows', 'opux_bw_verbose')
    import io
    log = io.StringIO()
    bootstrap.run_logged([sys.executable, '-c', 'print("pip noise")'], log, tmp_path, label='x')
    assert 'pip noise' in log.getvalue() and 'pip noise' not in capsys.readouterr().out
    bootstrap.VERBOSE = True
    bootstrap.run_logged([sys.executable, '-c', 'print("pip noise")'], io.StringIO(), tmp_path, label='x')
    assert 'pip noise' in capsys.readouterr().out


def test_heartbeat_on_console_is_korean_with_expected_duration(capsys, tmp_path):
    bootstrap = load('build_windows', 'opux_bw_heartbeat')
    import io
    log = io.StringIO()
    bootstrap.run_logged([sys.executable, '-c', 'import time; time.sleep(0.4)'], log, tmp_path,
                         label='[4/5] x', heartbeat=0.1, expected='5~15분')
    out = capsys.readouterr().out
    assert '진행 중' in out and '보통 5~15분' in out and 'still running' not in out
    assert 'still running' in log.getvalue()


def test_preflight_adds_plain_korean_summary_and_stays_ascii_source():
    raw = (ROOT / 'build_preflight.py').read_bytes()
    assert raw.isascii()
    preflight = load('build_preflight', 'opux_preflight')
    info = dict(implementation='cpython', version=[3, 15, 0], releaselevel='alpha', platform='win32',
                machine='AMD64', bits=64, gil_disabled=False, sysconfig_platform='win-amd64')
    import contextlib
    import io
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert preflight.check_candidate(None, info) == 10
    assert '사용 불가' in buffer.getvalue() and '64비트 Windows용 Python 3.11~3.14' in buffer.getvalue()


def test_validate_runtime_message_is_plain_korean():
    with pytest.raises(ValueError) as caught:
        builder.validate_runtime(dict(implementation='cpython', version=[3, 10, 1], releaselevel='final',
                                      platform='win32', machine='AMD64', bits=64, gil_disabled=False,
                                      sysconfig_platform='win-amd64'))
    message = str(caught.value)
    assert message.startswith('이 Python 으로는 빌드할 수 없습니다') and '시작하기.txt' in message


# [위험] 소스 폴더 맨 위의 배포관리.cmd 를 누르면 'EXE를 찾을 수 없습니다'만 나오고 끝났다.
def test_root_deploy_manager_points_to_latest_finished_build():
    text = read_cmd('배포관리.cmd')
    assert text.index('if exist "Admin\\WLR_OnAir_Admin.exe" goto :built') < text.index('if exist "build.py" goto :source_folder')
    block = label_block(text, 'source_folder')
    joined = '\n'.join(block)
    assert any('이 파일은 빌드가 끝난 폴더에서 실행하세요' in line for line in block)
    assert 'dir /b /ad /o-n "dist\\source-build-*"' in joined and 'BUILD_SUCCESS.txt' in joined
    assert 'start "" explorer.exe "%~dp0dist\\%ONAIR_LATEST%"' in joined
    assert [x.strip() for x in block][-2:] == ['pause', 'exit /b 1']
    no_build = [x.strip() for x in label_block(text, 'no_build')]
    assert any('build_windows.cmd' in x for x in no_build) and 'pause' in no_build


def test_build_output_gets_start_guide(tmp_path):
    out = tmp_path / 'out'
    out.mkdir()
    exe = tmp_path / 'a.exe'
    exe.write_bytes(b'MZ')
    builder.package_release(out, '3.18.0', {'ADMIN': exe})
    assert (out / '시작하기.txt').read_bytes() == (ROOT / '시작하기.txt').read_bytes()
    assert (out / '배포관리.cmd').is_file()


# ------------------------------------------------------------------ 4. 시작하기.txt

def test_start_guide_is_notepad_friendly_and_covers_end_to_end_steps():
    raw = (ROOT / '시작하기.txt').read_bytes()
    assert raw.startswith(b'\xef\xbb\xbf')                     # 메모장이 한글을 바로 보이게
    assert b'\n' not in raw.replace(b'\r\n', b'')
    text = raw.decode('utf-8-sig')
    numbers = [int(n) for n in re.findall(r'^(\d+)\. ', text, re.M)]
    assert numbers[:10] == list(range(1, 11))
    for words in ('python.org', '64-bit', 'collect_wheels.cmd', 'build_windows.cmd', '배포관리.cmd',
                  '키 백업', '설치 ZIP', '기존 공유 폴더 사용', '이 PC를 새로 연결', '버전올리기.cmd',
                  '되돌리기', 'build_logs', '로그 폴더 열기', '차단된 업데이트 다시 허용'):
        assert words in text, words


def test_readme_points_to_start_guide_first():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    first_link = readme.index('시작하기.txt')
    assert first_link < readme.index('## 빌드와 게시') < readme.index('WINDOWS_BUILD_GUIDE.md')
    assert '버전올리기.cmd' in readme
