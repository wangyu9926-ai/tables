# -*- coding: utf-8 -*-
"""多维表格（开放版）—— 存储层（只有本模块在用，不对外）。

连 config.MT_DB_CONFIG 指定的 MySQL（库名 MT_DB_NAME，见 backend/.env）。
坑：只读长连接挂未提交事务会持 MDL，所以这里每条连接都 autocommit=True，
且用上下文管理器保证关闭。
"""
import hashlib
import hmac
import json
import secrets
import threading
from contextlib import contextmanager

import pymysql

from ..config import MT_DB_CONFIG, MT_DB_NAME

DB_NAME = MT_DB_NAME

# 列类型白名单（前端只传这些）
COL_TYPES = ('text', 'number', 'date', 'select', 'multiselect', 'checkbox', 'user', 'image')

_CONNECT_KW = dict(MT_DB_CONFIG)
_CONNECT_KW.update({
    'database': DB_NAME,
    'cursorclass': pymysql.cursors.DictCursor,
    'autocommit': True,
    'connect_timeout': 10,
    'read_timeout': 30,
    'write_timeout': 30,
    'charset': 'utf8mb4',
})


class _Pool:
    """极简连接池（stdlib，不引第三方）。

    远程 MySQL（云上公网）新建连接握手成本高且并发易 2003 超时；
    复用已握手的连接后热路径只要几毫秒。
    取用前用 ping(reconnect=True) 探活，防止 MySQL 侧 wait_timeout 掐断后返回死连接。
    """
    def __init__(self, size=6):
        self._lock = threading.Lock()
        self._idle = []
        self._size = size
        self._made = 0

    def _new(self):
        return pymysql.connect(**_CONNECT_KW)

    def connection(self):
        with self._lock:
            if self._idle:
                c = self._idle.pop()
            elif self._made < self._size:
                self._made += 1
                c = None
            else:
                c = None
        if c is None:
            # 池已满或首次：新建（超出 size 时允许临时新建，靠归还回池收敛）
            return self._new()
        try:
            c.ping(reconnect=True)
            return c
        except Exception:
            try:
                c.close()
            except Exception:
                pass
            with self._lock:
                self._made -= 1
            return self._new()

    def release(self, c):
        try:
            with self._lock:
                if len(self._idle) < self._size:
                    self._idle.append(c)
                    return
            c.close()
        except Exception:
            try:
                c.close()
            except Exception:
                pass


_pool = _Pool()


@contextmanager
def conn():
    """从连接池借一条连接（autocommit=True，避免持 MDL 卡住别人的 DDL）。"""
    c = _pool.connection()
    try:
        yield c
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass
        raise
    finally:
        _pool.release(c)


def q(sql, args=None, one=False):
    with conn() as c, c.cursor() as cu:
        cu.execute(sql, args)
        rows = cu.fetchall()
    if one:
        return rows[0] if rows else None
    return rows


def ex(sql, args=None):
    """执行写语句，返回 lastrowid 或 affected rows。"""
    with conn() as c, c.cursor() as cu:
        cu.execute(sql, args)
        return cu.lastrowid if cu.lastrowid else cu.rowcount


def audit(table_id, row_id, action, actor, detail=None):
    """写审计/变动历史。detail 为 dict（会存 JSON）。审计失败不影响主流程，但要留痕到 stderr。

    table_id=0 表示系统级事件（用户管理等表外操作）。
    """
    try:
        ex("INSERT INTO mt_audit (table_id, row_id, action, actor, detail) VALUES (%s,%s,%s,%s,%s)",
           (table_id, row_id, action, actor or '',
            json.dumps(detail, ensure_ascii=False) if detail is not None else None))
    except Exception as e:  # pragma: no cover
        import sys
        print(f'[mtables] 审计写入失败 table={table_id} row={row_id} {action}: {e}', file=sys.stderr)


# ---------------- 角色（开放版权限模型） ----------------
# 游客（未登录）= 所有表只读；登录用户（staff/admin）= 所有表可编辑；admin 另管表结构与用户。
# 原「按表授权（mt_perm）」体系已移除。

def effective_role(table_id, payload, pmap=None):
    """综合角色：返回 ('editor'|'reader', 说明)。

    游客（payload=None）→ reader；登录用户（staff）→ editor；admin → editor（说明 admin）。
    """
    if not payload:
        return 'reader', 'guest'
    role = payload.get('role') or 'staff'
    if role == 'admin':
        return 'editor', 'admin'
    return 'editor', 'user'


def visible_table_ids(payload):
    """开放版：所有表对所有人可见（含游客）。返回 None 表示不过滤。"""
    return None


def jdump(v):
    return json.dumps(v, ensure_ascii=False) if v is not None else None


def jload(v):
    """mt_row.data 从 MySQL JSON 列读出来可能是 str 或已解析 dict。"""
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    try:
        return json.loads(v)
    except Exception:
        return {}


# ---------------- 用户（mt_user） ----------------

def hash_password(pw: str) -> str:
    """生成密码串。优先 scrypt，平台不支持时回退 pbkdf2（格式带前缀，可混存可校验）。"""
    salt = secrets.token_hex(16)
    try:
        dk = hashlib.scrypt((pw or '').encode(), salt=salt.encode(), n=2 ** 14, r=8, p=1, dklen=32)
        return 'scrypt$16384$8$1$%s$%s' % (salt, dk.hex())
    except ValueError:
        iters = 200000
        dk = hashlib.pbkdf2_hmac('sha256', (pw or '').encode(), salt.encode(), iters)
        return 'pbkdf2$%d$%s$%s' % (iters, salt, dk.hex())


