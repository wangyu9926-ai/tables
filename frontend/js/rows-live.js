/* rows-live.js — index.html 内联脚本拆出的第 6/12 段（原第 3252-3922 行）。
   加载顺序见 index.html 底部；全部是全局函数，跨文件可直接调用。 */
/* ============ 行 ============ */
/* 列级显示格式：日期 / 数字精度 / 千分位 / 百分比。
   存在 kb_column.options.display_format 里，不改变原始存储值。 */
function displayFormat(c,v){
  if(v===null||v===undefined||v==='') return v;
  var opts=c&&c.options;
  var df=(opts&&!Array.isArray(opts))?opts.display_format:null;
  if(!df) return v;
  if(df.kind==='date'){
    var m=/^(\d{4})-(\d{2})-(\d{2})/.exec(String(v));
    if(!m) return v;
    var p=df.pattern||'YYYY-MM-DD';
    return p.replace(/YYYY/g,m[1]).replace(/MM/g,m[2]).replace(/DD/g,m[3]);
  }
  if(df.kind==='number'){
    var n=Number(v);
    if(!isFinite(n)) return v;
    if(df.percent) n*=100;
    var prec=Math.max(0,Math.min(6,parseInt(df.precision,10)||0));
    var s=n.toFixed(prec);
    if(df.thousand){
      var parts=s.split('.');
      parts[0]=parts[0].replace(/\B(?=(\d{3})+(?!\d))/g,',');
      s=parts.join('.');
    }
    if(df.percent) s+='%';
    return s;
  }
  return v;
}
/* 防御性：历史数据/旧接口里偶尔会把 {url,name,...} 原样塞进文本列。
   表格里只显示人能读的 name/url/text，不再把 JSON 大括号甩给用户。 */
