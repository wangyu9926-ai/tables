# -*- coding: utf-8 -*-
"""按功能拆分的路由模块（APIRouter）。

约定（2026-11-26 起）：
- 新功能写成 `backend/modules/xxx.py`：文件里一个 `router = APIRouter()`，路由函数和请求体（Pydantic 模型）都放这个文件；
- 开头 `from backend.deps import *` 拿到数据库/引擎导出、缓存、权限、序列化等公共函数（`backend/deps.py` 不导入 app.py，所以没有循环）；
- 在 `backend/app.py` 里 `from backend.modules import xxx` + `app.include_router(xxx.router)`，
  **位置要放在这些路由原来注册的地方**（FastAPI 按注册顺序匹配路径，`/rows/bulk` 必须排在 `/rows/{rid}` 前面）；
- 需要在运行时改变、又要跨模块共享的状态，必须放在 dict/对象里（例如 `deps._META_VERS`）：
  `from ... import *` 复制的是**绑定**，裸标量在别的模块里只是导入那一刻的副本（踩过：元信息缓存永不失效）。
"""
