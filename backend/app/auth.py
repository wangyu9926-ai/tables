# -*- coding: utf-8 -*-
"""认证接口（开放版多用户）：登录签 JWT，登出作废会话。

账号存业务库 mt_user（管理员由 .env 引导创建，其余由管理员在界面添加）。
"""
from datetime import datetime, timezone, timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth_sessions import API_AUDIENCE, register_session, revoke_session
from .config import JWT_ALG, JWT_EXPIRE_HOURS, JWT_SECRET
from .deps import require_login
from .mtables import store

router = APIRouter(prefix='/api/auth', tags=['认证'])


class LoginRequest(BaseModel):
    username: str
    password: str


def _issue_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    expires = int((now + timedelta(hours=JWT_EXPIRE_HOURS)).timestamp())
    payload = {
        **register_session(user, expires),
        'iat': now,
        'exp': expires,
        'aud': API_AUDIENCE,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


@router.post('/login')
def login(body: LoginRequest):
    u = (body.username or '').strip()
    if not u or not body.password:
        raise HTTPException(401, '账号或密码错误')
    try:
        acct = store.get_user(username=u)
    except Exception:
        raise HTTPException(503, '数据库暂时不可用，请稍后重试')
    if not acct or acct.get('status') != 1:
        raise HTTPException(401, '账号或密码错误')
    if not store.verify_password(body.password, acct.get('password_hash') or ''):
        raise HTTPException(401, '账号或密码错误')
    user = {'id': int(acct['id']), 'username': acct['username'],
            'real_name': acct['real_name'] or acct['username'], 'role': acct['role'],
            'status': 1, 'password_hash': acct['password_hash'], '_auth_source': 'database'}
    return {'token': _issue_token(user),
            'user': {'id': user['id'], 'username': user['username'],
                     'real_name': user['real_name'], 'role': user['role']}}


@router.get('/me')
def me(payload: dict = Depends(require_login)):
    return {'id': payload.get('uid'), 'username': payload.get('sub'),
            'real_name': payload.get('name'), 'role': payload.get('role'), 'menus': None}


@router.post('/logout')
def logout(payload: dict = Depends(require_login)):
    revoke_session(payload['sid'])
    return {'ok': True}