function readJsonCell(v){
  if(typeof v!=='string') return v;
  var s=v.trim();
  if(!s || (s[0]!=='{' && s[0]!=='[')) return v;
  try {
    var o=JSON.parse(s);
    function flat(x, depth){
      if(x===null||x===undefined) return '';
      if(typeof x==='string'){
        var t=x.trim();
        if(t && (t[0]==='{'||t[0]==='[')){
          try{ return flat(JSON.parse(t),depth+1); }catch(e){}
        }
        return x;
      }
      if(typeof x!=='object') return String(x);
      if(depth>4) return '';
      if(Array.isArray(x)) return x.map(function(y){return flat(y,depth+1);}).filter(Boolean).join('、');
      var keys=['name','label','text','url','value','title','nick','identifier'];
      for(var i=0;i<keys.length;i++){ if(x[keys[i]]!==undefined && x[keys[i]]!==null && String(x[keys[i]]).trim()!=='') return String(x[keys[i]]); }
      if(x.data!==undefined) { var d=flat(x.data,depth+1); if(d) return d; }
      var vals=[];
      Object.keys(x).forEach(function(k){ var z=flat(x[k],depth+1); if(z) vals.push(z); });
      return vals.slice(0,3).join(' ');
    }
    var out=flat(o,0);
    return out || '—';
  } catch(e){ return v; }
}
function cellHtml(v,c){
  if(v===null||v===undefined||v===''||(Array.isArray(v)&&!v.length)) return '<span class="cell-empty">—</span>';
  if(c.type==='text') v=readJsonCell(v);
  if(c.type==='number'||c.type==='date'||c.type==='formula'||c.type==='text') v=displayFormat(c,v);
  var s=String(v);
  if(c.type==='checkbox') return '<span class="tag '+(v?'tag-green':'tag-gray')+'">'+(v?'✓ 是':'否')+'</span>';
  if(c.type==='select') return '<span class="tag '+tagFor(s,c)+'">'+esc(s)+'</span>';
  if(c.type==='multiselect') return (Array.isArray(v)?v:[v]).map(function(x){return '<span class="tag '+tagFor(x,c)+'">'+esc(x)+'</span>';}).join(' ');
  if(c.type==='image'){
    if(/^(https?:\/\/|\/api\/tables\/)/i.test(s)) return '<img class="cell-img" src="'+esc(s)+'" loading="lazy" alt="" title="双击修改图片">';
    return esc(s);
  }
  if(/^https?:\/\//i.test(s)) return '<a class="cell-link" href="'+esc(s)+'" target="_blank" title="'+esc(s)+'">'+esc(s)+'</a>';
  return esc(s);
}
/* 行数据本地缓存：切 Sheet / 切表先用上次结果秒开，再后台对齐。
   结构变更清空全部，行变更只清对应的那张表（见 invalidateRows）；最多保留 ROWS_CACHE_MAX 份。 */
var ROWS_CACHE = {}, rowsSeq = 0;

function rowsKey(tid, page, kw, sort, dir, filters, size) {
  return tid + '|' + (size === undefined ? state.size : size) + '|' + page + '|' + kw + '|' + sort + '|' + dir + '|' + JSON.stringify(filters || {});
}
function curRowsKey(tid) {
  return rowsKey(tid, state.page, state.kw, state.sort, state.dir, state.filters, state.size);
}
var ROWS_CACHE_MAX = 30;   // 同组各 Sheet 的第 1 页 + 一些翻页/筛选结果
function rowsCachePut(key, d) {
  delete ROWS_CACHE[key];              // 先删再放：最近用过的排在最后，淘汰时从最旧的开始
  ROWS_CACHE[key] = d;
  var ks = Object.keys(ROWS_CACHE);
  for (var i = 0; i < ks.length - ROWS_CACHE_MAX; i++) delete ROWS_CACHE[ks[i]];
}
/* 行缓存失效：结构变了（建表 / 删表 / 改列 / 改名）清全部；
   只是某一张表的行变了，就只清这一张的 —— 同组其它 Sheet 预取好的数据照样能用。 */
function invalidateRows(tid) {
  if (tid === undefined || tid === null) { ROWS_CACHE = {}; return; }
  var pre = tid + '|';
  Object.keys(ROWS_CACHE).forEach(function (k) { if (k.lastIndexOf(pre, 0) === 0) delete ROWS_CACHE[k]; });
}

var ROWDATA = {};
var GRID = null, GRID_RAF = 0;

/* 大页只渲染可视窗口：300 行 × 29 列不再一次性塞 8700 个单元格。
   行高固定 40/42px，用上下占位行撑住总高度，滚动时换窗口。 */
function gridRowH() {
  return (window.matchMedia && window.matchMedia('(max-width:860px)').matches) ? 42 : 40;
}
function gridRowHtml(r, gi, cols, canEdit) {
  var idx = state.size > 0 ? (state.page - 1) * state.size + gi + 1 : gi + 1;
  var html = '<tr data-rid="' + r.id + '"><td class="idx-col" title="行 #' + r.id + ' · 双击打开/关闭行详情 · 拖动可调整顺序">' + idx + '</td>';
  cols.forEach(function (c) {
    var isF = c.type === 'formula';
    var cv = r.data[c.key];
    var ttl = (c.type === 'formula' && c.expr)
      ? ' title="公式：' + esc(c.expr) + '"'
      : ((c.type === 'text' && cv != null && String(cv).length > 16)
        ? ' title="' + esc(String(cv)) + '"' : '');
    var cn = (CMT_SUM[state.tid] || {})[cmtKey(r.id, c.key)] || 0;
    html += '<td data-f="' + esc(c.key) + '" class="' + (isF ? 'formula' : (c.type === 'number' || c.type === 'formula' ? 'num ' : '') + 'editable') + '"'
      + ' data-ver="' + r.ver + '"' + ttl + '>' + cellHtml(cv, c)
      + (cn ? '<span class="cmt-dot" data-cmt="' + r.id + '|' + esc(c.key) + '" title="' + cn + ' 条批注">' + cn + '</span>' : '')
      + '</td>';
  });
  html += '<td class="row-ops">'
    + (canEdit ? '<button class="row-del" data-del-row="' + r.id + '" title="删除此行" aria-label="删除此行"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"/></svg></button>' : '')
    + '</td></tr>';
  return html;
}
function drawGridWindow(force) {
  if (!GRID) return;
  var wrap = document.querySelector('.tbl-wrap');
  GRID.rowH = gridRowH();
  var total = GRID.d.rows.length, rowH = GRID.rowH;
  var start = 0, end = total;
  if (total > 120 && wrap) {
    var visible = Math.max(1, Math.ceil(wrap.clientHeight / rowH));
    var buffer = 10;
    var first = Math.max(0, Math.floor(wrap.scrollTop / rowH));
    start = Math.max(0, first - buffer);
    end = Math.min(total, start + visible + buffer * 2);
  }
  if (!force && GRID.start === start && GRID.end === end) return;
  GRID.start = start; GRID.end = end;
  hideRowInsert();
  var html = '', colspan = GRID.cols.length + 2;
  if (start > 0) html += '<tr class="vsp"><td colspan="' + colspan + '" style="height:' + (start * rowH) + 'px"></td></tr>';
  for (var i = start; i < end; i++) html += gridRowHtml(GRID.d.rows[i], i, GRID.cols, GRID.canEdit);
  if (end < total) html += '<tr class="vsp"><td colspan="' + colspan + '" style="height:' + ((total - end) * rowH) + 'px"></td></tr>';
  $('tbody').innerHTML = html;
  paintSel();
}
function onGridScroll() {
  hideRowInsert();
  var wrapEl = document.querySelector('.tbl-wrap');
  if (wrapEl) wrapEl.classList.toggle('scrolled', wrapEl.scrollTop > 4);
  var ae = document.activeElement;
  if (ae && ae.closest && ae.closest('.mt-editor')) { try { ae.blur(); } catch (e) {} }
  if (GRID_RAF) return;
  GRID_RAF = setTimeout(function () { GRID_RAF = 0; drawGridWindow(false); }, 16);
}
function gridRowIndex(rid) {
  if (!GRID) return -1;
  for (var i = 0; i < GRID.d.rows.length; i++) if (GRID.d.rows[i].id === rid) return i;
  return -1;
}
function revealRow(rid) {
  if (!GRID) return false;
  var i = gridRowIndex(rid); if (i < 0) return false;
  var wrap = document.querySelector('.tbl-wrap'); if (!wrap) return false;
  var rowH = GRID.rowH, top = i * rowH;
  if (top < wrap.scrollTop || top + rowH > wrap.scrollTop + wrap.clientHeight) {
    wrap.scrollTop = Math.max(0, top - Math.floor(wrap.clientHeight / 2) + rowH / 2);
    drawGridWindow(true);
  }
  return true;
}
function initGridEvents() {
  /* 拖选：按住左键从一格拖到另一格；Shift+单击从锚点扩展 */
  var tb = $('tbody');
  if (tb) {
    tb.addEventListener('mousedown', function (e) {
      if (e.button !== 0) return;
      if (e.target.closest('button') || e.target.closest('a')) return;
      var td = e.target.closest('td[data-f]');
      if (!td || !GRID) return;
      SEL_DRAG = true;
      selectCell(td, e.shiftKey);
      e.preventDefault();
    });
    document.addEventListener('mousemove', function (e) {
      if (!SEL_DRAG || !SEL) return;
      var el = document.elementFromPoint(e.clientX, e.clientY);
      var td = el && el.closest && el.closest('#tbody td[data-f]');
      if (!td) return;
      var rc = cellRC(td); if (!rc) return;
      if (SEL.rid1 !== rc.rid || SEL.key1 !== rc.key) {
        SEL.rid1 = rc.rid; SEL.key1 = rc.key; paintSel();
      }
    });
    document.addEventListener('mouseup', function () { SEL_DRAG = false; });
  }
  var wrap = document.querySelector('.tbl-wrap');
  if (wrap) wrap.addEventListener('scroll', onGridScroll, { passive: true });
  window.addEventListener('resize', function () {
    if (!GRID) return;
    GRID.rowH = gridRowH();
    drawGridWindow(true);
  });
}
/* 键盘习惯（表格软件里通用的那一套）：单击选中一格之后
   Enter / F2 进入编辑；直接打字就用这个字开始改；
   ← → 左右移动；Esc 取消选中。Tab 在编辑框里是“提交并跳到右边一格”。 */
document.addEventListener('keydown', function (e) {
  if (e.defaultPrevented || e.isComposing || e.key === 'Process') return;
  var el = document.activeElement, tag = el && el.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || tag === 'BUTTON' || tag === 'A'
      || (el && el.isContentEditable)) return;          // 焦点在控件上时交给控件自己
  if (!$('table-view') || !$('table-view').classList.contains('on')) return;
  if ($('mask') || openFilter || document.querySelector('.cell-pop')) return;   // 弹窗/浮层打开时让它们处理键盘
  if ((e.ctrlKey || e.metaKey) && !e.altKey && (e.key === 'c' || e.key === 'C')) {
    if (copySelection()) e.preventDefault();
    return;
  }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  var sel = document.querySelector('#tbody td.cell-sel');
  if (!sel || !sel.isConnected) return;
  if (e.key === 'Enter' || e.key === 'F2') { e.preventDefault(); openEdit(sel); return; }
  if (e.key === 'Escape') { clearCellSel(); return; }
  if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); clearSelectionCells(); return; }
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight' || e.key === 'ArrowUp' || e.key === 'ArrowDown') {
    e.preventDefault();
    var dr = e.key === 'ArrowDown' ? 1 : e.key === 'ArrowUp' ? -1 : 0;
    var dc = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
    moveSel(dr, dc, e.shiftKey);
    return;
  }
  if (e.key.length === 1 && e.key !== ' ') {
    var cc = colByKey(sel.getAttribute('data-f'));
    if (cc && (cc.type === 'text' || cc.type === 'number')) { e.preventDefault(); openEdit(sel, e.key); }
  }
});
function renderRows(d) {
  hideRowInsert();
  FACETS = d.facets || {};
  ROWDATA = {};
  d.rows.forEach(function (r) { ROWDATA[r.id] = r.data; });
  if (RP && !d.rows.some(function (r) { return r.id === RP.rid; })) closeRowPanel();
  var pages = state.size > 0 ? Math.max(1, Math.ceil(d.total / state.size)) : 1;
  var cols = curCols();
  var canEdit = canEditRows();
  GRID = { d: d, cols: cols, canEdit: canEdit, rowH: gridRowH(), start: -1, end: -1 };
  ensureServerViews(state.tid);          // 团队视图：打开表时预取，视图菜单里直接可用
  drawGridWindow(true);
  var emptyEl = $('empty');
  if (d.total) { emptyEl.style.display = 'none'; }
  else {
    emptyEl.style.display = 'block';
    if (viewDirty()) {
      // 有数据但被筛没了：明确告诉你是筛掉的，并给一键清掉，而不是只说“还没有数据”
      emptyEl.innerHTML = '<div>没有符合搜索/筛选条件的行</div>'
        + '<div class="empty-sub">共 ' + ((curTable() || {}).count || 0) + ' 行，都被当前的搜索/筛选条件挡住了</div>'
        + '<button class="btn" id="empty-reset" type="button">清除搜索 / 筛选 / 排序</button>';
      var er = $('empty-reset'); if (er) er.onclick = resetView;
    } else {
      emptyEl.innerHTML = canEdit
        ? '<div>还没有数据</div><div class="empty-sub">点右上角「＋ 行」添加第一行，或用「导入」把 Excel 贴进来</div>'
        : '<div>还没有数据</div>';
    }
  }
  var btns = ''; var pp = pagerPages(state.page, pages);
  btns += '<button class="pg-btn" data-p="' + (state.page - 1) + '"' + (state.page <= 1 ? ' disabled' : '') + '>‹</button>';
  pp.forEach(function (x) { btns += x === '...' ? '<span>…</span>' : '<button class="pg-btn' + (x === state.page ? ' on' : '') + '" data-p="' + x + '">' + x + '</button>'; });
  btns += '<button class="pg-btn" data-p="' + (state.page + 1) + '"' + (state.page >= pages ? ' disabled' : '') + '>›</button>';
  $('pg-btns').innerHTML = btns;
  $('pg-btns').querySelectorAll('.pg-btn').forEach(function (b) { b.onclick = function () { if (!b.disabled) { state.page = +b.getAttribute('data-p'); loadRows(); } }; });
  var pgT = $('pg-total');
  if (pgT) {
    var allN = (curTable() || {}).count;
    var narrowed = !!(state.kw || hasAnyFilter());     // 只有搜索/筛选会减少行数，排序不会
    if (narrowed && allN) pgT.textContent = '筛选后 ' + d.total + ' 行（全部 ' + Math.max(allN, d.total) + ' 行）';
    else pgT.textContent = '共 ' + d.total + ' 行';
  }
  updateViewChrome();
  updateFunnels(); bindCells();
  saveView();   // 搜索/筛选/翻页/排序变化都随刷刷新回到原位
}

