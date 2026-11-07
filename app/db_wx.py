"""
小程序侧的数据库访问层。

和 app/db.py 的分工：
    db.py    —— 原有链路，保持不动
    db_wx.py —— 只补小程序需要的东西：归属字段迁移、分页查询、统计

设计要点
--------
1. 迁移是**幂等**的：`PRAGMA table_info` 探测后再 ALTER，重复执行无副作用。
2. 老任务（网页 / 飞书创建的）`owner_openid` 为空。默认把它们也算进
   「我的任务」，让从飞书迁过来的同事不会看到空列表；
   上线稳定后可把 `WX_INCLUDE_LEGACY_TASKS` 关掉。
3. 查询全部走参数绑定，status 多选用占位符展开，不做字符串拼接。
"""

from __future__ import annotations

import os
from typing import Any

from .db import connect

# 小程序端会出现的全部状态
ALL_STATUSES = (
    "queued",
    "running",
    "completed",
    "partial_completed",
    "failed",
    "cancelled",
    "paused_captcha",
)

_INCLUDE_LEGACY = os.environ.get("WX_INCLUDE_LEGACY_TASKS", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def ensure_schema() -> None:
    """给现有 tasks 表补小程序所需字段。幂等，可在每次启动时调用。"""
    with connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}

        if "owner_openid" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN owner_openid TEXT NOT NULL DEFAULT ''")
        if "owner_source" not in columns:
            # wechat / password / web / bot —— 便于排查任务是从哪个入口进来的
            conn.execute("ALTER TABLE tasks ADD COLUMN owner_source TEXT NOT NULL DEFAULT ''")

        # 小程序列表按 (归属, 时间倒序) 查，缺索引时任务量上千会明显变慢
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_owner_created "
            "ON tasks(owner_openid, created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_status_created "
            "ON tasks(status, created_at DESC)"
        )


def set_task_owner(task_id: str, owner_openid: str, owner_source: str) -> None:
    """给刚创建的任务打上归属。失败不抛错——归属丢失不应导致任务创建失败。"""
    try:
        with connect() as conn:
            conn.execute(
                "UPDATE tasks SET owner_openid = ?, owner_source = ? WHERE id = ?",
                (owner_openid or "", owner_source or "", task_id),
            )
    except Exception:
        pass


def _where_clause(
    statuses: list[str] | None,
    keyword: str,
    owner_openid: str | None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if statuses:
        placeholders = ", ".join("?" for _ in statuses)
        clauses.append(f"status IN ({placeholders})")
        params.extend(statuses)

    if keyword:
        clauses.append("(name LIKE ? OR id LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like])

    if owner_openid is not None:
        if _INCLUDE_LEGACY and owner_openid:
            # 自己创建的 + 迁移前的无归属任务
            clauses.append("(owner_openid = ? OR owner_openid = '')")
            params.append(owner_openid)
        else:
            clauses.append("owner_openid = ?")
            params.append(owner_openid)

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def query_tasks(
    *,
    page: int = 1,
    size: int = 20,
    statuses: list[str] | None = None,
    keyword: str = "",
    owner_openid: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """返回 (当前页记录, 总条数)。owner_openid=None 表示不限归属（管理员视角）。"""
    where, params = _where_clause(statuses, keyword, owner_openid)
    offset = max(0, (page - 1) * size)

    with connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM tasks{where}", params).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM tasks{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [*params, size, offset],
        ).fetchall()

    return rows, int(total)


def task_stats(owner_openid: str | None = None) -> dict[str, int]:
    """按状态统计。owner_openid=None 表示全局统计。"""
    where, params = _where_clause(None, "", owner_openid)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT status, COUNT(*) AS c FROM tasks{where} GROUP BY status", params
        ).fetchall()

    counts = {status: 0 for status in ALL_STATUSES}
    total = 0
    for row in rows:
        status = str(row["status"])
        count = int(row["c"])
        counts[status] = counts.get(status, 0) + count
        total += count

    return {
        **counts,
        "total": total,
        "active": counts.get("queued", 0) + counts.get("running", 0),
        "done": counts.get("completed", 0) + counts.get("partial_completed", 0),
        "issue": counts.get("failed", 0) + counts.get("paused_captcha", 0),
    }


def get_task_owner(task_id: str) -> dict[str, Any] | None:
    """只取归属相关字段，用于鉴权前的轻量查询。"""
    with connect() as conn:
        return conn.execute(
            "SELECT id, owner_openid FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
