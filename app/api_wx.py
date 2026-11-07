"""
小程序专用 REST 接口。

挂载方式（在 app/main.py 里加 3 行，见 server-patch/README.md）：
    from .api_wx import router as wx_router, on_startup as wx_on_startup
    wx_on_startup()
    app.include_router(wx_router)

安全约定
--------
1. **绝不返回 task_dir**：那是服务器绝对路径，属于信息泄露。
   `_serialize_task` 负责白名单式输出。
2. 每个涉及具体任务的接口都做归属校验（`_load_owned_task`）。
3. 下载走 `Path(name).name` 归一，挡住 `../` 穿越。
4. 上传复用与原链路一致的校验规则（.xlsx、排除 `~$` 临时锁文件）。
5. **错误信息与日志脱敏**：worker 抛出的异常里常带服务器绝对路径
   （如 `/opt/harmful-sample-web/data/tasks/<id>/skills/...`），
   业务用户和审核员都不该看到。`_scrub_paths` 统一替换成占位符。

   注意：脱敏只作用于本模块（小程序面）。管理员用的网页后台走 `main.py` 的另一套
   模板渲染，仍然能看到完整日志，排查问题不受影响。
"""

from __future__ import annotations

import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from .config import settings
from .db import create_task, get_task, parse_summary, update_task
from .db_wx import (
    ALL_STATUSES,
    ensure_schema,
    query_tasks,
    set_task_owner,
    task_stats,
)
from .task_runner import cancel_task, enqueue_task
from .wechat_auth import (
    WxLoginError,
    code2session,
    issue_token,
    verify_token,
    wx_settings,
)

router = APIRouter(prefix="/api/wx", tags=["miniprogram"])

API_VERSION = "1.0.0"
LOG_TAIL_LINES = 80


def on_startup() -> None:
    """在 FastAPI startup 里调用：把小程序需要的字段补齐。"""
    ensure_schema()


# --------------------------------------------------------------------------
# 鉴权
# --------------------------------------------------------------------------


@dataclass
class Principal:
    openid: str
    source: str  # wechat | password

    @property
    def is_admin(self) -> bool:
        """密码登录视为管理员：可看全部任务、可对任意任务操作。"""
        return self.source == "password"

    @property
    def owner_scope(self) -> str | None:
        """传给 db_wx 的归属过滤。None = 不限归属。"""
        return None if self.is_admin else self.openid


def current_principal(authorization: str | None = Header(default=None)) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="缺少登录凭据")

    token = authorization.split(" ", 1)[1].strip()
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")

    return Principal(openid=str(payload.get("sub") or ""), source=str(payload.get("src") or "wechat"))


# --------------------------------------------------------------------------
# 序列化：白名单输出
# --------------------------------------------------------------------------

_TASK_PUBLIC_FIELDS = (
    "id",
    "name",
    "note",
    "status",
    "current_step",
    "created_at",
    "started_at",
    "finished_at",
    "input_filename",
    "docx_count",
    "issue_count",
    "error_message",
)

# 匹配「至少两段」的绝对路径，例如 /opt/app/data/tasks/abc/input.xlsx
# 前置的 (?<![\w:/]) 用来避开 URL 里的路径（https://x.com/a/b 不会被命中，
# 因为 /a 前面是字母 m）。
_ABS_PATH_RE = re.compile(r"(?<![\w:/])(/(?:[\w.\-]+/)+[\w.\-]*)")


def _scrub_paths(text: str | None) -> str:
    """把服务器绝对路径换成占位符，避免信息泄露。

    真实场景：worker 找不到技能脚本时会直接抛
    ``can't open file '/opt/harmful-sample-web/data/tasks/<id>/skills/...'``，
    原样透传到小程序界面上既难看，也暴露了服务器目录结构。
    """
    if not text:
        return text or ""
    return _ABS_PATH_RE.sub("<服务器路径>", text)


