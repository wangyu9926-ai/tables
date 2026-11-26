/* shell.js — index.html 内联脚本拆出的第 2/12 段（原第 1509-2255 行）。
   加载顺序见 index.html 底部；全部是全局函数，跨文件可直接调用。 */
/* ============ 状态（通用：任意表，不写死业务） ============ */
var META = null, GROUPS = [];
var state = { tid:null, kw:'', page:1, size:(+localStorage.getItem('kb_pgsize')||50), sort:'', dir:1, filters:{} };
var FACETS = {}, openFilter = null, editing = null;
var TYPE_LABEL = {text:'文本',number:'数字',date:'日期',select:'单选',multiselect:'多选',
                  checkbox:'勾选',image:'图片',formula:'公式'};

function curTable() { return (META && META.tables) ? META.tables.find(function(t){return t.id===state.tid;}) : null; }
function canEditRows() { var t=curTable(); return !!(t && t.my_role==='editor'); }
function curCols() { var t=curTable(); return t ? t.columns.filter(function(c){return !c.hidden;}) : []; }
function colByKey(k) { return curTable().columns.find(function(c){return c.key===k;}); }
function curGroup() {
  for (var i=0;i<GROUPS.length;i++) if (GROUPS[i].sheets.some(function(s){return s.id===state.tid;})) return GROUPS[i];
  return GROUPS[0] || {name:'',sheets:[]};
}

/* ============ 三层导航：项目 → 表 → Sheet ============ */
var PROJECTS = [];   // [{name, groups:[{name,icon,sheets:[]}], tableCount}]

/* 元信息走 localStorage 缓存：刷新页面先用上次的结构秒开，再后台对齐最新。
   缓存按账号分开（见 scopeLocalKeys）：共用一台电脑时，后登录的人不能先看到上一位能看的项目、表和字段。 */
var META_KEY = 'kb_meta_v1';
function readMetaCache() {
  try { var m = JSON.parse(localStorage.getItem(META_KEY) || 'null'); return (m && m.tables) ? m : null; }
  catch (e) { return null; }
}
function writeMetaCache(m) {
  try { localStorage.setItem(META_KEY, JSON.stringify(m)); } catch (e) {}
}

