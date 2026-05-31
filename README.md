# 有害样本任务台

面向内容审核场景的自动化任务系统。业务用户只在飞书里操作：把 Excel 发给飞书机器人，机器人确认收件；云服务器在后台自动完成截图、质检、结构化判定、飞书文档生成和 Word 导出；完成后机器人把飞书文档链接和 Word 下载链接回给用户。

这个仓库当前已经具备可运行的任务队列、截图、质检、Word 生成和下载链路；下一阶段会在此基础上接入飞书机器人、飞书文档发布和极简管理员后台。

## 产品定位

- 业务入口：飞书机器人
- 主交付物：飞书文档
- 备份交付物：Word `.docx`
- 运行环境：Linux 云服务器
- 管理入口：极简后台，仅用于任务查看、失败重试、登录态和飞书凭证维护

## 目标流程

```text
用户在飞书中发送 Excel
-> 机器人回复“已收到”
-> 后端创建任务
-> Excel 预检和去重
-> Playwright 截图
-> 截图质量检查
-> 结构化样本生成
-> 每 50 条生成 1 份飞书文档
-> 同步生成 Word 备份
-> 机器人回飞书文档链接 + Word 下载链接
```

## 角色分工

### 业务用户

- 只在飞书里发任务、收结果
- 不登录服务器
- 不进入后台

### 云服务器

- 接收飞书机器人递送的任务
- 跑固定 worker，不依赖 Claude Code 参与生产任务
- 执行截图、质检、样本整理、飞书文档编排、Word 导出、文件打包

### 极简后台

- 查看任务列表
- 查看任务详情和运行日志
- 失败任务重试
- 上传或替换 `storage_state.json`
- 查看飞书配置状态

## 任务输入与输出

### 标准输入

- 一个 `.xlsx` 文件
- 用户通过飞书机器人会话发送给机器人
- 如果用户同时发送文本，文本可作为任务名；否则使用文件名

### 标准中间层

后端需要维护两层结构化数据：

1. 任务级 JSON  
   记录任务 ID、文件来源、状态、产物链接、失败摘要、发布时间等。

2. 样本级 JSON  
   每条记录至少包含：
   - 原序号
   - 来源平台
   - 作者
   - 原文
   - 链接
   - 页面文本
   - 截图路径
   - 截图状态
   - 机器初判字段
   - 人工修订字段
   - 发布版本字段

### 最终输出

- 每 50 条样本生成 1 份飞书文档
- 每 50 条样本生成 1 个 Word 文件
- 一个任务会返回多个飞书文档链接
- 同时返回 Word 下载链接
- 若部分截图失败，结果仍返回，但需要明确提示“部分条目截图待补”

## 系统模块

### 1. 飞书机器人入口

职责：

- 接收用户发送的 Excel 文件
- 立即回复“已收到”
- 把文件和基础元数据转发给后端
- 在任务完成后回结果
- 在任务失败时回失败通知

第一版机器人只需要支持 4 类动作：

- 新建任务
- 收到任务确认
- 完成通知
- 失败通知

第一版推荐使用私聊机器人 + 简洁文本消息，不先做复杂卡片交互。

### 2. 后端任务服务

当前仓库中的 FastAPI 服务已经提供了任务列表、任务详情、文件下载和单 worker 执行框架。后续需要把“网页上传任务”扩展为“飞书递送任务”。

生产任务执行原则：

- 固定 worker 执行确定性命令链
- 不在生产链路中启动 Claude Code
- 不依赖人工实时干预
- 单条失败不阻塞整体产出

当前固定命令链：

```text
excel_to_word.py --dry-run
capture_links.py
quality_check_screenshots.py
excel_to_word.py --screenshots-manifest
zip output files
```

### 3. 截图与质检

当前使用项目内置的 `skills/harmful_sample` 脚本链：

- `scripts/excel_to_word.py`
- `scripts/capture_links.py`
- `scripts/quality_check_screenshots.py`

现有规则：

- 截图使用 Playwright
- 识别 `login_required`、`blocked_or_blank`、`timeout`、`navigation_error`
- 截图不可用时，不阻塞 Word 生成，而是在文档中写入 `截图待补：原因`

### 4. 飞书文档生成层

这是下一阶段重点。

目标行为：

- 后端根据样本级 JSON 生成飞书文档内容
- 每 50 条样本对应 1 份飞书文档
- 飞书文档条目顺序沿用现有 Word 排版顺序

