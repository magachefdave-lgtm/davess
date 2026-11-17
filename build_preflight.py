"""Small, dependency-free Python/ABI policy and path-free preflight diagnostics.

Keep this module importable by older Python 3 versions so unsupported installed
interpreters can explain why they are refused. It never installs or downloads.
"""
import importlib
import platform
import struct
import sys
import sysconfig

SUPPORTED_MINORS = (11, 12, 13, 14)
REQUIREMENT = 'CPython 3.11-3.14 final, Windows AMD64, 64-bit, normal GIL'


def runtime_info():
    return {'implementation': sys.implementation.name,
            'version': list(sys.version_info[:3]),
            'releaselevel': sys.version_info.releaselevel,
            'platform': sys.platform,
            'machine': platform.machine(),
            'bits': struct.calcsize('P') * 8,
            'gil_disabled': bool(sysconfig.get_config_var('Py_GIL_DISABLED')),
            'sysconfig_platform': sysconfig.get_platform()}


def rejection_reasons(info):
    version = tuple(info['version'][:3])
    reasons = []
    if info['implementation'] != 'cpython':
        reasons.append('implementation is not CPython')
    if version[0] != 3 or version[1] not in SUPPORTED_MINORS:
        reasons.append('Python {0}.{1}.{2}: supported series are 3.11, 3.12, 3.13, 3.14'.format(*version))
    if info['releaselevel'] != 'final':
        reasons.append('pre-release Python is not supported; use a final release')
    if info['platform'] != 'win32':
        reasons.append('this is not a Windows runtime')
    if info['bits'] != 64:
        reasons.append('{0}-bit runtime; a 64-bit runtime is required'.format(info['bits']))
    if info['sysconfig_platform'] != 'win-amd64':
        reasons.append('runtime architecture is not Windows AMD64; ARM64 and 32-bit are unsupported')
    if info['gil_disabled']:
        reasons.append('free-threaded runtime; normal GIL is required')
    return reasons


def requirements_name(info):
    reasons = rejection_reasons(info)
    if reasons:
        raise ValueError('; '.join(reasons))
    return 'requirements-build-py{0}{1}.txt'.format(*info['version'][:2])


def runtime_dll(version):
    if version[0] != 3 or version[1] not in SUPPORTED_MINORS:
        raise ValueError('Unsupported Python DLL series')
    return 'python{0}{1}.dll'.format(*version[:2])


def check_candidate(log_path=None, info=None):
    info = runtime_info() if info is None else info
    reasons = rejection_reasons(info)
    missing_modules = []
    if not reasons:
        # Build-only capability check: frozen app runtime checks must stay ABI-only.
        for name in ('venv', 'ensurepip'):
            try:
                importlib.import_module(name)
            except Exception:
                missing_modules.append(name)
        if missing_modules:
            reasons.append('build modules unavailable: ' + ', '.join(missing_modules))
    # Only whitelisted runtime facts are logged. Never log sys.executable,
    # user paths, environment variables, raw launcher output or exception text.
    version = '{0}.{1}.{2}'.format(*info['version'][:3])
    line = 'Python {0}; {1}-bit; {2}; GIL {3}\n'.format(
        version, info['bits'], 'final' if info['releaselevel'] == 'final' else 'pre-release',
        'disabled' if info['gil_disabled'] else 'normal')
    if reasons:
        line += ''.join('REJECTED: ' + reason + '\n' for reason in reasons)
        if missing_modules:
            line += 'Embeddable Python is unsupported for builds.\n'
            line += u'Python \uc124\uce58 \ud504\ub85c\uadf8\ub7a8\uc5d0\uc11c pip\ub97c \ud3ec\ud568\ud574 \uc218\uc815(Modify) \ub610\ub294 \ubcf5\uad6c(Repair)\ud574 \uc8fc\uc138\uc694. \ub2e4\ub978 \uc124\uce58 \ud6c4\ubcf4\uac00 \uc788\uc73c\uba74 \uacc4\uc18d \ud655\uc778\ud569\ub2c8\ub2e4.\n'
        else:
            line += u'\uc0ac\uc6a9 \ubd88\uac00: ' + version + u' - 64\ube44\ud2b8 Windows\uc6a9 Python 3.11~3.14 \uc815\uc2dd\ud310\uc774 \ud544\uc694\ud569\ub2c8\ub2e4.\n'
    else:
        line += 'ACCEPTED: ' + requirements_name(info) + '\n'
        line += u'\uc0ac\uc6a9 \uac00\ub2a5: Python ' + version + u' (64\ube44\ud2b8 \uc815\uc2dd\ud310)\n'
    try:
        print(line, end='', flush=True)
    except UnicodeEncodeError:
        # Very old consoles/runtimes: keep the ASCII facts visible; the log keeps everything.
        print(line.encode('ascii', 'replace').decode('ascii'), end='', flush=True)
    if log_path:
        try:
            with open(str(log_path), 'a', encoding='utf-8') as log:
                log.write(line)
        except OSError:
            print('WARNING: Cannot save the preflight log; keep this screen.', flush=True)
    return 10 if reasons else 0


if __name__ == '__main__':
    try:
        raise SystemExit(check_candidate(sys.argv[1] if len(sys.argv) > 1 else None))
    except Exception:
        # Runtime/import/startup errors have a separate, non-accepting result.
        # Details can contain private paths, so do not print a raw traceback.
        print('PROBE COULD NOT RUN: runtime information could not be read.', flush=True)
        raise SystemExit(20)
