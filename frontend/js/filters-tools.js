/* filters-tools.js — index.html 内联脚本拆出的第 9/12 段（原第 5068-5666 行）。
   加载顺序见 index.html 底部；全部是全局函数，跨文件可直接调用。 */
/* ============ 筛选浮层（值多选 / 日期日历） ============ */
function closeFilterPop(){ closeCsMenu(); if(openFilter){openFilter=null;var p=$('f-pop');if(p)p.remove();} }
function isoDate(off){ var d=new Date();d.setDate(d.getDate()+(off||0));return d.toISOString().slice(0,10); }
function openFilterPop(k,btn){
  closeFilterPop(); var c=colByKey(k); if(!c)return;
  var pop=document.createElement('div'); pop.className='f-pop'; pop.id='f-pop';
  var r=btn.getBoundingClientRect();
  pop.style.left=Math.max(8,Math.min(r.left,window.innerWidth-300))+'px'; pop.style.top=(r.bottom+6)+'px';
  document.body.appendChild(pop); pop.onclick=function(e){e.stopPropagation();};
  openFilter=k;
  /* 筛选浮层顶部直接给「升序/降序」：不用再关掉浮层去点表头（同样只影响自己的视图） */
  function addSortRow(){
    var onAsc=(state.sort===k&&state.dir===1), onDesc=(state.sort===k&&state.dir===-1);
    /* 已经用了高级筛选：这里改单列条件会把整组高级筛选替换掉，先说清楚 */
    var advHint = isAdvFilter(state.filters)
      ? '<div class="fp-adv-hint">当前是「高级筛选」。在这里改条件，会整组替换掉原来的高级筛选。</div>' : '';
    pop.insertAdjacentHTML('afterbegin', advHint +
      '<div class="fp-sort">'
      + '<button type="button" class="fp-sort-b'+(onAsc?' on':'')+'" data-sort="asc">'
      + '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M6 11l6-6 6 6"/></svg>升序</button>'
      + '<button type="button" class="fp-sort-b'+(onDesc?' on':'')+'" data-sort="desc">'
      + '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M6 13l6 6 6-6"/></svg>降序</button>'
      + '</div>'
      + '<div class="fp-adv"><span data-act="adv">高级筛选：多列「且 / 或」…</span></div>');
    pop.querySelectorAll('[data-sort]').forEach(function(b){
      b.onclick=function(ev){
        ev.stopPropagation();
        state.sort=k; state.dir=b.getAttribute('data-sort')==='asc'?1:-1; state.page=1;
        closeFilterPop(); renderHead(); loadRows(); updateViewChrome();
        toast('已按「'+c.label+'」'+(state.dir===1?'升序':'降序')+'（只影响你自己的视图）');
      };
    });
    var advLink=pop.querySelector('[data-act="adv"]');
    if(advLink) advLink.onclick=function(ev){
      ev.stopPropagation(); closeFilterPop(); openAdvancedFilter(k);
    };
  }
  if(c.type==='date'){ buildDatePop(pop,k); addSortRow(); return; }
  var isNum = (c.type==='number'||c.type==='formula');
  var keys=Object.keys(FACETS[k]||{});
  var curF=state.filters[k];
  // 文本/选项列也能用“自定义条件”（包含 / 不等于…）：已经用条件筛过的列，再点漏斗直接回到条件编辑器
  var curCond=!!(curF && typeof curF==='object' && !Array.isArray(curF) && (curF.op || curF.from || curF.to));
  function buildList(){
    var sel=state.filters[k]; if(!Array.isArray(sel)) sel=[];
    pop.innerHTML='<input class="fp-search" placeholder="搜索选项…">'
      + keys.map(function(x){return '<div class="fp-opt" data-v="'+esc(x)+'"><input type="checkbox"'+(sel.indexOf(x)!==-1?' checked':'')+'><span class="fp-v">'+esc(x)+'</span></div>';}).join('')
      + '<div class="fp-foot"><span data-act="all">全选</span>'
      + '<span class="fp-foot-r">' + (keys.length ? '<span data-act="cond">自定义条件…</span>' : '') + '<span data-act="clear">清空</span></span></div>';
    pop.querySelector('.fp-search').oninput=function(){var q=this.value.toLowerCase();pop.querySelectorAll('.fp-opt').forEach(function(o){o.style.display=!q||o.getAttribute('data-v').toLowerCase().indexOf(q)!==-1?'':'none';});};
    pop.querySelectorAll('.fp-opt').forEach(function(o){
      var cb = o.querySelector('input');
      o.onclick=function(){
        var v=o.getAttribute('data-v'); if(!state.filters[k])state.filters[k]=[];
        var i=state.filters[k].indexOf(v);
        if(i===-1) state.filters[k].push(v); else state.filters[k].splice(i,1);
        if(!state.filters[k].length) delete state.filters[k];
        cb.checked = i===-1;
        state.page=1; loadRows(); updateFunnels();
      };
    });
    pop.querySelector('[data-act="clear"]').onclick=function(){
      delete state.filters[k]; state.page=1;
      pop.querySelectorAll('.fp-opt input').forEach(function(c){c.checked=false;});
      loadRows(); updateFunnels();
    };
    pop.querySelector('[data-act="all"]').onclick=function(){
      state.filters[k]=Object.keys(FACETS[k]||{}); state.page=1;
      pop.querySelectorAll('.fp-opt input').forEach(function(c){c.checked=true;});
      loadRows(); updateFunnels();
    };
    var condLink=pop.querySelector('[data-act="cond"]');
    if(condLink) condLink.onclick=function(e){
      e.stopPropagation();
      buildCondPop(pop,k,c,isNum,buildList);
      addSortRow();
    };
    addSortRow();
  }
  if(isNum || !keys.length || curCond){ buildCondPop(pop,k,c,isNum, keys.length?buildList:null); addSortRow(); return; }
  buildList();
}
/* ============ 高级筛选：多列条件 + 且 / 或 ============
   存法：state.filters = { logic:'and'|'or', conds:[{key,op,value,value2}] }
   和单列筛选一样只是你自己的视图状态（能存进「视图 ▾」），不写数据库、不影响别人。
   只支持一层组合：条件之间统一“全部满足”或“任一满足”，不做 A 且 (B 或 C) 的嵌套。 */
