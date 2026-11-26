# -*- coding: utf-8 -*-
"""表级变更通知：每张表一个递增序号 + 最近事件 + SSE 订阅者。

目的：别人改了数据，你的页面能自己刷新（服务器推送；长连接不可用时前端退回轮询 /changes）。
只记"哪几行变了"，不记数据本身 —— 页面收到后自己取最新值，所以不会把正在编辑的输入冲掉，
也不会因为一次推送就把整页重绘。

线程模型：bump() 可能从接口线程、汇总同步线程里调用；订阅者是 asyncio 队列，
跨线程投递用 loop.call_soon_threadsafe。慢客户端队列满了就丢这一次（它下次会收到更大的 seq）。
"""
import asyncio
import collections
import threading
import time

_LOCK = threading.Lock()
_SEQ = {}          # tid -> int
_EVENTS = {}       # tid -> deque[{'seq','kind','rows','at','actor'}]
_SUBS = {}         # tid -> set[(loop, queue)]
MAX_EVENTS = 200


def current(tid):
    with _LOCK:
        return _SEQ.get(int(tid), 0)


def bump(tid, kind='update', row_ids=(), actor=''):
    """任何写操作之后调用一次；kind: update/insert/delete/move/sync/comment/structure/reset。"""
    tid = int(tid)
    with _LOCK:
        seq = _SEQ.get(tid, 0) + 1
        _SEQ[tid] = seq
        ev = _EVENTS.setdefault(tid, collections.deque(maxlen=MAX_EVENTS))
        try:
            rows = [int(x) for x in (row_ids or ())][:200]
        except Exception:
            rows = []
        ev.append({'seq': seq, 'kind': kind, 'rows': rows,
                   'at': round(time.time(), 3), 'actor': (actor or '')[:64]})
        subs = list(_SUBS.get(tid, ()))
    for loop, q in subs:
        try:
            loop.call_soon_threadsafe(_offer, q, seq)
        except RuntimeError:
            pass                      # 事件循环已经关了


def _offer(q, seq):
    try:
        q.put_nowait(seq)
    except asyncio.QueueFull:
        pass                          # 慢客户端：丢这一次，下次 seq 更大，照样能追上


def since(tid, seq):
    """比 seq 新的事件。客户端落后太多（事件已被挤出队列）时给一条 reset，让它整页重取。"""
    tid = int(tid)
    with _LOCK:
        cur = _SEQ.get(tid, 0)
        ev = list(_EVENTS.get(tid, ()))
    try:
        seq = int(seq or 0)
    except Exception:
        seq = 0
    if seq > cur:
        # 服务刚重启过：进程内的序号从 0 重新开始，客户端手里的序号反而更大。
        # 必须明确回一条 reset，否则客户端一直以为自己比服务端还新，之后永远收不到变更、也不报错。
        return cur, [{'seq': cur, 'kind': 'reset', 'rows': [], 'at': round(time.time(), 3), 'actor': ''}]
    if seq >= cur:
        return cur, []
    if ev and seq < ev[0]['seq'] - 1:
        return cur, [{'seq': cur, 'kind': 'reset', 'rows': [], 'at': round(time.time(), 3), 'actor': ''}]
    return cur, [e for e in ev if e['seq'] > seq]


def subscribe(tid):
    tid = int(tid)
    loop = asyncio.get_running_loop()
    q = asyncio.Queue(maxsize=200)
    with _LOCK:
        _SUBS.setdefault(tid, set()).add((loop, q))
    return q


def unsubscribe(tid, q):
    with _LOCK:
        s = _SUBS.get(int(tid))
        if s:
            for item in list(s):
                if item[1] is q:
                    s.discard(item)
