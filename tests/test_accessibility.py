import json

import pytest
from fastapi import HTTPException
from test_public_map import public_fixture

from app.accessibility import attach_accessibility, normalize_accessibility
from app.public_map import PublicRepository, PublicRoute


def observed(id=1, rating='yes', name='測試國小', **changes):
    return {
        'type': 'node',
        'id': id,
        'lat': 25.031,
        'lon': 121.531,
        'tags': {
            'name': name,
            'wheelchair': rating,
            'toilets:wheelchair': 'no',
            'phone': 'should not export',
        },
        **changes,
    }


def test_normalizer_keeps_ratings_source_date_and_toilet_independent():
    values = normalize_accessibility(
        [observed(), observed(2, 'limited'), observed(3, 'no'), observed(4, 'unexpected')],
        '2026-07-15',
    )
    assert [p['rating'] for p in values] == ['yes', 'limited', 'no', 'unknown']
    assert all(p['toilet_rating'] == 'no' and p['timestamp'] == '2026-07-15' for p in values)
    assert values[0]['url'] == 'https://www.openstreetmap.org/node/1'
    assert 'phone' not in json.dumps(values)
    assert values[0]['check_date'] is None


def test_duplicate_and_outside_points_are_not_added_twice():
    values = normalize_accessibility([observed(), observed(), observed(2, lat=22.5)], 'test')
    assert len(values) == 1


def test_facility_match_is_not_route_or_entrance_certification(tmp_path):
    public_fixture(tmp_path / 'public')
    path = tmp_path / 'public/catalog.json'
    items = json.loads(path.read_text())
    items[0]['name'] = '臺北市測試國民小學'
    observations = normalize_accessibility([observed()], 'test')
    summary = attach_accessibility(items, observations)
    assert summary['matched_catalog_records'] == 1
    assert items[0]['wheelmap_osm']['rating'] == 'yes'
    assert items[0]['wheelmap_osm']['toilet_rating'] == 'no'
    assert not items[0]['wheelmap_osm']['route_verified'] and not items[0]['entrance_verified']
    path.write_text(json.dumps(items))
    # A green venue must never bypass strict full-route checks.
    body = PublicRoute(
        lat=25.03,
        lon=121.53,
        destination_id='real',
        needs={'mode': 'wheelchair', 'confirmed': True},
        shaking_stopped=True,
        evacuation_needed=True,
        acknowledge_unknown_status=True,
    )
    with pytest.raises(HTTPException) as exc:
        PublicRepository(tmp_path / 'public').reference_route(body)
    assert exc.value.status_code == 409


def test_conflicting_or_distant_same_name_stays_unknown():
    items = [
        {
            'name': '測試國小',
            'kind': 'shelter',
            'lat': 25.031,
            'lon': 121.531,
            'entrance_verified': False,
        }
    ]
    attach_accessibility(items, normalize_accessibility([observed(), observed(2, 'no')], 'test'))
    assert (
        items[0]['wheelmap_osm']['rating'] == 'unknown' and items[0]['wheelmap_osm']['conflicting']
    )
    attach_accessibility(items, normalize_accessibility([observed(lat=25.10)], 'test'))
    assert not items[0]['wheelmap_osm']['evidence']


def test_airraid_is_not_matched_to_generic_osm_facilities():
    items = [{'name': '測試國小', 'kind': 'airraid', 'lat': 25.031, 'lon': 121.531}]
    assert (
        attach_accessibility(items, normalize_accessibility([observed()], 'test'))[
            'matched_catalog_records'
        ]
        == 0
    )


def test_accessibility_query_and_catalog_filter(tmp_path):
    directory = tmp_path / 'public'
    public_fixture(directory)
    repo = PublicRepository(directory)
    values = normalize_accessibility([observed(name='real'), observed(2, 'no', name='air')], 'test')
    items = json.loads((directory / 'catalog.json').read_text())
    summary = attach_accessibility(items, values)
    (directory / 'catalog.json').write_text(json.dumps(items))
    (directory / 'accessibility.json').write_text(json.dumps({'items': values, 'meta': summary}))
    assert repo.accessibility('yes')['total'] == 1
    assert repo.accessibility('no', 'air')['total'] == 1
    assert repo.accessibility('all', 'missing')['total'] == 0
    assert repo.matches(wheelchair='yes')[0]['id'] == 'real'
    assert len(repo.matches(wheelchair='unknown')) == 3
