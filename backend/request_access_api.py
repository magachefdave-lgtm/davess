"""HTTP integration for private local receipts and selected admin recovery."""
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit
import hashlib
import hmac
import ipaddress
import json
import sqlite3
import time
import uuid

from fastapi import Request, Header
from fastapi.responses import JSONResponse
from . import request_access_schema as schema
from . import request_access_local as local
from . import request_recovery as recovery
from .request_access_store import ReceiptVault, ReceiptRecord


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


class HostNamespace:
    """Live globals view also supports runpy and unregistered import loaders."""
    def __init__(self, values):
        self.values = values

    def __getattr__(self, name):
        try:
            return self.values[name]
        except KeyError:
            raise AttributeError(name) from None


def operation_id(value):
    if not isinstance(value, str) or not 8 <= len(value) <= 128:
        raise schema.AccessError('INVALID_INPUT', '처리 번호를 확인해 주세요.', 400)
    return value


class RequestAccessAPI:
    def __init__(self, host):
        self.h = host
        self._vault = None

    def enabled(self):
        if self.h._cfg('request_access_enabled', 'true').lower() in ('false', '0', 'no'):
            raise schema.AccessError('DISABLED', '신청 권한 복구 기능이 중지되어 있습니다.')

    def vault(self):
        self.enabled()
        if not self.h.MANAGED_CONFIG:
            raise schema.AccessError('UNSUPPORTED_PROFILE', '설치형 OnAir의 트레이에서 화면을 다시 열어 주세요.')
        profile = Path(self.h.CONFIG_DIR).parent
        if self._vault is None or self._vault.profile_dir != profile:
            self._vault = ReceiptVault(profile)
        return self._vault

    def issue_ticket(self, origin):
        return local.issue_launch_ticket(origin, int(time.time()), vault=self.vault())

    def authorize(self, request):
        return local.require_local_session(request, request.headers.get('x-onair-local-session'),
                                           vault=self.vault())

    def admin(self, request, token):
        self.enabled()
        self.h.require_admin(token)
        if not self.h.is_loopback_client(request) and not self.h.ADMIN_PASSWORD:
            raise schema.AccessError('FORBIDDEN', '원격 관리자 접속에는 관리자 암호가 필요합니다.', 403)

    @contextmanager
    def db(self, write=False):
        conn = self.h.get_db_connection(readonly=not write)
        try:
            cur = conn.cursor()
            if write:
                cur.execute('BEGIN IMMEDIATE')
                self.h.require_holiday_write_contract(cur)
                self.h.ensure_change_request_schema(cur)
                schema.ensure_request_access_schema(cur)
            yield cur
            if write:
                conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise schema.AccessError('STORAGE_UNAVAILABLE', '공유 자료를 처리하지 못했습니다. 연결 후 다시 확인해 주세요.', 503, True) from None
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def record(self, cur, row, dataset):
        uid = schema.ensure_request_uid(cur, row['id'])
        return ReceiptRecord(dataset, uid, row['id'], row['receipt_token'], row['status'])

    def prepared_identity(self):
        # A viewer can be the first upgraded process to use an older database.
        # Prepare additive schema under the existing guarded write transaction
        # before any read depends on dataset identity or recovery tables.
        with self.db(write=True) as cur:
            return schema.read_dataset_identity(cur)

    def dinner_credentials(self, body):
        name = body.get('name')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise schema.AccessError('INVALID_INPUT', '내 이름을 선택해 주세요.', 400)
        name = name.strip()
        vault = self.vault()
        identity = self.prepared_identity()
        with self.db() as cur:
            self.h.require_member(cur, name, '회식 응답자')
        # Do not hold the shared DB lock while writing the user's local vault.
        key = vault.dinner_key(identity.dataset_id, name)
        with self.db() as cur:
            if schema.read_dataset_identity(cur).dataset_id != identity.dataset_id:
                raise schema.AccessError('DATASET_CHANGED', '연결 자료가 변경되었습니다. 다시 확인해 주세요.')
        return {'key': key, 'datasetKey': identity.dataset_id,
                'deviceLabel': str(self.h.HOST_LABEL or '내 PC')[:100]}

    def refresh(self, incoming=()):
        vault = self.vault()
        records, unresolved = [], []
        # No local file I/O is performed while the shared DB write lock is held.
        identity = self.prepared_identity()
        saved = vault.list_receipts(identity.dataset_id)
        tokens = {r.receipt_token for r in saved}
        if not isinstance(incoming, list) or len(incoming) > 10000:
            raise schema.AccessError('INVALID_INPUT', '보관할 신청 정보를 확인해 주세요.', 400)
        tokens.update(t for t in incoming if isinstance(t, str) and 20 <= len(t) <= 200)
        pending = vault.list_pending_submissions(identity.dataset_id)
        tokens.update(p.receipt_token for p in pending)
        with self.db(write=True) as cur:
            current = schema.read_dataset_identity(cur)
            if current.dataset_id != identity.dataset_id:
                raise schema.AccessError('DATASET_CHANGED', '연결 자료가 변경되었습니다.')
            for token in tokens:
                row = cur.execute('SELECT * FROM change_requests WHERE receipt_token=?', (token,)).fetchone()
                if row is None:
                    unresolved.append({'code': 'REQUEST_UNRESOLVED'})
                    continue
                records.append(self.record(cur, row, identity.dataset_id))
        vault.merge_receipts(records)
        known = {r.receipt_token: r for r in records}
        for prepared in pending:
            if prepared.receipt_token in known:
                vault.finish_submission(identity.dataset_id, prepared.operation_id, known[prepared.receipt_token])
        return {'receipts': [asdict(r) for r in records], 'unresolved': unresolved,
                'imported': len(records), 'datasetId': identity.dataset_id}

    def local_recovery_rows(self):
        identity = self.prepared_identity()
        data = self.vault().read()
        allowed = {r['recovery_id'] for r in data['recoveries'].values() if r.get('recovery_id')}
        with self.db() as cur:
            rows = cur.execute('SELECT * FROM request_recoveries WHERE dataset_id=? ORDER BY created_at DESC',
                               (identity.dataset_id,)).fetchall()
        return [self.public_row(r) for r in rows if r['id'] in allowed]

    @staticmethod
    def public_row(row):
        names = ('id', 'state', 'requested_name', 'host_label', 'account_label', 'created_at',
                 'expires_at', 'claimed_at', 'claim_deadline', 'completed_at', 'reason', 'version')
        result = {k: row[k] for k in names}
        result['expiresAt'] = result['expires_at']
        now = int(time.time())
        deadline = result['claim_deadline'] if result['state'] == 'CLAIMED' else result['expires_at']
        if result['state'] in ('PENDING', 'APPROVED', 'CLAIMED') and deadline and now >= deadline:
            result['state'] = 'EXPIRED'
        return result

    def create(self, body):
        name = body.get('requestedName', '')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise schema.AccessError('INVALID_INPUT', '신청한 이름을 확인해 주세요.', 400)
        name = name.strip()
        op = operation_id(body.get('operationId'))
        vault = self.vault()
        identity = self.prepared_identity()
        # Reopening a pending recovery uses its stored proof and create operation.
        for previous_op, previous in vault.read()['recoveries'].items():
            if previous['dataset_id'] == identity.dataset_id and previous['requested_name'] == name:
                with self.db() as cur:
                    if previous.get('recovery_id'):
                        row = cur.execute('SELECT * FROM request_recoveries WHERE id=?', (previous['recovery_id'],)).fetchone()
                    else:
                        # DB commit may have succeeded before local binding failed.
                        row = cur.execute("SELECT r.* FROM request_recoveries r JOIN request_recovery_events e ON e.recovery_id=r.id WHERE e.action='CREATE' AND e.operation_id=? AND r.dataset_id=? AND r.device_proof_hash=?",
                            (previous_op, identity.dataset_id, hashlib.sha256(previous['proof'].encode()).hexdigest())).fetchone()
                if row is not None and self.public_row(row)['state'] in ('PENDING', 'APPROVED', 'CLAIMED') and row['generation'] == identity.generation:
                    vault.bind_recovery(previous_op, row['id'])
                    return self.public_row(row)
        proof = vault.prepare_recovery(identity.dataset_id, op, name)
        device_id = vault.device_id
        with self.db(write=True) as cur:
            result = recovery.create_recovery(cur, identity, name, device_id,
                hashlib.sha256(proof.encode()).hexdigest(), op, int(time.time()),
                host_label=self.h.HOST_LABEL, account_label=self.h.ACTOR_LABEL)
        vault.bind_recovery(op, result.id)
        return result.as_dict()

    def claim(self, rid, body):
        vault = self.vault()
        proof = vault.read_recovery_proof(rid)
        proposed = operation_id(body.get('operationId'))
        # Keep claim ID stable even if a page reload generated a new click ID.
        with vault.transaction() as data:
            claim_id = data.setdefault('claims', {}).setdefault(rid, proposed)
        with self.db(write=True) as cur:
            bundle = recovery.claim_recovery(cur, rid, proof, claim_id, int(time.time()))
        receipt_digest = local.persist_claim(vault, bundle)
        with self.db(write=True) as cur:
            result = recovery.complete_recovery(cur, rid, proof, claim_id, receipt_digest, int(time.time()))
        return result.as_dict()

    def submit(self, body):
        action = body.get('action')
        if action not in ('SUBMIT', 'AMEND', 'CANCEL_ABSENCE'):
            raise schema.AccessError('INVALID_INPUT', '지원하지 않는 신청 작업입니다.', 400)
        op = operation_id(body.get('operationId'))
        # Model validation happens before preparing a durable operation.
        if action == 'SUBMIT':
            dto = self.h.ChangeRequestSubmitDTO.model_validate(body.get('request') or {})
            content = dto.model_dump()
        else:
            dto = self.h.RequestManageDTO.model_validate({'receipt_token': body.get('receiptToken'),
                'action': action, 'request': body.get('request') if action == 'AMEND' else None,
                'reason': body.get('reason', '')})
            content = dto.model_dump()
        identity = self.prepared_identity()
        pending = self.vault().prepare_submission(identity.dataset_id, op,
                                                canonical_digest([action, content]), action)
        result = self.h._submit_change_request(dto, prepared=pending) if action == 'SUBMIT' else self.h._manage_change_request(dto, prepared=pending)
        with self.db(write=True) as cur:
            row = cur.execute('SELECT * FROM change_requests WHERE receipt_token=?', (result['receiptToken'],)).fetchone()
            if row is None:
                raise schema.AccessError('REQUEST_UNRESOLVED', '접수한 신청을 다시 확인해 주세요.')
            record = self.record(cur, row, identity.dataset_id)
        self.vault().finish_submission(identity.dataset_id, op, record)
        return dict(result, persistence='saved')