async function loadRows() {
  var t = curTable(); if (!t) return;
  ensureCommentSummary(t.id);            // 批注角标：每张表拉一次汇总，之后走缓存
  var my = ++rowsSeq;
  var key = curRowsKey(t.id);
  var hit = ROWS_CACHE[key];
  if (hit) renderRows(hit);                 // 先用缓存画，交互零等待
  var p = new URLSearchParams({ page: state.page, size: state.size, q: state.kw, sort: state.sort,
    dir: state.dir > 0 ? 'asc' : 'desc', filters: JSON.stringify(state.filters) });
  var d = await api('/api/tables/' + t.id + '/rows?' + p);
  if (my !== rowsSeq) return;               // 期间切走了，丢弃返回
  var pages = state.size > 0 ? Math.max(1, Math.ceil(d.total / state.size)) : 1;
  if (state.page > pages) { state.page = pages; return loadRows(); }
  var same = hit && JSON.stringify(hit) === JSON.stringify(d);
  rowsCachePut(key, d);                   // 只更新这一份，不再把别的 Sheet 预取好的数据一起清掉
  if (!same) renderRows(d);                 // 行数据没变就不二次重绘，避免闪烁
}

/* ============ 切回页面时静默刷新当前页 ============
   别人刚改的数据不用手动刷新/重启就能看到：标签页从后台切回来、窗口重新获得焦点时，
   静默重取当前页（数据没变就不重绘，滚动位置不动，也不显示加载动画）。
   正在编辑格子、行编辑侧栏开着、弹窗里在填表、或者正在跑「删除/导入」这类忙碌操作时一律跳过，
   免得把你手上没提交的输入冲掉。 */
