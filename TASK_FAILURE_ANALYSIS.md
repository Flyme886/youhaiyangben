# 任务执行失败诊断报告

## 问题总结

**关键症状**：没有生成 Word 文件，任务未完成

**根本原因**：大量 URL 无法访问 + 验证码检测导致任务暂停

---

## 详细分析

### 1. URL 访问情况统计

```
总计: 200 条 URL

成功 (ok): 3 条 (1.5%)
├─ https://haokan.baidu.com/v?vid=5143824049252727497
├─ https://mnews.tianyancha.com/ll_t2p8ymt66p.html
└─ (1 条其他)

问题 URL: 197 条 (98.5%)
├─ blocked_or_blank: 128 条 (64%) ← 网站阻止/返回空白
├─ login_required: 57 条 (28.5%) ← 需要登录
├─ captcha_detected: 11 条 (5.5%) ← 检测到验证码 ⚠️
└─ navigation_error: 1 条 (0.5%)
```

**结论**：内容获取成功率极低，无法生成有意义的 Word 文档

---

## 问题根源

### 问题 1️⃣：验证码检测导致任务暂停（最紧迫）

**日志显示**：
```
"worker_issues": [
  "检测到验证码 11 条，任务暂停等待人工处理"
]
```

**代码位置**：`app/task_runner.py` 第 121-124 行
```python
if capture_counts.get("captcha_detected", 0) >= settings.captcha_pause_threshold:
    step_failures.append(f"检测到验证码 {capture_counts.get('captcha_detected', 0)} 条，...")
    finalize_task(task_id, task_dir, step_failures, paused=True)
    return  # ← 任务停止！
```

**当前配置**：`CAPTCHA_PAUSE_THRESHOLD = 1`（检测到 1 条验证码就暂停）

**影响**：任务在"截图采集"步骤后立即暂停，**从未进行 Word 生成步骤**

---

### 问题 2️⃣：大量 URL 无法被爬取

#### 2.1 被网站阻止 (128 条)

**常见来源**：抖音 (toutiao.com)、小红书、微信等

**原因**：
- 反爬虫机制（User-Agent 检查、IP 限流）
- 内容已删除或私密
- 需要 JavaScript 渲染但渲染失败

**典型例子**：
```
blocked_or_blank: https://www.toutiao.com/i1865764361160716/
blocked_or_blank: https://channels.weixin.qq.com/mobile/commonFinderJsApi.html?api=...
```

#### 2.2 需要登录 (57 条)

**常见来源**：微博、百度、知乎等

**需要什么**：有效的登录态 (Cookies/Session)

**解决方案**：需要上传 `storage_state.json`（Playwright 登录态快照）

**典型例子**：
```
login_required: http://weibo.com/2257126397/R0hfOyXDo
login_required: https://mbd.baidu.com/newspage/data/landingsuper?nid=...
```

#### 2.3 验证码检测 (11 条)

**来源**：微信公众号、百度等

**问题**：自动化爬虫触发了人机验证

---

### 问题 3️⃣：未完成最后的 Word 生成步骤

**应该执行的步骤流程**：

```
1. 预检 Excel           ✅ 通过
   ↓
2. 截图采集            ⏸️ 因验证码暂停
   ↓
3. 截图质检            ⏭️ 跳过（因步骤 2 暂停）
   ↓
4. 生成 Word            ⏭️ 跳过（因步骤 2 暂停）
   ↓
5. 完成标记.json        ❌ 未创建
```

**代码流程**（`task_runner.py`）：
```python
# 第 121-124 行
if capture_counts.get("captcha_detected", 0) >= settings.captcha_pause_threshold:
    finalize_task(...)  # 暂停，返回
    return              # ← 后续步骤都不执行！

# 下面的代码永不执行：
run_step(task_id, "生成 Word", ...)  # 第 147 行
```

---

## 解决方案

### 立即行动（优先级 🔴 高）

#### 方案 A：调整验证码阈值（快速修复）

**问题**：当前阈值过低 (`CAPTCHA_PAUSE_THRESHOLD=1`)

**修复方法**：
```bash
# 编辑后端配置 (.env)
CAPTCHA_PAUSE_THRESHOLD=20  # 允许检测 20 条验证码后才暂停
```

**优点**：快速，允许更多 URL 被处理

**缺点**：验证码多时仍可能失败，需要人工处理

#### 方案 B：提供有效的登录态（更有效）

**需要**：登录态快照文件 `storage_state.json`

**步骤**：

1. 在有浏览器的机器上用 Playwright 保存登录态：
```python
# 使用 Playwright 登录相关网站，保存状态
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    context = browser.new_context()
    page = context.new_page()
    
    # 登录微博、百度等
    page.goto("https://weibo.com/login")
    # ... 手动登录 ...
    
    # 保存状态
    context.storage_state(path="storage_state.json")
    context.close()
    browser.close()
```

2. 在管理台上传 `storage_state.json`