def replay_submission(cur, prepared):
    if prepared is None:
        return None
    identity = schema.read_dataset_identity(cur)
    saved = cur.execute('SELECT * FROM request_access_submissions WHERE dataset_id=? AND operation_id=?',
                        (identity.dataset_id, prepared.operation_id)).fetchone()
    if saved is None:
        return None
    if saved['input_digest'] != prepared.input_digest:
        raise schema.AccessError('OPERATION_CONFLICT', '같은 처리 번호의 내용이 다릅니다.')
    row = cur.execute('SELECT * FROM change_requests WHERE id=? AND request_uid=?',
                      (saved['request_id'], saved['request_uid'])).fetchone()
    if row is None or not hmac.compare_digest(saved['receipt_hash'], hashlib.sha256((row['receipt_token'] or '').encode()).hexdigest()) or row['receipt_token'] != prepared.receipt_token:
        raise schema.AccessError('STALE_SUBMISSION', '복원 후 신청 정보가 달라졌습니다. 새로 확인해 주세요.')
    return {'status': 'success', 'requestId': row['id'], 'receiptToken': row['receipt_token'],
            'requestType': row['request_type'], 'requestStatus': row['status'],
            'requestRevision': row['revision'], 'action': prepared.action,
            'message': '이미 접수된 신청을 확인했습니다.', 'replayed': True}


