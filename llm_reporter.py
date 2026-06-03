"""
LLM 播报模块

将任务原始状态数据转化为自然语言推送给飞书用户。
仅在以下三个时机调用：任务完成、任务失败、验证码暂停。
LLM 调用失败时自动降级为模板文字，不影响主流程。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("llm_reporter")

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
LLM_ENABLED = os.environ.get("LLM_ENABLED", "true").lower() in {"1", "true", "yes", "on"}

_SYSTEM_PROMPT = """\
你是有害样本整理任务播报助手。根据采集任务数据生成简洁友好的中文播报。

严格规则：
1. 只汇报结果，绝不闲聊，绝不回答任何额外问题
2. 最重要的是：分析主要失败原因（尽量按平台说明：微博/今日头条/抖音/微信视频号等等）
3. 给出1条具体可操作的建议
4. 使用 emoji，不用 markdown（不加粗、不用代码块）
5. 最多 6 行，语气简洁专业\
"""


def _call_llm(user_msg: str) -> str | None:
    """调用 DeepSeek，失败返回 None（调用方降级为模板）。"""
    if not LLM_ENABLED or not DEEPSEEK_API_KEY:
        return None
    try:
        from openai import OpenAI  # 懒导入，避免未安装时全局报错
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=350,
            temperature=0.3,
            stream=False,
        )
        text = resp.choices[0].message.content or ""
        return text.strip() or None
    except Exception as exc:
        logger.warning("LLM 调用失败，降级模板: %s", exc)
        return None


# ─── 公开接口 ──────────────────────────────────────────────────────────────────

def report_completed(
    filename: str,
    counts: dict,
    step_failures: list[str],
    is_partial: bool,
) -> str | None:
    """
    生成任务完成播报。
    counts 格式：{ok, login_required, blocked_or_blank, captcha_detected, ...}
    返回 None 表示 LLM 不可用，调用方应使用模板文字。
    """
    total = sum(counts.values()) if counts else 0
    if total == 0 and not step_failures:
        return None

    ok = counts.get("ok", 0)
    login = counts.get("login_required", 0)
    blocked = counts.get("blocked_or_blank", 0)
    captcha = counts.get("captcha_detected", 0)
    failures_str = "；".join(step_failures) if step_failures else "无"
    partial_hint = "（部分步骤未完成）" if is_partial else ""

    prompt = (
        f"任务已处理完成{partial_hint}，以下是结果数据，请生成播报：\n"
        f"文件：{filename}\n"
        f"总链接：{total} 条\n"
        f"截图成功：{ok} 条\n"
        f"需要登录（未采集）：{login} 条\n"
        f"被拦截或空白：{blocked} 条\n"
        f"验证码阻断：{captcha} 条\n"
        f"步骤报错：{failures_str}"
    )
    return _call_llm(prompt)


def report_failed(filename: str, error_message: str) -> str | None:
    """
    生成任务失败播报。
    返回 None 表示 LLM 不可用，调用方应使用模板文字。
    """
    prompt = (
        f"任务处理失败，请解释原因并告诉用户下一步该做什么：\n"
        f"文件：{filename}\n"
        f"错误详情：{error_message or '未知错误'}"
    )
    return _call_llm(prompt)


def report_paused(filename: str, error_message: str, task_id: str) -> str | None:
    """
    生成验证码暂停播报。
    必须在播报中包含"继续 {task_id}"指令。
    返回 None 表示 LLM 不可用，调用方应使用模板文字。
    """
    prompt = (
        f"任务因触发验证码而暂停，请告知用户情况及恢复方法：\n"
        f"文件：{filename}\n"
        f"暂停原因：{error_message or '截图过程中触发验证码'}\n"
        f"注意：播报中必须包含完整指令「继续 {task_id}」，让用户知道如何恢复。"
    )
    return _call_llm(prompt)
