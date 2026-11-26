/* auth-menu.js — index.html 内联脚本拆出的第 10/12 段（原第 5667-6277 行）。
   加载顺序见 index.html 底部；全部是全局函数，跨文件可直接调用。 */
/* ============ 登录 / 权限（网关 /__auth/*） ============ */
var ME = null, AUTH_ON = true;

async function loadMe() {
  try {
    var d = await api('/__auth/me');
    ME = d.user || null;
  } catch (e) {
    if (String(e.message).indexOf('404') !== -1) { AUTH_ON = false; }
    ME = null;
  }
  renderUserBox();
  applyRoleUI();
}
function renderUserBox() {
  var box = $('user-box');
  if (!box) return;
  if (!AUTH_ON) { box.style.display = 'none'; return; }
  box.style.display = '';
  if (!ME) {
    box.innerHTML = '<button class="top-tag" id="btn-login">登录</button>';
    $('btn-login').onclick = openLogin;
    return;
  }
  var name = ME.name || ME.username;
  box.innerHTML = '<button class="top-tag user-chip" id="btn-user">' + esc(name)
    + ' <span style="color:var(--text-3);font-size:10px">▾</span></button>'
    + '<div class="user-menu" id="user-menu">'
    + '<div data-act="pw">修改密码</div>'
    + ((ME.role === 'admin' || ME.can_users) ? '<div data-act="users">用户管理</div>' : '')
    + (ME.role === 'admin' ? '<div data-act="imgtrash">图片回收站</div>' : '')
    + '<div data-act="order" title="只恢复你自己拖出来的页签/侧栏顺序">恢复默认顺序</div>'
    + '<div class="um-sep"></div><div data-act="logout">退出登录</div></div>';
  $('btn-user').onclick = function (e) {
    e.stopPropagation();
    $('user-menu').classList.toggle('open');
  };
  $('user-menu').querySelectorAll('[data-act]').forEach(function (it) {
    it.onclick = function (e) {
      e.stopPropagation();
      $('user-menu').classList.remove('open');
      var a = it.getAttribute('data-act');
      if (a === 'users') openUserAdmin();
      else if (a === 'imgtrash') openImageTrash();
      else if (a === 'pw') openPassword();
      else if (a === 'order') resetUserOrder();
      else if (a === 'logout') doLogout();
    };
  });
}
function openLogin() {
  modal('登录',
    '<div class="frm"><label>账号</label><input id="lg-u" autocomplete="username" placeholder="用户名"></div>'
    + '<div class="frm"><label>密码</label><input id="lg-p" type="password" autocomplete="current-password" placeholder="密码"></div>'
    + '<div class="hint">账号由管理员开通；未登录无法查看任何数据。</div>',
    '<button class="btn" id="lg-ok">登录</button>',
    function () {
      $('lg-u').focus();
      async function go() {
        var u = $('lg-u').value.trim(), p = $('lg-p').value;
        if (!u || !p) { toast('请输入账号和密码'); return; }
        try {
          var d = await api('/__auth/login', 'POST', { username: u, password: p });
          ME = d.user || null;
          scopeLocalKeys();   // 换了账号，本地缓存跟着换
          closeModal(); renderUserBox();
          var npb = $('btn-new-project');
          if (npb) npb.style.display = ME ? '' : 'none';
          try { await refreshMeta(); } catch (e2) {}
          if (npb) npb.style.display = ME ? '' : 'none';
          toast('已登录：' + ((ME && (ME.name || ME.username)) || u));
        } catch (e) { toast('登录失败：' + e.message); }
      }
      $('lg-ok').onclick = go;
      $('lg-p').onkeydown = function (e) { if (e.key === 'Enter') go(); };
    });
}
/* ---------- 用户管理（站内弹窗，管理员） ---------- */
/* 图片回收站（管理员）：清空单元格/删行/删列/删表后图片软删在此，可恢复或彻底删除 */
function openImageTrash() {
  if (!ME || ME.role !== 'admin') { toast('需要管理员权限'); return; }
  modal('图片回收站',
    '<div id="it-body" style="min-height:120px">加载中…</div>',
    '<button class="btn" id="it-empty">清空回收站</button><button class="btn" id="it-close">关闭</button>',
    function () {
      $('it-close').onclick = closeModal;
      function kb(n) { return n > 1024 ? (n / 1024).toFixed(0) + ' KB' : n + ' B'; }
      function load() {
        api('/api/images/trash').then(function (d) {
          var rows = d.rows || [];
          if (!rows.length) { $('it-body').innerHTML = '<div style="color:var(--text-3);font-size:var(--fs-md);padding:20px 0;text-align:center">回收站是空的</div>'; return; }
          $('it-body').innerHTML = '<table class="ua-tbl"><thead><tr><th>预览</th><th>来源表</th><th>类型</th><th>大小</th><th>删除时间</th><th style="width:130px">操作</th></tr></thead><tbody>'
            + rows.map(function (r) {
              return '<tr data-id="' + r.id + '">'
                + '<td><img src="/api/tables/' + r.table_id + '/images/' + r.id + '" style="width:44px;height:30px;object-fit:cover;border-radius:5px;background:var(--gray-50)" '
                + 'onerror="this.replaceWith(document.createTextNode(\'图片\'))"></td>'
                + '<td>' + esc(r.table_name) + '</td><td>' + esc((r.mime || '').replace('image/', '')) + '</td>'
                + '<td>' + kb(r.size) + '</td><td>' + esc(String(r.deleted_at || '').replace('T', ' ').slice(0, 16)) + '</td>'
                + '<td><button class="mini" data-restore="' + r.id + '">恢复</button>'
                + '<button class="mini danger" data-purge="' + r.id + '">彻底删除</button></td></tr>';
            }).join('') + '</tbody></table>';
          $('it-body').querySelectorAll('[data-restore]').forEach(function (b) {
            b.onclick = function () {
              api('/api/images/trash/act', 'POST', { ids: [+b.getAttribute('data-restore')], action: 'restore' })
                .then(function () { toast('已恢复'); load(); })
                .catch(function (e) { toast(e.message); });
            };
          });
          $('it-body').querySelectorAll('[data-purge]').forEach(function (b) {
            b.onclick = function () {
              appConfirm('彻底删除', '这张图片将被永久删除，不可恢复？', '删除', true).then(function (ok) {
                if (!ok) return;
                api('/api/images/trash/act', 'POST', { ids: [+b.getAttribute('data-purge')], action: 'purge' })
                  .then(function () { toast('已彻底删除'); load(); })
                  .catch(function (e) { toast(e.message); });
              });
            };
          });
        }).catch(function (e) { $('it-body').innerHTML = '加载失败：' + esc(e.message); });
      }
      load();
      $('it-empty').onclick = function () {
        appConfirm('清空回收站', '回收站里的所有图片将被永久删除，不可恢复？', '清空', true).then(function (ok) {
          if (!ok) return;
          api('/api/images/trash').then(function (d) {
            var ids = (d.rows || []).map(function (r) { return r.id; });
            if (!ids.length) { toast('回收站是空的'); return; }
            api('/api/images/trash/act', 'POST', { ids: ids, action: 'purge' })
              .then(function () { toast('回收站已清空'); load(); })
              .catch(function (e) { toast(e.message); });
          });
        });
      };
    });
}

