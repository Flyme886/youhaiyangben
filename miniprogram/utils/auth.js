const { request, setToken, setOpenid, getToken, clearSession } = require('./request');

const LOGIN_PAGE = '/pages/login/login';

/** 把 wx.login 包成 Promise，失败返回 null 而不是抛错 */
function wxLoginCode() {
  return new Promise((resolve) => {
    wx.login({
      success(res) {
        resolve(res && res.code ? res.code : null);
      },
      fail(err) {
        console.warn('[auth] wx.login 失败', err);
        resolve(null);
      }
    });
  });
}

/**
 * 微信静默登录：wx.login 拿 code → 后端 code2session → 换自定义 token
 * @param {boolean} [silent] true 时不弹任何错误提示
 * @returns {Promise<{token:string, openid:string}|null>}
 */
function silentLogin(silent) {
  if (getToken()) {
    return Promise.resolve({ token: getToken(), openid: '' });
  }
  return wxLoginCode().then((code) => {
    if (!code) return null;
    return request({
      url: '/login',
      method: 'POST',
      data: { code },
      showError: !silent,
      silent401: true
    })
      .then((res) => {
        setToken(res.token);
        setOpenid(res.openid);
        return res;
      })
      .catch(() => null);
  });
}

/**
 * 账号密码登录（兜底通道）
 * 用于两种场景：
 *  1. 开发阶段还没配 AppID / AppSecret，无法 code2session
 *  2. 管理员在未绑定微信的环境下登录
 */
function loginWithPassword(password) {
  return request({
    url: '/login/password',
    method: 'POST',
    data: { password },
    silent401: true
  }).then((res) => {
    setToken(res.token);
    if (res.openid) setOpenid(res.openid);
    return res;
  });
}

/**
 * 页面调用：确保有登录态，没有就跳登录页
 * @returns {Promise<boolean>} 是否已具备登录态
 */
function ensureLogin() {
  if (getToken()) return Promise.resolve(true);
  const app = getApp();
  return (app && app.whenLoginReady ? app.whenLoginReady() : silentLogin(true)).then(
    (session) => {
      if (session && (session.token || getToken())) return true;
      redirectToLogin();
      return false;
    }
  );
}

function redirectToLogin() {
  const pages = getCurrentPages();
  const current = pages.length ? '/' + pages[pages.length - 1].route : '';
  if (current === LOGIN_PAGE) return;
  wx.reLaunch({ url: LOGIN_PAGE });
}

function logout() {
  clearSession();
  wx.reLaunch({ url: LOGIN_PAGE });
}

module.exports = {
  silentLogin,
  loginWithPassword,
  ensureLogin,
  redirectToLogin,
  logout,
  isLoggedIn: () => !!getToken()
};
