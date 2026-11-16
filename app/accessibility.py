"""OSM accessibility observations using the tags also displayed by Wheelmap.

No Wheelmap partner records are scraped or treated as ODbL data.
"""

from collections import Counter

from .osm_graph import haversine
from .public_data import canonical_name, valid_coordinate

RATINGS = {'yes', 'limited', 'no'}


def normalize_accessibility(elements, timestamp):
    observations = {}
    for element in elements:
        tags = element.get('tags', {})
        point = element.get('center', element)
        coordinate = valid_coordinate(point.get('lat'), point.get('lon'))
        if not coordinate or not (tags.get('wheelchair') or tags.get('toilets:wheelchair')):
            continue
        kind = element.get('type')
        if kind not in ('node', 'way', 'relation'):
            continue
        sid = f'{kind}/{element["id"]}'
        rating = tags.get('wheelchair', 'unknown')
        toilet = tags.get('toilets:wheelchair', 'unknown')
        observations[sid] = {
            'id': sid,
            'name': tags.get('name:zh')
            or tags.get('name')
            or {'toilets': '公共廁所', 'elevator': '電梯', 'crossing': '行人穿越點'}.get(
                tags.get('amenity') or tags.get('highway'), '未命名OSM設施'
            ),
            'lat': coordinate[0],
            'lon': coordinate[1],
            'rating': rating if rating in RATINGS else 'unknown',
            'toilet_rating': toilet if toilet in RATINGS else 'unknown',
            'description': tags.get('wheelchair:description:zh')
            or tags.get('wheelchair:description', ''),
            'category': tags.get('amenity')
            or tags.get('highway')
            or tags.get('leisure')
            or 'facility',
            'url': 'https://www.openstreetmap.org/' + sid,
            'timestamp': timestamp,
            'check_date': tags.get('check_date:wheelchair') or tags.get('survey:date') or None,
            'source': 'OpenStreetMap contributors',
            'license': 'ODbL-1.0',
        }
    return list(observations.values())


def attach_accessibility(catalog, observations):
    by_name = {}
    for point in observations:
        name = canonical_name(point['name'])
        if name and not point['name'].startswith('未命名'):
            by_name.setdefault(name, []).append(point)
    matched = 0
    for shelter in catalog:
        evidence = []
        if shelter['kind'] != 'airraid' and shelter['lat'] is not None:
            evidence = [
                point
                for point in by_name.get(canonical_name(shelter['name']), [])
                if haversine(shelter['lat'], shelter['lon'], point['lat'], point['lon']) <= 400
            ]
        values = {point['rating'] for point in evidence if point['rating'] != 'unknown'}
        toilets = {
            point['toilet_rating'] for point in evidence if point['toilet_rating'] != 'unknown'
        }
        shelter['wheelmap_osm'] = {
            'rating': next(iter(values)) if len(values) == 1 else 'unknown',
            'toilet_rating': next(iter(toilets)) if len(toilets) == 1 else 'unknown',
            'conflicting': len(values) > 1 or len(toilets) > 1,
            'evidence': evidence,
            'match_method': '同名且距離400m內的OSM設施；不是入口／全程查核'
            if evidence
            else '沒有可靠匹配的OSM評級',
            'route_verified': False,
        }
        matched += bool(evidence)
    return {
        'observations': len(observations),
        'matched_catalog_records': matched,
        'ratings': dict(Counter(point['rating'] for point in observations)),
        'catalog_ratings': dict(
            Counter(s['wheelmap_osm']['rating'] for s in catalog if s['kind'] != 'airraid')
        ),
    }
