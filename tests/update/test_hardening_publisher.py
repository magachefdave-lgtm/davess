"""게시 PC 보강(E-1/5/6/7/8/9) 및 core 메시지 시험. [위험] 주석은 막으려는 실제 사고다."""
import base64
import json
import os
from pathlib import Path
import shutil
import zipfile

import pytest

from updater import core, launcher, publisher
from updater.core import UpdateError, load_json
from test_publisher import release_zip

PASS = 'correct horse battery staple'


@pytest.fixture
def pub(tmp_path):
    repo = tmp_path / 'repo'
    key = tmp_path / 'private' / 'signing_key.json'
    public = publisher.initialize_repository(repo, key)
    return {'repo': repo, 'key': key, 'public': public, 'tmp': tmp_path}


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(root.rglob('*'))}


# ------------------------------------------------------------------ E-7 지문

# [위험] 지문 형식/계산이 바뀌면 관리자가 기존 설치 ZIP 의 지문과 눈으로 대조할 수 없다.
def test_fingerprint_format_is_stable():
    key = base64.b64encode(bytes(range(32))).decode()
    import hashlib
    expected = hashlib.sha256(bytes(range(32))).hexdigest()[:16].upper()
    assert core.public_key_fingerprint(key) == '-'.join(expected[i:i + 4] for i in range(0, 16, 4))
    assert len(core.public_key_fingerprint(key)) == 19
    with pytest.raises(UpdateError):
        core.public_key_fingerprint('not-base64')


# [위험] 설치 ZIP README 에 지문이 없으면 사용자가 공개키가 맞는지 대조할 수 없다.
def test_installer_readme_and_publish_result_show_fingerprint(pub):
    out = pub['tmp'] / 'out'
    result = publisher.publish_release(pub['repo'], release_zip(pub['tmp']), pub['key'], installer_output=out)
    fingerprint = core.public_key_fingerprint(pub['public'])
    assert result['fingerprint'] == fingerprint
    with zipfile.ZipFile(result['installers']['VIEWER']) as archive:
        assert fingerprint in archive.read('README.txt').decode('utf-8-sig')
        assert 'private_key' not in archive.read('onair_update.json').decode()


# ------------------------------------------------------------------ E-1 키 백업·복구

def backup(pub, name='backup.json', passphrase=PASS):
    return publisher.export_key_backup(pub['tmp'] / 'usb' / name, passphrase, pub['key'])


# [위험] 키 백업을 복구했더니 다른 키가 되어 기존 저장소·설치본 전부와 불일치한다.
def test_backup_roundtrip_restores_same_key(pub):
    result = backup(pub)
    original = load_json(pub['key'])
    pub['key'].unlink()
    restored = publisher.import_key_backup(result['path'], PASS, pub['key'], pub['repo'])
    assert restored['public_key'] == pub['public']
    assert load_json(pub['key'])['public_key'] == original['public_key']
    # 복구한 키로 기존 저장소에 계속 게시할 수 있다
    publisher.publish_release(pub['repo'], release_zip(pub['tmp']), pub['key'])


# [위험] 백업 파일에 개인키가 평문(또는 base64)으로 들어가 USB 분실이 곧 키 유출이 된다.
def test_backup_file_contains_no_plain_private_key(pub):
    result = backup(pub)
    text = Path(result['path']).read_text(encoding='utf-8')
    private = publisher._read_private_key(pub['key']).private_bytes(
        publisher.serialization.Encoding.Raw, publisher.serialization.PrivateFormat.Raw,
        publisher.serialization.NoEncryption())
    assert base64.b64encode(private).decode() not in text and private.hex() not in text
    document = json.loads(text)
    assert document['public_key'] == pub['public']                            # 공개키·지문은 평문
    assert document['fingerprint'] == core.public_key_fingerprint(pub['public'])
    assert document['kdf'] == 'scrypt' and document['kdf_params'] == {'n': 32768, 'r': 8, 'p': 1}
    assert set(document) >= {'schema', 'salt', 'nonce', 'ciphertext', 'created'}