var SOFT_REF_AT = 0, SOFT_REF_TIMER = null;
function softRefreshRows() {
  if (!state.tid || !curTable()) return;
  if (editing || BUSY_TIMER) return;
  if (document.querySelector('#modal-root .box') || document.querySelector('#row-panel.on')) return;
  if (!document.getElementById('table-view').classList.contains('on')) return;   // 不在表格页（分析页等）
  var now = Date.now();
  if (now - SOFT_REF_AT < 4000) return;      // 频繁切窗不反复打请求
  SOFT_REF_AT = now;
  if (LIVE.tid === state.tid) liveRefresh(); else loadRows();   // 能只补变化的行就不整页重绘
}
function scheduleSoftRefresh() {
  if (SOFT_REF_TIMER) clearTimeout(SOFT_REF_TIMER);
  SOFT_REF_TIMER = setTimeout(function () { SOFT_REF_TIMER = null; softRefreshRows(); }, 400);
}
document.addEventListener('visibilitychange', function () { if (!document.hidden) scheduleSoftRefresh(); });
window.addEventListener('focus', scheduleSoftRefresh);

/* ============ 实时刷新：服务器推送（SSE）+ 轮询兜底 ============
   页面开着某张表时挂一条长连接；服务端有人写入就推「哪几行变了」。
   收到提示只做一件事：静默重取当前页（数据没变就不重绘，滚动位置不动）。
   并发与冲突：多条提示 300ms 内合并成一次；正在取数时先记下、取完再补一次；
   正在编辑格子 / 行编辑侧栏开着 / 弹窗里在填表 / 忙碌遮罩时先攒着，等手上的动作结束再刷新。
   推送只带"哪几行变了"、不带数据，所以永远不会把你没保存的输入冲掉；
   两个人同时改同一格由服务端的版本号（乐观锁）裁决，后保存的那个会收到 409 提示。 */
