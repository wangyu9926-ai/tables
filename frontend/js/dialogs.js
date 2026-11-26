/* dialogs.js — index.html 内联脚本拆出的第 3/12 段（原第 2256-2492 行）。
   加载顺序见 index.html 底部；全部是全局函数，跨文件可直接调用。 */
/* ============ 通用弹窗 ============ */
function modal(title, bodyHtml, footHtml, onMount) {
  var root = $('modal-root');
  root.innerHTML = '<div class="mask" id="mask"><div class="box">'
    + '<div class="box-head"><h3>'+esc(title)+'</h3><button class="box-x" id="box-x">✕</button></div>'
    + '<div class="box-body">'+bodyHtml+'</div>'
    + '<div class="box-foot">'+footHtml+'</div></div></div>';
  enhanceSelects(root);
  $('box-x').onclick = closeModal;
  $('mask').onclick = function(e){ if(e.target===$('mask')) closeModal(); };
  if (onMount) onMount(root);
  enhanceSelects(root);
}
function closeModal(){ closeCsMenu(); closeDatePicker(); $('modal-root').innerHTML=''; }

/* ============ 写入密钥（HTTP POST 免登录插数据） ============ */
function openApiKeys() {
  var t = curTable();
  if (!t) return;
  var tid = t.id;
  var shortName = t.name.split('·').pop();
  var cols = (t.columns || []).filter(function (c) { return c.type !== 'formula'; });
  var sampleKey = cols.length ? cols[0].label : '列名';
  var sampleVal = '示例';
  if (cols.length) {
    var c0 = cols[0], o0 = c0.options && c0.options[0];
    if (o0 && typeof o0 === 'object') o0 = o0.label || o0.value || '';
    if (c0.type === 'number') sampleVal = 100;
    else if (c0.type === 'date') sampleVal = '2026-11-23';
    else if (c0.type === 'checkbox') sampleVal = true;
    else if (c0.type === 'select' && o0) sampleVal = o0;
    else if (c0.type === 'multiselect') sampleVal = o0 ? [o0] : [];
  }
  var sampleData = Object.create(null);
  sampleData[sampleKey] = sampleVal;
  var bodyText = '{"data":' + JSON.stringify(sampleData) + '}';

  modal('写入密钥 · ' + shortName,
    '<div class="ak-list" id="ak-list"><div class="ak-empty">加载中…</div></div>'
    + '<div class="ak-new"><input id="ak-note" maxlength="120" placeholder="备注（如：影刀到货机器人）">'
    + '<button class="btn btn-primary" id="ak-gen">生成密钥</button></div>'
    + '<div id="ak-secret"></div>',
    '<button class="btn" id="ak-close">关闭</button>',
    function () {
      $('ak-close').onclick = closeModal;
      $('ak-gen').onclick = gen;
      $('ak-note').onkeydown = function (e) { if (e.key === 'Enter') { e.preventDefault(); gen(); } };
      load();

      async function load() {
        try {
          var d = await api('/api/tables/' + tid + '/apikeys');
          renderList(d.rows || []);
        } catch (e) {
          $('ak-list').innerHTML = '<div class="ak-empty">加载失败：' + esc(e.message) + '</div>';
        }
      }

      function renderList(rows) {
        var box = $('ak-list');
        if (!rows.length) { box.innerHTML = '<div class="ak-empty">还没有密钥</div>'; return; }
        box.innerHTML = rows.map(function (k) {
          return '<div class="ak-item">'
            + '<code class="ak-prefix">' + esc(k.key_prefix) + '…</code>'
            + '<span class="ak-note">' + esc(k.note || '未备注') + '</span>'
            + '<span class="ak-meta">' + esc(k.created_by || '') + ' · ' + esc(k.created_at || '') + '</span>'
            + (k.revoked ? '<span class="ak-revoked">已作废</span>'
                         : '<button class="mini danger" data-kid="' + k.id + '">作废</button>')
            + '</div>';
        }).join('');
        box.querySelectorAll('[data-kid]').forEach(function (b) {
          b.onclick = async function () {
            var ok = await appConfirm('作废密钥', '作废后，使用该密钥的脚本将立即无法写入这张表。', '作废', true);
            if (!ok) return;
            try {
              await api('/api/tables/' + tid + '/apikeys/' + b.getAttribute('data-kid'), 'DELETE');
              toast('已作废'); load();
            } catch (e) { toast('作废失败：' + e.message); }
          };
        });
      }

      async function gen() {
        var btn = $('ak-gen');
        btn.disabled = true;
        try {
          var res = await api('/api/tables/' + tid + '/apikeys', 'POST', { note: $('ak-note').value.trim() });
          $('ak-note').value = '';
          showSecret(res.key);
          load();
        } catch (e) { toast('生成失败：' + e.message); }
        finally { btn.disabled = false; }
      }

      function showSecret(key) {
        var url = location.origin + '/api/in/' + tid;
        var curl = 'curl -X POST ' + url + ' \\\n'
          + '  -H "X-KB-Token: ' + key + '" \\\n'
          + '  -H "Content-Type: application/json" \\\n'
          + "  -d '" + bodyText + "'";
        $('ak-secret').innerHTML = '<div class="ak-secret">'
          + '<div class="ak-secret-head">新密钥只显示这一次，请立即复制</div>'
          + '<div class="ak-keyrow"><code>' + esc(key) + '</code>'
          + '<button class="btn" id="ak-copy-key">复制密钥</button></div>'
          + '<pre>' + esc(curl) + '</pre>'
          + '<div style="margin-top:8px"><button class="btn" id="ak-copy-curl">复制命令</button></div>'
          + '</div>';
        $('ak-copy-key').onclick = function () { copyText(key); };
        $('ak-copy-curl').onclick = function () { copyText(curl); };
      }
    });
}

