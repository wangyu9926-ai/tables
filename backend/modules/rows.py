# -*- coding: utf-8 -*-
"""行：列表筛选分页、按 ID 取、批量写、新建/移动/修改/删除"""
from backend.deps import *          # noqa: F401,F403
from fastapi import APIRouter       # noqa: F401

router = APIRouter()

class RowReq(BaseModel):
    data: dict
    ver: int | None = None
    before_id: int | None = None   # 在目标行之前插入（行号悬浮 + 号用）
    after_id: int | None = None    # 在目标行之后插入

class RowMoveReq(BaseModel):
    before_id: int | None = None   # 拖到这一行之前
    after_id: int | None = None    # 拖到这一行之后
    offset: int | None = None      # 或：相对当前位置移动 -1 / +1（右键“上移/下移”）

@router.get("/api/tables/{tid}/rows")
def list_rows(tid: int, page: int = 1, size: int = 50, q: str = '',
              sort: str = '', dir: str = 'asc', filters: str = ''):
    # 表/列结构直接从内存缓存取，省掉 2 次远程往返
    cached_t = None
    if _META['data']:
        cached_t = next((x for x in _META['data']['tables'] if x['id'] == tid), None)
    if cached_t:
        cols = [c for c in cached_t['columns'] if not c['hidden']]
        project = cached_t.get('project') or ''
    else:
        t0 = _need(tid)
        project = t0.get('project') or ''
        cols = columns(tid)
    need_table(tid, project)
    try:
        # 只有这张表自己是汇总总表、且队列里还有它没做完的增量时才等；读别的表不排队
        kb_sync.flush_for(tid, 1.5)
    except Exception:
        pass
    view = _table_view(tid, cols)
    data_rows = view['rows']

    kw = (q or '').strip().lower()
    if kw:
        data_rows = [r for r in data_rows
                     if any(v is not None and kw in str(v).lower() for v in r['data'].values())]

    multi = {}
    if filters:
        try:
            multi = json.loads(filters)
        except Exception:
            multi = {}
        # 只认 {列: 条件} 或 {logic, conds} 两种对象；数组 / 字符串 / 数字按“没有筛选”处理（和非法 JSON 同一口径）
        if not isinstance(multi, dict):
            multi = {}

    bykey = {c['key']: c for c in cols}
    formula_keys = {c['key'] for c in cols if c['type'] == 'formula'}

    def _uses_formula(flt):
        if isinstance(flt.get('conds'), list):
            return any(isinstance(c.get('key'), str) and c['key'] in formula_keys
                       for c in flt['conds'] if isinstance(c, dict))
        return any(isinstance(k, str) and k in formula_keys for k in flt)

    if multi and _uses_formula(multi):
        # 公式列参与筛选时先全量算公式；复制行，不能污染共享的视图缓存
        data_rows = [{'id': r['id'], 'ver': r['ver'], 'created_at': r['created_at'],
                      'updated_at': r['updated_at'],
                      'data': compute_formulas(r['data'], cols, project)} for r in data_rows]

    def _num(x):
        try:
            return float(str(x).replace(',', ''))
        except Exception:
            m = re.fullmatch(r'\s*(-?\d+(?:\.\d+)?)\s*([wW万])?\s*', str(x or ''))
            if m:
                n = float(m.group(1))
                return n * 10000 if m.group(2) else n
        return None

    def match(v, spec, ctype='text'):
        if isinstance(spec, dict):
            if 'op' in spec:
                op = spec.get('op')
                raw = spec.get('value')
                raw2 = spec.get('value2')
                if op in ('empty', 'notempty'):
                    is_empty = v is None or (isinstance(v, str) and not v.strip()) \
                        or (isinstance(v, (list, tuple)) and not v)
                    return is_empty if op == 'empty' else (not is_empty)
                if ctype == 'checkbox' and op in ('eq', 'neq'):
                    want = str(raw).strip().lower() in ('1', 'true', 'yes', 'on', '是')
                    hit = (v is True) == want
                    return hit if op == 'eq' else (not hit)
                if op in ('contains', 'notcontains'):
                    hit = str(raw or '').lower() in str(v or '').lower() if raw not in (None, '') else True
                    if op == 'contains':
                        return hit
                    return (not hit) and (v not in (None, ''))
                if op in ('eq', 'neq'):
                    if ctype in ('number', 'formula'):
                        a, b = _num(v), _num(raw)
                        hit = (a is not None and b is not None and a == b)
                    else:
                        hit = str(v or '').strip().lower() == str(raw or '').strip().lower()
                    return hit if op == 'eq' else (not hit)
                if op in ('gt', 'lt', 'gte', 'lte', 'between'):
                    a = _num(v)
                    if a is None:
                        return False
                    if op == 'between':
                        b1, b2 = _num(raw), _num(raw2)
                        return (b1 is None or a >= b1) and (b2 is None or a <= b2)
                    b = _num(raw)
                    if b is None:
                        return True
                    return {'gt': a > b, 'lt': a < b, 'gte': a >= b, 'lte': a <= b}[op]
                return True
            # 日期范围：起止一律按文本比较；传进来数字 / 对象时先转成文本，不能让比较本身报错
            if v is None or str(v).strip() == '':
                return False
            s = str(v).strip()[:10]
            f, to = str(spec.get('from') or ''), str(spec.get('to') or '')
            return (not f or s >= f) and (not to or s <= to)
        if not isinstance(spec, (str, list, tuple)):
            return True                             # 数字 / 布尔不是合法的筛选条件：这一项不筛
        if ctype == 'multiselect' or isinstance(v, (list, tuple)):
            arr = list(v) if isinstance(v, (list, tuple)) else ([v] if v not in (None, '') else [])
            if '（空）' in spec and not arr:
                return True
            return any(str(x) in spec for x in arr)
        if '（空）' in spec and (v is None or str(v).strip() == ''):
            return True
        return str(v or '') in spec

    # facets 来自视图缓存（筛选前全量统计），保证筛选浮层始终列出完整选项
    facets = view['facets']

    if multi:
        if isinstance(multi.get('conds'), list):
            # 高级筛选：{logic:'and'|'or', conds:[{key,op,value,value2} | {key,op:'daterange',value,value2}]}
            logic = 'or' if str(multi.get('logic') or 'and').lower() == 'or' else 'and'
            conds = []
            for c in multi['conds'][:20]:
                if not isinstance(c, dict):
                    continue
                key = c.get('key')
                if not isinstance(key, str) or key not in bykey:
                    continue                    # 列已经被删掉（或 key 不是文本）：忽略这条，不能把整表筛成空
                op = c.get('op') if isinstance(c.get('op'), str) else None
                if op == 'daterange':
                    spec = {'from': c.get('value') or '', 'to': c.get('value2') or ''}
                elif op:
                    spec = {'op': op, 'value': c.get('value'), 'value2': c.get('value2')}
                elif c.get('from') or c.get('to'):
                    spec = {'from': c.get('from') or '', 'to': c.get('to') or ''}
                else:
                    continue
                conds.append((key, spec))
            if conds:
                def _row_hit(r):
                    vals = [match(r['data'].get(k), s, bykey[k]['type']) for k, s in conds]
                    return any(vals) if logic == 'or' else all(vals)
                data_rows = [r for r in data_rows if _row_hit(r)]
        else:
            data_rows = [r for r in data_rows if all(
                match(r['data'].get(c), s, (bykey.get(c) or {}).get('type') or 'text')
                for c, s in multi.items() if s)]

    if sort:
        col = next((c for c in cols if c['key'] == sort), None)
        is_num = col and col['type'] in ('number', 'formula', 'date')

        def keyf(r):
            v = r['data'].get(sort)
            # 排序键必须同构：公式列 / 数字列里可能既有算得出的数、又有算不出的文本，
            # 直接混着返回 float 和 str 会让比较抛 TypeError（整页 500）。
            if v is None or str(v).strip() == '':
                return (1, 0, '')
            if is_num:
                try:
                    return (0, 0, float(str(v).replace(',', '')))
                except Exception:
                    pass
            return (0, 1, str(v))
        # 不能原地 sort：data_rows 可能直接引用共享视图缓存
        data_rows = sorted(data_rows, key=keyf, reverse=(dir == 'desc'))

    total = len(data_rows)
    if size and size > 0:
        start = (page - 1) * size
        page_rows = data_rows[start:start + size]
    else:
        page_rows = data_rows[:]          # size=0：全部
    # 公式列在分页后算
    fcs = [c for c in cols if c['type'] == 'formula']
    out = []
    for r in page_rows:
        d = compute_formulas(r['data'], cols, project) if fcs else dict(r['data'])
        out.append({'id': r['id'], 'data': _json_safe_numbers(d), 'ver': r['ver'],
                    'created_at': _ts(r['created_at']), 'updated_at': _ts(r['updated_at'])})
    return _json_response({'rows': out, 'total': total, 'facets': facets})