# [위험] 짧은 암호로 백업하면 USB 를 주운 사람이 암호를 맞춰 볼 수 있다.
@pytest.mark.parametrize('weak', ['', 'short', '12345678901'])
def test_short_passphrase_rejected_for_export_and_import(pub, weak):
    with pytest.raises(UpdateError, match='12자'):
        publisher.export_key_backup(pub['tmp'] / 'b.json', weak, pub['key'])
    result = backup(pub)
    pub['key'].unlink()
    with pytest.raises(UpdateError, match='12자'):
        publisher.import_key_backup(result['path'], weak, pub['key'])
    assert not pub['key'].exists()


# [위험] 틀린 암호인데 성공하거나, 원인을 알 수 없는 영어 예외가 나온다. 암호가 메시지에 노출된다.
def test_wrong_passphrase_has_specific_message_and_no_secret_leak(pub):
    result = backup(pub)
    pub['key'].unlink()
    with pytest.raises(UpdateError) as caught:
        publisher.import_key_backup(result['path'], 'wrong passphrase 123', pub['key'])
    assert '암호가 틀렸' in str(caught.value) and 'wrong passphrase' not in str(caught.value)
    assert PASS not in str(caught.value) and not pub['key'].exists()


# [위험] 손상/변조된 백업이 복구 성공으로 처리되거나(공개키만 바꿔치기 포함) 키 파일이 반쯤 써진다.
@pytest.mark.parametrize('damage', ['truncate', 'not_json', 'flip_cipher', 'swap_public', 'extra_field', 'bad_kdf'])
def test_damaged_backup_is_rejected_without_writing_key(pub, damage):
    result = backup(pub)
    path = Path(result['path'])
    document = json.loads(path.read_text(encoding='utf-8'))
    other = base64.b64encode(os.urandom(32)).decode()
    if damage == 'truncate':
        path.write_bytes(path.read_bytes()[:40])
    elif damage == 'not_json':
        path.write_bytes(b'hello')
    elif damage == 'flip_cipher':
        raw = bytearray(base64.b64decode(document['ciphertext']))
        raw[0] ^= 1
        document['ciphertext'] = base64.b64encode(bytes(raw)).decode()
        path.write_text(json.dumps(document))
    elif damage == 'swap_public':
        document['public_key'] = other
        document['fingerprint'] = core.public_key_fingerprint(other)
        path.write_text(json.dumps(document))
    elif damage == 'extra_field':
        document['x'] = 1
        path.write_text(json.dumps(document))
    else:
        document['kdf_params'] = {'n': 2 ** 30, 'r': 8, 'p': 1}
        path.write_text(json.dumps(document))
    pub['key'].unlink()
    with pytest.raises(UpdateError):
        publisher.import_key_backup(path, PASS, pub['key'])
    assert not pub['key'].exists()


# [위험] 다른 저장소(또는 오래된 키)의 백업을 복구해 저장소와 키가 어긋난다.
def test_import_requires_match_with_existing_repository_pin(pub):
    other = pub['tmp'] / 'other' / 'signing_key.json'
    publisher.initialize_repository(pub['tmp'] / 'repo2', other)
    result = publisher.export_key_backup(pub['tmp'] / 'usb' / 'other.json', PASS, other)
    pub['key'].unlink()
    before = snapshot(pub['repo'])
    with pytest.raises(UpdateError, match='저장소의 공개 키'):
        publisher.import_key_backup(result['path'], PASS, pub['key'], pub['repo'])
    assert not pub['key'].exists() and snapshot(pub['repo']) == before


# [위험] 복구가 이 PC 의 기존 개인키를 덮어써 현재 키를 영구히 잃는다.
def test_import_never_overwrites_existing_local_key_without_explicit_confirmation(pub):
    result = backup(pub)
    before = pub['key'].read_bytes()
    with pytest.raises(UpdateError, match='이미'):
        publisher.import_key_backup(result['path'], PASS, pub['key'])
    assert pub['key'].read_bytes() == before
    other = pub['tmp'] / 'o' / 'signing_key.json'
    publisher.initialize_repository(pub['tmp'] / 'r3', other)
    backup_other = publisher.export_key_backup(pub['tmp'] / 'usb' / 'x.json', PASS, other)
    with pytest.raises(UpdateError, match='덮어쓰지 않았습니다'):
        publisher.import_key_backup(backup_other['path'], PASS, pub['key'])
    assert pub['key'].read_bytes() == before
    done = publisher.import_key_backup(backup_other['path'], PASS, pub['key'], replace_existing=True)
    saved = list(pub['key'].parent.glob('signing_key.json.replaced-*'))
    assert len(saved) == 1 and saved[0].read_bytes() == before     # 기존 키는 이름만 바꿔 보관
    assert done['replaced_backup'] == str(saved[0])


