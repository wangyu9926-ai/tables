# -*- coding: utf-8 -*-
"""共享层：引擎导出、进程内缓存、权限/列/行的读写工具、JSON 序列化、缓存失效、常量。

`backend/app.py` 只负责装配（FastAPI 实例、异常处理器、中间件、include_router）；
功能路由在 `backend/modules/*.py`，都 `from backend.deps import *`。
本文件**不导入** app.py，所以不会出现循环导入。
"""
import csv
import asyncio
import gzip
import hashlib
import io
import json
import math
import os
import secrets
import threading
import time
import re
from datetime import date, datetime, timedelta
import pymysql
from urllib.parse import quote
from fastapi import FastAPI, Header, HTTPException, Request, UploadFile, File, Form
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
try:
    from .engine import (COL_TYPES, columns, compute_formulas, conn, eval_formula, ex, jdump,
                         jload, norm_val, q, slug, validate_expr, audit, init_schema, clear_xt,
                         invalidate_xt_table, flush_audit, CELL_MAX_TEXT, CELL_MAX_MULTI, csp_for_html)
    from . import sync as kb_sync
    from . import changes as kb_changes
except ImportError:               # 直接 python backend/app.py 调试
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from engine import (COL_TYPES, columns, compute_formulas, conn, eval_formula, ex, jdump,
                        jload, norm_val, q, slug, validate_expr, audit, init_schema, clear_xt,
                        invalidate_xt_table, flush_audit, CELL_MAX_TEXT, CELL_MAX_MULTI, csp_for_html)
    import sync as kb_sync
    import changes as kb_changes
import contextvars
try:
    import inspect
    from starlette.middleware import gzip as _gzip_middleware
    # 新版压缩中间件遇到已经带 Content-Encoding 的响应会跳过；老版会再压一次（页面就坏了），先确认
    _GZIP_PRECOMPRESSED = 'content-encoding' in inspect.getsource(_gzip_middleware)
except Exception:
    _GZIP_PRECOMPRESSED = False

BASE = os.path.dirname(os.path.abspath(__file__))

FRONTEND = os.path.join(os.path.dirname(BASE), "frontend")

def _check_len(value, limit, what):
    """文本长度按数据库列宽先校验（VARCHAR 按字符数计）。"""
    if value is not None and len(value) > limit:
        raise HTTPException(400, f'{what}不能超过 {limit} 个字')

def _col_width(w, default=0):
    """列宽在库里是 SMALLINT：负数、超过 32767、非数字都按默认值处理。"""
    try:
        w = int(w)
    except Exception:
        return default
    return w if 0 <= w <= 32767 else default

def _as_ints(items):
    """请求体里的列表只要其中的整数（列 id / 表 id）；别的元素一律丢弃，不能带进 SQL。"""
    return [int(x) for x in (items or []) if isinstance(x, int) and not isinstance(x, bool)]

_META = {'data': None}

_META_VERS = {'gen': 0, 'cnt': 0}     # gen：结构变了；cnt：只是行数变了
# 注意：`from backend.deps import *` 复制的是绑定，标量在别的模块里只是导入时的副本
# （拆分时踩过：元信息缓存永不失效）。要跨模块共享的可变状态一律放 dict/对象里。

_META_LOCK = threading.Lock()


_META_JSON = {}       # (元信息版本, 行数版本, 授权视图) -> 序列化好的 /api/meta 响应字节

def _json_response(obj):
    """接口 JSON 直接序列化。FastAPI 默认要先把每个值逐个“编码”一遍再 dumps，
    对几十行 × 几十列的行数据、80KB 的元信息来说，那一遍纯 Python 递归是每个请求最大的固定开销；
    这里的数据本来就只有 JSON 基本类型，直接 dumps 结果完全一样。"""
    try:
        body = json.dumps(obj, ensure_ascii=False, allow_nan=False, indent=None, separators=(',', ':'))
    except (TypeError, ValueError):
        return JSONResponse(obj)          # 出现了基本类型以外的值：退回 FastAPI 的标准流程
    return Response(content=body.encode('utf-8'), media_type='application/json')

_ROWS = {}          # table_id -> 原始行列表（远程库取一次，读多写少）

_ROWS_VER = {}      # table_id -> 行缓存版本（原地更新也要让派生视图失效）

_ROWS_LOCKS = {}

_ROWS_LOCKS_GUARD = threading.Lock()

_VIEW = {}          # table_id -> {'ver','rows','facets'}：转换 + facets 只算一次

_VIEW_LOCKS = {}

_VIEW_LOCKS_GUARD = threading.Lock()

def _rows_lock(tid):
    with _ROWS_LOCKS_GUARD:
        lk = _ROWS_LOCKS.get(tid)
        if lk is None:
            lk = threading.Lock()
            _ROWS_LOCKS[tid] = lk
        return lk

def _view_lock(tid):
    with _VIEW_LOCKS_GUARD:
        lk = _VIEW_LOCKS.get(tid)
        if lk is None:
            lk = threading.Lock()
            _VIEW_LOCKS[tid] = lk
        return lk

def _touch_rows(tid):
    """行数据变了：递增版本并丢掉该表的派生视图。"""
    _ROWS_VER[tid] = _ROWS_VER.get(tid, 0) + 1
    _VIEW.pop(tid, None)

def _drop_rows(tid):
    _ROWS.pop(tid, None)
    _touch_rows(tid)

def _invalidate_all():
    """结构/项目/看板/导入等变更：元信息和全部行缓存一起失效。"""
    _META_VERS['gen'] += 1
    _META['data'] = None
    for tid in set(_ROWS) | set(_VIEW) | set(_ROWS_LOCKS):
        _ROWS_VER[tid] = _ROWS_VER.get(tid, 0) + 1
    _ROWS.clear()
    _VIEW.clear()
    clear_xt()

def _bump_meta_count(tid, delta):
    """行增删只改 count，不用把 17 张表的元信息全部重查一遍。"""
    d = _META['data']
    if not d:
        # 元信息正在重建/尚未加载：让进行中的构建结果作废，下次按新 count 重建
        _META_VERS['gen'] += 1
        return
    for t in d.get('tables') or []:
        if t['id'] == tid:
            t['count'] = max(0, int(t.get('count') or 0) + delta)
            _META_VERS['cnt'] += 1
            return

