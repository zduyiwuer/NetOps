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
call "%ROOT%stop.bat"
if errorlevel 1 echo     （停止时出现问题，继续启动）

echo.
echo ==^> 重新启动
if /i "%~1"=="--rebuild" (
  call "%ROOT%start.bat" --rebuild
) else (
  call "%ROOT%start.bat"
)
exit /b %errorlevel%
