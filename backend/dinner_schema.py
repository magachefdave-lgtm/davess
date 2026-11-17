"""Additive dinner storage. Call migrations under the shared SQLite write lock."""
from .request_access_schema import AccessError

TABLES = ('dinner_roles', 'dinner_appointments', 'dinner_connections', 'dinner_polls', 'dinner_responses',
          'dinner_operations', 'dinner_events', 'dinner_audit')
# Restore content, but retain current authority/audit and invalidate its connections.
RESTORE_SKIP_TABLES = ('dinner_roles', 'dinner_appointments', 'dinner_connections', 'dinner_operations', 'dinner_audit')


def ensure_schema(cur):
    definitions = {
        'dinner_roles': '''id INTEGER PRIMARY KEY CHECK(id=1), member_name TEXT NOT NULL,
            start_date TEXT NOT NULL,end_date TEXT NOT NULL,revision INTEGER NOT NULL''',
        'dinner_appointments': '''id INTEGER PRIMARY KEY CHECK(id BETWEEN 1900 AND 9999),
            member_name TEXT NOT NULL,start_date TEXT NOT NULL,end_date TEXT NOT NULL,
            revision INTEGER NOT NULL''',
        'dinner_connections': '''id TEXT PRIMARY KEY,key_hash TEXT NOT NULL,member_name TEXT NOT NULL,
            role_revision INTEGER NOT NULL,dataset_id TEXT NOT NULL,generation TEXT NOT NULL,
            status TEXT NOT NULL,verification_code TEXT NOT NULL,device_label TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,decided_at TEXT''',
        'dinner_polls': '''id TEXT PRIMARY KEY,data_json TEXT NOT NULL,created_at TEXT NOT NULL''',
        'dinner_responses': '''poll_id TEXT NOT NULL,member_name TEXT NOT NULL,key_hash TEXT NOT NULL,
            answers_json TEXT NOT NULL,suggestion TEXT NOT NULL,updated_at TEXT NOT NULL,
            PRIMARY KEY(poll_id,member_name)''',
        'dinner_operations': '''scope TEXT NOT NULL,operation_id TEXT NOT NULL,input_digest TEXT NOT NULL,
            result_json TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(scope,operation_id)''',
        'dinner_events': '''id TEXT PRIMARY KEY,poll_id TEXT NOT NULL,member_name TEXT NOT NULL,
            event_type TEXT NOT NULL,message TEXT NOT NULL,created_at TEXT NOT NULL''',
        'dinner_audit': '''id TEXT PRIMARY KEY,action TEXT NOT NULL,subject_id TEXT NOT NULL,
            actor TEXT NOT NULL,created_at TEXT NOT NULL''',
    }
    for table, definition in definitions.items():
        cur.execute(f'CREATE TABLE IF NOT EXISTS {table} ({definition})')
    columns = {r[1] for r in cur.execute('PRAGMA table_info(dinner_connections)')}
    if 'role_id' not in columns:
        cur.execute('ALTER TABLE dinner_connections ADD COLUMN role_id INTEGER NOT NULL DEFAULT 1')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_dinner_connection_key ON dinner_connections(key_hash,created_at)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_dinner_event_member ON dinner_events(member_name,created_at)')


def after_restore(cur):
    """A restored DB must never reactivate a previously approved connection."""
    ensure_schema(cur)
    cur.execute("UPDATE dinner_connections SET status='INVALIDATED',reason='자료 복원 후 사용 연결을 다시 승인해 주세요.'")
    # A prior successful operation result must not replay against restored polls.
    cur.execute('DELETE FROM dinner_operations')


def after_members_changed(cur):
    """Removing and later readding a name must not resurrect its approved PC."""
    if cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dinner_connections'").fetchone():
        cur.execute("""UPDATE dinner_connections SET status='INVALIDATED',reason='구성원 명단에서 제외되었습니다.'
                       WHERE member_name NOT IN (SELECT name FROM members)
                       AND status IN ('PENDING','APPROVED')""")
