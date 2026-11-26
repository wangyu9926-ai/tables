# -*- coding: utf-8 -*-
"""牧马人线上多维表 API（表/列/行/公式/审计/关联），前端静态页。"""
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

BASE = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(os.path.dirname(BASE), "frontend")

app = FastAPI(title="牧马人线上多维表")
# 2M 小带宽部署时，JSON/HTML 响应必须压缩；Nginx 也开 gzip，双保险。
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
init_schema()


@app.exception_handler(pymysql.err.DataError)
async def _db_input_out_of_range(request, exc):
    """文本超过列宽、数值超出列的范围（1406 / 1264 / 1366）：是调用方传错了内容，
    回 400 并说清楚；不该表现成服务器故障 500。各接口自己能提前判断的仍然提前给准确的提示，这里是兜底。"""
    code = exc.args[0] if exc.args else ''
    print(f'[db] 输入超出允许范围（{code}）', flush=True)
    return JSONResponse({'detail': '内容过长或数值超出允许范围，请缩短后再试'}, status_code=400)


@app.exception_handler(RequestValidationError)
async def _validation_error(request, exc):
    """请求体不符合接口要求：回 422 并说明是哪个字段。不用框架默认的处理，原因有两个：
    它会把原始输入原样放进响应；输入里有 NaN / Infinity 这类不是合法 JSON 的数值时，
    它自己会在生成响应时再抛异常，结果 500。"""
    parts = []
    for e in exc.errors()[:5]:
        loc = ' → '.join(str(x) for x in e.get('loc', ())) or '请求体'
        parts.append(f'{loc}：{e.get("msg", "")}')
    return JSONResponse({'detail': '请求内容不符合接口要求（' + '；'.join(parts) + '）'}, status_code=422)


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

# 元信息（表/列/关联）在内存缓存：远程库任何一次查询都是 100ms 级 RTT，
# 首屏不该反复付这个成本。写操作按影响范围失效，不再一改单元格就全表重建。
_META = {'data': None}
_META_GEN = 0
_META_LOCK = threading.Lock()
_META_CNT = 0         # 行增删只改 count：计一次数，已序列化的 meta 响应就要重做
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
    global _META_GEN
    _META_GEN += 1
    _META['data'] = None
    for tid in set(_ROWS) | set(_VIEW) | set(_ROWS_LOCKS):
        _ROWS_VER[tid] = _ROWS_VER.get(tid, 0) + 1
    _ROWS.clear()
    _VIEW.clear()
    clear_xt()


def _bump_meta_count(tid, delta):
    """行增删只改 count，不用把 17 张表的元信息全部重查一遍。"""
    global _META_GEN, _META_CNT
    d = _META['data']
    if not d:
        # 元信息正在重建/尚未加载：让进行中的构建结果作废，下次按新 count 重建
        _META_GEN += 1
        return
    for t in d.get('tables') or []:
        if t['id'] == tid:
            t['count'] = max(0, int(t.get('count') or 0) + delta)
            _META_CNT += 1
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


@app.middleware("http")
async def _meta_cache_guard(request, call_next):
    resp = await call_next(request)
    if request.method != 'GET' and resp.status_code < 400:
        _invalidate_after(request.method, request.url.path,
                          getattr(request.state, 'kb_inserted', 0))
    return resp


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


kb_sync.set_target_change_hook(_sync_target_changed)


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


# ---------- 登录用户上下文（由 auth_gate 每个请求注入；无网关时为 None） ----------
import contextvars

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


# ==================== 元信息 ====================

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
    out = [{'id': t['id'], 'name': t['name'], 'note': t['note'], 'project': t['project'],
            'group_name': t['group_name'], 'icon': t['icon'], 'sort': t['sort'],
            'count': counts.get(t['id'], 0),
            'columns': cols_by_tid.get(t['id'], []),
            'relations': rels_by_tid.get(t['id'], [])} for t in tables]
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


@app.get("/api/meta")
def meta():
    """元信息缓存：首次构建后内存直出；写操作按影响范围失效。

    构建期间加锁，避免一批用户同时刷新时各自查 5 遍库（缓存击穿）。
    响应字节按「元信息版本 + 行数版本 + 授权视图」缓存：每次打开页面都会调这个接口，
    逐字段编码 80KB 的结构再输出，曾经是每个请求最大的一笔固定开销（约 17ms）。
    """
    key = (_META_GEN, _META_CNT, _meta_view())
    hit = _META_JSON.get(key)
    if hit is not None:
        return Response(content=hit, media_type='application/json')
    d = _META['data']
    if d is None:
        with _META_LOCK:
            d = _META['data']
            if d is None:
                gen = _META_GEN
                d = _build_meta()
                # 构建期间如果有写操作，不能把旧快照重新塞回缓存
                _META['data'] = d if _META_GEN == gen else None
    resp = _json_response(_safe_meta(d))
    # 序列化期间又有写入的话，这份字节对应的不是“现在”的版本，不缓存
    if (_META_GEN, _META_CNT, key[2]) == key:
        if len(_META_JSON) > 64:
            _META_JSON.clear()
        _META_JSON[key] = resp.body
    return resp


class GroupOrderReq(BaseModel):
    project: str = ''
    groups: list


class SheetOrderReq(BaseModel):
    tids: list


@app.post("/api/groups/sort")
def sort_groups(req: GroupOrderReq):
    """侧栏表（组）拖拽排序：按给定顺序重排组，并保持组内 Sheet 原有顺序。"""
    _need_admin()
    if not isinstance(req.groups, list):
        raise HTTPException(400, 'groups 必须是数组')
    for gi, gname in enumerate(req.groups):
        if not isinstance(gname, str):
            continue                             # 组名只可能是文本
        sql = "SELECT id FROM kb_table WHERE group_name=%s AND deleted=0"
        args = [gname]
        if req.project:
            sql += " AND project=%s"
            args.append(req.project)
        tids = [r['id'] for r in q(sql + " ORDER BY sort,id", tuple(args))]
        for si, tid in enumerate(tids):
            ex("UPDATE kb_table SET sort=%s WHERE id=%s", (gi * 1000 + si, tid))
    return {'ok': True}


