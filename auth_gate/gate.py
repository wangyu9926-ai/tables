# -*- coding: utf-8 -*-
"""登录 / 权限层（独立账号体系，不再与 8555 共用表）。

  - 未登录：一律不放行；访问 `/` 直接返回登录页，所有 `/api/*` 401，不暴露项目列表。
  - staff：只能看/改被授权的项目或表；viewer 只读，editor 可增删改行。
  - admin：全部权限 + 建表/改表/管列/项目 + 用户与授权管理。
  - 凭证：HttpOnly Cookie（SameSite=Lax，云端 Secure）+ 兼容 Authorization: Bearer。
    签名 HMAC-SHA256，密钥 auth_gate/secret.key（云端用 GATE_SECRET_PATH 指到 $DATA_DIR）。
    有效期 12h；登录 5 次失败锁 120s，同一 IP 20 次失败锁 10 分钟，同一账号 15 次失败锁 10 分钟。
    改密码 / 被重置密码后，旧凭证立即失效（kb_user.token_ver）。
  - 写请求只接受本站页面发起的（Sec-Fetch-Site / Origin 校验）和 JSON 请求体：
    同一个托管域名下的其它站点带着 Cookie 也伪造不了请求。
  - 内容安全策略：内联脚本按 sha256 放行（不再 'unsafe-inline'）；
    万一策略把页面拦住了，设环境变量 KB_CSP_UNSAFE_INLINE=1 退回旧行为。
  - 审计：把 backend.app.audit 包一层，写操作自动记录登录账号。

启动：python auth_gate/run_gate.py（默认 0.0.0.0:8614，可用 GATE_HOST/GATE_PORT 覆盖）
"""
import base64
import contextvars
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

GATE_DIR = Path(__file__).resolve().parent
GATE_DATA_DIR = Path(os.environ.get('GATE_DATA_DIR') or GATE_DIR)
PROJECT_ROOT = GATE_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

import backend.app as _bapp                 # noqa: E402  原应用（原样加载，不修改）
from backend.app import app                 # noqa: E402
from backend.engine import ex, q, setting_get, setting_set, csp_for_html   # noqa: E402  复用原连接池

COOKIE = 'kb_gate'
TOKEN_TTL = 12 * 3600
_SECRET_PATH = Path(os.environ.get('GATE_SECRET_PATH') or (GATE_DATA_DIR / 'secret.key'))
_COOKIE_SECURE = os.environ.get('GATE_COOKIE_SECURE') == '1'
_GATE_HTTPS = os.environ.get('GATE_HTTPS') == '1'
_ORIGIN_CHECK = os.environ.get('GATE_ORIGIN_CHECK') != '0'      # 老浏览器没有 Sec-Fetch-Site 时的兜底
_MAX_BODY = int(os.environ.get('GATE_MAX_BODY_MB', '25')) * 1024 * 1024   # 导入上限 20MB + 表单开销
_ctx_user = contextvars.ContextVar('gate_current_user', default=None)
# 登录态短缓存：key=(uid, username)，账号变更时同进程立即清空
_ACCT_CACHE = {}
_ACCT_TTL = float(os.environ.get('GATE_ACCOUNT_CACHE_TTL', '1.5'))


def clear_account_cache():
    _ACCT_CACHE.clear()

# ---------------- 密钥 ----------------

def _load_secret() -> bytes:
    try:
        s = _SECRET_PATH.read_bytes().strip()
        if len(s) >= 32:
            return s
    except OSError:
        pass
    s = secrets.token_urlsafe(48).encode()
    try:
        _SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SECRET_PATH.write_bytes(s)
    except OSError as e:
        print(f'[gate] 无法写入密钥 {_SECRET_PATH}: {e}', flush=True)
    return s


SECRET = _load_secret()

# ---------------- 令牌（HMAC-SHA256，纯标准库） ----------------

def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def issue_token(uid: int, username: str, role: str, tv: int = 0) -> str:
    """tv = kb_user.token_ver：改密 / 被重置时加一，已签发的旧凭证随之失效。"""
    payload = json.dumps({'uid': uid, 'sub': username, 'role': role, 'tv': int(tv or 0),
                          'iat': int(time.time()), 'exp': int(time.time()) + TOKEN_TTL},
                         ensure_ascii=False, separators=(',', ':')).encode()
    sig = hmac.new(SECRET, payload, hashlib.sha256).digest()
    return _b64e(payload) + '.' + _b64e(sig)


def parse_token(token: str):
    try:
        pb, sb = token.split('.', 1)
        payload = _b64d(pb)
        if not hmac.compare_digest(hmac.new(SECRET, payload, hashlib.sha256).digest(), _b64d(sb)):
            return None
        p = json.loads(payload)
        if p.get('exp', 0) <= time.time() or p.get('role') not in ('admin', 'staff'):
            return None
        return p
    except Exception:
        return None

# ---------------- 账号核验（实时查 kb_user） ----------------

def verify_password(pw: str, stored: str) -> bool:
    """与 D:\\2611项目\\多维表格 mtables/store.py 同款：scrypt$/pbkdf2$ 格式串，参数内嵌。"""
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


def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.scrypt((pw or '').encode(), salt=salt.encode(), n=2 ** 14, r=8, p=1, dklen=32)
    return 'scrypt$16384$8$1$%s$%s' % (salt, dk.hex())


def live_account(payload: dict):
    """按 token 核验 kb_user：停用/删号/改角色最多 1.5 秒内生效。

    高并发下每个请求都查一次 kb_user 会把远程库 RTT 叠加到所有接口上，
    这里做 1.5s 的短缓存；账号增删改/改密时同进程立即清空缓存。
    """
    if not payload:
        return None
    key = (str(payload.get('uid')), str(payload.get('sub')))
    now = time.time()
    hit = _ACCT_CACHE.get(key)
    if hit and now - hit[0] < _ACCT_TTL:
        acct = hit[1]
    else:
        acct = q("SELECT id,username,real_name,role,status,can_fields,can_users,token_ver FROM kb_user "
                 "WHERE id=%s", (payload.get('uid'),), one=True)
        if acct:
            _ACCT_CACHE[key] = (now, acct)
            if len(_ACCT_CACHE) > 1024:
                _ACCT_CACHE.clear()
    # token_ver 对不上 = 凭证签发之后改过密码或被重置过：按失效处理
    if (not acct or acct['id'] != payload.get('uid') or acct['username'] != payload.get('sub')
            or acct['status'] != 1 or acct['role'] not in ('admin', 'staff')
            or int(acct.get('token_ver') or 0) != int(payload.get('tv') or 0)):
        return None
    return {'uid': acct['id'], 'username': acct['username'],
            'name': acct['real_name'] or acct['username'], 'role': acct['role'],
            'can_fields': bool(acct.get('can_fields')), 'can_users': bool(acct.get('can_users'))}

# ---------------- 权限规则 ----------------

_ADMIN_ONLY = [
    (r'POST',    r'^/api/tables$'),                        # 建表
    (r'PATCH|DELETE', r'^/api/tables/\d+$'),               # 改/删表
    (r'PATCH',   r'^/api/tables/\d+/rename$'),             # 重命名
    (r'POST|PATCH|DELETE', r'^/api/projects'),             # 项目增改删
]
_AUDIT_GET = re.compile(r'^/api/tables/\d+/audit$')


def _admin_only(method: str, path: str) -> bool:
    return any(re.fullmatch(m, method) and re.fullmatch(p, path) for m, p in _ADMIN_ONLY)

# ---------------- 登录限速（内存级：同 IP+账号 5 次失败锁 120s；同 IP 20 次锁 10 分钟） ----------------

_fails = {}
_ip_fails = {}


