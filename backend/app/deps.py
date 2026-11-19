# -*- coding: utf-8 -*-
"""通用依赖：JWT 校验与访问守卫。

verify_token    —— 携带则校验、未携带返回 None（=游客）。只读接口用它，游客可看。
require_login   —— 无 token（游客）→ 401。一切写操作用它。
require_admin   —— 管理员专属（角色!=admin）→ 403。建表/管列/管用户等用它。
"""
import jwt
from fastapi import Header, HTTPException
from typing import Annotated

from .config import JWT_SECRET, JWT_ALG
from .auth_sessions import API_AUDIENCE, validate_session


def verify_token(authorization: Annotated[str | None, Header()] = None) -> dict:
    """返回 token payload（或 None=游客）。
    只应在明确公开的只读接口上使用；写接口请用 require_login/require_admin。"""
    if not authorization:
        return None
    if authorization.startswith('Bearer '):
        authorization = authorization[7:]
    try:
        payload = jwt.decode(authorization, JWT_SECRET, algorithms=[JWT_ALG], audience=API_AUDIENCE,
                             options={'require': ['exp', 'sub', 'uid', 'sid', 'session_version', 'auth_source']})
    except Exception:
        raise HTTPException(status_code=401, detail='token 无效或已过期')
    return validate_session(payload)


def require_login(authorization: Annotated[str | None, Header()] = None) -> dict:
    """强制登录：游客 → 401。返回 payload。"""
    payload = verify_token(authorization)
    if not payload:
        raise HTTPException(status_code=401, detail='请先登录')
    return payload


def require_admin(authorization: Annotated[str | None, Header()] = None) -> dict:
    """强制管理员：游客 → 401；角色非 admin → 403。返回 payload。"""
    payload = verify_token(authorization)
    if not payload:
        raise HTTPException(status_code=401, detail='请先登录')
    role = payload.get('role') or 'staff'
    if role != 'admin':
        raise HTTPException(status_code=403, detail='需要管理员权限')
    return payload
