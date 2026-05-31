from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import settings
from .db import get_task, update_task, utc_now


TASK_QUEUE: queue.Queue[str] = queue.Queue()
WORKER_STARTED = False
WORKER_LOCK = threading.Lock()

# 运行中子进程注册表（task_id → Popen），用于取消时杀进程
_ACTIVE_PROCS: dict[str, "subprocess.Popen[str]"] = {}
_ACTIVE_PROCS_LOCK = threading.Lock()


def _register_proc(task_id: str, proc: "subprocess.Popen[str]") -> None:
    with _ACTIVE_PROCS_LOCK:
        _ACTIVE_PROCS[task_id] = proc


def _unregister_proc(task_id: str) -> None:
    with _ACTIVE_PROCS_LOCK:
        _ACTIVE_PROCS.pop(task_id, None)


def _is_cancelled(task_id: str) -> bool:
    task = get_task(task_id)
    return task is not None and task.get("status") == "cancelled"


def cancel_task(task_id: str) -> bool:
    """标记任务为已取消，并终止正在运行的子进程。"""
    task = get_task(task_id)
    if not task or task["status"] not in {"queued", "running"}:
        return False
    update_task(task_id, status="cancelled", current_step="已取消", error_message="用户取消任务")
    with _ACTIVE_PROCS_LOCK:
        proc = _ACTIVE_PROCS.get(task_id)
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
    return True


def ensure_worker() -> None:
    global WORKER_STARTED
    with WORKER_LOCK:
        if WORKER_STARTED:
            return
        thread = threading.Thread(target=_worker_loop, name="harmful-sample-worker", daemon=True)
        thread.start()
        WORKER_STARTED = True


def enqueue_task(task_id: str) -> None:
    ensure_worker()
    TASK_QUEUE.put(task_id)


def _worker_loop() -> None:
    while True:
        task_id = TASK_QUEUE.get()
        try:
            task = get_task(task_id)
            if task and task.get("status") == "cancelled":
                pass  # 入队前已被取消，跳过
            else:
                run_task(task_id)
        finally:
            TASK_QUEUE.task_done()


def _extract_friendly_error(stdout: str) -> str:
    import re
    m = re.search(r"SystemExit: (.+)", stdout)
    if m:
        return m.group(1).strip()
    lines = [l.strip() for l in stdout.splitlines() if l.strip()]
    return lines[-1] if lines else ""


def _count_captured(manifest_path: Path) -> dict | None:
    if not manifest_path.exists():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        items = data.get("items", [])
        ok = sum(1 for it in items if it.get("status") == "ok")
        return {"ok": ok, "total": len(items)}
    except Exception:
        return None


