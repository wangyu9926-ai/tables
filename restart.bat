@echo off
chcp 936 >nul
rem 多维表格（开放版）重启：改了 .py 或 .env 后双击生效。
setlocal
cd /d "%~dp0backend"

set PORT=8555
for /f "usebackq eol=# tokens=1,2 delims==" %%a in (`findstr /b /i "PORT=" .env 2^>nul`) do set PORT=%%b

echo [1/2] 结束旧进程（端口 %PORT%）...
set PID=
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :%PORT% ^| findstr LISTENING') do set PID=%%a
if not "%PID%"=="" (
  echo   结束 PID=%PID%
  taskkill /F /PID %PID% >nul 2>&1
  ping -n 3 127.0.0.1 >nul
)

echo [2/2] 启动新进程...
start "MTables Backend" /min cmd /c "python run.py >> ..\.uvicorn.log 2>&1"
echo   已重启（等 2~3 秒刷新浏览器即可）
ping -n 3 127.0.0.1 >nul
exit /b 0