def _invalidate_after(method, path, inserted=0):
    """按路径精确失效：单元格编辑不再触发全库缓存重建。"""
    if path in ('/api/formula/eval', '/api/import/preview'):
        return
    if path.startswith('/api/upload/image') or path.startswith('/api/images/trash'):
        return
    if re.fullmatch(r'/api/tables/\d+/apikeys(?:/\d+)?', path):
        return
    m = re.fullmatch(r'/api/tables/(\d+)/rows/\d+/move', path)
    if m:
        # 只是顺序变了：行内容没变，但列表顺序要重拉；跨表索引与顺序无关，仍然只清这一张
        tid = int(m.group(1))
        _drop_rows(tid)
        invalidate_xt_table(tid)
        return
    m = re.fullmatch(r'/api/tables/(\d+)/rows/bulk', path)
    if m:
        tid = int(m.group(1))
        _drop_rows(tid)
        invalidate_xt_table(tid)
        _bump_meta_count(tid, int(inserted or 0))
        return
    m = re.fullmatch(r'/api/tables/(\d+)/rows(?:/(\d+))?', path)
    if m:
        tid = int(m.group(1))
        if method == 'PATCH' and m.group(2):
            # 行本身由 update_row 就地同步进 _ROWS；跨表索引只重建这一张表的
            invalidate_xt_table(tid)
            return
        if method in ('POST', 'DELETE'):
            _drop_rows(tid)
            invalidate_xt_table(tid)
            _bump_meta_count(tid, 1 if method == 'POST' else -1)
            return
    m = re.fullmatch(r'/api/in/(\d+)', path)
    if m:
        # 接口写入只动了这一张表的行：只失效这张表，不再清空所有人的缓存
        tid = int(m.group(1))
        _drop_rows(tid)
        invalidate_xt_table(tid)
        _bump_meta_count(tid, int(inserted or 0))
        return
    _invalidate_all()

def _raw_rows(tid):
    rows = _ROWS.get(tid)
    if rows is not None:
        return rows
    with _rows_lock(tid):
        rows = _ROWS.get(tid)
        if rows is None:
            rows = q("SELECT id,data,ver,created_at,updated_at FROM kb_row "
                     "WHERE table_id=%s AND deleted=0 ORDER BY sort,id", (tid,))
            _ROWS[tid] = rows
    return rows

def _cache_row_update(tid, rid, data, ver):
    """单元格更新后直接改缓存里的这一行，避免下次读取重拉整张表。"""
    _touch_rows(tid)
    rows = _ROWS.get(tid)
    if rows is None:
        return
    for r in rows:
        if r['id'] == rid:
            r['data'] = jdump(data)
            r['ver'] = ver
            r['updated_at'] = datetime.now()
            return

def _sync_target_changed(tid, delta):
    """汇总引擎写总表后，失效总表行缓存；插入/删除时同步计数。
    只动总表自己的行，跨表索引也只清总表这一张的。"""
    _drop_rows(tid)
    invalidate_xt_table(tid)
    if delta:
        _bump_meta_count(tid, delta)
    # 汇总写完总表也要通知页面：别人正开着总表，能自己刷新
    kb_changes.bump(tid, 'sync', (), 'sync')

def _facets_for(data_rows, cols):
    facets = {}
    for c in cols:
        if c['type'] not in ('select', 'checkbox', 'multiselect') and c['type'] != 'text':
            continue
        cnt = {}
        for r in data_rows:
            v = r['data'].get(c['key'])
            if c['type'] == 'multiselect':
                arr = v if isinstance(v, list) else ([v] if v not in (None, '') else [])
                if not arr:
                    cnt['（空）'] = cnt.get('（空）', 0) + 1
                else:
                    for x in arr:
                        k = str(x)
                        if k.strip():
                            cnt[k] = cnt.get(k, 0) + 1
            else:
                k = '（空）' if v is None or str(v).strip() == '' else str(v)
                cnt[k] = cnt.get(k, 0) + 1
        if 1 < len(cnt) <= 12:
            facets[c['key']] = dict(sorted(cnt.items(), key=lambda kv: -kv[1]))
    return facets

def _table_view(tid, cols):
    """把「JSON 字符串 → dict」和筛选选项统计缓存起来。

    大表每次翻页/换 Sheet 都重新转换 5000 行会把单核 CPU 吃满，
    多人同时用就排队；这里按表只算一次，写操作后失效重建。
    """
    ver = _ROWS_VER.get(tid, 0)
    v = _VIEW.get(tid)
    if v is not None and v['ver'] == ver:
        return v
    with _view_lock(tid):
        ver = _ROWS_VER.get(tid, 0)
        v = _VIEW.get(tid)
        if v is not None and v['ver'] == ver:
            return v
        recs = _raw_rows(tid)
        data_rows = [{'id': r['id'], 'ver': r['ver'], 'created_at': r['created_at'],
                      'updated_at': r['updated_at'], 'data': jload(r['data'])} for r in recs]
        v = {'ver': ver, 'rows': data_rows, 'facets': _facets_for(data_rows, cols)}
        if _ROWS_VER.get(tid, 0) == ver:
            _VIEW[tid] = v
        return v

AUTH_USER = contextvars.ContextVar('kb_auth_user', default=None)

_RANK = {'viewer': 1, 'editor': 2}

_PERM_TTL = 8.0

_PERM_CACHE = {}          # uid -> (ts, project_roles, table_roles)

_PERM_GEN = {}            # uid -> 授权视图版本号（每次重新读授权 +1）

def _uid():
    try:
        return int((AUTH_USER.get() or {}).get('uid') or 0)
    except Exception:
        return 0

def clear_perm_cache():
    _PERM_CACHE.clear()

def _perm_scopes(uid):
    now = time.time()
    hit = _PERM_CACHE.get(uid)
    if hit and now - hit[0] < _PERM_TTL:
        return hit[1], hit[2]
    pm, tm = {}, {}
    for p in q("SELECT scope_type,scope_key,role FROM kb_perm WHERE user_id=%s", (uid,)):
        key = p['scope_key']
        if p['scope_type'] == 'table':
            try:
                k = int(key)
            except Exception:
                continue
            tm[k] = p['role']          # 表级显式覆盖（含 none=禁止）
        elif p['scope_type'] == 'project':
            pm[key] = p['role']
    _PERM_CACHE[uid] = (now, pm, tm)
    _PERM_GEN[uid] = _PERM_GEN.get(uid, 0) + 1      # 授权视图换了一版（只增不减）
    if len(_PERM_CACHE) > 512:
        _PERM_CACHE.clear()
    return pm, tm

def table_role(tid, project=None):
    """当前请求对该表的有效角色：editor / viewer / None（无权限）。"""
    u = AUTH_USER.get()
    if u is None or u.get('role') == 'admin':
        return 'editor'                     # 本机未启网关 / 管理员
    if u.get('role') == 'guest':
        return None                         # 游客：只能看项目列表
    pm, tm = _perm_scopes(_uid())
    tr = tm.get(int(tid))
    if tr == 'none':
        return None                     # 表级显式禁止（即使项目有授权）
    return tr or pm.get(project or '')

def need_table(tid, project=None, write=False):
    u = AUTH_USER.get()
    if u is not None and u.get('role') == 'guest':
        raise HTTPException(401, '游客仅可查看项目列表，登录后查看数据：/login')
    role = table_role(tid, project)
    if role is None:
        raise HTTPException(403, '没有该表的访问权限')
    if write and role != 'editor':
        raise HTTPException(403, '只读授权，不能修改数据')
    return role