def run_task(task_id: str) -> None:
    task = get_task(task_id)
    if not task:
        return

    task_dir = Path(task["task_dir"])
    output_dir = task_dir / "output"
    screenshots_dir = output_dir / "screenshots"
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    log_path = task_dir / "run.log"
    update_task(task_id, status="running", current_step="预检 Excel", started_at=utc_now(), error_message="")

    deadline = time.time() + settings.task_timeout_seconds
    step_failures: list[str] = []

    # 检查 storage_state 文件年龄
    ss_path = settings.storage_state_path
    if ss_path.exists():
        age_days = (time.time() - ss_path.stat().st_mtime) / 86400
        if age_days > settings.storage_state_max_age_days:
            step_failures.append(
                f"⚠️ 登录态文件已 {int(age_days)} 天未更新（超过 {settings.storage_state_max_age_days} 天），"
                f"建议去 Admin 页面刷新 storage_state.json"
            )

    with log_path.open("a", encoding="utf-8", errors="replace") as log:
        log.write(f"[{datetime.now().isoformat(timespec='seconds')}] fixed worker started\n")
        log.write(f"skill_dir={settings.skill_dir}\n")
        log.flush()

        try:
            excel_script = settings.skill_dir / "scripts" / "excel_to_word.py"
            capture_script = settings.skill_dir / "scripts" / "capture_links.py"
            quality_script = settings.skill_dir / "scripts" / "quality_check_screenshots.py"
            input_xlsx = task_dir / "input.xlsx"
            links_manifest = output_dir / "links_manifest.json"
            screenshots_manifest = screenshots_dir / "screenshots_manifest.json"
            quality_manifest = screenshots_dir / "screenshot_quality_manifest.json"

            if _is_cancelled(task_id):
                return

            run_step(
                task_id,
                "预检 Excel",
                [settings.python_bin, str(excel_script), str(input_xlsx), "--output-dir", str(output_dir), "--dry-run"],
                task_dir,
                log,
                deadline,
                required=True,
            )

            if _is_cancelled(task_id):
                return

            capture_cmd = [
                settings.python_bin,
                str(capture_script),
                str(links_manifest),
                "--output-dir",
                str(screenshots_dir),
            ]
            if settings.storage_state_path.exists():
                capture_cmd.extend(["--storage-state", str(settings.storage_state_path)])
            if settings.node_bin:
                capture_cmd.extend(["--node-bin", settings.node_bin])
            if screenshots_manifest.exists():
                capture_cmd.extend(["--previous-manifest", str(screenshots_manifest)])
            capture_cmd.extend(
                [
                    "--engine",
                    settings.capture_engine,
                    "--profile",
                    settings.capture_profile,
                    "--headless",
                    "true" if settings.capture_headless else "false",
                    "--full-page",
                    "true" if settings.capture_full_page else "false",
                    "--drission-domains",
                    settings.drission_domains,
                    "--retries",
                    str(settings.capture_retries),
                ]
            )

            capture_ok = run_step(task_id, "截图采集", capture_cmd, task_dir, log, deadline, required=False)
            if not capture_ok:
                step_failures.append("截图采集失败")

            if _is_cancelled(task_id):
                return

            capture_counts = read_capture_counts(screenshots_manifest)
            if capture_counts.get("captcha_detected", 0) >= settings.captcha_pause_threshold:
                step_failures.append(f"检测到验证码 {capture_counts.get('captcha_detected', 0)} 条，任务暂停等待人工处理")
                finalize_task(task_id, task_dir, step_failures, paused=True)
                return

            if _is_cancelled(task_id):
                return

            if screenshots_manifest.exists():
                quality_ok = run_step(
                    task_id,
                    "截图质检",
                    [settings.python_bin, str(quality_script), str(screenshots_manifest)],
                    task_dir,
                    log,
                    deadline,
                    required=False,
                )
                if not quality_ok:
                    step_failures.append("截图质检失败")
            else:
                step_failures.append("未生成 screenshots_manifest.json")

            if _is_cancelled(task_id):
                return

            final_cmd = [settings.python_bin, str(excel_script), str(input_xlsx), "--output-dir", str(output_dir)]
            if quality_manifest.exists():
                final_cmd.extend(["--screenshots-manifest", str(quality_manifest)])
            elif screenshots_manifest.exists():
                final_cmd.extend(["--screenshots-manifest", str(screenshots_manifest)])
                step_failures.append("未生成 screenshot_quality_manifest.json，Word 使用原始截图")
            else:
                step_failures.append("未生成质检manifest，Word 将使用截图待补")

            word_ok = run_step(task_id, "生成 Word", final_cmd, task_dir, log, deadline, required=False)
            if not word_ok:
                step_failures.append("生成 Word 失败")
        except TimeoutError as exc:
            already_done = _count_captured(screenshots_dir / "screenshots_manifest.json")
            msg = str(exc)
            if already_done is not None:
                msg += f"（已完成截图 {already_done['ok']}/{already_done['total']}）"
            step_failures.append(msg)
        except subprocess.CalledProcessError as exc:
            if _is_cancelled(task_id):
                return
            raw = exc.output or ""
            friendly = _extract_friendly_error(raw)
            step_failures.append(friendly or f"{exc.cmd[0]} 退出码 {exc.returncode}")
        except Exception as exc:
            if _is_cancelled(task_id):
                return
            step_failures.append(f"worker error: {exc}")

    if _is_cancelled(task_id):
        return

    finalize_task(task_id, task_dir, step_failures, paused=False)