function copyText(txt) {
  function fallback() {
    var ta = document.createElement('textarea');
    ta.value = txt; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); toast('已复制'); }
    catch (e) { toast('复制失败，请手动选择'); }
    document.body.removeChild(ta);
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(txt).then(function () { toast('已复制'); }, fallback);
  } else fallback();
}

/* 站内确认框（替代原生 confirm）：可叠加在已有弹窗上，返回 Promise<boolean> */
function appConfirm(title, messageHtml, okText, danger) {
  return new Promise(function (resolve) {
    var root = $('confirm-root');
    root.innerHTML = '<div class="mask" id="cf-mask"><div class="box cf-box">'
      + '<div class="box-head"><h3>' + esc(title) + '</h3></div>'
      + '<div class="box-body"><div class="cf-msg">' + messageHtml + '</div></div>'
      + '<div class="box-foot"><button class="btn" id="cf-cancel">取消</button>'
      + '<button class="btn ' + (danger ? 'btn-danger' : 'btn-primary') + '" id="cf-ok">'
      + esc(okText || '确定') + '</button></div></div></div>';
    function done(v) {
      document.removeEventListener('keydown', onKey);
      root.innerHTML = '';
      resolve(v);
    }
    function onKey(e) { if (e.key === 'Escape') done(false); }
    document.addEventListener('keydown', onKey);
    $('cf-cancel').onclick = function () { done(false); };
    $('cf-ok').onclick = function () { done(true); };
    $('cf-mask').onclick = function (e) { if (e.target === $('cf-mask')) done(false); };
  });
}

/* ============ 新建项目 ============ */
function openNewProject() {
  modal('新建项目',
    '<div class="frm"><label>项目名</label><input id="np-name" placeholder="例如：牧马人商务合作看板"></div>',
    '<button class="btn" id="np-cancel">取消</button><button class="btn btn-primary" id="np-ok">创建</button>',
    function () {
      $('np-cancel').onclick = closeModal;
      $('np-name').focus();
      async function ok() {
        var name = $('np-name').value.trim();
        if (!name) { toast('请填项目名'); return; }
        try {
          await api('/api/projects', 'POST', { name: name, icon: '📁' });
          closeModal(); await refreshMeta(); goHomeProjects();
          toast('已创建项目：' + name);
        } catch (e) { toast('创建失败：' + e.message); }
      }
      $('np-ok').onclick = ok;
      $('np-name').onkeydown = function (e) { if (e.key === 'Enter') ok(); };
    });
}

