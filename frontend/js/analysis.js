/* analysis.js — index.html 内联脚本拆出的第 11/12 段（原第 6278-7437 行）。
   加载顺序见 index.html 底部；全部是全局函数，跨文件可直接调用。 */
/* ==================== 数据分析（自助图表） ==================== */
var ANA = {
  project: '', dashboards: [], currentId: null, canEdit: false,
  rows: {}, loads: {}, returnTid: null, previewToken: 0
};
var ANA_COLORS = ['#3370FF', '#34C724', '#FF8800', '#7B61FF', '#00B2B2',
                  '#F54A45', '#FFB400', '#8F959E', '#2BA471', '#D46B08',
                  '#6B7A99', '#B37FEB'];

function anaTables() {
  return (META.tables || []).filter(function (t) { return t.project === ANA.project; });
}
function anaTable(tid) {
  tid = +tid;
  return (META.tables || []).find(function (t) { return t.id === tid; }) || null;
}
function anaCols(tid) {
  var t = anaTable(tid);
  return t ? (t.columns || []).filter(function (c) { return !c.hidden; }) : [];
}
function anaCol(tid, key) {
  return anaCols(tid).find(function (c) { return c.key === key; }) || null;
}
function anaSheetName(t) {
  if (!t) return '';
  var a = String(t.name || '').split('·');
  return a.length > 1 ? a[a.length - 1] : t.name;
}
function anaEmpty(el, text, extra) {
  el.innerHTML = '<div class="ana-empty"><div class="ana-empty-t">' + esc(text) + '</div>'
    + (extra ? '<div>' + esc(extra) + '</div>' : '') + '</div>';
}

function openAnalysis(dashId) {
  if (!ME) { openLogin(); return; }
  var p = curProject;
  if (!p && state.tid) {
    p = PROJECTS.find(function (x) {
      return x.groups.some(function (g) { return g.sheets.some(function (s) { return s.id === state.tid; }); });
    }) || null;
  }
  if (!p) { toast('请先进入项目'); return; }
  if (!p.analysis_role) { toast('没有该项目的分析权限'); return; }
  closeRowPanel();
  closeMobileSidebar();
  ANA.project = p.name;
  ANA.returnTid = state.tid || ANA.returnTid;
  if (dashId) ANA.currentId = +dashId;
  ANA.rows = {}; ANA.loads = {};
  document.body.classList.remove('in-table');
  document.body.classList.add('in-analysis');
  $('home').style.display = 'none';
  $('proj-view').style.display = 'none';
  $('table-view').classList.remove('on');
  $('analysis-view').classList.add('on');
  $('ana-sub').textContent = p.name;
  renderSidebar();
  loadDashboards(true).then(function () {
    saveView({ v: 'analysis', id: ANA.currentId, project: p.name });
  });
}

function openAnalysisPage(id) {
  closeMobileSidebar();
  if ($('analysis-view').classList.contains('on')) {
    ANA.currentId = +id;
    renderAnaChrome(); renderAnaDash(); renderSidebarPages();
    saveView({ v: 'analysis', id: ANA.currentId, project: ANA.project });
  } else {
    openAnalysis(+id);
  }
}

function closeAnalysis() {
  $('analysis-view').classList.remove('on');
  document.body.classList.remove('in-analysis');
  if (ANA.returnTid && anaTable(ANA.returnTid)) {
    openSheet(ANA.returnTid);
  } else if (curProject) {
    openProject(curProject.name);
  } else {
    goHomeProjects();
  }
}

async function loadDashboards(autoCreate) {
  $('ana-body').innerHTML = '<div class="ana-empty"><div class="ana-empty-t">加载看板…</div></div>';
  try {
    var d = await api('/api/dashboards?project=' + encodeURIComponent(ANA.project));
    ANA.dashboards = d.dashboards || [];
    ANA.canEdit = !!d.can_edit;
    if (!ANA.dashboards.length && autoCreate && ANA.canEdit) {
      var c = await api('/api/dashboards', 'POST',
        { project: ANA.project, name: '默认看板', config: { charts: [] } });
      if (c && c.dashboard) ANA.dashboards = [c.dashboard];
    }
    if (!ANA.dashboards.some(function (x) { return x.id === ANA.currentId; })) {
      ANA.currentId = ANA.dashboards.length ? ANA.dashboards[0].id : null;
    }
    renderAnaChrome();
    renderAnaDash();
    renderSidebarPages();
  } catch (e) {
    $('ana-body').innerHTML = '<div class="ana-empty"><div class="ana-empty-t">读取看板失败</div>'
      + '<div>' + esc(e.message) + '</div></div>';
  }
}

function anaCurrentDash() {
  return ANA.dashboards.find(function (d) { return d.id === ANA.currentId; }) || null;
}

function renderAnaChrome() {
  var sel = $('ana-dash-sel');
  sel.innerHTML = ANA.dashboards.map(function (d) {
    return '<option value="' + d.id + '">' + esc(d.name) + '</option>';
  }).join('') || '<option value="">暂无看板</option>';
  // 新建看板收进这个下拉：原来它和「＋ 新建图表」两个加号并排，容易分不清
  if (ANA.canEdit) sel.innerHTML += '<option value="__new__">＋ 新建看板…</option>';
  sel.value = ANA.currentId ? String(ANA.currentId) : '';
  sel.disabled = !ANA.dashboards.length && !ANA.canEdit;
  ['ana-add-chart', 'ana-save', 'ana-dash-rename', 'ana-dash-del'].forEach(function (id) {
    var el = $(id);
    if (el) el.style.display = ANA.canEdit ? '' : 'none';
  });
  var pname = (curProject && curProject.name) || '';
  var big = null;
  (META.tables || []).forEach(function (t) { if ((t.count || 0) > 50000 && (!big || t.count > big.count)) big = t; });
  var sub = $('ana-sub');
  if (sub) {
    sub.textContent = pname + (big ? ' · 「' + String(big.name).split('·').pop() + '」' + big.count + ' 行，整表加载会慢' : '');
    sub.style.color = big ? '#b26a00' : '';
    sub.title = big ? '单表超过 5 万行时，分析页会把整张表拉到浏览器里聚合；数据量大建议先汇总成小表再分析。' : '';
  }
  $('ana-dash-rename').disabled = !ANA.currentId;
  $('ana-dash-del').disabled = !ANA.currentId;
  $('ana-add-chart').disabled = !ANA.canEdit;
  $('ana-save').disabled = !ANA.currentId;
}

function anaAutoTitle(c) {
  var cols = anaCols(c.table_id);
  var m = (c.metrics || [])[0] || { agg: 'count' };
  var ml = anaMetricLabel(m, cols);
  var d = c.dimension ? cols.find(function (x) { return x.key === c.dimension; }) : null;
  return (d ? d.label + ' · ' : '') + ml;
}

function anaCardHtml(c, idx) {
  var t = anaTable(c.table_id);
  var title = c.title || anaAutoTitle(c);
  var src = t ? t.name : '数据表已删除';
  return '<div class="ana-card" data-idx="' + idx + '">'
    + '<div class="ana-card-head">'
    + '<div class="ana-card-title" title="' + esc(title) + '">' + esc(title) + '</div>'
    + '<span class="ana-card-src" title="' + esc(src) + '">' + esc(src) + '</span>'
    + (ANA.canEdit ? '<div class="ana-card-acts">'
      + '<button class="ana-act" data-act="edit" title="编辑图表" aria-label="编辑图表">'
      + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg></button>'
      + '<button class="ana-act danger" data-act="del" title="删除图表" aria-label="删除图表">'
      + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6"/></svg></button>'
      + '</div>' : '')
    + '</div><div class="ana-chart" data-role="chart"><div class="ana-skel">加载中…</div></div></div>';
}

