"""The shipped host initializes and serves dinner data without altering duty data."""
import copy
from test_backend_regression import AUTH, SEED, load_app


def test_host_initializes_and_serves_dinner_summary_without_duty_changes(load_app):
    app = load_app('source')
    app.save(copy.deepcopy(SEED))
    before = app.data()
    response = app.get('/api/dinner/summary?name=A', headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json()['polls'] == []
    assert response.json()['role'] is None
    after = app.data()
    for key in ('members', 'schedules', 'absences', 'customHolidays', 'rules', 'globalMemo'):
        assert after[key] == before[key]
    with app.module.get_db_connection(readonly=True) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'dinner_polls', 'dinner_responses', 'dinner_connections'} <= tables
