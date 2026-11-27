# -*- coding: utf-8 -*-
"""元信息：项目/表/列/权限视图一次取全（前端首屏）"""
from backend.deps import *          # noqa: F401,F403
from fastapi import APIRouter       # noqa: F401

router = APIRouter()

@router.get("/api/meta")
def meta():
    """元信息缓存：首次构建后内存直出；写操作按影响范围失效。

    构建期间加锁，避免一批用户同时刷新时各自查 5 遍库（缓存击穿）。
    响应字节按「元信息版本 + 行数版本 + 授权视图」缓存：每次打开页面都会调这个接口，
    逐字段编码 80KB 的结构再输出，曾经是每个请求最大的一笔固定开销（约 17ms）。
    """
    key = (_META_VERS['gen'], _META_VERS['cnt'], _meta_view())
    hit = _META_JSON.get(key)
    if hit is not None:
        return Response(content=hit, media_type='application/json')
    d = _META['data']
    if d is None:
        with _META_LOCK:
            d = _META['data']
            if d is None:
                gen = _META_VERS['gen']
                d = _build_meta()
                # 构建期间如果有写操作，不能把旧快照重新塞回缓存
                _META['data'] = d if _META_VERS['gen'] == gen else None
    resp = _json_response(_safe_meta(d))
    # 序列化期间又有写入的话，这份字节对应的不是“现在”的版本，不缓存
    if (_META_VERS['gen'], _META_VERS['cnt'], key[2]) == key:
        if len(_META_JSON) > 64:
            _META_JSON.clear()
        _META_JSON[key] = resp.body
    return resp