@router.get("/api/tables/{tid}/rows/by-ids")
def rows_by_ids(tid: int, ids: str = ''):
    """只取指定的几行（实时刷新的"最小化"路径）：变更日志说哪几行变了，就只取这几行，
    不用把整页重算一遍。最多 200 行，和列表接口同一套行结构（公式列算好、隐藏列不出现）。"""
    _t = _need(tid)
    project = _t.get('project') or ''
    need_table(tid, project)
    want = []
    for x in (ids or '').split(','):
        x = x.strip()
        try:
            # 只收普通十进制数字：'²' 这类字符 isdigit() 也为真，却转不成整数
            if x and x.isascii() and x.isdigit():
                want.append(min(int(x), 18446744073709551615))
        except ValueError:
            pass
    want = want[:200]
    if not want:
        return _json_response({'rows': []})
    ph = ','.join(['%s'] * len(want))
    recs = q(f'SELECT id,ver,data,created_at,updated_at FROM kb_row '
             f'WHERE table_id=%s AND deleted=0 AND id IN ({ph})', tuple([tid] + want))
    return _json_response({'rows': _rows_view(tid, recs, columns(tid), project)})

class RowBulkReq(BaseModel):
    rows: list      # [{id?, data, ver?}]：带 id 走更新（ver 乐观锁），不带 id 就新建

