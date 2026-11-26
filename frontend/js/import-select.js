/* import-select.js — index.html 内联脚本拆出的第 12/12 段（原第 7438-8163 行）。
   加载顺序见 index.html 底部；全部是全局函数，跨文件可直接调用。 */
/* ==================== Excel / CSV 导入 ==================== */
var IMP = null;

function impVal(v) {
  if (v === null || v === undefined) return '';
  if (v === true) return '是';
  if (v === false) return '否';
  if (Array.isArray(v)) return v.join('、');
  return String(v);
}

function impTypeLabel(t) { return TYPE_LABEL[t] || t; }

function impFileStem() {
  return (IMP && IMP.filename ? IMP.filename : '导入表').replace(/\.[^.]+$/, '');
}

function impInitMapping() {
  var cols = anaCols(state.tid).filter(function (c) {
    return c.type !== 'formula' && c.type !== 'image';
  });
  var byLabel = Object.create(null);   // Excel 表头是什么都可能
  cols.forEach(function (c) { byLabel[c.label] = c.key; });
  IMP.mapping = IMP.preview.headers.map(function (h) {
    return byLabel[h.name] || (IMP.autoNew ? '__new__' : '');
  });
}

function impInitNew() {
  var stem = impFileStem();
  var sh = IMP.preview.sheet;
  IMP.name = (sh && sh !== 'CSV') ? stem + '·' + sh : stem;
  IMP.group = stem;
  IMP.columns = IMP.preview.headers.map(function (h) {
    var t = h.inferred_type === 'image' ? 'text' : h.inferred_type;
    return { label: h.name, type: t || 'text', include: true, options: h.options || null };
  });
}

function impPreviewHtml() {
  var hs = IMP.preview.headers || [], rs = IMP.preview.rows || [];
  return '<div class="imp-preview"><table><thead><tr><th>#</th>'
    + hs.map(function (h) { return '<th title="' + esc(h.name) + '">' + esc(h.name) + '</th>'; }).join('')
    + '</tr></thead><tbody>'
    + rs.map(function (r, i) {
      return '<tr><td>' + (i + 1) + '</td>'
        + r.map(function (v) { return '<td title="' + esc(impVal(v)) + '">' + esc(impVal(v)) + '</td>'; }).join('')
        + '</tr>';
    }).join('')
    + '</tbody></table></div>';
}

function impCanNewFields() { return !!(ME && (ME.role === 'admin' || ME.can_fields)); }

function impMapHtml() {
  if (!IMP.mapping || IMP.mapping.length !== IMP.preview.headers.length) impInitMapping();
  var canNew = impCanNewFields();
  if (!canNew) {
    // 没有字段管理权限的人不能靠导入“自动新建字段”（那等于改表结构）：不显示这个选项
    IMP.autoNew = false;
    IMP.mapping = IMP.mapping.map(function (m) { return m === '__new__' ? '' : m; });
  }
  var cols = anaCols(state.tid).filter(function (c) {
    return c.type !== 'formula' && c.type !== 'image';
  });
  var rows = IMP.preview.headers.map(function (h, i) {
    var opts = '<option value="">忽略</option>'
      + (canNew ? '<option value="__new__"' + (IMP.mapping[i] === '__new__' ? ' selected' : '') + '>新建字段</option>' : '')
      + cols.map(function (c) {
        return '<option value="' + esc(c.key) + '"' + (IMP.mapping[i] === c.key ? ' selected' : '') + '>'
          + esc(c.label) + '（' + esc(impTypeLabel(c.type)) + '）</option>';
      }).join('');
    return '<div class="imp-map-row"><div class="imp-src" title="' + esc(h.name) + '">'
      + esc(h.name) + '<em>' + esc(impTypeLabel(h.inferred_type)) + '</em></div>'
      + '<div class="imp-sample" title="' + esc((h.sample || []).join(' / ')) + '">'
      + esc((h.sample || []).slice(0, 2).join(' / ') || '—') + '</div>'
      + '<select data-map="' + i + '">' + opts + '</select></div>';
  }).join('');
  return '<div class="imp-map"><div class="imp-map-head"><span>Excel 列</span><span>示例</span><span>导入到</span></div>'
    + rows + '</div>'
    + (canNew ? '<label class="imp-auto"><input type="checkbox" id="imp-auto"' + (IMP.autoNew ? ' checked' : '')
    + '> 未匹配的列自动新建字段</label>' : '')
    + (IMP.target === 'replace'
      ? '<div class="imp-warn">覆盖会先清空当前表的现有行；导入过程中出错会回滚。</div>' : '');
}

