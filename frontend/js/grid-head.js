/* grid-head.js — index.html 内联脚本拆出的第 5/12 段（原第 2963-3251 行）。
   加载顺序见 index.html 底部；全部是全局函数，跨文件可直接调用。 */
/* ============ 列宽 ============ */
function colWStore(){ try{return JSON.parse(localStorage.getItem('kb_colw')||'{}');}catch(e){return {};} }
function defW(c){
  var byLabel=84+String(c.label||'').length*14;
  var base=c.width|| (c.type==='number'||c.type==='formula'?100:Math.min(240,Math.max(120,byLabel)));
  return Math.min(260,Math.max(96,base,byLabel));
}
function renderColgroup(){
  var t=curTable(); var html='<col style="width:72px">';
  curCols().forEach(function(c){ var s=colWStore()[t.id+'|'+c.key]; html+='<col data-c="'+esc(c.key)+'" style="width:'+Math.max(96,(s||defW(c)))+'px">'; });
  html+='<col style="width:'+(canEditRows()?56:40)+'px">';
  $('colgroup').innerHTML=html;
}

/* ============ 表头 ============ */
function filterActive(k){
  var f=state.filters[k]; if(!f) return false;
  if(Array.isArray(f)) return f.length>0;
  if(f.op) return f.op==='between' ? !!(f.value||f.value2) : !(f.value===undefined||f.value===''||f.value===null);
  return !!(f.from||f.to);
}
function hasAnyFilter(){
  if (isAdvFilter(state.filters)) return state.filters.conds.length > 0;
  return Object.keys(state.filters).some(filterActive);
}
function isAdvFilter(f){ return !!(f && typeof f === 'object' && !Array.isArray(f) && Array.isArray(f.conds)); }
/* 高级筛选用到哪几列：这些列头的漏斗也点亮，一眼看出筛的是哪几列 */
function advFilterKeys(){ return isAdvFilter(state.filters) ? state.filters.conds.map(function(c){ return c && c.key; }) : []; }
function viewDirty(){ return !!(state.kw || state.sort || hasAnyFilter()); }
/* 「重置」只在这种情况下出现：这一张表被你搜索/筛选/排序过。
   这些都是你自己的视图状态（存在你账号的本地视图里、随请求参数走），不会改动别人的界面。 */
function updateViewChrome(){
  var b=$('btn-reset'); if(b) b.style.display=viewDirty()?'':'none';
  updateViewBtn();
}
function resetView(){
  if(openFilter) closeFilterPop();
  state.kw=''; state.sort=''; state.dir=1; state.filters=Object.create(null); state.page=1;
  var kw=$('kw'); if(kw) kw.value='';
  renderHead(); loadRows(); updateViewChrome();
  toast('已恢复默认视图：搜索、筛选、排序都清掉了');
}

/* ============ 常用视图：把「搜索 + 筛选 + 排序」存成命名视图，一键切换 ============
   和筛选本身一样只存在本机（按账号），不写数据库；每张表各存各的。 */
