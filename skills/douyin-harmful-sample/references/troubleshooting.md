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

## 12. `blocked_or_blank` / 验证码 / 缺图都是降级路径，不是失败（交付优先）

- **现象**：某条 `status=blocked_or_blank`（正文 <20 字）、`captcha`、或 `timeout`，没截到图。
- **原因**：页面被风控/空白/超时，属设计内的降级，不是脚本出错。
- **处置（交付优先原则）**：
  1. **不当失败**：不要因此中断 Word 生成，也不要把它算进「失败条数」，更**不要停下来等人**。
  2. Word 里该位置**留空白**（适配 #8 已删掉「截图待补：<原因>」这类占位文案），
     文字部分（元信息 / 原文 / 链接 / `【违规要素】`）照常完整输出。
  3. 任务状态按「已交付」收尾（`completed` / `partial_completed`），
     末尾**一句话**说明命中条数即可（文案要求见 SKILL.md「交付优先原则」）。
  4. `verify_docx.py` 对缺图只打印 `no_image(x/y)` 提示、**不影响退出码**；
     文字层面的硬伤（`【违规要素】`框数 != 条目数、编号断号、防御性措辞）才判失败（退出码 2）。
  5. 采集状态本身**完整保留**在 `处理日志.csv` / `截图状态汇总.csv` / `完成标记.json` 里备查 ——
     留白只是不上正文，不是丢信息。
  6. 若占比异常高，回头查是否又触发限流（见 2）/ 登录态是否失效（见 15）。

## 12b. 状态语义：什么时候才允许出现「待重跑」

- **现象**：任务收尾状态拿不准该给 `completed` / `partial_completed` / `paused_captcha`。
- **原因**：历史上命中验证码就无条件暂停等人工，导致「有文字可交却一个 Word 都没出」。
- **处置**：**以「有没有 docx」为唯一判据**（`task_runner.finalize_task()`）：

  | 有没有 Word | 命中验证码 | 收尾状态 | `current_step` |
  |---|---|---|---|
  | 有，且无未附图条目 | — | `completed` | `完成` |
  | 有，且有未附图条目 | 可能有 | `partial_completed` | `完成` |
  | **没有** | 是 | `paused_captcha`（兜底态） | `等待人工处理` |
  | **没有** | 否 | `failed` | `失败` |

  也就是说 `paused_captcha` **只保留给「命中验证码且一个 Word 都没产出」**这种真的无物可交的情形。
  注意后端把多段说明用 `"; "` 拼接、小程序按 `;` 切段翻译 ——
  新增说明文案里**不能出现分号**，否则会被切碎。

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

## 16. 每条判定理由结尾都带「视频画面及完整语音需人工核验。」

- **现象**：每条 `【违规要素】` 的理由结尾都多一句「视频画面及完整语音需人工核验。」，501/501 全中。
- **原因**：仓库 `excel_to_word.py` **硬编码了两处**——`_keyword_classify` 对视频平台链接
  `notes.append("视频画面及完整语音需人工核验。")`；`classify_harm` 又对视频平台链接
  `note = (note + " 视频画面及完整语音需人工核验。").strip()`。本案链接全是抖音，于是条条命中。
- **口径**：判定理由应当是**断言**，不是免责对冲。这是典型的防御性表述，成品不要。
- **处置**：`apply_adaptations.py` 的适配 5 删除上述两处注入点，并注入 `DEFENSIVE_PHRASES` +
  `strip_defensive()` 兜底清洗（含大模型路径，防止以后启用 `DEEPSEEK_API_KEY` 时又冒出同类措辞）。
  **本仓库的 `skills/harmful_sample/scripts/excel_to_word.py` 已预置该修复**，clone 下来即已生效；
  只有在该文件被回退成上游原始版时才需要重跑 `apply_adaptations.py`。
- **校验**：`verify_docx.py` 会统计 `defensive=N`，**必须为 0**，否则退出码 2。
  注意规则未命中时的回落文案「依据文案及页面文字判定为有害样本。」是断言、不是对冲，不算违规。

---

## 17. 环境变量进了 ego 的 node 全是 `undefined`

- **现象**：`CAPTURE_CONFIG=/abs/cfg.json ego-browser nodejs < capture_douyin.js` 报
  `config_load_warn ENOENT: open '/capture_config.json'`，随后
  `Error: manifest / shots_dir / results_dir 必须配置`。`process.cwd()` 恒为 `/`。
- **原因**：`ego-browser nodejs` 的脚本**跑在 app 侧的嵌入式 node** 里，
  **调用方的环境变量不会透传**（实测 `FOO=bar ego-browser nodejs` 里 `process.env.FOO === undefined`）。
  所以不存在「环境变量单键覆盖」这条路，`SCRIPT_DIR` 也失效（`import.meta.url` 为 `/`）。
