/* sync-fields.js — index.html 内联脚本拆出的第 4/12 段（原第 2493-2962 行）。
   加载顺序见 index.html 底部；全部是全局函数，跨文件可直接调用。 */
/* ============ 汇总同步（子表 → 总表 单向复制） ============ */
var SYNC_TTYPES = ['text', 'number', 'date', 'select', 'multiselect', 'checkbox'];

function openSyncMgr() {
  var t = curTable();
  if (!t) return;
  if (!(ME && ME.role === 'admin')) { toast('需要管理员权限'); return; }
  var timer = null;
  var short = t.name.split('·')[1] || t.name;
  var body = '<div class="sync-sec" style="margin-top:0">已配置的汇总规则</div>'
    + '<div id="sync-rules"><div class="sync-empty">加载中…</div></div>'
    + '<div class="sync-sec">新增 / 修改汇总规则</div>'
    + '<div class="sync-form">'
    + '<div class="sync-form-head"><span style="font-size:13px;color:var(--text-2);flex:none">子表</span><select id="sync-src"></select></div>'
    + '<div class="sync-map" id="sync-map"><div class="sync-empty">先选择子表</div></div>'
    + '<div class="sync-foot"><button class="btn" id="sync-cancel">关闭</button>'
    + '<button class="btn btn-primary" id="sync-save">保存并同步</button></div></div>';
  modal('汇总同步 · ' + short, body, '', function (root) {
    var boxEl = root.querySelector('.box'); if (boxEl) boxEl.classList.add('sync-box');
    function sourceTables() {
      return META.tables.filter(function (x) {
        return x.id !== t.id && (x.project || '') === (t.project || '');
      });
    }
    function srcTable(id) {
      return META.tables.find(function (x) { return x.id === +id; });
    }
    function buildMap(srcTid, preset) {
      var src = srcTable(srcTid);
      var mapEl = $('sync-map');
      if (!src) { mapEl.innerHTML = '<div class="sync-empty">先选择子表</div>'; return; }
      var tcols = t.columns.filter(function (c) {
        return !c.hidden && SYNC_TTYPES.indexOf(c.type) !== -1;
      });
      var scols = src.columns.filter(function (c) {
        return c.type !== 'formula' && c.type !== 'image';
      });
      preset = preset || {};
      var h = '';
      tcols.forEach(function (c) {
        var opts = '<option value="">忽略</option><option value="$source_table">来源表名（自动）</option>'
          + scols.map(function (s) {
              return '<option value="' + esc(s.key) + '">' + esc(s.label) + '</option>';
            }).join('');
        h += '<div class="sync-map-row"><span class="tc" title="' + esc(c.label) + '">' + esc(c.label)
          + '<span class="tt">' + esc(TYPE_LABEL[c.type] || c.type) + '</span></span>'
          + '<select data-tk="' + esc(c.key) + '">' + opts + '</select></div>';
      });
      mapEl.innerHTML = h || '<div class="sync-empty">总表还没有可汇总的字段（文本/数字/日期/单选/多选/勾选）</div>';
      // 自动按同名字段匹配，已有规则按保存的映射回填
      mapEl.querySelectorAll('select[data-tk]').forEach(function (sel) {
        var tk = sel.getAttribute('data-tk');
        var want = preset[tk];
        if (!want) {
          var tc = tcols.find(function (x) { return x.key === tk; });
          var hit = scols.find(function (x) { return x.label === tc.label || x.key === tc.key; });
          if (hit) want = hit.key;
          else if (tc.type === 'text' && /来源|子表/.test(tc.label)) want = '$source_table';
        }
        sel.value = want || '';
      });
      enhanceSelects(mapEl);
    }
    function collect() {
      var out = Object.create(null);
      $('sync-map').querySelectorAll('select[data-tk]').forEach(function (s) {
        if (s.value) out[s.getAttribute('data-tk')] = s.value;
      });
      return out;
    }
    function selectSource(id, preset) {
      var sel = $('sync-src');
      var v = String(id || '');
      if (![...sel.options].some(function (o) { return o.value === v; })) return;
      sel.value = v;
      buildMap(v, preset);
    }
    function paintRules() {
      var el = $('sync-rules'); if (!el) return;
      if (!RULES.length) { el.innerHTML = '<div class="sync-empty">还没有汇总规则</div>'; return; }
      el.innerHTML = RULES.map(function (r) {
        var job = r.job || {};
        var status;
        if (job.status === 'queued') {
          status = '排队中…';
        } else if (job.status === 'running') {
          status = '同步中 ' + (job.done || 0) + ' / ' + (job.total || 0);
        } else if (job.status === 'error' || r.last_error) {
          status = '<span class="err">' + esc(job.error || r.last_error) + '</span>';
        } else if (r.last_sync_at) {
          status = '上次同步 ' + esc(r.last_sync_at) + ' · 已复制 ' + r.copied_count + ' 行';
        } else {
          status = '未同步';
        }
        return '<div class="sync-rule"><div class="sync-rule-top">'
          + '<span class="sync-rule-name" title="' + esc(r.source_name) + '">' + esc(r.source_name) + '</span>'
          + '<span class="sync-rule-acts">'
          + '<button class="mini" data-act="edit" data-id="' + r.id + '">映射</button>'
          + '<button class="mini" data-act="run" data-id="' + r.id + '">立即同步</button>'
          + '<button class="mini" data-act="toggle" data-id="' + r.id + '">' + (r.enabled ? '停用' : '启用') + '</button>'
          + '<button class="mini danger" data-act="del" data-id="' + r.id + '">删除</button>'
          + '</span></div>'
          + '<div class="sync-rule-meta">子表 ' + r.source_count + ' 行 · ' + status + '</div></div>';
      }).join('');
      el.querySelectorAll('[data-act]').forEach(function (b) {
        b.onclick = function () {
          var id = +b.getAttribute('data-id');
          var r = RULES.find(function (x) { return x.id === id; });
          if (!r) return;
          if (b.getAttribute('data-act') === 'edit') {
            selectSource(r.source_tid, r.mapping);
          } else if (b.getAttribute('data-act') === 'run') {
            api('/api/sync/' + id + '/run', 'POST', {}).then(function () { loadRules(); })
              .catch(function (e) { toast('同步失败：' + e.message); });
          } else if (b.getAttribute('data-act') === 'toggle') {
            api('/api/sync/' + id, 'PATCH', { enabled: !r.enabled }).then(function () { loadRules(); })
              .catch(function (e) { toast('保存失败：' + e.message); });
          } else if (b.getAttribute('data-act') === 'del') {
            appConfirm('删除汇总规则？', '会停止后续同步，并删除已经从「' + esc(r.source_name) + '」复制到总表的行。', '删除', true)
              .then(function (ok) {
                if (!ok) return;
                api('/api/sync/' + id, 'DELETE').then(function () { toast('已删除汇总规则'); loadRules(); })
                  .catch(function (e) { toast('删除失败：' + e.message); });
              });
          }
        };
      });
    }
    var RULES = [];
    function loadRules() {
      if (!$('sync-rules')) return;
      api('/api/sync?target_tid=' + t.id).then(function (d) {
        if (!$('sync-rules')) return;
        RULES = d.rules || [];
        paintRules();
        var running = RULES.some(function (r) { return r.job && (r.job.status === 'running' || r.job.status === 'queued'); });
        clearTimeout(timer);
        if (running) timer = setTimeout(loadRules, 900);
      }).catch(function (e) {
        if ($('sync-rules')) $('sync-rules').innerHTML = '<div class="sync-empty">读取失败：' + esc(e.message) + '</div>';
      });
    }
    var srcs = sourceTables();
    var srcSel = $('sync-src');
    if (!srcs.length) {
      srcSel.innerHTML = '<option value="">（同项目还没有其他表）</option>';
      srcSel.disabled = true;
      $('sync-save').disabled = true;
    } else {
      srcSel.innerHTML = srcs.map(function (s) {
        return '<option value="' + s.id + '">' + esc(s.name) + '</option>';
      }).join('');
      srcSel.onchange = function () { buildMap(+this.value); };
      selectSource(srcs[0].id);
    }
    $('sync-cancel').onclick = closeModal;
    $('sync-save').onclick = function () {
      var srcTid = +srcSel.value;
      if (!srcTid) { toast('先选择子表'); return; }
      var mapping = collect();
      if (!Object.keys(mapping).length) { toast('至少映射一个字段'); return; }
      var btn = this; btn.disabled = true;
      api('/api/sync', 'POST', { target_tid: t.id, source_tid: srcTid, mapping: mapping })
        .then(function () { toast('已开始汇总，总表会自动更新'); btn.disabled = false; loadRules(); })
        .catch(function (e) { toast('保存失败：' + e.message); btn.disabled = false; });
    };
    loadRules();
  });
}