function renderAnaDash() {
  var dash = anaCurrentDash();
  if (!dash) {
    $('ana-body').innerHTML = '<div class="ana-empty-board"><div class="t">'
      + (ANA.canEdit ? '还没有看板' : '暂无可查看的分析') + '</div>'
      + (ANA.canEdit ? '<div style="margin-top:14px"><button class="btn btn-primary" id="ana-empty-newdash">＋ 新建看板</button></div>' : '')
      + '</div>';
    if ($('ana-empty-newdash')) $('ana-empty-newdash').onclick = anaNewDashboard;
    return;
  }
  var charts = (dash.config && dash.config.charts) || [];
  if (!charts.length) {
    $('ana-body').innerHTML = '<div class="ana-empty-board"><div class="t">'
      + (ANA.canEdit ? '还没有图表' : '暂无可查看的图表') + '</div>'
      + (ANA.canEdit ? '<div>选一张表、一个维度、一个指标，就能生成图表</div>' : '')
      + (ANA.canEdit ? '<div style="margin-top:14px"><button class="btn btn-primary" id="ana-empty-add">＋ 新建图表</button></div>' : '')
      + '</div>';
    if ($('ana-empty-add')) $('ana-empty-add').onclick = function () { openChartEditor(null); };
    return;
  }
  $('ana-body').innerHTML = '<div class="ana-grid" id="ana-grid">'
    + charts.map(function (c, i) { return anaCardHtml(c, i); }).join('') + '</div>';
  var cards = $('ana-grid').querySelectorAll('.ana-card');
  cards.forEach(function (card, i) {
    var cfg = charts[i];
    card.querySelectorAll('[data-act]').forEach(function (btn) {
      btn.onclick = function (e) {
        e.stopPropagation();
        var act = btn.getAttribute('data-act');
        if (act === 'edit') openChartEditor(i);
        else if (act === 'del') anaDeleteChart(i);
      };
    });
    anaRenderCard(card, cfg);
  });
}

function anaGetRows(tid) {
  tid = +tid;
  if (ANA.rows[tid]) return Promise.resolve(ANA.rows[tid]);
  if (ANA.loads[tid]) return ANA.loads[tid];
  var p = api('/api/tables/' + tid + '/rows?size=0').then(function (d) {
    ANA.rows[tid] = d.rows || [];
    delete ANA.loads[tid];
    return ANA.rows[tid];
  }, function (e) {
    delete ANA.loads[tid];
    throw e;
  });
  ANA.loads[tid] = p;
  return p;
}

function anaRenderCard(card, cfg) {
  var el = card.querySelector('[data-role="chart"]');
  var t = anaTable(cfg.table_id);
  if (!t) { anaEmpty(el, '数据表不存在', '图表引用的表可能已被删除'); return; }
  anaGetRows(t.id).then(function (rows) {
    if (!document.body.contains(el)) return;
    anaRenderChart(el, cfg, rows);
  }).catch(function (e) {
    if (!document.body.contains(el)) return;
    anaEmpty(el, '没有该表的访问权限', e.message);
  });
}

