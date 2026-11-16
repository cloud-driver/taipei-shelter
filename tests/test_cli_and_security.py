import json
import sqlite3
import time
from contextlib import closing
from types import SimpleNamespace

import pytest

from app import cli
from app.data import fixture
from app.security import client_ip, constant_time_equal, password_hash, verify_password

# ---------------------------------------------------------------- security helpers


def test_password_roundtrip_and_malformed_hash():
    stored = password_hash('correct horse battery')
    assert verify_password('correct horse battery', stored)
    assert not verify_password('wrong password!!', stored)
    # A corrupted stored value must fail closed instead of raising.
    assert not verify_password('anything', 'not-hex-salt:abc')
    assert not verify_password('anything', '')


def test_constant_time_equal_accepts_non_ascii():
    assert constant_time_equal('é-token', 'é-token')
    assert not constant_time_equal('é-token', 'e-token')


def test_client_ip_without_client():
    assert client_ip(SimpleNamespace(client=None)) == 'unknown'
    assert client_ip(SimpleNamespace(client=SimpleNamespace(host='10.0.0.1'))) == '10.0.0.1'


def test_non_ascii_admin_token_is_rejected_not_crashing(client):
    r = client.get('/api/admin/overview', headers={'X-Admin-Token': 'é'.encode('latin-1')})
    assert r.status_code == 403


def test_experiment_csv_without_rows_returns_404(client):
    with closing(client.app.state.db.connect()) as conn:
        conn.execute(
            'INSERT INTO experiments VALUES (?,?,?,?)', ('empty', '{}', '{"rows": []}', time.time())
        )
        conn.commit()
    assert client.get('/api/experiments/empty/csv').status_code == 404


# ---------------------------------------------------------------- operator CLI


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    directory = tmp_path / 'store'
    monkeypatch.setenv('SHELTER_DATA_DIR', str(directory))
    monkeypatch.setenv('SHELTER_ADMIN_TOKEN', 'cli-test-administrator-token-000')
    return directory


def test_cli_export_validate_import(data_dir, tmp_path, capsys):
    exported = tmp_path / 'demo.json'
    assert cli.main(['export-demo', str(exported)]) == 0
    assert json.loads(exported.read_text(encoding='utf-8')) == fixture()
    # export never overwrites an existing file
    assert cli.main(['export-demo', str(exported)]) == 1

    capsys.readouterr()
    assert cli.main(['validate-data', str(exported)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['connected_components'] == 1 and len(report['sha256']) == 64

    assert cli.main(['import-data', str(exported)]) == 0
    with closing(sqlite3.connect(data_dir / 'shelter.sqlite3')) as conn:
        stored = conn.execute("SELECT value FROM meta WHERE key='import_sha256'").fetchone()[0]
    assert stored == report['sha256']


def test_cli_import_refuses_database_with_history(data_dir, tmp_path, capsys):
    exported = tmp_path / 'demo.json'
    cli.main(['export-demo', str(exported)])
    db = cli.open_database(data_dir)
    with db.transaction() as conn:
        conn.execute(
            'INSERT INTO shelter_audit(shelter_id,data,note,created_at) VALUES (?,?,?,?)',
            ('sA', '{}', 'manual check', time.time()),
        )
    assert cli.main(['import-data', str(exported)]) == 1
    assert 'SHELTER_DATA_DIR' in capsys.readouterr().err


def test_cli_backup_is_consistent_and_never_overwrites(data_dir, tmp_path):
    output = tmp_path / 'backup.sqlite3'
    assert cli.main(['backup', str(output)]) == 0
    with closing(sqlite3.connect(output)) as conn:
        assert conn.execute('SELECT COUNT(*) FROM shelters').fetchone()[0] == len(
            fixture()['shelters']
        )
    assert cli.main(['backup', str(output)]) == 1


def test_prune_keeps_recent_and_arrived(data_dir):
    db = cli.open_database(data_dir)
    old = time.time() - 40 * 86400
    with db.transaction() as conn:
        conn.execute("INSERT INTO sessions VALUES ('s-old', 'h1', NULL, ?)", (old,))
        conn.execute("INSERT INTO sessions VALUES ('s-arrived', 'h2', NULL, ?)", (old,))
        for aid, owner, status in [
            ('a-left', 's-old', 'left'),
            ('a-arrived', 's-arrived', 'arrived'),
        ]:
            conn.execute(
                'INSERT INTO allocations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (aid, owner, 'req-' + aid, 'x', 'sA', 1, 0, status, '{}', '{}', None, old, old),
            )
    result = cli.prune(db)
    assert result['deleted_inactive_allocations'] == 1
    with closing(db.connect()) as conn:
        sessions = {r[0] for r in conn.execute('SELECT id FROM sessions')}
        allocations = {r[0] for r in conn.execute('SELECT id FROM allocations')}
    # Sessions that still own arrived people are kept so occupancy is not silently freed.
    assert sessions == {'s-arrived'}
    assert allocations == {'a-arrived'}
