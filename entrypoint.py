"""Role-fixed launcher entrypoint; build.py fixes BUILD_ROLE per executable."""
import os
from pathlib import Path
import runpy
import sys

BUILD_ROLE = 'ADMIN'
APP_VERSION = '3.18.41'


def main():
    role = BUILD_ROLE
    if '--onair-manual-update' in sys.argv[1:]:
        if len(sys.argv) != 3 or sys.argv[1] != '--onair-manual-update':
            raise RuntimeError('업데이트 작업 번호가 필요합니다.')
        from updater.manual_update import run_helper
        return run_helper(role, sys.argv[2])
    if '--recover-update-trust' in sys.argv[1:]:
        arguments = sys.argv[1:]
        if (len(arguments) != 4 or arguments[0:2] != ['--recover-update-trust', '--fingerprint'] or
                arguments[3] != '--confirm' or arguments[2].startswith('--')):
            raise RuntimeError('복구 명령: --recover-update-trust --fingerprint "관리자가 별도로 알려 준 지문" --confirm')
        executable = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve()
        from updater.launcher import reenroll_update_trust
        archive = reenroll_update_trust(role, executable, version=APP_VERSION,
                                       fingerprint=arguments[2], confirm=True)
        message = f'업데이트 신뢰 복구를 마쳤습니다. 설정과 데이터는 보존했습니다.\n이전 기록 보관: {archive}\n프로그램을 다시 실행하세요.'
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'WLR OnAir 신뢰 복구', 0x40)
        elif sys.stdout:
            print(message)
        return 0
    if '--onair-self-test' in sys.argv[1:]:
        if len(sys.argv) != 3 or sys.argv[1] != '--onair-self-test':
            raise RuntimeError('자체점검 결과 파일 경로가 필요합니다.')
        from build_selftest import run
        return run(role, APP_VERSION, sys.argv[2])
    executable = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve()
    os.environ['SMART_DUTY_MODE'] = role
    if '--onair-app' in sys.argv[1:]:
        for name in ('WLR_CONFIG_DIR', 'WLR_HEALTH_FILE', 'WLR_UPDATE_INSTANCE_ID'):
            if not os.environ.get(name):
                raise RuntimeError('WLR OnAir 실행 파일(업데이트 실행기)로 다시 시작해 주세요.')
        if getattr(sys, 'frozen', False):
            runpy.run_module('onair_backend', run_name='__main__', alter_sys=True)
        else:
            runpy.run_path(str(Path(__file__).parent / 'backend/main.py'), run_name='__main__')
        return 0
    if '--publish' in sys.argv[1:]:
        if role != 'ADMIN':
            raise RuntimeError('조회용에는 업데이트 배포 관리 기능이 없습니다.')
        from updater.setup_ui import publisher_ui
        publisher_ui(executable)
        return 0
    from updater.setup_ui import ensure_setup
    from updater.launcher import run
    ensure_setup(role, executable)
    return run(role, executable, version=APP_VERSION)


# v3.18.0 직원 PC 메시지 상자: 내부 용어(서명·순서 번호·JSON·연결 파일 등)는 풀어 쓰고, 원문은 작게 '관리자 전달용'으로만 남긴다.
CAPTURE_HINT = '이 창을 캡처해 관리자에게 보내 주세요.'
_TECHNICAL = ('JSON', 'json', '서명', '순서 번호', '배포 차수', '해시', '연결 파일', '연결 폴더', '심볼릭', '정션',
              'state.json', '프로필', '인스턴스', '필드', 'Errno', 'Error', 'Traceback', 'sequence')


def startup_failure_message(exc):
    text = str(exc).strip() or exc.__class__.__name__
    if CAPTURE_HINT in text:
        body = text
    elif any(word in text for word in _TECHNICAL):
        body = f'프로그램을 준비하지 못했습니다. {CAPTURE_HINT}\n\n(관리자 전달용: {text})'
    else:
        body = f'{text}\n\n{CAPTURE_HINT}'
    return f'WLR OnAir를 시작하지 못했습니다.\n\n{body}'


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        message = startup_failure_message(exc)
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'WLR OnAir', 0x10)
        elif sys.stderr:
            print(message, file=sys.stderr)
        raise SystemExit(1)
