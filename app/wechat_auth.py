"""
微信小程序登录态：code2session + 自签 token。

设计要点
--------
1. `wx.login` 拿到的 code 只能换一次，且必须服务端换，前端不能碰 AppSecret。
2. `session_key` 属于敏感凭据，**只落服务端**，绝不随登录响应下发给小程序。
3. token 用 HMAC-SHA256 自签，不引入额外依赖（`itsdangerous` 虽然已在
   requirements 里，但那是 SessionMiddleware 在用，这里保持解耦）。
4. token 载荷里只放 openid 和过期时间，不放任何权限位——权限由服务端每次判定。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

JSCODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"


@dataclass(frozen=True)
class WxSettings:
    app_id: str = os.environ.get("WX_APP_ID", "")
    app_secret: str = os.environ.get("WX_APP_SECRET", "")
    # 生产环境务必在 .env 里换成一串随机值；留空则回落到 SESSION_SECRET
    token_secret: str = os.environ.get(
        "WX_TOKEN_SECRET", os.environ.get("SESSION_SECRET", "wx-token-fallback-secret")
    )
    # token 有效期，默认 7 天（与小程序端 storage 生命周期一致）
    token_ttl: int = int(os.environ.get("WX_TOKEN_TTL", str(7 * 24 * 3600)))
    # 是否允许「访问密码」兜底登录。生产环境如果把小程序完全交给微信登录，
    # 建议设为 false，避免保留一条可爆破的密码通道。
    allow_password_login: bool = os.environ.get("WX_ALLOW_PASSWORD_LOGIN", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.app_secret)


wx_settings = WxSettings()


class WxLoginError(Exception):
    """code2session 失败。message 已经是可直接展示给用户的中文文案。"""


# --------------------------------------------------------------------------
# 微信侧
# --------------------------------------------------------------------------


def code2session(code: str) -> dict[str, Any]:
    """用 code 换 openid / session_key。

    :raises WxLoginError: 配置缺失、网络异常或微信返回 errcode != 0
    """
    if not wx_settings.configured:
        raise WxLoginError("服务端尚未配置 WX_APP_ID / WX_APP_SECRET，请改用访问密码登录")
    if not code:
        raise WxLoginError("缺少登录 code")

    params = urllib.parse.urlencode(
        {
            "appid": wx_settings.app_id,
            "secret": wx_settings.app_secret,
            "js_code": code,
            "grant_type": "authorization_code",
        }
    )
    url = f"{JSCODE2SESSION_URL}?{params}"

    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise WxLoginError(f"连接微信接口失败：{exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise WxLoginError("微信接口返回了非 JSON 内容") from exc

    errcode = int(payload.get("errcode") or 0)
    if errcode != 0:
        # 常见：40029 code 无效、45011 频率限制、40125 secret 错误
        raise WxLoginError(_friendly_wx_error(errcode, payload.get("errmsg", "")))

    openid = payload.get("openid")
    if not openid:
        raise WxLoginError("微信未返回 openid")

    return {
        "openid": openid,
        "session_key": payload.get("session_key", ""),
        "unionid": payload.get("unionid", ""),
    }


def _friendly_wx_error(errcode: int, errmsg: str) -> str:
    known = {
        40029: "登录 code 已失效，请重新进入小程序",
        40125: "AppSecret 配置不正确",
        45011: "登录过于频繁，请稍后再试",
        40226: "该账号存在风险，已拒绝登录",
    }
    return known.get(errcode, f"微信登录失败（{errcode}: {errmsg}）")


# --------------------------------------------------------------------------
# token 签发与校验（纯标准库）
# --------------------------------------------------------------------------


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(body: bytes) -> str:
    digest = hmac.new(wx_settings.token_secret.encode("utf-8"), body, hashlib.sha256).digest()
    return _b64e(digest)


def issue_token(openid: str, source: str = "wechat") -> tuple[str, int]:
    """签发 token，返回 (token, 有效秒数)。

    :param source: 'wechat' 表示微信静默登录，'password' 表示访问密码兜底
    """
    now = int(time.time())
    payload = {
        "sub": openid,
        "src": source,
        "iat": now,
        "exp": now + wx_settings.token_ttl,
    }
    body = _b64e(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return f"{body}.{_sign(body.encode('ascii'))}", wx_settings.token_ttl


def verify_token(token: str) -> dict[str, Any] | None:
    """校验 token，合法返回载荷，否则返回 None（不抛异常，方便中间件里用）。"""
    if not token or "." not in token:
        return None
    body, _, signature = token.partition(".")
    if not body or not signature:
        return None

    expected = _sign(body.encode("ascii"))
    # 定长比较，避免时序侧信道
    if not hmac.compare_digest(expected, signature):
        return None

    try:
        payload = json.loads(_b64d(body))
    except (ValueError, json.JSONDecodeError):
        return None

    if int(payload.get("exp") or 0) < int(time.time()):
        return None

    return payload