# [위험] 기존 백업 파일을 덮어쓰거나 키와 같은 폴더(PC 고장 시 같이 소실)에 백업한다.
def test_export_never_overwrites_file_or_writes_next_to_key(pub):
    result = backup(pub)
    before = Path(result['path']).read_bytes()
    with pytest.raises(UpdateError, match='이미 있습니다'):
        publisher.export_key_backup(result['path'], PASS, pub['key'])
    assert Path(result['path']).read_bytes() == before
    with pytest.raises(UpdateError, match='개인 키가 있는 폴더'):
        publisher.export_key_backup(pub['key'].parent / 'b.json', PASS, pub['key'])


# [위험] 백업 표식에 키 자료가 들어가거나, 백업을 안 했는데 했다고 표시된다.
def test_backup_status_and_marker_hold_no_key_material(pub):
    status = publisher.key_backup_status(pub['key'])
    assert status['key_exists'] and not status['backed_up'] and '아직 만들지 않았습니다' in status['warning']
    result = backup(pub)
    status = publisher.key_backup_status(pub['key'])
    assert status['backed_up'] and status['warning'] is None and status['fingerprint'] == result['fingerprint']
    mark = load_json(pub['key'].parent / publisher.KEY_BACKUP_MARK)
    assert set(mark) == {'schema', 'fingerprint', 'created'}


# [위험] 키를 교체했는데 예전 백업 표식 때문에 '백업함' 으로 보인다.
def test_backup_mark_for_other_key_still_warns(pub):
    backup(pub)
    other = pub['tmp'] / 'o2' / 'signing_key.json'
    publisher.initialize_repository(pub['tmp'] / 'r4', other)
    shutil.copy(pub['key'].parent / publisher.KEY_BACKUP_MARK, other.parent / publisher.KEY_BACKUP_MARK)
    assert publisher.key_backup_status(other)['warning']


def test_backup_status_without_key(tmp_path):
    status = publisher.key_backup_status(tmp_path / 'none' / 'signing_key.json')
    assert status['key_exists'] is False and status['warning'] is None


# ------------------------------------------------------------------ E-6 읽기 전용 저장소 상태

# [위험] '상태 보기' 만 눌렀는데 저장소/폴더/잠금 파일/키가 만들어진다.
def test_repository_status_is_read_only_for_missing_empty_and_existing(pub):
    missing = pub['tmp'] / 'nothing' / 'here'
    empty = pub['tmp'] / 'empty'
    empty.mkdir()
    fresh_key = pub['tmp'] / 'fresh' / 'signing_key.json'
    before_tmp = snapshot(pub['tmp'])
    status = publisher.repository_status(missing, fresh_key)
    assert status['exists'] is False and status['new_repository'] is True
    status = publisher.repository_status(empty, fresh_key)
    assert status['empty'] is True and status['new_repository'] is True
    assert not fresh_key.parent.exists() and not missing.parent.exists()
    assert snapshot(pub['tmp']) == before_tmp
    publisher.publish_release(pub['repo'], release_zip(pub['tmp']), pub['key'])
    before = snapshot(pub['repo'])
    status = publisher.repository_status(pub['repo'], pub['key'])
    assert snapshot(pub['repo']) == before
    assert status['initialized'] and not status['new_repository']


# [위험] 확인 화면이 현재 버전·sequence·지문을 안 보여 주면 잘못된 저장소에 게시해도 모른다.
def test_repository_status_reports_roles_sequence_and_fingerprint(pub):
    publisher.publish_release(pub['repo'], release_zip(pub['tmp'], '3.15.0'), pub['key'], roles=('ADMIN', 'VIEWER'))
    publisher.publish_release(pub['repo'], release_zip(pub['tmp'], '3.15.1'), pub['key'], roles=('ADMIN',))
    (pub['repo'] / 'channels' / 'VIEWER.paused').write_text('x')
    status = publisher.repository_status(pub['repo'], pub['key'])
    assert status['roles']['ADMIN']['version'] == '3.15.1' and status['roles']['ADMIN']['sequence'] == 2
    assert status['roles']['VIEWER']['version'] == '3.15.0' and status['roles']['VIEWER']['sequence'] == 1
    assert status['roles']['VIEWER']['paused'] and not status['roles']['ADMIN']['paused']
    assert status['fingerprint'] == core.public_key_fingerprint(pub['public'])
    assert status['warnings'] == []