var VIEWS_KEY = 'kb_views_v1';
function readAllViews(){ try{ var o=JSON.parse(localStorage.getItem(VIEWS_KEY)||'{}'); return (o&&typeof o==='object'&&!Array.isArray(o))?o:{}; }catch(e){ return {}; } }
function writeAllViews(o){ try{ localStorage.setItem(VIEWS_KEY, JSON.stringify(o)); }catch(e){} }
function tableViews(){ var a=readAllViews()[String(state.tid)]; return Array.isArray(a)?a:[]; }
function saveTableViews(list){ var all=readAllViews(); all[String(state.tid)]=list; writeAllViews(all); }
function currentViewCore(){ return {kw:state.kw||'', sort:state.sort||'', dir:state.dir||1, filters:state.filters||{}}; }
function viewCoreKey(core){
  var f=core.filters||{}, ks=Object.keys(f).sort(), o=Object.create(null);
  ks.forEach(function(k){ o[k]=f[k]; });
  return JSON.stringify({kw:core.kw||'', sort:core.sort||'', dir:core.dir||1, filters:o});
}
function findActiveView(){
  var cur=viewCoreKey(currentViewCore());
  return tableViews().find(function(v){ return viewCoreKey(v)===cur; }) || null;
}
function updateViewBtn(){
  var b=$('btn-view'); if(!b) return;
  var v=findActiveView();
  var lbl=b.querySelector('span'); if(lbl) lbl.textContent=v?v.name:'视图';
  b.classList.toggle('on', !!v);
  b.title=v ? ('当前视图：'+v.name+'（只存本机）') : '常用视图：把当前的搜索/筛选/排序存下来一键切换（只存本机）';
}
/* 视图里引用的列可能已经被删掉：应用时把这些条件丢掉，免得整张表被筛成空 */
function sanitizeViewCore(core){
  var keys=Object.create(null); (curTable().columns||[]).forEach(function(c){ keys[c.key]=1; });
  var f=Object.create(null);
  if (isAdvFilter(core.filters)) {
    var conds = core.filters.conds.filter(function(c){ return c && keys[c.key]; });
    if (conds.length) f = { logic: core.filters.logic === 'or' ? 'or' : 'and', conds: conds };
  } else {
    Object.keys(core.filters||{}).forEach(function(k){ if(keys[k]) f[k]=core.filters[k]; });
  }
  return {kw:core.kw||'', sort:keys[core.sort]?core.sort:'', dir:core.dir||1, filters:f};
}
function applyView(v){
  var core=sanitizeViewCore(v);
  if(openFilter) closeFilterPop();
  state.kw=core.kw; state.sort=core.sort; state.dir=core.dir; state.filters=core.filters; state.page=1;
  var kw=$('kw'); if(kw) kw.value=state.kw;
  renderHead(); loadRows(); updateViewChrome();
  toast('已切换到视图「'+v.name+'」');
}
function deleteView(id){
  saveTableViews(tableViews().filter(function(v){ return v.id!==id; }));
  updateViewBtn(); toast('已删除视图');
}
function saveCurrentView(){
  if(!viewDirty()){ toast('当前没有搜索/筛选/排序，不用保存视图'); return; }
  var n=tableViews().length+1;
  modal('保存为常用视图',
    '<div class="frm"><label>视图名称</label><input id="vw-name" placeholder="例如：只看进行中 / 本周待发布" value="视图 '+n+'"></div>'
    + '<div class="hint">只保存当前的搜索词、筛选条件和排序，存在本机（只影响你自己）。同名会覆盖。</div>',
    '<button class="btn" id="vw-cancel">取消</button><button class="btn btn-primary" id="vw-ok">保存</button>',
    function(){
      $('vw-cancel').onclick=closeModal;
      $('vw-name').focus(); $('vw-name').select();
      $('vw-name').onkeydown=function(e){ if(e.key==='Enter') $('vw-ok').click(); };
      $('vw-ok').onclick=function(){
        var name=$('vw-name').value.trim();
        if(!name){ toast('请填写视图名称'); return; }
        var core=currentViewCore();
        var list=tableViews().filter(function(v){ return v.name!==name; });
        list.push({id:'v'+Date.now(), name:name, kw:core.kw, sort:core.sort, dir:core.dir, filters:core.filters});
        saveTableViews(list);
        closeModal(); updateViewBtn();
        toast('已保存视图「'+name+'」');
      };
    });
}
var SRV_VIEWS = {};
function invalidateServerViews(tid){ delete SRV_VIEWS[tid]; }
function ensureServerViews(tid){
  if (!tid) return Promise.resolve(null);
  if (SRV_VIEWS[tid]) return Promise.resolve(SRV_VIEWS[tid]);
  return api('/api/tables/' + tid + '/views').then(function (d) {
    SRV_VIEWS[tid] = d || { views: [], can_share: false };
    return SRV_VIEWS[tid];
  }).catch(function () { SRV_VIEWS[tid] = { views: [], can_share: false }; return SRV_VIEWS[tid]; });
}
function applyServerView(v){
  var c = v.config || {};
  applyView({ id: 's' + v.id, name: v.name, kw: c.kw || '', sort: c.sort || '',
              dir: c.dir || 1, filters: c.filters || {} });
  toast('已应用团队视图「' + v.name + '」');
}
function saveTeamView(){
  var n = (META && META.tables ? 0 : 0);
  modal('保存为团队视图',
    '<div class="frm"><label>视图名称</label><input id="tvw-name" placeholder="例如：全组标准视图 · 只看已发布"></div>'
    + '<div class="hint">团队视图存在服务端，本表所有人打开都能看到并使用；同名会覆盖。只有保存者和管理员能删除。</div>',
    '<button class="btn" id="tvw-cancel">取消</button><button class="btn btn-primary" id="tvw-ok">保存</button>',
    function(){
      $('tvw-cancel').onclick = closeModal;
      $('tvw-name').focus();
      $('tvw-name').onkeydown = function(e){ if(e.key==='Enter') $('tvw-ok').click(); };
      $('tvw-ok').onclick = async function(){
        var name = $('tvw-name').value.trim();
        if(!name){ toast('请填写视图名称'); return; }
        try {
          await api('/api/tables/' + state.tid + '/views', 'POST',
                    { name: name, config: currentViewCore(), shared: true });
          closeModal(); invalidateServerViews(state.tid); ensureServerViews(state.tid);
          toast('已保存团队视图「' + name + '」');
        } catch (e) { toast('保存失败：' + e.message); }
      };
    });
}
async function deleteServerView(id){
  try {
    await api('/api/views/' + id, 'DELETE');
    invalidateServerViews(state.tid);
    await ensureServerViews(state.tid);
    closeViewMenu();
    toast('已删除团队视图');
  } catch (e) { toast('删除失败：' + e.message); }
}
function closeViewMenu(){ var m=$('view-menu'); if(m) m.remove(); }
function openViewMenu(){
  var b=$('btn-view'); if(!b) return;
  closeViewMenu();
  var r=b.getBoundingClientRect(), list=tableViews(), active=findActiveView();
  var m=document.createElement('div'); m.className='ctx-menu view-menu'; m.id='view-menu';
  var html='<button type="button" class="ctx-item" data-act="default"><span>默认视图（清除搜索/筛选/排序）</span></button>';
  if(list.length){
    html+='<div class="ctx-sep"></div>';
    list.forEach(function(v){
      html+='<button type="button" class="ctx-item" data-vid="'+esc(v.id)+'"><span>'+esc(v.name)+'</span>'
        + (active&&active.id===v.id?'<span class="vw-check">✓</span>':'')
        + '<span class="vw-del" data-del="'+esc(v.id)+'" title="删除视图">×</span></button>';
    });
  }
  var sv = SRV_VIEWS[state.tid];
  html+='<div class="ctx-sep"></div>';
  if (!sv) {
    html+='<button type="button" class="ctx-item" disabled><span>团队视图加载中…</span></button>';
  } else {
    var sh = (sv.views || []).filter(function (v) { return v.shared; });
    html+='<div class="ctx-caption">团队视图（本表所有人可见）</div>';
    if (!sh.length) html+='<button type="button" class="ctx-item" disabled><span>还没有团队视图</span></button>';
    sh.forEach(function (v) {
      html+='<button type="button" class="ctx-item" data-sv="' + v.id + '"><span>' + esc(v.name) + '</span>'
        + '<span class="ctx-sub">' + esc(v.owner || '') + '</span>'
        + (((v.mine || ((window.ME || {}).role === 'admin'))) ? '<span class="vw-del" data-sdel="' + v.id + '" title="删除团队视图">×</span>' : '')
        + '</button>';
    });
    if (sv.can_share) html+='<button type="button" class="ctx-item" data-act="save-team"><span>保存当前条件为团队视图…</span></button>';
  }
  html+='<div class="ctx-sep"></div><button type="button" class="ctx-item" data-act="save"><span>保存当前条件为视图…</span></button>';
  m.innerHTML=html;
  if (!sv) ensureServerViews(state.tid).then(function(){ if ($('view-menu')) openViewMenu(); });
  document.body.appendChild(m);
  m.style.left=Math.max(8, Math.min(r.left, window.innerWidth-m.offsetWidth-8))+'px';
  m.style.top=Math.round(r.bottom+6)+'px';
  var mh=m.getBoundingClientRect().height;
  if(r.bottom+6+mh>window.innerHeight-8) m.style.top=Math.max(8, r.top-6-mh)+'px';
  m.querySelectorAll('[data-vid]').forEach(function(it){
    it.onclick=function(e){
      if(e.target.closest('[data-del]')) return;
      var v=tableViews().find(function(x){ return x.id===it.getAttribute('data-vid'); });
      closeViewMenu();
      if(v) applyView(v);
    };
  });
  m.querySelectorAll('[data-del]').forEach(function(x){
    x.onclick=function(e){ e.stopPropagation(); deleteView(x.getAttribute('data-del')); closeViewMenu(); };
  });
  m.querySelector('[data-act="default"]').onclick=function(){ closeViewMenu(); resetView(); };
  m.querySelector('[data-act="save"]').onclick=function(){ closeViewMenu(); saveCurrentView(); };
  m.querySelectorAll('[data-sv]').forEach(function(it){
    it.onclick=function(e){
      if(e.target.closest('[data-sdel]')) return;
      var sv2 = SRV_VIEWS[state.tid] || { views: [] };
      var v = (sv2.views || []).find(function(x){ return String(x.id) === it.getAttribute('data-sv'); });
      closeViewMenu();
      if (v) applyServerView(v);
    };
  });
  m.querySelectorAll('[data-sdel]').forEach(function(x){
    x.onclick=function(e){ e.stopPropagation(); deleteServerView(x.getAttribute('data-sdel')); };
  });
  var st = m.querySelector('[data-act="save-team"]');
  if (st) st.onclick=function(){ closeViewMenu(); saveTeamView(); };
}
document.addEventListener('click', function(e){
  var m=$('view-menu');
  if(m && !e.target.closest('#view-menu') && !e.target.closest('#btn-view')) closeViewMenu();
}, true);
document.addEventListener('keydown', function(e){ if(e.key==='Escape') closeViewMenu(); });
window.addEventListener('scroll', closeViewMenu, true);
window.addEventListener('resize', closeViewMenu);
function renderHead(){
  renderColgroup();
  var t=curTable(), cols=curCols();
  var html='<tr><th style="width:72px;cursor:default">#</th>';
  cols.forEach(function(c){
    var sorted = state.sort===c.key;
    var arrow=sorted?'<span class="th-sort">'+(state.dir===1?'▲':'▼')+'</span>':'';
    var f='<span class="th-f'+(filterActive(c.key)?' on':'')+'" data-c="'+esc(c.key)+'" title="筛选" role="button" aria-label="筛选">'
      + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linejoin="round"><path d="M4 5h16l-6 7v5.5l-4 2V12L4 5z"/></svg></span>';
    html+='<th data-c="'+esc(c.key)+'" class="'+(c.type==='number'||c.type==='formula'?'num ':'')+(sorted?'sorted':'')+'"'
      + ' title="点击按这一列排序，再点一次换方向；排序只影响你自己的视图">'
      + '<div class="th-inner"><span class="th-label">'+esc(c.label)+'</span><span class="col-type">'+esc(TYPE_LABEL[c.type]||c.type)+'</span>'+arrow+f+'</div>'
      + '<div class="col-grip"></div></th>';
  });
  html+='<th style="width:'+(canEditRows()?56:40)+'px;cursor:default">'+(hasAnyFilter()?'<span class="th-f on" id="clear-filters" title="清除全部筛选">✕</span>':'')+'</th></tr>';
  $('thead').innerHTML=html;
  $('thead').querySelectorAll('th[data-c]').forEach(function(th){ th.onclick=function(e){
    if(e.target.closest('.th-f')||e.target.closest('.col-grip'))return;
    // 拖列宽/双击复位时浏览器还会补一个 click，别让它变成“点表头排序”
    if(Date.now() < (window.__kbNoSortUntil || 0)) return;
    var k=th.getAttribute('data-c');
    if(state.sort===k){ if(state.dir===1)state.dir=-1; else {state.sort='';state.dir=1;} }
    else {state.sort=k;state.dir=1;}
    closeFilterPop(); renderHead(); loadRows();
  };});
  $('thead').querySelectorAll('.th-f[data-c]').forEach(function(f){ f.onclick=function(e){
    e.stopPropagation(); var k=f.getAttribute('data-c');
    if(openFilter===k) closeFilterPop(); else { closeFilterPop(); openFilterPop(k,f); }
  };});
    var cf=$('clear-filters'); if(cf) cf.onclick=function(e){ e.stopPropagation(); state.filters=Object.create(null);state.page=1;closeFilterPop();renderHead();loadRows();updateViewChrome(); };
  bindColResize();
}
function bindColResize(){
  var t=curTable();
  $('thead').querySelectorAll('th[data-c]').forEach(function(th){
    var grip=th.querySelector('.col-grip'); if(!grip)return;
    var k=th.getAttribute('data-c'), col=$('colgroup').querySelector('col[data-c="'+CSS.escape(k)+'"]'); if(!col)return;
    grip.onmousedown=function(e){ e.preventDefault();e.stopPropagation();
      window.__kbNoSortUntil = Date.now() + 700;
      var sx=e.clientX,sw=parseFloat(col.style.width)||100;
      document.body.classList.add('resizing');
      var mv=function(ev){ col.style.width=Math.max(96,Math.round(sw+ev.clientX-sx))+'px'; };
      var up=function(){ document.removeEventListener('mousemove',mv);document.body.classList.remove('resizing');
        window.__kbNoSortUntil = Date.now() + 300;
        var st=colWStore(); st[t.id+'|'+k]=parseFloat(col.style.width); localStorage.setItem('kb_colw',JSON.stringify(st)); };
      document.addEventListener('mousemove',mv); document.addEventListener('mouseup',up,{once:true});
    };
    grip.ondblclick=function(e){ e.preventDefault();e.stopPropagation();
      window.__kbNoSortUntil = Date.now() + 700;
      var st=colWStore(); delete st[t.id+'|'+k]; localStorage.setItem('kb_colw',JSON.stringify(st));
      col.style.width=defW(colByKey(k))+'px'; };
  });
}