function openUserAdmin() {
  if (!ME || (ME.role !== 'admin' && !ME.can_users)) { toast('需要账号管理权限'); return; }
  var isAdmin = ME.role === 'admin';
  var body = '<div class="ua-add">'
    + '<input id="ua-u" placeholder="账号（字母数字_）">'
    + '<input id="ua-n" placeholder="姓名">'
    + '<input id="ua-p" placeholder="初始密码 ≥6位">'
    + (isAdmin ? '<select id="ua-r"><option value="staff">可编辑用户</option><option value="admin">管理员</option></select>' : '')
    + '<button class="btn btn-primary" id="ua-addbtn" style="height:30px">添加</button>'
    + '</div><div class="ua-body"><div class="ua-left">'
    + '<table class="ua-tbl"><thead><tr><th>ID</th><th>账号</th><th>姓名</th><th>角色</th><th>字段管理</th><th>账号管理</th><th>状态</th><th>操作</th></tr></thead>'
    + '<tbody id="ua-rows"></tbody></table></div>'
    + '<div class="ua-right"><div class="ua-ghead">授权 · <span id="ua-gname">未选择用户</span></div>'
    + '<div class="ua-sec">项目授权（整个项目）</div><div class="ua-list" id="ua-prj"><div class="hint">点左侧「授权」</div></div>'
    + '<div class="ua-sec">表格授权（单张表）<span class="ua-cur" id="ua-cur"></span></div><div class="ua-list" id="ua-tab"></div></div>'
    + '</div>';
  modal('用户管理', body, '<button class="btn" id="ua-close">关闭</button>', function (root) {
    var boxEl = root.querySelector('.box'); if (boxEl) boxEl.classList.add('xwide');
    $('ua-close').onclick = closeModal;
    var CAT = null, GR = {}, UID = 0, UA_PRJ = '';

    function roleLabel(r) { return r === 'viewer' ? '只读' : (r === 'editor' ? '可编辑' : '无'); }
    function selHtml(st, key, cur, projRole, maxRole) {
      maxRole = maxRole || 'editor';
      var h = '<select data-st="' + st + '" data-key="' + esc(key) + '">';
      if (st === 'table') {
        h += '<option value=""' + (cur === '' ? ' selected' : '') + '>继承项目（' + roleLabel(projRole) + '）</option>'
          + '<option value="none"' + (cur === 'none' ? ' selected' : '') + '>无</option>';
      } else {
        h += '<option value=""' + (cur === '' ? ' selected' : '') + '>无</option>';
      }
      h += '<option value="viewer"' + (cur === 'viewer' ? ' selected' : '') + '>只读</option>';
      if (maxRole === 'editor') {
        h += '<option value="editor"' + (cur === 'editor' ? ' selected' : '') + '>可编辑</option>';
      }
      return h + '</select>';
    }
    function bindSel(s) {
      s.onchange = function () {
        var st = s.getAttribute('data-st'), key = s.getAttribute('data-key'), role = s.value;
        if (role === '') {
          delete GR[st + '|' + key];
          api('/__auth/grants/delete', 'POST', { uid: UID, scope_type: st, scope_key: key })
            .then(function () { if (st === 'project') { UA_PRJ = key; paintGrants(); } })
            .catch(function (e) { toast('保存失败：' + e.message); });
        } else {
          GR[st + '|' + key] = role;
          api('/__auth/grants', 'POST', { uid: UID, scope_type: st, scope_key: key, role: role })
            .then(function () { if (st === 'project') { UA_PRJ = key; paintGrants(); } })
            .catch(function (e) { toast('保存失败：' + e.message); });
        }
      };
    }
    function paintGrants() {
      var lim = CAT.limits || {}, pl = lim.projects || {}, tl = lim.tables || {};
      var projects = CAT.projects || [], tables = CAT.tables || [];
      // 默认选中第一个有表的项目；之后保持用户点选的项目
      if (!UA_PRJ || projects.indexOf(UA_PRJ) === -1) {
        UA_PRJ = '';
        for (var i = 0; i < projects.length; i++) {
          if (tables.some(function (t) { return (t.project || '') === projects[i]; })) {
            UA_PRJ = projects[i]; break;
          }
        }
        if (!UA_PRJ) UA_PRJ = projects[0] || '';
      }
      var gp = '';
      projects.forEach(function (p) {
        gp += '<div class="ua-row' + (p === UA_PRJ ? ' sel' : '') + '" data-p="' + esc(p) + '">'
            + '<span class="n" title="' + esc(p) + '">' + esc(p) + '</span>'
            + selHtml('project', p, GR['project|' + p] || '', '', pl[p] || 'editor') + '</div>';
      });
      $('ua-prj').innerHTML = gp || '<div class="hint">暂无项目</div>';
      $('ua-cur').textContent = UA_PRJ ? '· ' + UA_PRJ : '';
      var gt = '';
      tables.filter(function (t) { return (t.project || '') === UA_PRJ; }).forEach(function (t) {
        var projRole = GR['project|' + (t.project || '')] || '';
        var tv = GR['table|' + t.id];
        var cur = tv === 'none' ? 'none' : (tv || '');
        gt += '<div class="ua-row"><span class="n" title="' + esc(t.name) + '">' + esc(t.name) + '</span>'
            + selHtml('table', String(t.id), cur, projRole, tl[String(t.id)] || 'editor') + '</div>';
      });
      $('ua-tab').innerHTML = gt || '<div class="hint">该项目下还没有表</div>';
      $('ua-prj').querySelectorAll('.ua-row').forEach(function (row) {
        row.querySelector('.n').onclick = function () {
          UA_PRJ = row.getAttribute('data-p');
          paintGrants();
        };
      });
      $('ua-prj').querySelectorAll('select').forEach(bindSel);
      $('ua-tab').querySelectorAll('select').forEach(bindSel);
    }
    function loadGrants(uid, name) {
      UID = uid; GR = {};
      $('ua-gname').textContent = name;
      api('/__auth/grants?uid=' + uid).then(function (g) {
        (g.grants || []).forEach(function (x) { GR[x.scope_type + '|' + x.scope_key] = x.role; });
        if (CAT) { paintGrants(); return; }
        return api('/__auth/catalog').then(function (c) { CAT = c; paintGrants(); });
      }).catch(function (e) { toast('读取授权失败：' + e.message); });
    }
    function loadUsers() {
      api('/__auth/users').then(function (d) {
        var h = '';
        (d.users || []).forEach(function (u) {
          var isSelf = ME && (ME.uid === u.id || ME.id === u.id || ME.username === u.username);
          var manage = isAdmin || u.manageable;
          function capCell(key, cls) {
            var on = u.role === 'admin' ? true : !!u[key];
            if (isAdmin && u.role !== 'admin') {
              return '<button class="cap-btn ' + (on ? cls + '-on' : 'off') + '" data-cap="' + key + '" data-id="' + u.id
                + '" data-on="' + (on ? 1 : 0) + '" title="点击' + (on ? '取消' : '下发') + '">'
                + (on ? '已授权' : '未授权') + '</button>';
            }
            return '<span class="cap-btn ' + (on ? cls + '-on' : 'off') + '" style="cursor:default"'
              + (u.role === 'admin' ? ' title="管理员默认拥有"' : '') + '>' + (on ? '已授权' : '未授权') + '</span>';
          }
          var ops = '';
          if (isAdmin) {
            if (!isSelf) {
              ops += '<button class="mini" data-act="grant" data-id="' + u.id + '" data-name="' + esc(u.username) + '">授权</button>'
                + '<button class="mini" data-act="role" data-id="' + u.id + '" data-role="' + u.role + '">'
                + (u.role === 'admin' ? '降为可编辑' : '升为管理员') + '</button>'
                + '<button class="mini danger" data-act="toggle" data-id="' + u.id + '" data-status="' + u.status + '">'
                + (u.status === 1 ? '停用' : '启用') + '</button>';
            } else {
              ops += '<span class="ua-self">当前账号</span>';
            }
            ops += '<button class="mini" data-act="pw" data-id="' + u.id + '" data-name="' + esc(u.username) + '">重置密码</button>';
            if (!isSelf) ops += '<button class="mini danger" data-act="del" data-id="' + u.id + '" data-name="' + esc(u.username) + '">删除</button>';
          } else if (isSelf) {
            ops += '<span class="ua-self">当前账号</span>';
          } else if (manage) {
            ops += '<button class="mini" data-act="grant" data-id="' + u.id + '" data-name="' + esc(u.username) + '">授权</button>'
              + '<button class="mini danger" data-act="toggle" data-id="' + u.id + '" data-status="' + u.status + '">'
              + (u.status === 1 ? '停用' : '启用') + '</button>'
              + '<button class="mini" data-act="pw" data-id="' + u.id + '" data-name="' + esc(u.username) + '">重置密码</button>';
          }
          h += '<tr><td>' + u.id + '</td><td>' + esc(u.username) + '</td><td>' + esc(u.real_name || '') + '</td>'
            + '<td><span class="tag ' + (u.role === 'admin' ? 'tag-blue' : 'tag-purple') + '">' + (u.role === 'admin' ? '管理员' : '可编辑') + '</span></td>'
            + '<td>' + capCell('can_fields', 'cf') + '</td><td>' + capCell('can_users', 'cu') + '</td>'
            + '<td><span class="tag ' + (u.status === 1 ? 'tag-green' : 'tag-red') + '">' + (u.status === 1 ? '启用' : '停用') + '</span></td>'
            + '<td>' + ops + '</td></tr>';
        });
        $('ua-rows').innerHTML = h || '<tr><td colspan="8">暂无账号</td></tr>';
        var uaL = $('ua-rows').closest('.ua-left');
        if (uaL) uaL.scrollLeft = 0;
        $('ua-rows').querySelectorAll('[data-cap]').forEach(function (b) {
          b.onclick = function () {
            var key = b.getAttribute('data-cap'), id = +b.getAttribute('data-id');
            var body = {}; body[key] = +b.getAttribute('data-on') !== 1;
            api('/__auth/users/' + id, 'PATCH', body).then(function () {
              toast('已更新能力'); loadUsers();
            }).catch(function (e) { toast(e.message); });
          };
        });
        $('ua-rows').querySelectorAll('[data-act]').forEach(function (btn) {
          btn.onclick = function () {
            var act = btn.getAttribute('data-act'), id = +btn.getAttribute('data-id');
            if (act === 'grant') {
              loadGrants(id, btn.getAttribute('data-name'));
            }
            else if (act === 'role') {
              api('/__auth/users/' + id, 'PATCH', { role: btn.getAttribute('data-role') === 'admin' ? 'staff' : 'admin' })
                .then(loadUsers).catch(function (e) { toast(e.message); });
            } else if (act === 'toggle') {
              api('/__auth/users/' + id, 'PATCH', { status: +btn.getAttribute('data-status') === 1 ? 0 : 1 })
                .then(loadUsers).catch(function (e) { toast(e.message); });
            } else if (act === 'pw') {
              var un = btn.getAttribute('data-name');
              modal('重置密码 · ' + un,
                '<div class="frm"><label>新密码（至少 6 位）</label><input id="ua-pw" type="password" autocomplete="new-password"></div>',
                '<button class="btn" id="ua-pw-cancel">取消</button><button class="btn btn-primary" id="ua-pw-ok">保存</button>',
                function () {
                  $('ua-pw-cancel').onclick = closeModal;
                  var ipt = $('ua-pw'); ipt.focus();
                  function ok() {
                    var v = ipt.value || '';
                    if (v.length < 6) { toast('密码至少 6 位'); return; }
                    api('/__auth/users/' + id, 'PATCH', { password: v })
                      .then(function () { closeModal(); toast('密码已重置'); })
                      .catch(function (e) { toast(e.message); });
                  }
                  $('ua-pw-ok').onclick = ok;
                  ipt.onkeydown = function (e) {
                    if (e.key === 'Enter') ok();
                    if (e.key === 'Escape') closeModal();
                  };
                });
            } else if (act === 'del') {
              var un2 = btn.getAttribute('data-name');
              appConfirm('删除账号',
                '删除账号「<b>' + esc(un2) + '</b>」？<br><span style="color:var(--text-3)">该账号的项目/表授权会一并移除，登录立即失效。</span>',
                '删除', true).then(function (ok) {
                if (!ok) return;
                api('/__auth/users/' + id, 'DELETE').then(function () {
                  toast('已删除账号：' + un2); loadUsers();
                }).catch(function (e) { toast('删除失败：' + e.message); });
              });
            }
          };
        });
      }).catch(function (e) { toast('读取用户失败：' + e.message); });
    }
    $('ua-addbtn').onclick = function () {
      var u = $('ua-u').value.trim(), n = $('ua-n').value.trim(), p = $('ua-p').value;
      var r = isAdmin && $('ua-r') ? $('ua-r').value : 'staff';
      if (!u || p.length < 6) { toast('账号必填，密码至少 6 位'); return; }
      api('/__auth/users', 'POST', { username: u, real_name: n, password: p, role: r }).then(function () {
        toast('已添加：' + u + (isAdmin ? '' : '（点「授权」分配权限）'));
        $('ua-u').value = $('ua-n').value = $('ua-p').value = '';
        loadUsers();
      }).catch(function (e) { toast('添加失败：' + e.message); });
    };
    loadUsers();
  });
}

