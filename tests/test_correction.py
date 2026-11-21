from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import pytest
from app.modules.runs.service import run

REV2 = {'修订号': '2', '本年日销售': '2025-01-06:150'}


def register(client, run_id, data):
    return client.post(f'/api/runs/{run_id}/corrections', json={'data': data})


@pytest.fixture
def signed(client):
    run_id = client.post('/api/runs', json={}).json()['manifest']['run_id']
    response = client.post(f'/api/review/{run_id}',
                           json={'version': 0, 'decision': '确认', 'note': '月结已逐条核对'})
    assert response.status_code == 200
    return run_id


@pytest.fixture
def rev2(sample_csv):
    return sample_csv(REV2).decode()


def test_unsigned_run_rejected(client, rev2):
    run_id = client.post('/api/runs', json={}).json()['manifest']['run_id']
    response = register(client, run_id, rev2)
    assert (response.status_code, response.json()['code']) == (409, 'RUN_UNSIGNED')


def test_returned_run_rejected(client, signed, rev2):
    returned = client.post(f'/api/review/{signed}',
                           json={'version': 1, 'decision': '退回', 'note': '复核退回'})
    assert returned.status_code == 200
    response = register(client, signed, rev2)
    assert (response.status_code, response.json()['code']) == (409, 'RUN_UNSIGNED')


def test_register_ready_and_idempotent(client, signed, rev2):
    (correction,) = register(client, signed, rev2).json()['corrections']
    assert (correction['key'], correction['revision']) == ('00101', 2)
    assert correction['state'] == 'ready' and correction['signed_version'] == 1
    assert (correction['origin_amount'], correction['revised_amount'],
            correction['delta']) == ('20', '50', '30')
    (again,) = register(client, signed, rev2).json()['corrections']
    assert again['id'] == correction['id']
    assert len(client.get('/api/corrections').json()) == 1


def test_origin_run_untouched(client, signed, rev2):
    before = client.get(f'/api/runs/{signed}').json()
    register(client, signed, rev2)
    after = client.get(f'/api/runs/{signed}').json()
    assert after == before and after['adjustments']['lines'] == []
    export = client.get(f'/api/export/{signed}').json()
    assert export['corrections'][0]['state'] == 'ready'
    assert export['reconciliation']['delta'] == before['book']['delta']


def test_carry_forward_once(client, book, signed, rev2):
    register(client, signed, rev2)
    second = client.post('/api/runs', json={'book': book.decode() + '\n'}).json()
    (line,) = second['adjustments']['lines']
    assert (line['key'], line['status'], line['effective']) == ('00101', 'applied', '30')
    assert (line['delta'], line['revised_amount']) == ('30', '50')
    assert Decimal(second['adjustments']['settled']) == Decimal(second['book']['delta']) + 30
    third = client.post('/api/runs', json={'book': book.decode() + '\n\n'}).json()
    assert third['adjustments']['lines'] == []
    (correction,) = client.get('/api/corrections').json()
    assert correction['state'] == 'applied' and correction['carried'] == '30'
    assert correction['applied_run'] == second['manifest']['run_id']


def test_replay_keeps_single_application(client, book, signed, rev2):
    register(client, signed, rev2)
    second = client.post('/api/runs', json={'book': book.decode() + '\n'}).json()
    run_id = second['manifest']['run_id']
    assert client.post(f'/api/runs/{run_id}/replay').json() == second
    assert client.post('/api/runs', json={'book': book.decode() + '\n'}).json() == second
    assert len(client.get('/api/corrections').json()) == 1


def test_absorbed_when_source_contains_revision(client, source, signed, rev2):
    register(client, signed, rev2)
    text = source.decode('utf-8-sig')
    if not text.endswith('\n'):
        text += '\n'
    text += ('00101,2,2026-11-20T10:00:00+08:00,2024-01-01,53,2025-01-06,52,'
             '2024-01-01:100;2024-12-30:1100,2025-01-06:150,门店补报修订\n')
    second = client.post('/api/runs', json={'data': text}).json()
    (line,) = second['adjustments']['lines']
    assert (line['status'], line['effective']) == ('absorbed', '0')
    assert second['adjustments']['total'] == '0'
    assert Decimal(second['adjustments']['settled']) == Decimal(second['book']['delta'])


