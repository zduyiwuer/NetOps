@echo off
REM ============================================================
REM  NetOps 停止脚本（Windows）
REM
REM  用法：
REM    stop.bat           优雅停止（最多等待 10 秒后强制结束）
REM    stop.bat --force   立即强制结束
REM ============================================================
setlocal EnableDelayedExpansion

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "PIDFILE=%BACKEND%\data\run\netops.pid"
set "LOGFILE=%BACKEND%\data\logs\netops.log"
set "PORT=8900"
if defined NETOPS_PORT set "PORT=%NETOPS_PORT%"

set "FORCE=0"
if /i "%~1"=="--force" set "FORCE=1"
if /i "%~1"=="-f"      set "FORCE=1"
if /i "%~1"=="--help"  goto usage
if /i "%~1"=="-h"      goto usage
if not "%~1"=="" (
  echo 未知参数：%~1（用 --help 查看用法）
goto :finish
)

REM 参数合法，继续执行


pushd "%BACKEND%"

if not exist "%PIDFILE%" (
  echo 未找到 PID 文件，尝试按端口清理残留进程...
  python -m scripts.service_helper kill-netops-on-port %PORT% >nul 2>nul
  timeout /t 1 /nobreak >nul
  echo 服务未在运行（无 PID 文件）
  popd
goto :finish
)

set "PID="
for /f "usebackq delims=" %%P in (`python -m scripts.service_helper read-pid "%PIDFILE%" 2^>nul`) do set "PID=%%P"

if "!PID!"=="" (
  del /q "%PIDFILE%" >nul 2>nul
  echo PID 文件为空，已清理。服务未在运行。
  popd
goto :finish
)

python -m scripts.service_helper alive !PID! >nul 2>nul
if errorlevel 1 (
  echo 进程 !PID! 不存在（可能已退出），清理 PID 文件
  del /q "%PIDFILE%" >nul 2>nul
  echo 服务未在运行
  popd
goto :finish
)

echo ==^> 停止 NetOps（PID !PID!）

if "%FORCE%"=="1" (
  echo     强制结束...
  python -m scripts.service_helper kill !PID! --force >nul 2>nul
  timeout /t 1 /nobreak >nul
  del /q "%PIDFILE%" >nul 2>nul
  echo 已强制结束
  popd
goto :finish
)

REM 第一步：发送终止信号
echo     发送终止信号...
python -m scripts.service_helper kill !PID! >nul 2>nul

set "WAITED=0"
:wait_loop
if !WAITED! GEQ 10 goto force_kill
python -m scripts.service_helper alive !PID! >nul 2>nul
if errorlevel 1 (
  del /q "%PIDFILE%" >nul 2>nul
  echo     已停止（用时 !WAITED!s）
  popd
goto :finish
)
timeout /t 1 /nobreak >nul
set /a WAITED+=1
goto wait_loop

:force_kill
echo     等待超时（10s），强制结束...
python -m scripts.service_helper kill !PID! --force >nul 2>nul
timeout /t 1 /nobreak >nul
python -m scripts.service_helper kill-netops-on-port %PORT% >nul 2>nul
del /q "%PIDFILE%" >nul 2>nul
echo 已强制停止
popd
goto :finish

:usage
echo NetOps 停止脚本（Windows）
echo.
echo   stop.bat           优雅停止
echo   stop.bat --force   立即强制结束
exit /b 0

:finish
REM ---- 收尾：双击运行时暂停，避免窗口一闪而过 ----
if defined NETOPS_NO_PAUSE exit /b %ERRORLEVEL%
echo.
echo 按任意键关闭此窗口...
pause >nul
goto :finish

:finish
REM ---- 收尾：双击运行时暂停，避免窗口一闪而过 ----
if defined NETOPS_NO_PAUSE exit /b %ERRORLEVEL%
echo.
echo 按任意键关闭此窗口...
pause >nul
exit /b %ERRORLEVEL%