var LIVE = { es: null, tid: null, poll: null, timer: null, retry: null, lastSeq: 0,
             inflight: false, dirty: false, pending: false, failed: 0, updateIds: null };
function liveClose() {
  if (LIVE.es) { try { LIVE.es.close(); } catch (e) {} LIVE.es = null; }
  if (LIVE.poll) { clearInterval(LIVE.poll); LIVE.poll = null; }
  if (LIVE.timer) { clearTimeout(LIVE.timer); LIVE.timer = null; }
  if (LIVE.retry) { clearTimeout(LIVE.retry); LIVE.retry = null; }
  LIVE.tid = null; LIVE.lastSeq = 0; LIVE.dirty = false; LIVE.pending = false; LIVE.failed = 0;
  LIVE.updateIds = null;
}
function liveBusy() {
  return !!(editing || BUSY_TIMER || document.querySelector('#modal-root .box')
            || document.querySelector('#row-panel.on'));
}
/* 最小化刷新：先按变更日志判断，能只补一行就只补那一行，其他行一个字节都不动。
   只有行集合变了（新增/删除/翻页/总数变化）才退回整页重绘。 */
function liveFetchPage() {
  var t = curTable(); if (!t) return Promise.reject(new Error('no table'));
  var p = new URLSearchParams({ page: state.page, size: state.size, q: state.kw, sort: state.sort,
    dir: state.dir > 0 ? 'asc' : 'desc', filters: JSON.stringify(state.filters) });
  return api('/api/tables/' + t.id + '/rows?' + p);
}
function livePatchRows(d) {
  var g = GRID;
  if (!g || !g.d || !d || !Array.isArray(d.rows)) return false;
  var byId = {}, i;
  g.d.rows.forEach(function (r) { byId[r.id] = r; });
  var sameSet = d.rows.length === g.d.rows.length &&
                d.rows.every(function (r) { return !!byId[r.id]; });
  if (!sameSet || d.total !== g.d.total) return false;      // 行集合/总数变了 → 整页重绘
  var changed = [];
  d.rows.forEach(function (r, idx) {
    var o = byId[r.id];
    if (!o || o.ver !== r.ver || JSON.stringify(o.data) !== JSON.stringify(r.data)) changed.push(idx);
  });
  g.d = d; FACETS = d.facets || FACETS;
  ROWDATA = {}; d.rows.forEach(function (r) { ROWDATA[r.id] = r.data; });
  try { rowsCachePut(curRowsKey(state.tid), d); } catch (e) {}
  changed.forEach(function (idx) {
    var r = d.rows[idx];
    var tr = document.querySelector('#tbody tr[data-rid="' + r.id + '"]');
    if (!tr || !tr.parentNode) return;
    // 这一行正在编辑：先不动它，等编辑结束（下次变更/切回页面）再补
    if (editing && tr.querySelector('input, textarea, [contenteditable="true"]')) return;
    var tmp = document.createElement('tbody');
    tmp.innerHTML = gridRowHtml(r, idx, g.cols, g.canEdit);
    var ntr = tmp.firstElementChild;
    if (ntr) tr.parentNode.replaceChild(ntr, tr);
  });
  return true;
}
/* 只重画指定的几行（配合 /rows/by-ids：变更日志说哪几行变了就只取、只画这几行） */
function livePatchSomeRows(rows) {
  var g = GRID;
  if (!g || !g.d) return false;
  var byIdx = {};
  g.d.rows.forEach(function (r, i) { byIdx[r.id] = i; });
  var cache = ROWS_CACHE[curRowsKey(state.tid)];
  (rows || []).forEach(function (r) {
    var i = byIdx[r.id];
    if (i === undefined) return;                       // 不在当前页：不用重画
    g.d.rows[i] = r; ROWDATA[r.id] = r.data;
    if (cache && cache.rows) {
      for (var ci = 0; ci < cache.rows.length; ci++) if (cache.rows[ci].id === r.id) cache.rows[ci] = r;
    }
    var tr = document.querySelector('#tbody tr[data-rid="' + r.id + '"]');
    if (!tr || !tr.parentNode) return;
    if (editing && tr.querySelector('input, textarea, [contenteditable="true"]')) return;
    var tmp = document.createElement('tbody');
    tmp.innerHTML = gridRowHtml(r, i, g.cols, g.canEdit);
    var ntr = tmp.firstElementChild;
    if (ntr) tr.parentNode.replaceChild(ntr, tr);
  });
  return true;
}
function liveRefresh() {
  if (!state.tid || LIVE.tid !== state.tid) return;
  if (!document.getElementById('table-view').classList.contains('on')) return;
  if (liveBusy()) { LIVE.pending = true; liveRetryLater(); return; }   // 手上有动作，先攒着
  LIVE.pending = false;
  if (LIVE.inflight) { LIVE.dirty = true; return; }
  var ids = LIVE.updateIds; LIVE.updateIds = null;
  var fast = !!(ids && ids.length);
  var req = fast ? api('/api/tables/' + state.tid + '/rows/by-ids?ids=' + ids.join(','))
                 : liveFetchPage();
  LIVE.inflight = true;
  req.then(function (d) {
    LIVE.inflight = false;
    if (LIVE.dirty) { LIVE.dirty = false; liveSchedule(); }
    var patched = false;
    try { patched = fast ? livePatchSomeRows(d && d.rows) : livePatchRows(d); } catch (e) { patched = false; }
    if (!patched) renderRows(d);            // 行集合变了才整页重绘
  }).catch(function () { LIVE.inflight = false; });
}
function liveRetryLater() {
  if (LIVE.retry) return;
  LIVE.retry = setTimeout(function () {
    LIVE.retry = null;
    if (!LIVE.pending || LIVE.tid !== state.tid) return;
    if (liveBusy()) { liveRetryLater(); return; }
    LIVE.pending = false; liveSchedule();
  }, 1000);
}
function liveSchedule() {
  if (LIVE.timer) return;                                   // 15ms 内的多次提示合并成一次
  LIVE.timer = setTimeout(function () {
    LIVE.timer = null;
    if (LIVE.inflight) { LIVE.dirty = true; return; }       // 正在取数：取完再补
    liveRefresh();
  }, 15);
}
function liveOnChange(msg) {
  try {
    var evs = (msg && msg.events) || [], me = (ME && ME.username) || '';
    var reset = false;
    if (msg && typeof msg.seq === 'number' && msg.seq !== LIVE.lastSeq) {
      // 服务重启过：进程里的序号从 0 重新开始，比手里的还小 → 整页重取一次（否则这个页面以后永远收不到新变更）
      reset = msg.seq < LIVE.lastSeq;
      LIVE.lastSeq = msg.seq;
    }
    if (evs.some(function (e) { return e.kind === 'reset'; })) reset = true;
    if (reset) { LIVE.updateIds = null; liveSchedule(); return; }
    if (!evs.length) return;              // 序号对齐了、但没有变化：什么都不用刷
    // 自己刚保存触发的提示不用再刷一次（数据就是自己刚写的）
    if (evs.length && me && evs.every(function (e) { return e.actor === me; })) return;
    // 批注变了：角标计数要重取（这个只能整表重画可见行，批注本来就少）
    if (evs.length && evs.every(function (e) { return e.kind === 'comment'; }) && state.tid) {
      delete CMT_SUM[state.tid];
      ensureCommentSummary(state.tid);
      return;
    }
    // 全是"改行"且都在当前页 → 只取这几行（最小化刷新）；有增删/拖动/结构变化 → 走整页
    var upd = evs.filter(function (e) { return e.kind === 'update'; });
    if (evs.length && upd.length === evs.length) {
      var ids = [];
      upd.forEach(function (e) { (e.rows || []).forEach(function (id) { if (ids.indexOf(id) === -1) ids.push(id); }); });
      var onPage = ids.length > 0 && ids.length <= 20 && ids.every(function (id) {
        return !!(GRID && GRID.d && GRID.d.rows.some(function (r) { return r.id === id; }));
      });
      LIVE.updateIds = onPage ? ids : null;
    } else {
      LIVE.updateIds = null;
    }
  } catch (e) {}
  liveSchedule();
}
function liveStartPolling() {
  if (LIVE.es) { try { LIVE.es.close(); } catch (e) {} LIVE.es = null; }
  if (LIVE.poll || !LIVE.tid) return;
  LIVE.poll = setInterval(function () {
    if (!state.tid || LIVE.tid !== state.tid || document.hidden || liveBusy()) return;
    api('/api/tables/' + state.tid + '/changes?since=' + LIVE.lastSeq).then(function (d) {
      if (d) liveOnChange(d);
    }).catch(function () {});
  }, 5000);
}
function liveConnect() {
  liveClose();
  if (!state.tid) return;
  LIVE.tid = state.tid;
  if (!window.EventSource) { liveStartPolling(); return; }
  try {
    var es = new EventSource('/api/tables/' + state.tid + '/events');
    LIVE.es = es;
    es.addEventListener('hello', function (e) {
      LIVE.failed = 0;
      var s = 0;
      try { s = (JSON.parse(e.data) || {}).seq || 0; } catch (err) {}
      if (s < LIVE.lastSeq) { LIVE.updateIds = null; liveSchedule(); }   // 服务重启过：序号倒退，整页重取
      LIVE.lastSeq = s;
    });
    es.addEventListener('change', function (e) {
      try { liveOnChange(JSON.parse(e.data)); } catch (err) {}
    });
    es.onerror = function () {
      // EventSource 自己会重连；彻底断了（CLOSED）或连续失败多次才退回轮询
      LIVE.failed++;
      if (es.readyState === 2 || LIVE.failed > 6) liveStartPolling();
    };
  } catch (e) { liveStartPolling(); }
}