function openPassword() {
  modal('修改密码',
    '<div class="frm"><label>原密码</label><input id="pw-old" type="password"></div>'
    + '<div class="frm"><label>新密码（至少 6 位）</label><input id="pw-new" type="password"></div>',
    '<button class="btn" id="pw-cancel">取消</button><button class="btn btn-primary" id="pw-ok">保存</button>',
    function () {
      $('pw-cancel').onclick = closeModal;
      $('pw-ok').onclick = async function () {
        try {
          var d = await api('/__auth/me/password', 'POST',
            { old_password: $('pw-old').value, new_password: $('pw-new').value });
          closeModal(); ME = null; renderUserBox();
          toast(d.detail || '密码已修改，请重新登录');
        } catch (e) { toast('修改失败：' + e.message); }
      };
    });
}
async function doLogout() {
  try { await api('/__auth/logout', 'POST', {}); } catch (e) {}
  ME = null; renderUserBox();
  var npb = $('btn-new-project');
  if (npb) npb.style.display = 'none';
  ANA.rows = {}; ANA.loads = {}; ANA.dashboards = []; ANA.currentId = null;
  goHomeProjects();
  clearLocalCaches();   // 放在回首页之后：回首页时会再记一次“位置”
  try {
    META = await api('/api/meta');
    writeMetaCache(META);
    invalidateRows();
    rebuildGroups(); rebuildProjects();
    goHomeProjects();
  } catch (e2) {}
  toast('已退出登录');
}