/* ---------- 取值、筛选与聚合 ---------- */
function anaNum(v) {
  if (typeof v === 'number') return isFinite(v) ? v : null;
  if (typeof v === 'boolean') return v ? 1 : 0;
  if (v === null || v === undefined) return null;
  var s = String(v).trim().replace(/,/g, '').replace(/，/g, '');
  if (!s) return null;
  var m = s.match(/^(-?\d+(?:\.\d+)?)\s*(亿|万|千|w|W|k|K)?%?$/);
  if (!m) return null;
  var n = parseFloat(m[1]);
  if (!isFinite(n)) return null;
  var u = m[2] || '';
  if (u === '亿') n *= 1e8;
  else if (u === '万' || u === 'w' || u === 'W') n *= 1e4;
  else if (u === '千' || u === 'k' || u === 'K') n *= 1e3;
  return n;
}
function anaTrimZero(s) {
  return String(s).replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1');
}
function anaFmt(n, full) {
  if (n === null || n === undefined || typeof n !== 'number' || !isFinite(n)) return '—';
  if (full) return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
  var sign = n < 0 ? '-' : '', a = Math.abs(n);
  if (a >= 1e8) return sign + anaTrimZero((a / 1e8).toFixed(2)) + '亿';
  if (a >= 1e4) return sign + anaTrimZero((a / 1e4).toFixed(1)) + '万';
  if (Math.abs(a - Math.round(a)) < 1e-9) return sign + Math.round(a).toLocaleString('zh-CN');
  return sign + anaTrimZero(a.toFixed(2));
}
function anaFmtChart(n, cfg) {
  if (n === null || n === undefined || typeof n !== 'number' || !isFinite(n)) return '—';
  var p = cfg && cfg.precision;
  if (p !== undefined && p !== null && isFinite(+p)) {
    return n.toLocaleString('zh-CN', { minimumFractionDigits: +p, maximumFractionDigits: +p });
  }
  return anaFmt(n, true);
}
function anaIsEmpty(v) {
  return v === null || v === undefined || v === ''
    || (Array.isArray(v) && !v.length)
    || (typeof v === 'string' && !v.trim());
}
function anaDimLabel(v) {
  return anaIsEmpty(v) ? '（空）' : String(v);
}
function anaDateBucket(v, gran) {
  var s = String(v == null ? '' : v).slice(0, 10);
  if (!/^\d{4}/.test(s)) return '（空）';
  if (gran === 'year') return s.slice(0, 4);
  if (gran === 'day') return s;
  return s.slice(0, 7);
}
function anaMatch(v, f, col) {
  var op = f.op || 'contains', a = f.value, b = f.value2;
  if (op === 'empty') return anaIsEmpty(v);
  if (op === 'notempty') return !anaIsEmpty(v);
  if (op === 'checked') return !!v;
  if (op === 'unchecked') return !v;
  var arr = Array.isArray(v) ? v : [v];
  if (col && (col.type === 'number' || col.type === 'formula')) {
    var n = anaNum(v);
    if (op === 'between') {
      var x1 = anaNum(a), x2 = anaNum(b);
      return n !== null && (x1 === null || n >= x1) && (x2 === null || n <= x2);
    }
    var x = anaNum(a);
    if (n === null || x === null) return false;
    if (op === 'gte') return n >= x;
    if (op === 'lte') return n <= x;
    if (op === 'gt') return n > x;
    if (op === 'lt') return n < x;
    if (op === 'eq') return n === x;
    if (op === 'neq') return n !== x;
    return true;
  }
  if (col && col.type === 'date') {
    var s = String(v == null ? '' : v).slice(0, 10);
    if (!s) return false;
    if (op === 'between') {
      return (!a || s >= String(a).slice(0, 10)) && (!b || s <= String(b).slice(0, 10));
    }
    if (op === 'eq') return s === String(a || '').slice(0, 10);
    if (op === 'neq') return s !== String(a || '').slice(0, 10);
    return true;
  }
  if (op === 'eq') return arr.some(function (x) { return String(x == null ? '' : x) === String(a == null ? '' : a); });
  if (op === 'neq') return arr.every(function (x) { return String(x == null ? '' : x) !== String(a == null ? '' : a); });
  if (op === 'contains') {
    var needle = String(a == null ? '' : a).toLowerCase();
    return arr.some(function (x) {
      return String(x == null ? '' : x).toLowerCase().indexOf(needle) !== -1;
    });
  }
  return true;
}
function anaMetric(rows, m, byKey) {
  if (m.agg === 'count') return rows.length;
  if (m.agg === 'distinct') {
    var set = Object.create(null);   // 单元格的值当键
    rows.forEach(function (r) {
      var v = r.data[m.field], arr = Array.isArray(v) ? v : [v];
      arr.forEach(function (x) {
        if (!anaIsEmpty(x)) set[String(x)] = 1;
      });
    });
    return Object.keys(set).length;
  }
  var ns = [];
  rows.forEach(function (r) {
    var n = anaNum(r.data[m.field]);
    if (n !== null) ns.push(n);
  });
  if (!ns.length) return null;
  if (m.agg === 'sum') return ns.reduce(function (a, b) { return a + b; }, 0);
  if (m.agg === 'avg') return ns.reduce(function (a, b) { return a + b; }, 0) / ns.length;
  if (m.agg === 'min') return Math.min.apply(null, ns);
  if (m.agg === 'max') return Math.max.apply(null, ns);
  return rows.length;
}
function anaMetricLabel(m, cols) {
  var c = m.field ? cols.find(function (x) { return x.key === m.field; }) : null;
  if (m.agg === 'count') return '记录数';
  if (!c) return '指标';
  var pre = { distinct: '去重 ', sum: '合计 ', avg: '平均 ', min: '最小 ', max: '最大 ' };
  return (pre[m.agg] || '') + c.label;
}
function anaOpsFor(col) {
  if (col.type === 'date') {
    return [['between', '日期区间'], ['eq', '等于'], ['empty', '为空'], ['notempty', '不为空']];
  }
  if (col.type === 'number' || col.type === 'formula') {
    return [['between', '区间'], ['gte', '≥'], ['lte', '≤'], ['eq', '='], ['neq', '≠'],
            ['empty', '为空'], ['notempty', '不为空']];
  }
  if (col.type === 'checkbox') return [['checked', '已勾选'], ['unchecked', '未勾选']];
  if (col.type === 'select' || col.type === 'multiselect') {
    return [['eq', '等于'], ['neq', '不等于'], ['empty', '为空'], ['notempty', '不为空']];
  }
  return [['contains', '包含'], ['eq', '等于'], ['neq', '不等于'], ['empty', '为空'], ['notempty', '不为空']];
}
function anaAgg(rows, cfg) {
  var cols = anaCols(cfg.table_id);
  var byKey = Object.create(null);   // 列名当键，同上
  cols.forEach(function (c) { byKey[c.key] = c; });
  var metrics = ((cfg.metrics && cfg.metrics.length) ? cfg.metrics : [{ agg: 'count', field: '' }])
    .map(function (m) {
      return { agg: m.agg || 'count', field: m.field || '', label: anaMetricLabel(m, cols) };
    });
  var fs = (cfg.filters || []).filter(function (f) { return f && f.field && byKey[f.field]; });
  var data = rows.filter(function (r) {
    return fs.every(function (f) { return anaMatch(r.data[f.field], f, byKey[f.field]); });
  });
  var dimCol = cfg.dimension ? byKey[cfg.dimension] : null;
  var map = Object.create(null), order = [];   // 分组的值来自单元格内容，什么都可能是键
  data.forEach(function (r) {
    var keys;
    if (!dimCol) {
      keys = ['全部'];
    } else {
      var v = r.data[dimCol.key];
      if (dimCol.type === 'date') keys = [anaDateBucket(v, cfg.date_gran)];
      else if (Array.isArray(v)) keys = v.length ? v.map(function (x) { return String(x); }) : ['（空）'];
      else keys = [anaDimLabel(v)];
    }
    var seen = Object.create(null);
    keys.forEach(function (k) {
      if (seen[k]) return;
      seen[k] = 1;
      if (!map[k]) { map[k] = { label: k, rows: [] }; order.push(k); }
      map[k].rows.push(r);
    });
  });
  var out = order.map(function (k) {
    var g = map[k];
    return {
      label: k,
      values: metrics.map(function (m) { return anaMetric(g.rows, m, byKey); }),
      n: g.rows.length
    };
  });
  if (!dimCol && !out.length) {
    out = [{ label: '全部', values: metrics.map(function (m) { return anaMetric([], m, byKey); }), n: 0 }];
  }
  var sort = cfg.sort || (cfg.type === 'line' ? 'dim_asc' : 'metric_desc');
  if (cfg.type === 'line' && sort !== 'dim_desc') sort = 'dim_asc';
  if (sort === 'dim_asc' || sort === 'dim_desc') {
    out.sort(function (a, b) {
      var c = String(a.label).localeCompare(String(b.label), 'zh-CN');
      return sort === 'dim_asc' ? c : -c;
    });
  } else {
    out.sort(function (a, b) {
      var av = a.values[0], bv = b.values[0];
      if (av === null || av === undefined) av = sort === 'metric_asc' ? Infinity : -Infinity;
      if (bv === null || bv === undefined) bv = sort === 'metric_asc' ? Infinity : -Infinity;
      return sort === 'metric_asc' ? av - bv : bv - av;
    });
  }
  var lim = parseInt(cfg.limit, 10);
  if (isNaN(lim)) lim = cfg.type === 'line' ? 0 : 10;
  if (lim > 0 && out.length > lim) out = out.slice(0, lim);
  return { groups: out, metrics: metrics, total: data.length, dimCol: dimCol };
}

