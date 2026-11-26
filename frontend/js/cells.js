/* cells.js — index.html 内联脚本拆出的第 7/12 段（原第 3923-4527 行）。
   加载顺序见 index.html 底部；全部是全局函数，跨文件可直接调用。 */
/* ---------- 单击选中：右侧出现「展开编辑」按钮 ---------- */
/* 同一行里左右相邻的可编辑单元格（公式列不可编辑，跳过） */
function cellNeighbor(td, dir){
  var tr=td.closest('tr'); if(!tr) return null;
  var tds=[].slice.call(tr.querySelectorAll('td[data-f]')).filter(function(x){return !x.classList.contains('formula');});
  var i=tds.indexOf(td); if(i<0) return null;
  return tds[i+dir]||null;
}
function clearCellSel(){
  document.querySelectorAll('#tbody td.cell-sel').forEach(function(td){
    td.classList.remove('cell-sel');
    var b=td.querySelector('.cell-expand'); if(b) b.remove();
    var cb=td.querySelector('.cell-cmt'); if(cb) cb.remove();
  });
  document.querySelectorAll('#tbody td.in-sel, #tbody td.fill-target').forEach(function(td){
    td.classList.remove('in-sel'); td.classList.remove('fill-target');
  });
  SEL = null; SEL_DRAG = false; FILL = null;
  hideFillHandle();
}
function selectCell(td, extend){
  var rc = cellRC(td);
  if (!rc) return;
  if (extend && SEL) { SEL.rid1 = rc.rid; SEL.key1 = rc.key; }
  else { SEL = { rid0: rc.rid, key0: rc.key, rid1: rc.rid, key1: rc.key }; }
  document.querySelectorAll('#tbody td.cell-sel').forEach(function(x){
    x.classList.remove('cell-sel');
    var b=x.querySelector('.cell-expand'); if(b) b.remove();
    var cb=x.querySelector('.cell-cmt'); if(cb) cb.remove();
  });
  td.classList.add('cell-sel');
  var btn=document.createElement('button');
  btn.className='cell-expand';
  btn.title='放大编辑';
  btn.innerHTML='<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 4H4v5M15 20h5v-5M4 4l6 6M20 20l-6-6"/></svg>';
  btn.onclick=function(e){ e.stopPropagation(); openCellModal(td); };
  td.appendChild(btn);
  // 批注入口：没批注的格子也能从这里写
  var cmt=document.createElement('button');
  cmt.className='cell-cmt'; cmt.type='button'; cmt.title='批注';
  cmt.innerHTML='<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-8 8H7l-4 3v-6.5A8 8 0 0 1 11 4h2a8 8 0 0 1 8 8z"/></svg>';
  cmt.onclick=function(e){ e.stopPropagation(); openCellComments(+td.closest('tr').getAttribute('data-rid'), td.getAttribute('data-f'), td); };
  td.appendChild(cmt);
  paintSel();
}

/* ==================== 区域选择（拖选 / Shift / 方向键）、批量清空、复制、填充柄 ====================
   选区只记“行 id + 列 key”，跟渲染窗口无关：滚动、翻页、别人改数据都不会错位。 */
