# 有害样本 - 飞书机器人集成指南

## 系统架构

```
┌─────────────────────┐
│   飞书群聊用户       │
└──────────┬──────────┘
           │ 消息
           ▼
┌─────────────────────┐
│  飞书开放平台 API    │
└──────────┬──────────┘
           │ 事件回调
           ▼
┌─────────────────────┐
│ 飞书机器人服务      │ (feishu_bot.py)
│ (本地/云服务器)     │
└──────────┬──────────┘
           │ API 调用
           ▼
┌─────────────────────┐
│  后端服务 (FastAPI) │ (云服务器已部署)
│  - 创建任务         │
│  - 查询状态         │
│  - 下载文件         │
└─────────────────────┘
```

## 快速开始

### 第 1 步：准备环境

#### 1.1 查看后端信息
```bash
# 确认后端服务地址
BACKEND_IP = your-cloud-server-ip
BACKEND_PORT = 8787
BACKEND_PASSWORD = (从云服务器配置获取)
```

#### 1.2 创建飞书应用（如未创建）
```bash
# 使用 feishu-cli
cd ../feishu-cli
./feishu-cli config create-app --save

# 记下：App ID 和 App Secret
```

### 第 2 步：配置机器人

#### 2.1 复制环境变量文件
```bash
cp .env.bot.example .env.bot
```

#### 2.2 编辑 `.env.bot`
```bash
FEISHU_APP_ID=cli_xxxxx              # 从飞书创建的应用
FEISHU_APP_SECRET=xxxxxx             # 从飞书应用
FEISHU_BOT_TOKEN=xxxxx               # Bot Token (应用凭证)

BACKEND_URL=http://your-ip:8787      # 云服务器地址
BACKEND_PASSWORD=change-this-password # 后端密码

BOT_HOST=0.0.0.0
BOT_PORT=8000
```

### 第 3 步：本地测试

#### 3.1 安装依赖
```bash
# 如果还未安装
pip install fastapi uvicorn aiohttp requests

# 或使用 requirements.txt（需要创建）
pip install -r requirements.txt
```

#### 3.2 启动机器人
```bash
# 方式 1：直接运行
python feishu_bot.py

# 方式 2：使用 uvicorn
uvicorn feishu_bot:app --host 0.0.0.0 --port 8000

# 输出：
# INFO:     Uvicorn running on http://0.0.0.0:8000
```

#### 3.3 验证机器人运行
```bash
# 新开终端
curl http://localhost:8000/health

# 输出：
# {"status":"ok","time":"2024-05-31T..."}
```

### 第 4 步：配置飞书事件订阅

#### 4.1 内网穿透（本地测试）
```bash
# 下载 ngrok: https://ngrok.com/download
# 启动 ngrok
ngrok http 8000

# 输出：
# Forwarding    https://xxxx-xx-xxx.ngrok.io -> http://localhost:8000
```

#### 4.2 配置事件订阅
1. 打开飞书应用后台：https://open.feishu.cn/app
2. 选择你的应用
3. 左侧菜单 → "事件订阅"
4. 设置：
   - 请求地址：`https://xxxx.ngrok.io/feishu/webhook` （或你的服务器地址）
   - 验证 Token：应用凭证中的 Bot Token
5. 订阅事件：
   - `im.message.receive_v1`（接收消息）

### 第 5 步：测试机器人

#### 5.1 在飞书群聊中测试
1. 将机器人加入一个飞书群聊
2. 在群中发送命令：
   ```
   /help
   ```
3. 机器人应回复帮助信息

#### 5.2 测试各命令
```
/help          - 显示帮助
/list          - 列出任务
/status abc123 - 查询任务状态
```

## 生产部署

### 方案 A：云服务器部署

#### 步骤
1. SSH 进入云服务器
2. 克隆或上传项目
3. 安装依赖：`pip install -r requirements.txt`
4. 配置 `.env.bot`（设置 `BACKEND_URL` 为 localhost）
5. 使用 systemd 或 Docker 启动服务