/* ============ 右键菜单：行操作 / 表头列宽 ============ */
var CTX = null;
function closeCtxMenu() { if (CTX) { CTX.remove(); CTX = null; } }
function showCtxMenu(x, y, items) {
  closeCtxMenu();
  var m = document.createElement('div'); m.className = 'ctx-menu';
  m.innerHTML = items.map(function (it, i) {
    if (it.sep) return '<div class="ctx-sep"></div>';
    return '<button type="button" class="ctx-item' + (it.danger ? ' danger' : '') + '" data-i="' + i + '">'
      + '<span>' + esc(it.label) + '</span></button>';
  }).join('');
  document.body.appendChild(m);
  var r = m.getBoundingClientRect();
  m.style.left = Math.max(8, Math.min(x, window.innerWidth - r.width - 8)) + 'px';
  m.style.top = Math.max(8, Math.min(y, window.innerHeight - r.height - 8)) + 'px';
  m.querySelectorAll('[data-i]').forEach(function (b) {
    b.onclick = function (e) {
      e.stopPropagation();
      var it = items[+b.getAttribute('data-i')];
      closeCtxMenu();
      if (it && it.fn) it.fn();
    };
  });
  CTX = m;
}
async function insertRowAt(rid, where) {
  try {
    var body = { data: {} };
    body[where === 'before' ? 'before_id' : 'after_id'] = rid;
    var res = await api('/api/tables/' + state.tid + '/rows', 'POST', body);
    if (state.size > 0 && res.pos) state.page = Math.max(1, Math.ceil(res.pos / state.size));
    await refreshMeta(state.tid);
    await loadRows();
    var tr = await waitRowEl(res.id, 1200);
    if (tr) focusRowEl(res.id);
    toast(where === 'before' ? '已在上方插入一行' : '已在下方插入一行');
  } catch (e) { toast('插入失败：' + e.message); }
}
async function copyRow(rid) {
  if (!canEditRows()) return;
  var row = null;
  if (GRID) for (var i = 0; i < GRID.d.rows.length; i++) if (GRID.d.rows[i].id === rid) { row = GRID.d.rows[i]; break; }
  if (!row) { toast('这一行不在当前页'); return; }
  var data = {}, imgs = 0;
  curCols().forEach(function (c) {
    if (c.type === 'formula') return;
    if (c.type === 'image') { if (row.data[c.key]) imgs++; return; }   // 图片不复制（避免两行指向同一张图）
    if (row.data[c.key] !== undefined && row.data[c.key] !== null) data[c.key] = row.data[c.key];
  });
  try {
    var res = await api('/api/tables/' + state.tid + '/rows', 'POST', { data: data, after_id: rid });
    if (state.size > 0 && res.pos) state.page = Math.max(1, Math.ceil(res.pos / state.size));
    await refreshMeta(state.tid);
    await loadRows();
    var tr = await waitRowEl(res.id, 1200);
    if (tr) focusRowEl(res.id);
    toast('已复制这一行' + (imgs ? '（图片列不跟着复制）' : ''));
  } catch (e) { toast('复制失败：' + e.message); }
}
function autoFitCol(k) {
  var t = curTable(), c = colByKey(k); if (!t || !c) return;
  var cv = document.createElement('canvas').getContext('2d');
  var cell = document.querySelector('#tbody td[data-f]') || document.body;
  var cs = getComputedStyle(cell);
  cv.font = (cs.fontWeight || '400') + ' ' + (cs.fontSize || '13px') + ' ' + (cs.fontFamily || 'sans-serif');
  function tw(s) { return cv.measureText(String(s)).width; }
  var max = tw(c.label) + 12;
  if (GRID) GRID.d.rows.forEach(function (r) {
    var v = r.data[k], s;
    if (v === true) s = '是'; else if (v === false) s = '否';
    else if (Array.isArray(v)) s = v.join('、');
    else s = (v === null || v === undefined) ? '' : String(v);
    if (c.type === 'formula' && s) s = 'ƒ ' + s;
    var x = tw(s); if (x > max) max = x;
  });
  var width = Math.min(340, Math.max(96, Math.round(max + 24 + 38)));   // 单元格左右留白 + 表头筛选按钮
  var st = colWStore(); st[t.id + '|' + k] = width; localStorage.setItem('kb_colw', JSON.stringify(st));
  var col = $('colgroup').querySelector('col[data-c="' + CSS.escape(k) + '"]');
  if (col) col.style.width = width + 'px';
  toast('已按当前页内容把「' + c.label + '」调成 ' + width + 'px');
}
function resetColWidth(k) {
  var t = curTable(); if (!t) return;
  var st = colWStore(); delete st[t.id + '|' + k]; localStorage.setItem('kb_colw', JSON.stringify(st));
  var col = $('colgroup').querySelector('col[data-c="' + CSS.escape(k) + '"]');
  if (col) col.style.width = defW(colByKey(k)) + 'px';
}
function initCtxMenus() {
  var tb = $('tbody');
  if (tb) tb.addEventListener('contextmenu', function (e) {
    var tr = e.target.closest('tr[data-rid]'); if (!tr) return;
    e.preventDefault();
    var rid = +tr.getAttribute('data-rid'), canEdit = canEditRows();
    var items = [];
    if (canEdit) {
      items.push({ label: '上移一行', fn: function () { moveRowTo(rid, { offset: -1 }, '已上移一行'); } });
      items.push({ label: '下移一行', fn: function () { moveRowTo(rid, { offset: 1 }, '已下移一行'); } });
      items.push({ sep: true });
      items.push({ label: '在上方插入一行', fn: function () { insertRowAt(rid, 'before'); } });
      items.push({ label: '在下方插入一行', fn: function () { insertRowAt(rid, 'after'); } });
      items.push({ label: '复制这一行', fn: function () { copyRow(rid); } });
      items.push({ sep: true });
    }
    var cell = e.target.closest('td[data-f]');
    if (cell) items.push({ label: '添加 / 查看批注', fn: function () { openCellComments(rid, cell.getAttribute('data-f'), cell); } });
    items.push({ label: '打开行详情', fn: function () { toggleRowPanel(rid); } });
    if (canEdit) {
      items.push({ sep: true });
      items.push({ label: '删除这一行', danger: true, fn: function () { confirmDeleteRow(rid); } });
    }
    showCtxMenu(e.clientX, e.clientY, items);
  });
  var th = $('thead');
  if (th) th.addEventListener('contextmenu', function (e) {
    var cell = e.target.closest('th[data-c]'); if (!cell) return;
    e.preventDefault();
    var k = cell.getAttribute('data-c'), c = colByKey(k); if (!c) return;
    showCtxMenu(e.clientX, e.clientY, [
      { label: '按内容适应列宽（当前页）', fn: function () { autoFitCol(k); } },
      { label: '恢复默认列宽', fn: function () { resetColWidth(k); } },
      { sep: true },
      { label: '按此列升序', fn: function () { state.sort = k; state.dir = 1; state.page = 1; renderHead(); loadRows(); updateViewChrome(); } },
      { label: '按此列降序', fn: function () { state.sort = k; state.dir = -1; state.page = 1; renderHead(); loadRows(); updateViewChrome(); } }
    ]);
  });
  document.addEventListener('click', function (e) { if (CTX && !e.target.closest('.ctx-menu')) closeCtxMenu(); }, true);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeCtxMenu(); });
  window.addEventListener('scroll', closeCtxMenu, true);
  window.addEventListener('resize', closeCtxMenu);
  if (tb) tb.addEventListener('scroll', closeCtxMenu, { passive: true });
}

