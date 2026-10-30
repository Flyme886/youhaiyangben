const { silentLogin, loginWithPassword } = require('../../utils/auth');
const taskApi = require('../../services/task');
const { envVersionText } = require('../../utils/util');
const config = require('../../config/index');

Page({
  data: {
    loading: false,
    password: '',
    error: '',
    envLabel: '生产环境',
    /** 如 trial（体验版）——真机排查时最关键的一行 */
    envVersion: '',
    baseUrl: '',
    /** null = 检测中 */
    serverOk: null,
    serverMsg: '正在检测服务端连通性…',
    /** 失败的具体原因（含微信原始错误），避免各种失败长一个样 */
    serverDetail: ''
  },

  onLoad() {
    this.setData({
      envLabel: config.env === 'dev' ? '联调环境' : '生产环境',
      envVersion: envVersionText(),
      baseUrl: config.baseUrl
    });
    this.probeServer();
  },

  /**
   * 先探一次健康检查，把「服务不可达」和「登录失败」区分开。
   * 失败时把具体原因和微信原始错误一并展示——否则域名未加白名单、请求超时、
   * 证书握手失败在真机上长得一模一样，根本没法定位。
   */
  probeServer() {
    taskApi
      .checkHealth()
      .then(() => {
        this.setData({ serverOk: true, serverMsg: '服务端可达', serverDetail: '' });
      })
      .catch((err) => {
        const detail = (err && err.message) || '请求失败，原因未知';
        const raw = (err && err.errMsg) || '';
        const url = (err && err.url) || '';
        // 把「打到哪个地址失败」也带出来：地址错和域名没白名单是两回事，
        // 前者改配置，后者开调试——不看 URL 根本分不清。
        const lines = [detail];
        if (url) lines.push('请求地址：' + url);
        if (raw) lines.push('原始错误：' + raw);
        this.setData({
          serverOk: false,
          serverMsg: '服务端不可达',
          serverDetail: lines.join('\n')
        });
      });
  },

  onPasswordInput(e) {
    this.setData({ password: e.detail.value, error: '' });
  },

  /** 主通道：微信静默登录 */
  onWechatLogin() {
    if (this.data.loading) return;
    this.setData({ loading: true, error: '' });

    silentLogin(false).then((res) => {
      this.setData({ loading: false });
      if (res && res.token) {
        wx.switchTab({ url: '/pages/tasks/tasks' });
      } else {
        this.setData({
          error: '微信登录未成功。若服务端还没配 AppID / AppSecret，请改用下方访问密码登录。'
        });
      }
    });
  },

  /** 兜底通道：访问密码 */
  onPasswordLogin() {
    const pwd = (this.data.password || '').trim();
    if (!pwd) {
      this.setData({ error: '请输入访问密码' });
      return;
    }
    if (this.data.loading) return;
    this.setData({ loading: true, error: '' });

    loginWithPassword(pwd)
      .then(() => {
        this.setData({ loading: false, password: '' });
        wx.switchTab({ url: '/pages/tasks/tasks' });
      })
      .catch((err) => {
        this.setData({
          loading: false,
          error: err && err.code === 401 ? '访问密码不正确' : (err.message || '登录失败')
        });
      });
  },

  onRetryProbe() {
    this.setData({ serverOk: null, serverMsg: '正在检测服务端连通性…' });
    this.probeServer();
  }
});
