"""Build new Windows one-file EXEs from source, never from recovered binaries.

Double-click build_windows.cmd for the supported isolated build. Metadata/check
commands also work on other hosts; producing Windows executables requires Windows.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parent
ROLES = {'ADMIN': 'Admin', 'VIEWER': 'Viewer'}
SOURCE_FILES = ('VERSION', 'entrypoint.py', 'build.py', 'build_windows.py',
                'build_windows.cmd', '배포관리.cmd', 'build_preflight.py', 'build_selftest.py',
                'collect_wheels.py', 'collect_wheels.cmd', 'requirements.txt',
                'requirements-build.txt', 'requirements-build-py311.txt', 'requirements-build-py312.txt',
                'requirements-build-py313.txt', 'requirements-build-py314.txt',
                'requirements-test.txt', 'backend/main.py')
BACKEND_FILES = ('__init__.py', 'request_access_schema.py', 'request_access_store.py',
                 'request_access_local.py', 'request_recovery.py', 'request_access_api.py',
                 'dinner_schema.py', 'dinner_service.py', 'dinner_api.py')
BACKEND_MODULES = ('backend',) + tuple('backend.' + Path(name).stem for name in BACKEND_FILES[1:])
UPDATER_FILES = ('__init__.py', 'core.py', 'launcher.py', 'profiles.py', 'publisher.py', 'setup_ui.py', 'manual_update.py', 'process_identity.py')
METHOD = 'clean-source-pyinstaller'
BUILD_REVISION = 'python311-314-source-r4'

BUMP_HINT = '버전올리기.cmd 를 실행하세요(버전 번호를 한 번에 맞춰 줍니다).'
VERSION_FILES = ('VERSION', 'backend/main.py', 'entrypoint.py')
SUMS_NAME = 'SHA256SUMS.txt'
# SHA256SUMS.txt 에 넣지 않는 폴더: 빌드 PC 에서 생기는 산출물·임시 파일.
# 최상위에서만 건너뛰는 것(frontend/dist 는 소스다)과 어디서든 건너뛰는 캐시를 나눈다.
SUMS_SKIP_TOP = {'.build-envs', '.git', 'node_modules', 'build_logs', 'dist', 'wheels'}
SUMS_SKIP_ANY = {'__pycache__', '.pytest_cache'}

FROZEN_SELF_TEST_TIMEOUT = 120
COMPILE_TIMEOUT = 900
_WINDOWS = os.name == 'nt'


def load_source_module(name, path):
    """Load a sibling source file without writing __pycache__ into the source folder.

    -I does not imply -B, and SourceFileLoader caches bytecode next to the file.
    The flag is restored afterwards so importers (tests, build_windows) keep their setting.
    """
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


# Absolute-file import remains valid when build.py is launched with -I.
_runtime_policy = load_source_module('onair_build_preflight', ROOT / 'build_preflight.py')


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def runtime_info():
    return _runtime_policy.runtime_info()


def validate_runtime(info=None):
    info = runtime_info() if info is None else info
    reasons = _runtime_policy.rejection_reasons(info)
    if reasons:
        raise ValueError('이 Python 으로는 빌드할 수 없습니다. python.org 의 64비트 Windows용 '
                         'Python 3.11~3.14 정식판을 설치해 주세요(시작하기.txt 1단계). 세부 사유: '
                         + '; '.join(reasons))
    return info


def source_version(root):
    version = (root / 'VERSION').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('VERSION 버전 형식은 숫자.숫자.숫자여야 합니다.')
    if any(int(x) > 65535 for x in version.split('.')):
        raise ValueError('VERSION 숫자는 Windows 버전 리소스 범위를 넘을 수 없습니다.')
    for name in ('backend/main.py', 'entrypoint.py'):
        values = [node.value.value for node in ast.parse((root / name).read_text(encoding='utf-8')).body
                  if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                  and any(isinstance(t, ast.Name) and t.id == 'APP_VERSION' for t in node.targets)]
        if values != [version]:
            raise ValueError(f'버전 번호가 서로 다릅니다: VERSION 파일({version}) / {name}. '
                             f'{BUMP_HINT}')
    return version


def source_paths(root):
    paths = [root / p for p in SOURCE_FILES]
    paths += [root / 'backend' / name for name in BACKEND_FILES]
    paths += [root / 'updater' / name for name in UPDATER_FILES]
    assets = sorted((root / 'frontend/dist').rglob('*'))
    for p in assets:
        if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()):
            raise ValueError(f'정적 자산에 연결 경로를 사용할 수 없습니다: {p}')
    paths += [p for p in assets if not p.is_dir()]
    for p in paths:
        if p.is_relative_to(root / 'frontend/dist') and (
                p.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.ini', '.key', '.pem', '.pfx', '.p12'}
                or p.name.lower() in {'signing_key.json', 'onair_update.json', 'config.json', '.env'}):
            raise ValueError(f'운영 데이터/개인 키를 정적 자산에 넣을 수 없습니다: {p.name}')
        if not p.is_file() or any(q.is_symlink() or (hasattr(q, 'is_junction') and q.is_junction())
                                  for q in (p, *p.relative_to(root).parents) if q.is_absolute()):
            raise ValueError(f'연결 파일 또는 누락된 소스: {p}')
        # Resolve every ancestor as well: junction/symlink directories cannot leak files.
        if p.resolve() != root.resolve() / p.relative_to(root):
            raise ValueError(f'연결 경로는 빌드에 포함할 수 없습니다: {p}')
    if not (root / 'frontend/dist/index.html') in paths:
        raise ValueError('frontend/dist/index.html 파일이 없습니다.')
    return sorted(paths)


def create_source_manifest(root):
    root = Path(root)
    version = source_version(root)
    hashes = {p.relative_to(root).as_posix(): sha256_file(p) for p in source_paths(root)}
    canonical = json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return {'schema': 2, 'version': version, 'build_method': METHOD, 'build_revision': BUILD_REVISION,
            'status': 'build-files-ready-windows-validation-pending', 'build_performed': False,
            'windows_executed': False, 'deployed': False, 'operational_database_accessed': False,
            'python_requirement': _runtime_policy.REQUIREMENT,
            'frontend_sha256': hashes['frontend/dist/index.html'],
            'source_fingerprint': hashlib.sha256(canonical).hexdigest(), 'files': hashes}


def verify_source_manifest(root):
    root = Path(root)
    expected = json.loads((root / 'SOURCE_MANIFEST.json').read_text(encoding='utf-8'))
    actual = create_source_manifest(root)
    if expected.get('version') != actual['version']:
        raise ValueError(f"버전 번호가 서로 다릅니다: VERSION 파일({actual['version']}) / "
                         f"SOURCE_MANIFEST.json({expected.get('version')}). {BUMP_HINT}")
    if expected.get('files') != actual['files'] or expected.get('source_fingerprint') != actual['source_fingerprint']:
        changed = changed_source_files(expected.get('files') or {}, actual['files'])
        raise ValueError('소스 파일이 SOURCE_MANIFEST.json 에 기록된 내용과 다릅니다'
                         + (f" (바뀐 파일: {', '.join(changed[:8])}{' 외' if len(changed) > 8 else ''})" if changed else '')
                         + '. 버전 번호만 바꾸려면 버전올리기.cmd 를 실행하세요. 직접 고친 적이 없다면 '
                         'ZIP 전체를 새 폴더에 다시 압축 해제하세요. 일부러 고쳤다면 '
                         'WINDOWS_BUILD_GUIDE.md 의 매니페스트 갱신 절차를 따르세요.')
    return actual


def changed_source_files(expected_files, actual_files):
    """매니페스트와 현재 소스의 차이(추가·삭제·변경된 파일 이름)."""
    names = set(expected_files) | set(actual_files)
    return sorted(n for n in names if expected_files.get(n) != actual_files.get(n))


def windows_version_text(version, label):
    numbers = tuple(int(n) for n in version.split('.')) + (0,)
    return f'''VSVersionInfo(ffi=FixedFileInfo(filevers={numbers!r}, prodvers={numbers!r},
mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
kids=[StringFileInfo([StringTable('040904B0', [
StringStruct('FileDescription', 'WLR OnAir {label}'),
StringStruct('FileVersion', '{version}'), StringStruct('ProductVersion', '{version}'),
StringStruct('ProductName', 'WLR OnAir'),
StringStruct('OriginalFilename', 'WLR_OnAir_{label}.exe')])]),
VarFileInfo([VarStruct('Translation', [1033, 1200])])])
'''


def prepare_role(root, stage, role, version, fingerprint):
    if role not in ROLES:
        raise ValueError('Unknown build role')
    root, stage = Path(root), Path(stage)
    validated_sources = source_paths(root)
    stage.mkdir(parents=True, exist_ok=False)
    (stage / 'backend').mkdir()
    for name in BACKEND_FILES:
        shutil.copyfile(root / 'backend' / name, stage / 'backend' / name)
    shutil.copyfile(root / 'backend/main.py', stage / 'onair_backend.py')
    entry = (root / 'entrypoint.py').read_text(encoding='utf-8')
    if entry.count("BUILD_ROLE = 'ADMIN'") != 1:
        raise ValueError('entrypoint BUILD_ROLE template mismatch')
    (stage / 'entrypoint.py').write_text(entry.replace("BUILD_ROLE = 'ADMIN'", f"BUILD_ROLE = '{role}'"), encoding='utf-8')
    shutil.copyfile(root / 'build_selftest.py', stage / 'build_selftest.py')
    shutil.copyfile(root / 'build_preflight.py', stage / 'build_preflight.py')
    (stage / 'updater').mkdir()
    for name in UPDATER_FILES:
        if role == 'VIEWER' and name == 'publisher.py':
            continue
        shutil.copyfile(root / 'updater' / name, stage / 'updater' / name)
    # Enumerate/validate before copying; never copy databases, profiles or recovered runtime.
    assets = [p for p in validated_sources if p.is_relative_to(root / 'frontend/dist')]
    for source in assets:
        target = stage / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    info = {'schema': 1, 'version': version, 'role': role, 'build_method': METHOD, 'build_revision': BUILD_REVISION,
            'source_fingerprint': fingerprint, 'python': platform.python_version(),
            'python_runtime': runtime_info(),
            'backend_sha256': sha256_file(root / 'backend/main.py'),
            'frontend_sha256': sha256_file(root / 'frontend/dist/index.html'),
            'assets': {p.relative_to(root).as_posix(): sha256_file(p) for p in assets}}
    write_json(stage / 'onair_build_info.json', info)
    (stage / 'version_info.txt').write_text(windows_version_text(version, ROLES[role]), encoding='utf-8')
    return info


def pyinstaller_command(python, stage, work, dist, role):
    stage, work, dist = Path(stage), Path(work), Path(dist)
    command = [str(python), '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed', '--noupx',
               '--name', f'WLR_OnAir_{ROLES[role]}', '--distpath', str(dist), '--workpath', str(work),
               '--specpath', str(work), '--paths', str(stage), '--version-file', str(stage / 'version_info.txt'),
               '--add-data', f'{stage / "frontend/dist"}:frontend/dist',
               '--add-data', f'{stage / "onair_build_info.json"}:.',
               '--hidden-import', 'onair_backend', '--hidden-import', '_ctypes',
               '--hidden-import', '_cffi_backend', '--hidden-import', 'anyio._backends._asyncio',
               '--collect-submodules', 'uvicorn',
               '--collect-submodules', 'websockets']
    for name in BACKEND_MODULES:
        command += ['--hidden-import', name]
    if role == 'VIEWER':
        command += ['--exclude-module', 'updater.publisher']
    command.append(str(stage / 'entrypoint.py'))
    return command


def compile_role(stage, work, dist, role, log_path, timeout=COMPILE_TIMEOUT):
    """Bound one freezer invocation and keep complete per-role diagnostics."""
    runner = load_source_module('onair_compile_runner', ROOT / 'build_windows.py')
    label = '관리자용' if role == 'ADMIN' else '조회용'
    with Path(log_path).open('w', encoding='utf-8') as log:
        runner.run_logged(pyinstaller_command(sys.executable, stage, work, dist, role),
                          log, stage, timeout=timeout, label=f'{label} EXE 생성',
                          heartbeat=30.0, echo=True)


def build_progress(message):
    # The bootstrap relays this controlled marker while retaining noisy tool
    # output in its full log. Explicit UTF-8 is set on its build.py invocation.
    print('ONAIR_BUILD_PROGRESS ' + message, flush=True)


def verify_archive(executable, role, version, info):
    from PyInstaller.archive.readers import CArchiveReader
    import pefile
    archive = CArchiveReader(str(executable))
    entries = {name.replace('\\', '/'): name for name in archive.toc}
    expected_dll = _runtime_policy.runtime_dll(info['python_runtime']['version'])
    for required in ('frontend/dist/index.html', 'onair_build_info.json', expected_dll):
        if required not in entries:
            raise ValueError(f'EXE 필수 내장 파일 누락: {required}')
    embedded = json.loads(archive.extract(entries['onair_build_info.json']))
    if embedded != info:
        raise ValueError('EXE 내장 빌드 정보 불일치')
    for asset, digest in info['assets'].items():
        if asset not in entries or hashlib.sha256(archive.extract(entries[asset])).hexdigest() != digest:
            raise ValueError(f'EXE 정적 자산 hash 불일치: {asset}')
    pyz_names = [name for name in archive.toc if name.endswith('.pyz')]
    if len(pyz_names) != 1:
        raise ValueError('EXE Python archive count mismatch')
    modules = archive.open_embedded_archive(pyz_names[0]).toc
    for name in ('onair_backend', 'build_preflight', 'build_selftest', 'updater.core', 'updater.launcher', 'updater.profiles', 'updater.manual_update', 'updater.process_identity',
                 'updater.setup_ui', 'uvicorn', 'websockets') + BACKEND_MODULES:
        if name not in modules:
            raise ValueError(f'EXE 필수 Python 모듈 누락: {name}')
    if ('updater.publisher' in modules) != (role == 'ADMIN'):
        raise ValueError('EXE 관리자/조회용 publisher 분리 실패')
    with pefile.PE(str(executable)) as pe:
        if pe.FILE_HEADER.Machine != 0x8664 or pe.OPTIONAL_HEADER.Subsystem != 2:
            raise ValueError('EXE는 Windows AMD64 GUI 형식이어야 합니다.')
        values = {}
        for group in getattr(pe, 'FileInfo', []):
            for item in group:
                for table in getattr(item, 'StringTable', []):
                    values.update(table.entries)
        if values.get(b'FileVersion', b'').decode() != version or values.get(b'ProductVersion', b'').decode() != version:
            raise ValueError('EXE Windows 버전 리소스 불일치')
        manifests = []
        for resource in getattr(pe, 'DIRECTORY_ENTRY_RESOURCE').entries:
            if resource.id == 24:
                for child in resource.directory.entries:
                    for lang in child.directory.entries:
                        item = lang.data.struct
                        manifests.append(pe.get_data(item.OffsetToData, item.Size))
        if not manifests or any(b'asInvoker' not in value or b'requireAdministrator' in value for value in manifests):
            raise ValueError('EXE 권한 manifest가 asInvoker와 다릅니다.')
    return {'archive_verified': True, 'pe_amd64_gui_verified': True, 'version_resource_verified': True,
            'publisher_present': role == 'ADMIN', 'module_count': len(modules)}


def _kill_process_tree(process):
    """Stop the self-test process and its descendants (onefile bootloader + Python child).

    Killing only the bootloader leaves the unpacked Python child running on Windows,
    which keeps the temporary folder locked. Only the PID this run started is targeted.
    """
    if _WINDOWS:
        taskkill = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'taskkill.exe'
        try:
            subprocess.run([str(taskkill), '/F', '/T', '/PID', str(process.pid)],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, 9)
        except OSError:
            pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _remove_tree_best_effort(path, attempts=5, delay=0.5):
    """Delete a temporary folder; a lingering file lock must not mask the real error."""
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            return True
        except OSError:
            pass
        if not os.path.exists(path):
            return True
        if attempt + 1 < attempts:
            time.sleep(delay)
    print(f'WARNING: 임시 폴더를 지우지 못했습니다(파일 잠김). 나중에 직접 지워도 됩니다: {path}',
          file=sys.stderr, flush=True)
    return False


def frozen_self_test(executable, role, version, fingerprint, report_path, timeout=FROZEN_SELF_TEST_TIMEOUT):
    temp = tempfile.mkdtemp(prefix='onair-frozen-check-')
    try:
        environment = {k: v for k, v in os.environ.items()
                       if not k.upper().startswith(('WLR_', 'SMART_DUTY_', 'PYTHON', '_PYI_'))}
        environment.update({'LOCALAPPDATA': temp, 'APPDATA': temp,
                            'PYINSTALLER_RESET_ENVIRONMENT': '1'})
        group_options = ({'creationflags': getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0x200)}
                         if _WINDOWS else {'start_new_session': True})
        process = subprocess.Popen([str(executable), '--onair-self-test', str(report_path)], cwd=temp,
                                   env=environment, stdin=subprocess.DEVNULL, **group_options)
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(process)
            raise ValueError(f'{role} EXE 자체점검 시간 초과: {timeout}초 안에 끝나지 않아 '
                             f'자체점검 프로세스를 종료했습니다; {report_path}') from None
        except BaseException:
            _kill_process_tree(process)
            raise
    finally:
        _remove_tree_best_effort(temp)
    if returncode != 0 or not report_path.is_file():
        raise ValueError(f'{role} EXE 자체점검 실패 (exit={returncode}); {report_path}')
    data = json.loads(report_path.read_text(encoding='utf-8'))
    if (data.get('ok') is not True or data.get('role') != role or data.get('version') != version
            or data.get('source_fingerprint') != fingerprint):
        raise ValueError(f'{role} EXE 자체점검 정보 불일치: {report_path}')
    return data


def package_release(out, version, files):
    release = {'schema': 1, 'product': 'WLR_OnAir', 'version': version, 'data_schema': 1, 'files': {}}
    for role, path in files.items():
        release['files'][role] = {'path': f'{role}/{path.name}', 'sha256': sha256_file(path), 'size': path.stat().st_size}
    target = out / f'WLR_OnAir_v{version}_Update.zip'
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr('release.json', json.dumps(release, ensure_ascii=False, sort_keys=True))
        for role, path in files.items():
            archive.write(path, release['files'][role]['path'])
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise ValueError('Update ZIP integrity check failed')
    shutil.copyfile(ROOT / '배포관리.cmd', out / '배포관리.cmd')
    if (ROOT / '시작하기.txt').is_file():
        shutil.copyfile(ROOT / '시작하기.txt', out / '시작하기.txt')   # 완료 폴더에서도 다음 순서를 볼 수 있게
    return target


def installed_packages():
    return dict(sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions()))


def build(root, output):
    host = validate_runtime()
    manifest = verify_source_manifest(root)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / 'BUILD_INCOMPLETE.txt').write_text('빌드 진행/실패 상태입니다. BUILD_SUCCESS.txt 없이는 배포하지 마세요.\n', encoding='utf-8')
    try:
        files, artifacts = {}, []
        with tempfile.TemporaryDirectory(prefix='onair-source-stage-') as temp:
            for role, label in ROLES.items():
                stage = Path(temp) / role
                info = prepare_role(root, stage, role, manifest['version'], manifest['source_fingerprint'])
                title = '관리자용' if role == 'ADMIN' else '조회용'
                build_progress(f'{title}: EXE 생성 시작')
                compile_role(stage, Path(temp) / f'work-{role}', output / label, role, output / f'{label}_BUILD.log')
                executable = output / label / f'WLR_OnAir_{label}.exe'
                build_progress(f'{title}: EXE 생성 완료 · 내장 파일 검사 중')
                checks = verify_archive(executable, role, manifest['version'], info)
                build_progress(f'{title}: 내장 파일 검사 완료 · 실행 자체점검 중')
                checks['frozen_self_test'] = frozen_self_test(executable, role, manifest['version'],
                                                             manifest['source_fingerprint'], output / f'{label}_SELFTEST.json')
                build_progress(f'{title}: 자체점검 완료')
                files[role] = executable
                artifacts.append({'role': role, 'file': executable.relative_to(output).as_posix(),
                                  'sha256': sha256_file(executable), 'size': executable.stat().st_size, 'checks': checks})
        build_progress('업데이트 ZIP 생성 및 무결성 검사 중')
        package = package_release(output, manifest['version'], files)
        build_progress('업데이트 ZIP 완료 · 빌드 기록 정리 중')
        write_json(output / 'BUILD_MANIFEST.json', {'schema': 2, 'version': manifest['version'], 'build_method': METHOD, 'build_revision': BUILD_REVISION,
                   'source_fingerprint': manifest['source_fingerprint'], 'source_hashes': manifest['files'],
                   'runtime': host, 'packages': installed_packages(), 'artifacts': artifacts,
                   'update_zip': {'file': package.name, 'sha256': sha256_file(package)},
                   'manual_windows_acceptance': 'pending: first setup, tray, signed update/rollback, SMB; no deployment'})
        (output / 'BUILD_SUCCESS.txt').write_text('소스 기반 Windows 빌드와 내장 자체점검 통과.\n'
                   '실제 사용 전 WINDOWS_BUILD_GUIDE.md의 수동 검증을 완료하세요. 자동 게시되지 않았습니다.\n', encoding='utf-8')
        (output / 'BUILD_INCOMPLETE.txt').unlink()
        build_progress('전체 빌드 완료')
        print(f'BUILD SUCCESS: {output}', flush=True)
        return output
    except BaseException:
        (output / 'BUILD_INCOMPLETE.txt').write_text('빌드 또는 검증 실패. 이 폴더의 실행파일/ZIP을 사용·배포하지 마세요.\n', encoding='utf-8')
        raise


# ------------------------------------------------------------------ 버전 올리기 (버전올리기.cmd)

_APP_VERSION_LINE = re.compile(r"^APP_VERSION = '(\d+\.\d+\.\d+)'$", re.M)


def _check_new_version(text):
    text = str(text or '').strip()
    if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', text):
        raise ValueError(f'버전은 숫자.숫자.숫자 형식으로 입력하세요(예: 3.18.1). 입력한 값: {text!r}')
    if any(int(x) > 65535 for x in text.split('.')):
        raise ValueError('각 숫자는 65535 이하여야 합니다.')
    return text


def _version_tuple(text):
    return tuple(int(x) for x in text.split('.'))


def next_patch_version(version):
    major, minor, patch = _version_tuple(version)
    return f'{major}.{minor}.{patch + 1}'


def sha256sums_text(root):
    """SHA256SUMS.txt 내용: 소스 폴더의 모든 파일(자기 자신·빌드 산출물 제외), 경로 바이트 순."""
    root = Path(root)
    entries = []
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if relative.parts[0] in SUMS_SKIP_TOP or SUMS_SKIP_ANY.intersection(relative.parts[:-1]):
            continue
        if path.is_symlink() or not path.is_file() or relative.as_posix() == SUMS_NAME:
            continue
        entries.append(relative.as_posix())
    entries.sort(key=lambda name: name.encode('utf-8'))
    return ''.join(f'{sha256_file(root / name)}  {name}\n' for name in entries)


def _replace_versions(texts, old, new):
    """VERSION / main.py / entrypoint.py 의 새 내용을 만든다(쓰지는 않는다)."""
    result = {'VERSION': new + '\n'}
    for name in ('backend/main.py', 'entrypoint.py'):
        text = texts[name]
        found = _APP_VERSION_LINE.findall(text)
        if len(found) != 1:
            raise ValueError(f"{name} 에서 APP_VERSION = '...' 줄을 정확히 하나 찾지 못했습니다. "
                             '파일이 손상되었을 수 있으니 ZIP 을 새로 풀어 주세요.')
        text = _APP_VERSION_LINE.sub(f"APP_VERSION = '{new}'", text, count=1)
        if name == 'backend/main.py':
            # 문서 첫 줄 '"""WLR OnAir 3.17.13 — ...' 의 버전 표기도 함께 바꾼다.
            first, sep, rest = text.partition('\n')
            if first.startswith('"""WLR OnAir '):
                first = re.sub(r'\d+\.\d+\.\d+', new, first, count=1)
            text = first + sep + rest
        result[name] = text
    return result