def _locked(ip: str, username: str) -> int:
    rec = _fails.get((ip, username))
    if rec and len(rec) > 2 and rec[2] > time.time():
        return int(rec[2] - time.time())
    return 0


def _record_fail(ip: str, username: str):
    now = time.time()
    rec = _fails.get((ip, username)) or [0, 0, 0]
    if len(rec) < 3:
        rec = [rec[0], rec[1], 0]
    if (rec[2] and rec[2] <= now) or (rec[1] and now - rec[1] > 600):
        rec = [0, 0, 0]
    rec[0] += 1
    rec[1] = now
    if rec[0] >= 5:
        rec[2] = now + 120
    _fails[(ip, username)] = rec
    if len(_fails) > 4096:
        for k in list(_fails)[:2048]:
            _fails.pop(k, None)


def _clear_fail(ip: str, username: str):
    _fails.pop((ip, username), None)


def _ip_locked(ip: str) -> int:
    rec = _ip_fails.get(ip)
    if rec and len(rec) > 2 and rec[2] > time.time():
        return int(rec[2] - time.time())
    return 0


def _ip_record(ip: str):
    now = time.time()
    rec = _ip_fails.get(ip) or [0, 0, 0]
    if len(rec) < 3:
        rec = [rec[0], rec[1], 0]
    if (rec[2] and rec[2] <= now) or (rec[1] and now - rec[1] > 600):
        rec = [0, 0, 0]
    rec[0] += 1
    rec[1] = now
    if rec[0] >= 20:
        rec[2] = now + 600
    _ip_fails[ip] = rec
    if len(_ip_fails) > 4096:
        for k in list(_ip_fails)[:2048]:
            _ip_fails.pop(k, None)


# 按账号计数，不看来源 IP：攻击者换 IP（或伪造转发头）也绕不过去
_user_fails = {}
_USER_LOCK_AFTER = int(os.environ.get('GATE_USER_FAIL_LOCK', '15'))


def _user_locked(username: str) -> int:
    rec = _user_fails.get(username)
    if rec and len(rec) > 2 and rec[2] > time.time():
        return int(rec[2] - time.time())
    return 0


def _user_record(username: str):
    now = time.time()
    rec = _user_fails.get(username) or [0, 0, 0]
    if len(rec) < 3:
        rec = [rec[0], rec[1], 0]
    if (rec[2] and rec[2] <= now) or (rec[1] and now - rec[1] > 600):
        rec = [0, 0, 0]
    rec[0] += 1
    rec[1] = now
    if rec[0] >= _USER_LOCK_AFTER:
        rec[2] = now + 600
    _user_fails[username] = rec
    if len(_user_fails) > 4096:
        for k in list(_user_fails)[:2048]:
            _user_fails.pop(k, None)


# ---------------- 审计 actor 补齐（运行时包装，不改原文件） ----------------

_orig_audit = _bapp.audit


def _audit_with_actor(tid, rid, action, detail=None, actor=''):
    try:
        if not actor:
            actor = _ctx_user.get() or ''
    except Exception:
        pass
    return _orig_audit(tid, rid, action, detail, actor)


_bapp.audit = _audit_with_actor        # app.py 里 `audit(...)` 走这个名字

# ---------------- 页面 ----------------

_LOGO = '牧马人线上多维表'
_CSS = """
*{box-sizing:border-box;margin:0}
body{font-family:-apple-system,system-ui,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
     background:#F3F5F9;color:#0f1115;font-size:14px}
a{color:#4385CB}
.card{background:#fff;border:1px solid #E6EAF1;border-radius:12px;padding:28px;
      width:360px;max-width:calc(100vw - 32px);margin:12vh auto 0;box-shadow:0 8px 24px -12px rgba(23,29,41,.10)}
h1{font-size:18px;margin-bottom:4px}
.sub{color:#6B7280;font-size:12px;margin-bottom:18px}
label{display:block;font-size:12px;color:#4B5563;margin:12px 0 4px}
input,select{width:100%;padding:8px 10px;border:1px solid #E6EAF1;border-radius:8px;font-size:13px}
button{width:100%;margin-top:18px;padding:9px;border:0;border-radius:8px;background:#4385CB;
       color:#fff;font-size:14px;cursor:pointer}
button:active{background:#2C5A93}
button.ghost{background:#fff;color:#4385CB;border:1px solid #E6EAF1}
.err{color:#E40014;font-size:12px;min-height:16px;margin-top:10px}
.hint{font-size:12px;color:#6B7280;margin-top:14px;line-height:1.7}
.wrap{width:860px;max-width:calc(100vw - 24px);margin:6vh auto 40px;overflow-x:auto}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #E6EAF1;border-radius:12px;overflow:hidden}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid #EEF1F6;font-size:13px}
th{background:#F8FAFD;color:#4B5563;font-weight:500}
.tag{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px}
.tag.on{background:#E7F5EF;color:#167A55}.tag.off{background:#FBEAEA;color:#E40014}
.tag.admin{background:#EAF2FE;color:#2C5A93}.tag.staff{background:#F4F0FC;color:#8B5CF6}
.toolbar{display:flex;gap:8px;align-items:end;margin:16px 0;flex-wrap:wrap}
.toolbar label{margin:0 0 4px}
.toolbar input,.toolbar select{width:150px}
.toolbar button{width:auto;margin:0;padding:8px 16px}
.rowbtn{padding:3px 10px;font-size:12px;width:auto;margin:0;background:#fff;color:#4385CB;border:1px solid #E6EAF1;white-space:nowrap}
h2{font-size:16px;margin:24px 0 10px}
.pwbox{background:#EAF2FE;border:1px solid #BED6F3;border-radius:8px;padding:10px 12px;margin:10px 0;font-size:13px}
.cs-native{display:none!important}
.cs-select{display:flex;align-items:center;gap:6px;box-sizing:border-box;min-width:0;max-width:100%;
  height:34px;padding:0 10px;border:1px solid #E6EAF1;border-radius:7px;background:#fff;color:#0f1115;
  font-family:inherit;font-size:13px;line-height:1.2;text-align:left;cursor:pointer;outline:none}
.cs-select:hover{border-color:#C9CDD4}
.cs-select:focus-visible,.cs-select.cs-open{border-color:#4385CB;box-shadow:0 0 0 3px rgba(67,133,203,.12)}
.cs-select:disabled{opacity:.55;cursor:default;background:#F8FAFD}
.cs-select .cs-val{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cs-select .cs-caret{flex:none;color:#6B7280}
.cs-menu{position:fixed;z-index:900;max-height:280px;overflow-y:auto;padding:4px;background:#fff;
  border:1px solid #E6EAF1;border-radius:10px;box-shadow:0 14px 36px -8px rgba(23,29,41,.22)}
.cs-opt{display:flex;align-items:center;min-height:34px;padding:0 10px;border-radius:6px;font-size:13px;
  color:#4B5563;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cs-opt:hover,.cs-opt.active{background:#F3F5F9;color:#0f1115}
.cs-opt.on{color:#4385CB;font-weight:500}
.toolbar .cs-select{width:150px}
.grow .cs-select{width:132px;height:28px;padding:0 8px;font-size:12px;flex:none}
@media (max-width:480px){
  .card{margin-top:8vh;padding:22px}
  input,select{font-size:16px}
  .wrap{margin:12px 12px 40px}
}
"""

LOGIN_HTML = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录 · {_LOGO}</title><style>{_CSS}</style></head><body>
<div class="card">
  <h1>{_LOGO}</h1>
  <div class="sub">请登录后使用</div>
  <label>账号</label><input id="u" autocomplete="username" autofocus>
  <label>密码</label><input id="p" type="password" autocomplete="current-password">
  <button id="b" type="button">登 录</button>
  <div class="err" id="e"></div>
  <div class="hint">账号由管理员开通；未登录无法查看任何数据。</div>
