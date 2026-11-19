# -*- coding: utf-8 -*-
"""多维表格（开放版）—— HTTP 接口。

前缀 /api/mtables。权限模型见包 __init__.py 文档：
  读接口用 verify_token（游客=只读）；写行接口 require_login；表结构/用户/密钥等 require_admin。
写操作全部落审计（变动历史）；行更新走乐观锁（ver 不匹配 → 409）。
"""
import base64
import io
import json
import os
import re
import uuid
import mimetypes
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..deps import require_admin, require_login, verify_token
from . import store
from .store import COL_TYPES, audit, ex, jdump, jload, q, effective_role

router = APIRouter(prefix='/api/mtables', tags=['多维表格'])


# ==================== 工具 ====================

def _actor(payload):
    return (payload or {}).get('sub') or (payload or {}).get('username') or ''


def _is_admin(payload):
    return ((payload or {}).get('role') or 'staff') == 'admin'


def _need(table_id, payload, write=False):
    """校验访问：表存在即可读（游客只读）；write=True 要求已登录（admin 添加的用户即可）。"""
    t = q("SELECT id, name FROM mt_table WHERE id=%s AND deleted=0", (table_id,), one=True)
    if not t:
        raise HTTPException(404, '表不存在或已删除')
    role, _ = store.effective_role(table_id, payload)
    if write and role != 'editor':
        raise HTTPException(403, '只读权限：登录后才能修改（账号找管理员开通）')
    return role


def _sync_view(table_id):
    """列定义变更后，若该表视图已存在则自动重建（失败不影响主流程，留痕 stderr）。"""
    try:
        if store.view_exists(table_id):
            store.rebuild_view(table_id, _columns(table_id))
    except Exception as e:  # pragma: no cover
        import sys
        print(f'[mtables] 视图自动重建失败 table={table_id}: {e}', file=sys.stderr)


def _slug(s):
    """中文也允许做 key，但去掉空格与特殊符号。key 仅用于 JSON 存取。"""
    s = (s or '').strip()
    s = re.sub(r'[\s\'"`\\/]+', '_', s)
    return s[:64] or 'col'


def _ts(v):
    """datetime/日期统一成 'YYYY-MM-DD HH:MM:SS'。"""
    if v is None:
        return ''
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(v, date):
        return v.strftime('%Y-%m-%d')
    return str(v)


_COL_KEYS = {'id', 'table_id', 'key', 'label', 'type', 'options', 'required', 'width', 'sort', 'created_at'}


def _norm_val(col, v):
    """按列类型规范化单元格值。"""
    t = col['type']
    if v is None or v == '':
        return None
    if t == 'number':
        try:
            f = float(v)
            return int(f) if f == int(f) else f
        except Exception:
            raise HTTPException(400, f"列「{col['label']}」需要数字")
    if t == 'checkbox':
        if isinstance(v, bool):
            return v
        return str(v).lower() in ('1', 'true', 'yes', 'on', '是')
    if t == 'multiselect':
        if isinstance(v, (list, tuple)):
            return [str(x) for x in v]
        return [str(v)]
    if t == 'select':
        opts = col.get('options') or []
        v = str(v)
        if opts and v not in opts:
            raise HTTPException(400, f"列「{col['label']}」的值「{v}」不在可选项内")
        return v
    if t == 'date':
        s = str(v).strip()
        # 只接受 YYYY-MM-DD；带时间的截断
        m = re.match(r'^(\d{4}-\d{2}-\d{2})', s)
        if not m:
            raise HTTPException(400, f"列「{col['label']}」需要 YYYY-MM-DD 格式")
        return m.group(1)
    if t == 'image':
        return str(v).strip() or None
    return str(v)


def _columns(table_id):
    rows = q("SELECT id, `key`, label, type, options, required, width, sort "
             "FROM mt_column WHERE table_id=%s ORDER BY sort, id", (table_id,))
    out = []
    for r in rows:
        o = r['options']
        if isinstance(o, str):
            try:
                o = json.loads(o)
            except Exception:
                o = None
        out.append({**r, 'options': o, 'required': bool(r['required'])})
    return out


def _validate_row(cols, data, partial=False):
    """校验整行数据。partial=True 用于部分更新，只校验传入的键。"""
    bykey = {c['key']: c for c in cols}
    clean = {}
    for k, v in (data or {}).items():
        if k in _COL_KEYS and k in bykey:
            pass
        col = bykey.get(k)
        if not col:
            # 未知列直接忽略（列被删后残留的旧数据）
            continue
        clean[k] = _norm_val(col, v)
    if not partial:
        for c in cols:
            if c['required'] and not clean.get(c['key']):
                raise HTTPException(400, f"列「{c['label']}」必填")
    return clean


# ==================== 请求体 ====================