/* ---------- SVG 图表 ---------- */
function anaCut(s, n) {
  s = String(s == null ? '' : s);
  return s.length > n ? s.slice(0, n) + '…' : s;
}
function anaMaxOf(groups) {
  var max = 0;
  groups.forEach(function (g) {
    g.values.forEach(function (v) {
      if (typeof v === 'number' && isFinite(v) && v > max) max = v;
    });
  });
  return max > 0 ? max : 1;
}
function anaScale(max) {
  max = max > 0 ? max : 1;
  var raw = max / 5;
  var p = Math.pow(10, Math.floor(Math.log10(raw)));
  var n = raw / p;
  var mult = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  var step = mult * p;
  var top = step * 5;
  if (top < max) top = Math.ceil(max / step) * step;
  return { top: top, step: step };
}
function anaChartLegend(metrics) {
  if (metrics.length <= 1) return '';
  return '<div class="ana-legend">' + metrics.map(function (m, i) {
    return '<span><i style="background:' + ANA_COLORS[i % ANA_COLORS.length] + '"></i>'
      + esc(m.label) + '</span>';
  }).join('') + '</div>';
}
function anaTipAttr(text) {
  return esc(text);
}
function anaBarChart(groups, metrics, horizontal) {
  if (horizontal) return anaHBarChart(groups, metrics);
  var W = 640, H = 258, ml = 50, mr = 14, mt = 12, mb = 58;
  var scale = anaScale(anaMaxOf(groups)), top = scale.top;
  var pw = W - ml - mr, ph = H - mt - mb;
  var n = Math.max(1, groups.length), m = Math.max(1, metrics.length);
  var band = pw / n, bw = Math.min(36, (band * 0.7) / m), gap = (band - bw * m) / 2;
  var s = '<svg viewBox="0 0 ' + W + ' ' + H + '" style="height:' + H + 'px" aria-hidden="true">';
  for (var i = 0; i <= 5; i++) {
    var gy = mt + ph - ph * i / 5;
    s += '<line x1="' + ml + '" y1="' + gy.toFixed(1) + '" x2="' + (W - mr) + '" y2="' + gy.toFixed(1) + '" stroke="#F0F1F4" stroke-width="1"/>';
    s += '<text x="' + (ml - 8) + '" y="' + (gy + 4).toFixed(1) + '" text-anchor="end" font-size="10" fill="#86909C">' + esc(anaFmt(scale.step * i)) + '</text>';
  }
  groups.forEach(function (g, gi) {
    var cx = ml + gi * band + gap;
    g.values.forEach(function (v, j) {
      if (typeof v !== 'number' || !isFinite(v) || v <= 0) return;
      var h = Math.max(1, (v / top) * ph);
      var x = cx + j * bw, y = mt + ph - h;
      s += '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + Math.max(1, bw - 2).toFixed(1) + '" height="' + h.toFixed(1) + '" rx="2" fill="' + ANA_COLORS[j % ANA_COLORS.length] + '" data-tip="' + anaTipAttr(g.label + ' · ' + metrics[j].label + ': ' + anaFmt(v, true)) + '"/>';
    });
    var lx = ml + gi * band + band / 2, ly = H - mb + 18;
    var label = anaCut(g.label, n > 8 ? 9 : 8);
    if (n > 8) {
      s += '<text x="' + lx.toFixed(1) + '" y="' + ly + '" text-anchor="end" font-size="10" fill="#86909C" transform="rotate(-28 ' + lx.toFixed(1) + ' ' + ly + ')">' + esc(label) + '</text>';
    } else {
      s += '<text x="' + lx.toFixed(1) + '" y="' + ly + '" text-anchor="middle" font-size="10" fill="#86909C">' + esc(label) + '</text>';
    }
  });
  return s + '</svg>' + anaChartLegend(metrics);
}
function anaHBarChart(groups, metrics) {
  var W = 640, left = 132, right = 70, top = 6, bottom = 6;
  var m = Math.max(1, metrics.length);
  var rowH = Math.max(28, m * 18 + 10);
  var H = Math.max(190, top + bottom + groups.length * rowH);
  var max = anaMaxOf(groups);
  var pw = W - left - right;
  var s = '<svg viewBox="0 0 ' + W + ' ' + H + '" style="height:' + H + 'px" aria-hidden="true">';
  groups.forEach(function (g, gi) {
    var gy = top + gi * rowH;
    s += '<text x="8" y="' + (gy + rowH / 2 + 4) + '" font-size="11" fill="#4E5969">' + esc(anaCut(g.label, 14)) + '</text>';
    g.values.forEach(function (v, j) {
      if (typeof v !== 'number' || !isFinite(v) || v <= 0) return;
      var y = gy + 5 + j * 18;
      var w = Math.max(1, (v / max) * pw);
      s += '<rect x="' + left + '" y="' + y + '" width="' + w.toFixed(1) + '" height="14" rx="2" fill="' + ANA_COLORS[j % ANA_COLORS.length] + '" data-tip="' + anaTipAttr(g.label + ' · ' + metrics[j].label + ': ' + anaFmt(v, true)) + '"/>';
      s += '<text x="' + (left + w + 6).toFixed(1) + '" y="' + (y + 11) + '" font-size="10" fill="#86909C">' + esc(anaFmt(v)) + '</text>';
    });
  });
  return s + '</svg>' + anaChartLegend(metrics);
}
function anaLineChart(groups, metrics) {
  var W = 640, H = 258, ml = 50, mr = 16, mt = 12, mb = 52;
  var scale = anaScale(anaMaxOf(groups)), top = scale.top;
  var pw = W - ml - mr, ph = H - mt - mb, n = groups.length;
  var xAt = function (i) { return n <= 1 ? ml + pw / 2 : ml + i * pw / (n - 1); };
  var s = '<svg viewBox="0 0 ' + W + ' ' + H + '" style="height:' + H + 'px" aria-hidden="true">';
  for (var i = 0; i <= 5; i++) {
    var gy = mt + ph - ph * i / 5;
    s += '<line x1="' + ml + '" y1="' + gy.toFixed(1) + '" x2="' + (W - mr) + '" y2="' + gy.toFixed(1) + '" stroke="#F0F1F4" stroke-width="1"/>';
    s += '<text x="' + (ml - 8) + '" y="' + (gy + 4).toFixed(1) + '" text-anchor="end" font-size="10" fill="#86909C">' + esc(anaFmt(scale.step * i)) + '</text>';
  }
  var step = n > 7 ? Math.ceil(n / 7) : 1;
  groups.forEach(function (g, gi) {
    if (gi % step === 0 || gi === n - 1) {
      var lx = xAt(gi), anchor = 'middle';
      if (gi === 0) { anchor = 'start'; lx = ml; }
      else if (gi === n - 1) { anchor = 'end'; lx = W - mr; }
      s += '<text x="' + lx.toFixed(1) + '" y="' + (H - mb + 20) + '" text-anchor="' + anchor + '" font-size="10" fill="#86909C">' + esc(anaCut(g.label, 10)) + '</text>';
    }
  });
  metrics.forEach(function (met, j) {
    var pts = [];
    groups.forEach(function (g, gi) {
      var v = g.values[j];
      if (typeof v === 'number' && isFinite(v)) pts.push({ x: xAt(gi), y: mt + ph - (v / top) * ph, v: v, label: g.label });
    });
    if (!pts.length) return;
    var color = ANA_COLORS[j % ANA_COLORS.length];
    if (pts.length > 1) {
      s += '<polyline fill="none" stroke="' + color + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" points="'
        + pts.map(function (p) { return p.x.toFixed(1) + ',' + p.y.toFixed(1); }).join(' ') + '"/>';
    }
    pts.forEach(function (p) {
      s += '<circle cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="3.2" fill="#fff" stroke="' + color + '" stroke-width="2" data-tip="' + anaTipAttr(p.label + ' · ' + met.label + ': ' + anaFmt(p.v, true)) + '"/>';
    });
  });
  return s + '</svg>' + anaChartLegend(metrics);
}
function anaPieChart(groups, metrics, cfg) {
  var items = groups.map(function (g) { return { label: g.label, v: g.values[0] }; })
    .filter(function (x) { return typeof x.v === 'number' && isFinite(x.v) && x.v > 0; });
  if (!items.length) return '<div class="ana-empty"><div class="ana-empty-t">没有可展示的数值</div></div>';
  var W = 640, H = 250, cx = 320, cy = 125, r1 = 92, r2 = 56;
  var total = items.reduce(function (a, b) { return a + b.v; }, 0);
  var s = '<svg viewBox="0 0 ' + W + ' ' + H + '" style="height:' + H + 'px" aria-hidden="true">';
  function pt(r, a) { return (cx + r * Math.cos(a)).toFixed(2) + ' ' + (cy + r * Math.sin(a)).toFixed(2); }
  if (items.length === 1) {
    s += '<circle cx="' + cx + '" cy="' + cy + '" r="' + ((r1 + r2) / 2) + '" fill="none" stroke="' + ANA_COLORS[0] + '" stroke-width="' + (r1 - r2) + '" data-tip="' + anaTipAttr(items[0].label + ': ' + anaFmt(items[0].v, true)) + '"/>';
  } else {
    var a0 = -Math.PI / 2;
    items.forEach(function (it, i) {
      var a1 = a0 + (it.v / total) * Math.PI * 2;
      var large = (a1 - a0) > Math.PI ? 1 : 0;
      var d = 'M' + pt(r1, a0) + ' A' + r1 + ' ' + r1 + ' 0 ' + large + ' 1 ' + pt(r1, a1)
        + ' L' + pt(r2, a1) + ' A' + r2 + ' ' + r2 + ' 0 ' + large + ' 0 ' + pt(r2, a0) + ' Z';
      s += '<path d="' + d + '" fill="' + ANA_COLORS[i % ANA_COLORS.length] + '" data-tip="' + anaTipAttr(it.label + ': ' + anaFmt(it.v, true) + ' · ' + Math.round(it.v / total * 100) + '%') + '"/>';
      a0 = a1;
    });
  }
  s += '</svg>';
  var legend = '<div class="ana-legend ana-legend-col">' + items.map(function (it, i) {
    var pct = total ? Math.round(it.v / total * 1000) / 10 : 0;
    return '<span title="' + esc(it.label) + '"><i style="background:' + ANA_COLORS[i % ANA_COLORS.length] + '"></i>'
      + esc(anaCut(it.label, 12)) + ' ' + esc(anaFmtChart(it.v, cfg)) + ' (' + pct + '%)</span>';
  }).join('') + '</div>';
  return '<div class="ana-pie-flex">' + s + legend + '</div>'
    + (metrics.length > 1 ? '<div class="ana-note">环形图只展示第一个指标</div>' : '');
}
function anaTableChart(groups, metrics, res, cfg) {
  var dimLabel = res.dimCol ? res.dimCol.label : '指标';
  var hasCount = metrics.some(function (m) { return m.agg === 'count'; });
  var rank = !!(cfg && cfg.rank);
  var hideCount = !!(cfg && cfg.hide_count);
  var head = (rank ? '<th style="width:44px">排名</th>' : '') + '<th style="width:34%">' + esc(dimLabel) + '</th>'
    + metrics.map(function (m) { return '<th class="num">' + esc(m.label) + '</th>'; }).join('')
    + (hasCount || hideCount ? '' : '<th class="num" style="width:84px">记录数</th>');
  var body = groups.map(function (g, gi) {
    return '<tr>' + (rank ? '<td class="num">' + (gi + 1) + '</td>' : '')
      + '<td title="' + esc(g.label) + '">' + esc(g.label) + '</td>'
      + g.values.map(function (v) { return '<td class="num">' + esc(anaFmtChart(v, cfg)) + '</td>'; }).join('')
      + (hasCount || hideCount ? '' : '<td class="num">' + esc(String(g.n)) + '</td>') + '</tr>';
  }).join('');
  return '<div class="ana-table-wrap"><table class="ana-table"><thead><tr>' + head
    + '</tr></thead><tbody>' + body + '</tbody></table></div>';
}
function anaKpiHtml(groups, metrics, res, cfg) {
  var g = groups[0] || { values: [null], n: 0 };
  var sub = (cfg && cfg.title && cfg.title !== metrics[0].label) ? metrics[0].label : '';
  return '<div class="ana-kpi"><div class="v" title="' + esc(anaFmtChart(g.values[0], cfg)) + '">'
    + esc(anaFmtChart(g.values[0], cfg)) + '</div>'
    + (sub ? '<div class="l">' + esc(sub) + '</div>' : '')
    + '<div class="n">筛选后 ' + res.total + ' 条记录</div></div>'
    + (metrics.length > 1 ? '<div class="ana-note">指标卡只展示第一个指标</div>' : '');
}
function anaRenderChart(el, cfg, rows) {
  var res;
  try { res = anaAgg(rows, cfg); }
  catch (e) {
    anaEmpty(el, '图表配置有误', e.message);
    return;
  }
  var type = cfg.type || 'bar';
  if (!res.total && type !== 'kpi') {
    anaEmpty(el, '没有匹配数据', '调整筛选条件后再试');
    return;
  }
  if (type === 'kpi') el.innerHTML = anaKpiHtml(res.groups, res.metrics, res, cfg);
  else if (type === 'table') el.innerHTML = anaTableChart(res.groups, res.metrics, res, cfg);
  else if (type === 'pie') el.innerHTML = anaPieChart(res.groups, res.metrics, cfg);
  else if (type === 'line') el.innerHTML = anaLineChart(res.groups, res.metrics);
  else if (type === 'hbar') el.innerHTML = anaBarChart(res.groups, res.metrics, true);
  else el.innerHTML = anaBarChart(res.groups, res.metrics, false);
}