var SEL = null, SEL_DRAG = false, FILL = null, FILL_DRAG = false;
function cellRC(td){
  if (!td || !GRID) return null;
  var tr = td.closest('tr[data-rid]'); if (!tr) return null;
  var rid = +tr.getAttribute('data-rid'), ri = gridRowIndex(rid);
  var key = td.getAttribute('data-f'), ci = -1;
  GRID.cols.forEach(function (c, i) { if (c.key === key) ci = i; });
  if (ri < 0 || ci < 0) return null;
  return { ri: ri, ci: ci, rid: rid, key: key };
}
function selRange(){
  if (!SEL || !GRID) return null;
  var rm = {}, cm = {};
  GRID.d.rows.forEach(function (r, i) { rm[r.id] = i; });
  GRID.cols.forEach(function (c, i) { cm[c.key] = i; });
  var r0 = rm[SEL.rid0], r1 = rm[SEL.rid1], c0 = cm[SEL.key0], c1 = cm[SEL.key1];
  if (r0 === undefined || r1 === undefined || c0 === undefined || c1 === undefined) return null;
  return { r0: Math.min(r0, r1), r1: Math.max(r0, r1), c0: Math.min(c0, c1), c1: Math.max(c0, c1),
           ar: r0, ac: c0 };
}
function paintSel(){
  var tb = $('tbody'); if (!tb) return;
  tb.querySelectorAll('td.in-sel').forEach(function (t) { t.classList.remove('in-sel'); });
  var s = selRange();
  if (!s) { hideFillHandle(); return; }
  var rm = {}, cm = {};
  GRID.d.rows.forEach(function (r, i) { rm[r.id] = i; });
  GRID.cols.forEach(function (c, i) { cm[c.key] = i; });
  Array.prototype.forEach.call(tb.querySelectorAll('tr[data-rid]'), function (tr) {
    var ri = rm[+tr.getAttribute('data-rid')];
    if (ri === undefined || ri < s.r0 || ri > s.r1) return;
    Array.prototype.forEach.call(tr.querySelectorAll('td[data-f]'), function (td) {
      var ci = cm[td.getAttribute('data-f')];
      if (ci !== undefined && ci >= s.c0 && ci <= s.c1) td.classList.add('in-sel');
    });
  });
  positionFillHandle(s);
}
function hideFillHandle(){ var h = $('fill-handle'); if (h) h.style.display = 'none'; }
function positionFillHandle(s){
  var tb = $('tbody'); if (!tb) return;
  var endKey = GRID.cols[s.c1].key, endCell = null;
  Array.prototype.forEach.call(tb.querySelectorAll('td[data-f]'), function (td) {
    if (td.getAttribute('data-f') !== endKey) return;
    var ri = gridRowIndex(+td.closest('tr').getAttribute('data-rid'));
    if (ri === s.r1) endCell = td;
  });
  var h = $('fill-handle');
  if (!h) {
    h = document.createElement('div'); h.id = 'fill-handle';
    h.title = '向下拖动填充（重复选区的第一行）';
    h.addEventListener('mousedown', beginFillDrag);
    document.body.appendChild(h);
  }
  if (!endCell || !GRID.canEdit) { h.style.display = 'none'; return; }   // 只读账号不显示填充柄
  var r = endCell.getBoundingClientRect();
  h.style.display = 'block';
  h.style.left = Math.round(r.right - 5) + 'px';
  h.style.top = Math.round(r.bottom - 5) + 'px';
}
function moveSel(dr, dc, extend){
  var s = selRange(); if (!s) return;
  var ar = extend ? s.r1 : s.r0, ac = extend ? s.c1 : s.c0;
  var nr = Math.max(0, Math.min(GRID.d.rows.length - 1, ar + dr));
  var nc = Math.max(0, Math.min(GRID.cols.length - 1, ac + dc));
  var row = GRID.d.rows[nr]; if (!row) return;
  var key = GRID.cols[nc].key;
  if (extend) { SEL.rid1 = row.id; SEL.key1 = key; }
  else { SEL = { rid0: row.id, key0: key, rid1: row.id, key1: key }; }
  revealRow(row.id);
  paintSel();
  var tb = $('tbody'), hit = null;
  Array.prototype.forEach.call(tb ? tb.querySelectorAll('td[data-f]') : [], function (td) {
    if (td.getAttribute('data-f') === key && +td.closest('tr').getAttribute('data-rid') === row.id) hit = td;
  });
  if (hit) { document.querySelectorAll('#tbody td.cell-sel').forEach(function (x) { x.classList.remove('cell-sel'); }); hit.classList.add('cell-sel'); }
}
/* 批量写：一次请求改多行 / 补建缺少的行。某几行冲突或值不合法只影响那几行。 */
function bulkRows(items){
  /* 接口一次最多 500 行：多的自动分批发（粘贴几千行也不会被拦） */
  var out = { updated: 0, created: [], conflicts: [], errors: [] };
  var CH = 500;
  var chain = Promise.resolve(out);
  for (var i = 0; i < items.length; i += CH) {
    (function (off, part) {
      chain = chain.then(function (acc) {
        return api('/api/tables/' + state.tid + '/rows/bulk', 'POST', { rows: part }).then(function (r) {
          acc.updated += (r.updated || 0);
          acc.created = acc.created.concat(r.created || []);
          (r.conflicts || []).forEach(function (x) { acc.conflicts.push(x); });
          (r.errors || []).forEach(function (x) { x.index = (x.index || 0) + off; acc.errors.push(x); });
          return acc;
        });
      });
    })(i, items.slice(i, i + CH));
  }
  return chain;
}
async function fillRange(s, valueFn){
  if (!canEditRows()) { toast('只读权限，不能修改'); return 0; }
  var items = [], cells = [], n = 0;
  for (var ri = s.r0; ri <= s.r1; ri++) {
    var row = GRID.d.rows[ri]; if (!row) continue;
    var data = {};
    for (var ci = s.c0; ci <= s.c1; ci++) {
      var c = GRID.cols[ci];
      if (!c || c.type === 'formula' || c.type === 'image') continue;
      var v = valueFn(ri, ci);
      if (v === undefined) continue;
      var old = (row.data || {})[c.key];
      if (sameVal(old, v)) continue;
      data[c.key] = v;
      cells.push({ rid: row.id, key: c.key, old: old === undefined ? null : old, new: v });
      n++;
    }
    if (Object.keys(data).length) items.push({ id: row.id, ver: row.ver, data: data });
  }
  if (!items.length) { toast('没有需要改动的格子'); return 0; }
  showBusy('正在写入 ' + n + ' 格…');
  try {
    var r = await bulkRows(items);
    if (cells.length) undoPush({ tid: state.tid, label: '批量修改 ' + cells.length + ' 格', cells: cells });
    await loadRows();
    var bad = (r.conflicts || []).length + (r.errors || []).length;
    toast('已写入 ' + n + ' 格' + (bad ? '，' + bad + ' 行没成功（可能别人刚改过）' : ''));
    return n;
  } catch (e) { toast('写入失败：' + e.message); return -1; }
  finally { hideBusy(); }
}
function clearSelectionCells(){
  var s = selRange(); if (!s) return;
  fillRange(s, function () { return null; });
}
function fillSelectionWithValue(s, v){
  return fillRange(s, function () { return v; });
}
function copySelection(){
  var s = selRange(); if (!s) return false;
  var lines = [];
  for (var ri = s.r0; ri <= s.r1; ri++) {
    var row = GRID.d.rows[ri], cells = [];
    for (var ci = s.c0; ci <= s.c1; ci++) {
      var v = (row.data || {})[GRID.cols[ci].key];
      if (Array.isArray(v)) v = v.join(',');
      else if (v === null || v === undefined) v = '';
      cells.push(String(v).replace(/[\t\r\n]+/g, ' '));
    }
    lines.push(cells.join('\t'));
  }
  var text = lines.join('\n'), n = (s.r1 - s.r0 + 1) * (s.c1 - s.c0 + 1);
  var done = function () { toast('已复制 ' + n + ' 格'); };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(function () { legacyCopyText(text); done(); });
  } else { legacyCopyText(text); done(); }
  return true;
}function legacyCopyText(text){
  var ta = document.createElement('textarea');
  ta.value = text; ta.style.position = 'fixed'; ta.style.left = '-9999px'; ta.style.top = '0';
  document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); } catch (e) {}
  ta.remove();
}
function beginFillDrag(e){
  e.preventDefault(); e.stopPropagation();
  var s = selRange(); if (!s || !GRID) return;
  FILL = { s: s, to: s.r1 }; FILL_DRAG = true;
  document.body.classList.add('filling');
  document.addEventListener('mousemove', onFillMove);
  document.addEventListener('mouseup', endFillDrag, { once: true });
}
function onFillMove(e){
  if (!FILL_DRAG || !FILL || !GRID) return;
  var wrap = document.querySelector('.tbl-wrap'); if (!wrap) return;
  var r = wrap.getBoundingClientRect();
  var y = e.clientY - r.top + wrap.scrollTop;
  var ri = Math.max(0, Math.min(GRID.d.rows.length - 1, Math.floor(y / GRID.rowH)));
  FILL.to = Math.max(FILL.s.r1, ri);
  paintFillPreview();
}
function paintFillPreview(){
  var tb = $('tbody'); if (!tb || !FILL) return;
  tb.querySelectorAll('td.fill-target').forEach(function (t) { t.classList.remove('fill-target'); });
  var s = FILL.s, rm = {};
  GRID.d.rows.forEach(function (r, i) { rm[r.id] = i; });
  var cm = {}; GRID.cols.forEach(function (c, i) { cm[c.key] = i; });
  Array.prototype.forEach.call(tb.querySelectorAll('tr[data-rid]'), function (tr) {
    var ri = rm[+tr.getAttribute('data-rid')];
    if (ri === undefined || ri <= s.r1 || ri > FILL.to) return;
    Array.prototype.forEach.call(tr.querySelectorAll('td[data-f]'), function (td) {
      var ci = cm[td.getAttribute('data-f')];
      if (ci !== undefined && ci >= s.c0 && ci <= s.c1) td.classList.add('fill-target');
    });
  });
}
function endFillDrag(){
  document.removeEventListener('mousemove', onFillMove);
  FILL_DRAG = false; document.body.classList.remove('filling');
  var s = FILL && FILL.s, to = FILL && FILL.to;
  FILL = null;
  document.querySelectorAll('#tbody td.fill-target').forEach(function (t) { t.classList.remove('fill-target'); });
  if (!s || to <= s.r1) return;
  var h = s.r1 - s.r0 + 1;
  fillRange({ r0: s.r1 + 1, r1: Math.min(to, GRID.d.rows.length - 1), c0: s.c0, c1: s.c1 },
    function (ri, ci) {
      var src = GRID.d.rows[s.r0 + ((ri - s.r0) % h)];
      var v = (src.data || {})[GRID.cols[ci].key];
      return v === undefined ? null : v;
    });
}