/* 记住离开时的位置：刷新/重开浏览器回到原来的表、筛选和分析页（筛选条件是业务数据，同样按账号分开） */
var VIEW_KEY = 'kb_view_v1';
/* 自己拖出来的侧栏/页签顺序，也只存在本机（按账号）：团队共用一张表，别人的顺序不该被我改掉 */
var ORDER_KEY = 'kb_order_v1';
function readOrder() {
  try { var o = JSON.parse(localStorage.getItem(ORDER_KEY) || '{}'); return (o && typeof o === 'object' && !Array.isArray(o)) ? o : {}; }
  catch (e) { return {}; }
}
function writeOrder(o) { try { localStorage.setItem(ORDER_KEY, JSON.stringify(o)); } catch (e) {} }
/* 把本机保存的顺序套到分组/页签上；没拖过的保持原来的表结构顺序 */
function applyUserOrder(groups) {
  var o = readOrder();
  if (!groups || !groups.length) return groups;
  if (o.g && o.g.length) {
    groups.sort(function (a, b) {
      var ia = o.g.indexOf(a.name), ib = o.g.indexOf(b.name);
      if (ia < 0 && ib < 0) return 0;
      if (ia < 0) return 1;
      if (ib < 0) return -1;
      return ia - ib;
    });
  }
  groups.forEach(function (g) {
    var ord = (o.s || {})[g.name];
    if (ord && ord.length) {
      g.sheets.sort(function (a, b) {
        var ia = ord.indexOf(a.id), ib = ord.indexOf(b.id);
        if (ia < 0 && ib < 0) return 0;
        if (ia < 0) return 1;
        if (ib < 0) return -1;
        return ia - ib;
      });
    }
  });
  return groups;
}
function resetUserOrder() {
  try { localStorage.removeItem(ORDER_KEY); } catch (e) {}
  refreshMeta();
  toast('已恢复默认顺序');
}
/* 登录态拿到后调用：缓存键带上账号 ID。旧版本不分账号，存在这两个不带后缀的键里，顺手清掉。 */
function scopeLocalKeys() {
  var uid = (ME && ME.uid) ? '_u' + ME.uid : '';
  META_KEY = 'kb_meta_v1' + uid; VIEW_KEY = 'kb_view_v1' + uid; ORDER_KEY = 'kb_order_v1' + uid;
  VIEWS_KEY = 'kb_views_v1' + uid;
  if (uid) {
    ['kb_meta_v1', 'kb_view_v1', 'kb_order_v1', 'kb_views_v1'].forEach(function (k) { try { localStorage.removeItem(k); } catch (e) {} });
    // 共用电脑：别的账号留下的缓存也清掉（只留自己的）。有人没点退出就关浏览器时，下一位不会继承
    try {
      Object.keys(localStorage).forEach(function (k) {
        if (/^kb_(meta|view|views|order)_v1_u\d+$/.test(k) && k.slice(-uid.length) !== uid) localStorage.removeItem(k);
      });
    } catch (e) {}
  }
}
/* 退出登录时清掉这个账号在本机留下的缓存（结构 + 上次看到的位置和筛选条件 + 自己拖的顺序 + 常用视图） */
function clearLocalCaches() {
  [META_KEY, VIEW_KEY, ORDER_KEY, VIEWS_KEY].forEach(function (k) { try { localStorage.removeItem(k); } catch (e) {} });
}
function saveView(v) {
  try {
    if (!v) {
      if ($('analysis-view').classList.contains('on')) v = { v: 'analysis', id: ANA.currentId, project: ANA.project };
      else if ($('table-view').classList.contains('on') && state.tid)
        v = { v: 'table', tid: state.tid, kw: state.kw, page: state.page, sort: state.sort, dir: state.dir, filters: state.filters };
      else if ($('proj-view').style.display === 'block' && curProject) v = { v: 'proj', project: curProject.name };
      else v = { v: 'home' };
    }
    localStorage.setItem(VIEW_KEY, JSON.stringify(v));
  } catch (e) {}
}
function restoreView() {
  var v = null;
  try { v = JSON.parse(localStorage.getItem(VIEW_KEY) || 'null'); } catch (e) {}
  if (!v) return false;
  if (v.v === 'table' && v.tid && META.tables.some(function (t) { return t.id === v.tid; })) {
    var t0 = META.tables.find(function (t) { return t.id === v.tid; });
    state.kw = v.kw || '';
    state.page = v.page || 1;
    state.sort = v.sort || '';
    state.dir = v.dir || 1;
    state.filters = v.filters || {};
    // 恢复时丢掉已不存在的列的筛选，避免把整表筛没
    if (t0 && typeof state.filters === 'object') {
      var ks = Object.create(null);
      (t0.columns || []).forEach(function (c) { ks[c.key] = 1; });
      Object.keys(state.filters).forEach(function (k) { if (!ks[k]) delete state.filters[k]; });
    }
    openSheet(v.tid, true);
    var kwEl = $('kw'); if (kwEl) kwEl.value = state.kw;
    return true;
  }
  if (v.v === 'analysis' && v.id) {
    if (v.project) curProject = PROJECTS.find(function (p) { return p.name === v.project; }) || curProject;
    if (curProject) { openAnalysisPage(v.id); return true; }
  }
  if (v.v === 'proj' && v.project && PROJECTS.some(function (p) { return p.name === v.project; })) {
    openProject(v.project);
    return true;
  }
  return false;
}

