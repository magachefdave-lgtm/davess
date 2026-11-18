"""User-facing bootstrap; installs only into a NEW disposable local virtualenv."""
from __future__ import annotations

from datetime import datetime
import codecs
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import uuid

ROOT = Path(__file__).resolve().parent


def clean_environment():
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(('PIP_', 'PYTHON', '_PYI_'))
           and k.upper() not in {'VIRTUAL_ENV', '__PYVENV_LAUNCHER__'}}
    env['PIP_CONFIG_FILE'] = os.devnull
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    return env


WHEELHOUSE_NAME = 'wheels'


def wheelhouse_dir(root=ROOT):
    """오프라인 빌드용 wheel 모음 폴더. 사내망에는 PyPI 가 없으므로,
    인터넷 PC 에서 collect_wheels.py 로 만든 wheels/ 가 있으면 그쪽을 쓴다.
    폴더가 없으면 검증된 로컬 캐시를 쓰고 누락분만 공식 PyPI 에서 받는다."""
    path = Path(root) / WHEELHOUSE_NAME
    if not path.is_dir():
        return None
    if not any(path.glob('*.whl')):
        raise FileNotFoundError(
            f'{WHEELHOUSE_NAME} 폴더가 비어 있습니다. 인터넷이 되는 PC 에서 '
            'collect_wheels.cmd 를 실행해 wheel 을 채우거나, 이 폴더를 지우고 '
            'PyPI 에서 내려받도록 하세요.')
    return path


def install_command(python, requirements, wheelhouse=None):
    """해시 고정 설치 명령. wheelhouse 가 있으면 네트워크를 쓰지 않는다.
    --require-hashes 와 --only-binary 는 두 경로 모두에서 유지한다."""
    source = (['--no-index', '--find-links', str(wheelhouse)] if wheelhouse
              else ['--index-url', 'https://pypi.org/simple'])
    return [str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input',
            *source, '--only-binary=:all:', '--require-hashes', '--no-deps', '--no-compile',
            '-r', str(requirements)]


def download_command(python, requirements, destination):
    # ABI locks already list every dependency. Avoid redundant resolver/index work.
    return [str(python), '-m', 'pip', 'download', '--disable-pip-version-check', '--no-input',
            '--index-url', 'https://pypi.org/simple', '--only-binary=:all:', '--require-hashes',
            '--no-deps', '--retries', '1', '--timeout', '20', '--progress-bar', 'off',
            '--dest', str(destination), '-r', str(requirements)]


def locked_wheels(requirements):
    """Read the complete, one-pin-per-line ABI lock; never accept unpinned cache inputs."""
    rows = []
    for line in Path(requirements).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*==[^\s;]+\s+--hash=sha256:([a-f0-9]{64})', line)
        if not match:
            raise ValueError('빌드 패키지 고정 목록 형식이 올바르지 않습니다: ' + Path(requirements).name)
        rows.append((line, match.group(1)))
    if not rows:
        raise ValueError('빌드 패키지 고정 목록이 비어 있습니다.')
    return rows


def wheel_cache_dir(requirements):
    base = Path(os.environ['LOCALAPPDATA']) if os.environ.get('LOCALAPPDATA') else Path.home() / '.cache'
    key = hashlib.sha256(Path(requirements).read_bytes()).hexdigest()[:16]
    return base / 'WLR_OnAir' / 'build-wheels' / (Path(requirements).stem + '-' + key)


def verified_wheels(folder, rows):
    wanted = {digest for _, digest in rows}
    found = {}
    if folder is None:
        return found
    for wheel in Path(folder).glob('*.whl'):
        try:
            digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        except OSError:
            continue
        if digest in wanted:
            found[digest] = wheel
    return found