/* ============ 拖动行调整顺序 ============
   行顺序是表数据的一部分（和表格软件一样，所有人可见）；正在按列排序时不能拖（显示顺序不由表决定）。
   拖的时候只在目标位置画一条蓝线，松手才发请求。 */
async function moveRowTo(rid, body, msg) {
  if (state.sort) { toast('正在按列排序，先点表头取消排序再调整顺序'); return; }
  try {
    var res = await api('/api/tables/' + state.tid + '/rows/' + rid + '/move', 'POST', body);
    if (state.size > 0 && res.pos) state.page = Math.max(1, Math.ceil(res.pos / state.size));
    await loadRows();
    var tr = await waitRowEl(rid, 1200);
    if (tr) focusRowEl(rid);
    toast(res.moved === false ? '位置没有变化' : (msg || ('已移动到第 ' + res.pos + ' 行（所有人可见）')));
  } catch (e) { toast('移动失败：' + e.message); }
}
function dropLineEl() {
  var el = document.getElementById('row-drop-line');
  if (!el) { el = document.createElement('div'); el.className = 'row-drop-line'; el.id = 'row-drop-line'; document.body.appendChild(el); }
  return el;
}
function hideDropLine() { var el = document.getElementById('row-drop-line'); if (el) el.style.display = 'none'; }
function rowDropTarget(y) {
  var trs = [].slice.call(document.querySelectorAll('#tbody tr[data-rid]'));
  if (!trs.length) return null;
  for (var i = 0; i < trs.length; i++) {
    var r = trs[i].getBoundingClientRect();
    if (y < r.top + r.height / 2) return { where: 'before', tr: trs[i], y: r.top };
  }
  var last = trs[trs.length - 1], lr = last.getBoundingClientRect();
  return { where: 'after', tr: last, y: lr.bottom };
}
function initRowDrag() {
  var tb = $('tbody');
  if (!tb) return;
  var st = null;
  tb.addEventListener('pointerdown', function (e) {
    if (e.button !== 0 || e.pointerType !== 'mouse') return;   // 手机不拖：用右键菜单的上移/下移
    var td = e.target.closest('td.idx-col'); if (!td) return;
    var tr = td.closest('tr[data-rid]'); if (!tr) return;
    if (!canEditRows()) return;
    if (state.sort) { toast('正在按列排序，先点表头取消排序再拖动行'); return; }
    st = { rid: +tr.getAttribute('data-rid'), tr: tr, y: e.clientY, active: false, target: null };
  });
  tb.addEventListener('pointermove', function (e) {
    if (!st) return;
    if (!st.active) {
      if (Math.abs(e.clientY - st.y) < 6) return;
      st.active = true;
      st.tr.classList.add('row-drag');
      hideRowInsert(); closeCtxMenu();
    }
    e.preventDefault();
    var t = rowDropTarget(e.clientY);
    var line = dropLineEl();
    if (!t) { hideDropLine(); st.target = null; return; }
    st.target = t;
    var wrap = document.querySelector('.tbl-wrap').getBoundingClientRect();
    line.style.left = Math.round(wrap.left) + 'px';
    line.style.width = Math.round(wrap.width) + 'px';
    line.style.top = Math.round(t.y - 1) + 'px';
    line.style.display = 'block';
  });
  function finish(ok) {
    if (!st) return;
    var cur = st; st = null;
    hideDropLine();
    cur.tr.classList.remove('row-drag');
    if (!ok || !cur.active || !cur.target) return;
    var targetRid = +cur.target.tr.getAttribute('data-rid');
    if (cur.target.where === 'before') {
      if (targetRid !== cur.rid) moveRowTo(cur.rid, { before_id: targetRid });
    } else {
      moveRowTo(cur.rid, { after_id: targetRid });
    }
  }
  document.addEventListener('pointerup', function () { if (st) finish(true); });
  document.addEventListener('pointercancel', function () { if (st) finish(false); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && st) { finish(false); toast('已取消拖动'); }
  });
}

initTbodyEvents();
initRowInsert();
initGridEvents();
initCtxMenus();
initRowDrag();
$('btn-sb-toggle').onclick = toggleSidebar;
$('btn-sb-toggle-ana').onclick = toggleSidebar;
applySidebarState();

