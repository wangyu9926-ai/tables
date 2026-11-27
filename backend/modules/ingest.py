# -*- coding: utf-8 -*-
"""免登录写入接口 /api/in/{tid}（表级密钥 + 幂等键）"""
from backend.deps import *          # noqa: F401,F403
from fastapi import APIRouter       # noqa: F401

router = APIRouter()

class ApiInsertReq(BaseModel):
    data: dict | None = None
    rows: list | None = None

@router.post("/api/in/{tid}")
def api_insert(request: Request, tid: int, req: ApiInsertReq, x_kb_token: str = Header(default=''),
               x_idempotency_key: str = Header(default='')):
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
    # 幂等键：脚本重试（超时、网络抖动）不会重复插入——同一个键只写一次，重试直接返回上次的结果
    idem = (x_idempotency_key or '').strip()[:120]
    if idem:
        prev = q("SELECT result FROM kb_idem WHERE table_id=%s AND idem_key=%s", (tid, idem), one=True)
        if prev:
            out = jload(prev['result'])
            out['replayed'] = True
            return _json_response(out)
        try:
            ex("INSERT INTO kb_idem(table_id,idem_key,result) VALUES(%s,%s,'{}')", (tid, idem))
        except pymysql.err.IntegrityError:
            prev = q("SELECT result FROM kb_idem WHERE table_id=%s AND idem_key=%s", (tid, idem), one=True)
            out = jload(prev['result']) if prev else {}
            out['replayed'] = True
            return _json_response(out)
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
    result = {'ok': True, 'inserted': len(clean_rows), 'ids': ids}
    if idem:
        try:
            ex("UPDATE kb_idem SET result=%s WHERE table_id=%s AND idem_key=%s",
               (jdump(result), tid, idem))
            ex("DELETE FROM kb_idem WHERE at < NOW() - INTERVAL 7 DAY")     # 顺手清理过期的
        except Exception as e:
            print(f'[db] 写入幂等结果失败：{e!r}', flush=True)
    return result