function advOpsFor(type){
  if(type==='number'||type==='formula')
    return [['eq','等于'],['neq','不等于'],['gt','大于'],['gte','≥'],['lt','小于'],['lte','≤'],['between','区间'],['empty','为空'],['notempty','不为空']];
  if(type==='date') return [['daterange','区间'],['empty','为空'],['notempty','不为空']];
  if(type==='checkbox') return [['cb_yes','是'],['cb_no','否']];
  if(type==='multiselect') return [['contains','包含'],['notcontains','不包含'],['empty','为空'],['notempty','不为空']];
  return [['contains','包含'],['notcontains','不包含'],['eq','等于'],['neq','不等于'],['empty','为空'],['notempty','不为空']];
}
function advFilterCols(){ return (curTable().columns||[]).filter(function(c){ return c.type!=='image'; }); }
function openAdvancedFilter(seedKey){
  var cols=advFilterCols();
  if(!cols.length){ toast('这张表还没有可以筛选的列'); return; }
  var src=isAdvFilter(state.filters)?state.filters:null, init=[];
  if(src) init=src.conds.map(function(x){
    return {key:x.key, op:x.op||'contains', value:x.value==null?'':String(x.value), value2:x.value2==null?'':String(x.value2)};
  }).filter(function(x){ return cols.some(function(c){ return c.key===x.key; }); }).slice(0,20);
  var ADV={logic:(src&&src.logic==='or')?'or':'and', conds:init};
  function colOf(k){ return cols.filter(function(c){ return c.key===k; })[0]||cols[0]; }
  function seedCond(k0){
    var kk=(k0&&cols.some(function(c){return c.key===k0;}))?k0:cols[0].key;
    return {key:kk, op:advOpsFor(colOf(kk).type)[0][0], value:'', value2:''};
  }
  if(!ADV.conds.length) ADV.conds=[seedCond(seedKey)];
  modal('高级筛选', '<div id="adv-body"></div>',
    '<button class="btn" id="adv-clear">清除筛选</button><button class="btn" id="adv-cancel">取消</button>'
    + '<button class="btn btn-primary" id="adv-ok">应用</button>',
    function(root){
      root.querySelector('.box').classList.add('wide');
      var body=root.querySelector('#adv-body');
      body.innerHTML='<div class="adv-logic"><span>条件之间：</span>'
        + '<label><input type="radio" name="adv-logic" value="and">全部满足（且）</label>'
        + '<label><input type="radio" name="adv-logic" value="or">任一满足（或）</label></div>'
        + '<div id="adv-conds"></div>'
        + '<button type="button" class="adv-add" id="adv-add">＋ 添加条件</button>'
        + '<div class="adv-hint">· 条件之间只能统一“全部满足”或“任一满足”，不支持 (A 且 (B 或 C)) 这种嵌套。<br>'
        + '· 留空的条件会被忽略；一条都不填就相当于不筛选。<br>'
        + '· 和普通筛选一样只影响你自己的视图，别人看到的还是全部数据。</div>';
      var wrap=body.querySelector('#adv-conds');
      function syncRadios(){
        body.querySelectorAll('input[name="adv-logic"]').forEach(function(r){
          r.checked=(r.value===ADV.logic);
          r.onchange=function(){ ADV.logic=r.value; };
        });
      }
      function rowHtml(c){
        var col=colOf(c.key), ops=advOpsFor(col.type);
        var op=ops.some(function(o){ return o[0]===c.op; })?c.op:ops[0][0];
        var isNum=(col.type==='number'||col.type==='formula');
        var vt=col.type==='date'?' type="date"':(isNum?' inputmode="decimal"':'');
        return '<select class="adv-f" data-native="1">'+cols.map(function(x){
            return '<option value="'+esc(x.key)+'"'+(x.key===col.key?' selected':'')+'>'
              + esc(x.label)+(x.hidden?'（隐藏）':'')+'</option>';
          }).join('')+'</select>'
          + '<select class="adv-op" data-native="1">'+ops.map(function(o){
            return '<option value="'+o[0]+'"'+(o[0]===op?' selected':'')+'>'+o[1]+'</option>';
          }).join('')+'</select>'
          + '<div class="adv-vals"><input class="adv-v"'+vt+' placeholder="值"><input class="adv-v2"'+vt+' placeholder="到"></div>';
      }
      function syncRow(row){
        var op=row.querySelector('.adv-op').value;
        var vals=row.querySelector('.adv-vals'), v=row.querySelector('.adv-v'), v2=row.querySelector('.adv-v2');
        var needVals=(op!=='empty' && op!=='notempty' && op!=='cb_yes' && op!=='cb_no');
        vals.style.display=needVals?'':'none';
        v2.style.display=(op==='between'||op==='daterange')?'':'none';
        v.setAttribute('placeholder', op==='daterange'?'开始日期':(op==='between'?'最小值':'值'));
        v2.setAttribute('placeholder', op==='daterange'?'结束日期':'最大值');
      }
      function renderConds(){
        wrap.innerHTML=ADV.conds.map(function(c,i){
          return '<div class="adv-cond" data-i="'+i+'">'+rowHtml(c)
            + '<button type="button" class="adv-del" title="删除这条条件">✕</button></div>';
        }).join('');
        wrap.querySelectorAll('.adv-cond').forEach(function(row,i){
          var c=ADV.conds[i];
          var v=row.querySelector('.adv-v'), v2=row.querySelector('.adv-v2');
          v.value=c.value||''; v2.value=c.value2||'';
          /* 边输边回写：改别的行/删行会整组重绘，没回写的值会被清掉（真浏览器里踩到过） */
          v.oninput=function(){ ADV.conds[i].value=v.value; };
          v2.oninput=function(){ ADV.conds[i].value2=v2.value; };
          row.querySelector('.adv-f').onchange=function(){
            ADV.conds[i]={key:this.value, op:advOpsFor(colOf(this.value).type)[0][0], value:'', value2:''};
            renderConds();
          };
          row.querySelector('.adv-op').onchange=function(){
            ADV.conds[i].op=this.value; syncRow(row);
          };
          row.querySelector('.adv-del').onclick=function(){
            ADV.conds.splice(i,1); if(!ADV.conds.length) ADV.conds=[seedCond()];
            renderConds();
          };
          syncRow(row);
        });
      }
      function collect(){
        var out=[];
        wrap.querySelectorAll('.adv-cond').forEach(function(row){
          var key=row.querySelector('.adv-f').value, op=row.querySelector('.adv-op').value;
          var v=(row.querySelector('.adv-v').value||'').trim();
          var v2=(row.querySelector('.adv-v2').value||'').trim();
          if(op==='empty'||op==='notempty'){ out.push({key:key, op:op}); return; }
          if(op==='cb_yes'){ out.push({key:key, op:'eq', value:'是'}); return; }
          if(op==='cb_no'){ out.push({key:key, op:'eq', value:'否'}); return; }
          if(op==='daterange'){ if(v||v2) out.push({key:key, op:'daterange', value:v, value2:v2}); return; }
          if(op==='between'){
            if(v&&v2) out.push({key:key, op:'between', value:v, value2:v2});
            else if(v) out.push({key:key, op:'gte', value:v});
            else if(v2) out.push({key:key, op:'lte', value:v2});
            return;
          }
          if(v==='') return;
          out.push({key:key, op:op, value:v});
        });
        return out.slice(0,20);
      }
      body.querySelector('#adv-add').onclick=function(){
        if(ADV.conds.length>=20){ toast('最多 20 个条件'); return; }
        ADV.conds.push(seedCond()); renderConds();
      };
      root.querySelector('#adv-cancel').onclick=function(){ closeModal(); };
      root.querySelector('#adv-clear').onclick=function(){
        state.filters=Object.create(null); state.page=1; closeModal();
        renderHead(); loadRows(); updateViewChrome(); toast('已清除筛选');
      };
      root.querySelector('#adv-ok').onclick=function(){
        var list=collect();
        if(!list.length) state.filters=Object.create(null);
        else state.filters={ logic:ADV.logic==='or'?'or':'and', conds:list };
        state.page=1; closeModal(); renderHead(); loadRows(); updateViewChrome();
        toast(list.length ? ('高级筛选：'+list.length+' 个条件，'+(ADV.logic==='or'?'任一满足':'全部满足')+'（只影响你自己的视图）')
                          : '条件都是空的，已清除筛选');
      };
      syncRadios(); renderConds();
      var f0=wrap.querySelector('.adv-v'); if(f0 && f0.style.display!=='none') f0.focus();
    });
}
/* 文本/数字/公式：条件筛选（包含、等于、大于…、区间） */
function buildCondPop(pop,k,c,isNum,onBack){
  var cur=state.filters[k]||{};
  var ops = isNum
    ? [['eq','等于'],['gt','大于'],['lt','小于'],['gte','≥'],['lte','≤'],['between','区间']]
    : [['contains','包含'],['eq','等于'],['neq','不等于'],['notcontains','不包含']];
  if(!cur.op) cur={op:ops[0][0], value:'', value2:''};
  pop.innerHTML='<div class="fc">'
    + '<select class="fc-op">'+ops.map(function(o){return '<option value="'+o[0]+'">'+o[1]+'</option>';}).join('')+'</select>'
    + '<input class="fc-v" placeholder="值">'
    + '<input class="fc-v2" placeholder="到" style="display:none">'
    + '</div>'
    + '<div class="fp-foot">'
    + (onBack ? '<span data-act="back">← 按值选择</span>' : '<span></span>')
    + '<span class="fp-foot-r"><span data-act="clear">清除</span><span data-act="apply">应用</span></span>'
    + '</div>';
  enhanceSelects(pop);
  var back=pop.querySelector('[data-act="back"]');
  if(back && onBack) back.onclick=function(e){ e.stopPropagation(); onBack(); };
  var opSel=pop.querySelector('.fc-op'), v1=pop.querySelector('.fc-v'), v2=pop.querySelector('.fc-v2');
  opSel.value=cur.op||ops[0][0]; v1.value=cur.value||''; v2.value=cur.value2||'';
  function sync(){ v2.style.display = opSel.value==='between' ? '' : 'none'; }
  opSel.onchange=sync; sync();
  v1.onkeydown=function(e){ if(e.key==='Enter') apply(); };
  v2.onkeydown=function(e){ if(e.key==='Enter') apply(); };
  function apply(){
    var op=opSel.value, a=v1.value.trim(), b=v2.value.trim();
    if(op==='between'){ if(!a&&!b){ delete state.filters[k]; } else state.filters[k]={op:op,value:a,value2:b}; }
    else { if(a===''){ delete state.filters[k]; } else state.filters[k]={op:op,value:a}; }
    state.page=1; closeFilterPop(); loadRows(); updateFunnels();
  }
  pop.querySelector('[data-act="apply"]').onclick=apply;
  pop.querySelector('[data-act="clear"]').onclick=function(){
    delete state.filters[k]; state.page=1; closeFilterPop(); loadRows(); updateFunnels();
  };
  v1.focus();
}

