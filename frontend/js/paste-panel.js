/* paste-panel.js — index.html 内联脚本拆出的第 8/12 段（原第 4528-5067 行）。
   加载顺序见 index.html 底部；全部是全局函数，跨文件可直接调用。 */
/* ============ 从 Excel 粘贴一片单元格 ============
   以当前选中的格子为左上角，按 Tab/换行拆开粘贴；只写“当前页已有的行”，不会悄悄插行。
   一行发一个 PATCH（带乐观锁），最后整体刷新并按格数汇报。整片粘贴在撤销栈里算一条。 */
function pasteMatrix(td, text) {
  if (!canEditRows()) { toast('只读权限，不能粘贴'); return; }
  if (!GRID) return;
  var m = String(text).replace(/\r\n?/g, '\n').replace(/\n$/, '').split('\n').map(function (l) { return l.split('\t'); });
  if (!m.length || !m[0]) { toast('没有可粘贴的内容'); return; }
  var cols = GRID.cols.filter(function (c) { return c.type !== 'formula' && c.type !== 'image'; });
  var startKey = td.getAttribute('data-f'), startCol = -1;
  cols.forEach(function (c, i) { if (c.key === startKey) startCol = i; });
  if (startCol < 0) { toast('请先点一个可编辑的单元格（公式列不能粘贴）'); return; }
  var startRow = gridRowIndex(+td.closest('tr').getAttribute('data-rid'));
  if (startRow < 0) { toast('这一行不在当前页'); return; }
  var s = selRange();
  // 只复制了一个值、却选了一片：铺满选区（和 Excel 一样）
  if (m.length === 1 && m[0].length === 1 && s && (s.r1 > s.r0 || s.c1 > s.c0)) {
    fillSelectionWithValue(s, m[0][0] === '' ? null : m[0][0]);
    return;
  }
  var nCol = 0; m.forEach(function (x) { nCol = Math.max(nCol, x.length); });
  nCol = Math.min(nCol, cols.length - startCol);
  if (nCol < 1) { toast('右边没有可粘贴的列'); return; }
  var items = [], cells = [], n = 0, made = 0;
  for (var y = 0; y < m.length; y++) {
    var row = GRID.d.rows[startRow + y], data = {};
    for (var x = 0; x < nCol; x++) {
      if (m[y][x] === undefined) continue;
      var c = cols[startCol + x], v = String(m[y][x]);
      data[c.key] = v === '' ? null : v;
    }
    if (!Object.keys(data).length) continue;
    if (row) {
      var changed = {};
      for (var k in data) {
        var old = (row.data || {})[k];
        if (sameVal(old, data[k])) continue;
        changed[k] = data[k];
        cells.push({ rid: row.id, key: k, old: old === undefined ? null : old, new: data[k] });
        n++;
      }
      if (Object.keys(changed).length) items.push({ id: row.id, ver: row.ver, data: changed });
    } else {
      items.push({ data: data });            // 当前页没有这一行：在表尾新建
      n += Object.keys(data).length; made++;
    }
  }
  if (!items.length) { toast('粘贴的内容和现有数据一样，没有改动'); return; }
  showBusy('正在粘贴 ' + n + ' 格…');
  (async function () {
    try {
      var r = await bulkRows(items);
      if (cells.length) undoPush({ tid: state.tid, label: '粘贴 ' + cells.length + ' 格', cells: cells });
      await loadRows();
      var bad = (r.conflicts || []).length + (r.errors || []).length;
      var msg = '已粘贴 ' + n + ' 格';
      if (made) msg += '，新建 ' + made + ' 行';
      if (bad) msg += '，' + bad + ' 行没成功（可能别人刚改过）';
      toast(msg);
    } catch (e) { toast('粘贴失败：' + e.message); }
    hideBusy();
  })();
}
document.addEventListener('paste', function (e) {
  var el = document.activeElement, tag = el && el.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || (el && el.isContentEditable)) return;   // 在输入框/编辑器里粘贴走浏览器默认
  if (!$('table-view') || !$('table-view').classList.contains('on')) return;
  if ($('mask') || openFilter) return;
  var td = document.querySelector('#tbody td.cell-sel');
  if (!td) return;
  var cd = e.clipboardData || window.clipboardData;
  var text = cd ? cd.getData('text/plain') : '';
  if (!text) return;
  e.preventDefault();
  pasteMatrix(td, text);
});

