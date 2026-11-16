"""Operator commands; no implicit deletion or network data import.

Usage::

    python -m app.cli admin-token
    python -m app.cli export-demo demo.json
    python -m app.cli validate-data data/demo.json
    python -m app.cli import-data data/demo.json      # only into an empty data directory
    python -m app.cli backup backup.sqlite3
    python -m app.cli prune                           # apply the 30-day retention policy

The data directory is ``$SHELTER_DATA_DIR`` (default: ``.local/`` in the project root).
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

from .data import fixture
from .datasets import quality_report, read_dataset
from .db import Database, admin_secret, bump_revision

RETENTION_DAYS = 30
RATE_LIMIT_RETENTION = 24 * 3600


def data_directory() -> Path:
    default = Path(__file__).resolve().parent.parent / '.local'
    return Path(os.environ.get('SHELTER_DATA_DIR', default))


def open_database(directory: Path) -> Database:
    return Database(directory / 'shelter.sqlite3')


def cmd_admin_token(args, directory):
    print(admin_secret(directory))


def cmd_export_demo(args, directory):
    path = Path(args.output)
    with path.open('x', encoding='utf-8') as f:  # 'x' refuses to overwrite an existing file
        f.write(json.dumps(fixture(), ensure_ascii=False, indent=2) + '\n')
    print(path.resolve())


def validated_dataset(path):
    """Parse and validate a dataset file; return ``(data, quality_report)``."""
    data = read_dataset(path)
    report = quality_report(data)
    report['sha256'] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return data, report


def cmd_validate_data(args, directory):
    _, report = validated_dataset(args.path)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_import_data(args, directory):
    data, report = validated_dataset(args.path)
    db = open_database(directory)
    with db.transaction() as conn:
        has_history = any(
            conn.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone()
            for table in ('allocations', 'reports', 'shelter_audit')
        )
        if has_history:
            raise ValueError(
                '資料庫已有導航、災情或場所查核紀錄。請指定新的 SHELTER_DATA_DIR，以保留現有資料。'
            )
        conn.execute('DELETE FROM shelters')
        for shelter in data['shelters']:
            conn.execute(
                'INSERT INTO shelters VALUES (?,?,?)',
                (shelter['id'], json.dumps(shelter, ensure_ascii=False), time.time()),
            )
        conn.execute(
            "UPDATE meta SET value=? WHERE key='dataset'", (json.dumps(data, ensure_ascii=False),)
        )
        conn.execute(
            'INSERT OR REPLACE INTO meta VALUES (?,?)', ('import_sha256', report['sha256'])
        )
        bump_revision(conn)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_backup(args, directory):
    output = Path(args.output)
    if output.exists():
        raise ValueError('備份目的檔已存在，請使用新的檔名')
    db = open_database(directory)
    # closing() is required: sqlite3's own context manager commits but never closes.
    with closing(db.connect()) as source, closing(sqlite3.connect(output)) as target:
        source.backup(target)
    if os.name != 'nt':
        os.chmod(output, 0o600)
    print(output.resolve())


def prune(db: Database, now: float | None = None) -> dict:
    """Apply the retention policy. Confirmed road closures are never cleared here."""
    now = time.time() if now is None else now
    cutoff = now - RETENTION_DAYS * 86400
    with db.transaction() as conn:
        deleted = conn.execute(
            'DELETE FROM allocations WHERE created_at<? '
            "AND status IN ('cancelled','expired','invalidated','left')",
            (cutoff,),
        ).rowcount
        conn.execute('DELETE FROM photos WHERE created_at<?', (cutoff,))
        redacted = conn.execute(
            "UPDATE reports SET owner_id=NULL, description='已依保存期限移除內容' WHERE created_at<?",
            (cutoff,),
        ).rowcount
        conn.execute(
            'DELETE FROM profile_history WHERE created_at<? '
            'AND (user_id,version) NOT IN (SELECT id,version FROM users)',
            (cutoff,),
        )
        conn.execute('DELETE FROM rate_limits WHERE starts_at<?', (now - RATE_LIMIT_RETENTION,))
        # Keep expired sessions that still own arrivals; an operator must reconcile occupants.
        conn.execute(
            'DELETE FROM sessions WHERE expires_at<? '
            "AND id NOT IN (SELECT owner_id FROM allocations WHERE status='arrived')",
            (cutoff,),
        )
    return {
        'deleted_inactive_allocations': deleted,
        'redacted_reports': redacted,
        'closed_edges_retained': True,
    }


def cmd_prune(args, directory):
    print(json.dumps(prune(open_database(directory))))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='安行本機演練後端管理')
    sub = parser.add_subparsers(dest='command', required=True)

    def command(name, handler, help_text, *arguments):
        p = sub.add_parser(name, help=help_text)
        for argument in arguments:
            p.add_argument(argument)
        p.set_defaults(handler=handler)

    command('admin-token', cmd_admin_token, '顯示管理金鑰，僅在可信終端使用')
    command('export-demo', cmd_export_demo, '輸出標準演練資料 JSON', 'output')
    command('validate-data', cmd_validate_data, '驗證標準化資料與拓撲', 'path')
    command('import-data', cmd_import_data, '匯入新資料；僅允許無導航／災情紀錄的資料庫', 'path')
    command('backup', cmd_backup, '以 SQLite backup API 產生一致備份', 'output')
    command('prune', cmd_prune, '刪除30天前已結束導航及去識別過期災情內容；封閉狀態保留')
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.handler(args, data_directory())
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
