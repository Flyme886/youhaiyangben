#!/bin/bash
# 部署飞书机器人到云服务器
# 在本地运行：bash deploy/deploy_bot.sh

set -e

SERVER="root@43.138.247.131"
PORT=2222
REMOTE="/opt/harmful-sample-web"

echo "=== 上传机器人文件 ==="
scp -P $PORT feishu_bot.py feishu_bot_requirements.txt "$SERVER:$REMOTE/"

echo "=== 安装依赖 ==="
ssh -p $PORT "$SERVER" "cd $REMOTE && venv/bin/pip install -r feishu_bot_requirements.txt -q"

echo "=== 安装 systemd 服务 ==="
scp -P $PORT deploy/feishu-bot.service "$SERVER:/etc/systemd/system/"
ssh -p $PORT "$SERVER" "systemctl daemon-reload && systemctl enable feishu-bot && systemctl restart feishu-bot"

echo "=== 服务状态 ==="
ssh -p $PORT "$SERVER" "systemctl status feishu-bot --no-pager"

echo "=== 完成 ==="
