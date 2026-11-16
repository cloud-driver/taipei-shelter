import io
import json
import os
import sqlite3
import subprocess
import sys
import time

import pytest
from PIL import Image
from pydantic import ValidationError

from app.data import fixture
from app.datasets import Dataset, quality_report, read_dataset


def photo_report(client, guest):
    return client.post(
        '/api/reports',
        headers=guest,
        json={
            'request_id': 'photo-report-request',
            'edge_id': 'e-n00-n01',
            'kind': 'debris',
            'description': '演練照片測試',
            'observed_at': time.time(),
        },
    ).json()['id']


def image_bytes():
    image = Image.new('RGB', (40, 30), 'green')
    out = io.BytesIO()
    exif = Image.Exif()
    exif[270] = 'PRIVATE METADATA'
    exif[315] = 'PRIVATE AUTHOR'
    image.save(out, 'JPEG', exif=exif)
    return out.getvalue()


def test_photo_private_and_metadata_removed(client, guest, admin):
    rid = photo_report(client, guest)
    r = client.put(
        f'/api/reports/{rid}/photo',
        content=image_bytes(),
        headers={**guest, 'Content-Type': 'image/jpeg'},
    )
    assert r.status_code == 200
    assert client.get(f'/api/reports/{rid}/photo').status_code == 401
    other = {'Authorization': 'Bearer ' + client.post('/api/guest').json()['token']}
    assert client.get(f'/api/reports/{rid}/photo', headers=other).status_code == 404
    for headers in [guest, admin]:
        r = client.get(f'/api/reports/{rid}/photo', headers=headers)
        assert r.status_code == 200
        assert b'PRIVATE' not in r.content
        with Image.open(io.BytesIO(r.content)) as image:
            assert not image.getexif()
    assert client.get('/api/admin/overview', headers=admin).json()['reports'][0]['has_photo'] == 1


def test_invalid_and_oversized_photo(client, guest):
    rid = photo_report(client, guest)
    assert (
        client.put(
            f'/api/reports/{rid}/photo',
            content=b'not image',
            headers={**guest, 'Content-Type': 'image/jpeg'},
        ).status_code
        == 422
    )
    assert (
        client.put(
            f'/api/reports/{rid}/photo',
            content=b'<svg/>',
            headers={**guest, 'Content-Type': 'image/svg+xml'},
        ).status_code
        == 415
    )
    assert (
        client.put(
            f'/api/reports/{rid}/photo',
            content=b'x' * (2 * 1024 * 1024 + 1),
            headers={**guest, 'Content-Type': 'image/png'},
        ).status_code
        == 413
    )


def test_photo_upload_requires_report_ownership(client, guest):
    rid = photo_report(client, guest)
    other = {'Authorization': 'Bearer ' + client.post('/api/guest').json()['token']}
    assert (
        client.put(
            f'/api/reports/{rid}/photo',
            content=image_bytes(),
            headers={**other, 'Content-Type': 'image/jpeg'},
        ).status_code
        == 404
    )


def test_deleting_account_deletes_photo(client, guest, admin):
    rid = photo_report(client, guest)
    client.put(
        f'/api/reports/{rid}/photo',
        content=image_bytes(),
        headers={**guest, 'Content-Type': 'image/jpeg'},
    )
    client.delete('/api/auth/me', headers=guest)
    assert client.get(f'/api/reports/{rid}/photo', headers=admin).status_code == 404


def test_streamed_body_size_limit(client):
    def chunks():
        for _ in range(40):
            yield b'x' * 1024

    assert client.post('/api/guest', content=chunks()).status_code == 413


def test_fixture_import_schema_and_quality(tmp_path):
    path = tmp_path / 'data.json'
    path.write_text(json.dumps(fixture()))
    data = read_dataset(path)
    report = quality_report(data)
    assert report['connected_components'] == 1
    assert report['unknown_capacity'] == 1
    assert report['unknown_accessible_capacity'] == 2
    assert report['population_period'] == 'synthetic'


@pytest.mark.parametrize(
    'bad',
    [
        'duplicate',
        'missing_node',
        'capacity',
        'period',
        'crs',
        'outside',
        'elderly',
        'nan',
        'background',
    ],
)
def test_invalid_dataset_rejected(bad):
    data = fixture()
    if bad == 'duplicate':
        data['nodes'].append(data['nodes'][0])
    if bad == 'missing_node':
        data['shelters'][0]['node_id'] = 'missing'
    if bad == 'capacity':
        data['shelters'][0]['accessible_capacity'] = 1000
    if bad == 'period':
        data['areas'][0]['stat_period'] = '2026-10'
    if bad == 'crs':
        data['crs'] = 'EPSG:3826'
    if bad == 'outside':
        data['nodes'][0]['lat'] = 40
    if bad == 'elderly':
        data['areas'][0]['elderly'] = 999999
    if bad == 'nan':
        data['edges'][0]['length'] = float('nan')
    if bad == 'background':
        data['shelters'][0]['background_accessible'] = 10
    with pytest.raises(ValidationError):
        Dataset.model_validate(data)


def test_cli_export_validate_import_backup_and_prune(tmp_path):
    env = {**os.environ, 'SHELTER_DATA_DIR': str(tmp_path / 'store')}

    def command(*args):
        return subprocess.run(
            [sys.executable, '-m', 'app.cli', *args], env=env, capture_output=True, text=True
        )

    path = tmp_path / 'demo.json'
    assert command('export-demo', str(path)).returncode == 0
    assert command('export-demo', str(path)).returncode == 1  # Never overwrite source fixtures.
    r = command('validate-data', str(path))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)['unknown_capacity'] == 1
    assert command('import-data', str(path)).returncode == 0
    backup = tmp_path / 'backup.sqlite3'
    assert command('backup', str(backup)).returncode == 0
    with sqlite3.connect(backup) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert command('backup', str(backup)).returncode == 1
    assert command('prune').returncode == 0


def test_cli_import_preserves_existing_history(client, guest, payload, tmp_path):
    client.post('/api/plans', json=payload, headers=guest)
    path = tmp_path / 'demo.json'
    path.write_text(json.dumps(fixture()))
    # Point the CLI to the existing application's directory and expected database name.

    store = tmp_path / 'existing'
    store.mkdir()
    backup = store / 'shelter.sqlite3'
    with client.app.state.db.connect() as source, sqlite3.connect(backup) as dest:
        source.backup(dest)
    r = subprocess.run(
        [sys.executable, '-m', 'app.cli', 'import-data', str(path)],
        env={**os.environ, 'SHELTER_DATA_DIR': str(store)},
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1 and '保留現有資料' in r.stderr
    with sqlite3.connect(backup) as conn:
        assert conn.execute('SELECT COUNT(*) FROM allocations').fetchone()[0] == 1
