# -*- coding: utf-8 -*-
"""变更通知：轮询 /changes + SSE /events"""
from backend.deps import *          # noqa: F401,F403
from fastapi import APIRouter       # noqa: F401

router = APIRouter()

@router.get("/api/tables/{tid}/changes")
def table_changes(tid: int, since: int = 0):
    """轮询兜底：客户端带上次拿到的 seq，返回比它新的事件。"""
    _t = _need(tid)
    need_table(tid, _t.get('project') or '')
    seq, evs = kb_changes.since(tid, since)
    return _json_response({'seq': seq, 'events': evs})

@router.get("/api/tables/{tid}/events")
async def table_events(tid: int, request: Request):
    """SSE：页面开着这张表时挂一条长连接，有写入就推一条变更提示。"""
    t = await run_in_threadpool(_need, tid)
    await run_in_threadpool(need_table, tid, t.get('project') or '')
    q_ = kb_changes.subscribe(tid)

    async def gen():
        last = kb_changes.current(tid)
        try:
            yield 'event: hello\ndata: ' + json.dumps({'seq': last}) + '\n\n'
            while True:
                if await request.is_disconnected():
                    break
                try:
                    await asyncio.wait_for(q_.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ': ping\n\n'          # 保活，别让代理把空闲连接掐了
                    continue
                cur, evs = kb_changes.since(tid, last)
                last = cur
                if evs:
                    yield ('event: change\ndata: '
                           + json.dumps({'seq': cur, 'events': evs}, ensure_ascii=False) + '\n\n')
        finally:
            kb_changes.unsubscribe(tid, q_)

    return StreamingResponse(gen(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