@router.post("/api/tables/{tid}/rows/bulk")
def rows_bulk(tid: int, req: RowBulkReq, request: Request):
    """粘贴大块内容 / 清空选区 / 填充柄：一次请求改多行或补建缺少的行。
    某一行失败（版本冲突、值不合法）只把那一行单独返回，不影响其它行；新建的行排在表尾。"""
    t = _need(tid)
    need_table(tid, t.get('project') or '', write=True)
    items = req.rows if isinstance(req.rows, list) else []
    if not items or len(items) > 500:
        raise HTTPException(400, 'rows 需要 1~500 条')
    cols = _table_columns(tid, include_hidden=True)
    bykey = {c['key']: c for c in cols if c['type'] != 'formula'}
    actor = (AUTH_USER.get() or {}).get('username') or 'local'
    updated, conflicts, errors, created = [], [], [], []
    con = conn()
    try:
        cur = con.cursor()
        nxt = None
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                errors.append({'index': i, 'detail': '不是对象'})
                continue
            data = it.get('data')
            if not isinstance(data, dict):
                errors.append({'index': i, 'detail': 'data 必须是对象'})
                continue
            clean = {}
            try:
                for k, v in data.items():
                    if not isinstance(k, str):      # JSON 键一定是文本，这里只防手工构造的畸形请求
                        continue
                    c = bykey.get(k)
                    if c:
                        clean[c['key']] = norm_val(c, v)
            except (ValueError, TypeError) as e:
                errors.append({'index': i, 'detail': str(e)})
                continue
            raw_id = it.get('id')
            rid = None
            if raw_id not in (None, '', 0, '0', False):
                if isinstance(raw_id, bool) or not (
                        isinstance(raw_id, int)
                        or (isinstance(raw_id, str) and raw_id.isascii() and raw_id.isdigit())):
                    errors.append({'index': i, 'detail': 'id 必须是整数'})
                    continue
                rid = int(raw_id)
            if rid:
                cur.execute("SELECT data,ver FROM kb_row WHERE id=%s AND table_id=%s AND deleted=0",
                            (rid, tid))
                row = cur.fetchone()
                if not row:
                    errors.append({'index': i, 'id': rid, 'detail': '行不存在'})
                    continue
                ver = it.get('ver')
                if ver is not None and not isinstance(ver, bool):
                    try:
                        ver = int(ver)
                    except (TypeError, ValueError):
                        errors.append({'index': i, 'id': rid, 'detail': 'ver 必须是整数'})
                        continue
                elif isinstance(ver, bool):
                    errors.append({'index': i, 'id': rid, 'detail': 'ver 必须是整数'})
                    continue
                if ver is not None and ver != int(row['ver']):
                    conflicts.append({'index': i, 'id': rid, 'ver': row['ver']})
                    continue
                merged = jload(row['data'])
                merged.update(clean)
                cur.execute("UPDATE kb_row SET data=%s,ver=ver+1 WHERE id=%s AND table_id=%s",
                            (jdump(merged), rid, tid))
                updated.append({'id': rid, 'ver': int(row['ver']) + 1, 'data': merged})
            else:
                if nxt is None:
                    cur.execute("SELECT COALESCE(MAX(sort),0) m FROM kb_row WHERE table_id=%s", (tid,))
                    nxt = int((cur.fetchone() or {}).get('m') or 0)
                nxt += 1
                cur.execute("INSERT INTO kb_row(table_id,data,sort) VALUES(%s,%s,%s)",
                            (tid, jdump(clean), nxt))
                created.append({'id': cur.lastrowid, 'ver': 1, 'data': clean})
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()
    for r in updated:
        audit(tid, r['id'], 'update_row', {k: {'old': None, 'new': v} for k, v in r['data'].items()})
    for r in created:
        audit(tid, r['id'], 'create_row', {k: {'old': None, 'new': v} for k, v in r['data'].items()})
    kb_changes.bump(tid, 'update', [r['id'] for r in updated] + [r['id'] for r in created], actor)
    ids = [r['id'] for r in updated]
    if ids:
        try:
            kb_sync.source_ids(tid, ids)
        except Exception as e:
            print(f'[sync] bulk update hook failed: {e!r}', flush=True)
    request.state.kb_inserted = len(created)      # 缓存只失效这张表并补上行数
    return _json_response(_json_safe_numbers({'ok': True, 'updated': len(updated), 'created': created,
                                              'conflicts': conflicts, 'errors': errors}))