</div>
<script>
var u=document.getElementById('u'),p=document.getElementById('p'),e=document.getElementById('e');
function doLogin(){{
  e.textContent='';
  fetch('/__auth/login',{{method:'POST',headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{username:u.value.trim(),password:p.value}})}})
  .then(function(r){{return r.json().then(function(j){{return {{ok:r.ok,j:j}}}})}})
  .then(function(x){{
    if(x.ok){{location.href='/';return}}
    e.textContent=(x.j&&x.j.detail)||'登录失败';
  }}).catch(function(){{e.textContent='网络错误'}});
}}
/* 按钮的点击必须在这里绑定，不能写成标签上的 onclick 属性：
   内容安全策略只放行带哈希的脚本，标签上的内联事件会被浏览器拦掉，按钮就点了没反应。 */
document.getElementById('b').addEventListener('click',doLogin);
p.addEventListener('keydown',function(ev){{if(ev.key==='Enter')doLogin()}})
</script></body></html>"""

ADMIN_HTML = ("""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>用户管理 · __LOGO__</title><style>__CSS__
.grantbox{display:flex;gap:18px;background:#fff;border:1px solid #E6EAF1;border-radius:12px;padding:14px}
.gcol{flex:1;min-width:0}
.gcol h3{font-size:13px;color:#4B5563;margin:0 0 8px}
.glist{max-height:280px;overflow:auto}
.grow{display:flex;align-items:center;gap:8px;padding:5px 2px;border-bottom:1px solid #EEF1F6;font-size:13px}
.grow .gn{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.grow select{width:132px;padding:3px 6px;font-size:12px}
.sub{font-size:12px;color:#6B7280;font-weight:400}
.sub2{font-size:12px;color:#6B7280;font-weight:400;margin-left:8px}
@media(max-width:640px){
  .grantbox{flex-direction:column;gap:12px}
  .toolbar>div{flex:1 1 44%}
  .toolbar input,.toolbar select,.toolbar .cs-select{width:100%}
  .wrap table{min-width:760px}
}
.ovl{position:fixed;inset:0;background:rgba(29,33,41,.4);display:none;align-items:center;justify-content:center;z-index:60}
.ovl.on{display:flex}
.ovl .box{background:#fff;border-radius:12px;padding:18px 20px;width:380px;max-width:92vw;box-shadow:0 18px 48px rgba(0,0,0,.18)}
.ovl h3{margin:0 0 8px;font-size:15px}
.ovl .msg{font-size:13px;line-height:1.7;color:#1D2129}
.ovl input{width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid #E0E3EA;border-radius:7px;margin-top:10px;font-size:13px}
.ovl .ft{display:flex;justify-content:flex-end;gap:8px;margin-top:14px}
</style></head><body>
<div class="wrap">
  <h1 style="font-size:20px">用户管理 <span class="sub">独立账号体系（kb_user / kb_perm）</span></h1>
  <h2>添加用户</h2>
  <div class="toolbar">
    <div><label>账号</label><input id="nu" placeholder="字母数字_"></div>
    <div><label>姓名</label><input id="nr" placeholder="显示名"></div>
    <div><label>初始密码</label><input id="np" placeholder="≥6位"></div>
    <div><label>角色</label><select id="nrole"><option value="staff">可编辑用户</option><option value="admin">管理员</option></select></div>
    <button id="add-user" type="button">添加</button>
  </div>
  <div class="err" id="e"></div>
  <div class="pwbox" id="pw" style="display:none"></div>
  <h2>现有账号</h2>
  <table><thead><tr><th>ID</th><th>账号</th><th>姓名</th><th>角色</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead>
  <tbody id="rows"></tbody></table>
  <h2>项目 / 表格授权 <span class="sub2" id="gt">未选择用户</span></h2>
  <div class="grantbox">
    <div class="gcol"><h3>项目授权（整个项目下所有表）</h3><div class="glist" id="gprj"><div class="hint">点用户行「授权」查看</div></div></div>
    <div class="gcol"><h3>表格授权（单张表）</h3><div class="glist" id="gtab"></div></div>
  </div>
  <p class="hint">staff 只能看到被授权的项目/表；只读=可看可筛可导出，可编辑=还能增删改行。项目授权与表格授权同时存在时取权限更高的那个。管理员拥有全部权限。</p>
</div>
<div class="ovl" id="ovl"><div class="box"><h3 id="ov-t"></h3><div class="msg" id="ov-b"></div><input id="ov-in"><div class="ft"><button class="rowbtn" id="ov-c">取消</button><button class="rowbtn" id="ov-o">确定</button></div></div></div>
<script>
var CAT=null, GR={}, G_UID=0, MYID=__MEID__;
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function api(path,method,body){
  return fetch(path,{method:method,headers:{'Content-Type':'application/json'},
      body:body?JSON.stringify(body):undefined})
  .then(function(r){
    return r.json().then(function(j){ return {ok:r.ok, j:j}; });
  });
}
var OVK=null;
function ask(opt){
  OVK=opt;
  document.getElementById('ov-t').textContent=opt.title||'提示';
  document.getElementById('ov-b').innerHTML=opt.msg||'';
  var inp=document.getElementById('ov-in');
  inp.style.display=opt.input?'':'none';
  if(opt.input){inp.value=opt.value||'';inp.type=opt.pw?'password':'text';}
  document.getElementById('ov-o').textContent=opt.ok||'确定';
  document.getElementById('ovl').classList.add('on');
  if(opt.input){inp.focus();inp.select();}
}
function ovClose(){document.getElementById('ovl').classList.remove('on');OVK=null;}
function ovDone(){
  var o=OVK;var v=o&&o.input?document.getElementById('ov-in').value:undefined;
  ovClose();if(o&&o.onOk)o.onOk(v);
}
document.getElementById('ov-c').onclick=ovClose;
document.getElementById('ov-o').onclick=ovDone;
document.getElementById('ov-in').addEventListener('keydown',function(e){
  if(e.key==='Enter')ovDone();
  if(e.key==='Escape')ovClose();
});
function err(m){ask({title:'提示',msg:esc(m||'失败'),ok:'好'});}
function load(){
  api('/__auth/users','GET').then(function(x){
    if(!x.ok){location.href='/login';return}
    var h='';
    x.j.users.forEach(function(u){
      h+='<tr><td>'+u.id+'</td><td>'+esc(u.username)+'</td><td>'+esc(u.real_name||'')+'</td>'
        +'<td><span class="tag '+u.role+'">'+(u.role==='admin'?'管理员':'可编辑')+'</span></td>'
        +'<td><span class="tag '+(u.status===1?'on':'off')+'">'+(u.status===1?'启用':'停用')+'</span></td>'
        +'<td>'+(u.updated_at||'')+'</td><td>'
        +'<button class="rowbtn" data-grant="'+u.id+'" data-name="'+esc(u.username)+'">授权</button> '
        +'<button class="rowbtn" data-pw="'+u.id+'" data-name="'+esc(u.username)+'">重置密码</button> '
        +(u.id===MYID ? '<span class="hint">当前账号</span> '
          : '<button class="rowbtn" data-toggle="'+u.id+'" data-status="'+u.status+'">'+(u.status===1?'停用':'启用')+'</button> '
            +'<button class="rowbtn" data-role="'+u.id+'" data-roleval="'+u.role+'">'+(u.role==='admin'?'降为可编辑':'升为管理员')+'</button> '
            +'<button class="rowbtn" data-del="'+u.id+'" data-name="'+esc(u.username)+'">删除</button>')
        +'</td></tr>';
    });
    document.getElementById('rows').innerHTML=h||'<tr><td colspan="7">暂无</td></tr>';
    document.querySelectorAll('#rows [data-grant]').forEach(function(b){
      b.onclick=function(){ openGrants(+b.getAttribute('data-grant'), b.getAttribute('data-name')); };
    });
    document.querySelectorAll('#rows [data-toggle]').forEach(function(b){
      b.onclick=function(){ toggle(+b.getAttribute('data-toggle'), +b.getAttribute('data-status')); };
    });
    document.querySelectorAll('#rows [data-pw]').forEach(function(b){
      b.onclick=function(){ resetPw(+b.getAttribute('data-pw'), b.getAttribute('data-name')); };
    });
    document.querySelectorAll('#rows [data-role]').forEach(function(b){
      b.onclick=function(){ setRole(+b.getAttribute('data-role'), b.getAttribute('data-roleval')); };
    });
    document.querySelectorAll('#rows [data-del]').forEach(function(b){
      b.onclick=function(){ delUser(+b.getAttribute('data-del'), b.getAttribute('data-name')); };
    });
  });
}
function addUser(){
  var e=document.getElementById('e');e.textContent='';
  api('/__auth/users','POST',{username:document.getElementById('nu').value.trim(),
    real_name:document.getElementById('nr').value.trim(),
    password:document.getElementById('np').value,role:document.getElementById('nrole').value})
  .then(function(x){if(!x.ok){e.textContent=x.j.detail||'失败';return}
    document.getElementById('nu').value=document.getElementById('nr').value=document.getElementById('np').value='';
    load();});
}
function toggle(id,status){
  api('/__auth/users/'+id,'PATCH',{status:status===1?0:1})
  .then(function(x){if(!x.ok)err(x.j.detail||'失败');load();});
}
function resetPw(id,name){
  ask({title:'重置密码',msg:'给「'+esc(name)+'」设置新密码（至少 6 位）：',input:true,pw:true,ok:'保存',
    onOk:function(v){
      if(!v||v.length<6){err('密码至少 6 位');return;}
      api('/__auth/users/'+id,'PATCH',{password:v})
      .then(function(x){if(!x.ok)err(x.j.detail||'失败');else ask({title:'提示',msg:'密码已重置',ok:'好'});load();});
    }});
}
function delUser(id,name){
  ask({title:'删除账号',msg:'删除账号「'+esc(name)+'」？该账号的项目/表授权会一并移除，登录立即失效。',ok:'删除',
    onOk:function(){
      api('/__auth/users/'+id,'DELETE')
      .then(function(x){if(!x.ok)err(x.j.detail||'失败');load();});
    }});
}
function setRole(id,role){
  api('/__auth/users/'+id,'PATCH',{role:role==='admin'?'staff':'admin'})
  .then(function(x){if(!x.ok)err(x.j.detail||'失败');load();});
}
function roleLabel(r){return r==='viewer'?'只读':(r==='editor'?'可编辑':'无');}
function sel(st,key,cur,projRole){
  var h='<select data-st="'+st+'" data-key="'+esc(key)+'">';
  if(st==='table'){
    h+='<option value=""'+(cur===''?' selected':'')+'>继承项目（'+roleLabel(projRole)+'）</option>'
      +'<option value="none"'+(cur==='none'?' selected':'')+'>无</option>';
  } else {
    h+='<option value=""'+(cur===''?' selected':'')+'>无</option>';
  }
  h+='<option value="viewer"'+(cur==='viewer'?' selected':'')+'>只读</option>'
    +'<option value="editor"'+(cur==='editor'?' selected':'')+'>可编辑</option></select>';
  return h;
}
function paint(){
  var gp='';
  CAT.projects.forEach(function(p){gp+='<div class="grow"><span class="gn" title="'+esc(p)+'">'+esc(p)+'</span>'+sel('project',p,GR['project|'+p]||'','')+'</div>';});
  document.getElementById('gprj').innerHTML=gp||'<div class="hint">暂无项目</div>';
  var gt='';
  CAT.tables.forEach(function(t){
    var projRole=GR['project|'+(t.project||'')]||'';
    var tv=GR['table|'+t.id], cur=(tv==='none'?'none':(tv||''));
    gt+='<div class="grow"><span class="gn" title="'+esc(t.name)+'">'+esc(t.name)+'</span>'+sel('table',String(t.id),cur,projRole)+'</div>';
  });
  document.getElementById('gtab').innerHTML=gt;
  document.querySelectorAll('.grantbox select').forEach(function(s){
    s.onchange=function(){
      var st=s.getAttribute('data-st'), key=s.getAttribute('data-key'), role=s.value;
      if(role===''){
        delete GR[st+'|'+key];
        api('/__auth/grants/delete','POST',{uid:G_UID,scope_type:st,scope_key:key}).then(function(){ if(st==='project') paint(); });
      } else {
        GR[st+'|'+key]=role;
        api('/__auth/grants','POST',{uid:G_UID,scope_type:st,scope_key:key,role:role}).then(function(){ if(st==='project') paint(); });
      }
    };
  });
}
function openGrants(uid,name){
  G_UID=uid; GR={};
  document.getElementById('gt').textContent='· '+name;
  api('/__auth/grants?uid='+uid,'GET').then(function(x){
    if(!x.ok){alert(x.j.detail||'读取失败');return;}
    x.j.grants.forEach(function(g){GR[g.scope_type+'|'+g.scope_key]=g.role;});
    if(CAT){paint();return;}
    api('/__auth/catalog','GET').then(function(c){
      if(!c.ok){alert(c.j.detail||'读取失败');return;}
      CAT=c.j; paint();
    });
  });
}
/* 自定义下拉：替换原生 select */
(function(){
  if(window.__csInit) return; window.__csInit=1;
  var OPEN=null;
  function close(){ if(!OPEN)return; OPEN.btn.classList.remove('cs-open'); OPEN.menu.remove(); OPEN=null; }
  function sync(sel){ var b=sel.__csBtn; if(!b)return; var o=sel.options[sel.selectedIndex];
    b.querySelector('.cs-val').textContent=(o?o.textContent.trim():'')||'—'; b.disabled=!!sel.disabled; }
  function open(sel){
    close(); var b=sel.__csBtn; if(!b||!sel.options.length)return;
    var m=document.createElement('div'); m.className='cs-menu';
    Array.from(sel.options).forEach(function(o,i){
      var d=document.createElement('div'); d.className='cs-opt'+(i===sel.selectedIndex?' on':'');
      d.textContent=(o.textContent||'').trim()||'—';
      d.onclick=function(e){ e.stopPropagation(); if(o.disabled)return;
        sel.selectedIndex=i; sync(sel); close(); sel.dispatchEvent(new Event('change',{bubbles:true})); };
      m.appendChild(d);
    });
    m.addEventListener('click',function(e){e.stopPropagation();});
    document.body.appendChild(m);
    var r=b.getBoundingClientRect(), w=Math.min(Math.max(r.width,160),Math.min(320,innerWidth-16));
    m.style.width=w+'px'; m.style.left=Math.max(8,Math.min(r.left,innerWidth-8-w))+'px'; m.style.top='0px';
    var mh=m.offsetHeight, below=innerHeight-r.bottom-8, above=r.top-8;
    var top=(below>=mh||below>=above)?r.bottom+4:r.top-4-mh;
    m.style.top=Math.max(8,Math.min(top,innerHeight-8-mh))+'px';
    b.classList.add('cs-open'); OPEN={sel:sel,btn:b,menu:m};
  }
  function enhance(root){
    (root||document).querySelectorAll('select').forEach(function(sel){
      if(sel.__cs||sel.multiple)return; sel.__cs=1;
      var b=document.createElement('button'); b.type='button'; b.className='cs-select'+(sel.className?' '+sel.className:'');
      b.innerHTML='<span class="cs-val"></span><svg class="cs-caret" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>';
      b.setAttribute('role','combobox'); b.setAttribute('aria-haspopup','listbox');
      sel.parentNode.insertBefore(b,sel.nextSibling); sel.classList.add('cs-native'); sel.__csBtn=b;
      b.onclick=function(e){ e.stopPropagation(); if(!sel.disabled) open(sel); };
      try{ var vd=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value');
        Object.defineProperty(sel,'value',{configurable:true,get:function(){return vd.get.call(sel);},set:function(v){vd.set.call(sel,v);sync(sel);}});
      }catch(e){}
      new MutationObserver(function(){sync(sel);}).observe(sel,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled']});
      sync(sel);
    });
  }
  document.addEventListener('click',function(e){ if(!OPEN)return; if(e.target.closest('.cs-menu')||e.target.closest('.cs-select'))return; close(); });
  document.addEventListener('keydown',function(e){ if(OPEN&&e.key==='Escape')close(); });
  new MutationObserver(function(ms){ ms.forEach(function(m){ m.addedNodes.forEach(function(n){ if(n.nodeType!==1)return; if(n.tagName==='SELECT')enhance(n); else if(n.querySelectorAll)enhance(n); }); }); }).observe(document.body,{childList:true,subtree:true});
  enhance(document);
})();
/* 按钮的点击在脚本里绑定，不能写成标签上的 onclick 属性：内容安全策略会拦掉标签上的内联事件。 */
document.getElementById('add-user').addEventListener('click',addUser);
['nu','nr','np'].forEach(function(id){document.getElementById(id).addEventListener('keydown',function(ev){if(ev.key==='Enter')addUser();});});
load();
</script></body></html>""" ).replace("__CSS__", _CSS).replace("__LOGO__", _LOGO)
# ---------------- /__auth 路由（在中间件里自己接，不进原 app） ----------------

def _resp_json(data, status=200, cookie=None, clear_cookie=False):
    r = JSONResponse(data, status_code=status)
    if cookie:
        r.set_cookie(COOKIE, cookie, max_age=TOKEN_TTL, httponly=True,
                     secure=_COOKIE_SECURE, samesite='lax', path='/')
    if clear_cookie:
        r.delete_cookie(COOKIE, path='/', secure=_COOKIE_SECURE, httponly=True, samesite='lax')
    return r


def _is_json(request) -> bool:
    ct = (request.headers.get('content-type') or '').strip().lower()
    return ct == 'application/json' or ct.startswith('application/json;') or ct.endswith('+json')


async def _body(request) -> dict:
    """只解析 JSON 请求体。浏览器里“文本 / 表单”这类请求不需要预检就能跨源发出，
    要是不认类型就解析，别的站点就能带着用户的登录态伪造出管理员操作。"""
    if not _is_json(request):
        return {}
    try:
        d = await request.json()
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _cross_site_write(request) -> bool:
    """浏览器发出的写请求，如果不是本站页面发起的，就拒绝。
    优先看 Sec-Fetch-Site（浏览器自己标的，页面改不了）：本站页面发起的是 same-origin；
    同一个域名下的其它站点（比如同一个托管平台上的邻居站点）是 same-site —— Cookie 照带，必须挡。
    老浏览器没有这个头时，退而比较 Origin 和本站地址。"""
    sfs = (request.headers.get('sec-fetch-site') or '').strip().lower()
    if sfs:
        return sfs not in ('same-origin', 'none')
    if not _ORIGIN_CHECK:
        return False
    origin = request.headers.get('origin')
    if not origin:
        return False                 # 不是浏览器发的（脚本 / RPA），没有这个头
    try:
        o = urlsplit(origin)
    except Exception:
        return True
    if o.scheme not in ('http', 'https') or not o.netloc:
        return True
    hosts = {h.strip().lower() for h in (request.headers.get('host') or '').split(',') if h.strip()}
    hosts.update(h.strip().lower() for h in (request.headers.get('x-forwarded-host') or '').split(',') if h.strip())
    return o.netloc.lower() not in hosts


def _current_user(request):
    p = parse_token(request.cookies.get(COOKIE) or '')
    if not p:
        auth = request.headers.get('authorization') or ''
        if auth.lower().startswith('bearer '):
            p = parse_token(auth[7:].strip())
    return p, live_account(p)


def _login_result(ip, u, pw):
    """登录校验。scrypt 较慢，整个登录网关都在线程池里跑，100 人同时登录不会把事件循环堵死。"""
    wait = _locked(ip, u) or _ip_locked(ip) or _user_locked(u)
    if wait:
        return _resp_json({'detail': f'失败次数过多，{wait} 秒后再试'}, 429)
    acct = q("SELECT id,username,password_hash,real_name,role,status,token_ver FROM kb_user "
             "WHERE username=%s", (u,), one=True)
    if not acct or acct.get('status') != 1 or not verify_password(pw, acct.get('password_hash') or ''):
        _record_fail(ip, u)
        _ip_record(ip)
        _user_record(u)
        return _resp_json({'detail': '账号或密码错误'}, 401)
    _clear_fail(ip, u)
    _ip_fails.pop(ip, None)
    _user_fails.pop(u, None)
    tok = issue_token(int(acct['id']), acct['username'], acct['role'], tv=acct.get('token_ver') or 0)
    return _resp_json({'ok': True, 'user': {'username': acct['username'],
                         'name': acct['real_name'] or acct['username'], 'role': acct['role']}},
                      cookie=tok)


def _client_ip(request) -> str:
    """云端经代理时取 X-Forwarded-For；直连时用 peer，避免客户端伪造头绕过限流。"""
    peer = (request.client.host if request.client else '?') or '?'
    if peer in ('127.0.0.1', '::1') or peer.startswith(('10.', '172.', '192.168.')):
        xff = (request.headers.get('x-forwarded-for') or '').split(',')[0].strip()
        if xff:
            return xff[:64]
        xr = (request.headers.get('x-real-ip') or '').strip()
        if xr:
            return xr[:64]
    return peer


# ---------------- 可下发能力（字段管理 / 账号管理） ----------------

_ROLE_RANK = {'none': 0, 'viewer': 1, 'editor': 2}


def _can_users(user):
    return bool(user) and (user.get('role') == 'admin' or user.get('can_users'))


def _perm_maps(uid):
    pm, tm = {}, {}
    for r in q("SELECT scope_type,scope_key,role FROM kb_perm WHERE user_id=%s", (uid,)):
        if r['scope_type'] == 'project':
            pm[r['scope_key']] = r['role']
        elif r['scope_type'] == 'table':
            tm[str(r['scope_key'])] = r['role']
    return pm, tm


def _eff_table_role(tid, project, pm, tm):
    tr = tm.get(str(tid))
    if tr in ('viewer', 'editor'):
        return tr
    if tr == 'none':
        return 'none'
    return pm.get(project or '')


def _target_manageable(manager, target):
    """账号管理下发：目标必须是 staff，且其全部正向授权都在管理者自身权限内。"""
    if manager.get('role') == 'admin':
        return True
    if not target or target.get('role') != 'staff' or int(target.get('id') or 0) == int(manager.get('uid') or 0):
        return False
    if target.get('can_fields') or target.get('can_users'):
        return False          # 带能力的账号只能由管理员操作，防止下级互相停用/改密
    if (target.get('created_by') or '') != (manager.get('username') or ''):
        return False          # 只允许管理自己新增的账号
    pm, tm = _perm_maps(manager['uid'])
    for r in q("SELECT scope_type,scope_key,role FROM kb_perm WHERE user_id=%s", (target['id'],)):
        role = r['role']
        if role not in ('viewer', 'editor'):
            continue
        if r['scope_type'] == 'project':
            mr = pm.get(r['scope_key'])
        else:
            key = str(r['scope_key'])
            if not key.isdigit():
                return False
            tb = q("SELECT project FROM kb_table WHERE id=%s AND deleted=0", (int(key),), one=True)
            if not tb:
                return False
            mr = _eff_table_role(int(key), tb.get('project') or '', pm, tm)
        if not mr or mr == 'none' or _ROLE_RANK.get(mr, 0) < _ROLE_RANK.get(role, 0):
            return False
    return True


def _copy_grants(src_uid, dst_uid):
    """预留：批量复制授权（当前新建账号默认无授权，由创建者按需下发）。"""
    for r in q("SELECT scope_type,scope_key,role FROM kb_perm WHERE user_id=%s", (src_uid,)):
        ex("INSERT INTO kb_perm(user_id,scope_type,scope_key,role) VALUES(%s,%s,%s,%s) "
           "ON DUPLICATE KEY UPDATE role=VALUES(role)",
           (dst_uid, r['scope_type'], r['scope_key'], r['role']))


def _catalog_projects():
    """授权目录里的项目 = 项目登记表全部 + 历史表里出现但未登记的项目。

    不能只从 kb_table 里取 DISTINCT project：新建的空项目还没有表，
    会从管理员的项目授权下拉里消失，后面多项目时就授不了。
    """
    out, seen = [], set()
    for r in q("SELECT name FROM kb_project WHERE deleted=0 ORDER BY sort,name"):
        n = r['name']
        if n and n not in seen:
            seen.add(n); out.append(n)
    for r in q("SELECT project, MIN(sort) AS s FROM kb_table "
               "WHERE deleted=0 AND project<>'' GROUP BY project ORDER BY s"):
        n = r['project']
        if n and n not in seen:
            seen.add(n); out.append(n)
    return out


_LOGIN_CSP = csp_for_html(LOGIN_HTML)


def _gate(request):
    """同步函数：由 _gate_async 放进线程池执行，里面的每条 SQL 都不会卡住事件循环。"""
    path, method = request.url.path, request.method
    ip = _client_ip(request)

    if path == '/login' and method == 'GET':
        return HTMLResponse(LOGIN_HTML, headers={'Content-Security-Policy': _LOGIN_CSP})

    if path == '/__auth/login' and method == 'POST':
        d = request.state.gate_body
        return _login_result(ip, (d.get('username') or '').strip(), d.get('password') or '')

    if path == '/__auth/logout':
        return _resp_json({'ok': True}, clear_cookie=True)

    payload, user = _current_user(request)

    if path == '/__auth/me':
        return _resp_json({'user': user})

    if path == '/__auth/admin' and method == 'GET':
        if not user or user['role'] != 'admin':
            return HTMLResponse('', status_code=302, headers={'Location': '/login'})
        # 这个页面的脚本里嵌着当前管理员的 ID，每个人看到的内容不一样，策略按本次内容算
        html = ADMIN_HTML.replace('__MEID__', str(user['uid']))
        return HTMLResponse(html, headers={'Content-Security-Policy': csp_for_html(html)})

    if path == '/__auth/users' and method == 'GET':
        if not _can_users(user):
            return _resp_json({'detail': '需要账号管理权限'}, 403)
        rows = q("SELECT id,username,real_name,role,status,can_fields,can_users,created_by,"
                 "DATE_FORMAT(updated_at,'%Y-%m-%d %H:%i') AS updated_at "
                 "FROM kb_user ORDER BY id")
        if user['role'] != 'admin':
            rows = [r for r in rows
                    if int(r['id']) == int(user['uid']) or r.get('created_by') == user['username']]
        for r in rows:
            r['can_fields'] = bool(r.get('can_fields'))
            r['can_users'] = bool(r.get('can_users'))
            r['manageable'] = _target_manageable(user, r)
        return _resp_json({'users': rows})

    if path == '/__auth/users' and method == 'POST':
        if not _can_users(user):
            return _resp_json({'detail': '需要账号管理权限'}, 403)
        d = request.state.gate_body
        u = (d.get('username') or '').strip()
        pw = d.get('password') or ''
        if not re.fullmatch(r'[A-Za-z0-9_.-]{2,32}', u):
            return _resp_json({'detail': '账号需 2~32 位字母/数字/_.-'}, 400)
        if len(pw) < 6:
            return _resp_json({'detail': '密码至少 6 位'}, 400)
        is_admin = user['role'] == 'admin'
        role = (d.get('role') or 'staff') if is_admin else 'staff'
        if role not in ('admin', 'staff'):
            return _resp_json({'detail': '角色无效'}, 400)
        if q("SELECT id FROM kb_user WHERE username=%s", (u,), one=True):
            return _resp_json({'detail': f'账号「{u}」已存在'}, 409)
        cf = 1 if (is_admin and d.get('can_fields')) else 0
        cu = 1 if (is_admin and d.get('can_users')) else 0
        new_id = ex("INSERT INTO kb_user(username,password_hash,real_name,role,status,can_fields,can_users,created_by) "
                    "VALUES(%s,%s,%s,%s,1,%s,%s,%s)",
                    (u, hash_password(pw), (d.get('real_name') or '').strip()[:64], role, cf, cu, user['username']))
        clear_account_cache()
        return _resp_json({'ok': True})

    m = re.fullmatch(r'/__auth/users/(\d+)', path)
    if m and method == 'PATCH':
        if not _can_users(user):
            return _resp_json({'detail': '需要账号管理权限'}, 403)
        target_id = int(m.group(1))
        tgt = q("SELECT id,username,role,status,can_fields,can_users,created_by FROM kb_user WHERE id=%s",
                (target_id,), one=True)
        if not tgt:
            return _resp_json({'detail': '账号不存在'}, 404)
        is_admin = user['role'] == 'admin'
        d = request.state.gate_body
        if not is_admin:
            if target_id == int(user['uid']):
                return _resp_json({'detail': '当前账号请用右上角「修改密码」'}, 400)
            if any(k in d for k in ('role', 'can_fields', 'can_users')):
                return _resp_json({'detail': '只有管理员能调整角色或下发能力'}, 403)
            if not _target_manageable(user, tgt):
                return _resp_json({'detail': '该账号权限超过你，不能操作'}, 403)
        sets, args = [], []
        if d.get('password') is not None:
            if len(d['password']) < 6:
                return _resp_json({'detail': '密码至少 6 位'}, 400)
            sets.append('password_hash=%s'); args.append(hash_password(d['password']))
            sets.append('token_ver=token_ver+1')      # 被重置密码的账号，手里的旧凭证全部失效
        if d.get('real_name') is not None:
            sets.append('real_name=%s'); args.append(str(d['real_name']).strip()[:64])
        if d.get('status') is not None:
            if int(d['status']) not in (0, 1):
                return _resp_json({'detail': 'status 只能是 0/1'}, 400)
            if int(d['status']) == 0 and target_id == user['uid']:
                return _resp_json({'detail': '不能停用自己'}, 400)
            sets.append('status=%s'); args.append(int(d['status']))
        if is_admin and d.get('role') is not None:
            if d['role'] not in ('admin', 'staff'):
                return _resp_json({'detail': '角色无效'}, 400)
            if d['role'] != 'admin' and target_id == user['uid']:
                return _resp_json({'detail': '不能降级自己'}, 400)
            sets.append('role=%s'); args.append(d['role'])
        if is_admin and (d.get('can_fields') is not None or d.get('can_users') is not None):
            if tgt['role'] == 'admin':
                return _resp_json({'detail': '管理员默认拥有全部能力'}, 400)
            for k in ('can_fields', 'can_users'):
                if d.get(k) is not None:
                    sets.append(k + '=%s'); args.append(1 if d[k] else 0)
        if not sets:
            return _resp_json({'detail': '没有可更新的字段'}, 400)
        # 保护最后一个可用管理员
        if ('status=%s' in sets and args[sets.index('status=%s')] == 0) or \
           ('role=%s' in sets and args[sets.index('role=%s')] == 'staff'):
            if tgt['role'] == 'admin' and tgt['status'] == 1:
                n = q("SELECT COUNT(*) n FROM kb_user WHERE role='admin' AND status=1 AND id<>%s",
                      (target_id,), one=True)['n']
                if n == 0:
                    return _resp_json({'detail': '这是最后一个可用管理员，不能停用/降级'}, 400)
        args.append(target_id)
        ex(f"UPDATE kb_user SET {','.join(sets)} WHERE id=%s", tuple(args))
        clear_account_cache()
        if is_admin and ('role=%s' in sets):
            try: _bapp.clear_perm_cache()
            except Exception: pass
        return _resp_json({'ok': True})

    if m and method == 'DELETE':
        if not user or user['role'] != 'admin':
            return _resp_json({'detail': '需要管理员权限'}, 403)
        uid = int(m.group(1))
        if uid == user['uid']:
            return _resp_json({'detail': '不能删除自己'}, 400)
        tgt = q("SELECT id,username,role,status FROM kb_user WHERE id=%s", (uid,), one=True)
        if not tgt:
            return _resp_json({'detail': '账号不存在'}, 404)
        if tgt['role'] == 'admin' and tgt['status'] == 1:
            n = q("SELECT COUNT(*) n FROM kb_user WHERE role='admin' AND status=1 AND id<>%s",
                  (uid,), one=True)['n']
            if n == 0:
                return _resp_json({'detail': '这是最后一个可用管理员，不能删除'}, 400)
        ex("DELETE FROM kb_perm WHERE user_id=%s", (uid,))
        ex("DELETE FROM kb_user WHERE id=%s", (uid,))
        clear_account_cache()
        return _resp_json({'ok': True})

    if path == '/__auth/catalog' and method == 'GET':
        if not _can_users(user):
            return _resp_json({'detail': '需要账号管理权限'}, 403)
        if user['role'] == 'admin':
            projects = _catalog_projects()
            tables = q("SELECT id,name,project FROM kb_table WHERE deleted=0 ORDER BY sort,id")
            return _resp_json({'projects': projects, 'tables': tables, 'limits': {}})
        # 下发的账号管理员：目录只给自己的项目/表，并带最大可授角色
        pm, tm = _perm_maps(user['uid'])
        active = set(_catalog_projects())
        proj_roles = {k: v for k, v in pm.items() if v in ('viewer', 'editor') and k in active}
        projects = sorted(proj_roles)
        tables, tab_lim = [], {}
        for t in q("SELECT id,name,project FROM kb_table WHERE deleted=0 ORDER BY sort,id"):
            eff = _eff_table_role(t['id'], t.get('project') or '', pm, tm)
            if eff in ('viewer', 'editor'):
                tables.append(t)
                tab_lim[str(t['id'])] = eff
        return _resp_json({'projects': projects, 'tables': tables,
                           'limits': {'projects': proj_roles, 'tables': tab_lim}})

    if path == '/__auth/grants' and method == 'GET':
        if not _can_users(user):
            return _resp_json({'detail': '需要账号管理权限'}, 403)
        uid = (request.query_params.get('uid') or '').strip()
        if not uid.isdigit():
            return _resp_json({'detail': 'uid 无效'}, 400)
        if user['role'] != 'admin':
            tgt = q("SELECT id,username,role,status,can_fields,can_users,created_by FROM kb_user WHERE id=%s",
                    (int(uid),), one=True)
            if not _target_manageable(user, tgt):
                return _resp_json({'detail': '只能管理自己新增的账号'}, 403)
        rows = q("SELECT scope_type,scope_key,role FROM kb_perm WHERE user_id=%s", (int(uid),))
        return _resp_json({'grants': rows})

    if path == '/__auth/grants' and method == 'POST':
        if not _can_users(user):
            return _resp_json({'detail': '需要账号管理权限'}, 403)
        d = request.state.gate_body
        try:
            uid = int(d.get('uid') or 0)
        except Exception:
            uid = 0
        st = d.get('scope_type') or ''
        key = str(d.get('scope_key') or '').strip()[:200]
        role = d.get('role') or ''
        ok_role = role in ('viewer', 'editor') or (st == 'table' and role == 'none')
        if not uid or st not in ('project', 'table') or not key or not ok_role:
            return _resp_json({'detail': '参数无效'}, 400)
        tgt = q("SELECT id,username,role,status,can_fields,can_users,created_by FROM kb_user WHERE id=%s",
                (uid,), one=True)
        if not tgt:
            return _resp_json({'detail': '用户不存在'}, 404)
        if user['role'] != 'admin':
            if not _target_manageable(user, tgt):
                return _resp_json({'detail': '只能管理自己新增的账号'}, 403)
            pm, tm = _perm_maps(user['uid'])
            if st == 'project':
                max_role = pm.get(key)
            else:
                if not key.isdigit():
                    return _resp_json({'detail': 'scope_key 无效'}, 400)
                tb = q("SELECT project FROM kb_table WHERE id=%s AND deleted=0", (int(key),), one=True)
                if not tb:
                    return _resp_json({'detail': '表不存在'}, 404)
                max_role = _eff_table_role(int(key), tb.get('project') or '', pm, tm)
            if max_role not in ('viewer', 'editor'):
                return _resp_json({'detail': '超出你的权限范围'}, 403)
            if role != 'none' and _ROLE_RANK.get(role, 0) > _ROLE_RANK.get(max_role, 0):
                return _resp_json({'detail': '不能授予超过自己的权限'}, 403)
        ex("INSERT INTO kb_perm(user_id,scope_type,scope_key,role) VALUES(%s,%s,%s,%s) "
           "ON DUPLICATE KEY UPDATE role=VALUES(role)", (uid, st, key, role))
        try: _bapp.clear_perm_cache()
        except Exception: pass
        return _resp_json({'ok': True})

    if path == '/__auth/grants/delete' and method == 'POST':
        if not _can_users(user):
            return _resp_json({'detail': '需要账号管理权限'}, 403)
        d = request.state.gate_body
        try:
            uid = int(d.get('uid') or 0)
        except Exception:
            uid = 0
        st = d.get('scope_type') or ''
        key = str(d.get('scope_key') or '').strip()[:200]
        if not uid or st not in ('project', 'table') or not key:
            return _resp_json({'detail': '参数无效'}, 400)
        if user['role'] != 'admin':
            tgt = q("SELECT id,username,role,status,can_fields,can_users,created_by FROM kb_user WHERE id=%s",
                    (uid,), one=True)
            if not _target_manageable(user, tgt):
                return _resp_json({'detail': '只能管理自己新增的账号'}, 403)
        ex("DELETE FROM kb_perm WHERE user_id=%s AND scope_type=%s AND scope_key=%s",
           (uid, st, key))
        try: _bapp.clear_perm_cache()
        except Exception: pass
        return _resp_json({'ok': True})

    if path == '/__auth/me/password' and method == 'POST':
        if not user:
            return _resp_json({'detail': '请先登录'}, 401)
        d = request.state.gate_body
        acct = q("SELECT password_hash FROM kb_user WHERE id=%s", (user['uid'],), one=True)
        if not acct or not verify_password(d.get('old_password') or '', acct['password_hash']):
            return _resp_json({'detail': '原密码不正确'}, 400)
        if len(d.get('new_password') or '') < 6:
            return _resp_json({'detail': '新密码至少 6 位'}, 400)
        ex("UPDATE kb_user SET password_hash=%s, token_ver=token_ver+1 WHERE id=%s",
           (hash_password(d['new_password']), user['uid']))
        clear_account_cache()
        return _resp_json({'ok': True, 'detail': '已修改，旧登录即刻失效，请用新密码重新登录'},
                          clear_cookie=True)

    return _resp_json({'detail': '接口不存在'}, 404)


async def _gate_async(request):
    """/login 和 /__auth/* 的入口。请求体只能在这里读（async 上下文），
    其余全部交给线程池里的 _gate：以前这些分支在事件循环里同步查远程库，每条 SQL 都会让所有人的请求停一下。"""
    if request.method in ('POST', 'PATCH', 'PUT', 'DELETE'):
        request.state.gate_body = await _body(request)
    return await run_in_threadpool(_gate, request)


# ---------------- 主中间件（挂在原 app 外层，后加者先执行） ----------------

async def _next_with_user(request, call_next, user):
    """把当前登录用户注入 backend.app 的上下文（权限过滤/行级写校验用）。"""
    ctx = user or {'uid': 0, 'username': 'guest', 'name': '游客', 'role': 'guest'}
    tok = _bapp.AUTH_USER.set(ctx)
    try:
        return await call_next(request)
    finally:
        _bapp.AUTH_USER.reset(tok)


@app.middleware("http")
async def _gate_middleware(request, call_next):
    path, method = request.url.path, request.method
    if path == '/healthz':
        try:
            await run_in_threadpool(q, 'SELECT 1')    # 以前在事件循环里查库：数据库一慢，所有人都停住
            return _resp_json({'ok': True, 'db': True})
        except Exception as e:
            print(f'[health] db check failed: {e!r}', flush=True)
            return _resp_json({'ok': False, 'db': False}, 503)
    # 这两项必须放在最前面：/login 和 /__auth/* 也要过（建账号、改授权恰恰是最该防跨站的操作）
    api_in = method == 'POST' and re.fullmatch(r'/api/in/\d+', path)
    if method not in ('GET', 'HEAD', 'OPTIONS'):
        # 请求体上限：没有的话，任何登录用户发一个超大文件就能把内存吃光
        cl = request.headers.get('content-length') or ''
        if cl.isdigit() and int(cl) > _MAX_BODY:
            return _resp_json({'detail': f'请求体不能超过 {_MAX_BODY // 1048576}MB'}, 413)
        # 免登录写入（凭表级 X-KB-Token，脚本 / RPA 调用，不带浏览器来源头）不参与来源校验
        if not api_in and _cross_site_write(request):
            return _resp_json({'detail': '请求不是本站页面发起的，已拒绝'}, 403)
    if path == '/login' or path.startswith('/__auth'):
        return await _gate_async(request)
    if method == 'OPTIONS':
        return await call_next(request)
    if api_in:
        return await call_next(request)
    payload, user = await run_in_threadpool(_current_user, request)
    if user is None:
        if path == '/favicon.ico':
            return Response(status_code=204)
        if method in ('GET', 'HEAD'):
            if path.startswith('/api/'):
                return _resp_json({'detail': '请先登录'}, 401)
            return HTMLResponse(LOGIN_HTML, headers={'Cache-Control': 'no-store',
                                                     'Content-Security-Policy': _LOGIN_CSP})
        return _resp_json({'detail': '请先登录'}, 401)
    if user['role'] != 'admin' and _admin_only(method, path):
        return _resp_json({'detail': '建表/改表/管列/项目管理仅限管理员（admin）'}, 403)
    _ctx_user.set(user['username'])               # 供审计 actor 使用
    return await _next_with_user(request, call_next, user)


@app.middleware("http")
async def _security_middleware(request, call_next):
    resp = await call_next(request)
    h = resp.headers
    h.setdefault('X-Content-Type-Options', 'nosniff')
    h.setdefault('X-Frame-Options', 'DENY')
    h.setdefault('Referrer-Policy', 'same-origin')
    h.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=(), payment=()')
    # 页面响应由各自的处理函数带上按内容算好的策略；这里是给接口等其它响应的严格兜底
    h.setdefault('Content-Security-Policy', csp_for_html())
    h.setdefault('X-Robots-Tag', 'noindex, nofollow, noarchive')
    if _GATE_HTTPS:
        h.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    p = request.url.path
    if p.startswith('/api/') or p.startswith('/__auth') or p in ('/login', '/healthz'):
        h.setdefault('Cache-Control', 'no-store')
    return resp


def ensure_admin_exists():
    """首次启动：从旧的 mt_user 迁移一次账号到 kb_user，之后本应用完全独立。"""
    if q("SELECT id FROM kb_user WHERE role='admin' AND status=1 LIMIT 1", one=True):
        _seed_default_grants()
        return
    migrated = 0
    try:
        old = q("SELECT username,password_hash,real_name,role,status FROM mt_user ORDER BY id")
    except Exception:
        old = []
    for r in old:
        if not r.get('username') or q("SELECT id FROM kb_user WHERE username=%s", (r['username'],), one=True):
            continue
        ex("INSERT INTO kb_user(username,password_hash,real_name,role,status,created_by) "
           "VALUES(%s,%s,%s,%s,%s,'migrate')",
           (r['username'], r['password_hash'], (r.get('real_name') or '')[:64],
            r['role'] if r.get('role') in ('admin', 'staff') else 'staff',
            1 if r.get('status') == 1 else 0))
        migrated += 1
    if q("SELECT id FROM kb_user WHERE role='admin' AND status=1 LIMIT 1", one=True):
        print(f'[gate] 已从 mt_user 迁移 {migrated} 个账号到 kb_user（独立表）')
        _seed_default_grants()
        return
    u = os.environ.get('GATE_ADMIN_USER', 'admin').strip() or 'admin'
    pw = os.environ.get('GATE_ADMIN_PASSWORD') or secrets.token_urlsafe(9)
    ex("INSERT INTO kb_user(username,password_hash,real_name,role,status,created_by) "
       "VALUES(%s,%s,'管理员','admin',1,'gate-bootstrap')", (u, hash_password(pw)))
    boot_file = GATE_DATA_DIR / 'admin_bootstrap.txt'
    try:
        GATE_DATA_DIR.mkdir(parents=True, exist_ok=True)
        boot_file.write_text(
            '引导管理员：%s / %s；仅首次创建时生成，请登录后尽快改密码' % (u, pw),
            encoding='utf-8')
    except OSError:
        pass
    print(f'[gate] 已引导创建管理员：{u} / {pw}（也写在 {boot_file}）')
    _seed_default_grants()


_SEED_DONE = 'perm_seed_done'


def _seed_default_grants():
    """一次性初始化：全新部署首次启动时，给已有的 staff 默认授予全部项目 editor
    （对齐迁移前的可编辑体验），做完在 kb_setting 记一个标记，之后启动不再执行。

    不能拿「kb_perm 是否为空」当条件：那样管理员清空授权后一重启，权限就会被悄悄还原。
    旧库没有标记但已经有授权记录 = 早已初始化过，只补标记、不发放。"""
    if setting_get(_SEED_DONE):
        return
    if q("SELECT id FROM kb_perm LIMIT 1", one=True):
        setting_set(_SEED_DONE, 'already-had-perms')
        return
    projects = [r['project'] for r in q(
        "SELECT DISTINCT project FROM kb_table WHERE deleted=0 AND project<>''")]
    for u in q("SELECT id FROM kb_user WHERE role='staff' AND status=1"):
        for prj in projects:
            ex("INSERT IGNORE INTO kb_perm(user_id,scope_type,scope_key,role) "
               "VALUES(%s,'project',%s,'editor')", (u['id'], prj))
    setting_set(_SEED_DONE, 'seeded')

