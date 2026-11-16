"""Explicit refresh of public source caches; never downloads OSM map tiles."""

import argparse
import gzip
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.public_data import SOURCES, csv_rows

QUERY = """[out:json][timeout:180][maxsize:268435456];
(way["highway"](24.94,121.44,25.23,121.69);
nwr["amenity"~"^(school|university|college)$"](24.94,121.44,25.23,121.69);
);out body;>;out skel qt;"""


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--osm', action='store_true', help='Also make one bounded Overpass request (large download)'
    )
    parser.add_argument(
        '--accessibility',
        action='store_true',
        help='Fetch a bounded OSM wheelchair-tag snapshot (optional)',
    )
    parser.add_argument('--raw-dir', type=Path, default=root / '.local/public-raw')
    args = parser.parse_args()
    args.raw_dir.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for key, filename in [
        ('education', 'taipei_shelters.ods'),
        ('nfa', 'national_shelters.csv'),
        ('parks', 'parks.json'),
        ('airraid', 'airraid.csv'),
    ]:
        source = SOURCES[key]
        req = urllib.request.Request(
            source['url'],
            headers={'User-Agent': 'AnxingPublicData/1.0 (explicit local dataset refresh)'},
        )
        with urllib.request.urlopen(req, timeout=120) as response:
            raw = response.read()
        rows = json.loads(raw.decode('utf-8-sig')) if key == 'parks' else csv_rows(raw)
        if not rows:
            raise ValueError('Empty source: ' + key)
        target = args.raw_dir / filename
        temp = target.with_suffix(target.suffix + '.part')
        temp.write_bytes(raw)
        temp.replace(target)
        manifest[filename] = {
            'url': source['url'],
            'retrieved_at': datetime.now(UTC).isoformat(),
            'sha256': hashlib.sha256(raw).hexdigest(),
            'bytes': len(raw),
        }
        print(key, len(rows), 'rows', flush=True)
    (args.raw_dir / 'downloads.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    if args.osm:
        req = urllib.request.Request(
            SOURCES['osm']['url'],
            data=urllib.parse.urlencode({'data': QUERY}).encode(),
            headers={'User-Agent': 'AnxingPublicData/1.0'},
        )
        with urllib.request.urlopen(req, timeout=240) as response:
            raw = response.read()
        data = json.loads(raw)
        if data.get('remark') or not data.get('elements'):
            raise ValueError('Incomplete Overpass response; existing graph preserved')
        target = root / 'data/public/osm-overpass.json.gz'
        temp = target.with_suffix('.part')
        with gzip.open(temp, 'wb') as output:
            output.write(raw)
        temp.replace(target)
        print('OSM snapshot', data.get('osm3s', {}).get('timestamp_osm_base'), flush=True)
    if args.accessibility:
        query = '[out:json][timeout:60];(nwr["wheelchair"](24.94,121.44,25.23,121.69);nwr["toilets:wheelchair"](24.94,121.44,25.23,121.69););out center tags;'
        req = urllib.request.Request(
            SOURCES['osm']['url'],
            data=urllib.parse.urlencode({'data': query}).encode(),
            headers={'User-Agent': 'AnxingPublicData/1.0'},
        )
        with urllib.request.urlopen(req, timeout=90) as response:
            raw = response.read()
        data = json.loads(raw)
        if data.get('remark') or not data.get('elements'):
            raise ValueError('Incomplete accessibility response; existing snapshot preserved')
        target = root / 'data/public/osm-accessibility.json.gz'
        temp = target.with_suffix('.part')
        with gzip.open(temp, 'wb') as output:
            output.write(raw)
        temp.replace(target)
        print('Accessibility snapshot', data.get('osm3s', {}).get('timestamp_osm_base'))
    print('Next: python scripts/build_public_snapshot.py' + ('' if args.osm else ' --catalog-only'))
    print(
        'Review source versions, counts, unlocated.json and routes before replacing a deployed snapshot.'
    )


if __name__ == '__main__':
    main()
