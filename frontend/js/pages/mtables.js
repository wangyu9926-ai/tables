/* 模块：多维表格（开放版）
 * ------------------------------------------------------------------
 * 权限模型（2026-11 用户确认）：
 *   游客（未登录）—— 所有表可看，不能改（界面上不出现编辑入口）
 *   登录用户（admin 添加）—— 所有表可增/改/删行，可看变动历史
 *   管理员 —— 另可建表/删表/管列/导入Excel/管用户/HTTP密钥/DB视图
 *
 * 行编辑走乐观锁：每次保存带上读到的 ver，服务器 ver 不匹配回 409，
 * 弹窗提示「已被别人改过」，不覆盖别人的修改。
 *
 * 变动历史（审计）：所有写操作落 mt_audit，登录用户可在每张表里查看
 * 「谁、何时、改了哪行哪个字段、旧值→新值」。
 * ------------------------------------------------------------------ */
registerPage('mtables', {
  title: '多维表格', icon: 'datamgmt',
  render: function (container) {
    if (window.useVuePage && window.useVuePage('mtables') && window.mountPageApp) {
      return renderMtablesVue(container);
    }
    return renderMtablesLegacy(container);
  }
});

var MT_TYPE_LABEL = {
  text: '文本', number: '数字', date: '日期',
  select: '单选', multiselect: '多选', checkbox: '勾选', user: '人员', image: '图片'
};