/* ---------- 图表编辑器 ---------- */
function anaDimCols(tid) {
  return anaCols(tid).filter(function (c) {
    return ['text', 'select', 'multiselect', 'date', 'number', 'checkbox'].indexOf(c.type) !== -1;
  });
}
function anaDefaultDim(tid) {
  var cols = anaDimCols(tid);
  var c = cols.find(function (x) { return x.type === 'select'; })
    || cols.find(function (x) { return x.type === 'text'; })
    || cols[0] || null;
  return c ? c.key : null;
}
function anaDefaultChart() {
  var ts = anaTables();
  var t = ts[0];
  if (!t) return null;
  return {
    id: 'c_' + Date.now() + '_' + Math.random().toString(36).slice(2, 7),
    type: 'bar', table_id: t.id, dimension: anaDefaultDim(t.id), date_gran: 'month',
    metrics: [{ agg: 'count', field: '' }], filters: [], sort: 'metric_desc',
    limit: 10, title: ''
  };
}

function anaFieldOptions(cols, selected, allowEmpty) {
  var html = allowEmpty ? '<option value="">（空）</option>' : '';
  html += cols.map(function (c) {
    return '<option value="' + esc(c.key) + '"' + (c.key === selected ? ' selected' : '') + '>'
      + esc(c.label) + '</option>';
  }).join('');
  return html;
}

function anaFilterValueHtml(f, col) {
  if (['empty', 'notempty', 'checked', 'unchecked'].indexOf(f.op) !== -1) return '<div></div>';
  if (col.type === 'select' || col.type === 'multiselect') {
    var opts = (col.options || []).map(function (x) { return String(x); });
    if (f.value === undefined || f.value === null) f.value = opts[0] || '';
    return '<select data-f="value">' + opts.map(function (x) {
      return '<option value="' + esc(x) + '"' + (String(f.value) === x ? ' selected' : '') + '>' + esc(x) + '</option>';
    }).join('') + '</select>';
  }
  if (col.type === 'date') {
    if (f.op === 'between') {
      return '<div class="ana-filter-val2"><input class="date-compact" readonly data-f="value" placeholder="开始" value="' + esc(f.value || '') + '">'
        + '<input class="date-compact" readonly data-f="value2" placeholder="结束" value="' + esc(f.value2 || '') + '"></div>';
    }
    return '<input class="date-compact" readonly data-f="value" placeholder="选择日期" value="' + esc(f.value || '') + '">';
  }
  if (col.type === 'number' || col.type === 'formula') {
    if (f.op === 'between') {
      return '<div class="ana-filter-val2"><input type="number" data-f="value" placeholder="最小" value="' + esc(f.value == null ? '' : f.value) + '">'
        + '<input type="number" data-f="value2" placeholder="最大" value="' + esc(f.value2 == null ? '' : f.value2) + '"></div>';
    }
    return '<input type="number" data-f="value" placeholder="数值" value="' + esc(f.value == null ? '' : f.value) + '">';
  }
  return '<input data-f="value" placeholder="值" value="' + esc(f.value == null ? '' : f.value) + '">';
}