def project_role(project):
    """当前请求对该项目的角色：editor / viewer / None。"""
    u = AUTH_USER.get()
    if u is None or u.get('role') == 'admin':
        return 'editor'
    if u.get('role') == 'guest':
        return None
    pm, _tm = _perm_scopes(_uid())
    return pm.get(project or '')

def _project_exists(project):
    if q("SELECT 1 AS n FROM kb_project WHERE name=%s AND deleted=0", (project,), one=True):
        return True
    return bool(q("SELECT 1 AS n FROM kb_table WHERE project=%s AND deleted=0 LIMIT 1",
                  (project,), one=True))

def project_can_view(project):
    """分析页只看项目级授权：admin / 项目 viewer / 项目 editor；表级授权不单独放行。"""
    return project_role(project) in ('viewer', 'editor')

def need_project(project, write=False):
    u = AUTH_USER.get()
    if u is not None and u.get('role') == 'guest':
        raise HTTPException(401, '游客仅可查看项目列表，登录后查看数据：/login')
    if not project:
        raise HTTPException(400, '缺少项目名')
    if not _project_exists(project):
        raise HTTPException(404, '项目不存在')
    role = project_role(project)
    if write:
        if role != 'editor':
            raise HTTPException(403, '需要项目编辑权限')
        return role
    if not project_can_view(project):
        raise HTTPException(403, '没有该项目的访问权限')
    return role or 'viewer'

def _meta_table(tid):
    """从已加载的元信息缓存取表；缓存没建好时返回 None，由调用方查库。"""
    d = _META['data']
    if not d:
        return None
    for t in d.get('tables') or []:
        if t['id'] == tid:
            return t
    return None

def _table_columns(tid, include_hidden=False):
    t = _meta_table(tid)
    if t is not None:
        cols = t.get('columns') or []
        return cols if include_hidden else [c for c in cols if not c['hidden']]
    return columns(tid, include_hidden)

def _need(tid):
    t = _meta_table(tid)
    if t is not None:
        return t
    t = q("SELECT id,name,note,project,group_name,icon,sort FROM kb_table WHERE id=%s AND deleted=0", (tid,), one=True)
    if not t:
        raise HTTPException(404, '表不存在')
    return t

def _need_admin():
    """结构管理（建表/删表/项目管理）只允许管理员，前端隐藏入口之外接口也要挡住。"""
    u = AUTH_USER.get()
    if u is None or u.get('role') != 'admin':
        raise HTTPException(403, '需要管理员权限')

def _need_fields(tid):
    """字段管理：admin，或被下发 can_fields 且对该表有编辑权的人。
    不能超过自身表权限：只读/无权的表仍然不能动字段。"""
    u = AUTH_USER.get()
    if u is None:
        raise HTTPException(403, '需要字段管理权限')
    if u.get('role') == 'admin':
        return
    if not u.get('can_fields'):
        raise HTTPException(403, '需要字段管理权限（由管理员下发）')
    t = _need(tid)
    need_table(tid, t.get('project') or '', write=True)

def _xt_check_for(project):
    """非管理员写公式时：跨表函数引用的每一张表，都必须是自己有权读的表。
    管理员返回 None（不限制）。"""
    u = AUTH_USER.get()
    if u is None or u.get('role') == 'admin':
        return None
    return lambda tid: table_role(tid, project) is not None

def _ts(v):
    if v is None:
        return ''
    return str(v)[:19]

def _page_has_visible_chart(page, table_map):
    cfg = page.get('config')
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except Exception:
            cfg = {}
    charts = cfg.get('charts') if isinstance(cfg, dict) else None
    for c in charts or []:
        try:
            tid = int(c.get('table_id'))
        except Exception:
            continue
        t = table_map.get(tid)
        if not t or t.get('project') != page.get('project'):
            continue
        if table_role(tid, t.get('project')) is not None:
            return True
    return False

def _safe_meta(d):
    tables = _apply_visibility(d['tables'])
    prjs = [dict(p) for p in _visible_projects(d['projects'], tables)]
    for p in prjs:
        r = project_role(p['name'])
        p['analysis_role'] = r if r in ('viewer', 'editor') else None
    pages = d.get('analysis_pages', [])
    u = AUTH_USER.get()
    if u is not None and u.get('role') == 'guest':
        pages = []
    elif u is not None and u.get('role') != 'admin':
        pm, _tm = _perm_scopes(_uid())
        table_map = {t['id']: t for t in d['tables']}
        pages = [p for p in pages
                 if p.get('project') in pm and _page_has_visible_chart(p, table_map)]
    pages = [{k: p.get(k) for k in ('id', 'project', 'name', 'sort')} for p in pages]
    return {'tables': tables, 'projects': prjs, 'analysis_pages': pages,
            'col_types': d['col_types']}

def _apply_visibility(tables):
    """按当前用户过滤表，并给每张表标注 my_role。游客看不到任何表。"""
    u = AUTH_USER.get()
    if u is not None and u.get('role') == 'guest':
        return []
    if u is None or u.get('role') == 'admin':
        role = 'editor'
        return [{**t, 'my_role': role} for t in tables]
    pm, tm = _perm_scopes(_uid())
    out = []
    for t in tables:
        tr = tm.get(t['id'])
        if tr == 'none':
            continue
        r = tr or pm.get(t['project'] or '')
        if r:
            out.append({**t, 'my_role': r})
    return out

def _visible_projects(prjs, tables):
    u = AUTH_USER.get()
    if u is None or u.get('role') in ('admin', 'guest'):
        return prjs
    pm, tm = _perm_scopes(_uid())
    names = {p['name'] for p in prjs if p['name'] in pm}
    for t in tables:
        if t['id'] in tm and tm[t['id']] != 'none' and t.get('project') in {p['name'] for p in prjs}:
            names.add(t['project'])
    return [p for p in prjs if p['name'] in names]

