# -*- coding: utf-8 -*-
"""子表 → 总表 单向镜像汇总。

- 一个总表可以接多张子表；每张子表一条 kb_sync 配置。
- 子表新增/修改/删除，对应复制/更新/软删总表行，不做任何计算。
- 关联关系存 kb_sync_link(source_rid → target_rid)，重复全量同步不会重复插入。
- 总表行只在映射字段变化时更新，保留总表自己的其他列。
"""
import json
import queue
import threading
import time

try:
    from .engine import audit, conn, ex, jdump, jload, q
except ImportError:
    from engine import audit, conn, ex, jdump, jload, q

SRC_TABLE_NAME = '$source_table'

_JOBS = {}
_PENDING = {}
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_FULL_LOCK = threading.Lock()
_HOOK = None
_SRC_CONFIGS = {}


def invalidate_configs():
    _SRC_CONFIGS.clear()


def _source_configs(tid):
    """按来源表缓存启用规则，避免每次改单元格都多查一次 kb_sync。"""
    if tid not in _SRC_CONFIGS:
        _SRC_CONFIGS[tid] = q(
            "SELECT id,target_tid,source_tid,name,mapping,enabled FROM kb_sync "
            "WHERE source_tid=%s AND enabled=1", (tid,))
    return _SRC_CONFIGS[tid]


_EVENT_Q = queue.Queue()
_EVENT_STARTED = False
_EVENT_LOCK = threading.Lock()
_PEND_TARGET = {}       # target_tid -> 队列里还没做完、会写到这张总表的增量条数


def _event_worker():
    while True:
        sync, rid, action = _EVENT_Q.get()
        try:
            cur = _cfg(sync['id'])
            if not cur or not cur['enabled']:
                continue
            job = _JOBS.get(sync['id'])
            if job and job.get('status') == 'running':
                _PENDING.setdefault(sync['id'], []).append((rid, action))
                continue
            with _lock(sync['id']):
                mirror_one(cur, rid, action)
        except Exception as e:
            print(f'[sync] event {sync.get("id")}/{rid} failed: {e!r}', flush=True)
        finally:
            t = int(sync.get('target_tid') or 0)
            if _PEND_TARGET.get(t):
                _PEND_TARGET[t] -= 1
                if _PEND_TARGET[t] <= 0:
                    _PEND_TARGET.pop(t, None)
            _EVENT_Q.task_done()


def _ensure_event_worker():
    global _EVENT_STARTED
    if _EVENT_STARTED:
        return
    with _EVENT_LOCK:
        if not _EVENT_STARTED:
            threading.Thread(target=_event_worker, name='kb-sync-events', daemon=True).start()
            _EVENT_STARTED = True


def flush_for(tid, timeout=2.0):
    """读某张表之前，只在「这张表是汇总总表、且它还有没做完的增量」时才等。
    以前不管读哪张表都要等整个同步队列：别人在子表上连续改格子时，所有人读表都会被拖慢。"""
    tid = int(tid)
    if not _EVENT_STARTED or not _PEND_TARGET.get(tid):
        return
    end = time.monotonic() + timeout
    while _PEND_TARGET.get(tid) and time.monotonic() < end:
        time.sleep(0.01)


def set_target_change_hook(fn):
    """由 app 注入：总表缓存失效/行数变化。fn(target_tid, delta)。"""
    global _HOOK
    _HOOK = fn


def _notify(target_tid, delta=0):
    if _HOOK is None:
        return
    try:
        _HOOK(target_tid, delta)
    except Exception:
        pass


def _lock(sid):
    with _LOCKS_GUARD:
        lk = _LOCKS.get(sid)
        if lk is None:
            lk = threading.Lock()
            _LOCKS[sid] = lk
        return lk


def _cfg(sid):
    return q("SELECT id,target_tid,source_tid,name,mapping,enabled,last_sync_at,last_error "
             "FROM kb_sync WHERE id=%s", (sid,), one=True)


def _source_name(tid):
    r = q("SELECT name FROM kb_table WHERE id=%s AND deleted=0", (tid,), one=True)
    return r['name'] if r else ''


def _mapping(sync):
    m = sync.get('mapping')
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except Exception:
            m = {}
    return m if isinstance(m, dict) else {}


def _mapped(sync, row, source_name):
    out = {}
    for tk, sk in _mapping(sync).items():
        if not tk or not sk:
            continue
        out[tk] = source_name if sk == SRC_TABLE_NAME else row.get(sk)
    return out