async function boot() {
  await loadMe();   // 先拿登录态：恢复分析页/按钮显隐都需要 ME
  scopeLocalKeys(); // 本地缓存按账号分开，要在读缓存之前定好键
  var cached = readMetaCache();
  if (cached) {                       // 有缓存：先渲染，不等网络
    META = cached; rebuildGroups(); rebuildProjects();
    if (!restoreView()) goHomeProjects();
  }
  var fresh = await api('/api/meta');
  var sameMeta = cached && JSON.stringify(cached) === JSON.stringify(fresh);
  META = fresh; writeMetaCache(fresh);
  if (sameMeta) { setTimeout(prefetchRows, 600); return; }   // 数据没变就不重绘，避免刷新闪一下
  rebuildGroups(); rebuildProjects();
  if (!cached) { goHomeProjects(); setTimeout(prefetchRows, 600); return; }
  // 已有缓存时按当前层级原地刷新，不把用户踢回首页
  if (document.body.classList.contains('in-table') && state.tid) {
    if (META.tables.some(function (x) { return x.id === state.tid; })) {
      $('btn-analysis').style.display = (curProject && curProject.analysis_role) ? '' : 'none';
      renderSidebar(); renderSheetBar(); renderHead(); loadRows();
    } else { goHomeProjects(); }
  } else if ($('analysis-view').classList.contains('on')) {
    renderSidebar(); renderAnaChrome(); renderAnaDash();
  } else if ($('proj-view').style.display !== 'none' && curProject) {
    openProject(curProject.name);
  } else {
    goHomeProjects();
  }
  setTimeout(prefetchRows, 600);
}
function rebuildGroups() {
  /* 名字当键的字典一律不带原型：项目名 / 分组名 / 表名是用户随便起的，叫 __proto__、constructor、toString
     这类名字时，普通对象取到的是对象自带的成员，整个首页会渲染失败（谁都进不来） */
  var map = Object.create(null);
  META.tables.forEach(function (t) {
    var g = t.group_name || t.name;
    if (!map[g]) map[g] = {name:g, icon:t.icon||'📄', sort:t.sort, sheets:[]};
    map[g].sheets.push(t);
  });
  GROUPS = Object.keys(map).map(function (k) {
    var g = map[k];
    g.sheets.sort(function (a, b) { return (a.sort - b.sort) || (a.id - b.id); });
    g.sort = g.sheets.length ? g.sheets[0].sort : g.sort;
    return g;
  }).sort(function(a,b){return a.sort-b.sort;});
  applyUserOrder(GROUPS);
}
function rebuildProjects() {
  var map = Object.create(null);
  (META.projects || []).forEach(function (p) {
    map[p.name] = {name:p.name, icon:p.icon || '📁', groups:[], sort:p.sort || 0,
                   analysis_role:p.analysis_role || null};
  });
  META.tables.forEach(function (t) {
    var p = t.project || '未归类';
    if (!map[p]) map[p] = {name:p, icon:'📁', groups:[], sort:999, analysis_role:null};
    map[p].groups.push(t);
  });
  // 每个项目内再按 group_name 聚合
  PROJECTS = Object.keys(map).map(function (k) {
    var prj = map[k], gmap = Object.create(null);
    prj.groups.forEach(function (t) {
      var g = t.group_name || t.name;
      if (!gmap[g]) gmap[g] = {name:g, icon:t.icon||'📄', sheets:[]};
      gmap[g].sheets.push(t);
    });
    var groups = Object.keys(gmap).map(function (g) {
      var grp = gmap[g];
      grp.sheets.sort(function (a, b) { return (a.sort - b.sort) || (a.id - b.id); });
      grp.sort = grp.sheets.length ? grp.sheets[0].sort : 999999;
      return grp;
    }).sort(function (a, b) { return a.sort - b.sort; });
    return {name: prj.name, icon: prj.icon, tableCount: prj.groups.length,
            analysis_role: prj.analysis_role || null, groups: groups};
  });
  // 同步当前项目引用，避免排序后侧栏仍读旧 PROJECTS 对象
  if (curProject) curProject = PROJECTS.find(function (p) { return p.name === curProject.name; }) || curProject;
  PROJECTS.forEach(function (p) { applyUserOrder(p.groups); });
}

/* ---------- 第一层：项目列表 ---------- */
function goHomeProjects() {
  closeFilterPop();
  closeRowPanel();
  closeMobileSidebar();
  liveClose();                       // 回项目首页就断开推送
  document.body.classList.remove('in-table');
  document.body.classList.remove('in-analysis');
  $('analysis-view').classList.remove('on');
  $('table-view').classList.remove('on');
  $('proj-view').style.display = 'none';
  $('home').style.display = 'block';
  var kw = ($('home-kw') ? $('home-kw').value : '').trim().toLowerCase();
  var list = PROJECTS.filter(function (p) { return !kw || p.name.toLowerCase().indexOf(kw) !== -1; });
  $('home-count').textContent = '';
  var html = list.map(function (p) {
    var ops = '';
    if (ME && ME.role === 'admin') {
      ops = '<button class="hc-ren" data-ren-p="' + esc(p.name) + '" title="重命名项目" aria-label="重命名项目">'
        + '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L19.5 8.5a2.1 2.1 0 0 0-3-3L5 17v3z"/><path d="M13.5 6.5l3 3"/></svg></button>'
        + '<button class="hc-del" data-del-p="' + esc(p.name) + '" title="删除项目" aria-label="删除项目">'
        + '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"/></svg></button>';
    }
    var nT = 0;
    (p.groups || []).forEach(function (g) { nT += (g.sheets || []).length; });
    return '<div class="home-card" data-p="' + esc(p.name) + '">' + ops
      + '<span class="hc-ic">' + esc(p.icon || '📁') + '</span>'
      + '<div class="hc-name">' + esc(p.name) + '</div>'
      + '<div class="hc-meta">' + nT + ' 张表</div>'
      + '</div>';
  }).join('');
  $('home-body').innerHTML = html ? '<div class="home-grid">' + html + '</div>'
    : '<div class="home-empty">还没有项目</div>';
  $('home-body').querySelectorAll('.home-card').forEach(function (card) {
    card.onclick = function () { enterProject(card.getAttribute('data-p')); };
  });
  $('home-body').querySelectorAll('[data-del-p]').forEach(function (b) {
    b.onclick = function (e) { e.stopPropagation(); confirmDeleteProject(b.getAttribute('data-del-p')); };
  });
  $('home-body').querySelectorAll('[data-ren-p]').forEach(function (b) {
    b.onclick = function (e) { e.stopPropagation(); renameProject(b.getAttribute('data-ren-p')); };
  });
  saveView({ v: 'home' });
  applyRoleUI();
}

