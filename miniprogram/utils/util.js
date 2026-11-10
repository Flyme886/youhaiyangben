/**
 * 通用格式化工具
 * 注意：后端用 datetime.now().isoformat(timespec="seconds")，产出形如
 * "2026-09-29T12:11:17" 的本地时间字符串（无时区）。
 * iOS 对非标准 ISO 串的解析不稳定，所以这里手动解析，不走 new Date(string)。
 */

function parseIso(str) {
  if (!str) return null;
  const m = String(str).match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):?(\d{2})?/);
  if (!m) return null;
  return new Date(
    Number(m[1]),
    Number(m[2]) - 1,
    Number(m[3]),
    Number(m[4]),
    Number(m[5]),
    Number(m[6] || 0)
  );
}

function pad(n) {
  return n < 10 ? '0' + n : String(n);
}

/** "2026-09-29T12:11:17" → "09-29 12:11" */
function formatShort(str) {
  const d = parseIso(str);
  if (!d) return '—';
  return pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
}

/** "2026-09-29T12:11:17" → "2026-09-29 12:11:17" */
function formatFull(str) {
  const d = parseIso(str);
  if (!d) return '—';
  return (
    d.getFullYear() +
    '-' +
    pad(d.getMonth() + 1) +
    '-' +
    pad(d.getDate()) +
    ' ' +
    pad(d.getHours()) +
    ':' +
    pad(d.getMinutes()) +
    ':' +
    pad(d.getSeconds())
  );
}

/** 相对时间："刚刚" / "12 分钟前" / "3 小时前" / "2 天前" */
function fromNow(str) {
  const d = parseIso(str);
  if (!d) return '—';
  const diff = Date.now() - d.getTime();
  if (diff < 0) return formatShort(str);
  const min = Math.floor(diff / 60000);
  if (min < 1) return '刚刚';
  if (min < 60) return min + ' 分钟前';
  const hour = Math.floor(min / 60);
  if (hour < 24) return hour + ' 小时前';
  const day = Math.floor(hour / 24);
  if (day < 30) return day + ' 天前';
  return formatShort(str);
}

/** 两个时间点之间耗时："1 分 12 秒" / "2 小时 3 分" */
function durationText(startStr, endStr) {
  const a = parseIso(startStr);
  const b = parseIso(endStr) || new Date();
  if (!a) return '—';
  let sec = Math.max(0, Math.round((b.getTime() - a.getTime()) / 1000));
  if (sec < 60) return sec + ' 秒';
  const min = Math.floor(sec / 60);
  sec %= 60;
  if (min < 60) return min + ' 分 ' + sec + ' 秒';
  const hour = Math.floor(min / 60);
  return hour + ' 小时 ' + (min % 60) + ' 分';
}

/** 字节数 → "1.2 MB" */
function formatSize(bytes) {
  const n = Number(bytes);
  if (!n || n < 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return (i === 0 ? v : v.toFixed(1)) + ' ' + units[i];
}

/** 截断长文本，保留首尾 */
function truncate(str, max) {
  const s = String(str || '');
  if (s.length <= max) return s;
  return s.slice(0, max) + '…';
}

/** 文件名安全化（下载时用于给文件命名） */
function safeFileName(name) {
  return String(name || 'file').replace(/[\\/:*?"<>|]/g, '_');
}

/**
 * 读当前运行版本并译成中文。
 *
 * 真机排查时这是最关键的一行：同一个界面在「开发版 / 体验版 / 正式版」下
 * 走的 baseUrl 完全不同（见 config/index.js 的三级判定），
 * 报错时不知道跑的是哪个版本，就无从判断地址对不对。
 *
 * @returns {string} 形如 `trial（体验版）`；取不到时为 `未取到`
 */
function envVersionText() {
  const MAP = { develop: '开发版', trial: '体验版', release: '正式版' };
  try {
    const info = wx.getAccountInfoSync();
    const v = (info && info.miniProgram && info.miniProgram.envVersion) || '';
    if (!v) return '未取到';
    return v + '（' + (MAP[v] || '未知版本') + '）';
  } catch (err) {
    return '未取到';
  }
}

/** "2026-09-29T12:11:17" → "12:11" */
function formatClock(str) {
  const d = parseIso(str);
  if (!d) return '';
  return pad(d.getHours()) + ':' + pad(d.getMinutes());
}

/**
 * 列表分组用的日期标签：今天 / 昨天 / 11月8日 / 2025年12月3日
 * @returns {{key:string, label:string}}
 */
function dayLabel(str) {
  const d = parseIso(str);
  if (!d) return { key: 'unknown', label: '更早' };
  const key = d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const that = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const days = Math.round((today.getTime() - that.getTime()) / 86400000);
  let label;
  if (days === 0) label = '今天';
  else if (days === 1) label = '昨天';
  else if (d.getFullYear() === today.getFullYear()) label = d.getMonth() + 1 + '月' + d.getDate() + '日';
  else label = d.getFullYear() + '年' + (d.getMonth() + 1) + '月' + d.getDate() + '日';
  return { key, label };
}

module.exports = {
  parseIso,
  formatClock,
  dayLabel,
  formatShort,
  formatFull,
  fromNow,
  durationText,
  formatSize,
  truncate,
  safeFileName,
  envVersionText
};