/* 空闲时只预取当前分组里最近的 2~4 个 Sheet；17 张全预取会平白多传 1.6MB、拖慢首屏 */
function prefetchRows() {
  if (!state.tid || !document.getElementById('table-view').classList.contains('on')) return;
  var grp = curGroup();
  var maxN = state.size >= 200 ? 2 : 4;
  var todo = ((grp && grp.sheets) || []).filter(function (t) { return t.id !== state.tid; }).slice(0, maxN);
  var idle = window.requestIdleCallback || function (fn) { return setTimeout(fn, 300); };
  (function next() {
    var t = todo.shift(); if (!t) return;
    var key = rowsKey(t.id, 1, '', '', 1, {}, state.size);
    if (ROWS_CACHE[key] || (state.tid === t.id && state.page === 1 && !state.kw && !state.sort)) { idle(next, {timeout: 2000}); return; }
    idle(function () {
      fetch('/api/tables/' + t.id + '/rows?page=1&size=' + state.size + '&q=&sort=&dir=asc&filters=%7B%7D')
        .then(function (r) { return r.json(); })
        .then(function (d) { rowsCachePut(key, d); })
        .catch(function () {})
        .then(function () { setTimeout(next, 200); });
    }, {timeout: 4000});
  })();
}
function pagerPages(cur,total){
  if(total<=7){var a=[];for(var i=1;i<=total;i++)a.push(i);return a;}
  var s=[1,total,cur-1,cur,cur+1].filter(function(x){return x>=1&&x<=total;}).sort(function(a,b){return a-b;});
  var o=[],last=0; s.forEach(function(x){ if(x-last>1)o.push('...'); if(o[o.length-1]!==x)o.push(x); last=x; }); return o;
}
function updateFunnels(){
  var ak=advFilterKeys();
  $('thead').querySelectorAll('.th-f[data-c]').forEach(function(f){
    var k=f.getAttribute('data-c'), c=colByKey(k);
    f.classList.toggle('on',filterActive(k)||ak.indexOf(k)!==-1);
    f.style.display='';
  });
}

