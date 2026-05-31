# 有害样本 - 飞书机器人集成总结

## ✅ 已完成

### 1. 飞书机器人核心代码
- ✅ `feishu_bot.py` - 完整的飞书机器人服务
  - 飞书 API 客户端（Token 管理、消息发送）
  - 后端 API 客户端（任务管理、文件下载）
  - 事件处理和命令路由
  - 支持命令：/help, /list, /status

### 2. 配置和启动脚本
- ✅ `.env.bot.example` - 环境变量模板
- ✅ `start_bot.sh` - Linux/Mac 启动脚本
- ✅ `start_bot.bat` - Windows 启动脚本
- ✅ `requirements-bot.txt` - Python 依赖列表

### 3. 完整文档
- ✅ `FEISHU_BOT_INTEGRATION.md` - 详细集成指南
- ✅ `feishu_bot_config.md` - 集成概览
- ✅ 本文档 - 快速参考

## 🚀 快速开始（5 步，15 分钟）

### 第 1 步：获取后端信息
```
询问：云服务器 IP、端口、访问密码
示例：
  后端地址: 1.2.3.4:8787
  密码: change-this-password
```

### 第 2 步：创建 .env.bot 配置
```bash
cp .env.bot.example .env.bot
```

编辑 `.env.bot`：
```bash
# 飞书应用凭证（从飞书创建的应用）
FEISHU_APP_ID=cli_xxxxx
FEISHU_APP_SECRET=xxxxxx
FEISHU_BOT_TOKEN=xxxxx

# 后端服务（云服务器）
BACKEND_URL=http://1.2.3.4:8787
BACKEND_PASSWORD=change-this-password

# 本地机器人服务
BOT_HOST=0.0.0.0
BOT_PORT=8000
```

### 第 3 步：安装依赖
```bash
# Windows
pip install -r requirements-bot.txt

# Linux/Mac
pip3 install -r requirements-bot.txt
```

### 第 4 步：启动机器人
```bash
# Windows
start_bot.bat

# Linux/Mac
bash start_bot.sh

# 或手动启动
python feishu_bot.py
```

验证：
```bash
curl http://localhost:8000/health
# 输出：{"status":"ok","time":"..."}
```

### 第 5 步：配置飞书事件订阅

#### 5.1 本地测试（使用 ngrok）
```bash
# 下载 ngrok: https://ngrok.com/download
ngrok http 8000
# 复制 https URL
```

#### 5.2 在飞书后台配置
1. https://open.feishu.cn/app → 你的应用
2. 左侧菜单：事件订阅
3. 请求地址：`https://xxxx.ngrok.io/feishu/webhook`
4. 验证 Token：Bot Token
5. 订阅事件：`im.message.receive_v1`

#### 5.3 测试
在飞书群中发送：
```
/help
```

机器人应该回复帮助信息 ✅

## 📋 文件清单

```
harmful-sample-web/
├── feishu_bot.py                    # ⭐ 机器人核心代码
├── .env.bot.example                 # 环境变量模板
├── requirements-bot.txt             # 依赖列表
├── start_bot.sh                     # Linux/Mac 启动脚本
├── start_bot.bat                    # Windows 启动脚本
├── FEISHU_BOT_INTEGRATION.md        # 详细集成指南
├── feishu_bot_config.md             # 集成概览
└── BOT_SETUP_SUMMARY.md             # 本文件（快速参考）
```

## 🎮 机器人命令

| 命令 | 说明 | 用法 |
|------|------|------|
| `/help` | 显示帮助 | `/help` |
| `/list` | 列出最近 10 个任务 | `/list` |
| `/status <task_id>` | 查询任务状态 | `/status abc123` |

## 🔧 配置说明

### 飞书应用配置
```
FEISHU_APP_ID          - 飞书应用 ID（创建应用时获取）
FEISHU_APP_SECRET      - 飞书应用密钥（创建应用时获取）
FEISHU_BOT_TOKEN       - 机器人 Token（应用凭证中获取）
```

**获取方式**：
1. https://open.feishu.cn/app
2. 创建新应用或选择现有应用
3. "凭证与基础信息" → 复制 App ID, App Secret, Bot Token

### 后端服务配置
```
BACKEND_URL            - 后端服务地址（http://ip:port）
BACKEND_PASSWORD       - 后端访问密码
```