/* ============ 单元格批注（挂在格子上的备注，不改数据；只读账号也能写） ============
   表格打开时拉一次汇总（每格几条），有批注的格子右上角显示橙色角标；点角标或选中格子后点气泡按钮打开。 */
var CMT_SUM = {};                 // tid -> {'rowid|key': n}；undefined 表示还没拉过
var CMT_POP = null;
function cmtKey(rid, k) { return rid + '|' + k; }
function ensureCommentSummary(tid) {
  if (!tid) return Promise.resolve({});
  if (CMT_SUM[tid] !== undefined) return Promise.resolve(CMT_SUM[tid]);
  return fetch('/api/tables/' + tid + '/comments/summary', { cache: 'no-store' })
    .then(function (r) { return r.ok ? r.json() : { items: [] }; })
    .then(function (d) {
      var m = {};
      (d.items || []).forEach(function (x) { m[cmtKey(x.row_id, x.col_key)] = x.n; });
      CMT_SUM[tid] = m;
      if (state.tid === tid && GRID) drawGridWindow(true);
      return m;
    })
    .catch(function () { CMT_SUM[tid] = {}; return {}; });
}
function invalidateCommentSummary(tid) { delete CMT_SUM[tid]; }
function closeCellComments() { if (CMT_POP) { CMT_POP.remove(); CMT_POP = null; } }
function openCellComments(rid, key, anchor) {
  closeCellComments(); closeCtxMenu();
  var c = colByKey(key);
  var pop = document.createElement('div'); pop.className = 'cmt-pop';
  pop.innerHTML = '<div class="cmt-head"><b>' + esc(c ? c.label : key) + '</b> · 行 #' + rid
    + '<button class="cmt-x" type="button" title="关闭">×</button></div>'
    + '<div class="cmt-list" id="cmt-list"><div class="cmt-empty">加载中…</div></div>'
    + '<div class="cmt-input"><textarea id="cmt-text" placeholder="写一条批注…（Ctrl+Enter 发送）"></textarea>'
    + '<div class="cmt-foot"><span class="cmt-hint">批注不改数据，只读账号也能写</span><span style="flex:1"></span>'
    + '<button class="btn btn-primary" id="cmt-send" type="button" style="height:28px">发送</button></div></div>';
  document.body.appendChild(pop); CMT_POP = pop;
  var r = anchor.getBoundingClientRect(), pw = pop.offsetWidth, ph = pop.offsetHeight;
  var left = Math.min(Math.max(8, r.left), window.innerWidth - pw - 8);
  var top = r.bottom + 6;
  if (top + ph > window.innerHeight - 8) top = Math.max(8, r.top - ph - 6);
  pop.style.left = Math.round(left) + 'px'; pop.style.top = Math.round(top) + 'px';
  pop.onclick = function (e) { e.stopPropagation(); };
  pop.querySelector('.cmt-x').onclick = closeCellComments;
  function send() {
    var ta = pop.querySelector('#cmt-text'), body = ta.value.trim();
    if (!body) { ta.focus(); return; }
    var btn = pop.querySelector('#cmt-send');
    btn.disabled = true;
    api('/api/tables/' + state.tid + '/comments', 'POST', { row_id: rid, col_key: key, body: body })
      .then(function () {
        ta.value = '';
        invalidateCommentSummary(state.tid);
        ensureCommentSummary(state.tid);
        renderCmList(pop, rid, key);
        toast('批注已添加');
      })
      .catch(function (e) { toast('批注失败：' + e.message); })
      .then(function () { btn.disabled = false; });
  }
  pop.querySelector('#cmt-send').onclick = send;
  pop.querySelector('#cmt-text').onkeydown = function (e) {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); }
    else if (e.key === 'Escape') { e.stopPropagation(); closeCellComments(); }
  };
  pop.querySelector('#cmt-text').focus();
  renderCmList(pop, rid, key);
}
async function renderCmList(pop, rid, key) {
  var listEl = pop.querySelector('#cmt-list');
  try {
    var d = await api('/api/tables/' + state.tid + '/comments?row_id=' + rid + '&col_key=' + encodeURIComponent(key));
    var cs = d.comments || [];
    listEl.innerHTML = cs.length ? cs.map(function (c) {
      return '<div class="cmt-item"><div class="cmt-meta"><span>' + esc(c.author) + '</span><span>' + esc(c.at) + '</span>'
        + ((c.mine || (ME && ME.role === 'admin')) ? '<button class="cmt-del" type="button" data-del="' + c.id + '" title="删除批注">×</button>' : '')
        + '</div><div class="cmt-body">' + esc(c.body) + '</div></div>';
    }).join('') : '<div class="cmt-empty">还没有批注</div>';
    listEl.querySelectorAll('[data-del]').forEach(function (b) {
      b.onclick = function (e) {
        e.stopPropagation();
        api('/api/comments/' + b.getAttribute('data-del'), 'DELETE').then(function () {
          invalidateCommentSummary(state.tid);
          ensureCommentSummary(state.tid);
          renderCmList(pop, rid, key);
        }).catch(function (err) { toast('删除失败：' + err.message); });
      };
    });
  } catch (e) { listEl.innerHTML = '<div class="cmt-empty">读取失败：' + esc(e.message) + '</div>'; }
}
document.addEventListener('click', function (e) {
  if (CMT_POP && !e.target.closest('.cmt-pop') && !e.target.closest('.cmt-dot') && !e.target.closest('.cell-cmt')) closeCellComments();
}, true);
document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeCellComments(); });
window.addEventListener('scroll', closeCellComments, true);
window.addEventListener('resize', closeCellComments);