function confirmDeleteProject(name) {
  if (!ME || ME.role !== 'admin') { toast('只有管理员可以删除项目'); return; }
  var p = null;
  for (var i = 0; i < PROJECTS.length; i++) if (PROJECTS[i].name === name) { p = PROJECTS[i]; break; }
  var n = 0;
  if (p && p.groups) p.groups.forEach(function (g) { n += (g.sheets || []).length; });
  appConfirm('删除项目',
    '删除项目「<b>' + esc(name) + '</b>」？'
    + (n ? '<br><span style="color:var(--text-3)">项目下 ' + n + ' 张表和全部行数据会一并删除。</span>'
         : '<br><span style="color:var(--text-3)">该项目暂无数据表。</span>'),
    '删除', true).then(function (ok) {
    if (!ok) return;
    if (DELETING_PROJECTS[name]) { toast('正在删除，请稍候…'); return; }
    DELETING_PROJECTS[name] = true;
    var busyTimer = setTimeout(function () {
      showBusy('正在删除项目「' + name + '」');
    }, 500);
    var finish = function () {
      clearTimeout(busyTimer);
      hideBusy();
      delete DELETING_PROJECTS[name];
    };
    api('/api/projects/' + encodeURIComponent(name), 'DELETE').then(function (res) {
      if (curProject && curProject.name === name) curProject = null;
      return refreshMeta().then(function () {
        goHomeProjects();
        toast('已删除项目：' + name + (res.deleted ? '（' + res.deleted + ' 张表）' : ''));
      });
    }).catch(function (e) {
      toast('删除失败：' + e.message);
    }).then(finish, finish);
  });
}

function renameProject(name) {
  if (!ME || ME.role !== 'admin') { toast('只有管理员可以重命名项目'); return; }
  modal('重命名项目',
    '<div class="frm"><label>项目名</label><input id="pj-new" value="' + esc(name) + '"></div>',
    '<button class="btn" id="pj-cancel">取消</button><button class="btn btn-primary" id="pj-ok">保存</button>',
    function () {
      $('pj-cancel').onclick = closeModal;
      var ipt = $('pj-new');
      ipt.focus(); ipt.select();
      function ok() {
        var v = ipt.value.trim();
        if (!v || v === name) { closeModal(); return; }
        api('/api/projects/' + encodeURIComponent(name), 'PATCH', { name: v }).then(function () {
          closeModal();
          toast('已重命名为 ' + v);
          refreshMeta().then(goHomeProjects);
        }).catch(function (e) { toast('重命名失败：' + e.message); });
      }
      $('pj-ok').onclick = ok;
      ipt.onkeydown = function (e) {
        if (e.key === 'Enter') ok();
        if (e.key === 'Escape') closeModal();
      };
    });
}

/* ---------- 第二层：项目内的表列表 ---------- */
var curProject = null;
/* 点项目 → 直接进第一张表；项目为空才显示「新建表」页 */
function enterProject(name) {
  var p = PROJECTS.find(function (x) { return x.name === name; });
  if (!p) return;
  curProject = p;
  var g = p.groups.find(function (x) { return x.sheets && x.sheets.length; });
  if (g) { openSheet(g.sheets[0].id); return; }
  if (!ME) { openLogin(); return; }     // 游客只能看项目列表，进表需登录
  openProject(name);
}
function openProject(name) {
  curProject = PROJECTS.find(function (p) { return p.name === name; }) || null;
  if (!curProject) return;
  closeRowPanel();
  closeMobileSidebar();
  document.body.classList.remove('in-table');
  document.body.classList.remove('in-analysis');
  $('analysis-view').classList.remove('on');
  $('home').style.display = 'none';
  $('table-view').classList.remove('on');
  $('proj-view').style.display = 'block';
  $('proj-name').textContent = curProject.name;
  $('proj-count').textContent = '';
  var html = curProject.groups.map(function (g) {
    var sheets = g.sheets.map(function (s) { return s.name.split('·')[1] || s.name; });
    return '<div class="home-card" data-g="' + esc(g.name) + '">'
      + '<div class="hc-name">' + esc(g.name) + '</div>'
      + '<div class="hc-meta">' + esc(sheets.join(' · ')) + '</div></div>';
  }).join('');
  if (!html) {
    var canNew = ME && ME.role === 'admin';
    var canImp = ME && (ME.role === 'admin' || (curProject && curProject.analysis_role === 'editor'));
    if (canNew) html += '<div class="home-card home-empty-card" id="empty-new-table">'
      + '<div class="hc-ic">＋</div><div class="hc-name">新建表</div></div>';
    if (canImp) html += '<div class="home-card home-empty-card" id="empty-import">'
      + '<div class="hc-ic"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/></svg></div>'
      + '<div class="hc-name">导入 Excel</div></div>';
    if (!html) html = '<div class="home-empty">暂无数据表</div>';
  }
  $('proj-body').innerHTML = '<div class="home-grid">' + html + '</div>';
  if ($('empty-new-table')) $('empty-new-table').onclick = function () { openNewTable(''); };
  if ($('empty-import')) $('empty-import').onclick = function () { openImport(true); };
  $('proj-body').querySelectorAll('.home-card[data-g]').forEach(function (card) {
    card.onclick = function () {
      var gname = card.getAttribute('data-g');
      var grp = curProject.groups.find(function (x) { return x.name === gname; });
      if (grp) openSheet(grp.sheets[0].id);
    };
  });
  saveView({ v: 'proj', project: curProject.name });
  applyRoleUI();
}
function goProject() {
  closeFilterPop();
  $('table-view').classList.remove('on');
  if (curProject) openProject(curProject.name); else goHomeProjects();
}

