@echo off
echo === 制造业Agent 启动脚本 ===

REM 1. 杀掉所有占用8000端口的进程
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000.*LISTENING"') do (
    echo 杀死进程 %%a (端口8000)
    taskkill /F /PID %%a 2>nul
)
timeout /t 3 /nobreak >nul

REM 2. 启动服务
echo 启动服务...
python -m uvicorn src.api.app:app --host 0.0.0.0 --port 8000 --reload
pause