function impNewHtml() {
  if (!IMP.columns || IMP.columns.length !== IMP.preview.headers.length) impInitNew();
  if (!IMP.name) IMP.name = impFileStem();
  var types = ['text', 'number', 'date', 'select', 'multiselect', 'checkbox'];
  var rows = IMP.preview.headers.map(function (h, i) {
    var c = IMP.columns[i];
    return '<tr><td><input type="checkbox" data-inc="' + i + '"' + (c.include ? ' checked' : '') + '></td>'
      + '<td><input type="text" data-label="' + i + '" value="' + esc(c.label) + '"></td>'
      + '<td><select data-type="' + i + '">' + types.map(function (t) {
        return '<option value="' + t + '"' + (c.type === t ? ' selected' : '') + '>' + impTypeLabel(t) + '</option>';
      }).join('') + '</select></td>'
      + '<td class="imp-sample" title="' + esc((h.sample || []).join(' / ')) + '">'
      + esc((h.sample || []).slice(0, 2).join(' / ') || '—') + '</td></tr>';
  }).join('');
  return '<div class="frm row2"><div><label>新表名</label><input id="imp-name" value="' + esc(IMP.name) + '"></div>'
    + '<div><label>组名（侧栏分组）</label><input id="imp-group" value="' + esc(IMP.group) + '"></div></div>'
    + '<div class="imp-cols-title">导入列（可改类型，取消勾选则不导入）</div>'
    + '<div class="imp-cols"><table><thead><tr><th></th><th>列名</th><th>类型</th><th>示例</th></tr></thead><tbody>'
    + rows + '</tbody></table></div>';
}

