# -*- coding: utf-8 -*-
"""公式试算 / 变动历史 / 关联（保留接口）"""
from backend.deps import *          # noqa: F401,F403
from fastapi import APIRouter       # noqa: F401

router = APIRouter()

class ExprReq(BaseModel):
    expr: str
    row: dict = {}
    tid: int | None = None

@router.post("/api/formula/eval")
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

@router.get("/api/tables/{tid}/audit")
def history(tid: int, limit: int = 100):
    t = _need(tid)
    need_table(tid, t.get('project') or '')
    flush_audit()          # 审计是后台批量写，读历史前先落盘
    limit = max(0, min(int(limit), 1000))      # 负数 / 超大值会被数据库当成语法或范围错误
    rows = q("SELECT id,row_id,action,actor,detail,"
             "DATE_FORMAT(at,'%%Y-%%m-%%d %%H:%%i:%%s') AS at "
             "FROM kb_audit WHERE table_id=%s ORDER BY id DESC LIMIT %s", (tid, limit))
    return {'rows': rows}

@router.get("/api/related")
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
