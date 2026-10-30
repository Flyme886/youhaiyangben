Component({
  options: {
    addGlobalClass: true
  },

  properties: {
    /** 主文案 */
    text: { type: String, value: '暂无数据' },
    /** 辅助说明 */
    hint: { type: String, value: '' },
    /** 按钮文案；为空则不渲染按钮 */
    actionText: { type: String, value: '' },
    /** 纯色图标主题：paper 默认 */
    plain: { type: Boolean, value: false }
  },

  methods: {
    onAction() {
      this.triggerEvent('action');
    }
  }
});
