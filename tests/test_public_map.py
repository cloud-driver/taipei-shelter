import gzip
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Needs
from app.osm_graph import WalkingGraph, edge_allowed, from_overpass, way_walkable
from app.public_data import canonical_address, canonical_name, valid_coordinate
from app.public_map import PublicRoute


def public_fixture(directory):
    def place(id, **kwargs):
        return {
            'id': id,
            'name': id,
            'kind': 'shelter',
            'district': '大安區',
            'address': '臺北市大安區測試地址',
            'lat': 25.031,
            'lon': 121.531,
            'earthquake': True,
            'earthquake_role': 'primary',
            'status': 'unknown',
            'source': 'education',
            'coordinate_source': 'nfa',
            'coordinate_method': 'fixture',
            'entrance_verified': False,
            'accessible_facility': None,
            'capacity': 100,
            **kwargs,
        }

    items = [
        place('real'),
        place('closed', status='closed'),
        place('air', kind='airraid', earthquake=False, earthquake_role='not_applicable'),
        place('missing', lat=None, lon=None),
        place('park', kind='park', earthquake_role='disaster_park'),
    ]
    nodes = [
        {'id': 'a', 'lat': 25.03, 'lon': 121.53},
        {'id': 'b', 'lat': 25.031, 'lon': 121.53},
        {'id': 'c', 'lat': 25.031, 'lon': 121.531},
        {'id': 'd', 'lat': 25.03, 'lon': 121.531},
    ]

    def edge(id, a, b, length):
        return {
            'id': id,
            'a': a,
            'b': b,
            'length': length,
            'stairs': False,
            'slope': None,
            'width': None,
            'surface_ok': None,
            'name': id,
        }

    graph = {
        'nodes': nodes,
        'edges': [
            edge('ab', 'a', 'b', 100),
            edge('bc', 'b', 'c', 100),
            edge('ad', 'a', 'd', 140),
            edge('dc', 'd', 'c', 140),
        ],
        'timestamp': '2026-07-15T00:00:00Z',
    }
    directory.mkdir(parents=True)
    (directory / 'catalog.json').write_text(json.dumps(items))
    (directory / 'sources.json').write_text(
        json.dumps({'version': 'test-public', 'snapshot_at': '2026-10-21', 'sources': {}})
    )
    (directory / 'shelters.csv').write_text('id,name\nreal,real\n')
    with gzip.open(directory / 'walking-graph.json.gz', 'wt') as f:
        json.dump(graph, f)


@pytest.fixture
def public_client(tmp_path):
    directory = tmp_path / 'public'
    public_fixture(directory)
    app = create_app(tmp_path / 'db', 'test-public-admin-token-32-chars', public_data_dir=directory)
    with TestClient(app) as c:
        yield c


def body(**changes):
    return {
        'lat': 25.03,
        'lon': 121.53,
        'destination_id': 'real',
        'needs': {'mode': 'walking', 'confirmed': True},
        'shaking_stopped': True,
        'evacuation_needed': True,
        'acknowledge_unknown_status': True,
        **changes,
    }


def test_catalog_is_public_and_keeps_unlocated(public_client):
    result = public_client.get('/api/public/catalog?kind=all').json()
    assert result['total'] == 5 and result['located'] == 4
    assert any(s['id'] == 'missing' and s['lat'] is None for s in result['items'])
    points = public_client.get('/api/public/points?kind=all').json()
    assert points['total'] == 5 and points['unlocated'] == 1 and len(points['items']) == 4


def test_catalog_filters_and_pagination(public_client):
    assert public_client.get('/api/public/catalog?kind=airraid').json()['total'] == 1
    assert public_client.get('/api/public/catalog?kind=earthquake').json()['total'] == 4
    assert public_client.get('/api/public/catalog?q=real').json()['total'] == 1
    assert (
        len(public_client.get('/api/public/catalog?kind=all&offset=2&limit=2').json()['items']) == 2
    )
    assert public_client.get('/api/public/catalog?district=臺中市').status_code == 422
    assert public_client.get('/api/public/points?district=臺中市').status_code == 422
    assert public_client.get('/api/public/catalog/missing-id').status_code == 404
    assert public_client.get('/api/public/download.csv').status_code == 200


def test_reference_route_is_network_based_and_no_allocation(public_client):
    r = public_client.post('/api/public/route', json=body())
    assert r.status_code == 200, r.text
    p = r.json()
    assert p['edge_ids'] == ['ab', 'bc'] and p['distance_m'] == 200
    assert p['geometry']['coordinates'] == [[121.53, 25.03], [121.53, 25.031], [121.531, 25.031]]
    assert not p['allocation_created'] and p['mode'] == 'public-reference'
    with public_client.app.state.db.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM allocations').fetchone()[0] == 0


@pytest.mark.parametrize(
    'change,expected',
    [
        ({'shaking_stopped': False}, 409),
        ({'evacuation_needed': False}, 409),
        ({'acknowledge_unknown_status': False}, 409),
        ({'needs': {'mode': 'walking', 'confirmed': False}}, 422),
        ({'destination_id': 'air'}, 422),
        ({'destination_id': 'closed'}, 409),
        ({'destination_id': 'missing'}, 409),
        ({'destination_id': 'not-in-catalog'}, 404),
        ({'lat': 22}, 422),
    ],
)
def test_reference_rejects_unsafe_or_invalid_requests(public_client, change, expected):
    assert public_client.post('/api/public/route', json=body(**change)).status_code == expected


