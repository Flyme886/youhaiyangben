# 小程序接口层：/api/wx/*

这套补丁**不改动原有任何业务逻辑**，只做三件事：

1. 新增一层小程序专用 REST 接口（`app/api_wx.py`）
2. 新增微信登录换 openid 的逻辑（`app/wechat_auth.py`）
3. 给 `tasks` 表补 `owner_openid` 字段，让业务用户在列表里只看到自己的任务（`app/db_wx.py`）

原有的网页后台、飞书机器人、`/internal/*` 接口全部照常工作。

> **本仓库已内置**（2026-11-03 从 `server-patch/` 合入，不再需要 `cp` 文件）：
> `app/api_wx.py`、`app/db_wx.py`、`app/wechat_auth.py`、`tests/test_api_wx.py`、
> `deploy/nginx-harmful-sample.conf`，并在 `app/main.py` 里挂好了 router。
> 下面第一节保留原始放置说明，供在其他服务器上复现；本仓库只需跳到第三节配环境变量。

## 一、文件放置（其他服务器复现时看，本仓库可跳过）

在项目根目录（也就是 `app/`、`skills/` 的同级目录）执行：

```bash
cd /opt/harmful-sample-web

# 1. 三个模块必须在 app/ 包内
#    本仓库已包含，无需复制；从零部署时按下面对应关系放好
app/api_wx.py           # 小程序 REST 接口
app/db_wx.py            # 归属字段迁移 + 分页查询 + 统计
app/wechat_auth.py      # code2session + 自签 token

# 2. Nginx 站点配置
sudo cp deploy/nginx-harmful-sample.conf /etc/nginx/conf.d/harmful-sample.conf
```

> `app/api_wx.py` 里用的是相对导入（`from .db import ...`），
> 所以必须放在 `app/` 包**内部**，放到别处会 `ImportError`。

---

## 二、`app/main.py` 的改动（本仓库已应用）

需要在三处加 4 行（本仓库 `app/main.py` 第 15-23、33 行已加好）：

- 导入：`from .api_wx import on_startup as wx_on_startup` / `from .api_wx import router as wx_router`
- 挂载：`app = FastAPI(...)` 之后 `app.include_router(wx_router)`
- 启动：`init_db()` 之后 `wx_on_startup()`

在其他机器上复现时可手动改，**别用 pip 装最新 fastapi**（见文末版本警告）。

<details>
<summary>展开看具体位置</summary>

在文件顶部导入区加：


```python
from .api_wx import on_startup as wx_on_startup
from .api_wx import router as wx_router
```

在已有的 `@app.on_event("startup")` 函数里，把 `init_db()` 之后补一行：

```python
@app.on_event("startup")
def startup() -> None:
    settings.task_root.mkdir(parents=True, exist_ok=True)
    init_db()
    wx_on_startup()                      # ← 新增：补 owner_openid 字段与索引（幂等）
    for task in list_recoverable_tasks():
        update_task(task["id"], status="queued", current_step="服务重启后重新排队")
        enqueue_task(task["id"])
```

在 `app = FastAPI(...)` 创建之后、第一个路由之前加一行：

```python
app.include_router(wx_router)            # ← 新增：挂载 /api/wx/*
```

改完 `main.py` 的 diff 大致是这样：

```diff
+from .api_wx import on_startup as wx_on_startup
+from .api_wx import router as wx_router

 app = FastAPI(title="有害样本任务台")
+app.include_router(wx_router)

 @app.on_event("startup")
 def startup() -> None:
     settings.task_root.mkdir(parents=True, exist_ok=True)
     init_db()
+    wx_on_startup()
```

</details>

> ⚠️ **依赖必须按 `requirements.txt` 钉住版本装。**
> 实测过：新版 starlette 删除了旧版 `TemplateResponse(name, context)` 签名，
> 而原项目 `main.py` 用的正是旧签名 → 网页后台 `/login` 直接 500
> （`TypeError: unhashable type: 'dict'`）。
> 用 `pip install fastapi` 装最新版就会踩到。请用 `pip install -r requirements.txt`。

---

## 三、新增环境变量

追加到 `/opt/harmful-sample-web/.env`：

```env
# ---------- 微信小程序 ----------
WX_APP_ID=wxXXXXXXXXXXXXXXXX
WX_APP_SECRET=在微信公众平台「开发管理-开发设置」里获取
# 独立于 SESSION_SECRET 的 token 签名密钥，建议 32 位以上随机串
WX_TOKEN_SECRET=请替换成一串随机值
# token 有效期（秒），默认 7 天
WX_TOKEN_TTL=604800
# 是否保留「访问密码」兜底登录。迁移期建议 true，稳定后改 false
WX_ALLOW_PASSWORD_LOGIN=true
# 是否把历史任务（网页/飞书创建、无归属）也展示给业务用户
WX_INCLUDE_LEGACY_TASKS=true
```

生成随机密钥：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