def test_pending_kept_and_resolved(client, book, signed, sample_csv):
    broken = sample_csv({'修订号': '2', '本年日销售': 'N/A'}).decode()
    (pending,) = register(client, signed, broken).json()['corrections']
    assert (pending['state'], pending['reason'], pending['revision']) == ('pending', 'REQUIRED', 2)
    second = client.post('/api/runs', json={'book': book.decode() + '\n'}).json()
    assert second['adjustments']['lines'] == []
    (still,) = client.get('/api/corrections').json()
    assert still['state'] == 'pending'
    fixed = sample_csv(REV2).decode()
    (ready,) = register(client, signed, fixed).json()['corrections']
    assert ready['id'] == pending['id'] and ready['state'] == 'ready' and ready['delta'] == '30'
    third = client.post('/api/runs', json={'book': book.decode() + '\n\n'}).json()
    assert [line['effective'] for line in third['adjustments']['lines']] == ['30']


def test_pending_from_conflicting_evidence(client, signed, sample_csv):
    conflicted = sample_csv({'修订号': '2', '本年日销售': '2025-01-06:150;2025-01-06:160'}).decode()
    (pending,) = register(client, signed, conflicted).json()['corrections']
    assert (pending['state'], pending['reason']) == ('pending', 'FISCAL_CONFLICT')


def test_higher_revision_supersedes_pending(client, signed, sample_csv):
    broken = sample_csv({'修订号': '2', '本年日销售': 'N/A'}).decode()
    (pending,) = register(client, signed, broken).json()['corrections']
    higher = sample_csv({'修订号': '3', '本年日销售': '2025-01-06:150'}).decode()
    (ready,) = register(client, signed, higher).json()['corrections']
    states = {item['id']: item['state'] for item in client.get('/api/corrections').json()}
    assert states[pending['id']] == 'superseded' and states[ready['id']] == 'ready'


def test_revision_not_higher(client, signed, sample_csv):
    response = register(client, signed, sample_csv({'修订号': '1'}).decode())
    assert (response.status_code, response.json()['code']) == (422, 'CORRECTION_REVISION')


def test_unknown_key(client, signed, sample_csv):
    response = register(client, signed, sample_csv({'门店财周单': '09999', '修订号': '2'}).decode())
    assert (response.status_code, response.json()['code']) == (422, 'CORRECTION_KEY_UNKNOWN')


def test_empty_evidence_rejected(client, signed):
    response = client.post(f'/api/runs/{signed}/corrections', json={})
    assert (response.status_code, response.json()['code']) == (422, 'REQUIRED')


def test_second_correction_carries_remaining_delta(client, book, signed, sample_csv):
    register(client, signed, sample_csv(REV2).decode())
    client.post('/api/runs', json={'book': book.decode() + '\n'})
    higher = sample_csv({'修订号': '3', '本年日销售': '2025-01-06:170'}).decode()
    register(client, signed, higher)
    third = client.post('/api/runs', json={'book': book.decode() + '\n\n'}).json()
    (line,) = third['adjustments']['lines']
    assert (line['revision'], line['delta']) == (3, '50')
    assert (line['previously_carried'], line['effective']) == ('30', '20')


def test_concurrent_claim_once(client, source, book, signed, rev2):
    register(client, signed, rev2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda payload: run(source, payload), [book + b'\n', book + b'\n\n']))
    carried = [result for result in results if result['adjustments']['lines']]
    assert len(carried) == 1 and carried[0]['adjustments']['total'] == '30'
    (correction,) = client.get('/api/corrections').json()
    assert correction['state'] == 'applied'
    assert correction['applied_run'] == carried[0]['manifest']['run_id']


def test_export_lineage(client, book, signed, rev2):
    (correction,) = register(client, signed, rev2).json()['corrections']
    second = client.post('/api/runs', json={'book': book.decode() + '\n'}).json()
    origin = client.get(f'/api/export/{signed}').json()
    assert origin['corrections'][0]['id'] == correction['id']
    assert origin['corrections'][0]['applied_run'] == second['manifest']['run_id']
    assert origin['corrections'][0]['evidence']['source_sha256']
    follow = client.get(f"/api/export/{second['manifest']['run_id']}").json()
    assert follow['adjustments']['lines'][0]['origin_run'] == signed
    assert follow['corrections'] == []


def test_detail_endpoint(client, signed, rev2):
    (correction,) = register(client, signed, rev2).json()['corrections']
    detail = client.get(f"/api/corrections/{correction['id']}")
    assert detail.status_code == 200
    assert detail.json()['evidence']['record']['amount'] == '50'
    missing = client.get('/api/corrections/none')
    assert (missing.status_code, missing.json()['code']) == (404, 'CORRECTION_UNKNOWN')
