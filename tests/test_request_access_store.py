from concurrent.futures import ThreadPoolExecutor
import os
import pytest
from backend.request_access_store import ReceiptVault, ReceiptRecord

def row(i):
    return ReceiptRecord('ds', f'uid{i}', i, f'secret{i}', 'PENDING')

def test_more_than_100_receipts_retained(tmp_path):
    v=ReceiptVault(tmp_path)
    v.merge_receipts([row(i) for i in range(150)])
    assert len(ReceiptVault(tmp_path).list_receipts('ds'))==150

def test_two_writers_merge_without_lost_receipt(tmp_path):
    def write(i): ReceiptVault(tmp_path).merge_receipts([row(i)])
    with ThreadPoolExecutor(max_workers=8) as pool: list(pool.map(write,range(40)))
    assert len(ReceiptVault(tmp_path).list_receipts('ds'))==40

def test_atomic_write_failure_keeps_prior_vault(tmp_path,monkeypatch):
    v=ReceiptVault(tmp_path);v.merge_receipts([row(1)])
    before=v.path.read_bytes()
    monkeypatch.setattr(os,'replace',lambda *a: (_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(Exception):v.merge_receipts([row(2)])
    assert v.path.read_bytes()==before

def test_pending_and_recovery_survive_restart(tmp_path):
    v=ReceiptVault(tmp_path)
    p=v.prepare_submission('ds','op','digest','SUBMIT')
    assert ReceiptVault(tmp_path).prepare_submission('ds','op','digest','SUBMIT')==p
    with pytest.raises(Exception):v.prepare_submission('ds','op','other','SUBMIT')
    proof=v.prepare_recovery('ds','recop','Name');v.bind_recovery('recop','rid')
    assert ReceiptVault(tmp_path).read_recovery_proof('rid')==proof
    with pytest.raises(Exception):v.prepare_recovery('ds','recop','Other')

def test_corrupt_and_symlink_rejected(tmp_path):
    v=ReceiptVault(tmp_path);v.merge_receipts([row(1)]);v.path.write_bytes(b'plaintext')
    with pytest.raises(Exception):v.list_receipts('ds')
    link=tmp_path/'link';link.symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(Exception):ReceiptVault(link)

def test_finished_submission_retry_retains_original_receipt(tmp_path):
    v=ReceiptVault(tmp_path);p=v.prepare_submission('ds','op','digest','SUBMIT')
    r=ReceiptRecord('ds','u',1,p.receipt_token,'PENDING')
    v.finish_submission('ds','op',r)
    assert v.prepare_submission('ds','op','digest','SUBMIT')==p
    v.finish_submission('ds','op',r)
    assert v.list_pending_submissions('ds')==[]

def test_relative_profile_rejected():
    with pytest.raises(Exception):ReceiptVault('relative-profile')
