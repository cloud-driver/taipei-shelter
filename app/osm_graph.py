"""Local OSM pedestrian graph: no live routing/geocoding service and no location uploads."""

import gzip
import heapq
import itertools
import json
import math
from collections import Counter, defaultdict


def haversine(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    h = (
        math.sin((b - a) / 2) ** 2
        + math.cos(a) * math.cos(b) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 12742000 * math.asin(min(1, math.sqrt(h)))


def parse_measure(value):
    import re

    if value is None:
        return None
    match = re.fullmatch(r'\s*(-?\d+(?:\.\d+)?)\s*(?:m|%|°)?\s*', str(value))
    return float(match[1]) if match else None


def way_walkable(tags):
    highway = tags.get('highway', '')
    if highway in ('motorway', 'motorway_link', 'trunk', 'trunk_link'):
        return tags.get('foot') in ('yes', 'designated', 'permissive')
    if highway not in {
        'primary',
        'primary_link',
        'secondary',
        'secondary_link',
        'tertiary',
        'tertiary_link',
        'residential',
        'living_street',
        'unclassified',
        'service',
        'pedestrian',
        'footway',
        'path',
        'steps',
        'track',
        'road',
        'cycleway',
    }:
        return False
    if tags.get('foot') in ('no', 'private', 'use_sidepath'):
        return False
    if tags.get('access') in ('private', 'no', 'customers') and tags.get('foot') not in (
        'yes',
        'designated',
        'permissive',
    ):
        return False
    if highway == 'cycleway' and tags.get('foot') not in ('yes', 'designated', 'permissive'):
        return False
    if tags.get('area') == 'yes' or tags.get('construction') or tags.get('proposed'):
        return False
    return True


def osm_edge_attributes(tags):
    slope = parse_measure(tags.get('incline'))
    width = parse_measure(tags.get('width') or tags.get('est_width'))
    surface = tags.get('surface')
    surface_ok = (
        True
        if surface in ('asphalt', 'paved', 'concrete', 'paving_stones', 'concrete:plates')
        else False
        if surface in ('sand', 'mud', 'ground', 'gravel', 'unpaved', 'cobblestone')
        else None
    )
    return {
        'stairs': tags.get('highway') == 'steps',
        'slope': abs(slope) if slope is not None else None,
        'width': width,
        'surface_ok': surface_ok,
        'wheelchair': tags.get('wheelchair'),
        'name': tags.get('name:zh') or tags.get('name') or tags.get('highway', '步道'),
        'highway': tags.get('highway'),
        'tunnel': tags.get('tunnel'),
        'foot_direction': tags.get('oneway:foot', 'no'),
    }


def edge_allowed(edge, needs):
    if not needs.can_stairs and edge['stairs']:
        return False
    if needs.step_free:
        if edge['stairs'] or edge.get('wheelchair') in ('no', 'limited'):
            return False
        if (
            edge.get('slope') is None
            or edge.get('width') is None
            or edge.get('surface_ok') is not True
        ):
            return False
        if edge['slope'] > needs.max_slope or edge['width'] < needs.min_width:
            return False
    return True


def _emit_edge(chain, way, start_index, attrs, nodes, edges):
    """Append one graph edge for a run of consecutive OSM nodes (``chain``) of ``way``.

    Loops and zero-length runs are skipped. Both end nodes are registered in ``nodes``.
    """
    if len(chain) < 2 or chain[0]['id'] == chain[-1]['id']:
        return
    length = sum(
        haversine(a['lat'], a['lon'], b['lat'], b['lon']) for a, b in itertools.pairwise(chain)
    )
    if length <= 0:
        return
    na, nb = chain[0], chain[-1]
    aid, bid = str(na['id']), str(nb['id'])
    nodes[aid] = {'id': aid, 'lat': na['lat'], 'lon': na['lon']}
    nodes[bid] = {'id': bid, 'lat': nb['lat'], 'lon': nb['lon']}
    edges.append(
        {
            'id': f'osm-w{way["id"]}-{start_index}',
            'a': aid,
            'b': bid,
            'length': round(length, 2),
            'way_id': way['id'],
            'geometry': [[n['lon'], n['lat']] for n in chain],
            **attrs,
        }
    )


def from_overpass(data):
    elements = data['elements']
    node_data = {}
    ways = []
    relations = []
    pois = []
    for element in elements:
        if element['type'] == 'node' and 'lat' in element:
            old = node_data.get(element['id'], {})
            node_data[element['id']] = {
                **old,
                **element,
                'tags': {**old.get('tags', {}), **element.get('tags', {})},
            }
            tags = element.get('tags', {})
            if tags.get('name'):
                pois.append(
                    {
                        'name': tags.get('name:zh') or tags['name'],
                        'lat': element['lat'],
                        'lon': element['lon'],
                        'osm_type': 'node',
                        'osm_id': element['id'],
                        'tags': tags,
                    }
                )
        elif element['type'] == 'way':
            ways.append(element)
        elif element['type'] == 'relation':
            relations.append(element)
    way_lookup = {w['id']: w for w in ways}
    ways = list(way_lookup.values())
    for way in ways:
        tags = way.get('tags', {})
        coords = [node_data[n] for n in way.get('nodes', []) if n in node_data]
        if (
            tags.get('name')
            and tags.get('amenity') in ('school', 'university', 'college')
            and coords
        ):
            pois.append(
                {
                    'name': tags.get('name:zh') or tags['name'],
                    'lat': sum(n['lat'] for n in coords) / len(coords),
                    'lon': sum(n['lon'] for n in coords) / len(coords),
                    'osm_type': 'way',
                    'osm_id': way['id'],
                    'tags': tags,
                }
            )
    for relation in relations:
        tags = relation.get('tags', {})
        coords = []
        for m in relation.get('members', []):
            if m['type'] == 'way':
                coords.extend(
                    node_data[n]
                    for n in way_lookup.get(m['ref'], {}).get('nodes', [])
                    if n in node_data
                )
            elif m['type'] == 'node' and m['ref'] in node_data:
                coords.append(node_data[m['ref']])
        if tags.get('name') and coords:
            pois.append(
                {
                    'name': tags.get('name:zh') or tags['name'],
                    'lat': sum(n['lat'] for n in coords) / len(coords),
                    'lon': sum(n['lon'] for n in coords) / len(coords),
                    'osm_type': 'relation',
                    'osm_id': relation['id'],
                    'tags': tags,
                }
            )
    nodes = {}
    edges = []
    usable = [way for way in ways if way_walkable(way.get('tags', {}))]
    shared = Counter(n for way in usable for n in way.get('nodes', []))
    for way in usable:
        tags = way.get('tags', {})
        refs = way.get('nodes', [])
        attrs = osm_edge_attributes(tags)
        coords = [node_data[n] for n in refs if n in node_data]
        if tags.get('name') and coords:
            pois.append(
                {
                    'name': tags.get('name:zh') or tags['name'],
                    'lat': sum(n['lat'] for n in coords) / len(coords),
                    'lon': sum(n['lon'] for n in coords) / len(coords),
                    'osm_type': 'way',
                    'osm_id': way['id'],
                    'tags': tags,
                }
            )
        chain = []
        start_index = 0

        for index, ref in enumerate(refs):
            n = node_data.get(ref)
            if not n:
                _emit_edge(chain, way, start_index, attrs, nodes, edges)
                chain = []
                continue
            nt = n.get('tags', {})
            forbidden = (
                nt.get('access') in ('no', 'private')
                or nt.get('foot') in ('no', 'private')
                or nt.get('barrier') in ('wall', 'fence')
            )
            if forbidden:
                _emit_edge(chain, way, start_index, attrs, nodes, edges)
                chain = []
                continue
            if not chain:
                start_index = index
            chain.append(n)
            split = (
                shared[ref] > 1
                or index == len(refs) - 1
                or len(chain) >= 80
                or bool(nt.get('barrier'))
                or bool(nt.get('entrance'))
            )
            if split and len(chain) > 1:
                _emit_edge(chain, way, start_index, attrs, nodes, edges)
                chain = [n]
                start_index = index
    # Repeated Overpass outputs may contain nodes twice (body + skeleton). Ways are only emitted once.
    timestamp = data.get('osm3s', {}).get('timestamp_osm_base', 'unknown')
    return {
        'nodes': list(nodes.values()),
        'edges': edges,
        'timestamp': timestamp,
        'license': 'ODbL-1.0',
        'attribution': '© OpenStreetMap contributors',
        'bounds': [121.44, 24.94, 121.69, 25.23],
        'source': 'https://overpass.private.coffee/api/interpreter',
    }, pois


class WalkingGraph:
    def __init__(self, data):
        self.meta = {k: v for k, v in data.items() if k not in ('nodes', 'edges')}
        self.nodes = {n['id']: n for n in data['nodes']}
        self.edges = {e['id']: e for e in data['edges']}
        self.adjacency = defaultdict(list)
        self.grid = defaultdict(list)
        for edge in self.edges.values():
            direction = edge.get('foot_direction', 'no')
            if direction != '-1':
                self.adjacency[edge['a']].append((edge['b'], edge))
            if direction not in ('yes', '1', 'true'):
                self.adjacency[edge['b']].append((edge['a'], edge))
        # 0.001-degree cells: 100m scale, bounded nearby lookup, no global nearest-place call.
        for n in self.nodes.values():
            self.grid[(math.floor(n['lat'] * 1000), math.floor(n['lon'] * 1000))].append(n['id'])
        self.components = {}
        self.component_sizes = {}
        # Weak connectivity, only used to avoid snapping to tiny isolated graph artifacts.
        weak = defaultdict(list)
        for e in self.edges.values():
            weak[e['a']].append(e['b'])
            weak[e['b']].append(e['a'])
        for nid in self.nodes:
            if nid in self.components:
                continue
            component = len(self.component_sizes)
            queue = [nid]
            self.components[nid] = component
            size = 0
            while queue:
                u = queue.pop()
                size += 1
                for v in weak[u]:
                    if v not in self.components:
                        self.components[v] = component
                        queue.append(v)
            self.component_sizes[component] = size

    @classmethod
    def load(cls, path):
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            return cls(json.load(f))

    def nearest(self, lat, lon, needs=None, max_distance=250, limit=8):
        cy, cx = math.floor(lat * 1000), math.floor(lon * 1000)
        cells = math.ceil(max_distance / 90) + 1
        choices = []
        for y in range(cy - cells, cy + cells + 1):
            for x in range(cx - cells, cx + cells + 1):
                for nid in self.grid.get((y, x), []):
                    n = self.nodes[nid]
                    d = haversine(lat, lon, n['lat'], n['lon'])
                    if d > max_distance:
                        continue
                    if self.component_sizes[self.components[nid]] < 20 and len(self.nodes) > 100:
                        continue
                    if needs and not any(edge_allowed(e, needs) for _, e in self.adjacency[nid]):
                        continue
                    choices.append((d, nid))
        return sorted(choices)[:limit]

    def dijkstra(self, origin, needs, blocked=None, targets=None, max_distance=30000):
        blocked = blocked or set()
        dist = {origin: 0}
        prev = {}
        queue = [(0, origin)]
        pending = set(targets) if targets is not None else None
        while queue:
            d, u = heapq.heappop(queue)
            if d != dist[u]:
                continue
            if d > max_distance:
                break
            if pending is not None:
                pending.discard(u)
                if not pending:
                    break
            for v, e in self.adjacency[u]:
                if e['id'] in blocked or not edge_allowed(e, needs):
                    continue
                nd = d + e['length']
                if nd < dist.get(v, float('inf')) and nd <= max_distance:
                    dist[v] = nd
                    prev[v] = (u, e['id'])
                    heapq.heappush(queue, (nd, v))
        return dist, prev

    def path(self, origin, target, prev):
        nodes = [target]
        edges = []
        u = target
        while u != origin:
            if u not in prev:
                raise ValueError('Target is not reachable')
            u, e = prev[u]
            nodes.append(u)
            edges.append(e)
        nodes.reverse()
        edges.reverse()
        return nodes, edges

    def route_steps(self, edge_ids, node_ids):
        steps = []
        for eid in edge_ids:
            e = self.edges[eid]
            name = {
                'footway': '步行通道',
                'pedestrian': '行人步道',
                'path': '步道',
                'steps': '樓梯',
                'service': '巷道',
                'residential': '住宅道路',
                'living_street': '人車共用道路',
                'unclassified': '未命名道路',
                'track': '產業道路',
                'cycleway': '自行車道（允許步行）',
            }.get(e['name'], e['name'])
            if steps and steps[-1]['name'] == name:
                steps[-1]['distance_m'] += e['length']
                steps[-1]['edge_ids'].append(eid)
            else:
                steps.append(
                    {
                        'name': name,
                        'distance_m': e['length'],
                        'edge_ids': [eid],
                        'stairs': e['stairs'],
                    }
                )
        for step in steps:
            step['distance_m'] = round(step['distance_m'], 1)
        return steps

    def geometry(self, edge_ids, node_ids):
        coordinates = []
        for eid, a in zip(edge_ids, node_ids, strict=False):
            e = self.edges[eid]
            segment = e.get('geometry') or [
                [self.nodes[e['a']]['lon'], self.nodes[e['a']]['lat']],
                [self.nodes[e['b']]['lon'], self.nodes[e['b']]['lat']],
            ]
            if a != e['a']:
                segment = list(reversed(segment))
            coordinates.extend(segment if not coordinates else segment[1:])
        if not coordinates:
            n = self.nodes[node_ids[0]]
            coordinates = [[n['lon'], n['lat']], [n['lon'], n['lat']]]
        return coordinates
