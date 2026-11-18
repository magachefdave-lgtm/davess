"""Isolated packaged-runtime smoke test. Never imports the application backend.

No server, browser, production profile, database, signing key, updater or publisher
operation is started. The only persistent output is the requested JSON report.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import sys
import traceback

from build_preflight import rejection_reasons, runtime_info


def verify_payload(bundle, role, version):
    info = json.loads((bundle / 'onair_build_info.json').read_text(encoding='utf-8'))
    if info.get('role') != role:
        raise ValueError('Embedded role mismatch')
    if info.get('version') != version or info.get('build_method') != 'clean-source-pyinstaller':
        raise ValueError('Embedded version/build method mismatch')
    for name, expected in info['assets'].items():
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or not name.startswith('frontend/dist/'):
            raise ValueError('Unsafe asset path')
        if hashlib.sha256((bundle / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f'Asset hash mismatch: {name}')
    if 'frontend/dist/index.html' not in info['assets']:
        raise ValueError('Missing frontend asset hash')
    return info


def verify_runtime(info, actual=None):
    expected = info.get('python_runtime')
    if not isinstance(expected, dict):
        raise ValueError('Missing embedded Python runtime identity')
    actual = runtime_info() if actual is None else actual
    if rejection_reasons(actual):
        raise ValueError('Frozen Python runtime mismatch: ' + '; '.join(rejection_reasons(actual)))
    keys = ('implementation', 'version', 'releaselevel', 'platform', 'bits',
            'gil_disabled', 'sysconfig_platform')
    if any(actual.get(key) != expected.get(key) for key in keys):
        raise ValueError('Frozen Python runtime mismatch against build metadata')
    return actual


def check_modules(role):
    required = ('fastapi', 'pydantic', 'starlette', 'uvicorn', 'websockets', 'cryptography',
                '_ctypes', '_cffi_backend', 'ssl', 'sqlite3', 'updater.core', 'updater.launcher',
                'updater.profiles', 'updater.setup_ui', 'uvicorn.loops.asyncio',
                'uvicorn.protocols.http.h11_impl', 'uvicorn.protocols.websockets.websockets_impl',
                'uvicorn.lifespan.on', 'anyio._backends._asyncio',
                'backend', 'backend.request_access_schema', 'backend.request_access_store',
                'backend.request_access_local', 'backend.request_recovery', 'backend.request_access_api',
                'backend.dinner_schema', 'backend.dinner_service', 'backend.dinner_api', 'backend.master_auth')
    for name in required:
        importlib.import_module(name)
    backend = importlib.util.find_spec('onair_backend')
    if backend is None or backend.loader.get_code('onair_backend') is None:
        raise ValueError('Frozen backend code missing')
    has_publisher = importlib.util.find_spec('updater.publisher') is not None
    if has_publisher != (role == 'ADMIN'):
        raise ValueError('Publisher role isolation failed')
    if role == 'ADMIN':
        importlib.import_module('updater.publisher')  # import has no key/repository side effects
    import sqlite3
    with sqlite3.connect(':memory:') as connection:
        if connection.execute('SELECT 1').fetchone() != (1,):
            raise ValueError('SQLite runtime failed')
    # RFC 8032 public verification vector: no private key is created or loaded.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    public = bytes.fromhex('d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a')
    signature = bytes.fromhex('e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555f'
                              'b8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b')
    Ed25519PublicKey.from_public_bytes(public).verify(signature, b'')
    import ctypes
    for name in ('kernel32', 'user32', 'shell32') + (('crypt32',) if role == 'ADMIN' else ()):
        ctypes.WinDLL(name, use_last_error=True)
    return list(required)


def run(role, version, report_path):
    report = {'ok': False, 'role': role, 'version': version}
    target = Path(report_path)
    try:
        if not target.is_absolute() or not target.parent.is_dir() or target.exists():
            raise ValueError('Self-test requires a new absolute report filename in an existing directory')
        if not getattr(sys, 'frozen', False) or sys.platform != 'win32':
            raise ValueError('Self-test requires a frozen Windows executable')
        for name in ('stdout', 'stderr'):
            if getattr(sys, name) is None:
                setattr(sys, name, open(os.devnull, 'w'))
        info = verify_payload(Path(sys._MEIPASS), role, version)
        actual_runtime = verify_runtime(info)
        modules = check_modules(role)
        report.update({'ok': True, 'python': platform.python_version(), 'python_runtime': actual_runtime, 'source_fingerprint': info['source_fingerprint'],
                       'frontend_sha256': info.get('frontend_sha256'), 'modules_imported': modules,
                       'publisher_present': role == 'ADMIN', 'backend_executed': False,
                       'production_data_accessed': False, 'dpapi_roundtrip_tested': False,
                       'tray_interaction_tested': False})
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        report['traceback'] = traceback.format_exc()
    try:
        with target.open('x', encoding='utf-8') as fp:
            json.dump(report, fp, ensure_ascii=False, indent=2)
            fp.write('\n')
    except Exception:
        return 1
    return 0 if report['ok'] else 1
