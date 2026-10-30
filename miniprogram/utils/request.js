const config = require('../config/index');

const TOKEN_KEY = 'token';
const OPENID_KEY = 'openid';

/* ---------------- 登录态存储 ---------------- */

function getToken() {
  return wx.getStorageSync(TOKEN_KEY) || '';
}

function setToken(token) {
  if (token) wx.setStorageSync(TOKEN_KEY, token);
}

function getOpenid() {
  return wx.getStorageSync(OPENID_KEY) || '';
}

function setOpenid(openid) {
  if (openid) wx.setStorageSync(OPENID_KEY, openid);
}

function clearSession() {
  wx.removeStorageSync(TOKEN_KEY);
  wx.removeStorageSync(OPENID_KEY);
}

/* ---------------- URL 拼装 ---------------- */

function buildUrl(path) {
  if (/^https?:\/\//.test(path)) return path;
  return config.baseUrl + config.apiPrefix + path;
}

function authHeader() {
  const token = getToken();
  return token ? { Authorization: 'Bearer ' + token } : {};
}

/* ---------------- 401 统一处理 ---------------- */

let redirecting = false;

function handleUnauthorized() {
  clearSession();
  if (redirecting) return;
  redirecting = true;
  wx.showToast({ title: '登录已过期，请重新登录', icon: 'none' });
  setTimeout(() => {
    wx.reLaunch({ url: '/pages/login/login' });
    redirecting = false;
  }, 900);
}

/** FastAPI 的报错体可能是 {detail: "..."} 或 {detail: [{msg: "..."}]}，统一抽成一句话 */
function pickMessage(body, fallback) {
  if (!body) return fallback;
  const detail = body.detail || body.message || body.error;
  if (!detail) return fallback;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail[0].msg || JSON.stringify(detail[0]);
  }
  return fallback;
}

function toast(message) {
  wx.showToast({ title: message, icon: 'none', duration: 2400 });
}

/**
 * 把 wx.request 的 fail 翻译成人能看懂的话。
 *
 * 之前所有网络失败都显示「服务端不可达」，于是
 * 「域名不在白名单」「超时」「证书握手失败」这三种完全不同的问题
 * 长得一模一样，真机上根本没法定位。真实原因其实都在 errMsg 里。
 */
function describeNetworkError(err) {
  const errMsg = (err && err.errMsg) || '';

  // 未备案域名 + 没开「开发调试」时，微信会直接拦掉，返回这个错误
  if (/url not in domain list/i.test(errMsg)) {
    return {
      short: '域名未在白名单内',
      full:
        '该域名不在微信的合法域名白名单内（未备案域名）。' +
        '请在右上角胶囊「···」→ 弹层滑到底部 → 打开「开发调试」，然后重试。'
    };
  }
  if (/timeout/i.test(errMsg)) {
    return {
      short: '请求超时',
      full: '请求超时：当前网络访问不到服务端。请换 WiFi 或移动网络后重试。'
    };
  }
  if (/ssl|handshake|cert/i.test(errMsg)) {
    return {
      short: 'HTTPS 握手失败',
      full: 'HTTPS 证书握手失败，可能是网络被劫持或证书链不完整。'
    };
  }
  if (/abort|cancel|interrupt/i.test(errMsg)) {
    return { short: '请求被中断', full: '请求被中断，请重试。' };
  }
  return {
    short: '网络异常',
    full: '网络请求失败' + (errMsg ? '：' + errMsg : '，请确认服务端可达。')
  };
}

/* ---------------- JSON 请求 ---------------- */

/**
 * @param {object} options
 * @param {string} options.url      接口路径，如 '/tasks'
 * @param {string} [options.method] GET / POST / DELETE
 * @param {object} [options.data]   query 或 body
 * @param {boolean} [options.showError=true] 失败时是否自动 toast
 * @param {boolean} [options.silent401=false] 401 时是否静默（供登录页自检使用）
 */
function request(options) {
  const {
    url,
    method = 'GET',
    data = {},
    header = {},
    showError = true,
    silent401 = false
  } = options;

  return new Promise((resolve, reject) => {
    wx.request({
      url: buildUrl(url),
      method,
      data,
      timeout: config.timeout,
      header: Object.assign({ 'content-type': 'application/json' }, authHeader(), header),
      success(res) {
        const status = res.statusCode;
        const body = res.data;

        if (status === 401) {
          if (!silent401) handleUnauthorized();
          reject({ code: 401, message: '未登录或登录已过期' });
          return;
        }
        if (status >= 200 && status < 300) {
          resolve(body);
          return;
        }
        const message = pickMessage(body, '请求失败（' + status + '）');
        if (showError) toast(message);
        reject({ code: status, message });
      },
      fail(err) {
        console.error('[request] 网络失败', url, err);
        const d = describeNetworkError(err);
        if (showError) toast(d.short);
        reject({
          code: -1,
          message: d.full,
          short: d.short,
          errMsg: (err && err.errMsg) || '',
          url: buildUrl(url)
        });
      }
    });
  });
}

