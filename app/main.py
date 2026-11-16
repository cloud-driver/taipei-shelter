"""FastAPI application: public shelter catalogue, OSM routing and the capacity drill.

Layout of ``create_app``:

1. middleware (same-origin writes, body size limits, security headers)
2. auth helpers (guest / account sessions, admin token, rate limiting)
3. routes: health & map, accounts, navigation plans, hazard reports, admin, analysis
4. static pages

All drill data is synthetic; nothing here promises real shelter space or rescue dispatch.
"""

import csv
import io
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .db import Database, admin_secret, bump_revision, expire, get_dataset, loads
from .models import (
    Event,
    Login,
    Mode,
    Needs,
    Plan,
    Profile,
    Register,
    Report,
    Review,
    Scenario,
    ShelterUpdate,
    Transition,
)
from .public_map import PublicRepository, make_router
from .routing import ACTIVE as ACTIVE_STATUSES
from .routing import analysis, blocked_targets, candidates, invalidate, shortest_paths
from .security import (
    SECURITY_HEADERS,
    client_ip,
    constant_time_equal,
    digest,
    hash_request,
    password_hash,
    verify_password,
)
from .simulation import experiment

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get('SHELTER_DATA_DIR', ROOT / '.local'))
API_VERSION = '2.0.0'
DEFAULT_ALLOWED_HOSTS = '127.0.0.1,localhost,[::1],testserver'

# Lifetimes (seconds)
SESSION_TTL = 24 * 3600
PROPOSAL_TTL = 10 * 60  # an unaccepted route proposal holds capacity this long
ACCEPTED_TTL = 20 * 60  # after accept / arrive the hold is refreshed for this long
REPORT_MAX_AGE = 24 * 3600  # reports about older observations are rejected
REPORT_DEDUP_WINDOW = 5 * 60

# Request body limits (bytes)
MAX_JSON_BODY = 32 * 1024
MAX_PHOTO_BODY = 2 * 1024 * 1024
MAX_PHOTO_PIXELS = 12_000_000


# Allowed allocation state machine: action -> (target status, allowed source statuses)
TRANSITIONS = {
    'accept': ('accepted', ('proposed',)),
    'cancel': ('cancelled', ('proposed', 'accepted', 'invalidated')),
    'arrive': ('arrived', ('accepted',)),
    'leave': ('left', ('arrived',)),
}


def fail(code, message, **extra):
    """Abort the request with a JSON ``{"detail": {"message": ...}}`` body."""
    raise HTTPException(code, detail={'message': message, **extra})


def current_revision(conn) -> int:
    return int(conn.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])


def public_allocation(row, conn):
    """Serialize an allocation row for its owner, flagging routes made stale by newer data."""
    result = {
        k: row[k]
        for k in [
            'id',
            'shelter_id',
            'group_size',
            'accessible_count',
            'status',
            'created_at',
            'expires_at',
            'profile_version',
        ]
    }
    route = json.loads(row['route'])
    result.update(
        route=route,
        route_current=route['revision'] == current_revision(conn)
        and row['status'] in ACTIVE_STATUSES,
        requirements=json.loads(row['request']),
        drill=True,
        message='短期演練配置，不是場所保留床位承諾；到達後仍計入負載，離開才釋出。',
    )
    return result


