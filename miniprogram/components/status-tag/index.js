const { describeStatus } = require('../../utils/status');

Component({
  options: {
    // 让父页面的 class 能作用到组件根节点，方便外部调整间距
    addGlobalClass: true
  },

  properties: {
    /** 后端返回的原始状态值，如 running / partial_completed */
    status: { type: String, value: '' },
    /** 覆盖默认文案；不传则用 STATUS_MAP 的 label */
    text: { type: String, value: '' },
    /** 紧凑模式：更小的内边距，用于列表卡片 */
    compact: { type: Boolean, value: false }
  },

  data: {
    label: '',
    tone: 'muted',
    live: false
  },

  observers: {
    'status, text': function (status, text) {
      const meta = describeStatus(status);
      this.setData({
        label: text || meta.label,
        tone: meta.tone,
        live: status === 'running'
      });
    }
  }
});