def _same(old, new):
    for k, v in new.items():
        if old.get(k) != v:
            return False
    return True


def _delete_link(link, sync):
    if not link:
        return
    n = ex("UPDATE kb_row SET deleted=1 WHERE id=%s AND table_id=%s AND deleted=0",
           (link['target_rid'], sync['target_tid']))
    ex("DELETE FROM kb_sync_link WHERE id=%s", (link['id'],))
    audit(sync['target_tid'], link['target_rid'], 'sync_delete',
          {'sync': sync['name'], 'source_rid': link['source_rid']},
          actor=('sync:' + (sync['name'] or '汇总'))[:64])
    if n:
        _notify(sync['target_tid'], -1)


def _create_target(sync, data, source_rid, source_name, create_link=True):
    mx = q("SELECT COALESCE(MAX(sort),0)+1 AS n FROM kb_row WHERE table_id=%s",
           (sync['target_tid'],), one=True)['n']
    rid = ex("INSERT INTO kb_row(table_id,data,sort) VALUES(%s,%s,%s)",
             (sync['target_tid'], jdump(data), mx))
    if create_link:
        ex("INSERT INTO kb_sync_link(sync_id,source_rid,target_rid) VALUES(%s,%s,%s)",
           (sync['id'], source_rid, rid))
    audit(sync['target_tid'], rid, 'sync_insert',
          {'sync': sync['name'], 'source': source_name, 'source_rid': source_rid},
          actor=('sync:' + (sync['name'] or '汇总'))[:64])
    _notify(sync['target_tid'], 1)
    return rid


def mirror_one(sync, source_rid, action='upsert', source_data=None, source_name=None):
    """把一条子表行镜像到总表。action=delete 时软删总表行。"""
    target_tid = sync['target_tid']
    if action == 'delete':
        link = q("SELECT id,target_rid,source_rid FROM kb_sync_link "
                 "WHERE sync_id=%s AND source_rid=%s", (sync['id'], source_rid), one=True)
        _delete_link(link, sync)
        return 'delete'
    if source_data is None:
        r = q("SELECT data FROM kb_row WHERE id=%s AND table_id=%s AND deleted=0",
              (source_rid, sync['source_tid']), one=True)
        if not r:
            link = q("SELECT id,target_rid,source_rid FROM kb_sync_link "
                     "WHERE sync_id=%s AND source_rid=%s", (sync['id'], source_rid), one=True)
            _delete_link(link, sync)
            return 'delete'
        source_data = jload(r['data'])
    if not isinstance(source_data, dict):
        source_data = {}
    if source_name is None:
        source_name = _source_name(sync['source_tid'])
    data = _mapped(sync, source_data, source_name)
    link = q("SELECT id,target_rid FROM kb_sync_link WHERE sync_id=%s AND source_rid=%s",
             (sync['id'], source_rid), one=True)
    if link:
        tgt = q("SELECT data FROM kb_row WHERE id=%s AND table_id=%s AND deleted=0",
                (link['target_rid'], target_tid), one=True)
        if tgt:
            old = jload(tgt['data']) or {}
            if _same(old, data):
                return 'skip'
            merged = {**old, **data}
            ex("UPDATE kb_row SET data=%s,ver=ver+1 WHERE id=%s",
               (jdump(merged), link['target_rid']))
            audit(target_tid, link['target_rid'], 'sync_update',
                  {'sync': sync['name'], 'source_rid': source_rid},
                  actor=('sync:' + (sync['name'] or '汇总'))[:64])
            _notify(target_tid, 0)
            return 'update'
        # 总表行被手工删了：重新复制一份，并把关联指到新行
        rid = _create_target(sync, data, source_rid, source_name, create_link=False)
        ex("UPDATE kb_sync_link SET target_rid=%s WHERE id=%s", (rid, link['id']))
        return 'insert'
    rid = _create_target(sync, data, source_rid, source_name)
    return 'insert'


def _process_pending(sid, sync):
    items = _PENDING.pop(sid, [])
    if not items:
        return
    cur = q("SELECT enabled FROM kb_sync WHERE id=%s", (sid,), one=True)
    if not cur or not cur['enabled']:
        return
    for rid, action in items:
        try:
            mirror_one(sync, rid, action)
        except Exception as e:
            print(f'[sync] pending {sid}/{rid} failed: {e!r}', flush=True)