def create_app(db_path=None, admin_token=None, public_data_dir=None):
    db = Database(db_path or DATA_DIR / 'shelter.sqlite3')
    secret = admin_token or admin_secret(DATA_DIR)
    dummy = password_hash('synthetic-dummy-password')
    app = FastAPI(
        title='臺北官方避難名冊・OSM導航與演練 API',
        version=API_VERSION,
        docs_url=None,
        redoc_url=None,
        description='macOS / Debian 後端：官方名冊、OSM步行路線參考與OSM無障礙標記；容量分流與供需實驗使用合成資料。不提供官方即時預警、現場查核或救援派遣。',
    )
    app.state.db = db
    public_repository = PublicRepository(
        public_data_dir or os.environ.get('SHELTER_PUBLIC_DATA_DIR', ROOT / 'data/public')
    )
    app.state.public_repository = public_repository
    hosts = [
        h.strip()
        for h in os.environ.get('SHELTER_ALLOWED_HOSTS', DEFAULT_ALLOWED_HOSTS).split(',')
        if h.strip()
    ]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # ------------------------------------------------------------------ middleware

    @app.middleware('http')
    async def guard(request: Request, call_next):
        """Reject cross-origin writes and oversized bodies; add security headers."""
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            origin = request.headers.get('origin')
            if origin and origin not in (
                f'http://{request.headers.get("host")}',
                f'https://{request.headers.get("host")}',
            ):
                return JSONResponse({'detail': {'message': '不允許跨來源寫入'}}, status_code=403)
            limit = MAX_PHOTO_BODY if request.url.path.endswith('/photo') else MAX_JSON_BODY
            size = request.headers.get('content-length')
            if size and (not size.isdigit() or int(size) > limit):
                return JSONResponse({'detail': {'message': '請求內容過大'}}, status_code=413)
            chunks = bytearray()
            async for chunk in request.stream():
                chunks.extend(chunk)
                if len(chunks) > limit:
                    return JSONResponse({'detail': {'message': '請求內容過大'}}, status_code=413)
            # The stream is consumed above; cache it so route handlers can still read the body.
            request._body = bytes(chunks)
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, exc):
        # Never echo passwords, tokens, or an entire sensitive input in validation responses.
        return JSONResponse(
            {
                'detail': {
                    'message': '欄位驗證失敗',
                    'fields': [
                        {'field': '.'.join(str(x) for x in e['loc']), 'message': e['msg']}
                        for e in exc.errors()
                    ],
                }
            },
            status_code=422,
        )

    # ------------------------------------------------------------------ auth helpers

    def rate(conn, key, limit, seconds=60):
        """Fixed-window rate limit: at most ``limit`` calls per ``seconds`` for ``key``."""
        now = time.time()
        row = conn.execute('SELECT * FROM rate_limits WHERE key=?', (key,)).fetchone()
        if row and now - row['starts_at'] < seconds:
            if row['count'] >= limit:
                fail(429, '操作過於頻繁，請稍後再試')
            conn.execute('UPDATE rate_limits SET count=count+1 WHERE key=?', (key,))
        else:
            conn.execute('INSERT OR REPLACE INTO rate_limits VALUES (?,?,?)', (key, 1, now))

    def rate_attempt(key, limit, seconds=60):
        # Commit failed login attempts too; cannot roll them back with authentication errors.
        with db.transaction() as conn:
            rate(conn, key, limit, seconds)

    def auth(authorization: str | None = Header(None)):
        if not authorization or not authorization.startswith('Bearer '):
            fail(401, '請先使用訪客模式或登入')
        with closing(db.connect()) as conn:
            session = conn.execute(
                'SELECT * FROM sessions WHERE token_hash=? AND expires_at>?',
                (digest(authorization[7:]), time.time()),
            ).fetchone()
        if not session:
            fail(401, '工作階段已過期，請重新登入或使用訪客模式')
        return dict(session)

    def admin(x_admin_token: str | None = Header(None)):
        if not x_admin_token or not constant_time_equal(x_admin_token, secret):
            fail(403, '需要管理員權限')

    def new_session(conn, user_id=None):
        token, sid = secrets.token_urlsafe(32), uuid.uuid4().hex
        end = time.time() + SESSION_TTL
        conn.execute('INSERT INTO sessions VALUES (?,?,?,?)', (sid, digest(token), user_id, end))
        return {'token': token, 'expires_at': end, 'guest': user_id is None}

    def live_session(conn, session):
        # Dependencies run before acquiring the write lock; re-check after a concurrent logout.
        if not conn.execute(
            'SELECT 1 FROM sessions WHERE id=? AND expires_at>?', (session['id'], time.time())
        ).fetchone():
            fail(401, '工作階段已過期或已登出')

    def owner_allocation(conn, aid, session):
        row = conn.execute(
            'SELECT * FROM allocations WHERE id=? AND owner_id=?', (aid, session['id'])
        ).fetchone()
        if not row:
            fail(404, '找不到此導航配置')
        return row

    # ------------------------------------------------------------------ status & map

    @app.get('/api/health', tags=['狀態'])
    def health():
        with closing(db.connect()) as conn:
            conn.execute('SELECT 1').fetchone()
        return {
            'status': 'ok',
            'version': API_VERSION,
            'mode': 'public-reference-and-synthetic-drill',
            'database': 'sqlite',
            'time': time.time(),
        }

    @app.get('/api/map', tags=['公開資料'])
    def map_data():
        with db.transaction() as conn:
            expire(conn)
            data = get_dataset(conn)
            block_e, block_s = blocked_targets(conn)
            live_load = loads(conn)
            for s in data['shelters']:
                n, a = live_load.get(s['id'], (0, 0))
                s['estimated_load'] = n + s['background']
                s['accessible_load'] = a + s['background_accessible']
                s['temporarily_avoided'] = s['id'] in block_s
            data['blocked_edges'] = sorted(block_e)
            data['blocked_shelters'] = sorted(block_s)
            data['generated_at'] = time.time()
            data['notice'] = (
                '示意路網與演練場所；不可作為真實避難導航。位置是示意入口，不是已查核入口。'
            )
            return data

    @app.get('/api/events', tags=['地震演練'])
    def events():
        with closing(db.connect()) as conn:
            return {
                'items': [
                    json.loads(r[0])
                    for r in conn.execute('SELECT data FROM events ORDER BY rowid DESC LIMIT 50')
                ],
                'notice': '僅演練事件；不是官方即時地震預警，沒有 APNs 推播。',
            }

    # ------------------------------------------------------------------ accounts

    @app.post('/api/guest', tags=['帳號'])
    def guest(request: Request):
        rate_attempt('guest:' + client_ip(request), 60)
        with db.transaction() as conn:
            return new_session(conn)

    @app.post('/api/auth/register', status_code=201, tags=['帳號'])
    def register(body: Register, request: Request):
        rate_attempt('register:' + client_ip(request), 20)
        hashed = password_hash(body.password)
        user_id, now = uuid.uuid4().hex, time.time()
        profile = body.profile.model_dump_json()
        with db.transaction() as conn:
            try:
                conn.execute(
                    'INSERT INTO users VALUES (?,?,?,?,1,?)',
                    (user_id, body.username.lower(), hashed, profile, now),
                )
            except sqlite3.IntegrityError:
                fail(409, '此帳號名稱無法使用')
            conn.execute('INSERT INTO profile_history VALUES (?,?,?,?)', (user_id, 1, profile, now))
            return {**new_session(conn, user_id), 'profile_version': 1}

    @app.post('/api/auth/login', tags=['帳號'])
    def login(body: Login, request: Request):
        rate_attempt('login:' + client_ip(request), 20)
        with db.transaction() as conn:
            user = conn.execute(
                'SELECT * FROM users WHERE username=?', (body.username.lower(),)
            ).fetchone()
            valid = verify_password(body.password, user['password'] if user else dummy)
            if not user or not valid:
                fail(401, '帳號或密碼錯誤')
            return new_session(conn, user['id'])

    @app.get('/api/auth/me', tags=['帳號'])
    def me(session=Depends(auth)):
        if not session['user_id']:
            return {'guest': True, 'profile': None}
        with closing(db.connect()) as conn:
            user = conn.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
            if not user:
                fail(401, '帳號已不存在')
            return {
                'guest': False,
                'username': user['username'],
                'profile': json.loads(user['profile']),
                'version': user['version'],
            }

    @app.put('/api/auth/profile', tags=['帳號'])
    def profile(body: Profile, session=Depends(auth)):
        if not session['user_id']:
            fail(403, '訪客不儲存個人需求，請使用本次導航設定')
        with db.transaction() as conn:
            live_session(conn, session)
            conn.execute(
                'UPDATE users SET profile=?,version=version+1 WHERE id=?',
                (body.model_dump_json(), session['user_id']),
            )
            version = conn.execute(
                'SELECT version FROM users WHERE id=?', (session['user_id'],)
            ).fetchone()[0]
            conn.execute(
                'INSERT INTO profile_history VALUES (?,?,?,?)',
                (session['user_id'], version, body.model_dump_json(), time.time()),
            )
            conn.execute(
                "UPDATE allocations SET status='invalidated' WHERE owner_id IN (SELECT id FROM sessions WHERE user_id=?) AND status IN ('proposed','accepted')",
                (session['user_id'],),
            )
            return {
                'version': version,
                'profile': body.model_dump(),
                'message': '需求已更新；進行中的配置已釋放，請重新規劃',
            }

    def preserve_arrivals(conn, session_ids):
        """Before deleting sessions, keep their arrived people as anonymous background load.

        Otherwise logging out would silently free shelter space that is still occupied.
        """
        for sid in session_ids:
            for row in conn.execute(
                "SELECT * FROM allocations WHERE owner_id=? AND status='arrived'", (sid,)
            ).fetchall():
                sr = conn.execute(
                    'SELECT data FROM shelters WHERE id=?', (row['shelter_id'],)
                ).fetchone()
                if sr is None:  # shelter was removed by a data import; nothing to preserve
                    continue
                s = json.loads(sr[0])
                s['background'] += row['group_size']
                s['background_accessible'] += row['accessible_count']
                conn.execute(
                    'UPDATE shelters SET data=? WHERE id=?',
                    (json.dumps(s, ensure_ascii=False), s['id']),
                )

    @app.post('/api/auth/logout', tags=['帳號'])
    def logout(session=Depends(auth)):
        with db.transaction() as conn:
            preserve_arrivals(conn, [session['id']])
            conn.execute('DELETE FROM sessions WHERE id=?', (session['id'],))
        return {'message': '已登出並釋放在途配置；已到達人數轉為匿名背景負載'}

    @app.delete('/api/auth/me', tags=['帳號'])
    def delete_me(session=Depends(auth)):
        with db.transaction() as conn:
            ids = (
                [
                    r[0]
                    for r in conn.execute(
                        'SELECT id FROM sessions WHERE user_id=?', (session['user_id'],)
                    )
                ]
                if session['user_id']
                else [session['id']]
            )
            preserve_arrivals(conn, ids)
            for sid in ids:
                conn.execute(
                    'DELETE FROM photos WHERE report_id IN (SELECT id FROM reports WHERE owner_id=?)',
                    (sid,),
                )
                conn.execute(
                    "UPDATE reports SET description='使用者已刪除內容' WHERE owner_id=?", (sid,)
                )
            if session['user_id']:
                conn.execute('DELETE FROM users WHERE id=?', (session['user_id'],))
            else:
                conn.execute('DELETE FROM sessions WHERE id=?', (session['id'],))
        return {'message': '帳號、需求版本及工作階段已刪除；已到達人數僅保留匿名統計'}

    # ------------------------------------------------------------------ navigation plans

    @app.post('/api/plans', status_code=201, tags=['導航配置'])
    def plan(body: Plan, session=Depends(auth)):
        """Pick a reachable shelter and atomically hold capacity for the whole group.

        Safety preconditions are checked first; hard constraints (stairs, closures, unknown
        accessibility) are never relaxed. Retrying with the same ``request_id`` is idempotent.
        """
        if not body.shaking_stopped or not body.evacuation_needed:
            fail(409, '需先確認搖晃停止且需要撤離；搖晃時請就地保護')
        if not body.needs.confirmed or body.needs.mode == 'unsure':
            fail(422, '請先確認本次行動需求並選擇明確行進模式')
        if body.needs.mode == 'assistance' and not body.helper_present:
            fail(422, '需要他人協助時，請確認協助者同行；本系統不會派遣救援')
        if body.accessible_count > 0:
            body.needs.step_free, body.needs.can_stairs = True, False
        if body.needs.step_free:
            body.accessible_count = max(1, body.accessible_count)
        fingerprint = hash_request(body)
        with db.transaction() as conn:
            live_session(conn, session)
            expire(conn)
            existing = conn.execute(
                'SELECT * FROM allocations WHERE owner_id=? AND request_id=?',
                (session['id'], body.request_id),
            ).fetchone()
            if existing:
                if existing['request_hash'] != fingerprint:
                    fail(409, '同一請求識別不可用於不同內容')
                return public_allocation(existing, conn)
            rate(conn, 'plan:' + session['id'], 30)
            previous = None
            if body.replace_id:
                previous = owner_allocation(conn, body.replace_id, session)
                if previous['status'] not in ('proposed', 'accepted', 'invalidated', 'expired'):
                    fail(409, '此配置不能重新規劃')
            elif conn.execute(
                "SELECT 1 FROM allocations WHERE owner_id=? AND status IN ('proposed','accepted','arrived')",
                (session['id'],),
            ).fetchone():
                fail(409, '已有進行中配置；請取消、離開或使用重新規劃')
            data = get_dataset(conn)
            if body.origin not in {n['id'] for n in data['nodes']}:
                fail(422, '找不到起點；僅支援演練路網節點')
            block_e, block_s = blocked_targets(conn)
            choices, reasons = candidates(
                data,
                body.origin,
                body.needs,
                body.group_size,
                body.accessible_count,
                load=loads(conn, body.replace_id),
                blocked_edges=block_e,
                blocked_shelters=block_s,
                extra=body.max_extra_minutes,
            )
            if not choices:
                fail(
                    409,
                    '無法確認可達且容量足夠的場所；不放寬必要條件。請洽場所管理端，急難請聯絡119。',
                    reasons=reasons,
                )
            if previous:
                conn.execute(
                    "UPDATE allocations SET status='cancelled' WHERE id=?", (body.replace_id,)
                )
            version = None
            if session['user_id']:
                version = conn.execute(
                    'SELECT version FROM users WHERE id=?', (session['user_id'],)
                ).fetchone()[0]
            aid, now = uuid.uuid4().hex, time.time()
            conn.execute(
                'INSERT INTO allocations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (
                    aid,
                    session['id'],
                    body.request_id,
                    fingerprint,
                    choices[0]['shelter_id'],
                    body.group_size,
                    body.accessible_count,
                    'proposed',
                    json.dumps(choices[0], ensure_ascii=False),
                    body.model_dump_json(),
                    version,
                    now,
                    now + PROPOSAL_TTL,
                ),
            )
            row = conn.execute('SELECT * FROM allocations WHERE id=?', (aid,)).fetchone()
            return {
                **public_allocation(row, conn),
                'alternatives': choices[1:],
                'exclusions': reasons,
            }

    @app.get('/api/plans/{aid}', tags=['導航配置'])
    def get_plan(aid: str, session=Depends(auth)):
        with db.transaction() as conn:
            expire(conn)
            return public_allocation(owner_allocation(conn, aid, session), conn)

    @app.post('/api/plans/{aid}/transition', tags=['導航配置'])
    def transition(aid: str, body: Transition, session=Depends(auth)):
        with db.transaction() as conn:
            expire(conn)
            invalidate(conn, get_dataset(conn))
            row = owner_allocation(conn, aid, session)
            target, allowed = TRANSITIONS[body.action]
            if row['status'] == target:
                return public_allocation(row, conn)
            if row['status'] not in allowed:
                fail(409, f'無法從 {row["status"]} 執行 {body.action}')
            conn.execute(
                'UPDATE allocations SET status=?,expires_at=? WHERE id=?',
                (target, time.time() + ACCEPTED_TTL, aid),
            )
            return public_allocation(owner_allocation(conn, aid, session), conn)

    # ------------------------------------------------------------------ hazard reports

    @app.get('/api/reports', tags=['災情'])
    def reports():
        with closing(db.connect()) as conn:
            # Public view has no reporter ID, personal prose, or precise personal coordinates.
            return {
                'items': [
                    dict(r)
                    for r in conn.execute(
                        'SELECT id,target_type,target_id,kind,status,observed_at,created_at,reviewed_at FROM reports ORDER BY created_at DESC LIMIT 200'
                    )
                ]
            }

    @app.post('/api/reports', status_code=201, tags=['災情'])
    def report(body: Report, session=Depends(auth)):
        now = time.time()
        if body.observed_at > now + 60 or body.observed_at < now - REPORT_MAX_AGE:
            fail(422, '發現時間需在最近24小時內，且不可在未來')
        fingerprint = hash_request(body)
        with db.transaction() as conn:
            live_session(conn, session)
            old = conn.execute(
                'SELECT * FROM reports WHERE owner_id=? AND request_id=?',
                (session['id'], body.request_id),
            ).fetchone()
            if old:
                if old['request_hash'] != fingerprint:
                    fail(409, '同一請求識別不可用於不同內容')
                return {'id': old['id'], 'status': old['status'], 'message': '既有回報，未重複建立'}
            rate(conn, 'report:' + session['id'], 8)
            data = get_dataset(conn)
            target = body.edge_id or body.shelter_id
            kind = 'edge' if body.edge_id else 'shelter'
            if target not in {x['id'] for x in data['edges' if body.edge_id else 'shelters']}:
                fail(422, '回報對象不在演練資料內')
            duplicate = conn.execute(
                "SELECT id,status FROM reports WHERE owner_id=? AND target_id=? AND kind=? AND created_at>? AND status<>'resolved'",
                (session['id'], target, body.kind, now - REPORT_DEDUP_WINDOW),
            ).fetchone()
            if duplicate:
                return {
                    'id': duplicate['id'],
                    'status': duplicate['status'],
                    'message': '五分鐘內相同災情已去重',
                }
            rid = uuid.uuid4().hex
            status = 'avoiding' if body.kind in ('blocked', 'fire', 'closure') else 'pending'
            conn.execute(
                'INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (
                    rid,
                    session['id'],
                    body.request_id,
                    fingerprint,
                    kind,
                    target,
                    body.kind,
                    body.description,
                    body.observed_at,
                    status,
                    now,
                    None,
                    None,
                ),
            )
            bump_revision(conn)
            invalidate(conn, get_dataset(conn))
            return {
                'id': rid,
                'status': status,
                'message': '演練回報已記錄；高嚴重度暫時避開，尚未核實。未送達救援機關。',
            }

    # ------------------------------------------------------------------ admin

    @app.get('/api/admin/overview', dependencies=[Depends(admin)], tags=['管理'])
    def overview():
        with db.transaction() as conn:
            expire(conn)
            counts = {
                r[0]: r[1]
                for r in conn.execute('SELECT status,COUNT(*) FROM allocations GROUP BY status')
            }
            return {
                'allocations': counts,
                'reports': [
                    dict(r)
                    for r in conn.execute(
                        'SELECT id,target_type,target_id,kind,description,status,observed_at,reviewed_at,note,EXISTS(SELECT 1 FROM photos WHERE report_id=reports.id) AS has_photo FROM reports ORDER BY created_at DESC LIMIT 200'
                    )
                ],
                'notice': '管理端不提供帳號、個人行動需求或個人起點。',
            }

    @app.put('/api/reports/{rid}/photo', tags=['災情'])
    async def upload_photo(rid: str, request: Request, session=Depends(auth)):
        if request.headers.get('content-type', '').split(';')[0] not in ('image/jpeg', 'image/png'):
            fail(415, '照片只接受 JPEG 或 PNG，最大2MB')
        with db.transaction() as conn:
            row = conn.execute('SELECT owner_id FROM reports WHERE id=?', (rid,)).fetchone()
            if not row or row['owner_id'] != session['id']:
                fail(404, '找不到可上傳照片的本人回報')
            rate(conn, 'photo:' + session['id'], 8)
        raw = await request.body()
        try:
            with Image.open(io.BytesIO(raw)) as image:
                if (
                    image.format not in ('JPEG', 'PNG')
                    or image.width * image.height > MAX_PHOTO_PIXELS
                ):
                    fail(422, '圖片格式不支援或超過1200萬像素')
                image.load()
                clean = ImageOps.exif_transpose(image).convert('RGB')
                clean.thumbnail((1600, 1600))
                out = io.BytesIO()
                clean.save(out, 'JPEG', quality=85)  # No source EXIF, GPS, comments, or filename.
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            fail(422, '圖片無法解碼，請使用有效的 JPEG 或 PNG')
        with db.transaction() as conn:
            if not conn.execute(
                'SELECT 1 FROM reports WHERE id=? AND owner_id=?', (rid, session['id'])
            ).fetchone():
                fail(404, '回報已不存在或不屬於本人')
            conn.execute(
                'INSERT OR REPLACE INTO photos VALUES (?,?,?)', (rid, out.getvalue(), time.time())
            )
        return {
            'message': '照片已儲存，已移除原始 EXIF 與 GPS 中繼資料',
            'bytes': len(out.getvalue()),
        }

    @app.get('/api/reports/{rid}/photo', tags=['災情'])
    def get_photo(
        rid: str, authorization: str | None = Header(None), x_admin_token: str | None = Header(None)
    ):
        is_admin = x_admin_token is not None and constant_time_equal(x_admin_token, secret)
        session = None if is_admin else auth(authorization)
        with closing(db.connect()) as conn:
            row = conn.execute(
                'SELECT image,owner_id FROM photos JOIN reports ON reports.id=photos.report_id WHERE report_id=?',
                (rid,),
            ).fetchone()
        if not row or (not is_admin and row['owner_id'] != session['id']):
            fail(404, '找不到可讀取的照片')
        return Response(
            row['image'], media_type='image/jpeg', headers={'Cache-Control': 'no-store'}
        )

    @app.patch('/api/admin/reports/{rid}', dependencies=[Depends(admin)], tags=['管理'])
    def review(rid: str, body: Review):
        with db.transaction() as conn:
            if not conn.execute('SELECT 1 FROM reports WHERE id=?', (rid,)).fetchone():
                fail(404, '找不到回報')
            conn.execute(
                'UPDATE reports SET status=?,note=?,reviewed_at=? WHERE id=?',
                (body.status, body.note, time.time(), rid),
            )
            conn.execute(
                'INSERT INTO report_audit(report_id,status,note,created_at) VALUES (?,?,?,?)',
                (rid, body.status, body.note, time.time()),
            )
            bump_revision(conn)
            invalidate(conn, get_dataset(conn))
            return {
                'id': rid,
                'status': body.status,
                'message': '已更新；確認封閉不會因時間到期自動解除。',
            }

    @app.patch('/api/admin/shelters/{sid}', dependencies=[Depends(admin)], tags=['管理'])
    def update_shelter(sid: str, body: ShelterUpdate):
        with db.transaction() as conn:
            expire(conn)
            row = conn.execute('SELECT * FROM shelters WHERE id=?', (sid,)).fetchone()
            if not row:
                fail(404, '找不到場所')
            s = json.loads(row['data'])
            arrived = conn.execute(
                "SELECT COALESCE(SUM(group_size),0),COALESCE(SUM(accessible_count),0) FROM allocations WHERE shelter_id=? AND status='arrived'",
                (sid,),
            ).fetchone()
            if body.capacity is not None and body.background + arrived[0] > body.capacity:
                fail(409, '容量不可低於已到達與背景人數，請先校正人數')
            if (
                body.accessible_capacity is not None
                and body.background_accessible + arrived[1] > body.accessible_capacity
            ):
                fail(409, '無障礙容量不可低於已到達與背景適用人數')
            s.update(body.model_dump(exclude={'note'}))
            conn.execute(
                'UPDATE shelters SET data=?,updated_at=? WHERE id=?',
                (json.dumps(s, ensure_ascii=False), time.time(), sid),
            )
            conn.execute(
                'INSERT INTO shelter_audit(shelter_id,data,note,created_at) VALUES (?,?,?,?)',
                (sid, json.dumps(s, ensure_ascii=False), body.note, time.time()),
            )
            # Re-check all in-flight configurations after a supply update, without moving arrived people.
            conn.execute(
                "UPDATE allocations SET status='invalidated' WHERE shelter_id=? AND status IN ('proposed','accepted')",
                (sid,),
            )
            bump_revision(conn)
            return {'shelter': s, 'message': '場所已更新；相關在途配置已釋放，需重新規劃'}

    @app.post('/api/admin/events', dependencies=[Depends(admin)], tags=['管理'])
    def upsert_event(body: Event):
        if body.occurred_at > body.published_at or body.published_at > time.time() + 60:
            fail(422, '事件時間不可晚於發布時間，且發布時間不可在未來')
        with db.transaction() as conn:
            row = conn.execute('SELECT * FROM events WHERE event_id=?', (body.event_id,)).fetchone()
            if row and body.version <= row['version']:
                fail(409, '重複或舊版本事件不能覆蓋最新版本')
            conn.execute(
                'INSERT OR REPLACE INTO events VALUES (?,?,?)',
                (body.event_id, body.version, body.model_dump_json()),
            )
        return body.model_dump()

    # ------------------------------------------------------------------ analysis & experiments

    @app.get('/api/analysis', tags=['分析'])
    def get_analysis():
        with closing(db.connect()) as conn:
            e, s = blocked_targets(conn)
            return analysis(get_dataset(conn), e, s)

    @app.get('/api/accessibility', tags=['分析'])
    def accessibility(origin: str = 'n22', mode: Mode = 'walking', step_free: bool = False):
        with closing(db.connect()) as conn:
            data = get_dataset(conn)
            e, _ = blocked_targets(conn)
        if origin not in {n['id'] for n in data['nodes']}:
            fail(422, '起點不在路網內')
        needs = Needs(mode=mode, step_free=step_free, can_stairs=not step_free)
        paths = shortest_paths(data, origin, needs, e)
        return {
            'origin': origin,
            'mode': mode,
            'speed_mps': needs.speed,
            'drill': True,
            'thresholds_minutes': [5, 10, 15],
            'nodes': [
                {
                    'id': n['id'],
                    'minutes': paths[n['id']]['minutes'] if n['id'] in paths else None,
                    'within': next(
                        (
                            t
                            for t in [5, 10, 15]
                            if n['id'] in paths and paths[n['id']]['minutes'] <= t
                        ),
                        None,
                    ),
                }
                for n in data['nodes']
            ],
            'notice': '沿示意路網計算；5／10／15分鐘是分析門檻，不是官方安全到達承諾。',
        }

    @app.post('/api/experiments', tags=['實驗'])
    def simulate(body: Scenario, session=Depends(auth)):
        rate_attempt('experiment:' + session['id'], 5)
        with closing(db.connect()) as conn:
            data = get_dataset(conn)
        result = experiment(data, body)
        eid = uuid.uuid4().hex
        with db.transaction() as conn:
            conn.execute(
                'INSERT INTO experiments VALUES (?,?,?,?)',
                (eid, body.model_dump_json(), json.dumps(result, ensure_ascii=False), time.time()),
            )
        return {**result, 'id': eid}

    @app.get('/api/experiments/{eid}', tags=['實驗'])
    def get_experiment(eid: str):
        with closing(db.connect()) as conn:
            row = conn.execute('SELECT result FROM experiments WHERE id=?', (eid,)).fetchone()
        if not row:
            fail(404, '找不到實驗')
        return {**json.loads(row['result']), 'id': eid}

    @app.get('/api/experiments/{eid}/csv', tags=['實驗'])
    def experiment_csv(eid: str):
        result = get_experiment(eid)
        if not result.get('rows'):
            fail(404, '此實驗沒有可匯出的資料列')
        out = io.StringIO()
        keys = [k for k, v in result['rows'][0].items() if not isinstance(v, (list, dict))]
        writer = csv.DictWriter(out, fieldnames=keys, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(result['rows'])
        return Response(
            '\ufeff' + out.getvalue(),
            media_type='text/csv',
            headers={'Content-Disposition': f'attachment; filename="experiment-{eid}.csv"'},
        )

    # ------------------------------------------------------------------ static pages

    @app.get('/', include_in_schema=False)
    def index():
        return FileResponse(ROOT / 'static/index.html')

    @app.get('/test', include_in_schema=False)
    def test_page():
        return FileResponse(ROOT / 'static/test.html')

    @app.get('/admin', include_in_schema=False)
    def admin_page():
        return FileResponse(ROOT / 'static/admin.html')

    @app.get('/docs', include_in_schema=False)
    def docs_page():
        return FileResponse(ROOT / 'static/docs.html')

    @app.get('/sw.js', include_in_schema=False)
    def service_worker():
        return FileResponse(
            ROOT / 'static/sw.js',
            headers={'Service-Worker-Allowed': '/', 'Cache-Control': 'no-cache'},
        )

    app.include_router(make_router(public_repository, db, rate_attempt))
    app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
    return app


def app_factory():
    return create_app()
