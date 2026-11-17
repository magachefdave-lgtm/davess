"""New installations must report equal signed releases as up to date."""
from pathlib import Path
import pytest
from updater import core, launcher, manual_update
from test_core import release, envelope
from test_launcher import site, fake_spawn


@pytest.mark.parametrize('role',['ADMIN','VIEWER'])
@pytest.mark.parametrize('highest',[0,2,3])
def test_equal_signed_release_is_no_download_and_preserves_profile(tmp_path,role,highest):
    repository,profile,key,public,payload,package,manifest=release(tmp_path,role=role,version='3.18.31',sequence=3)
    (profile/'config.ini').write_text('existing configuration')
    (profile/'state.json').write_text('{"sentinel":"preserve"}')
    before={p.name:p.read_bytes() for p in profile.iterdir()}
    assert core.stage_update(repository,profile,role,public,highest_sequence=highest,current_version='3.18.31') is None
    assert {p.name:p.read_bytes() for p in profile.iterdir()}==before


@pytest.mark.parametrize('failure',['old_sequence','bad_signature','missing_manifest'])
def test_equal_version_never_bypasses_signed_metadata_and_sequence_guards(tmp_path,failure):
    repository,profile,key,public,payload,package,manifest=release(tmp_path,version='3.18.31',sequence=3)
    if failure=='bad_signature':
        signed=envelope(payload,key);signed['payload']['sequence']=4;core.atomic_json(manifest,signed)
    if failure=='missing_manifest':manifest.unlink()
    with pytest.raises(core.UpdateError):
        core.stage_update(repository,profile,'ADMIN',public,highest_sequence=4 if failure=='old_sequence' else 0,current_version='3.18.31')
    assert not (profile/'versions').exists()


@pytest.mark.parametrize('role',['ADMIN','VIEWER'])
def test_fresh_launcher_equal_release_has_no_failure_notice_and_later_update_still_works(site,tmp_path,monkeypatch,role):
    repository,unused,key,public,payload,package,manifest=release(tmp_path,role=role,version='3.18.31',sequence=3)
    exe=site['exe'].with_name(core.EXECUTABLES[role]);exe.write_bytes(site['exe'].read_bytes())
    site.update(exe=exe,role=role)
    bootstrap=dict(site['bootstrap'],role=role,public_key=public,repository=str(repository))
    core.atomic_json(exe.parent/'onair_update.json',bootstrap)
    records=[];monkeypatch.setattr(launcher,'_spawn',fake_spawn(site,records))
    before=site['ini'].read_bytes(),site['db'].read_bytes()
    for _ in range(2):
        assert launcher.run(role,exe,version='3.18.31')==0
        status=core.load_json(site['profile']/launcher.STATUS_FILE)
        assert status['result']=='up_to_date' and status['notice'] is False
        assert status['current_version']=='3.18.31' and '최신' in status['message']
        state=core.load_json(site['profile']/'state.json')
        assert state['highest_sequence']==0 and state['pending'] is None
    assert not any('업데이트 확인 실패' in p.read_text() for p in site['profile'].glob('launcher.log*'))
    pin=site['profile']/'onair_update.json'
    assert manual_update.check_release(site['profile'],role,'3.18.31')['result']=='up_to_date'
    newer=dict(payload,version='3.18.32',sequence=4,package=f'packages/{role}/3.18.32-{payload["sha256"][:12]}.zip')
    new_package=repository/newer['package'];new_package.write_bytes(package.read_bytes())
    core.atomic_json(manifest,envelope(newer,key))
    assert launcher.run(role,exe,version='3.18.31')==0
    state=core.load_json(site['profile']/'state.json')
    assert state['active']['version']=='3.18.32' and state['highest_sequence']==4
    assert core.load_json(site['profile']/launcher.STATUS_FILE)['result']=='updated'
    assert launcher.run(role,exe,version='3.18.31')==0
    assert core.load_json(site['profile']/launcher.STATUS_FILE)['result']=='up_to_date'
    assert (site['ini'].read_bytes(),site['db'].read_bytes())==before