function renderImport() {
  if (!IMP) return;
  var body = '', foot = '', title = IMP.step === 1 ? '导入 Excel' : '导入 Excel · 映射';
  if (IMP.loading) {
    body = '<div class="imp-loading">正在处理文件…</div>';
    foot = '<button class="btn" id="imp-cancel">取消</button>';
  } else if (IMP.step === 1) {
    body = '<div class="imp-top"><div class="frm imp-file"><label>Excel / CSV 文件</label>'
      + '<input type="file" id="imp-file" class="imp-file-native" accept=".xlsx,.xls,.xlsm,.csv">'
      + '<label class="imp-pick" for="imp-file">选择文件</label>'
      + '<div class="imp-file-name" id="imp-file-name">' + esc(IMP.filename || '未选择文件') + '</div></div>'
      + '<div class="frm imp-head"><label>表头行</label><input type="number" id="imp-header" min="1" value="'
      + (IMP.headerRow || 1) + '"></div></div>';
    foot = '<button class="btn" id="imp-cancel">取消</button><button class="btn btn-primary" id="imp-next">下一步</button>';
  } else {
    var p = IMP.preview;
    body = '<div class="imp-summary"><span>文件 <b>' + esc(p.filename) + '</b></span>'
      + '<span>工作表 <select id="imp-sheet">' + p.sheets.map(function (s) {
        return '<option value="' + esc(s) + '"' + (s === IMP.sheet ? ' selected' : '') + '>' + esc(s) + '</option>';
      }).join('') + '</select></span>'
      + '<span>表头行 <input type="number" id="imp-header2" min="1" value="' + (IMP.headerRow || 1) + '"></span>'
      + '<button class="imp-reload" id="imp-reload">重新读取</button>'
      + '<span style="margin-left:auto">共 <b>' + p.total_rows + '</b> 行 × <b>' + p.total_cols + '</b> 列</span></div>'
      + (IMP.projectImport ? '' :
        '<div class="frm"><label>导入方式</label><div class="imp-target">'
        + '<label><input type="radio" name="imp-target" value="append"' + (IMP.target === 'append' ? ' checked' : '') + '>追加到当前表</label>'
        + '<label><input type="radio" name="imp-target" value="replace"' + (IMP.target === 'replace' ? ' checked' : '') + '>覆盖当前表</label>'
        + '<label><input type="radio" name="imp-target" value="new"' + (IMP.target === 'new' ? ' checked' : '') + '>新建一张表</label>'
        + '</div></div>')
      + (IMP.target === 'new' ? impNewHtml() : impMapHtml())
      + '<div class="imp-preview-title">数据预览（前 8 行）</div>' + impPreviewHtml();
    foot = '<button class="btn" id="imp-back">上一步</button><button class="btn btn-primary" id="imp-start">开始导入</button>';
  }
  modal(title, body, foot, function (root) {
    root.querySelector('.box').classList.add('imp-box');
    if (IMP.step === 1) root.querySelector('.box').classList.add('imp-step1');
    var cancel = $('imp-cancel'); if (cancel) cancel.onclick = function () { IMP = null; closeModal(); };
    if (IMP.loading) return;
    if (IMP.step === 1) {
      $('imp-file').onchange = function () {
        if (!this.files || !this.files[0]) return;
        IMP.file = this.files[0];
        IMP.filename = this.files[0].name;
        $('imp-file-name').textContent = IMP.filename;
      };
      $('imp-next').onclick = function () {
        var f = IMP.file || ($('imp-file').files && $('imp-file').files[0]);
        if (!f) { toast('请选择文件'); return; }
        IMP.file = f;
        IMP.filename = f.name;
        IMP.headerRow = Math.max(1, parseInt($('imp-header').value, 10) || 1);
        impParse(f, '', IMP.headerRow);
      };
      return;
    }
    $('imp-sheet').onchange = function () {
      IMP.sheet = this.value; IMP.mapping = null; IMP.columns = null;
      impParse(IMP.file, IMP.sheet, IMP.headerRow);
    };
    $('imp-reload').onclick = function () {
      IMP.sheet = $('imp-sheet').value;
      IMP.headerRow = Math.max(1, parseInt($('imp-header2').value, 10) || 1);
      IMP.mapping = null; IMP.columns = null;
      impParse(IMP.file, IMP.sheet, IMP.headerRow);
    };
    root.querySelectorAll('input[name="imp-target"]').forEach(function (r) {
      r.onchange = function () { if (r.checked) { IMP.target = r.value; renderImport(); } };
    });
    if (IMP.target === 'new') {
      $('imp-name').oninput = function () { IMP.name = this.value; };
      $('imp-group').oninput = function () { IMP.group = this.value; };
      root.querySelectorAll('[data-inc]').forEach(function (cb) {
        cb.onchange = function () { IMP.columns[+cb.getAttribute('data-inc')].include = cb.checked; };
      });
      root.querySelectorAll('[data-label]').forEach(function (inp) {
        inp.oninput = function () { IMP.columns[+inp.getAttribute('data-label')].label = inp.value; };
      });
      root.querySelectorAll('[data-type]').forEach(function (sel) {
        sel.onchange = function () { IMP.columns[+sel.getAttribute('data-type')].type = sel.value; };
      });
    } else {
      root.querySelectorAll('[data-map]').forEach(function (sel) {
        sel.onchange = function () { IMP.mapping[+sel.getAttribute('data-map')] = sel.value; };
      });
      var auto = $('imp-auto');
      if (auto) auto.onchange = function () {
        IMP.autoNew = auto.checked;
        root.querySelectorAll('[data-map]').forEach(function (sel) {
          var i = +sel.getAttribute('data-map');
          if (IMP.mapping[i] === '' || IMP.mapping[i] === '__new__') {
            IMP.mapping[i] = IMP.autoNew ? '__new__' : '';
            sel.value = IMP.mapping[i];
          }
        });
      };
    }
    $('imp-back').onclick = function () { IMP.step = 1; IMP.mapping = null; IMP.columns = null; renderImport(); };
    $('imp-start').onclick = impStart;
  });
}

async function impParse(file, sheet, headerRow) {
  var own = IMP;
  IMP.loading = true; renderImport();
  var fd = new FormData();
  fd.append('file', file);
  fd.append('sheet', sheet || '');
  fd.append('header_row', String(headerRow || 1));
  // 预览也带上导入目标：服务端据此校验权限（解析 Excel 是重活，不能只凭“已登录”就调用）
  if (IMP.projectImport || !state.tid) fd.append('project', (curProject && curProject.name) || '');
  else fd.append('table_id', String(state.tid));
  try {
    var d = await apiForm('/api/import/preview', fd);
    if (IMP !== own) return;
    IMP.preview = d;
    IMP.step = 2;
    IMP.sheet = d.sheet;
    IMP.headerRow = d.header_row;
    IMP.mapping = null;
    IMP.columns = null;
    IMP.loading = false;
    renderImport();
  } catch (e) {
    if (IMP !== own) return;
    IMP.loading = false;
    IMP.step = 1;
    renderImport();
    toast('读取失败：' + e.message);
  }
}

