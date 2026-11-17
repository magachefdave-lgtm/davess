import sqlite3
import pytest
from backend.request_access_schema import *


def database():
    c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row
    c.execute('CREATE TABLE app_settings(key TEXT PRIMARY KEY,value TEXT)')
    c.execute('CREATE TABLE change_requests(id INTEGER PRIMARY KEY, requester_name TEXT,request_type TEXT,status TEXT,receipt_token TEXT,amends_request_id INTEGER,target_date TEXT)')
    c.execute("INSERT INTO change_requests VALUES(1,'Kim','ABSENCE','APPROVED','old',NULL,'2027-01-01')")
    register_connection(c); ensure_request_access_schema(c.cursor()); return c


def test_migration_preserves_requests_and_history():
    c=database(); row=dict(c.execute('SELECT * FROM change_requests').fetchone())
    assert row.pop('request_uid'); assert row['receipt_token']=='old'; assert row['status']=='APPROVED'
    first=read_dataset_identity(c.cursor()); ensure_request_access_schema(c.cursor()); assert read_dataset_identity(c.cursor())==first


def test_old_viewer_insert_gets_uid_lazily():
    c=database(); c.execute("INSERT INTO change_requests(id) VALUES(2)")
    assert c.execute('SELECT request_uid FROM change_requests WHERE id=2').fetchone()[0] is None
    assert ensure_request_uid(c.cursor(),2)==ensure_request_uid(c.cursor(),2)


def test_old_admin_restore_rolls_back_protected_tables(tmp_path):
    p=tmp_path/'db'; c=database(); c.commit(); d=sqlite3.connect(p); c.backup(d); d.close()
    d=sqlite3.connect(p)
    with pytest.raises(sqlite3.OperationalError): d.execute("DELETE FROM request_recoveries")