/* ---------- 图片编辑：上传 / 粘贴 / 拖入 / 外链 ---------- */
function openImageEditor(tr, c, td) {
  var rid = +tr.getAttribute('data-rid');
  var cur = (ROWDATA[rid] || {})[c.key] || '';
  var url = cur, uploading = false;
  var body = '<div class="img-preview" id="im-prev"></div>'
    + '<div class="img-actions"><label class="img-upload">上传图片'
    + '<input type="file" id="im-file" accept="image/png,image/jpeg,image/gif,image/webp,image/bmp" style="display:none"></label>'
    + '<button class="btn" id="im-clear" type="button">移除</button></div>'
    + '<div class="img-msg" id="im-msg">支持 Ctrl+V 粘贴、拖入图片或上传本地文件，最大 8MB</div>'
    + '<div class="frm" style="margin-top:10px"><label>图片链接</label>'
    + '<input id="im-url" placeholder="https://... 或上传本地图片"></div>';
  modal('编辑图片', body,
    '<button class="btn" id="im-cancel">取消</button><button class="btn btn-primary" id="im-save">保存</button>',
    function (root) {
      root.querySelector('.box').classList.add('img-box');
      var prev = $('im-prev'), msg = $('im-msg');
      function preview(u) {
        prev.innerHTML = u
          ? '<img src="' + esc(u) + '" alt="">'
          : '<div class="img-preview-empty">还没有图片</div>';
      }
      function setUrl(u, sync) {
        url = u || '';
        preview(url);
        if (sync) $('im-url').value = url;
      }
      setUrl(cur, true);
      $('im-cancel').onclick = closeModal;
      $('im-clear').onclick = function () { setUrl('', true); msg.textContent = ''; };
      $('im-file').onchange = function () {
        var f = this.files && this.files[0];
        this.value = '';
        upload(f);
      };
      $('im-url').oninput = function () {
        url = this.value.trim();
        preview(url);
      };
      async function upload(f) {
        if (!f) return;
        if (f.size > 8 * 1024 * 1024) { msg.textContent = '图片不能超过 8MB'; return; }
        uploading = true;
        $('im-save').disabled = true;
        msg.textContent = '上传中…';
        try {
          var d = await uploadImageFile(state.tid, f);
          setUrl(d.url, true);
          msg.textContent = '已上传';
        } catch (e) {
          msg.textContent = '上传失败：' + e.message;
        }
        uploading = false;
        $('im-save').disabled = false;
      }
      root.addEventListener('paste', function (e) {
        var items = e.clipboardData && e.clipboardData.items;
        if (!items) return;
        for (var i = 0; i < items.length; i++) {
          if (items[i].type && items[i].type.indexOf('image/') === 0) {
            e.preventDefault();
            upload(items[i].getAsFile());
            return;
          }
        }
      });
      prev.addEventListener('dragover', function (e) { e.preventDefault(); });
      prev.addEventListener('drop', function (e) {
        e.preventDefault();
        var f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
        upload(f);
      });
      $('im-save').onclick = function () {
        if (uploading) { toast('图片还在上传，请稍候'); return; }
        var u = $('im-url').value.trim();
        closeModal(); clearCellSel();
        patchCell(tr, c.key, u);
      };
    });
}