#### systemd 服务配置
```ini
# /etc/systemd/system/feishu-bot.service
[Unit]
Description=Feishu Bot Service
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/harmful-sample-web
Environment="PATH=/home/ubuntu/venv/bin"
ExecStart=/home/ubuntu/venv/bin/python feishu_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

启动：
```bash
sudo systemctl daemon-reload
sudo systemctl enable feishu-bot
sudo systemctl start feishu-bot
sudo systemctl status feishu-bot
```

#### Nginx 反向代理
```nginx
location /feishu {
    proxy_pass http://localhost:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

### 方案 B：Docker 部署

#### Dockerfile
```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["python", "feishu_bot.py"]
```

#### docker-compose.yml
```yaml
version: '3.8'
services:
  bot:
    build: .
    container_name: feishu-bot
    ports:
      - "8000:8000"
    env_file: .env.bot
    restart: always
```

启动：
```bash
docker-compose up -d
```

## 故障排查

### 机器人无法启动
```bash
# 检查依赖
pip list | grep -E "fastapi|uvicorn|requests"

# 检查端口占用
lsof -i :8000

# 查看错误日志
python feishu_bot.py 2>&1 | tee bot.log
```

### 无法连接后端
```bash
# 测试后端连接
curl http://BACKEND_IP:8787/health

# 检查防火墙
sudo ufw status
sudo ufw allow 8787

# 检查 .env.bot 配置
cat .env.bot | grep BACKEND
```

### 无法收到飞书消息
1. 检查飞书事件订阅配置
2. 确认请求地址是否正确（HTTPS）
3. 验证 Bot Token 是否匹配
4. 查看飞书后台的事件日志

### Token 过期
机器人会自动刷新 token，如果仍然失败：
```bash
# 检查应用凭证
# https://open.feishu.cn/app → 凭证与基础信息

# 检查 .env.bot 中的 App ID 和 Secret
cat .env.bot | grep FEISHU
```

## 功能扩展

### 添加更多命令

在 `handle_text_command()` 函数中添加：

```python
elif cmd == "/submit":
    # 处理上传任务
    bot_client.send_text_message(sender_id, "请上传 Excel 文件...")

elif cmd == "/download":
    # 处理文件下载
    task_id = parts[1] if len(parts) > 1 else ""
    filename = parts[2] if len(parts) > 2 else ""
    # 调用 backend.download_file()
```

### 支持文件上传

扩展机器人以支持用户上传文件：

```python
# 在 handle_message_event 中处理
if msg_type == "file":
    file_key = message.get("file_key", "")
    # 下载文件并创建任务
```

### 支持卡片消息

发送更好看的卡片消息：

```python
card_json = {
    "config": {"wide_screen_mode": True},
    "elements": [
        {
            "tag": "div",
            "text": {
                "content": "任务已创建",
                "tag": "lark_md"
            }
        }
    ]
}
bot_client.send_card_message(user_id, json.dumps(card_json))
```

## 监控和维护

### 日志查看
```bash
# 实时日志
tail -f bot.log

# Systemd 日志
journalctl -u feishu-bot -f

# Docker 日志
docker logs -f feishu-bot
```

### 定期备份
```bash
# 备份数据库和配置
cp -r data/ backup/$(date +%Y%m%d)/
cp .env.bot backup/$(date +%Y%m%d)/
```

### 定期更新
```bash
# 检查依赖更新
pip list --outdated

# 更新依赖
pip install -r requirements.txt --upgrade
```

## 安全建议

1. **凭证管理**
   - 不要提交 `.env.bot` 到 Git
   - 使用强密码
   - 定期轮换 App Secret

2. **网络安全**
   - 使用 HTTPS（Let's Encrypt 免费证书）
   - 配置防火墙，仅允许必要的端口
   - 验证飞书签名（可选扩展）

3. **数据安全**
   - 定期备份数据
   - 配置访问日志
   - 监控异常活动

## 相关文档

- 飞书 OpenAPI 文档：https://open.feishu.cn/document/
- FastAPI 文档：https://fastapi.tiangolo.com/
- 项目原始后端：`app/main.py`

## 常见问题

**Q: 机器人和后端如何通信？**
A: 机器人是独立的服务，通过 HTTP REST API 调用后端服务。

**Q: 可以在飞书中上传 Excel 文件吗？**
A: 可以，需要扩展 `handle_message_event()` 来处理文件消息。

**Q: 机器人会监听群聊中的所有消息吗？**
A: 是的，在 `handle_text_command()` 中可以选择响应或忽略。

**Q: 多台云服务器如何部署？**
A: 每台服务器运行独立的机器人实例，或使用负载均衡。

---

**立即开始**：按照"快速开始"的 5 个步骤，15 分钟内让机器人运行起来！
