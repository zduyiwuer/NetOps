@echo off
REM ============================================================
REM  NetOps 重启脚本（Windows）
REM
REM  用法：
REM    restart.bat           停止后重新后台启动
REM    restart.bat --rebuild 重启并强制重新构建前端
REM ============================================================
setlocal

set "ROOT=%~dp0"

echo ==^> 停止服务
set "NETOPS_NO_PAUSE=1"
call "%ROOT%stop.bat"
if errorlevel 1 echo     （停止时出现问题，继续启动）

echo.
echo ==^> 重新启动
if /i "%~1"=="--rebuild" (
  call "%ROOT%start.bat" --rebuild
) else (
  call "%ROOT%start.bat"
)
goto :finish

:finish
REM ---- 收尾：双击运行时暂停，避免窗口一闪而过 ----
if defined NETOPS_NO_PAUSE exit /b %ERRORLEVEL%
echo.
echo 按任意键关闭此窗口...
pause >nul
exit /b %ERRORLEVEL%
