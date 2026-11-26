@echo off
rem 这个入口也走登录网关 + HTTPS，和「启动看板-带登录.bat」完全一样。
rem 以前这里是 python run.py 8614（明文、免登录）——谁都能直接改数据，已停用。
title 牧马人线上多维表（带登录）
cd /d "%~dp0"
call "启动看板-带登录.bat"
