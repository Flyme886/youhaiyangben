from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .config import BASE_DIR, settings
from .db import create_task, get_task, init_db, list_recoverable_tasks, list_tasks, parse_summary, update_task
from .task_runner import cancel_task, enqueue_task


app = FastAPI(title="有害样本任务台")
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


@app.on_event("startup")
def startup() -> None:
    settings.task_root.mkdir(parents=True, exist_ok=True)
    init_db()
    for task in list_recoverable_tasks():
        update_task(task["id"], status="queued", current_step="服务重启后重新排队")
        enqueue_task(task["id"])


def require_login(request: Request) -> None:
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


@app.get("/")
def home(request: Request):
    if not request.session.get("authenticated"):
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/tasks", status_code=303)


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})


@app.post("/login")
def login(request: Request, password: str = Form(...)):
    if password != settings.app_password:
        return templates.TemplateResponse("login.html", {"request": request, "error": "密码不正确"}, status_code=401)
    request.session["authenticated"] = True
    return RedirectResponse("/tasks", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/tasks")
def tasks_page(request: Request):
    require_login(request)
    tasks = list_tasks()
    return templates.TemplateResponse("tasks.html", {"request": request, "tasks": tasks})


@app.get("/tasks/new")
def new_task_page(request: Request):
    require_login(request)
    return templates.TemplateResponse("new_task.html", {"request": request, "error": ""})


@app.post("/tasks")
async def create_task_page(
    request: Request,
    name: str = Form(...),
    note: str = Form(""),
    file: UploadFile = File(...),
):
    require_login(request)
    filename = Path(file.filename or "").name
    if not filename.lower().endswith(".xlsx") or filename.startswith("~$"):
        return templates.TemplateResponse(
            "new_task.html",
            {"request": request, "error": "请上传正常的 .xlsx 文件"},
            status_code=400,
        )

    task_id = uuid.uuid4().hex[:12]
    task_dir = settings.task_root / task_id
    output_dir = task_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    input_path = task_dir / "input.xlsx"
    with input_path.open("wb") as dst:
        shutil.copyfileobj(file.file, dst)
    create_task(task_id, name.strip() or filename, note.strip(), task_dir, filename)
    enqueue_task(task_id)
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


@app.get("/tasks/{task_id}")
def task_detail(request: Request, task_id: str):
    require_login(request)
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404)
    task_dir = Path(task["task_dir"])
    output_dir = task_dir / "output"
    files = []
    if output_dir.exists():
        for path in sorted(output_dir.iterdir()):
            if path.is_file():
                files.append({"name": path.name, "size": path.stat().st_size})
    log_path = task_dir / "run.log"
    log_tail = ""
    if log_path.exists():
        text = log_path.read_text(encoding="utf-8", errors="replace")
        log_tail = "\n".join(text.splitlines()[-80:])
    return templates.TemplateResponse(
        "detail.html",
        {
            "request": request,
            "task": task,
            "summary": parse_summary(task),
            "summary_pretty": json.dumps(parse_summary(task), ensure_ascii=False, indent=2),
            "files": files,
            "log_tail": log_tail,
            "storage_state": storage_state_info(),
        },
    )


@app.post("/tasks/{task_id}/resume")
def resume_task(request: Request, task_id: str):
    require_login(request)
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404)
    if task["status"] in {"paused_captcha", "partial_completed", "failed"}:
        update_task(task_id, status="queued", current_step="重新排队", error_message="")
        enqueue_task(task_id)
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


@app.get("/admin")
def admin_page(request: Request):
    require_login(request)
    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "storage_state": storage_state_info(), "settings": settings, "message": "", "error": ""},
    )


@app.post("/admin/storage-state")
async def upload_storage_state(request: Request, file: UploadFile = File(...)):
    require_login(request)
    filename = Path(file.filename or "").name
    if not filename.endswith(".json"):
        return templates.TemplateResponse(
            "admin.html",
            {"request": request, "storage_state": storage_state_info(), "settings": settings, "message": "", "error": "请上传 storage_state.json"},
            status_code=400,
        )
    settings.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
    with settings.storage_state_path.open("wb") as dst:
        shutil.copyfileobj(file.file, dst)
    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "storage_state": storage_state_info(), "settings": settings, "message": "登录态已更新", "error": ""},
    )


@app.get("/tasks/{task_id}/download/{filename}")
def download_file(request: Request, task_id: str, filename: str):
    require_login(request)
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404)
    safe_name = Path(filename).name
    path = Path(task["task_dir"]) / "output" / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, filename=safe_name)


# ============ 飞书机器人内部 API（API Key 鉴权）============


def _require_bot_key(x_bot_key: str = Header(default=None)) -> None:
    if not settings.bot_api_key or x_bot_key != settings.bot_api_key:
        raise HTTPException(status_code=401, detail="Invalid bot API key")


@app.post("/internal/tasks/upload")
async def internal_upload_task(
    name: str = Form(""),
    file: UploadFile = File(...),
    _: None = Depends(_require_bot_key),
):
    filename = Path(file.filename or "").name
    if not filename.lower().endswith(".xlsx") or filename.startswith("~$"):
        raise HTTPException(status_code=400, detail="请上传 .xlsx 文件")
    task_id = uuid.uuid4().hex[:12]
    task_dir = settings.task_root / task_id
    (task_dir / "output").mkdir(parents=True, exist_ok=True)
    with (task_dir / "input.xlsx").open("wb") as dst:
        shutil.copyfileobj(file.file, dst)
    task_name = name.strip() or filename
    create_task(task_id, task_name, "", task_dir, filename)
    enqueue_task(task_id)
    return JSONResponse({"task_id": task_id, "name": task_name})


@app.get("/internal/tasks")
def internal_list_tasks(_: None = Depends(_require_bot_key)):
    return JSONResponse(list_tasks())


@app.get("/internal/tasks/{task_id}")
def internal_get_task(task_id: str, _: None = Depends(_require_bot_key)):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404)
    return JSONResponse(task)


@app.post("/internal/tasks/{task_id}/cancel")
def internal_cancel_task(task_id: str, _: None = Depends(_require_bot_key)):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404)
    ok = cancel_task(task_id)
    return JSONResponse({"ok": ok, "status": get_task(task_id)["status"]})


@app.post("/internal/tasks/{task_id}/resume")
def internal_resume_task(task_id: str, _: None = Depends(_require_bot_key)):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404)
    if task["status"] in {"paused_captcha", "partial_completed", "failed"}:
        update_task(task_id, status="queued", current_step="Bot 触发重新排队", error_message="")
        enqueue_task(task_id)
        return JSONResponse({"ok": True, "resumed": True})
    return JSONResponse({"ok": True, "resumed": False, "status": task["status"]})


@app.get("/internal/tasks/{task_id}/files")
def internal_list_files(task_id: str, _: None = Depends(_require_bot_key)):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404)
    output_dir = Path(task["task_dir"]) / "output"
    files = []
    if output_dir.exists():
        for p in sorted(output_dir.iterdir()):
            if p.is_file():
                files.append({"name": p.name, "size": p.stat().st_size})
    return JSONResponse(files)


@app.get("/internal/tasks/{task_id}/download/{filename}")
def internal_download_file(task_id: str, filename: str, _: None = Depends(_require_bot_key)):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404)
    safe_name = Path(filename).name
    path = Path(task["task_dir"]) / "output" / safe_name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, filename=safe_name)


def storage_state_info() -> dict[str, str | bool]:
    path = settings.storage_state_path
    exists = path.exists()
    updated_at = ""
    if exists:
        updated_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    return {"exists": exists, "path": str(path), "updated_at": updated_at}
