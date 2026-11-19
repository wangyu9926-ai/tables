"""持久登录会话与实时账号核验（开放版：账号存业务库 mt_user）。

- 会话落 sqlite（backend/runtime/auth_sessions.sqlite3），重启不丢、可撤销；
- 每个请求按 sid 复查会话，并实时核验 mt_user（改密码/停用/改角色 → 会话立刻失效）。
"""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import hmac
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import uuid

from fastapi import HTTPException

from .config import JWT_SECRET
from .database import _app_ready, appdb_fresh_user

SESSION_VERSION = 1
API_AUDIENCE = 'mtables-open-api'
_SESSION_PATH = Path(__file__).resolve().parents[1] / 'runtime' / 'auth_sessions.sqlite3'
_SESSION_ID = re.compile(r'^[0-9a-f]{32}$')
request_sessions = ContextVar('request_auth_sessions', default=None)


def security_stamp(account: dict) -> str:
    """账号关键字段指纹：密码/角色/状态一变，所有旧会话立即失效。"""
    fields = {key: account.get(key) for key in ('id', 'username', 'password_hash', 'role', 'status',
                                                'real_name')}
    serialized = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    return hmac.new(JWT_SECRET.encode('utf-8'), serialized.encode('utf-8'), hashlib.sha256).hexdigest()


def current_account(uid: int, username: str, source: str) -> dict:
    if source != 'database':
        raise HTTPException(401, '登录来源无效，请重新登录')
    if not _app_ready():
        raise HTTPException(503, '账号库未就绪，请稍后重试')
    try:
        account = appdb_fresh_user(uid=uid, username=username)
    except Exception:
        raise HTTPException(503, '账号状态暂时无法核验，请稍后重试；未使用旧权限放行')
    if not account or account.get('id') != uid or account.get('username') != username or account.get('status') != 1:
        raise HTTPException(401, '账号已停用、删除或变更，请重新登录')
    if account.get('role') not in ('admin', 'staff') or not account.get('password_hash'):
        raise HTTPException(401, '账号权限或凭证无效，请重新登录')
    return account


@contextmanager
def session_database(create=False):
    connection = None
    try:
        path = _SESSION_PATH.resolve()
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(str(path), timeout=5)
            connection.execute('CREATE TABLE IF NOT EXISTS sessions ('
                               'sid TEXT PRIMARY KEY, uid INTEGER NOT NULL, username TEXT NOT NULL, '
                               'source TEXT NOT NULL, stamp TEXT NOT NULL, role TEXT NOT NULL, '
                               'expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)')
            connection.execute('CREATE INDEX IF NOT EXISTS sessions_user ON sessions(uid,source)')
        else:
            if not path.is_file():
                raise HTTPException(401, '登录会话不存在或已失效，请重新登录')
            connection = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        yield connection
        connection.commit()
    except (OSError, sqlite3.Error):
        if connection is not None:
            connection.rollback()
        raise HTTPException(503, '登录会话存储不可用，未降级放行，请稍后重试')
    finally:
        if connection is not None:
            connection.close()


def register_session(user: dict, expires: float) -> dict:
    uid, username = user.get('id'), user.get('username')
    source = user.get('_auth_source', 'database')
    if type(uid) is not int or not 0 < uid < 2 ** 63 or not isinstance(username, str) or not username:
        raise HTTPException(401, '账号身份无效，请重新登录')
    if type(expires) not in (int, float) or not math.isfinite(expires):
        raise HTTPException(401, '登录有效期无效')
    account = current_account(uid, username, source)
    stamp = security_stamp(account)
    verified = user.get('_auth_stamp')
    if verified is not None and not hmac.compare_digest(stamp, verified):
        raise HTTPException(401, '账号在登录过程中发生变更，请重新登录')
    if user.get('role') != account['role']:
        raise HTTPException(401, '账号角色在登录过程中发生变更，请重新登录')
    sid = uuid.uuid4().hex
    with session_database(create=True) as connection:
        connection.execute('DELETE FROM sessions WHERE expires <= ?', (time.time(),))
        connection.execute('INSERT INTO sessions(sid,uid,username,source,stamp,role,expires) VALUES(?,?,?,?,?,?,?)',
                           (sid, uid, username, source, stamp, account['role'], expires))
    return {'sid': sid, 'session_version': SESSION_VERSION, 'auth_source': source,
            'sub': username, 'uid': uid, 'role': account['role'],
            'name': account.get('real_name') or username}


def revoke_session(sid: str) -> None:
    with session_database() as connection:
        connection.execute('UPDATE sessions SET revoked=1 WHERE sid=?', (sid,))


def revoke_user_sessions(uid: int) -> None:
    """管理端改密/停用/删人后立刻踢掉该用户全部会话。"""
    if not _SESSION_PATH.exists():
        return
    with session_database() as connection:
        connection.execute("UPDATE sessions SET revoked=1 WHERE uid=?", (uid,))


def validate_session(payload: dict) -> dict:
    sid, uid, username = payload.get('sid'), payload.get('uid'), payload.get('sub')
    expires, source = payload.get('exp'), payload.get('auth_source')
    if (not isinstance(sid, str) or not _SESSION_ID.fullmatch(sid) or type(uid) is not int or not 0 < uid < 2 ** 63
            or not isinstance(username, str) or not username or type(payload.get('session_version')) is not int or payload.get('session_version') != SESSION_VERSION
            or type(expires) not in (int, float) or not math.isfinite(expires) or expires <= time.time()
            or source != 'database'):
        raise HTTPException(401, '旧登录或会话格式无效，请重新登录')
    cache = request_sessions.get()
    key = (sid, uid, username, source, payload.get('role'), expires)
    if cache is not None and key in cache:
        return {**payload, **cache[key]}
    with session_database() as connection:
        session = connection.execute('SELECT * FROM sessions WHERE sid=?', (sid,)).fetchone()
    if (not session or session['revoked'] or session['uid'] != uid or session['username'] != username
            or session['source'] != source or session['role'] != payload.get('role') or session['expires'] != expires):
        raise HTTPException(401, '登录会话已撤销或变更，请重新登录')
    try:
        account = current_account(uid, username, source)
    except HTTPException as exc:
        if exc.status_code == 401:
            revoke_session(sid)
        raise
    if not hmac.compare_digest(session['stamp'], security_stamp(account)):
        revoke_session(sid)
        raise HTTPException(401, '账号权限、密码或授权已变更，请重新登录')
    fresh = {'role': account['role'], 'name': account.get('real_name') or username,
             'department': account.get('department') or '', 'menus': None}
    if cache is not None:
        cache[key] = fresh
    return {**payload, **fresh}
