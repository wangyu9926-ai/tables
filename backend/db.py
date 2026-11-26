# -*- coding: utf-8 -*-
"""数据库连接配置。

优先读环境变量 `MUMA_DB_*`，其次读本机 `backend/local.secrets.json`（可用 `MUMA_SECRETS_PATH` 改路径），
最后用内置默认值；**口令必须由环境变量或本机配置文件提供**，两处都没有会给出明确提示（不会拿空口令去连库）。
首次部署可以让 `auth_gate/run_gate.py` 的配置向导把这些值一次写好（见「全新部署」）。

连接池：远程库每次 connect 都要 TCP+auth 握手（约 150~300ms），之前每个查询都新建连接，
首屏光握手就 1 秒多。这里用线程安全的惰性连接池复用长连接；只有闲置超过 IDLE_PING 秒才 ping 一次。
"""
import json
import os
import queue
import threading
import time

import pymysql

_LOCAL_SECRETS = os.environ.get('MUMA_SECRETS_PATH') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'local.secrets.json')

DEFAULTS = {
    'MUMA_DB_HOST': 'localhost',
    'MUMA_DB_PORT': '3306',
    'MUMA_DB_USER': 'root',
    'MUMA_DB_NAME': 'kb_tables',
    'MUMA_DB_CONNECT_TIMEOUT': '5',
    'MUMA_DB_READ_TIMEOUT': '20',
    'MUMA_DB_WRITE_TIMEOUT': '20',
}

_CFG = None            # 延迟构建的 pymysql 连接参数
DB_CONFIG = {}         # 兼容：任何连接建好后这里就是当前配置（不含口令以外的敏感信息）


def _local_secrets() -> dict:
    try:
        with open(_LOCAL_SECRETS, 'r', encoding='utf-8') as f:
            d = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise RuntimeError(f'{_LOCAL_SECRETS} 读不出来：请确认它是合法的 JSON') from None
    return d if isinstance(d, dict) else {}


def _pick(key):
    """环境变量 > 本机配置文件 > 默认值。"""
    v = os.environ.get(key)
    if v not in (None, ''):
        return v
    v = _local_secrets().get(key)
    if v not in (None, ''):
        return v
    return DEFAULTS.get(key)


def _build_config() -> dict:
    pw = _pick('MUMA_DB_PASSWORD')
    if not pw:
        raise RuntimeError(
            '数据库口令没有配置：请设置环境变量 MUMA_DB_PASSWORD，或在本机新建\n'
            f'  {_LOCAL_SECRETS}\n'
            '  内容形如 {"MUMA_DB_HOST": "…", "MUMA_DB_USER": "…", "MUMA_DB_PASSWORD": "…", "MUMA_DB_NAME": "…"}\n'
            '  （这个文件不要提交、不要外发）。也可以直接运行「启动看板-带登录.bat」，首次会引导你填。')
    return {
        'host': _pick('MUMA_DB_HOST'),
        'port': int(_pick('MUMA_DB_PORT')),
        'user': _pick('MUMA_DB_USER'),
        'password': pw,
        'database': _pick('MUMA_DB_NAME'),
        'charset': 'utf8mb4',
        'connect_timeout': int(_pick('MUMA_DB_CONNECT_TIMEOUT')),
        'read_timeout': int(_pick('MUMA_DB_READ_TIMEOUT')),
        'write_timeout': int(_pick('MUMA_DB_WRITE_TIMEOUT')),
    }


def config() -> dict:
    global _CFG
    if _CFG is None:
        _CFG = _build_config()
        DB_CONFIG.clear()
        DB_CONFIG.update({k: v for k, v in _CFG.items() if k != 'password'})
    return _CFG


def configured() -> bool:
    """是否已经有可用的连接配置（不实际连库）。没有就说明要走首次配置向导。"""
    try:
        _build_config()
        return True
    except Exception:
        return False


def probe(cfg=None, timeout=5):
    """实际连一次库，确认地址/账号/口令/库名可用。失败会抛 pymysql 的异常。"""
    c = dict(cfg or config())
    c['connect_timeout'] = min(int(c.get('connect_timeout') or timeout), timeout)
    con = pymysql.connect(**c)
    try:
        with con.cursor() as cur:
            cur.execute('SELECT 1')
            cur.fetchone()
    finally:
        con.close()


def reload():
    """配置改过之后（首次配置向导写完文件）重新读取，并把池子里的旧连接丢掉。"""
    global _CFG, _pool, _slots
    _CFG = None
    config()
    with _pool_lock:
        pool = _pool
        _pool = None
        _slots = None
    if pool is not None:
        while True:
            try:
                raw, _since = pool.get_nowait()
            except queue.Empty:
                break
            try:
                raw.close()
            except Exception:
                pass


POOL_SIZE = int(os.environ.get("MUMA_DB_POOL", "16"))
POOL_WAIT = float(os.environ.get("MUMA_DB_POOL_WAIT", "8"))
IDLE_PING = float(os.environ.get("MUMA_DB_IDLE_PING", "2.5"))

_pool = None
_slots = None
_pool_lock = threading.Lock()


def _new_conn():
    return pymysql.connect(**config(), cursorclass=pymysql.cursors.DictCursor,
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