# [위험] 서명이 틀린(변조된) 채널을 정상 버전처럼 보여 준다.
def test_repository_status_flags_unverifiable_channel_and_foreign_key(pub):
    publisher.publish_release(pub['repo'], release_zip(pub['tmp']), pub['key'])
    channel = pub['repo'] / 'channels' / 'ADMIN.json'
    document = json.loads(channel.read_text())
    document['payload']['version'] = '9.9.9'
    channel.write_text(json.dumps(document))
    status = publisher.repository_status(pub['repo'], pub['key'])
    assert status['roles']['ADMIN']['error'] and status['roles']['ADMIN']['version'] is None
    other = pub['tmp'] / 'o' / 'signing_key.json'
    publisher.initialize_repository(pub['tmp'] / 'r5', other)
    assert any('다릅니다' in w for w in publisher.repository_status(pub['repo'], other)['warnings'])
    assert any('서명 키가 없습니다' in w for w in
               publisher.repository_status(pub['repo'], pub['tmp'] / 'zz' / 'signing_key.json')['warnings'])


# [위험] 마지막으로 쓴 경로 기억이 깨진 파일 때문에 게시 화면을 못 열게 한다.
def test_last_paths_roundtrip_and_garbage(pub):
    publisher.save_last_paths({'repository': r'\\pc\share', 'release': 'C:/a.zip', 'junk': 'x'}, pub['key'])
    assert publisher.load_last_paths(pub['key']) == {'repository': r'\\pc\share', 'release': 'C:/a.zip'}
    (pub['key'].parent / publisher.LAST_PATHS).write_bytes(b'\xff\x00garbage')
    assert publisher.load_last_paths(pub['key']) == {}


# ------------------------------------------------------------------ E-5 sequence 단조 가드

def sequences(repo):
    return {role: load_json(repo / 'channels' / f'{role}.json')['payload']['sequence'] for role in ('ADMIN', 'VIEWER')}


# [위험] 저장소를 옛 백업으로 되돌린 채 게시하면 sequence 가 낮아져 직원 PC 가 업데이트를 거부한다.
def test_restored_repository_stops_publication_until_forced(pub):
    publisher.publish_release(pub['repo'], release_zip(pub['tmp'], '3.15.0'), pub['key'])
    snapshot_dir = pub['tmp'] / 'old-backup'
    shutil.copytree(pub['repo'], snapshot_dir)
    publisher.publish_release(pub['repo'], release_zip(pub['tmp'], '3.15.1'), pub['key'])
    publisher.publish_release(pub['repo'], release_zip(pub['tmp'], '3.15.2'), pub['key'])
    assert sequences(pub['repo']) == {'ADMIN': 3, 'VIEWER': 3}
    shutil.rmtree(pub['repo'])
    shutil.copytree(snapshot_dir, pub['repo'])                      # 저장소 복원(옛 상태)
    before = snapshot(pub['repo'])
    with pytest.raises(UpdateError, match='복원되었거나 지워진'):
        publisher.publish_release(pub['repo'], release_zip(pub['tmp'], '3.15.3'), pub['key'])
    assert snapshot(pub['repo']) == before                          # 아무것도 게시되지 않음
    result = publisher.publish_release(pub['repo'], release_zip(pub['tmp'], '3.15.3'), pub['key'],
                                       force_sequence_reset=True)
    assert result['channels']['ADMIN']['sequence'] == 4             # 기록(3) 다음부터 이어짐 -> 클라이언트가 받는다
    client = core.verify_envelope(load_json(pub['repo'] / 'channels' / 'ADMIN.json'), pub['public'], 'ADMIN')
    assert client['sequence'] > 3


