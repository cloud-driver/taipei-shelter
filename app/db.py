import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .data import fixture

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
 profile TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS profile_history (
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, version INTEGER NOT NULL,
 profile TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(user_id,version));
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
 user_id TEXT REFERENCES users(id) ON DELETE CASCADE, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS shelters (
 id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS allocations (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 request_id TEXT NOT NULL, request_hash TEXT NOT NULL, shelter_id TEXT NOT NULL REFERENCES shelters(id),
 group_size INTEGER NOT NULL CHECK(group_size>0), accessible_count INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('proposed','accepted','arrived','cancelled','expired','invalidated','left')),
 route TEXT NOT NULL, request TEXT NOT NULL, profile_version INTEGER,
 created_at REAL NOT NULL, expires_at REAL NOT NULL, UNIQUE(owner_id, request_id));
CREATE TABLE IF NOT EXISTS reports (
 id TEXT PRIMARY KEY, owner_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
 request_id TEXT NOT NULL, request_hash TEXT NOT NULL, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
 kind TEXT NOT NULL, description TEXT NOT NULL, observed_at REAL NOT NULL,
 status TEXT NOT NULL, created_at REAL NOT NULL, reviewed_at REAL, note TEXT,
 UNIQUE(owner_id,request_id));
CREATE TABLE IF NOT EXISTS report_audit (
 id INTEGER PRIMARY KEY, report_id TEXT NOT NULL REFERENCES reports(id),
 status TEXT NOT NULL, note TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS photos (
 report_id TEXT PRIMARY KEY REFERENCES reports(id) ON DELETE CASCADE,
 image BLOB NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS shelter_audit (
 id INTEGER PRIMARY KEY, shelter_id TEXT NOT NULL REFERENCES shelters(id),
 data TEXT NOT NULL, note TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, version INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS experiments (id TEXT PRIMARY KEY, params TEXT NOT NULL, result TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rate_limits (key TEXT PRIMARY KEY, count INTEGER NOT NULL, starts_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS allocation_load ON allocations(shelter_id,status,expires_at);
CREATE INDEX IF NOT EXISTS session_user ON sessions(user_id);
"""


class Database:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript(SCHEMA)
            if not conn.execute("SELECT 1 FROM meta WHERE key='dataset'").fetchone():
                data = fixture()
                conn.execute(
                    'INSERT INTO meta VALUES (?,?)',
                    ('dataset', json.dumps(data, ensure_ascii=False)),
                )
                conn.execute('INSERT INTO meta VALUES (?,?)', ('revision', '1'))
                for shelter in data['shelters']:
                    conn.execute(
                        'INSERT INTO shelters VALUES (?,?,?)',
                        (shelter['id'], json.dumps(shelter, ensure_ascii=False), time.time()),
                    )
                conn.commit()
        if os.name != 'nt':
            os.chmod(self.path, 0o600)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        return conn

    @contextmanager
    def transaction(self):
        conn = self.connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


def get_dataset(conn):
    data = json.loads(conn.execute("SELECT value FROM meta WHERE key='dataset'").fetchone()[0])
    data['shelters'] = [
        dict(json.loads(s['data']), updated_at=s['updated_at'])
        for s in conn.execute('SELECT * FROM shelters ORDER BY id')
    ]
    data['revision'] = int(
        conn.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
    )
    return data


def bump_revision(conn):
    conn.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")


def expire(conn, now=None):
    conn.execute(
        "UPDATE allocations SET status='expired' WHERE status IN ('proposed','accepted') AND expires_at<=?",
        (now or time.time(),),
    )


def loads(conn, exclude=None):
    # Arrived people remain counted until leave; arrival never creates fictitious new space.
    rows = conn.execute(
        'SELECT shelter_id, SUM(group_size) n, SUM(accessible_count) a FROM allocations '
        "WHERE status IN ('proposed','accepted','arrived') AND (? IS NULL OR id<>?) GROUP BY shelter_id",
        (exclude, exclude),
    )
    return {r['shelter_id']: (r['n'], r['a']) for r in rows}


def admin_secret(directory):
    env = os.environ.get('SHELTER_ADMIN_TOKEN')
    if env:
        if len(env) < 24:
            raise RuntimeError('SHELTER_ADMIN_TOKEN must have at least 24 characters')
        return env
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        os.chmod(directory, 0o700)
    path = directory / 'admin-token'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        value = path.read_text(encoding='utf-8').strip()
        if len(value) < 24:
            raise RuntimeError(
                'The saved admin token is incomplete; recover or replace it before starting'
            ) from None
        return value
    token = secrets.token_urlsafe(32)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(token + '\n')
    return token
