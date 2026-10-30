const { request, upload, download } = require('../utils/request');
const config = require('../config/index');

/**
 * 任务列表
 * @param {object} [params]
 * @param {number} [params.page=1]
 * @param {number} [params.size=20]
 * @param {string} [params.status=''] 状态筛选，空表示全部
 * @param {string} [params.keyword=''] 任务名 / ID 模糊搜索
 */
function listTasks(params) {
  const p = params || {};
  const data = {
    page: p.page || 1,
    size: p.size || config.pageSize
  };
  if (p.status) data.status = p.status;
  if (p.keyword) data.keyword = p.keyword;
  return request({ url: '/tasks', data });
}

/** 任务详情：含 summary、文件列表、日志尾部 */
function getTask(taskId) {
  return request({ url: '/tasks/' + taskId });
}

/** 当前登录用户自己的任务（业务用户视角） */
function listMyTasks(params) {
  const p = params || {};
  const data = { page: p.page || 1, size: p.size || config.pageSize };
  if (p.status) data.status = p.status;
  return request({ url: '/tasks/mine', data });
}

/**
 * 上传 Excel 创建任务
 * @returns {Promise<{task_id:string, name:string}>}
 */
function createTask(options) {
  const formData = {};
  if (options.name) formData.name = options.name;
  if (options.note) formData.note = options.note;
  return upload({
    filePath: options.filePath,
    formData,
    onProgress: options.onProgress
  });
}

/**
 * 重跑 / 补齐图片（paused_captcha / partial_completed / failed 可用）。
 * partial_completed 说明文字已交付、只是缺图，重跑只会补没截到的那几条。
 */
function resumeTask(taskId) {
  return request({ url: '/tasks/' + taskId + '/resume', method: 'POST' });
}

/** 取消任务（queued / running 可用） */
function cancelTask(taskId) {
  return request({ url: '/tasks/' + taskId + '/cancel', method: 'POST' });
}

/** 产物文件列表 */
function listFiles(taskId) {
  return request({ url: '/tasks/' + taskId + '/files' });
}

/** 下载单个产物到本地临时路径 */
function downloadFile(taskId, filename) {
  return download({
    url: '/tasks/' + taskId + '/download/' + encodeURIComponent(filename)
  });
}

/** 概览统计：总任务数 / 各状态数量 */
function fetchStats() {
  return request({ url: '/stats', showError: false });
}

/** 服务端健康检查（用于「我的」页展示连通性） */
function checkHealth() {
  return request({ url: '/health', showError: false });
}

module.exports = {
  listTasks,
  listMyTasks,
  getTask,
  createTask,
  resumeTask,
  cancelTask,
  listFiles,
  downloadFile,
  fetchStats,
  checkHealth
};
