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
    防止别的进程（自动重启脚本等）再用 127.0.0.1 细粒度绑定抢走流量绕过门卫。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    s.bind((host, port))
    s.listen(2048)
    s.set_inheritable(False)
    return s


if __name__ == "__main__":
    host = os.environ.get('GATE_HOST', '0.0.0.0')
    port = int(os.environ.get('GATE_PORT', '8614'))
    # 本机启动默认加密；云端托管（GATE_HTTPS=1，平台已在上层做了 HTTPS）或显式 GATE_SSL=0 时走明文
    default_ssl = '0' if os.environ.get('GATE_HTTPS') == '1' else '1'
    cert = ensure_cert() if os.environ.get('GATE_SSL', default_ssl) != '0' else None
    if cert:
        os.environ['GATE_COOKIE_SECURE'] = '1'     # 走 HTTPS：Cookie 标记为 Secure。必须在导入 gate 之前设
    # 这里才开始导入 gate：它导入时会读环境变量、初始化表结构
    from gate import app, ensure_admin_exists     # noqa: E402  加载原应用 + 挂门卫层

    ensure_admin_exists()
    scheme = 'https' if cert else 'http'
    shown = '127.0.0.1' if host == '0.0.0.0' else host
    url = f'{scheme}://{shown}:{port}'
    print(f"牧马人商务合作看板（带登录，{scheme.upper()}）→ {url}")
    print(f"    登录页 {url}/login    用户管理 {url}/__auth/admin")
    if host == '0.0.0.0':
        for ip in _lan_names()[1]:
            if ip != '127.0.0.1':
                print(f"    局域网同事访问：{scheme}://{ip}:{port}（首次打开浏览器会提示证书不受信任，点“继续前往”）")
    if not cert:
        print('    注意：当前是明文 HTTP，密码在网络上不加密；只适合在本机临时调试。')
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
    server.run(sockets=[sock])
