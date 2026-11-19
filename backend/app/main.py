# -*- coding: utf-8 -*-
"""多维表格（开放版）—— FastAPI 入口。

权限模型（本版）：
  游客（未登录）—— 所有表可看（列表/数据/搜索），不能改；
  登录用户（admin 添加）—— 所有表可增改删行、可看变动历史；
  管理员（.env 引导）—— 另可建表/删表/管列/导入 Excel/管用户/密钥/DB视图。

组成：认证（多用户 JWT + sqlite 会话）+ 多维表格模块 + 前端静态托管。
无全局认证中间件：读接口游客可用（verify_token），写接口各自 require_login / require_admin。
"""
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .auth import router as auth_router
from .auth_sessions import request_sessions
from .config import ADMIN_PASSWORD, ADMIN_USERNAME
from .mtables.router import router as mtables_router
from .mtables import store

_FRONTEND_DIR = Path(__file__).resolve().parents[2] / 'frontend'


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        if store.ensure_admin(ADMIN_USERNAME, ADMIN_PASSWORD):
            print(f'[mtables] 已创建管理员账号：{ADMIN_USERNAME}（密码来自 .env ADMIN_PASSWORD，请尽快在「用户管理」里修改）')
    except Exception as e:
        print(f'[mtables] 管理员账号初始化跳过（数据库未就绪？请先建库并导入 schema.sql）：{e}', file=sys.stderr)
    yield


app = FastAPI(
    title='多维表格（开放版）API',
    description='游客可看 · 登录可改 · 管理员建表管用户 · 变动全留痕',
    version='2.0.0',
    lifespan=lifespan,
)


@app.middleware('http')
async def _per_request_session_cache(request, call_next):
    """只做每请求会话缓存（同一请求内多次 validate_session 不重复查 sqlite），不做拦截。"""
    context = request_sessions.set({})
    try:
        return await call_next(request)
    finally:
        request_sessions.reset(context)


app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],   # 前端默认同源托管；前后端分离部署时再收紧为具体来源
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(auth_router)
app.include_router(mtables_router)


@app.get('/api/health')
def health():
    return {'ok': True}


app.mount('/css', StaticFiles(directory=str(_FRONTEND_DIR / 'css')), name='css')
app.mount('/js', StaticFiles(directory=str(_FRONTEND_DIR / 'js')), name='js')


@app.get('/')
def index():
    return FileResponse(_FRONTEND_DIR / 'index.html')