/* ============ 字段管理（后端真列：显隐/调序/改类型/公式/删列/加列） ============ */
function openFieldMgr() {
  var t = curTable();
  if (!t) return;
  if (!(ME && (ME.role === 'admin' || (ME.can_fields && t.my_role === 'editor')))) {
    toast('没有字段管理权限'); return;
  }
  var cols = t.columns.slice().sort(function(a,b){return a.sort-b.sort;});
  var rows = cols.map(function(c,i){
    var fexpr=(c.type==='formula'&&c.expr)
      ? '<span class="fm-expr" title="公式：'+esc(c.expr)+'">'+esc(c.expr)+'</span>' : '';
    return '<div class="fm-row'+(c.hidden?' off':'')+'" data-id="'+c.id+'" data-i="'+i+'">'
      + '<input type="checkbox"'+(c.hidden?'':' checked')+' tabindex="-1">'
      + '<span class="fm-name" title="'+esc(c.label)+'">'+esc(c.label)+(c.type==='formula'?' <span style="color:var(--purple)">ƒ</span>':'')+'</span>'
      + fexpr
      + '<span class="fm-type">'+esc(TYPE_LABEL[c.type]||c.type)+'</span>'
      + '<span class="fm-ud">'
      + '<button data-ud="-1" title="上移" aria-label="上移"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5"/><path d="M6 11l6-6 6 6"/></svg></button>'
      + '<button data-ud="1" title="下移" aria-label="下移"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"/><path d="M6 13l6 6 6-6"/></svg></button>'
      + '</span>'
      + '<button class="fm-del" data-edit="'+c.id+'" title="编辑列">✎</button>'
      + '<button class="fm-del" data-del="'+c.id+'" title="删除列">✕</button></div>';
  }).join('');
  modal('字段管理 · '+(t.name.split('·')[1]||t.name),
    '<div class="fm-hint">隐藏列只影响显示，不是权限：有这张表访问权限的人仍能拿到隐藏列的值，敏感数据请用「用户管理 → 授权」隔离。</div>'
    + '<div id="fm-list">'+rows+'</div>'
    + '<div style="margin-top:10px"><button class="btn btn-primary" id="fm-add">＋ 新增字段</button></div>',
    '<span style="flex:1;font-size:var(--fs-xs);color:var(--text-3)" id="fm-sum"></span>'
    + '<button class="btn" id="fm-cancel">关闭</button><button class="btn btn-primary" id="fm-ok">保存</button>',
    function(root){
      var boxEl=document.querySelector('#modal-root .box'); if(boxEl) boxEl.classList.add('wide');
      function sum(){ $('fm-sum').textContent='显示 '+$('fm-list').querySelectorAll('.fm-row input:checked').length+' / '+$('fm-list').querySelectorAll('.fm-row').length+' 列'; }
      function syncUd(){
        var all=[...$('fm-list').querySelectorAll('.fm-row')];
        all.forEach(function(r,k){
          r.setAttribute('data-i',k);
          r.querySelector('[data-ud="-1"]').disabled = (k===0);
          r.querySelector('[data-ud="1"]').disabled = (k===all.length-1);
        });
      }
      function bindRow(row){
        row.querySelector('input').onchange=function(){ row.classList.toggle('off',!this.checked); sum(); };
        row.querySelectorAll('[data-ud]').forEach(function(b){ b.onclick=function(){
          var all=[...$('fm-list').querySelectorAll('.fm-row')], i=+row.getAttribute('data-i'), j=i+ +b.getAttribute('data-ud');
          if(j<0||j>=all.length) return;
          var body=$('fm-list');
          if(+b.getAttribute('data-ud')===-1) body.insertBefore(row,all[j]); else body.insertBefore(all[j],row);
          syncUd();
        };});
        row.querySelector('[data-edit]').onclick=function(){ openColumnEdit(+row.getAttribute('data-id')); };
        row.querySelector('[data-del]').onclick=async function(){
          var cid=+row.getAttribute('data-id');
          var c=t.columns.find(function(x){return x.id===cid;});
          // 警告：有公式列引用了这一列
          var refs=t.columns.filter(function(x){
            return x.type==='formula' && x.expr &&
              (x.expr.indexOf('{'+c.key+'}')!==-1 || x.expr.indexOf('{'+c.label+'}')!==-1);
          });
          var refHtml=refs.length
            ? '<br><div style="margin-top:8px;padding:8px 10px;background:var(--bg-orange);border-radius:7px;font-size:12px;color:var(--text-2)">'
              + '<b style="font-weight:400;color:var(--orange)">注意</b>：'+refs.length+' 个公式列引用了这一列（'
              + esc(refs.map(function(x){return x.label;}).join('、'))
              + '），删除后这些公式将取不到值。</div>'
            : '';
          var ok=await appConfirm('删除列',
            '删除列「<b>'+esc(c.label)+'</b>」？<br><span style="color:var(--text-3)">该列所有数据会一并清除，不可恢复。</span>'+refHtml,
            '删除', true);
          if(!ok) return;
          try {
            await api('/api/tables/'+t.id+'/columns/'+cid,'DELETE');
            // 本地直接摘掉这一列：不关字段管理弹窗、不整表重拉，避免删一个字段闪一下
            var hadFilter = !!state.filters[c.key];
            var hadSort = state.sort === c.key;
            t.columns = t.columns.filter(function(x){return x.id!==cid;});
            delete state.filters[c.key];
            if(state.sort===c.key){ state.sort=''; state.dir=1; }
            writeMetaCache(META);
            invalidateRows();
            renderHead();
            document.querySelectorAll('#tbody td[data-f="'+CSS.escape(c.key)+'"]').forEach(function(td){ td.remove(); });
            row.remove();
            syncUd(); sum();
            closeFilterPop();
            if (refs.length || hadFilter || hadSort) {
              state.page = 1;
              await loadRows();          // 只有公式引用/筛选/排序受影响时才重拉当前页
            }
            toast('已删除列：'+c.label);
          }
          catch(e){ toast('删除失败：'+e.message); }
        };
      }
      $('fm-list').querySelectorAll('.fm-row').forEach(bindRow); syncUd(); sum();
      $('fm-cancel').onclick=closeModal;
      $('fm-add').onclick=function(){ openColumnEdit(null); };
      $('fm-ok').onclick=async function(){
        var order=[], hidden=[];
        $('fm-list').querySelectorAll('.fm-row').forEach(function(row){
          var cid=+row.getAttribute('data-id'); order.push(cid);
          if(!row.querySelector('input').checked) hidden.push(cid);
        });
        try { await api('/api/tables/'+t.id+'/columns/sort','POST',{order:order,hidden:hidden});
              closeModal(); closeFilterPop(); state.page=1; await refreshMeta(t.id); toast('字段已保存'); }
        catch(e){ toast('保存失败：'+e.message); }
      };
    });
}