async function impStart() {
  if (!IMP || !IMP.preview) return;
  var cfg;
  if (IMP.target === 'new') {
    if (!IMP.name || !IMP.name.trim()) { toast('请填新表名'); return; }
    if (!IMP.columns.some(function (c) { return c.include; })) { toast('至少保留一列'); return; }
    cfg = {
      target: 'new', project: (curProject && curProject.name) || '',
      name: IMP.name.trim(), group_name: (IMP.group || '').trim(),
      sheet: IMP.sheet, header_row: IMP.headerRow,
      columns: IMP.columns.map(function (c, i) {
        return { source: i, label: c.label, type: c.type, include: c.include, options: c.options };
      })
    };
  } else {
    if (!IMP.mapping.some(Boolean)) { toast('至少映射一个字段'); return; }
    var mode = IMP.target === 'replace' ? 'replace' : 'append';
    if (mode === 'replace') {
      var t = anaTable(state.tid);
      var ok = await appConfirm('覆盖当前表',
        '覆盖会先清空「<b>' + esc(t ? t.name : '当前表') + '</b>」的现有行，确定导入？',
        '覆盖导入', true);
      if (!ok) return;
    }
    cfg = {
      target: 'table', table_id: state.tid, sheet: IMP.sheet,
      header_row: IMP.headerRow, mode: mode, mapping: IMP.mapping
    };
  }
  var own = IMP;
  IMP.loading = true; renderImport();
  var fd = new FormData();
  fd.append('file', IMP.file);
  fd.append('config', JSON.stringify(cfg));
  try {
    var res = await apiForm('/api/import/commit', fd);
    if (IMP !== own) return;
    IMP.loading = false;
    closeModal();
    IMP = null;
    toast('导入成功：' + res.inserted + ' 行');
    try { await refreshMeta(); } catch (e2) {}
    if (res.table_id) openSheet(res.table_id);
    else if ($('table-view').classList.contains('on')) loadRows();
  } catch (e) {
    if (IMP !== own) return;
    IMP.loading = false;
    renderImport();
    toast('导入失败：' + e.message);
  }
}

$('btn-import').onclick = function () { openImport(false); };

function openImport(startNew) {
  if (!ME) { openLogin(); return; }
  if (!startNew && !state.tid) { toast('请先打开一张表'); return; }
  IMP = {
    step: 1, file: null, filename: '', preview: null, sheet: '', headerRow: 1,
    target: startNew ? 'new' : 'append', projectImport: !!startNew,
    autoNew: true, mapping: null, columns: null, name: '', group: ''
  };
  renderImport();
}

/* ---------- 自定义下拉：替换所有原生 select ---------- */
var CS_OPEN = null;

function enhanceSelects(root) {
  var scope = root || document;
  if (scope.tagName === 'SELECT') { enhanceSelect(scope); return; }
  scope.querySelectorAll('select').forEach(function (sel) {
    if (!sel.__cs && !sel.closest('.cs-menu')) enhanceSelect(sel);
  });
}

function enhanceSelect(sel) {
  /* data-native：这一格要原生下拉（高级筛选的紧凑条件行，自己画的下拉放不进网格） */
  if (!sel || sel.__cs || sel.multiple || sel.hasAttribute('data-native')) return;
  sel.__cs = true;
  var btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'cs-select' + (sel.className ? ' ' + sel.className : '');
  btn.innerHTML = '<span class="cs-val"></span>'
    + '<svg class="cs-caret" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>';
  btn.setAttribute('role', 'combobox');
  btn.setAttribute('aria-haspopup', 'listbox');
  btn.setAttribute('aria-expanded', 'false');
  if (sel.id) btn.setAttribute('data-cs-id', sel.id);
  if (sel.title) btn.title = sel.title;
  if (sel.getAttribute('aria-label')) btn.setAttribute('aria-label', sel.getAttribute('aria-label'));
  sel.parentNode.insertBefore(btn, sel.nextSibling);
  sel.classList.add('cs-native');
  sel.setAttribute('tabindex', '-1');
  sel.__csBtn = btn;

  btn.onclick = function (e) {
    e.stopPropagation();
    if (sel.disabled) return;
    openCsMenu(sel);
  };
  btn.onkeydown = function (e) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      openCsMenu(sel, e.key === 'ArrowUp' ? -1 : 1);
    }
  };

  try {
    var vd = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
    Object.defineProperty(sel, 'value', {
      configurable: true,
      get: function () { return vd.get.call(sel); },
      set: function (v) { vd.set.call(sel, v); csSync(sel); }
    });
    var sd = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'selectedIndex');
    if (sd) Object.defineProperty(sel, 'selectedIndex', {
      configurable: true,
      get: function () { return sd.get.call(sel); },
      set: function (v) { sd.set.call(sel, v); csSync(sel); }
    });
  } catch (e) {}

  var mo = new MutationObserver(function () { csSync(sel); });
  mo.observe(sel, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'style', 'class'] });
  sel.__csMo = mo;
  csSync(sel);
}