@router.post("/api/tables/{tid}/rows")
def create_row(tid: int, req: RowReq):
    _t = _need(tid)
    need_table(tid, _t.get('project') or '', write=True)
    cols = _table_columns(tid, include_hidden=True)
    bykey = {c['key']: c for c in cols if c['type'] != 'formula'}
    clean = {}
    for k, v in (req.data or {}).items():
        if k in bykey:
            try:
                clean[k] = norm_val(bykey[k], v)
            except ValueError as e:
                raise HTTPException(400, str(e))
    # 必填只在「往必填列写空值」时拦；新建空行放行（对齐多维表格习惯，先建行再填）
    if req.before_id and req.after_id:
        raise HTTPException(400, 'before_id 和 after_id 只能选一个')

    # 插入中间行要把后续 sort 整体后移 1；锚点行加 FOR UPDATE 后和 INSERT
    # 放在同一事务里，并发插入不会拿到重复 sort。表内 sort 本来就是连续唯一值。
    con = conn()
    try:
        cur = con.cursor()
        anchor = None
        if req.before_id or req.after_id:
            cur.execute("SELECT id,sort FROM kb_row WHERE id=%s AND table_id=%s AND deleted=0 FOR UPDATE",
                        (req.before_id or req.after_id, tid))
            anchor = cur.fetchone()
            if not anchor:
                raise HTTPException(404, '目标行不存在')
        if anchor:
            if req.before_id:
                new_sort = int(anchor['sort'])
                cur.execute("UPDATE kb_row SET sort=sort+1 WHERE table_id=%s AND sort>=%s",
                            (tid, new_sort))
            else:
                new_sort = int(anchor['sort']) + 1
                cur.execute("UPDATE kb_row SET sort=sort+1 WHERE table_id=%s AND sort>%s",
                            (tid, anchor['sort']))
        else:
            cur.execute("SELECT COALESCE(MAX(sort),0) m FROM kb_row WHERE table_id=%s", (tid,))
            new_sort = int((cur.fetchone() or {}).get('m') or 0) + 1
        cur.execute("INSERT INTO kb_row(table_id,data,sort) VALUES(%s,%s,%s)",
                    (tid, jdump(clean), new_sort))
        rid = cur.lastrowid
        con.commit()
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()

    # 新行在整张表里的 1-based 位置；前端据此直接跳到所在页
    pos = q("SELECT COUNT(*) c FROM kb_row WHERE table_id=%s AND deleted=0 "
            "AND (sort<%s OR (sort=%s AND id<%s))", (tid, new_sort, new_sort, rid), one=True)['c'] + 1
    audit(tid, rid, 'create_row', {k: {'old': None, 'new': v} for k, v in clean.items()})
    try:
        kb_sync.source_event(tid, rid, 'upsert')
    except Exception as e:
        print(f'[sync] create_row hook failed: {e!r}', flush=True)
    kb_changes.bump(tid, 'insert', [rid], ((AUTH_USER.get() or {}).get('username') or ''))
    return {'ok': True, 'id': rid, 'ver': 1, 'pos': pos}

