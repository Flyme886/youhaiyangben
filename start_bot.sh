#!/bin/bash

# 有害样本 - 飞书机器人启动脚本

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}有害样本 - 飞书机器人启动${NC}"
echo -e "${GREEN}================================${NC}"

# 检查 Python
echo -e "\n${YELLOW}[1/4] 检查 Python...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}✗ Python 3 未安装${NC}"
    exit 1
fi
PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
echo -e "${GREEN}✓ Python ${PYTHON_VERSION}${NC}"

# 检查依赖
echo -e "\n${YELLOW}[2/4] 检查依赖...${NC}"
if [ ! -f "requirements-bot.txt" ]; then
    echo -e "${RED}✗ requirements-bot.txt 不存在${NC}"
    exit 1
fi

# 尝试导入必要模块
python3 -c "import fastapi" 2>/dev/null && echo -e "${GREEN}✓ 依赖已安装${NC}" || {
    echo -e "${YELLOW}⚠ 依赖缺失，正在安装...${NC}"
    pip3 install -r requirements-bot.txt
    echo -e "${GREEN}✓ 依赖安装完成${NC}"
}

# 检查配置文件
echo -e "\n${YELLOW}[3/4] 检查配置...${NC}"
if [ ! -f ".env.bot" ]; then
    if [ -f ".env.bot.example" ]; then
        echo -e "${YELLOW}⚠ 未找到 .env.bot，从模板创建...${NC}"
        cp .env.bot.example .env.bot
        echo -e "${YELLOW}⚠ 请编辑 .env.bot 并填入实际配置${NC}"
        exit 1
    fi
else
    echo -e "${GREEN}✓ 配置文件已存在${NC}"
fi

# 检查后端连接
echo -e "\n${YELLOW}[4/4] 检查后端连接...${NC}"
BACKEND_URL=$(grep "^BACKEND_URL=" .env.bot | cut -d'=' -f2)
if [ -z "$BACKEND_URL" ]; then
    echo -e "${RED}✗ BACKEND_URL 未配置${NC}"
    exit 1
fi

echo -e "${YELLOW}正在测试后端连接: ${BACKEND_URL}${NC}"
if curl -s "$BACKEND_URL/health" > /dev/null 2>&1; then
    echo -e "${GREEN}✓ 后端连接正常${NC}"
else
    echo -e "${YELLOW}⚠ 无法连接后端，但继续启动...${NC}"
fi

# 启动机器人
echo -e "\n${GREEN}================================${NC}"
echo -e "${GREEN}启动机器人服务...${NC}"
echo -e "${GREEN}================================${NC}"
echo -e "\n${YELLOW}机器人运行在: http://0.0.0.0:8000${NC}"
echo -e "${YELLOW}飞书事件订阅地址: https://your-domain/feishu/webhook${NC}"
echo -e "${YELLOW}健康检查: http://localhost:8000/health${NC}"
echo -e "\n${YELLOW}按 Ctrl+C 停止服务${NC}\n"

python3 feishu_bot.py