function csSync(sel) {
  var btn = sel.__csBtn;
  if (!btn) return;
  var opt = sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex] : null;
  var txt = opt ? String(opt.textContent || '').trim() : '';
  var val = btn.querySelector('.cs-val');
  if (val) val.textContent = txt || '—';
  btn.classList.toggle('cs-placeholder', !txt);
  btn.disabled = !!sel.disabled;
  btn.setAttribute('aria-disabled', sel.disabled ? 'true' : 'false');
  if (btn.classList.contains('opt-color')) {
    btn.style.background = sel.style.background || '';
    btn.style.color = sel.style.color || '';
  }
}

function closeCsMenu() {
  if (!CS_OPEN) return;
  CS_OPEN.btn.setAttribute('aria-expanded', 'false');
  CS_OPEN.btn.classList.remove('cs-open');
  CS_OPEN.menu.remove();
  CS_OPEN = null;
}

function csOpts() { return CS_OPEN ? Array.from(CS_OPEN.menu.querySelectorAll('.cs-opt')) : []; }

function setCsActive(i, scroll) {
  if (!CS_OPEN) return;
  var opts = csOpts();
  if (!opts.length) return;
  i = Math.max(0, Math.min(opts.length - 1, i));
  opts.forEach(function (o, k) { o.classList.toggle('active', k === i); });
  CS_OPEN.active = i;
  if (scroll) {
    var el = opts[i], menu = CS_OPEN.menu;
    if (el) menu.scrollTop = Math.max(0, el.offsetTop - (menu.clientHeight - el.offsetHeight) / 2);
  }
}

function moveCsActive(delta) {
  if (!CS_OPEN) return;
  var opts = csOpts();
  var i = CS_OPEN.active;
  do { i += delta; } while (i >= 0 && i < opts.length && opts[i].classList.contains('disabled'));
  if (i >= 0 && i < opts.length) setCsActive(i, true);
}

function chooseCs(i) {
  if (!CS_OPEN) return;
  var sel = CS_OPEN.sel;
  var opt = sel.options[i];
  if (!opt || opt.disabled) return;
  sel.selectedIndex = i;
  csSync(sel);
  closeCsMenu();
  sel.dispatchEvent(new Event('change', { bubbles: true }));
}

function openCsMenu(sel, dir) {
  closeCsMenu();
  var btn = sel.__csBtn;
  var opts = Array.from(sel.options);
  if (!btn || !opts.length) return;
  var menu = document.createElement('div');
  menu.className = 'cs-menu';
  menu.setAttribute('role', 'listbox');
  menu.innerHTML = opts.map(function (o, i) {
    var on = i === sel.selectedIndex;
    return '<div class="cs-opt' + (on ? ' on' : '') + (o.disabled ? ' disabled' : '') + '"'
      + ' role="option" aria-selected="' + (on ? 'true' : 'false') + '" data-i="' + i + '">'
      + '<span class="cs-opt-t">' + esc(String(o.textContent || '').trim() || '—') + '</span>'
      + (on ? '<svg class="cs-check" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>' : '')
      + '</div>';
  }).join('');
  menu.addEventListener('click', function (e) {
    e.stopPropagation();
    var row = e.target.closest('.cs-opt');
    if (row) chooseCs(+row.getAttribute('data-i'));
  });
  document.body.appendChild(menu);

  var r = btn.getBoundingClientRect();
  var mw = Math.min(Math.max(r.width, 160), Math.min(360, window.innerWidth - 16));
  menu.style.width = mw + 'px';
  menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - 8 - mw)) + 'px';
  menu.style.top = '0px';
  var mh = menu.offsetHeight;
  var below = window.innerHeight - r.bottom - 8;
  var above = r.top - 8;
  var top = (below >= mh || below >= above) ? r.bottom + 4 : r.top - 4 - mh;
  menu.style.top = Math.max(8, Math.min(top, window.innerHeight - 8 - mh)) + 'px';

  CS_OPEN = { sel: sel, btn: btn, menu: menu, active: sel.selectedIndex };
  btn.classList.add('cs-open');
  btn.setAttribute('aria-expanded', 'true');
  try { btn.focus({ preventScroll: true }); } catch (e) { btn.focus(); }
  setCsActive(dir < 0 ? Math.max(0, sel.selectedIndex) : (sel.selectedIndex >= 0 ? sel.selectedIndex : 0), true);
  if (dir) moveCsActive(dir < 0 ? -1 : 1);
}