/* ---------- 第三层：表（Sheet 页签在内） ---------- */
function openSheet(tid, keepState) {
  closeRowPanel();
  closeMobileSidebar();
  state.tid = tid;
  if (!keepState) { state.kw=''; state.page=1; state.sort=''; state.filters=Object.create(null); $('kw').value = ''; }
  var t = META.tables.find(function (x) { return x.id === tid; });
  // 定位所属项目（侧栏按项目过滤）
  curProject = PROJECTS.find(function (p) {
    return p.groups.some(function (g) { return g.sheets.some(function (s) { return s.id === tid; }); });
  }) || curProject;
  document.body.classList.remove('in-analysis');
  document.body.classList.add('in-table');
  $('analysis-view').classList.remove('on');
  $('home').style.display = 'none';
  $('proj-view').style.display = 'none';
  $('table-view').classList.add('on');
  liveConnect();                     // 实时刷新：换表就换一条推送连接
  setTvName(t, t ? curGroupName(t) + ' · ' + (t.name.split('·')[1] || t.name) : '');
  $('btn-import').style.display = (t && t.my_role === 'editor') ? '' : 'none';
  $('btn-analysis').style.display = (curProject && curProject.analysis_role) ? '' : 'none';
  $('btn-sync').style.display = (t && ME && ME.role === 'admin') ? '' : 'none';
  $('btn-apikey').style.display = (t && ME && ME.role === 'admin') ? '' : 'none';
  renderSidebar(); renderSheetBar(); renderHead();
  if (!ROWS_CACHE[curRowsKey(tid)]) {
    // 这张 Sheet 没有现成的数据：先把上一张的行清掉、显示“加载中”，不让旧数据挂在新表头下面
    renderRows({rows: [], total: 0, facets: {}});
    $('empty').textContent = '加载中…';
  }
  loadRows().then(prefetchRows, function () {});   // 切过来之后顺手把同组相邻的 Sheet 备好，下次切换零等待
  saveView({ v: 'table', tid: tid, kw: state.kw, page: state.page, sort: state.sort, dir: state.dir, filters: state.filters });
}
function curGroupName(t) { return t ? (t.group_name || t.name) : ''; }
function renderAll() {
  if ($('analysis-view').classList.contains('on')) {
    renderSidebar(); renderAnaChrome(); renderAnaDash();
  } else if ($('table-view').classList.contains('on')) {
    $('btn-analysis').style.display = (curProject && curProject.analysis_role) ? '' : 'none';
    $('btn-sync').style.display = (ME && ME.role === 'admin') ? '' : 'none';
    $('btn-apikey').style.display = (ME && ME.role === 'admin') ? '' : 'none';
    renderSidebar(); renderSheetBar(); renderHead(); loadRows();
  } else if ($('proj-view').style.display === 'block') {
    openProject(curProject ? curProject.name : (PROJECTS[0] && PROJECTS[0].name));
  } else {
    goHomeProjects();
  }
}

/* ============ 侧栏：一组一个条目，组内 Sheet 走顶部页签 ============ */
var sbLastClick = { name: '', t: 0 };

