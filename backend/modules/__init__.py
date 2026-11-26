# -*- coding: utf-8 -*-
"""按功能拆分的路由模块（APIRouter）。

约定（2026-11-26 起）：
- 新功能写成 `backend/modules/xxx.py`，里面一个 `router = APIRouter()`，路由和请求体都放模块里；
- 在 `backend/app.py` 里 `from backend.modules import xxx` + `app.include_router(xxx.router)`，
  位置放在原来写这些路由的地方（FastAPI 按注册顺序匹配路径，别随意挪动顺序）；
- 公共函数（q/ex/need_table/缓存失效…）仍在 app.py，模块里用 `_a()` 延迟导入拿，避免循环导入。

老路由暂时留在 app.py：它们共用模块级缓存、且路径顺序互相咬合（例如 /rows/bulk 必须排在
/rows/{rid} 前面），要拆得一个模块一轮回归，别和功能改动混在一起。
"""