def _build_meta():
    pages = q("SELECT id,project,name,sort,config FROM kb_dashboard "
              "WHERE deleted=0 AND is_page=1 ORDER BY project,sort,id")
    tables = q("SELECT id,name,note,project,group_name,icon,sort FROM kb_table WHERE deleted=0 ORDER BY sort,id")
    if not tables:
        prjs = [{'name': r['name'], 'icon': r['icon'], 'sort': r['sort']}
                for r in q("SELECT name,icon,sort FROM kb_project WHERE deleted=0 ORDER BY sort,name")]
        return {'tables': [], 'projects': prjs, 'analysis_pages': pages,
                'col_types': list(COL_TYPES)}
    counts = {r['table_id']: r['n'] for r in q(
        "SELECT table_id, COUNT(*) AS n FROM kb_row WHERE deleted=0 GROUP BY table_id")}
    colrows = q("SELECT table_id,id,`key`,label,type,options,option_colors,expr,width,sort,required,hidden "
                "FROM kb_column WHERE deleted=0 ORDER BY table_id,sort,id")
    rels_row = q("SELECT from_tid,from_field,to_tid,to_field FROM kb_relation")
    cols_by_tid, rels_by_tid = {}, {}
    for r in colrows:
        o = r.pop('options')
        if isinstance(o, str):
            try:
                o = json.loads(o)
            except Exception:
                o = None
        r['options'] = o
        oc = r.pop('option_colors', None)
        if isinstance(oc, str):
            try:
                oc = json.loads(oc)
            except Exception:
                oc = None
        r['option_colors'] = oc
        r['required'] = bool(r['required'])
        r['hidden'] = bool(r['hidden'])
        cols_by_tid.setdefault(r.pop('table_id'), []).append(r)
    for r in rels_row:
        rels_by_tid.setdefault(r['from_tid'], []).append(r)
        if r['to_tid'] != r['from_tid']:
            rels_by_tid.setdefault(r['to_tid'], []).append(r)
    sync_src = {}
    for r in q("SELECT s.target_tid, t.name FROM kb_sync s JOIN kb_table t ON t.id=s.source_tid ORDER BY s.id"):
        sync_src.setdefault(r['target_tid'], []).append(r['name'])
    out = [{'id': t['id'], 'name': t['name'], 'note': t['note'], 'project': t['project'],
            'group_name': t['group_name'], 'icon': t['icon'], 'sort': t['sort'],
            'count': counts.get(t['id'], 0),
            'columns': cols_by_tid.get(t['id'], []),
            'relations': rels_by_tid.get(t['id'], []),
            # 这张表是汇总总表时，列出它的子表：前端据此提示“改/删镜像行会同步回子表”
            'sync_sources': sync_src.get(t['id'], [])} for t in tables]
    prjs = [{'name': r['name'], 'icon': r['icon'], 'sort': r['sort']}
            for r in q("SELECT name,icon,sort FROM kb_project WHERE deleted=0 ORDER BY sort,name")]
    known = {p['name'] for p in prjs}
    for r in q("SELECT project FROM kb_table WHERE deleted=0 AND project<>'' GROUP BY project ORDER BY MIN(sort)"):
        if r['project'] not in known:
            prjs.append({'name': r['project'], 'icon': '📁', 'sort': 999})
    return {'tables': out, 'projects': prjs, 'analysis_pages': pages,
            'col_types': list(COL_TYPES)}

def _meta_view():
    """当前请求的“授权视图”标识：同一个人、同一版授权看到的元信息完全一样，
    所以序列化结果可以按视图缓存（管理员和未启网关的本机都看全部）。"""
    u = AUTH_USER.get()
    if u is None or u.get('role') == 'admin':
        return 'all'
    if u.get('role') == 'guest':
        return 'guest'
    uid = _uid()
    _perm_scopes(uid)                      # 授权一变，版本号就变
    return (uid, _PERM_GEN.get(uid, 0))

def _renumber_formula_refs(project, pairs):
    """表/组改名后，同步更新同项目公式列里写死的「表名」引用。"""
    pairs = [(o, n) for o, n in (pairs or []) if o and n and o != n]
    if not pairs or not project:
        return
    rows = q("SELECT c.id, c.table_id, c.expr FROM kb_column c JOIN kb_table t ON t.id = c.table_id "
             "WHERE c.type='formula' AND c.deleted=0 AND t.deleted=0 AND t.project=%s", (project,))
    for r in rows:
        e = r['expr']
        if not e:
            continue
        ne = e
        for old, new in pairs:
            ne = ne.replace('"' + old + '"', '"' + new + '"')
        if ne != e:
            ex("UPDATE kb_column SET expr=%s WHERE id=%s", (ne, r['id']))
            # 记在公式所在的那张表名下（之前错记成列 id，这张表的变动历史里看不到这条）
            audit(r['table_id'], None, 'update_column', {'expr_ref': 'table_renamed'})

def _jdump_col(v):
    """列定义里的 options / option_colors 要存进 JSON 列：NaN / Infinity 不是合法 JSON，数据库会拒收，先在这里拦下。"""
    if v is None:
        return None
    try:
        return json.dumps(v, ensure_ascii=False, allow_nan=False)
    except ValueError:
        raise HTTPException(400, '列定义里有无效的数值（NaN / Infinity）')

def _json_safe_numbers(d):
    """超过 2^53 的整数在 JavaScript 里会丢精度（订单号 / 商品 ID / 手机号一旦被当成数字就少几位）。
    接口里把这类整数换成字符串返回，页面显示的就是原始数字；写库仍然是精确整数。
    真正的办法是这类列用文本（导入时 16 位以上的纯数字会自动按文本建列）。"""
    if not isinstance(d, dict):
        return d
    for k, v in list(d.items()):
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, int) and abs(v) > 9007199254740992:
            d[k] = str(v)
        elif isinstance(v, float) and not math.isfinite(v):
            d[k] = None
        elif isinstance(v, list):
            d[k] = [str(x) if isinstance(x, int) and not isinstance(x, bool) and abs(x) > 9007199254740992
                    else x for x in v]
    return d

def _rows_view(tid, recs, cols, project=''):
    formula_cols = [c for c in cols if c['type'] == 'formula']
    out = []
    for r in recs:
        d = compute_formulas(jload(r['data']), cols, project) if formula_cols else dict(jload(r['data']))
        out.append({'id': r['id'], 'data': _json_safe_numbers(d), 'ver': r['ver'],
                    'created_at': _ts(r['created_at']), 'updated_at': _ts(r['updated_at'])})
    return out

def _commenter():
    u = AUTH_USER.get() or {}
    return int(u.get('uid') or 0), str(u.get('username') or 'local')[:64]

_CSV_DANGER = ('=', '+', '-', '@', '\t', '\r')

def _csv_cell(v):
    """导出 CSV 防公式注入：以 = + - @ 开头的文本，用 Excel 打开时会被当成公式执行。"""
    if isinstance(v, str) and v[:1] in _CSV_DANGER:
        return "'" + v
    return v

_IMG_MAX = 8 * 1024 * 1024

_IMG_TABLE_MAX = int(os.environ.get('KB_IMAGE_TABLE_MB', '512')) * 1024 * 1024

def _image_kind(raw):
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if raw.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if raw.startswith(b'GIF87a') or raw.startswith(b'GIF89a'):
        return 'image/gif'
    if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        return 'image/webp'
    if raw.startswith(b'BM'):
        return 'image/bmp'
    return None

def _cell_iids(v):
    """从单元格值里解析图片 id（值形如 /api/tables/{tid}/images/{iid}）。"""
    if not v:
        return []
    return [int(m.group(1)) for m in re.finditer(r'/api/tables/\d+/images/(\d+)', str(v))]

def _trash_image(iid):
    """图片进回收站（软删，可在管理员「图片回收站」里恢复/彻底删除）。"""
    ex("UPDATE kb_image SET deleted=1, deleted_at=NOW() WHERE id=%s AND deleted=0", (iid,))

_KEY_PREFIX = 'kbk_'

def _key_hash(plain: str) -> str:
    return hashlib.sha256(plain.encode('utf-8')).hexdigest()