@app.post("/api/groups/{group}/sort")
def sort_sheets(group: str, req: SheetOrderReq):
    """Sheet 页签拖拽排序：只改本组内顺序，组在侧栏的位置不变。"""
    if not isinstance(req.tids, list):
        raise HTTPException(400, 'tids 必须是数组')
    tids = _as_ints(req.tids)
    if not tids:
        return {'ok': True}
    first = q("SELECT project FROM kb_table WHERE id=%s AND deleted=0", (tids[0],), one=True)
    need_project((first or {}).get('project') or '', write=True)
    row = q("SELECT COALESCE(MIN(sort),0) b FROM kb_table WHERE group_name=%s AND deleted=0",
            (group,), one=True)
    base = (int(row['b'] or 0) // 1000) * 1000
    for i, tid in enumerate(tids):
        ex("UPDATE kb_table SET sort=%s WHERE id=%s AND group_name=%s", (base + i, tid, group))
    return {'ok': True}


class GroupRenameReq(BaseModel):
    project: str
    old_name: str
    new_name: str


@app.patch("/api/groups/rename")
def rename_group(req: GroupRenameReq):
    """重命名侧栏分组：同步更新组内每张表的 group_name 和「组·Sheet」全名。"""
    project = (req.project or '').strip()
    old = (req.old_name or '').strip()
    new = (req.new_name or '').strip()
    if not project or not old or not new:
        raise HTTPException(400, '缺少项目名或分组名')
    if '·' in new:
        raise HTTPException(400, '分组名不能包含 ·')
    if len(new) > 80:
        raise HTTPException(400, '分组名不能超过 80 个字')
    if old == new:
        return {'ok': True, 'group': new, 'updated': 0}
    rows = q("SELECT id,name FROM kb_table WHERE project=%s AND group_name=%s AND deleted=0 "
             "ORDER BY sort,id", (project, old))
    if not rows:
        raise HTTPException(404, '分组不存在')
    if q("SELECT id FROM kb_table WHERE project=%s AND group_name=%s AND deleted=0 LIMIT 1",
         (project, new), one=True):
        raise HTTPException(409, f'项目里已有分组「{new}」')
    updates, seen = [], set()
    for r in rows:
        need_table(r['id'], project, write=True)
        if '·' in r['name']:
            short = r['name'].split('·', 1)[1]
        else:
            short = r['name'] if r['name'] != old else ''
        full = f'{new}·{short}' if short else new
        if len(full) > 160:
            raise HTTPException(400, f'表名过长：{full}')
        if full in seen:
            raise HTTPException(409, f'重命名后表名重复：{full}')
        seen.add(full)
        dup = q("SELECT id FROM kb_table WHERE name=%s AND deleted=0 AND id<>%s",
                (full, r['id']), one=True)
        if dup:
            raise HTTPException(409, f'表名「{full}」已存在')
        updates.append((r['id'], r['name'], full))
    for tid, old_full, full in updates:
        ex("UPDATE kb_table SET name=%s, group_name=%s WHERE id=%s", (full, new, tid))
        audit(tid, None, 'rename_table', {'old': old_full, 'new': full})
    _renumber_formula_refs(project, [(o, n) for _, o, n in updates])
    return {'ok': True, 'group': new, 'updated': len(updates)}


def _renumber_formula_refs(project, pairs):
    """表/组改名后，同步更新同项目公式列里写死的「表名」引用。"""
    pairs = [(o, n) for o, n in (pairs or []) if o and n and o != n]
    if not pairs or not project:
        return
    rows = q("SELECT c.id, c.expr FROM kb_column c JOIN kb_table t ON t.id = c.table_id "
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
            audit(r['id'], None, 'update_column', {'expr_ref': 'table_renamed'})


# ==================== 表 CRUD ====================

class TableReq(BaseModel):
    name: str = ''
    note: str = ''
    project: str = ''
    group_name: str = ''
    icon: str = '📄'
    columns: list | None = None     # [{key?,label,type?,options?,expr?,required?,width?}]


def _jdump_col(v):
    """列定义里的 options / option_colors 要存进 JSON 列：NaN / Infinity 不是合法 JSON，数据库会拒收，先在这里拦下。"""
    if v is None:
        return None
    try:
        return json.dumps(v, ensure_ascii=False, allow_nan=False)
    except ValueError:
        raise HTTPException(400, '列定义里有无效的数值（NaN / Infinity）')


@app.post("/api/tables")
def create_table(req: TableReq):
    _need_admin()
    name = (req.name or '').strip()
    if not name:
        raise HTTPException(400, '表名不能为空')
    _check_len(name, 160, '表名')
    _check_len(req.note, 500, '表备注')
    _check_len(req.project, 160, '项目名')
    _check_len(req.group_name, 80, '分组名')
    _check_len(req.icon, 16, '图标')
    if req.project and not _project_exists(req.project):
        raise HTTPException(404, '项目不存在')
    if q("SELECT id FROM kb_table WHERE name=%s AND deleted=0", (name,), one=True):
        raise HTTPException(409, f'表「{name}」已存在')
    defs = req.columns or [{'label': '名称', 'type': 'text', 'required': True},
                           {'label': '备注', 'type': 'text'}]
    # 列定义先全部校验完再建表：以前是边建表边校验，列定义出错时表已经建出来了，留下没有列的空壳表
    for c in defs:
        if not isinstance(c, dict):
            raise HTTPException(400, '列定义格式不对：每一列都必须是对象')
    labels = [{'key': slug(c.get('key') or c.get('label') or ''),
               'label': str(c.get('label') or '')} for c in defs]
    pending = {'name': name, 'labels': {x['label'].strip() for x in labels}}
    cols_out = []
    used = set()
    for i, c in enumerate(defs):
        label = str(c.get('label') or '').strip()
        if not label:
            continue
        _check_len(label, 200, '列名')
        key = slug(c.get('key') or label)
        while key in used:
            key += '_'
        used.add(key)
        t = c.get('type') or 'text'
        if t not in COL_TYPES:
            raise HTTPException(400, f'未知列类型 {t}')
        expr = c.get('expr') if t == 'formula' else None
        if t == 'formula':
            # 表还没建：公式里引用本表时按这份定义来判断
            ok, msg = validate_expr(expr, labels, req.project or '', pending=pending)
            if not ok:
                raise HTTPException(400, f'公式列「{label}」：{msg}')
        cols_out.append((key, label, t, _jdump_col(c.get('options')), _jdump_col(c.get('option_colors')), expr,
                         _col_width(c.get('width')), i, 1 if c.get('required') else 0, 1 if c.get('hidden') else 0))
    # 软删的同名表仍占着 UNIQUE(name)，彻底清掉它的残留，避免 1062
    old = q("SELECT id FROM kb_table WHERE name=%s AND deleted=1", (name,), one=True)
    if old:
        ex("DELETE FROM kb_column WHERE table_id=%s", (old['id'],))
        ex("DELETE FROM kb_row WHERE table_id=%s", (old['id'],))
        ex("DELETE FROM kb_table WHERE id=%s", (old['id'],))
    mx = q("SELECT COALESCE(MAX(sort),0) m FROM kb_table", one=True)['m']
    try:
        tid = ex("INSERT INTO kb_table(name,note,project,group_name,icon,sort) VALUES(%s,%s,%s,%s,%s,%s)",
                 (name, req.note or '', req.project or '', req.group_name or '', req.icon or '📄', mx + 1))
    except pymysql.err.IntegrityError:
        raise HTTPException(409, f'表「{name}」已存在')
    audit(tid, None, 'create_table', {'name': name})
    for key, label, t, options, colors, expr, width, i, required, hidden in cols_out:
        ex("INSERT INTO kb_column(table_id,`key`,label,type,options,option_colors,expr,width,sort,required,hidden) "
           "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
           (tid, key, label, t, options, colors, expr, width, i, required, hidden))
        audit(tid, None, 'create_column', {'label': label, 'type': t})
    return {'ok': True, 'id': tid, 'name': name}


@app.patch("/api/tables/{tid}")
def update_table(tid: int, req: TableReq):
    _need_admin()
    _need(tid)
    sets, args = [], []
    if req.name and req.name.strip():
        new = req.name.strip()
        _check_len(new, 160, '表名')
        if q("SELECT id FROM kb_table WHERE name=%s AND deleted=0 AND id<>%s", (new, tid), one=True):
            raise HTTPException(409, f'表「{new}」已存在')
        sets.append('name=%s'); args.append(new)
    if req.note is not None:
        _check_len(req.note, 500, '表备注')
        sets.append('note=%s'); args.append(req.note)
    if req.project:
        _check_len(req.project, 160, '项目名')
        sets.append('project=%s'); args.append(req.project)
    if req.group_name:
        _check_len(req.group_name, 80, '分组名')
        sets.append('group_name=%s'); args.append(req.group_name)
    if req.icon:
        _check_len(req.icon, 16, '图标')
        sets.append('icon=%s'); args.append(req.icon)
    if sets:
        args.append(tid)
        try:
            ex(f"UPDATE kb_table SET {','.join(sets)} WHERE id=%s", tuple(args))
        except pymysql.err.IntegrityError:
            raise HTTPException(409, '表名已被占用')
        audit(tid, None, 'update_table', {'name': req.name})
    return {'ok': True}


@app.delete("/api/tables/{tid}")
def delete_table(tid: int):
    _need_admin()
    _need(tid)
    ex("UPDATE kb_table SET deleted=1 WHERE id=%s", (tid,))
    ex("UPDATE kb_row SET deleted=1 WHERE table_id=%s", (tid,))
    ex("UPDATE kb_image SET deleted=1, deleted_at=NOW() WHERE table_id=%s AND deleted=0", (tid,))
    try:
        kb_sync.cleanup_tables([tid])
    except Exception as e:
        print(f'[sync] cleanup table failed: {e!r}', flush=True)
    audit(tid, None, 'delete_table', {})
    return {'ok': True}


class RenameReq(BaseModel):
    short_name: str        # Sheet 短名（页签上显示的部分）


@app.patch("/api/tables/{tid}/rename")
def rename_table(tid: int, req: RenameReq):
    """双击 Sheet 页签重命名：只改短名，保留组名前缀「组·」。"""
    t = _need(tid)
    need_table(tid, t['project'] or '', write=True)
    short = (req.short_name or '').strip()
    if not short:
        raise HTTPException(400, '名称不能为空')
    group = t['group_name'] or ''
    full = (group + '·' + short) if group else short
    _check_len(full, 160, '表名')
    dup = q("SELECT id FROM kb_table WHERE name=%s AND deleted=0 AND id<>%s", (full, tid), one=True)
    if dup:
        raise HTTPException(409, f'同组下已有「{short}」')
    ex("UPDATE kb_table SET name=%s WHERE id=%s", (full, tid))
    audit(tid, None, 'rename_table', {'old': t['name'], 'new': full})
    if full != t['name']:
        _renumber_formula_refs(t['project'] or '', [(t['name'], full)])
    return {'ok': True, 'name': full}


# ==================== 项目（三层：项目 → 表 → Sheet） ====================

class ProjectReq(BaseModel):
    name: str
    icon: str = '📁'


@app.get("/api/projects")
def list_projects():
    rows = q("SELECT name,icon,sort FROM kb_project WHERE deleted=0 ORDER BY sort,name")
    cnt = {r['project']: r['tables'] for r in q(
        "SELECT project, COUNT(*) AS tables FROM kb_table WHERE deleted=0 GROUP BY project")}
    names = [r['name'] for r in rows]
    for n in cnt:
        if n and n not in names:
            rows.append({'name': n, 'icon': '📁', 'sort': 999})
            names.append(n)
    u = AUTH_USER.get()
    if u and u.get('role') == 'staff':
        pm, tm = _perm_scopes(_uid())
        rows = [r for r in rows if r['name'] in pm]
    return {'rows': [{'name': r['name'], 'icon': r['icon'], 'tables': cnt.get(r['name'], 0)}
                     for r in rows]}


@app.post("/api/projects")
def create_project(req: ProjectReq):
    _need_admin()
    name = (req.name or '').strip()
    if not name:
        raise HTTPException(400, '项目名不能为空')
    _check_len(name, 160, '项目名')
    _check_len(req.icon, 16, '图标')
    dup = q("SELECT id FROM kb_project WHERE name=%s AND deleted=0", (name,), one=True)
    if dup:
        raise HTTPException(409, '项目已存在')
    mx = q("SELECT COALESCE(MAX(sort),0) m FROM kb_project WHERE deleted=0", one=True)['m']
    # 软删的同名项目仍占着 UNIQUE(name)：复用那条记录，否则 INSERT 撞唯一键直接 500
    old = q("SELECT id FROM kb_project WHERE name=%s AND deleted=1", (name,), one=True)
    try:
        if old:
            ex("UPDATE kb_project SET deleted=0, icon=%s, sort=%s WHERE id=%s",
               (req.icon or '📁', mx + 1, old['id']))
        else:
            ex("INSERT INTO kb_project(name,icon,sort) VALUES(%s,%s,%s)",
               (name, req.icon or '📁', mx + 1))
    except pymysql.err.IntegrityError:
        raise HTTPException(409, '项目已存在')
    return {'ok': True, 'name': name}


@app.patch("/api/projects/{name}")
def rename_project(name: str, req: ProjectReq):
    _need_admin()
    new = (req.name or '').strip()
    if not new:
        raise HTTPException(400, '项目名不能为空')
    _check_len(new, 160, '项目名')
    if new != name:
        dup = q("SELECT id FROM kb_project WHERE name=%s AND deleted=0", (new,), one=True)
        if dup:
            raise HTTPException(409, '项目已存在')
        # 软删的同名项目占着 UNIQUE(name)，先清掉这条残留，否则改名撞唯一键 500
        stale = q("SELECT id FROM kb_project WHERE name=%s AND deleted=1", (new,), one=True)
        if stale:
            ex("DELETE FROM kb_project WHERE id=%s", (stale['id'],))
    # 项目名是表 / 看板 / 项目授权的外键：五条更新放在同一个事务里，中途失败整体回滚
    con = conn()
    try:
        cur = con.cursor()
        cur.execute("UPDATE kb_project SET name=%s WHERE name=%s AND deleted=0", (new, name))
        cur.execute("UPDATE kb_table SET project=%s WHERE project=%s AND deleted=0", (new, name))
        # 看板按项目名归属：不同步的话，改名后项目下的看板和分析页会全部消失
        cur.execute("UPDATE kb_dashboard SET project=%s, updated_at=updated_at WHERE project=%s", (new, name))
        # 项目级授权按项目名存储，改名要同步，否则用户会“突然没权限”
        cur.execute("DELETE FROM kb_perm WHERE scope_type='project' AND scope_key=%s", (new,))
        cur.execute("UPDATE kb_perm SET scope_key=%s WHERE scope_type='project' AND scope_key=%s", (new, name))
        con.commit()
    except pymysql.err.IntegrityError:
        con.rollback()
        raise HTTPException(409, '项目已存在')
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    clear_perm_cache()
    return {'ok': True, 'name': new}


@app.delete("/api/projects/{name}")
def delete_project(name: str):
    """删项目 = 软删该项目下所有表。"""
    _need_admin()
    n = ex("UPDATE kb_project SET deleted=1 WHERE name=%s AND deleted=0", (name,))
    tids = [r['id'] for r in q("SELECT id FROM kb_table WHERE project=%s AND deleted=0", (name,))]
    if not n and not tids:
        # 重复点击/并发删除：项目和表都已删过，直接返回
        return {'ok': True, 'deleted': 0}
    try:
        kb_sync.cleanup_tables(tids)
    except Exception as e:
        print(f'[sync] cleanup project failed: {e!r}', flush=True)
    for tid in tids:
        ex("UPDATE kb_table SET deleted=1 WHERE id=%s", (tid,))
        ex("UPDATE kb_row SET deleted=1 WHERE table_id=%s", (tid,))
        ex("UPDATE kb_image SET deleted=1, deleted_at=NOW() WHERE table_id=%s AND deleted=0", (tid,))
        audit(tid, None, 'delete_table', {'by': 'delete_project'})
    # 删项目同时清项目级授权，避免以后同名重建时继承旧权限
    ex("DELETE FROM kb_perm WHERE scope_type='project' AND scope_key=%s", (name,))
    clear_perm_cache()
    return {'ok': True, 'deleted': len(tids)}


# ==================== 列 CRUD（含公式列） ====================

class ColumnReq(BaseModel):
    label: str
    key: str | None = None
    type: str = 'text'
    options: list | None = None
    option_colors: dict | None = None
    expr: str | None = None
    width: int | None = None
    sort: int | None = None
    # 改列时“传了才改”：不传就不动原有的列宽 / 必填 / 隐藏
    required: bool | None = None
    hidden: bool | None = None


@app.post("/api/tables/{tid}/columns")
def add_column(tid: int, req: ColumnReq):
    _need_fields(tid)
    tbl = _need(tid)
    label = (req.label or '').strip()
    if not label:
        raise HTTPException(400, '列名不能为空')
    _check_len(label, 200, '列名')
    t = req.type or 'text'
    if t not in COL_TYPES:
        raise HTTPException(400, f'未知列类型 {t}')
    existing = columns(tid, include_hidden=True)
    known = [{'key': c['key'], 'label': c['label']} for c in existing]
    expr = req.expr if t == 'formula' else None
    if t == 'formula':
        ok, msg = validate_expr(expr, known, tbl.get('project') or '',
                                xt_check=_xt_check_for(tbl.get('project') or ''))
        if not ok:
            raise HTTPException(400, f'公式错误：{msg}')
    key = slug(req.key or label)
    base, n = key, 2
    # 唯一键 (table_id, key) 把已经软删的列也算在内：查重必须带上它们，
    # 否则删掉一列再建同名列会撞唯一键（直接 500）
    keys = {r['key'] for r in q("SELECT `key` FROM kb_column WHERE table_id=%s", (tid,))}
    while key in keys:
        key = f'{base}_{n}'; n += 1
    if req.sort is None:
        sort = q("SELECT COALESCE(MAX(sort),0) m FROM kb_column WHERE table_id=%s", (tid,), one=True)['m'] + 1
    else:
        sort = max(0, min(int(req.sort), 2147483647))
    cid = ex("INSERT INTO kb_column(table_id,`key`,label,type,options,option_colors,expr,width,sort,required,hidden) "
             "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
             (tid, key, label, t, _jdump_col(req.options), _jdump_col(req.option_colors), expr, _col_width(req.width),
              sort, 1 if req.required else 0, 1 if req.hidden else 0))
    audit(tid, None, 'create_column', {'label': label, 'type': t, 'expr': expr})
    return {'ok': True, 'id': cid, 'key': key}


@app.patch("/api/tables/{tid}/columns/{cid}")
def update_column(tid: int, cid: int, req: ColumnReq):
    _need_fields(tid)
    tbl = _need(tid)
    col = q("SELECT * FROM kb_column WHERE id=%s AND table_id=%s AND deleted=0", (cid, tid), one=True)
    if not col:
        raise HTTPException(404, '列不存在')
    sets, args, detail = [], [], {}
    if req.label and req.label.strip() != col['label']:
        _check_len(req.label.strip(), 200, '列名')
        sets.append('label=%s'); args.append(req.label.strip())
        detail['label'] = {'old': col['label'], 'new': req.label.strip()}
    if req.type and req.type != col['type']:
        if req.type not in COL_TYPES:
            raise HTTPException(400, f'未知列类型 {req.type}')
        sets.append('type=%s'); args.append(req.type)
        detail['type'] = {'old': col['type'], 'new': req.type}
    if req.options is not None:
        sets.append('options=%s'); args.append(_jdump_col(req.options))
    if req.option_colors is not None:
        sets.append('option_colors=%s'); args.append(_jdump_col(req.option_colors))
    if req.type == 'formula' or col['type'] == 'formula':
        expr = req.expr if req.expr is not None else col['expr']
        known = [{'key': c['key'], 'label': c['label']}
                 for c in columns(tid, include_hidden=True) if c['id'] != cid]
        ok, msg = validate_expr(expr, known, tbl.get('project') or '',
                                xt_check=_xt_check_for(tbl.get('project') or ''))
        if not ok:
            raise HTTPException(400, f'公式错误：{msg}')
        sets.append('expr=%s'); args.append(expr)
        detail['expr'] = {'old': col['expr'], 'new': expr}
    if req.width is not None:
        sets.append('width=%s'); args.append(_col_width(req.width))
    if req.sort is not None:
        sets.append('sort=%s'); args.append(max(0, min(int(req.sort), 2147483647)))
    if req.required is not None:
        sets.append('required=%s'); args.append(1 if req.required else 0)
    if req.hidden is not None:
        sets.append('hidden=%s'); args.append(1 if req.hidden else 0)
    if not sets:
        return {'ok': True}       # 没有任何要改的字段
    args += [cid, tid]
    ex(f"UPDATE kb_column SET {','.join(sets)} WHERE id=%s AND table_id=%s", tuple(args))
    audit(tid, None, 'update_column', {'col': col['label'], **detail})
    return {'ok': True}


@app.delete("/api/tables/{tid}/columns/{cid}")
def delete_column(tid: int, cid: int):
    _need_fields(tid)
    _need(tid)
    col = q("SELECT `key`,label FROM kb_column WHERE id=%s AND table_id=%s", (cid, tid), one=True)
    if not col:
        raise HTTPException(404, '列不存在')
    ex("UPDATE kb_column SET deleted=1 WHERE id=%s AND table_id=%s", (cid, tid))
    # 该列引用的图片先进回收站（要在 JSON_REMOVE 前解析）
    for r in q("SELECT data FROM kb_row WHERE table_id=%s AND deleted=0", (tid,)):
        for iid in _cell_iids(jload(r['data']).get(col['key'])):
            _trash_image(iid)
    # 一条 SQL 清掉所有行里的该字段；逐行 UPDATE 在远程库上会卡很久
    path = '$.' + json.dumps(col['key'], ensure_ascii=False)
    ex("UPDATE kb_row SET data=JSON_REMOVE(data, %s), ver=ver+1 "
       "WHERE table_id=%s AND deleted=0 AND JSON_CONTAINS_PATH(data, 'one', %s)",
       (path, tid, path))
    audit(tid, None, 'delete_column', {'label': col['label']})
    return {'ok': True}


class SortColsReq(BaseModel):
    order: list          # [col_id, ...]
    hidden: list = []    # [col_id, ...] 隐藏的列 id


@app.post("/api/tables/{tid}/columns/sort")
def sort_columns(tid: int, req: SortColsReq):
    _need_fields(tid)
    _need(tid)
    order = _as_ints(req.order)
    hid = set(_as_ints(req.hidden))
    for i, cid in enumerate(order):
        ex("UPDATE kb_column SET sort=%s,hidden=%s WHERE id=%s AND table_id=%s",
           (i, 1 if cid in hid else 0, cid, tid))
    audit(tid, None, 'sort_columns', {'order': order, 'hidden': list(hid)})
    return {'ok': True}


# ==================== 行 CRUD（自动算公式、乐观锁） ====================

class RowReq(BaseModel):
    data: dict
    ver: int | None = None
    before_id: int | None = None   # 在目标行之前插入（行号悬浮 + 号用）
    after_id: int | None = None    # 在目标行之后插入


class RowMoveReq(BaseModel):
    before_id: int | None = None   # 拖到这一行之前
    after_id: int | None = None    # 拖到这一行之后
    offset: int | None = None      # 或：相对当前位置移动 -1 / +1（右键“上移/下移”）


def _rows_view(tid, recs, cols, project=''):
    formula_cols = [c for c in cols if c['type'] == 'formula']
    out = []
    for r in recs:
        d = compute_formulas(jload(r['data']), cols, project) if formula_cols else jload(r['data'])
        out.append({'id': r['id'], 'data': d, 'ver': r['ver'],
                    'created_at': _ts(r['created_at']), 'updated_at': _ts(r['updated_at'])})
    return out


@app.get("/api/tables/{tid}/rows")
def list_rows(tid: int, page: int = 1, size: int = 50, q: str = '',
              sort: str = '', dir: str = 'asc', filters: str = ''):
    # 表/列结构直接从内存缓存取，省掉 2 次远程往返
    cached_t = None
    if _META['data']:
        cached_t = next((x for x in _META['data']['tables'] if x['id'] == tid), None)
    if cached_t:
        cols = [c for c in cached_t['columns'] if not c['hidden']]
        project = cached_t.get('project') or ''
    else:
        t0 = _need(tid)
        project = t0.get('project') or ''
        cols = columns(tid)
    need_table(tid, project)
    try:
        # 只有这张表自己是汇总总表、且队列里还有它没做完的增量时才等；读别的表不排队
        kb_sync.flush_for(tid, 1.5)
    except Exception:
        pass
    view = _table_view(tid, cols)
    data_rows = view['rows']

    kw = (q or '').strip().lower()
    if kw:
        data_rows = [r for r in data_rows
                     if any(v is not None and kw in str(v).lower() for v in r['data'].values())]

    multi = {}
    if filters:
        try:
            multi = json.loads(filters)
        except Exception:
            multi = {}
        # 只认 {列: 条件} 或 {logic, conds} 两种对象；数组 / 字符串 / 数字按“没有筛选”处理（和非法 JSON 同一口径）
        if not isinstance(multi, dict):
            multi = {}

    bykey = {c['key']: c for c in cols}
    formula_keys = {c['key'] for c in cols if c['type'] == 'formula'}

    def _uses_formula(flt):
        if isinstance(flt.get('conds'), list):
            return any(isinstance(c.get('key'), str) and c['key'] in formula_keys
                       for c in flt['conds'] if isinstance(c, dict))
        return any(isinstance(k, str) and k in formula_keys for k in flt)

    if multi and _uses_formula(multi):
        # 公式列参与筛选时先全量算公式；复制行，不能污染共享的视图缓存
        data_rows = [{'id': r['id'], 'ver': r['ver'], 'created_at': r['created_at'],
                      'updated_at': r['updated_at'],
                      'data': compute_formulas(r['data'], cols, project)} for r in data_rows]

    def _num(x):
        try:
            return float(str(x).replace(',', ''))
        except Exception:
            m = re.fullmatch(r'\s*(-?\d+(?:\.\d+)?)\s*([wW万])?\s*', str(x or ''))
            if m:
                n = float(m.group(1))
                return n * 10000 if m.group(2) else n
        return None

    def match(v, spec, ctype='text'):
        if isinstance(spec, dict):
            if 'op' in spec:
                op = spec.get('op')
                raw = spec.get('value')
                raw2 = spec.get('value2')
                if op in ('empty', 'notempty'):
                    is_empty = v is None or (isinstance(v, str) and not v.strip()) \
                        or (isinstance(v, (list, tuple)) and not v)
                    return is_empty if op == 'empty' else (not is_empty)
                if ctype == 'checkbox' and op in ('eq', 'neq'):
                    want = str(raw).strip().lower() in ('1', 'true', 'yes', 'on', '是')
                    hit = (v is True) == want
                    return hit if op == 'eq' else (not hit)
                if op in ('contains', 'notcontains'):
                    hit = str(raw or '').lower() in str(v or '').lower() if raw not in (None, '') else True
                    if op == 'contains':
                        return hit
                    return (not hit) and (v not in (None, ''))
                if op in ('eq', 'neq'):
                    if ctype in ('number', 'formula'):
                        a, b = _num(v), _num(raw)
                        hit = (a is not None and b is not None and a == b)
                    else:
                        hit = str(v or '').strip().lower() == str(raw or '').strip().lower()
                    return hit if op == 'eq' else (not hit)
                if op in ('gt', 'lt', 'gte', 'lte', 'between'):
                    a = _num(v)
                    if a is None:
                        return False
                    if op == 'between':
                        b1, b2 = _num(raw), _num(raw2)
                        return (b1 is None or a >= b1) and (b2 is None or a <= b2)
                    b = _num(raw)
                    if b is None:
                        return True
                    return {'gt': a > b, 'lt': a < b, 'gte': a >= b, 'lte': a <= b}[op]
                return True
            # 日期范围：起止一律按文本比较；传进来数字 / 对象时先转成文本，不能让比较本身报错
            if v is None or str(v).strip() == '':
                return False
            s = str(v).strip()[:10]
            f, to = str(spec.get('from') or ''), str(spec.get('to') or '')
            return (not f or s >= f) and (not to or s <= to)
        if not isinstance(spec, (str, list, tuple)):
            return True                             # 数字 / 布尔不是合法的筛选条件：这一项不筛
        if ctype == 'multiselect' or isinstance(v, (list, tuple)):
            arr = list(v) if isinstance(v, (list, tuple)) else ([v] if v not in (None, '') else [])
            if '（空）' in spec and not arr:
                return True
            return any(str(x) in spec for x in arr)
        if '（空）' in spec and (v is None or str(v).strip() == ''):
            return True
        return str(v or '') in spec

    # facets 来自视图缓存（筛选前全量统计），保证筛选浮层始终列出完整选项
    facets = view['facets']

    if multi:
        if isinstance(multi.get('conds'), list):
            # 高级筛选：{logic:'and'|'or', conds:[{key,op,value,value2} | {key,op:'daterange',value,value2}]}
            logic = 'or' if str(multi.get('logic') or 'and').lower() == 'or' else 'and'
            conds = []
            for c in multi['conds'][:20]:
                if not isinstance(c, dict):
                    continue
                key = c.get('key')
                if not isinstance(key, str) or key not in bykey:
                    continue                    # 列已经被删掉（或 key 不是文本）：忽略这条，不能把整表筛成空
                op = c.get('op') if isinstance(c.get('op'), str) else None
                if op == 'daterange':
                    spec = {'from': c.get('value') or '', 'to': c.get('value2') or ''}
                elif op:
                    spec = {'op': op, 'value': c.get('value'), 'value2': c.get('value2')}
                elif c.get('from') or c.get('to'):
                    spec = {'from': c.get('from') or '', 'to': c.get('to') or ''}
                else:
                    continue
                conds.append((key, spec))
            if conds:
                def _row_hit(r):
                    vals = [match(r['data'].get(k), s, bykey[k]['type']) for k, s in conds]
                    return any(vals) if logic == 'or' else all(vals)
                data_rows = [r for r in data_rows if _row_hit(r)]
        else:
            data_rows = [r for r in data_rows if all(
                match(r['data'].get(c), s, (bykey.get(c) or {}).get('type') or 'text')
                for c, s in multi.items() if s)]

    if sort:
        col = next((c for c in cols if c['key'] == sort), None)
        is_num = col and col['type'] in ('number', 'formula', 'date')

        def keyf(r):
            v = r['data'].get(sort)
            if v is None or str(v).strip() == '':
                return (1, 0)
            if is_num:
                try:
                    return (0, float(str(v).replace(',', '')))
                except Exception:
                    pass
            return (0, str(v))
        # 不能原地 sort：data_rows 可能直接引用共享视图缓存
        data_rows = sorted(data_rows, key=keyf, reverse=(dir == 'desc'))

    total = len(data_rows)
    if size and size > 0:
        start = (page - 1) * size
        page_rows = data_rows[start:start + size]
    else:
        page_rows = data_rows[:]          # size=0：全部
    # 公式列在分页后算
    fcs = [c for c in cols if c['type'] == 'formula']
    out = []
    for r in page_rows:
        d = compute_formulas(r['data'], cols, project) if fcs else r['data']
        out.append({'id': r['id'], 'data': d, 'ver': r['ver'],
                    'created_at': _ts(r['created_at']), 'updated_at': _ts(r['updated_at'])})
    return _json_response({'rows': out, 'total': total, 'facets': facets})


@app.get("/api/tables/{tid}/rows/by-ids")
def rows_by_ids(tid: int, ids: str = ''):
    """只取指定的几行（实时刷新的"最小化"路径）：变更日志说哪几行变了，就只取这几行，
    不用把整页重算一遍。最多 200 行，和列表接口同一套行结构（公式列算好、隐藏列不出现）。"""
    _t = _need(tid)
    project = _t.get('project') or ''
    need_table(tid, project)
    want = []
    for x in (ids or '').split(','):
        x = x.strip()
        try:
            # 只收普通十进制数字：'²' 这类字符 isdigit() 也为真，却转不成整数
            if x and x.isascii() and x.isdigit():
                want.append(min(int(x), 18446744073709551615))
        except ValueError:
            pass
    want = want[:200]
    if not want:
        return _json_response({'rows': []})
    ph = ','.join(['%s'] * len(want))
    recs = q(f'SELECT id,ver,data,created_at,updated_at FROM kb_row '
             f'WHERE table_id=%s AND deleted=0 AND id IN ({ph})', tuple([tid] + want))
    return _json_response({'rows': _rows_view(tid, recs, columns(tid), project)})


@app.post("/api/tables/{tid}/rows")
def create_row(tid: int, req: RowReq):
    _t = _need(tid)
    need_table(tid, _t.get('project') or '', write=True)
    cols = _table_columns(tid, include_hidden=True)
    bykey = {c['key']: c for c in cols if c['type'] != 'formula'}
    clean = {}
    for k, v in (req.data or {}).items():
        if k in bykey:
            try:
                clean[k] = norm_val(bykey[k], v)
            except ValueError as e:
                raise HTTPException(400, str(e))
    # 必填只在「往必填列写空值」时拦；新建空行放行（对齐多维表格习惯，先建行再填）
    if req.before_id and req.after_id:
        raise HTTPException(400, 'before_id 和 after_id 只能选一个')

    # 插入中间行要把后续 sort 整体后移 1；锚点行加 FOR UPDATE 后和 INSERT
    # 放在同一事务里，并发插入不会拿到重复 sort。表内 sort 本来就是连续唯一值。
    con = conn()
    try:
        cur = con.cursor()
        anchor = None
        if req.before_id or req.after_id:
            cur.execute("SELECT id,sort FROM kb_row WHERE id=%s AND table_id=%s AND deleted=0 FOR UPDATE",
                        (req.before_id or req.after_id, tid))
            anchor = cur.fetchone()
            if not anchor:
                raise HTTPException(404, '目标行不存在')
        if anchor:
            if req.before_id:
                new_sort = int(anchor['sort'])
                cur.execute("UPDATE kb_row SET sort=sort+1 WHERE table_id=%s AND sort>=%s",
                            (tid, new_sort))
            else:
                new_sort = int(anchor['sort']) + 1
                cur.execute("UPDATE kb_row SET sort=sort+1 WHERE table_id=%s AND sort>%s",
                            (tid, anchor['sort']))
        else:
            cur.execute("SELECT COALESCE(MAX(sort),0) m FROM kb_row WHERE table_id=%s", (tid,))
            new_sort = int((cur.fetchone() or {}).get('m') or 0) + 1
        cur.execute("INSERT INTO kb_row(table_id,data,sort) VALUES(%s,%s,%s)",
                    (tid, jdump(clean), new_sort))
        rid = cur.lastrowid
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()

    # 新行在整张表里的 1-based 位置；前端据此直接跳到所在页
    pos = q("SELECT COUNT(*) c FROM kb_row WHERE table_id=%s AND deleted=0 "
            "AND (sort<%s OR (sort=%s AND id<%s))", (tid, new_sort, new_sort, rid), one=True)['c'] + 1
    audit(tid, rid, 'create_row', {k: {'old': None, 'new': v} for k, v in clean.items()})
    try:
        kb_sync.source_event(tid, rid, 'upsert')
    except Exception as e:
        print(f'[sync] create_row hook failed: {e!r}', flush=True)
    kb_changes.bump(tid, 'insert', [rid], ((AUTH_USER.get() or {}).get('username') or ''))
    return {'ok': True, 'id': rid, 'ver': 1, 'pos': pos}


@app.post("/api/tables/{tid}/rows/{rid}/move")
def move_row(tid: int, rid: int, req: RowMoveReq):
    """把一行拖到另一行前/后，或相对移动 ±1。行顺序是表数据的一部分（和表格软件一样，所有人可见）。
    在一个事务里锁住整张表的行，只重写位置发生变化的那一段 sort，保持连续、不产生空洞。"""
    _t = _need(tid)
    need_table(tid, _t.get('project') or '', write=True)
    given = [bool(req.before_id), bool(req.after_id), req.offset is not None]
    if sum(given) != 1:
        raise HTTPException(400, 'before_id / after_id / offset 必须且只能给一个')
    if req.offset is not None and req.offset not in (-1, 1):
        raise HTTPException(400, 'offset 只能是 -1 或 1')

    con = conn()
    try:
        cur = con.cursor()
        # 连软删的行一起锁：它们也占着 sort，重排时一起保持唯一。
        # 但“第几行 / 上移下移”只看可见行（和插入接口的 pos 口径一致）。
        cur.execute("SELECT id,sort,deleted FROM kb_row WHERE table_id=%s ORDER BY sort,id FOR UPDATE", (tid,))
        rows = cur.fetchall()
        all_ids = [r['id'] for r in rows]
        cur_sort = {r['id']: int(r['sort']) for r in rows}
        vis = [r['id'] for r in rows if not r['deleted']]
        if rid not in vis:
            raise HTTPException(404, '行不存在')
        old_vi = vis.index(rid)
        rest = [x for x in vis if x != rid]
        if req.offset is not None:
            tgt_vi = max(0, min(len(rest), old_vi + req.offset))
        else:
            anchor = req.before_id or req.after_id
            if anchor not in vis or anchor == rid:
                raise HTTPException(404, '目标行不存在')
            a_vi = rest.index(anchor)
            tgt_vi = a_vi if req.before_id else a_vi + 1
        if tgt_vi == old_vi:
            con.rollback()
            return {'ok': True, 'pos': old_vi + 1, 'moved': False}
        # 把“插到第 tgt_vi 个可见行之前”翻译成整张表（含软删行）里的插入位置
        old_i = all_ids.index(rid)
        rest_all = [x for x in all_ids if x != rid]
        ins_i = rest_all.index(rest[tgt_vi]) if tgt_vi < len(rest) else len(rest_all)
        new_all = list(rest_all)
        new_all.insert(ins_i, rid)
        lo, hi = min(old_i, ins_i), max(old_i, ins_i)
        base = cur_sort[new_all[lo - 1]] if lo > 0 else 0
        changed = 0
        for i in range(lo, hi + 1):
            ns = base + (i - lo) + 1
            if cur_sort[new_all[i]] != ns:
                cur.execute("UPDATE kb_row SET sort=%s WHERE id=%s", (ns, new_all[i]))
                changed += 1
        con.commit()
    except HTTPException:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()
    audit(tid, rid, 'move_row', {'from': old_vi + 1, 'to': tgt_vi + 1})
    kb_changes.bump(tid, 'move', [rid], ((AUTH_USER.get() or {}).get('username') or ''))
    _drop_rows(tid)
    invalidate_xt_table(tid)
    return {'ok': True, 'pos': tgt_vi + 1, 'moved': changed > 0}


@app.patch("/api/tables/{tid}/rows/{rid}")
def update_row(tid: int, rid: int, req: RowReq):
    _t = _need(tid)
    need_table(tid, _t.get('project') or '', write=True)
    cur = q("SELECT data,ver FROM kb_row WHERE id=%s AND table_id=%s AND deleted=0", (rid, tid), one=True)
    if not cur:
        raise HTTPException(404, '行不存在')
    if req.ver is None or int(req.ver) != int(cur['ver']):
        raise HTTPException(409, '这行已被别人改过，请刷新后重试')
    cols = _table_columns(tid, include_hidden=True)
    bykey = {c['key']: c for c in cols if c['type'] != 'formula'}
    old = jload(cur['data'])
    patch = {}
    for k, v in (req.data or {}).items():
        if k in bykey:
            try:
                patch[k] = norm_val(bykey[k], v)
            except ValueError as e:
                raise HTTPException(400, str(e))
    merged = {**old, **patch}
    for k, v in patch.items():
        c = bykey.get(k)
        if c and c['required'] and v in (None, '', []):
            raise HTTPException(400, f"列「{c['label']}」必填，不能清空")
    detail = {k: {'old': old.get(k), 'new': v} for k, v in patch.items() if old.get(k) != v}
    n = ex("UPDATE kb_row SET data=%s,ver=ver+1 WHERE id=%s AND ver=%s",
           (jdump(merged), rid, req.ver))
    if not n:
        # 上面的预检之后、这条 UPDATE 之前，有人改了同一行：什么都没存上，按冲突处理。
        # 不能当成功：否则会写出假的审计、把没保存的内容写进行缓存。
        raise HTTPException(409, '这行已被别人改过，请刷新后重试')
    # 图片单元格：旧图不再被引用时进回收站（软删）
    for c in cols:
        if c['type'] == 'image':
            for iid in set(_cell_iids(old.get(c['key']))) - set(_cell_iids(merged.get(c['key']))):
                _trash_image(iid)
    audit(tid, rid, 'update_row', detail)
    newver = int(req.ver) + 1
    try:
        _cache_row_update(tid, rid, merged, newver)
    except Exception:
        _ROWS.pop(tid, None)
    try:
        kb_sync.source_event(tid, rid, 'upsert')
    except Exception as e:
        print(f'[sync] update_row hook failed: {e!r}', flush=True)
    actor = ((AUTH_USER.get() or {}).get('username') or '')
    kb_changes.bump(tid, 'update', [rid], actor)
    # 总表（镜像行）被改：把映射字段的值回写子表。只有带链接的镜像行才回写，总表自己新增的行不动子表。
    back = 0
    if kb_sync.is_master(tid):
        try:
            back = kb_sync.reverse_update(tid, rid, merged,
                                          [k for k in patch if old.get(k) != patch[k]], actor=actor)
        except Exception as e:
            print(f'[sync] reverse_update failed: {e!r}', flush=True)
    # 传 project：跨表函数（SUMIF/LOOKUP…）只有带上它才可用，否则这一行里的跨表公式会算成空，
    # 用户刚改一格就看到同行的跨表列变成“—”，刷新才回来。
    # 跨表索引是按数据库里的行建的，本表这一行刚改过：引用本表的公式（COUNTIF 本表）要按新值算，
    # 所以先把本表的索引作废（中间件在响应之后还会再作废一次，重复无害）。
    invalidate_xt_table(tid)
    return {'ok': True, 'ver': newver, 'synced_back': back,
            'row': compute_formulas(merged, cols, _t.get('project') or '')}


@app.delete("/api/tables/{tid}/rows/{rid}")
def delete_row(tid: int, rid: int):
    _t = _need(tid)
    need_table(tid, _t.get('project') or '', write=True)
    cur = q("SELECT data FROM kb_row WHERE id=%s AND table_id=%s AND deleted=0", (rid, tid), one=True)
    if not cur:
        raise HTTPException(404, '行不存在')
    ex("UPDATE kb_row SET deleted=1 WHERE id=%s", (rid,))
    # 行里引用的图片进回收站
    d = jload(cur['data'])
    for c in _table_columns(tid, include_hidden=True):
        if c['type'] == 'image':
            for iid in _cell_iids(d.get(c['key'])):
                _trash_image(iid)
    audit(tid, rid, 'delete_row', {'data': d})
    actor = ((AUTH_USER.get() or {}).get('username') or '')
    # 镜像行在总表被删：子表对应行一起软删（镜像语义）。不联动的话，下次全量同步会把这行又复制回来。
    if kb_sync.is_master(tid):
        try:
            kb_sync.reverse_delete(tid, rid, actor=actor)
        except Exception as e:
            print(f'[sync] reverse_delete failed: {e!r}', flush=True)
    kb_changes.bump(tid, 'delete', [rid], actor)
    try:
        kb_sync.source_event(tid, rid, 'delete')
    except Exception as e:
        print(f'[sync] delete_row hook failed: {e!r}', flush=True)
    return {'ok': True}


# ==================== 变更通知（服务器推送 / 轮询兜底） ====================
# 只推"哪几行变了"，不推数据：页面收到后自己取最新值，正在编辑的格子不会被覆盖；
# 多人同时改也只是多推几条提示，页面会合并成一次刷新（前端 300ms 去抖）。
@app.get("/api/tables/{tid}/changes")
def table_changes(tid: int, since: int = 0):
    """轮询兜底：客户端带上次拿到的 seq，返回比它新的事件。"""
    _t = _need(tid)
    need_table(tid, _t.get('project') or '')
    seq, evs = kb_changes.since(tid, since)
    return _json_response({'seq': seq, 'events': evs})


@app.get("/api/tables/{tid}/events")
async def table_events(tid: int, request: Request):
    """SSE：页面开着这张表时挂一条长连接，有写入就推一条变更提示。"""
    t = await run_in_threadpool(_need, tid)
    await run_in_threadpool(need_table, tid, t.get('project') or '')
    q_ = kb_changes.subscribe(tid)

    async def gen():
        last = kb_changes.current(tid)
        try:
            yield 'event: hello\ndata: ' + json.dumps({'seq': last}) + '\n\n'
            while True:
                if await request.is_disconnected():
                    break
                try:
                    await asyncio.wait_for(q_.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ': ping\n\n'          # 保活，别让代理把空闲连接掐了
                    continue
                cur, evs = kb_changes.since(tid, last)
                last = cur
                if evs:
                    yield ('event: change\ndata: '
                           + json.dumps({'seq': cur, 'events': evs}, ensure_ascii=False) + '\n\n')
        finally:
            kb_changes.unsubscribe(tid, q_)

    return StreamingResponse(gen(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


# ==================== 单元格批注（挂在格子上的备注，不改数据） ====================

class CommentReq(BaseModel):
    row_id: int
    col_key: str
    body: str


def _commenter():
    u = AUTH_USER.get() or {}
    return int(u.get('uid') or 0), str(u.get('username') or 'local')[:64]


@app.get("/api/tables/{tid}/comments/summary")
def comments_summary(tid: int):
    t = _need(tid)
    need_table(tid, t.get('project') or '')
    rows = q("SELECT row_id,col_key,COUNT(*) n FROM kb_comment WHERE table_id=%s AND deleted=0 "
             "GROUP BY row_id,col_key", (tid,))
    return _json_response({'items': [{'row_id': r['row_id'], 'col_key': r['col_key'], 'n': int(r['n'])} for r in rows]})


@app.get("/api/tables/{tid}/comments")
def comments_list(tid: int, row_id: int = 0, col_key: str = ''):
    t = _need(tid)
    need_table(tid, t.get('project') or '')
    if not row_id or not col_key:
        raise HTTPException(400, '需要指定 row_id 和 col_key')
    rows = q("SELECT id,body,author_id,author_name,created_at FROM kb_comment "
             "WHERE table_id=%s AND row_id=%s AND col_key=%s AND deleted=0 ORDER BY id", (tid, row_id, col_key))
    uid, _ = _commenter()
    return _json_response({'comments': [{'id': r['id'], 'body': r['body'], 'author_id': r['author_id'],
                                         'author': r['author_name'] or ('用户%d' % r['author_id']),
                                         'mine': int(r['author_id']) == uid,
                                         'at': _ts(r['created_at'])} for r in rows]})


@app.post("/api/tables/{tid}/comments")
def comment_add(tid: int, req: CommentReq):
    """批注不是改数据：有这张表的读权限就能写（只读账号也可以），删只能删自己的（管理员可以删任意）。"""
    t = _need(tid)
    need_table(tid, t.get('project') or '')
    body = (req.body or '').strip()
    if not body:
        raise HTTPException(400, '批注内容不能为空')
    if len(body) > 2000:
        raise HTTPException(400, '批注不能超过 2000 字')
    cols = {c['key'] for c in _table_columns(tid, include_hidden=True)}
    if req.col_key not in cols:
        raise HTTPException(404, '列不存在')
    row = q("SELECT id FROM kb_row WHERE id=%s AND table_id=%s AND deleted=0", (req.row_id, tid), one=True)
    if not row:
        raise HTTPException(404, '行不存在')
    uid, uname = _commenter()
    con = conn()
    try:
        cur = con.cursor()
        cur.execute("INSERT INTO kb_comment(table_id,row_id,col_key,body,author_id,author_name) "
                    "VALUES(%s,%s,%s,%s,%s,%s)", (tid, req.row_id, req.col_key, body, uid, uname))
        cid = cur.lastrowid
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()
    kb_changes.bump(tid, 'comment', [req.row_id], uname)
    return _json_response({'ok': True, 'id': cid,
                           'comment': {'id': cid, 'body': body, 'author_id': uid, 'author': uname,
                                       'mine': True, 'at': _ts(datetime.now())}})


@app.delete("/api/comments/{cid}")
def comment_del(cid: int):
    r = q("SELECT id,table_id,row_id,author_id FROM kb_comment WHERE id=%s AND deleted=0", (cid,), one=True)
    if not r:
        raise HTTPException(404, '批注不存在')
    t = _need(r['table_id'])
    need_table(r['table_id'], t.get('project') or '')
    uid, _ = _commenter()
    if int(r['author_id']) != uid and (AUTH_USER.get() or {}).get('role') != 'admin':
        raise HTTPException(403, '只能删除自己的批注')
    ex("UPDATE kb_comment SET deleted=1 WHERE id=%s", (cid,))
    kb_changes.bump(r['table_id'], 'comment', [r['row_id']], ((AUTH_USER.get() or {}).get('username') or ''))
    return {'ok': True}


# ==================== 公式预览 / 审计 / 导出 / 关联 ====================

class ExprReq(BaseModel):
    expr: str
    row: dict = {}
    tid: int | None = None


@app.post("/api/formula/eval")
def formula_eval(req: ExprReq):
    """前端编辑公式时实时试算（不落库）。
    只有对该表有字段管理权限的人能调；公式引用的外部表也必须在自己权限内。
    必须带表 ID：不带的话没有项目范围，任何账号都能借此读任意表。"""
    if not req.tid:
        raise HTTPException(400, '缺少表 ID')
    _need_fields(req.tid)
    t = _need(req.tid)
    project = t.get('project') or ''
    known = [{'key': c['key'], 'label': c['label']} for c in columns(req.tid)]
    ok, msg = validate_expr(req.expr, known, project, xt_check=_xt_check_for(project))
    if not ok:
        return {'ok': False, 'error': msg}
    return {'ok': True, 'value': eval_formula(req.expr, req.row or {}, project)}


@app.get("/api/tables/{tid}/audit")
def history(tid: int, limit: int = 100):
    t = _need(tid)
    need_table(tid, t.get('project') or '')
    flush_audit()          # 审计是后台批量写，读历史前先落盘
    limit = max(0, min(int(limit), 1000))      # 负数 / 超大值会被数据库当成语法或范围错误
    rows = q("SELECT id,row_id,action,actor,detail,"
             "DATE_FORMAT(at,'%%Y-%%m-%%d %%H:%%i:%%s') AS at "
             "FROM kb_audit WHERE table_id=%s ORDER BY id DESC LIMIT %s", (tid, limit))
    return {'rows': rows}


@app.get("/api/related")
def related(tid: int, row_id: int):
    t = _need(tid)
    need_table(tid, t.get('project') or '')
    # 行必须属于当前表：否则可以拿别的表的行来这里探测关联值
    r = q("SELECT data FROM kb_row WHERE id=%s AND table_id=%s AND deleted=0", (row_id, tid), one=True)
    if not r:
        raise HTTPException(404, '行不存在')
    data = jload(r['data'])
    tabs = {x['id']: x for x in q("SELECT id,name,project,deleted FROM kb_table")}
    out = []
    for rel in q("SELECT * FROM kb_relation WHERE from_tid=%s OR to_tid=%s", (tid, tid)):
        if rel['from_tid'] == tid:
            other, myf, of = rel['to_tid'], rel['from_field'], rel['to_field']
        else:
            other, myf, of = rel['from_tid'], rel['to_field'], rel['from_field']
        ot = tabs.get(other)
        if not ot or ot['deleted']:
            continue
        # 对方表也要有读权限：不然等于借关联接口读一张自己无权的表
        if table_role(other, ot.get('project') or '') is None:
            continue
        val = data.get(myf)
        if val is None or str(val).strip() == '':
            continue
        vl = str(val).strip().lower()
        cols = columns(other)
        hits = []
        # 走行缓存，不再每次把对方表整张查一遍
        for rr in _raw_rows(other):
            d = jload(rr['data'])
            if d.get(of) is not None and str(d.get(of)).strip().lower() == vl:
                hits.append({'id': rr['id'], 'data': compute_formulas(d, cols, ot.get('project') or '')})
        if hits:
            out.append({'table_id': other, 'table_name': ot['name'],
                        'my_field': myf, 'other_field': of, 'value': str(val),
                        'rows': hits[:30], 'total': len(hits),
                        'columns': [c['key'] for c in cols[:6]]})
    out.sort(key=lambda g: -g['total'])
    return _json_response({'groups': out})


_CSV_DANGER = ('=', '+', '-', '@', '\t', '\r')


def _csv_cell(v):
    """导出 CSV 防公式注入：以 = + - @ 开头的文本，用 Excel 打开时会被当成公式执行。"""
    if isinstance(v, str) and v[:1] in _CSV_DANGER:
        return "'" + v
    return v


@app.get("/api/tables/{tid}/export")
def export_csv(tid: int):
    t = _need(tid)
    need_table(tid, t.get('project') or '')
    cols = columns(tid)
    project = t.get('project') or ''
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c['label'] for c in cols])
    for r in q("SELECT data FROM kb_row WHERE table_id=%s AND deleted=0 ORDER BY sort,id", (tid,)):
        d = compute_formulas(jload(r['data']), cols, project)
        w.writerow([_csv_cell(d.get(c['key'])) for c in cols])
    fname = quote(f"{t['name']}.csv")
    return StreamingResponse(iter([buf.getvalue()]), media_type='text/csv; charset=utf-8',
                             headers={'Content-Disposition': f"attachment; filename*=UTF-8''{fname}"})


# ==================== 图片上传 / 读取 ====================

_IMG_MAX = 8 * 1024 * 1024


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


class ImageTrashReq(BaseModel):
    ids: list
    action: str          # purge=彻底删除 / restore=恢复


@app.get("/api/images/trash")
def image_trash():
    _need_admin()
    rows = q("SELECT i.id, i.table_id, i.mime, i.size, i.deleted_at, "
             "COALESCE(t.name, '（表已删除）') AS table_name "
             "FROM kb_image i LEFT JOIN kb_table t ON t.id = i.table_id "
             "WHERE i.deleted=1 ORDER BY i.deleted_at DESC, i.id DESC LIMIT 500")
    return {'rows': rows}


@app.post("/api/images/trash/act")
def image_trash_act(req: ImageTrashReq):
    _need_admin()
    ids = [int(x) for x in (req.ids or []) if str(x).strip().isdigit()]
    if not ids:
        raise HTTPException(400, '没有选择图片')
    ph = ','.join(['%s'] * len(ids))
    if req.action == 'purge':
        ex(f"DELETE FROM kb_image WHERE id IN ({ph}) AND deleted=1", tuple(ids))
    elif req.action == 'restore':
        ex(f"UPDATE kb_image SET deleted=0, deleted_at=NULL WHERE id IN ({ph}) AND deleted=1", tuple(ids))
    else:
        raise HTTPException(400, 'action 无效')
    return {'ok': True, 'action': req.action}


@app.post("/api/upload/image")
def upload_image(file: UploadFile = File(...), tid: int = Form(...)):
    """同步函数：FastAPI 会把它放进线程池，写库（远程库一次往返）不占主线程。"""
    t = _need(tid)
    need_table(tid, t.get('project') or '', write=True)
    # 最多读到「上限 + 1」字节就停：超限的文件不用整个读进内存
    raw = file.file.read(_IMG_MAX + 1)
    if not raw:
        raise HTTPException(400, '图片为空')
    if len(raw) > _IMG_MAX:
        raise HTTPException(413, '图片不能超过 8MB')
    mime = _image_kind(raw)
    if not mime:
        raise HTTPException(400, '只支持 png / jpg / gif / webp / bmp')
    actor = (AUTH_USER.get() or {}).get('username') or 'local'
    iid = ex("INSERT INTO kb_image(table_id,mime,size,data,created_by) VALUES(%s,%s,%s,%s,%s)",
             (tid, mime, len(raw), raw, actor))
    return {'ok': True, 'id': iid, 'mime': mime, 'size': len(raw),
            'url': f'/api/tables/{tid}/images/{iid}'}


@app.get("/api/tables/{tid}/images/{iid}")
def get_image(tid: int, iid: int):
    t = _need(tid)
    need_table(tid, t.get('project') or '')
    r = q("SELECT mime,data FROM kb_image WHERE id=%s AND table_id=%s", (iid, tid), one=True)
    if not r:
        raise HTTPException(404, '图片不存在')
    return Response(content=r['data'], media_type=r['mime'],
                    headers={'Cache-Control': 'private, no-store',
                             'X-Content-Type-Options': 'nosniff'})


# ==================== HTTP 写入（表级密钥，免登录插数据） ====================

_KEY_PREFIX = 'kbk_'


class ApiKeyReq(BaseModel):
    note: str = ''


class ApiInsertReq(BaseModel):
    data: dict | None = None
    rows: list | None = None


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


@app.get("/api/tables/{tid}/apikeys")
def apikey_list(tid: int):
    _need_admin()
    _need(tid)
    rows = q("SELECT id,key_prefix,note,created_by,revoked,"
             "DATE_FORMAT(created_at,'%%Y-%%m-%%d %%H:%%i') AS created_at "
             "FROM kb_apikey WHERE table_id=%s ORDER BY id DESC", (tid,))
    return {'rows': rows}


@app.post("/api/tables/{tid}/apikeys")
def apikey_create(tid: int, req: ApiKeyReq):
    """生成密钥：明文只在本次响应出现一次，库里只存 sha256。"""
    _need_admin()
    _need(tid)
    note = (req.note or '').strip()[:120]
    plain = _KEY_PREFIX + secrets.token_hex(20)
    actor = (AUTH_USER.get() or {}).get('username') or 'admin'
    kid = ex("INSERT INTO kb_apikey(table_id,key_hash,key_prefix,note,created_by) "
             "VALUES(%s,%s,%s,%s,%s)",
             (tid, _key_hash(plain), plain[:12], note, actor))
    audit(tid, None, 'apikey_create', {'id': kid, 'note': note}, actor)
    return {'ok': True, 'id': kid, 'key': plain}


@app.delete("/api/tables/{tid}/apikeys/{kid}")
def apikey_revoke(tid: int, kid: int):
    _need_admin()
    _need(tid)
    r = q("SELECT id,note FROM kb_apikey WHERE id=%s AND table_id=%s", (kid, tid), one=True)
    if not r:
        raise HTTPException(404, '密钥不存在')
    ex("UPDATE kb_apikey SET revoked=1 WHERE id=%s AND table_id=%s", (kid, tid))
    actor = (AUTH_USER.get() or {}).get('username') or 'admin'
    audit(tid, None, 'apikey_revoke', {'id': kid, 'note': r['note']}, actor)
    return {'ok': True}


@app.post("/api/in/{tid}")
def api_insert(request: Request, tid: int, req: ApiInsertReq, x_kb_token: str = Header(default='')):
    """免登录写入（影刀 / RPA / 脚本用）。请求头 `X-KB-Token: kbk_...`。

    body 二选一：{"data":{...}} 单行；{"rows":[{...},...]} 批量（≤1000 行）。
    列名可写字段 key 或字段显示名；未知字段忽略，公式列不可写，必填列校验。
    """
    key = _key_auth(tid, x_kb_token)
    _need(tid)
    cols = columns(tid, include_hidden=True)
    if not cols:
        raise HTTPException(400, '这张表还没有列，先在界面加列')
    items = req.rows if req.rows is not None else ([req.data] if req.data is not None else [])
    if not items:
        raise HTTPException(400, 'body 需要 {"data":{...}} 或 {"rows":[...]}')
    if len(items) > 1000:
        raise HTTPException(400, '单次最多 1000 行')
    bykey, bylabel = {}, {}
    for c in cols:
        if c['type'] == 'formula':
            continue
        bykey[c['key']] = c
        bylabel[c['label']] = c
    clean_rows = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise HTTPException(400, f'第 {i + 1} 行不是对象')
        clean = {}
        for k, v in item.items():
            c = bykey.get(k) or bylabel.get(k)
            if not c:                     # 未知列/公式列直接忽略
                continue
            try:
                clean[c['key']] = norm_val(c, v)
            except ValueError as e:
                raise HTTPException(400, f'第 {i + 1} 行：{e}')
        for c in cols:
            if c['type'] == 'formula' or not c['required']:
                continue
            if c['key'] not in clean or clean[c['key']] in (None, '', []):
                raise HTTPException(400, f'第 {i + 1} 行：列「{c["label"]}」必填')
        clean_rows.append(clean)
    mx = q("SELECT COALESCE(MAX(sort),0) m FROM kb_row WHERE table_id=%s",
           (tid,), one=True)['m']
    actor = ('api:' + (key['note'] or ('key#' + str(key['id']))))[:64]
    first_id = None
    for start in range(0, len(clean_rows), 500):
        chunk = clean_rows[start:start + 500]
        sql = ("INSERT INTO kb_row(table_id,data,sort) VALUES "
               + ','.join(['(%s,%s,%s)'] * len(chunk)))
        args = []
        for k, d in enumerate(chunk):
            args += [tid, jdump(d), mx + start + k + 1]
        rid = ex(sql, tuple(args))
        if first_id is None:
            first_id = rid
    ids = list(range(int(first_id), int(first_id) + len(clean_rows)))
    detail = {'count': len(clean_rows), 'key': key['key_prefix']}
    if len(clean_rows) == 1:
        detail['data'] = clean_rows[0]
    audit(tid, None, 'api_insert', detail, actor)
    # 告诉缓存中间件这次写了几行：只失效这一张表并补上行数，不清空全部缓存
    request.state.kb_inserted = len(clean_rows)
    try:
        kb_sync.source_ids(tid, ids)
    except Exception as e:
        print(f'[sync] api_insert hook failed: {e!r}', flush=True)
    return {'ok': True, 'inserted': len(clean_rows), 'ids': ids}


# ==================== Excel / CSV 导入 ====================

_IMP_MAX_BYTES = 20 * 1024 * 1024
_IMP_MAX_ROWS = 50000
_IMP_MAX_COLS = 300


def _imp_login():
    u = AUTH_USER.get()
    if u is not None and u.get('role') == 'guest':
        raise HTTPException(401, '游客仅可查看项目列表，登录后使用导入：/login')


def _imp_pd():
    import pandas as pd
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


@app.post("/api/import/preview")
async def import_preview(file: UploadFile = File(...), sheet: str = Form(''),
                         header_row: int = Form(1), table_id: int = Form(0), project: str = Form('')):
    """先读预览：可选工作表、看行列数、看前 8 行；不会直接落库。
    解析放到线程池：在 async 函数里同步跑 pandas，会把所有人的请求一起卡住。"""
    _imp_login()
    filename = os.path.basename(file.filename or '')
    if not filename:
        raise HTTPException(400, '没有文件名')
    if os.path.splitext(filename.lower())[1] not in ('.xlsx', '.xls', '.xlsm', '.csv'):
        raise HTTPException(400, '只支持 xlsx / xls / xlsm / csv')
    await run_in_threadpool(_imp_check_target, table_id, project)
    raw = await _read_upload(file, _IMP_MAX_BYTES, '文件不能超过 20MB')
    if not raw:
        raise HTTPException(400, '文件为空')
    return await run_in_threadpool(_imp_preview_sync, raw, filename, sheet, header_row)


@app.post("/api/import/commit")
async def import_commit(file: UploadFile = File(...), config: str = Form(...)):
    """落库。读文件之外的全部工作（解析、类型转换、写库）都在线程池里做。"""
    _imp_login()
    try:
        cfg = json.loads(config)
        if not isinstance(cfg, dict):
            raise ValueError()
    except Exception:
        raise HTTPException(400, '导入配置不是合法 JSON')
    filename = os.path.basename(file.filename or '')
    if os.path.splitext(filename.lower())[1] not in ('.xlsx', '.xls', '.xlsm', '.csv'):
        raise HTTPException(400, '只支持 xlsx / xls / xlsm / csv')
    raw = await _read_upload(file, _IMP_MAX_BYTES, '文件不能超过 20MB')
    if not raw:
        raise HTTPException(400, '文件为空')
    return await run_in_threadpool(_imp_commit_sync, raw, filename, cfg)


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


# ==================== 汇总同步（子表 → 总表，单向复制） ====================

class SyncReq(BaseModel):
    target_tid: int
    source_tid: int
    mapping: dict
    name: str = ''
    enabled: bool = True


class SyncPatch(BaseModel):
    mapping: dict | None = None
    name: str | None = None
    enabled: bool | None = None


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


@app.get("/api/sync")
def list_sync(target_tid: int):
    _need_admin()
    _need(target_tid)
    rows = q("SELECT s.id,s.target_tid,s.source_tid,s.name,s.mapping,s.enabled,"
             "s.last_sync_at,s.last_error,t.name AS source_name,t.project AS source_project "
             "FROM kb_sync s JOIN kb_table t ON t.id=s.source_tid "
             "WHERE s.target_tid=%s ORDER BY s.id", (target_tid,))
    rules = [_sync_rule_row(r) for r in rows]
    for r in rules:
        r['source_count'] = q("SELECT COUNT(*) AS n FROM kb_row "
                              "WHERE table_id=%s AND deleted=0", (r['source_tid'],), one=True)['n']
        r['copied_count'] = q("SELECT COUNT(*) AS n FROM kb_sync_link "
                              "WHERE sync_id=%s", (r['id'],), one=True)['n']
    return {'rules': rules}


@app.post("/api/sync")
def create_sync(req: SyncReq):
    _need_admin()
    tt, st, mapping = _sync_validate(req.target_tid, req.source_tid, req.mapping)
    name = (req.name or '').strip()[:160] or st['name']
    old = q("SELECT id FROM kb_sync WHERE target_tid=%s AND source_tid=%s",
            (req.target_tid, req.source_tid), one=True)
    if old:
        sid = old['id']
        ex("UPDATE kb_sync SET name=%s,mapping=%s,enabled=%s,last_error='' WHERE id=%s",
           (name, jdump(mapping), 1 if req.enabled else 0, sid))
    else:
        sid = ex("INSERT INTO kb_sync(target_tid,source_tid,name,mapping,enabled,created_by) "
                 "VALUES(%s,%s,%s,%s,%s,%s)",
                 (req.target_tid, req.source_tid, name, jdump(mapping),
                  1 if req.enabled else 0, (AUTH_USER.get() or {}).get('username') or ''))
    kb_sync.invalidate_configs()
    job = kb_sync.start_full(sid) if req.enabled else None
    return {'ok': True, 'id': sid, 'job': job}


@app.patch("/api/sync/{sid}")
def patch_sync(sid: int, req: SyncPatch):
    _need_admin()
    r = q("SELECT id,target_tid,source_tid,mapping,enabled FROM kb_sync WHERE id=%s",
          (sid,), one=True)
    if not r:
        raise HTTPException(404, '汇总规则不存在')
    mapping = req.mapping
    if mapping is not None:
        _tt, _st, mapping = _sync_validate(r['target_tid'], r['source_tid'], mapping)
        ex("UPDATE kb_sync SET mapping=%s,last_error='' WHERE id=%s", (jdump(mapping), sid))
    if req.name is not None:
        ex("UPDATE kb_sync SET name=%s WHERE id=%s", ((req.name or '').strip()[:160], sid))
    if req.enabled is not None:
        ex("UPDATE kb_sync SET enabled=%s WHERE id=%s", (1 if req.enabled else 0, sid))
    kb_sync.invalidate_configs()
    cur = q("SELECT enabled FROM kb_sync WHERE id=%s", (sid,), one=True)
    job = None
    if cur and cur['enabled']:
        job = kb_sync.start_full(sid)
    return {'ok': True, 'job': job}


@app.post("/api/sync/{sid}/run")
def run_sync(sid: int):
    _need_admin()
    r = q("SELECT id,enabled FROM kb_sync WHERE id=%s", (sid,), one=True)
    if not r:
        raise HTTPException(404, '汇总规则不存在')
    if not r['enabled']:
        raise HTTPException(400, '汇总规则已停用')
    return {'ok': True, 'job': kb_sync.start_full(sid)}


@app.delete("/api/sync/{sid}")
def delete_sync(sid: int):
    _need_admin()
    if not kb_sync.delete_config(sid, purge=True):
        raise HTTPException(404, '汇总规则不存在')
    return {'ok': True}


# ==================== 数据分析看板 ====================

class DashboardReq(BaseModel):
    project: str
    name: str = '默认看板'
    config: dict | None = None
    is_page: bool = False


class DashboardPatch(BaseModel):
    name: str | None = None
    config: dict | None = None
    is_page: bool | None = None


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


@app.get("/api/dashboards")
def list_dashboards(project: str = ''):
    role = need_project(project, write=False)
    rows = q("SELECT id,project,name,config,is_page,created_by,sort,updated_at FROM kb_dashboard "
             "WHERE project=%s AND deleted=0 ORDER BY sort,id", (project,))
    out = []
    for r in rows:
        d = _dashboard_out(r)
        d['config'] = _filter_dash_charts(d['config'], project)
        if role != 'editor' and not d['config']['charts']:
            continue
        out.append(d)
    return {'dashboards': out,
            'my_role': role, 'can_edit': project_role(project) == 'editor'}


@app.post("/api/dashboards")
def create_dashboard(req: DashboardReq):
    need_project(req.project, write=True)
    name = _valid_dashboard_name(req.name)
    cfg = req.config if isinstance(req.config, dict) else {'charts': []}
    if len(json.dumps(cfg, ensure_ascii=False)) > 400000:
        raise HTTPException(400, '看板配置过大')
    nxt = q("SELECT COALESCE(MAX(sort),0)+1 AS n FROM kb_dashboard WHERE project=%s", (req.project,), one=True)
    actor = (AUTH_USER.get() or {}).get('username') or 'local'
    did = ex("INSERT INTO kb_dashboard (project,name,config,is_page,created_by,sort) VALUES (%s,%s,%s,%s,%s,%s)",
             (req.project, name, jdump(cfg), 1 if req.is_page else 0, actor, int(nxt['n'] or 1)))
    r = q("SELECT id,project,name,config,is_page,created_by,sort,updated_at FROM kb_dashboard WHERE id=%s", (did,), one=True)
    return {'dashboard': _dashboard_out(r)}


@app.patch("/api/dashboards/{did}")
def update_dashboard(did: int, req: DashboardPatch):
    r = q("SELECT * FROM kb_dashboard WHERE id=%s AND deleted=0", (did,), one=True)
    if not r:
        raise HTTPException(404, '看板不存在')
    need_project(r['project'], write=True)
    sets, args = [], []
    if req.name is not None:
        sets.append('name=%s')
        args.append(_valid_dashboard_name(req.name))
    if req.config is not None:
        cfg = req.config if isinstance(req.config, dict) else {'charts': []}
        if len(json.dumps(cfg, ensure_ascii=False)) > 400000:
            raise HTTPException(400, '看板配置过大')
        sets.append('config=%s')
        args.append(jdump(cfg))
    if req.is_page is not None:
        sets.append('is_page=%s')
        args.append(1 if req.is_page else 0)
    if sets:
        args.append(did)
        ex("UPDATE kb_dashboard SET " + ', '.join(sets) + " WHERE id=%s", tuple(args))
    r = q("SELECT id,project,name,config,is_page,created_by,sort,updated_at FROM kb_dashboard WHERE id=%s", (did,), one=True)
    return {'dashboard': _dashboard_out(r)}


@app.delete("/api/dashboards/{did}")
def delete_dashboard(did: int):
    r = q("SELECT * FROM kb_dashboard WHERE id=%s AND deleted=0", (did,), one=True)
    if not r:
        raise HTTPException(404, '看板不存在')
    need_project(r['project'], write=True)
    ex("UPDATE kb_dashboard SET deleted=1 WHERE id=%s", (did,))
    return {'ok': True}


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

try:
    import inspect
    from starlette.middleware import gzip as _gzip_middleware
    # 新版压缩中间件遇到已经带 Content-Encoding 的响应会跳过；老版会再压一次（页面就坏了），先确认
    _GZIP_PRECOMPRESSED = 'content-encoding' in inspect.getsource(_gzip_middleware)
except Exception:
    _GZIP_PRECOMPRESSED = False

_INDEX_CACHE = {'key': None}


def _index_asset():
    """首页 HTML 按文件版本缓存：原始内容、gzip 结果、ETag、CSP 一起算好。
    以前每次打开都重新读盘、重新压缩 340KB（约 17ms 单核时间），而且不让浏览器缓存，
    2M 带宽下每次打开都要重下 84KB。"""
    path = os.path.join(FRONTEND, 'index.html')
    st = os.stat(path)
    key = (int(st.st_mtime), st.st_size)
    c = _INDEX_CACHE
    if c['key'] != key:
        with open(path, 'rb') as f:
            raw = f.read()
        c.update(key=key, raw=raw, gz=gzip.compress(raw, 5),
                 etag=f'"kb{st.st_mtime_ns:x}-{st.st_size:x}"',
                 csp=csp_for_html(raw.decode('utf-8', 'replace')))
    return c


@app.get("/")
def index(request: Request):
    c = _index_asset()
    # 允许浏览器存，但每次都要拿 ETag 来问一句：文件没变就回 304，不用重新下载
    headers = {'ETag': c['etag'], 'Cache-Control': 'no-cache',
               'X-Content-Type-Options': 'nosniff',
               'Content-Security-Policy': c['csp']}
    if request.headers.get('if-none-match') == c['etag']:
        return Response(status_code=304, headers=headers)
    if _GZIP_PRECOMPRESSED and 'gzip' in (request.headers.get('accept-encoding') or '').lower():
        headers['Content-Encoding'] = 'gzip'
        headers['Vary'] = 'Accept-Encoding'
        return Response(content=c['gz'], media_type='text/html; charset=utf-8', headers=headers)
    return Response(content=c['raw'], media_type='text/html; charset=utf-8', headers=headers)