def _flush_links(new_links, link_updates):
    if not new_links and not link_updates:
        return
    con = conn()
    try:
        cur = con.cursor()
        for i in range(0, len(new_links), 300):
            cur.executemany("INSERT INTO kb_sync_link(sync_id,source_rid,target_rid) "
                            "VALUES(%s,%s,%s)", new_links[i:i + 300])
        for i in range(0, len(link_updates), 300):
            cur.executemany("UPDATE kb_sync_link SET target_rid=%s WHERE id=%s",
                            link_updates[i:i + 300])
        con.commit()
    finally:
        con.close()


def _full_mirror(sync, rows, job):
    """全量对账：链接与总表行一次载入，按行写入，链接最后批量落库。"""
    sid = sync['id']
    tid = sync['target_tid']
    src_ids = {r['id'] for r in rows}
    sname = _source_name(sync['source_tid'])
    links = {l['source_rid']: l for l in q(
        "SELECT id,source_rid,target_rid FROM kb_sync_link WHERE sync_id=%s", (sid,))}
    target_rows = {}
    for r in q("SELECT id,data FROM kb_row WHERE table_id=%s AND deleted=0", (tid,)):
        d = jload(r['data'])
        target_rows[r['id']] = d if isinstance(d, dict) else {}
    base_sort = q("SELECT COALESCE(MAX(sort),0)+1 AS n FROM kb_row WHERE table_id=%s",
                  (tid,), one=True)['n']
    new_links, link_updates = [], []
    for idx, r in enumerate(rows):
        if idx % 20 == 0:
            cur = q("SELECT enabled FROM kb_sync WHERE id=%s", (sid,), one=True)
            if not cur or not cur['enabled']:
                job['error'] = '规则已停用'
                break
        try:
            src = jload(r['data'])
            if not isinstance(src, dict):
                src = {}
            data = _mapped(sync, src, sname)
            link = links.get(r['id'])
            if link:
                old = target_rows.get(link['target_rid'])
                if old is not None:
                    if not _same(old, data):
                        merged = {**old, **data}
                        n = ex("UPDATE kb_row SET data=%s,ver=ver+1 "
                               "WHERE id=%s AND table_id=%s AND deleted=0",
                               (jdump(merged), link['target_rid'], tid))
                        if n:
                            target_rows[link['target_rid']] = merged
                            audit(tid, link['target_rid'], 'sync_update',
                                  {'sync': sync['name'], 'source_rid': r['id']},
                                  actor=('sync:' + (sync['name'] or '汇总'))[:64])
                            _notify(tid, 0)
                        else:
                            old = None
                if old is None:
                    rid = ex("INSERT INTO kb_row(table_id,data,sort) VALUES(%s,%s,%s)",
                             (tid, jdump(data), base_sort))
                    base_sort += 1
                    target_rows[rid] = data
                    link_updates.append((rid, link['id']))
                    audit(tid, rid, 'sync_insert',
                          {'sync': sync['name'], 'source': sname, 'source_rid': r['id']},
                          actor=('sync:' + (sync['name'] or '汇总'))[:64])
                    _notify(tid, 1)
            else:
                rid = ex("INSERT INTO kb_row(table_id,data,sort) VALUES(%s,%s,%s)",
                         (tid, jdump(data), base_sort))
                base_sort += 1
                target_rows[rid] = data
                new_links.append((sid, r['id'], rid))
                audit(tid, rid, 'sync_insert',
                      {'sync': sync['name'], 'source': sname, 'source_rid': r['id']},
                      actor=('sync:' + (sync['name'] or '汇总'))[:64])
                _notify(tid, 1)
        except Exception as e:
            job['error'] = str(e)[:400]
            print(f'[sync] row {sid}/{r["id"]} failed: {e!r}', flush=True)
        job['done'] += 1
    _flush_links(new_links, link_updates)
    for source_rid, link in links.items():
        if source_rid not in src_ids:
            _delete_link(link, sync)


def start_full(sid):
    """后台全量对账；已有任务在跑/排队则复用。"""
    job = _JOBS.get(sid)
    if job and job.get('status') in ('running', 'queued'):
        return job
    j = {'status': 'queued', 'total': 0, 'done': 0, 'error': '', 'started': time.time()}
    _JOBS[sid] = j
    threading.Thread(target=_full_worker, args=(sid,), name=f'kb-sync-{sid}', daemon=True).start()
    return j


