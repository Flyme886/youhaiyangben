"""
有害样本 - 飞书机器人

流程：
  1. 用户发 Excel 文件给机器人
  2. 机器人立即回"已收到，发送「取消」可撤销"
  3. 每步骤变化时推送进度
  4. 完成后发摘要文本 + Word 文件
  5. 失败/暂停时发通知，暂停时提示"继续 {task_id}"
  6. 收到重复文件名（仍在处理中）时拒绝并提示
  7. 收到"取消"文本时取消最近一个活跃任务
  8. 收到"继续 {task_id}"文本时恢复暂停任务

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
import llm_reporter
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

STATE_FILE = Path(__file__).parent / "bot_state.json"
POLL_INTERVAL = 30  # 秒


# ─── 状态持久化 ────────────────────────────────────────────────────────────────
# state 结构: {task_id: {chat_id, filename, notified, created_at, last_step}}

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
        s[task_id] = {
            "chat_id": chat_id,
            "filename": filename,
            "notified": False,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "last_step": "",
        }
        _save_state(s)


def state_mark_notified(task_id: str, at_status: str = "") -> None:
    with _state_lock:
        s = _load_state()
        if task_id in s:
            s[task_id]["notified"] = True
            if at_status:
                s[task_id]["notified_at_status"] = at_status
            _save_state(s)


def state_unmark_notified(task_id: str) -> None:
    with _state_lock:
        s = _load_state()
        if task_id in s:
            s[task_id]["notified"] = False
            s[task_id].pop("notified_at_status", None)
            _save_state(s)


def state_update_step(task_id: str, step: str) -> None:
    with _state_lock:
        s = _load_state()
        if task_id in s:
            s[task_id]["last_step"] = step
            _save_state(s)


def state_pending() -> dict:
    with _state_lock:
        s = _load_state()
        return {tid: info for tid, info in s.items() if not info.get("notified")}


def state_find_active_by_filename(chat_id: str, filename: str) -> Optional[str]:
    """若该 chat 下同文件名的任务仍在处理中（未通知），返回其 task_id。"""
    with _state_lock:
        s = _load_state()
        for tid, info in s.items():
            if (
                info.get("chat_id") == chat_id
                and info.get("filename") == filename
                and not info.get("notified")
            ):
                return tid
    return None


def state_latest_active(chat_id: str) -> Optional[tuple[str, str]]:
    """返回该 chat 最新的未通知任务 (task_id, filename)，无则 None。"""
    with _state_lock:
        s = _load_state()
        candidates = [
            (tid, info)
            for tid, info in s.items()
            if info.get("chat_id") == chat_id and not info.get("notified")
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[1].get("created_at", ""), reverse=True)
        tid, info = candidates[0]
        return tid, info.get("filename", "")


# ─── 飞书 API 工具 ───────────────────────────────────────────────────────────────

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


# ─── 后端 API 客户端 ────────────────────────────────────────────────────────────

_backend = requests.Session()
_backend.headers.update({"X-Bot-Key": BOT_API_KEY})


def backend_upload(file_bytes: bytes, filename: str, task_name: str) -> Optional[str]:
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


def backend_queue_position(task_id: str) -> int:
    try:
        tasks = _backend.get(f"{BACKEND_URL}/internal/tasks", timeout=10).json()
        queued = [t for t in tasks if t["status"] in {"queued", "running"} and t["id"] != task_id]
        return len(queued)
    except Exception:
        return 0


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


def backend_cancel(task_id: str) -> bool:
    try:
        resp = _backend.post(f"{BACKEND_URL}/internal/tasks/{task_id}/cancel", timeout=10)
        resp.raise_for_status()
        return resp.json().get("ok", False)
    except Exception as e:
        logger.error("backend_cancel %s failed: %s", task_id, e)
        return False


def backend_resume(task_id: str) -> bool:
    try:
        resp = _backend.post(f"{BACKEND_URL}/internal/tasks/{task_id}/resume", timeout=10)
        resp.raise_for_status()
        return resp.json().get("ok", False)
    except Exception as e:
        logger.error("backend_resume %s failed: %s", task_id, e)
        return False


# ─── 文字指令处理 ──────────────────────────────────────────────────────────────

def _handle_text(chat_id: str, text: str) -> None:
    text = text.strip()

    if text == "取消":
        result = state_latest_active(chat_id)
        if not result:
            send_text(chat_id, "ℹ️ 当前没有进行中的任务")
            return
        task_id, filename = result
        ok = backend_cancel(task_id)
        if ok:
            state_mark_notified(task_id, "cancelled")
            send_text(chat_id, f"🛑 任务《{filename}》已取消")
        else:
            send_text(chat_id, "⚠️ 取消失败，任务可能已完成或不在运行中")
        return

    if text.startswith("继续"):
        parts = text.split()
        if len(parts) < 2:
            send_text(chat_id, "请输入「继续 任务ID」，任务ID 在任务暂停提示中可见")
            return
        task_id = parts[1]
        ok = backend_resume(task_id)
        if ok:
            state_unmark_notified(task_id)
            send_text(chat_id, f"▶️ 任务 {task_id} 已重新排队，完成后自动发送结果")
        else:
            send_text(chat_id, f"⚠️ 任务 {task_id} 无法继续（状态不对或不存在）")
        return


# ─── 消息处理 ──────────────────────────────────────────────────────────────────

def on_message(data: P2ImMessageReceiveV1) -> None:
    try:
        event = data.event
        message = event.message
        chat_id = message.chat_id or ""
        msg_type = message.message_type or ""

        # 文字指令（取消 / 继续 task_id）
        if msg_type == "text":
            content = json.loads(message.content or "{}")
            text = content.get("text", "").strip()
            if text:
                _handle_text(chat_id, text)
            return

        if msg_type != "file":
            return

        content = json.loads(message.content or "{}")
        file_key = content.get("file_key", "")
        file_name = content.get("file_name", "upload.xlsx")
        message_id = message.message_id or ""

        if not file_name.lower().endswith(".xlsx"):
            send_text(chat_id, "⚠️ 仅支持 .xlsx 格式，请重新发送")
            return

        # 重复文件检测：同文件名仍在处理中则拒绝
        dup_id = state_find_active_by_filename(chat_id, file_name)
        if dup_id:
            send_text(
                chat_id,
                f"⚠️ 与已发送文件完全相同，请检查\n（《{file_name}》正在处理中，任务ID: {dup_id}）",
            )
            return

        logger.info("收到文件 %s (key=%s) from chat %s", file_name, file_key, chat_id)

        file_bytes = download_feishu_file(message_id, file_key)
        if not file_bytes:
            send_text(chat_id, "❌ 文件下载失败，请重试")
            return

        task_id = backend_upload(file_bytes, file_name, file_name)
        if not task_id:
            send_text(chat_id, "❌ 任务创建失败，请联系管理员")
            return

        state_add(task_id, chat_id, file_name)
        ahead = backend_queue_position(task_id)
        if ahead == 0:
            queue_hint = "立即开始处理"
        else:
            queue_hint = f"排队中（前方 {ahead} 个任务）"
        send_text(
            chat_id,
            f"✅ 已收到《{file_name}》，{queue_hint}\n完成后自动发送 Word 结果\n发送「取消」可撤销此任务",
        )
        logger.info("task %s created for chat %s (ahead=%d)", task_id, chat_id, ahead)

    except Exception:
        logger.exception("on_message error")


# ─── 后台轮询：进度通知 + 完成回传 ───────────────────────────────────────────────

def _poller() -> None:
    logger.info("轮询线程启动，间隔 %ds", POLL_INTERVAL)
    while True:
        try:
            _check_pending()
            _check_resumed()
        except Exception:
            logger.exception("poller error")
        time.sleep(POLL_INTERVAL)


def _check_pending() -> None:
    pending = state_pending()
    if not pending:
        return

    for task_id, info in list(pending.items()):
        chat_id = info["chat_id"]
        filename = info["filename"]
        last_step = info.get("last_step", "")

        task = backend_get_task(task_id)
        if not task:
            continue

        status = task.get("status", "")
        current_step = task.get("current_step", "")

        # 步骤变化时推送进度（仅 running 状态）
        if status == "running" and current_step and current_step != last_step:
            state_update_step(task_id, current_step)
            send_text(chat_id, f"⏳ 《{filename}》→ {current_step}")

        if status in {"completed", "partial_completed"}:
            _notify_completed(task_id, task, chat_id, filename, status)

        elif status == "failed":
            err = task.get("error_message", "")
            msg = llm_reporter.report_failed(filename, err) \
                  or f"❌ 《{filename}》处理失败\n{err or '详情见后台日志'}"
            send_text(chat_id, msg)
            state_mark_notified(task_id, "failed")

        elif status == "paused_captcha":
            err = task.get("error_message", "")
            msg = llm_reporter.report_paused(filename, err, task_id) \
                  or (f"⏸️ 《{filename}》任务暂停 — 触发验证码\n{err or ''}\n"
                      f"处理完毕后，发送「继续 {task_id}」恢复任务")
            send_text(chat_id, msg)
            state_mark_notified(task_id, "paused_captcha")

        elif status == "cancelled":
            state_mark_notified(task_id, "cancelled")


def _check_resumed() -> None:
    """任务在网页后台被 resume 时，bot 状态里仍是 notified=True，
    此函数扫描这些任务，若后端已重新变为 running/queued 则重置轮询。"""
    with _state_lock:
        s = _load_state()
        candidates = {
            tid: info for tid, info in s.items()
            if info.get("notified") and info.get("notified_at_status") in {"failed", "paused_captcha"}
        }
    for task_id in candidates:
        task = backend_get_task(task_id)
        if task and task.get("status") in {"running", "queued", "completed", "partial_completed"}:
            state_unmark_notified(task_id)


def _notify_completed(task_id: str, task: dict, chat_id: str, filename: str, status: str) -> None:
    docx_files = [f for f in backend_list_files(task_id) if f["name"].endswith(".docx")]
    if not docx_files:
        return  # Word 还没写完，下次轮询再试

    # 解析统计数据
    try:
        summary = json.loads(task.get("summary_json", "{}"))
        counts = summary.get("capture_status_counts") or {}
        step_failures = summary.get("worker_issues") or []
    except Exception:
        counts = {}
        step_failures = []

    is_partial = (status == "partial_completed")

    # 尝试 LLM 播报，失败自动降级为模板
    msg = llm_reporter.report_completed(filename, counts, step_failures, is_partial)
    if not msg:
        total = sum(counts.values()) if counts else 0
        ok_n = counts.get("ok", 0)
        login_n = counts.get("login_required", 0)
        blocked_n = counts.get("blocked_or_blank", 0)
        hint = "（部分截图待补）" if is_partial else ""
        parts = [f"✅ 《{filename}》处理完成{hint}，正在发送 Word 报告……"]
        if total > 0:
            parts.append(f"📊 共 {total} 条 | 成功 {ok_n} | 需登录 {login_n} | 被拦截 {blocked_n}")
        msg = "\n".join(parts)

    send_text(chat_id, msg)

    success_count = 0
    for f in docx_files:
        data = backend_download(task_id, f["name"])
        if data and send_file(chat_id, data, f["name"]):
            success_count += 1
        else:
            send_text(chat_id, f"⚠️ {f['name']} 发送失败，请从后台手动下载")

    logger.info("task %s: sent %d/%d docx to chat %s", task_id, success_count, len(docx_files), chat_id)
    state_mark_notified(task_id, status)


# ─── 主入口 ───────────────────────────────────────────────────────────────────

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
