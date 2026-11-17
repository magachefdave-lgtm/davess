"""Launch-authorized local sessions; no HTTP ticket issuance endpoint."""
from dataclasses import dataclass
import hashlib, ipaddress, json, secrets, threading, time
from urllib.parse import urlsplit
from .request_access_schema import AccessError
from .request_access_store import ReceiptRecord

@dataclass(frozen=True)
class LocalSession:
    session_id: str
    origin: str
    expires_at: int
    token: str | None = None

_vault=None
_tickets={}
_lock=threading.Lock()

def configure_vault(vault):
    global _vault
    _vault=vault

def _deny():return AccessError('FORBIDDEN','프로그램에서 연 브라우저 연결이 필요합니다.',status=403)
def _hash(value):return hashlib.sha256(value.encode()).hexdigest()
def _store(vault):
    result=vault if vault is not None else _vault
    if result is None:raise AccessError('UNSUPPORTED_PROFILE','개인 보관소를 지원하지 않는 실행 환경입니다.')
    return result

def _origin(origin):
    try:
        p=urlsplit(origin)
        if p.scheme not in ('http','https') or p.username or p.password or p.path or p.query or p.fragment or not p.hostname:raise ValueError()
        if p.hostname!='localhost' and not ipaddress.ip_address(p.hostname).is_loopback:raise ValueError()
        port=p.port
        if origin!=f'{p.scheme}://{p.netloc}':raise ValueError()
        return origin
    except Exception:raise _deny() from None

def issue_launch_ticket(origin,now,vault=None):
    store=_store(vault);origin=_origin(origin)
    token=secrets.token_urlsafe(32)
    with _lock:
        for key,value in list(_tickets.items()):
            if now>=value[1]:del _tickets[key]
        _tickets[_hash(token)]=(origin,int(now)+90,str(store.path))
    return token

def exchange_launch_ticket(ticket,origin,now,vault=None):
    store=_store(vault);origin=_origin(origin)
    with _lock:
        entry=_tickets.pop(_hash(ticket),None)
    if not entry or entry[0]!=origin or now>=entry[1] or entry[2]!=str(store.path):raise _deny()
    token=secrets.token_urlsafe(32);digest=_hash(token);expiry=int(now)+8*3600
    store.put_session(digest,origin,expiry)
    return LocalSession(digest,origin,expiry,token)

def require_local_session(request,token,vault=None,now=None):
    store=_store(vault)
    try:
        host=request.client.host
        # Starlette's in-process test transport is not a network peer.
        if host!='testclient' and not ipaddress.ip_address(host).is_loopback:raise ValueError()
        if not isinstance(token,str) or not token:raise ValueError()
        entry=store.get_session(_hash(token))
        if not entry or (int(time.time()) if now is None else now)>=entry['expires_at']:raise ValueError()
        actual=f'{request.url.scheme}://{request.headers.get("host", "")}'
        if _origin(actual)!=entry['origin']:raise ValueError()
        origin=request.headers.get('origin')
        if origin:
            if origin!=entry['origin']:raise ValueError()
        elif request.method.upper() not in ('GET','HEAD'):raise ValueError()
        return LocalSession(_hash(token),entry['origin'],entry['expires_at'])
    except AccessError:raise
    except Exception:raise _deny() from None

def receipt_digest(receipts):
    rows=[{'request_uid':r.request_uid,'request_id':r.request_id,'receipt_token':r.receipt_token} for r in receipts]
    rows.sort(key=lambda r:r['request_uid'])
    return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def persist_claim(vault,bundle):
    try:
        records=[r if isinstance(r,ReceiptRecord) else ReceiptRecord(**r) for r in bundle.receipts]
        vault.merge_receipts(records)
        stored={r.request_uid:r for r in vault.list_receipts(bundle.dataset_id)}
        if any(stored.get(r.request_uid)!=r for r in records):raise ValueError()
        return receipt_digest(records)
    except Exception:raise AccessError('LOCAL_PERSIST_FAILED','신청 권한을 개인 보관소에 저장하지 못했습니다.',status=503,retryable=True) from None

def redact_access_text(text):
    """Mask legacy receipt URLs and structured secret fields before logging."""
    import re
    text=re.sub(r'(/api/duty/request-status/)[^\s?"\']+',r'\1[REDACTED]',str(text),flags=re.I)
    return re.sub(r'((?:receipt[_-]?token|session[_-]?token|ticket|proof|X-OnAir-Local-Session|X-OnAir-Dinner-Key)["\']?\s*[:=]\s*["\']?)[^\s,;"\'&]+',r'\1[REDACTED]',text,flags=re.I)

class CredentialLogFilter:
    def filter(self,record):
        record.msg=redact_access_text(record.getMessage());record.args=()
        return True
