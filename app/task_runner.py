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
WORKER_STARTED = 0
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
    """按 settings.task_workers 拉起常驻 worker 线程（幂等）。"""
    global WORKER_STARTED
    target = max(1, settings.task_workers)
    with WORKER_LOCK:
        if WORKER_STARTED >= target:
            return
        for index in range(WORKER_STARTED, target):
            threading.Thread(
                target=_worker_loop,
                name=f"harmful-sample-worker-{index + 1}",
                daemon=True,
            ).start()
        WORKER_STARTED = target


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
        except Exception as exc:
            # 单个任务抛出未预期异常时，绝不能把 worker 线程带走 ——
            # 线程一死，队列就再也消费不动，服务看着是活的却永远不干活。
            try:
                update_task(
                    task_id,
                    status="failed",
                    current_step="失败",
                    finished_at=utc_now(),
                    error_message=f"worker error: {exc}",
                )
            except Exception:
                pass
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
    # 命中验证码风控时，不再提前 return：仍然跑完「截图质检 → 生成 Word」，
    # 让用户在拿到 Word（截图位留空）的同时看到任务被标记为等待人工处理。
    captcha_paused = False

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
                    "--batch-size",
                    str(settings.capture_batch_size),
                    "--batch-delay-min-ms",
                    str(settings.capture_batch_delay_min_ms),
                    "--batch-delay-max-ms",
                    str(settings.capture_batch_delay_max_ms),
                ]
            )
            # 代理只在配置了才提示，并且**只走环境变量、不进命令行**：
            # run_step 会把整条命令写进 run.log，而代理 URL 带 user:pass，落盘即泄密。
            # capture_playwright.py 的 --proxy 默认值会读 CAPTURE_PROXY / HTTPS_PROXY。
            if settings.capture_proxy:
                log.write("[capture] 住宅代理已配置（走 CAPTURE_PROXY 环境变量，不写入 run.log）\n")
            if settings.capture_user_agent:
                capture_cmd.extend(["--user-agent", settings.capture_user_agent])

            capture_ok = run_step(task_id, "截图采集", capture_cmd, task_dir, log, deadline, required=False)
            if not capture_ok:
                step_failures.append("截图采集失败")

            if _is_cancelled(task_id):
                return

            capture_counts = read_capture_counts(screenshots_manifest)
            captcha_hits = capture_counts.get("captcha_detected", 0)
            if captcha_hits >= settings.captcha_pause_threshold:
                captcha_paused = True
                total_hits = sum(capture_counts.values())
                # 交付优先：验证码只作为「一句简短说明」，不再中断流程、不再要求人工介入。
                # 文案刻意不含 ";" —— 后端用 "; " 拼接多段失败信息，前端按 ";" 切段翻译，
                # 说明句里带分号会被切碎。
                step_failures.append(
                    f"{captcha_hits}/{total_hits} 条命中平台验证码，未取到截图，"
                    f"Word 已按文字版正常交付（图片位置留空）"
                )

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
                step_failures.append("未生成质检manifest，Word 按文字版交付")

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

    finalize_task(task_id, task_dir, step_failures, paused=captcha_paused)


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


def count_missing_screenshots(
    output_dir: Path, summary: dict, capture_counts: dict[str, int]
) -> int:
    """数出「文档里不会出现图片」的记录条数（= 前端展示的「未附图条数」）。

    首选**质检清单**，因为它才是「这张图能不能用」的权威口径：
      quality ∈ {usable, needs_review} → 会插图；
      unusable / not_captured / 其它 → 正文留白。

    质检清单不存在、为空、或结构不符时，退化为抓取计数（排除确实带图的 ok）。

    刻意不含 len(step_failures)：那是流程告警条数，不是记录条数，
    混进来会让前端显示的数字比实际记录数还多（实测 1057 条被显示成 1058）。
    """
    quality_manifest = output_dir / "screenshots" / "screenshot_quality_manifest.json"
    if quality_manifest.exists():
        try:
            data = json.loads(quality_manifest.read_text(encoding="utf-8"))
        except Exception:
            data = None
        items = data.get("items") if isinstance(data, dict) else data
        if isinstance(items, list) and items:
            missing = 0
            for it in items:
                if not isinstance(it, dict):
                    continue
                quality = str(it.get("quality") or it.get("status") or "").strip()
                if quality in {"usable", "needs_review"}:
                    continue
                if not quality and (it.get("images") or it.get("screenshot_path")):
                    # 清单里没写质量字段，退化成「有没有落到图片」
                    continue
                missing += 1
            return missing

    status_counts = summary.get("screenshot_status_counts") or {}
    return sum(
        int(v) for k, v in status_counts.items() if k not in {"ok", "usable", "needs_review"}
    )


def finalize_task(task_id: str, task_dir: Path, step_failures: list[str], paused: bool) -> None:
    output_dir = task_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = read_completion_summary(output_dir)
    capture_counts = read_capture_counts(output_dir / "screenshots" / "screenshots_manifest.json")
    if capture_counts:
        summary["capture_status_counts"] = capture_counts
    docx_files = sorted(output_dir.glob("*.docx"))
    # issue_count 对外就是「未附图条数」。口径必须与**文档本身**一致：
    #   文档里一条记录有没有图，取决于质检清单 quality 是否 usable / needs_review
    #   （excel_to_word 只在 quality in {usable, needs_review} 时才插图）。
    # 只按抓取状态数会低估：实测 8 条里抓取 ok=6，但质检把 3 张判成 unusable，
    # 正文实际只有 5 张图 —— 按抓取数算会报「未附图 2」，而文档里是 3 条留白。
    issue_count = count_missing_screenshots(output_dir, summary, capture_counts)
    # 有未附图记录、或有流程性告警，都属「部分完成」；两者皆无才是「完成」。
    has_issues = bool(issue_count) or bool(step_failures)
    summary["missing_screenshots"] = issue_count

    if step_failures:
        summary["worker_issues"] = step_failures
    zip_outputs(task_dir, output_dir)

    # 交付优先原则：**只要有 Word 产出，就按「已交付」收尾**，验证码/截图失败只留一句说明，
    # 不进入等待人工介入的状态。paused_captcha 仅保留给「命中验证码且一个 Word 都没产出」
    # 这种真的无物可交的兜底情形。
    if docx_files:
        final_status = "partial_completed" if has_issues else "completed"
        current_step = "完成"
        error_message = "; ".join(step_failures)
    elif paused:
        final_status = "paused_captcha"
        current_step = "等待人工处理"
        error_message = "; ".join(step_failures) or "命中验证码且未产出任何 Word"
    else:
        final_status = "failed"
        current_step = "失败"
        error_message = "; ".join(step_failures) or "No Word files were generated."

    # 结案结论：每次任务都产出一段，供小程序任务详情页展示。
    # 结论生成是「加法」不是「必需步骤」——失败了也要照常收尾，所以整段包在 try 里，
    # build_conclusion 内部还有模板兜底，正常情况下不会走到 except。
    try:
        import llm_reporter

        task_row = get_task(task_id) or {}
        summary["conclusion"] = llm_reporter.build_conclusion(
            filename=str(task_row.get("input_filename") or ""),
            capture_counts=capture_counts or {},
            step_failures=step_failures,
            docx_count=len(docx_files),
            issue_count=issue_count,
            is_partial=final_status == "partial_completed",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[conclusion] 结论生成失败，跳过：{exc}", flush=True)

    update_task(
        task_id,
        status=final_status,
        current_step=current_step,
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
