import copy

import pytest

from app.data import fixture
from app.models import Needs, Scenario
from app.routing import analysis, candidates, capacity_fits, edge_allowed, shortest_paths
from app.simulation import experiment, percentile


def test_dijkstra_uses_network_not_straight_line():
    data = fixture()
    routes = shortest_paths(data, 'n00', Needs())
    assert len(routes['n44']['edge_ids']) == 8
    lookup = {e['id']: e for e in data['edges']}
    assert (
        abs(
            routes['n44']['distance_m']
            - sum(lookup[e]['length'] for e in routes['n44']['edge_ids'])
        )
        < 0.1
    )


def test_unknown_surface_slope_and_narrow_paths_rejected():
    n = Needs(mode='wheelchair')
    edges = fixture()['edges']
    assert not edge_allowed(next(e for e in edges if e['stairs']), n)
    assert not edge_allowed(next(e for e in edges if e['slope'] is None), n)
    assert not edge_allowed(next(e for e in edges if e['width'] == 0.7), n)
    assert not edge_allowed(next(e for e in edges if e['surface_ok'] is None), n)


def test_hard_closures_never_outweighed_by_short_distance():
    data = fixture()
    choice, _ = candidates(data, 'n22', Needs(), blocked_edges={'e-n22-n23'}, extra=30)
    assert all('e-n22-n23' not in c['edge_ids'] for c in choice)
    assert all(c['shelter_id'] not in ('sD', 'sE', 'sF') for c in choice)


def test_accessible_capacity_is_reserved():
    s = fixture()['shelters'][0]
    assert capacity_fits(s, (21, 0), 1, 0)
    assert not capacity_fits(s, (22, 0), 1, 0)
    assert capacity_fits(s, (22, 0), 1, 1)
    assert not capacity_fits(s, (28, 6), 1, 1)


def test_unknown_capacity_not_filled():
    s = fixture()['shelters'][3]
    assert not capacity_fits(s, (0, 0), 1, 0)


def test_extra_minutes_is_hard_limit():
    data = fixture()
    choices, _ = candidates(data, 'n22', Needs(), extra=0)
    assert all(p['extra_minutes'] == 0 for p in choices)


def test_analysis_shared_supply_and_demand_conservation():
    data = fixture()
    a = analysis(data)
    assert all(r['assigned'] + r['unassigned'] == r['demand'] for r in a['areas'])
    for s in data['shelters']:
        if s['capacity'] is not None:
            assert a['load'].get(s['id'], 0) <= s['capacity'] - (s['accessible_capacity'] or 0)
    assert sum(r['assigned'] for r in a['areas']) == sum(a['load'].values())


def test_experiments_reproducible_and_input_not_mutated():
    data = fixture()
    snapshot = copy.deepcopy(data)
    params = Scenario(seed=42, repeats=3)
    assert experiment(data, params) == experiment(data, params)
    assert data == snapshot


@pytest.mark.parametrize('app_rate', [0, 0.1, 0.3, 0.6, 1])
def test_simulation_partition_conservation_and_known_blocks(app_rate):
    result = experiment(
        fixture(),
        Scenario(seed=11, repeats=2, app_rate=app_rate, blocked_rate=0.2, false_report_rate=0.2),
    )
    for row in result['rows']:
        assert row['assigned'] + row['unassigned'] == row['demand']
        assert row['assigned'] == sum(row['loads'].values())
        assert 0 <= row['danger_exposure_people'] <= row['assigned']
        assert row['constraint_violations'] == 0
        assert row['common_assigned_people'] <= row['assigned']
    for seed in [11, 12]:
        same = [r for r in result['rows'] if r['seed'] == seed]
        assert len({tuple(r['true_blocked_edges']) for r in same}) == 1
        assert len({tuple(r['observed_blocked_edges']) for r in same}) == 1
    if app_rate == 0:
        for seed in [11, 12]:
            same = [r for r in result['rows'] if r['seed'] == seed]
            assert same[0]['loads'] == same[1]['loads'] == same[2]['loads']


def test_full_app_capacity_and_zero_report_errors():
    r = experiment(
        fixture(),
        Scenario(
            population=1000,
            evacuation_rate=1,
            app_rate=1,
            blocked_rate=0,
            false_report_rate=0,
            accessibility_rate=0,
            repeats=1,
        ),
    )
    assert r['rows'][0]['over_capacity'] > 0
    for row in r['rows'][1:]:
        assert row['over_capacity'] == 0
        assert row['danger_exposure_people'] == 0
        assert row['unassigned'] > 0


def test_extreme_wheelchair_all_demand():
    r = experiment(
        fixture(), Scenario(accessibility_rate=1, app_rate=1, repeats=1, blocked_rate=0.5)
    )
    for row in r['rows']:
        assert row['general_success_rate'] is None
        assert row['accessible_group_assigned'] <= row['accessible_group_demand']


def test_percentile_empty_and_interpolation():
    assert percentile([]) is None
    assert percentile([2]) == 2
    assert percentile([0, 10]) == pytest.approx(9.5)


def test_experiment_api_csv_and_retrieval(client, guest):
    r = client.post(
        '/api/experiments', json={'seed': 1, 'repeats': 1, 'population': 20}, headers=guest
    )
    assert r.status_code == 200
    eid = r.json()['id']
    assert client.get('/api/experiments/' + eid).json() == r.json()
    csv = client.get('/api/experiments/' + eid + '/csv')
    assert csv.status_code == 200 and 'strategy' in csv.text and 'paired_mean_minutes' in csv.text
    assert client.get('/api/experiments/missing').status_code == 404
