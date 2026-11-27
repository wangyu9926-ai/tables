# -*- coding: utf-8 -*-
"""牧马人线上多维表：应用装配层。

FastAPI 实例、异常处理器、中间件、各功能路由的挂载、静态目录与首页都在这里；
业务路由在 backend/modules/*.py，共享函数/缓存/常量在 backend/deps.py。
**include 的顺序有意义**：FastAPI 按注册顺序匹配路径（例如 /rows/bulk 必须排在 /rows/{rid} 前面），
下面这个顺序就是拆分前的注册顺序，别随手调整。
"""
from backend.deps import *          # noqa: F401,F403

app = FastAPI(title="牧马人线上多维表")

app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)

init_schema()

kb_sync.set_target_change_hook(_sync_target_changed)

@app.exception_handler(pymysql.err.DataError)
async def _db_input_out_of_range(request, exc):
    """文本超过列宽、数值超出列的范围（1406 / 1264 / 1366）：是调用方传错了内容，
    回 400 并说清楚；不该表现成服务器故障 500。各接口自己能提前判断的仍然提前给准确的提示，这里是兜底。"""
    code = exc.args[0] if exc.args else ''
    print(f'[db] 输入超出允许范围（{code}）', flush=True)
    return JSONResponse({'detail': '内容过长或数值超出允许范围，请缩短后再试'}, status_code=400)

@app.exception_handler(RequestValidationError)
async def _validation_error(request, exc):
    """请求体不符合接口要求：回 422 并说明是哪个字段。不用框架默认的处理，原因有两个：
    它会把原始输入原样放进响应；输入里有 NaN / Infinity 这类不是合法 JSON 的数值时，
    它自己会在生成响应时再抛异常，结果 500。"""
    parts = []
    for e in exc.errors()[:5]:
        loc = ' → '.join(str(x) for x in e.get('loc', ())) or '请求体'
        parts.append(f'{loc}：{e.get("msg", "")}')
    return JSONResponse({'detail': '请求内容不符合接口要求（' + '；'.join(parts) + '）'}, status_code=422)

@app.middleware("http")
async def _meta_cache_guard(request, call_next):
    resp = await call_next(request)
    if request.method != 'GET' and resp.status_code < 400:
        _invalidate_after(request.method, request.url.path,
                          getattr(request.state, 'kb_inserted', 0))
    return resp

# ---------------- 各功能模块（顺序 = 拆分前的路由注册顺序） ----------------
from backend.modules import meta as _mod_meta          # noqa: E402
app.include_router(_mod_meta.router)
from backend.modules import projects as _mod_projects          # noqa: E402
app.include_router(_mod_projects.router)
from backend.modules import tables as _mod_tables          # noqa: E402
app.include_router(_mod_tables.router)
from backend.modules import columns as _mod_columns          # noqa: E402
app.include_router(_mod_columns.router)
from backend.modules import rows as _mod_rows          # noqa: E402
app.include_router(_mod_rows.router)
from backend.modules import events as _mod_events          # noqa: E402
app.include_router(_mod_events.router)
from backend.modules import comments as _mod_comments          # noqa: E402
app.include_router(_mod_comments.router)
from backend.modules import misc as _mod_misc          # noqa: E402
app.include_router(_mod_misc.router)
from backend.modules import export as _mod_export          # noqa: E402
app.include_router(_mod_export.router)
from backend.modules import images as _mod_images          # noqa: E402
app.include_router(_mod_images.router)
from backend.modules import apikeys as _mod_apikeys          # noqa: E402
app.include_router(_mod_apikeys.router)
from backend.modules import ingest as _mod_ingest          # noqa: E402
app.include_router(_mod_ingest.router)
from backend.modules import imports as _mod_imports          # noqa: E402
app.include_router(_mod_imports.router)
from backend.modules import sync_api as _mod_sync_api          # noqa: E402
app.include_router(_mod_sync_api.router)
from backend.modules import views as _mod_views          # noqa: E402
app.include_router(_mod_views.router)
from backend.modules import dashboards as _mod_dashboards          # noqa: E402
app.include_router(_mod_dashboards.router)

app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

@app.get("/")
def index(request: Request):
    c = _index_asset()
    # 允许浏览器存，但每次都要拿 ETag 来问一句：文件没变就回 304，不用重新下载
    headers = {'ETag': c['etag'], 'Cache-Control': 'no-cache',
               'X-Content-Type-Options': 'nosniff',
               'Content-Security-Policy': c['csp']}
    if request.headers.get('if-none-match') == c['etag']:
        return Response(status_code=304, headers=headers)
    if _GZIP_PRECOMPRESSED and 'gzip' in (request.headers.get('accept-encoding') or '').lower():
        headers['Content-Encoding'] = 'gzip'
        headers['Vary'] = 'Accept-Encoding'
        return Response(content=c['gz'], media_type='text/html; charset=utf-8', headers=headers)
    return Response(content=c['raw'], media_type='text/html; charset=utf-8', headers=headers)