/* ---------- 放大编辑弹窗（按列类型给对应控件） ---------- */
function openCellModal(td){
  var tr=td.closest('tr'), rid=+tr.getAttribute('data-rid');
  var c=colByKey(td.getAttribute('data-f'));
  if(!c) return;
  var et=curTable();
  if(et && et.my_role!=='editor'){ openRowPanel(rid); return; }
  if(c.type==='image'){ openImageEditor(tr,c,td); return; }
  var data=ROWDATA[rid]||{};
  var v=data[c.key];
  var body='';
  if(c.type==='formula'){
    body='<div class="cell-big-view">'+(v===null||v===undefined||v===''?'—':esc(String(v)))+'</div>'
        +'<div class="hint">公式列只读</div>';
  } else if(c.type==='select'){
    var opts=(c.options||[]);
    body='<div class="frm"><label>'+esc(c.label)+'</label><select id="cm-val"><option value="">（空）</option>'
      +opts.map(function(o){return '<option value="'+esc(o)+'"'+(String(v)===String(o)?' selected':'')+'>'+esc(o)+'</option>';}).join('')
      +'</select></div>';
  } else if(c.type==='multiselect'){
    var arr=Array.isArray(v)?v.map(String):(v?[String(v)]:[]);
    body='<div class="frm"><label>'+esc(c.label)+'</label><div class="cm-checks">'
      +(c.options||[]).map(function(o){return '<label class="cm-check"><input type="checkbox" value="'+esc(o)+'"'+(arr.indexOf(String(o))!==-1?' checked':'')+'> '+esc(o)+'</label>';}).join('')
      +'</div></div>';
  } else if(c.type==='checkbox'){
    body='<div class="frm"><label>'+esc(c.label)+'</label><label class="cm-check"><input type="checkbox" id="cm-val"'+(v?' checked':'')+'> 是</label></div>';
  } else if(c.type==='number'){
    body='<div class="frm"><label>'+esc(c.label)+'</label><input id="cm-val" type="text" inputmode="decimal" autocomplete="off" value="'+(v===null||v===undefined?'':esc(String(v)))+'"></div>';
  } else if(c.type==='date'){
    body='<div class="frm"><label>'+esc(c.label)+'</label><div class="date-field">'
      + '<input id="cm-val" readonly placeholder="选择日期" value="'+esc(String(v||'').slice(0,10))+'">'
      + '<button type="button" class="date-btn" id="cm-date-btn">'+DATE_ICON_SVG+'</button></div></div>';
  } else {
    body='<div class="frm"><label>'+esc(c.label)+'</label><textarea id="cm-val" class="cell-big" rows="10" placeholder="在这里输入…">'+esc(v===null||v===undefined?'':String(v))+'</textarea></div>';
  }
  var foot = c.type==='formula'
    ? '<button class="btn" id="cm-close">关闭</button>'
    : '<button class="btn" id="cm-cancel">取消</button><button class="btn btn-primary" id="cm-ok">保存</button>';
  modal('编辑 · '+c.label, body, foot, function(){
    $('cm-close') && ($('cm-close').onclick=function(){ closeModal(); });
    $('cm-cancel') && ($('cm-cancel').onclick=function(){ closeModal(); });
    var ok=$('cm-ok'); if(!ok) return;
    if(c.type==='date'){
      var cmv=$('cm-val'), cmb=$('cm-date-btn');
      if(cmv) cmv.onclick=function(){ openDatePicker(cmv); };
      if(cmb) cmb.onclick=function(e){ e.preventDefault(); openDatePicker(cmv); };
    }
    ok.onclick=function(){
      var val;
      if(c.type==='multiselect'){
        val=Array.from(document.querySelectorAll('.cm-checks input:checked')).map(function(x){return x.value;});
      } else if(c.type==='checkbox'){
        val=$('cm-val').checked;
      } else {
        val=$('cm-val').value;
      }
      closeModal(); clearCellSel();
      patchCell(tr, c.key, val);
    };
  });
}
/* ---------- 单选：双击弹出可选项浮层 ---------- */
var cellPop = null;
function closeCellPop(){ if(cellPop){ cellPop.remove(); cellPop=null; } }
function openSelectPop(td, c){
  closeCellPop();
  var tr=td.closest('tr'), key=c.key;
  var cur=td.textContent.trim(); if(cur==='—')cur='';
  var pop=document.createElement('div');
  pop.className='cell-pop';
  var r=td.getBoundingClientRect();
  pop.style.left=Math.max(8,Math.min(r.left, window.innerWidth-250))+'px';
  pop.style.top=Math.min(r.bottom+4, window.innerHeight-300)+'px';
  pop.style.minWidth=Math.max(r.width,170)+'px';
  pop.innerHTML=(c.options||[]).map(function(o){
    return '<div class="cp-opt'+(o===cur?' on':'')+'" data-v="'+esc(o)+'">'
      + '<span class="tag '+tagFor(o,c)+'">'+esc(o)+'</span>'
      + (o===cur?'<span class="cp-check">✓</span>':'')+'</div>';
  }).join('') + '<div class="cp-opt cp-clear" data-v="">（空）</div>';
  document.body.appendChild(pop); cellPop=pop;
  pop.onclick=function(e){
    e.stopPropagation();
    var it=e.target.closest('.cp-opt'); if(!it) return;
    var v=it.getAttribute('data-v');
    closeCellPop(); clearCellSel();
    patchCell(tr, key, v);
  };
  setTimeout(function(){
    document.addEventListener('click', function h(ev){
      if(!ev.target.closest('.cell-pop')){ closeCellPop(); document.removeEventListener('click',h); }
    });
  },0);
}