def _key_auth(tid: int, token: str) -> dict:
    """校验 X-KB-Token：存在、未作废、且属于这张表。"""
    token = (token or '').strip()
    if not token:
        raise HTTPException(401, '缺少 X-KB-Token 请求头')
    row = q("SELECT id, table_id, key_prefix, note, revoked FROM kb_apikey "
            "WHERE key_hash=%s", (_key_hash(token),), one=True)
    if not row or row['revoked']:
        raise HTTPException(401, '密钥无效或已作废')
    if int(row['table_id']) != int(tid):
        raise HTTPException(401, '密钥不属于这张表')
    return row

_IMP_MAX_BYTES = 20 * 1024 * 1024

_IMP_MAX_ROWS = 50000

_IMP_MAX_COLS = 300

def _imp_login():
    u = AUTH_USER.get()
    if u is not None and u.get('role') == 'guest':
        raise HTTPException(401, '游客仅可查看项目列表，登录后使用导入：/login')

def _imp_pd():
    try:
        import pandas as pd
    except ImportError:          # 服务器漏装依赖时给一句人话，别让用户看到 500
        raise HTTPException(503, '服务器缺少 pandas，Excel/CSV 导入不可用（pip install -r requirements.txt）')
    return pd

def _imp_blank(v):
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    try:
        import pandas as pd
        if v is pd.NaT or v is pd.NA:
            return True
        r = pd.isna(v)
        if not isinstance(r, (list, tuple)) and bool(r):
            return True
    except Exception:
        pass
    if isinstance(v, str) and not v.strip():
        return True
    return False

def _imp_cell(v):
    """Excel 单元格 → JSON 安全值（日期转字符串，整数化浮点）。"""
    if _imp_blank(v):
        return None
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M') if (v.hour or v.minute or v.second) else v.strftime('%Y-%m-%d')
    if isinstance(v, date):
        return v.strftime('%Y-%m-%d')
    try:
        if hasattr(v, 'item') and not isinstance(v, (str, bytes)):
            v = v.item()
    except Exception:
        pass
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M') if (v.hour or v.minute or v.second) else v.strftime('%Y-%m-%d')
    if isinstance(v, date):
        return v.strftime('%Y-%m-%d')
    if isinstance(v, bool):
        return v
    if isinstance(v, float) and math.isfinite(v) and v.is_integer():
        return int(v)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    s = str(v).strip()
    return s or None

def _imp_num(v):
    if _imp_blank(v):
        return None
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            return None
        return int(v) if isinstance(v, float) and v.is_integer() else v
    s = str(v).strip().replace(',', '').replace('，', '')
    m = re.fullmatch(r'(-?\d+(?:\.\d+)?)\s*(亿|万|千|w|W|k|K)?%?', s)
    if not m:
        return None
    n = float(m.group(1))
    u = m.group(2) or ''
    if u == '亿':
        n *= 1e8
    elif u in ('万', 'w', 'W'):
        n *= 1e4
    elif u in ('千', 'k', 'K'):
        n *= 1e3
    return int(n) if n.is_integer() else n

def _imp_date(v):
    if _imp_blank(v):
        return None
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d')
    if isinstance(v, date):
        return v.strftime('%Y-%m-%d')
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        n = int(v)
        if 20000 <= n <= 90000:
            return (date(1899, 12, 30) + timedelta(days=n)).strftime('%Y-%m-%d')
        s = str(n)
        if len(s) == 8 and 19000101 <= n <= 21001231:
            return f'{s[:4]}-{s[4:6]}-{s[6:]}'
        return None
    s = str(v).strip()
    if re.fullmatch(r'\d{8}', s):
        return f'{s[:4]}-{s[4:6]}-{s[6:]}'
    m = re.match(r'^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})', s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime('%Y-%m-%d')
        except Exception:
            return None
    return None

def _imp_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v).strip().lower()
    return s in ('true', '1', '是', 'yes', 'y', '✓', '已勾选', '勾选')

def _imp_text(v):
    if _imp_blank(v):
        return ''
    if isinstance(v, bool):
        return '是' if v else '否'
    if isinstance(v, float) and math.isfinite(v) and v.is_integer():
        return str(int(v))
    return str(v).strip()

def _imp_multi(v):
    if isinstance(v, list):
        return [str(x).strip() for x in v if not _imp_blank(x)]
    s = _imp_text(v)
    if not s:
        return []
    return [x.strip() for x in re.split(r'[,，;；、\n]+', s) if x.strip()]

def _imp_multi_options(vals):
    uniq, seen = [], set()
    for v in vals:
        for x in _imp_multi(v):
            if x not in seen:
                seen.add(x)
                uniq.append(x)
    return uniq

def _imp_infer(header, vals):
    non = [v for v in vals if not _imp_blank(v)]
    if not non:
        return 'text', None
    if all(isinstance(v, bool) for v in non):
        return 'checkbox', None
    bool_words = {'true', 'false', '是', '否', 'yes', 'no', 'y', 'n', '1', '0'}
    if all(str(v).strip().lower() in bool_words for v in non):
        return 'checkbox', None
    ds = [_imp_date(v) for v in non]
    if all(d is not None for d in ds):
        if all(isinstance(v, str) and re.search(r'[-/年月.]', v) for v in non):
            return 'date', None
        if any(k in (header or '') for k in ('日期', '时间')):
            return 'date', None
    # 长数字（订单号 / 商品 ID / 手机号 / 身份证 …）按文本建列：做成数字列，前端 JS 只有 15~16 位精度，
    # 落库是对的，但显示和再编辑会少几位；带前导 0 的编号也一样。
    if all(re.fullmatch(r'\d{16,}', str(v).strip()) for v in non):
        return 'text', None
    if any(k in (header or '') for k in ('ID', 'Id', 'id', '编号', '单号', '订单号', '手机', '电话',
                                         '身份证', '卡号', '条码', '邮编', '账号')) \
            and all(re.fullmatch(r'\d+', str(v).strip()) for v in non):
        return 'text', None
    ns = [_imp_num(v) for v in non]
    if all(n is not None for n in ns):
        if any(k in (header or '') for k in ('日期', '时间')):
            return 'date', None
        return 'number', None
    uniq, seen = [], set()
    for v in non:
        s = _imp_text(v)
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    bad = ('姓名', '名称', '昵称', '账号', '标题', '备注', '链接', '编号', 'ID', 'id')
    if (1 < len(uniq) <= 30 and len(uniq) <= max(2, int(len(non) * 0.6))
            and not any(k in (header or '') for k in bad)
            and all(len(x) <= 24 and '\n' not in x for x in uniq)):
        return 'select', uniq
    return 'text', None

