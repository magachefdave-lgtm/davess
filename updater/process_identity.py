"""Exact process identity and owned candidate containment; never kill old apps."""
from __future__ import annotations
import os
from pathlib import Path
import time
from . import core

IS_WINDOWS = os.name == 'nt'


def _kernel():
    import ctypes
    from ctypes import wintypes as w
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    for name, args, result in (
        ('OpenProcess', [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        ('CloseHandle', [w.HANDLE], w.BOOL),
        ('WaitForSingleObject', [w.HANDLE, w.DWORD], w.DWORD),
        ('GetProcessTimes', [w.HANDLE] + [ctypes.POINTER(w.FILETIME)] * 4, w.BOOL),
        ('QueryFullProcessImageNameW', [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)], w.BOOL),
        ('CreateJobObjectW', [ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
        ('SetInformationJobObject', [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
        ('AssignProcessToJobObject', [w.HANDLE, w.HANDLE], w.BOOL),
        ('QueryInformationJobObject', [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p], w.BOOL)):
        f = getattr(k, name); f.argtypes = args; f.restype = result
    return k


class Identity:
    def __init__(self, pid):
        self.pid, self.handle = int(pid), None
        if IS_WINDOWS:
            import ctypes
            from ctypes import wintypes as w
            k = _kernel()
            self.handle = k.OpenProcess(0x100000 | 0x1000, False, self.pid)
            if not self.handle:
                if ctypes.get_last_error() == 87:
                    raise ProcessLookupError(self.pid)
                raise core.UpdateError('기존 프로그램의 종료 확인 권한이 없습니다.')
            try:
                creation, exit_time, kernel_time, user = (w.FILETIME() for _ in range(4))
                if not k.GetProcessTimes(self.handle, *[ctypes.byref(v) for v in (creation, exit_time, kernel_time, user)]):
                    raise OSError('GetProcessTimes')
                self.created = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                buf, length = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
                if not k.QueryFullProcessImageNameW(self.handle, 0, buf, ctypes.byref(length)):
                    raise OSError('QueryFullProcessImageName')
                self.path = os.path.normcase(os.path.abspath(buf.value))
            except BaseException:
                self.close(); raise
        else:
            raw = Path(f'/proc/{self.pid}/stat').read_text().rsplit(')', 1)[1].split()
            self.created = int(raw[19])
            self.path = str(Path(f'/proc/{self.pid}/exe').resolve(strict=True))

    def document(self):
        return dict(pid=self.pid, created=self.created, path=self.path)

    def alive(self):
        if IS_WINDOWS:
            result = _kernel().WaitForSingleObject(self.handle, 0)
            if result not in (0, 258):
                raise core.UpdateError('기존 프로그램의 종료를 확인하지 못했습니다.')
            return result == 258
        try:
            raw = Path(f'/proc/{self.pid}/stat').read_text().rsplit(')', 1)[1].split()
            return int(raw[19]) == self.created and raw[0] != 'Z'
        except FileNotFoundError:
            return False

    def close(self):
        if self.handle:
            _kernel().CloseHandle(self.handle); self.handle = None


def parent_map():
    if IS_WINDOWS:
        from .launcher import _process_parent_map
        return _process_parent_map()
    out = {}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            raw = path.read_text().rsplit(')', 1)[1].split()
            out[int(path.parent.name)] = int(raw[1])
        except (OSError, ValueError, IndexError):
            pass
    return out


def capture_old_chain(role, current_pid, active_path):
    """Capture the app/bootloader and supervisor/bootloader before shutdown.

    On Windows every ancestor must have this role's executable name. The first
    different image is the shell, not part of the application. At least one
    different path (the installed launcher) is required unless an earlier manual
    helper supervises the same cached executable.
    """
    from .launcher import EXECUTABLES
    parents, identities, pid = parent_map(), [], current_pid
    try:
        for _ in range(8):
            try:
                item = Identity(pid)
            except (FileNotFoundError, ProcessLookupError):
                if len(identities) >= 2: break
                raise
            if identities and item.created > identities[-1].created:
                item.close(); break
            if IS_WINDOWS and Path(item.path).name.lower() != EXECUTABLES[role].lower():
                item.close(); break
            if not IS_WINDOWS and identities and item.path != identities[0].path:
                item.close(); break
            identities.append(item)
            # New supervisors advertise their own identity boundary; the old
            # installed launcher is recognized by its role-fixed Windows image.
            boundary = os.environ.get('WLR_SUPERVISOR_PID')
            if boundary and str(pid) == boundary:
                # Include the supervisor's onefile bootloader on Windows.
                if IS_WINDOWS:
                    parent = Identity(parents[pid])
                    if parent.path == item.path and parent.created <= item.created:
                        identities.append(parent)
                    else: parent.close()
                break
            pid = parents.get(pid, 0)
            if pid <= 1:
                break
        if len(identities) < 2:
            raise core.UpdateError('현재 프로그램과 실행기를 확인하지 못했습니다.')
        if IS_WINDOWS and identities[0].path != os.path.normcase(os.path.abspath(active_path)):
            raise core.UpdateError('현재 실행 파일이 검증된 버전과 다릅니다.')
        return identities
    except BaseException:
        for item in identities: item.close()
        raise


def reopen_chain(documents):
    identities = []
    try:
        if not isinstance(documents, list) or not 2 <= len(documents) <= 8:
            raise core.UpdateError('기존 실행 계통이 올바르지 않습니다.')
        for doc in documents:
            item = Identity(doc['pid']); identities.append(item)
            if item.document() != doc or not item.alive():
                raise core.UpdateError('기존 프로그램이 변경되어 재시작하지 않았습니다.')
        return identities
    except BaseException:
        for item in identities: item.close()
        raise


def wait_old_chain(chain, timeout_seconds=30):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if all(not item.alive() for item in chain): return True
        time.sleep(.05)
    return all(not item.alive() for item in chain)


class CandidateTracker:
    """Windows Job membership survives root exit; POSIX uses a private session.

    Windows starts suspended and joins the job BEFORE it can create children.
    A failed job assignment never resumes the candidate. No kill-on-close flag
    is set: a helper failure must not force-close the running application.
    """
    def __init__(self, process):
        self.process, self.job, self.ownership_complete = process, None, False
        if IS_WINDOWS:
            import ctypes
            from ctypes import wintypes as w
            k = _kernel()
            self.job = k.CreateJobObjectW(None, None)
            class BasicLimit(ctypes.Structure):
                _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                           ('flags', w.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                           ('active_limit', w.DWORD), ('affinity', ctypes.c_size_t),
                           ('priority', w.DWORD), ('scheduling', w.DWORD)]
            class ExtendedLimit(ctypes.Structure):
                _fields_ = [('basic', BasicLimit), ('io', ctypes.c_ulonglong * 6),
                           ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                           ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]
            limits = ExtendedLimit(); limits.basic.flags = 0x800  # BREAKAWAY_OK, no kill-on-close
            if self.job and not k.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                process.kill(); process.wait(timeout=5); self.close()
                raise core.UpdateError('독립 업데이트 도우미 실행 환경을 준비하지 못했습니다.')
            if not self.job or not k.AssignProcessToJobObject(self.job, int(process._handle)):
                process.kill(); process.wait(timeout=5); self.close()
                raise core.UpdateError('새 프로그램의 실행 계통을 확보하지 못했습니다.')
            ntdll = ctypes.WinDLL('ntdll')
            ntdll.NtResumeProcess.argtypes = [w.HANDLE]; ntdll.NtResumeProcess.restype = ctypes.c_long
            if ntdll.NtResumeProcess(int(process._handle)) != 0:
                process.kill(); process.wait(timeout=5); self.close()
                raise core.UpdateError('새 프로그램을 시작하지 못했습니다.')
        self.ownership_complete = True

    def stopped(self):
        if not self.ownership_complete: return False
        if IS_WINDOWS:
            import ctypes
            from ctypes import wintypes as w
            class Accounting(ctypes.Structure):
                _fields_ = [('user', ctypes.c_longlong), ('kernel', ctypes.c_longlong),
                           ('period_user', ctypes.c_longlong), ('period_kernel', ctypes.c_longlong),
                           ('faults', w.DWORD), ('total', w.DWORD), ('active', w.DWORD), ('terminated', w.DWORD)]
            value = Accounting()
            if not _kernel().QueryInformationJobObject(self.job, 1, ctypes.byref(value), ctypes.sizeof(value), None):
                self.ownership_complete = False; return False
            return value.active == 0
        for path in Path('/proc').glob('[0-9]*/stat'):
            try:
                fields = path.read_text().rsplit(')', 1)[1].split()
                if int(fields[2]) == self.process.pid and fields[0] != 'Z': return False
            except FileNotFoundError: pass
            except (OSError, ValueError, IndexError):
                self.ownership_complete = False; return False
        return self.process.poll() is not None

    def close(self):
        if self.job:
            _kernel().CloseHandle(self.job); self.job = None


def wait_candidate_stopped(tracker, timeout_seconds=5):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if tracker.stopped(): return True
        time.sleep(.05)
    return tracker.stopped()