**获取方式**：
- 后端地址：询问云服务器管理员
- 密码：查看后端的 .env 配置中的 APP_PASSWORD

### 机器人服务配置
```
BOT_HOST               - 监听地址（0.0.0.0 表示全网络）
BOT_PORT               - 监听端口（默认 8000）
```

## 🌐 生产部署建议

### 部署到云服务器
1. 在云服务器上安装 Python 3.8+
2. 上传项目文件
3. 安装依赖：`pip install -r requirements-bot.txt`
4. 配置 `.env.bot`（BACKEND_URL 设置为 localhost）
5. 使用 systemd 或 Docker 运行服务

### 使用 Nginx 反向代理
```nginx
location /feishu/webhook {
    proxy_pass http://localhost:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

### 配置防火墙
```bash
# 允许 8000 端口
sudo ufw allow 8000
```

## 🐛 故障排查

| 问题 | 解决方案 |
|------|---------|
| `ModuleNotFoundError: No module named 'fastapi'` | `pip install -r requirements-bot.txt` |
| `Connection refused` (后端) | 检查 BACKEND_URL、后端服务状态 |
| `Unauthorized` (飞书) | 检查 FEISHU_APP_ID, FEISHU_APP_SECRET |
| `Port 8000 already in use` | 修改 BOT_PORT 或杀死占用进程 |
| 无法收到飞书消息 | 检查事件订阅配置、请求地址、Bot Token |

## 📚 相关文件

### 主要代码
- `feishu_bot.py` - 机器人主程序（450+ 行）
  - `FeishuBotConfig` - 配置管理
  - `BackendClient` - 后端 API 客户端
  - `FeishuBotClient` - 飞书 API 客户端
  - `handle_feishu_event()` - 事件处理
  - `handle_text_command()` - 命令处理

### 后端相关
- `app/main.py` - 后端主程序
- `app/db.py` - 数据库操作
- `app/config.py` - 后端配置

## 💡 功能扩展方向

### Phase 2：增强功能
- [ ] 支持上传 Excel 文件到飞书并创建任务
- [ ] 发送卡片消息，提供任务详情预览
- [ ] 支持任务暂停、恢复、取消
- [ ] 任务完成后自动发送结果文件

### Phase 3：高级功能
- [ ] 定时任务状态推送（如长时间运行的任务）
- [ ] 任务管理后台（飞书内嵌网页）
- [ ] 批量任务导入
- [ ] 任务历史统计

### Phase 4：智能化
- [ ] AI 辅助命令理解（LLM）
- [ ] 自动任务推荐
- [ ] 错误诊断和建议

## ❓ 常见问题

**Q：机器人和后端在同一台服务器吗？**
A：不一定。机器人通过网络 API 调用后端，可以在不同服务器上。

**Q：可以有多个机器人实例吗？**
A：可以，但需要使用不同的监听端口，或使用负载均衡。

**Q：如何重启机器人？**
A：
- 本地测试：Ctrl+C 后重新运行
- Systemd：`sudo systemctl restart feishu-bot`
- Docker：`docker-compose restart bot`

**Q：日志在哪里？**
A：
- 终端输出直接显示
- 或重定向到文件：`python feishu_bot.py > bot.log 2>&1`

**Q：如何修改监听端口？**
A：编辑 `.env.bot` 中的 `BOT_PORT`

## 🚀 下一步行动

### 立即（现在）
1. ✅ 准备后端服务信息
2. ✅ 创建和编辑 `.env.bot`
3. ✅ 运行 `start_bot.sh` 或 `start_bot.bat`

### 今天内
4. ✅ 配置飞书事件订阅
5. ✅ 在飞书群中测试 `/help` 命令

### 本周内
6. ⏳ 部署到云服务器（可选）
7. ⏳ 实现上传文件功能
8. ⏳ 添加更多命令

## 📞 获取帮助

### 查看详细文档
- `FEISHU_BOT_INTEGRATION.md` - 完整集成指南

### 查看代码注释
- `feishu_bot.py` - 每个类和函数都有说明

### 官方文档
- 飞书 OpenAPI：https://open.feishu.cn/document/
- FastAPI：https://fastapi.tiangolo.com/

---

**开始吧！** 按照"快速开始"的 5 步，15 分钟内让飞书机器人运行起来！

有任何问题？检查 `FEISHU_BOT_INTEGRATION.md` 的"故障排查"章节。