def _imp_load(raw, filename, sheet, header_row):
    pd = _imp_pd()
    if header_row < 1:
        raise HTTPException(400, '表头行必须大于 0')
    bio = io.BytesIO(raw)
    lower = (filename or '').lower()
    if lower.endswith('.csv'):
        sheets = ['CSV']
        sel = 'CSV'
        df = None
        last = None
        for enc in ('utf-8-sig', 'gb18030', 'utf-8'):
            try:
                bio.seek(0)
                df = pd.read_csv(bio, header=header_row - 1, dtype=object, encoding=enc)
                break
            except Exception as e:
                last = e
        if df is None:
            raise HTTPException(400, f'CSV 读取失败：{last}')
    else:
        bio.seek(0)
        try:
            xls = pd.ExcelFile(bio)
        except Exception as e:
            raise HTTPException(400, f'Excel 读取失败：{e}')
        sheets = list(xls.sheet_names)
        if not sheets:
            raise HTTPException(400, '工作簿里没有工作表')
        sel = sheet or sheets[0]
        if sel not in sheets:
            raise HTTPException(400, '所选工作表不存在')
        try:
            df = pd.read_excel(xls, sheet_name=sel, header=header_row - 1, dtype=object)
        except Exception as e:
            raise HTTPException(400, f'工作表读取失败：{e}')
    if df is None or df.shape[1] == 0:
        raise HTTPException(400, '没有读到列，请检查表头行')
    df = df.dropna(how='all')
    keep = []
    for i in range(df.shape[1]):
        col = df.iloc[:, i]
        if any(not _imp_blank(v) for v in col.tolist()):
            keep.append(i)
    if not keep:
        raise HTTPException(400, '没有读到数据行')
    if len(keep) > _IMP_MAX_COLS:
        raise HTTPException(400, f'列数超过 {_IMP_MAX_COLS}')
    headers, used = [], set()
    for i in keep:
        raw_name = df.columns[i]
        s = '' if _imp_blank(raw_name) else str(raw_name).strip()
        s = s.rstrip(':：').strip()
        if not s:
            s = f'列{i + 1}'
        base, n = s, 2
        while s in used:
            s = f'{base}（{n}）'
            n += 1
        used.add(s)
        headers.append((i, s))
    rows = []
    for tup in df.itertuples(index=False, name=None):
        vals = [_imp_cell(tup[i] if i < len(tup) else None) for i, _h in headers]
        if all(v is None for v in vals):
            continue
        rows.append(vals)
    if len(rows) > _IMP_MAX_ROWS:
        raise HTTPException(400, f'行数超过 {_IMP_MAX_ROWS}，请拆分文件')
    return sheets, sel, [h for _i, h in headers], rows

def _imp_coerce(v, col, collect):
    t = col['type']
    if _imp_blank(v):
        return None
    if t == 'formula':
        raise HTTPException(400, f'公式列「{col["label"]}」不能导入')
    if t == 'image':
        return None
    if t == 'number':
        return _imp_num(v)
    if t == 'date':
        return _imp_date(v) or _imp_text(v)
    if t == 'checkbox':
        return _imp_bool(v)
    if t == 'multiselect':
        arr = _imp_multi(v)
        if len(arr) > CELL_MAX_MULTI:
            raise HTTPException(400, f'列「{col["label"]}」有单元格选了 {len(arr)} 项，超过上限 {CELL_MAX_MULTI} 项')
        collect.setdefault(col['key'], [])
        for x in arr:
            if x not in collect[col['key']]:
                collect[col['key']].append(x)
        return arr
    s = _imp_text(v)
    if len(s) > CELL_MAX_TEXT:
        raise HTTPException(400, f'列「{col["label"]}」有单元格超过 {CELL_MAX_TEXT} 字，请精简后再导入')
    if t == 'select' and s:
        collect.setdefault(col['key'], [])
        if s not in collect[col['key']]:
            collect[col['key']].append(s)
    return s

def _imp_bulk(tid, rows, replace=False):
    if not rows:
        return 0
    con = conn()
    cur = con.cursor()
    try:
        if replace:
            cur.execute("UPDATE kb_row SET deleted=1 WHERE table_id=%s", (tid,))
        cur.execute("SELECT COALESCE(MAX(sort),0) AS m FROM kb_row WHERE table_id=%s", (tid,))
        row = cur.fetchone()
        base = int((row or {}).get('m') or 0)
        params = [(tid, jdump(d), base + i + 1) for i, d in enumerate(rows)]
        for i in range(0, len(params), 200):
            cur.executemany("INSERT INTO kb_row(table_id,data,sort) VALUES(%s,%s,%s)", params[i:i + 200])
        con.commit()
        return len(rows)
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def _imp_add_column(tid, label, typ, options, sort):
    key = slug(label) or 'col'
    base = key
    n = 2
    while q("SELECT id FROM kb_column WHERE table_id=%s AND `key`=%s", (tid, key), one=True):
        key = f'{base}_{n}'
        n += 1
    ex("INSERT INTO kb_column(table_id,`key`,label,type,options,sort) VALUES(%s,%s,%s,%s,%s,%s)",
       (tid, key, label, typ, jdump(options), sort))
    audit(tid, None, 'create_column', {'label': label, 'type': typ})
    return {'key': key, 'label': label, 'type': typ, 'options': options}

def _imp_check_target(table_id=0, project=''):
    """导入预览的目标权限。预览也要有目标的编辑权：解析 Excel 是同步的重活，
    不能只凭“已登录”就调用（否则任何零权限账号都能让服务端解析任意文件）。
    二选一：table_id（导入到已有表）或 project（新建到哪个项目）。"""
    tid = int(table_id or 0)
    if tid:
        t = _need(tid)
        need_table(tid, t.get('project') or '', write=True)
        return
    prj = (project or '').strip()
    if prj:
        need_project(prj, write=True)
        return
    raise HTTPException(400, '缺少导入目标：请传 table_id（导入到已有表）或 project（新建到哪个项目）')

async def _read_upload(file: UploadFile, limit: int, msg: str) -> bytes:
    """分块读上传文件，读到上限就停：超限的文件不用整个读进内存。"""
    chunks, total = [], 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, msg)
        chunks.append(chunk)
    return b''.join(chunks)

def _imp_preview_sync(raw, filename, sheet, header_row):
    sheets, sel, headers, rows = _imp_load(raw, filename, sheet, header_row)
    out_cols = []
    for i, h in enumerate(headers):
        vals = [r[i] for r in rows]
        typ, opts = _imp_infer(h, vals)
        sample = []
        for v in vals:
            if not _imp_blank(v):
                sample.append('是' if v is True else ('否' if v is False else _imp_text(v)))
            if len(sample) >= 3:
                break
        out_cols.append({'index': i, 'name': h, 'inferred_type': typ, 'options': opts,
                         'sample': sample})
    return {'filename': filename, 'sheets': sheets, 'sheet': sel,
            'header_row': header_row, 'headers': out_cols,
            'rows': rows[:8], 'total_rows': len(rows), 'total_cols': len(headers)}

def _imp_int(v):
    """导入配置里的数字字段（请求体里的 JSON 字符串，没有任何类型约束）：转不成整数就回 400。"""
    try:
        return int(float(v))
    except (TypeError, ValueError, OverflowError):
        raise HTTPException(400, '导入配置里的数字不合法')