function buildDatePop(pop,k){
  pop.classList.add('date-pop');
  var cur=state.filters[k]||{}; var tmpF=cur.from||'',tmpT=cur.to||'',picking=tmpF&&tmpT?'start':(tmpF?'end':'start'),aq='';
  var vy=tmpF?+tmpF.slice(0,4):new Date().getFullYear(), vm=tmpF?+tmpF.slice(5,7)-1:new Date().getMonth();
  pop.innerHTML='<div class="dp-wrap"><div class="dp-side"><div class="dp-t">快捷</div>'
    + ['今天','昨天','最近 7 天','最近 30 天','本月','上月','今年','全部'].map(function(x){return '<button class="dp-q" data-q="'+x+'">'+x+'</button>';}).join('')
    + '</div><div class="dp-main"><div class="dp-head">'
    + '<button class="dp-nav" data-nav="-12" title="上一年"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6l-6 6 6 6M11 6l-6 6 6 6"/></svg></button>'
    + '<button class="dp-nav" data-nav="-1" title="上个月"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 6l-6 6 6 6"/></svg></button>'
    + '<span class="dp-ym"></span>'
    + '<button class="dp-nav" data-nav="1" title="下个月"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg></button>'
    + '<button class="dp-nav" data-nav="12" title="下一年"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l6 6-6 6M13 6l6 6-6 6"/></svg></button>'
    + '</div>'
    + '<div class="dp-week"><span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span>六</span><span>日</span></div><div class="dp-days"></div>'
    + '<div class="dp-vals"><input id="dp-from" readonly><span>→</span><input id="dp-to" readonly></div></div></div>'
    + '<div class="dp-foot"><span></span><div><button class="dp-clear" data-act="clear">清除</button> <button class="dp-ok" data-act="ok">确定</button></div></div>';
  function fmt(y,m,d){return y+'-'+String(m+1).padStart(2,'0')+'-'+String(d).padStart(2,'0');}
  function draw(){
    pop.querySelector('.dp-ym').textContent=vy+'年'+(vm+1)+'月';
    var first=(new Date(vy,vm,1).getDay()+6)%7,dim=new Date(vy,vm+1,0).getDate(),pd=new Date(vy,vm,0).getDate(),h='';
    for(var i=0;i<first;i++)h+='<span class="dp-day other">'+(pd-first+1+i)+'</span>';
    for(var d=1;d<=dim;d++){var ds=fmt(vy,vm,d),cl='dp-day';
      if(tmpF&&tmpT&&ds>tmpF&&ds<tmpT)cl+=' inrange'; if(ds===tmpF||ds===tmpT)cl+=' endpoint';
      h+='<span class="'+cl+'" data-d="'+ds+'">'+d+'</span>';}
    var rest=(7-(first+dim)%7)%7; for(var j=1;j<=rest;j++)h+='<span class="dp-day other">'+j+'</span>';
    pop.querySelector('.dp-days').innerHTML=h;
    pop.querySelector('#dp-from').value=tmpF; pop.querySelector('#dp-to').value=tmpT;
    pop.querySelectorAll('.dp-q').forEach(function(b){b.classList.toggle('on',b.getAttribute('data-q')===aq);});
    pop.querySelectorAll('.dp-day[data-d]').forEach(function(cell){cell.onclick=function(){
      var ds=cell.getAttribute('data-d');
      if(picking==='start'){tmpF=ds;tmpT='';picking='end';} else {if(ds<tmpF){tmpT=tmpF;tmpF=ds;}else tmpT=ds;picking='start';}
      aq='';draw();};});
  }
  pop.querySelectorAll('.dp-nav').forEach(function(b){b.onclick=function(){
    var d=+b.getAttribute('data-nav'); vm+=d;
    while(vm<0){vm+=12;vy--;}
    while(vm>11){vm-=12;vy++;}
    draw();};});
  pop.querySelectorAll('.dp-q').forEach(function(b){b.onclick=function(){
    var q=b.getAttribute('data-q'),f,t2;
    if(q==='今天'){f=t2=isoDate();} else if(q==='昨天'){f=t2=isoDate(-1);}
    else if(q==='本月'){f=isoDate().slice(0,8)+'01';t2=isoDate();}
    else if(q==='上月'){var n=new Date(),y=n.getFullYear(),m=n.getMonth()-1;if(m<0){m=11;y--;}f=y+'-'+String(m+1).padStart(2,'0')+'-01';t2=y+'-'+String(m+1).padStart(2,'0')+'-'+String(new Date(y,m+1,0).getDate()).padStart(2,'0');}
    else if(q==='今年'){f=isoDate().slice(0,4)+'-01-01';t2=isoDate();}
    else if(q==='最近 7 天'||q==='最近 30 天'){f=isoDate(-(parseInt(q.replace(/\D/g,''))-1));t2=isoDate();}
    else {tmpF=tmpT='';picking='start';}
    if(q!=='全部'){tmpF=f;tmpT=t2;picking='start';} aq=q;draw();};});
  pop.querySelector('[data-act="clear"]').onclick=function(){delete state.filters[k];state.page=1;closeFilterPop();renderHead();loadRows();};
  pop.querySelector('[data-act="ok"]').onclick=function(){
    if(tmpF||tmpT)state.filters[k]={from:tmpF,to:tmpT};else delete state.filters[k];
    state.page=1;closeFilterPop();renderHead();loadRows();};
  draw();
}
if(!window.__outside){window.__outside=true;document.addEventListener('click',function(e){if(openFilter&&!e.target.closest('.f-pop')&&!e.target.closest('.th-f'))closeFilterPop();});
document.addEventListener('click',function(e){ if(!e.target.closest('#tbody td.cell-sel')&&!e.target.closest('#tbody td.in-sel')&&!e.target.closest('.cell-expand')&&!e.target.closest('.cell-pop')&&!e.target.closest('#fill-handle')&&!e.target.closest('#modal-root')) clearCellSel(); });
document.addEventListener('keydown',function(e){if(e.key==='Escape'&&openFilter)closeFilterPop();});}