def _scrub_deep(value):
    """递归脱敏。

    脱敏统一放在**响应边界**做一次，而不是散落在各个字段上——
    因为路径会从多个地方漏出来：error_message、current_step、run.log 的 log_tail，
    以及 summary（它来自 worker 写的原始 summary_json，里面也嵌了命令行）。
    漏一处就等于没做。
    """
    if isinstance(value, str):
        return _scrub_paths(value)
    if isinstance(value, dict):
        return {key: _scrub_deep(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_scrub_deep(item) for item in value]
    return value


def _serialize_task(task: dict) -> dict:
    """只输出白名单字段。task_dir / summary_json 原始串都不外泄。"""
    out = {key: task.get(key) for key in _TASK_PUBLIC_FIELDS}
    out["started_at"] = out.get("started_at") or None
    out["finished_at"] = out.get("finished_at") or None
    return _scrub_deep(out)


def _list_output_files(task_dir: str) -> list[dict]:
    output_dir = Path(task_dir) / "output"
    if not output_dir.exists():
        return []
    files = []
    for path in sorted(output_dir.iterdir()):
        if path.is_file():
            files.append({"name": path.name, "size": path.stat().st_size})
    return files


def _log_tail(task_dir: str) -> str:
    log_path = Path(task_dir) / "run.log"
    if not log_path.exists():
        return ""
    text = log_path.read_text(encoding="utf-8", errors="replace")
    # 不在这里脱敏：统一由调用方的 _scrub_deep 处理，避免两处逻辑漂移
    return "\n".join(text.splitlines()[-LOG_TAIL_LINES:])


def _can_access(task: dict, principal: Principal) -> bool:
    if principal.is_admin:
        return True
    owner = str(task.get("owner_openid") or "")
    # 历史遗留任务（网页/飞书创建，无归属）对所有人可见，
    # 与 db_wx._INCLUDE_LEGACY 的策略保持一致
    if owner == "":
        return True
    return owner == principal.openid


def _load_owned_task(task_id: str, principal: Principal) -> dict:
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not _can_access(task, principal):
        # 用 404 而不是 403，避免通过状态码探测任务是否存在
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


# --------------------------------------------------------------------------
# 登录
# --------------------------------------------------------------------------


@router.post("/login")
def wx_login(payload: dict):
    """小程序静默登录：code → openid → 自定义 token。"""
    code = str((payload or {}).get("code") or "").strip()
    try:
        session = code2session(code)
    except WxLoginError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    token, ttl = issue_token(session["openid"], source="wechat")
    # 注意：session_key 不下发，需要时由服务端另行持有
    return {"token": token, "openid": session["openid"], "expires_in": ttl}


@router.post("/login/password")
def password_login(payload: dict):
    """访问密码兜底登录。用于未配置 AppID 或管理员应急入口。"""
    if not wx_settings.allow_password_login:
        raise HTTPException(status_code=403, detail="服务端已关闭密码登录")

    password = str((payload or {}).get("password") or "")
    if not password or password != settings.app_password:
        raise HTTPException(status_code=401, detail="访问密码不正确")

    token, ttl = issue_token("", source="password")
    return {"token": token, "openid": "", "expires_in": ttl, "admin": True}


# --------------------------------------------------------------------------
# 健康 / 统计
# --------------------------------------------------------------------------


@router.get("/health")
def health():
    return {
        "status": "ok",
        "server_time": datetime.now().isoformat(timespec="seconds"),
        "version": API_VERSION,
    }


@router.get("/stats")
def stats(principal: Principal = Depends(current_principal)):
    data = task_stats(principal.owner_scope)
    return data


# --------------------------------------------------------------------------
# 任务查询
# --------------------------------------------------------------------------


def _parse_statuses(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    items = [s.strip() for s in raw.split(",") if s.strip()]
    valid = [s for s in items if s in ALL_STATUSES]
    return valid or None


@router.get("/tasks")
def list_tasks(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="逗号分隔，如 queued,running"),
    keyword: str | None = Query(None, max_length=60),
    principal: Principal = Depends(current_principal),
):
    """管理视角：不限归属。非管理员调用会被拒绝。"""
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="仅管理员可查看全部任务")

    rows, total = query_tasks(
        page=page,
        size=size,
        statuses=_parse_statuses(status),
        keyword=(keyword or "").strip(),
        owner_openid=None,
    )
    return _page_payload(rows, total, page, size)


