# 踩坑记录

## 截图采集

**微博 login_required 误判**
- 原因：导航栏"登录"按钮触发弱关键词匹配
- 修法：区分强/弱信号，bodyText > 500字符时弱词不算登录墙

**截图底部大片空白**
- 原因：fullPage 截取了无限滚动占位符的空白高度
- 修法：截图前滚动触发懒加载，再按叶节点底部裁剪

**成功率低的真正原因不是 IP**
- 本地浏览器能看是因为有积累的 Cookie，Playwright 每次零状态
- 修法：`export_cookies.py` 导出登录态，`--storage-state` 注入

**emoji 导致任务崩溃**
- 原因：Node.js 写 `\uD83C` 转义，Python `json.dumps(ensure_ascii=False)` 重写时报 UnicodeEncodeError
- 修法：用 `_safe_json()`（encode utf-8 errors=replace）

## 飞书机器人

**群里 @ 无响应**
- 新加权限（`im:message.group_at_msg:readonly`）必须**发布新版本**才对群生效，私聊不受影响
- 验证方式：事件配置页 → 点「验证」+ 私聊测试，可区分连接层还是权限层问题

**群消息 @ 文本需清洗**
- `@有害样本整理 取消` 中的 `@xxx` 需 `re.sub(r"@[^\s]+", "", text)` 剥离后再匹配指令

## 部署

**云服务器没有 git**
- 用 `scp -P 2222` 直接传文件，传完 `systemctl restart` 或手动重启进程

**bot 启动必须 source .env**
- `nohup python feishu_bot.py` 直接跑会报 `FEISHU_APP_ID 未设置`
- 正确姿势：`set -a && source .env && set +a && nohup python feishu_bot.py`