**效果**：57 条"需要登录"的 URL 可能变为可访问

#### 方案 C：改进反爬虫对抗（长期方案）

**修改**：`capture_links.py` 或爬虫引擎配置

**方法**：
- 添加更多 User-Agent 变种
- 增加请求延迟（避免 IP 限流）
- 实现代理池
- 使用无头浏览器扩展（如 Stealth.js）

---

## 具体修复步骤

### Step 1: 查看当前配置

```bash
# 检查后端配置
grep -r "CAPTCHA_PAUSE_THRESHOLD" harmful-sample-web/

# 检查是否有 storage_state.json
ls -la harmful-sample-web/data/storage_state.json
```

### Step 2: 提高验证码阈值（快速）

**文件**：`harmful-sample-web/app/config.py`

```python
# 当前：
captcha_pause_threshold: int = int(os.environ.get("CAPTCHA_PAUSE_THRESHOLD", "1"))

# 修改建议：改为 15-20
# 然后在 .env 中设置
CAPTCHA_PAUSE_THRESHOLD=15
```

### Step 3: 获取登录态（推荐）

**如果有微博/百度等账号**：
1. 用浏览器登录
2. 导出 `storage_state.json`
3. 通过管理台上传

**验证上传**：
```bash
# 检查管理台显示的 storage_state 信息
# 应该显示 "exists: true" 和 "updated_at: ..."
```

### Step 4: 重新执行任务

```bash
# 上传同样的 Excel 文件，重新执行任务
# 这次应该能访问更多 URL
```

---

## 预期改进

### 当前状态
```
成功: 3 条 (1.5%)
失败: 197 条 (98.5%)
结果: 0 个 Word 文件
```

### 修复后预期

**仅增加验证码阈值**：
```
成功: 3 + ~8 = 11 条 (5.5%)  [避免提前暂停]
失败: 189 条 (94.5%)
结果: 1 个 Word 文件（内容稀少）
```

**增加验证码阈值 + 登录态**：
```
成功: 11 + ~30 = 41 条 (20%)  [需要登录的 URL 变可访问]
失败: 159 条 (79.5%)
结果: 1 个 Word 文件（内容较丰富）
```

**完整方案**（改进爬虫 + 登录态 + 高阈值）：
```
成功: 60+ 条 (30%+)
失败: 140- 条 (<70%)
结果: 1 个内容充分的 Word 文件
```

---

## 根本问题反思

### 为什么成功率这么低？

1. **URL 来源问题**
   - 大量来自严格的社交媒体平台（抖音、微博、小红书）
   - 这些平台都有强大的反爬虫措施
   - 大部分内容已被删除或设为私密

2. **爬虫能力有限**
   - 没有有效的登录态
   - User-Agent 等可能被识别为爬虫
   - 没有代理池避免 IP 限流

3. **任务配置过于敏感**
   - 验证码阈值太低（1 就暂停）
   - 导致大部分任务失败

### 长期改进建议

| 方案 | 难度 | 效果 | 优先级 |
|------|------|------|--------|
| 提高验证码阈值 | ⭐ 简单 | ⭐⭐ 有限 | 🔴 立即 |
| 上传登录态 | ⭐ 简单 | ⭐⭐⭐ 中等 | 🔴 立即 |
| 改进爬虫逻辑 | ⭐⭐⭐ 困难 | ⭐⭐⭐⭐ 显著 | 🟡 本周 |
| 使用代理池 | ⭐⭐⭐⭐ 很难 | ⭐⭐⭐⭐⭐ 显著 | 🟢 可选 |

---

## 检查清单

- [ ] 检查当前 CAPTCHA_PAUSE_THRESHOLD 值
- [ ] 检查是否有 storage_state.json
- [ ] 修改验证码阈值为 15+
- [ ] 获取微博/百度登录态
- [ ] 上传 storage_state.json
- [ ] 重新执行任务
- [ ] 验证 Word 文件是否生成
- [ ] 检查 Word 文件内容是否充分

---

## 关键代码位置

**验证码阈值检查**：`app/task_runner.py:121-124`
```python
if capture_counts.get("captcha_detected", 0) >= settings.captcha_pause_threshold:
    finalize_task(task_id, task_dir, step_failures, paused=True)
    return
```

**Word 生成逻辑**：`app/task_runner.py:141-147`
```python
final_cmd = [settings.python_bin, str(excel_script), str(input_xlsx), ...]
run_step(task_id, "生成 Word", final_cmd, ...)
```

**配置加载**：`app/config.py:43`
```python
captcha_pause_threshold: int = int(os.environ.get("CAPTCHA_PAUSE_THRESHOLD", "1"))
```

---

## 下一步行动

1. **今天内**：提高验证码阈值到 15
2. **本周内**：获取并上传 storage_state.json
3. **本月内**：考虑改进爬虫逻辑或使用更好的反爬虫对抗技术

---

**结论**：这不是代码 bug，而是任务配置和爬虫能力的问题。✅ 都可以修复！
