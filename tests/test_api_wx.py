#!/usr/bin/env python3
"""
/api/wx/* 接口集成自检。

用法（在仓库根目录，即 app/ 所在的那一层执行）：

    # 1. 确认 app/ 下已存在 api_wx.py / db_wx.py / wechat_auth.py（见
    #    docs/小程序后端接口-server-patch.md），并确保 .env 里配好：
    #    APP_PASSWORD / WX_TOKEN_SECRET / WX_ALLOW_PASSWORD_LOGIN=true
    # 2. 跑测试（会用独立的临时数据库，不碰 data/app.db）
    python tests/test_api_wx.py

设计说明
--------
- 用独立的临时目录做 DATABASE_PATH / TASK_ROOT，**不会污染生产库**。
- 打桩两处：enqueue_task（不启动真实 worker）、code2session（不真的调微信）。
  其余全部走真实实现，包括鉴权、归属过滤、路径归一、文件流。
- 断言全部写在 PRODUCT 部分；如果某条失败，请先确认不是环境问题
  （缺 .env、缺 fastapi/httpx 依赖等）。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = next(
    parent for parent in Path(__file__).resolve().parents if (parent / "app" / "api_wx.py").exists()
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_SANDBOX = Path(tempfile.mkdtemp(prefix="wx-api-test-"))
os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("WX_TOKEN_SECRET", "integration-test-secret")
os.environ["WX_ALLOW_PASSWORD_LOGIN"] = "true"
os.environ["DATABASE_PATH"] = str(_SANDBOX / "app.db")
os.environ["TASK_ROOT"] = str(_SANDBOX / "tasks")
os.environ["WX_INCLUDE_LEGACY_TASKS"] = "true"

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError:
    sys.exit("缺少依赖，请先 pip install fastapi httpx python-multipart")

from app import api_wx  # noqa: E402
from app.api_wx import on_startup, router  # noqa: E402
from app.db import create_task, get_task, init_db, update_task  # noqa: E402
from app import db_wx  # noqa: E402

# ---- 打桩：不启动真实 worker，不真调微信 ----
api_wx.enqueue_task = lambda task_id: None
api_wx.code2session = lambda code: {
    "openid": f"o{code}",
    "session_key": "stub-session-key",
    "unionid": "",
}

app = FastAPI()
init_db()
on_startup()
app.include_router(router)
client = TestClient(app)

_OK: list[str] = []
_FAIL: list[str] = []


def check(name: str, condition: bool, extra: object = "") -> None:
    if condition:
        _OK.append(name)
        print(f"  \033[32m[PASS]\033[0m {name}")
    else:
        _FAIL.append(name)
        print(f"  \033[31m[FAIL]\033[0m {name}  {extra}")


def section(title: str) -> None:
    print(f"\n\033[1m=== {title} ===\033[0m")


def headers_for(code: str) -> dict:
    r = client.post("/api/wx/login", json={"code": code})
    return {"Authorization": "Bearer " + r.json()["token"]}


ADMIN = {
    "Authorization": "Bearer "
    + client.post("/api/wx/login/password", json={"password": os.environ["APP_PASSWORD"]}).json()["token"]
}
A = headers_for("ALICE")
B = headers_for("BOB")
XLSX = ("t.xlsx", b"PK\x03\x04fake", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

try:
    section("1 · 鉴权边界")
    r = client.get("/api/wx/health")
    check("health 免鉴权且返回 ok", r.status_code == 200 and r.json()["status"] == "ok")
    check("health 字段最小化", set(r.json()) == {"status", "server_time", "version"})
    check("无 token → 401", client.get("/api/wx/tasks/mine").status_code == 401)
    check("伪造 token → 401",
          client.get("/api/wx/tasks/mine", headers={"Authorization": "Bearer a.b"}).status_code == 401)
    check("非 Bearer 头 → 401",
          client.get("/api/wx/tasks/mine", headers={"Authorization": "Basic x"}).status_code == 401)

    section("2 · 登录")
    check("密码错误 → 401",
          client.post("/api/wx/login/password", json={"password": "definitely-wrong"}).status_code == 401)
    check("密码正确 → admin 标记",
          client.post("/api/wx/login/password",
                      json={"password": os.environ["APP_PASSWORD"]}).json().get("admin") is True)
    r = client.post("/api/wx/login", json={"code": "ALICE"})
    check("微信登录返回 openid", r.json()["openid"] == "oALICE")
    check("登录响应不下发 session_key", "session_key" not in r.text)
    check("登录响应字段恰好为 token/openid/expires_in",
          set(r.json()) == {"token", "openid", "expires_in"})

    section("3 · 上传校验")
    check("非 xlsx → 400",
          client.post("/api/wx/tasks", files={"file": ("a.pdf", b"x", "application/pdf")},
                      data={}, headers=A).status_code == 400)
    check("~$ 临时锁文件 → 400",
          client.post("/api/wx/tasks", files={"file": ("~$a.xlsx", b"x", "application/octet-stream")},
                      data={}, headers=A).status_code == 400)
    check("无扩展名 → 400",
          client.post("/api/wx/tasks", files={"file": ("noext", b"x", "application/octet-stream")},
                      data={}, headers=A).status_code == 400)
    r = client.post("/api/wx/tasks", files={"file": XLSX},
                    data={"name": "Alice 的批次", "note": "高优先级"}, headers=A)
    A_TASK = r.json()["task_id"]
    check("合法上传返回 task_id", r.status_code == 200 and bool(A_TASK))
    check("任务名被保留", r.json()["name"] == "Alice 的批次")
    check("name 留空时回落文件名",
          client.post("/api/wx/tasks", files={"file": ("监测数据.xlsx", b"PK", "application/octet-stream")},
                      data={}, headers=B).json()["name"] == "监测数据.xlsx")
    B_TASK = client.post("/api/wx/tasks", files={"file": ("b.xlsx", b"PK", "application/octet-stream")},
                         data={}, headers=B).json()["task_id"]

    section("4 · 归属隔离（业务用户之间互不可见）")
    a_ids = [x["id"] for x in client.get("/api/wx/tasks/mine", headers=A).json()["items"]]
    check("A 看不到 B 的任务", B_TASK not in a_ids, a_ids)
    check("B 看不到 A 的任务",
          A_TASK not in [x["id"] for x in client.get("/api/wx/tasks/mine", headers=B).json()["items"]])
    check("越权读详情 → 404", client.get(f"/api/wx/tasks/{B_TASK}", headers=A).status_code == 404)
    check("越权读文件列表 → 404", client.get(f"/api/wx/tasks/{B_TASK}/files", headers=A).status_code == 404)
    check("越权下载 → 404", client.get(f"/api/wx/tasks/{B_TASK}/download/x.docx", headers=A).status_code == 404)
    check("越权取消 → 404", client.post(f"/api/wx/tasks/{B_TASK}/cancel", headers=A).status_code == 404)
    check("越权重试 → 404", client.post(f"/api/wx/tasks/{B_TASK}/resume", headers=A).status_code == 404)
    check("不存在的任务 → 404", client.get("/api/wx/tasks/nope123", headers=A).status_code == 404)
    check("管理员可查看任意任务", client.get(f"/api/wx/tasks/{B_TASK}", headers=ADMIN).status_code == 200)
    check("业务用户访问 /tasks → 403", client.get("/api/wx/tasks", headers=A).status_code == 403)
    check("管理员访问 /tasks → 200", client.get("/api/wx/tasks", headers=ADMIN).status_code == 200)

    section("5 · 信息泄露")
    body = client.get(f"/api/wx/tasks/{A_TASK}", headers=A).text
    check("响应无 task_dir", "task_dir" not in body)
    check("响应无服务器绝对路径", str(_SANDBOX) not in body and "/data/tasks" not in body)
    check("响应无 summary_json 原串", "summary_json" not in body)
    check("列表响应同样干净", "task_dir" not in client.get("/api/wx/tasks/mine", headers=A).text)

    section("6 · 筛选 / 分页 / 统计")
    for tid, owner, status in [
        ("seed1", "oALICE", "completed"),
        ("seed2", "oALICE", "running"),
        ("seed3", "oALICE", "failed"),
        ("legacy", "", "queued"),
    ]:
        create_task(tid, f"历史{tid}", "", Path(os.environ["TASK_ROOT"]) / tid, "a.xlsx")
        db_wx.set_task_owner(tid, owner, "wechat")
        update_task(tid, status=status)
    # A 名下共 5 条：A_TASK(queued) seed1(completed) seed2(running) seed3(failed) legacy(queued)

    got = sorted(x["id"] for x in client.get("/api/wx/tasks/mine?status=queued,running", headers=A).json()["items"])
    check("多状态筛选 queued,running → 3 条", got == sorted([A_TASK, "seed2", "legacy"]), got)
    check("单状态 running → 1 条",
          [x["id"] for x in client.get("/api/wx/tasks/mine?status=running", headers=A).json()["items"]] == ["seed2"])
    check("已完成族 → 1 条",
          [x["id"] for x in client.get("/api/wx/tasks/mine?status=completed,partial_completed",
                                       headers=A).json()["items"]] == ["seed1"])
    check("非法状态值被丢弃且不报错",
          client.get("/api/wx/tasks/mine?status=' OR 1=1--", headers=A).json()["total"] == 5)
    check("合法与非法混合只取合法",
          client.get("/api/wx/tasks/mine?status=running,hack", headers=A).json()["total"] == 1)

    p1 = client.get("/api/wx/tasks/mine?page=1&size=2", headers=A).json()
    p3 = client.get("/api/wx/tasks/mine?page=3&size=2", headers=A).json()
    check("分页 size=2 返回 2 条", len(p1["items"]) == 2)
    check("首页 has_more=true", p1["has_more"] is True)
    check("page/size/total 正确回显", (p1["page"], p1["size"], p1["total"]) == (1, 2, 5))
    check("末页 has_more=false", len(p3["items"]) == 1 and p3["has_more"] is False, p3)
    check("翻页无重叠", set(x["id"] for x in p1["items"]).isdisjoint({x["id"] for x in p3["items"]}))
    check("size 超上限被拒 422", client.get("/api/wx/tasks/mine?size=999", headers=A).status_code == 422)
    check("page=0 被拒 422", client.get("/api/wx/tasks/mine?page=0", headers=A).status_code == 422)

    s = client.get("/api/wx/stats", headers=A).json()
    check("A 统计 total=5", s["total"] == 5, s)
    check("queued=2 / running=1 / completed=1 / failed=1",
          (s["queued"], s["running"], s["completed"], s["failed"]) == (2, 1, 1, 1), s)
    check("聚合字段 active=3 / done=1 / issue=1",
          (s["active"], s["done"], s["issue"]) == (3, 1, 1), s)
    check("统计自洽（各分项之和 = total）",
          s["active"] + s["done"] + s["issue"] + s["cancelled"] == s["total"], s)
    check("管理员统计为全局（A 的 5 条 + B 的 2 条 = 7）",
          client.get("/api/wx/stats", headers=ADMIN).json()["total"] == 7)

    section("7 · 详情结构")
    d = client.get(f"/api/wx/tasks/{A_TASK}", headers=A).json()
    check("必需字段齐备",
          {"id", "name", "note", "status", "current_step", "created_at",
           "files", "summary", "log_tail"} <= set(d))
    check("note 被保留", d["note"] == "高优先级")
    check("新建任务状态为 queued", d["status"] == "queued")
    check("初始 files 为空", d["files"] == [])
    check("未运行时 log_tail 为空串", d["log_tail"] == "")
    check("空时间序列化为 null", d["started_at"] is None and d["finished_at"] is None)

    section("8 · 重试与取消")
    check("queued 任务 resume → resumed=false",
          client.post(f"/api/wx/tasks/{A_TASK}/resume", headers=A).json()
          == {"ok": True, "resumed": False, "status": "queued"})
    check("failed 任务 resume → resumed=true",
          client.post("/api/wx/tasks/seed3/resume", headers=A).json() == {"ok": True, "resumed": True})
    check("重试后落库为 queued", get_task("seed3")["status"] == "queued")
    r = client.post(f"/api/wx/tasks/{A_TASK}/cancel", headers=A)
    check("取消 → status=cancelled", r.json()["status"] == "cancelled", r.text)
    check("取消已持久化", get_task(A_TASK)["status"] == "cancelled")
    check("current_step 记为已取消", get_task(A_TASK)["current_step"] == "已取消")
    check("error_message 留痕", get_task(A_TASK)["error_message"] == "用户取消任务")
    check("重复取消 → ok=false 且状态不变",
          client.post(f"/api/wx/tasks/{A_TASK}/cancel", headers=A).json()
          == {"ok": False, "status": "cancelled"})
    check("cancelled 不可 resume",
          client.post(f"/api/wx/tasks/{A_TASK}/resume", headers=A).json()["resumed"] is False)

    section("9 · 文件下载与路径穿越")
    out = Path(os.environ["TASK_ROOT"]) / A_TASK / "output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "第01批.docx").write_bytes(b"DOCX-CONTENT")
    (out / "Word结果.zip").write_bytes(b"ZIP-CONTENT")
    files = client.get(f"/api/wx/tasks/{A_TASK}/files", headers=A).json()
    check("文件列表 2 个", len(files) == 2, files)
    check("中文文件名保留", "第01批.docx" in [f["name"] for f in files])
    check("返回文件体积",
          [f["size"] for f in files if f["name"] == "第01批.docx"][0] == len(b"DOCX-CONTENT"))
    check("下载内容一致",
          client.get(f"/api/wx/tasks/{A_TASK}/download/第01批.docx", headers=A).content == b"DOCX-CONTENT")
    check("以附件形式返回",
          "attachment" in client.get(f"/api/wx/tasks/{A_TASK}/download/Word结果.zip",
                                     headers=A).headers.get("content-disposition", ""))
    check("路径穿越 ../ → 404",
          client.get(f"/api/wx/tasks/{A_TASK}/download/..%2F..%2F..%2Fetc%2Fpasswd",
                     headers=A).status_code == 404)
    check("绝对路径注入 → 404",
          client.get(f"/api/wx/tasks/{A_TASK}/download/%2Fetc%2Fpasswd", headers=A).status_code == 404)
    check("不存在的文件 → 404",
          client.get(f"/api/wx/tasks/{A_TASK}/download/zzz.docx", headers=A).status_code == 404)

    section("10 · 服务器路径脱敏")
    # worker 报错时会把绝对路径原样写进 error_message / run.log，
    # 这些内容会经 /api/wx 透传到小程序界面，必须脱敏后再给前端。
    leaky = (
        "can't open file '/opt/harmful-sample-web/data/tasks/abc123/"
        "skills/harmful_sample/scripts/excel_to_word.py': [Errno 2] No such file"
    )
    update_task(A_TASK, error_message=leaky, current_step="失败")
    log = Path(os.environ["TASK_ROOT"]) / A_TASK / "run.log"
    log.write_text(
        "[2026-01-01 00:00:00] STEP 预检 Excel\n"
        "/opt/harmful-sample-web/.venv/bin/python "
        "/opt/harmful-sample-web/skills/harmful_sample/scripts/excel_to_word.py\n",
        encoding="utf-8",
    )

    d = client.get(f"/api/wx/tasks/{A_TASK}", headers=A).json()
    check("error_message 里的服务器路径已脱敏",
          "/opt/harmful-sample-web" not in d["error_message"], d["error_message"])
    check("error_message 保留了可读的报错原文",
          "No such file" in d["error_message"], d["error_message"])
    check("log_tail 里的服务器路径已脱敏",
          "/opt/harmful-sample-web" not in d["log_tail"], d["log_tail"])
    check("log_tail 仍保留步骤信息",
          "STEP 预检 Excel" in d["log_tail"], d["log_tail"])
    check("列表接口同样脱敏",
          all("/opt/harmful-sample-web" not in (t.get("error_message") or "")
              for t in client.get("/api/wx/tasks", headers=ADMIN).json()["items"]))

    # 反向断言：相对路径、普通中文文案、URL 都不该被误伤
    check("相对路径不被误伤", api_wx._scrub_paths("data/tasks/abc/output") == "data/tasks/abc/output")
    check("中文文案不被误伤", api_wx._scrub_paths("没有产出任何 Word，需要排查后重试")
          == "没有产出任何 Word，需要排查后重试")
    check("URL 不被误伤",
          api_wx._scrub_paths("https://example.com/a/b/c") == "https://example.com/a/b/c")

    # summary 来自 worker 写的原始 summary_json，里面也嵌命令行路径，必须一起脱敏
    nested = api_wx._scrub_deep(
        {"summary": {"cmd": "/opt/app/skills/x/y.py", "n": 3},
         "log_tail": ["/opt/app/data/tasks/t1/run.log"]}
    )
    check("嵌套 dict 被脱敏", nested["summary"]["cmd"] == "<服务器路径>", nested)
    check("嵌套 list 被脱敏", nested["log_tail"][0] == "<服务器路径>", nested)
    check("非字符串值原样保留", nested["summary"]["n"] == 3, nested)

finally:
    shutil.rmtree(_SANDBOX, ignore_errors=True)

print("\n" + "=" * 52)
print(f"结果：{len(_OK)} 通过 / {len(_FAIL)} 失败")
if _FAIL:
    print("失败项：")
    for name in _FAIL:
        print(f"  - {name}")
print("=" * 52)
sys.exit(1 if _FAIL else 0)