@router.post("/api/tables/{tid}/rows/{rid}/move")
def move_row(tid: int, rid: int, req: RowMoveReq):
    """把一行拖到另一行前/后，或相对移动 ±1。行顺序是表数据的一部分（和表格软件一样，所有人可见）。
    在一个事务里锁住整张表的行，只重写位置发生变化的那一段 sort，保持连续、不产生空洞。"""
    _t = _need(tid)
    need_table(tid, _t.get('project') or '', write=True)
    given = [bool(req.before_id), bool(req.after_id), req.offset is not None]
    if sum(given) != 1:
        raise HTTPException(400, 'before_id / after_id / offset 必须且只能给一个')
    if req.offset is not None and req.offset not in (-1, 1):
        raise HTTPException(400, 'offset 只能是 -1 或 1')

    con = conn()
    try:
        cur = con.cursor()
        # 连软删的行一起锁：它们也占着 sort，重排时一起保持唯一。
        # 但“第几行 / 上移下移”只看可见行（和插入接口的 pos 口径一致）。
        cur.execute("SELECT id,sort,deleted FROM kb_row WHERE table_id=%s ORDER BY sort,id FOR UPDATE", (tid,))
        rows = cur.fetchall()
        all_ids = [r['id'] for r in rows]
        cur_sort = {r['id']: int(r['sort']) for r in rows}
        vis = [r['id'] for r in rows if not r['deleted']]
        if rid not in vis:
            raise HTTPException(404, '行不存在')
        old_vi = vis.index(rid)
        rest = [x for x in vis if x != rid]
        if req.offset is not None:
            tgt_vi = max(0, min(len(rest), old_vi + req.offset))
        else:
            anchor = req.before_id or req.after_id
            if anchor not in vis or anchor == rid:
                raise HTTPException(404, '目标行不存在')
            a_vi = rest.index(anchor)
            tgt_vi = a_vi if req.before_id else a_vi + 1
        if tgt_vi == old_vi:
            con.rollback()
            return {'ok': True, 'pos': old_vi + 1, 'moved': False}
        # 把“插到第 tgt_vi 个可见行之前”翻译成整张表（含软删行）里的插入位置
        old_i = all_ids.index(rid)
        rest_all = [x for x in all_ids if x != rid]
        ins_i = rest_all.index(rest[tgt_vi]) if tgt_vi < len(rest) else len(rest_all)
        new_all = list(rest_all)
        new_all.insert(ins_i, rid)
        lo, hi = min(old_i, ins_i), max(old_i, ins_i)
        base = cur_sort[new_all[lo - 1]] if lo > 0 else 0
        changed = 0
        for i in range(lo, hi + 1):
            ns = base + (i - lo) + 1
            if cur_sort[new_all[i]] != ns:
                cur.execute("UPDATE kb_row SET sort=%s WHERE id=%s", (ns, new_all[i]))
                changed += 1
        con.commit()
    except HTTPException:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()
    audit(tid, rid, 'move_row', {'from': old_vi + 1, 'to': tgt_vi + 1})
    kb_changes.bump(tid, 'move', [rid], ((AUTH_USER.get() or {}).get('username') or ''))
    _drop_rows(tid)
    invalidate_xt_table(tid)
    return {'ok': True, 'pos': tgt_vi + 1, 'moved': changed > 0}