document.addEventListener('click', function (e) {
  if (!CS_OPEN) return;
  var t = e.target;
  if (t.closest('.cs-menu') || t.closest('.cs-select')) return;
  closeCsMenu();
});
document.addEventListener('keydown', function (e) {
  if (!CS_OPEN) return;
  if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); closeCsMenu(); return; }
  if (e.key === 'ArrowDown') { e.preventDefault(); e.stopImmediatePropagation(); moveCsActive(1); return; }
  if (e.key === 'ArrowUp') { e.preventDefault(); e.stopImmediatePropagation(); moveCsActive(-1); return; }
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopImmediatePropagation(); chooseCs(CS_OPEN.active); return; }
  if (e.key === 'Tab') closeCsMenu();
});
document.addEventListener('scroll', function (e) {
  if (!CS_OPEN) return;
  if (CS_OPEN.menu.contains(e.target)) return;
  closeCsMenu();
}, true);
window.addEventListener('resize', closeCsMenu);

var csObserver = new MutationObserver(function (muts) {
  muts.forEach(function (m) {
    if (m.type !== 'childList') return;
    m.addedNodes.forEach(function (n) {
      if (n.nodeType !== 1) return;
      if (n.tagName === 'SELECT') enhanceSelect(n);
      else if (n.querySelectorAll) enhanceSelects(n);
    });
  });
});
csObserver.observe(document.body, { childList: true, subtree: true });
enhanceSelects(document);

