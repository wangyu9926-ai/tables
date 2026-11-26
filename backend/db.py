# -*- coding: utf-8 -*-
"""数据库连接配置：sentuor_test（PolarDB，与 8002 平台/多维表格共用实例）
本应用只使用 kb_ 前缀的表，不动库里其他表。

连接池：远程 PolarDB 每次 connect 都要 TCP+auth 握手（约 150~300ms），
之前每个查询都新建连接，首屏光握手就 1 秒多。这里用线程安全的惰性连接池
复用长连接；只有闲置超过 IDLE_PING 秒才 ping 一次，连续查询不再付这个 RTT。

口令不写在源码里（源码会被拷贝、同步、打包）：先看环境变量 MUMA_DB_PASSWORD，
再看本机专用的 backend/local.secrets.json；两处都没有就直接报错，不会拿空口令去连库。
"""
import json
import os
import queue
import threading
import time

import pymysql

_LOCAL_SECRETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'local.secrets.json')


def _db_password() -> str:
    pw = os.environ.get('MUMA_DB_PASSWORD')
    if pw:
        return pw
    try:
        with open(_LOCAL_SECRETS, 'r', encoding='utf-8') as f:
            pw = (json.load(f) or {}).get('MUMA_DB_PASSWORD') or ''
    except FileNotFoundError:
        pw = ''
    except (OSError, ValueError):
        raise RuntimeError(f'{_LOCAL_SECRETS} 读不出来：请确认它是合法的 JSON') from None
    if not pw:
        raise RuntimeError(
            '数据库口令没有配置：请设置环境变量 MUMA_DB_PASSWORD，或在本机新建\n'
            f'  {_LOCAL_SECRETS}\n'
            '  内容形如 {"MUMA_DB_PASSWORD": "口令"}（这个文件不要提交、不要外发）')
    return pw


DB_CONFIG = {
    "host": os.environ.get("MUMA_DB_HOST", "sentuor2026.mysql.polardb.rds.aliyuncs.com"),
    "port": int(os.environ.get("MUMA_DB_PORT", "3306")),
    "user": os.environ.get("MUMA_DB_USER", "st_rpa"),
    "password": _db_password(),
    "database": os.environ.get("MUMA_DB_NAME", "sentuor_test"),
    "charset": "utf8mb4",
    "connect_timeout": int(os.environ.get("MUMA_DB_CONNECT_TIMEOUT", "5")),
    "read_timeout": int(os.environ.get("MUMA_DB_READ_TIMEOUT", "20")),
    "write_timeout": int(os.environ.get("MUMA_DB_WRITE_TIMEOUT", "20")),
}

POOL_SIZE = int(os.environ.get("MUMA_DB_POOL", "16"))
POOL_WAIT = float(os.environ.get("MUMA_DB_POOL_WAIT", "8"))
IDLE_PING = float(os.environ.get("MUMA_DB_IDLE_PING", "2.5"))

_pool = None
_slots = None
_pool_lock = threading.Lock()


def _new_conn():
    return pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor,
                           autocommit=False)


class _PooledConn:
    """pymysql Connection 的薄包装：close() 归还池子而不是真断开。"""

    __slots__ = ("_raw", "_pool", "_slots", "_since", "_closed")

    def __init__(self, raw, pool, slots, since):
        object.__setattr__(self, "_raw", raw)
        object.__setattr__(self, "_pool", pool)
        object.__setattr__(self, "_slots", slots)
        object.__setattr__(self, "_since", since)
        object.__setattr__(self, "_closed", False)

    def __getattr__(self, name):
        return getattr(self._raw, name)

    def __setattr__(self, name, value):
        setattr(self._raw, name, value)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if object.__getattribute__(self, "_closed"):
            return
        object.__setattr__(self, "_closed", True)
        raw = self._raw
        pool = self._pool
        slots = self._slots
        try:
            if raw.open:
                # 归还前结束事务：否则 autocommit=False 的连接会一直持有
                # REPEATABLE READ 快照和元数据锁（会让 DDL/后续读取卡住或读到旧数据）
                try:
                    raw.rollback()
                except Exception:
                    try:
                        raw.close()
                    except Exception:
                        pass
                    return
                try:
                    pool.put_nowait((raw, time.monotonic()))
                except queue.Full:
                    try:
                        raw.close()
                    except Exception:
                        pass
                return
            try:
                raw.close()
            except Exception:
                pass
        except Exception:
            try:
                raw.close()
            except Exception:
                pass
        finally:
            slots.release()


def _get_pool():
    global _pool, _slots
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = queue.LifoQueue(maxsize=POOL_SIZE)
                _slots = threading.BoundedSemaphore(POOL_SIZE)
    return _pool, _slots


def conn():
    pool, slots = _get_pool()
    # 并发上限：超过 POOL_SIZE 的请求排队等连接，而不是无限新建连接。
    # 否则一波 40 并发会打出 40 条新连接，每条都要重新做 150~300ms 握手。
    if not slots.acquire(timeout=POOL_WAIT):
        raise pymysql.err.OperationalError(2013, "连接池繁忙，请稍后重试")
    try:
        try:
            raw, since = pool.get_nowait()
        except queue.Empty:
            raw, since = _new_conn(), time.monotonic()
        else:
            if time.monotonic() - since > IDLE_PING:
                try:
                    raw.ping(reconnect=True)
                except Exception:
                    try:
                        raw.close()
                    except Exception:
                        pass
                    raw = _new_conn()
                since = time.monotonic()
        return _PooledConn(raw, pool, slots, since)
    except Exception:
        slots.release()
        raise