function renderSidebar() {
  var cur = curGroup();
  var inAna = $('analysis-view').classList.contains('on');
  // 侧栏只显示当前项目下的表
  var groups = curProject ? curProject.groups : GROUPS;
  var html = '';
  groups.forEach(function (g) {
    var active = !inAna && g.name === cur.name;
    var del = (ME && ME.role === 'admin')
      ? '<button class="sb-del" data-del-g="' + esc(g.name) + '" title="删除分组" aria-label="删除分组">'
        + '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"/></svg></button>'
      : '';
    html += '<div class="sb-item'+(active?' active':'')+'" data-g="'+esc(g.name)+'" title="双击重命名">'
      + '<span class="sb-name">'+esc(g.name)+'</span>' + del + '</div>';
  });
  $('sb-list').innerHTML = html;
  $('sb-list').querySelectorAll('[data-del-g]').forEach(function (b) {
    b.onclick = function (e) { e.stopPropagation(); confirmDeleteGroup(b.getAttribute('data-del-g')); };
  });
  $('sb-list').querySelectorAll('.sb-item').forEach(function (d) {
    d.onclick = function () {
      if (d.querySelector('input')) return;
      var name = d.getAttribute('data-g');
      var now = Date.now();
      var dbl = sbLastClick.name === name && (now - sbLastClick.t) < 800;
      sbLastClick = { name: name, t: now };
      if (dbl) {
        startRenameGroup(d, name);
        return;
      }
      if (!inAna && name === curGroup().name) return;
      var grp = (curProject ? curProject.groups : GROUPS).find(function (x) { return x.name === name; });
      if (!grp) return;
      openSheet(grp.sheets[0].id);
    };
  });
  renderSidebarPages();
}

function startRenameGroup(el, oldName) {
  if (el.querySelector('input')) return;
  var grp = (curProject ? curProject.groups : GROUPS).find(function (x) { return x.name === oldName; });
  if (!grp) return;
  if (!grp.sheets.every(function (s) { return s.my_role === 'editor'; })) {
    toast('只读权限，不能重命名分组');
    return;
  }
  el.classList.add('editing');
  el.innerHTML = '<input class="sb-rename-ipt">';
  var ipt = el.querySelector('input');
  ipt.value = oldName;
  ipt.focus();
  ipt.select();
  var done = false;
  async function save(ok) {
    if (done) return;
    done = true;
    var val = ipt.value.trim();
    if (ok && val && val !== oldName) {
      try {
        await api('/api/groups/rename', 'PATCH',
          { project: (curProject ? curProject.name : ''), old_name: oldName, new_name: val });
        grp.sheets.forEach(function (s) {
          var short = '';
          if (s.name.indexOf(oldName + '·') === 0) short = s.name.slice(oldName.length + 1);
          else if (s.name.indexOf('·') !== -1) short = s.name.split('·')[1];
          else if (s.name !== oldName) short = s.name;
          s.group_name = val;
          s.name = short ? val + '·' + short : val;
        });
        writeMetaCache(META);
        rebuildGroups(); rebuildProjects();
        if (curProject) curProject = PROJECTS.find(function (p) { return p.name === curProject.name; }) || curProject;
        renderSidebar(); renderSheetBar();
        var t = META.tables.find(function (x) { return x.id === state.tid; });
        if (t && $('tv-name')) setTvName(t, curGroupName(t) + ' · ' + (t.name.split('·')[1] || t.name));
        toast('已重命名为 ' + val);
        return;
      } catch (e) {
        toast('重命名失败：' + e.message);
      }
    }
    renderSidebar();
  }
  ipt.onkeydown = function (e) {
    e.stopPropagation();
    if (e.key === 'Enter') { e.preventDefault(); save(true); }
    else if (e.key === 'Escape') { save(false); }
  };
  ipt.onblur = function () { if (!done) save(true); };
}

function renderSidebarPages() {
  var el = $('sb-pages');
  if (!el) return;
  var pages = (META.analysis_pages || []).filter(function (p) {
    return p.project === (curProject ? curProject.name : '');
  });
  if (!pages.length) { el.innerHTML = ''; return; }
  var inAna = $('analysis-view').classList.contains('on');
  el.innerHTML = '<div class="sb-sep"></div>' + pages.map(function (p) {
    var active = inAna && ANA.currentId === p.id;
    return '<div class="sb-item sb-page'+(active?' active':'')+'" data-page="' + p.id + '">'
      + '<span class="sb-ic"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M4 19V10M10 19V5M16 19v-6M22 19H2"/></svg></span>'
      + '<span class="sb-name">' + esc(p.name) + '</span></div>';
  }).join('');
  el.querySelectorAll('.sb-page').forEach(function (d) {
    d.onclick = function () { openAnalysisPage(+d.getAttribute('data-page')); };
  });
}

/* ============ 页签 + 新增表/字段管理 ============ */
function applyRoleUI() {
  var admin = !!(ME && ME.role === 'admin');
  ['btn-new-project', 'btn-new-table', 'btn-new-table-sb', 'btn-new-group'].forEach(function (id) {
    var el = $(id); if (el) el.style.display = admin ? '' : 'none';
  });
  var t = curTable();
  var canEdit = !!(t && t.my_role === 'editor');
  var ar = $('btn-add-row'); if (ar) ar.style.display = canEdit ? '' : 'none';
  var sa = $('sheet-add'), fm = $('field-mgr'), sd = $('sheet-del');
  if (sa) sa.style.display = admin ? '' : 'none';
  var canFields = admin || !!(ME && ME.can_fields && t && t.my_role === 'editor');
  if (fm) fm.style.display = canFields ? '' : 'none';
  if (sd) sd.style.display = admin ? '' : 'none';
}