async function openChartEditor(idx) {
  if (!ANA.canEdit) return;
  var dash = anaCurrentDash();
  if (!dash) { toast('请先新建看板'); return; }
  var charts = (dash.config && dash.config.charts) || [];
  var editing = idx !== null && idx !== undefined && charts[idx];
  var st = editing ? JSON.parse(JSON.stringify(charts[idx])) : anaDefaultChart();
  if (!st) { toast('当前项目没有可用数据表'); return; }
  var ts = anaTables();
  if (!ts.some(function (t) { return t.id === st.table_id; })) st.table_id = ts[0] ? ts[0].id : null;
  st.metrics = (st.metrics && st.metrics.length) ? st.metrics : [{ agg: 'count', field: '' }];
  st.filters = st.filters || [];
  st.date_gran = st.date_gran || 'month';
  st.limit = (st.limit === undefined || st.limit === null) ? 10 : st.limit;
  if (st.type === 'line' && st.sort !== 'dim_desc') st.sort = 'dim_asc';
  var types = [['bar', '柱状图'], ['hbar', '条形图'], ['line', '折线图'],
               ['pie', '环形图'], ['kpi', '指标卡'], ['table', '明细表']];

  var body = '<div class="ana-editor">'
    + '<div class="ana-form">'
    + '<div class="frm"><label>图表标题</label><input id="ac-title" placeholder="' + esc(anaAutoTitle(st)) + '" value="' + esc(st.title || '') + '"></div>'
    + '<div class="frm row2"><div><label>图表类型</label><select id="ac-type">'
    + types.map(function (x) { return '<option value="' + x[0] + '"' + (st.type === x[0] ? ' selected' : '') + '>' + x[1] + '</option>'; }).join('')
    + '</select></div><div><label>数据表</label><select id="ac-table">'
    + ts.map(function (t) { return '<option value="' + t.id + '"' + (st.table_id === t.id ? ' selected' : '') + '>' + esc(t.name) + '</option>'; }).join('')
    + '</select></div></div>'
    + '<div class="frm row2"><div><label>维度</label><select id="ac-dim"></select></div>'
    + '<div id="ac-gran-wrap"><label>日期粒度</label><select id="ac-gran">'
    + '<option value="day">按天</option><option value="month">按月</option><option value="year">按年</option>'
    + '</select></div></div>'
    + '<div class="frm"><label>指标</label><div id="ac-metrics"></div>'
    + '<button class="ana-add" id="ac-add-metric" type="button">＋ 添加指标</button></div>'
    + '<div class="frm row2"><div><label>排序</label><select id="ac-sort">'
    + '<option value="metric_desc">指标从高到低</option><option value="metric_asc">指标从低到高</option>'
    + '<option value="dim_asc">维度升序</option><option value="dim_desc">维度降序</option>'
    + '</select></div><div><label>显示条数（0 为全部）</label><input id="ac-limit" type="number" min="0" max="200" value="' + esc(st.limit) + '"></div></div>'
    + '<div class="frm"><label>筛选</label><div id="ac-filters"></div>'
    + '<button class="ana-add" id="ac-add-filter" type="button">＋ 添加筛选</button></div>'
    + '</div>'
    + '<div class="ana-preview"><div class="ana-preview-head">预览<span id="ac-src"></span></div>'
    + '<div class="ana-chart" id="ac-preview"><div class="ana-skel">计算中…</div></div></div>'
    + '</div>';

  modal(editing ? '编辑图表' : '新建图表', body,
    '<button class="btn" id="ac-cancel">取消</button><button class="btn btn-primary" id="ac-save">保存图表</button>',
    function (root) {
      root.querySelector('.box').classList.add('ana-box');
      $('ac-cancel').onclick = closeModal;
      $('ac-save').onclick = saveChart;

      function rerenderDims() {
        var cols = anaDimCols(st.table_id);
        var dimSel = $('ac-dim');
        dimSel.innerHTML = '<option value="">不分组</option>'
          + cols.map(function (c) {
            return '<option value="' + esc(c.key) + '"' + (st.dimension === c.key ? ' selected' : '') + '>' + esc(c.label) + '</option>';
          }).join('');
        var dc = st.dimension ? cols.find(function (c) { return c.key === st.dimension; }) : null;
        dimSel.disabled = st.type === 'kpi';
        var showGran = !!(dc && dc.type === 'date' && st.type !== 'kpi');
        $('ac-gran-wrap').style.display = showGran ? '' : 'none';
        var dimRow = dimSel.closest('.frm');
        if (dimRow) dimRow.classList.toggle('one-col', !showGran);
        if (dc && dc.type === 'date') $('ac-gran').value = st.date_gran || 'month';
        if (st.type === 'kpi') { st.dimension = null; dimSel.value = ''; }
      }

      function rerenderMetrics() {
        var cols = anaCols(st.table_id);
        var nums = cols.filter(function (c) { return c.type === 'number' || c.type === 'formula'; });
        var wrap = $('ac-metrics');
        var aggs = [['count', '计数'], ['distinct', '去重计数'], ['sum', '求和'],
                    ['avg', '平均'], ['min', '最小'], ['max', '最大']];
        wrap.innerHTML = st.metrics.map(function (m, i) {
          var allowed = m.agg === 'count' ? [] : (m.agg === 'distinct' ? cols : nums);
          if (m.agg !== 'count' && allowed.length && !allowed.some(function (c) { return c.key === m.field; })) {
            m.field = allowed[0].key;
          }
          var fieldHtml;
          if (!m.agg || m.agg === 'count') {
            fieldHtml = '<select disabled><option>记录数</option></select>';
          } else if (!allowed.length) {
            fieldHtml = '<select disabled><option>（没有可用字段）</option></select>';
          } else {
            fieldHtml = '<select data-mfield="' + i + '">' + anaFieldOptions(allowed, m.field, false) + '</select>';
          }
          return '<div class="ana-metric-row">'
            + '<select data-magg="' + i + '">' + aggs.map(function (a) {
              return '<option value="' + a[0] + '"' + (m.agg === a[0] ? ' selected' : '') + '>' + a[1] + '</option>';
            }).join('') + '</select>'
            + fieldHtml
            + '<button class="ana-metric-del" type="button" data-mdel="' + i + '" title="删除指标">'
            + '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>';
        }).join('');
        wrap.querySelectorAll('[data-magg]').forEach(function (sel) {
          sel.onchange = function () {
            var i = +sel.getAttribute('data-magg');
            st.metrics[i].agg = sel.value;
            if (sel.value === 'count') st.metrics[i].field = '';
            rerenderMetrics(); schedulePreview();
          };
        });
        wrap.querySelectorAll('[data-mfield]').forEach(function (sel) {
          sel.onchange = function () {
            st.metrics[+sel.getAttribute('data-mfield')].field = sel.value;
            schedulePreview();
          };
        });
        wrap.querySelectorAll('[data-mdel]').forEach(function (btn) {
          btn.onclick = function () {
            if (st.metrics.length <= 1) { toast('至少保留一个指标'); return; }
            st.metrics.splice(+btn.getAttribute('data-mdel'), 1);
            rerenderMetrics(); schedulePreview();
          };
        });
      }

      function rerenderFilters() {
        var cols = anaCols(st.table_id).filter(function (c) { return c.type !== 'image'; });
        var wrap = $('ac-filters');
        if (!st.filters.length) {
          wrap.innerHTML = '<div class="hint" style="font-size:12px;color:#86909C;margin-bottom:6px">未设置筛选，统计整张表</div>';
          return;
        }
        wrap.innerHTML = st.filters.map(function (f, i) {
          if (!f.field || !cols.some(function (c) { return c.key === f.field; })) {
            f.field = cols.length ? cols[0].key : '';
            f.op = '';
            f.value = ''; f.value2 = '';
          }
          var col = cols.find(function (c) { return c.key === f.field; });
          if (!col) return '';
          var ops = anaOpsFor(col);
          if (!f.op || !ops.some(function (o) { return o[0] === f.op; })) {
            f.op = ops[0][0]; f.value = ''; f.value2 = '';
          }
          return '<div class="ana-filter-row">'
            + '<select data-ffield="' + i + '">' + anaFieldOptions(cols, f.field, false) + '</select>'
            + '<select data-fop="' + i + '">' + ops.map(function (o) {
              return '<option value="' + o[0] + '"' + (f.op === o[0] ? ' selected' : '') + '>' + o[1] + '</option>';
            }).join('') + '</select>'
            + '<div class="ana-filter-val">' + anaFilterValueHtml(f, col) + '</div>'
            + '<button class="ana-metric-del" type="button" data-fdel="' + i + '" title="删除筛选">'
            + '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>';
        }).join('');
        wrap.querySelectorAll('[data-ffield]').forEach(function (sel) {
          sel.onchange = function () {
            var i = +sel.getAttribute('data-ffield');
            st.filters[i] = { field: sel.value, op: '', value: '', value2: '' };
            rerenderFilters(); schedulePreview();
          };
        });
        wrap.querySelectorAll('[data-fop]').forEach(function (sel) {
          sel.onchange = function () {
            var i = +sel.getAttribute('data-fop');
            st.filters[i].op = sel.value;
            st.filters[i].value = ''; st.filters[i].value2 = '';
            rerenderFilters(); schedulePreview();
          };
        });
        wrap.querySelectorAll('[data-fdel]').forEach(function (btn) {
          btn.onclick = function () {
            st.filters.splice(+btn.getAttribute('data-fdel'), 1);
            rerenderFilters(); schedulePreview();
          };
        });
        wrap.querySelectorAll('[data-f="value"],[data-f="value2"]').forEach(function (inp) {
          inp.oninput = inp.onchange = function () {
            var row = inp.closest('.ana-filter-row');
            var fsel = row.querySelector('[data-ffield]');
            var i = +fsel.getAttribute('data-ffield');
            st.filters[i][inp.getAttribute('data-f')] = inp.value;
            schedulePreview();
          };
          if (inp.classList.contains('date-compact')) inp.onclick = function () { openDatePicker(inp); };
        });
      }

      var previewTimer = null;
      function schedulePreview() {
        var ti = $('ac-title');
        if (ti && !ti.value) ti.placeholder = anaAutoTitle(st);
        clearTimeout(previewTimer);
        previewTimer = setTimeout(renderPreview, 120);
      }
      function renderPreview() {
        var el = $('ac-preview');
        if (!el) return;
        var t = anaTable(st.table_id);
        if (!t) { anaEmpty(el, '请选择数据表'); return; }
        el.innerHTML = '<div class="ana-skel">计算中…</div>';
        var token = ++ANA.previewToken;
        anaGetRows(t.id).then(function (rows) {
          if (token !== ANA.previewToken || !$('ac-preview')) return;
          $('ac-src').textContent = t.name + ' · ' + rows.length + ' 行';
          anaRenderChart($('ac-preview'), st, rows);
        }).catch(function (e) {
          if (token === ANA.previewToken) anaEmpty($('ac-preview'), '读取失败', e.message);
        });
      }

      $('ac-title').oninput = function () { st.title = this.value; };
      $('ac-type').onchange = function () {
        st.type = this.value;
        if (st.type === 'kpi') { st.limit = 1; }
        else if (st.type === 'line') {
          if (st.limit === 10) st.limit = 0;
          if (st.sort === 'metric_desc' || st.sort === 'metric_asc') st.sort = 'dim_asc';
        } else if (st.limit === 1) { st.limit = 10; }
        $('ac-limit').value = st.limit;
        $('ac-sort').value = st.sort;
        rerenderDims(); schedulePreview();
      };
      $('ac-table').onchange = function () {
        st.table_id = +this.value;
        st.dimension = anaDefaultDim(st.table_id);
        st.metrics = [{ agg: 'count', field: '' }];
        st.filters = [];
        st.title = '';
        $('ac-title').value = '';
        $('ac-title').placeholder = anaAutoTitle(st);
        rerenderDims(); rerenderMetrics(); rerenderFilters(); schedulePreview();
      };
      $('ac-dim').onchange = function () {
        st.dimension = this.value || null;
        rerenderDims(); schedulePreview();
      };
      $('ac-gran').onchange = function () { st.date_gran = this.value; schedulePreview(); };
      $('ac-sort').onchange = function () { st.sort = this.value; schedulePreview(); };
      $('ac-limit').oninput = function () {
        var v = parseInt(this.value, 10);
        st.limit = isNaN(v) ? 10 : Math.max(0, Math.min(200, v));
        schedulePreview();
      };
      $('ac-add-metric').onclick = function () {
        if (st.metrics.length >= 4) { toast('最多 4 个指标'); return; }
        st.metrics.push({ agg: 'count', field: '' });
        rerenderMetrics(); schedulePreview();
      };
      $('ac-add-filter').onclick = function () {
        if (st.filters.length >= 8) { toast('最多 8 个筛选'); return; }
        st.filters.push({ field: anaCols(st.table_id)[0] ? anaCols(st.table_id)[0].key : '', op: '', value: '', value2: '' });
        rerenderFilters(); schedulePreview();
      };
      $('ac-sort').value = st.sort || 'metric_desc';
      rerenderDims(); rerenderMetrics(); rerenderFilters();
      renderPreview();
    });

  function saveChart() {
    if (!st.table_id) { toast('请选择数据表'); return; }
    if (st.type !== 'kpi' && !st.dimension) { toast('请选择维度'); return; }
    if (!st.metrics.length) { toast('请添加指标'); return; }
    for (var i = 0; i < st.metrics.length; i++) {
      var m = st.metrics[i];
      if (m.agg !== 'count' && !m.field) { toast('请选择指标字段'); return; }
    }
    var out = JSON.parse(JSON.stringify(st));
    out.title = ($('ac-title').value || '').trim() || anaAutoTitle(out);
    out.limit = Math.max(0, Math.min(200, parseInt(out.limit, 10) || 0));
    var cur = anaCurrentDash();
    var list = ((cur.config && cur.config.charts) || []).slice();
    if (editing) list[idx] = out; else list.push(out);
    anaSaveCharts(list).then(function () {
      closeModal(); renderAnaDash(); toast('图表已保存');
    }).catch(function (e) { toast('保存失败：' + e.message); });
  }
}

