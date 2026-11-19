# -*- coding: utf-8 -*-
"""账号库 = 业务库的 mt_user 表（开放版多用户）。

auth_sessions.py 通过两处钩子实时核验账号：
  _app_ready()      —— True = 已配置账号库
  appdb_fresh_user  —— 按 uid/username 查 mt_user
"""
from .mtables import store as _store


def _app_ready() -> bool:
    return True


def appdb_fresh_user(uid=None, username=None):
    try:
        return _store.get_user(uid=uid, username=username)
    except Exception:
        return None