def run_step(
    task_id: str,
    label: str,
    cmd: list[str],
    cwd: Path,
    log: Any,
    deadline: float,
    required: bool,
) -> bool:
    if time.time() > deadline:
        raise TimeoutError(f"任务超时，停止于：{label}")

    update_task(task_id, current_step=label)
    started = time.time()
    log.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] STEP {label}\n")
    log.write(" ".join(cmd) + "\n")
    log.flush()

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["NODE_BIN"] = settings.node_bin
    if os.environ.get("CHROMIUM_EXECUTABLE_PATH"):
        env["CHROMIUM_EXECUTABLE_PATH"] = os.environ["CHROMIUM_EXECUTABLE_PATH"]

    timeout = max(1, int(deadline - time.time()))
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    _register_proc(task_id, proc)
    try:
        stdout, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, _ = proc.communicate()
        _unregister_proc(task_id)
        raise TimeoutError(f"任务超时，停止于：{label}")
    _unregister_proc(task_id)

    elapsed = round(time.time() - started, 2)
    if stdout:
        log.write(stdout)
        if not stdout.endswith("\n"):
            log.write("\n")
    log.write(f"[{datetime.now().isoformat(timespec='seconds')}] exit={proc.returncode} elapsed={elapsed}s\n")
    log.flush()

    if proc.returncode == 0:
        return True
    if required:
        raise subprocess.CalledProcessError(proc.returncode, cmd, output=stdout)
    return False


def finalize_task(task_id: str, task_dir: Path, step_failures: list[str], paused: bool) -> None:
    output_dir = task_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = read_completion_summary(output_dir)
    capture_counts = read_capture_counts(output_dir / "screenshots" / "screenshots_manifest.json")
    if capture_counts:
        summary["capture_status_counts"] = capture_counts
    docx_files = sorted(output_dir.glob("*.docx"))
    status_counts = summary.get("screenshot_status_counts") or {}
    issue_count = sum(int(v) for k, v in status_counts.items() if k not in {"ok", "usable", "needs_review", "not_captured"})
    issue_count += len(step_failures)

    if step_failures:
        summary["worker_issues"] = step_failures
    zip_outputs(task_dir, output_dir)

    if paused:
        final_status = "paused_captcha"
        error_message = "; ".join(step_failures)
    elif docx_files and issue_count:
        final_status = "partial_completed"
        error_message = "; ".join(step_failures)
    elif docx_files:
        final_status = "completed"
        error_message = ""
    else:
        final_status = "failed"
        error_message = "; ".join(step_failures) or "No Word files were generated."

    update_task(
        task_id,
        status=final_status,
        current_step="等待人工处理" if final_status == "paused_captcha" else ("完成" if final_status in {"completed", "partial_completed"} else "失败"),
        finished_at=utc_now(),
        docx_count=len(docx_files),
        issue_count=issue_count,
        summary_json=json.dumps(summary, ensure_ascii=False),
        error_message=error_message,
    )


def read_capture_counts(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {}
    counts: dict[str, int] = {}
    for item in data.get("items", []):
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def read_completion_summary(output_dir: Path) -> dict[str, Any]:
    marker = output_dir / "完成标记.json"
    if marker.exists():
        try:
            return json.loads(marker.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            return {"issues": ["完成标记.json 不是有效 JSON"]}
    return {"issues": ["未找到完成标记.json"], "docx_files": [p.name for p in output_dir.glob("*.docx")]}


def zip_outputs(task_dir: Path, output_dir: Path) -> None:
    result_zip = output_dir / "Word结果.zip"
    full_zip = output_dir / "完整产物包.zip"
    with zipfile.ZipFile(result_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(output_dir.glob("*.docx")):
            zf.write(path, path.name)
    with zipfile.ZipFile(full_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(task_dir.rglob("*")):
            if path.is_file() and path != full_zip:
                zf.write(path, path.relative_to(task_dir))
