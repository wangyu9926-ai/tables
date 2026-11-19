/* 全站 Vue 桥（任务书-全站Vue化迁移 批0 基建）
 * 能力:
 *   mountPageApp(component, container)  挂载当前活动 Vue 实例（图片页可在切页时挂起，挂新前卸当前活动实例）
 *   unmountPageApp()                    卸载当前实例（先调页面 pageCleanup 钩子：echarts dispose / 事件解绑）
 *   VChart                              echarts 封装组件（props: option/height；onUnmounted 自动 dispose）
 *   KpiGrid                             KPI 卡网格（props: cards，同原生 kpiCards 口径，value 支持 HTML）
 *   VPager                              分页器（props: page/pages/total；emit: go）
 * 约定:
 *   页面级清理钩子 = 组件根 proxy.pageCleanup()（可选），unmount 前调用；
 *   navigate() 在渲染新页前调用 unmountPageApp()，保证旧实例先卸载再清容器。
 * 红线: 无 emoji；零构建（全局 Vue，模板字符串）；不引 vue-router/pinia。
 */
(function () {
  'use strict';

  /* ---- tooltip 模式一次性探测（2026-10-22）----
   * 本仓 echarts 在部分环境（实测 ZCode 嵌入式 webview）里 **trigger:'axis' 的 tooltip
   * 内容永不生成**（axisPointer 虚线画得出、内容框出不来，richText 同样无效）；
   * trigger:'item' + html 一切正常（饼图即此路径，用户实测有提示）。
   * 后果链：巡检探针见「无 tooltip DOM」→ 每 3s 全量重置 → 无限重建最终把 series 状态
   * 搞坏（sampling 处理器 reading 'type' 崩溃、tooltip 组件整个从 getOption 消失）。
   * 探测失败 → `window.__TOOLTIP_MODE='item'`：全站把 trigger:'axis' 降级为 'item'
   * （单点提示，牺牲同柱多系列对比换「有得看」）；真实浏览器探测通过，保持 axis 原样。
   * 探测必须验「有文字的叶子 div」：空 tooltip 容器是同步建的、内容异步填充，
   * 只验 div 存在会把永远等不到内容的 webview 误判成正常（实测踩坑）。 */
  window.__TOOLTIP_MODE = (function () {
    try {
      if (!window.echarts || !document.body) return 'html';
      var d = document.createElement('div');
      d.style.cssText = 'position:fixed;left:-9999px;top:0;width:300px;height:180px';
      document.body.appendChild(d);
      var inst = echarts.init(d);
      inst.setOption({
        tooltip: { show: true, trigger: 'axis', renderMode: 'html' },
        xAxis: { type: 'category', data: ['a', 'b', 'c'] },
        yAxis: { type: 'value' },
        series: [{ type: 'line', data: [1, 2, 3] }]
      });
      inst.dispatchAction({ type: 'showTip', seriesIndex: 0, dataIndex: 1 });
      var ok = false;
      Array.from(inst.getDom().querySelectorAll('div')).forEach(function (x) {
        if (!x.children.length && x.textContent && x.textContent.length > 1) ok = true;
      });
      inst.dispose(); d.remove();
      return ok ? 'html' : 'item';
    } catch (e) { return 'item'; }
  })();

  /* axis→item 降级时给 formatter 兜底：axis 版 formatter（params.map/params[0]）
   * 收到 item 的单参数对象会抛错 → tooltip 全灭；包一层退回默认「名 值」文案。 */
  window.__tooltipItemSafe = function (tooltip) {
    if (!tooltip || typeof tooltip.formatter !== 'function') return;
    var f = tooltip.formatter;
    tooltip.formatter = function (params) {
      try { return f(params); } catch (e) {
        var p = Array.isArray(params) ? params[0] : params;
        return p ? ((p.marker || '') + p.name + '  ' + (p.value == null ? '-' : p.value)) : '';
      }
    };
  };

  var _current = null;   // { app, container }

  /* 页级开关：VUE_ALL=false 全站回原生；PAGE_VUE[pageId]=false 该页回原生。缺省=Vue。 */
  window.useVuePage = function (pageId) {
    try {
      if (window.VUE_ALL === false) return false;
      if (window.PAGE_VUE && window.PAGE_VUE[pageId] === false) return false;
    } catch (e) { /* 忽略 */ }
    return true;
  };

  window.mountPageApp = function (component, container) {
    if (!window.Vue || typeof Vue.createApp !== 'function') {
      container.innerHTML = '<div class="empty is-err">Vue 运行时未加载（检查 js/vendor/vue.global.prod.js）</div>';
      return null;
    }
    window.unmountPageApp();
    var app = Vue.createApp(component);
    var vm = app.mount(container);
    _current = { app: app, container: container, vm: vm };
    return vm;
  };

  window.unmountPageApp = function () {
    if (!_current) return;
    var app = _current.app;
    try {
      var proxy = _current.vm || (app._instance && app._instance.proxy);
      if (proxy && typeof proxy.pageCleanup === 'function') proxy.pageCleanup();
    } catch (e) { /* 页面未定义清理钩子时忽略 */ }
    try { app.unmount(); } catch (e) { /* 容器已被清掉时忽略 */ }
    _current = null;
  };

  window.suspendPageApp = function (container) {
    if (!_current || _current.container !== container) return null;
    var current = _current; _current = null; return current;
  };
  window.getPageApp = function (container) { return _current && _current.container === container ? (_current.vm || (_current.app._instance && _current.app._instance.proxy)) : null; };
  window.resumePageApp = function (current) {
    if (_current === current) return;
    window.unmountPageApp(); _current = current;
  };
  window.disposePageApp = function (current) {
    var previous = _current; _current = current;
    window.unmountPageApp();
    if (previous !== current) _current = previous;
  };
  window.isPageAppBusy = function (container) {
    if (!_current || _current.container !== container) return false;
    var proxy = _current.vm || (_current.app._instance && _current.app._instance.proxy);
    return !!(proxy && ['busy', 'genBusy', 'optimizing', 'opBusy', 'brandSaving', 'sellBusy'].some(function (field) {
      return proxy.$data[field] === true;
    }));
  };

  /* ---- VChart：echarts 封装（option 变更自动 setOption，卸载自动 dispose） ---- */
  window.VChart = {
    name: 'VChart',
    props: {
      option: { type: Object, default: null },
      height: { type: [Number, String], default: 320 },
      group: { type: String, default: '' }
    },
    template: '<div ref="el" :style="{ width: \'100%\', height: (isNaN(Number(height)) ? height : Number(height) + \'px\') }"></div>',
    data: function () { return { inst: null }; },
    mounted: function () {
      if (!window.echarts) return;
      var self = this;
      /* 布局未稳时 init 会把 canvas 锁死在临时宽度（实测 grid 拉伸前只有 28px，图被压扁），
       * 双帧等待布局定型后再 init，init 后立刻 resize 对齐最终宽度。 */
      var doInit = function () {
        /* 卸载守卫：组件被卸载（或 DOM 已分离）时放弃 init——
         * 否则会在分离 DOM 上建出孤儿实例（无人 dispose，全局事件报 getAxis，实测踩坑） */
        if (self._gone || self.inst || !self.$refs.el || !self.$refs.el.isConnected) return;
        self.inst = echarts.init(self.$refs.el);
        if (self.group) self.inst.group = self.group;
        if (self.option) {
          self.inst.setOption(self._plain(self.option), true);
        }
        self.inst.resize();
        /* tooltip 巡检自愈（2026-10-13 实测）：页面运行中 tooltip 组件会进入坏状态——
         * trigger:axis 的图悬浮无数值，dispatchAction showTip 也不渲染 DOM（页面实例
         * 实锤 showTip 无 DOM；setOption(getOption(), true) 全量重设即恢复）。
         * 触发条件黑盒无法复现（疑与实例多次 setOption 的内部状态累积有关），
         * 故做自闭环巡检：每 3s 试弹一次探针，弹不出就全量重设；连续 3 次健康即停。 */
        var instHeal = self.inst;
        var okStreak = 0;
        /* 健康满 3 次后转 15s 低频永久巡检（2026-10-08 用户再遇：停表后 tooltip 再次坏掉无人修） */
        var healProbe = function () {
          if (self._gone || !instHeal) { clearInterval(self._healTimer); self._healTimer = null; return; }
          try {
            instHeal.dispatchAction({ type: 'showTip', seriesIndex: 0, dataIndex: 0 });
            var ok = false;
            /* html tooltip 挂在图表容器内（叶子 div 带文本即健康；容器子 div 仅 tooltip 一层） */
            Array.from(instHeal.getDom().querySelectorAll('div')).forEach(function (x) {
              if (!x.children.length && x.textContent && x.textContent.length > 1) ok = true;
            });
            instHeal.dispatchAction({ type: 'hideTip' });
            if (!ok) {
              instHeal.setOption(instHeal.getOption(), true);
              okStreak = 0;
            } else if (++okStreak === 3) {
              clearInterval(self._healTimer);
              self._healTimer = setInterval(healProbe, 15000);
            }
          } catch (e) {
            clearInterval(self._healTimer);
            self._healTimer = null;
          }
        };
        /* 巡检照跑：'item' 降级模式下 tooltip 仍是 html DOM，坏状态可被探针自愈。 */
        self._healTimer = setInterval(healProbe, 3000);
      };
      requestAnimationFrame(function () { requestAnimationFrame(doInit); });
      this._onResize = function () { if (this.inst) this.inst.resize(); }.bind(this);
      window.addEventListener('resize', this._onResize);
    },
    beforeUnmount: function () {
      this._gone = true;
      window.removeEventListener('resize', this._onResize);
      if (this._healTimer) { clearInterval(this._healTimer); this._healTimer = null; }
      if (this.inst) { this.inst.dispose(); this.inst = null; }
    },
    watch: {
      option: {
        deep: true,
        handler: function (o) {
          if (this.inst && o) {
            /* notMerge 全量替换；坏状态由上方巡检自愈兜底（数据更新可能重置健康计数无效，
             * 巡检按实例健康实测为准）。 */
            this.inst.setOption(this._plain(o), true);
          }
        }
      }
    },
    methods: {
      resize: function () { if (this.inst) this.inst.resize(); },
      /* data 里的静态 option 会被 Vue 深度 reactive 包装，proxy 传给 echarts 会在
       * overallReset 阶段炸 reading 'type'（实测 aging/org-overview）；
       * 深克隆剥离 proxy，保证 echarts 拿到纯对象。
       * 同时注入 tooltip.renderMode='html'：本仓 echarts.min.js 的默认 renderMode
       * 被解析成非法值 'auto'，html 模式 tooltip（trigger:axis）DOM 不渲染 →
       * 悬浮无数值（饼图 richText 分支不受影响）。与原生 chart() 助手同修。
       * 图表丝滑动画默认值（2026-10-22 界面优化）：与 app.js _chartMotionDefaults
       * 同款内联实现（本文件先于 app.js 加载，不能反向引用）；页面显式配置不覆盖。 */
      _plain: function (o) {
        try {
          var p = JSON.parse(JSON.stringify(o));
          /* tooltip 环境适配（探测结论见文件头 __TOOLTIP_MODE）：
           * renderMode 恒补 'html'（本仓 echarts 缺省解析成非法 'auto'）；
           * 'item' 降级环境把 axis 触发改 item（axis 内容在该环境永不成型），
           *   并给函数 formatter 包容错（axis 版收到 item 单参必抛错）。 */
          if (p && p.tooltip) {
            p.tooltip.renderMode = 'html';
            if ((window.__TOOLTIP_MODE || 'html') === 'item' && p.tooltip.trigger === 'axis') {
              p.tooltip.trigger = 'item';
              window.__tooltipItemSafe(p.tooltip);
            }
          }
          if (p && typeof p === 'object') {
            var list = Array.isArray(p.series) ? p.series : (p.series ? [p.series] : []);
            var isBar = list.some(function (s) { return s && s.type === 'bar'; });
            if (p.animation === undefined) p.animation = true;
            if (p.animationDuration === undefined) p.animationDuration = isBar ? 700 : 900;
            if (p.animationEasing === undefined) p.animationEasing = 'cubicOut';
            if (isBar && p.animationDelay === undefined) p.animationDelay = 100;
            if (!isBar && list.length > 1) {
              list.forEach(function (s, i) {
                if (s && s.animationDelay === undefined) s.animationDelay = i * 90;
              });
            }
            if (p.animationDurationUpdate === undefined) p.animationDurationUpdate = 500;
            if (p.animationEasingUpdate === undefined) p.animationEasingUpdate = 'cubicInOut';
          }
          return p;
        } catch (e) { return o; }
      }
    },
    expose: ['resize', 'inst']
  };

  /* ---- KpiGrid：KPI 卡网格（value 为 HTML，与原生 kpiCards 口径一致） ----
   * 外层只有 grid 布局类，kpi 卡片类只上内层卡；cls 供调用方传网格特化类（如产品页 prod-kpi-row）。 */
  window.KpiGrid = {
    name: 'KpiGrid',
    props: {
      cards: { type: Array, default: function () { return []; } },
      cls: { type: String, default: 'g-auto' }
    },
    template: '<div class="grid" :class="cls">' +
      '<div class="kpi" v-for="c in cards" :key="c.name">' +
      '<div class="k-name">{{ c.name }}</div>' +
      '<div class="k-val" v-html="c.value"></div>' +
      '<div class="k-delta" v-html="c.delta || \'\'"></div>' +
      '</div></div>'
  };

  /* ---- VPager：分页器（2026-10-06 全站统一规范：翻页钮在左，右下「当前第X/Y页·共N条」+可选每页条数，
   *   总页数>2 时「跳转到第N页」输入区单独一行居中；@go 载荷=目标页码，@size 载荷=每页条数） ---- */
  window.VPager = {
    name: 'VPager',
    props: {
      page: { type: Number, default: 1 }, pages: { type: Number, default: 1 }, total: { type: Number, default: 0 },
      size: { type: Number, default: 0 },
      sizes: { type: Array, default: function () { return []; } }
    },
    emits: ['go', 'size'],
    data: function () { return { jumpTo: null }; },
    methods: {
      doJump: function () {
        var p = parseInt(this.jumpTo, 10);
        if (isNaN(p) || p < 1) p = 1;
        if (p > this.pages) p = this.pages;
        this.jumpTo = null;
        if (p !== this.page) this.$emit('go', p);
      }
    },
    /* 一行三段式（用户 2026-10-06 定稿）：左信息 / 中翻页钮+跳转 / 右每页条数 */
    template: '<div v-if="pages > 1" style="display:flex;align-items:center;gap:10px;margin-top:10px;flex-wrap:wrap">' +
        '<span class="pgbar-info" style="flex:1;min-width:110px">当前第 {{ page }} / {{ pages }} 页<template v-if="total"> · 共 {{ total }} 条</template></span>' +
        '<span style="display:inline-flex;gap:6px;align-items:center">' +
          '<button type="button" class="btn btn-sm" :disabled="page <= 1" @click="$emit(\'go\', 1)">首页</button>' +
          '<button type="button" class="btn btn-sm" :disabled="page <= 1" @click="$emit(\'go\', page - 1)">上一页</button>' +
          '<button type="button" class="btn btn-sm btn-primary" :disabled="page >= pages" @click="$emit(\'go\', page + 1)">下一页</button>' +
          '<button type="button" class="btn btn-sm" :disabled="page >= pages" @click="$emit(\'go\', pages)">末页</button>' +
          '<span style="display:inline-flex;align-items:center;gap:6px;margin-left:6px">跳转到第 ' +
            '<input type="number" class="ctl pgbar-no" min="1" :max="pages" v-model.number="jumpTo" @keyup.enter="doJump" style="width:64px;text-align:center"> 页' +
            '<button type="button" class="btn btn-sm" @click="doJump">跳转</button></span>' +
        '</span>' +
        '<span style="flex:1;min-width:110px;display:inline-flex;justify-content:flex-end;align-items:center;gap:6px">' +
          '<template v-if="sizes && sizes.length"><label class="pgbar-label">每页</label>' +
          '<select class="ctl pgbar-size" :value="size" @change="$emit(\'size\', Number($event.target.value))">' +
            '<option v-for="n in sizes" :key="n" :value="n">{{ n }} 条</option></select></template>' +
        '</span>' +
    '</div>'
  };
})();
