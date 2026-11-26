/* core.js — index.html 内联脚本拆出的第 1/12 段（原第 1382-1508 行）。
   加载顺序见 index.html 底部；全部是全局函数，跨文件可直接调用。 */
var $ = function (id) { return document.getElementById(id); };
function esc(s) { return String(s).replace(/[&<>"']/g, function (c) {
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
function toast(msg) { var t=$('toast'); t.textContent=msg; t.classList.add('show');
  clearTimeout(t._h); t._h=setTimeout(function(){t.classList.remove('show');},1900); }

var BUSY_TIMER = null, BUSY_START = 0, DELETING_PROJECTS = Object.create(null);
function showBusy(title) {
  var r = $('busy-root'); if (!r) return;
  r.innerHTML = '<div class="busy-mask"><div class="busy-box" role="status" aria-live="polite">'
    + '<div class="busy-title">' + esc(title) + '<span class="busy-cursor"></span></div>'
    + '<div class="busy-sub" id="busy-sub">已等待 0 秒</div></div></div>';
  r.classList.add('on');
  BUSY_START = Date.now();
  clearInterval(BUSY_TIMER);
  BUSY_TIMER = setInterval(function () {
    var e = $('busy-sub'); if (!e) return;
    var s = Math.floor((Date.now() - BUSY_START) / 1000);
    e.textContent = '已等待 ' + s + ' 秒' + (s >= 10 ? '，数据较多，仍在处理，请稍候…' : '');
  }, 500);
}
function hideBusy() {
  clearInterval(BUSY_TIMER); BUSY_TIMER = null;
  var r = $('busy-root'); if (r) { r.classList.remove('on'); r.innerHTML = ''; }
}
/* 离开页面前把已经画出来的数据清掉。
   浏览器会把离开的页面整页留在“后退缓存”里，之后按“后退”能原样恢复 —— 不发请求、不查登录态。
   共用一台电脑时，下一位按一下后退就可能看到上一位的表。所以：跳登录页之前先清空；
   万一还是被恢复出来（见下面 pageshow），就强制重新加载，由网关按当前登录态决定。 */
function wipeScreen() {
  ROWS_CACHE = {}; ROWDATA = {};
  ['sb-list', 'home-body', 'proj-body', 'thead', 'tbody', 'sheet-bar', 'ana-body', 'rp-body'].forEach(function (id) {
    var el = $(id); if (el) el.innerHTML = '';
  });
  var kw = $('kw'); if (kw) kw.value = '';
}
function gotoLogin() {
  try { wipeScreen(); } catch (e) {}
  location.href = '/login';
}
window.addEventListener('pageshow', function (e) { if (e.persisted) location.reload(); });

async function api(path, method, body) {
  var opt = { method: method || 'GET', headers: {'Content-Type':'application/json'}, cache: 'no-store' };
  if (body !== undefined) opt.body = JSON.stringify(body);
  var r = await fetch(path, opt);
  if (r.status === 401) { gotoLogin(); throw new Error('登录已失效'); }
  if (!r.ok) { var e = await r.json().catch(function(){return {};}); throw new Error(e.detail || ('HTTP '+r.status)); }
  return r.json();
}
async function apiForm(path, fd) {
  var r = await fetch(path, { method:'POST', body:fd, cache:'no-store' });
  if (r.status === 401) { gotoLogin(); throw new Error('登录已失效'); }
  if (!r.ok) { var e = await r.json().catch(function(){return {};}); throw new Error(e.detail || ('HTTP '+r.status)); }
  return r.json();
}
async function uploadImageFile(tid, file) {
  var fd = new FormData();
  fd.append('file', file);
  fd.append('tid', String(tid));
  return await apiForm('/api/upload/image', fd);
}

/* ---------- 侧栏收起 / 展开 ---------- */
var SB_KEY = 'kb_sb_collapsed';
function sbToggleIcon(collapsed) {
  return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    + '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>'
    + (collapsed ? '<path d="M13 9l3 3-3 3"/>' : '<path d="M16 9l-3 3 3 3"/>')
    + '</svg>';
}
function applySidebarState() {
  var collapsed = false;
  try { collapsed = localStorage.getItem(SB_KEY) === '1'; } catch (e) {}
  document.body.classList.toggle('sb-collapsed', collapsed);
  var label = collapsed ? '展开数据表' : '收起数据表';
  ['btn-sb-toggle', 'btn-sb-toggle-ana'].forEach(function (id) {
    var b = $(id);
    if (!b) return;
    b.title = label;
    b.setAttribute('aria-label', label);
    b.innerHTML = sbToggleIcon(collapsed) + '<span>' + (collapsed ? '展开' : '收起') + '</span>';
  });
}
function toggleSidebar() {
  var collapsed = !document.body.classList.contains('sb-collapsed');
  try { localStorage.setItem(SB_KEY, collapsed ? '1' : '0'); } catch (e) {}
  applySidebarState();
}
var TAG_N = 11;
function tagClass(s) {
  // 语义色优先（都取自 11 色盘）
  if (/已合作|已发布|已完成|完成/.test(s)) return 'tag-c4';   // 薄荷浅绿
  if (/已建联/.test(s)) return 'tag-c0';                      // 浅天青蓝
  if (/建联中|待发布|进行中|处理中/.test(s)) return 'tag-c1';  // 浅杏奶橘黄
  if (/待定|待处理/.test(s)) return 'tag-c7';                 // 浅鹅黄
  if (/拒绝|取消|失败/.test(s)) return 'tag-c9';               // 豆沙浅红
  var h = 0;
  for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return 'tag-c' + (h % TAG_N);
}
var COLOR_NAMES = ['浅天青蓝','浅杏奶橘黄','淡薰衣草紫','浅桃粉','薄荷浅绿','浅焦糖棕',
                   '浅灰蓝','浅鹅黄','灰调青灰','豆沙浅红','浅灰紫'];
function tagColorIndex(v, c){
  var s=String(v), oc=(c&&c.option_colors)||{};
  if(oc[s]!==undefined && oc[s]!==null && oc[s]!=='') return +oc[s];
  return null;
}
function tagFor(v, c) {
  var s = String(v);
  var fixed = tagColorIndex(s, c);
  if (fixed !== null && fixed >= 0 && fixed < TAG_N) return 'tag-c' + fixed;
  var opts = (c && c.options) || [];
  var i = opts.indexOf(s);
  if (i !== -1 && !/已合作|已发布|已完成|完成|已建联|建联中|待发布|进行中|处理中|待定|待处理|拒绝|取消|失败/.test(s)) {
    // 每列一个起始色，列内按选项顺序轮流用 11 色盘；跳过已被手动指定的颜色
    var base = 0, ck = String((c && (c.key || c.label)) || '');
    for (var j = 0; j < ck.length; j++) base = (base * 31 + ck.charCodeAt(j)) >>> 0;
    var used = {}, oc2 = (c && c.option_colors) || {};
    Object.keys(oc2).forEach(function (k) { if (oc2[k] !== '') used[+oc2[k]] = 1; });
    var idx = (base + i) % TAG_N, tries = 0;
    while (used[idx] && tries < TAG_N) { idx = (idx + 1) % TAG_N; tries++; }
    return 'tag-c' + idx;
  }
  return tagClass(s);
}

