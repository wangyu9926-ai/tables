# -*- coding: utf-8 -*-
"""汇总同步规则：增删改查 + 立即同步"""
from backend.deps import *          # noqa: F401,F403
from fastapi import APIRouter       # noqa: F401

router = APIRouter()

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

@router.get("/api/sync")
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

@router.post("/api/sync")
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

@router.patch("/api/sync/{sid}")
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

@router.post("/api/sync/{sid}/run")
def run_sync(sid: int):
    _need_admin()
    r = q("SELECT id,enabled FROM kb_sync WHERE id=%s", (sid,), one=True)
    if not r:
        raise HTTPException(404, '汇总规则不存在')
    if not r['enabled']:
        raise HTTPException(400, '汇总规则已停用')
    return {'ok': True, 'job': kb_sync.start_full(sid)}

@router.delete("/api/sync/{sid}")
def delete_sync(sid: int):
    _need_admin()
    if not kb_sync.delete_config(sid, purge=True):
        raise HTTPException(404, '汇总规则不存在')
    return {'ok': True}
