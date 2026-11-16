"""Validate delivered real snapshot and one route per Taipei district, without network calls."""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.public_data import DISTRICTS, valid_coordinate
from app.public_map import PublicRepository, PublicRoute


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    started = time.perf_counter()
    repo = PublicRepository(root / 'data/public')
    catalog, meta = repo.load()
    assert len(catalog) == len({s['id'] for s in catalog}) == meta['catalog_count']
    assert set(s['district'] for s in catalog) == set(DISTRICTS)
    counts = Counter(s['kind'] for s in catalog)
    assert counts == meta['kind_counts']
    assert all(valid_coordinate(s['lat'], s['lon']) for s in catalog if s['lat'] is not None)
    assert sum(s['lat'] is None for s in catalog) == meta['unlocated_count']
    assert all(not s['earthquake'] for s in catalog if s['kind'] == 'airraid')
    assert all(s['status'] == 'closed' for s in catalog if s.get('suspended'))
    assert all(s['status'] in ('unknown', 'closed') and not s['entrance_verified'] for s in catalog)
    assert not any('phone' in s or 'contact' in s for s in catalog)
    access = repo.accessibility()
    assert access['total'] == meta['accessibility']['observations'] and not access['route_verified']
    assert (
        sum(bool(s.get('wheelmap_osm', {}).get('evidence')) for s in catalog)
        == meta['accessibility']['matched_catalog_records']
    )
    assert all(not s['wheelmap_osm']['route_verified'] for s in catalog)
    graph = repo.graph
    assert len(graph.nodes) == meta['osm']['node_count']
    assert len(graph.edges) == meta['osm']['edge_count']
    assert all(
        e['length'] > 0 and e['a'] in graph.nodes and e['b'] in graph.nodes
        for e in graph.edges.values()
    )
    routes = []
    for district in DISTRICTS:
        # Use an actual graph node near a published disaster park as the test origin.
        park = next(s for s in catalog if s['kind'] == 'park' and s['district'] == district)
        snaps = repo.snaps(park)
        assert snaps, (district, 'park has no nearby walking road')
        _, target = snaps[0]
        distances, previous = graph.dijkstra(
            target,
            PublicRoute(
                lat=park['lat'],
                lon=park['lon'],
                needs={'confirmed': True},
                shaking_stopped=True,
                evacuation_needed=True,
            ).needs,
            max_distance=350,
        )
        origin = max(distances, key=distances.get)
        point = graph.nodes[origin]
        result = repo.reference_route(
            PublicRoute(
                lat=point['lat'],
                lon=point['lon'],
                destination_id=park['id'],
                needs={'mode': 'walking', 'confirmed': True},
                shaking_stopped=True,
                evacuation_needed=True,
                acknowledge_unknown_status=True,
            )
        )
        assert result['shelter']['id'] == park['id'] and not result['allocation_created']
        assert result['edge_ids'] and len(result['geometry']['coordinates']) > 2
        assert all(e in graph.edges for e in result['edge_ids'])
        routes.append(
            {
                'district': district,
                'target': park['name'],
                'distance_m': result['distance_m'],
                'edges': len(result['edge_ids']),
                'coordinates': len(result['geometry']['coordinates']),
            }
        )
    result = {
        'status': 'PASS',
        'catalog_count': len(catalog),
        'kinds': dict(counts),
        'unlocated': meta['unlocated_count'],
        'accessibility': meta['accessibility'],
        'osm_timestamp': meta['osm']['timestamp'],
        'graph_nodes': len(graph.nodes),
        'graph_edges': len(graph.edges),
        'district_routes': routes,
        'elapsed_seconds': round(time.perf_counter() - started, 3),
    }
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
