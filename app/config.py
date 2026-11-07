from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    app_password: str = os.environ.get("APP_PASSWORD", "change-this-password")
    host: str = os.environ.get("HOST", "0.0.0.0")
    port: int = int(os.environ.get("PORT", "8787"))
    task_root: Path = Path(os.environ.get("TASK_ROOT", str(BASE_DIR / "data" / "tasks")))
    database_path: Path = Path(os.environ.get("DATABASE_PATH", str(BASE_DIR / "data" / "app.db")))
    skill_dir: Path = Path(os.environ.get("SKILL_DIR", str(BASE_DIR / "skills" / "harmful_sample")))
    python_bin: str = os.environ.get("PYTHON_BIN", sys.executable)
    node_bin: str = os.environ.get("NODE_BIN", shutil.which("node") or "node")
    storage_state_path: Path = Path(os.environ.get("STORAGE_STATE_PATH", str(BASE_DIR / "data" / "storage_state.json")))
    capture_engine: str = os.environ.get("CAPTURE_ENGINE", "playwright")
    capture_profile: str = os.environ.get("CAPTURE_PROFILE", "desktop")
    drission_domains: str = os.environ.get("DRISSION_DOMAINS", "xiaohongshu.com,weibo.com")
    captcha_pause_threshold: int = int(os.environ.get("CAPTCHA_PAUSE_THRESHOLD", "1"))
    capture_headless: bool = os.environ.get("CAPTURE_HEADLESS", "true").lower() in {"1", "true", "yes", "on"}
    capture_full_page: bool = os.environ.get("CAPTURE_FULL_PAGE", "true").lower() in {"1", "true", "yes", "on"}
    task_timeout_seconds: int = int(os.environ.get("TASK_TIMEOUT_SECONDS", "7200"))
    session_secret: str = os.environ.get("SESSION_SECRET", "harmful-sample-web-local-secret")
    bot_api_key: str = os.environ.get("BOT_API_KEY", "")
    storage_state_max_age_days: int = int(os.environ.get("STORAGE_STATE_MAX_AGE_DAYS", "7"))
    capture_retries: int = int(os.environ.get("CAPTURE_RETRIES", "2"))
    # ── 抗风控：并发/节奏/出口 IP ─────────────────────────────────────────────
    # 默认 3 路并发 + 1.2~3s 随机间隔。想要更「像人」可把 CAPTURE_BATCH_SIZE 调成 1
    # （单线程，但 1000+ 条样本会明显更慢）；想更快可调到 6，但更易触发风控。
    capture_batch_size: int = int(os.environ.get("CAPTURE_BATCH_SIZE", "3"))
    capture_batch_delay_min_ms: int = int(os.environ.get("CAPTURE_BATCH_DELAY_MIN_MS", "1200"))
    capture_batch_delay_max_ms: int = int(os.environ.get("CAPTURE_BATCH_DELAY_MAX_MS", "3000"))
    capture_proxy: str = os.environ.get("CAPTURE_PROXY", "") or os.environ.get("HTTPS_PROXY", "")
    capture_user_agent: str = os.environ.get("CAPTURE_USER_AGENT", "")
    # ── 任务级并发 ────────────────────────────────────────────────────────────
    # 原来只有一个 worker 线程消费队列，一个 1000+ 条的任务会把后面所有任务堵住
    # 半小时以上（实测 1057 条 33 分钟）。默认 2 个：一个任务跑抓取时，另一个任务
    # 仍能立刻开始。注意内存占用 ≈ 任务数 × (1 个 chromium + batch_size 个页面)，
    # 2C4G 轻量机建议不要超过 2；4G 以上可试 3。
    task_workers: int = max(1, int(os.environ.get("TASK_WORKERS", "2")))
    feishu_bot_notify_url: str = os.environ.get("FEISHU_BOT_NOTIFY_URL", "")


settings = Settings()
