# 快速修复指南 - 5 分钟解决没有 Word 文件的问题

## 🔴 问题

任务执行完成，但：
- ❌ 没有生成 Word 文件
- ⚠️ 任务显示"暂停"状态
- 📊 只有 3 条 URL 成功，197 条失败

## ⚡ 根本原因

**验证码太敏感了！** 当前配置在检测到 1 条验证码就暂停任务。

```python
# 当前配置（太严格）
CAPTCHA_PAUSE_THRESHOLD=1  # 检测到 1 条验证码就暂停 ← 问题！
```

结果：
1. 截图采集到验证码
2. 任务立即暂停
3. Word 生成步骤永不执行
4. 没有输出文件

## ✅ 一行代码修复

### 方法 1：修改 .env 文件

**找到文件**：`harmful-sample-web/.env`

**添加这一行**：
```
CAPTCHA_PAUSE_THRESHOLD=20
```

**完整例子**：
```env
APP_PASSWORD=change-this-password
HOST=0.0.0.0
PORT=8787
TASK_ROOT=/opt/harmful-sample-web/data/tasks
CAPTCHA_PAUSE_THRESHOLD=20          # ← 新增这行！
```

**重启应用**：
```bash
# 如果用 systemd
sudo systemctl restart harmful-sample-web

# 或重新启动应用进程
```

### 方法 2：修改源代码（如果无法重启）

**文件**：`harmful-sample-web/app/config.py`

**找到第 43 行**：
```python
captcha_pause_threshold: int = int(os.environ.get("CAPTCHA_PAUSE_THRESHOLD", "1"))
```

**改为**：
```python
captcha_pause_threshold: int = int(os.environ.get("CAPTCHA_PAUSE_THRESHOLD", "20"))
```

---

## 🧪 验证修复

修改后，重新执行任务：

1. **上传同样的 Excel 文件**
2. **等待任务完成**
3. **检查输出**：
   - ✅ 应该看到 Word 文件（`*.docx`）
   - ✅ 任务状态变为"完成"而不是"暂停"

---

## 📈 效果预期

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| 验证码阈值 | 1 | 20 |
| 任务状态 | ⏸️ 暂停 | ✅ 完成 |
| Word 文件 | 0 | 1+ |
| 成功 URL | 3 | 8-15 |

---

## 🔧 进阶修复（可选）

### 如果还是想要更高的成功率

上传登录态（`storage_state.json`）：

1. **访问管理页面**：`http://your-server:8787/admin`
2. **点击"上传 storage_state.json"**
3. **选择你从浏览器导出的登录态文件**
4. **重新执行任务**

**期望结果**：
- 57 条需要登录的 URL 可能变为可访问
- Word 文件内容更丰富

---

## 📝 配置说明

### CAPTCHA_PAUSE_THRESHOLD 含义

```
值为 1    → 检测到 1 条验证码就暂停（太严格！）
值为 5    → 允许 5 条验证码，第 6 条时暂停
值为 10   → 允许 10 条验证码，第 11 条时暂停
值为 20   → 允许 20 条验证码，第 21 条时暂停
值为 999  → 几乎不会暂停（除非超时）
```

**推荐值**：`15-20`（平衡安全性和完成率）

---

## ⚠️ 注意

- ✅ 修改这个参数是**安全的**，不会影响其他功能
- ✅ 任务在验证码太多时仍会暂停，但不会过早放弃
- ℹ️ 验证码多时，某些 URL 可能仍然无法访问（正常现象）

---

## 🆘 还是没有 Word 文件？

如果修改后还是没有 Word 文件：

1. **检查日志**：
   ```bash
   tail -f harmful-sample-web/data/tasks/[task_id]/run.log
   ```

2. **查看输出目录**：
   ```bash
   ls -la harmful-sample-web/data/tasks/[task_id]/output/
   ```

3. **常见问题**：
   - 文件权限问题 → 检查 output 目录权限
   - Python 脚本错误 → 查看 run.log 的完整错误信息
   - Excel 格式问题 → 确保 Excel 列名正确

---

## 总结

只需修改一个参数，就能解决问题！

```diff
- CAPTCHA_PAUSE_THRESHOLD=1
+ CAPTCHA_PAUSE_THRESHOLD=20
```

重启应用，重新执行任务，享受 Word 文件！✅
