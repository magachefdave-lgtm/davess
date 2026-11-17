"""New names fit the supported four-character UI; legacy identity stays intact."""
import copy
import sqlite3
import pytest
from test_backend_regression import AUTH, DAY_A, SEED, load_app

@pytest.fixture
def seeded(load_app):
    site=load_app('source');site.save(copy.deepcopy(SEED));return site

@pytest.mark.parametrize('name',['남궁민수','ABCD'])
def test_new_four_character_name_can_be_registered(seeded,name):
    members=copy.deepcopy(SEED['members'])+[dict(id=7,name=name,gender='M')]
    seeded.save(dict(members=members))
    assert any(m['name']==name for m in seeded.data()['members'])

def test_five_character_new_name_rejects_entire_save(seeded):
    before=seeded.data()
    response=seeded.post('/api/duty/save-all',dict(baseVersion=before['dataVersion'],
        members=before['members']+[dict(id=7,name='홍길동사원',gender='M')],comments={DAY_A:'must not commit'}))
    assert response.status_code==400,response.text
    assert '4자' in response.json()['detail']
    assert seeded.data()==before

def test_existing_long_name_is_preserved_when_other_data_is_saved(seeded):
    name='기존사원이름'
    with sqlite3.connect(seeded.module.DB_PATH) as db:
        db.execute('INSERT INTO members(id,name,gender) VALUES (7,?,?)',(name,'M'))
        db.execute('INSERT INTO schedules(date_key,slot_index,worker_name,role) VALUES (?,?,?,?)',('2030-06-21',0,name,'당직'))
    before=seeded.data();seeded.save(dict(members=before['members'],comments={DAY_A:'변경 메모'}))
    after=seeded.data();assert after['members']==before['members'];assert after['schedules']['2030-06-21']==before['schedules']['2030-06-21']

def test_json_backup_can_restore_legacy_long_names(seeded):
    data=seeded.data();data['members'].append(dict(id=7,name='옛날사원이름',gender='M'))
    response=seeded.post('/api/system/import-json',dict(data=data,baseVersion=data['dataVersion'],restoreLegacy=True))
    assert response.status_code==200,response.text
    assert any(m['name']=='옛날사원이름' for m in seeded.data()['members'])

@pytest.mark.parametrize('name',[12345,None,True,[],{}])
def test_non_string_name_cannot_bypass_registration_limit(seeded,name):
    before=seeded.data()
    response=seeded.post('/api/duty/save-all',dict(baseVersion=before['dataVersion'],members=before['members']+[dict(id=7,name=name,gender='M')]))
    assert response.status_code==400,response.text
    assert seeded.data()==before
