from __future__ import annotations

from typing import Iterable


CAPTCHA_TERMS = [
    "captcha",
    "recaptcha",
    "hcaptcha",
    "cloudflare",
    "verify",
    "verification",
    "risk",
    "challenge",
    "验证码",
    "滑块",
    "安全验证",
    "访问异常",
    "环境异常",
    "人机验证",
]

LOGIN_TERMS = ["登录", "login", "sign in", "请先登录", "登录后查看"]


def detect_text_signals(text: str, prefix: str) -> list[str]:
    lower = text.lower()
    signals = []
    for term in CAPTCHA_TERMS:
        if term.lower() in lower:
            signals.append(f"{prefix}:{term}")
    return signals


def detect_login_text(text: str) -> bool:
    lower = text.lower()
    return any(term.lower() in lower for term in LOGIN_TERMS)


def has_manual_action_signal(status: str, signals: Iterable[str]) -> bool:
    return status in {"captcha_detected", "login_required"} or bool(list(signals))
