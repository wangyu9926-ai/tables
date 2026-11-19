@echo off
chcp 936 >nul
rem 多维表格（开放版）启动：双击即可。首次使用先看 README.md 完成安装。
setlocal
cd /d "%~dp0backend"

set PORT=8555
for /f "usebackq eol=# tokens=1,2 delims==" %%a in (`findstr /b /i "PORT=" .env 2^>nul`) do set PORT=%%b
set URL=http://127.0.0.1:%PORT%

echo.
echo [1/3] 启动后端（端口 %PORT%）...
start "MTables Backend" /min cmd /c "python run.py >> ..\.uvicorn.log 2>&1"

echo [2/3] 等待服务就绪...
set /a n=0
:waitloop
ping -n 2 127.0.0.1 >nul
set CODE=
for /f "delims=" %%c in ('curl.exe -s -o nul -w "%%{http_code}" %URL%/api/health 2^>nul') do set CODE=%%c
if not "%CODE%"=="200" ( set /a n+=1 & if %n% LSS 30 goto waitloop )
if %n% GEQ 30 (
  echo   [ERROR] 服务 30 次未就绪，看日志：.uvicorn.log
  pause & exit /b 1
)

echo [3/3] 打开浏览器 %URL%
start "" %URL%
echo   游客直接可看；登录账号找管理员在「用户管理」里开通。
echo   （后端黑窗口请最小化，不要关闭；关了服务就停）
ping -n 3 127.0.0.1 >nul
exit /b 0