/* 表名 + 「汇总总表」徽标：这张表是汇总目标时，从子表汇总来的行改/删会写回子表，界面要说清楚 */
function setTvName(t, txt) {
  var el = $('tv-name');
  if (!el) return;
  var src = (t && t.sync_sources && t.sync_sources.length) ? t.sync_sources : null;
  el.textContent = txt;
  if (src) {
    el.appendChild(document.createTextNode(' '));
    var chip = document.createElement('span');
    chip.className = 'sync-chip';
    chip.textContent = '汇总总表';
    chip.title = '本表汇总了「' + src.join('」「') + '」。从子表汇总来的行：在这里改会写回子表，'
      + '删会连子表那一行一起删；在这张表里自己新增的行不会同步到子表。';
    el.appendChild(chip);
  }
  el.title = txt + (src ? '（汇总总表：改/删汇总行会同步回子表）' : '');
}
function renderSheetBar() {
  var g = curGroup();
  var html = g.sheets.map(function (s) {
    return '<span class="sheet-tab'+(s.id===state.tid?' on':'')+'" data-id="'+s.id+'">'
      + esc(s.name.split('·')[1]||s.name) + '</span>';
  }).join('');
  html += '<span class="sheet-tab sheet-add" id="sheet-add" title="新增 Sheet" aria-label="新增 Sheet">'
       + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg></span>'
       +  '<span class="sheet-tab field-mgr" id="field-mgr"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h16"/></svg> 字段管理</span>'
       +  '<span class="sheet-tab sheet-del" id="sheet-del" title="删除此 Sheet" aria-label="删除此 Sheet">'
       +  '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"/></svg></span>';
  $('sheet-bar').innerHTML = html;
  $('sheet-bar').querySelectorAll('.sheet-tab[data-id]').forEach(function (el) {
    el.title = '双击重命名';
    el.onclick = function () {
      if (el.querySelector('input')) return;       // 编辑中不切表
      var id=+el.getAttribute('data-id'); if(id===state.tid) return;
      state.tid=id; state.kw=''; state.page=1; state.sort=''; state.filters=Object.create(null);
      closeFilterPop(); $('kw').value='';
      var tt=META.tables.find(function(x){return x.id===id;});
      setTvName(tt, tt ? curGroupName(tt)+' · '+(tt.name.split('·')[1]||tt.name) : '');
      renderSheetBar(); renderHead(); loadRows();
    };
    el.ondblclick = function () { startRenameTab(el); };
  });
  $('sheet-add').onclick = addSheetToGroup;
  $('field-mgr').onclick = openFieldMgr;
  $('sheet-del').onclick = confirmDeleteSheet;
  applyRoleUI();
}

/* Sheet 页签「＋」：在当前分组里新建一个空 Sheet（复制当前列结构，不弹新建表框） */
var addSheetBusy = false;
async function addSheetToGroup() {
  var t = curTable(), g = curGroup();
  if (!t || !g || !g.name || addSheetBusy) return;
  addSheetBusy = true;
  var btn = $('sheet-add'); if (btn) btn.classList.add('busy');
  try {
    // 组内已有 SheetN 取最大编号 +1；全库重名再顺延
    var used = Object.create(null);
    META.tables.forEach(function (x) { used[x.name] = 1; });
    var maxN = 0;
    META.tables.forEach(function (x) {
      if ((x.group_name || x.name) !== g.name) return;
      var short = x.name.indexOf('·') >= 0 ? x.name.slice(x.name.indexOf('·') + 1) : x.name;
      var m = /^Sheet\s*(\d+)$/i.exec(short);
      if (m) maxN = Math.max(maxN, parseInt(m[1], 10));
    });
    var n = maxN + 1, short, full, guard = 0;
    do { short = 'Sheet' + n; full = g.name + '·' + short; n++; guard++; }
    while (used[full] && guard < 10000);

    // 复制当前 Sheet 的列（含选项/选项色/公式/宽度/必填/隐藏），不复制行
    var cols = (t.columns || []).map(function (c) {
      return { key: c.key, label: c.label, type: c.type, options: c.options,
               option_colors: c.option_colors, expr: c.expr, width: c.width,
               required: c.required, hidden: c.hidden };
    });
    var d = await api('/api/tables', 'POST', {
      name: full, project: t.project || (curProject && curProject.name) || '',
      group_name: g.name, icon: t.icon || '📄', columns: cols.length ? cols : null
    });
    if (!d || !d.id) throw new Error('未返回新表 ID');

    // 只拉元信息，不先重绘旧 Sheet，避免闪一下
    META = await api('/api/meta');
    writeMetaCache(META);
    invalidateRows();
    rebuildGroups(); rebuildProjects();
    openSheet(d.id);
    toast('已新增 Sheet：' + short);
  } catch (e) {
    toast('新增 Sheet 失败：' + e.message);
  } finally {
    addSheetBusy = false;
    var b = $('sheet-add'); if (b) b.classList.remove('busy');
  }
}

