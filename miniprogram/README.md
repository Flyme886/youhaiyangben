# 舆见小工具 · 微信小程序端（样本任务台）

把「有害样本任务台」搬到微信里的业务端。业务用户不用开电脑、不用进飞书，在手机上就能：

1. 选一个 Excel 交上去；
2. 看任务跑到哪一步；
3. 跑完直接下载 Word（每条样本 = 编号标题 / 原始链接 / 页面截图）。

管理员功能（登录态维护、批量重跑、日志排查）仍然留在网页后台，不在小程序里。

## 技术栈

微信原生小程序，零构建、无 npm 依赖，克隆下来用微信开发者工具直接导入即可。

- 基础库：`3.5.5`（`project.config.json` 中 `libVersion`）
- AppID：`wx163f957c85ceff4b`（换成自己的需要在开发者工具里改，或改 `project.config.json`）
- 代码体积：约 100KB（含图标），远低于主包 2MB 限制，暂未做分包

## 目录结构

```text
miniprogram/
├── app.js / app.json / app.wxss     启动、路由与全局样式
├── config/index.js                 环境判定与后端地址（唯一需要改的地方）
├── pages/
│   ├── login/                      登录（静默登录失败时兜底）
│   ├── tasks/                      任务列表（tabBar 首页）
│   ├── task-new/                   新建任务（选 Excel + 上传进度）
│   ├── task-detail/                任务详情（阶段进度、文件下载、重跑）
│   └── mine/                       我的（登录态、服务连通性）
├── components/
│   ├── status-tag/                 状态标签（按后端 status 映射文案与颜色）
│   └── empty-state/                空状态（纯 CSS 插图，无二进制资源）
├── services/task.js                业务接口封装
├── utils/
│   ├── request.js                  请求封装：token 注入 / 401 重入保护 / 上传进度与取消 / 下载
│   ├── auth.js                     wx.login → 后端 code2session
│   ├── status.js                   后端状态 → 展示文案（唯一口径）
│   └── util.js                     时间、文件大小等格式化
└── assets/icons/                   tabBar 图标（暖纸 + 墨绿）
```

## 导入与运行

1. 微信开发者工具 → 导入项目 → 选择本目录。
2. AppID 填自己的（或沿用 `wx163f957c85ceff4b`，需要有该小程序的管理权限）。
3. 改 `config/index.js`：
   - 本地联调：`PROFILES.dev.baseUrl` 默认 `http://127.0.0.1:8787`，并在「详情 → 本地设置」勾选
     **不校验合法域名、web-view、TLS 版本以及 HTTPS 证书**。
   - 线上：`PROFILES.prod.baseUrl` 改成自己的 https 域名，且在微信后台「开发管理 → 开发设置 →
     服务器域名」把同一个域名同时加进 **request / uploadFile / downloadFile** 三类白名单。
     三类缺一类，上传或下载整条挂掉。
4. 真机预览如果后端地址不可达，用 `FORCE_BASE_URL` 临时指向内网穿透域名；验证完清空。

环境判定是自动的（`develop` → dev，`trial` / `release` → prod），不用手动切；
`config/index.js` 的 `validate()` 会在启动时自检（生产非 https、结尾带斜杠、占位域名都会直接拦下并提示）。

> 域名尚未备案时，体验版需要在微信里点右上角「···」→ 打开调试，否则请求会被拦成
> `url not in domain list`。这是微信的白名单限制，不是代码问题。

## 后端依赖（重要）

小程序所有请求都走 `config/index.js` 里的 `apiPrefix = '/api/wx'`，共 12 个接口，覆盖：

| 能力 | 方法 | 路径 |
| --- | --- | --- |
| 静默登录 | POST | `/api/wx/login` |
| 任务列表 / 我的任务 | GET | `/api/wx/tasks`、`/api/wx/tasks/mine` |
| 任务详情 | GET | `/api/wx/tasks/{id}` |
| 上传 Excel 建任务 | POST | `/api/wx/tasks`（multipart） |
| 重跑 / 补齐图片 | POST | `/api/wx/tasks/{id}/resume` |
| 取消 | POST | `/api/wx/tasks/{id}/cancel` |
| 产物列表 / 下载 | GET | `/api/wx/tasks/{id}/files`、`/download/{filename}` |
| 概览统计 / 健康检查 | GET | `/api/wx/stats`、`/api/wx/health` |

**以上接口层已内置在本仓库**（2026-11-03 从 `server-patch/` 合入，不需要再打补丁）：

| 文件 | 作用 |
| --- | --- |
| `app/api_wx.py` | 12 个 `/api/wx/*` 路由（登录、任务 CRUD、上传、下载、统计） |
| `app/wechat_auth.py` | `code2session` 换 openid + HMAC-SHA256 自签 token |
| `app/db_wx.py` | `tasks` 表补 `owner_openid` / `owner_source`，实现用户归属隔离 |
| `app/main.py` | 第 15-16 行导入、第 23 行 `include_router`、第 33 行 `wx_on_startup()` |
| `deploy/nginx-harmful-sample.conf` | `/api/wx/` 最长前缀转发（压过既有 `/api/`） |
| `tests/test_api_wx.py` | 82 项集成断言（鉴权、归属隔离、分页、路径穿越） |

完整部署步骤、环境变量清单、逐条验证命令见 **`docs/小程序后端接口-server-patch.md`**。

⚠️ **只跑小程序端不够。** 后端 `.env` 必须配好 `WX_APP_ID`、`WX_APP_SECRET`、
`WX_TOKEN_SECRET` 三项（见根目录 `.env.example` 的「微信小程序」段）。
缺任一项，登录接口会失败，小程序上的表现是「一直转圈 / 登录失败」；
如果 `app/main.py` 的 router 没挂上，则是所有请求 404。两者排查方向不同。

## 状态口径

展示文案只在 `utils/status.js` 里定义，和后端 `app/db.py` 的 `status` 字段一一对应：

| status | 展示 | 含义 |
| --- | --- | --- |
| `queued` | 排队中 | 已入队，等 worker 取 |
| `running` | 处理中 | worker 正在执行 |
| `completed` | 已完成 | 全部产物已生成 |
| `partial_completed` | 已完成 | 文档已交付，部分条目没取到截图，图片位留空 |
| `failed` | 失败 | 一个 Word 都没产出，需要排查 |
| `cancelled` | 已取消 | 手动取消 |
| `paused_captcha` | 待重跑 | 兜底态：命中验证码且无任何产物 |

判断原则是「有没有产出 Word」：只要有文档就按已完成交付，缺图只留一句说明，
不把正常的文字版交付判成失败。

## 不入库的文件

`project.private.config.json`（开发者工具的本地私有配置）已在 `.gitignore` 中排除；
`node_modules/`、`miniprogram_npm/`、`*.log` 同样不入库。