async function anaSaveCharts(charts) {
  var d = await api('/api/dashboards/' + ANA.currentId, 'PATCH', { config: { charts: charts } });
  var dash = anaCurrentDash();
  if (dash && d && d.dashboard) dash.config = d.dashboard.config;
  return d;
}

/* ---------- 看板与图表操作 ---------- */
async function anaAddChart() {
  if (!ANA.canEdit) return;
  if (!ANA.dashboards.length) {
    try {
      var c = await api('/api/dashboards', 'POST',
        { project: ANA.project, name: '默认看板', config: { charts: [] } });
      ANA.dashboards = [c.dashboard];
      ANA.currentId = c.dashboard.id;
      renderAnaChrome();
    } catch (e) { toast('新建看板失败：' + e.message); return; }
  }
  openChartEditor(null);
}

async function anaDeleteChart(idx) {
  var dash = anaCurrentDash();
  if (!dash) return;
  var charts = ((dash.config && dash.config.charts) || []).slice();
  var c = charts[idx];
  if (!c) return;
  var ok = await appConfirm('删除图表',
    '删除图表「<b>' + esc(c.title || anaAutoTitle(c)) + '</b>」？', '删除', true);
  if (!ok) return;
  charts.splice(idx, 1);
  anaSaveCharts(charts).then(function () {
    renderAnaDash(); toast('图表已删除');
  }).catch(function (e) { toast('删除失败：' + e.message); });
}