def prepare_wheelhouse(python, requirements, workspace, log, root=ROOT):
    supplied = wheelhouse_dir(root)
    if supplied is not None:
        _say('   wheels 폴더에서 설치합니다. (인터넷 없이)')
        return supplied
    rows = locked_wheels(requirements)
    cache = wheel_cache_dir(requirements)
    try:
        cache.mkdir(parents=True, exist_ok=True)
        found = verified_wheels(cache, rows)
    except OSError as exc:
        _write_log(f'Package cache unavailable: {exc}\n', log, False)
        _say('   저장 공간에 접근할 수 없어 이번 빌드에서만 패키지를 준비합니다.')
        cache, found = None, {}
    missing = [(line, digest) for line, digest in rows if digest not in found]
    if not missing:
        _say(f'   저장된 패키지 {len(rows)}개를 재사용합니다. (인터넷 없이)')
        _write_log(f'Verified wheel cache hit: {cache}\n', log, False)
        return cache
    stage = Path(workspace) / 'wheels'
    stage.mkdir()
    for path in found.values():
        shutil.copyfile(path, stage / path.name)
    missing_lock = Path(workspace) / 'missing-wheels.txt'
    missing_lock.write_text('\n'.join(line for line, _ in missing) + '\n', encoding='utf-8')
    _say(f'   저장된 {len(found)}개 재사용 · 필요한 {len(missing)}개만 PyPI에서 내려받습니다.')
    run_logged(download_command(python, missing_lock, stage), log, root, timeout=600,
               label='[3/5-a] Download missing wheels', expected='첫 다운로드는 네트워크 상태에 따라 다름')
    downloaded = verified_wheels(stage, rows)
    if len(downloaded) != len({digest for _, digest in rows}):
        raise ValueError('내려받은 패키지의 수 또는 해시가 맞지 않습니다. 설치하지 않았습니다.')
    # Publish verified, complete files atomically. A cache failure must not spoil this build.
    if cache is not None:
        for _, digest in missing:
            wheel = downloaded[digest]
            pending = cache / (wheel.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                shutil.copyfile(wheel, pending)
                pending.replace(cache / wheel.name)
            except OSError as exc:
                _write_log(f'Could not retain wheel {wheel.name}: {exc}\n', log, False)
                _say('   패키지 저장을 완료하지 못했습니다. 현재 빌드는 계속합니다.')
                break
            finally:
                try:
                    pending.unlink(missing_ok=True)
                except OSError:
                    pass
    return stage


# 자식 프로그램(pip, PyInstaller)의 전체 출력은 로그 파일에만 남긴다. 화면에는 한국어 단계와
# 진행 시간만 보인다. 전부 화면에서 보려면 ONAIR_BUILD_VERBOSE=1 로 실행한다(자세히).
VERBOSE = os.environ.get('ONAIR_BUILD_VERBOSE', '').strip() == '1'


def _say(text):
    """화면 전용 안내(로그에는 쓰지 않는다)."""
    print(text, flush=True)


def _write_log(text, log, echo=True):
    if echo:
        print(text, end='', flush=True)
    log.write(text)
    log.flush()


def _elapsed_text(seconds):
    seconds = int(seconds)
    return f'{seconds // 60}분 {seconds % 60:02d}초' if seconds >= 60 else f'{seconds}초'


def _linux_descendants(root_pid):
    """Retain creation identities for owned children that start a new session."""
    rows = {}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            fields = path.read_text().rsplit(')', 1)[1].split()
            rows[int(path.parent.name)] = (int(fields[1]), int(fields[2]), int(fields[19]))
        except (OSError, ValueError, IndexError):
            continue
    owned, pending = [], [root_pid]
    seen = {root_pid}
    while pending:
        parent = pending.pop()
        for pid, (ppid, group, created) in rows.items():
            if ppid == parent and pid not in seen:
                seen.add(pid); pending.append(pid); owned.append((pid, group, created))
    return owned


def _terminate_owned_process_tree(process, log):
    """Only stop the process group/tree created by this run, never Python by name."""
    if process.poll() is not None:
        # The PID is no longer owned after reaping. Never target a reused PID.
        return
    if os.name == 'nt':
        taskkill = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/taskkill.exe'
        try:
            result = subprocess.run(
                [str(taskkill), '/PID', str(process.pid), '/T', '/F'],
                env=clean_environment(), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace', timeout=5)
            if result.stdout:
                _write_log(result.stdout, log)
            if result.returncode:
                _write_log(f'WARNING: tree cleanup returned {result.returncode}; '
                           'will stop the direct child. Descendant cleanup is not confirmed.\n', log)
        except (OSError, subprocess.TimeoutExpired) as exc:
            _write_log(f'WARNING: tree cleanup unavailable ({exc}); '
                       'will stop the direct child. Descendant cleanup is not confirmed.\n', log)
    else:
        # The initial live guard retains ownership. Do not poll/reap again
        # before group targeting: reaping could release the PID for reuse.
        descendants = _linux_descendants(process.pid)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as exc:
            _write_log(f'WARNING: process-group cleanup failed ({exc}); '
                       'descendant cleanup is not confirmed.\n', log)
        for pid, group, created in reversed(descendants):
            try:
                fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
                if int(fields[19]) != created:
                    continue
                if group == pid:
                    os.killpg(pid, signal.SIGKILL)
                else:
                    os.kill(pid, signal.SIGKILL)
            except (OSError, ValueError, IndexError):
                pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError as exc:
            _write_log(f'WARNING: direct child cleanup failed: {exc}\n', log)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _write_log(f'WARNING: process {process.pid} has not stopped; cleanup is not confirmed.\n', log)


def run_logged(command, log, cwd, *, timeout=None, label=None, heartbeat=10.0, echo=None, expected=None):
    """Tail a regular output file and finish when our process exits, never on pipe EOF.

    A hook or frozen bootloader can pass its stdout handle to another process.
    That inherited handle must not keep a completed build waiting indefinitely.
    Separate read/write opens also prevent the reader from moving the child's
    write position. Windows temporary handles delete the spool on last close.
    """
    if timeout is not None and timeout <= 0:
        raise ValueError('timeout must be positive')
    if heartbeat <= 0:
        raise ValueError('heartbeat must be positive')
    echo = VERBOSE if echo is None else echo
    line = subprocess.list2cmdline([str(x) for x in command])
    stage = label or 'Build subprocess'
    limit = f'; timeout {timeout:g}s' if timeout is not None else ''
    _write_log(f'\n{stage}{limit}\n> {line}\n', log, echo)
    group_options = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt'
                     else {'start_new_session': True})
    descriptor, output_name = tempfile.mkstemp(prefix='onair-build-output-', suffix='.log')
    os.close(descriptor)
    output_path = Path(output_name)
    temporary = getattr(os, 'O_TEMPORARY', 0)
    binary = getattr(os, 'O_BINARY', 0)
    writer = reader = process = None
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    recent_output, progress_buffer, active_progress = '', '', ''
    started = time.monotonic()
    next_heartbeat = started + heartbeat

    def consume(data, final=False):
        nonlocal recent_output, progress_buffer, active_progress
        text = decoder.decode(data, final=final)
        recent_output = (recent_output + text)[-8192:]
        _write_log(text, log, echo)
        if echo:
            return
        lines = (progress_buffer + text).splitlines(keepends=True)
        progress_buffer = ''
        for item in lines:
            if not item.endswith(('\n', '\r')) and not final:
                progress_buffer = item[-8192:]
            elif item.startswith('ONAIR_BUILD_PROGRESS '):
                active_progress = item[len('ONAIR_BUILD_PROGRESS '):].strip()[:300]
                _say('   ' + active_progress)

    def drain_snapshot():
        # Bound the final read to bytes available at process exit: a descendant
        # may still append, but it cannot extend the controller's completion wait.
        remaining = max(0, os.fstat(reader.fileno()).st_size - reader.tell())
        while remaining:
            data = reader.read(min(65536, remaining))
            if not data:
                break
            remaining -= len(data)
            consume(data)
        consume(b'', final=True)

    try:
        writer = os.fdopen(os.open(output_path, os.O_WRONLY | os.O_TRUNC | binary | temporary), 'wb', buffering=0)
        reader = os.fdopen(os.open(output_path, os.O_RDONLY | binary | temporary), 'rb', buffering=0)
        process = subprocess.Popen(command, cwd=str(cwd), env=clean_environment(),
                                   stdin=subprocess.DEVNULL, stdout=writer,
                                   stderr=subprocess.STDOUT, **group_options)
        writer.close()
        writer = None
        while True:
            data = reader.read(65536)
            if data:
                consume(data)
            result = process.poll()
            if result is not None:
                drain_snapshot()
                break
            now = time.monotonic()
            if timeout is not None and now - started >= timeout:
                _write_log(f'\nTIMEOUT: {stage} exceeded {timeout:g}s. Stopping this build process tree.\n', log)
                raise subprocess.TimeoutExpired(command, timeout)
            if now >= next_heartbeat:
                _write_log(f'\n{stage}: still running ({now - started:.0f}s elapsed){limit}\n', log, echo)
                if not echo:
                    detail = active_progress or '진행 중'
                    _say(f'   ... {detail} ({_elapsed_text(now - started)} 경과'
                         + (f', 보통 {expected}' if expected else '') + ')')
                next_heartbeat = now + heartbeat
            time.sleep(.05)
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            _write_log(f'\n{stage}: interrupted. Stopping this build process tree.\n', log)
        if process is not None:
            _terminate_owned_process_tree(process, log)
        if reader is not None:
            drain_snapshot()
        if isinstance(exc, subprocess.TimeoutExpired):
            exc.output = recent_output
            exc.stage = stage
        raise
    finally:
        if writer is not None:
            writer.close()
        if reader is not None:
            reader.close()
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            # O_TEMPORARY owns deletion on Windows even if a descendant still
            # holds its inherited write handle. Never hide a build error here.
            if not temporary:
                _write_log(f'WARNING: temporary build output remains: {output_path}\n', log, False)
    _write_log(f'\n{stage}: exit code {result} ({time.monotonic() - started:.1f}s elapsed)\n', log, echo)
    if result:
        error = subprocess.CalledProcessError(result, command, output=recent_output)
        error.stage = stage
        raise error


def create_build_environment(env_dir, log, cwd):
    """Create a fresh local environment; ensurepip uses Python's bundled wheels."""
    env_dir = Path(env_dir)
    env_dir.mkdir()  # exclusive reservation: never repair/reuse an earlier build
    installed_python = getattr(sys, '_base_executable', None) or sys.executable
    _say('   [2/5-a] 새 빌드 환경을 만드는 중입니다.')
    run_logged([str(installed_python), '-I', '-u', '-X', 'utf8', '-m', 'venv',
                '--without-pip', str(env_dir)], log, cwd, timeout=180,
               label='[2/5-a] Create isolated environment', expected='10~30초')
    python = env_dir / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    _say('   [2/5-b] Python에 포함된 pip를 준비하는 중입니다. (인터넷 불필요)')
    run_logged([str(python), '-I', '-u', '-X', 'utf8', '-m', 'ensurepip',
                '--upgrade', '--default-pip'], log, cwd, timeout=300,
               label='[2/5-b] Install bundled pip (offline)', expected='30초~1분')
    return python


def builder_module():
    # -I does not imply -B: without this the source folder gets __pycache__/build*.pyc.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location('onair_source_builder', ROOT / 'build.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def requirements_for_runtime(info, root=ROOT):
    builder = builder_module()
    builder.validate_runtime(info)
    name = builder._runtime_policy.requirements_name(info)
    path = Path(root) / name
    if not path.is_file():
        raise FileNotFoundError('Missing ABI-specific build lock: ' + name)
    return path


def recover_missing_package_marker(root):
    """Restore only the known, inert updater marker; never change the manifest.

    Some incomplete source extractions omit this file. Existing files, links and
    custom markers remain subject to the normal source verification below.
    """
    root = Path(root)
    relative = 'updater/__init__.py'
    target = root / relative
    manifest_path = root / 'SOURCE_MANIFEST.json'
    if target.exists() or target.is_symlink() or not manifest_path.is_file():
        return None
    parent = target.parent
    if not parent.is_dir():
        return None
    if (parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction())
            or parent.resolve() != root.resolve() / 'updater'):
        raise ValueError('연결 경로에는 기본 파일을 복구할 수 없습니다: updater')
    content = b'"""WLR OnAir automatic update components."""\n'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if (manifest.get('files') or {}).get(relative) != hashlib.sha256(content).hexdigest():
        return None
    try:
        with target.open('xb') as output:
            output.write(content)
    except FileExistsError:
        # Another build may have restored it. Do not overwrite; verify it next.
        return None
    return relative


