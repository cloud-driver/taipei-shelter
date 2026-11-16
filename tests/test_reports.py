import time

import pytest


def report(client, guest, **change):
    body = {
        'request_id': 'disaster-request-001',
        'edge_id': 'e-n22-n23',
        'kind': 'blocked',
        'observed_at': time.time(),
        'description': '測試用災情描述，不是真實現場資料',
    }
    body.update(change)
    return client.post('/api/reports', json=body, headers=guest)


def test_high_severity_invalidates_route_and_replans(client, guest, payload, admin):
    p = client.post('/api/plans', json=payload, headers=guest).json()
    edge = p['route']['edge_ids'][0]
    response = report(client, guest, edge_id=edge)
    assert response.status_code == 201 and response.json()['status'] == 'avoiding'
    assert edge in client.get('/api/map').json()['blocked_edges']
    assert client.get('/api/plans/' + p['id'], headers=guest).json()['status'] == 'invalidated'
    payload.update(request_id='replan-after-hazard-01', replace_id=p['id'])
    new = client.post('/api/plans', json=payload, headers=guest)
    assert new.status_code == 201
    assert edge not in new.json()['route']['edge_ids']


def test_pending_does_not_close_and_review_applies(client, guest, admin):
    r = report(client, guest, kind='debris').json()
    assert r['status'] == 'pending'
    assert not client.get('/api/map').json()['blocked_edges']
    assert (
        client.patch(
            '/api/admin/reports/' + r['id'],
            json={'status': 'confirmed', 'note': '人工查核測試'},
            headers=admin,
        ).status_code
        == 200
    )
    assert 'e-n22-n23' in client.get('/api/map').json()['blocked_edges']
    client.patch(
        '/api/admin/reports/' + r['id'],
        json={'status': 'resolved', 'note': '已確認障礙移除'},
        headers=admin,
    )
    assert not client.get('/api/map').json()['blocked_edges']


def test_confirmed_does_not_auto_expire(client, guest, admin):
    r = report(client, guest).json()
    client.patch(
        '/api/admin/reports/' + r['id'],
        json={'status': 'confirmed', 'note': '確認封閉的測試'},
        headers=admin,
    )
    with client.app.state.db.transaction() as conn:
        conn.execute('UPDATE reports SET created_at=0,observed_at=0,reviewed_at=0')
    assert 'e-n22-n23' in client.get('/api/map').json()['blocked_edges']


def test_multiple_reports_need_all_resolved(client, guest, admin):
    a = report(client, guest).json()
    other = {'Authorization': 'Bearer ' + client.post('/api/guest').json()['token']}
    b = report(client, other, request_id='another-source-request').json()
    client.patch(
        '/api/admin/reports/' + a['id'],
        json={'status': 'resolved', 'note': '第一則解除測試'},
        headers=admin,
    )
    assert 'e-n22-n23' in client.get('/api/map').json()['blocked_edges']
    client.patch(
        '/api/admin/reports/' + b['id'],
        json={'status': 'resolved', 'note': '第二則解除測試'},
        headers=admin,
    )
    assert not client.get('/api/map').json()['blocked_edges']


def test_report_idempotency_and_dedupe(client, guest):
    now = time.time()
    a = report(client, guest, observed_at=now).json()
    b = report(client, guest, observed_at=now).json()
    assert a['id'] == b['id']
    assert report(client, guest, description='不同內容卻相同識別').status_code == 409
    c = report(client, guest, request_id='different-key-same-target').json()
    assert c['id'] == a['id']


@pytest.mark.parametrize(
    'change',
    [
        {'observed_at': 0},
        {'observed_at': time.time() + 100000},
        {'edge_id': 'missing'},
        {'shelter_id': 'sA'},
        {'kind': 'closure'},
    ],
)
def test_invalid_report(client, guest, change):
    assert report(client, guest, **change).status_code == 422


def test_public_report_redacts_prose_and_owner(client, guest, admin):
    report(client, guest, description='private details test')
    public = client.get('/api/reports').json()['items'][0]
    assert 'owner_id' not in public and 'description' not in public
    view = client.get('/api/admin/overview', headers=admin).json()
    assert view['reports'][0]['description'] == 'private details test'
    assert 'owner_id' not in str(view) and 'user_id' not in str(view)