class TableReq(BaseModel):
    name: str
    note: str = ''
    columns: Optional[list] = None   # [{key?,label,type?,options?,required?}]


class ImportReq(BaseModel):
    name: str
    note: str = ''
    file_b64: str   # .xlsx 文件内容（前端 FileReader 转 base64）


class ColumnReq(BaseModel):
    key: Optional[str] = None
    label: str
    type: str = 'text'
    options: Optional[list] = None
    required: bool = False
    width: int = 0
    sort: Optional[int] = None


class RowReq(BaseModel):
    data: dict
    ver: Optional[int] = None


class ApiKeyReq(BaseModel):
    note: str = ''


class ApiRowReq(BaseModel):
    """HTTP 密钥写入：data=单行 或 rows=批量（≤1000）。"""
    data: Optional[dict] = None
    rows: Optional[list] = None


class UserCreateReq(BaseModel):
    username: str
    password: str
    real_name: str = ''


class UserPatchReq(BaseModel):
    password: Optional[str] = None
    real_name: Optional[str] = None
    status: Optional[int] = None   # 1=启用 0=停用


# ==================== 表（游客可看） ====================

@router.get('/tables')
def list_tables(payload: dict = Depends(verify_token)):
    """表列表。开放版：所有人（含游客）可见全部表；游客 role=reader 只读。

    性能铁律：计数用聚合一次查完，绝不在循环里按表查库。
    """
    rows = q("SELECT t.id, t.name, t.note, t.created_by, t.created_at, "
             "(SELECT COUNT(*) FROM mt_row r WHERE r.table_id=t.id AND r.deleted=0) AS row_count, "
             "(SELECT COUNT(*) FROM mt_column c WHERE c.table_id=t.id) AS col_count "
             "FROM mt_table t WHERE t.deleted=0 ORDER BY t.id")
    is_admin = _is_admin(payload)
    out = []
    for r in rows:
        role, _ = effective_role(r['id'], payload)
        out.append({'id': r['id'], 'name': r['name'], 'note': r['note'],
                    'created_by': r['created_by'], 'created_at': _ts(r['created_at']),
                    'row_count': r['row_count'], 'col_count': r['col_count'], 'role': role,
                    'heavy': int(r['row_count']) >= HEAVY_ROWS,
                    'can_edit': role == 'editor', 'is_admin': is_admin})
    return {'rows': out}


@router.get('/tables/{table_id}')
def get_table(table_id: int, payload: dict = Depends(verify_token)):
    role = _need(table_id, payload)
    t = q("SELECT * FROM mt_table WHERE id=%s AND deleted=0", (table_id,), one=True)
    return {'table': {**t, 'role': role, 'can_edit': role == 'editor'},
            'columns': _columns(table_id)}


@router.post('/tables')
def create_table(req: TableReq, payload: dict = Depends(require_admin)):
    """建表（仅 admin）。可同时带列定义。"""
    name = (req.name or '').strip()
    if not name:
        raise HTTPException(400, '表名不能为空')
    if q("SELECT id FROM mt_table WHERE name=%s AND deleted=0", (name,), one=True):
        raise HTTPException(400, f'表「{name}」已存在')
    me = _actor(payload)
    tid = ex("INSERT INTO mt_table (name, note, created_by) VALUES (%s,%s,%s)", (name, req.note or '', me))
    audit(tid, None, 'create_table', me, {'name': name})
    cols = req.columns or [
        {'label': '名称', 'type': 'text', 'required': True},
        {'label': '备注', 'type': 'text'},
    ]
    used = set()
    for i, c in enumerate(cols):
        label = (c.get('label') or '').strip()
        if not label:
            continue
        key = _slug(c.get('key') or label)
        while key in used:
            key = key + '_'
        used.add(key)
        t = c.get('type') or 'text'
        if t not in COL_TYPES:
            raise HTTPException(400, f'未知列类型 {t}')
        ex("INSERT INTO mt_column (table_id, `key`, label, type, options, required, sort) "
           "VALUES (%s,%s,%s,%s,%s,%s,%s)",
           (tid, key, label, t, jdump(c.get('options')), 1 if c.get('required') else 0, i))
        audit(tid, None, 'create_column', me, {'label': label, 'type': t})
    return {'id': tid, 'name': name}


# ==================== Excel 导入建表 ====================

_BOOL_WORDS = {'是': True, '否': False, 'y': True, 'n': False, 'yes': True, 'no': False,
               'true': True, 'false': False}
_DATE_RE = re.compile(r'^(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})日?$')
MAX_IMPORT_ROWS = 10000


