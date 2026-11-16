"""Validated import boundary. Never infer unknown capacity or accessibility."""

from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .models import Model


class Node(Model):
    id: str = Field(min_length=1, max_length=40, pattern=r'^[a-zA-Z0-9_-]+$')
    name: str = Field(min_length=1, max_length=120)
    lat: float = Field(ge=24.9, le=25.3)
    lon: float = Field(ge=121.4, le=121.7)


class Edge(Model):
    id: str = Field(min_length=1, max_length=40, pattern=r'^[a-zA-Z0-9_-]+$')
    a: str
    b: str
    length: float = Field(gt=0, le=20000)
    stairs: bool
    slope: float | None = Field(ge=0, le=100)
    width: float | None = Field(ge=0, le=100)
    surface_ok: bool | None
    risk: float = Field(ge=0, le=1)


class Shelter(Model):
    id: str = Field(min_length=1, max_length=40, pattern=r'^[a-zA-Z0-9_-]+$')
    name: str = Field(min_length=1, max_length=120)
    node_id: str
    capacity: int | None = Field(ge=0, le=100000)
    accessible_capacity: int | None = Field(ge=0, le=100000)
    status: Literal['open', 'closed', 'unknown']
    entrance_step_free: bool | None
    accessible_toilet: bool | None
    space_accessible: bool | None
    disaster: Literal['earthquake', 'airraid', 'other']
    background: int = Field(0, ge=0, le=100000)
    background_accessible: int = Field(0, ge=0, le=100000)
    source: str = Field(min_length=1, max_length=500)
    version: str = Field(min_length=1, max_length=120)

    @model_validator(mode='after')
    def verify_capacity(self):
        if self.accessible_capacity is not None and (
            self.capacity is None or self.accessible_capacity > self.capacity
        ):
            raise ValueError(
                'accessible capacity requires known total capacity and cannot exceed it'
            )
        if self.background_accessible > self.background:
            raise ValueError('accessible background exceeds total background')
        return self


class Area(Model):
    id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    node_id: str
    population: int = Field(ge=1, le=100000)
    elderly: int = Field(ge=0, le=100000)
    area_km2: float = Field(gt=0, le=100)
    bounds: list[float] = Field(min_length=4, max_length=4)
    stat_period: str = Field(pattern=r'^(synthetic|\d{4}-(0[1-9]|1[0-2]))$')
    boundary_version: str = Field(min_length=1, max_length=120)

    @model_validator(mode='after')
    def verify(self):
        if self.elderly > self.population:
            raise ValueError('elderly population exceeds total')
        w, s, e, n = self.bounds
        if not (121.4 <= w < e <= 121.7 and 24.9 <= s < n <= 25.3):
            raise ValueError('invalid WGS84 bounds')
        return self


class Dataset(Model):
    nodes: list[Node] = Field(min_length=1, max_length=3000)
    edges: list[Edge] = Field(min_length=1, max_length=10000)
    shelters: list[Shelter] = Field(min_length=1, max_length=1000)
    areas: list[Area] = Field(min_length=1, max_length=1000)
    version: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=1000)
    drill: Literal[True]
    crs: Literal['EPSG:4326']

    @model_validator(mode='after')
    def links(self):
        for values in [self.nodes, self.edges, self.shelters, self.areas]:
            duplicates = [key for key, n in Counter(v.id for v in values).items() if n > 1]
            if duplicates:
                raise ValueError('duplicate identifiers: ' + ', '.join(duplicates[:10]))
        nodes = {n.id for n in self.nodes}
        for edge in self.edges:
            if edge.a not in nodes or edge.b not in nodes or edge.a == edge.b:
                raise ValueError('invalid edge endpoints: ' + edge.id)
        for entry in [*self.shelters, *self.areas]:
            if entry.node_id not in nodes:
                raise ValueError('unmatched node: ' + entry.id)
        if len({a.stat_period for a in self.areas}) > 1:
            raise ValueError('population statistical periods must be identical')
        return self


def read_dataset(path):
    path = Path(path)
    if path.stat().st_size > 10_000_000:
        raise ValueError('dataset exceeds 10 MB import limit')
    return Dataset.model_validate_json(path.read_text(encoding='utf-8')).model_dump()


def quality_report(data):
    adjacency = {n['id']: set() for n in data['nodes']}
    for e in data['edges']:
        adjacency[e['a']].add(e['b'])
        adjacency[e['b']].add(e['a'])
    unseen = set(adjacency)
    components = 0
    while unseen:
        components += 1
        stack = [unseen.pop()]
        while stack:
            for n in adjacency[stack.pop()] & unseen:
                unseen.remove(n)
                stack.append(n)
    s = data['shelters']
    count = len(s)
    return {
        'version': data['version'],
        'drill': True,
        'nodes': len(adjacency),
        'edges': len(data['edges']),
        'connected_components': components,
        'isolated_nodes': sorted(n for n, neighbors in adjacency.items() if not neighbors),
        'shelters': count,
        'unknown_capacity': sum(v['capacity'] is None for v in s),
        'known_capacity_coverage': sum(v['capacity'] is not None for v in s) / max(count, 1),
        'unknown_accessible_capacity': sum(v['accessible_capacity'] is None for v in s),
        'unknown_entrances': sum(v['entrance_step_free'] is None for v in s),
        'population_period': data['areas'][0]['stat_period'],
        'areas': len(data['areas']),
        'warning': '不連通與未知資料保留；未檢驗現地道路、入口、官方開設與可用容量。',
    }
