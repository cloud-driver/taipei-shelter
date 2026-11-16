"""Reproducible synthetic fixtures, deliberately unrelated to actual shelter capacity."""

from math import asin, cos, radians, sin, sqrt

VERSION = 'synthetic-daan-v1'
SOURCE = '程式產生的演練資料；非官方場所、道路或人口資料'


def distance(a, b):
    lat1, lat2 = radians(a['lat']), radians(b['lat'])
    dlat, dlon = lat2 - lat1, radians(b['lon'] - a['lon'])
    return 6371000 * 2 * asin(sqrt(sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2))


def fixture():
    nodes = [
        {
            'id': f'n{r}{c}',
            'name': f'演練街口 {r + 1}-{c + 1}',
            'lat': 25.020 + r * 0.002,
            'lon': 121.532 + c * 0.0024,
        }
        for r in range(5)
        for c in range(5)
    ]
    lookup = {n['id']: n for n in nodes}
    edges = []
    for r in range(5):
        for c in range(5):
            for dr, dc in [(0, 1), (1, 0)]:
                if r + dr > 4 or c + dc > 4:
                    continue
                a, b = f'n{r}{c}', f'n{r + dr}{c + dc}'
                edges.append(
                    {
                        'id': f'e-{a}-{b}',
                        'a': a,
                        'b': b,
                        'length': round(distance(lookup[a], lookup[b]), 2),
                        'stairs': (a, b) == ('n21', 'n22'),
                        'slope': None if (a, b) == ('n12', 'n13') else 3.0,
                        'width': 0.7 if (a, b) == ('n32', 'n33') else 1.5,
                        'surface_ok': None if (a, b) == ('n03', 'n04') else True,
                        'risk': 0.12 if r == 2 else 0.02,
                    }
                )
    shelters = []
    for sid, name, node, cap, acap, state, step, toilet, disaster in [
        ('sA', '演練場所 A・西側廣場', 'n10', 28, 6, 'open', True, True, 'earthquake'),
        ('sB', '演練場所 B・中央活動中心', 'n23', 36, 8, 'open', True, True, 'earthquake'),
        ('sC', '演練場所 C・東北運動場', 'n44', 42, 4, 'open', True, False, 'earthquake'),
        ('sD', '演練場所 D・待查核入口', 'n04', None, None, 'unknown', None, None, 'earthquake'),
        ('sE', '演練場所 E・關閉測試', 'n40', 20, 3, 'closed', True, True, 'earthquake'),
        ('sF', '演練防空地下室・不列入震災', 'n22', 50, None, 'open', False, False, 'airraid'),
    ]:
        shelters.append(
            {
                'id': sid,
                'name': name,
                'node_id': node,
                'capacity': cap,
                'accessible_capacity': acap,
                'status': state,
                'entrance_step_free': step,
                'accessible_toilet': toilet,
                'space_accessible': step,
                'disaster': disaster,
                'background': 0,
                'background_accessible': 0,
                'source': SOURCE,
                'version': VERSION,
            }
        )
    areas = [
        {
            'id': f'demo-{r}-{c}',
            'name': f'示意需求區 {r + 1}-{c + 1}',
            'node_id': f'n{r * 2}{c * 2}',
            'population': 120 + (r * 3 + c) * 23,
            'elderly': 25 + (r * 3 + c) * 3,
            'area_km2': 0.25,
            'bounds': [
                121.531 + c * 0.0048,
                25.019 + r * 0.004,
                121.5358 + c * 0.0048,
                25.023 + r * 0.004,
            ],
            'stat_period': 'synthetic',
            'boundary_version': VERSION,
        }
        for r in range(3)
        for c in range(3)
    ]
    return {
        'nodes': nodes,
        'edges': edges,
        'shelters': shelters,
        'areas': areas,
        'version': VERSION,
        'source': SOURCE,
        'drill': True,
        'crs': 'EPSG:4326',
    }
