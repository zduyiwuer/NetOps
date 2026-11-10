@echo off
REM NetOps 一键启动（Windows）
setlocal
set ROOT=%~dp0
set PORT=8900

echo ==^> 检查依赖
where python >nul 2>nul || (echo 未找到 python & exit /b 1)
where node >nul 2>nul || (echo 未找到 node & exit /b 1)

cd /d "%ROOT%backend"
python -c "import fastapi" >nul 2>nul || (
  echo ==^> 安装后端依赖
  python -m pip install -r requirements.txt
)

cd /d "%ROOT%frontend"
if not exist node_modules (
  echo ==^> 安装前端依赖
  call npm install
)
if not exist dist (
  echo ==^> 构建前端
  call npm run build
)

cd /d "%ROOT%backend"
if not exist data\backups mkdir data\backups
if not exist data\reports mkdir data\reports
if not exist data\imports mkdir data\imports

echo ==^> 启动 NetOps  -^>  http://localhost:%PORT%
python -m uvicorn app.main:app --host 0.0.0.0 --port %PORT%
endlocal