def verify_password(pw: str, stored: str) -> bool:
    try:
        parts = (stored or '').split('$')
        if parts[0] == 'scrypt' and len(parts) == 6:
            _, n, r, p, salt, hexdk = parts
            dk = hashlib.scrypt((pw or '').encode(), salt=salt.encode(),
                                n=int(n), r=int(r), p=int(p), dklen=len(hexdk) // 2)
            return hmac.compare_digest(dk.hex(), hexdk)
        if parts[0] == 'pbkdf2' and len(parts) == 4:
            _, iters, salt, hexdk = parts
            dk = hashlib.pbkdf2_hmac('sha256', (pw or '').encode(), salt.encode(),
                                     int(iters), dklen=len(hexdk) // 2)
            return hmac.compare_digest(dk.hex(), hexdk)
    except Exception:
        return False
    return False


_USER_COLS = 'id, username, password_hash, real_name, role, status, created_by, created_at, updated_at'


def get_user(uid=None, username=None):
    """按 id 或用户名取用户（含 password_hash，仅供认证内部用）。"""
    if uid is not None:
        return q(f"SELECT {_USER_COLS} FROM mt_user WHERE id=%s", (int(uid),), one=True)
    if username:
        return q(f"SELECT {_USER_COLS} FROM mt_user WHERE username=%s", (username,), one=True)
    return None


def list_users():
    return q("SELECT id, username, real_name, role, status, created_by, created_at, updated_at "
             "FROM mt_user ORDER BY id")


def ensure_admin(username: str, password: str) -> bool:
    """启动时引导管理员：不存在则创建；存在则只纠正角色/状态，绝不覆盖密码。

    返回 True=本次新建了管理员。
    """
    row = q("SELECT id FROM mt_user WHERE username=%s", (username,), one=True)
    if row:
        ex("UPDATE mt_user SET role='admin', status=1 WHERE id=%s", (row['id'],))
        return False
    ex("INSERT INTO mt_user (username, password_hash, real_name, role, status, created_by) "
       "VALUES (%s,%s,%s,'admin',1,'system')",
       (username, hash_password(password), '管理员'))
    return True


# ---------------- HTTP 写入密钥（免登录插数据用） ----------------

def apikey_create(table_id, note, actor):
    """生成密钥，返回明文（只此一次；库里只存 sha256）。"""
    plain = 'mtk_' + secrets.token_hex(20)
    h = hashlib.sha256(plain.encode()).hexdigest()
    ex("INSERT INTO mt_apikey (table_id, key_hash, key_prefix, note, created_by) VALUES (%s,%s,%s,%s,%s)",
       (table_id, h, plain[:12], (note or '').strip()[:120], actor or ''))
    return plain


def apikey_lookup(plain):
    """按明文查密钥记录；无效返回 None。"""
    if not plain:
        return None
    h = hashlib.sha256(plain.encode()).hexdigest()
    return q("SELECT id, table_id, note, revoked FROM mt_apikey WHERE key_hash=%s", (h,), one=True)


def apikey_list(table_id):
    return q("SELECT id, key_prefix, note, created_by, created_at, revoked "
             "FROM mt_apikey WHERE table_id=%s ORDER BY id DESC", (table_id,))


# ---------------- 数据库视图（把多维表镜像成库里的实时视图） ----------------

def view_name(table_id):
    return 'mtv_t' + str(int(table_id))


def view_exists(table_id):
    r = q("SELECT COUNT(*) c FROM information_schema.VIEWS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
          (DB_NAME, view_name(table_id)), one=True)
    return bool(r and r['c'])


def rebuild_view(table_id, cols):
    """按当前列定义重建实时视图（CREATE OR REPLACE，幂等）。

    数字/勾选列 +0 出数值（NULL 保持 NULL），其余 JSON_UNQUOTE 出文本；
    JSON path 统一用 $."key" 引号形式，key 里带点也不会解析成嵌套。
    """
    tid = int(table_id)
    exprs = ["`id`", "`sort`", "`ver`", "`created_by`", "`updated_by`", "`created_at`", "`updated_at`"]
    for c in cols:
        path = '$."%s"' % c['key']
        if c['type'] in ('number', 'checkbox'):
            e = "(JSON_EXTRACT(`data`, '%s') + 0)" % path
        else:
            e = "JSON_UNQUOTE(JSON_EXTRACT(`data`, '%s'))" % path
        exprs.append("%s AS `%s`" % (e, c['key']))
    # 拼接项都是受控值：view_name 是 mtv_t+int，key 经 _slug 消毒并反引号包裹，tid 是 int
    ex("CREATE OR REPLACE VIEW `%s` AS SELECT %s FROM `mt_row` WHERE `table_id` = %s AND `deleted` = 0 ORDER BY `sort`, `id`"
       % (view_name(tid), ', '.join(exprs), tid))


def drop_view(table_id):
    ex("DROP VIEW IF EXISTS `%s`" % view_name(int(table_id)))
