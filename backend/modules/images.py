# -*- coding: utf-8 -*-
"""图片：回收站、上传、读取"""
from backend.deps import *          # noqa: F401,F403
from fastapi import APIRouter       # noqa: F401

router = APIRouter()

class ImageTrashReq(BaseModel):
    ids: list
    action: str          # purge=彻底删除 / restore=恢复

@router.get("/api/images/trash")
def image_trash():
    _need_admin()
    rows = q("SELECT i.id, i.table_id, i.mime, i.size, i.deleted_at, "
             "COALESCE(t.name, '（表已删除）') AS table_name "
             "FROM kb_image i LEFT JOIN kb_table t ON t.id = i.table_id "
             "WHERE i.deleted=1 ORDER BY i.deleted_at DESC, i.id DESC LIMIT 500")
    return {'rows': rows}

@router.post("/api/images/trash/act")
def image_trash_act(req: ImageTrashReq):
    _need_admin()
    ids = [int(x) for x in (req.ids or []) if str(x).strip().isdigit()]
    if not ids:
        raise HTTPException(400, '没有选择图片')
    ph = ','.join(['%s'] * len(ids))
    if req.action == 'purge':
        ex(f"DELETE FROM kb_image WHERE id IN ({ph}) AND deleted=1", tuple(ids))
    elif req.action == 'restore':
        ex(f"UPDATE kb_image SET deleted=0, deleted_at=NULL WHERE id IN ({ph}) AND deleted=1", tuple(ids))
    else:
        raise HTTPException(400, 'action 无效')
    return {'ok': True, 'action': req.action}

@router.post("/api/upload/image")
def upload_image(file: UploadFile = File(...), tid: int = Form(...)):
    """同步函数：FastAPI 会把它放进线程池，写库（远程库一次往返）不占主线程。"""
    t = _need(tid)
    need_table(tid, t.get('project') or '', write=True)
    # 最多读到「上限 + 1」字节就停：超限的文件不用整个读进内存
    raw = file.file.read(_IMG_MAX + 1)
    if not raw:
        raise HTTPException(400, '图片为空')
    if len(raw) > _IMG_MAX:
        raise HTTPException(413, '图片不能超过 8MB')
    mime = _image_kind(raw)
    if not mime:
        raise HTTPException(400, '只支持 png / jpg / gif / webp / bmp')
    used = int(q("SELECT COALESCE(SUM(size),0) s FROM kb_image WHERE table_id=%s AND deleted=0",
                 (tid,), one=True)['s'] or 0)
    if used + len(raw) > _IMG_TABLE_MAX:
        raise HTTPException(413, f'这张表的图片已用 {used // 1048576}MB，上限 {_IMG_TABLE_MAX // 1048576}MB'
                                 f'（KB_IMAGE_TABLE_MB 可调）；先把回收站里的图片彻底删掉')
    actor = (AUTH_USER.get() or {}).get('username') or 'local'
    iid = ex("INSERT INTO kb_image(table_id,mime,size,data,created_by) VALUES(%s,%s,%s,%s,%s)",
             (tid, mime, len(raw), raw, actor))
    return {'ok': True, 'id': iid, 'mime': mime, 'size': len(raw),
            'url': f'/api/tables/{tid}/images/{iid}'}

@router.get("/api/tables/{tid}/images/{iid}")
def get_image(tid: int, iid: int):
    t = _need(tid)
    need_table(tid, t.get('project') or '')
    r = q("SELECT mime,data FROM kb_image WHERE id=%s AND table_id=%s", (iid, tid), one=True)
    if not r:
        raise HTTPException(404, '图片不存在')
    return Response(content=r['data'], media_type=r['mime'],
                    headers={'Cache-Control': 'private, no-store',
                             'X-Content-Type-Options': 'nosniff'})