def _full_worker(sid):
    j = _JOBS[sid]
    sync = _cfg(sid)
    if not sync:
        j.update(status='error', error='汇总规则不存在')
        return
    lk = _lock(sid)
    # 全量同步全局串行：多条规则同时全量会一起打满连接池，前台就会卡
    with _FULL_LOCK, lk:
        if sid not in _JOBS:
            return
        sync = _cfg(sid)
        if not sync:
            j.update(status='error', error='汇总规则不存在')
            return
        if not sync['enabled']:
            j.update(status='error', error='规则已停用')
            return
        j['status'] = 'running'
        try:
            rows = q("SELECT id,data FROM kb_row WHERE table_id=%s AND deleted=0 ORDER BY sort,id",
                     (sync['source_tid'],))
            j['total'] = len(rows)
            _full_mirror(sync, rows, j)
            j['status'] = 'done' if not j['error'] else 'error'
            ex("UPDATE kb_sync SET last_sync_at=NOW(),last_error=%s WHERE id=%s",
               (j['error'][:500], sid))
            _process_pending(sid, sync)
        except Exception as e:
            j['status'] = 'error'
            j['error'] = str(e)[:400]
            print(f'[sync] full {sid} failed: {e!r}', flush=True)
            try:
                ex("UPDATE kb_sync SET last_error=%s WHERE id=%s", (j['error'][:500], sid))
            except Exception:
                pass


def source_event(tid, rid, action='upsert'):
    """子表行变化后调用：排队同步到所有以它为来源的总表，不阻塞源表保存。"""
    syncs = _source_configs(tid)
    if not syncs:
        return
    _ensure_event_worker()
    for sync in syncs:
        t = int(sync['target_tid'])
        _PEND_TARGET[t] = _PEND_TARGET.get(t, 0) + 1
        _EVENT_Q.put((sync, rid, action))


def source_bulk(tid):
    """导入/批量写入后：对相关规则做一次后台全量对账。"""
    for r in _source_configs(tid):
        start_full(r['id'])


# ==================== 反向：总表（镜像行）→ 子表 ====================
# 规则：只有"从子表汇总来的行"（kb_sync_link 里有链接）才回写；在总表直接新增的行没有链接，不动子表。
# 回写只覆盖映射字段；子表行版本号 +1，别人正开着这行会被乐观锁拦住（409），不会悄悄覆盖。
_TLS = threading.local()


def is_master(tid):
    """这张表是不是某条汇总规则的总表（是的话，改它的行要考虑回写子表）。"""
    return bool(q("SELECT 1 FROM kb_sync WHERE target_tid=%s AND enabled=1 LIMIT 1", (tid,), one=True))


def links_for_target_row(tid, rid):
    return q("SELECT l.id link_id,l.sync_id,l.source_rid,s.source_tid,s.mapping,s.name "
             "FROM kb_sync_link l JOIN kb_sync s ON s.id=l.sync_id "
             "WHERE s.target_tid=%s AND s.enabled=1 AND l.target_rid=%s", (tid, rid))


def reverse_update(tid, rid, data, changed, actor=''):
    """总表的镜像行被改：把映射字段的新值写回子表对应行。返回写回的子表行数。"""
    if getattr(_TLS, 'busy', False) or not changed:
        return 0
    n = 0
    for l in links_for_target_row(tid, rid):
        mp = _mapping(l)
        patch = {}
        for tk in changed:
            sk = mp.get(tk)
            if sk and sk != SRC_TABLE_NAME and tk in data:
                patch[sk] = data.get(tk)
        if not patch:
            continue
        row = q("SELECT data FROM kb_row WHERE id=%s AND table_id=%s AND deleted=0",
                (l['source_rid'], l['source_tid']), one=True)
        if not row:
            continue
        old = jload(row['data'])
        merged = {**old, **patch}
        diff = {k: {'old': old.get(k), 'new': v} for k, v in patch.items() if old.get(k) != v}
        if not diff:
            continue
        _TLS.busy = True          # 这条线上的写入不再触发"子表→总表"的排队，避免来回打转
        try:
            ex("UPDATE kb_row SET data=%s,ver=ver+1 WHERE id=%s AND table_id=%s AND deleted=0",
               (jdump(merged), l['source_rid'], l['source_tid']))
            audit(l['source_tid'], l['source_rid'], 'sync_back',
                  {'sync': l.get('name') or '', 'master_rid': rid, 'fields': diff},
                  actor=(actor or 'sync:汇总回写')[:64])
            _notify(l['source_tid'], 0)
            n += 1
        finally:
            _TLS.busy = False
    return n