/* ============ 新建表格（任意表，可带列定义） ============ */
/* 在项目内新建一张「表」（组），首 Sheet 名默认 Sheet1 */
function openNewTable(defaultGroup) {
  var prj = curProject ? curProject.name : (PROJECTS[0] ? PROJECTS[0].name : '默认项目');
  var gopts = (curProject ? curProject.groups : []).map(function(g){return '<option value="'+esc(g.name)+'">'+esc(g.name)+'</option>';}).join('');
  modal('新建表 · ' + prj,
    '<div class="frm"><label>表名</label><input id="nt-group" list="grp-list" placeholder="例如：达人报价表">'
    + '<datalist id="grp-list">'+gopts+'</datalist></div>'
    + '<div class="frm"><label>首个 Sheet 名</label><input id="nt-name" value="Sheet1"></div>'
    + '<div class="frm"><label>列</label><div id="nt-cols"></div>'
    + '<button class="btn" id="nt-addcol" type="button" style="margin-top:2px">＋ 添加列</button></div>',
    '<button class="btn" id="nt-cancel">取消</button><button class="btn btn-primary" id="nt-ok">创建</button>',
    function(){
      var box = $('nt-cols');
      function addRow(label, type, expr) {
        var row = document.createElement('div');
        row.className = 'nt-col';
        row.innerHTML = '<input class="nt-col-name" placeholder="列名" value="' + esc(label || '') + '">'
          + '<select class="nt-col-type">' + Object.keys(TYPE_LABEL).map(function (k) {
              return '<option value="' + k + '">' + TYPE_LABEL[k] + '</option>';
            }).join('') + '</select>'
          + '<button class="nt-col-del" type="button" title="删除列">✕</button>'
          + '<input class="nt-col-expr" placeholder="= ROUND({金额}/100,2)">';
        var sel = row.querySelector('.nt-col-type');
        sel.value = type || 'text';
        var ex = row.querySelector('.nt-col-expr');
        if (expr) ex.value = expr;
        function syncExpr() { ex.style.display = sel.value === 'formula' ? '' : 'none'; }
        sel.onchange = syncExpr; syncExpr();
        row.querySelector('.nt-col-del').onclick = function () {
          if (box.querySelectorAll('.nt-col').length > 1) row.remove();
          else { row.querySelector('.nt-col-name').value = ''; ex.value = ''; }
        };
        box.appendChild(row);
      }
      addRow('名称', 'text');
      addRow('备注', 'text');
      $('nt-addcol').onclick = function () { addRow('', 'text'); };

      $('nt-cancel').onclick = closeModal;
      $('nt-ok').onclick = async function(){
        var gname = $('nt-group').value.trim();
        var sname = ($('nt-name').value.trim() || 'Sheet1');
        if(!gname){ toast('请填表名'); return; }
        var cols = [];
        box.querySelectorAll('.nt-col').forEach(function (row) {
          var label = row.querySelector('.nt-col-name').value.trim();
          if (!label) return;
          var type = row.querySelector('.nt-col-type').value;
          var expr = type === 'formula' ? (row.querySelector('.nt-col-expr').value.trim().replace(/^=\s*/, '') || null) : null;
          cols.push({ label: label, type: type, expr: expr });
        });
        try {
          var full = gname + '·' + sname;
          var d = await api('/api/tables','POST',{name:full, project: prj, group_name: gname,
                                                 icon:'📄', columns: cols.length?cols:null});
          closeModal();
          await refreshMeta();
          if (d && d.id) openSheet(d.id); else openProject(prj);
          toast('已创建表：'+gname);
        } catch(e) { toast('创建失败：'+e.message); }
      };
    });
}