/* ---------------- 文件上传（multipart） ---------------- */

/**
 * 上传 Excel 并生成任务。
 * @param {object} options
 * @param {string} options.filePath   wx.chooseMessageFile 拿到的路径
 * @param {object} [options.formData] 额外字段（name / note）
 * @param {(p:number)=>void} [options.onProgress] 上传进度 0-100
 * @returns {{ promise: Promise, task: object }} 可调用 task.abort() 取消上传
 */
function upload(options) {
  const { filePath, formData = {}, onProgress } = options;
  let aborted = false;
  let uploadTask = null;

  const promise = new Promise((resolve, reject) => {
    uploadTask = wx.uploadFile({
      url: buildUrl('/tasks'),
      filePath,
      name: 'file',
      formData,
      timeout: config.uploadTimeout,
      header: authHeader(),
      success(res) {
        const status = res.statusCode;
        let body = res.data;
        // uploadFile 的 data 是字符串，需要手动解析
        try {
          body = typeof body === 'string' ? JSON.parse(body || '{}') : body;
        } catch (e) {
          reject({ code: -1, message: '服务端返回了非 JSON 内容' });
          return;
        }
        if (status === 401) {
          handleUnauthorized();
          reject({ code: 401, message: '登录已过期' });
          return;
        }
        if (status >= 200 && status < 300) {
          resolve(body);
          return;
        }
        const message = pickMessage(body, '上传失败（' + status + '）');
        toast(message);
        reject({ code: status, message });
      },
      fail(err) {
        // 用户主动取消不该弹「上传失败」
        if (aborted) {
          reject({ code: -2, aborted: true, message: '已取消上传' });
          return;
        }
        console.error('[upload] 失败', err);
        const d = describeNetworkError(err);
        toast(d.short === '网络异常' ? '上传失败，请重试' : d.short);
        reject({
          code: -1,
          message: d.full,
          short: d.short,
          errMsg: (err && err.errMsg) || ''
        });
      }
    });

    if (typeof onProgress === 'function' && uploadTask && uploadTask.onProgressUpdate) {
      uploadTask.onProgressUpdate((e) => onProgress(e.progress));
    }
  });

  /**
   * ⚠️ abort 必须挂在 Promise **构造完成之后**，不能写在 executor 里。
   *
   * Promise 的 executor 是**同步执行**的，执行到那里时 `promise` 还没被赋值；
   * 又因为 project.config.json 里 `es6: true`（ES6 转 ES5），`const` 会被转成 `var`，
   * TDZ 消失，于是不会报「Cannot access 'promise' before initialization」，
   * 而是真机上看到的那句：
   *     Cannot set properties of undefined (setting 'abort')
   * 这个错会直接掐断整次上传，用户只能看到一句英文报错。
   */
  promise.abort = () => {
    aborted = true;
    if (uploadTask && uploadTask.abort) uploadTask.abort();
  };

  return promise;
}

/* ---------------- 文件下载 ---------------- */

/**
 * 下载产物文件到本地临时路径。
 * 注意：wx.downloadFile 支持自定义 header，所以能直接带 token。
 * @returns {Promise<{tempFilePath:string}>} 可用 wx.openDocument / wx.shareFileMessage 消费
 */
function download(options) {
  const { url, showError = true } = options;

  return new Promise((resolve, reject) => {
    wx.downloadFile({
      url: buildUrl(url),
      timeout: config.uploadTimeout,
      header: authHeader(),
      success(res) {
        if (res.statusCode === 401) {
          handleUnauthorized();
          reject({ code: 401, message: '登录已过期' });
          return;
        }
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res);
          return;
        }
        if (showError) toast('下载失败（' + res.statusCode + '）');
        reject({ code: res.statusCode, message: '下载失败' });
      },
      fail(err) {
        console.error('[download] 失败', err);
        const d = describeNetworkError(err);
        if (showError) toast('下载失败，请重试');
        reject({
          code: -1,
          message: d.full,
          short: d.short,
          errMsg: (err && err.errMsg) || '',
          url: buildUrl(url)
        });
      }
    });
  });
}

module.exports = {
  request,
  upload,
  download,
  buildUrl,
  describeNetworkError,
  getToken,
  setToken,
  getOpenid,
  setOpenid,
  clearSession,
  toast
};
