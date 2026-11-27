# -*- coding: utf-8 -*-
"""列（字段）：加/改/删/排序"""
from backend.deps import *          # noqa: F401,F403
from fastapi import APIRouter       # noqa: F401

router = APIRouter()

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

@router.post("/api/tables/{tid}/columns")
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

@router.patch("/api/tables/{tid}/columns/{cid}")
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

@router.delete("/api/tables/{tid}/columns/{cid}")
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

@router.post("/api/tables/{tid}/columns/sort")
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