def _imp_commit_sync(raw, filename, cfg):
    header_row = _imp_int(cfg.get('header_row') or 1)
    sheets, sel, headers, rows = _imp_load(raw, filename, cfg.get('sheet') or '', header_row)
    target = cfg.get('target') or 'table'

    if target == 'new':
        project = str(cfg.get('project') or '').strip()
        name = str(cfg.get('name') or '').strip()
        group = str(cfg.get('group_name') or '').strip()
        if not project or not name:
            raise HTTPException(400, '新建表需要项目名和表名')
        _check_len(name, 160, '表名')
        _check_len(project, 160, '项目名')
        _check_len(group, 160, '分组名')
        need_project(project, write=True)
        if q("SELECT id FROM kb_table WHERE name=%s AND deleted=0", (name,), one=True):
            raise HTTPException(409, f'表「{name}」已存在')
        old = q("SELECT id FROM kb_table WHERE name=%s AND deleted=1", (name,), one=True)
        if old:
            ex("DELETE FROM kb_column WHERE table_id=%s", (old['id'],))
            ex("DELETE FROM kb_row WHERE table_id=%s", (old['id'],))
            ex("DELETE FROM kb_table WHERE id=%s", (old['id'],))
        coldefs = cfg.get('columns')
        if not isinstance(coldefs, list) or not coldefs:
            coldefs = []
            for i, h in enumerate(headers):
                typ, opts = _imp_infer(h, [r[i] for r in rows])
                coldefs.append({'source': i, 'label': h, 'type': typ, 'options': opts})
        seen_src, cols = set(), []
        for d in coldefs:
            try:
                src = int(d.get('source'))
            except Exception:
                continue
            if src < 0 or src >= len(headers) or src in seen_src:
                continue
            if d.get('include') is False:
                continue
            typ = d.get('type') or 'text'
            if typ not in COL_TYPES or typ in ('formula', 'image'):
                typ = 'text'
            label = str(d.get('label') or headers[src] or '').strip() or str(headers[src])
            _check_len(label, 200, '列名')
            opts = d.get('options')
            if not isinstance(opts, list):
                opts = None
            if typ == 'multiselect' and not opts:
                opts = _imp_multi_options([r[src] for r in rows])
            if typ == 'select' and not opts:
                _t, opts = _imp_infer(label, [r[src] for r in rows])
                opts = opts if isinstance(opts, list) else None
            seen_src.add(src)
            cols.append({'source': src, 'label': label, 'type': typ, 'options': opts})
        if not cols:
            raise HTTPException(400, '没有可导入的列')
        # 选项先序列化好再建表：等插列时才发现 JSON 不合法，表已经建出来了，会留下没有列的空壳表
        opts_json = [_jdump_col(c.get('options')) for c in cols]
        mx = q("SELECT COALESCE(MAX(sort),0) AS m FROM kb_table", one=True)['m']
        tid = ex("INSERT INTO kb_table(name,note,project,group_name,icon,sort) VALUES(%s,%s,%s,%s,%s,%s)",
                 (name, f'导入：{filename}'[:500], project, group, '📄', int(mx) + 1))
        used_keys = set()
        by_key = {}
        for i, c in enumerate(cols):
            key = slug(c['label']) or 'col'
            base, n = key, 2
            while key in used_keys:
                key = f'{base}_{n}'
                n += 1
            used_keys.add(key)
            ex("INSERT INTO kb_column(table_id,`key`,label,type,options,sort) VALUES(%s,%s,%s,%s,%s,%s)",
               (tid, key, c['label'], c['type'], opts_json[i], i))
            c['key'] = key
            by_key[key] = {'key': key, 'label': c['label'], 'type': c['type'],
                           'options': c.get('options')}
        collect = {}
        out_rows = []
        for r in rows:
            d = {}
            for c in cols:
                v = _imp_coerce(r[c['source']], by_key[c['key']], collect)
                if v is not None and v != []:
                    d[c['key']] = v
            if d:
                out_rows.append(d)
        if not out_rows:
            raise HTTPException(400, '没有可导入的数据')
        _imp_bulk(tid, out_rows, False)
        for key, added in collect.items():
            col = by_key[key]
            old_opts = list(col.get('options') or [])
            merged = old_opts + [x for x in added if x not in old_opts]
            if merged != old_opts:
                ex("UPDATE kb_column SET options=%s WHERE table_id=%s AND `key`=%s",
                   (jdump(merged), tid, key))
        audit(tid, None, 'import_new_table', {'name': name, 'rows': len(out_rows), 'file': filename,
                                             'sheet': sel, 'columns': len(cols)})
        return {'ok': True, 'table_id': tid, 'name': name, 'inserted': len(out_rows),
                'skipped': len(rows) - len(out_rows), 'columns': len(cols)}

    tid = _imp_int(cfg.get('table_id') or 0)
    if not tid:
        raise HTTPException(400, '缺少目标表')
    t = _need(tid)
    need_table(tid, t.get('project') or '', write=True)
    mode = cfg.get('mode') or 'append'
    if mode not in ('append', 'replace'):
        raise HTTPException(400, '导入方式不正确')
    mapping = cfg.get('mapping')
    if not isinstance(mapping, list) or len(mapping) != len(headers):
        raise HTTPException(400, '列映射与文件列数不一致')
    cols = columns(tid)
    by_key = {c['key']: c for c in cols}
    by_label = {c['label']: c for c in cols}
    # 追加时的「自动新建字段」等于改表结构：要有这张表的字段管理权限才能用，
    # 否则就绕过了“管列仅限管理员 / 被下发字段能力的人”
    if any(m == '__new__' and headers[i] not in by_label for i, m in enumerate(mapping)):
        _need_fields(tid)
    explicit = [m for m in mapping if m and m != '__new__']
    if len(explicit) != len(set(explicit)):
        raise HTTPException(400, '同一字段被映射了多次')
    for m in explicit:
        c = by_key.get(m)
        if not c:
            raise HTTPException(400, f'映射字段不存在：{m}')
        if c['type'] == 'formula':
            raise HTTPException(400, f'列「{c["label"]}」是公式列，不能导入')
    max_sort = max([int(c.get('sort') or 0) for c in cols] or [0])
    targets, seen, created = [], set(), 0
    for i, m in enumerate(mapping):
        if not m:
            targets.append(None)
            continue
        if m == '__new__':
            label = headers[i]
            exist = by_label.get(label)
            if exist:
                if exist['type'] == 'formula':
                    raise HTTPException(400, f'列「{label}」是公式列，不能导入')
                col = exist
            else:
                typ, opts = _imp_infer(label, [r[i] for r in rows])
                if typ in ('formula', 'image'):
                    typ = 'text'
                max_sort += 1
                col = _imp_add_column(tid, label, typ, opts, max_sort)
                cols.append(col)
                by_label[label] = col
                created += 1
            key = col['key']
        else:
            col = by_key.get(m)
            if not col:
                raise HTTPException(400, f'映射字段不存在：{m}')
            if col['type'] == 'formula':
                raise HTTPException(400, f'列「{col["label"]}」是公式列，不能导入')
            key = m
        if key in seen:
            raise HTTPException(400, f'同一字段被映射了多次：{col["label"]}')
        seen.add(key)
        targets.append(key)
        by_key[key] = col
    if not seen:
        raise HTTPException(400, '至少映射一个字段')
    collect = {}
    out_rows = []
    for r in rows:
        d = {}
        for i, key in enumerate(targets):
            if not key:
                continue
            v = _imp_coerce(r[i], by_key[key], collect)
            if v is not None and v != []:
                d[key] = v
        if d:
            out_rows.append(d)
    if not out_rows:
        raise HTTPException(400, '没有可导入的数据')
    _imp_bulk(tid, out_rows, mode == 'replace')
    for key, added in collect.items():
        col = by_key[key]
        old_opts = list(col.get('options') or [])
        merged = old_opts + [x for x in added if x not in old_opts]
        if merged != old_opts:
            ex("UPDATE kb_column SET options=%s WHERE table_id=%s AND `key`=%s",
               (jdump(merged), tid, key))
    audit(tid, None, 'import_rows', {'rows': len(out_rows), 'mode': mode, 'file': filename,
                                     'sheet': sel, 'new_columns': created})
    try:
        kb_sync.source_bulk(tid)
    except Exception as e:
        print(f'[sync] import hook failed: {e!r}', flush=True)
    return {'ok': True, 'table_id': tid, 'inserted': len(out_rows),
            'skipped': len(rows) - len(out_rows), 'new_columns': created,
            'mode': mode}