/* 删除当前 Sheet（页签右侧垃圾桶，admin） */
function confirmDeleteSheet() {
  if (!ME || ME.role !== 'admin') { toast('只有管理员可以删除 Sheet'); return; }
  var t = curTable();
  if (!t) return;
  appConfirm('删除 Sheet',
    '删除「<b>' + esc(t.name) + '</b>」？<br><span style="color:var(--text-3)">表内所有行会一并删除。</span>',
    '删除', true).then(function (ok) {
    if (!ok) return;
    api('/api/tables/' + t.id, 'DELETE').then(function () {
      var gname = (t.group_name || t.name);
      META.tables = META.tables.filter(function (x) { return x.id !== t.id; });
      writeMetaCache(META);
      invalidateRows();
      rebuildGroups(); rebuildProjects();
      if (curProject) curProject = PROJECTS.find(function (p) { return p.name === curProject.name; }) || curProject;
      toast('已删除 Sheet');
      // 组里还有别的 Sheet 就留在当前组，别把人踢回项目页
      var g = GROUPS.find(function (x) { return x.name === gname && x.sheets.length; });
      if (g) openSheet(g.sheets[0].id); else goProject();
    }).catch(function (e) { toast('删除失败：' + e.message); });
  });
}

/* 删除整个分组（侧栏分组右侧垃圾桶，admin）：组内所有 Sheet 一并删除 */
function confirmDeleteGroup(gname) {
  if (!ME || ME.role !== 'admin') { toast('只有管理员可以删除分组'); return; }
  var g = null;
  var scope = curProject ? curProject.groups : GROUPS;
  for (var i = 0; i < scope.length; i++) if (scope[i].name === gname) { g = scope[i]; break; }
  if (!g || !g.sheets || !g.sheets.length) return;
  var sheets = g.sheets.slice();
  appConfirm('删除分组',
    '删除分组「<b>' + esc(gname) + '</b>」？<br><span style="color:var(--text-3)">组内 '
    + sheets.length + ' 个 Sheet 和全部行数据会一并删除。</span>',
    '删除', true).then(function (ok) {
    if (!ok) return;
    (async function () {
      for (var j = 0; j < sheets.length; j++) {
        await api('/api/tables/' + sheets[j].id, 'DELETE');
      }
      var ids = sheets.map(function (s) { return s.id; });
      META.tables = META.tables.filter(function (x) { return ids.indexOf(x.id) === -1; });
      writeMetaCache(META);
      invalidateRows();
      rebuildGroups(); rebuildProjects();
      if (curProject) curProject = PROJECTS.find(function (p) { return p.name === curProject.name; }) || curProject;
      toast('已删除分组：' + gname);
      goProject();
    })().catch(function (e) { toast('删除失败：' + e.message); refreshMeta(); });
  });
}

/* 双击 Sheet 页签重命名：就地输入框，回车/失焦保存，Esc 取消 */
function startRenameTab(el) {
  if (el.querySelector('input')) return;
  var id = +el.getAttribute('data-id');
  var t = META.tables.find(function (x) { return x.id === id; });
  if (!t || t.my_role !== 'editor') { toast('只读权限，不能重命名'); return; }
  var old = t.name.split('·')[1] || t.name;
  var w = Math.round(el.getBoundingClientRect().width);
  el.classList.add('editing');
  el.style.width = Math.max(56, w) + 'px';
  el.innerHTML = '<input class="tab-ipt">';
  var ipt = el.querySelector('input');
  ipt.value = old; ipt.focus(); ipt.select();
  var done = false;
  async function save(ok) {
    if (done) return; done = true;
    var val = ipt.value.trim();
    if (ok && val && val !== old) {
      try {
        var res = await api('/api/tables/' + id + '/rename', 'PATCH', { short_name: val });
        var rec = META.tables.find(function (x) { return x.id === id; });
        if (rec) rec.name = res.name || rec.name;
        writeMetaCache(META);
        rebuildGroups(); rebuildProjects();
        if ($('tv-name') && state.tid === id) setTvName(rec, curGroupName(rec) + ' · ' + val);
        renderSidebar(); renderSheetBar();
        toast('已重命名为 ' + val);
        return;
      } catch (e) { toast('重命名失败：' + e.message); }
    }
    renderSheetBar();
  }
  ipt.onkeydown = function (e) {
    e.stopPropagation();
    if (e.key === 'Enter') { e.preventDefault(); save(true); }
    else if (e.key === 'Escape') save(false);
  };
  ipt.onblur = function () { if (!done) save(true); };
}

