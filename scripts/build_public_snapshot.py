"""Build reproducible, attributed local shelter and OSM snapshots from cached public files."""

import argparse
import csv
import gzip
import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.accessibility import attach_accessibility, normalize_accessibility
from app.osm_graph import from_overpass
from app.public_data import SOURCES, make_catalog


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument('--raw-dir', type=Path, default=root / '.local/public-raw')
    parser.add_argument('--output', type=Path, default=root / 'data/public')
    parser.add_argument('--osm', type=Path, default=root / 'data/public/osm-overpass.json.gz')
    parser.add_argument('--catalog-only', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    pois = []
    graph_meta = {'status': 'unavailable'}
    if args.catalog_only:
        graph_meta = json.loads((args.output / 'sources.json').read_text(encoding='utf-8'))['osm']
        with gzip.open(args.output / 'osm-places.json.gz', 'rt', encoding='utf-8') as f:
            pois = json.load(f)
    elif args.osm.exists():
        with gzip.open(args.osm, 'rt', encoding='utf-8') as f:
            osm = json.load(f)
        if osm.get('remark'):
            raise ValueError('Incomplete Overpass response: ' + osm['remark'])
        graph, pois = from_overpass(osm)
        if not graph['nodes'] or not graph['edges']:
            raise ValueError('Empty OSM graph')
        with gzip.open(args.output / 'walking-graph.json.gz', 'wt', encoding='utf-8') as f:
            json.dump(graph, f, separators=(',', ':'), ensure_ascii=False)
        graph_meta = {k: v for k, v in graph.items() if k not in ('nodes', 'edges')}
        graph_meta.update(
            status='ready', node_count=len(graph['nodes']), edge_count=len(graph['edges'])
        )
        with gzip.open(args.output / 'osm-places.json.gz', 'wt', encoding='utf-8') as f:
            json.dump(pois, f, ensure_ascii=False)
        print('OSM graph', graph_meta, flush=True)
    extra = args.output / 'osm-pois.json'
    if extra.exists():
        for element in json.loads(extra.read_text(encoding='utf-8')).get('elements', []):
            coords = element.get('center', element)
            tags = element.get('tags', {})
            if tags.get('name') and 'lat' in coords:
                pois.append(
                    {
                        'name': tags.get('name:zh') or tags['name'],
                        'lat': coords['lat'],
                        'lon': coords['lon'],
                    }
                )
    items, issues, counts = make_catalog(args.raw_dir, pois)
    accessibility_path = args.output / 'osm-accessibility.json.gz'
    if accessibility_path.exists():
        with gzip.open(accessibility_path, 'rt', encoding='utf-8') as f:
            accessibility_raw = json.load(f)
        if accessibility_raw.get('remark'):
            raise ValueError('Incomplete accessibility source')
        accessibility_timestamp = accessibility_raw['osm3s']['timestamp_osm_base']
        observations = normalize_accessibility(
            accessibility_raw['elements'], accessibility_timestamp
        )
        accessibility_scope = 'wheelchair與toilets:wheelchair標記，臺北及鄰近緩衝範圍'
    else:
        accessibility_timestamp = graph_meta.get('timestamp')
        observations = normalize_accessibility(
            [
                {
                    'type': p.get('osm_type'),
                    'id': p.get('osm_id'),
                    'lat': p['lat'],
                    'lon': p['lon'],
                    'tags': p.get('tags', {}),
                }
                for p in pois
            ],
            accessibility_timestamp,
        )
        accessibility_scope = '路網與學校快照內已存在的具名OSM設施，不是完整Wheelmap資料'
    accessibility_meta = attach_accessibility(items, observations)
    accessibility_meta.update(
        timestamp=accessibility_timestamp,
        scope=accessibility_scope,
        source='OpenStreetMap contributors；使用Wheelmap相同評級標籤，未使用Wheelmap專屬／合作夥伴資料',
        license='ODbL-1.0',
        reference='https://news.wheelmap.org/en/FAQ/',
        wheelmap_url='https://wheelmap.org/',
    )
    (args.output / 'accessibility.json').write_text(
        json.dumps(
            {'meta': accessibility_meta, 'items': observations},
            ensure_ascii=False,
            separators=(',', ':'),
        ),
        encoding='utf-8',
    )
    sources = {k: dict(v) for k, v in SOURCES.items()}
    for key, file in [
        ('education', 'taipei_shelters.ods'),
        ('nfa', 'national_shelters.csv'),
        ('parks', 'parks.json'),
        ('airraid', 'airraid.csv'),
    ]:
        path = args.raw_dir / file
        sources[key]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        sources[key]['bytes'] = path.stat().st_size
        sources[key]['downloaded_at'] = datetime.fromtimestamp(
            path.stat().st_mtime, UTC
        ).isoformat()
    now = datetime.now(UTC).isoformat()
    # Location matching is separate from operational availability and entrance verification.
    meta = {
        'snapshot_at': now,
        'version': 'taipei-public-' + now[:10],
        'sources': sources,
        'raw_counts': counts,
        'catalog_count': len(items),
        'kind_counts': dict(Counter(s['kind'] for s in items)),
        'earthquake_records': sum(s['earthquake'] for s in items),
        'located_earthquake_records': sum(s['earthquake'] and s['lat'] is not None for s in items),
        'closed_earthquake_records': sum(
            s['earthquake'] and s['status'] == 'closed' for s in items
        ),
        'unlocated_count': sum(s['lat'] is None for s in items),
        'coordinate_sources': dict(Counter(s['coordinate_source'] or 'unlocated' for s in items)),
        'osm': graph_meta,
        'accessibility': accessibility_meta,
        'issues': issues,
        'notes': [
            '筆數是名冊紀錄，不等於去重後獨立場地數；同一公園可能同時出現在收容與防災公園來源。',
            '收容名冊不表示現場已開設；未確認開設者只提供路線參考，不能配置容量。',
            '官方無障礙設施欄位不代表已查核入口與全程通行；未知條件不放寬。',
            '座標可能是建物／校園位置；終點停在附近OSM路網節點，入口與接駁段仍需確認。',
            '防空設施獨立圖層，不作為震災導航目的地。',
        ],
    }
    (args.output / 'catalog.json').write_text(
        json.dumps(items, ensure_ascii=False, separators=(',', ':')), encoding='utf-8'
    )
    (args.output / 'sources.json').write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    with (args.output / 'shelters.csv').open('w', encoding='utf-8-sig', newline='') as f:
        keys = [
            'id',
            'name',
            'kind',
            'district',
            'village',
            'address',
            'lat',
            'lon',
            'capacity',
            'earthquake',
            'earthquake_role',
            'status',
            'source',
            'coordinate_source',
            'coordinate_method',
            'entrance_verified',
        ]
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(items)
    print(
        json.dumps(
            {
                k: meta[k]
                for k in [
                    'raw_counts',
                    'catalog_count',
                    'kind_counts',
                    'earthquake_records',
                    'located_earthquake_records',
                    'unlocated_count',
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    missing = [s for s in items if s['lat'] is None]
    (args.output / 'unlocated.json').write_text(
        json.dumps(missing, ensure_ascii=False, indent=2), encoding='utf-8'
    )


if __name__ == '__main__':
    main()