def test_unknown_report_review(client, admin):
    assert (
        client.patch(
            '/api/admin/reports/missing',
            json={'status': 'confirmed', 'note': '測試審查'},
            headers=admin,
        ).status_code
        == 404
    )


def test_report_rate_limit(client, guest):
    edges = client.get('/api/map').json()['edges']
    for i in range(8):
        assert (
            report(
                client, guest, edge_id=edges[i]['id'], request_id=f'report-limit-{i:03}'
            ).status_code
            == 201
        )
    assert (
        report(client, guest, edge_id=edges[9]['id'], request_id='report-limit-last').status_code
        == 429
    )


def test_shelter_closure_excludes_destination(client, guest, payload):
    p = client.post('/api/plans', json=payload, headers=guest).json()
    r = report(client, guest, edge_id=None, shelter_id=p['shelter_id'], kind='closure')
    assert r.status_code == 201
    assert client.get('/api/plans/' + p['id'], headers=guest).json()['status'] == 'invalidated'
    payload.update(request_id='after-shelter-closure', replace_id=p['id'])
    new = client.post('/api/plans', json=payload, headers=guest).json()
    assert new['shelter_id'] != p['shelter_id']


def test_shelter_update_and_arrival_floor(client, guest, payload, admin):
    p = client.post('/api/plans', json=payload, headers=guest).json()
    for action in ['accept', 'arrive']:
        client.post('/api/plans/' + p['id'] + '/transition', json={'action': action}, headers=guest)
    update = {
        'status': 'open',
        'capacity': 1,
        'accessible_capacity': 0,
        'background': 0,
        'background_accessible': 0,
        'entrance_step_free': True,
        'space_accessible': True,
        'accessible_toilet': True,
        'note': '容量變更測試',
    }
    assert (
        client.patch(
            '/api/admin/shelters/' + p['shelter_id'], json=update, headers=admin
        ).status_code
        == 409
    )
    update.update(capacity=20, accessible_capacity=4, entrance_step_free=None)
    assert (
        client.patch(
            '/api/admin/shelters/' + p['shelter_id'], json=update, headers=admin
        ).status_code
        == 200
    )
    assert client.get('/api/plans/' + p['id'], headers=guest).json()['status'] == 'arrived'


def test_event_version_and_drill_only(client, admin):
    now = time.time()
    body = {
        'event_id': 'test-001',
        'version': 2,
        'occurred_at': now,
        'published_at': now,
        'title': '地震演練測試',
        'magnitude': 6,
        'source': '本機演練',
        'drill': True,
    }
    assert client.post('/api/admin/events', json=body, headers=admin).status_code == 200
    assert client.post('/api/admin/events', json=body, headers=admin).status_code == 409
    body['version'] = 1
    assert client.post('/api/admin/events', json=body, headers=admin).status_code == 409
    assert client.get('/api/events').json()['items'][0]['version'] == 2
    body.update(version=3, drill=False)
    assert client.post('/api/admin/events', json=body, headers=admin).status_code == 422


def test_unknown_accessibility_and_all_blocked_fail_closed(client, guest, payload, admin):
    with client.app.state.db.transaction() as conn:
        import json

        for row in conn.execute('SELECT * FROM shelters').fetchall():
            s = json.loads(row['data'])
            s['entrance_step_free'] = None
            conn.execute('UPDATE shelters SET data=? WHERE id=?', (json.dumps(s), s['id']))
    payload['needs']['mode'] = 'wheelchair'
    assert client.post('/api/plans', json=payload, headers=guest).status_code == 409


def test_all_routes_blocked_no_relaxation(client, guest, payload):
    with client.app.state.db.transaction() as conn:
        dataset = json_dataset(conn)
        for i, e in enumerate(dataset['edges']):
            conn.execute(
                'INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (
                    f'r{i}',
                    None,
                    f'req{i}',
                    'hash',
                    'edge',
                    e['id'],
                    'blocked',
                    '測試',
                    time.time(),
                    'confirmed',
                    time.time(),
                    time.time(),
                    '測試',
                ),
            )
    assert client.post('/api/plans', json=payload, headers=guest).status_code == 409


def json_dataset(conn):
    from app.db import get_dataset

    return get_dataset(conn)