# [위험] 강제 게시 뒤 로컬 기록이 낮아져(복원된 저장소의 낮은 번호로) 가드가 다음번에 무력화된다.
def test_local_record_never_decreases_even_when_forced_with_restored_repository(pub):
    publisher.publish_release(pub['repo'], release_zip(pub['tmp'], '3.15.0'), pub['key'])
    saved = pub['tmp'] / 'saved'
    shutil.copytree(pub['repo'], saved)
    publisher.publish_release(pub['repo'], release_zip(pub['tmp'], '3.15.1'), pub['key'])
    publisher.publish_release(pub['repo'], release_zip(pub['tmp'], '3.15.2'), pub['key'])
    shutil.rmtree(pub['repo'])
    shutil.copytree(saved, pub['repo'])
    publisher.publish_release(pub['repo'], release_zip(pub['tmp'], '3.15.3'), pub['key'],
                              roles=('ADMIN',), force_sequence_reset=True)
    assert load_json(pub['key'].parent / publisher.LAST_PUBLISHED)['roles'] == {'ADMIN': 4, 'VIEWER': 3}


# [위험] 저장소가 통째로 지워져도(빈 폴더) 알아채지 못하고 sequence 1 부터 다시 게시한다.
def test_wiped_repository_is_detected(pub):
    publisher.publish_release(pub['repo'], release_zip(pub['tmp']), pub['key'])
    shutil.rmtree(pub['repo'])
    with pytest.raises(UpdateError, match='복원되었거나 지워진'):
        publisher.publish_release(pub['repo'], release_zip(pub['tmp'], '3.15.1'), pub['key'])


# [위험] 정상 게시 흐름(연속 게시)까지 가드가 막는다 / 기록이 갱신되지 않는다.
def test_normal_publications_update_local_record_and_are_not_blocked(pub):
    for version in ('3.15.0', '3.15.1', '3.15.2'):
        publisher.publish_release(pub['repo'], release_zip(pub['tmp'], version), pub['key'])
    record = load_json(pub['key'].parent / publisher.LAST_PUBLISHED)
    assert record['roles'] == {'ADMIN': 3, 'VIEWER': 3}


# [위험] 게시가 실패했는데 로컬 기록이 먼저 올라가 다음 정상 게시가 영구히 막힌다.
def test_failed_publication_does_not_advance_local_record(pub):
    publisher.publish_release(pub['repo'], release_zip(pub['tmp']), pub['key'])
    with pytest.raises(UpdateError):
        publisher.publish_release(pub['repo'], release_zip(pub['tmp'], '3.14.0'), pub['key'])   # 버전 하향
    assert load_json(pub['key'].parent / publisher.LAST_PUBLISHED)['roles'] == {'ADMIN': 1, 'VIEWER': 1}


# [위험] 기록 파일이 깨졌다고 게시를 영구히 못 하게 되면 안 된다(경고만).
def test_corrupt_local_record_warns_but_publishes(pub):
    publisher.publish_release(pub['repo'], release_zip(pub['tmp']), pub['key'])
    (pub['key'].parent / publisher.LAST_PUBLISHED).write_bytes(b'garbage')
    result = publisher.publish_release(pub['repo'], release_zip(pub['tmp'], '3.15.1'), pub['key'])
    assert any('마지막 게시 기록' in w for w in result['warnings'])


# [위험] 클라이언트가 낮은 sequence 를 거부할 때 사용자가 원인을 알 수 없다(동작은 그대로 거부).
def test_client_message_for_lower_sequence_mentions_restored_repository(pub, tmp_path):
    publisher.publish_release(pub['repo'], release_zip(pub['tmp']), pub['key'])
    with pytest.raises(UpdateError, match='저장소가 교체·복구된 것 같습니다. 관리자에게 알려 주세요'):
        core.stage_update(pub['repo'], tmp_path / 'profile', 'ADMIN', pub['public'], highest_sequence=5)


# ------------------------------------------------------------------ E-8 min_launcher / 불변 규칙