@router.patch("/api/tables/{tid}/rows/{rid}")
def update_row(tid: int, rid: int, req: RowReq):
    _t = _need(tid)
    need_table(tid, _t.get('project') or '', write=True)
    cur = q("SELECT data,ver FROM kb_row WHERE id=%s AND table_id=%s AND deleted=0", (rid, tid), one=True)
    if not cur:
        raise HTTPException(404, '行不存在')
    if req.ver is None or int(req.ver) != int(cur['ver']):
        raise HTTPException(409, '这行已被别人改过，请刷新后重试')
    cols = _table_columns(tid, include_hidden=True)
    bykey = {c['key']: c for c in cols if c['type'] != 'formula'}
    old = jload(cur['data'])
    patch = {}
    for k, v in (req.data or {}).items():
        if k in bykey:
            try:
                patch[k] = norm_val(bykey[k], v)
            except ValueError as e:
                raise HTTPException(400, str(e))
    merged = {**old, **patch}
    for k, v in patch.items():
        c = bykey.get(k)
        if c and c['required'] and v in (None, '', []):
            raise HTTPException(400, f"列「{c['label']}」必填，不能清空")
    detail = {k: {'old': old.get(k), 'new': v} for k, v in patch.items() if old.get(k) != v}
    n = ex("UPDATE kb_row SET data=%s,ver=ver+1 WHERE id=%s AND ver=%s",
           (jdump(merged), rid, req.ver))
    if not n:
        # 上面的预检之后、这条 UPDATE 之前，有人改了同一行：什么都没存上，按冲突处理。
        # 不能当成功：否则会写出假的审计、把没保存的内容写进行缓存。
        raise HTTPException(409, '这行已被别人改过，请刷新后重试')
    # 图片单元格：旧图不再被引用时进回收站（软删）
    for c in cols:
        if c['type'] == 'image':
            for iid in set(_cell_iids(old.get(c['key']))) - set(_cell_iids(merged.get(c['key']))):
                _trash_image(iid)
    audit(tid, rid, 'update_row', detail)
    newver = int(req.ver) + 1
    try:
        _cache_row_update(tid, rid, merged, newver)
    except Exception:
        _ROWS.pop(tid, None)
    try:
        kb_sync.source_event(tid, rid, 'upsert')
    except Exception as e:
        print(f'[sync] update_row hook failed: {e!r}', flush=True)
    actor = ((AUTH_USER.get() or {}).get('username') or '')
    kb_changes.bump(tid, 'update', [rid], actor)
    # 总表（镜像行）被改：把映射字段的值回写子表。只有带链接的镜像行才回写，总表自己新增的行不动子表。
    back = 0
    if kb_sync.is_master(tid):
        try:
            back = kb_sync.reverse_update(tid, rid, merged,
                                          [k for k in patch if old.get(k) != patch[k]], actor=actor)
        except Exception as e:
            print(f'[sync] reverse_update failed: {e!r}', flush=True)
    # 传 project：跨表函数（SUMIF/LOOKUP…）只有带上它才可用，否则这一行里的跨表公式会算成空，
    # 用户刚改一格就看到同行的跨表列变成“—”，刷新才回来。
    # 跨表索引是按数据库里的行建的，本表这一行刚改过：引用本表的公式（COUNTIF 本表）要按新值算，
    # 所以先把本表的索引作废（中间件在响应之后还会再作废一次，重复无害）。
    invalidate_xt_table(tid)
    return {'ok': True, 'ver': newver, 'synced_back': back,
            'row': _json_safe_numbers(compute_formulas(merged, cols, _t.get('project') or ''))}

@router.delete("/api/tables/{tid}/rows/{rid}")
def delete_row(tid: int, rid: int):
    _t = _need(tid)
    need_table(tid, _t.get('project') or '', write=True)
    cur = q("SELECT data FROM kb_row WHERE id=%s AND table_id=%s AND deleted=0", (rid, tid), one=True)
    if not cur:
        raise HTTPException(404, '行不存在')
    ex("UPDATE kb_row SET deleted=1 WHERE id=%s", (rid,))
    # 行里引用的图片进回收站
    d = jload(cur['data'])
    for c in _table_columns(tid, include_hidden=True):
        if c['type'] == 'image':
            for iid in _cell_iids(d.get(c['key'])):
                _trash_image(iid)
    audit(tid, rid, 'delete_row', {'data': d})
    actor = ((AUTH_USER.get() or {}).get('username') or '')
    # 镜像行在总表被删：子表对应行一起软删（镜像语义）。不联动的话，下次全量同步会把这行又复制回来。
    if kb_sync.is_master(tid):
        try:
            kb_sync.reverse_delete(tid, rid, actor=actor)
        except Exception as e:
            print(f'[sync] reverse_delete failed: {e!r}', flush=True)
    kb_changes.bump(tid, 'delete', [rid], actor)
    try:
        kb_sync.source_event(tid, rid, 'delete')
    except Exception as e:
        print(f'[sync] delete_row hook failed: {e!r}', flush=True)
    return {'ok': True}
