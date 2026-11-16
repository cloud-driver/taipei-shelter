"""Drill routing: hard constraints first, Dijkstra, then capacity-constrained group allocation.

Pipeline for one request (see ``candidates``):

1. drop edges that violate the group's needs (stairs, slope, width, unknown surface ...)
2. Dijkstra from the origin over the remaining graph
3. drop shelters that are closed, not for earthquakes, reported unsafe or lack accessibility
4. drop shelters more than ``extra`` minutes beyond the nearest feasible one
5. drop shelters without capacity for the whole group (families are never split)
6. rank by a normalised score of time, route risk and load
"""

import heapq
import json
from math import inf

from .models import Needs, Plan

# Weights of the ranking score (see proposal: a·time + b·risk + c·load).
SCORE_TIME_WEIGHT = 0.55
SCORE_RISK_WEIGHT = 0.2
SCORE_LOAD_WEIGHT = 0.25
SCORE_TIME_SCALE_MINUTES = 30

ACTIVE = ('proposed', 'accepted', 'arrived')


def blocked_targets(conn):
    edges, shelters = set(), set()
    for r in conn.execute(
        "SELECT target_type,target_id FROM reports WHERE status IN ('avoiding','confirmed')"
    ):
        (edges if r['target_type'] == 'edge' else shelters).add(r['target_id'])
    return edges, shelters


def edge_allowed(edge, needs):
    if not needs.can_stairs and edge['stairs']:
        return False
    if needs.step_free:
        if (
            edge['stairs']
            or edge['slope'] is None
            or edge['width'] is None
            or edge['surface_ok'] is not True
        ):
            return False
        if edge['slope'] > needs.max_slope or edge['width'] < needs.min_width:
            return False
    return True


def shortest_paths(data, origin, needs, blocked=None):
    blocked = blocked or set()
    adjacency = {n['id']: [] for n in data['nodes']}
    for edge in data['edges']:
        if edge['id'] not in blocked and edge_allowed(edge, needs):
            adjacency[edge['a']].append((edge['b'], edge))
            adjacency[edge['b']].append((edge['a'], edge))
    if origin not in adjacency:
        return {}
    distances, previous = {origin: 0}, {}
    queue = [(0, origin)]
    while queue:
        dist, node = heapq.heappop(queue)
        if dist > distances[node]:
            continue
        for nxt, edge in adjacency[node]:
            nd = dist + edge['length']
            if nd < distances.get(nxt, inf):
                distances[nxt] = nd
                previous[nxt] = (node, edge)
                heapq.heappush(queue, (nd, nxt))
    paths = {}
    for destination, length in distances.items():
        nodes, edges, current = [destination], [], destination
        while current != origin:
            current, edge = previous[current]
            nodes.append(current)
            edges.append(edge)
        paths[destination] = {
            'node_ids': list(reversed(nodes)),
            'edge_ids': [e['id'] for e in reversed(edges)],
            'distance_m': round(length, 1),
            'minutes': round(length / needs.speed / 60 + needs.rest_minutes, 3),
            'risk': round(sum(e['length'] * e['risk'] for e in edges) / max(length, 1), 4),
        }
    return paths


def shelter_allowed(s, needs, blocked=None):
    if s['disaster'] != 'earthquake' or s['status'] != 'open' or s['id'] in (blocked or set()):
        return False
    if needs.step_free and (
        s['entrance_step_free'] is not True or s['space_accessible'] is not True
    ):
        return False
    if needs.accessible_toilet and s['accessible_toilet'] is not True:
        return False
    return True


def capacity_fits(s, load, group, accessible):
    cap, acap = s['capacity'], s['accessible_capacity']
    if cap is None:
        return False
    total = load[0] + s['background'] + group
    a_total = load[1] + s['background_accessible'] + accessible
    if total > cap:
        return False
    if accessible and (acap is None or a_total > acap):
        return False
    if acap is not None:
        # Preserve verified accessible capacity even when general demand is high.
        if total - a_total > cap - acap:
            return False
    return True


