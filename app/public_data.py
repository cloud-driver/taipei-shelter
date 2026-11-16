"""Normalizers for published Taipei shelter data. Missing data never becomes a fact."""

import csv
import io
import json
import re
import statistics
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from pathlib import Path

DISTRICTS = [
    '中正區',
    '大同區',
    '中山區',
    '松山區',
    '大安區',
    '萬華區',
    '信義區',
    '士林區',
    '北投區',
    '內湖區',
    '南港區',
    '文山區',
]
SOURCES = {
    'education': {
        'title': '臺北市可供避難收容處所一覽表',
        'agency': '臺北市教育局',
        'page': 'https://data.taipei/dataset/detail?id=aaf97773-3631-40e2-b3cc-da87bf2ce1d5',
        'url': 'https://data.taipei/api/frontstage/tpeod/dataset/resource.download?rid=4c92dbd4-d259-495a-8390-52628119a4dd',
        'published_version': '2026-09-14',
        'license': '政府資料開放授權條款第1版',
    },
    'nfa': {
        'title': '避難收容處所點位檔',
        'agency': '內政部消防署',
        'page': 'https://data.gov.tw/dataset/73242',
        'url': 'https://opdadm.moi.gov.tw/api/v1/no-auth/resource/api/dataset/ED6CF735-6C03-4573-A882-72C1BEC799CB/resource/54550E2F-4567-4C8F-BD2E-E54E9D0386B8/download',
        'published_version': '來源檔未提供觀測日期；另記下載時間',
        'license': '政府資料開放授權條款第1版',
    },
    'parks': {
        'title': '臺北市防災公園',
        'agency': '臺北市工務局公園處',
        'page': 'https://data.taipei/dataset/detail?id=5800ed81-4aa8-48c9-ae6b-e1e23a91a695',
        'url': 'https://parks.gov.taipei/parks/json/',
        'published_version': '資料平臺詮釋更新2026-07-22；API未提供逐筆日期',
        'license': '政府資料開放授權條款第1版',
    },
    'airraid': {
        'title': '115年臺北市防空疏散避難設施資料集',
        'agency': '臺北市警察局',
        'page': 'https://data.taipei/dataset/detail?id=70a6216e-3730-4d1d-b334-62fca2dd71cd',
        'url': 'https://data.taipei/api/frontstage/tpeod/dataset/resource.download?rid=3bd658e7-8e8a-446b-8df5-89b2b896ff97',
        'published_version': '2026-09清冊，2026-10-06上架',
        'license': '政府資料開放授權條款第1版',
    },
    'osm': {
        'title': 'OpenStreetMap 臺北與鄰區步行路網',
        'agency': 'OpenStreetMap contributors',
        'page': 'https://www.openstreetmap.org/copyright',
        'url': 'https://overpass.private.coffee/api/interpreter',
        'published_version': '見路網快照timestamp',
        'license': 'ODbL 1.0',
    },
}


def csv_rows(raw):
    # Download labels are not reliable. Detect ODS by ZIP signature.
    if raw.startswith(b'PK'):
        ns = {
            't': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
            'x': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
        }
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            root = ET.fromstring(archive.read('content.xml'))
        rows = []
        for row in root.findall('.//t:table/t:table-row', ns):
            cells = []
            for cell in row.findall('t:table-cell', ns):
                text = '\n'.join(''.join(p.itertext()) for p in cell.findall('x:p', ns)).strip()
                repeat = int(cell.get('{' + ns['t'] + '}number-columns-repeated', '1'))
                cells.extend([text] * min(repeat, 50))
            if any(cells):
                rows.append(cells)
        if not rows:
            raise ValueError('Published spreadsheet has no rows')
        headers = rows[0]
        return [{k: v for k, v in zip(headers, row, strict=False) if k} for row in rows[1:]]
    for encoding in ['utf-8-sig', 'cp950']:
        try:
            return list(csv.DictReader(io.StringIO(raw.decode(encoding))))
        except UnicodeDecodeError:
            continue
    raise ValueError('Unsupported published file encoding')


def number(value):
    value = str(value or '').replace(',', '').strip()
    try:
        n = int(value)
        return n if n >= 0 else None
    except ValueError:
        return None


