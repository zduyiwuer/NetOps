@echo off
REM ============================================================
REM  NetOps 服务状态查看（Windows）
REM ============================================================
setlocal EnableDelayedExpansion

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "PIDFILE=%BACKEND%\data\run\netops.pid"
set "LOGFILE=%BACKEND%\data\logs\netops.log"
set "PORT=8900"
if defined NETOPS_PORT set "PORT=%NETOPS_PORT%"

echo === NetOps 服务状态 ===

pushd "%BACKEND%"

if not exist "%PIDFILE%" (
  echo 状态    : 未运行（无 PID 文件）
  python -m scripts.service_helper reachable %PORT% >nul 2>nul
  if not errorlevel 1 echo 提示    : 端口 %PORT% 有服务在监听，但缺少 PID 文件，请执行 stop.bat 清理
  popd
  exit /b 1
)

set "PID="
for /f "usebackq delims=" %%P in ("%PIDFILE%") do set "PID=%%P"

if "!PID!"=="" (
  echo 状态    : 未运行（PID 文件为空）
  popd
  exit /b 1
)

python -m scripts.service_helper alive !PID! >nul 2>nul
if errorlevel 1 (
  echo 状态    : 未运行（PID 文件已失效，进程不存在）
  popd
  exit /b 1
)

echo 状态    : 运行中
echo PID     : !PID!

python -m scripts.service_helper reachable %PORT% >nul 2>nul
if errorlevel 1 (
  echo 健康    : 异常（端口 %PORT% 无法连接，进程可能仍在启动或已卡死）
) else (
  echo 健康    : 正常（端口 %PORT% 可连接）
)

echo 日志    : %LOGFILE%
if exist "%LOGFILE%" (
  for %%F in ("%LOGFILE%") do echo 日志大小: %%~zF 字节
)

popd
exit /b 0