def candidates(
    data,
    origin,
    needs,
    group=1,
    accessible=0,
    load=None,
    blocked_edges=None,
    blocked_shelters=None,
    capacity=True,
    extra=5,
):
    paths = shortest_paths(data, origin, needs, blocked_edges)
    possible, reasons = [], []
    for s in data['shelters']:
        if not shelter_allowed(s, needs, blocked_shelters):
            reasons.append(
                {
                    'shelter_id': s['id'],
                    'reason': '未確認開設、非震災適用、回報避開或設施不符／未知',
                }
            )
            continue
        if s['node_id'] not in paths:
            reasons.append({'shelter_id': s['id'], 'reason': '路網阻斷或沿途條件不符／未知'})
            continue
        possible.append((s, paths[s['node_id']]))
    shortest = min((p['minutes'] for _, p in possible), default=None)
    result = []
    for s, path in possible:
        if path['minutes'] > shortest + extra + 1e-8:
            reasons.append({'shelter_id': s['id'], 'reason': '超過可接受的額外步行時間'})
            continue
        current = (load or {}).get(s['id'], (0, 0))
        if capacity and not capacity_fits(s, current, group, accessible):
            reasons.append({'shelter_id': s['id'], 'reason': '總容量或保留無障礙容量不足／未知'})
            continue
        ratio = (current[0] + s['background'] + group) / max(s['capacity'] or 1, 1)
        if capacity:
            score = (
                SCORE_TIME_WEIGHT * (path['minutes'] / SCORE_TIME_SCALE_MINUTES)
                + SCORE_RISK_WEIGHT * path['risk']
                + SCORE_LOAD_WEIGHT * ratio
            )
        else:  # strategy A ("nearest shelter"): time only
            score = path['minutes']
        result.append(
            {
                'shelter_id': s['id'],
                'shelter_name': s['name'],
                **path,
                'score': round(score, 6),
                'extra_minutes': round(path['minutes'] - shortest, 3),
                'speed_mps': needs.speed,
                'speed_assumed': needs.speed_mps is None,
                'dataset_version': data['version'],
                'revision': data.get('revision', 1),
                'entrance_node': s['node_id'],
            }
        )
    result.sort(key=lambda p: (p['score'], p['minutes'], p['shelter_id']))
    return result, reasons


def invalidate(conn, data):
    """Release unsafe in-flight proposals; retain arrival counts and audit records."""
    edges, shelters = blocked_targets(conn)
    lookup = {s['id']: s for s in data['shelters']}
    edge_lookup = {e['id']: e for e in data['edges']}
    for row in conn.execute(
        "SELECT * FROM allocations WHERE status IN ('proposed','accepted')"
    ).fetchall():
        plan = Plan.model_validate_json(row['request'])
        route = json.loads(row['route'])
        shelter = lookup.get(row['shelter_id'])
        invalid = shelter is None or not shelter_allowed(shelter, plan.needs, shelters)
        invalid = invalid or any(
            e in edges or e not in edge_lookup or not edge_allowed(edge_lookup[e], plan.needs)
            for e in route['edge_ids']
        )
        if invalid:
            conn.execute("UPDATE allocations SET status='invalidated' WHERE id=?", (row['id'],))


def analysis(data, blocked_edges=None, blocked_shelters=None):
    """Shared-capacity population allocation, ordered by origin ID for reproducibility."""
    needs = Needs(confirmed=True)
    load, rows = {}, []
    for area in sorted(data['areas'], key=lambda a: a['id']):
        paths = shortest_paths(data, area['node_id'], needs, blocked_edges)
        times = [
            paths[s['node_id']]['minutes']
            for s in data['shelters']
            if shelter_allowed(s, needs, blocked_shelters) and s['node_id'] in paths
        ]
        demand = round(area['population'] * 0.3)
        assigned = 0
        ordered = sorted(
            (
                s
                for s in data['shelters']
                if shelter_allowed(s, needs, blocked_shelters)
                and s['node_id'] in paths
                and s['capacity'] is not None
            ),
            key=lambda s: (paths[s['node_id']]['minutes'], s['id']),
        )
        shortest = min(times) if times else 0
        for s in ordered:
            if paths[s['node_id']]['minutes'] > shortest + 5:
                continue
            n, a = load.get(s['id'], (0, 0))
            free = s['capacity'] - s['background'] - n
            if s['accessible_capacity'] is not None:
                free = min(
                    free,
                    s['capacity']
                    - s['accessible_capacity']
                    - (s['background'] - s['background_accessible'])
                    - (n - a),
                )
            take = min(demand - assigned, max(0, free))
            load[s['id']] = (n + take, a)
            assigned += take
            if assigned == demand:
                break
        rows.append(
            {
                **area,
                'density': round(area['population'] / area['area_km2'], 2),
                'elderly_ratio': round(area['elderly'] / area['population'], 4),
                'nearest_minutes': min(times) if times else None,
                'demand': demand,
                'assigned': assigned,
                'unassigned': demand - assigned,
            }
        )
    return {
        'areas': rows,
        'load': {s: n[0] for s, n in load.items()},
        'evacuation_rate': 0.3,
        'allocation_order': '區域代碼排序、依路程批次填入最近可用容量；保留無障礙名額、額外步行上限5分；排序可能影響結果',
        'source': data['source'],
        'version': data['version'],
        'drill': data['drill'],
    }
