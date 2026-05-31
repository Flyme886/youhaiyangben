"""
有害样本 - 飞书机器人（第一版）

流程：
  1. 用户发 Excel 文件给机器人
  2. 机器人立即回"已收到"
  3. 后端异步跑截图 → 质检 → Word 生成
  4. 完成后机器人把 Word 文件发回原会话
  5. 失败时发失败通知

环境变量（.env）：
  FEISHU_APP_ID / FEISHU_APP_SECRET
  BACKEND_URL     默认 http://localhost:8787
  BOT_API_KEY     与后端约定的内部鉴权 Key
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateFileRequest,
    CreateFileRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    P2ImMessageReceiveV1,
)
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("feishu_bot")

APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8787")
BOT_API_KEY = os.environ.get("BOT_API_KEY", "")
FEISHU_API = "https://open.feishu.cn/open-apis"

# 记录"哪个任务对应哪个会话"，重启后持久化
STATE_FILE = Path(__file__).parent / "bot_state.json"
POLL_INTERVAL = 30  # 秒


# ─── 状态持久化（task_id -> {chat_id, filename}）────────────────────────────

_state_lock = threading.Lock()


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def state_add(task_id: str, chat_id: str, filename: str) -> None:
    with _state_lock:
        s = _load_state()
        s[task_id] = {"chat_id": chat_id, "filename": filename, "notified": False}
        _save_state(s)


def state_mark_notified(task_id: str) -> None:
    with _state_lock:
        s = _load_state()
        if task_id in s:
            s[task_id]["notified"] = True
            _save_state(s)


def state_pending() -> dict:
    with _state_lock:
        s = _load_state()
        return {tid: info for tid, info in s.items() if not info.get("notified")}


# ─── 飞书 API 工具 ───────────────────────────────────────────────────────────

_token_cache: dict = {"token": "", "expire": 0}
_token_lock = threading.Lock()


def _tenant_token() -> str:
    with _token_lock:
        if time.time() < _token_cache["expire"] - 60:
            return _token_cache["token"]
        resp = requests.post(
            f"{FEISHU_API}/auth/v3/tenant_access_token/internal",
            json={"app_id": APP_ID, "app_secret": APP_SECRET},
            timeout=10,
        )
        data = resp.json()
        _token_cache["token"] = data["tenant_access_token"]
        _token_cache["expire"] = time.time() + data.get("expire", 7200)
        return _token_cache["token"]


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_tenant_token()}"}


def download_feishu_file(message_id: str, file_key: str) -> Optional[bytes]:
    """从飞书消息下载文件内容"""
    try:
        resp = requests.get(
            f"{FEISHU_API}/im/v1/messages/{message_id}/resources/{file_key}",
            headers=_auth_headers(),
            params={"type": "file"},
            timeout=60,
        )
        if resp.status_code == 200:
            return resp.content
        logger.error("download feishu file failed: %s %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.error("download feishu file error: %s", e)
    return None


_lark_client: Optional[lark.Client] = None


def lark_client() -> lark.Client:
    global _lark_client
    if _lark_client is None:
        _lark_client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()
    return _lark_client


def send_text(chat_id: str, text: str) -> None:
    req = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        .build()
    )
    resp = lark_client().im.v1.message.create(req)
    if not resp.success():
        logger.error("send_text failed: %s %s", resp.code, resp.msg)


def send_file(chat_id: str, file_bytes: bytes, filename: str) -> bool:
    """上传文件到飞书并发送到会话"""
    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(file_bytes)
        tmp = f.name
    try:
        with open(tmp, "rb") as fh:
            up_resp = lark_client().im.v1.file.create(
                CreateFileRequest.builder()
                .request_body(
                    CreateFileRequestBody.builder()
                    .file_type("stream")
                    .file_name(filename)
                    .file(fh)
                    .build()
                )
                .build()
            )
        if not up_resp.success():
            logger.error("file upload failed: %s %s", up_resp.code, up_resp.msg)
            return False
        file_key = up_resp.data.file_key
        send_resp = lark_client().im.v1.message.create(
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("file")
                .content(json.dumps({"file_key": file_key}))
                .build()
            )
            .build()
        )
        return send_resp.success()
    finally:
        os.unlink(tmp)


# ─── 后端 API 客户端 ──────────────────────────────────────────────────────────

_backend = requests.Session()
_backend.headers.update({"X-Bot-Key": BOT_API_KEY})


def backend_upload(file_bytes: bytes, filename: str, task_name: str) -> Optional[str]:
    """上传 Excel 到后端创建任务，返回 task_id"""
    try:
        resp = _backend.post(
            f"{BACKEND_URL}/internal/tasks/upload",
            files={"file": (filename, file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"name": task_name},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("task_id")
    except Exception as e:
        logger.error("backend_upload failed: %s", e)
        return None


def backend_get_task(task_id: str) -> Optional[dict]:
    try:
        resp = _backend.get(f"{BACKEND_URL}/internal/tasks/{task_id}", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("backend_get_task %s failed: %s", task_id, e)
        return None


def backend_list_files(task_id: str) -> list[dict]:
    try:
        resp = _backend.get(f"{BACKEND_URL}/internal/tasks/{task_id}/files", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return []


def backend_download(task_id: str, filename: str) -> Optional[bytes]:
    try:
        resp = _backend.get(
            f"{BACKEND_URL}/internal/tasks/{task_id}/download/{filename}",
            timeout=60,
            stream=True,
        )
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        logger.error("backend_download %s/%s failed: %s", task_id, filename, e)
        return None


# ─── 消息处理 ─────────────────────────────────────────────────────────────────

def on_message(data: P2ImMessageReceiveV1) -> None:
    try:
        event = data.event
        message = event.message
        chat_id = message.chat_id or ""
        msg_type = message.message_type or ""

        if msg_type != "file":
            return

        content = json.loads(message.content or "{}")
        file_key = content.get("file_key", "")
        file_name = content.get("file_name", "upload.xlsx")
        message_id = message.message_id or ""

        if not file_name.lower().endswith(".xlsx"):
            send_text(chat_id, "⚠️ 仅支持 .xlsx 格式，请重新发送")
            return

        logger.info("收到文件 %s (key=%s) from chat %s", file_name, file_key, chat_id)

        # 下载文件
        file_bytes = download_feishu_file(message_id, file_key)
        if not file_bytes:
            send_text(chat_id, "❌ 文件下载失败，请重试")
            return

        # 上传到后端创建任务
        task_id = backend_upload(file_bytes, file_name, file_name)
        if not task_id:
            send_text(chat_id, "❌ 任务创建失败，请联系管理员")
            return

        state_add(task_id, chat_id, file_name)
        send_text(chat_id, f"✅ 已收到《{file_name}》\n后台处理中，完成后自动发送 Word 结果")
        logger.info("task %s created for chat %s", task_id, chat_id)

    except Exception:
        logger.exception("on_message error")


# ─── 后台轮询：任务完成后回传 Word ────────────────────────────────────────────

def _poller() -> None:
    logger.info("轮询线程启动，间隔 %ds", POLL_INTERVAL)
    while True:
        try:
            _check_pending()
        except Exception:
            logger.exception("poller error")
        time.sleep(POLL_INTERVAL)


def _check_pending() -> None:
    pending = state_pending()
    if not pending:
        return

    for task_id, info in pending.items():
        chat_id = info["chat_id"]
        filename = info["filename"]

        task = backend_get_task(task_id)
        if not task:
            continue

        status = task.get("status", "")

        if status in {"completed", "partial_completed"}:
            docx_files = [f for f in backend_list_files(task_id) if f["name"].endswith(".docx")]
            if not docx_files:
                # Word 还没写完，稍后再查
                continue

            hint = "（部分截图待补）" if status == "partial_completed" else ""
            send_text(chat_id, f"✅ 《{filename}》处理完成{hint}，正在发送 Word 报告……")

            success_count = 0
            for f in docx_files:
                data = backend_download(task_id, f["name"])
                if data and send_file(chat_id, data, f["name"]):
                    success_count += 1
                else:
                    send_text(chat_id, f"⚠️ {f['name']} 发送失败，请从后台手动下载")

            logger.info("task %s: sent %d/%d docx to chat %s", task_id, success_count, len(docx_files), chat_id)
            state_mark_notified(task_id)

        elif status == "failed":
            err = task.get("error_message", "")
            send_text(chat_id, f"❌ 《{filename}》处理失败\n{err or '详情见后台日志'}")
            state_mark_notified(task_id)


# ─── 主入口 ──────────────────────────────────────────────────────────────────

def main() -> None:
    if not APP_ID or not APP_SECRET:
        raise RuntimeError("FEISHU_APP_ID / FEISHU_APP_SECRET 未设置")
    if not BOT_API_KEY:
        raise RuntimeError("BOT_API_KEY 未设置")

    logger.info("启动飞书机器人…")

    threading.Thread(target=_poller, daemon=True).start()

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )
    ws = lark.ws.Client(APP_ID, APP_SECRET, event_handler=handler, log_level=lark.LogLevel.INFO)
    ws.start()


if __name__ == "__main__":
    main()
