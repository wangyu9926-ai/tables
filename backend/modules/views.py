# -*- coding: utf-8 -*-
"""保存的视图：个人视图 + 团队共享视图（表 kb_view）。"""
from backend.deps import *          # noqa: F401,F403
from fastapi import APIRouter       # noqa: F401

router = APIRouter()

class ViewReq(BaseModel):
    name: str = ''
    config: dict | None = None
    shared: bool = False

@router.get("/api/tables/{tid}/views")
def list_views(tid: int):
    """这张表的视图：自己的 + 所有人共享的团队视图。"""
    t = _need(tid)
    need_table(tid, t.get('project') or '')
    uid = _uid()
    rows = q("SELECT id,name,owner_id,owner_name,shared,config,"
             "DATE_FORMAT(updated_at,'%%Y-%%m-%%d %%H:%%i') AS updated_at FROM kb_view "
             "WHERE table_id=%s AND (owner_id=%s OR shared=1) "
             "ORDER BY shared DESC, updated_at DESC, id DESC", (tid, uid))
    out = []
    for r in rows:
        cfg = jload(r['config'])
        out.append({'id': r['id'], 'name': r['name'], 'owner': r['owner_name'],
                    'mine': int(r['owner_id']) == int(uid), 'shared': bool(r['shared']),
                    'config': cfg if isinstance(cfg, dict) else {}, 'updated_at': r['updated_at']})
    return {'views': out, 'can_share': table_role(tid, t.get('project') or '') == 'editor'}

@router.post("/api/tables/{tid}/views")
def save_view(tid: int, req: ViewReq):
    """保存视图：同名覆盖。shared=1 是团队视图（本表所有人可见），需要这张表的编辑权限。"""
    t = _need(tid)
    need_table(tid, t.get('project') or '')
    name = (req.name or '').strip()
    if not name:
        raise HTTPException(400, '视图名不能为空')
    _check_len(name, 40, '视图名')
    cfg = req.config if isinstance(req.config, dict) else {}
    shared = 1 if req.shared else 0
    if shared:
        need_table(tid, t.get('project') or '', write=True)
    uid = _uid()
    uname = ((AUTH_USER.get() or {}).get('username') or 'local')[:64]
    ex("INSERT INTO kb_view(table_id,name,owner_id,owner_name,shared,config) VALUES(%s,%s,%s,%s,%s,%s) "
       "ON DUPLICATE KEY UPDATE shared=VALUES(shared), config=VALUES(config), owner_name=VALUES(owner_name)",
       (tid, name, uid, uname, shared, jdump(cfg)))
    v = q("SELECT id FROM kb_view WHERE table_id=%s AND owner_id=%s AND name=%s", (tid, uid, name), one=True)
    return {'ok': True, 'id': v['id'] if v else None}

@router.delete("/api/views/{vid}")
def delete_view(vid: int):
    r = q("SELECT id,table_id,owner_id,owner_name FROM kb_view WHERE id=%s", (vid,), one=True)
    if not r:
        raise HTTPException(404, '视图不存在')
    t = _need(r['table_id'])
    need_table(r['table_id'], t.get('project') or '')
    u = AUTH_USER.get() or {}
    if int(r['owner_id']) != _uid() and u.get('role') != 'admin':
        raise HTTPException(403, '只能删除自己保存的视图')
    ex("DELETE FROM kb_view WHERE id=%s", (vid,))
    return {'ok': True}