/* ---------- 自定义日期选择器 ---------- */
var DATE_ICON_SVG = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18"/></svg>';
var DP = null;
function dpPad(n) { return (n < 10 ? '0' : '') + n; }
function dpFmt(y, m, d) { return y + '-' + dpPad(m + 1) + '-' + dpPad(d); }
function dpParse(s) {
  var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s || ''));
  return m ? { y: +m[1], m: +m[2] - 1, d: +m[3] } : null;
}
function dpToday() { var n = new Date(); return { y: n.getFullYear(), m: n.getMonth(), d: n.getDate() }; }
function closeDatePicker() { if (!DP) return; var p = DP.pop; DP = null; if (p && p.parentNode) p.remove(); }
function openDatePicker(input, opts) {
  opts = opts || {};
  if (DP && DP.input === input) { closeDatePicker(); return; }
  closeDatePicker();
  var sel = dpParse(input.value);
  var base = sel || dpToday();
  var pop = document.createElement('div');
  pop.className = 'dp-pop';
  pop.setAttribute('role', 'dialog');
  pop.innerHTML = '<div class="dp-phead">'
    + '<button type="button" class="dp-pnav" data-nav="py" title="上一年"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6l-6 6 6 6M11 6l-6 6 6 6"/></svg></button>'
    + '<button type="button" class="dp-pnav" data-nav="pm" title="上个月"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 6l-6 6 6 6"/></svg></button>'
    + '<span class="dp-pym"></span>'
    + '<button type="button" class="dp-pnav" data-nav="nm" title="下个月"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg></button>'
    + '<button type="button" class="dp-pnav" data-nav="ny" title="下一年"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l6 6-6 6M13 6l6 6-6 6"/></svg></button>'
    + '</div>'
    + '<div class="dp-pweek">' + ['一', '二', '三', '四', '五', '六', '日'].map(function (x) { return '<span>' + x + '</span>'; }).join('') + '</div>'
    + '<div class="dp-pdays"></div>'
    + '<div class="dp-pfoot"><button type="button" class="dp-pclear">清除</button><button type="button" class="dp-ptoday">今天</button></div>';
  document.body.appendChild(pop);
  DP = { input: input, pop: pop, y: base.y, m: base.m, sel: sel, opts: opts };

  function draw() {
    if (!DP) return;
    DP.pop.querySelector('.dp-pym').textContent = DP.y + '年' + (DP.m + 1) + '月';
    var first = new Date(DP.y, DP.m, 1);
    var off = (first.getDay() + 6) % 7;          // 周一为第一列
    var start = new Date(DP.y, DP.m, 1 - off);
    var t = dpToday(), html = '';
    for (var i = 0; i < 42; i++) {
      var dt = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
      var y = dt.getFullYear(), m = dt.getMonth(), d = dt.getDate();
      var cl = 'dp-pday';
      if (m !== DP.m) cl += ' other';
      if (y === t.y && m === t.m && d === t.d) cl += ' today';
      if (DP.sel && y === DP.sel.y && m === DP.sel.m && d === DP.sel.d) cl += ' sel';
      html += '<button type="button" class="' + cl + '" data-d="' + dpFmt(y, m, d) + '">' + d + '</button>';
    }
    DP.pop.querySelector('.dp-pdays').innerHTML = html;
  }
  DP.draw = draw;

  function pick(v) {
    if (!DP) return;
    var inp = DP.input, cb = DP.opts && DP.opts.onPick;
    closeDatePicker();
    inp.value = v;
    try { inp.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
    inp.dispatchEvent(new Event('change', { bubbles: true }));
    if (cb) cb(v);
  }
  DP.pick = pick;

  draw();
  pop.addEventListener('mousedown', function (e) { e.preventDefault(); });
  pop.querySelectorAll('[data-nav]').forEach(function (b) {
    b.onclick = function () {
      var k = b.getAttribute('data-nav');
      if (k === 'py') DP.y--;
      else if (k === 'ny') DP.y++;
      else if (k === 'pm') { DP.m--; if (DP.m < 0) { DP.m = 11; DP.y--; } }
      else if (k === 'nm') { DP.m++; if (DP.m > 11) { DP.m = 0; DP.y++; } }
      draw();
    };
  });
  pop.querySelector('.dp-pclear').onclick = function () { pick(''); };
  pop.querySelector('.dp-ptoday').onclick = function () { var t = dpToday(); pick(dpFmt(t.y, t.m, t.d)); };
  pop.querySelector('.dp-pdays').onclick = function (e) {
    var b = e.target.closest('.dp-pday');
    if (b) pick(b.getAttribute('data-d'));
  };

  var r = input.getBoundingClientRect();
  var w = pop.offsetWidth, h = pop.offsetHeight;
  var left = Math.min(Math.max(8, r.left), window.innerWidth - 8 - w);
  var below = window.innerHeight - r.bottom - 8, above = r.top - 8;
  var top = (below >= h || below >= above) ? r.bottom + 6 : r.top - 6 - h;
  pop.style.left = Math.round(left) + 'px';
  pop.style.top = Math.round(Math.max(8, Math.min(top, window.innerHeight - 8 - h))) + 'px';
}
function positionDatePicker() {
  if (!DP) return;
  var inp = DP.input, pop = DP.pop;
  if (!inp.isConnected) { closeDatePicker(); return; }
  var r = inp.getBoundingClientRect();
  if (r.bottom < 0 || r.top > window.innerHeight || r.right < 0 || r.left > window.innerWidth) {
    closeDatePicker(); return;
  }
  var w = pop.offsetWidth, h = pop.offsetHeight;
  var left = Math.min(Math.max(8, r.left), window.innerWidth - 8 - w);
  var below = window.innerHeight - r.bottom - 8, above = r.top - 8;
  var top = (below >= h || below >= above) ? r.bottom + 6 : r.top - 6 - h;
  pop.style.left = Math.round(left) + 'px';
  pop.style.top = Math.round(Math.max(8, Math.min(top, window.innerHeight - 8 - h))) + 'px';
}
document.addEventListener('click', function (e) {
  if (!DP) return;
  if (e.target.closest('.dp-pop') || e.target === DP.input || e.target.closest('.date-btn')) return;
  closeDatePicker();
}, true);
document.addEventListener('keydown', function (e) {
  if (!DP) return;
  if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); closeDatePicker(); return; }
  if (e.key === 'Enter') {
    if (DP.sel) { e.preventDefault(); e.stopImmediatePropagation(); DP.pick(dpFmt(DP.sel.y, DP.sel.m, DP.sel.d)); }
    return;
  }
  var delta = e.key === 'ArrowLeft' ? -1 : e.key === 'ArrowRight' ? 1 : e.key === 'ArrowUp' ? -7 : e.key === 'ArrowDown' ? 7 : 0;
  if (delta) {
    e.preventDefault(); e.stopImmediatePropagation();
    var cur = DP.sel ? new Date(DP.sel.y, DP.sel.m, DP.sel.d) : new Date(DP.y, DP.m, 1);
    cur.setDate(cur.getDate() + delta);
    DP.sel = { y: cur.getFullYear(), m: cur.getMonth(), d: cur.getDate() };
    DP.y = DP.sel.y; DP.m = DP.sel.m;
    DP.draw();
  }
});
document.addEventListener('scroll', function (e) {
  if (!DP) return;
  if (DP.pop.contains(e.target)) return;
  positionDatePicker();
}, true);
window.addEventListener('resize', positionDatePicker);

