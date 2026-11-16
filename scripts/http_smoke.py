"""Exercise a running backend. Own temporary visitor is cleaned in finally."""

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    args = parser.parse_args()
    base = args.url.rstrip('/')
    token = None
    checks = 0

    def request(path, method='GET', body=None, expected=200):
        nonlocal checks
        req = urllib.request.Request(
            base + path,
            method=method,
            headers={
                **({'Authorization': 'Bearer ' + token} if token else {}),
                **({'Content-Type': 'application/json'} if body is not None else {}),
            },
            data=json.dumps(body).encode() if body is not None else None,
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                status = response.status
                raw = response.read()
                data = (
                    json.loads(raw)
                    if 'application/json' in response.headers.get('content-type', '')
                    else raw.decode()
                )
        except urllib.error.HTTPError as error:
            status = error.code
            data = json.loads(error.read())
        assert status == expected, (path, status, data)
        checks += 1
        return data

    start = time.perf_counter()
    try:
        assert request('/api/health')['status'] == 'ok'
        assert request('/api/map')['drill'] is True
        public = request('/api/public/sources')
        assert len(public['districts']) == 12 and public['raw_counts']['education_rows'] > 400
        assert request('/api/public/accessibility')['route_verified'] is False
        assert request('/api/public/catalog?wheelchair=yes')['total'] > 0
        route = request(
            '/api/public/route',
            'POST',
            {
                'lat': 25.0422,
                'lon': 121.5354,
                'needs': {'mode': 'walking', 'confirmed': True},
                'shaking_stopped': True,
                'evacuation_needed': True,
                'acknowledge_unknown_status': True,
            },
        )
        assert route['allocation_created'] is False and len(route['geometry']['coordinates']) > 2
        assert all(e.startswith('osm-') for e in route['edge_ids'])
        request('/api/admin/overview', expected=403)
        token = request('/api/guest', 'POST')['token']
        payload = {
            'request_id': str(uuid.uuid4()),
            'origin': 'n22',
            'group_size': 1,
            'needs': {'confirmed': True},
            'shaking_stopped': True,
            'evacuation_needed': True,
            'max_extra_minutes': 30,
        }
        p = request('/api/plans', 'POST', payload, 201)
        assert request('/api/plans', 'POST', payload, 201)['id'] == p['id']
        assert (
            request('/api/plans/' + p['id'] + '/transition', 'POST', {'action': 'accept'})['status']
            == 'accepted'
        )
        assert (
            request('/api/plans/' + p['id'] + '/transition', 'POST', {'action': 'cancel'})['status']
            == 'cancelled'
        )
        e = request('/api/experiments', 'POST', {'seed': 123, 'repeats': 1, 'population': 30})
        assert len(e['rows']) == 3
        assert 'strategy' in request('/api/experiments/' + e['id'] + '/csv')
        a = request('/api/analysis')
        assert all(row['assigned'] + row['unassigned'] == row['demand'] for row in a['areas'])
        reach = request('/api/accessibility?origin=n22&mode=wheelchair')
        assert len(reach['nodes']) > 0
    finally:
        if token:
            request('/api/auth/logout', 'POST')
    print(
        json.dumps(
            {
                'status': 'PASS',
                'http_checks': checks,
                'elapsed_seconds': round(time.perf_counter() - start, 3),
            },
            ensure_ascii=False,
        )
    )


if __name__ == '__main__':
    main()