/* ============ 行编辑侧栏（双击行号） ============ */
var RP = null;

function rpDisplay(v) {
  if (v === null || v === undefined || v === '') return '';
  if (v === true) return '是';
  if (v === false) return '否';
  if (Array.isArray(v)) return v.join('、');
  return String(v);
}

function rpIcon(t) {
  var p = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';
  if (t === 'number') return '<svg width="14" height="14" ' + p + '><path d="M10 4L8 20M16 4l-2 16M4 9h16M4 15h16"/></svg>';
  if (t === 'date') return '<svg width="14" height="14" ' + p + '><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 11h18"/></svg>';
  if (t === 'select') return '<svg width="14" height="14" ' + p + '><circle cx="12" cy="12" r="8"/><path d="M9 10l3 3 3-3"/></svg>';
  if (t === 'multiselect') return '<svg width="14" height="14" ' + p + '><path d="M20.6 13.4L12 22l-9-9V4h9z"/><circle cx="8" cy="8" r="1.4"/></svg>';
  if (t === 'checkbox') return '<svg width="14" height="14" ' + p + '><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 12l3 3 5-6"/></svg>';
  if (t === 'image') return '<svg width="14" height="14" ' + p + '><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 16l-5-5-8 8"/></svg>';
  if (t === 'formula') return '<svg width="14" height="14" ' + p + '><path d="M14 5h-2a3 3 0 0 0-3 3v11M7 11h6M13 13l5 7M18 13l-5 7"/></svg>';
  return '<svg width="14" height="14" ' + p + '><path d="M4 6h16M4 12h16M4 18h11"/></svg>';
}

function rpName(row, cols) {
  var c = cols.find(function (x) {
    return (x.type === 'text' || x.type === 'select') && rpDisplay(row[x.key]);
  });
  if (c) return rpDisplay(row[c.key]);
  c = cols.find(function (x) { return rpDisplay(row[x.key]); });
  return c ? rpDisplay(row[c.key]) : '';
}