/* 双击编辑（带 ver 乐观锁，公式列不可编辑） */
function bindCells() { /* 事件已用委托绑在 tbody 上，重绘时无需逐格绑定 */ }

/* ---------- 行号间悬浮 +：在指定位置插入一行 ---------- */
var ROW_INS_EL = null;
function hideRowInsert() {
  if (ROW_INS_EL) ROW_INS_EL.classList.remove('on');
  ROW_INS_EL && (ROW_INS_EL._target = null);
}
function initRowInsert() {
  var btn = document.createElement('button');
  btn.type = 'button'; btn.className = 'row-ins'; btn.id = 'row-ins';
  btn.title = '在此处插入一行'; btn.setAttribute('aria-label', '在此处插入一行');
  btn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>';
  document.body.appendChild(btn);
  ROW_INS_EL = btn;
  btn.addEventListener('mousedown', function (e) { e.preventDefault(); });

  function canInsert() {
    return !!(state.tid && canEditRows() && !state.kw && !state.sort
      && (!state.filters || !Object.keys(state.filters).length)
      && window.matchMedia && window.matchMedia('(hover:hover)').matches);
  }

  document.addEventListener('mousemove', function (e) {
    if (e.target === btn || btn.contains(e.target)) return;   // 移到按钮上时不重新计算/隐藏
    if (!canInsert()) { hideRowInsert(); return; }
    var td = e.target.closest && e.target.closest('td.idx-col');
    if (!td) { hideRowInsert(); return; }
    var tr = td.closest('tr'); if (!tr) { hideRowInsert(); return; }
    var rect = tr.getBoundingClientRect();
    var dTop = Math.abs(e.clientY - rect.top), dBottom = Math.abs(e.clientY - rect.bottom);
    if (Math.min(dTop, dBottom) > 14) { hideRowInsert(); return; }   // 只在行号上下边界附近出现
    var rid = +tr.getAttribute('data-rid');
    var atTop = dTop <= dBottom;
    var y = atTop ? rect.top : rect.bottom;
    var ix = td.getBoundingClientRect();
    btn._target = { before_id: atTop ? rid : null, after_id: atTop ? null : rid };
    btn.style.left = Math.round(ix.left + 2) + 'px';
    btn.style.top = Math.round(y) + 'px';
    btn.classList.add('on');
  }, { passive: true });

  var wrap = document.querySelector('.tbl-wrap');
  if (wrap) wrap.addEventListener('scroll', hideRowInsert, { passive: true });
  window.addEventListener('resize', hideRowInsert);

  btn.onclick = async function (e) {
    e.preventDefault(); e.stopPropagation();
    var target = btn._target;
    if (!target || btn.disabled) return;
    btn.disabled = true;
    hideRowInsert();
    try {
      var res = await api('/api/tables/' + state.tid + '/rows', 'POST',
        { data: {}, before_id: target.before_id, after_id: target.after_id });
      if (state.size > 0 && res.pos) state.page = Math.max(1, Math.ceil(res.pos / state.size));
      await refreshMeta(state.tid);
      await loadRows();
      var tr = await waitRowEl(res.id, 1200);
      if (tr) focusRowEl(res.id);
      toast('已插入一行');
    } catch (err) {
      toast('插入失败：' + err.message);
    } finally { btn.disabled = false; }
  };
}