STEPS = {
    1: ('빌드 준비 확인', '약 10초'),
    2: ('빌드 전용 작업 공간 만들기 (설치된 Python 은 바뀌지 않습니다)', '약 1분'),
    3: ('필요한 부품 설치', '저장된 부품 재사용 시 약 1분, 첫 다운로드는 네트워크에 따라 다름'),
    4: ('관리자용·조회용·최고 관리자용 프로그램 만들기와 자체 점검', '약 8~25분, 가장 오래 걸립니다'),
    5: ('마무리', '몇 초'),
}


def _step(number, extra=''):
    title, duration = STEPS[number]
    _say(f'\n[{number}/5] {title}{extra}  (예상: {duration})')


def open_folder(path):
    """Windows 탐색기로 폴더를 연다. 실패해도 빌드 결과에는 영향이 없다."""
    if os.name != 'nt':
        return False
    try:
        os.startfile(str(path))  # noqa: S606 - 사용자가 만든 결과 폴더를 여는 것뿐
        return True
    except OSError:
        return False


def failure_hint(exc):
    """실패 원인을 비개발자용 한 줄로. 자세한 내용은 로그에 있다."""
    text = str(exc)
    if isinstance(exc, KeyboardInterrupt):
        return '빌드를 중간에 멈췄습니다.'
    if isinstance(exc, subprocess.TimeoutExpired):
        return f'{getattr(exc, "stage", "빌드 단계")}에서 {exc.timeout:g}초 제한을 넘겨 멈췄습니다.'
    if isinstance(exc, subprocess.CalledProcessError):
        return f'{getattr(exc, "stage", "빌드 단계")}에서 오류가 났습니다. (종료 코드 {exc.returncode})'
    if 'wheels' in text or 'PyPI' in text:
        return text
    return text or exc.__class__.__name__