function saveAnalysisPage() {
  if (!ANA.canEdit) return;
  var dash = anaCurrentDash();
  if (!dash) { toast('还没有看板'); return; }
  var charts = (dash.config && dash.config.charts) || [];
  if (!charts.length) { toast('先添加图表再保存'); return; }
  if (dash.is_page) { anaSavePage(dash, dash.name); return; }
  var def = (dash.name && dash.name !== '默认看板') ? dash.name : '数据分析';
  modal('保存分析页面',
    '<div class="frm"><label>页面名称</label><input id="asp-name" value="' + esc(def) + '"></div>',
    '<button class="btn" id="asp-cancel">取消</button><button class="btn btn-primary" id="asp-ok">保存</button>',
    function () {
      $('asp-cancel').onclick = closeModal;
      $('asp-name').focus();
      $('asp-name').select();
      $('asp-name').onkeydown = function (e) { if (e.key === 'Enter') $('asp-ok').click(); };
      $('asp-ok').onclick = function () {
        var name = $('asp-name').value.trim();
        if (!name) { toast('请填写页面名称'); return; }
        anaSavePage(dash, name);
      };
    });
}

async function anaSavePage(dash, name) {
  var d;
  try {
    d = await api('/api/dashboards/' + dash.id, 'PATCH',
      { name: name, is_page: true, config: dash.config });
  } catch (e) {
    toast('保存失败：' + e.message);
    return;
  }
  dash.name = d.dashboard.name;
  dash.is_page = true;
  dash.config = d.dashboard.config;
  closeModal();
  toast('已保存到「' + dash.name + '」');
  try { await refreshMeta(); } catch (e2) {}
  if (!(META.analysis_pages || []).some(function (p) { return p.id === dash.id; })) {
    META.analysis_pages = (META.analysis_pages || []).concat([{
      id: dash.id, project: dash.project, name: dash.name, sort: dash.sort || 0
    }]);
  }
  renderAnaChrome(); renderAnaDash(); renderSidebar();
}

function anaNewDashboard() {
  if (!ANA.canEdit) return;
  modal('新建看板',
    '<div class="frm"><label>看板名称</label><input id="and-name" placeholder="例如：投放总览"></div>',
    '<button class="btn" id="and-cancel">取消</button><button class="btn btn-primary" id="and-ok">创建</button>',
    function () {
      $('and-cancel').onclick = closeModal;
      $('and-name').focus();
      $('and-name').onkeydown = function (e) { if (e.key === 'Enter') $('and-ok').click(); };
      $('and-ok').onclick = async function () {
        var name = $('and-name').value.trim();
        if (!name) { toast('请填写看板名称'); return; }
        try {
          var c = await api('/api/dashboards', 'POST',
            { project: ANA.project, name: name, config: { charts: [] } });
          ANA.dashboards.push(c.dashboard);
          ANA.currentId = c.dashboard.id;
          closeModal(); renderAnaChrome(); renderAnaDash();
          toast('看板已创建');
        } catch (e) { toast('创建失败：' + e.message); }
      };
    });
}

function anaRenameDashboard() {
  var dash = anaCurrentDash();
  if (!dash) return;
  modal('重命名看板',
    '<div class="frm"><label>看板名称</label><input id="anr-name" value="' + esc(dash.name) + '"></div>',
    '<button class="btn" id="anr-cancel">取消</button><button class="btn btn-primary" id="anr-ok">保存</button>',
    function () {
      $('anr-cancel').onclick = closeModal;
      $('anr-name').focus();
      $('anr-name').select();
      $('anr-name').onkeydown = function (e) { if (e.key === 'Enter') $('anr-ok').click(); };
      $('anr-ok').onclick = async function () {
        var name = $('anr-name').value.trim();
        if (!name) { toast('请填写看板名称'); return; }
        try {
          var d = await api('/api/dashboards/' + dash.id, 'PATCH', { name: name });
          dash.name = d.dashboard.name;
          if (dash.is_page) {
            var pg = (META.analysis_pages || []).find(function (p) { return p.id === dash.id; });
            if (pg) pg.name = dash.name;
          }
          closeModal(); renderAnaChrome(); renderSidebarPages();
          toast('已重命名');
        } catch (e) { toast('重命名失败：' + e.message); }
      };
    });
}

async function anaDeleteDashboard() {
  var dash = anaCurrentDash();
  if (!dash) return;
  var ok = await appConfirm('删除看板',
    '删除看板「<b>' + esc(dash.name) + '</b>」及其中所有图表？', '删除', true);
  if (!ok) return;
  api('/api/dashboards/' + dash.id, 'DELETE').then(function () {
    ANA.dashboards = ANA.dashboards.filter(function (x) { return x.id !== dash.id; });
    ANA.currentId = ANA.dashboards.length ? ANA.dashboards[0].id : null;
    META.analysis_pages = (META.analysis_pages || []).filter(function (p) { return p.id !== dash.id; });
    renderAnaChrome(); renderAnaDash(); renderSidebarPages();
    toast('看板已删除');
  }).catch(function (e) { toast('删除失败：' + e.message); });
}

/* ---------- 事件绑定 ---------- */
$('btn-analysis').onclick = openAnalysis;
$('btn-reset').onclick = resetView;
$('btn-view').onclick = function (e) {
  e.stopPropagation();
  if ($('view-menu')) closeViewMenu(); else openViewMenu();
};
$('btn-ana-back').onclick = closeAnalysis;
$('ana-dash-sel').onchange = function () {
  if (this.value === '__new__') {          // 下拉里的「＋ 新建看板…」
    this.value = ANA.currentId ? String(ANA.currentId) : '';
    anaNewDashboard();
    return;
  }
  ANA.currentId = this.value ? +this.value : null;
  renderAnaDash();
};
$('ana-dash-rename').onclick = anaRenameDashboard;
$('ana-dash-del').onclick = anaDeleteDashboard;
$('ana-save').onclick = saveAnalysisPage;
$('ana-add-chart').onclick = anaAddChart;
$('ana-refresh').onclick = function () {
  ANA.rows = {}; ANA.loads = {}; renderAnaDash();
};

document.addEventListener('mousemove', function (e) {
  var tip = $('ana-tip');
  if (!tip) return;
  var t = e.target && e.target.closest ? e.target.closest('[data-tip]') : null;
  if (!t) { tip.classList.remove('on'); return; }
  tip.textContent = t.getAttribute('data-tip');
  tip.classList.add('on');
  var x = e.clientX + 12, y = e.clientY + 14;
  var w = tip.offsetWidth, h = tip.offsetHeight;
  if (x + w > window.innerWidth - 8) x = e.clientX - w - 12;
  if (y + h > window.innerHeight - 8) y = e.clientY - h - 14;
  tip.style.left = x + 'px';
  tip.style.top = y + 'px';
});
document.addEventListener('mouseleave', function () {
  var tip = $('ana-tip');
  if (tip) tip.classList.remove('on');
});