def register_submission(cur, prepared, request_id):
    uid = schema.ensure_request_uid(cur, request_id)
    if prepared is not None:
        identity = schema.read_dataset_identity(cur)
        cur.execute('INSERT INTO request_access_submissions(dataset_id,operation_id,input_digest,request_uid,request_id,receipt_hash,created_at) VALUES(?,?,?,?,?,?,?)',
                    (identity.dataset_id, prepared.operation_id, prepared.input_digest, uid, request_id,
                     hashlib.sha256(prepared.receipt_token.encode()).hexdigest(), int(time.time())))


def install(app, host):
    if isinstance(host, dict):
        host = HostNamespace(host)
    api = RequestAccessAPI(host)

    @app.exception_handler(schema.AccessError)
    async def access_error(request, error):
        return JSONResponse(error.as_dict(), status_code=error.status,
                            headers={'Cache-Control': 'no-store'})

    @app.middleware('http')
    async def private_responses(request, call_next):
        result = await call_next(request)
        if request.url.path.startswith(('/api/local/request-access', '/api/duty/request-recoveries')):
            result.headers['Cache-Control'] = 'no-store'
            result.headers['Referrer-Policy'] = 'no-referrer'
        return result

    @app.post('/api/local/request-access/session')
    def exchange(request: Request, body: dict):
        origin = f'{request.url.scheme}://{request.headers.get("host", "")}'
        if not host.is_loopback_client(request) or request.headers.get('origin') != origin:
            raise schema.AccessError('FORBIDDEN', 'OnAir에서 연 브라우저를 사용해 주세요.', 403)
        session = local.exchange_launch_ticket(body.get('ticket', ''), origin, int(time.time()), vault=api.vault())
        return {'sessionToken': session.token, 'expiresAt': session.expires_at}

    @app.post('/api/local/request-access/receipts/import')
    def import_receipts(request: Request, body: dict):
        api.authorize(request)
        return api.refresh(body.get('receipts', []))

    @app.post('/api/local/request-access/receipts/read')
    def read_receipts(request: Request):
        api.authorize(request)
        return api.refresh([])

    @app.get('/api/local/request-access/dinner-profile')
    def dinner_profile():
        # A managed profile with a missing/expired session must never silently
        # switch to a new browser credential and lose response ownership.
        return {'managed': bool(host.MANAGED_CONFIG)}

    @app.post('/api/local/request-access/dinner-credentials')
    def dinner_credentials(request: Request, body: dict):
        api.authorize(request)
        return api.dinner_credentials(body)

    @app.get('/api/local/request-access/recoveries')
    def local_recoveries(request: Request):
        api.authorize(request)
        return {'recoveries': api.local_recovery_rows()}

    @app.post('/api/local/request-access/recoveries')
    def create_recovery(request: Request, body: dict):
        api.authorize(request)
        return api.create(body)

    @app.get('/api/local/request-access/recoveries/{rid}')
    def local_recovery(request: Request, rid: str):
        api.authorize(request)
        api.vault().read_recovery_proof(rid)
        with api.db() as cur:
            row = cur.execute('SELECT * FROM request_recoveries WHERE id=?', (rid,)).fetchone()
            if row is None:
                raise schema.AccessError('REQUEST_UNRESOLVED', '복구 요청을 확인할 수 없습니다.')
            return api.public_row(row)

    @app.post('/api/local/request-access/recoveries/{rid}/claim')
    def claim(request: Request, rid: str, body: dict):
        api.authorize(request)
        return api.claim(rid, body)

    @app.post('/api/local/request-access/recoveries/{rid}/cancel')
    def cancel(request: Request, rid: str, body: dict):
        api.authorize(request)
        proof = api.vault().read_recovery_proof(rid)
        with api.db(write=True) as cur:
            return recovery.cancel_recovery(cur, rid, proof, int(time.time())).as_dict()

    @app.post('/api/local/request-access/submit')
    def submit(request: Request, body: dict):
        api.authorize(request)
        from pydantic import ValidationError
        try:
            return api.submit(body)
        except ValidationError:
            raise schema.AccessError('INVALID_INPUT', '신청 내용을 확인해 주세요.', 422) from None

    @app.get('/api/duty/request-recoveries')
    def admin_recoveries(request: Request, x_admin_token: str = Header(default='')):
        api.admin(request, x_admin_token)
        with api.db(write=True) as cur:
            rows = cur.execute('SELECT * FROM request_recoveries ORDER BY created_at DESC LIMIT 500').fetchall()
            return {'recoveries': [api.public_row(r) for r in rows],
                    'notifications': schema.notification_summary(cur, int(time.time()))}

    @app.get('/api/duty/request-recoveries/{rid}/candidates')
    def candidates(request: Request, rid: str, offset: int = 0, limit: int = 50, q: str = '', x_admin_token: str = Header(default='')):
        api.admin(request, x_admin_token)
        with api.db(write=True) as cur:
            scope = recovery.preview_recovery(cur, rid, [])
            grouped = {}
            for item in scope.items:
                group = grouped.setdefault(item['root_uid'], {'count': 0})
                group['count'] += 1
                if item['request_uid'] == item['root_uid']:
                    group.update(item, rootUid=item['root_uid'])
            groups = [g for g in grouped.values() if 'rootUid' in g]
            if q:
                groups = [g for g in groups if q[:200].casefold() in json.dumps(g, ensure_ascii=False).casefold()]
            groups.sort(key=lambda g: g['request_id'], reverse=True)
            start = max(0, offset)
            return {'groups': groups[start:start + max(1, min(100, limit))], 'total': len(groups), 'blockers': scope.blockers}

    @app.post('/api/duty/request-recoveries/{rid}/preview')
    def preview(request: Request, rid: str, body: dict, x_admin_token: str = Header(default='')):
        api.admin(request, x_admin_token)
        with api.db(write=True) as cur:
            return recovery.preview_recovery(cur, rid, body.get('rootUids', [])).as_dict()

    @app.post('/api/duty/request-recoveries/{rid}/decision')
    def decide(request: Request, rid: str, body: dict, x_admin_token: str = Header(default='')):
        api.admin(request, x_admin_token)
        reason = body.get('reason') or ''
        if not isinstance(reason, str) or len(reason) > 2000:
            raise schema.AccessError('INVALID_INPUT', '사유는 2000자 이내로 입력해 주세요.', 400)
        roots = body.get('rootUids', [])
        if not isinstance(roots, list) or len(roots) > 1000 or not all(isinstance(r, str) for r in roots):
            raise schema.AccessError('INVALID_INPUT', '선택한 신청을 확인해 주세요.', 400)
        with api.db(write=True) as cur:
            result = recovery.decide_recovery(cur, rid, body.get('action'), roots,
                body.get('expectedDigest'), operation_id(body.get('operationId')), reason, int(time.time()),
                actor_label=host.ACTOR_LABEL, host_label=host.HOST_LABEL)
        return result.as_dict()

    return api