/* ============ 历史 / 新增行 / 导出 ============ */
$('btn-new-table').onclick=function(){openNewTable('');};
$('pg-size').value = String(state.size);
$('pg-size').onchange=function(){
  state.size = +this.value; state.page = 1;
  localStorage.setItem('kb_pgsize', String(state.size));
  renderRows(ROWS_CACHE[curRowsKey(state.tid)] || {rows:[], total:0, facets:{}});  // 立即清空旧页
  loadRows();
};
$('btn-new-project').onclick=openNewProject;
$('btn-new-table-sb').onclick=function(){ openNewTable(''); };
$('btn-back-proj').onclick=function(){ goHomeProjects(); };
$('btn-back').onclick=function(){ goHomeProjects(); };
$('btn-new-group').onclick=function(){ openNewTable(''); };
var hkwTimer; $('home-kw').oninput=function(){ clearTimeout(hkwTimer);
  hkwTimer=setTimeout(function(){ goHomeProjects(); },200); };
function waitRowEl(rid, timeout) {
  return new Promise(function (resolve) {
    var t0 = Date.now();
    (function tick() {
      revealRow(rid);
      var tr = document.querySelector('#tbody tr[data-rid="' + rid + '"]');
      if (tr) return resolve(tr);
      if (Date.now() - t0 >= timeout) return resolve(null);
      setTimeout(tick, 50);
    })();
  });
}
function focusRowEl(rid) {
  revealRow(rid);
  var tr = document.querySelector('#tbody tr[data-rid="' + rid + '"]');
  if (!tr) return false;
  try { tr.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' }); }
  catch (e) { tr.scrollIntoView(); }
  tr.classList.remove('row-flash');
  void tr.offsetWidth;
  tr.classList.add('row-flash');
  setTimeout(function () { tr.classList.remove('row-flash'); }, 1800);
  return true;
}
$('btn-add-row').onclick = async function () {
  if (!state.tid) { toast('请先打开一张表'); return; }
  try {
    var res = await api('/api/tables/' + state.tid + '/rows', 'POST', { data: {} });
    var rid = res && res.id;
    var hadView = !!(state.kw || state.sort || Object.keys(state.filters).length);
    if (hadView) {
      state.kw = ''; $('kw').value = '';
      state.sort = ''; state.dir = 1; state.filters = Object.create(null);
      closeFilterPop();
    }
    var t = curTable();
    var cnt = ((t && t.count) || 0) + 1;
    state.page = state.size > 0 ? Math.max(1, Math.ceil(cnt / state.size)) : 1;
    await refreshMeta(state.tid);
    var tr = await waitRowEl(rid, 700);
    if (!tr) {
      var total = (curTable() && curTable().count) || cnt;
      var last = state.size > 0 ? Math.max(1, Math.ceil(total / state.size)) : 1;
      if (last !== state.page) { state.page = last; await loadRows(); }
      tr = await waitRowEl(rid, 1400);
    }
    if (tr) {
      focusRowEl(rid);
      toast(hadView ? '已添加并定位到新行（已清除筛选/排序）' : '已添加一行，已定位');
    } else {
      toast('已新增一行，请翻到最后一页查看');
    }
  } catch (e) { toast('新增失败：' + e.message); }
};
function closeExportMenu(){ var m=$('export-menu'); if(m) m.remove(); }
function openExportMenu(){
  var b=$('btn-csv'); if(!b) return;
  closeExportMenu();
  var m=document.createElement('div'); m.className='ctx-menu view-menu'; m.id='export-menu';
  m.innerHTML='<button type="button" class="ctx-item" data-fmt="csv"><span>导出 CSV</span></button>'
    + '<button type="button" class="ctx-item" data-fmt="xlsx"><span>导出 Excel（.xlsx，含公式值、冻结表头）</span></button>';
  document.body.appendChild(m);
  var r=b.getBoundingClientRect();
  m.style.left=Math.max(8, Math.min(r.left, window.innerWidth-m.offsetWidth-8))+'px';
  m.style.top=Math.round(r.bottom+6)+'px';
  m.querySelectorAll('[data-fmt]').forEach(function(it){
    it.onclick=function(){
      var fmt=it.getAttribute('data-fmt');
      closeExportMenu();
      window.open('/api/tables/'+state.tid+'/export'+(fmt==='xlsx'?'?fmt=xlsx':''),'_blank');
    };
  });
}
document.addEventListener('click', function(e){
  var m=$('export-menu');
  if(m && !e.target.closest('#export-menu') && !e.target.closest('#btn-csv')) closeExportMenu();
}, true);
$('btn-csv').onclick=openExportMenu;
$('btn-apikey').onclick = openApiKeys;
$('btn-sync').onclick = openSyncMgr;
$('btn-hist').onclick=async function(){
  var t=curTable();
  var d=await api('/api/tables/'+state.tid+'/audit?limit=200');
  var labelOf=Object.create(null);
  (t.columns||[]).forEach(function(c){ labelOf[c.key]=c.label; });
  function colName(k){ return labelOf[k]||k; }
  function fmtVal(v){
    if(v===null||v===undefined||v==='') return '<span class="hv-none">空</span>';
    if(typeof v==='object') v=JSON.stringify(v);
    var str=String(v);
    return '<span class="hv">'+esc(str.length>90?str.slice(0,90)+'…':str)+'</span>';
  }
  function diff(o,n){ return fmtVal(o)+'<span class="hv-arrow">→</span>'+fmtVal(n); }
  function describe(r){
    var det=r.detail||{};
    if(typeof det==='string'){ try{ det=JSON.parse(det); }catch(e){ det={}; } }
    var act=r.action, rowTag = r.row_id ? ('行 #'+r.row_id) : '';
    var head='', diffs=[];
    if(act==='update_row'){
      var ks=Object.keys(det||{});
      if(!ks.length){ head=(rowTag?rowTag+' · ':'')+'保存（无字段变化）'; }
      else {
        head=(rowTag?rowTag+' · ':'')+ks.map(function(k){return '列「'+colName(k)+'」';}).join('、');
        ks.forEach(function(k){ diffs.push(diff((det[k]||{}).old,(det[k]||{}).new)); });
      }
    } else if(act==='create_row'){
      head='新增'+(rowTag||'一行');
      var ck=Object.keys(det||{});
      if(ck.length) head+='（'+ck.map(colName).join('、')+'）';
    } else if(act==='delete_row'){
      head='删除'+(rowTag||'一行');
    } else if(act==='create_column'){
      head='新增列「'+(det.label||'')+'」'+(det.type?'（'+(TYPE_LABEL[det.type]||det.type)+'）':'');
    } else if(act==='delete_column'){
      head='删除列「'+(det.label||'')+'」';
    } else if(act==='update_column'){
      head='修改列「'+(det.col||det.label||'')+'」';
      if(det.label) diffs.push('名称 '+diff(det.label.old,det.label.new));
      if(det.type) diffs.push('类型 '+diff(TYPE_LABEL[det.type.old]||det.type.old, TYPE_LABEL[det.type.new]||det.type.new));
      if(det.expr) diffs.push('公式 '+diff(det.expr.old,det.expr.new));
    } else if(act==='rename_table'){
      head='重命名表'; diffs.push(diff(det.old,det.new));
    } else if(act==='create_table'){
      head='新建表「'+(det.name||'')+'」';
    } else if(act==='import_rows'){
      head='导入 Excel：新增 '+(det.rows||0)+' 行'+(det.mode==='replace'?'（覆盖）':'');
    } else if(act==='import_new_table'){
      head='导入 Excel 新建表「'+(det.name||'')+'」：'+(det.rows||0)+' 行';
    } else if(act==='delete_table'){
      head='删除表';
    } else if(act==='sort_columns'){
      head='调整列顺序 / 显隐';
    } else if(act==='fix_column_shift'){
      head='修正列错位（'+(det.rows||0)+' 行）';
    } else if(act==='api_insert'){
      head='接口写入 '+(det.count||det.rows||0)+' 行'+(det.key?('（密钥 '+det.key+'）'):'');
    } else if(act==='apikey_create'){
      head='生成写入密钥'+(det.key?(' '+det.key):'')+(det.note?('（'+det.note+'）'):'');
    } else if(act==='apikey_revoke'){
      head='作废写入密钥'+(det.note?('（'+det.note+'）'):'');
    } else if(act==='sync_insert'||act==='sync_update'||act==='sync_delete') {
      head=(act==='sync_insert'?'汇总同步：新增':(act==='sync_update'?'汇总同步：更新':'汇总同步：删除'))+(rowTag?' '+rowTag:'')+'（来自子表）';
    } else if(act==='sync_cleanup'){
      head='汇总同步：清理总表里已失效的复制行'+(det.rows?('（'+det.rows+' 行）'):'');
    } else if(act==='update_table'){
      head='修改表设置';
    } else {
      // 认不出的动作：动作名 + 截断的详情，不再整段甩原始 JSON
      head=esc(act||'其他操作');
      var detTxt=Object.keys(det||{}).length?JSON.stringify(det):'';
      if(detTxt) diffs.push('<span class="hv-none">'+esc(detTxt.length>120?detTxt.slice(0,120)+'…':detTxt)+'</span>');
    }
    return {head:head, diffs:diffs};
  }
  var rows=d.rows.length?d.rows.map(function(r){
    var info=describe(r);
    var actor=r.actor?('@'+r.actor):'系统';
    return '<div class="hist-row">'
      + '<div class="hist-time">'+esc(r.at)+'</div>'
      + '<div class="hist-main"><div class="hist-line">'
      +   '<span class="hist-actor">'+esc(actor)+'</span>'
      +   '<span class="hist-head">'+esc(info.head)+'</span></div>'
      +   (info.diffs.length?'<div class="hist-diff">'+info.diffs.join('<br>')+'</div>':'')
      + '</div></div>';
  }).join(''):'<div class="dr-empty">暂无变动记录</div>';
  modal('变动历史 · '+(t.name.split('·')[1]||t.name),
    '<div class="hist-tbl">表：'+esc(t.name)+'</div>'+rows,
    '<button class="btn" id="hx">关闭</button>',
    function(root){ root.querySelector('.box').classList.add('wide'); $('hx').onclick=closeModal;});
};
var kwTimer;$('kw').oninput=function(){clearTimeout(kwTimer);kwTimer=setTimeout(function(){state.kw=$('kw').value;state.page=1;loadRows();},220);};

/* ============ 拖拽排序（长按 320ms 激活，手掌光标） ============ */
function enableDragSort(container, selector, axis, onDone, endBeforeEl) {
  var pressTimer = null, dragEl = null, sx = 0, sy = 0, active = false, pid = null, rafPending = false;

  function items() {
    return Array.from(container.querySelectorAll(selector)).filter(function (x) {
      return x !== dragEl && !x.classList.contains('sheet-add') && !x.classList.contains('field-mgr');
    });
  }
  function stop() {
    clearTimeout(pressTimer); pressTimer = null;
    if (active && dragEl) {
      dragEl.classList.remove('dragging');
      container.classList.remove('sorting');
      container.style.touchAction = '';
      suppressClick(container);
      onDone();
    }
    active = false; dragEl = null;
    try { container.releasePointerCapture(pid); } catch (e) {}
    pid = null;
  }
  function suppressClick(c) {
    var h = function (ev) { ev.stopPropagation(); ev.preventDefault(); c.removeEventListener('click', h, true); };
    c.addEventListener('click', h, true);
    setTimeout(function () { c.removeEventListener('click', h, true); }, 400);
  }
  container.addEventListener('pointerdown', function (e) {
    if (e.button !== 0) return;
    if (e.target.closest('button')) return;
    if (container.id === 'sb-list' && !(ME && ME.role === 'admin')) return;
    if (container.id === 'sheet-bar' && !canEditRows()) return;
    var el = e.target.closest(selector);
    if (!el || el.classList.contains('sheet-add') || el.classList.contains('field-mgr') || el.classList.contains('sheet-del')) return;
    dragEl = el; sx = e.clientX; sy = e.clientY; pid = e.pointerId; active = false;
    clearTimeout(pressTimer);
    pressTimer = setTimeout(function () {
      active = true;
      el.classList.add('dragging');
      container.classList.add('sorting');
      container.style.touchAction = 'none';
      try { container.setPointerCapture(pid); } catch (err) {}
    }, 320);
  });
  container.addEventListener('pointermove', function (e) {
    if (!dragEl || pid === null) return;
    var dx = Math.abs(e.clientX - sx), dy = Math.abs(e.clientY - sy);
    if (!active) {
      if (dx > 6 || dy > 6) { clearTimeout(pressTimer); pressTimer = null; dragEl = null; pid = null; }
      return;
    }
    e.preventDefault();
    var cx = e.clientX, cy = e.clientY;
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(function () {
      rafPending = false;
      if (!dragEl) return;
      var list = items(), ref = null;
      for (var i = 0; i < list.length; i++) {
        var r = list[i].getBoundingClientRect();
        var mid = axis === 'y' ? r.top + r.height / 2 : r.left + r.width / 2;
        var pos = axis === 'y' ? cy : cx;
        if (pos < mid) { ref = list[i]; break; }
      }
      if (ref) container.insertBefore(dragEl, ref);
      else {
        var anchorEl = typeof endBeforeEl === 'function' ? endBeforeEl() : endBeforeEl;
        if (anchorEl) container.insertBefore(dragEl, anchorEl);
        else container.appendChild(dragEl);
      }
    });
  });
  ['pointerup', 'pointercancel', 'pointerleave'].forEach(function (ev) {
    container.addEventListener(ev, function () { if (dragEl) stop(); });
  });
}

/* 侧栏分组排序：只存在本机（按账号），不改数据库、不影响别人 */
enableDragSort($('sb-list'), '.sb-item', 'y', function () {
  var names = Array.from($('sb-list').querySelectorAll('.sb-item')).map(function (x) { return x.getAttribute('data-g'); });
  var o = readOrder(); o.g = names; writeOrder(o);
  applyUserOrder(GROUPS); renderSidebar();
  toast('顺序已保存（只影响你自己的视图）');
});

/* Sheet 页签排序：同样只存在本机，不写数据库 */
enableDragSort($('sheet-bar'), '.sheet-tab[data-id]', 'x', function () {
  var g = curGroup(); if (!g) return;
  var tids = Array.from($('sheet-bar').querySelectorAll('.sheet-tab[data-id]')).map(function (x) { return +x.getAttribute('data-id'); });
  var o = readOrder(); o.s = o.s || {}; o.s[g.name] = tids; writeOrder(o);
  applyUserOrder(GROUPS); renderSheetBar();
  toast('顺序已保存（只影响你自己的视图）');
}, function () { return $('sheet-add'); });