- **处置**：由 `run_capture.sh` 在脚本前面拼一段 **prelude**：
  ```
  globalThis.__CAPTURE_ENV = {CONFIG:"…", LIMIT:"12", TAG:"c0", FRESH:"true", SPACE:"3"};
  ```
  `capture_douyin.js` 用 `envOf(key)` 取值：**prelude 优先，其次 `process.env.CAPTURE_*`**。
  要加配置项 = 改 prelude + 在 `envOf()` 的读取处加一行。每次调用都是全新全局上下文，不会残留。

## 18. 空间被用户接管 → 不能自行抢回

- **现象**：
  `Error: task.listTabs: The user has taken control of this task space and ended the task…`
  或 `task space not found: 8`。
- **原因**：① 该空间 `ownership === "user"`；② 传的数字 id 不存在（新建空间要传**名字**）。
- **处置**：先 `listTaskSpaces()` 看归属。**属于用户的必须先问用户**，拿到明确授权后再
  `claimTaskSpace(<id>)`；不要自己抢、也不要靠新建空间绕过（ego 明确禁止「用新空间绕过卡住的空间」）。
  接管后 `SPACE_ID=<id>` 交给 `run_capture.sh` 即可。

## 19. 分片脚本第二片就误停 / pending 恒等于 CHUNK

- **现象**：`--- 片 c0: pending=12 ---` 后立刻 `ERROR: 连续两片 pending 无变化(12)`。
- **原因**：`pending` 在 `capture_douyin.js` 里被 `limit` 截断（`pending = pending.slice(0, limit)`），
  每片都恒等于 `CHUNK`，拿它当「无进展」判据必然第二片就命中。
- **处置**：改看 **`remaining`**（未完成且未达重试上限的真实剩余条数）。`capture_douyin.js` 现在打印
  `already_ok N remaining M pending K`，`run_capture.sh` 只解析 `remaining`。

## 20. 永久 `blocked_or_blank` 的条目导致死循环

- **现象**：抓一轮跑不完，每片都在重抓同样几条。
- **原因**：断点续跑只跳过 `status==="ok"`，长期失败的条目每片都被重新选中 → `remaining` 永不归零。
- **处置**：`capture_douyin.js` 额外统计每个 item 的**尝试次数**，达到 `max_attempts`（默认 2）
  即视为已处置（降级路径，不是失败）。想再多试几次就调大 `max_attempts`。
- **与交付优先的关系**：封顶后这些条目就按**缺图留白**进入 Word，不再阻塞生成。
  「封顶」不是「放弃」，而是把不确定性从「整批卡住」收敛成「少数几条没有图」——
  后者照样是可交付的成品（配套见 12）。

## 21. 整页停在「加载中」→ 截图只有一个转圈图标

- **现象**：`status=blocked_or_blank`，`page_text` 长度 0 或 56，截图是纯深色 + 「加载中」。
  `page.goto` + `settle` 已经返回，但抖音 SPA 一直没渲染详情内容。
- **原因**：客户端渲染没走完 / 首屏 chunk 拉取失败，页面卡在骨架态。只等固定 `settle` 不够。
- **处置**：`capture_douyin.js` 加了**正文轮询**：每轮最多等 5s（后续轮 4s），
  用 `stillLoading()` 判定（空/过短/尾部含「加载中|加载失败|网络异常|请稍后重试」），
  仍卡住就 **`reload()` 一次**（必须继续用 `domcontentloaded`），共 3 轮。
  修复前首片 12 条有 2–4 条空白，修复后 12/12 `ok`。

## 22. 重建标签页时选中已关闭的僵尸页 → 每条都 `page pNN was closed`

- **现象**：`status=navigation_error`，`reason="page p6 was closed"`，连续多条如此。
- **原因**：`fresh` 模式下先 `close()` 掉所有页，紧接着 `task.pages()` 仍会短暂返回刚关掉的对象，
  取第一个就拿到死页。
- **处置**：关闭后 `await new Promise(r => setTimeout(r, 600))`，再遍历 `task.pages()`，
  对每个候选 `await p.url()` 探活（失败即跳过），全不可用才 `task.newPage()`。

## 23. `ok` 里混着「你要观看的视频不存在」≠ 抓取失败

- **现象**：截图显示「你要观看的视频不存在」，仍被判 `ok`。
- **原因**：原视频已被作者删除/下架，抖音返回该提示页；页面文字 ≥20 字，故不算空白。
- **处置**：**这是真实状态的证据，保留即可**，不要当失败重抓。
  统计口径：这批 351 条里约一成属于此类。

