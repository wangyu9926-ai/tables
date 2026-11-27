# -*- coding: utf-8 -*-
"""数据分析看板"""
from backend.deps import *          # noqa: F401,F403
from fastapi import APIRouter       # noqa: F401

router = APIRouter()

class DashboardReq(BaseModel):
    project: str
    name: str = '默认看板'
    config: dict | None = None
    is_page: bool = False

class DashboardPatch(BaseModel):
    name: str | None = None
    config: dict | None = None
    is_page: bool | None = None

@router.get("/api/dashboards")
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

@router.post("/api/dashboards")
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

@router.patch("/api/dashboards/{did}")
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

@router.delete("/api/dashboards/{did}")
def delete_dashboard(did: int):
    r = q("SELECT * FROM kb_dashboard WHERE id=%s AND deleted=0", (did,), one=True)
    if not r:
        raise HTTPException(404, '看板不存在')
    need_project(r['project'], write=True)
    ex("UPDATE kb_dashboard SET deleted=1 WHERE id=%s", (did,))
    return {'ok': True}
