"""Public (official) shelter catalogue and OSM reference routing under ``/api/public``.

The catalogue never implies a shelter is open or has space; routes are references only and
no capacity is allocated.
"""

import json
import threading
from contextlib import closing
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import Field

from .models import Model, Needs
from .osm_graph import WalkingGraph
from .public_data import DISTRICTS
from .routing import blocked_targets
from .security import client_ip


class PublicRoute(Model):
    lat: float = Field(ge=24.94, le=25.23)
    lon: float = Field(ge=121.44, le=121.69)
    destination_id: str | None = Field(None, max_length=80)
    needs: Needs
    shaking_stopped: bool
    evacuation_needed: bool
    acknowledge_unknown_status: bool = False
    helper_present: bool = False
    max_minutes: float = Field(60, ge=1, le=180)


class PublicRepository:
    def __init__(self, directory):
        self.directory = Path(directory)
        self._catalog = None
        self._graph = None
        self._meta = None
        self._accessibility = None
        self._lock = threading.RLock()
        self._snap_cache = {}

    def load(self):
        with self._lock:
            if self._catalog is None:
                try:
                    self._catalog = json.loads(
                        (self.directory / 'catalog.json').read_text(encoding='utf-8')
                    )
                    self._meta = json.loads(
                        (self.directory / 'sources.json').read_text(encoding='utf-8')
                    )
                except (OSError, ValueError) as error:
                    self._catalog = None
                    raise HTTPException(
                        503,
                        detail={'message': '尚未建立官方資料快照，請執行公開資料同步與建置腳本'},
                    ) from error
            return self._catalog, self._meta

    @property
    def graph(self):
        with self._lock:
            if self._graph is None:
                path = self.directory / 'walking-graph.json.gz'
                if not path.exists():
                    raise HTTPException(
                        503,
                        detail={
                            'message': 'OSM 本機路網尚未建立，無法計算道路導航；不以直線假裝路線'
                        },
                    )
                self._graph = WalkingGraph.load(path)
            return self._graph

    def accessibility(self, rating='all', q=''):
        with self._lock:
            if self._accessibility is None:
                try:
                    self._accessibility = json.loads(
                        (self.directory / 'accessibility.json').read_text(encoding='utf-8')
                    )
                except (OSError, ValueError) as error:
                    raise HTTPException(
                        503, detail={'message': '無障礙標記快照尚未建立'}
                    ) from error
            data = self._accessibility
        points = [
            p
            for p in data['items']
            if (rating == 'all' or p['rating'] == rating)
            and (not q or q.casefold() in p['name'].casefold())
        ]
        return {
            'meta': data['meta'],
            'items': points,
            'total': len(points),
            'route_verified': False,
        }

    def find(self, sid):
        items, _ = self.load()
        return next((s for s in items if s['id'] == sid), None)

    def matches(self, kind='earthquake', district='', q='', wheelchair='all'):
        items, _ = self.load()
        q = q.strip().casefold()
        return [
            s
            for s in items
            if (kind == 'all' or (s['earthquake'] if kind == 'earthquake' else s['kind'] == kind))
            and (not district or s['district'] == district)
            and (
                wheelchair == 'all'
                or s.get('wheelmap_osm', {}).get('rating', 'unknown') == wheelchair
            )
            and (not q or q in (s['name'] + s['address'] + s['id']).casefold())
        ]

    def snaps(self, shelter):
        sid = shelter['id']
        with self._lock:
            if sid not in self._snap_cache:
                self._snap_cache[sid] = self.graph.nearest(
                    shelter['lat'], shelter['lon'], max_distance=300, limit=12
                )
            return self._snap_cache[sid]

    def reference_route(self, body, blocked_edges=None, blocked_shelters=None):
        if not body.shaking_stopped or not body.evacuation_needed:
            raise HTTPException(
                409, detail={'message': '需先確認搖晃停止且需要撤離；搖晃時請就地保護'}
            )
        if not body.needs.confirmed or body.needs.mode == 'unsure':
            raise HTTPException(422, detail={'message': '請先確認本次行進模式與必要需求'})
        if body.needs.mode == 'assistance' and not body.helper_present:
            raise HTTPException(
                422, detail={'message': '需要協助時請先確認協助者同行；導航不是救援派遣'}
            )
        if not body.acknowledge_unknown_status:
            raise HTTPException(
                409,
                detail={
                    'message': '公開名冊不代表現場已開設；請確認理解此為路線參考，沒有容量配置或床位保證'
                },
            )
        if body.destination_id:
            selected = self.find(body.destination_id)
            if not selected:
                raise HTTPException(404, detail={'message': '找不到此官方名冊紀錄'})
            if not selected['earthquake']:
                raise HTTPException(
                    422,
                    detail={'message': '此紀錄不適用震災，防空／其他災害場所不能作為震災目的地'},
                )
            pool = [selected]
        else:
            pool = self.matches()
        pool = [
            s
            for s in pool
            if s['status'] != 'closed'
            and s['lat'] is not None
            and s['id'] not in (blocked_shelters or set())
        ]
        if body.needs.step_free or body.needs.accessible_toilet:
            # A published "accessible facility" flag is insufficient to certify an entrance or full route.
            pool = [
                s
                for s in pool
                if s.get('entrance_verified') is True
                and s.get('space_accessible') is True
                and (not body.needs.accessible_toilet or s.get('accessible_toilet') is True)
            ]
            if not pool:
                raise HTTPException(
                    409,
                    detail={
                        'message': '目前公開資料不足以確認入口與全程無障礙條件，無法提供符合需求的路線；不會改推薦樓梯或未知通道。請洽場所管理單位，急難請聯絡119。'
                    },
                )
        if not pool:
            raise HTTPException(
                409,
                detail={'message': '沒有可參考的震災適用場所（可能暫停用、座標待核對或災情封閉）'},
            )
        graph = self.graph
        starts = graph.nearest(body.lat, body.lon, body.needs, max_distance=150, limit=1)
        if not starts:
            raise HTTPException(
                409,
                detail={
                    'message': '起點150m內找不到符合條件的OSM步行路網；請在地圖上修正到實際戶外道路起點'
                },
            )
        start_gap, origin = starts[0]
        targets = {}
        candidates = []
        for s in pool:
            snaps = self.snaps(s)
            if snaps:
                # Use the geographically closest public graph node; never teleport to a remote component.
                gap, nid = snaps[0]
                targets.setdefault(nid, []).append((s, gap))
        distances, previous = graph.dijkstra(
            origin,
            body.needs,
            blocked_edges,
            targets=targets,
            max_distance=max(
                0, (body.max_minutes - body.needs.rest_minutes) * 60 * body.needs.speed
            ),
        )
        for nid, values in targets.items():
            if nid not in distances:
                continue
            for s, gap in values:
                candidates.append((distances[nid], s['id'], nid, s, gap))
        candidates.sort(key=lambda p: (p[0], p[1]))
        if not candidates:
            raise HTTPException(
                409,
                detail={
                    'message': '本機OSM路網內沒有可達的符合條件目的地；可能路網不連通、時間上限或已回報封閉。不放寬必要條件。'
                },
            )
        distance, _, target, shelter, end_gap = candidates[0]
        node_ids, edge_ids = graph.path(origin, target, previous)
        geometry = graph.geometry(edge_ids, node_ids)
        return {
            'mode': 'public-reference',
            'allocation_created': False,
            'shelter': shelter,
            'distance_m': round(distance, 1),
            'minutes': round(distance / body.needs.speed / 60 + body.needs.rest_minutes, 2),
            'speed_mps': body.needs.speed,
            'speed_assumed': body.needs.speed_mps is None,
            'origin_snap_m': round(start_gap, 1),
            'destination_snap_m': round(end_gap, 1),
            'origin_node': origin,
            'destination_node': target,
            'node_ids': node_ids,
            'edge_ids': edge_ids,
            'geometry': {'type': 'LineString', 'coordinates': geometry},
            'steps': graph.route_steps(edge_ids, node_ids),
            'alternatives': [
                {
                    'id': s['id'],
                    'name': s['name'],
                    'distance_m': round(d, 1),
                    'minutes': round(d / body.needs.speed / 60 + body.needs.rest_minutes, 2),
                }
                for d, _, _, s, _ in candidates[1:6]
            ],
            'osm_timestamp': graph.meta.get('timestamp'),
            'catalog_version': self.load()[1]['version'],
            'notice': '沿OSM步行道路計算的路線參考；不是已確認開設／安全路線，不會配置容量。',
            'warnings': [
                '估計時間只含路網行程與指定休息，未包含起終點未查核銜接段。',
                '終點是場所附近的路網節點；名冊座標與OSM位置不等於已查核入口。',
                '未取得即時現場道路、空位及開設資訊，請依官方疏散指示與現場情況判斷。',
            ],
            'attribution': '© OpenStreetMap contributors · ODbL 1.0',
        }


