@echo off
REM 有害样本 - 飞书机器人启动脚本 (Windows)

setlocal enabledelayedexpansion

echo ================================
echo 有害样本 - 飞书机器人启动
echo ================================

REM 检查 Python
echo.
echo [1/4] 检查 Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo 错误: Python 未安装或不在 PATH 中
    pause
    exit /b 1
)
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo 找到 Python %PYTHON_VERSION%

REM 检查依赖
echo.
echo [2/4] 检查依赖...
if not exist "requirements-bot.txt" (
    echo 错误: requirements-bot.txt 不存在
    pause
    exit /b 1
)

python -c "import fastapi" 2>nul
if errorlevel 1 (
    echo 依赖缺失，正在安装...
    pip install -r requirements-bot.txt
    if errorlevel 1 (
        echo 安装失败
        pause
        exit /b 1
    )
    echo 依赖安装完成
) else (
    echo 依赖已安装
)

REM 检查配置文件
echo.
echo [3/4] 检查配置...
if not exist ".env.bot" (
    if exist ".env.bot.example" (
        echo 警告: 未找到 .env.bot，从模板创建...
        copy .env.bot.example .env.bot
        echo 警告: 请编辑 .env.bot 并填入实际配置
        pause
        exit /b 1
    )
) else (
    echo 配置文件已存在
)

REM 启动机器人
echo.
echo ================================
echo 启动机器人服务...
echo ================================
echo.
echo 机器人运行在: http://0.0.0.0:8000
echo 飞书事件订阅地址: https://your-domain/feishu/webhook
echo 健康检查: http://localhost:8000/health
echo.
echo 按 Ctrl+C 停止服务
echo.

python feishu_bot.py

pause
