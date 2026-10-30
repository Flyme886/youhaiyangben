const { silentLogin } = require('./utils/auth');
const config = require('./config/index');

App({
  globalData: {
    openid: '',
    /** 服务端连通性，供「我的」页展示 */
    serverOk: false,
    env: config.env,
    baseUrl: config.baseUrl
  },

  onLaunch() {
    // 先做配置自检：生产环境下地址写错就是全盘不可用，
    // 与其让用户看到一堆「网络异常」，不如启动时就把原因讲清楚。
    const problems = config.validate();
    if (problems.length) {
      const detail = problems.map((p, i) => (i + 1) + '. ' + p).join('\n');
      console.error('[app] 配置自检未通过：\n' + detail);
      // 仅在生产环境弹窗（development 下看 console 就够了，别打断调试）
      if (config.env === 'prod') {
        wx.showModal({
          title: '配置未完成',
          content: detail,
          showCancel: false,
          confirmText: '知道了'
        });
      }
    }

    // 启动即做一次静默登录（wx.login → 后端 code2session），
    // 业务页面通过 app.whenLoginReady() 等待，避免各自处理竞态。
    this.ensureLoginPromise = silentLogin(true)
      .then((session) => {
        if (session) {
          this.globalData.openid = session.openid || '';
        }
        return session;
      })
      .catch((err) => {
        // 静默登录失败不阻塞启动，由页面在需要时跳登录页
        console.warn('[app] 静默登录失败：', err && err.message);
        return null;
      });
  },

  /** 页面里 await 这个，保证登录态就绪后再发业务请求 */
  whenLoginReady() {
    return this.ensureLoginPromise || Promise.resolve(null);
  }
});
