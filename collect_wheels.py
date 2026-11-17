"""인터넷이 되는 PC 에서 한 번 실행해 오프라인 빌드용 wheels/ 를 만든다.

사내망 PC 에는 PyPI 가 없다. 이 스크립트가 만든 wheels/ 폴더를 소스와 함께
옮겨 두면 build_windows.cmd 가 네트워크를 쓰지 않고 그 폴더에서 설치한다.

네 개의 ABI 락(3.11~3.14)을 모두 내려받으므로, 사내망 PC 의 Python minor 가
무엇이든 맞는 wheel 이 들어 있다. 해시는 락 파일에 고정된 값으로 검증하며,
--require-hashes 가 붙어 있어 값이 하나라도 다르면 여기서 멈춘다.

사용법 (인터넷 PC, Windows):
    collect_wheels.cmd          더블클릭
또는
    python collect_wheels.py
"""
from __future__ import annotations

from datetime import datetime
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
WHEELHOUSE = ROOT / 'wheels'
LOG_DIR_NAME = 'build_logs'      # build_windows 와 같은 폴더. SHA256SUMS·매니페스트 대상이 아니다.

# (requirements 파일, python-version, abi) — build.py 의 락 선택 규칙과 같은 순서
TARGETS = [
    ('requirements-build-py311.txt', '3.11', 'cp311'),
    ('requirements-build-py312.txt', '3.12', 'cp312'),
    ('requirements-build-py313.txt', '3.13', 'cp313'),
    ('requirements-build-py314.txt', '3.14', 'cp314'),
]
PLATFORM = 'win_amd64'

# collect_wheels.cmd 가 원인별로 다른 안내를 띄우도록 종료 코드를 나눈다.
# 1 은 파이썬 자체 오류(문법·import 실패 등)와 겹치므로 쓰지 않는다.
EXIT_OK = 0
EXIT_MISSING_LOCK = 11      # 소스 폴더가 불완전함 (락 파일 없음)
EXIT_NO_PIP = 12            # 찾은 Python 에 pip 가 없음 (다운로드 시도 전)
EXIT_DOWNLOAD_FAILED = 13   # pip download 실패: 인터넷/PyPI 차단/해시 불일치


def clean_environment():
    """상속된 pip/Python 설정을 배제한다 (build_windows.clean_environment 와 같은 기준)."""
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(('PIP_', 'PYTHON', '_PYI_'))
           and k.upper() not in {'VIRTUAL_ENV', '__PYVENV_LAUNCHER__'}}
    env['PIP_CONFIG_FILE'] = os.devnull
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    return env


def download_command(requirements, python_version, abi):
    return [sys.executable, '-m', 'pip', 'download',
            '--disable-pip-version-check', '--no-input',
            '--index-url', 'https://pypi.org/simple',
            '--only-binary=:all:', '--require-hashes',
            '--platform', PLATFORM,
            '--python-version', python_version,
            '--implementation', 'cp',
            '--abi', abi,
            '-d', str(WHEELHOUSE),
            '-r', str(requirements)]


def run_logged(command, log):
    """pip 출력을 화면과 로그 파일에 함께 남긴다(실패했을 때 보낼 파일이 남도록)."""
    log.write('> ' + subprocess.list2cmdline([str(x) for x in command]) + '\n')
    log.flush()
    process = subprocess.Popen(command, cwd=str(ROOT), env=clean_environment(), stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for raw in iter(process.stdout.readline, b''):
        text = raw.decode('utf-8', 'replace')
        print(text, end='', flush=True)
        log.write(text)
        log.flush()
    process.stdout.close()
    code = process.wait()
    log.write(f'exit code {code}\n')
    log.flush()
    return code


def new_log_path():
    folder = ROOT / LOG_DIR_NAME
    folder.mkdir(exist_ok=True)
    return folder / f'collect_wheels_{datetime.now():%Y%m%d_%H%M%S}.log'


def main():
    missing = [name for name, _, _ in TARGETS if not (ROOT / name).is_file()]
    if missing:
        print('빌드 락 파일이 없습니다: ' + ', '.join(missing), file=sys.stderr)
        return EXIT_MISSING_LOCK
    if importlib.util.find_spec('pip') is None:
        print('이 Python 에는 pip 가 없습니다. 아직 아무것도 내려받지 않았습니다.',
              file=sys.stderr, flush=True)
        return EXIT_NO_PIP
    WHEELHOUSE.mkdir(exist_ok=True)
    log_path = new_log_path()
    with log_path.open('a', encoding='utf-8') as log:
        for number, (name, python_version, abi) in enumerate(TARGETS, 1):
            print(f'\n[{number}/{len(TARGETS)}] Python {python_version} 용 부품 내려받기 (약 1분)', flush=True)
            log.write(f'\n=== {name} (CPython {python_version} {PLATFORM}) ===\n')
            command = download_command(ROOT / name, python_version, abi)
            if run_logged(command, log):
                print(f'\n내려받기 실패: {name}\n'
                      '회사 네트워크가 PyPI 를 막고 있는지 확인하세요. '
                      '인증서 검증을 끄거나 --trusted-host 를 붙이지 마세요.\n'
                      f'pip 메시지 전체를 이 파일에 저장했습니다. 도움을 요청할 때 이 파일을 보내 주세요:\n  {log_path}',
                      file=sys.stderr, flush=True)
                return EXIT_DOWNLOAD_FAILED
    wheels = sorted(WHEELHOUSE.glob('*.whl'))
    total = sum(w.stat().st_size for w in wheels)
    print(f'\n완료: {WHEELHOUSE} 에 wheel {len(wheels)}개 ({total / 1048576:.1f} MB)  (기록: {log_path})')
    print('이 폴더를 소스 폴더와 함께 사내망 PC 로 옮긴 뒤 build_windows.cmd 를 실행하세요.')
    print('PyPI 에서 직접 내려받게 하려면 wheels 폴더를 지우면 됩니다.')
    return EXIT_OK


if __name__ == '__main__':
    raise SystemExit(main())