def bump_version(root, new_version, *, accept_changed=False, out=print):
    """VERSION·APP_VERSION 을 한 번에 바꾸고 SOURCE_MANIFEST.json·SHA256SUMS.txt 를 다시 만든다.

    다른 소스 파일이 매니페스트와 다르면(누군가 고쳤거나 손상) accept_changed 없이는 멈춘다.
    중간에 실패하면 바꾼 파일을 원래대로 되돌린다.
    """
    root = Path(root)
    new = _check_new_version(new_version)
    current = (root / 'VERSION').read_text(encoding='utf-8').strip()
    if re.fullmatch(r'\d+\.\d+\.\d+', current) and _version_tuple(new) <= _version_tuple(current):
        raise ValueError(f'새 버전({new})은 지금 버전({current})보다 높아야 합니다. '
                         '이미 게시한 PC 들은 낮거나 같은 번호를 받지 않습니다.')
    manifest_path = root / 'SOURCE_MANIFEST.json'
    try:
        expected = json.loads(manifest_path.read_text(encoding='utf-8')).get('files') or {}
    except (OSError, ValueError):
        expected = {}
    actual = {p.relative_to(root).as_posix(): sha256_file(p) for p in source_paths(root)}
    changed = [n for n in changed_source_files(expected, actual) if n not in VERSION_FILES]
    if changed and not accept_changed:
        raise ValueError('버전 번호 외에도 SOURCE_MANIFEST.json 기록과 다른 소스 파일이 있습니다: '
                         + ', '.join(changed[:8]) + (' 외' if len(changed) > 8 else '')
                         + '. 직접 고친 적이 없다면 ZIP 을 새 폴더에 다시 풀어 주세요. '
                         '일부러 고친 것이 맞으면 확인 질문에 y 로 답하세요.')
    originals = {name: (root / name).read_bytes() for name in (*VERSION_FILES, 'SOURCE_MANIFEST.json', SUMS_NAME)
                 if (root / name).is_file()}
    texts = {name: originals[name].decode('utf-8') for name in VERSION_FILES}
    updated = _replace_versions(texts, current, new)

    def write(name, data):
        target = root / name
        temporary = target.with_name(target.name + '.bump-tmp')
        temporary.write_bytes(data)
        os.replace(temporary, target)

    try:
        for name in VERSION_FILES:
            write(name, updated[name].encode('utf-8'))
        if source_version(root) != new:
            raise ValueError('버전을 바꾼 뒤 확인에 실패했습니다.')
        manifest = create_source_manifest(root)
        write('SOURCE_MANIFEST.json', (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
        verify_source_manifest(root)
        write(SUMS_NAME, sha256sums_text(root).encode('utf-8'))
    except BaseException:
        for name, data in originals.items():
            try:
                write(name, data)
            except OSError:
                pass
        raise
    out(f'버전을 {current} → {new} 로 바꿨습니다.')
    out('  바뀐 파일: VERSION, backend/main.py, entrypoint.py, SOURCE_MANIFEST.json, SHA256SUMS.txt')
    if changed:
        out('  함께 기록한 수정 파일: ' + ', '.join(changed))
    out('다음 단계: 같은 폴더의 build_windows.cmd 를 더블클릭해 새 버전을 빌드하세요.')
    out('           빌드가 끝나면 열리는 폴더의 배포관리.cmd 로 게시합니다.')
    return {'old': current, 'new': new, 'changed': changed}


def bump_version_interactive(root, ask=input, out=print):
    """버전올리기.cmd 의 대화형 진행. 0=완료, 1=취소/오류."""
    root = Path(root)
    current = (root / 'VERSION').read_text(encoding='utf-8').strip()
    default = next_patch_version(current) if re.fullmatch(r'\d+\.\d+\.\d+', current) else ''
    out(f'지금 버전: {current}')
    out('새 버전 번호를 입력하고 Enter 를 누르세요. 그냥 Enter 를 누르면 '
        f'{default or "(기본값 없음)"} 으로 올립니다.')
    out('(되돌리기: 옛 소스로 다시 배포할 때도 지금 게시된 버전보다 높은 번호를 입력하세요.)')
    try:
        answer = (ask('새 버전> ') or '').strip() or default
        try:
            return 0 if bump_version(root, answer, out=out) else 1
        except ValueError as exc:
            if 'SOURCE_MANIFEST.json 기록과 다른 소스 파일' not in str(exc):
                raise
            out(str(exc))
            if (ask('정말 이 수정 내용까지 포함해 버전을 올릴까요? (y/N)> ') or '').strip().lower() not in ('y', 'yes'):
                out('취소했습니다. 아무 파일도 바꾸지 않았습니다.')
                return 1
            bump_version(root, answer, accept_changed=True, out=out)
            return 0
    except (ValueError, OSError) as exc:
        out(f'[오류] {exc}')
        out('아무 파일도 바꾸지 않았습니다.')
        return 1
    except (EOFError, KeyboardInterrupt):
        out('\n취소했습니다. 아무 파일도 바꾸지 않았습니다.')
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--check', action='store_true', help='verify source fingerprints, no compilation')
    group.add_argument('--refresh-manifest', action='store_true', help='explicitly accept edited source hashes, no compilation')
    group.add_argument('--bump-version', nargs='?', const='', metavar='X.Y.Z',
                       help='set VERSION/APP_VERSION and refresh SOURCE_MANIFEST.json and SHA256SUMS.txt '
                            '(no value: ask interactively, used by 버전올리기.cmd)')
    parser.add_argument('--yes', action='store_true', help='with --bump-version: also accept other edited source files')
    parser.add_argument('--output', type=Path, help='new output directory; existing directories are never overwritten')
    args = parser.parse_args(argv)
    if args.bump_version is not None:
        if args.bump_version == '':
            raise SystemExit(bump_version_interactive(ROOT))
        try:
            bump_version(ROOT, args.bump_version, accept_changed=args.yes)
        except ValueError as exc:
            print(f'[오류] {exc}', file=sys.stderr)
            raise SystemExit(1)
        return
    if args.refresh_manifest:
        write_json(ROOT / 'SOURCE_MANIFEST.json', create_source_manifest(ROOT))
        print('SOURCE_MANIFEST.json refreshed; no EXE built.')
    elif args.check:
        try:
            print(json.dumps(verify_source_manifest(ROOT), ensure_ascii=False, indent=2))
        except ValueError as exc:
            print(f'[오류] {exc}', file=sys.stderr)
            raise SystemExit(1)
    else:
        if args.output is None:
            parser.error('Use build_windows.cmd, or provide --output with a NEW directory in a prepared build venv.')
        build(ROOT, args.output)


if __name__ == '__main__':
    main()
