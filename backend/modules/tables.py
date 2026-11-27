# -*- coding: utf-8 -*-
"""表：建/改/删/改名"""
from backend.deps import *          # noqa: F401,F403
from fastapi import APIRouter       # noqa: F401

router = APIRouter()

class TableReq(BaseModel):
    name: str = ''
    note: str = ''
    project: str = ''
    group_name: str = ''
    icon: str = '📄'
    columns: list | None = None     # [{key?,label,type?,options?,expr?,required?,width?}]

@router.post("/api/tables")
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

@router.patch("/api/tables/{tid}")
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

@router.delete("/api/tables/{tid}")
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

@router.patch("/api/tables/{tid}/rename")
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