def main(opener=open_folder):
    run_id = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
    log_dir = ROOT / 'build_logs'
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f'{run_id}.log'
    output = ROOT / 'dist' / f'source-build-{run_id}'
    workspace = None
    started = time.monotonic()
    _say('WLR OnAir 프로그램 만들기(빌드)를 시작합니다. 전체 10~20분 정도 걸립니다.')
    _say('창을 닫지 말고 기다려 주세요. 자세한 진행 기록은 build_logs 폴더에 저장됩니다.')
    try:
        with log_path.open('x', encoding='utf-8') as log:
            try:
                builder = builder_module()
                _step(1)
                info = builder.validate_runtime()
                requirements = requirements_for_runtime(info)
                restored = recover_missing_package_marker(ROOT)
                if restored:
                    message = f'   누락된 기본 파일 복구: {restored} · 원본 무결성 확인 중'
                    _say(message)
                    log.write(message + '\n')
                manifest = builder.verify_source_manifest(ROOT)
                _say(f'   버전 {manifest["version"]} / Python {".".join(map(str, info["version"]))} 확인')
                log.write(f'Host: {info!r}\nWheel lock: {requirements.name}\nSource fingerprint: {manifest["source_fingerprint"]}\n')
                _step(2)
                # Download/ZIP extraction names can be long. Keep pip's deep tree
                # outside that path, using a fresh directory owned by this run only.
                workspace = Path(tempfile.mkdtemp(prefix='oa-'))
                env_dir = workspace / 'v'
                log.write(f'Temporary build environment: {env_dir}\n')
                python = create_build_environment(env_dir, log, ROOT)
                _step(3)
                wheelhouse = prepare_wheelhouse(python, requirements, workspace, log, ROOT)
                log.write(f'Wheel source: {wheelhouse}\n')
                _say('   [3/5-b] 준비된 패키지를 설치합니다. (인터넷 없이)')
                run_logged(install_command(python, requirements, wheelhouse), log, ROOT,
                           label='[3/5-b] Install verified local wheels', timeout=300, expected='약 1분')
                run_logged([str(python), '-m', 'pip', 'check'], log, ROOT, label='[3/5] pip check', timeout=60)
                _step(4)
                run_logged([str(python), '-I', '-u', '-X', 'utf8', str(ROOT / 'build.py'), '--output', str(output)], log, ROOT,
                           label='[4/5] Compile Admin, Viewer, MasterAdmin and self-test', timeout=3600, heartbeat=30.0, expected='8~25분')
                if not (output / 'BUILD_SUCCESS.txt').is_file() or (output / 'BUILD_INCOMPLETE.txt').exists():
                    raise RuntimeError('빌드 완료 표식이 없습니다.')
                _step(5)
                log.write(f'OUTPUT: {output}\n')
            except BaseException:
                traceback.print_exc(file=log)
                raise
        opened = opener(output)
        _say(f'\n빌드 성공 ({_elapsed_text(time.monotonic() - started)} 걸림)')
        _say(f'완료 폴더: {output}' + ('  (탐색기로 열었습니다)' if opened else ''))
        _say(f'빌드 기록: {log_path}')
        _say('\n다음 할 일: 완료 폴더의 배포관리.cmd 를 더블클릭해 게시하세요.')
        _say('            (처음 게시라면 게시 직후 나오는 화면에서 키 백업을 꼭 만드세요. 시작하기.txt 5단계)')
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f'\n[빌드 실패] {failure_hint(exc)}', file=sys.stderr, flush=True)
        details = getattr(exc, 'output', None)
        if details:
            if isinstance(details, bytes):
                details = details.decode('utf-8', errors='replace')
            lines = str(details).strip().splitlines()[-8:]
            print('마지막 오류 내용:\n' + '\n'.join(line[:400] for line in lines),
                  file=sys.stderr, flush=True)
        print(f'빌드 기록(로그) 파일: {log_path}', file=sys.stderr, flush=True)
        print('실패한 폴더의 실행파일은 사용하거나 배포하지 마세요.', file=sys.stderr, flush=True)
        print('도움을 요청할 때는 위 로그 파일 하나와 이 창의 화면 캡처를 보내 주세요.\n'
              '(로그에는 개인 키나 암호가 들어 있지 않습니다. 오류 해결: WINDOWS_BUILD_GUIDE.md)',
              file=sys.stderr, flush=True)
        if log_dir.is_dir():
            opener(log_dir)
        return 1
    finally:
        if workspace is not None:
            try:
                shutil.rmtree(workspace)
            except OSError:
                # Build success/failure is already decided. A busy temporary file
                # must not erase that result or cause cleanup of another folder.
                _say(f'임시 빌드 환경을 자동 정리하지 못했습니다: {workspace}')


if __name__ == '__main__':
    raise SystemExit(main())