function initTbodyEvents() {
  var tb = $('tbody');
  tb.addEventListener('click', function (e) {
    var cmtDot = e.target.closest('.cmt-dot');
    if (cmtDot) {
      e.stopPropagation();
      var parts = cmtDot.getAttribute('data-cmt').split('|');
      openCellComments(+parts[0], parts[1], cmtDot);
      return;
    }
    var delRow = e.target.closest('[data-del-row]');
    if (delRow) { e.stopPropagation(); confirmDeleteRow(+delRow.getAttribute('data-del-row')); return; }
    if (isMobile()) {
      var midx = e.target.closest('td.idx-col');
      if (midx) {
        var mtr = midx.closest('tr');
        if (mtr) { toggleRowPanel(+mtr.getAttribute('data-rid')); return; }
      }
    }
    if (e.target.closest('a') || e.target.closest('.cell-expand')) return;
    var td = e.target.closest('td.editable, td.formula');
    if (td && !td.querySelector('input')) {
      var cc = colByKey(td.getAttribute('data-f'));
      if (cc && cc.type === 'checkbox') {          // 勾选列：单击直接切换
        td.setAttribute('data-ts', String(Date.now()));
        openEdit(td);
        return;
      }
      selectCell(td, e.shiftKey);
    }
  });
  tb.addEventListener('dblclick', function (e) {
    if (e.target.closest('a') || e.target.closest('.cell-expand')) return;
    var idx = e.target.closest('td.idx-col');
    if (idx) {
      var itr = idx.closest('tr');
      if (itr) toggleRowPanel(+itr.getAttribute('data-rid'));
      return;
    }
    var td = e.target.closest('td.editable');
    if (td) {
      if (Date.now() - (+td.getAttribute('data-ts') || 0) < 600) {  // 刚单击切换过勾选
        td.removeAttribute('data-ts');
        return;
      }
      clearCellSel(); openEdit(td);
    }
  });
}

