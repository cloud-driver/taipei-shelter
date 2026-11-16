import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import ADMIN, account, register
from fastapi.testclient import TestClient

from app.db import loads
from app.main import create_app


def test_health(client):
    assert client.get('/api/health').json()['status'] == 'ok'


@pytest.mark.parametrize(
    'path',
    [
        '/',
        '/test',
        '/admin',
        '/static/app.js',
        '/static/style.css',
        '/sw.js',
        '/openapi.json',
        '/docs',
    ],
)
def test_pages(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers['x-content-type-options'] == 'nosniff'


def test_map_anonymous_drill_and_unknown(client):
    data = client.get('/api/map').json()
    assert data['drill'] is True
    assert data['source']
    assert next(s for s in data['shelters'] if s['id'] == 'sD')['capacity'] is None
    assert len(data['edges']) == 40


def test_cross_origin_write_rejected(client):
    assert client.post('/api/guest', headers={'Origin': 'https://evil.invalid'}).status_code == 403


def test_same_origin_write_allowed(client):
    assert client.post('/api/guest', headers={'Origin': 'http://testserver'}).status_code == 200


def test_dns_rebinding_host_rejected(client):
    assert client.get('/api/health', headers={'Host': 'evil.invalid'}).status_code == 400


def test_oversized_body(client):
    assert client.post('/api/guest', content='x' * 32769).status_code == 413


def test_validation_does_not_echo_password(client):
    r = client.post(
        '/api/auth/login',
        json={'username': 'x', 'password': 'secret-password', 'unexpected': 'sensitive'},
    )
    assert r.status_code == 422
    assert 'secret-password' not in r.text and 'sensitive' not in r.text


def test_register_needs_consent_and_minimum_information(client):
    assert register(client, profile={'consent': True}).status_code == 422
    assert register(client, profile={'age': 20, 'consent': False}).status_code == 422


def test_age_does_not_infer_disability(client):
    headers = account(client)
    profile = client.get('/api/auth/me', headers=headers).json()['profile']
    assert profile['age'] == 70 and profile['needs'] is None


def test_password_hashed_and_tokens_hashed(client):
    response = register(client)
    with client.app.state.db.connect() as conn:
        stored = conn.execute('SELECT password FROM users').fetchone()[0]
        session = conn.execute('SELECT token_hash FROM sessions').fetchone()[0]
    assert 'test-password' not in stored
    assert session == hashlib.sha256(response.json()['token'].encode()).hexdigest()


def test_duplicate_username_case_insensitive(client):
    assert register(client, 'Tester').status_code == 201
    assert register(client, 'tester').status_code == 409


def test_login_logout_revocation(client):
    register(client)
    assert (
        client.post('/api/auth/login', json={'username': 'tester', 'password': 'wrong'}).status_code
        == 401
    )
    result = client.post(
        '/api/auth/login', json={'username': 'TESTER', 'password': 'test-password-12345'}
    )
    headers = {'Authorization': 'Bearer ' + result.json()['token']}
    assert client.get('/api/auth/me', headers=headers).status_code == 200
    assert client.post('/api/auth/logout', headers=headers).status_code == 200
    assert client.get('/api/auth/me', headers=headers).status_code == 401


def test_failed_login_rate_limit_commits(client):
    for _ in range(20):
        assert (
            client.post(
                '/api/auth/login', json={'username': 'nobody', 'password': 'wrong'}
            ).status_code
            == 401
        )
    assert (
        client.post('/api/auth/login', json={'username': 'nobody', 'password': 'wrong'}).status_code
        == 429
    )


def test_guest_cannot_store_profile(client, guest):
    assert (
        client.put(
            '/api/auth/profile', headers=guest, json={'age': 20, 'consent': True}
        ).status_code
        == 403
    )


def test_nav_needs_session(client, payload):
    assert client.post('/api/plans', json=payload).status_code == 401


@pytest.mark.parametrize('field', ['shaking_stopped', 'evacuation_needed'])
def test_no_departure_without_confirmation(client, guest, payload, field):
    payload[field] = False
    assert client.post('/api/plans', json=payload, headers=guest).status_code == 409


def test_needs_require_confirmation(client, guest, payload):
    payload['needs']['confirmed'] = False
    assert client.post('/api/plans', json=payload, headers=guest).status_code == 422


def test_unsure_needs_block_navigation(client, guest, payload):
    payload['needs']['mode'] = 'unsure'
    assert client.post('/api/plans', json=payload, headers=guest).status_code == 422


def test_assistance_requires_helper(client, guest, payload):
    payload['needs']['mode'] = 'assistance'
    assert client.post('/api/plans', json=payload, headers=guest).status_code == 422
    payload['helper_present'] = True
    assert client.post('/api/plans', json=payload, headers=guest).status_code == 201


@pytest.mark.parametrize(
    'change',
    [
        {'group_size': 0},
        {'accessible_count': 3},
        {'origin': 'missing'},
        {'group_size': 31},
        {'needs': {'speed_mps': float('inf')}},
    ],
)
def test_invalid_plan_input(client, guest, payload, change):
    payload.update(change)
    if change.get('needs', {}).get('speed_mps') == float('inf'):
        r = client.post(
            '/api/plans',
            content=json.dumps(payload),
            headers={**guest, 'Content-Type': 'application/json'},
        )
    else:
        r = client.post('/api/plans', json=payload, headers=guest)
    assert r.status_code == 422


def test_wheelchair_hard_constraints(client, guest, payload):
    payload['needs'].update(mode='wheelchair', accessible_toilet=True)
    result = client.post('/api/plans', json=payload, headers=guest).json()
    assert result['accessible_count'] == 1
    graph = client.get('/api/map').json()
    edge_lookup = {e['id']: e for e in graph['edges']}
    for edge in result['route']['edge_ids']:
        e = edge_lookup[edge]
        assert (
            not e['stairs']
            and e['slope'] is not None
            and e['width'] >= 0.9
            and e['surface_ok'] is True
        )
    assert result['shelter_id'] in ('sA', 'sB')


def test_accessible_group_forces_step_free(client, guest, payload):
    payload['accessible_count'] = 1
    result = client.post('/api/plans', json=payload, headers=guest).json()
    assert 'e-n21-n22' not in result['route']['edge_ids']


def test_idempotent_plan_and_collision(client, guest, payload):
    first = client.post('/api/plans', json=payload, headers=guest).json()
    second = client.post('/api/plans', json=payload, headers=guest).json()
    assert first['id'] == second['id']
    payload['group_size'] = 3
    assert client.post('/api/plans', json=payload, headers=guest).status_code == 409
    with client.app.state.db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM allocations').fetchone()[0] == 1


def test_one_active_plan_and_other_user_privacy(client, guest, payload):
    result = client.post('/api/plans', json=payload, headers=guest).json()
    payload['request_id'] = 'another-request-123'
    assert client.post('/api/plans', json=payload, headers=guest).status_code == 409
    other = {'Authorization': 'Bearer ' + client.post('/api/guest').json()['token']}
    assert client.get('/api/plans/' + result['id'], headers=other).status_code == 404
    assert (
        client.post(
            '/api/plans/' + result['id'] + '/transition', json={'action': 'cancel'}, headers=other
        ).status_code
        == 404
    )


def test_state_transitions_and_arrival_keeps_occupancy(client, guest, payload):
    result = client.post('/api/plans', json=payload, headers=guest).json()
    path = '/api/plans/' + result['id'] + '/transition'
    assert client.post(path, json={'action': 'arrive'}, headers=guest).status_code == 409
    assert (
        client.post(path, json={'action': 'accept'}, headers=guest).json()['status'] == 'accepted'
    )
    assert client.post(path, json={'action': 'arrive'}, headers=guest).json()['status'] == 'arrived'
    s = next(
        s for s in client.get('/api/map').json()['shelters'] if s['id'] == result['shelter_id']
    )
    assert s['estimated_load'] == 2
    assert client.post(path, json={'action': 'cancel'}, headers=guest).status_code == 409
    assert client.post(path, json={'action': 'leave'}, headers=guest).json()['status'] == 'left'
    s = next(
        s for s in client.get('/api/map').json()['shelters'] if s['id'] == result['shelter_id']
    )
    assert s['estimated_load'] == 0


def test_expired_plan_releases_capacity(client, guest, payload):
    p = client.post('/api/plans', json=payload, headers=guest).json()
    with client.app.state.db.transaction() as conn:
        conn.execute('UPDATE allocations SET expires_at=0 WHERE id=?', (p['id'],))
    assert client.get('/api/plans/' + p['id'], headers=guest).json()['status'] == 'expired'
    with client.app.state.db.connect() as conn:
        assert not loads(conn)


def test_atomic_replan_and_failed_replan_retains_original(client, guest, payload):
    first = client.post('/api/plans', json=payload, headers=guest).json()
    payload.update(request_id='replacement-request-123', replace_id=first['id'], origin='n00')
    new = client.post('/api/plans', json=payload, headers=guest).json()
    assert client.get('/api/plans/' + first['id'], headers=guest).json()['status'] == 'cancelled'
    payload.update(
        request_id='failed-replacement-123',
        replace_id=new['id'],
        group_size=30,
        accessible_count=30,
    )
    assert client.post('/api/plans', json=payload, headers=guest).status_code == 409
    assert client.get('/api/plans/' + new['id'], headers=guest).json()['status'] == 'proposed'


def test_profile_version_invalidates_all_user_sessions(client, payload):
    headers = account(client)
    p = client.post('/api/plans', json=payload, headers=headers).json()
    assert p['profile_version'] == 1
    login = client.post(
        '/api/auth/login', json={'username': 'tester', 'password': 'test-password-12345'}
    ).json()
    second = {'Authorization': 'Bearer ' + login['token']}
    response = client.put(
        '/api/auth/profile',
        json={'age': 70, 'needs': {'mode': 'wheelchair'}, 'consent': True},
        headers=second,
    )
    assert response.json()['version'] == 2
    assert client.get('/api/plans/' + p['id'], headers=headers).json()['status'] == 'invalidated'
    with client.app.state.db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM profile_history').fetchone()[0] == 2


def test_delete_user_removes_private_data_and_preserves_arrived_count(client, payload):
    headers = account(client)
    p = client.post('/api/plans', json=payload, headers=headers).json()
    for action in ['accept', 'arrive']:
        client.post(
            '/api/plans/' + p['id'] + '/transition', json={'action': action}, headers=headers
        )
    assert client.delete('/api/auth/me', headers=headers).status_code == 200
    assert client.get('/api/auth/me', headers=headers).status_code == 401
    with client.app.state.db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM profile_history').fetchone()[0] == 0
    shelter = next(
        s for s in client.get('/api/map').json()['shelters'] if s['id'] == p['shelter_id']
    )
    assert shelter['estimated_load'] == 2 and shelter['background'] == 2


def test_admin_requires_secret(client):
    assert client.get('/api/admin/overview').status_code == 403
    assert client.get('/api/admin/overview', headers={'X-Admin-Token': 'bad'}).status_code == 403


def test_persistence_after_recreating_app(client, guest, payload):
    p = client.post('/api/plans', json=payload, headers=guest).json()
    other_app = create_app(client.app.state.db.path, ADMIN)
    with TestClient(other_app) as other:
        assert other.get('/api/plans/' + p['id'], headers=guest).json()['status'] == 'proposed'


def test_concurrent_allocations_never_overbook(client, payload):
    headers = [
        {'Authorization': 'Bearer ' + client.post('/api/guest').json()['token']} for _ in range(24)
    ]

    def allocate(i):
        body = {**payload, 'group_size': 5, 'request_id': f'concurrent-request-{i:03}'}
        return client.post('/api/plans', json=body, headers=headers[i])

    with ThreadPoolExecutor(max_workers=12) as pool:
        responses = list(pool.map(allocate, range(24)))
    assert all(r.status_code in (201, 409) for r in responses)
    assert any(r.status_code == 201 for r in responses)
    assert any(r.status_code == 409 for r in responses)
    data = client.get('/api/map').json()
    for s in data['shelters']:
        if s['capacity'] is not None:
            assert s['estimated_load'] <= s['capacity']
            if s['accessible_capacity'] is not None:
                assert (
                    s['estimated_load'] - s['accessible_load']
                    <= s['capacity'] - s['accessible_capacity']
                )
    with client.app.state.db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM allocations').fetchone()[0] == sum(
            r.status_code == 201 for r in responses
        )


