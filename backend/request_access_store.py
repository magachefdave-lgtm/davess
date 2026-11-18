"""Private per-user receipt storage. Windows uses current-user DPAPI.

POSIX uses filesystem isolation only, never claims DPAPI encryption.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path
import copy, ctypes, hashlib, json, os, secrets, stat, tempfile, threading, uuid
from .request_access_schema import AccessError

@dataclass(frozen=True)
class ReceiptRecord:
    dataset_id: str
    request_uid: str
    request_id: int
    receipt_token: str
    status: str

@dataclass(frozen=True)
class PendingSubmission:
    operation_id: str
    input_digest: str
    receipt_token: str
    action: str

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()

def _error(code='LOCAL_PERSIST_FAILED'):
    return AccessError(code, '개인 권한 보관소를 사용할 수 없습니다.', status=503 if code=='LOCAL_PERSIST_FAILED' else 409, retryable=code=='LOCAL_PERSIST_FAILED')

def _dpapi(data, protect):
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]
    buf=ctypes.create_string_buffer(data);src=Blob(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte)));out=Blob()
    entropy_buf=ctypes.create_string_buffer(b'WLR.OnAir.RequestAccess.v1')
    entropy=Blob(len(entropy_buf)-1,ctypes.cast(entropy_buf,ctypes.POINTER(ctypes.c_ubyte)))
    if protect:
        ok=ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src),None,ctypes.byref(entropy),None,None,1,ctypes.byref(out))
    else:
        ok=ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src),None,ctypes.byref(entropy),None,None,1,ctypes.byref(out))
    if not ok: raise _error()
    try:return ctypes.string_at(out.pbData,out.cbData)
    finally:ctypes.windll.kernel32.LocalFree(out.pbData)

class ReceiptVault:
    def __init__(self, profile_dir: Path, *, create=True):
        if not profile_dir or not Path(profile_dir).is_absolute():raise _error('UNSUPPORTED_PROFILE')
        p=Path(profile_dir).absolute()
        if str(p).startswith(('\\\\','//')):raise _error('UNSUPPORTED_PROFILE')
        for part in [p,*p.parents]:
            if part.is_symlink() or (getattr(part,'is_junction',lambda:False)()):raise _error('UNSUPPORTED_PROFILE')
        if os.name=='nt':
            if ctypes.windll.kernel32.GetDriveTypeW(str(p.anchor))==4:raise _error('UNSUPPORTED_PROFILE')
        else:
            # Reject network mounts, even when exposed under a local pathname.
            try:
                mounts=[]
                for line in Path('/proc/mounts').read_text().splitlines():
                    cols=line.split()
                    if len(cols)>2 and (str(p)==cols[1] or str(p).startswith(cols[1].rstrip('/')+'/')):mounts.append((len(cols[1]),cols[2]))
                if mounts and max(mounts)[1] in {'cifs','smbfs','nfs','nfs4','9p','sshfs','fuse.sshfs'}:raise _error('UNSUPPORTED_PROFILE')
            except OSError:pass
        if create:
            p.mkdir(parents=True,exist_ok=True,mode=0o700)
        elif not p.is_dir():
            raise _error('UNSUPPORTED_PROFILE')
        if os.name!='nt':
            st=p.stat()
            if st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)&0o077:raise _error('UNSUPPORTED_PROFILE')
        self.profile_dir=p;self.path=p/'request_access.v1.dat';self.lock_path=p/'request_access.v1.lock'
        with _LOCKS_GUARD:self._lock=_LOCKS.setdefault(str(self.path),threading.RLock())
    def _safe(self,path):
        if path.is_symlink():raise _error()
        if path.exists() and os.name!='nt':
            s=path.stat()
            if s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)&0o077 or s.st_nlink!=1:raise _error()
    @contextmanager
    def _locked(self):
        with self._lock:
            self._safe(self.lock_path)
            fd=os.open(self.lock_path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
            try:
                if os.name=='nt':
                    import msvcrt
                    if os.fstat(fd).st_size==0:os.write(fd,b'0')
                    os.lseek(fd,0,0);msvcrt.locking(fd,msvcrt.LK_LOCK,1)
                else:
                    import fcntl
                    fcntl.flock(fd,fcntl.LOCK_EX)
                yield
            except AccessError:raise
            except Exception:raise _error() from None
            finally:os.close(fd)
    def _read(self):
        self._safe(self.path)
        if not self.path.exists():return {'version':1,'device_id':str(uuid.uuid4()),'receipts':{},'pending':{},'recoveries':{},'sessions':{}}
        try:
            raw=self.path.read_bytes();prefix=b'WLRDP1\0' if os.name=='nt' else b'WLRPOSIX1\0'
            if not raw.startswith(prefix):raise ValueError()
            raw=raw[len(prefix):]
            if os.name=='nt':raw=_dpapi(raw,False)
            obj=json.loads(raw)
            if obj.get('version')!=1 or not all(isinstance(obj.get(k),dict) for k in ('receipts','pending','recoveries','sessions')):raise ValueError()
            return obj
        except Exception:raise _error() from None
    def _write(self,obj):
        raw=json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        raw=(b'WLRDP1\0'+_dpapi(raw,True)) if os.name=='nt' else b'WLRPOSIX1\0'+raw
        self._safe(self.path)
        fd,name=tempfile.mkstemp(prefix='.request-access-',dir=self.profile_dir)
        try:
            with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            os.replace(name,self.path)
            if self._read()!=obj:raise _error()
        finally:
            if os.path.exists(name):os.unlink(name)
    @contextmanager
    def transaction(self):
        with self._locked():
            obj=self._read();yield obj;self._write(obj)
    def read(self):
        with self._locked():return copy.deepcopy(self._read())
    @property
    def device_id(self):
        with self.transaction() as d:result=d['device_id']
        return result
    def merge_receipts(self,records):
        with self.transaction() as d:
            for record in records:
                if not isinstance(record,ReceiptRecord) or not record.receipt_token or not record.request_uid or not record.dataset_id:raise _error()
                d['receipts'].setdefault(record.dataset_id,{})[record.request_uid]=asdict(record)
    def list_receipts(self,dataset_id):return [ReceiptRecord(**r) for r in self.read()['receipts'].get(dataset_id,{}).values()]
    def existing_dinner_key(self, dataset_id, name):
        # Vault changes use atomic file replacement. A lookup only needs one
        # complete snapshot, and must not initialize a sibling's lock or keys.
        with self._lock:
            key = self._read().get('dinner_keys', {}).get(dataset_id, {}).get(name)
            if key is not None and (not isinstance(key, str) or len(key) < 43):
                raise _error()
            return key

    def dinner_key(self, dataset_id, name):
        """Return a capability only after its first atomic durable write succeeds.

        Dataset generations deliberately are not part of this key: restoring a
        backup must not strand an employee's response on the same installation.
        CA connection revocation is enforced separately in the shared database.
        """
        if not isinstance(dataset_id, str) or not dataset_id or not isinstance(name, str) or not name:
            raise _error()
        with self._locked():
            data = self._read()
            keys = data.setdefault('dinner_keys', {}).setdefault(dataset_id, {})
            key = keys.get(name)
            if key is not None:
                if not isinstance(key, str) or len(key) < 43:
                    raise _error()
                return key
            key = secrets.token_urlsafe(32)
            keys[name] = key
            self._write(data)
            return key
    def prepare_submission(self,dataset_id,operation_id,input_digest,action):
        if action not in {'SUBMIT','AMEND','CANCEL_ABSENCE'}:raise AccessError('OPERATION_CONFLICT','지원하지 않는 신청 작업입니다.')
        with self.transaction() as d:
            entries=d['pending'].setdefault(dataset_id,{})
            old=entries.get(operation_id) or d.get('completed_submissions',{}).get(dataset_id,{}).get(operation_id)
            if old and (old['input_digest']!=input_digest or old['action']!=action):raise AccessError('OPERATION_CONFLICT','처리번호의 신청 내용이 다릅니다.')
            if not old:old=entries[operation_id]=asdict(PendingSubmission(operation_id,input_digest,secrets.token_urlsafe(32),action))
            result=PendingSubmission(**old)
        return result
    def finish_submission(self,dataset_id,operation_id,record):
        with self.transaction() as d:
            pending=d['pending'].get(dataset_id,{}).get(operation_id) or d.get('completed_submissions',{}).get(dataset_id,{}).get(operation_id)
            if not pending or record.dataset_id!=dataset_id or record.receipt_token!=pending['receipt_token']:raise AccessError('OPERATION_CONFLICT','신청 보관 정보가 다릅니다.')
            d['receipts'].setdefault(dataset_id,{})[record.request_uid]=asdict(record)
            d.setdefault('completed_submissions',{}).setdefault(dataset_id,{})[operation_id]=pending
            d['pending'].get(dataset_id,{}).pop(operation_id,None)
    def list_pending_submissions(self,dataset_id):return [PendingSubmission(**r) for r in self.read()['pending'].get(dataset_id,{}).values()]
    def prepare_recovery(self,dataset_id,operation_id,requested_name):
        with self.transaction() as d:
            old=d['recoveries'].get(operation_id)
            if old and (old['dataset_id']!=dataset_id or old['requested_name']!=requested_name):raise AccessError('OPERATION_CONFLICT','처리번호의 복구 내용이 다릅니다.')
            if not old:old=d['recoveries'][operation_id]={'dataset_id':dataset_id,'requested_name':requested_name,'proof':secrets.token_urlsafe(32),'recovery_id':None}
            proof=old['proof']
        return proof
    def bind_recovery(self,operation_id,recovery_id):
        with self.transaction() as d:
            old=d['recoveries'].get(operation_id)
            if not old or old['recovery_id'] not in (None,recovery_id):raise AccessError('OPERATION_CONFLICT','복구 연결 정보가 다릅니다.')
            old['recovery_id']=recovery_id
    def read_recovery_proof(self,recovery_id):
        for r in self.read()['recoveries'].values():
            if r['recovery_id']==recovery_id:return r['proof']
        raise AccessError('FORBIDDEN','이 PC에서 요청한 복구가 아닙니다.',status=403)
    def put_session(self,token_hash,origin,expires_at):
        with self.transaction() as d:d['sessions'][token_hash]={'origin':origin,'expires_at':expires_at}
    def get_session(self,token_hash):return self.read()['sessions'].get(token_hash)
