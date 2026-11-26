# -*- coding: utf-8 -*-
"""通用多维表格引擎（架构对齐 D:\2611项目\多维表格 的 mt_* 设计，独立前缀 kb_）

kb_table    表定义（可任意建/改/删；group_name 用于前端页签分组）
kb_column   列定义（key/label/type/options/expr/width/sort/required/hidden）
kb_row      行数据（JSON，ver 乐观锁，软删）
kb_audit    变动历史（谁/何时/哪行哪字段 旧值→新值）
kb_relation 表间关联（可选）
kb_setting  系统设置（键值）：记录一次性初始化标记等

列类型：text/number/date/select/multiselect/checkbox/image/formula
formula 列：expr 用 {列名} 引用同行字段，后端 AST 白名单求值，只读、不落库。
"""
import ast
import atexit
import base64
import hashlib
import json
import math
import operator
import os
import queue
import re
import sys
import threading
import time
from datetime import datetime

import pymysql

try:
    from .db import conn          # 以 backend.engine 被 uvicorn 加载
except ImportError:
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from db import conn

COL_TYPES = ('text', 'number', 'date', 'select', 'multiselect', 'checkbox',
             'image', 'formula')

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS kb_project (
      id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      name VARCHAR(160) NOT NULL UNIQUE,
      icon VARCHAR(16) NOT NULL DEFAULT '📁',
      sort INT NOT NULL DEFAULT 0,
      deleted TINYINT NOT NULL DEFAULT 0,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS kb_table (
      id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      name VARCHAR(160) NOT NULL UNIQUE,
      note VARCHAR(500) NOT NULL DEFAULT '',
      project VARCHAR(160) NOT NULL DEFAULT '',
      group_name VARCHAR(160) NOT NULL DEFAULT '',
      icon VARCHAR(16) NOT NULL DEFAULT '',
      sort INT NOT NULL DEFAULT 0,
      deleted TINYINT NOT NULL DEFAULT 0,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      KEY idx_prj (project, sort),
      KEY idx_grp (group_name, sort)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS kb_column (
      id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      table_id INT UNSIGNED NOT NULL,
      `key` VARCHAR(120) NOT NULL,
      label VARCHAR(200) NOT NULL,
      type VARCHAR(20) NOT NULL DEFAULT 'text',
      options JSON NULL,
      option_colors JSON NULL COMMENT '选项颜色 label -> 调色板序号',
      expr TEXT NULL COMMENT '公式表达式，仅 formula 列',
      width SMALLINT NOT NULL DEFAULT 0,
      sort INT NOT NULL DEFAULT 0,
      required TINYINT NOT NULL DEFAULT 0,
      hidden TINYINT NOT NULL DEFAULT 0,
      deleted TINYINT NOT NULL DEFAULT 0,
      UNIQUE KEY uk_col (table_id, `key`),
      KEY idx_col (table_id, sort)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS kb_row (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      table_id INT UNSIGNED NOT NULL,
      data JSON NOT NULL,
      ver INT NOT NULL DEFAULT 1,
      sort INT NOT NULL DEFAULT 0,
      deleted TINYINT NOT NULL DEFAULT 0,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      KEY idx_row (table_id, deleted, sort)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS kb_audit (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      table_id INT UNSIGNED NOT NULL,
      row_id BIGINT UNSIGNED NULL,
      action VARCHAR(24) NOT NULL,
      actor VARCHAR(64) NOT NULL DEFAULT '',
      detail JSON NULL,
      at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      KEY idx_audit (table_id, at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS kb_user (
      id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      username VARCHAR(64) NOT NULL UNIQUE,
      password_hash VARCHAR(255) NOT NULL,
      real_name VARCHAR(64) NOT NULL DEFAULT '',
      role VARCHAR(16) NOT NULL DEFAULT 'staff',
      status TINYINT NOT NULL DEFAULT 1,
      can_fields TINYINT NOT NULL DEFAULT 0 COMMENT '1=可管理字段（仅限有编辑权的表）',
      can_users TINYINT NOT NULL DEFAULT 0 COMMENT '1=可管理账号（不能超过自身权限）',
      token_ver INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '登录凭证版本：改密/重置时加一，旧凭证立即失效',
      created_by VARCHAR(64) NOT NULL DEFAULT '',
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS kb_perm (
      id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      user_id INT UNSIGNED NOT NULL,
      scope_type VARCHAR(16) NOT NULL COMMENT 'project | table',
      scope_key VARCHAR(200) NOT NULL COMMENT '项目名 或 表 id',
      role VARCHAR(16) NOT NULL DEFAULT 'viewer' COMMENT 'viewer | editor',
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uk_perm (user_id, scope_type, scope_key),
      KEY idx_perm (scope_type, scope_key)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS kb_relation (
      id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      from_tid INT UNSIGNED NOT NULL,
      from_field VARCHAR(120) NOT NULL,
      to_tid INT UNSIGNED NOT NULL,
      to_field VARCHAR(120) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS kb_dashboard (
      id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      project VARCHAR(160) NOT NULL DEFAULT '',
      name VARCHAR(160) NOT NULL DEFAULT '默认看板',
      config LONGTEXT NULL COMMENT '图表配置 JSON',
      is_page TINYINT NOT NULL DEFAULT 0 COMMENT '1=显示在侧栏的数据分析页面',
      created_by VARCHAR(64) NOT NULL DEFAULT '',
      sort INT NOT NULL DEFAULT 0,
      deleted TINYINT NOT NULL DEFAULT 0,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      KEY idx_dash (project, sort)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS kb_image (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      table_id INT UNSIGNED NOT NULL,
      mime VARCHAR(64) NOT NULL DEFAULT 'image/png',
      size INT UNSIGNED NOT NULL DEFAULT 0,
      data LONGBLOB NOT NULL,
      created_by VARCHAR(64) NOT NULL DEFAULT '',
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      KEY idx_img (table_id, id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS kb_comment (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      table_id INT UNSIGNED NOT NULL,
      row_id INT UNSIGNED NOT NULL,
      col_key VARCHAR(120) NOT NULL,
      body VARCHAR(2000) NOT NULL,
      author_id INT UNSIGNED NOT NULL DEFAULT 0,
      author_name VARCHAR(80) NOT NULL DEFAULT '',
      deleted TINYINT NOT NULL DEFAULT 0,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      KEY idx_cell (table_id, row_id),
      KEY idx_tbl (table_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS kb_apikey (
      id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      table_id INT UNSIGNED NOT NULL,
      key_hash CHAR(64) NOT NULL COMMENT 'sha256(明文 key)，明文只在生成时显示一次',
      key_prefix CHAR(12) NOT NULL DEFAULT '' COMMENT '明文前缀，列表展示用',
      note VARCHAR(120) NOT NULL DEFAULT '' COMMENT '用途备注，如：影刀到货机器人',
      created_by VARCHAR(64) NOT NULL DEFAULT '',
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      revoked TINYINT NOT NULL DEFAULT 0,
      UNIQUE KEY uk_key_hash (key_hash),
      KEY idx_key_table (table_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS kb_sync (
      id INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      target_tid INT UNSIGNED NOT NULL COMMENT '总表（被汇总表）',
      source_tid INT UNSIGNED NOT NULL COMMENT '子表（数据来源）',
      name VARCHAR(160) NOT NULL DEFAULT '',
      mapping JSON NOT NULL COMMENT '目标列 key -> 来源列 key；$source_table=来源表名',
      enabled TINYINT NOT NULL DEFAULT 1,
      created_by VARCHAR(64) NOT NULL DEFAULT '',
      last_sync_at DATETIME NULL,
      last_error VARCHAR(500) NOT NULL DEFAULT '',
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      UNIQUE KEY uk_sync (target_tid, source_tid),
      KEY idx_sync_src (source_tid)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS kb_sync_link (
      id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
      sync_id INT UNSIGNED NOT NULL,
      source_rid BIGINT UNSIGNED NOT NULL,
      target_rid BIGINT UNSIGNED NOT NULL,
      created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      UNIQUE KEY uk_sync_srcrow (sync_id, source_rid),
      KEY idx_sync_target (sync_id, target_rid)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS kb_setting (
      k VARCHAR(64) NOT NULL PRIMARY KEY,
      v VARCHAR(255) NOT NULL DEFAULT '',
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]


def init_schema():
    con = conn()
    cur = con.cursor()
    for ddl in SCHEMA:
        cur.execute(ddl)
    # 幂等补列：老库 kb_table 没有 project 字段时补上
    cur.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='kb_table' AND COLUMN_NAME='project'")
    if not cur.fetchone()['n']:
        cur.execute("ALTER TABLE kb_table ADD COLUMN project VARCHAR(160) NOT NULL DEFAULT '' AFTER note")
        cur.execute("ALTER TABLE kb_table ADD KEY idx_prj (project, sort)")
    # 单选/多选：每个选项的颜色（label -> 调色板序号）
    cur.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='kb_column' AND COLUMN_NAME='option_colors'")
    if not cur.fetchone()['n']:
        cur.execute("ALTER TABLE kb_column ADD COLUMN option_colors JSON NULL AFTER options")
    # 分析看板：is_page=1 表示保存为侧栏「数据分析」页面
    cur.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='kb_dashboard' AND COLUMN_NAME='is_page'")
    if not cur.fetchone()['n']:
        cur.execute("ALTER TABLE kb_dashboard ADD COLUMN is_page TINYINT NOT NULL DEFAULT 0 "
                    "COMMENT '1=显示在侧栏的数据分析页面' AFTER config")
    # 可下发能力：字段管理 / 账号管理
    for col, comment in (('can_fields', '1=可管理字段（仅限有编辑权的表）'),
                         ('can_users', '1=可管理账号（不能超过自身权限）')):
        cur.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='kb_user' AND COLUMN_NAME=%s", (col,))
        if not cur.fetchone()['n']:
            cur.execute(f"ALTER TABLE kb_user ADD COLUMN {col} TINYINT NOT NULL DEFAULT 0 COMMENT %s", (comment,))
    # 登录凭证版本：改密 / 重置密码时加一，已签发的旧凭证随之失效
    cur.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='kb_user' AND COLUMN_NAME='token_ver'")
    if not cur.fetchone()['n']:
        cur.execute("ALTER TABLE kb_user ADD COLUMN token_ver INT UNSIGNED NOT NULL DEFAULT 0 "
                    "COMMENT '登录凭证版本：改密/重置时加一，旧凭证立即失效' AFTER can_users")
    # 图片回收站：软删标记 + 删除时间
    cur.execute("SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='kb_image' AND COLUMN_NAME='deleted'")
    if not cur.fetchone()['n']:
        cur.execute("ALTER TABLE kb_image ADD COLUMN deleted TINYINT NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE kb_image ADD COLUMN deleted_at DATETIME NULL")
        cur.execute("ALTER TABLE kb_image ADD KEY idx_img_del (deleted, deleted_at)")
    con.commit()
    con.close()


def q(sql, args=None, one=False):
    for attempt in (0, 1):
        con = conn()
        try:
            cur = con.cursor()
            cur.execute(sql, args)
            rows = cur.fetchall()
            con.close()
            return (rows[0] if rows else None) if one else rows
        except pymysql.err.OperationalError:
            con.close()          # 归还/重连，再试一次（远程库偶发断连）
            if attempt:
                raise
        except Exception:
            # 其他异常（唯一键/语法错等）也必须归还连接：否则事务不结束，
            # 行锁会一直挂到超时，把后续写操作全卡死
            try:
                con.rollback()
            except Exception:
                pass
            con.close()
            raise


def ex(sql, args=None):
    for attempt in (0, 1):
        con = conn()
        try:
            cur = con.cursor()
            cur.execute(sql, args)
            rid = cur.lastrowid or cur.rowcount
            con.commit()
            con.close()
            return rid
        except pymysql.err.OperationalError:
            try:
                con.rollback()
            except Exception:
                pass
            con.close()
            if attempt:
                raise
        except Exception:
            # 同上：IntegrityError 等异常路径不能把连接连同事务留在池外
            try:
                con.rollback()
            except Exception:
                pass
            con.close()
            raise


def jload(v):
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    try:
        return json.loads(v)
    except Exception:
        return {}


def jdump(v):
    return json.dumps(v, ensure_ascii=False) if v is not None else None


_AUDIT_Q = queue.Queue()
_AUDIT_LOCK = threading.Lock()
_AUDIT_STARTED = False


def _audit_worker():
    """审计落库走后台批量写：单元格保存不再等一次远程 INSERT+COMMIT。"""
    while True:
        first = _AUDIT_Q.get()
        batch = [first]
        deadline = time.monotonic() + 0.25
        while len(batch) < 50:
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                break
            try:
                batch.append(_AUDIT_Q.get(timeout=timeout))
            except queue.Empty:
                break
        con = None
        try:
            con = conn()
            cur = con.cursor()
            cur.executemany(
                "INSERT INTO kb_audit(table_id,row_id,action,actor,detail) VALUES(%s,%s,%s,%s,%s)",
                batch)
            con.commit()
        except Exception as e:
            if con is not None:
                try:
                    con.rollback()
                except Exception:
                    pass
            import sys
            print(f'[kb] audit batch failed: {e}', file=sys.stderr)
        finally:
            if con is not None:
                con.close()
            for _ in batch:
                _AUDIT_Q.task_done()


def _ensure_audit_worker():
    global _AUDIT_STARTED
    if _AUDIT_STARTED:
        return
    with _AUDIT_LOCK:
        if not _AUDIT_STARTED:
            threading.Thread(target=_audit_worker, name='kb-audit', daemon=True).start()
            _AUDIT_STARTED = True


def flush_audit(timeout=3.0):
    """读变动历史/退出前把队列写完，保证读得到刚发生的操作。"""
    if not _AUDIT_STARTED:
        return
    end = time.monotonic() + timeout
    while _AUDIT_Q.unfinished_tasks and time.monotonic() < end:
        time.sleep(0.02)


def audit(tid, rid, action, detail=None, actor=''):
    try:
        _ensure_audit_worker()
        _AUDIT_Q.put((tid, rid, action, actor, jdump(detail)))
    except Exception as e:
        import sys
        print(f'[kb] audit enqueue failed: {e}', file=sys.stderr)


atexit.register(lambda: flush_audit(3.0))


def slug(s):
    s = (str(s) if s is not None else '').strip()      # 请求体里的 key 可能是数字 / 对象：先当文本处理
    s = re.sub(r"[\s'\"`\\/]+", '_', s)
    return s[:120] or 'col'


def setting_get(k, default=None):
    r = q("SELECT v FROM kb_setting WHERE k=%s", (k,), one=True)
    return r['v'] if r else default


def setting_set(k, v):
    ex("INSERT INTO kb_setting(k,v) VALUES(%s,%s) ON DUPLICATE KEY UPDATE v=VALUES(v)",
       (k, str(v)[:255]))


# ---------------- 内容安全策略（CSP） ----------------
# 网页是一个带内联脚本的单文件。与其放行所有内联脚本（'unsafe-inline' 等于没有 XSS 防线），
# 不如把页面里每个 <script> 块的 sha256 算出来放进策略：页面里多出任何一段脚本都不会被执行。
# 样式（style-src）暂时保留 'unsafe-inline'：界面大量使用内联 style，它不能执行脚本。
# 万一策略把页面拦住了，设环境变量 KB_CSP_UNSAFE_INLINE=1 可一键退回旧行为。
_CSP_BASE = ("default-src 'self'; img-src 'self' data: blob: https:; "
             "style-src 'self' 'unsafe-inline'; script-src 'self'%s; "
             "connect-src 'self'; font-src 'self' data:; object-src 'none'; "
             "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
_SCRIPT_RE = re.compile(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', re.S | re.I)
# 写在标签上的事件（<button onclick="...">）和 javascript: 链接：只认脚本哈希的策略会把它们拦掉 ——
# 表现是按钮点了没反应、页面不报错。脚本里拼出来的这类 HTML 字符串（'<button onclick="...">'）插入页面后同样被拦。
_TAG_HANDLER_RE = re.compile(r'<[a-z][^<>]*\son[a-z]+\s*=|\shref\s*=\s*["\']?\s*javascript\s*:', re.I)
_STR_HANDLER_RE = re.compile(r'<[a-z][^<>]{0,400}\son[a-z]+\s*=\s*["\']', re.I)


def csp_inline_hashes(html):
    """页面里所有内联 <script> 的 sha256（CSP 使用的格式）。
    浏览器解析 HTML 时会先把换行统一成 LF，所以这里按统一后的文本算。"""
    text = html if isinstance(html, str) else html.decode('utf-8', 'replace')
    out = []
    for m in _SCRIPT_RE.finditer(text):
        code = m.group(1).replace('\r\n', '\n').replace('\r', '\n')
        out.append("'sha256-%s'" % base64.b64encode(hashlib.sha256(code.encode('utf-8')).digest()).decode())
    return out


def csp_inline_problem(html):
    """页面里有会被“只认脚本哈希”的策略拦掉的写法时，返回一段说明；没有就返回 None。"""
    text = html if isinstance(html, str) else html.decode('utf-8', 'replace')
    m = _TAG_HANDLER_RE.search(_SCRIPT_RE.sub('', text)) or _STR_HANDLER_RE.search(text)
    if m:
        return '标签上有内联事件或 javascript: 链接：' + text[max(0, m.start() - 10):m.end() + 30].replace('\r', '').replace('\n', ' ')
    return None


def csp_for_html(html=None):
    """html=None：不带哈希的严格策略（JSON 接口等非页面响应用它兜底）。"""
    if os.environ.get('KB_CSP_UNSAFE_INLINE') == '1':
        return _CSP_BASE % " 'unsafe-inline'"
    if html is None:
        return _CSP_BASE % ''
    hashes = csp_inline_hashes(html)
    text = html if isinstance(html, str) else html.decode('utf-8', 'replace')
    if not hashes and '<script' in text:
        # 页面里有脚本却一段都没识别出来：宁可退回旧行为，也不能把页面拦成白屏
        why = '页面里有脚本但没识别出内联脚本块'
    else:
        # 按钮的事件要写在脚本里（addEventListener / .onclick = ...）。哪天有人又写成 onclick="..."，
        # 与其让按钮在用户手里点了没反应，不如这个页面退回旧行为，并在日志里喊出来。
        why = csp_inline_problem(text)
    if why:
        print('[CSP] 警告：%s；这个页面退回 unsafe-inline（旧行为）' % why, file=sys.stderr, flush=True)
        return _CSP_BASE % " 'unsafe-inline'"
    return _CSP_BASE % ((' ' + ' '.join(hashes)) if hashes else '')


# ---------------- 列定义 ----------------

def columns(tid, include_hidden=False):
    sql = ("SELECT id,`key`,label,type,options,option_colors,expr,width,sort,required,hidden "
           "FROM kb_column WHERE table_id=%s AND deleted=0")
    if include_hidden:
        sql += " ORDER BY sort,id"
        rows = q(sql, (tid,))
    else:
        sql += " AND hidden=0 ORDER BY sort,id"
        rows = q(sql, (tid,))
    out = []
    for r in rows:
        o = r['options']
        if isinstance(o, str):
            try:
                o = json.loads(o)
            except Exception:
                o = None
        oc = r.get('option_colors')
        if isinstance(oc, str):
            try:
                oc = json.loads(oc)
            except Exception:
                oc = None
        out.append({**r, 'options': o, 'option_colors': oc, 'required': bool(r['required']),
                    'hidden': bool(r['hidden'])})
    return out


# 单元格大小上限：整张表的行常驻内存，没有上限的话一格就能撑爆缓存和数据库行。
# 现有数据里最大的单元格不到 700 字，2 万字的上限余量很大。
CELL_MAX_TEXT = 20000
CELL_MAX_OPTION = 500
CELL_MAX_MULTI = 200
CELL_MAX_IMAGE = 2000


def norm_val(col, v):
    """按列类型规范化单元格值（对齐参考项目 _norm_val）；超限抛 ValueError（接口转成 400）"""
    t = col['type']
    if v is None or v == '':
        return None
    if t == 'number':
        s = str(v).strip().replace(',', '').replace('，', '')
        m = re.fullmatch(r'(-?\d+(?:\.\d+)?)\s*(亿|万|千|w|W|k|K)?%?', s)
        if m:
            n = float(m.group(1))
            n *= {'亿': 1e8, '万': 1e4, 'w': 1e4, 'W': 1e4,
                  '千': 1e3, 'k': 1e3, 'K': 1e3}.get(m.group(2) or '', 1)
            return int(n) if n.is_integer() else n
        try:
            f = float(s)
            return int(f) if f == int(f) else f
        except Exception:
            return None
    if t == 'checkbox':
        if isinstance(v, bool):
            return v
        return str(v).lower() in ('1', 'true', 'yes', 'on', '是')
    if t == 'multiselect':
        arr = [str(x) for x in v] if isinstance(v, (list, tuple)) else [str(v)]
        if len(arr) > CELL_MAX_MULTI:
            raise ValueError(f"列「{col['label']}」最多选 {CELL_MAX_MULTI} 项（现在是 {len(arr)} 项）")
        if any(len(x) > CELL_MAX_OPTION for x in arr):
            raise ValueError(f"列「{col['label']}」每个选项不能超过 {CELL_MAX_OPTION} 字")
        return arr
    if t == 'select':
        s = str(v)
        if len(s) > CELL_MAX_OPTION:
            raise ValueError(f"列「{col['label']}」的选项值不能超过 {CELL_MAX_OPTION} 字")
        opts = col.get('options') or []
        if opts and s not in opts:
            raise ValueError(f"列「{col['label']}」的值「{s}」不在可选项内")
        return s
    if t == 'date':
        m = re.match(r'^(\d{4}-\d{2}-\d{2})', str(v).strip())
        return m.group(1) if m else None
    if t == 'formula':
        return None          # 公式列不落库
    s = str(v)
    limit = CELL_MAX_IMAGE if t == 'image' else CELL_MAX_TEXT
    if len(s) > limit:
        raise ValueError(f"列「{col['label']}」的内容不能超过 {limit} 字（现在是 {len(s)} 字）")
    return s


# ---------------- 公式求值（AST 白名单，不用 eval） ----------------

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}
_CMP = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Gt: operator.gt,
    ast.GtE: operator.ge, ast.Lt: operator.lt, ast.LtE: operator.le,
}
def _pdate(v):
    """把 'YYYY-MM-DD…' 字符串/None 解析成 datetime；解析失败返回 None。"""
    if v is None or v == '':
        return None
    try:
        return datetime.strptime(str(v).strip()[:10], '%Y-%m-%d')
    except Exception:
        return None


def _truth(v):
    return bool(v)


TEXT_MAX_RESULT = 20000     # 公式算出来的文本结果上限


def _cap_str(s):
    return s if len(s) <= TEXT_MAX_RESULT else s[:TEXT_MAX_RESULT]


def _concat2(a, b):
    return _cap_str(('' if a is None else str(a)) + ('' if b is None else str(b)))


# ---------------- 正则安全检查 ----------------
# Python 的 re 没有超时，而且回溯期间不释放 GIL：一个灾难性回溯的正则会让「整个服务」卡死，
# 不是只占一个线程。所以 REGEXEXTRACT 的模式在建公式时就做结构检查：
#   拒绝「大次数量词里面又套了可变长度内容」，以及量词总数过多的写法。
# 现网用到的 \d{15,}、https://[^\s]+ 这类单层量词不受影响。
try:                                     # Python 3.11+
    from re import _parser as _sre_parse, _constants as _sre_c
except ImportError:                      # pragma: no cover  更老的 Python
    import sre_parse as _sre_parse       # type: ignore
    import sre_constants as _sre_c       # type: ignore

_REPEAT_OPS = tuple(op for op in (_sre_c.MAX_REPEAT, _sre_c.MIN_REPEAT,
                                  getattr(_sre_c, 'POSSESSIVE_REPEAT', None)) if op is not None)
_ATOMIC_GROUP = getattr(_sre_c, 'ATOMIC_GROUP', None)
_BIG_REPEAT = 10              # 次数达到这个值（或不限次数）的量词，里面不能再套可变长度的内容
_REGEX_MAX_PATTERN = 200
_REGEX_MAX_SUBJECT = 2000    # 只在内容的前 2000 字里找
_REGEX_MAX_QUANT = 10         # 全文可变长度的量词总数（相邻的多个 .* 会成倍回溯）
_REGEX_VERDICT = {}           # 模式 -> 不安全的原因（'' 表示安全）


def _fixed_width(sp):
    """这段子表达式是否永远恰好匹配 N 个字符；长度不固定返回 None。"""
    total = 0
    for op, av in sp:
        if op in (_sre_c.LITERAL, _sre_c.NOT_LITERAL, _sre_c.IN, _sre_c.ANY):
            total += 1
        elif op is _sre_c.AT or op in (_sre_c.ASSERT, _sre_c.ASSERT_NOT):
            pass
        elif op in _REPEAT_OPS:
            lo, hi, sub = av
            n = _fixed_width(sub)
            if lo != hi or n is None:
                return None
            total += lo * n
        elif op is _sre_c.SUBPATTERN:
            n = _fixed_width(av[3])
            if n is None:
                return None
            total += n
        elif op is _sre_c.BRANCH:
            ns = [_fixed_width(a) for a in av[1]]
            if not ns or None in ns or len(set(ns)) != 1:
                return None
            total += ns[0]
        else:
            return None
    return total


def _risky_inside(sp):
    """大次数量词里面不允许出现的东西：可变长度的量词、长度不一致的分组选项、反向引用、环视。"""
    for op, av in sp:
        if op in _REPEAT_OPS:
            lo, hi, sub = av
            if lo != hi or _risky_inside(sub):
                return True
        elif op is _sre_c.BRANCH:
            ns = [_fixed_width(a) for a in av[1]]
            if not ns or None in ns or len(set(ns)) != 1:
                return True
        elif op is _sre_c.SUBPATTERN:
            if _risky_inside(av[3]):
                return True
        elif op in (_sre_c.GROUPREF, _sre_c.GROUPREF_EXISTS, _sre_c.ASSERT, _sre_c.ASSERT_NOT):
            return True
        elif _ATOMIC_GROUP is not None and op is _ATOMIC_GROUP:
            if _risky_inside(av):
                return True
    return False


def _count_quant(sp):
    n = 0
    for op, av in sp:
        if op in _REPEAT_OPS:
            if av[0] != av[1]:
                n += 1
            n += _count_quant(av[2])
        elif op is _sre_c.SUBPATTERN:
            n += _count_quant(av[3])
        elif op is _sre_c.BRANCH:
            n += sum(_count_quant(a) for a in av[1])
    return n


def _find_big_repeat(sp):
    for op, av in sp:
        if op in _REPEAT_OPS:
            lo, hi, sub = av
            if (hi >= _BIG_REPEAT) and _risky_inside(sub):
                return True
            if _find_big_repeat(sub):
                return True
        elif op is _sre_c.SUBPATTERN:
            if _find_big_repeat(av[3]):
                return True
        elif op is _sre_c.BRANCH:
            if any(_find_big_repeat(a) for a in av[1]):
                return True
    return False


def regex_problem(pattern):
    """检查一个正则能不能安全地在服务端执行；安全返回 None，否则返回原因（文本）。"""
    key = pattern if isinstance(pattern, str) else str(pattern)
    hit = _REGEX_VERDICT.get(key)
    if hit is not None:
        return hit or None
    reason = ''
    if len(key) > _REGEX_MAX_PATTERN:
        reason = f'正则不能超过 {_REGEX_MAX_PATTERN} 字'
    else:
        try:
            tree = _sre_parse.parse(key)
        except Exception:
            reason = '正则写法有误'
        else:
            if _count_quant(tree) > _REGEX_MAX_QUANT:
                reason = f'量词太多（最多 {_REGEX_MAX_QUANT} 个）'
            elif _find_big_repeat(tree):
                reason = ('量词里又套了可变长度的内容，遇到不匹配的文本会无限回溯；'
                          '请改写，例如用 [0-9]+ 代替 ([0-9]+)')
    if len(_REGEX_VERDICT) > 4096:
        _REGEX_VERDICT.clear()
    _REGEX_VERDICT[key] = reason
    return reason or None


def _regex_extract(s, pattern):
    """REGEXEXTRACT：返回第一个匹配；空值/非法或有风险的正则/无匹配返回 None。"""
    if s is None:
        return None
    if regex_problem(pattern):
        return None
    try:
        m = re.search(str(pattern), str(s)[:_REGEX_MAX_SUBJECT])
        return m.group(0) if m else None
    except re.error:
        return None


def _rept(s, n):
    """REPT：重复次数封顶，结果不超过 TEXT_MAX_RESULT 个字符。"""
    if s is None:
        return ''
    base = str(s)[:TEXT_MAX_RESULT]
    try:
        times = max(0, int(n))
    except Exception:
        times = 0
    if not base:
        return ''
    return base * min(times, TEXT_MAX_RESULT // len(base))


_FUNCS = {
    'ROUND': lambda x, n=2: round(float(x), int(n)),
    'ABS': lambda x: abs(float(x)),
    'MAX': lambda *a: max(float(x) for x in a),
    'MIN': lambda *a: min(float(x) for x in a),
    'SUM': lambda *a: sum(float(x) for x in a),
    'IF': lambda c, a, b: a if c else b,
    'INT': lambda x: int(float(x)),
    'CEIL': lambda x: math.ceil(float(x)),
    'FLOOR': lambda x: math.floor(float(x)),
    'LEN': lambda s: len(str(s)),
    'ISBLANK': lambda x: x is None or str(x) == '',
    'CONCAT': lambda *a: _cap_str(''.join('' if x is None else str(x) for x in a)),
    # —— 逻辑 ——
    'AND': lambda *a: all(_truth(x) for x in a),
    'OR': lambda *a: any(_truth(x) for x in a),
    'NOT': lambda x: not _truth(x),
    'TRUE': True,
    'FALSE': False,
    # —— 数学补充 ——
    'MOD': lambda a, b: (float(a) % float(b)) if b else None,
    'POWER': lambda a, b: float(a) ** float(b),
    'SQRT': lambda x: math.sqrt(float(x)) if float(x) >= 0 else None,
    'ROUNDUP': lambda x, n=0: math.ceil(float(x) * (10 ** int(n))) / (10 ** int(n)) if x is not None else None,
    'ROUNDDOWN': lambda x, n=0: math.floor(float(x) * (10 ** int(n))) / (10 ** int(n)) if x is not None else None,
    'AVERAGE': lambda *a: (lambda vs: sum(vs) / len(vs) if vs else None)(
        [float(x) for x in a if x is not None and str(x) != '']),
    'COUNTA': lambda *a: sum(1 for x in a if x is not None and str(x) != ''),
    'ISNUMBER': lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
    'ISTEXT': lambda x: isinstance(x, str),
    # —— 文本 ——
    'LEFT': lambda s, n=1: ('' if s is None else str(s))[:max(0, int(n))],
    'RIGHT': lambda s, n=1: ('' if s is None else str(s))[-max(0, int(n)):] if n else '',
    'MID': lambda s, start, n: ('' if s is None else str(s))[max(0, int(start) - 1): max(0, int(start) - 1) + max(0, int(n))],
    'TRIM': lambda s: str(s).strip() if s is not None else '',
    'UPPER': lambda s: str(s).upper() if s is not None else '',
    'LOWER': lambda s: str(s).lower() if s is not None else '',
    'SUBSTITUTE': lambda s, old, new: _cap_str(str(s).replace(str(old), str(new))) if s is not None else '',
    'FIND': lambda sub, s: (str(s).find(str(sub)) + 1) if s is not None and str(sub) in str(s) else 0,
    'REPT': _rept,
    'CONCATENATE': lambda *a: _cap_str(''.join('' if x is None else str(x) for x in a)),
    'REGEXEXTRACT': _regex_extract,
    # —— 日期 ——
    'TODAY': lambda: datetime.now().strftime('%Y-%m-%d'),
    'NOW': lambda: datetime.now().strftime('%Y-%m-%d %H:%M'),
    'YEAR': lambda x: _pdate(x).year if _pdate(x) else None,
    'MONTH': lambda x: _pdate(x).month if _pdate(x) else None,
    'DAY': lambda x: _pdate(x).day if _pdate(x) else None,
    'DATE': lambda y, m, d: (datetime(int(y), int(m), int(d)).strftime('%Y-%m-%d')),
    'DAYS': lambda end, start: (lambda a, b: (a - b).days if a and b else None)(_pdate(end), _pdate(start)),
    'DATEDIF': lambda end, start: (lambda a, b: (a - b).days if a and b else None)(_pdate(end), _pdate(start)),
    'WEEKDAY': lambda x: _pdate(x).isoweekday() if _pdate(x) else None,
}
_FIELD_RE = re.compile(r'\{([^{}]+)\}')


def _field_refs(expr):
    """返回不在引号里的 {列名} 位置，避免把正则里的 {15,} 当成字段。"""
    refs = []
    i, n = 0, len(expr or '')
    quote = None
    while i < n:
        ch = expr[i]
        if quote:
            if ch == '\\':
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ('"', "'"):
            quote = ch
            i += 1
            continue
        if ch == '{':
            j = expr.find('}', i + 1)
            if j != -1:
                name = expr[i + 1:j]
                if name and '{' not in name and '}' not in name:
                    refs.append((i, j + 1, name))
                i = j + 1
                continue
        i += 1
    return refs


def _replace_field_refs(expr, fn):
    """只替换引号外的 {列名}；fn(name) 返回替换后的 Python 字面量文本。"""
    refs = _field_refs(expr)
    if not refs:
        return expr
    out, last = [], 0
    for start, end, name in refs:
        out.append(expr[last:start])
        out.append(fn(name))
        last = end
    out.append(expr[last:])
    return ''.join(out)


# ---------------- 跨表引用（同项目内） ----------------
# 公式里可用：
#   LOOKUP("表名","匹配字段",{当前字段},"取值字段")
#   SUMIF / AVGIF / MAXIF / MINIF("表名","匹配字段",{当前字段},"取值字段")
#   COUNTIF("表名","匹配字段",{当前字段})
# 表名 = kb_table.name（含「组·Sheet」），按匹配字段值相等的行取值/汇总。
XT_FUNCS = ('LOOKUP', 'SUMIF', 'COUNTIF', 'AVGIF', 'MAXIF', 'MINIF')

_XT = {}
_XT_TABLE = {}
_XT_INDEX = {}
_XT_VER = {}                  # table_id -> 该表的失效版本
_XT_GEN = 0                   # 全局失效版本（结构变更时递增）
_XT_LOCK = threading.Lock()   # 重建索引时单飞：同一时刻只有一个请求查库重建


def _xt_stamp(tid):
    """跨表缓存的“代次”：读取期间只要发生过失效，读到的就不能再放进缓存。"""
    return (_XT_GEN, _XT_VER.get(int(tid), 0))


def clear_xt():
    global _XT_GEN
    _XT_GEN += 1
    _XT.clear()
    _XT_TABLE.clear()
    _XT_INDEX.clear()


def invalidate_xt_table(tid):
    """某一张表的行变了：只丢这一张表的跨表数据与索引，别的表的索引照常复用。"""
    tid = int(tid)
    _XT_VER[tid] = _XT_VER.get(tid, 0) + 1
    _XT.pop(tid, None)
    for key in list(_XT_INDEX):       # 先整体取快照：工作线程可能正在往里写
        if key[0] == tid:
            _XT_INDEX.pop(key, None)


def _xt_table(name, project=None):
    name = (name or '').strip()
    if not name:
        return None
    key = (name, project or '')
    if key in _XT_TABLE:
        return _XT_TABLE[key]
    t = q("SELECT id,name,project FROM kb_table WHERE name=%s AND deleted=0", (name,), one=True)
    if not t:
        t = q("SELECT id,name,project FROM kb_table WHERE group_name=%s AND deleted=0 "
              "ORDER BY sort,id", (name,), one=True)
    if not t or (project and (t.get('project') or '') != project):
        t = None
    _XT_TABLE[key] = t
    return t


def _xt_rows(tid, stamp=None):
    if tid in _XT:
        return _XT[tid]
    if stamp is None:
        stamp = _xt_stamp(tid)
    cols = q("SELECT `key`,label FROM kb_column WHERE table_id=%s AND deleted=0 "
             "ORDER BY sort,id", (tid,))
    rows = q("SELECT data FROM kb_row WHERE table_id=%s AND deleted=0 ORDER BY sort,id", (tid,))
    out = []
    for r in rows:
        d = jload(r['data']) or {}
        out.append({c['label']: d.get(c['key']) for c in cols})
    if _xt_stamp(tid) == stamp:
        _XT[tid] = out
    return out


def _xt_index(tid, field):
    """按 (table_id, 字段) 建值索引；SUMIF/LOOKUP 不再每行扫描全表。"""
    f = str(field).strip()
    key = (tid, f)
    if key in _XT_INDEX:
        return _XT_INDEX[key]
    with _XT_LOCK:
        if key in _XT_INDEX:           # 排队期间别的请求已经建好
            return _XT_INDEX[key]
        stamp = _xt_stamp(tid)
        idx = {}
        for r in _xt_rows(tid, stamp):
            v = r.get(f)
            s = '' if v is None else str(v).strip()
            idx.setdefault(s, []).append(r)
        if _xt_stamp(tid) == stamp:
            _XT_INDEX[key] = idx
    return idx



def _xt_funcs(project):
    def _target(name):
        t = _xt_table(name, project)
        if not t:
            raise ValueError(f'引用的表「{name}」不存在或不在当前项目')
        return t

    def _matches(name, field, value):
        t = _target(name)
        f = str(field).strip()
        v = '' if value is None else str(value).strip()
        return _xt_index(t['id'], f).get(v, [])

    def LOOKUP(name, field, value, take):
        take = str(take).strip()
        for r in _matches(name, field, value):
            v = r.get(take)
            if v not in (None, ''):
                return v
        return None

    def SUMIF(name, field, value, take):
        take = str(take).strip()
        return sum(_tonum(r.get(take)) for r in _matches(name, field, value))

    def COUNTIF(name, field, value):
        return len(_matches(name, field, value))

    def AVGIF(name, field, value, take):
        take = str(take).strip()
        vals = [_tonum(r.get(take)) for r in _matches(name, field, value)]
        return (sum(vals) / len(vals)) if vals else None

    def MAXIF(name, field, value, take):
        take = str(take).strip()
        vals = [_tonum(r.get(take)) for r in _matches(name, field, value)]
        return max(vals) if vals else None

    def MINIF(name, field, value, take):
        take = str(take).strip()
        vals = [_tonum(r.get(take)) for r in _matches(name, field, value)]
        return min(vals) if vals else None

    return {'LOOKUP': LOOKUP, 'SUMIF': SUMIF, 'COUNTIF': COUNTIF,
            'AVGIF': AVGIF, 'MAXIF': MAXIF, 'MINIF': MINIF}


def _tonum(v):
    if v is None or v == '':
        return 0
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip().replace(',', '')
    m = re.fullmatch(r'(-?\d+(?:\.\d+)?)([wW万]?)(%?)', s)
    if m:
        n = float(m.group(1))
        if m.group(2):
            n *= 10000
        if m.group(3):
            n /= 100
        return n
    try:
        return float(s)
    except Exception:
        return 0


_EXPR_MAX = 4000          # 单条公式的长度上限
_TPL_CACHE = {}           # 公式原文 -> (语法树, 依次出现的字段名)
_FUNCS_CACHE = {}         # project -> 完整函数表


def _field_value(v):
    """单元格值代入公式时的形态：空值 -> None（算术里当 0、比较一律 False、ISBLANK 为真），
    勾选 -> 1/0，数字原样，纯数字文本（含 1.2w / 12%）按数值，其余按文本。"""
    if v is None or v == '':
        return None
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    # 19 位订单号/视频ID 等长数字必须保持字符串，否则 float 会丢精度，COUNTIF 就匹配不到了
    if re.fullmatch(r'-?[\d,.]+([wW万]|%)?', s) and len(re.sub(r'\D', '', s)) <= 15:
        return _tonum(v)
    return s


def _compile_expr(expr):
    """一条公式只解析一次：{列名} 换成占位符名 __KBF0__、__KBF1__…，
    逐行求值时只往 env 里填值，不再每格都替换文本、重新解析。"""
    names = []

    def placeholder(name):
        names.append(name.strip())
        return '__KBF%d__' % (len(names) - 1)

    # Excel 风格单等号 → Python ==（避免误伤 == != >= <=）
    raw = re.sub(r'(?<![=!<>])=(?!=)', '==', expr.strip())
    tree = ast.parse(_replace_field_refs(raw, placeholder), mode='eval')
    if len(_TPL_CACHE) > 4096:
        _TPL_CACHE.clear()
    _TPL_CACHE[expr] = hit = (tree, names)
    return hit


def _funcs_for(project):
    """完整函数表：内置函数 + 该项目的跨表函数（按项目缓存，不必每格重建）。"""
    key = '' if project is None else project
    f = _FUNCS_CACHE.get(key)
    if f is None:
        f = dict(_FUNCS)
        if project is not None:
            f.update(_xt_funcs(project))
        if len(_FUNCS_CACHE) > 256:
            _FUNCS_CACHE.clear()
        _FUNCS_CACHE[key] = f
    return f


def eval_formula(expr, row, project=None):
    """用受限 AST 求值：{列名} 取同行值。出错返回 None。
    传 project 时可用跨表函数（LOOKUP/SUMIF/COUNTIF/AVGIF/MAXIF/MINIF）。"""
    if not expr or not expr.strip() or len(expr) > _EXPR_MAX:
        return None
    try:
        tree, names = _TPL_CACHE.get(expr) or _compile_expr(expr)
        env = {'__KBF%d__' % i: _field_value(row.get(nm)) for i, nm in enumerate(names)}
        funcs = _funcs_for(project)

        def walk(node):
            if isinstance(node, ast.Expression):
                return walk(node.body)
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
                return _OPS[type(node.op)](walk(node.operand))
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitXor):
                a, b = walk(node.left), walk(node.right)   # Excel 的 ^ 幂
                y = 0 if b is None else b
                if abs(y) > 20:
                    return None
                return operator.pow(0 if a is None else a, y)
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitAnd):
                a, b = walk(node.left), walk(node.right)   # Excel 的 & 文本连接
                return _concat2(a, b)
            if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
                a, b = walk(node.left), walk(node.right)
                if isinstance(node.op, ast.Div):
                    if b is None or b == 0:
                        raise ZeroDivisionError()   # 交给 IFERROR / 整体置空
                    return operator.truediv(0 if a is None else a, b)
                if isinstance(node.op, ast.Mod):
                    if b is None or b == 0:
                        raise ZeroDivisionError()
                    return operator.mod(0 if a is None else a, b)
                if isinstance(node.op, ast.Pow):
                    y = 0 if b is None else b
                    if abs(y) > 20:
                        return None
                    return operator.pow(0 if a is None else a, y)
                # 其余算术：空值按 0
                a, b = 0 if a is None else a, 0 if b is None else b
                if isinstance(a, str) or isinstance(b, str):
                    # 文本 + 文本一直是直接拼接，保持不变；其余「文本参与算术」一律按出错处理——
                    # 尤其 文本*数字 会一次分配巨大的字符串
                    if isinstance(a, str) and isinstance(b, str) and isinstance(node.op, ast.Add):
                        return _cap_str(a + b)
                    raise ValueError('文本不能参与算术运算')
                return _OPS[type(node.op)](a, b)
            if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _CMP:
                a, b = walk(node.left), walk(node.comparators[0])
                if a is None or b is None:
                    return False                    # 空值参与比较一律 False，用 ISBLANK 判空
                if isinstance(a, str) and isinstance(b, str):
                    a, b = a.strip().lower(), b.strip().lower()
                return _CMP[type(node.ops[0])](a, b)
            if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
                vals = [_truth(walk(v)) for v in node.values]
                return all(vals) if isinstance(node.op, ast.And) else any(vals)
            if isinstance(node, ast.IfExp):
                return walk(node.body) if _truth(walk(node.test)) else walk(node.orelse)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                fn = node.func.id.upper()
                if fn == 'IFERROR':                 # 惰性：第一个参数报错/为空才取第二个
                    try:
                        v = walk(node.args[0])
                    except Exception:
                        v = None
                    if v is None or v == '':
                        return walk(node.args[1]) if len(node.args) > 1 else None
                    return v
                f = funcs.get(fn)
                if not f:
                    raise ValueError('未知函数 ' + node.func.id)
                args = [walk(a) for a in node.args]
                return f(*args)
            if isinstance(node, ast.Name):
                if node.id in env:
                    return env[node.id]             # 公式里的 {列名}
                nm = node.id.upper()
                if nm == '__KB_NONE__':
                    return None
                if nm in ('TRUE', 'FALSE'):
                    return nm == 'TRUE'
                return 0
            raise ValueError('不支持的表达式')

        val = walk(tree)
        if val is None:
            return None
        if isinstance(val, float):
            if math.isnan(val) or math.isinf(val):
                return None
            if abs(val - int(val)) < 1e-12:
                return int(val)
            return round(val, 6)
        return val
    except Exception:
        return None


def compute_formulas(row, cols, project=None):
    """按列顺序依次计算 formula 列（后列可引用前列结果），结果合并进行数据。"""
    out = dict(row)
    for c in cols:
        if c['type'] == 'formula':
            out[c['key']] = eval_formula(c['expr'], out, project)
    return out


def validate_expr(expr, cols, project=None, xt_check=None):
    """建/改公式列时校验：引用列存在、跨表函数参数合法、语法可解析。
    xt_check(table_id) -> bool：给非管理员作者用的，跨表函数引用的每一张表都要通过它。"""
    if not isinstance(expr, str):
        return False, '公式必须是文本'
    if not expr or not expr.strip():
        return False, '公式不能为空'
    if len(expr) > _EXPR_MAX:
        return False, f'公式太长（最多 {_EXPR_MAX} 字符）'
    known = {c['label'] for c in cols} | {c['key'] for c in cols}
    refs = [name for _s, _e, name in _field_refs(expr)]
    for r in refs:
        if r.strip() not in known:
            return False, f'引用了不存在的列「{r}」'
    try:
        raw = re.sub(r'(?<![=!<>])=(?!=)', '==', expr.strip())
        probe_src = _replace_field_refs(raw, lambda name: "'x'")
        tree = ast.parse(probe_src, mode='eval')
    except SyntaxError:
        return False, '公式语法错误'

    xt_used = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        fn = node.func.id.upper()
        if fn not in _FUNCS and fn not in XT_FUNCS and fn != 'IFERROR':
            return False, f'不支持的函数 {node.func.id}'
        if fn == 'REGEXEXTRACT':
            # 正则在任何求值之前先做结构检查：Python 的 re 没有超时，坏正则会卡死整个服务
            a1 = node.args[1] if len(node.args) > 1 else None
            if not (isinstance(a1, ast.Constant) and isinstance(a1.value, str)):
                return False, 'REGEXEXTRACT 的正则要用双引号写死'
            prob = regex_problem(a1.value)
            if prob:
                return False, f'正则有风险，可能卡死服务：{prob}'
        if fn not in XT_FUNCS:
            continue
        xt_used = True
        args = node.args

        def lit(i):
            if i < len(args) and isinstance(args[i], ast.Constant) and isinstance(args[i].value, str):
                return args[i].value
            return None

        tname, mfield, take = lit(0), lit(1), lit(3)
        if not tname or not mfield:
            return False, (f'{fn} 的表名、匹配字段要用双引号写死，'
                           f'例如 LOOKUP("达人建联表-抖音·Sheet1","达人昵称",{{达人昵称}},"粉丝数")')
        t = _xt_table(tname, project)
        if not t:
            return False, f'引用的表「{tname}」不存在或不在当前项目'
        if xt_check is not None and not xt_check(t['id']):
            return False, f'没有权限引用表「{tname}」'
        tcols = {c['label'] for c in q(
            "SELECT label FROM kb_column WHERE table_id=%s AND deleted=0", (t['id'],))}
        if mfield not in tcols:
            return False, f'表「{tname}」没有字段「{mfield}」'
        if fn != 'COUNTIF':
            if not take:
                return False, f'{fn} 还需要第 4 个参数：取值字段（双引号，例如 "粉丝数"）'
            if take not in tcols:
                return False, f'表「{tname}」没有字段「{take}」'

    if xt_used:
        return True, ''          # 跨表结果依赖数据，建列时不做数值试算

    probe = {c['key']: 1 for c in cols}
    for c in cols:
        if c.get('type') == 'formula':
            probe[c['key']] = 1
    if eval_formula(expr, probe, project) is None:
        return False, '公式语法错误或使用了不支持的函数'
    return True, ''