/* 新增/编辑列（含公式编辑器 + 实时试算） */
function openColumnEdit(cid) {
  var t = curTable();
  var c = cid ? t.columns.find(function(x){return x.id===cid;}) : null;
  var typeOpts = Object.keys(TYPE_LABEL).map(function(k){return '<option value="'+k+'"'+(c&&c.type===k?' selected':'')+'>'+TYPE_LABEL[k]+'</option>';}).join('');
  var refChips = t.columns.filter(function(x){return !x.id||x.id!==cid;}).slice(0,14).map(function(x){
    return '<span class="chip-ins" data-ins="'+esc(x.label)+'">{'+esc(x.label)+'}</span>'; }).join('');
  modal(c?('编辑字段 · '+c.label):'新增字段',
    '<div class="frm"><label>字段名称</label><input id="ce-label" value="'+esc(c?c.label:'')+'" placeholder="例如：千次互动成本"></div>'
    + '<div class="frm"><label>类型</label><select id="ce-type">'+typeOpts+'</select></div>'
    + '<div class="frm" id="ce-expr-wrap" style="display:none"><label>公式表达式</label>'
    + '<textarea id="ce-expr" placeholder="ROUND({合作金额}/IF({总互动}=0,1,{总互动})*1000,2)">'+esc(c&&c.expr||'')+'</textarea>'
    + '<div class="xt-box"><div class="xt-title">引用其他表（同项目）</div>'
    + '<div class="xt-row"><select id="xt-table" title="要引用的表"></select></div>'
    + '<div class="xt-row" style="margin-top:6px">'
    + '<select id="xt-match" title="对方表的匹配字段"></select>'
    + '<span class="xt-eq">=</span>'
    + '<select id="xt-local" title="本表的匹配字段"></select>'
    + '<select id="xt-take" title="对方表的取值字段"></select>'
    + '<select id="xt-fn"><option value="LOOKUP">第一项</option><option value="SUMIF">求和</option>'
    + '<option value="COUNTIF">计数</option><option value="AVGIF">平均</option>'
    + '<option value="MAXIF">最大</option><option value="MINIF">最小</option></select>'
    + '<button class="btn" id="xt-insert" type="button">插入</button></div>'
    + '<div class="hint" id="xt-hint"></div></div>'
    + '<div class="expr-preview" id="ce-preview">试算结果将显示在这里</div>'
    + '<div class="hint">用 {列名} 引用同行字段；支持 + - * /、比较、ROUND/ABS/MAX/MIN/SUM/IF/CONCAT/INT/CEIL/FLOOR/LEN/ISBLANK/REGEXEXTRACT。点击字段插入：<br>'+refChips+'</div></div>'
    + '<div class="frm" id="ce-opts-wrap" style="display:none"><label>选项</label>'
    + '<div id="ce-opt-rows"></div>'
    + '<button class="btn" id="ce-opt-add" type="button" style="margin-top:2px">＋ 添加选项</button></div>',
    '<button class="btn" id="ce-cancel">取消</button><button class="btn btn-primary" id="ce-ok">保存</button>',
    function(){
      var optBox = $('ce-opt-rows');
      function paintColor(sel){
        var i = sel.value === '' ? null : +sel.value;
        if (i === null) { sel.style.background='var(--gray-100)'; sel.style.color='var(--text-2)'; }
        else {
          var probe = document.createElement('span');
          probe.className = 'tag tag-c' + i;
          document.body.appendChild(probe);
          var cs = getComputedStyle(probe);
          sel.style.background = cs.backgroundColor;
          sel.style.color = cs.color;
          probe.remove();
        }
      }
      function addOptRow(label, color) {
        var row = document.createElement('div');
        row.className = 'opt-row';
        row.innerHTML = '<input class="opt-name" placeholder="选项名" value="' + esc(label || '') + '">'
          + '<select class="opt-color">' + (function () {
              var h = '<option value="">自动</option>';
              for (var i = 0; i < COLOR_NAMES.length; i++) h += '<option value="' + i + '">' + COLOR_NAMES[i] + '</option>';
              return h;
            })() + '</select>'
          + '<button class="opt-del" type="button" title="删除选项">✕</button>';
        var sel = row.querySelector('.opt-color');
        sel.value = (color === undefined || color === null) ? '' : String(color);
        paintColor(sel);
        sel.onchange = function () { paintColor(sel); };
        row.querySelector('.opt-del').onclick = function () { row.remove(); };
        optBox.appendChild(row);
      }
      var initOpts = (c && Array.isArray(c.options)) ? c.options : [];
      var initColors = (c && c.option_colors) || {};
      initOpts.forEach(function (o) { addOptRow(o, initColors[o]); });
      if (!initOpts.length) addOptRow('', null);
      $('ce-opt-add').onclick = function () { addOptRow('', null); };

      function sync(){
        var ty = $('ce-type').value;
        $('ce-expr-wrap').style.display = ty==='formula'?'':'none';
        $('ce-opts-wrap').style.display = (ty==='select'||ty==='multiselect')?'':'none';
        if(ty==='formula') preview();
      }
      $('ce-type').onchange=sync; sync();
      $('ce-expr').oninput=preview;
      $('ce-expr-wrap').querySelectorAll('.chip-ins').forEach(function(ch){ ch.onclick=function(){
        var ta=$('ce-expr'); var p=ta.selectionStart, v=ch.getAttribute('data-ins');
        ta.value=ta.value.slice(0,p)+v+ta.value.slice(p); ta.focus(); ta.selectionStart=p+v.length; preview();
      };});
      var timer=null;
      function preview(){
        clearTimeout(timer);
        timer=setTimeout(async function(){
          var expr=$('ce-expr').value.trim(); var box=$('ce-preview');
          if(!expr){ box.textContent='试算结果将显示在这里'; box.classList.remove('err'); return; }
          // 取当前表第一行做试算
          var row={}; t.columns.forEach(function(x){ row[x.label]=1; row[x.key]=1; });
          try {
            var r=await api('/api/formula/eval','POST',{expr:expr,row:row,tid:t.id});
            if(r.ok) box.textContent='= '+String(r.value);
            else { box.textContent=r.error; box.classList.add('err'); }
          } catch(e){ box.textContent=e.message; box.classList.add('err'); }
        },300);
      }
      // ---- 跨表引用构建器 ----
      var otherTabs = META.tables.filter(function(x){ return x.id!==t.id && (x.project||'')===(t.project||''); });
      var xsel=$('xt-table');
      xsel.innerHTML = otherTabs.map(function(x){ return '<option value="'+esc(x.name)+'">'+esc(x.name)+'</option>'; }).join('');
      var curLabels = t.columns.map(function(c){return c.label;});
      function xtTarget(){ return otherTabs.find(function(x){return x.name===xsel.value;}); }
      function refreshXt(){
        var tg=xtTarget();
        if(!tg){ $('xt-match').innerHTML=''; $('xt-take').innerHTML=''; $('xt-hint').textContent=''; return; }
        var tl=tg.columns.map(function(c){return c.label;});
        var common=curLabels.filter(function(l){return tl.indexOf(l)!==-1;});
        $('xt-match').innerHTML=tl.map(function(l){return '<option>'+esc(l)+'</option>';}).join('');
        $('xt-take').innerHTML=tl.map(function(l){return '<option>'+esc(l)+'</option>';}).join('');
        $('xt-local').innerHTML=curLabels.map(function(l){return '<option>'+esc(l)+'</option>';}).join('');
        // 有同名字段就默认选同名，否则按「昵称/名称」猜一对
        var guess = common[0] || curLabels.find(function(l){return /昵称|名称|达人/.test(l);});
        if(guess){
          var mi=curLabels.indexOf(guess), ti=tl.indexOf(guess);
          if(ti!==-1) $('xt-match').value=guess;
          if(mi!==-1) $('xt-local').value=guess;
          else { var li=$('xt-local').options[0]; if(li) $('xt-local').value=li.value; }
        }
        syncXtHint();
      }
      function syncXtHint(){
        $('xt-hint').textContent = '按 对方「'+$('xt-match').value+'」= 本表「{'+$('xt-local').value+'}」匹配';
      }
      xsel.onchange=refreshXt;
      $('xt-match').onchange=syncXtHint; $('xt-local').onchange=syncXtHint;
      refreshXt();
      $('xt-insert').onclick=function(){
        var tg=xtTarget(); if(!tg){ toast('先选一张表'); return; }
        var m=$('xt-match').value, lf=$('xt-local').value, tk=$('xt-take').value, fn=$('xt-fn').value;
        if(!m || !lf){ toast('先选匹配字段'); return; }
        var code = (fn==='COUNTIF')
          ? 'COUNTIF("'+tg.name+'","'+m+'",{'+lf+'})'
          : fn+'("'+tg.name+'","'+m+'",{'+lf+'},"'+tk+'")';
        var ta=$('ce-expr'), pos=ta.selectionStart||ta.value.length;
        ta.value = ta.value.slice(0,pos)+code+ta.value.slice(pos);
        ta.focus(); ta.selectionStart=ta.selectionEnd=pos+code.length;
        preview();
      };
      $('ce-cancel').onclick=function(){ openFieldMgr(); };
      $('ce-ok').onclick=async function(){
        var labels=[], colors=Object.create(null);
        optBox.querySelectorAll('.opt-row').forEach(function(row){
          var name=row.querySelector('.opt-name').value.trim(); if(!name) return;
          if(labels.indexOf(name)!==-1) return;
          labels.push(name);
          var cv=row.querySelector('.opt-color').value;
          if(cv!=='') colors[name]=+cv;
        });
        // 改列时带上原有的必填/隐藏：编辑器里没有这两个开关，不带会被接口当成“取消”
        var body={ label:$('ce-label').value.trim(), type:$('ce-type').value,
                   expr:$('ce-expr').value.trim()||null,
                   options:null,
                   option_colors:Object.keys(colors).length?colors:null,
                   required:c ? !!c.required : false, hidden:c ? !!c.hidden : false, sort:c?c.sort:null };
        var et=$('ce-type').value;
        if(et==='select'||et==='multiselect'){
          body.options=labels.length?labels:null;
        }else if(c && !Array.isArray(c.options)){
          body.options=c.options;           // 保留日期/数字的 display_format，别编辑一次就丢
        }
        if(et!=='select'&&et!=='multiselect') body.option_colors=null;
        if(!body.label){ toast('请填字段名'); return; }
        try {
          if(c) await api('/api/tables/'+t.id+'/columns/'+c.id,'PATCH',body);
          else await api('/api/tables/'+t.id+'/columns','POST',body);
          closeModal(); await refreshMeta(t.id); toast(c?'字段已更新':'字段已新增');
        } catch(e){ toast('保存失败：'+e.message); }
      };
    });
}

async function refreshMeta(selectTid) {
  META = await api('/api/meta');
  writeMetaCache(META);
  invalidateRows();
  rebuildGroups(); rebuildProjects();
  if (selectTid) state.tid = selectTid;
  if (!META.tables.some(function(t){return t.id===state.tid;})) {
    state.tid = (GROUPS[0] && GROUPS[0].sheets[0]) ? GROUPS[0].sheets[0].id : null;
  }
  if ($('table-view').classList.contains('on') && state.tid) {
    var t = META.tables.find(function (x) { return x.id === state.tid; });
    if ($('tv-name')) setTvName(t, t ? curGroupName(t) + ' · ' + (t.name.split('·')[1] || t.name) : '');
    // 项目/组可能变了，重建当前项目引用
    curProject = PROJECTS.find(function (p) {
      return p.groups.some(function (g) { return g.sheets.some(function (s) { return s.id === state.tid; }); });
    }) || curProject;
    renderAll();
  } else {
    renderAll();
  }
}

