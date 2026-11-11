@echo off
REM ============================================================
REM  NetOps 启动脚本（Windows）
REM
REM  用法：
REM    start.bat                后台启动（默认）
REM    start.bat --foreground   前台运行（Ctrl+C 停止）
REM    start.bat --rebuild      强制重新构建前端
REM
REM  后台运行产物：
REM    backend\data\run\netops.pid   进程 PID
REM    backend\data\logs\netops.log   运行日志
REM ============================================================
setlocal EnableDelayedExpansion

set "ROOT=%~dp0"
set "BACKEND=%ROOT%backend"
set "FRONTEND=%ROOT%frontend"
set "RUNDIR=%BACKEND%\data\run"
set "LOGDIR=%BACKEND%\data\logs"
set "PIDFILE=%RUNDIR%\netops.pid"
set "LOGFILE=%LOGDIR%\netops.log"

set "PORT=8900"
if defined NETOPS_PORT set "PORT=%NETOPS_PORT%"
set "HOST=0.0.0.0"
set "FOREGROUND=0"
set "FORCEBUILD=0"

:parse_args
if "%~1"=="" goto after_args
if /i "%~1"=="--foreground" set "FOREGROUND=1" & shift & goto parse_args
if /i "%~1"=="-f"          set "FOREGROUND=1" & shift & goto parse_args
if /i "%~1"=="--rebuild"  set "FORCEBUILD=1" & shift & goto parse_args
if /i "%~1"=="--help"     goto usage
if /i "%~1"=="-h"         goto usage
echo 未知参数：%~1（用 --help 查看用法）
goto :finish

:usage
echo NetOps 启动脚本（Windows）
echo.
echo   start.bat                后台启动（默认）
echo   start.bat --foreground   前台运行
echo   start.bat --rebuild      强制重新构建前端
exit /b 0

:after_args

REM ------------------------------------------------------------ 检查依赖
echo ==^> 检查依赖
where python >nul 2>nul || (echo 未找到 python，请先安装 Python 3.10+ & exit /b 1)
where node   >nul 2>nul || (echo 未找到 node，请先安装 Node.js 18+ & exit /b 1)

pushd "%BACKEND%"
python -c "import fastapi" >nul 2>nul || (
  echo ==^> 安装后端依赖
  python -m pip install -r requirements.txt
)

pushd "%FRONTEND%"
if not exist node_modules (
  echo ==^> 安装前端依赖
  call npm install
)
if "%FORCEBUILD%"=="1" goto do_build
if not exist dist goto do_build
goto build_done
:do_build
echo ==^> 构建前端
call npm run build
:build_done
popd

pushd "%BACKEND%"
if not exist data\backups mkdir data\backups
if not exist data\reports  mkdir data\reports
if not exist data\imports  mkdir data\imports
if not exist "%RUNDIR%"    mkdir "%RUNDIR%"
if not exist "%LOGDIR%"    mkdir "%LOGDIR%"

REM ------------------------------------------- 已在运行检查（PID 文件）
if exist "%PIDFILE%" (
  set "OLDPID="
  for /f "usebackq delims=" %%P in ("%PIDFILE%") do set "OLDPID=%%P"
  if not "!OLDPID!"=="" (
    python -m scripts.service_helper alive !OLDPID! >nul 2>nul
    if not errorlevel 1 (
      echo NetOps 已在运行（PID !OLDPID!），如需重启请执行 restart.bat
      popd
goto :finish
    )
  )
  del /q "%PIDFILE%" >nul 2>nul
)

REM ---------------------------------------------------- 端口占用检测
echo ==^> 检查端口 %PORT%
python -m scripts.service_helper port-busy %PORT% >nul 2>nul
if not errorlevel 1 (
  echo 端口 %PORT% 已被占用，请先释放该端口
  popd
goto :finish
)

REM ------------------------------------------------------------ 前台模式
if "%FOREGROUND%"=="1" (
  echo ==^> 前台启动 NetOps  -^>  http://localhost:%PORT%
  echo     按 Ctrl+C 停止
  python -m uvicorn app.main:app --host %HOST% --port %PORT%
  popd
goto :finish
)

REM ------------------------------------------------------------ 后台模式
echo ==^> 后台启动 NetOps

REM 日志按次切分，避免单文件无限增长
if exist "%LOGFILE%" (
  for %%F in ("%LOGFILE%") do (
    if %%~zF GTR 5242880 (
      move /y "%LOGFILE%" "%LOGDIR%\netops_%RANDOM%.log" >nul 2>nul
    )
  )
)

REM 用 Python 以 Windows 原生分离标志启动脱离控制台的进程：
REM   DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB
REM 这样父窗口 / 终端关闭后服务仍继续运行。
set "NEWPID="
for /f "usebackq delims=" %%P in (`python -m scripts.service_helper spawn "%LOGFILE%" "%BACKEND%" python -m uvicorn app.main:app --host %HOST% --port %PORT% 2^>nul`) do set "NEWPID=%%P"

if not defined NEWPID (
  echo 启动失败：无法创建后台进程，请检查日志：%LOGFILE%
  popd
goto :finish
)
REM PID 文件由 helper 统一用 LF 换行写入
REM （bat 的 for /f 读 CRLF 会把 \r 带进变量，导致 taskkill 拿到错误 PID）
python -m scripts.service_helper write-pid "%PIDFILE%" %NEWPID%
REM ---------------------------------------------------- 健康检查等待就绪
echo ==^> 等待服务就绪
for /L %%I in (1,1,60) do (
  REM 必须「进程存活」且「端口可连」才算就绪，避免只查端口导致假成功
  python -m scripts.service_helper healthy %NEWPID% %PORT% >nul 2>nul
  if not errorlevel 1 goto ready
  echo | set /p "=."
  timeout /t 1 /nobreak >nul
)
goto not_ready

:ready
echo.
echo     启动成功（PID %NEWPID%）
echo     访问地址 : http://localhost:%PORT%
echo     运行日志 : %LOGFILE%
echo     停止服务 : stop.bat
popd
goto :finish

:not_ready
echo.
echo 服务在 60 秒内未就绪。最近日志：
powershell -NoProfile -Command "Get-Content -Tail 20 '%LOGFILE%' -EA SilentlyContinue"
echo 进程仍在运行，可用 stop.bat 停止
popd
goto :finish

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