_SYNC_TARGET_TYPES = ('text', 'number', 'date', 'select', 'multiselect', 'checkbox')

def _sync_validate(target_tid, source_tid, mapping):
    tt = _need(target_tid)
    st = _need(source_tid)
    if target_tid == source_tid:
        raise HTTPException(400, '总表和子表不能是同一张表')
    if (tt.get('project') or '') != (st.get('project') or ''):
        raise HTTPException(400, '只能汇总同一项目内的表')
    if not isinstance(mapping, dict) or not mapping:
        raise HTTPException(400, '至少映射一个字段')
    tcols = {c['key']: c for c in _table_columns(target_tid, include_hidden=True)}
    scols = {c['key']: c for c in _table_columns(source_tid, include_hidden=True)}
    clean = {}
    for tk, sk in mapping.items():
        tk, sk = str(tk), str(sk)
        tc = tcols.get(tk)
        if not tc:
            raise HTTPException(400, f'总表没有字段「{tk}」')
        if tc['type'] not in _SYNC_TARGET_TYPES:
            raise HTTPException(400, f'总表字段「{tc["label"]}」类型不支持汇总')
        if sk == kb_sync.SRC_TABLE_NAME:
            clean[tk] = sk
            continue
        sc = scols.get(sk)
        if not sc:
            raise HTTPException(400, f'子表没有字段「{sk}」')
        if sc['type'] in ('formula', 'image'):
            raise HTTPException(400, f'子表字段「{sc["label"]}」是公式/图片，暂不支持复制')
        clean[tk] = sk
    return tt, st, clean

def _sync_rule_row(r):
    m = r.get('mapping')
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except Exception:
            m = {}
    return {'id': r['id'], 'target_tid': r['target_tid'], 'source_tid': r['source_tid'],
            'source_name': r.get('source_name') or '', 'source_project': r.get('source_project') or '',
            'name': r.get('name') or r.get('source_name') or '', 'mapping': m,
            'enabled': bool(r.get('enabled')), 'last_sync_at': _ts(r.get('last_sync_at')),
            'last_error': r.get('last_error') or '', 'job': kb_sync.job_status(r['id'])}

def _dashboard_out(r):
    cfg = jload(r.get('config') or '{}')
    if not isinstance(cfg, dict):
        cfg = {}
    if not isinstance(cfg.get('charts'), list):
        cfg['charts'] = []
    return {'id': r['id'], 'project': r['project'], 'name': r['name'],
            'config': cfg, 'is_page': bool(r.get('is_page')),
            'created_by': r.get('created_by') or '',
            'sort': r.get('sort') or 0, 'updated_at': _ts(r.get('updated_at'))}

def _valid_dashboard_name(name):
    name = (name or '').strip()
    if not name:
        raise HTTPException(400, '请填写看板名称')
    if len(name) > 60:
        raise HTTPException(400, '看板名称不能超过 60 个字')
    return name

def _filter_dash_charts(cfg, project):
    """只读用户只返回其有权查看的表的图表，避免暴露无权表的结构。"""
    if project_role(project) == 'editor':
        return cfg
    charts = cfg.get('charts') or []
    out = []
    for c in charts:
        try:
            tid = int(c.get('table_id'))
        except Exception:
            continue
        t = q("SELECT project FROM kb_table WHERE id=%s AND deleted=0", (tid,), one=True)
        if not t or t.get('project') != project:
            continue
        if table_role(tid, project) is not None:
            out.append(c)
    cfg = dict(cfg)
    cfg['charts'] = out
    return cfg

_INDEX_CACHE = {'key': None}

_JS_DIR = os.path.join(FRONTEND, 'js')

def _assets_version():
    """前端 js 的总版本（取最新 mtime）：index.html 里的 ?v=__KBV__ 用它替换。
    js 改了、html 没改，也要让浏览器重新拉脚本（静态文件默认会被缓存）。"""
    sig = 0
    try:
        for fn in os.listdir(_JS_DIR):
            if fn.endswith('.js'):
                sig = max(sig, os.stat(os.path.join(_JS_DIR, fn)).st_mtime_ns)
    except OSError:
        pass
    return sig

def _index_asset():
    """首页 HTML 按文件版本缓存：原始内容、gzip 结果、ETag、CSP 一起算好。
    以前每次打开都重新读盘、重新压缩 340KB（约 17ms 单核时间），而且不让浏览器缓存，
    2M 带宽下每次打开都要重下 84KB。"""
    path = os.path.join(FRONTEND, 'index.html')
    st = os.stat(path)
    ver = _assets_version()
    key = (int(st.st_mtime), st.st_size, ver)
    c = _INDEX_CACHE
    if c['key'] != key:
        with open(path, 'rb') as f:
            raw = f.read()
        raw = raw.replace(b'__KBV__', ('%x' % (ver & 0xffffffffffff)).encode())    # 脚本版本号
        c.update(key=key, raw=raw, gz=gzip.compress(raw, 5),
                 etag=f'"kb{st.st_mtime_ns:x}-{st.st_size:x}-{ver & 0xffffffffffff:x}"',
                 csp=csp_for_html(raw.decode('utf-8', 'replace')))
    return c

# 星号导入时把这个文件里的所有名字（含下划线开头的内部函数）都导出给 app.py / modules/*.py
__all__ = [n for n in list(globals()) if not n.startswith('__')]
