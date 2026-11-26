@echo off
title 牧马人线上多维表（带登录）
cd /d "%~dp0"
echo 正在启动 牧马人线上多维表（带登录门卫，HTTPS）...
echo   未登录只能看登录页；登录后按项目/表授权读写；建表/管列/管用户需管理员。
echo   局域网同事用  https://这台电脑的局域网IP:8614  访问，启动后下面会列出具体地址。
echo   第一次打开浏览器会提示证书不受信任，点“继续前往”即可。
echo   登录页: https://127.0.0.1:8614/login   用户管理: https://127.0.0.1:8614/__auth/admin
echo   只想本机访问：先执行  set GATE_HOST=127.0.0.1  再运行本文件。
python auth_gate\run_gate.py
pause
