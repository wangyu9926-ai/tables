# -*- coding: utf-8 -*-
"""单元格批注：列表/新增/删除（只读账号也能写）"""
from backend.deps import *          # noqa: F401,F403
from fastapi import APIRouter       # noqa: F401

router = APIRouter()

class CommentReq(BaseModel):
    row_id: int
    col_key: str
    body: str

@router.get("/api/tables/{tid}/comments/summary")
def comments_summary(tid: int):
    t = _need(tid)
    need_table(tid, t.get('project') or '')
    rows = q("SELECT row_id,col_key,COUNT(*) n FROM kb_comment WHERE table_id=%s AND deleted=0 "
             "GROUP BY row_id,col_key", (tid,))
    return _json_response({'items': [{'row_id': r['row_id'], 'col_key': r['col_key'], 'n': int(r['n'])} for r in rows]})

@router.get("/api/tables/{tid}/comments")
def comments_list(tid: int, row_id: int = 0, col_key: str = ''):
    t = _need(tid)
    need_table(tid, t.get('project') or '')
    if not row_id or not col_key:
        raise HTTPException(400, '需要指定 row_id 和 col_key')
    rows = q("SELECT id,body,author_id,author_name,created_at FROM kb_comment "
             "WHERE table_id=%s AND row_id=%s AND col_key=%s AND deleted=0 ORDER BY id", (tid, row_id, col_key))
    uid, _ = _commenter()
    return _json_response({'comments': [{'id': r['id'], 'body': r['body'], 'author_id': r['author_id'],
                                         'author': r['author_name'] or ('用户%d' % r['author_id']),
                                         'mine': int(r['author_id']) == uid,
                                         'at': _ts(r['created_at'])} for r in rows]})

@router.post("/api/tables/{tid}/comments")
def comment_add(tid: int, req: CommentReq):
    """批注不是改数据：有这张表的读权限就能写（只读账号也可以），删只能删自己的（管理员可以删任意）。"""
    t = _need(tid)
    need_table(tid, t.get('project') or '')
    body = (req.body or '').strip()
    if not body:
        raise HTTPException(400, '批注内容不能为空')
    if len(body) > 2000:
        raise HTTPException(400, '批注不能超过 2000 字')
    cols = {c['key'] for c in _table_columns(tid, include_hidden=True)}
    if req.col_key not in cols:
        raise HTTPException(404, '列不存在')
    row = q("SELECT id FROM kb_row WHERE id=%s AND table_id=%s AND deleted=0", (req.row_id, tid), one=True)
    if not row:
        raise HTTPException(404, '行不存在')
    uid, uname = _commenter()
    con = conn()
    try:
        cur = con.cursor()
        cur.execute("INSERT INTO kb_comment(table_id,row_id,col_key,body,author_id,author_name) "
                    "VALUES(%s,%s,%s,%s,%s,%s)", (tid, req.row_id, req.col_key, body, uid, uname))
        cid = cur.lastrowid
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()
    kb_changes.bump(tid, 'comment', [req.row_id], uname)
    return _json_response({'ok': True, 'id': cid,
                           'comment': {'id': cid, 'body': body, 'author_id': uid, 'author': uname,
                                       'mine': True, 'at': _ts(datetime.now())}})

@router.delete("/api/comments/{cid}")
def comment_del(cid: int):
    r = q("SELECT id,table_id,row_id,author_id FROM kb_comment WHERE id=%s AND deleted=0", (cid,), one=True)
    if not r:
        raise HTTPException(404, '批注不存在')
    t = _need(r['table_id'])
    need_table(r['table_id'], t.get('project') or '')
    uid, _ = _commenter()
    if int(r['author_id']) != uid and (AUTH_USER.get() or {}).get('role') != 'admin':
        raise HTTPException(403, '只能删除自己的批注')
    ex("UPDATE kb_comment SET deleted=1 WHERE id=%s", (cid,))
    kb_changes.bump(r['table_id'], 'comment', [r['row_id']], ((AUTH_USER.get() or {}).get('username') or ''))
    return {'ok': True}