每条内容建议保持以下结构：

1. 编号 / 原序号 / 来源 / `@作者`
2. 原文
3. 链接
4. 截图
5. 违规要素与理由

飞书文档是主协作稿；Word 是备份和归档产物。

### 5. 极简后台

后台不是业务入口，只服务管理员。

最小功能范围：

- 任务列表
- 任务详情
- 运行日志
- 失败重试
- 上传 `storage_state.json`
- 查看飞书凭证配置是否有效

不做：

- 业务同事逐条审核入口
- 富文本在线排版器
- 完整权限系统
- 多角色工作台

## 当前实现状态

当前已具备：

- FastAPI 服务
- SQLite 任务表
- 单 worker 队列
- Excel 上传创建任务
- 截图、质检、Word 生成、压缩包导出
- 服务重启后恢复队列

当前尚未完成：

- 飞书机器人接收 Excel
- 飞书回调 / 文件下载
- 飞书文档创建与内容写入
- Word 与飞书文档的双发布
- 极简后台中的登录态上传和失败重试入口

## 目录结构

```text
app/
  main.py                 FastAPI 入口
  db.py                   SQLite 任务存储
  task_runner.py          固定 worker 执行链
  templates/              管理后台模板
  static/                 后台样式

skills/harmful_sample/
  SKILL.md
  scripts/
    excel_to_word.py
    capture_links.py
    quality_check_screenshots.py
  assets/template.docx

data/
  tasks/                  任务运行目录
  app.db                  SQLite 数据库
  storage_state.json      平台登录态

deploy_ubuntu.sh          Ubuntu 部署脚本
harmful-sample-web.service
run_server.sh
```

## 部署

推荐部署路径：

```bash
/opt/harmful-sample-web
```

部署步骤：

```bash
sudo mkdir -p /opt/harmful-sample-web
sudo cp -r . /opt/harmful-sample-web/
cd /opt/harmful-sample-web
sudo bash deploy_ubuntu.sh
sudo nano .env
sudo systemctl restart harmful-sample-web
```

内网访问：

```text
http://服务器内网IP:8787
```

公网访问前建议放到 Nginx/HTTPS/VPN 后面。

## 配置

当前 `.env` 关键字段：

```env
APP_PASSWORD=change-this-password
TASK_ROOT=/opt/harmful-sample-web/data/tasks
DATABASE_PATH=/opt/harmful-sample-web/data/app.db
SKILL_DIR=/opt/harmful-sample-web/skills/harmful_sample
PYTHON_BIN=/opt/harmful-sample-web/.venv/bin/python
NODE_BIN=/usr/bin/node
STORAGE_STATE_PATH=/opt/harmful-sample-web/data/storage_state.json
TASK_TIMEOUT_SECONDS=7200
```

后续接飞书时，建议新增：

```env
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_BOT_TOKEN=
FEISHU_VERIFICATION_TOKEN=
FEISHU_ENCRYPT_KEY=
FEISHU_PARENT_FOLDER_TOKEN=
```

## 异常处理策略

### 飞书机器人侧

- 收到文件后立即回“已收到”
- 后端完全失败时回失败通知
- 后端部分成功时回结果，并注明“部分条目截图待补”

### 后端侧

- 单条链接失败不阻塞整个任务
- 只要产出了文档，就标记为 `partial_completed` 或 `completed`
- 完全没有产出文档才标记为 `failed`
- `run.log` 保留完整命令链日志
- `完整产物包.zip` 用于排障和复核

### 管理员侧

- 登录态失效时更新 `storage_state.json`
- 飞书凭证异常时在极简后台查看配置状态
- 对失败任务执行重试

## 下一阶段开发顺序

建议按这个顺序推进：

1. 飞书机器人收文件并把 Excel 递送到后端
2. 后端接入飞书任务创建接口
3. 任务完成后回文本结果消息
4. 飞书文档生成与发布
5. Word 下载链接回传
6. 极简后台补上失败重试和登录态维护

## 设计边界

这套系统当前明确不做：

- 多业务入口并存
- 复杂飞书卡片工作台
- 全量在线富文本编辑器
- 自动验证码处理
- 多 worker 并发调度
- 视觉大模型看图判定

第一版目标很明确：**让业务用户只在飞书里发 Excel，然后稳定收到飞书文档和 Word 结果。**