def make_router(repository, db, rate_attempt):
    router = APIRouter(prefix='/api/public', tags=['官方名冊與OSM'])
    Kind = Literal['all', 'earthquake', 'shelter', 'park', 'airraid']
    Rating = Literal['all', 'yes', 'limited', 'no', 'unknown']

    @router.get('/sources')
    def sources():
        _, meta = repository.load()
        return {
            **meta,
            'districts': DISTRICTS,
            'open_status_feed': 'unavailable',
            'operational_status': '名冊開設狀態未確認，僅提供路線參考',
        }

    @router.get('/accessibility')
    def accessibility(rating: Rating = 'all', q: str = Query('', max_length=120)):
        return repository.accessibility(rating, q)

    @router.get('/catalog')
    def catalog(
        kind: Kind = 'earthquake',
        district: str = '',
        q: str = Query('', max_length=120),
        wheelchair: Rating = 'all',
        offset: int = Query(0, ge=0),
        limit: int = Query(40, ge=1, le=200),
    ):
        if district and district not in DISTRICTS:
            raise HTTPException(422, detail={'message': '無效行政區'})
        items = repository.matches(kind, district, q, wheelchair)
        return {
            'total': len(items),
            'located': sum(s['lat'] is not None for s in items),
            'offset': offset,
            'limit': limit,
            'items': items[offset : offset + limit],
            'version': repository.load()[1]['version'],
        }

    @router.get('/points')
    def points(
        kind: Kind = 'earthquake',
        district: str = '',
        q: str = Query('', max_length=120),
        wheelchair: Rating = 'all',
    ):
        if district and district not in DISTRICTS:
            raise HTTPException(422, detail={'message': '無效行政區'})
        items = repository.matches(kind, district, q, wheelchair)
        return {
            'items': [
                {
                    k: s[k]
                    for k in [
                        'id',
                        'name',
                        'kind',
                        'district',
                        'lat',
                        'lon',
                        'earthquake',
                        'earthquake_role',
                        'status',
                    ]
                }
                for s in items
                if s['lat'] is not None
            ],
            'total': len(items),
            'unlocated': sum(s['lat'] is None for s in items),
        }

    @router.get('/catalog/{sid}')
    def detail(sid: str):
        item = repository.find(sid)
        if not item:
            raise HTTPException(404, detail={'message': '找不到官方名冊紀錄'})
        return item

    @router.get('/download.csv')
    def download_catalog():
        return FileResponse(
            repository.directory / 'shelters.csv',
            media_type='text/csv',
            filename='taipei-public-shelters.csv',
        )

    @router.post('/route')
    def route(body: PublicRoute, request: Request):
        rate_attempt('public-route:' + client_ip(request), 30)
        # sqlite3's own context manager only commits; closing() actually releases the handle.
        with closing(db.connect()) as conn:
            e, s = blocked_targets(conn)
        return repository.reference_route(body, e, s)

    return router
