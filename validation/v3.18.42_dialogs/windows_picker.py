"""Verify the real WinForms Browse dialog above a regular desktop window."""
from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes as w
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from updater.setup_ui import browse_result


# Keep this probe usable from the source ZIP without installing test packages.
def main():
    if sys.platform != 'win32':
        print('SKIP: This check needs a Windows desktop.')
        return 0
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    signatures = {
        'GetProcessWindowStation': (w.HANDLE, ()),
        'GetUserObjectInformationW': (w.BOOL, (w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD))),
        'CreateWindowExW': (w.HWND, (w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID)),
        'SetForegroundWindow': (w.BOOL, (w.HWND,)),
        'IsWindowVisible': (w.BOOL, (w.HWND,)),
        'EnumWindows': (w.BOOL, (enum_proc, w.LPARAM)),
        'GetWindowThreadProcessId': (w.DWORD, (w.HWND, ctypes.POINTER(w.DWORD))),
        'GetClassNameW': (ctypes.c_int, (w.HWND, w.LPWSTR, ctypes.c_int)),
        'GetWindow': (w.HWND, (w.HWND, w.UINT)),
        'GetWindowLongW': (w.LONG, (w.HWND, ctypes.c_int)),
        'PostMessageW': (w.BOOL, (w.HWND, w.UINT, w.WPARAM, w.LPARAM)),
        'DestroyWindow': (w.BOOL, (w.HWND,)),
    }
    for name, (result, args) in signatures.items():
        fn = getattr(user32, name)
        fn.restype = result
        fn.argtypes = args

    # UOI_FLAGS reports whether this runner has a visible desktop session.
    flags = (w.DWORD * 3)()
    required = w.DWORD()
    if not user32.GetUserObjectInformationW(user32.GetProcessWindowStation(), 1, flags,
                                            ctypes.sizeof(flags), ctypes.byref(required)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not flags[2] & 1:
        print('SKIP: No visible window station on this Windows runner.')
        return 0

    backdrop = user32.CreateWindowExW(0, 'STATIC', 'WLR OnAir Browse test', 0x10CF0000,
                                      20, 20, 860, 680, None, None, None, None)
    if not backdrop:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        with tempfile.TemporaryDirectory(prefix='onair-browse-') as temp:
            for field, kind, value in [('repository', 'folder', temp), ('release', 'zip', str(Path(temp) / 'existing.zip'))]:
                user32.SetForegroundWindow(backdrop)
                picker_process = None
                launch = subprocess.Popen

                def record_process(*args, **kwargs):
                    nonlocal picker_process
                    picker_process = launch(*args, **kwargs)
                    return picker_process

                def find_dialog(hwnd, _):
                    process_id = w.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
                    if process_id.value == picker_process.pid and user32.IsWindowVisible(hwnd):
                        name = ctypes.create_unicode_buffer(64)
                        user32.GetClassNameW(hwnd, name, len(name))
                        if name.value == '#32770':
                            dialogs.append(hwnd)
                            return False
                    return True

                dialogs = []
                with ThreadPoolExecutor(max_workers=1) as executor, patch.object(subprocess, 'Popen', record_process):
                    future = executor.submit(browse_result, field, kind, {field: value})
                    try:
                        deadline = time.monotonic() + 20
                        while not dialogs and time.monotonic() < deadline:
                            if future.done():
                                raise AssertionError(f'{kind} picker exited before its dialog was visible: {future.result()}')
                            if picker_process:
                                user32.EnumWindows(enum_proc(find_dialog), 0)
                            if not dialogs:
                                time.sleep(.05)
                        assert dialogs, f'{kind} picker did not open'
                        dialog = dialogs[0]
                        owner = user32.GetWindow(dialog, 4)  # GW_OWNER
                        assert owner and user32.IsWindowVisible(owner), f'{kind} picker lost its visible owner'
                        assert user32.GetWindowLongW(owner, -20) & 8, f'{kind} picker owner is not topmost'
                        window = user32.GetWindow(backdrop, 3)  # GW_HWNDPREV
                        while window and window != dialog:
                            window = user32.GetWindow(window, 3)
                        assert window == dialog, f'{kind} picker is behind the normal window'
                        assert user32.PostMessageW(dialog, 0x0010, 0, 0), 'Could not dismiss the picker'
                        result = future.result(timeout=8)
                        assert result['values'] == {}, f'{kind} cancellation unexpectedly changed the field'
                        print(f'PASS: {kind} picker is above a normal window and cancellation preserves the path.', flush=True)
                    finally:
                        if not future.done() and picker_process:
                            picker_process.terminate()
    finally:
        user32.DestroyWindow(backdrop)
    return 0


if __name__ == '__main__':
    sys.exit(main())
