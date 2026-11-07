"""
任务结论生成模块

把一次采集任务的原始计数，转成一段可读的中文结案结论，供小程序任务详情页展示。
设计原则：**永远返回一段文字**。大模型不可用、超时、报错，都降级为模板结论，
不允许因为结论生成失败而影响任务本身。

原始用途是给飞书机器人播报（已废弃），report_completed / report_failed / report_paused
保留以兼容旧调用方。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("llm_reporter")

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
LLM_ENABLED = os.environ.get("LLM_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
# 结论生成不能拖住 worker：超时就降级模板
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "25"))

_STATUS_LABELS = {
    "ok": "截图成功",
    "login_required": "需要登录",
    "blocked_or_blank": "页面被拦截或空白",
    "captcha_detected": "触发验证码",
    "navigation_error": "链接打不开",
    "timeout": "加载超时",
    "not_captured": "未抓取",
    "unusable": "截图不可用",
}

_SYSTEM_PROMPT = """\
你是内容审核证据采集任务的结案助理。根据给定的采集数据，写一段简洁、客观的中文结案结论。

严格规则：
1. 只依据给定数字，绝不编造任何数据
2. 共三段，每段独立一行，段与段之间空一行：第一段说总体情况，第二段说主要问题，第三段说下一步建议
3. 每段一到两句，全篇不超过 120 字，宁可少说也不要凑字数
4. 数量为 0 的指标一律不提；不要写「部分完成」「未部分完成」这类判断词
5. 同一个信息只说一次；不要解释显而易见的原因（例如「没截到图所以文档留白」）
6. 不使用 markdown 标记、emoji、序号符号、书名号以外的装饰
7. 若产出 Word 为 0，第一段直接说明失败原因，不要铺垫
"""


def _call_llm(user_msg: str) -> str | None:
    """调用 DeepSeek，失败返回 None（调用方降级为模板）。失败原因必须留痕，避免静默降级。"""
    if not LLM_ENABLED:
        logger.info("LLM_ENABLED=false，结论走模板")
        return None
    if not DEEPSEEK_API_KEY:
        logger.warning("缺少 DEEPSEEK_API_KEY，结论走模板")
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
            max_tokens=400,
            temperature=0.3,
            stream=False,
            timeout=LLM_TIMEOUT_SECONDS,
        )
        choice = resp.choices[0]
        text = (choice.message.content or "").strip()
        if not text:
            logger.warning(
                "LLM 返回空内容，结论走模板（finish_reason=%s）",
                getattr(choice, "finish_reason", "unknown"),
            )
            return None
        return text
    except Exception as exc:
        logger.warning("LLM 结论生成失败，结论走模板: %s: %s", type(exc).__name__, exc)
        return None


def _top_problems(capture_counts: dict, limit: int = 3) -> list[str]:
    """把失败状态按数量排序，转成人话，例如「页面被拦截或空白 12 条」。"""
    items = [
        (status, count)
        for status, count in (capture_counts or {}).items()
        if status != "ok" and isinstance(count, int) and count > 0
    ]
    items.sort(key=lambda kv: kv[1], reverse=True)
    return [
        f"{_STATUS_LABELS.get(status, status)} {count} 条"
        for status, count in items[:limit]
    ]


def _template_conclusion(
    total: int,
    ok: int,
    docx_count: int,
    issue_count: int,
    step_failures: list[str],
    capture_counts: dict,
) -> str:
    """大模型不可用时的兜底结论。保证信息完整，读起来像人写的。"""
    lines: list[str] = []

    if docx_count > 0:
        head = f"本次共处理 {total} 条链接，截图成功 {ok} 条，生成 {docx_count} 份 Word。"
        if issue_count > 0:
            head += f"其中 {issue_count} 条在文档中未附图，已留白占位。"
        lines.append(head)
    else:
        lines.append(
            f"本次共处理 {total} 条链接，但未能生成 Word，任务未完成。"
        )

    problems = _top_problems(capture_counts)
    if problems:
        lines.append("主要问题：" + "、".join(problems) + "。")
    if step_failures:
        detail = "；".join(str(x) for x in step_failures[:2])
        lines.append(f"流程告警：{detail}。")

    if docx_count > 0 and (issue_count > 0 or problems):
        lines.append("建议补齐对应平台的登录态后重跑，重跑只会补抓缺失的条目，已完成的不会重复采集。")
    elif docx_count > 0:
        lines.append("本次无缺图，可直接使用。")
    else:
        lines.append("建议检查链接是否有效、登录态是否过期，修正后重新上传。")

    return "\n".join(lines)


def build_conclusion(
    filename: str,
    capture_counts: dict,
    step_failures: list[str],
    docx_count: int = 0,
    issue_count: int = 0,
    is_partial: bool = False,
) -> str:
    """
    生成任务结案结论。**永远返回非空字符串**。

    capture_counts 形如 {"ok": 58, "login_required": 3, "blocked_or_blank": 1}
    """
    counts = {k: v for k, v in (capture_counts or {}).items() if isinstance(v, int)}
    total = sum(counts.values())
    ok = counts.get("ok", 0)

    fallback = _template_conclusion(
        total, ok, docx_count, issue_count, step_failures or [], counts
    )

    if total == 0 and not step_failures:
        return fallback

    # 只把非零指标喂给模型，避免它复述「未附图 0 条」这类废话
    prompt_lines = [f"文件：{filename or '未命名'}", f"总链接数：{total} 条"]
    if ok:
        prompt_lines.append(f"截图成功：{ok} 条")
    if issue_count:
        prompt_lines.append(f"未附图（文档留白）：{issue_count} 条")
    if docx_count:
        prompt_lines.append(f"产出 Word：{docx_count} 份")
    problems = _top_problems(counts)
    if problems:
        prompt_lines.append("失败分布：" + "、".join(problems))
    if step_failures:
        prompt_lines.append("流程告警：" + "；".join(step_failures))
    prompt = "\n".join(prompt_lines)

    text = _call_llm(prompt)
    if not text:
        return fallback

    # 大模型偶尔会带 markdown 记号，清一遍保证前端纯文本可读
    cleaned = text.replace("**", "").replace("##", "").strip()
    return cleaned or fallback


# ─── 兼容旧调用方（飞书机器人，已废弃） ──────────────────────────────────────


def report_completed(
    filename: str,
    counts: dict,
    step_failures: list[str],
    is_partial: bool,
) -> str | None:
    prompt = (
        f"任务已处理完成{'（部分步骤未完成）' if is_partial else ''}：\n"
        f"文件：{filename}\n"
        f"总链接：{sum(counts.values()) if counts else 0} 条\n"
        f"失败分布：{'、'.join(_top_problems(counts) or ['无'])}\n"
        f"步骤报错：{'；'.join(step_failures) if step_failures else '无'}"
    )
    return _call_llm(prompt)


def report_failed(filename: str, error_message: str) -> str | None:
    prompt = (
        f"任务处理失败，请解释原因并告诉用户下一步该做什么：\n"
        f"文件：{filename}\n"
        f"错误详情：{error_message or '未知错误'}"
    )
    return _call_llm(prompt)


def report_paused(filename: str, error_message: str, task_id: str) -> str | None:
    prompt = (
        f"任务因触发验证码而暂停，请告知用户情况及恢复方法：\n"
        f"文件：{filename}\n"
        f"暂停原因：{error_message or '截图过程中触发验证码'}"
    )
    return _call_llm(prompt)
