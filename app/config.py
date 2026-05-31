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


settings = Settings()