function rpFieldHtml(c, v) {
  var key = esc(c.key);
  if (RP.readonly) {
    var txt = rpDisplay(displayFormat(c, c.type === 'text' ? readJsonCell(v) : v)) || '—';
    if (c.type === 'multiselect') {
      var ra = Array.isArray(v) ? v.map(String) : (v ? [String(v)] : []);
      return '<div class="rp-tags">' + ra.map(function (x) {
        return '<span class="tag ' + tagFor(x, c) + '">' + esc(x) + '</span>';
      }).join('') + (ra.length ? '' : '<span class="rp-empty">—</span>') + '</div>';
    }
    if (c.type === 'image' && /^(https?:\/\/|\/api\/tables\/)/i.test(txt)) {
      return '<img class="rp-img-preview" src="' + esc(txt) + '" alt="">';
    }
    return '<div class="rp-readonly' + (c.type === 'formula' ? ' rp-fx' : '') + '">' + esc(txt) + '</div>';
  }
  if (c.type === 'image') {
    var iu = rpDisplay(v);
    var has = /^(https?:\/\/|\/api\/tables\/)/i.test(iu);
    return '<div class="rp-image"><div class="rp-image-box">'
      + (has ? '<img src="' + esc(iu) + '" alt="">' : '<span class="rp-image-empty">未设置</span>')
      + '</div><div class="rp-image-side">'
      + '<button class="rp-upload" type="button" data-rp-upload="' + key + '">上传图片</button>'
      + '<input type="file" data-rp-file="' + key + '" accept="image/png,image/jpeg,image/gif,image/webp,image/bmp" style="display:none">'
      + '<input class="rp-input" data-rp-text="' + key + '" placeholder="或粘贴图片链接" value="' + esc(iu) + '">'
      + '</div></div>';
  }
  if (c.type === 'formula') {
    return '<div class="rp-readonly rp-fx" data-rp-formula="' + key + '" title="公式：' + esc(c.expr || '') + '">' + esc(rpDisplay(displayFormat(c, v)) || '—') + '</div>';
  }
  if (c.type === 'select') {
    var sval = v ? String(v) : '';
    return '<div class="rp-pick" data-rp-pick="' + key + '" tabindex="0" role="button">'
      + (sval ? '<span class="tag ' + tagFor(sval, c) + '">' + esc(sval) + '</span>'
              : '<span class="rp-pick-empty">从下拉菜单选择</span>')
      + '<svg class="rp-caret" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg>'
      + '</div>';
  }
  if (c.type === 'multiselect') {
    var arr = Array.isArray(v) ? v.map(String) : (v ? [String(v)] : []);
    return '<div class="rp-tags" data-rp-tags="' + key + '">'
      + arr.map(function (x) {
        return '<span class="tag ' + tagFor(x, c) + '">' + esc(x)
          + '<button class="rp-tag-x" data-rp-rm="' + esc(x) + '" title="移除">×</button></span>';
      }).join('')
      + '<button class="rp-add" data-rp-add="' + key + '" type="button">＋ 添加</button></div>';
  }
  if (c.type === 'checkbox') {
    return '<label class="rp-check"><input type="checkbox" data-rp-chk="' + key + '"'
      + (v ? ' checked' : '') + '> 是</label>';
  }
  if (c.type === 'date') {
    return '<div class="date-field rp-date"><input class="rp-input" readonly data-rp-date="' + key + '" placeholder="选择日期" value="' + esc(String(v || '').slice(0, 10)) + '">'
      + (RP && RP.readonly ? '' : '<button type="button" class="date-btn" data-rp-date-btn="' + key + '">' + DATE_ICON_SVG + '</button>')
      + '</div>';
  }
  if (c.type === 'number') {
    return '<input class="rp-input" type="text" inputmode="decimal" autocomplete="off" data-rp-text="' + key + '" placeholder="请输入数字" value="'
      + esc(v === null || v === undefined ? '' : String(v)) + '">';
  }
  return '<input class="rp-input" data-rp-text="' + key + '" placeholder="请输入内容" value="'
    + esc(v === null || v === undefined ? '' : (c.type === 'text' ? readJsonCell(v) : String(v))) + '">';
}