@router.get("/tasks/mine")
def list_my_tasks(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    keyword: str | None = Query(None, max_length=60),
    principal: Principal = Depends(current_principal),
):
    """业务视角：微信登录只看自己的；密码登录（管理员）看全部。"""
    rows, total = query_tasks(
        page=page,
        size=size,
        statuses=_parse_statuses(status),
        keyword=(keyword or "").strip(),
        owner_openid=principal.owner_scope,
    )
    return _page_payload(rows, total, page, size)


def _page_payload(rows: list[dict], total: int, page: int, size: int) -> dict:
    return {
        "items": [_serialize_task(row) for row in rows],
        "total": total,
        "page": page,
        "size": size,
        "has_more": page * size < total,
    }


@router.get("/tasks/{task_id}")
def task_detail(task_id: str, principal: Principal = Depends(current_principal)):
    task = _load_owned_task(task_id, principal)
    # summary 与 log_tail 里都可能嵌服务器绝对路径，整包过一遍脱敏
    return _scrub_deep(
        {
            **_serialize_task(task),
            "summary": parse_summary(task),
            "files": _list_output_files(task["task_dir"]),
            "log_tail": _log_tail(task["task_dir"]),
        }
    )


@router.get("/tasks/{task_id}/files")
def task_files(task_id: str, principal: Principal = Depends(current_principal)):
    task = _load_owned_task(task_id, principal)
    return _list_output_files(task["task_dir"])


# --------------------------------------------------------------------------
# 任务创建 / 操作
# --------------------------------------------------------------------------


@router.post("/tasks")
async def create_task_api(
    name: str = Form(""),
    note: str = Form(""),
    file: UploadFile = File(...),
    principal: Principal = Depends(current_principal),
):
    """上传 .xlsx 建任务。校验规则与原网页入口完全一致。"""
    filename = Path(file.filename or "").name
    if not filename.lower().endswith(".xlsx") or filename.startswith("~$"):
        raise HTTPException(status_code=400, detail="请上传正常的 .xlsx 文件")

    task_id = uuid.uuid4().hex[:12]
    task_dir = settings.task_root / task_id
    (task_dir / "output").mkdir(parents=True, exist_ok=True)
    with (task_dir / "input.xlsx").open("wb") as dst:
        shutil.copyfileobj(file.file, dst)

    task_name = name.strip() or filename
    create_task(task_id, task_name, note.strip(), task_dir, filename)
    set_task_owner(task_id, principal.openid, principal.source)
    enqueue_task(task_id)

    return {"task_id": task_id, "name": task_name}


_RESUMABLE = {"paused_captcha", "partial_completed", "failed"}


@router.post("/tasks/{task_id}/resume")
def resume_task(task_id: str, principal: Principal = Depends(current_principal)):
    task = _load_owned_task(task_id, principal)
    if task["status"] not in _RESUMABLE:
        return {"ok": True, "resumed": False, "status": task["status"]}

    update_task(task_id, status="queued", current_step="小程序端重新排队", error_message="")
    enqueue_task(task_id)
    return {"ok": True, "resumed": True}


@router.post("/tasks/{task_id}/cancel")
def cancel_task_api(task_id: str, principal: Principal = Depends(current_principal)):
    _load_owned_task(task_id, principal)
    ok = cancel_task(task_id)
    current = get_task(task_id)
    return {"ok": ok, "status": current["status"] if current else ""}


# --------------------------------------------------------------------------
# 文件下载
# --------------------------------------------------------------------------


@router.get("/tasks/{task_id}/download/{filename}")
def download(task_id: str, filename: str, principal: Principal = Depends(current_principal)):
    task = _load_owned_task(task_id, principal)

    # Path(...).name 归一，`../../etc/passwd` 会被压成 `passwd`
    safe_name = Path(filename).name
    path = Path(task["task_dir"]) / "output" / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(path, filename=safe_name)
