# -*- coding: utf-8 -*-
"""Excel / CSV 导入：预览 / 提交"""
from backend.deps import *          # noqa: F401,F403
from fastapi import APIRouter       # noqa: F401

router = APIRouter()

@router.post("/api/import/preview")
async def import_preview(file: UploadFile = File(...), sheet: str = Form(''),
                         header_row: int = Form(1), table_id: int = Form(0), project: str = Form('')):
    """先读预览：可选工作表、看行列数、看前 8 行；不会直接落库。
    解析放到线程池：在 async 函数里同步跑 pandas，会把所有人的请求一起卡住。"""
    _imp_login()
    filename = os.path.basename(file.filename or '')
    if not filename:
        raise HTTPException(400, '没有文件名')
    if os.path.splitext(filename.lower())[1] not in ('.xlsx', '.xls', '.xlsm', '.csv'):
        raise HTTPException(400, '只支持 xlsx / xls / xlsm / csv')
    await run_in_threadpool(_imp_check_target, table_id, project)
    raw = await _read_upload(file, _IMP_MAX_BYTES, '文件不能超过 20MB')
    if not raw:
        raise HTTPException(400, '文件为空')
    return await run_in_threadpool(_imp_preview_sync, raw, filename, sheet, header_row)

@router.post("/api/import/commit")
async def import_commit(file: UploadFile = File(...), config: str = Form(...)):
    """落库。读文件之外的全部工作（解析、类型转换、写库）都在线程池里做。"""
    _imp_login()
    try:
        cfg = json.loads(config)
        if not isinstance(cfg, dict):
            raise ValueError()
    except Exception:
        raise HTTPException(400, '导入配置不是合法 JSON')
    filename = os.path.basename(file.filename or '')
    if os.path.splitext(filename.lower())[1] not in ('.xlsx', '.xls', '.xlsm', '.csv'):
        raise HTTPException(400, '只支持 xlsx / xls / xlsm / csv')
    raw = await _read_upload(file, _IMP_MAX_BYTES, '文件不能超过 20MB')
    if not raw:
        raise HTTPException(400, '文件为空')
    return await run_in_threadpool(_imp_commit_sync, raw, filename, cfg)