def valid_coordinate(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
        return (
            (round(lat, 7), round(lon, 7))
            if 24.94 <= lat <= 25.23 and 121.44 <= lon <= 121.69
            else None
        )
    except (ValueError, TypeError):
        return None


def chinese_number(value):
    digits = {
        '零': 0,
        '〇': 0,
        '一': 1,
        '二': 2,
        '兩': 2,
        '三': 3,
        '四': 4,
        '五': 5,
        '六': 6,
        '七': 7,
        '八': 8,
        '九': 9,
    }
    if not any(c in value for c in '十百千'):
        return ''.join(str(digits[c]) for c in value)
    total, current = 0, 0
    for c in value:
        if c in digits:
            current = digits[c]
        else:
            total += (current or 1) * {'十': 10, '百': 100, '千': 1000}[c]
            current = 0
    return str(total + current)


def canonical_address(value, district=''):
    value = unicodedata.normalize('NFKC', str(value)).replace('台', '臺').replace('路', '路')
    value = re.sub(r'\s+', '', value)
    value = re.sub(
        r'[零〇一二三四五六七八九十百千兩]+(?=段|巷|弄|號)', lambda m: chinese_number(m[0]), value
    )
    value = value.replace('臺北市', '')
    for d in DISTRICTS:
        value = value.replace(d, '')
    if '號' in value:
        value = value.split('號')[0] + '號'
    return district + value


def canonical_name(value):
    value = unicodedata.normalize('NFKC', str(value)).replace('台', '臺')
    for text in [
        '臺北市立',
        '臺北市',
        '市立',
        '國立',
        '私立',
        '財團法人',
        '學校財團法人',
        '財團法人私立',
    ]:
        value = value.replace(text, '')
    for a, b in [
        ('國民小學', '國小'),
        ('國民中學', '國中'),
        ('高級中等學校', '高中'),
        ('高級中學', '高中'),
        ('高級工業職業學校', '高工'),
        ('高級商業職業學校', '高商'),
    ]:
        value = value.replace(a, b)
    for d in DISTRICTS:
        value = value.replace(d, '')
    value = re.sub(r'[（(].*?[）)]', '', value)
    return re.sub(r'[\s()（）·・]', '', value)


def make_catalog(raw_dir, pois=None):
    raw_dir = Path(raw_dir)
    issues = []
    items = []
    poi_index = defaultdict(list)
    for poi in pois or []:
        poi_index[canonical_name(poi['name'])].append(poi)
    education = [
        r
        for r in csv_rows((raw_dir / 'taipei_shelters.ods').read_bytes())
        if r.get('收容所編號', '').startswith('SA')
    ]
    national = [
        r
        for r in csv_rows((raw_dir / 'national_shelters.csv').read_bytes())
        if r.get('縣市及鄉鎮市區', '').startswith(('臺北市', '台北市'))
    ]
    air = csv_rows((raw_dir / 'airraid.csv').read_bytes())
    parks = json.loads((raw_dir / 'parks.json').read_text(encoding='utf-8-sig'))
    park_points = {
        canonical_name(p['pm_name']): valid_coordinate(p['pm_Latitude'], p['pm_Longitude'])
        for p in parks
    }
    address_coords = defaultdict(list)
    name_coords = defaultdict(list)
    for row in national:
        district = next((d for d in DISTRICTS if d in row['縣市及鄉鎮市區']), '')
        point = valid_coordinate(row.get('緯度'), row.get('經度'))
        if point:
            obj = {
                'point': point,
                'source': 'nfa',
                'method': '同區、同門牌核對消防署公開座標',
                'name': row['避難收容處所名稱'],
            }
            address_coords[canonical_address(row['避難收容處所地址'], district)].append(obj)
            name_coords[(district, canonical_name(row['避難收容處所名稱']))].append(obj)
        else:
            issues.append(
                {
                    'source': 'nfa',
                    'name': row['避難收容處所名稱'],
                    'reason': '座標不在臺北及緩衝範圍；未使用',
                }
            )
    for i, row in enumerate(air):
        district = next((d for d in DISTRICTS if d in row.get('地址', '')), '')
        if not district:
            district = next((d for d in DISTRICTS if d[:2] in row.get('轄管分局', '')), '')
        values = re.findall(r'-?\d+(?:\.\d+)?', row.get('緯經度', ''))
        point = valid_coordinate(*values[:2]) if len(values) >= 2 else None
        if not point and len(values) >= 2:
            point = valid_coordinate(values[1], values[0])
        sid = 'air-' + (row.get('電腦編號') or str(i + 1))
        items.append(
            {
                'id': sid,
                'name': row.get('建築物名稱') or '防空設施',
                'kind': 'airraid',
                'district': district,
                'village': row.get('村里別', ''),
                'address': row.get('地址', ''),
                'lat': point[0] if point else None,
                'lon': point[1] if point else None,
                'capacity': number(row.get('可容納人數')),
                'earthquake': False,
                'earthquake_role': 'not_applicable',
                'status': 'unknown',
                'accessible_facility': None,
                'entrance_verified': False,
                'source': 'airraid',
                'coordinate_source': 'airraid' if point else None,
                'coordinate_method': '警察局公布緯經度，非入口查核',
                'basement': row.get('地下樓層數', ''),
                'location_quality': 'published' if point else 'unlocated',
            }
        )
        if point:
            address_coords[canonical_address(row.get('地址', ''), district)].append(
                {
                    'point': point,
                    'source': 'airraid',
                    'method': '同門牌比對警察局公開座標（非地震入口查核）',
                }
            )
    for row in education:
        name = row.get('名稱(學校機關全銜)') or row.get('名稱', '')
        district = row['鄉鎮']
        address = row['門牌地址']
        key = canonical_address(address, district)
        matches = address_coords.get(key, [])
        if not matches:
            matches = name_coords.get((district, canonical_name(name)), [])
        point = None
        source = None
        method = '尚未取得可核對座標'
        if matches:
            # Prefer the shelter dataset over the building lookup. Reject conflicting points.
            best = [m for m in matches if m['source'] == 'nfa'] or matches
            lats = [m['point'][0] for m in best]
            lons = [m['point'][1] for m in best]
            if max(lats) - min(lats) < 0.001 and max(lons) - min(lons) < 0.001:
                point = (statistics.median(lats), statistics.median(lons))
                source = best[0]['source']
                method = best[0]['method']
            else:
                issues.append(
                    {
                        'source': 'education',
                        'id': row['收容所編號'],
                        'name': name,
                        'reason': '同地址座標分歧超過約100m，改列待核對',
                    }
                )
        if not point and park_points.get(canonical_name(name)):
            point = park_points[canonical_name(name)]
            source = 'parks'
            method = '同名比對公園處公布公園位置，非入口查核'
        if not point and pois:
            matches = poi_index.get(canonical_name(name), [])
            # Nodes and polygons of the same school are one cluster, not independent evidence.
            if matches:
                lats = [p['lat'] for p in matches]
                lons = [p['lon'] for p in matches]
                if max(lats) - min(lats) < 0.003 and max(lons) - min(lons) < 0.003:
                    point = (statistics.median(lats), statistics.median(lons))
                    source = 'osm'
                    method = 'OSM 同名設施匹配位置，非已查核入口'
        role = {'是': 'primary', '備用': 'backup'}.get(row.get('震災'), 'not_applicable')
        suspended = '暫停用' in name or '暫停用' in row.get('備考', '')
        items.append(
            {
                'id': row['收容所編號'],
                'name': name,
                'kind': 'shelter',
                'district': district,
                'village': row.get('村里', ''),
                'address': '臺北市' + district + address
                if not address.startswith(('臺北市', '台北市'))
                else address,
                'lat': point[0] if point else None,
                'lon': point[1] if point else None,
                'capacity': number(row.get('容納人數')),
                'maximum_capacity': number(row.get('最大容納人數')),
                'earthquake': role != 'not_applicable',
                'earthquake_role': role,
                'status': 'closed' if suspended else 'unknown',
                'notes': row.get('備考', ''),
                'suspended': suspended,
                'accessible_facility': {'是': True, '否': False}.get(row.get('無障礙設施')),
                'entrance_verified': False,
                'source': 'education',
                'coordinate_source': source,
                'coordinate_method': method,
                'location_quality': 'matched' if point else 'unlocated',
            }
        )
    park_districts = {
        '復興公園': '北投區',
        '士林官邸公園': '士林區',
        '外雙溪公園': '士林區',
        '榮星花園公園': '中山區',
        '大湖公園': '內湖區',
        '玉泉公園': '大同區',
        '民權公園A': '松山區',
        '青年公園': '萬華區',
        '二二八和平公園': '中正區',
        '大安森林公園': '大安區',
        '松德公園': '信義區',
        '南港公園': '南港區',
        '景華公園': '文山區',
    }
    for i, row in enumerate(parks):
        point = valid_coordinate(row.get('pm_Latitude'), row.get('pm_Longitude'))
        items.append(
            {
                'id': f'park-{i + 1:02}',
                'name': row['pm_name'],
                'kind': 'park',
                'district': park_districts.get(row['pm_name'], ''),
                'address': '',
                'lat': point[0] if point else None,
                'lon': point[1] if point else None,
                'capacity': number(row.get('di_capacity')),
                'earthquake': True,
                'earthquake_role': 'disaster_park',
                'status': 'unknown',
                'accessible_facility': None,
                'entrance_verified': False,
                'source': 'parks',
                'coordinate_source': 'parks',
                'coordinate_method': '公園處公布公園位置，非入口查核',
                'location_quality': 'published' if point else 'unlocated',
            }
        )
    # Keep every unique official identifier; collisions must be investigated, never overwritten.
    ids = [i['id'] for i in items]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate published identifiers')
    return (
        items,
        issues,
        {
            'education_rows': len(education),
            'national_taipei_rows': len(national),
            'parks': len(parks),
            'airraid_rows': len(air),
        },
    )
