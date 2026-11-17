"""Additive request ownership schema. Call only inside a writable transaction."""
from dataclasses import dataclass, asdict
import uuid

class AccessError(Exception):
    def __init__(self, code, detail, status=409, retryable=False):
        self.code, self.detail, self.status, self.retryable = code, detail, status, retryable
        super().__init__(f'{code}: {detail}')
    def as_dict(self):
        return dict(code=self.code, detail=self.detail, retryable=self.retryable)

@dataclass(frozen=True)
class DatasetIdentity:
    dataset_id: str
    generation: str
    def as_dict(self): return asdict(self)

PROTECTED_TABLES = ('request_recoveries','request_recovery_items','request_recovery_events','request_access_submissions')
IDENTITY_KEYS = ('request_access_dataset_id', 'request_access_generation')


def _has_identity_value(value):
    return isinstance(value, str) and bool(value.strip())

def register_connection(conn):
    conn.create_function('wlr_request_access_contract', 0, lambda: 1)


def read_dataset_identity(cur):
    values=dict(cur.execute("SELECT key,value FROM app_settings WHERE key IN ('request_access_dataset_id','request_access_generation')").fetchall())
    if not all(_has_identity_value(values.get(k)) for k in IDENTITY_KEYS):
        raise AccessError('DATASET_CHANGED','자료 식별정보를 확인할 수 없습니다.')
    return DatasetIdentity(values['request_access_dataset_id'],values['request_access_generation'])


def notification_summary(cur, now):
    """Read-only admin badge data; never expose names, reasons or capabilities."""
    if not cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='request_recoveries'").fetchone():
        return None
    identity = read_dataset_identity(cur)
    rows = cur.execute("SELECT id FROM request_recoveries WHERE dataset_id=? AND generation=? "
                       "AND state='PENDING' AND expires_at>? ORDER BY id",
                       (identity.dataset_id, identity.generation, now)).fetchall()
    return {'datasetKey': identity.dataset_id + ':' + identity.generation,
            'pendingIds': [row[0] for row in rows]}


def ensure_request_uid(cur, request_id):
    row=cur.execute('SELECT request_uid FROM change_requests WHERE id=?',(request_id,)).fetchone()
    if row is None: raise AccessError('REQUEST_UNRESOLVED','신청을 확인할 수 없습니다.')
    if not row[0]:
        cur.execute('UPDATE change_requests SET request_uid=? WHERE id=? AND (request_uid IS NULL OR request_uid=\'\')',(str(uuid.uuid4()),request_id))
    return cur.execute('SELECT request_uid FROM change_requests WHERE id=?',(request_id,)).fetchone()[0]


def ensure_request_access_schema(cur):
    values = dict(cur.execute("SELECT key,value FROM app_settings WHERE key IN ('request_access_dataset_id','request_access_generation')").fetchall())
    missing = [key for key in IDENTITY_KEYS if not _has_identity_value(values.get(key))]
    if missing:
        tables = {row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if any(cur.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone()
               for table in PROTECTED_TABLES if table in tables):
            # Never change the dataset of an existing authorization history.
            raise AccessError('DATASET_CHANGED', '복구 이력이 있는 자료의 식별정보가 누락되었습니다. 관리자에게 자료 확인을 요청해 주세요.')
        for key in missing:
            cur.execute('INSERT INTO app_settings(key,value) VALUES(?,?) '
                        'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                        (key, str(uuid.uuid4())))
    cols={r[1] for r in cur.execute('PRAGMA table_info(change_requests)')}
    if 'request_uid' not in cols: cur.execute('ALTER TABLE change_requests ADD COLUMN request_uid TEXT')
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_change_requests_uid ON change_requests(request_uid) WHERE request_uid IS NOT NULL AND request_uid<>''")
    for row in cur.execute("SELECT id FROM change_requests WHERE request_uid IS NULL OR request_uid=''").fetchall(): ensure_request_uid(cur,row[0])
    definitions={
      'request_recoveries': '''id TEXT PRIMARY KEY,dataset_id TEXT NOT NULL,generation TEXT NOT NULL,requested_name TEXT NOT NULL,device_id TEXT NOT NULL,device_proof_hash TEXT NOT NULL,host_label TEXT DEFAULT '',account_label TEXT DEFAULT '',state TEXT NOT NULL,created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL,review_digest TEXT,claim_id TEXT,claimed_at INTEGER,claim_deadline INTEGER,completed_at INTEGER,version INTEGER NOT NULL DEFAULT 1,reason TEXT DEFAULT '',receipt_digest TEXT''',
      'request_recovery_items': '''recovery_id TEXT NOT NULL,request_uid TEXT NOT NULL,request_id INTEGER NOT NULL,identity_digest TEXT NOT NULL,root_uid TEXT NOT NULL,UNIQUE(recovery_id,request_uid)''',
      'request_recovery_events': '''event_id TEXT PRIMARY KEY,recovery_id TEXT NOT NULL,action TEXT NOT NULL,actor_label TEXT DEFAULT '',host_label TEXT DEFAULT '',at INTEGER NOT NULL,reason TEXT DEFAULT '',operation_id TEXT,input_digest TEXT,safe_result_json TEXT,UNIQUE(recovery_id,operation_id)''',
      'request_access_submissions': '''dataset_id TEXT NOT NULL,operation_id TEXT NOT NULL,input_digest TEXT NOT NULL,request_uid TEXT NOT NULL,request_id INTEGER NOT NULL,receipt_hash TEXT NOT NULL,created_at INTEGER NOT NULL,UNIQUE(dataset_id,operation_id)'''}
    for table,definition in definitions.items():
        cur.execute(f'CREATE TABLE IF NOT EXISTS {table} ({definition})')
        for action in ('INSERT','UPDATE','DELETE'):
            cur.execute(f"CREATE TRIGGER IF NOT EXISTS guard_{table}_{action.lower()} BEFORE {action} ON {table} WHEN wlr_request_access_contract() IS NOT 1 BEGIN SELECT RAISE(ABORT,'request access write contract required'); END")


def after_access_restore(cur):
    cur.execute("UPDATE app_settings SET value=? WHERE key='request_access_generation'",(str(uuid.uuid4()),))
    cur.execute("UPDATE request_recoveries SET state='INVALIDATED',version=version+1,reason='자료가 복원되어 다시 확인해야 합니다.' WHERE state IN ('PENDING','APPROVED','CLAIMED')")
    for row in cur.execute("SELECT id FROM change_requests WHERE request_uid IS NULL OR request_uid=''").fetchall(): ensure_request_uid(cur,row[0])
