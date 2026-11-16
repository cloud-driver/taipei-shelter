import copy
import random
import statistics

from .models import Needs
from .routing import candidates


def percentile(values, q=0.95):
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] * (hi - k) + ordered[hi] * (k - lo) if hi != lo else ordered[lo]


def weighted_times(assignments, common=None):
    return [
        p['minutes']
        for gid, p in assignments.items()
        if common is None or gid in common
        for _ in range(p['size'])
    ]


def run_once(data, params, seed):
    rng = random.Random(seed)
    demand = round(params.population * params.daytime_multiplier * params.evacuation_rate)
    # Independent ground truth and observed reports. C only knows observed blocks.
    truth = {e['id'] for e in data['edges'] if rng.random() < params.blocked_rate}
    observed = {e for e in sorted(truth) if rng.random() < 0.8}
    false = {
        e['id']
        for e in data['edges']
        if e['id'] not in truth and rng.random() < params.false_report_rate
    }
    observed |= false
    groups = []
    remaining = demand
    while remaining:
        size = min(rng.randint(1, 4), remaining)
        accessible = rng.random() < params.accessibility_rate
        groups.append(
            {
                'id': len(groups),
                'size': size,
                'accessible': 1 if accessible else 0,
                'origin': rng.choice(data['nodes'])['id'],
                'participates': rng.random() < params.app_rate * params.acceptance_rate,
                'needs': Needs(mode='wheelchair' if accessible else 'walking', confirmed=True),
            }
        )
        remaining -= size
    outputs, assignments_all = [], []
    for strategy in ['A', 'B', 'C']:
        # Nonparticipants are assigned once, with the same nearest behavior across all strategies.
        load = {s['id']: (0, 0) for s in data['shelters']}
        assignments, unassigned = {}, 0
        ordered = [g for g in groups if not g['participates']] + [
            g for g in groups if g['participates']
        ]
        for g in ordered:
            managed = g['participates'] and strategy != 'A'
            choices, _ = candidates(
                data,
                g['origin'],
                g['needs'],
                g['size'],
                g['accessible'],
                load=load,
                blocked_edges=observed if managed and strategy == 'C' else set(),
                capacity=managed,
                extra=params.max_extra_minutes if managed else 1000,
            )
            if not choices:
                unassigned += g['size']
                continue
            p = choices[0]
            sid = p['shelter_id']
            n, a = load[sid]
            load[sid] = n + g['size'], a + g['accessible']
            assignments[g['id']] = {
                **p,
                'size': g['size'],
                'accessible': g['accessible'],
                'managed': managed,
            }
        over = sum(
            max(0, load[s['id']][0] + s['background'] - s['capacity'])
            for s in data['shelters']
            if s['capacity'] is not None
        )
        over_sites = sum(
            load[s['id']][0] + s['background'] > s['capacity']
            for s in data['shelters']
            if s['capacity'] is not None
        )
        exposure = sum(p['size'] for p in assignments.values() if set(p['edge_ids']) & truth)
        a_demand = sum(g['size'] for g in groups if g['accessible'])
        a_success = sum(p['size'] for p in assignments.values() if p['accessible'])
        general_demand = demand - a_demand
        times = weighted_times(assignments)
        outputs.append(
            {
                'strategy': strategy,
                'seed': seed,
                'demand': demand,
                'assigned': demand - unassigned,
                'unassigned': unassigned,
                'unassigned_rate': unassigned / max(demand, 1),
                'over_capacity': over,
                'overloaded_shelters': over_sites,
                'danger_exposure_people': exposure,
                'mean_minutes': statistics.mean(times) if times else None,
                'p95_minutes': percentile(times),
                'accessible_group_demand': a_demand,
                'accessible_group_assigned': a_success,
                'accessible_success_rate': a_success / a_demand if a_demand else None,
                'general_success_rate': (demand - unassigned - a_success) / general_demand
                if general_demand
                else None,
                'loads': {sid: n[0] for sid, n in load.items()},
                'assigned_group_ids': sorted(assignments),
                'constraint_violations': sum(
                    1
                    for p in assignments.values()
                    if strategy == 'C' and p['managed'] and set(p['edge_ids']) & observed
                ),
                'true_blocked_edges': sorted(truth),
                'observed_blocked_edges': sorted(observed),
                'false_report_edges': sorted(false),
            }
        )
        assignments_all.append(assignments)
    common = set.intersection(*(set(a) for a in assignments_all))
    for out, assignments in zip(outputs, assignments_all, strict=True):
        paired = weighted_times(assignments, common)
        out['common_assigned_people'] = len(paired)
        out['paired_mean_minutes'] = statistics.mean(paired) if paired else None
        out['paired_p95_minutes'] = percentile(paired)
        baseline = assignments_all[0]
        extra = [
            assignments[g]['minutes'] - baseline[g]['minutes']
            for g in common
            for _ in range(assignments[g]['size'])
        ]
        out['paired_extra_minutes'] = statistics.mean(extra) if extra else None
    return outputs


def experiment(data, params):
    data = copy.deepcopy(data)
    # Analysis is isolated from live app reservations; explicitly retain scenario backgrounds.
    rows = [row for i in range(params.repeats) for row in run_once(data, params, params.seed + i)]
    metrics = [
        'unassigned_rate',
        'over_capacity',
        'danger_exposure_people',
        'paired_mean_minutes',
        'paired_p95_minutes',
        'paired_extra_minutes',
        'accessible_success_rate',
        'general_success_rate',
    ]
    summary = []
    for strategy in ['A', 'B', 'C']:
        same = [r for r in rows if r['strategy'] == strategy]
        item = {'strategy': strategy}
        for key in metrics:
            values = [r[key] for r in same if r[key] is not None]
            item[key] = {
                'mean': statistics.mean(values) if values else None,
                'min': min(values) if values else None,
                'max': max(values) if values else None,
            }
        summary.append(item)
    return {
        'params': params.model_dump(),
        'rows': rows,
        'summary': summary,
        'data_version': data['version'],
        'data_revision': data.get('revision', 1),
        'algorithm_version': 'group-greedy-v1',
        'drill': True,
        'assumptions': [
            '人口、場所、路網皆為合成演練資料；不是全市實測分析。',
            'A 最近場所不考慮容量／災情；B 對接受建議者限制容量；C 再避開收到的災情。',
            '未使用或未接受建議者採 A；三策略使用同一批互斥人群，先計入非使用者負載。',
            '真實障礙由種子生成；模擬回報涵蓋其中 80%，另產生假回報；暴露依真值計算。',
            '共同安置者時間以三策略皆成功的相同群組、逐人加權計算。',
            '無障礙成功率以含無障礙成員的整組人數計算，家庭不拆分。',
            '日間倍率為假設；無排隊、人流動態、真實通知延遲或救援成效模型。',
            '保留無障礙名額可能增加一般需求拒絕率；範圍為多種子最小至最大，非信賴區間。',
        ],
    }
