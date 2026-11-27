# -*- coding: utf-8 -*-
"""项目与分组：建/改名/删、组与 Sheet 排序"""
from backend.deps import *          # noqa: F401,F403
from fastapi import APIRouter       # noqa: F401

router = APIRouter()

class GroupOrderReq(BaseModel):
    project: str = ''
    groups: list

class SheetOrderReq(BaseModel):
    tids: list

@router.post("/api/groups/sort")
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

@router.post("/api/groups/{group}/sort")
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

@router.patch("/api/groups/rename")
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

class ProjectReq(BaseModel):
    name: str
    icon: str = '📁'

@router.get("/api/projects")
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

@router.post("/api/projects")
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

@router.patch("/api/projects/{name}")
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

@router.delete("/api/projects/{name}")
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