function renderRowPanel() {
  if (!RP) return;
  var row = ROWDATA[RP.rid] || RP.row || {};
  var cols = curCols();
  $('rp-title').textContent = rpName(row, cols) || ('行 #' + RP.rid);
  $('rp-sub').textContent = '行 #' + RP.rid;
  $('rp-status').textContent = RP.readonly ? '只读' : '';
  var rpDel = $('rp-del'); if (rpDel) rpDel.style.display = RP.readonly ? 'none' : '';
  $('rp-body').innerHTML = cols.map(function (c) {
    return '<div class="rp-field" data-rp-field="' + esc(c.key) + '">'
      + '<div class="rp-label">' + rpIcon(c.type) + '<span title="' + esc(c.label) + '">' + esc(c.label) + '</span></div>'
      + '<div class="rp-val">' + rpFieldHtml(c, row[c.key]) + '</div></div>';
  }).join('');
  rpBindFields();
}

function rpRefresh(row) {
  if (!RP) return;
  row = row || ROWDATA[RP.rid] || RP.row || {};
  var cols = curCols();
  $('rp-title').textContent = rpName(row, cols) || ('行 #' + RP.rid);
  $('rp-sub').textContent = '行 #' + RP.rid;
  $('rp-body').querySelectorAll('[data-rp-formula]').forEach(function (el) {
    var k = el.getAttribute('data-rp-formula');
    el.textContent = rpDisplay(row[k]) || '—';
  });
}

async function rpSave(key, val, rerender) {
  if (!RP) return false;
  if (RP.readonly) { toast('只读权限，不能修改'); return false; }
  var col = colByKey(key);
  if (col && col.type === 'image') rerender = true;
  var rid = RP.rid;
  $('rp-status').textContent = '保存中…';
  try {
    var res = await saveRowValue(rid, key, val);
    RP.row = ROWDATA[rid] || res.row || {};
    if (!ROWDATA[rid]) { closeRowPanel(); return false; }
    $('rp-status').textContent = '已保存';
    if (rerender) {
      var sc = $('rp-body').scrollTop;
      renderRowPanel();
      $('rp-body').scrollTop = sc;
      $('rp-status').textContent = '已保存';
    } else {
      rpRefresh();
    }
    return true;
  } catch (e) {
    $('rp-status').textContent = '保存失败';
    toast('保存失败：' + e.message);
    // 乐观锁冲突：自动拉最新数据重绘面板，避免一直冲突
    if (String(e.message).indexOf('已被别人改过') !== -1) {
      invalidateRows(state.tid);
      loadRows().then(function () {
        if (RP && RP.rid === rid) {
          var sc = $('rp-body').scrollTop;
          renderRowPanel();
          $('rp-body').scrollTop = sc;
          $('rp-status').textContent = '内容已刷新，请重试';
        }
      });
    }
    return false;
  }
}

function closeRowPanel() {
  closeDatePicker();
  var p = $('row-panel');
  if (p) p.classList.remove('on');
  closeCellPop();
  RP = null;
}

function confirmDeleteRow(rid) {
  var t = curTable();
  if (!t) return;
  if (!canEditRows()) { toast('只读权限，不能删除'); return; }
  var nm = rpName(ROWDATA[rid] || {}, curCols()) || '';
  if (!nm || nm === '0') nm = 'ID ' + rid;   // 空行/纯数字 0 时显示行 ID
  var syncSub = (t.sync_sources && t.sync_sources.length)
    ? '<div class="del-row-sub del-row-sync">本表是汇总总表：如果这行是从子表汇总来的，子表里对应的那一行也会一起删除。</div>'
    : '';
  appConfirm('删除行',
    '确定删除「<b>' + esc(nm) + '</b>」？'
    + '<div class="del-row-sub">行 ID ' + rid + '，删除后从当前表移除。</div>' + syncSub,
    '删除', true).then(function (ok) {
    if (!ok) return;
    api('/api/tables/' + t.id + '/rows/' + rid, 'DELETE').then(function () {
      if (RP && RP.rid === rid) closeRowPanel();
      if (t.count > 0) t.count = t.count - 1;   // 分页里的「全部 N 行」跟着减，不用再拉一次元信息
      invalidateRows(state.tid);
      loadRows().then(function () { toast('已删除行 #' + rid); });
    }).catch(function (e) { toast('删除失败：' + e.message); });
  });
}

