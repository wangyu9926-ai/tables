# -*- coding: utf-8 -*-
"""HTTP 写入密钥：生成 / 列表 / 作废"""
from backend.deps import *          # noqa: F401,F403
from fastapi import APIRouter       # noqa: F401

router = APIRouter()

class ApiKeyReq(BaseModel):
    note: str = ''

@router.get("/api/tables/{tid}/apikeys")
def apikey_list(tid: int):
    _need_admin()
    _need(tid)
    rows = q("SELECT id,key_prefix,note,created_by,revoked,"
             "DATE_FORMAT(created_at,'%%Y-%%m-%%d %%H:%%i') AS created_at "
             "FROM kb_apikey WHERE table_id=%s ORDER BY id DESC", (tid,))
    return {'rows': rows}

@router.post("/api/tables/{tid}/apikeys")
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

@router.delete("/api/tables/{tid}/apikeys/{kid}")
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