> ⚠️ `WX_APP_SECRET` 是服务端凭据，**只能存在于服务器 `.env`，绝不能写进小程序代码**。
> 小程序里任何位置出现 AppSecret 都视为严重安全事故。

> ⚠️ `TASK_ROOT` 与 `SKILL_DIR` **必须写绝对路径**（如 `/opt/harmful-sample-web/...`）。
> 实测过：worker 的**工作目录是 `task_dir`**，写成相对路径会被解析到
> `data/tasks/<task_id>/` 下面，任务一提交就报
> `can't open file '.../data/tasks/<id>/skills/harmful_sample/scripts/excel_to_word.py'`。

改完重启：

```bash
sudo systemctl restart harmful-sample-web
sudo systemctl reload nginx
```

---

## 四、验证

### 1. 健康检查（不需要登录）

```bash
curl -s https://your-domain.example.com/api/wx/health
# {"status":"ok","server_time":"2026-09-29T12:30:00","version":"1.0.0"}
```

### 2. 鉴权拦截是否生效

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://your-domain.example.com/api/wx/tasks/mine
# 期望 401
```

### 3. 密码登录 + 查统计

```bash
TOKEN=$(curl -s -X POST https://your-domain.example.com/api/wx/login/password \
  -H 'content-type: application/json' \
  -d '{"password":"你的APP_PASSWORD"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

curl -s https://your-domain.example.com/api/wx/stats -H "Authorization: Bearer $TOKEN"
```

### 4. 数据库字段是否补齐

```bash
sqlite3 /opt/harmful-sample-web/data/app.db "PRAGMA table_info(tasks);" | grep owner
# 应看到 owner_openid 与 owner_source
```

### 5. 路径穿越是否被挡住

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://your-domain.example.com/api/wx/tasks/xxx/download/..%2F..%2F.env" \
  -H "Authorization: Bearer $TOKEN"
# 期望 404
```

### 6. 一键跑完整集成自检（推荐）

仓库里带了一份接口集成测试，**82 项断言**，覆盖鉴权、归属隔离、
信息泄露、分页统计、重试取消、路径穿越：

```bash
cd /opt/harmful-sample-web
pip install -r requirements.txt httpx            # 已装可跳过
python tests/test_api_wx.py
```

预期输出结尾：

```
结果：82 通过 / 0 失败
```

它用**独立的临时数据库**（`tempfile.mkdtemp`），不会碰 `data/app.db`，
可以随时在生产机上跑，作为发版前的回归门禁。

只打桩两处：`enqueue_task`（不启动真实 worker）和 `code2session`（不真调微信），
其余全部走真实实现——包括鉴权、归属过滤、路径归一和文件流。

---

## 五、接口清单

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/wx/login` | `{code}` → `{token, openid, expires_in}` |
| POST | `/api/wx/login/password` | `{password}` → `{token, admin}` |
| GET | `/api/wx/health` | 无需鉴权 |
| GET | `/api/wx/stats` | 按状态统计 |
| GET | `/api/wx/tasks` | 全部任务（仅管理员） |
| GET | `/api/wx/tasks/mine` | 我的任务 |
| GET | `/api/wx/tasks/{id}` | 详情（含 summary / files / log_tail） |
| POST | `/api/wx/tasks` | multipart 上传 `.xlsx` |
| POST | `/api/wx/tasks/{id}/resume` | 继续任务 |
| POST | `/api/wx/tasks/{id}/cancel` | 取消任务 |
| GET | `/api/wx/tasks/{id}/files` | 产物列表 |
| GET | `/api/wx/tasks/{id}/download/{filename}` | 下载产物 |

查询参数：`page`、`size`（≤100）、`status`（逗号分隔多状态）、`keyword`（任务名/ID 模糊）。

---

## 六、权限模型

| 登录方式 | `source` | 可见范围 | 能否重试/取消 |
|---|---|---|---|
| 微信静默登录 | `wechat` | 仅自己的任务 | 仅自己的 |
| 访问密码 | `password` | 全部任务 | 全部任务 |

- 历史任务（`owner_openid` 为空）默认对所有人可见，由 `WX_INCLUDE_LEGACY_TASKS` 控制。
  迁移完成后建议设为 `false`。
- 无权访问的任务统一返回 **404** 而不是 403，避免通过状态码枚举出任务 ID。

---

## 七、上线前建议补的两件事

1. **接口限流**：`/api/wx/login` 与 `/api/wx/tasks`（上传）最需要限制。
   最省事的做法是在 Nginx 层加 `limit_req`：

   ```nginx
   limit_req_zone $binary_remote_addr zone=wxapi:10m rate=20r/m;

   location /api/wx/login {
       limit_req zone=wxapi burst=5 nodelay;
       proxy_pass http://harmful_sample_app;
   }
   ```

2. **登录态过期告警**：`storage_state.json` 超过 `STORAGE_STATE_MAX_AGE_DAYS` 天
   截图成功率会明显下降。可以在 worker 里加一条订阅消息推送给管理员，
   让「任务大面积 截图待补」提前被发现。
