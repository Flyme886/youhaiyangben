# 抖音截取 Troubleshooting（现象 → 原因 → 处置）

本轮真实跑通 501 条遇到并定位的问题。每条都来自运行日志（`_ego/*.log`）与脚本内注释，
**未编造**。未列的现象不要臆测原因。

---

## 1. 3 标签页并发 → 连续 `net::ERR_ABORTED` + 验证码成簇

- **现象**：开 3 标签页并发抓，日志里 `net::ERR_ABORTED` 连续出现，且验证码页面成簇冒出。
- **原因**：并发触发抖音风控。
- **处置**：**单线程**，一个 space 内只复用 1 个标签页。见 SKILL.md「抖音抓取铁律」。

## 2. 整个会话 IP 限流 `验证过于频繁[5009]`

- **现象**：并发后连 `www.douyin.com` 官网首页都打不开，报 `验证过于频繁[5009]`。
- **原因**：风控把整个会话的出口 IP 限流，不只是当前任务。
- **处置**：停止抓取，**冷却数分钟**再重跑；限流是短时的。重跑用 `run_capture.sh`，`results*.jsonl` 里已 `ok` 的 item_id 会自动跳过（断点续跑）。

## 3. `/share/video/` 返回「验证码中间页」

- **现象**：用原始 `https://www.douyin.com/share/video/{id}` 打开，落到一个验证码/中转页，正文为空。
- **原因**：抖音对 `/share/video/` 路径返回「验证码中间页」。
- **处置**：抓取时把路径改写为 `https://www.douyin.com/video/{id}`（`capture_douyin.js` 内 `replace("/share/video/","/video/")`）。**Word 的「链接：」行仍输出原始 share 链接**，数据不动。

## 4. `page.goto` 默认等 `load` → 单条 20–300 秒

- **现象**：不指定 `waitUntil` 时，每条都要等到超时（20–300 秒）才返回。
- **原因**：抖音视频/直播流持续加载，页面 `load` 事件永远不触发。
- **处置**：`page.goto(url, { timeout: WD.nav, waitUntil: "domcontentloaded" })`。这是铁律，不要改回 `load`。

## 5. 渲染进程约 15 条后劣化（2 秒/条 → 60–300 秒/条）

- **现象**：同一次会话里前面约 15 条稳定 2 秒/条，之后单条骤涨到 60–300 秒。
- **原因**：Ego Lite 同一标签页渲染进程跑久了劣化。
- **处置**：**分片 + 每片重建标签页**。`run_capture.sh` 每片只抓 `CHUNK`（默认 12、10–12 最佳）条，并以 `CAPTURE_FRESH=true` 让 `capture_douyin.js` 每片开头 `close` 旧页后复用 space 内已有页重建，稳定回约 2 秒/条。

## 6. 页面卡死后污染后续所有条目

- **现象**：某条卡死后，后续条目全部拿到同一张坏截图/同一段错误正文。
- **原因**：卡死状态残留在同一页面对象上，没有被清掉。
- **处置**：每条加**超时看门狗**（Promise 竞赛，见 `capture_douyin.js` 内 `withTimeout`）；超时（`WATCHDOG:*`）后**必须 `resetPage()` 重置到 `about:blank`** 再继续。`capture_douyin.js` 内 `recycle`（默认 99）也会在会话内周期性重置兜底。

## 7. 登录弹窗两个 `.douyin_login_new_class`

- **现象**：未登录时页面里有**两个**同名 `.douyin_login_new_class`，只关一个关不干净，大弹窗还在。
- **原因**：一个是右侧边栏引导卡，一个是真正全屏的登录大弹窗。
- **处置**：`hideLogin()` 取 `getBoundingClientRect()` **面积最大**的那个，再用 `parentElement` 逐级向上找 `position:fixed` 且覆盖 ≥80% 视口的祖先，对其设 `display:none !important`。

## 8. 「验证码登录」字样导致验证码误报

- **现象**：正常页面被判成验证码页，`status=captcha_detected`。
- **原因**：登录弹窗文案里有「验证码登录」按钮，用 `/验证码/` 匹配全文会命中误报。
- **处置**：验证码判定**只看标题/正文特定词**：`验证码中间页`、`请完成下列验证`、`拖动完成上方拼图`、`人机验证`、`安全验证`。不要用 `/验证码/` 匹配全文。

## 9. 页面账本冲突 `target ... is already page pNN`