def test_unknown_accessibility_never_relaxed(public_client):
    r = public_client.post(
        '/api/public/route', json=body(needs={'mode': 'wheelchair', 'confirmed': True})
    )
    assert r.status_code == 409 and '不足' in r.json()['detail']['message']
    r = public_client.post(
        '/api/public/route', json=body(needs={'accessible_toilet': True, 'confirmed': True})
    )
    assert r.status_code == 409


def test_blocked_osm_edge_reroutes(public_client):
    repo = public_client.app.state.public_repository
    route = repo.reference_route(PublicRoute(**body()), blocked_edges={'ab'})
    assert route['edge_ids'] == ['ad', 'dc'] and route['distance_m'] == 280
    with pytest.raises(HTTPException) as blocked:
        repo.reference_route(PublicRoute(**body()), blocked_edges={'ab', 'ad'})
    assert blocked.value.status_code == 409


def test_no_graph_never_invents_straight_route(public_client):
    repo = public_client.app.state.public_repository
    (repo.directory / 'walking-graph.json.gz').unlink()
    r = public_client.post('/api/public/route', json=body())
    assert r.status_code == 503 and '不以直線' in r.json()['detail']['message']


@pytest.mark.parametrize(
    'tags,allowed',
    [
        ({'highway': 'motorway'}, False),
        ({'highway': 'trunk'}, False),
        ({'highway': 'residential'}, True),
        ({'highway': 'footway', 'access': 'private'}, False),
        ({'highway': 'footway', 'foot': 'no'}, False),
        ({'highway': 'cycleway'}, False),
        ({'highway': 'footway', 'area': 'yes'}, False),
        ({'highway': 'construction'}, False),
        ({'highway': 'footway', 'access': 'private', 'foot': 'yes'}, True),
    ],
)
def test_osm_pedestrian_access(tags, allowed):
    assert way_walkable(tags) is allowed


def test_osm_compression_preserves_curves_and_permissions():
    data = {
        'elements': [
            {'type': 'node', 'id': 1, 'lat': 25.03, 'lon': 121.53},
            {'type': 'node', 'id': 2, 'lat': 25.0305, 'lon': 121.5302},
            {'type': 'node', 'id': 3, 'lat': 25.031, 'lon': 121.53},
            {
                'type': 'way',
                'id': 7,
                'nodes': [1, 2, 3],
                'tags': {'highway': 'footway', 'name': '彎曲步道'},
            },
        ]
    }
    graph, _ = from_overpass(data)
    assert len(graph['nodes']) == 2 and len(graph['edges']) == 1
    g = WalkingGraph(graph)
    edge = graph['edges'][0]
    coords = g.geometry([edge['id']], [edge['a'], edge['b']])
    assert len(coords) == 3 and coords[1] == [121.5302, 25.0305]
    assert g.geometry([edge['id']], [edge['b'], edge['a']]) == list(reversed(coords))


def test_overpass_skeleton_does_not_erase_private_barrier():
    data = {
        'elements': [
            {'type': 'node', 'id': 1, 'lat': 25.03, 'lon': 121.53},
            {
                'type': 'node',
                'id': 2,
                'lat': 25.0305,
                'lon': 121.5302,
                'tags': {'access': 'private'},
            },
            {'type': 'node', 'id': 3, 'lat': 25.031, 'lon': 121.53},
            {'type': 'way', 'id': 7, 'nodes': [1, 2, 3], 'tags': {'highway': 'footway'}},
            {'type': 'node', 'id': 2, 'lat': 25.0305, 'lon': 121.5302},
        ]
    }
    graph, _ = from_overpass(data)
    assert not graph['edges']


def test_explicit_pedestrian_oneway():
    graph = {
        'nodes': [
            {'id': 'a', 'lat': 25.03, 'lon': 121.53},
            {'id': 'b', 'lat': 25.031, 'lon': 121.53},
        ],
        'edges': [
            {'id': 'x', 'a': 'a', 'b': 'b', 'length': 100, 'stairs': False, 'foot_direction': 'yes'}
        ],
    }
    g = WalkingGraph(graph)
    assert 'b' in g.dijkstra('a', Needs())[0]
    assert 'a' not in g.dijkstra('b', Needs())[0]


def test_normalization_does_not_accept_wrong_city_coordinate():
    assert valid_coordinate('22.4797', '120.913') is None
    assert valid_coordinate('25.03', '121.53') == (25.03, 121.53)
    assert canonical_address('臺北市大安區和平東路二段九十四號', '大安區') == canonical_address(
        '和平東路2段94號', '大安區'
    )
    assert canonical_name('臺北市大安區古亭國民小學') == canonical_name('古亭國小')


def test_osm_unknown_width_and_slope_fail_accessibility():
    edge = {'stairs': False, 'slope': None, 'width': None, 'surface_ok': True}
    assert edge_allowed(edge, Needs())
    assert not edge_allowed(edge, Needs(mode='wheelchair'))