# [위험] 서명 payload 키 집합이 바뀌면 이미 설치된 런처가 모든 업데이트를 거부한다.
def test_signed_payload_and_channel_format_unchanged(pub):
    assert core._PAYLOAD_KEYS == {'schema', 'product', 'role', 'version', 'sequence', 'package', 'sha256',
                                  'size', 'executable', 'min_launcher', 'data_schema'}
    publisher.publish_release(pub['repo'], release_zip(pub['tmp']), pub['key'])
    envelope = load_json(pub['repo'] / 'channels' / 'ADMIN.json')
    assert set(envelope) == {'payload', 'signature'} and set(envelope['payload']) == core._PAYLOAD_KEYS
    assert set(load_json(pub['repo'] / publisher.PUBLIC_PIN)) == {'schema', 'product', 'public_key'}


# [위험] 기본값을 올리면 기존 PC 전부가 업데이트를 못 받는다.
def test_default_min_launcher_stays_1_0_0_and_old_launcher_accepts(pub, monkeypatch):
    result = publisher.publish_release(pub['repo'], release_zip(pub['tmp']), pub['key'])
    assert result['channels']['ADMIN']['min_launcher'] == '1.0.0'
    monkeypatch.setattr(core, 'LAUNCHER_VERSION', '1.0.0')            # 옛 런처 흉내
    core.verify_envelope(load_json(pub['repo'] / 'channels' / 'ADMIN.json'), pub['public'], 'ADMIN')
    assert core.LAUNCHER_VERSION == '1.0.0'


def test_launcher_version_is_1_1_0():
    assert core.LAUNCHER_VERSION == '1.1.0'


# [위험] 선택 인자가 서명에 반영되지 않거나, 새 런처 필요 업데이트를 옛 런처가 설치한다.
def test_min_launcher_argument_is_signed_and_old_launcher_refuses_with_clear_message(pub, monkeypatch):
    result = publisher.publish_release(pub['repo'], release_zip(pub['tmp']), pub['key'], min_launcher='1.1.0')
    assert result['channels']['ADMIN']['min_launcher'] == '1.1.0'
    envelope = load_json(pub['repo'] / 'channels' / 'ADMIN.json')
    core.verify_envelope(envelope, pub['public'], 'ADMIN')                  # 새 런처(1.1.0)는 받는다
    monkeypatch.setattr(core, 'LAUNCHER_VERSION', '1.0.1')
    with pytest.raises(UpdateError, match='새 설치 ZIP이 필요합니다. 관리자에게 알려 주세요'):
        core.verify_envelope(envelope, pub['public'], 'ADMIN')


@pytest.mark.parametrize('bad', ['2.0.0', 'abc', '1.1'])
def test_min_launcher_validated_before_any_mutation(tmp_path, bad):
    repo, key = tmp_path / 'repo', tmp_path / 'k' / 'signing_key.json'
    with pytest.raises(UpdateError):
        publisher.publish_release(repo, release_zip(tmp_path), key, min_launcher=bad)
    assert not repo.exists() and not key.exists()


# ------------------------------------------------------------------ E-9 비상 정지

# [위험] 정지가 설치된 버전/게시 파일까지 지우거나, 서명된 채널을 건드린다.
def test_pause_and_resume_only_touch_marker_files(pub):
    publisher.publish_release(pub['repo'], release_zip(pub['tmp']), pub['key'])
    before = snapshot(pub['repo'])
    publisher.set_paused(pub['repo'], ['ADMIN'], True)
    after = snapshot(pub['repo'])
    assert set(after) - set(before) == {'channels/ADMIN.paused'} and all(after[k] == before[k] for k in before)
    assert publisher.repository_status(pub['repo'], pub['key'])['roles']['ADMIN']['paused']
    publisher.set_paused(pub['repo'], ['ADMIN'], False)
    assert snapshot(pub['repo']) == before


# [위험] 게시된 적 없는 경로/잘못된 역할에 표식 파일을 만들어 엉뚱한 폴더를 어지럽힌다.
def test_pause_rejects_unknown_repository_and_role(pub, tmp_path):
    with pytest.raises(UpdateError):
        publisher.set_paused(tmp_path / 'nowhere', ['ADMIN'], True)
    assert not (tmp_path / 'nowhere').exists()
    with pytest.raises(UpdateError):
        publisher.set_paused(pub['repo'], ['EVIL'], True)
    with pytest.raises(UpdateError):
        publisher.set_paused(pub['repo'], [], True)
