# -*- coding: utf-8 -*-
"""带登录门卫启动：python auth_gate/run_gate.py

默认监听 0.0.0.0:8614（给局域网用），并且走 HTTPS：首次启动自动生成一张本机自签证书，
密码和登录态在局域网里是加密传输的。浏览器第一次访问会提示证书不受信任，点“继续前往”即可。

  - 只想本机用：            GATE_HOST=127.0.0.1
  - 临时退回明文 HTTP：     GATE_SSL=0
  - 局域网 IP 变了：        删掉 auth_gate/certs/ 再启动，会按新地址重新生成
  - 云端托管（平台已在上层做 HTTPS，start.sh 里有 GATE_HTTPS=1）：这里不再套一层证书
"""
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
CERT_DIR = Path(os.environ.get('GATE_CERT_DIR') or (_HERE / 'certs'))


def _lan_names():
    """证书里要写上哪些地址：localhost、本机主机名、所有 IPv4（含局域网 IP）。"""
    names, ips = ['localhost'], ['127.0.0.1']
    try:
        host = socket.gethostname()
        if host and host not in names:
            names.append(host)
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            if info[4][0] not in ips:
                ips.append(info[4][0])
    except Exception:
        pass
    return names, ips


def _gen_cert_python(crt, key, names, ips):
    import datetime
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[-1])])
    san = [x509.DNSName(n) for n in names] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(k.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(k, hashes.SHA256()))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.TraditionalOpenSSL,
                                    serialization.NoEncryption()))
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _gen_cert_openssl(crt, key, names, ips):
    """环境里没有 cryptography 时，退回用 openssl 命令行生成。"""
    import shutil
    import subprocess
    exe = shutil.which('openssl')
    if not exe:
        for cand in (r'D:\Git\usr\bin\openssl.exe', r'C:\Program Files\Git\usr\bin\openssl.exe',
                     '/usr/bin/openssl'):
            if os.path.exists(cand):
                exe = cand
                break
    if not exe:
        raise RuntimeError('既没有 cryptography 也没有 openssl')
    san = ','.join([f'DNS:{n}' for n in names] + [f'IP:{i}' for i in ips])
    subprocess.run([exe, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '3650',
                    '-subj', f'/CN={names[-1]}', '-addext', f'subjectAltName={san}',
                    '-keyout', str(key), '-out', str(crt)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ensure_cert():
    """生成（或复用）本机自签证书。返回 (证书路径, 私钥路径)；生成不了返回 None（退回 HTTP）。"""
    crt, key = CERT_DIR / 'server.crt', CERT_DIR / 'server.key'
    if crt.exists() and key.exists():
        return str(crt), str(key)
    names, ips = _lan_names()
    try:
        CERT_DIR.mkdir(parents=True, exist_ok=True)
        try:
            _gen_cert_python(crt, key, names, ips)
        except ImportError:
            _gen_cert_openssl(crt, key, names, ips)
    except Exception as e:
        print(f'[gate] 生成 HTTPS 证书失败（{e!r}），本次退回明文 HTTP', flush=True)
        return None
    print(f'[gate] 已生成本机自签证书：{crt}', flush=True)
    return str(crt), str(key)


def _exclusive_socket(host: str, port: int):
    """自建监听 socket：Windows 下 SO_EXCLUSIVEADDRUSE 独占端口，
    防止别的进程（自动重启脚本等）再用 127.0.0.1 细粒度绑定抢走流量绕过门卫。
    Linux / macOS 下用 SO_REUSEADDR：重启后 TIME_WAIT 的连接不会让端口绑不上（服务器上重启很常见）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    elif hasattr(socket, 'SO_REUSEADDR'):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    s.listen(2048)
    s.set_inheritable(False)
    return s


# ==================== 首次部署向导 ====================
# 新部署不该一上来就是 traceback 或者随机密码：数据库没配好、库里还没有管理员时，
# 直接在启动窗口里问一遍，写好配置、建好表、建好管理员，再启动服务。已配置的部署完全跳过。

def _stdin_mode():
    """GATE_SETUP_STDIN=1：按行从标准输入读答案（无人值守部署 / 自动化安装）。
    答案顺序 = 向导提问顺序：数据库地址、端口、用户名、库名、口令、（库不存在时）y、再输口令，
    然后 管理员账号、密码、密码。"""
    return os.environ.get('GATE_SETUP_STDIN', '') == '1'


def _can_ask():
    """能不能向部署者提问：标准输入按行给答案（GATE_SETUP_STDIN=1），或者标准输入和标准输出都是终端。
    否则（nohup、重定向、任务计划）绝不等待输入，直接给出配置指引后退出。
    （Windows 下把 stdin 重定向到 NUL 时 isatty() 仍可能返回 True，所以必须连 stdout 一起看。）"""
    if _stdin_mode():
        return True
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:
        return False


def _ask(label, default=''):
    tip = f'{label} [{default}]: ' if default else f'{label}: '
    try:
        v = input(tip).strip()
    except EOFError:
        return default
    return v or default


def _ask_password(label='口令'):
    if _stdin_mode():
        try:
            return sys.stdin.readline().rstrip('\r\n')
        except Exception:
            return ''
    import getpass
    try:
        return getpass.getpass(f'{label}（输入不回显）: ')
    except Exception:
        try:
            return input(f'{label}: ').strip()
        except EOFError:
            return ''


def _secrets_path():
    from pathlib import Path
    return Path(os.environ.get('MUMA_SECRETS_PATH') or (_HERE.parent / 'backend' / 'local.secrets.json'))


def _write_secrets(values):
    import json as _json
    p = _secrets_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if p.exists():
        try:
            data = _json.loads(p.read_text(encoding='utf-8')) or {}
        except Exception:
            data = {}
    data.update(values)
    p.write_text(_json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass
    return p


def _setup_db():
    """数据库没配好或连不上时，在启动窗口里问一遍；非交互环境给出明确指引后退出。"""
    sys.path.insert(0, str(_HERE.parent))
    try:
        from backend import db as kbdb
    except Exception as e:
        print(f'[setup] 加载数据库配置失败：{e!r}')
        sys.exit(2)
    bad = None
    if kbdb.configured():
        try:
            kbdb.probe()
            return                                  # 已配好且连得上：跳过
        except Exception as e:
            bad = e
            print(f'[setup] 数据库连不上：{e}')
            if not _can_ask():
                print(f'    请检查环境变量 MUMA_DB_* 或 {_secrets_path()}')
                sys.exit(2)
            print('    重新填写连接信息。')
    else:
        if not _can_ask():
            print('[setup] 还没有数据库配置。三选一：')
            print('    1) 设置环境变量 MUMA_DB_HOST / MUMA_DB_PORT / MUMA_DB_USER / MUMA_DB_PASSWORD / MUMA_DB_NAME')
            print('    2) 双击「启动看板-带登录.bat」，首次会在窗口里引导你填')
            print('    3) 无人值守：设 GATE_SETUP_STDIN=1，把答案按行喂给标准输入（顺序见 README）')
            sys.exit(2)
        print('=== 首次部署：配置数据库 ===')
        if not _stdin_mode():
            print('（输入前请切到英文输入法；中文输入法会把字母和回车吃掉）')

    cur = {}
    try:
        cur = kbdb._local_secrets()
    except Exception:
        pass
    host = _ask('数据库地址', os.environ.get('MUMA_DB_HOST') or cur.get('MUMA_DB_HOST') or 'localhost')
    port = _ask('端口', os.environ.get('MUMA_DB_PORT') or cur.get('MUMA_DB_PORT') or '3306')
    user = _ask('用户名', os.environ.get('MUMA_DB_USER') or cur.get('MUMA_DB_USER') or 'kb')
    name = _ask('库名', os.environ.get('MUMA_DB_NAME') or cur.get('MUMA_DB_NAME') or 'kb_tables')
    try:
        port_i = int(port)
    except ValueError:
        print('[setup] 端口必须是数字。')
        sys.exit(2)
    for _attempt in range(3):
        pw = _ask_password('数据库口令')
        # 空口令不在这里拦：先拿它连一次，连得上就接受（本机开发库常见），但提醒只适合本机
        cfg = {'host': host, 'port': port_i, 'user': user, 'password': pw,
               'database': name, 'charset': 'utf8mb4', 'connect_timeout': 5}
        print('    正在连接…', end='', flush=True)
        try:
            kbdb.probe(cfg)
        except Exception as e:
            print('失败')
            print(f'    {e}')
            if 'Unknown database' in str(e) or '(1049' in str(e):
                if _ask('    库不存在，现在创建？(y/N)', 'N').lower() in ('y', 'yes', '是'):
                    try:
                        import pymysql
                        c2 = dict(cfg)
                        c2.pop('database', None)
                        con = pymysql.connect(**c2)
                        try:
                            with con.cursor() as cc:
                                cc.execute('CREATE DATABASE IF NOT EXISTS `%s` CHARACTER SET utf8mb4' % name)
                            con.commit()
                        finally:
                            con.close()
                        print(f'    已创建库 {name}')
                    except Exception as e2:
                        print(f'    创建失败：{e2}')
            continue
        print('成功')
        if not pw:
            print('    注意：数据库口令为空，只适合本机开发库；对外部署请给账号设置口令。')
        if user.strip().lower() == 'root':
            print('    建议：给本应用单独建一个只对目标库有权限的账号，不要用 root（见 README「安全」）。')
        p = _write_secrets({'MUMA_DB_HOST': host, 'MUMA_DB_PORT': str(port_i), 'MUMA_DB_USER': user,
                            'MUMA_DB_PASSWORD': pw, 'MUMA_DB_NAME': name})
        print(f'    已保存到 {p}（已在 .gitignore 里，不要外发）')
        kbdb.reload()
        return
    print('[setup] 数据库没有配好，已停止。')
    sys.exit(2)


def _setup_admin():
    """库里还没有管理员时，让部署者自己定账号密码（不再随机生成）。"""
    from backend.engine import ex, init_schema, q
    try:
        has = q("SELECT id FROM kb_user WHERE role='admin' AND status=1 LIMIT 1", one=True)
    except Exception:
        # 空库：还没有 kb_* 表，先建表再判断（已配置的部署只多一条很便宜的查询）
        try:
            init_schema()
        except Exception as e:
            print(f'[setup] 初始化表结构失败：{e}')
            sys.exit(2)
        has = q("SELECT id FROM kb_user WHERE role='admin' AND status=1 LIMIT 1", one=True)
    if has:
        return
    if not _can_ask():
        return                                     # 非交互：退回 gate 的随机引导（会打印在日志里）
    print('=== 创建管理员账号 ===')
    u = (_ask('管理员账号', 'admin') or 'admin')[:64]
    pw = ''
    for _try in range(5):                         # 最多问 5 轮：标准输入读完（EOF）时不会死循环
        pw = _ask_password('管理员密码（至少 6 位）')
        if len(pw) < 6:
            print('    太短了，至少 6 位。')
            continue
        if _ask_password('再输一次') != pw:
            print('    两次不一致。')
            continue
        break
    else:
        print('[setup] 管理员密码没有设置成功，已停止。')
        sys.exit(2)
    from gate import hash_password                 # 此时数据库已就绪，gate 可以安全导入
    ex("INSERT INTO kb_user(username,password_hash,real_name,role,status,created_by) "
       "VALUES(%s,%s,%s,'admin',1,'setup')", (u, hash_password(pw), u))
    print(f'    管理员 {u} 已创建，登录后请尽快改密码。')


def _run_server(server, sock):
    """用 uvicorn 自己的事件循环工厂建循环，装上异常处理器后再跑 serve（只吞掉一种“客户端断开”的噪音）。

    Windows + Python 3.11 的解释器问题：浏览器刷新 / 关标签页时那条推送长连接被硬断开，asyncio 关闭这条
    连接时会再抛一次 ConnectionResetError(10054)，被当成“回调异常”整段堆栈打进启动窗口。它不影响任何连接，
    但会让窗口刷满红字。Linux 上没有这个问题。"""
    import asyncio

    try:
        factory = server.config.get_loop_factory()      # uvicorn 0.36+；返回事件循环工厂（Windows 上是 ProactorEventLoop）
    except Exception:
        factory = None
    loop = factory() if factory else asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _quiet(lp, context):
        exc = context.get('exception')
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)) and \
                '_call_connection_lost' in str(context.get('message', '')):
            return
        lp.default_exception_handler(context)

    loop.set_exception_handler(_quiet)
    try:
        loop.run_until_complete(server.serve(sockets=[sock]))
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()
        asyncio.set_event_loop(None)


if __name__ == "__main__":
    # 服务器（Linux）默认跑在 UTC，日期公式 / 变动历史会差 8 小时：没显式设 TZ 时按东八区（GATE_TZ 可改）。
    # Windows 没有 time.tzset()，按系统时区走，不受影响。
    if hasattr(time, 'tzset') and not os.environ.get('TZ'):
        os.environ['TZ'] = os.environ.get('GATE_TZ', 'Asia/Shanghai')
        try:
            time.tzset()
        except Exception:
            pass
    host = os.environ.get('GATE_HOST', '0.0.0.0')
    port = int(os.environ.get('GATE_PORT', '8614'))
    # 本机启动默认加密；云端托管（GATE_HTTPS=1，平台已在上层做了 HTTPS）或显式 GATE_SSL=0 时走明文
    default_ssl = '0' if os.environ.get('GATE_HTTPS') == '1' else '1'
    cert = ensure_cert() if os.environ.get('GATE_SSL', default_ssl) != '0' else None
    if cert:
        os.environ['GATE_COOKIE_SECURE'] = '1'     # 走 HTTPS：Cookie 标记为 Secure。必须在导入 gate 之前设
    # 首次部署：先确认数据库配置和管理员账号（已配好的部署会直接跳过）
    _setup_db()
    _setup_admin()
    # 这里才开始导入 gate：它导入时会读环境变量、挂门卫层
    from gate import app, ensure_admin_exists     # noqa: E402  加载原应用 + 挂门卫层

    ensure_admin_exists()
    scheme = 'https' if cert else 'http'
    shown = '127.0.0.1' if host == '0.0.0.0' else host
    url = f'{scheme}://{shown}:{port}'
    print(f"牧马人商务合作看板（带登录，{scheme.upper()}）→ {url}")
    print(f"    登录页 {url}/login    用户管理 {url}/__auth/admin")
    if host == '0.0.0.0':
        note = '（首次打开浏览器会提示证书不受信任，点“继续前往”）' if cert else ''
        for ip in _lan_names()[1]:
            if ip != '127.0.0.1':
                print(f"    局域网同事访问：{scheme}://{ip}:{port}{note}")
    if not cert:
        if os.environ.get('GATE_HTTPS') == '1':
            print('    注意：GATE_HTTPS=1 表示“上层代理已做 HTTPS”，应用这一段是明文；'
                  '请只监听 127.0.0.1（GATE_HOST=127.0.0.1），不要把这个端口暴露到外网。')
        else:
            print('    注意：当前是明文 HTTP，密码在网络上不加密；只适合在本机临时调试；'
                  '对外请用域名 + 正式证书（见 README「部署到服务器」）。')
    import uvicorn
    try:
        sock = _exclusive_socket(host, port)
    except OSError as e:
        print(f"[gate] 端口 {port} 绑定失败（可能旧服务还没停或被占用）：{e}")
        sys.exit(1)
    extra = dict(ssl_certfile=cert[0], ssl_keyfile=cert[1]) if cert else {}
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", **extra))
    if os.environ.get('GATE_OPEN_BROWSER', '1') != '0':
        # 等服务起来再开浏览器（以前是 bat 里先开，页面常常比服务早一步）
        threading.Timer(1.2, lambda: webbrowser.open(url + '/login')).start()
    _run_server(server, sock)