/* ---------- 手机端：侧栏抽屉 & 表格操作菜单 ---------- */
var MOBILE_MQ = window.matchMedia('(max-width:860px)');
function isMobile() { return MOBILE_MQ.matches; }
function openMobileSidebar() { document.body.classList.add('mobile-sb-open'); }
function closeMobileSidebar() { document.body.classList.remove('mobile-sb-open'); }
function toggleMobileSidebar() {
  if (document.body.classList.contains('mobile-sb-open')) closeMobileSidebar();
  else openMobileSidebar();
}
$('btn-mobile-menu').onclick = function (e) { e.stopPropagation(); toggleMobileSidebar(); };
$('sidebar-mask').onclick = closeMobileSidebar;
if (MOBILE_MQ.addEventListener) {
  MOBILE_MQ.addEventListener('change', function (e) { if (!e.matches) closeMobileSidebar(); });
}

function closeTableMenu() {
  var m = $('table-menu');
  if (m) m.classList.remove('on');
}
function openTableMenu() {
  var menu = $('table-menu');
  if (!menu) return;
  if (menu.classList.contains('on')) { closeTableMenu(); return; }
  var t = curTable();
  var items = [];
  function add(label, icon, fn) { items.push({ label: label, icon: icon, fn: fn }); }
  add('导出 CSV',
    '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12M7 10l5 5 5-5M5 21h14"/></svg>',
    function () { $('btn-csv').click(); });
  if (t && t.my_role === 'editor') add('导入 Excel / CSV',
    '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21V9M7 14l5-5 5 5M5 3h14"/></svg>',
    function () { $('btn-import').click(); });
  add('变动历史',
    '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    function () { $('btn-hist').click(); });
  if (curProject && curProject.analysis_role) add('数据分析',
    '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 19V10M10 19V5M16 19v-6M22 19H2"/></svg>',
    function () { $('btn-analysis').click(); });
  if (ME && ME.role === 'admin') add('汇总同步',
    '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h6v6H4zM4 13h6v6H4zM14 12h6M17 9l3 3-3 3"/></svg>',
    function () { $('btn-sync').click(); });
  if (ME && ME.role === 'admin') add('写入密钥',
    '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 7a4 4 0 1 0-4 4l-6 6v3h3l6-6a4 4 0 0 0 1-.9"/><circle cx="16.5" cy="7.5" r=".8"/></svg>',
    function () { $('btn-apikey').click(); });
  if (ME && ME.role === 'admin') add('新建表格',
    '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
    function () { $('btn-new-table').click(); });
  add('重置视图（清除搜索/筛选/排序）',
    '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1"/><path d="M3 4v5h5"/></svg>',
    function () { resetView(); });
  add('恢复默认顺序（页签/侧栏）',
    '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h10M4 12h16M4 17h7"/></svg>',
    function () { resetUserOrder(); });
  menu.innerHTML = items.map(function (it, i) {
    return '<button class="tm-item" type="button" data-i="' + i + '">' + it.icon + '<span>' + it.label + '</span></button>';
  }).join('');
  var btn = $('btn-more');
  var r = btn.getBoundingClientRect();
  menu.style.top = Math.round(r.bottom + 6) + 'px';
  menu.style.left = 'auto';
  menu.style.right = Math.max(8, Math.round(window.innerWidth - r.right)) + 'px';
  menu.classList.add('on');
  menu.querySelectorAll('.tm-item').forEach(function (b) {
    b.onclick = function () {
      closeTableMenu();
      items[+b.getAttribute('data-i')].fn();
    };
  });
}
$('btn-more').onclick = function (e) { e.stopPropagation(); openTableMenu(); };
document.addEventListener('click', function (e) {
  if (!e.target.closest('#table-menu') && !e.target.closest('#btn-more')) closeTableMenu();
});
document.addEventListener('scroll', closeTableMenu, true);
window.addEventListener('resize', closeTableMenu);

/* 启动：失败自动重试，仍失败给提示（避免首屏空白） */
function startApp() {
  boot().catch(function (e) {
    console.error('boot failed', e);
    setTimeout(function () {
      boot().catch(function (e2) {
        console.error('boot retry failed', e2);
        var b = document.getElementById('home-body');
        if (b) b.innerHTML = '<div class="home-empty">加载失败：' + esc(e2.message) + '<br>请刷新页面重试</div>';
      });
    }, 600);
  });
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',startApp);else startApp();