/* ---------- 多选：双击弹出勾选浮层 ---------- */
function openMultiPop(td, c){
  closeCellPop();
  var tr=td.closest('tr'), key=c.key, rid=+tr.getAttribute('data-rid');
  var raw=(ROWDATA[rid]||{})[key];
  var arr=Array.isArray(raw)?raw.map(String):(raw?[String(raw)]:[]);
  var pop=document.createElement('div');
  pop.className='cell-pop cell-pop-multi';
  var r=td.getBoundingClientRect();
  pop.style.left=Math.max(8,Math.min(r.left, window.innerWidth-260))+'px';
  pop.style.top=Math.min(r.bottom+4, window.innerHeight-340)+'px';
  pop.style.minWidth=Math.max(r.width,190)+'px';
  pop.innerHTML=(c.options||[]).map(function(o){
    return '<label class="cp-opt cp-multi" data-v="'+esc(o)+'">'
      + '<input type="checkbox" value="'+esc(o)+'"'+(arr.indexOf(String(o))!==-1?' checked':'')+'>'
      + '<span class="tag '+tagFor(o,c)+'">'+esc(o)+'</span></label>';
  }).join('') + '<div class="cp-foot"><button class="btn" data-act="cancel">取消</button>'
    + '<button class="btn btn-primary" data-act="ok">确定</button></div>';
  document.body.appendChild(pop); cellPop=pop;
  pop.onclick=function(e){ e.stopPropagation(); };
  pop.querySelector('[data-act="cancel"]').onclick=function(){ closeCellPop(); };
  pop.querySelector('[data-act="ok"]').onclick=function(){
    var vals=Array.from(pop.querySelectorAll('input:checked')).map(function(x){return x.value;});
    closeCellPop(); clearCellSel();
    patchCell(tr, key, vals);
  };
  setTimeout(function(){
    document.addEventListener('click', function h(ev){
      if(!ev.target.closest('.cell-pop')){ closeCellPop(); document.removeEventListener('click',h); }
    });
  },0);
}

