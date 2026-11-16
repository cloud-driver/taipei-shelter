import pytest
from fastapi.testclient import TestClient

from app.main import create_app

ADMIN = 'unit-test-administrator-token-32-chars'


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / 'test.sqlite3', ADMIN)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def admin():
    return {'X-Admin-Token': ADMIN}


@pytest.fixture
def guest(client):
    return {'Authorization': 'Bearer ' + client.post('/api/guest').json()['token']}


@pytest.fixture
def payload():
    return {
        'request_id': 'test-plan-request-0001',
        'origin': 'n22',
        'group_size': 2,
        'accessible_count': 0,
        'helper_present': False,
        'needs': {'mode': 'walking', 'confirmed': True},
        'shaking_stopped': True,
        'evacuation_needed': True,
        'max_extra_minutes': 30,
    }


def register(client, name='tester', profile=None):
    return client.post(
        '/api/auth/register',
        json={
            'username': name,
            'password': 'test-password-12345',
            'profile': profile or {'age': 70, 'consent': True},
        },
    )


def account(client, name='tester', profile=None):
    response = register(client, name, profile)
    assert response.status_code == 201, response.text
    return {'Authorization': 'Bearer ' + response.json()['token']}