function renderMtablesVue(container) {
  var cmp = {
    data: function () {
      return {
        booting: true,
        me: { user: '', role: 'guest', is_admin: false, logged_in: false },
        tables: [],
        // 当前打开的表
        tid: null,
        table: null,
        columns: [],
        canEdit: false,
        rows: [],
        total: 0,
        page: 1,
        size: 100,
        keyword: '',
        loadingRows: false,
        // 弹窗
        dlg: '',            // '' | 'newTable' | 'newCol' | 'editCol' | 'editRow' | 'users' | 'apikeys' | 'audit' | 'dbview'
        busy: false,
        // 新建表
        nt: { name: '', note: '', cols: [{ label: '名称', type: 'text', required: true }] },
        // Excel 导入
        imp: { name: '', note: '', fileB64: '' },
        // 新建/编辑列
        nc: { id: null, label: '', type: 'text', options: [], required: false },
        ncOptText: '',
        // 编辑行
        er: { id: null, ver: null, data: {} },
        // 单元格就地编辑（双击）：editing={rid,key}，editVal 为工作值
        editing: null,
        editVal: null,
        savingRow: false,
        // 用户管理（admin）
        users: [],
        nu: { username: '', password: '', real_name: '' },
        rp: { uid: null, password: '' },   // 重置密码
        // HTTP 写入密钥（admin）
        apikeys: [],
        newKeyNote: '',
        lastKey: '',
        // 数据库视图
        dbv: { exists: false, name: '', columns: [] },
        // 图片列加载失败的 URL
        imgErr: {},
        imgNonce: {},
        // 图片查看弹窗（点缩略图页内放大，不开新标签）
        viewer: { urls: [], idx: 0, err: false },
        // 变动历史
        audits: [],
        typeLabel: MT_TYPE_LABEL,
        colTypes: ['text', 'number', 'date', 'select', 'multiselect', 'checkbox', 'user', 'image']
      };
    },
    mounted: function () {
      this.boot();
      this._mtViewerKey = (e) => {
        if (!e.isTrusted || !this.viewer.urls.length) return;
        if (e.key === 'Escape') this.closeViewer();
        else if (e.key === 'ArrowLeft') this.vPrev();
        else if (e.key === 'ArrowRight') this.vNext();
      };
      document.addEventListener('keydown', this._mtViewerKey);
    },
    beforeUnmount: function () {
      this.closeEdit();   // 切页时摘掉 document 级点击监听
      if (this._mtViewerKey) document.removeEventListener('keydown', this._mtViewerKey);
    },
    directives: {
      /* 编辑框挂载即聚焦（Vue3 组件级指令） */
      focus: { mounted: function (el) { el.focus(); } }
    },
    computed: {
      pageCount: function () { return Math.max(1, Math.ceil(this.total / this.size)); },
      isAdmin: function () { return !!(this.me && this.me.is_admin); },
      isLoggedIn: function () { return !!(this.me && this.me.logged_in); },
      emptyListText: function () {
        return this.isAdmin ? '还没有表格，点「新建表格」开始' : '还没有表格';
      },
      emptyRowsText: function () {
        return this.canEdit ? '还没有数据，点「新增行」开始' : '还没有数据';
      }
    },
    methods: {
      /* ---- 启动 ---- */
      boot: async function () {
        this.booting = true;
        try {
          var m = await window.API.mtMeta();
          this.me = m || this.me;
          await this.loadTables();
        } catch (e) {
          toast('加载失败：' + this.errMsg(e));
        } finally {
          this.booting = false;
        }
      },
      errMsg: function (e) {
        if (!e) return '网络错误';
        return e.message || e.detail || '网络错误';
      },
      loadTables: async function () {
        var d = await window.API.mtTables();
        this.tables = d.rows || [];
      },
      openTable: async function (t) {
        this.tid = t.id;
        this.page = 1;
        this.keyword = '';
        await this.reload();
      },
      reload: async function () {
        if (!this.tid) return;
        this.loadingRows = true;
        try {
          var d = await window.API.mtTable(this.tid);
          this.table = d.table;
          this.columns = d.columns || [];
          this.canEdit = !!(d.table && d.table.can_edit);
          await this.loadRows();
        } catch (e) {
          toast('打开表失败：' + this.errMsg(e));
        } finally {
          this.loadingRows = false;
        }
      },
      loadRows: async function () {
        if (!this.tid) return;
        var d = await window.API.mtRows(this.tid, {
          page: this.page, size: this.size, q: this.keyword || ''
        });
        this.rows = d.rows || [];
        this.total = d.total || 0;
      },
      backToList: function () {
        this.tid = null;
        this.table = null;
        this.columns = [];
        this.rows = [];
        this.total = 0;
        this.loadTables();
      },
      search: function () { this.page = 1; this.loadRows(); },
      goPage: function (p) {
        if (p < 1 || p > this.pageCount) return;
        this.page = p;
        this.loadRows();
      },
      /* ---- 单元格显示 ---- */
      cellText: function (col, row) {
        var v = (row.data || {})[col.key];
        if (v === null || v === undefined || v === '') return '';
        if (col.type === 'checkbox') return v ? '是' : '否';
        if (col.type === 'multiselect') return Array.isArray(v) ? v.join('、') : String(v);
        return String(v);
      },
      cellBool: function (col, row) {
        return !!((row.data || {})[col.key]);
      },
      /* 图片列：值支持逗号/分号/空白分隔多个 URL */
      imgList: function (col, row) {
        var v = (row.data || {})[col.key];
        if (!v) return [];
        return String(v).split(/[,，;；\s]+/).filter(function (x) { return x; });
      },
      imgFail: function (u) {
        // 网络抖动自动重试一次（key 变更强制重建 <img>），再失败才降级为链接
        var n = this.imgNonce[u] || 0;
        if (n < 1) {
          var self = this;
          setTimeout(function () { self.imgNonce[u] = n + 1; }, 1200);
        } else {
          this.imgErr[u] = true;
        }
      },
      /* ---- 图片查看弹窗（页内放大，不开新标签） ---- */
      openViewer: function (urls, idx) {
        var list = (urls || []).filter(function (x) { return x; });
        if (!list.length) return;
        this.viewer = { urls: list, idx: Math.min(Math.max(idx || 0, 0), list.length - 1), err: false };
      },
      closeViewer: function () { this.viewer = { urls: [], idx: 0, err: false }; },
      vPrev: function () {
        if (this.viewer.urls.length < 2) return;
        this.viewer.idx = (this.viewer.idx - 1 + this.viewer.urls.length) % this.viewer.urls.length;
        this.viewer.err = false;
      },
      vNext: function () {
        if (this.viewer.urls.length < 2) return;
        this.viewer.idx = (this.viewer.idx + 1) % this.viewer.urls.length;
        this.viewer.err = false;
      },
      vErr: function () { this.viewer.err = true; },
      /* ---- 单元格就地编辑（Excel 式双击；勾选列单击直接切） ---- */
      startEdit: function (r, c) {
        if (!this.canEdit || this.savingRow) return;
        if (c.type === 'checkbox') return;
        var v = (r.data || {})[c.key];
        if (c.type === 'multiselect') {
          this.editVal = Array.isArray(v) ? v.slice() : [];
          this.editing = { rid: r.id, key: c.key };
          var self = this;
          this._mtOutside = function (ev) {
            var pop = document.querySelector('.mt-editor-pop');
            if (pop && !pop.contains(ev.target)) self.commitCell(r, c);
          };
          setTimeout(function () { document.addEventListener('click', self._mtOutside); }, 0);
        } else {
          this.editVal = (v === null || v === undefined) ? '' : v;
          this.editing = { rid: r.id, key: c.key };
        }
      },
      closeEdit: function () {
        if (this._mtOutside) {
          document.removeEventListener('click', this._mtOutside);
          this._mtOutside = null;
        }
        this.editing = null;
        this.editVal = null;
      },
      cancelEdit: function () { this.closeEdit(); },
      commitCell: async function (r, c, keepOpen) {
        if (!this.editing || this.editing.rid !== r.id || this.editing.key !== c.key) return;
        var old = (r.data || {})[c.key];
        var val = this.editVal;
        if (c.type === 'number') {
          val = (val === '' || val === null || val === undefined) ? null : Number(val);
        } else if (val === '') {
          val = null;
        }
        if (JSON.stringify(old === undefined || old === null ? null : old) === JSON.stringify(val)) {
          if (!keepOpen) this.closeEdit();
          return;   // 值没变不打库，避免白涨乐观锁 ver
        }
        var d = {}; d[c.key] = val;
        await this.saveCell(r, d);
        if (!keepOpen) this.closeEdit();
      },
      saveCell: async function (r, d) {
        if (this.savingRow) return;
        this.savingRow = true;
        try {
          var res = await window.API.mtRowUpdate(this.tid, r.id, { data: d, ver: r.ver });
          r.data = res.data || r.data;
          r.ver = res.ver;
        } catch (e) {
          var msg = String(this.errMsg(e));
          if (msg.indexOf('409') >= 0 || msg.indexOf('版本') >= 0 || msg.indexOf('刷新') >= 0) {
            toast('这行已被别人改过，已为你刷新');
            this.closeEdit();
            await this.loadRows();
          } else {
            toast('保存失败：' + msg);
          }
        } finally {
          this.savingRow = false;
        }
      },
      toggleCell: async function (r, c, ev) {
        if (!this.canEdit) return;
        var val = !!((r.data || {})[c.key]);
        if (this.savingRow) {
          if (ev && ev.target) ev.target.checked = val;
          return;
        }
        var d = {}; d[c.key] = !val;
        await this.saveCell(r, d);
        if (ev && ev.target) ev.target.checked = !!((r.data || {})[c.key]);
      },
      /* ---- 新建表（admin） ---- */
      openNewTable: function () {
        this.nt = { name: '', note: '', cols: [{ label: '名称', type: 'text', required: true }] };
        this.dlg = 'newTable';
      },
      addNtCol: function () { this.nt.cols.push({ label: '', type: 'text', required: false }); },
      delNtCol: function (i) { if (this.nt.cols.length > 1) this.nt.cols.splice(i, 1); },
      submitNewTable: async function () {
        var name = (this.nt.name || '').trim();
        if (!name) { toast('表名不能为空'); return; }
        var cols = (this.nt.cols || []).filter(function (c) { return (c.label || '').trim(); });
        if (!cols.length) { toast('至少要有一个列'); return; }
        this.busy = true;
        try {
          var r = await window.API.mtTableCreate({ name: name, note: this.nt.note || '', columns: cols });
          this.dlg = '';
          toast('已建表：' + r.name);
          await this.loadTables();
          var t = this.tables.filter(function (x) { return x.id === r.id; })[0];
          if (t) await this.openTable(t);
        } catch (e) {
          toast('建表失败：' + this.errMsg(e));
        } finally { this.busy = false; }
      },
      /* ---- Excel 导入建表（admin） ---- */
      openImport: function () {
        this.imp = { name: '', note: '', fileB64: '' };
        this.dlg = 'import';
      },
      onImportFile: function (ev) {
        var f = (ev.target && ev.target.files && ev.target.files[0]) || null;
        if (!f) { this.imp.fileB64 = ''; return; }
        var self = this;
        var rd = new FileReader();
        rd.onload = function () {
          var s = String(rd.result || '');
          self.imp.fileB64 = s.indexOf('base64,') >= 0 ? s.slice(s.indexOf('base64,') + 7) : s;
          if (!self.imp.name) self.imp.name = f.name.replace(/\.xlsx$/i, '');
        };
        rd.onerror = function () { toast('读取文件失败'); };
        rd.readAsDataURL(f);
      },
      submitImport: async function () {
        var name = (this.imp.name || '').trim();
        if (!name) { toast('表名不能为空'); return; }
        if (!this.imp.fileB64) { toast('先选一个 .xlsx 文件'); return; }
        this.busy = true;
        try {
          var r = await window.API.mtImport({ name: name, note: this.imp.note || '', file_b64: this.imp.fileB64 });
          this.dlg = '';
          toast('已导入：' + r.name + '（' + (r.col_count || 0) + ' 列 / ' + (r.row_count || 0) + ' 行）');
          await this.loadTables();
          var t = this.tables.filter(function (x) { return x.id === r.id; })[0];
          if (t) await this.openTable(t);
        } catch (e) {
          toast('导入失败：' + this.errMsg(e));
        } finally { this.busy = false; }
      },
      deleteTable: function (t) {
        var self = this;
        window.confirmModal({
          title: '删除表「' + t.name + '」？',
          msg: '表会从列表里移除，行数据一并软删除。',
          okText: '删除', cancelText: '取消',
          onOk: function () {
            return window.API.mtTableDelete(t.id).then(function () {
              toast('已删除');
              if (self.tid === t.id) self.backToList(); else self.loadTables();
            });
          }
        });
      },
      /* ---- 列管理（admin） ---- */
      openNewCol: function () {
        this.nc = { id: null, label: '', type: 'text', options: [], required: false };
        this.ncOptText = '';
        this.dlg = 'newCol';
      },
      openEditCol: function (col) {
        this.nc = {
          id: col.id, label: col.label, type: col.type,
          options: (col.options || []).slice(), required: !!col.required
        };
        this.ncOptText = (col.options || []).join('、');
        this.dlg = 'editCol';
      },
      needOptions: function () {
        return this.nc.type === 'select' || this.nc.type === 'multiselect';
      },
      submitCol: async function () {
        var label = (this.nc.label || '').trim();
        if (!label) { toast('列名不能为空'); return; }
        var opts = null;
        if (this.needOptions()) {
          opts = (this.ncOptText || '').split(/[、,，\s]+/).filter(function (x) { return x; });
          if (!opts.length) { toast('单选/多选列至少要有一个选项'); return; }
        }
        var body = { label: label, type: this.nc.type, options: opts, required: !!this.nc.required };
        this.busy = true;
        try {
          if (this.nc.id) {
            await window.API.mtColUpdate(this.tid, this.nc.id, body);
            toast('列已更新');
          } else {
            await window.API.mtColCreate(this.tid, body);
            toast('列已添加');
          }
          this.dlg = '';
          await this.reload();
        } catch (e) {
          toast('保存列失败：' + this.errMsg(e));
        } finally { this.busy = false; }
      },
      deleteCol: function (col) {
        var self = this;
        window.confirmModal({
          title: '删除列「' + col.label + '」？',
          msg: '该列在所有行里的数据都会清掉，不可恢复。',
          okText: '删除', cancelText: '取消',
          onOk: function () {
            return window.API.mtColDelete(self.tid, col.id).then(function () {
              toast('列已删除');
              return self.reload();
            });
          }
        });
      },
      /* ---- 行编辑（登录用户） ---- */
      openNewRow: function () {
        var d = {};
        this.columns.forEach(function (c) {
          d[c.key] = c.type === 'checkbox' ? false : (c.type === 'multiselect' ? [] : '');
        });
        this.er = { id: null, ver: null, data: d };
        this.dlg = 'editRow';
      },
      openEditRow: function (row) {
        var d = {};
        this.columns.forEach(function (c) {
          var v = (row.data || {})[c.key];
          d[c.key] = v === undefined || v === null
            ? (c.type === 'checkbox' ? false : (c.type === 'multiselect' ? [] : ''))
            : v;
        });
        this.er = { id: row.id, ver: row.ver, data: d };
        this.dlg = 'editRow';
      },
      submitRow: async function () {
        this.busy = true;
        try {
          if (this.er.id) {
            await window.API.mtRowUpdate(this.tid, this.er.id, { data: this.er.data, ver: this.er.ver });
            toast('已保存');
          } else {
            await window.API.mtRowCreate(this.tid, { data: this.er.data });
            toast('已添加');
          }
          this.dlg = '';
          await this.loadRows();
        } catch (e) {
          var msg = this.errMsg(e);
          // 乐观锁冲突：不静默，明确提示刷新
          if (msg.indexOf('版本') >= 0 || msg.indexOf('刷新') >= 0) {
            toast('保存失败：这行已被别人改过，请关闭后刷新');
          } else {
            toast('保存失败：' + msg);
          }
        } finally { this.busy = false; }
      },
      deleteRow: function (row) {
        var self = this;
        window.confirmModal({
          title: '删除这一行？',
          msg: '删除后可在变动历史里查到记录，但行本身不再显示。',
          okText: '删除', cancelText: '取消',
          onOk: function () {
            return window.API.mtRowDelete(self.tid, row.id).then(function () {
              toast('已删除');
              return self.loadRows();
            });
          }
        });
      },
      /* ---- 图片上传（编辑行弹窗里，image 列） ---- */
      mtUpload: async function (ev, key) {
        var f = ev.target && ev.target.files && ev.target.files[0];
        if (!f) return;
        var fd = new FormData();
        fd.append('file', f);
        try {
          var token = localStorage.getItem('mt_token');
          var res = await fetch('/api/mtables/upload', {
            method: 'POST', body: fd,
            headers: token ? { 'Authorization': 'Bearer ' + token } : {}
          });
          if (!res.ok) {
            var t = await res.text().catch(function () { return ''; });
            toast('上传失败：' + t);
            return;
          }
          var r = await res.json();
          var cur = String(this.er.data[key] || '').split(/[,，;；\s]+/).filter(function (x) { return x; });
          cur.push(r.url);
          this.er.data[key] = cur.join(',');
          toast('图片已上传');
        } catch (e) {
          toast('上传失败：网络错误');
        }
        ev.target.value = '';
      },
      /* ---- 用户管理（仅 admin） ---- */
      openUsers: async function () {
        this.dlg = 'users';
        this.nu = { username: '', password: '', real_name: '' };
        this.rp = { uid: null, password: '' };
        await this.loadUsers();
      },
      loadUsers: async function () {
        try {
          var d = await window.API.mtUsers();
          this.users = d.rows || [];
        } catch (e) { toast('读取用户失败：' + this.errMsg(e)); }
      },
      submitUser: async function () {
        var u = (this.nu.username || '').trim();
        if (!u) { toast('填账号'); return; }
        if ((this.nu.password || '').length < 6) { toast('密码至少 6 位'); return; }
        this.busy = true;
        try {
          await window.API.mtUserCreate({
            username: u, password: this.nu.password, real_name: (this.nu.real_name || '').trim()
          });
          toast('已添加用户 ' + u + '（可编辑全部表格）');
          this.nu = { username: '', password: '', real_name: '' };
          await this.loadUsers();
        } catch (e) { toast('添加失败：' + this.errMsg(e)); }
        finally { this.busy = false; }
      },
      toggleUser: function (u) {
        var self = this;
        var to = u.status ? 0 : 1;
        window.confirmModal({
          title: (u.status ? '停用 ' : '启用 ') + u.username + '？',
          msg: u.status ? '停用后该账号立刻被踢下线，无法登录（数据保留）。' : '启用后该账号可重新登录编辑。',
          okText: u.status ? '停用' : '启用', cancelText: '取消', danger: !!u.status,
          onOk: function () {
            return window.API.mtUserPatch(u.id, { status: to }).then(function () {
              toast(u.status ? '已停用' : '已启用');
              return self.loadUsers();
            });
          }
        });
      },
      startResetPw: function (u) { this.rp = { uid: u.id, password: '' }; },
      submitResetPw: async function () {
        if ((this.rp.password || '').length < 6) { toast('新密码至少 6 位'); return; }
        this.busy = true;
        try {
          await window.API.mtUserPatch(this.rp.uid, { password: this.rp.password });
          toast('密码已重置（该账号需重新登录）');
          this.rp = { uid: null, password: '' };
        } catch (e) { toast('重置失败：' + this.errMsg(e)); }
        finally { this.busy = false; }
      },
      delUser: function (u) {
        var self = this;
        window.confirmModal({
          title: '删除用户 ' + u.username + '？',
          msg: '删除后该账号无法登录；他名下的历史改动记录（变动历史里）仍保留。',
          okText: '删除', cancelText: '取消',
          onOk: function () {
            return window.API.mtUserDelete(u.id).then(function () {
              toast('已删除');
              return self.loadUsers();
            });
          }
        });
      },
      roleLabel: function (r) { return r === 'admin' ? '管理员' : '可编辑'; },
      /* ---- HTTP 写入密钥（仅 admin） ---- */
      openKeys: async function () {
        this.dlg = 'apikeys';
        this.newKeyNote = '';
        this.lastKey = '';
        await this.loadApiKeys();
      },
      loadApiKeys: async function () {
        try {
          var d = await window.API.mtApiKeys(this.tid);
          this.apikeys = d.rows || [];
        } catch (e) { this.apikeys = []; }
      },
      genApiKey: async function () {
        this.busy = true;
        try {
          var r = await window.API.mtApiKeyCreate(this.tid, { note: this.newKeyNote || '' });
          this.lastKey = r.key;
          this.newKeyNote = '';
          await this.loadApiKeys();
        } catch (e) { toast('生成失败：' + this.errMsg(e)); }
        finally { this.busy = false; }
      },
      copyKey: function () {
        navigator.clipboard.writeText(this.lastKey).then(function () { toast('已复制'); },
          function () { toast('复制失败，请手动选中复制'); });
      },
      revokeApiKey: function (k) {
        var self = this;
        window.confirmModal({
          title: '作废密钥 ' + k.key_prefix + '…？',
          msg: '作废后用这把密钥的 HTTP 写入会立刻失败，不可恢复。',
          okText: '作废', cancelText: '取消',
          onOk: function () {
            return window.API.mtApiKeyRevoke(self.tid, k.id).then(function () {
              toast('已作废');
              return self.loadApiKeys();
            });
          }
        });
      },
      /* ---- 数据库视图（仅 admin） ---- */
      openDbView: async function () {
        this.dlg = 'dbview';
        try {
          var d = await window.API.mtDbView(this.tid);
          this.dbv = d || { exists: false, name: '', columns: [] };
        } catch (e) { toast('读取视图状态失败：' + this.errMsg(e)); }
      },
      genDbView: async function () {
        this.busy = true;
        try {
          var r = await window.API.mtDbViewCreate(this.tid);
          toast('视图已生成：' + r.name);
          this.dbv.exists = true;
          this.dbv.name = r.name;
        } catch (e) { toast('生成失败：' + this.errMsg(e)); }
        finally { this.busy = false; }
      },
      dropDbView: function () {
        var self = this;
        window.confirmModal({
          title: '删除视图 ' + this.dbv.name + '？',
          msg: '多维表本身和数据不受影响，只是数据库里不能再 SELECT 这张视图。',
          okText: '删除', cancelText: '取消',
          onOk: function () {
            return window.API.mtDbViewDrop(self.tid).then(function () {
              toast('视图已删除');
              self.dbv.exists = false;
            });
          }
        });
      },
      /* ---- 变动历史（登录用户可看） ---- */
      openAudit: async function () {
        this.dlg = 'audit';
        try {
          var d = await window.API.mtAudit(this.tid, { limit: 200 });
          this.audits = d.rows || [];
        } catch (e) { toast('读取变动历史失败：' + this.errMsg(e)); }
      },
      actionLabel: function (a) {
        var m = {
          create_table: '建表', delete_table: '删表',
          create_column: '加列', update_column: '改列', delete_column: '删列',
          create_row: '新增行', update_row: '修改行', delete_row: '删除行',
          import_rows: '导入数据', api_insert: 'API写入',
          user_create: '添加用户', user_update: '改用户', user_delete: '删用户',
          apikey_create: '生成密钥', apikey_revoke: '作废密钥',
          dbview_create: '生成视图', dbview_drop: '删除视图'
        };
        return m[a] || a;
      },
      detailText: function (d) {
        if (!d) return '';
        try {
          var ks = Object.keys(d);
          if (!ks.length) return '';
          return ks.map(function (k) {
            var v = d[k];
            if (v && typeof v === 'object' && ('old' in v || 'new' in v)) {
              var o = v.old === null || v.old === undefined ? '空' : JSON.stringify(v.old);
              var n = v.new === null || v.new === undefined ? '空' : JSON.stringify(v.new);
              return k + ': ' + o + ' → ' + n;
            }
            return k + ': ' + JSON.stringify(v);
          }).join('；');
        } catch (e) { return ''; }
      }
    },
    template:
      '<div>' +
        /* 顶部标题（身份/登录按钮在右上角浮动胶囊里） */
        '<div class="page-head">' +
          '<div class="page-title">多维表格' +
            '<small>游客可看 · 登录可改 · 改动全留痕</small>' +
          '</div>' +
        '</div>' +

        '<div v-if="booting" class="empty">加载中…</div>' +

        /* ============ 表列表 ============ */
        '<div v-else-if="!tid">' +
          '<div class="card" style="margin-bottom:14px">' +
            '<div class="filters" style="flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:0">' +
              '<button v-if="isAdmin" type="button" class="btn btn-primary btn-sm" @click="openNewTable">新建表格</button>' +
              '<button v-if="isAdmin" type="button" class="btn btn-sm" @click="openImport">导入 Excel</button>' +
              '<button v-if="isAdmin" type="button" class="btn btn-sm" @click="openUsers">用户管理</button>' +
              '<span class="quad-note">共 {{ tables.length }} 张表' +
                '<template v-if="!isLoggedIn">（游客只读，登录后可编辑）</template>' +
              '</span>' +
            '</div>' +
          '</div>' +
          '<div class="card">' +
            '<table class="tbl" v-if="tables.length">' +
              '<thead><tr>' +
                '<th>表名</th><th>说明</th><th>列数</th><th>行数</th>' +
                '<th>我的权限</th><th>创建人</th><th>创建时间</th><th>操作</th>' +
              '</tr></thead>' +
              '<tbody>' +
                '<tr v-for="t in tables" :key="t.id">' +
                  '<td><b>{{ t.name }}</b></td>' +
                  '<td><span class="quad-note">{{ t.note || "—" }}</span></td>' +
                  '<td>{{ t.col_count }}</td>' +
                  '<td>{{ t.row_count }}<template v-if="t.heavy"> ' +
                    '<span style="color:var(--orange)" title="行数较大：已可做实体表迁移，找管理员处理">⚠</span></template></td>' +
                  '<td><span class="tag" :class="t.can_edit ? \'tag-blue\' : \'tag-purple\'">' +
                    '{{ t.can_edit ? "可编辑" : "只读" }}</span></td>' +
                  '<td>{{ t.created_by || "—" }}</td>' +
                  '<td><span class="quad-note">{{ t.created_at }}</span></td>' +
                  '<td>' +
                    '<button type="button" class="btn btn-sm btn-link" @click="openTable(t)">打开</button>' +
                    '<button v-if="isAdmin" type="button" class="btn btn-sm btn-link" @click="deleteTable(t)">删除</button>' +
                  '</td>' +
                '</tr>' +
              '</tbody>' +
            '</table>' +
            '<div class="empty" v-if="!tables.length">' +
              '{{ emptyListText }}' +
            '</div>' +
          '</div>' +
        '</div>' +

        /* ============ 打开某张表 ============ */
        '<div v-else>' +
          '<div class="card" style="margin-bottom:14px">' +
            '<div class="filters" style="flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:0">' +
              '<button type="button" class="btn btn-sm btn-link" @click="backToList">← 返回列表</button>' +
              '<b style="font-size:var(--fs-title)">{{ table ? table.name : "" }}</b>' +
              '<span class="tag" :class="canEdit ? \'tag-blue\' : \'tag-purple\'">' +
                '{{ canEdit ? "可编辑" : "只读" }}</span>' +
              '<input v-model="keyword" class="ctl" style="min-width:180px" ' +
                     'placeholder="搜索本表内容…" @keydown.enter="search">' +
              '<button type="button" class="btn btn-sm" @click="search">搜索</button>' +
              '<button v-if="canEdit" type="button" class="btn btn-primary btn-sm" @click="openNewRow">新增行</button>' +
              '<button v-if="isLoggedIn" type="button" class="btn btn-sm" @click="openAudit">变动历史</button>' +
              '<button v-if="isAdmin" type="button" class="btn btn-sm" @click="openNewCol">加列</button>' +
              '<button v-if="isAdmin" type="button" class="btn btn-sm" @click="openKeys">写入密钥</button>' +
              '<button v-if="isAdmin" type="button" class="btn btn-sm" @click="openDbView">DB视图</button>' +
              '<span class="quad-note" style="margin-left:auto">共 {{ total }} 行</span>' +
              '<span v-if="total >= 100000" style="color:var(--orange)">⚠ 行数较大，查询变慢请找管理员做实体表迁移</span>' +
            '</div>' +
          '</div>' +

          '<div class="card">' +
            '<div v-if="loadingRows" class="empty">读取中…</div>' +
            '<table class="tbl" v-else-if="rows.length">' +
              '<thead><tr>' +
                '<th v-for="c in columns" :key="c.key">{{ c.label }}' +
                  '<span v-if="c.required" style="color:#e5484d"> *</span>' +
                  '<button v-if="isAdmin" type="button" class="btn btn-sm btn-link" ' +
                          '@click="openEditCol(c)" title="改这一列">改列</button>' +
                '</th>' +
                '<th>最后修改</th>' +
                '<th>操作</th>' +
              '</tr></thead>' +
              '<tbody>' +
                '<tr v-for="r in rows" :key="r.id">' +
                  '<td v-for="c in columns" :key="c.key" class="mt-cell" ' +
                      ':class="{ \'mt-editing\': editing && editing.rid === r.id && editing.key === c.key }" ' +
                      ':title="canEdit ? \'双击编辑\' : \'\'" @dblclick="startEdit(r, c)">' +
                    /* 内容永远渲染（编辑时也不拿掉）——列宽/行高稳定，表格绝不跳动 */
                    '<input v-if="canEdit && c.type === \'checkbox\'" type="checkbox" ' +
                           ':checked="cellBool(r, c)" @change="toggleCell(r, c, $event)">' +
                    '<div v-else-if="c.type === \'image\' && imgList(c, r).length" class="mt-imgs">' +
                      '<span v-for="(u, i) in imgList(c, r)" :key="i" class="mt-img-btn" ' +
                            'title="点击放大查看（Esc 关闭）" @click="openViewer(imgList(c, r), i)">' +
                        '<img v-if="!imgErr[u]" :key="u + (imgNonce[u] || 0)" :src="u" loading="lazy" ' +
                             'referrerpolicy="no-referrer" @error="imgFail(u)">' +
                        '<span v-else class="quad-note">图挂了</span>' +
                      '</span>' +
                    '</div>' +
                    '<template v-else-if="c.type === \'image\'"><span class="quad-note">—</span></template>' +
                    '<template v-else>{{ cellText(c, r) }}</template>' +

                    /* 编辑浮层：盖在内容上（绝对定位），不参与表格布局 */
                    '<div v-if="editing && editing.rid === r.id && editing.key === c.key && ' +
                            'c.type !== \'checkbox\' && c.type !== \'multiselect\'" ' +
                         'class="mt-editor" @dblclick.stop>' +
                      '<input v-focus class="mt-editor-ipt" ' +
                             ':type="c.type === \'number\' ? \'number\' : (c.type === \'date\' ? \'date\' : \'text\')" ' +
                             'v-model="editVal" ' +
                             '@keyup.enter="commitCell(r, c)" @blur="commitCell(r, c)" @keydown.esc="cancelEdit">' +
                    '</div>' +
                    '<select v-else-if="editing && editing.rid === r.id && editing.key === c.key && c.type === \'select\'" ' +
                            'v-focus class="mt-editor mt-editor-sel" v-model="editVal" ' +
                            '@change="commitCell(r, c)" @blur="commitCell(r, c)" @keydown.esc="cancelEdit">' +
                      '<option value="">（空）</option>' +
                      '<option v-for="o in (c.options || [])" :key="o" :value="o">{{ o }}</option>' +
                    '</select>' +
                    '<div v-else-if="editing && editing.rid === r.id && editing.key === c.key && c.type === \'multiselect\'" ' +
                         'class="mt-editor-pop">' +
                      '<label v-for="o in (c.options || [])" :key="o">' +
                        '<input type="checkbox" :value="o" v-model="editVal" @change="commitCell(r, c, true)">{{ o }}</label>' +
                      '<div class="quad-note">点空白处收起</div>' +
                    '</div>' +
                  '</td>' +
                  '<td><span class="quad-note">{{ r.updated_by || r.created_by }} · {{ r.updated_at }}</span></td>' +
                  '<td>' +
                    '<button v-if="canEdit" type="button" class="btn btn-sm btn-link" @click="openEditRow(r)">编辑</button>' +
                    '<button v-if="canEdit" type="button" class="btn btn-sm btn-link" @click="deleteRow(r)">删除</button>' +
                    '<span v-if="!canEdit" class="quad-note">只读</span>' +
                  '</td>' +
                '</tr>' +
              '</tbody>' +
            '</table>' +
            '<div class="empty" v-else-if="!columns.length">这张表还没有列，找管理员加列</div>' +
            '<div class="empty" v-else>{{ emptyRowsText }}</div>' +

            /* 分页 */
            '<div v-if="pageCount > 1" class="filters" style="justify-content:center;gap:8px;margin-top:12px;margin-bottom:0">' +
              '<button type="button" class="btn btn-sm" @click="goPage(page - 1)" :disabled="page <= 1">上一页</button>' +
              '<span class="quad-note">{{ page }} / {{ pageCount }}</span>' +
              '<button type="button" class="btn btn-sm" @click="goPage(page + 1)" :disabled="page >= pageCount">下一页</button>' +
            '</div>' +
          '</div>' +
        '</div>' +

        /* ============ 弹窗：新建表 ============ */
        '<div v-if="dlg === \'newTable\'" class="modal-mask" @click.self="dlg = \'\'">' +
          '<div class="modal" style="max-width:640px">' +
            '<div class="modal-head"><b>新建表格</b>' +
              '<button type="button" class="btn btn-sm btn-link" @click="dlg = \'\'">×</button></div>' +
            '<div class="modal-body" style="display:flex;flex-direction:column;gap:10px">' +
              '<label class="quad-note">表名</label>' +
              '<input v-model="nt.name" class="ctl" maxlength="120" placeholder="例如：采购到货登记">' +
              '<label class="quad-note">说明（可空）</label>' +
              '<input v-model="nt.note" class="ctl" maxlength="500" placeholder="这张表干什么用的">' +
              '<label class="quad-note">列（至少一列）</label>' +
              '<div v-for="(c, i) in nt.cols" :key="i" style="display:flex;gap:6px;align-items:center">' +
                '<input v-model="c.label" class="ctl" style="flex:1" placeholder="列名">' +
                '<select v-model="c.type" class="ctl" style="width:98px">' +
                  '<option v-for="t in colTypes" :key="t" :value="t">{{ typeLabel[t] }}</option>' +
                '</select>' +
                '<label class="quad-note" style="display:inline-flex;align-items:center;gap:3px">' +
                  '<input type="checkbox" v-model="c.required">必填</label>' +
                '<button type="button" class="btn btn-sm btn-link" @click="delNtCol(i)">删</button>' +
              '</div>' +
              '<button type="button" class="btn btn-sm" @click="addNtCol">+ 再加一列</button>' +
              '<div class="quad-note">单选/多选的选项可在建表后点列头的「改列」设置</div>' +
            '</div>' +
            '<div class="modal-foot">' +
              '<button type="button" class="btn btn-sm" @click="dlg = \'\'">取消</button>' +
              '<button type="button" class="btn btn-primary btn-sm" :disabled="busy" @click="submitNewTable">建表</button>' +
            '</div>' +
          '</div>' +
        '</div>' +

        /* ============ 弹窗：Excel 导入 ============ */
        '<div v-if="dlg === \'import\'" class="modal-mask" @click.self="dlg = \'\'">' +
          '<div class="modal" style="max-width:520px">' +
            '<div class="modal-head"><b>导入 Excel 建新表</b>' +
              '<button type="button" class="btn btn-sm btn-link" @click="dlg = \'\'">×</button></div>' +
            '<div class="modal-body" style="display:flex;flex-direction:column;gap:10px">' +
              '<label class="quad-note">选择 .xlsx 文件</label>' +
              '<input type="file" class="ctl" accept=".xlsx" @change="onImportFile">' +
              '<label class="quad-note">表名</label>' +
              '<input v-model="imp.name" class="ctl" maxlength="120" placeholder="默认用文件名">' +
              '<label class="quad-note">说明（可空）</label>' +
              '<input v-model="imp.note" class="ctl" maxlength="500" placeholder="这张表干什么用的">' +
              '<div class="quad-note">取第一个工作表；第一行当列名；自动识别 数字/日期/勾选/文本 列，' +
                '导完可点列头「改列」调整；上限 1 万行 / 10MB。</div>' +
            '</div>' +
            '<div class="modal-foot">' +
              '<button type="button" class="btn btn-sm" @click="dlg = \'\'">取消</button>' +
              '<button type="button" class="btn btn-primary btn-sm" :disabled="busy || !imp.fileB64" @click="submitImport">导入</button>' +
            '</div>' +
          '</div>' +
        '</div>' +

        /* ============ 弹窗：列 ============ */
        '<div v-if="dlg === \'newCol\' || dlg === \'editCol\'" class="modal-mask" @click.self="dlg = \'\'">' +
          '<div class="modal" style="max-width:520px">' +
            '<div class="modal-head"><b>{{ nc.id ? "修改列" : "添加列" }}</b>' +
              '<button type="button" class="btn btn-sm btn-link" @click="dlg = \'\'">×</button></div>' +
            '<div class="modal-body" style="display:flex;flex-direction:column;gap:10px">' +
              '<label class="quad-note">列名</label>' +
              '<input v-model="nc.label" class="ctl" maxlength="120" placeholder="列名">' +
              '<label class="quad-note">类型</label>' +
              '<select v-model="nc.type" class="ctl">' +
                '<option v-for="t in colTypes" :key="t" :value="t">{{ typeLabel[t] }}</option>' +
              '</select>' +
              '<template v-if="needOptions()">' +
                '<label class="quad-note">可选项（用 、 或逗号分隔）</label>' +
                '<input v-model="ncOptText" class="ctl" placeholder="待处理、进行中、已完成">' +
              '</template>' +
              '<label class="quad-note" style="display:inline-flex;align-items:center;gap:5px">' +
                '<input type="checkbox" v-model="nc.required"> 必填</label>' +
              '<div v-if="nc.id">' +
                '<button type="button" class="btn btn-sm btn-link" @click="deleteCol({id: nc.id, label: nc.label})">删除这一列</button>' +
              '</div>' +
            '</div>' +
            '<div class="modal-foot">' +
              '<button type="button" class="btn btn-sm" @click="dlg = \'\'">取消</button>' +
              '<button type="button" class="btn btn-primary btn-sm" :disabled="busy" @click="submitCol">保存</button>' +
            '</div>' +
          '</div>' +
        '</div>' +

        /* ============ 弹窗：行编辑 ============ */
        '<div v-if="dlg === \'editRow\'" class="modal-mask" @click.self="dlg = \'\'">' +
          '<div class="modal" style="max-width:560px">' +
            '<div class="modal-head"><b>{{ er.id ? "编辑行" : "新增行" }}</b>' +
              '<button type="button" class="btn btn-sm btn-link" @click="dlg = \'\'">×</button></div>' +
            '<div class="modal-body" style="display:flex;flex-direction:column;gap:10px">' +
              '<template v-for="c in columns" :key="c.key">' +
                '<label class="quad-note">{{ c.label }}' +
                  '<span v-if="c.required" style="color:#e5484d"> *</span></label>' +
                '<input v-if="c.type === \'text\' || c.type === \'user\'" v-model="er.data[c.key]" class="ctl">' +
                '<div v-else-if="c.type === \'image\'" style="display:flex;gap:6px;align-items:center">' +
                  '<input v-model="er.data[c.key]" class="ctl" style="flex:1" placeholder="图片URL，多个用逗号分隔">' +
                  '<label class="btn btn-sm" style="margin:0">上传图片' +
                    '<input type="file" accept=".jpg,.jpeg,.png,.webp,.gif" hidden @change="mtUpload($event, c.key)"></label>' +
                '</div>' +
                '<input v-else-if="c.type === \'number\'" v-model="er.data[c.key]" class="ctl" type="number">' +
                '<input v-else-if="c.type === \'date\'" v-model="er.data[c.key]" class="ctl" type="date">' +
                '<select v-else-if="c.type === \'select\'" v-model="er.data[c.key]" class="ctl">' +
                  '<option value="">（空）</option>' +
                  '<option v-for="o in (c.options || [])" :key="o" :value="o">{{ o }}</option>' +
                '</select>' +
                '<select v-else-if="c.type === \'multiselect\'" v-model="er.data[c.key]" class="ctl" multiple size="4">' +
                  '<option v-for="o in (c.options || [])" :key="o" :value="o">{{ o }}</option>' +
                '</select>' +
                '<label v-else-if="c.type === \'checkbox\'" class="quad-note" style="display:inline-flex;align-items:center;gap:5px">' +
                  '<input type="checkbox" v-model="er.data[c.key]"> 是</label>' +
                '<input v-else v-model="er.data[c.key]" class="ctl">' +
              '</template>' +
              '<div v-if="er.id" class="quad-note">乐观锁版本 {{ er.ver }}：别人改了这行，保存会被拒绝</div>' +
            '</div>' +
            '<div class="modal-foot">' +
              '<button type="button" class="btn btn-sm" @click="dlg = \'\'">取消</button>' +
              '<button type="button" class="btn btn-primary btn-sm" :disabled="busy" @click="submitRow">保存</button>' +
            '</div>' +
          '</div>' +
        '</div>' +

        /* ============ 弹窗：用户管理（admin） ============ */
        '<div v-if="dlg === \'users\'" class="modal-mask" @click.self="dlg = \'\'">' +
          '<div class="modal" style="max-width:720px">' +
            '<div class="modal-head"><b>用户管理</b>' +
              '<button type="button" class="btn btn-sm btn-link" @click="dlg = \'\'">×</button></div>' +
            '<div class="modal-body" style="display:flex;flex-direction:column;gap:12px;max-height:70vh;overflow:auto">' +
              '<div class="quad-note">只有管理员能在这里添加/停用/删除用户。添加的用户可编辑全部表格、查看变动历史；' +
                '未登录的游客只能查看。改密码或停用会立刻让该账号下线。</div>' +
              '<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;border:1px solid var(--border);border-radius:var(--radius-sm);padding:10px">' +
                '<input v-model="nu.username" class="ctl" style="width:140px" maxlength="64" placeholder="账号（登录名）">' +
                '<input v-model="nu.password" class="ctl" style="width:140px" type="text" placeholder="密码 ≥6 位" autocomplete="off">' +
                '<input v-model="nu.real_name" class="ctl" style="width:120px" maxlength="64" placeholder="姓名（可空）">' +
                '<button type="button" class="btn btn-primary btn-sm" :disabled="busy" @click="submitUser">添加用户</button>' +
              '</div>' +
              '<table class="tbl" v-if="users.length">' +
                '<thead><tr><th>账号</th><th>姓名</th><th>角色</th><th>状态</th><th>创建人</th><th>创建时间</th><th>操作</th></tr></thead>' +
                '<tbody>' +
                  '<tr v-for="u in users" :key="u.id">' +
                    '<td><b>{{ u.username }}</b></td>' +
                    '<td>{{ u.real_name || "—" }}</td>' +
                    '<td><span class="tag" :class="u.role === \'admin\' ? \'tag-blue\' : \'tag-green\'">{{ roleLabel(u.role) }}</span></td>' +
                    '<td><span class="tag" :class="u.status ? \'tag-blue\' : \'tag-purple\'">{{ u.status ? "启用" : "停用" }}</span></td>' +
                    '<td>{{ u.created_by || "—" }}</td>' +
                    '<td><span class="quad-note">{{ u.created_at }}</span></td>' +
                    '<td style="white-space:nowrap">' +
                      '<button type="button" class="btn btn-sm btn-link" @click="startResetPw(u)">重置密码</button>' +
                      '<button v-if="u.role !== \'admin\'" type="button" class="btn btn-sm btn-link" @click="toggleUser(u)">' +
                        '{{ u.status ? "停用" : "启用" }}</button>' +
                      '<button v-if="u.role !== \'admin\'" type="button" class="btn btn-sm btn-link" @click="delUser(u)">删除</button>' +
                    '</td>' +
                  '</tr>' +
                  '<tr v-if="rp.uid">' +
                    '<td :colspan="7" style="background:var(--panel-2)">' +
                      '<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">' +
                        '<span class="quad-note">给 {{ (users.filter(x => x.id === rp.uid)[0] || {}).username }} 设新密码：</span>' +
                        '<input v-model="rp.password" class="ctl" style="width:150px" type="text" placeholder="新密码 ≥6 位" autocomplete="off">' +
                        '<button type="button" class="btn btn-primary btn-sm" :disabled="busy" @click="submitResetPw">确定</button>' +
                        '<button type="button" class="btn btn-sm" @click="rp = { uid: null, password: \'\' }">取消</button>' +
                      '</div>' +
                    '</td>' +
                  '</tr>' +
                '</tbody>' +
              '</table>' +
            '</div>' +
            '<div class="modal-foot"><button type="button" class="btn btn-sm" @click="dlg = \'\'">关闭</button></div>' +
          '</div>' +
        '</div>' +

        /* ============ 弹窗：HTTP 写入密钥（admin） ============ */
        '<div v-if="dlg === \'apikeys\'" class="modal-mask" @click.self="dlg = \'\'">' +
          '<div class="modal" style="max-width:600px">' +
            '<div class="modal-head"><b>HTTP 写入密钥 · {{ table ? table.name : "" }}</b>' +
              '<button type="button" class="btn btn-sm btn-link" @click="dlg = \'\'">×</button></div>' +
            '<div class="modal-body" style="display:flex;flex-direction:column;gap:10px">' +
              '<div class="quad-note">给影刀 / 脚本免登录插数据用（只写不读）。</div>' +
              '<div style="display:flex;gap:6px;align-items:center">' +
                '<input v-model="newKeyNote" class="ctl" style="flex:1" maxlength="120" ' +
                       'placeholder="用途备注，例如：影刀到货登记机器人">' +
                '<button type="button" class="btn btn-primary btn-sm" :disabled="busy" @click="genApiKey">生成密钥</button>' +
              '</div>' +
              '<div v-if="lastKey" style="border:1px solid var(--orange);border-radius:var(--radius-sm);padding:8px 10px">' +
                '<div class="quad-note">密钥只显示这一次，请立即复制保存：</div>' +
                '<div style="display:flex;gap:8px;align-items:center">' +
                  '<b style="font-family:var(--font-num);user-select:all;word-break:break-all">{{ lastKey }}</b>' +
                  '<button type="button" class="btn btn-sm" @click="copyKey">复制</button>' +
                '</div>' +
              '</div>' +
              '<table class="tbl" v-if="apikeys.length">' +
                '<thead><tr><th>密钥</th><th>用途</th><th>生成人</th><th>时间</th><th>状态</th><th>操作</th></tr></thead>' +
                '<tbody>' +
                  '<tr v-for="k in apikeys" :key="k.id">' +
                    '<td><span style="font-family:var(--font-num)">{{ k.key_prefix }}…</span></td>' +
                    '<td>{{ k.note || "—" }}</td>' +
                    '<td>{{ k.created_by || "—" }}</td>' +
                    '<td><span class="quad-note">{{ k.created_at }}</span></td>' +
                    '<td><span class="tag" :class="k.revoked ? \'tag-purple\' : \'tag-blue\'">' +
                      '{{ k.revoked ? "已作废" : "有效" }}</span></td>' +
                    '<td><button v-if="!k.revoked" type="button" class="btn btn-sm btn-link" ' +
                            '@click="revokeApiKey(k)">作废</button></td>' +
                  '</tr>' +
                '</tbody>' +
              '</table>' +
              '<div class="quad-note">写入用法（免登录）：POST /api/mtables/in/{{ tid }} ，' +
                '请求头 X-MT-Token: 密钥；body 单行 {"data":{列名:值}} 或批量 ' +
                '{"rows":[{...},...]}（单次 ≤ 1000 行）。</div>' +
            '</div>' +
            '<div class="modal-foot"><button type="button" class="btn btn-sm" @click="dlg = \'\'">关闭</button></div>' +
          '</div>' +
        '</div>' +

        /* ============ 弹窗：数据库视图 ============ */
        '<div v-if="dlg === \'dbview\'" class="modal-mask" @click.self="dlg = \'\'">' +
          '<div class="modal" style="max-width:560px">' +
            '<div class="modal-head"><b>数据库视图 · {{ table ? table.name : "" }}</b>' +
              '<button type="button" class="btn btn-sm btn-link" @click="dlg = \'\'">×</button></div>' +
            '<div class="modal-body" style="display:flex;flex-direction:column;gap:10px">' +
              '<div class="quad-note">在业务库生成实时视图 <b style="font-family:var(--font-num)">{{ dbv.name }}</b>' +
                '，Navicat / 影刀 SQL / BI 直接 SELECT 即可。视图是活的镜像：' +
                '表里增改数据立刻可见，加列/改列/删列会自动重建，无需任何同步操作。</div>' +
              '<div style="display:flex;gap:8px;align-items:center">' +
                '<span class="tag" :class="dbv.exists ? \'tag-blue\' : \'tag-purple\'">{{ dbv.exists ? "已生成" : "未生成" }}</span>' +
                '<button type="button" class="btn btn-primary btn-sm" :disabled="busy" @click="genDbView">' +
                  '{{ dbv.exists ? "重建视图（按当前列）" : "生成视图" }}</button>' +
                '<button v-if="dbv.exists" type="button" class="btn btn-sm" @click="dropDbView">删除视图</button>' +
              '</div>' +
              '<div v-if="dbv.exists" class="quad-note">查询示例：</div>' +
              '<div v-if="dbv.exists" style="font-family:var(--font-num);background:var(--panel-2);border-radius:var(--radius-sm);padding:8px 10px;word-break:break-all">SELECT * FROM `{{ dbv.name }}` LIMIT 20;</div>' +
              '<div class="quad-note">视图列 = 系统列（id/sort/ver/创建人/修改人/时间）+ 你的业务列；' +
                '数字/勾选列出数值，文本列出字符串。</div>' +
            '</div>' +
            '<div class="modal-foot"><button type="button" class="btn btn-sm" @click="dlg = \'\'">关闭</button></div>' +
          '</div>' +
        '</div>' +

        /* ============ 弹窗：变动历史 ============ */
        '<div v-if="dlg === \'audit\'" class="modal-mask" @click.self="dlg = \'\'">' +
          '<div class="modal" style="max-width:860px">' +
            '<div class="modal-head"><b>变动历史 · {{ table ? table.name : "" }}</b>' +
              '<button type="button" class="btn btn-sm btn-link" @click="dlg = \'\'">×</button></div>' +
            '<div class="modal-body" style="max-height:60vh;overflow:auto">' +
              '<table class="tbl" v-if="audits.length">' +
                '<thead><tr><th>时间</th><th>人</th><th>动作</th><th>行</th><th>明细（旧值 → 新值）</th></tr></thead>' +
                '<tbody>' +
                  '<tr v-for="a in audits" :key="a.id">' +
                    '<td><span class="quad-note">{{ a.created_at }}</span></td>' +
                    '<td>{{ a.actor || "—" }}</td>' +
                    '<td><span class="tag tag-blue">{{ actionLabel(a.action) }}</span></td>' +
                    '<td><span class="quad-note">{{ a.row_id || "—" }}</span></td>' +
                    '<td style="font-size:var(--fs-sm)">{{ detailText(a.detail) }}</td>' +
                  '</tr>' +
                '</tbody>' +
              '</table>' +
              '<div class="empty" v-if="!audits.length">还没有变更记录</div>' +
              '<div class="quad-note" style="margin-top:8px">最近 200 条。所有增删改（含影刀 API 写入）都会记录在这里。</div>' +
            '</div>' +
            '<div class="modal-foot"><button type="button" class="btn btn-sm" @click="dlg = \'\'">关闭</button></div>' +
          '</div>' +
        '</div>' +

        /* ============ 图片查看弹窗（页内放大，最高层） ============ */
        '<div v-if="viewer.urls.length" class="mt-viewer" @click.self="closeViewer">' +
          '<button type="button" class="mt-viewer-x" title="关闭（Esc）" @click="closeViewer">×</button>' +
          '<button v-if="viewer.urls.length > 1" type="button" class="mt-viewer-nav mt-viewer-prev" ' +
                  'title="上一张（←）" @click.stop="vPrev">‹</button>' +
          '<div class="mt-viewer-stage">' +
            '<img v-if="!viewer.err" :key="viewer.urls[viewer.idx] + \'#\' + viewer.idx" ' +
                 ':src="viewer.urls[viewer.idx]" referrerpolicy="no-referrer" @error="vErr">' +
            '<div v-else class="mt-viewer-err">' +
              '<div>图片加载失败</div>' +
              '<div class="quad-note" style="word-break:break-all;max-width:60vw">{{ viewer.urls[viewer.idx] }}</div>' +
            '</div>' +
          '</div>' +
          '<button v-if="viewer.urls.length > 1" type="button" class="mt-viewer-nav mt-viewer-next" ' +
                  'title="下一张（→）" @click.stop="vNext">›</button>' +
          '<div v-if="viewer.urls.length > 1" class="mt-viewer-cap">{{ viewer.idx + 1 }} / {{ viewer.urls.length }}</div>' +
        '</div>' +
      '</div>'
  };
  window.mountPageApp(cmp, container);
}

/* ---- 原生回退（VUE_ALL=false 时用，只读提示版） ---- */
function renderMtablesLegacy(container) {
  container.innerHTML =
    '<div class="page-head"><div class="page-title">多维表格' +
    '<small>游客可看 · 登录可改 · 改动全留痕</small></div></div>' +
    '<div class="card"><div class="empty">多维表格需要 Vue 运行时。' +
    '请检查 index.html 里的 js/vendor/vue.global.prod.js 是否加载成功。</div></div>';
}