def _to_num(v):
    """数字列取值：bool 不是数字；数字串容忍千分位逗号。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return int(f) if f == int(f) else f
    s = str(v).strip().replace(',', '')
    try:
        f = float(s)
        return int(f) if f == int(f) else f
    except Exception:
        return None


def _to_datestr(v):
    """日期列取值：Excel 日期单元格是 datetime；纯时间部分为 0 时只留日期。"""
    if isinstance(v, datetime):
        if (v.hour, v.minute, v.second) == (0, 0, 0):
            return v.strftime('%Y-%m-%d')
        return v.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(v, date):
        return v.strftime('%Y-%m-%d')
    m = _DATE_RE.match(str(v).strip())
    if m:
        return '%04d-%02d-%02d' % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def _infer_type(values):
    """按一列非空样本（最多 500 个）推断列类型；全空 → text。"""
    vals = [v for v in values if v is not None and str(v).strip() != ''][:500]
    if not vals:
        return 'text'
    if all(isinstance(v, bool) for v in vals):
        return 'checkbox'
    if all(isinstance(v, (datetime, date)) for v in vals):
        return 'date'
    if all(_to_num(v) is not None for v in vals):
        return 'number'
    if all(isinstance(v, str) and _to_datestr(v) is not None for v in vals):
        return 'date'
    if all(isinstance(v, str) and v.strip().lower() in _BOOL_WORDS for v in vals):
        return 'checkbox'
    return 'text'


def _norm_import(v, t):
    """按推断出的类型把单元格值落成 JSON 值；空单元格不落键。"""
    if v is None or str(v).strip() == '':
        return None
    if t == 'number':
        return _to_num(v)
    if t == 'date':
        return _to_datestr(v)
    if t == 'checkbox':
        if isinstance(v, bool):
            return v
        return _BOOL_WORDS.get(str(v).strip().lower(), False)
    if isinstance(v, (datetime, date)):
        return _to_datestr(v)
    return str(v).strip()


@router.post('/tables/import')
def import_table(req: ImportReq, payload: dict = Depends(require_admin)):
    """Excel 导入成新表（仅 admin）。

    只吃 .xlsx；取第一个工作表；第一个非空行当列名，其后每行一条数据（全空行跳过）；
    列类型按整列样本自动识别（数字/日期/勾选/文本），导完可点列头「改列」调整。
    行按 500/批 multi-VALUES 插入；整次导入只写一条 import_rows 审计，不逐行刷。
    """
    name = (req.name or '').strip()
    if not name:
        raise HTTPException(400, '表名不能为空')
    if q("SELECT id FROM mt_table WHERE name=%s AND deleted=0", (name,), one=True):
        raise HTTPException(400, f'表「{name}」已存在')
    try:
        raw = base64.b64decode(req.file_b64)
    except Exception:
        raise HTTPException(400, '文件内容读取失败')
    if not raw:
        raise HTTPException(400, '文件是空的')
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(400, '文件超过 10MB 上限，请拆分后导入')
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception:
        raise HTTPException(400, 'Excel 解析失败：只支持 .xlsx，老 .xls 请先另存为 .xlsx')
    try:
        grid = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    finally:
        wb.close()

    def _filled(r):
        return any(c is not None and str(c).strip() != '' for c in r)

    hidx = next((i for i, r in enumerate(grid) if _filled(r)), None)
    if hidx is None:
        raise HTTPException(400, '工作表是空的')
    colpos = [(j, str(c).strip()) for j, c in enumerate(grid[hidx])
              if c is not None and str(c).strip() != '']
    if not colpos:
        raise HTTPException(400, '第一行没有列名，无法导入（第一行会被当作列名）')
    body_rows = []
    for r in grid[hidx + 1:]:
        if not _filled(r):
            continue
        body_rows.append(r)
        if len(body_rows) > MAX_IMPORT_ROWS:
            raise HTTPException(400, f'数据超过 {MAX_IMPORT_ROWS} 行上限，请拆分后导入')

    used = set()
    cols = []
    for j, label in colpos:
        key = _slug(label)
        while key in used:
            key = key + '_'
        used.add(key)
        vals = [r[j] if j < len(r) else None for r in body_rows]
        cols.append({'key': key, 'label': label, 'pos': j, 'type': _infer_type(vals)})

    me = _actor(payload)
    tid = ex("INSERT INTO mt_table (name, note, created_by) VALUES (%s,%s,%s)",
             (name, req.note or '', me))
    audit(tid, None, 'create_table', me, {'name': name, 'from': 'excel-import'})
    for i, c in enumerate(cols):
        ex("INSERT INTO mt_column (table_id, `key`, label, type, options, required, sort) "
           "VALUES (%s,%s,%s,%s,%s,0,%s)", (tid, c['key'], c['label'], c['type'], None, i))
        audit(tid, None, 'create_column', me, {'label': c['label'], 'type': c['type']})
    for start in range(0, len(body_rows), 500):
        chunk = body_rows[start:start + 500]
        sql = ("INSERT INTO mt_row (table_id, data, sort, created_by, updated_by) VALUES "
               + ','.join(['(%s,%s,%s,%s,%s)'] * len(chunk)))
        args = []
        for k, r in enumerate(chunk):
            data = {}
            for c in cols:
                if c['pos'] >= len(r):
                    continue
                v = _norm_import(r[c['pos']], c['type'])
                if v is not None:
                    data[c['key']] = v
            args += [tid, jdump(data), start + k + 1, me, me]
        ex(sql, tuple(args))
    audit(tid, None, 'import_rows', me, {'count': len(body_rows),
                                         'types': {c['label']: c['type'] for c in cols}})
    _sync_view(tid)
    return {'id': tid, 'name': name, 'row_count': len(body_rows), 'col_count': len(cols)}


@router.delete('/tables/{table_id}')
def delete_table(table_id: int, payload: dict = Depends(require_admin)):
    """删表（仅 admin，软删；行数据一并软删）。"""
    t = q("SELECT name FROM mt_table WHERE id=%s AND deleted=0", (table_id,), one=True)
    if not t:
        raise HTTPException(404, '表不存在')
    me = _actor(payload)
    ex("UPDATE mt_row SET deleted=1 WHERE table_id=%s", (table_id,))
    ex("UPDATE mt_table SET deleted=1 WHERE id=%s", (table_id,))
    try:
        store.drop_view(table_id)   # 视图随表一起删（best-effort，失败不影响删表）
    except Exception:
        pass
    audit(table_id, None, 'delete_table', me, {'name': t['name']})
    return {'ok': True}


# ==================== 列（仅 admin） ====================

@router.post('/tables/{table_id}/columns')
def add_column(table_id: int, req: ColumnReq, payload: dict = Depends(require_admin)):
    _need(table_id, payload)
    label = (req.label or '').strip()
    if not label:
        raise HTTPException(400, '列名不能为空')
    t = req.type or 'text'
    if t not in COL_TYPES:
        raise HTTPException(400, f'未知列类型 {t}')
    existing = {c['key'] for c in _columns(table_id)}
    key = _slug(req.key or label)
    base = key
    n = 2
    while key in existing:
        key = f'{base}_{n}'
        n += 1
    if req.sort is None:
        mx = q("SELECT COALESCE(MAX(sort),0) m FROM mt_column WHERE table_id=%s", (table_id,), one=True)['m']
        sort = mx + 1
    else:
        sort = req.sort
    ex("INSERT INTO mt_column (table_id, `key`, label, type, options, required, width, sort) "
       "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
       (table_id, key, label, t, jdump(req.options), 1 if req.required else 0, req.width or 0, sort))
    audit(table_id, None, 'create_column', _actor(payload), {'label': label, 'type': t})
    _sync_view(table_id)
    return {'ok': True, 'key': key}


@router.patch('/tables/{table_id}/columns/{col_id}')
def update_column(table_id: int, col_id: int, req: ColumnReq, payload: dict = Depends(require_admin)):
    _need(table_id, payload)
    col = q("SELECT * FROM mt_column WHERE id=%s AND table_id=%s", (col_id, table_id), one=True)
    if not col:
        raise HTTPException(404, '列不存在')
    sets, args, detail = [], [], {}
    if req.label and req.label.strip() != col['label']:
        sets.append('label=%s'); args.append(req.label.strip())
        detail['label'] = {'old': col['label'], 'new': req.label.strip()}
    if req.type and req.type != col['type']:
        if req.type not in COL_TYPES:
            raise HTTPException(400, f'未知列类型 {req.type}')
        sets.append('type=%s'); args.append(req.type)
        detail['type'] = {'old': col['type'], 'new': req.type}
    if req.options is not None:
        sets.append('options=%s'); args.append(jdump(req.options))
        detail['options'] = {'old': col['options'], 'new': req.options}
    sets.append('required=%s'); args.append(1 if req.required else 0)
    if req.width is not None:
        sets.append('width=%s'); args.append(req.width)
    if req.sort is not None:
        sets.append('sort=%s'); args.append(req.sort)
    args += [col_id, table_id]
    ex(f"UPDATE mt_column SET {', '.join(sets)} WHERE id=%s AND table_id=%s", tuple(args))
    audit(table_id, None, 'update_column', _actor(payload), {'col': col['label'], **detail})
    _sync_view(table_id)
    return {'ok': True}


@router.delete('/tables/{table_id}/columns/{col_id}')
def delete_column(table_id: int, col_id: int, payload: dict = Depends(require_admin)):
    """删列（仅 admin）。同时把各行 data 里该键清掉。"""
    _need(table_id, payload)
    col = q("SELECT `key`, label FROM mt_column WHERE id=%s AND table_id=%s", (col_id, table_id), one=True)
    if not col:
        raise HTTPException(404, '列不存在')
    ex("DELETE FROM mt_column WHERE id=%s AND table_id=%s", (col_id, table_id))
    rows = q("SELECT id, data FROM mt_row WHERE table_id=%s AND deleted=0", (table_id,))
    for r in rows:
        d = jload(r['data'])
        if col['key'] in d:
            d.pop(col['key'])
            ex("UPDATE mt_row SET data=%s, ver=ver+1 WHERE id=%s", (jdump(d), r['id']))
    audit(table_id, None, 'delete_column', _actor(payload), {'label': col['label']})
    _sync_view(table_id)
    return {'ok': True}


# ==================== 行（游客可看；登录用户可改） ====================

HEAVY_ROWS = 100000   # 行数阈值：到量前端亮黄灯提醒，届时做一次性实体表迁移

_ROW_COLS = 'id, data, ver, sort, created_by, updated_by, created_at, updated_at'


@router.get('/tables/{table_id}/rows')
def list_rows(table_id: int, page: int = Query(1, ge=1), size: int = Query(100, ge=1, le=1000),
              q_text: str = Query('', alias='q'), payload: dict = Depends(verify_token)):
    """行列表（游客可看）。搜索：≥2 字符走 ngram 全文索引（短语模式）；
    单字符 ngram 分不出词，回退 LIKE 全扫（行少时无所谓）。"""
    _need(table_id, payload)
    offset = (page - 1) * size
    kw = (q_text or '').strip()
    if kw:
        if len(kw) >= 2:
            phrase = '"' + kw.replace('"', '').replace('*', '') + '"'
            cond, arg = ' AND MATCH(data_text) AGAINST (%s IN BOOLEAN MODE)', phrase
        else:
            cond, arg = ' AND data LIKE %s', f'%{kw}%'
        total = q(f"SELECT COUNT(*) c FROM mt_row WHERE table_id=%s AND deleted=0{cond}",
                  (table_id, arg), one=True)['c']
        rows = q(f"SELECT {_ROW_COLS} FROM mt_row WHERE table_id=%s AND deleted=0{cond} "
                 "ORDER BY sort, id LIMIT %s OFFSET %s", (table_id, arg, size, offset))
    else:
        total = q("SELECT COUNT(*) c FROM mt_row WHERE table_id=%s AND deleted=0", (table_id,), one=True)['c']
        rows = q(f"SELECT {_ROW_COLS} FROM mt_row WHERE table_id=%s AND deleted=0 "
                 "ORDER BY sort, id LIMIT %s OFFSET %s", (table_id, size, offset))
    return {'total': total, 'page': page, 'size': size,
            'rows': [{'id': r['id'], 'data': jload(r['data']), 'ver': r['ver'],
                      'created_by': r['created_by'], 'updated_by': r['updated_by'],
                      'created_at': _ts(r['created_at']), 'updated_at': _ts(r['updated_at'])} for r in rows]}


@router.post('/tables/{table_id}/rows')
def create_row(table_id: int, req: RowReq, payload: dict = Depends(require_login)):
    _need(table_id, payload, write=True)
    cols = _columns(table_id)
    data = _validate_row(cols, req.data, partial=False)
    me = _actor(payload)
    nxt = q("SELECT COALESCE(MAX(sort),0) m FROM mt_row WHERE table_id=%s", (table_id,), one=True)['m']
    rid = ex("INSERT INTO mt_row (table_id, data, sort, created_by, updated_by) VALUES (%s,%s,%s,%s,%s)",
             (table_id, jdump(data), nxt + 1, me, me))
    audit(table_id, rid, 'create_row', me, {k: {'old': None, 'new': v} for k, v in data.items()})
    return {'id': rid, 'ver': 1, 'data': data}


@router.patch('/tables/{table_id}/rows/{row_id}')
def update_row(table_id: int, row_id: int, req: RowReq, payload: dict = Depends(require_login)):
    """改行。必须带 ver（客户端上次读到的版本），不匹配 → 409 乐观锁冲突。"""
    _need(table_id, payload, write=True)
    cur = q(f"SELECT {_ROW_COLS} FROM mt_row WHERE id=%s AND table_id=%s AND deleted=0", (row_id, table_id), one=True)
    if not cur:
        raise HTTPException(404, '行不存在')
    if req.ver is None:
        raise HTTPException(400, '缺少 ver（乐观锁版本号）')
    if int(req.ver) != int(cur['ver']):
        raise HTTPException(409, f"这行已被别人改过（你手里的版本 {req.ver}，库里 {cur['ver']}），请刷新后重试")
    cols = _columns(table_id)
    patch = _validate_row(cols, req.data, partial=True)
    old = jload(cur['data'])
    # 必填校验：合并后的完整行
    merged = {**old, **patch}
    for c in cols:
        if c['required'] and not merged.get(c['key']):
            raise HTTPException(400, f"列「{c['label']}」必填")
    detail = {k: {'old': old.get(k), 'new': v} for k, v in patch.items() if old.get(k) != v}
    me = _actor(payload)
    ex("UPDATE mt_row SET data=%s, ver=ver+1, updated_by=%s WHERE id=%s AND table_id=%s AND ver=%s",
       (jdump(merged), me, row_id, table_id, req.ver))
    # 复查：并发下 UPDATE 可能 0 行
    after = q("SELECT ver FROM mt_row WHERE id=%s", (row_id,), one=True)
    if after is None or int(after['ver']) != int(req.ver) + 1:
        raise HTTPException(409, '这行在你保存的瞬间被改动，请刷新后重试')
    if detail:
        audit(table_id, row_id, 'update_row', me, detail)
    return {'ok': True, 'ver': after['ver'], 'data': merged}


@router.delete('/tables/{table_id}/rows/{row_id}')
def delete_row(table_id: int, row_id: int, payload: dict = Depends(require_login)):
    """删行（软删）。"""
    _need(table_id, payload, write=True)
    cur = q("SELECT data FROM mt_row WHERE id=%s AND table_id=%s AND deleted=0", (row_id, table_id), one=True)
    if not cur:
        raise HTTPException(404, '行不存在')
    ex("UPDATE mt_row SET deleted=1 WHERE id=%s AND table_id=%s", (row_id, table_id))
    audit(table_id, row_id, 'delete_row', _actor(payload), {'data': jload(cur['data'])})
    return {'ok': True}


# ==================== 用户管理（仅 admin；添加的用户即可改表） ====================

_USERNAME_RE = re.compile(r'^[A-Za-z0-9_.@\-\u4e00-\u9fa5]{2,64}$')


@router.get('/users')
def list_users_ep(payload: dict = Depends(require_admin)):
    """用户列表（仅 admin）。"""
    return {'rows': [{**r, 'created_at': _ts(r['created_at']), 'updated_at': _ts(r['updated_at'])}
                     for r in store.list_users()]}


@router.post('/users')
def create_user_ep(req: UserCreateReq, payload: dict = Depends(require_admin)):
    """添加用户（仅 admin）。新用户 role=staff：全部表可编辑、可看变动历史。"""
    u = (req.username or '').strip()
    if not _USERNAME_RE.fullmatch(u):
        raise HTTPException(400, '账号需 2~64 位，可用字母/数字/中文/._@-')
    if len(req.password or '') < 6:
        raise HTTPException(400, '密码至少 6 位')
    if store.get_user(username=u):
        raise HTTPException(400, f'账号「{u}」已存在')
    me = _actor(payload)
    uid = ex("INSERT INTO mt_user (username, password_hash, real_name, role, status, created_by) "
             "VALUES (%s,%s,%s,'staff',1,%s)",
             (u, store.hash_password(req.password), (req.real_name or '').strip()[:64], me))
    audit(0, None, 'user_create', me, {'username': u, 'role': 'staff'})
    return {'ok': True, 'id': uid, 'username': u}


@router.patch('/users/{uid}')
def patch_user_ep(uid: int, req: UserPatchReq, payload: dict = Depends(require_admin)):
    """改用户（仅 admin）：重置密码 / 改姓名 / 停用启用。改密/停用会立刻踢掉该用户会话。"""
    acct = store.get_user(uid=uid)
    if not acct:
        raise HTTPException(404, '用户不存在')
    me = _actor(payload)
    sets, args, detail = [], [], {}
    if req.password is not None:
        if len(req.password) < 6:
            raise HTTPException(400, '密码至少 6 位')
        sets.append('password_hash=%s'); args.append(store.hash_password(req.password))
        detail['password'] = '已重置'
    if req.real_name is not None:
        sets.append('real_name=%s'); args.append((req.real_name or '').strip()[:64])
        detail['real_name'] = {'old': acct['real_name'], 'new': (req.real_name or '').strip()[:64]}
    if req.status is not None:
        if int(req.status) not in (0, 1):
            raise HTTPException(400, 'status 只能 0/1')
        if acct['username'] == me and int(req.status) == 0:
            raise HTTPException(400, '不能停用自己')
        sets.append('status=%s'); args.append(int(req.status))
        detail['status'] = {'old': acct['status'], 'new': int(req.status)}
    if not sets:
        raise HTTPException(400, '没有要改的内容')
    args.append(uid)
    ex(f"UPDATE mt_user SET {', '.join(sets)} WHERE id=%s", tuple(args))
    if 'password' in detail or 'status' in detail:
        from ..auth_sessions import revoke_user_sessions
        revoke_user_sessions(uid)
    audit(0, None, 'user_update', me, {'username': acct['username'], **detail})
    return {'ok': True}


@router.delete('/users/{uid}')
def delete_user_ep(uid: int, payload: dict = Depends(require_admin)):
    """删除用户（仅 admin）。不能删自己。"""
    acct = store.get_user(uid=uid)
    if not acct:
        raise HTTPException(404, '用户不存在')
    me = _actor(payload)
    if acct['username'] == me:
        raise HTTPException(400, '不能删除自己')
    ex("DELETE FROM mt_user WHERE id=%s", (uid,))
    from ..auth_sessions import revoke_user_sessions
    revoke_user_sessions(uid)
    audit(0, None, 'user_delete', me, {'username': acct['username']})
    return {'ok': True}


# ==================== HTTP 写入密钥（免登录插数据） ====================

def _key_auth(table_id: int, token: str) -> dict:
    """校验 X-MT-Token：存在、未作废、且属于这张表。"""
    row = store.apikey_lookup((token or '').strip())
    if not row or row['revoked']:
        raise HTTPException(401, '密钥无效或已作废')
    if int(row['table_id']) != int(table_id):
        raise HTTPException(401, '密钥不属于这张表')
    return row


@router.get('/tables/{table_id}/apikeys')
def apikey_list_ep(table_id: int, payload: dict = Depends(require_admin)):
    _need(table_id, payload)
    return {'rows': [{**r, 'created_at': _ts(r['created_at']), 'revoked': bool(r['revoked'])}
                     for r in store.apikey_list(table_id)]}


@router.post('/tables/{table_id}/apikeys')
def apikey_create_ep(table_id: int, req: ApiKeyReq, payload: dict = Depends(require_admin)):
    """生成密钥（仅 admin）。明文只在本次响应里出现一次，库里只存 sha256。"""
    t = q("SELECT name FROM mt_table WHERE id=%s AND deleted=0", (table_id,), one=True)
    if not t:
        raise HTTPException(404, '表不存在或已删除')
    plain = store.apikey_create(table_id, req.note, _actor(payload))
    audit(table_id, None, 'apikey_create', _actor(payload), {'note': (req.note or '').strip()[:120]})
    return {'key': plain}


@router.delete('/tables/{table_id}/apikeys/{kid}')
def apikey_revoke_ep(table_id: int, kid: int, payload: dict = Depends(require_admin)):
    r = q("SELECT id, note FROM mt_apikey WHERE id=%s AND table_id=%s", (kid, table_id), one=True)
    if not r:
        raise HTTPException(404, '密钥不存在')
    ex("UPDATE mt_apikey SET revoked=1 WHERE id=%s AND table_id=%s", (kid, table_id))
    audit(table_id, None, 'apikey_revoke', _actor(payload), {'id': kid, 'note': r['note']})
    return {'ok': True}


@router.post('/in/{table_id}')
def api_insert(table_id: int, req: ApiRowReq, x_mt_token: str = Header(default='')):
    """免登录写入（影刀/脚本用）。头 `X-MT-Token: <密钥>`。

    body 二选一：{"data":{列key:值}} 单行；{"rows":[{...},...]} 批量（≤1000）。
    校验同手动插行（列类型/必填），审计一条 api_insert。
    """
    key = _key_auth(table_id, x_mt_token)
    t = q("SELECT id FROM mt_table WHERE id=%s AND deleted=0", (table_id,), one=True)
    if not t:
        raise HTTPException(404, '表不存在或已删除')
    cols = _columns(table_id)
    if not cols:
        raise HTTPException(400, '这张表还没有列，先在界面加列')
    items = req.rows if req.rows is not None else ([req.data] if req.data is not None else [])
    if not items:
        raise HTTPException(400, 'body 需要 {"data":{...}} 或 {"rows":[...]}')
    if len(items) > 1000:
        raise HTTPException(400, '单次最多 1000 行')
    clean_rows = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise HTTPException(400, f'第 {i + 1} 行不是对象')
        try:
            clean_rows.append(_validate_row(cols, item, partial=False))
        except HTTPException as e:
            raise HTTPException(400, f'第 {i + 1} 行：{e.detail}')
    me = 'api:' + (key['note'] or f"key#{key['id']}")
    nxt = q("SELECT COALESCE(MAX(sort),0) m FROM mt_row WHERE table_id=%s", (table_id,), one=True)['m']
    first_id = None
    for start in range(0, len(clean_rows), 500):
        chunk = clean_rows[start:start + 500]
        sql = ("INSERT INTO mt_row (table_id, data, sort, created_by, updated_by) VALUES "
               + ','.join(['(%s,%s,%s,%s,%s)'] * len(chunk)))
        args = []
        for k, d in enumerate(chunk):
            args += [table_id, jdump(d), nxt + start + k + 1, me, me]
        rid = ex(sql, tuple(args))
        if first_id is None:
            first_id = rid
    # multi-VALUES 语句的 auto_increment 是整段连续的，first_id 起顺推即各行 id
    ids = list(range(int(first_id), int(first_id) + len(clean_rows)))
    detail = {'count': len(clean_rows)}
    if len(clean_rows) == 1:
        detail['data'] = clean_rows[0]
    audit(table_id, None, 'api_insert', me, detail)
    return {'ok': True, 'inserted': len(clean_rows), 'ids': ids}


# ==================== 数据库视图（镜像到业务库可 SQL 查询，仅 admin） ====================

@router.get('/tables/{table_id}/dbview')
def dbview_status(table_id: int, payload: dict = Depends(require_admin)):
    _need(table_id, payload)
    cols = _columns(table_id)
    return {'exists': store.view_exists(table_id), 'name': store.view_name(table_id),
            'columns': [{'label': c['label'], 'type': c['type']} for c in cols]}


@router.post('/tables/{table_id}/dbview')
def dbview_create(table_id: int, payload: dict = Depends(require_admin)):
    """生成/重建实时视图（仅 admin）。视图永远同步，无需刷新；列变更时自动重建。"""
    _need(table_id, payload)
    cols = _columns(table_id)
    if not cols:
        raise HTTPException(400, '这张表还没有列，先加列再生成视图')
    name = store.view_name(table_id)
    try:
        store.rebuild_view(table_id, cols)
    except Exception as e:
        raise HTTPException(500, f'数据库拒绝建视图（检查 {store.DB_NAME} 账号是否有 CREATE VIEW / DROP 权限）：{e}')
    audit(table_id, None, 'dbview_create', _actor(payload), {'name': name})
    return {'ok': True, 'name': name}


@router.delete('/tables/{table_id}/dbview')
def dbview_drop(table_id: int, payload: dict = Depends(require_admin)):
    _need(table_id, payload)
    store.drop_view(table_id)
    audit(table_id, None, 'dbview_drop', _actor(payload), {'name': store.view_name(table_id)})
    return {'ok': True}


# ==================== 变动历史（登录用户可看） ====================

@router.get('/tables/{table_id}/audit')
def audit_list(table_id: int, limit: int = Query(100, ge=1, le=500),
               payload: dict = Depends(require_login)):
    """变动历史（登录用户可看）：谁、何时、哪张表哪行、哪个字段 旧值→新值。"""
    _need(table_id, payload)
    rows = q("SELECT id, row_id, action, actor, detail, created_at FROM mt_audit "
             "WHERE table_id=%s ORDER BY id DESC LIMIT %s", (table_id, limit))
    out = []
    for r in rows:
        d = r['detail']
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except Exception:
                pass
        out.append({**r, 'detail': d, 'created_at': _ts(r['created_at'])})
    return {'rows': out}


@router.get('/meta')
def meta(payload: dict = Depends(verify_token)):
    """前端用：我的身份 + 列类型清单。游客也可访问（logged_in=false）。"""
    if not payload:
        return {'user': '', 'role': 'guest', 'is_admin': False, 'logged_in': False,
                'col_types': list(COL_TYPES)}
    return {'user': _actor(payload), 'role': payload.get('role') or 'staff',
            'is_admin': _is_admin(payload), 'logged_in': True, 'col_types': list(COL_TYPES)}


# ==================== 图片上传（image 列用，登录用户） ====================

_MT_FILES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'mt_files')
_MT_IMG_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
_MT_IMG_MAX = 10 * 1024 * 1024


@router.post('/upload')
async def mt_upload(file: UploadFile = File(...), payload: dict = Depends(require_login)):
    """图片上传（image 列用）：jpg/png/webp/gif ≤10MB，随机文件名，返回访问 URL。"""
    ext = os.path.splitext(file.filename or '')[1].lower()
    if ext not in _MT_IMG_EXT:
        raise HTTPException(400, f'不支持的图片格式 {ext}，仅支持 jpg/png/webp/gif')
    data = await file.read()
    if len(data) > _MT_IMG_MAX:
        raise HTTPException(400, '图片超过 10MB 上限')
    os.makedirs(_MT_FILES_DIR, exist_ok=True)
    fname = uuid.uuid4().hex + ext
    fpath = os.path.join(_MT_FILES_DIR, fname)
    with open(fpath, 'wb') as f:
        f.write(data)
    return {'ok': True, 'url': f'/api/mtables/files/{fname}'}


@router.get('/files/{fname}')
def mt_file(fname: str):
    """图片直显（文件名 32 位随机不可猜，公开可 <img> 引用；仅白名单扩展名）。"""
    if not re.fullmatch(r'[0-9a-f]{32}' + r'\.(jpg|jpeg|png|webp|gif)', fname):
        raise HTTPException(404, '不存在')
    fpath = os.path.join(_MT_FILES_DIR, fname)
    if not os.path.isfile(fpath):
        raise HTTPException(404, '不存在')
    mt = mimetypes.guess_type(fpath)[0] or 'application/octet-stream'
    return FileResponse(fpath, media_type=mt)