function toggleRowPanel(rid) {
  if (RP && RP.rid === rid && $('row-panel').classList.contains('on')) { closeRowPanel(); return; }
  openRowPanel(rid);
}
function openRowPanel(rid) {
  if (!ROWDATA[rid]) { toast('行数据还没加载'); return; }
  var t = curTable();
  RP = { rid: rid, row: ROWDATA[rid], readonly: !(t && t.my_role === 'editor') };
  $('row-panel').classList.add('on');
  renderRowPanel();
}

function rpOpenSelect(btn, c) {
  if (!RP || RP.readonly) return;
  closeCellPop();
  var cur = (ROWDATA[RP.rid] || {})[c.key];
  var curS = cur ? String(cur) : '';
  var opts = (c.options || []).map(String);
  if (curS && opts.indexOf(curS) === -1) opts.unshift(curS);
  var pop = document.createElement('div');
  pop.className = 'cell-pop';
  var r = btn.getBoundingClientRect();
  pop.style.left = Math.max(8, Math.min(r.left, window.innerWidth - 260)) + 'px';
  pop.style.top = Math.min(r.bottom + 4, window.innerHeight - 300) + 'px';
  pop.style.minWidth = Math.max(r.width, 190) + 'px';
  pop.innerHTML = opts.map(function (o) {
    return '<div class="cp-opt' + (curS === o ? ' on' : '') + '" data-v="' + esc(o) + '">'
      + '<span class="tag ' + tagFor(o, c) + '">' + esc(o) + '</span>'
      + (curS === o ? '<span class="cp-check">✓</span>' : '') + '</div>';
  }).join('') + '<div class="cp-opt cp-clear" data-v="">（空）</div>';
  document.body.appendChild(pop);
  cellPop = pop;
  pop.onclick = function (e) {
    e.stopPropagation();
    var it = e.target.closest('.cp-opt');
    if (!it) return;
    var v = it.getAttribute('data-v');
    closeCellPop();
    rpSave(c.key, v, true);
  };
  setTimeout(function () {
    document.addEventListener('click', function h(ev) {
      if (!ev.target.closest('.cell-pop')) { closeCellPop(); document.removeEventListener('click', h); }
    });
  }, 0);
}

function rpOpenMulti(btn, c) {
  if (!RP || RP.readonly) return;
  closeCellPop();
  var cur = ROWDATA[RP.rid] && ROWDATA[RP.rid][c.key];
  var arr = Array.isArray(cur) ? cur.map(String) : (cur ? [String(cur)] : []);
  var opts = (c.options || []).map(String);
  arr.forEach(function (x) { if (opts.indexOf(x) === -1) opts.unshift(x); });
  if (!opts.length) { toast('该字段还没有选项'); return; }
  var pop = document.createElement('div');
  pop.className = 'cell-pop cell-pop-multi';
  var r = btn.getBoundingClientRect();
  pop.style.left = Math.max(8, Math.min(r.left, window.innerWidth - 260)) + 'px';
  pop.style.top = Math.min(r.bottom + 4, window.innerHeight - 320) + 'px';
  pop.style.minWidth = '210px';
  pop.innerHTML = opts.map(function (o) {
    return '<label class="cp-opt cp-multi" data-v="' + esc(o) + '">'
      + '<input type="checkbox" value="' + esc(o) + '"' + (arr.indexOf(o) !== -1 ? ' checked' : '') + '>'
      + '<span class="tag ' + tagFor(o, c) + '">' + esc(o) + '</span></label>';
  }).join('')
    + '<div class="cp-foot"><button class="btn" type="button" data-act="cancel">取消</button>'
    + '<button class="btn btn-primary" type="button" data-act="ok">确定</button></div>';
  document.body.appendChild(pop);
  cellPop = pop;
  pop.onclick = function (e) { e.stopPropagation(); };
  pop.querySelector('[data-act="cancel"]').onclick = function () { closeCellPop(); };
  pop.querySelector('[data-act="ok"]').onclick = function () {
    var vals = Array.from(pop.querySelectorAll('input:checked')).map(function (x) { return x.value; });
    closeCellPop();
    rpSave(c.key, vals, true);
  };
  setTimeout(function () {
    document.addEventListener('click', function h(ev) {
      if (!ev.target.closest('.cell-pop')) { closeCellPop(); document.removeEventListener('click', h); }
    });
  }, 0);
}

