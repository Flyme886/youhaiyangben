/**
 * 交付印章
 *
 * 详情页里任务一结束就盖这枚章；用户正盯着看时（轮询中由进行中转为结束），
 * 传 stamp=true 让它「压下去」一次并轻震，作为结果到达的唯一一次动效。
 *
 * tone:
 *   seal  朱砂 —— 交付了（含缺图交付）
 *   void  灰墨 —— 没交付（失败 / 待重跑 / 撤回）
 */
Component({
  options: {
    addGlobalClass: false
  },

  properties: {
    text: { type: String, value: '' },
    /** 章下方的小字，通常是日期 */
    sub: { type: String, value: '' },
    tone: { type: String, value: 'seal' },
    /** true 时播放一次落章动画 */
    stamp: { type: Boolean, value: false },
    /** 尺寸：md（详情页）/ lg（登录页品牌章） */
    size: { type: String, value: 'md' }
  },

  data: {
    chars: []
  },

  observers: {
    text(text) {
      // 四个字排成两行两列，三个字以内一行排开 —— 和真实公章的排字习惯一致
      const s = String(text || '');
      this.setData({
        chars: s.length === 4 ? [s.slice(0, 2), s.slice(2)] : [s]
      });
    }
  }
});
