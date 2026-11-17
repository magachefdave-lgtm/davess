from types import SimpleNamespace
import pytest
from backend.request_access_store import ReceiptVault
from backend.request_access_local import configure_vault,issue_launch_ticket,exchange_launch_ticket,require_local_session

def req(origin='http://127.0.0.1:8000',host='127.0.0.1'):
    return SimpleNamespace(client=SimpleNamespace(host=host),headers={'origin':origin,'host':'127.0.0.1:8000'},url=SimpleNamespace(scheme='http'),method='POST')

def test_ticket_is_single_use_and_expires_after_90_seconds(tmp_path):
    configure_vault(ReceiptVault(tmp_path))
    t=issue_launch_ticket('http://127.0.0.1:8000',10)
    exchange_launch_ticket(t,'http://127.0.0.1:8000',11)
    with pytest.raises(Exception):exchange_launch_ticket(t,'http://127.0.0.1:8000',11)
    t=issue_launch_ticket('http://127.0.0.1:8000',10)
    with pytest.raises(Exception):exchange_launch_ticket(t,'http://127.0.0.1:8000',100)

def test_session_survives_restart_until_8_hours(tmp_path):
    configure_vault(ReceiptVault(tmp_path));s=exchange_launch_ticket(issue_launch_ticket('http://127.0.0.1:8000',10),'http://127.0.0.1:8000',11)
    configure_vault(ReceiptVault(tmp_path))
    assert require_local_session(req(),s.token,now=12).session_id==s.session_id
    with pytest.raises(Exception):require_local_session(req(),s.token,now=11+28800)

def test_localhost_without_launch_session_cannot_read_vault(tmp_path):
    configure_vault(ReceiptVault(tmp_path))
    with pytest.raises(Exception):require_local_session(req(),'missing',now=1)

def test_wrong_origin_and_remote_are_denied(tmp_path):
    configure_vault(ReceiptVault(tmp_path));s=exchange_launch_ticket(issue_launch_ticket('http://127.0.0.1:8000',10),'http://127.0.0.1:8000',11)
    for r in [req('http://evil.test'),req(host='192.168.0.2'),req(origin='')]:
        with pytest.raises(Exception):require_local_session(r,s.token,now=12)

def test_diagnostics_and_access_logs_redact_credentials():
    from backend.request_access_local import redact_access_text
    value=redact_access_text('GET /api/duty/request-status/SECRET HTTP/1.1 ticket=SECRET sessionToken=TOKEN receipt_token=RECEIPT')
    assert 'SECRET' not in value and 'TOKEN' not in value and 'RECEIPT' not in value