function rpBindFields() {
  if (!RP || RP.readonly) return;
  var body = $('rp-body');
  body.querySelectorAll('[data-rp-text]').forEach(function (inp) {
    var key = inp.getAttribute('data-rp-text');
    var orig = inp.value;
    inp.onkeydown = function (e) {
      if (e.key === 'Enter') { e.preventDefault(); inp.blur(); }
      else if (e.key === 'Escape') {
        e.stopPropagation();
        inp.value = orig;
        inp._cancel = true;
        inp.blur();
      }
    };
    inp.onblur = function () {
      if (inp._cancel) { inp._cancel = false; return; }
      var v = inp.value;
      if (v === orig) return;
      orig = v;
      rpSave(key, v, false);
    };
  });
  body.querySelectorAll('[data-rp-date]').forEach(function (inp) {
    var key = inp.getAttribute('data-rp-date');
    inp.onchange = function () { rpSave(key, inp.value, false); };
    inp.onclick = function () { openDatePicker(inp); };
    var db = body.querySelector('[data-rp-date-btn="' + CSS.escape(key) + '"]');
    if (db) db.onclick = function (e) { e.preventDefault(); openDatePicker(inp); };
  });
  body.querySelectorAll('[data-rp-pick]').forEach(function (btn) {
    var key = btn.getAttribute('data-rp-pick');
    var c = colByKey(key);
    if (!c) return;
    btn.onclick = function (e) { e.stopPropagation(); rpOpenSelect(btn, c); };
    btn.onkeydown = function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); rpOpenSelect(btn, c); }
    };
  });
  body.querySelectorAll('[data-rp-chk]').forEach(function (chk) {
    chk.onchange = function () { rpSave(chk.getAttribute('data-rp-chk'), chk.checked, false); };
  });
  body.querySelectorAll('[data-rp-upload]').forEach(function (btn) {
    btn.onclick = function (e) {
      e.stopPropagation();
      var key = btn.getAttribute('data-rp-upload');
      var fi = body.querySelector('[data-rp-file="' + CSS.escape(key) + '"]');
      if (fi) fi.click();
    };
  });
  body.querySelectorAll('[data-rp-file]').forEach(function (inp) {
    inp.onchange = async function () {
      var f = this.files && this.files[0];
      var key = inp.getAttribute('data-rp-file');
      this.value = '';
      if (!f) return;
      if (f.size > 8 * 1024 * 1024) { toast('图片不能超过 8MB'); return; }
      $('rp-status').textContent = '上传中…';
      try {
        var d = await uploadImageFile(state.tid, f);
        await rpSave(key, d.url, true);
      } catch (e) {
        $('rp-status').textContent = '上传失败';
        toast('上传失败：' + e.message);
      }
    };
  });
  body.querySelectorAll('[data-rp-tags]').forEach(function (wrap) {
    var key = wrap.getAttribute('data-rp-tags');
    var c = colByKey(key);
    wrap.querySelectorAll('[data-rp-rm]').forEach(function (btn) {
      btn.onclick = function (e) {
        e.stopPropagation();
        var cur = (ROWDATA[RP.rid] || {})[key];
        var arr = Array.isArray(cur) ? cur.map(String) : (cur ? [String(cur)] : []);
        var v = btn.getAttribute('data-rp-rm');
        rpSave(key, arr.filter(function (x) { return x !== v; }), true);
      };
    });
    var add = wrap.querySelector('[data-rp-add]');
    if (add) add.onclick = function (e) { e.stopPropagation(); rpOpenMulti(add, c); };
  });
}

$('rp-close').onclick = closeRowPanel;
$('rp-del').onclick = function () { if (RP) confirmDeleteRow(RP.rid); };
$('rp-title').ondblclick = closeRowPanel;
document.addEventListener('keydown', function (e) {
  if (e.key !== 'Escape' || !RP) return;
  if (e.target.closest('#row-panel input, #row-panel textarea, #row-panel select')) return;
  closeRowPanel();
});