function openEdit(td, seed){
  if(td.querySelector('input')||document.querySelector('.cell-pop')) return;   // 已在编辑
  var c=colByKey(td.getAttribute('data-f'));
  if(!c||c.type==='formula') return;
  var et=curTable();
  if(et && et.my_role!=='editor'){ toast('只读权限，不能修改数据'); return; }
  if(c.type==='image'){ openImageEditor(td.closest('tr'),c,td); return; }
  if(c.type==='checkbox'){
    var tr0=td.closest('tr'), cur0=td.textContent.indexOf('是')!==-1;
    patchCell(tr0, c.key, !cur0); return;
  }
  if(c.type==='select'){ openSelectPop(td,c); return; }
  if(c.type==='multiselect'){ openMultiPop(td,c); return; }
  var raw=td.textContent.trim(); if(raw==='—')raw='';
  var orig=td.innerHTML;
  if(c.type==='date'){
    var rawD=raw.slice(0,10);
    td.innerHTML='<div class="mt-editor mt-date"><input class="mt-editor-ipt" readonly placeholder="选择日期" value="'+esc(rawD)+'"><span class="mt-date-ic">'+DATE_ICON_SVG+'</span></div>';
    var iptD=td.querySelector('input');
    var commitD=function(v){
      iptD.onblur=null;
      if(String(v||'')===String(rawD)){ td.innerHTML=orig; editing=false; return; }
      patchCell(td.closest('tr'), c.key, v);
    };
    iptD.focus();
    iptD.onclick=function(){ openDatePicker(iptD,{onPick:commitD}); };
    iptD.onkeydown=function(e){
      if(e.key==='Enter'){
        e.preventDefault();
        if(DP && DP.sel) return;                 // 交给全局 Enter：选中的日期
        e.stopPropagation(); closeDatePicker(); commitD(iptD.value);
      } else if(e.key==='Escape'){
        e.stopPropagation(); closeDatePicker(); iptD.onblur=null; td.innerHTML=orig; editing=false;
      }
    };
    iptD.onblur=function(){ if(DP && DP.input===iptD) return; commitD(iptD.value); };
    editing=true;
    openDatePicker(iptD,{onPick:commitD});
    return;
  }
  // 数字列用文本框：库里允许「2万 / 1.8万 / 4.7万」这类带单位的值，
  // type=number 会把它们判为非法并清空，失焦保存就把原值覆盖没了
  var inputType = c.type==='number' ? ' type="text" inputmode="decimal" autocomplete="off"' : '';
  td.innerHTML='<div class="mt-editor"><input class="mt-editor-ipt"'+inputType+'></div>';
  var ipt=td.querySelector('input');
  ipt.value = (typeof seed==='string' && seed.length) ? seed : raw;   // 直接打字编辑：用新输入的字替换原值
  ipt.focus();
  var len = ipt.value.length;
  try { ipt.setSelectionRange(len, len); } catch(e) {}   // 光标放末尾，不全选，避免误覆盖
  var commit = function(){
    var v = ipt.value;
    ipt.onblur = null;
    if (v === raw) { td.innerHTML = orig; editing = false; return; }
    patchCell(td.closest('tr'), c.key, v);
  };
  ipt.onkeydown=function(e){
    if(e.key==='Enter'){ e.preventDefault(); e.stopPropagation(); commit(); }
    else if(e.key==='Escape'){ e.stopPropagation(); ipt.onblur=null; td.innerHTML=orig; editing=false; }
    else if(e.key==='Tab'){
      // 表格习惯：Tab 提交并跳到右边一格，Shift+Tab 往左
      e.preventDefault(); e.stopPropagation();
      var nb=cellNeighbor(td, e.shiftKey?-1:1);
      commit();
      if(nb && nb.isConnected) selectCell(nb);
    }
  };
  ipt.onblur=commit;
  editing=true;
}
async function saveRowValue(rid,key,val){
  var tr=document.querySelector('#tbody tr[data-rid="'+rid+'"]');
  var g=null, gi=-1;
  if(GRID){ for(var i=0;i<GRID.d.rows.length;i++){ if(GRID.d.rows[i].id===rid){ g=GRID.d.rows[i]; gi=i; break; } } }
  if(!tr && !g) throw new Error('该行不在当前页');
  var td=tr ? tr.querySelector('td[data-f="'+CSS.escape(key)+'"]') : null;
  var ver=g ? (+g.ver||1) : (td ? (+td.getAttribute('data-ver')||1) : 1);
  var cc=colByKey(key), oldHtml=td ? td.innerHTML : null;
  var oldVal=(ROWDATA[rid]||{})[key];
  // 先就地显示新值，网络往返期间输入框已经消失，不让人等
  if(td && cc){ td.innerHTML=cellHtml(val,cc); td.setAttribute('data-ts',String(Date.now())); }
  var res;
  try { res=await api('/api/tables/'+state.tid+'/rows/'+rid,'PATCH',{data:{[key]:val},ver:ver}); }
  catch(e){ if(td && oldHtml!==null) td.innerHTML=oldHtml; throw e; }
  if (res && res.synced_back) toast('已同步回子表 ' + res.synced_back + ' 行');
  var row=res.row||{}; var nver=res.ver||ver+1;
  ROWDATA[rid]=row;
  if(g){ g.data=row; g.ver=nver; }
  // 记一笔撤销：把这一格的原值、新值存起来（Ctrl+Z 再把原值保存一次）
  if(!UNDO_SUPPRESS && !sameVal(oldVal,val)) undoPush({tid:state.tid,label:(cc&&cc.label)||key,cells:[{rid:rid,key:key,old:oldVal===undefined?null:oldVal,new:val}]});
  // 有筛选/排序/搜索时行可能移位，重新拉一次；否则只就地更新这一行（含公式列），不闪整表
  if(state.kw||state.sort||Object.keys(state.filters).length){ await loadRows(); return res; }
  invalidateRows(state.tid);              // 只有当前表的行变了
  if(tr && tr.isConnected){
    tr.querySelectorAll('td[data-f]').forEach(function(cell){
      var c2=colByKey(cell.getAttribute('data-f')); if(!c2)return;
      cell.innerHTML=cellHtml(row[c2.key],c2);
      cell.setAttribute('data-ver',nver);
    });
  }
  return res;
}
async function patchCell(tr,key,val){
  editing=false;
  try { await saveRowValue(+tr.getAttribute('data-rid'),key,val); }
  catch(e){ toast('保存失败：'+e.message); await loadRows(); }
}