def test_concurrent_duplicate_request_only_inserts_once(client, guest, payload):
    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(
            pool.map(lambda _: client.post('/api/plans', json=payload, headers=guest), range(10))
        )
    assert all(r.status_code == 201 for r in responses)
    assert len({r.json()['id'] for r in responses}) == 1


def test_session_expiry_rejected(client, guest, payload):
    with client.app.state.db.transaction() as conn:
        conn.execute('UPDATE sessions SET expires_at=0')
    assert client.post('/api/plans', json=payload, headers=guest).status_code == 401


def test_own_requirements_returned_for_reload(client, guest, payload):
    payload['needs'] = {'mode': 'wheelchair', 'confirmed': True}
    p = client.post('/api/plans', json=payload, headers=guest).json()
    saved = client.get('/api/plans/' + p['id'], headers=guest).json()
    assert saved['requirements']['needs']['mode'] == 'wheelchair'
    assert saved['requirements']['accessible_count'] == 1


def test_accessibility_map_network_thresholds(client):
    result = client.get('/api/accessibility?origin=n22&mode=wheelchair').json()
    assert result['speed_mps'] == 0.8 and result['drill']
    assert result['thresholds_minutes'] == [5, 10, 15]
    assert next(n for n in result['nodes'] if n['id'] == 'n22')['minutes'] == 0
    assert client.get('/api/accessibility?origin=missing').status_code == 422
    assert client.get('/api/accessibility?mode=airplane').status_code == 422