- **现象**：连续 `page.close()` 再 `task.newPage()` 后报错 `... is already page pNN`。
- **原因**：Ego 页面账本里同一 id 被重复登记。
- **处置**：**复用 space 内已有页面**：`for (const p of await task.pages()) { page = p; break; }`，只在确实没有页时才 `task.newPage()`。不要每片都新建。

## 10. 前台命令被 SIGTERM（约 120 秒上限）

- **现象**：直接在对话里跑长截图命令，约 120 秒后被 `SIGTERM` 杀掉。
- **原因**：前台命令有执行时长上限。
- **处置**：放**后台**跑，并把 stdout/stderr 重定向到日志文件（如 `nohup ... > capture.log 2>&1 &`），用 `tail -f` 观察；或用 `run_capture.sh` 自带的分片循环。

## 11. macOS 无 `timeout` 命令

- **现象**：脚本里写 `timeout 30 ...` 会报 command not found。
- **原因**：macOS 没有 GNU `timeout`。
- **处置**：本包脚本**均不依赖 `timeout`**；超时统一用 Ego 环境内的 Promise 竞赛看门狗实现。写新脚本时也不要用 `timeout`。

## 12. `blocked_or_blank` 与「截图待补」是降级路径，不是失败

- **现象**：某条 `status=blocked_or_blank`（正文 <20 字），Word 里变成「截图待补：<原因>」。
- **原因**：页面被风控/空白，属设计内的降级，不是脚本出错。
- **处置**：**不当失败**：不要因此中断 Word 生成，也不要把它算进"失败条数"。成品以「截图待补」呈现即可；若占比异常高，回头查是否又触发限流（见 2）。

## 13. 作者名自带 `@` → 元信息出现 `@@`

- **现象**：元信息成了 `（编号650 · 原序号1234 · 抖音 · @@某某）`，多一个 `@`。
- **原因**：Excel「作者」列本身就以 `@` 开头（本案 501 条里有 8 条如此，形如 `@某某`），而元信息固定再拼一个 `@` 前缀。
- **处置**：`apply_adaptations.py` 注入的元信息拼装会先 `record.author.lstrip("@").strip()` 再拼 `@`；若去前缀后为空则整段 `· @作者` 省略。**只改展示、不动数据**——`links_manifest.json` / `screenshots_manifest.json` 里仍是原始作者名。

## 14. `ego-browser: command not found`

- **现象**：`command -v ego-browser` 无输出；或脚本报 `ERROR: ego-browser 不存在`。
- **原因**：ego lite 没装；或装了但 onboarding 未完成，`~/.local/bin` 未注册/未进 PATH。
- **处置**（**自己装，不要停下来问用户下载**）：
  1. 先补 PATH 再试：`export PATH="$HOME/.local/bin:$PATH"`。
  2. 仍没有 → 跑官方安装脚本：
     `sh ~/.workbuddy/skills/ego-browser/scripts/install.sh`
     （自动识别 CPU 架构 → 下载 DMG → 装到 `/Applications` → 去 quarantine → 启动 app）。
  3. 手动直链（脚本失败时按架构二选一）：arm64 `https://cdn.ego.app/setup/macos/arm64/egolite.dmg`；
     x64 `https://cdn.ego.app/setup/macos/x64/egolite.dmg`；官网 <https://lite.ego.app/>。
  4. 首次 onboarding 是**GUI 操作，需用户在 app 内完成**，完成后 `ego-browser` 才注册进 PATH。
  5. 验证：`ego-browser nodejs <<'EOF'` + `console.log('ego-browser ready')` + `EOF` → 打印即就绪。
- **补充**：本技能链**所有截图都走 ego lite**；任何浏览器自动调研/抓取任务也应优先它，别换无头方案。

## 15. 未登录抖音 → 整批截图都是登录页或空白

- **现象**：截图大面积是登录引导页，或 `status=blocked_or_blank`（正文 <20 字）成片出现。
- **原因**：ego lite 的持久化 profile 里没有抖音登录态；登录弹窗只能隐藏，隐藏不等于已登录。
- **处置**：**抓取前先确认用户在 ego lite 里已登录抖音**（打开 `www.douyin.com` 扫码一次即可，
  登录态持久化）。这一步需要用户配合，属正常人工介入点，不是任务失败。
- **注意**：不要把它当 `blocked_or_blank` 降级路径消化掉——先确认登录态，再判断是否真是风控降级。