/* ============ 撤销 / 重做（Ctrl+Z / Ctrl+Y） ============
   栈里存“一组单元格改动”：单格编辑是一条，批量粘贴是一条。撤销就是把旧值再保存一遍，
   仍然走乐观锁（期间别人改过这一行会失败并提示），并且只对当前这张表生效。 */
var UNDO_STACK = [], REDO_STACK = [], UNDO_MAX = 50;
var UNDO_SUPPRESS = false, UNDO_BUSY = false;
function sameVal(a, b) {
  return JSON.stringify(a === undefined ? null : a) === JSON.stringify(b === undefined ? null : b);
}
function undoPush(entry) {
  if (UNDO_SUPPRESS) return;
  UNDO_STACK.push(entry);
  if (UNDO_STACK.length > UNDO_MAX) UNDO_STACK.shift();
  REDO_STACK = [];
}
function rowVer(rid) {
  if (!GRID) return null;
  for (var i = 0; i < GRID.d.rows.length; i++) if (GRID.d.rows[i].id === rid) return +GRID.d.rows[i].ver || 1;
  return null;
}
async function applyUndoEntry(entry, redo) {
  if (UNDO_BUSY) return false;
  UNDO_BUSY = true; UNDO_SUPPRESS = true;
  var ok = 0, fail = 0, lastErr = '';
  try {
    var byRow = {};
    entry.cells.forEach(function (c) { (byRow[c.rid] = byRow[c.rid] || []).push(c); });
    var rids = Object.keys(byRow);
    for (var i = 0; i < rids.length; i++) {
      var rid = +rids[i], list = byRow[rids[i]];
      var ver = rowVer(rid);
      if (ver === null) { fail += list.length; continue; }     // 不在当前页，拿不到版本号
      var data = {};
      list.forEach(function (c) { data[c.key] = redo ? c.new : c.old; });
      try { await api('/api/tables/' + state.tid + '/rows/' + rid, 'PATCH', { data: data, ver: ver }); ok += list.length; }
      catch (e) { fail += list.length; lastErr = e.message; }
    }
  } finally { UNDO_SUPPRESS = false; UNDO_BUSY = false; }
  try { await loadRows(); } catch (e) {}
  if (ok) {
    toast((redo ? '已重做' : '已撤销') + '：' + entry.label + (fail ? ('（' + fail + ' 格没成功）') : ''));
  } else {
    toast((redo ? '重做失败' : '撤销失败') + '：' + (lastErr || '这几行不在当前页，没改动'));
  }
  return ok > 0;
}
async function doUndo() {
  var e = UNDO_STACK[UNDO_STACK.length - 1];
  if (!e) { toast('没有可撤销的修改'); return; }
  if (e.tid !== state.tid) { toast('最近一次修改在别的表，切回那张表再撤销'); return; }
  if (await applyUndoEntry(e, false)) { UNDO_STACK.pop(); REDO_STACK.push(e); }
}
async function doRedo() {
  var e = REDO_STACK[REDO_STACK.length - 1];
  if (!e) { toast('没有可重做的修改'); return; }
  if (e.tid !== state.tid) { toast('最近一次修改在别的表，切回那张表再重做'); return; }
  if (await applyUndoEntry(e, true)) { REDO_STACK.pop(); UNDO_STACK.push(e); }
}
document.addEventListener('keydown', function (e) {
  if (!(e.ctrlKey || e.metaKey)) return;
  var el = document.activeElement, tag = el && el.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || (el && el.isContentEditable)) return;  // 输入框里的撤销交给浏览器
  if (!$('table-view') || !$('table-view').classList.contains('on')) return;
  if ($('mask') || openFilter) return;
  var k = (e.key || '').toLowerCase();
  if (k === 'z' && !e.shiftKey) { e.preventDefault(); doUndo(); }
  else if ((k === 'z' && e.shiftKey) || k === 'y') { e.preventDefault(); doRedo(); }
});