def reverse_delete(tid, rid, actor=''):
    """总表删掉镜像行：子表对应行一起软删（镜像语义），并记审计。返回联动的子表行数。"""
    if getattr(_TLS, 'busy', False):
        return 0
    n = 0
    for l in links_for_target_row(tid, rid):
        row = q("SELECT data FROM kb_row WHERE id=%s AND table_id=%s AND deleted=0",
                (l['source_rid'], l['source_tid']), one=True)
        if not row:
            continue
        _TLS.busy = True
        try:
            ex("UPDATE kb_row SET deleted=1 WHERE id=%s AND table_id=%s",
               (l['source_rid'], l['source_tid']))
            audit(l['source_tid'], l['source_rid'], 'sync_back_delete',
                  {'sync': l.get('name') or '', 'master_rid': rid, 'data': jload(row['data'])},
                  actor=(actor or 'sync:汇总删除')[:64])
            _notify(l['source_tid'], -1)
            n += 1
        finally:
            _TLS.busy = False
    return n


def source_ids(tid, ids):
    """批量接口插入后的增量同步：少量行走队列，大量行走全量对账。"""
    ids = [int(x) for x in (ids or [])]
    if not ids:
        return
    if len(ids) > 100:
        source_bulk(tid)
        return
    for rid in ids:
        source_event(tid, rid, 'upsert')


def delete_config(sid, purge=True):
    sync = _cfg(sid)
    if not sync:
        return False
    if purge:
        # 批量软删总表副本：逐条 UPDATE 在 400+ 行时会把删除请求拖到一两分钟
        links = q("SELECT target_rid FROM kb_sync_link WHERE sync_id=%s", (sid,))
        ids = [int(x['target_rid']) for x in links]
        removed = 0
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            ph = ','.join(['%s'] * len(chunk))
            removed += ex(f"UPDATE kb_row SET deleted=1,ver=ver+1 "
                          f"WHERE table_id=%s AND deleted=0 AND id IN ({ph})",
                          tuple([sync['target_tid']] + chunk)) or 0
        if removed:
            audit(sync['target_tid'], None, 'sync_cleanup',
                  {'sync': sync['name'], 'rows': removed},
                  actor=('sync:' + (sync['name'] or '汇总'))[:64])
            _notify(sync['target_tid'], -removed)
    ex("DELETE FROM kb_sync_link WHERE sync_id=%s", (sid,))
    ex("DELETE FROM kb_sync WHERE id=%s", (sid,))
    _JOBS.pop(sid, None)
    _PENDING.pop(sid, None)
    invalidate_configs()
    return True


def cleanup_source_table(tid):
    """子表被删：其汇总规则和已复制到总表的行一起清掉。"""
    for s in q("SELECT id FROM kb_sync WHERE source_tid=%s", (tid,)):
        delete_config(s['id'], purge=True)


def cleanup_target_table(tid):
    for s in q("SELECT id FROM kb_sync WHERE target_tid=%s", (tid,)):
        ex("DELETE FROM kb_sync_link WHERE sync_id=%s", (s['id'],))
        ex("DELETE FROM kb_sync WHERE id=%s", (s['id'],))
        _JOBS.pop(s['id'], None)
        _PENDING.pop(s['id'], None)
    invalidate_configs()


def cleanup_tables(tids):
    if not tids:
        return
    ph = ','.join(['%s'] * len(tids))
    for s in q(f"SELECT id FROM kb_sync WHERE source_tid IN ({ph})", tuple(tids)):
        delete_config(s['id'], purge=True)
    for s in q(f"SELECT id FROM kb_sync WHERE target_tid IN ({ph})", tuple(tids)):
        ex("DELETE FROM kb_sync_link WHERE sync_id=%s", (s['id'],))
        ex("DELETE FROM kb_sync WHERE id=%s", (s['id'],))
        _JOBS.pop(s['id'], None)
        _PENDING.pop(s['id'], None)
    invalidate_configs()


def job_status(sid):
    j = _JOBS.get(sid)
    if not j:
        return None
    return {k: j.get(k) for k in ('status', 'total', 'done', 'error')}
