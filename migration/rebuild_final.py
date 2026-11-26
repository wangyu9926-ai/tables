# -*- coding: utf-8 -*-
"""最终迁移：17 个视图，视图名按用户矩阵，行按钉钉视图筛选，带显示格式。

分组 / 表名（17）：
  达人数据看板-抖音：表格1(全部) 表格2(郭诺言) 表格3(陈宇娇) 表格4(毛艳雯)
  达人建联表-抖音：  表格(全部) 表格1(郭诺言+抖音) 表格2(陈宇娇+抖音) 表格3(毛艳雯+抖音)
  达人数据看板-红书：表格(全部) 表格2(全部)
  达人建联表-红书：  表格(全部) 表格1(郭诺言)
  抖音看后搜数据源： 表格视图(全部) 表格(全部)
  拍单状态跟进：     表格(全部) 表格1(郭诺言) 表格2(毛艳雯)
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / 'backend'))

import build_views_spec as BV  # noqa: E402
from db import conn  # noqa: E402

DUMP = BV.B.DUMP
VIEWS = BV.B.VIEWS
PROJECT = BV.PROJECT

PLAN = [
    ('达人数据看板-抖音', [('qvGDAH2', '表格1'), ('hfeUc4R', '表格2'),
                             ('1maIyvt', '表格3'), ('D7BOk2a', '表格4')]),
    ('达人建联表-抖音', [('dkJtsR5', '表格'), ('Y9xpwrL', '表格1'),
                           ('X4RmcKU', '表格2'), ('YlMHkOK', '表格3')]),
    ('达人数据看板-红书', [('htKUukd', '表格'), ('TfGqq4t', '表格2')]),
    ('达人建联表-红书', [('KcWP8bz', '表格'), ('cweR38W', '表格1')]),
    ('抖音看后搜数据源', [('AzwfHsv', '表格视图'), ('Lrh1fza', '表格')]),
    ('拍单状态跟进', [('dU3vTNA', '表格'), ('jwNVdDu', '表格1'), ('2Z15910', '表格2')]),
]


def display_format(f):
    t = f.get('type')
    props = (((f.get('config') or {}).get('renderFieldConfig') or {})
             .get('props') or {})
    if t == 'date':
        return {'kind': 'date', 'pattern': props.get('format') or 'YYYY-MM-DD'}
    if t == 'number':
        return {'kind': 'number', 'precision': int(props.get('precision') or 0),
                'thousand': bool(props.get('thousandSeparator')),
                'percent': props.get('format') == 'percent'}
    if t == 'formula':
        fm = props.get('formatter') or {}
        if fm.get('type') != 'number':
            return None
        cfg = fm.get('config') or {}
        val = fm.get('value')
        prec = 0 if val == 'INT' else 1 if val == 'FLOAT_1' else 2 if val == 'FLOAT_2' else int(cfg.get('precision') or 0)
        return {'kind': 'number', 'precision': prec,
                'thousand': bool(cfg.get('thousandSeparator')),
                'percent': cfg.get('format') == 'percent' or fm.get('format') == 'percent'}
    return None


def labels_for_filter(field, value):
    enums = (((field.get('config') or {}).get('renderFieldConfig') or {})
             .get('props', {}).get('enums') or [])
    vals = value if isinstance(value, list) else [value]
    out = []
    for x in vals:
        e = next((e for e in enums if e.get('id') == x or e.get('value') == x), None)
        out.append((e or {}).get('label') or (e or {}).get('value') or x)
    return out


def row_matches(row, filters, by_id):
    for f in filters:
        field = by_id.get(f.get('fieldId'))
        if not field:
            return False
        raw = row['cells'].get(field['name'])
        arr = raw if isinstance(raw, list) else ([raw] if raw not in (None, '') else [])
        want = labels_for_filter(field, f.get('value'))
        if f.get('symbol') != 'EQ' or not any(x in arr for x in want):
            return False
    return True


def build():
    tables = []
    sort = 0
    for group, views in PLAN:
        sid = next(k for k, v in DUMP['sheets'].items() if v['name'] == group)
        sheet = DUMP['sheets'][sid]
        by_id = {f['id']: f for f in sheet['fields']}
        metas = BV.field_meta_for_sheet(sheet)
        row_label = {}
        for s2id, sh2 in DUMP['sheets'].items():
            primary = next((f for f in sh2['fields'] if f.get('isPrimary')), None)
            if not primary:
                continue
            m = {}
            for r in sh2['rows']:
                v = r['cells'].get(primary['name'])
                if isinstance(v, list):
                    v = '、'.join(BV.B.text(x) for x in v)
                m[r['id']] = BV.B.text(v)
            row_label[s2id] = m
        for vid, out_name in views:
            view = (VIEWS[sid]['views'] or {}).get(vid)
            if not view:
                raise SystemExit(f'找不到视图 {group} {vid}')
            order = view.get('columns') or []
            hidden_map = ((view.get('custom') or {}).get('hiddenFields') or {})
            width_map = ((view.get('custom') or {}).get('widthMap') or {})
            ids = [x for x in order if x in by_id]
            ids += [f['id'] for f in sheet['fields'] if f['id'] not in ids]
            columns, used_keys = [], set()
            for si, fid in enumerate(ids):
                meta = metas[fid]
                base = BV.B.slug(meta['label'])
                key, n = base, 2
                while key in used_keys:
                    key = f'{base}_{n}'
                    n += 1
                used_keys.add(key)
                width = width_map.get(fid)
                try:
                    width = int(round(float(width))) if width is not None else 0
                except Exception:
                    width = 0
                if width < 0 or width > 800:
                    width = 0
                fmt = display_format(by_id[fid])
                options = meta['option_colors'] if False else None
                if meta['type'] in ('select', 'multiselect'):
                    options = meta['options']
                elif fmt:
                    options = {'display_format': fmt}
                columns.append({
                    'key': key, 'label': meta['label'], 'type': meta['type'],
                    'source_type': meta['source_type'], 'source_id': fid,
                    'dataType': meta['dataType'], 'options': options,
                    'option_colors': meta['option_colors'], 'width': width,
                    'hidden': bool(hidden_map.get(fid) or fid not in order),
                    'sort': si,
                })
            filters = view.get('filter') or []
            rows = []
            for r in sheet['rows']:
                if filters and not row_matches(r, filters, by_id):
                    continue
                d = {}
                for col in columns:
                    f = by_id[col['source_id']]
                    v = BV.value_for(col, f, r['cells'].get(f['name']), row_label)
                    if v is not None:
                        d[col['key']] = v
                rows.append(d)
            sort += 1
            tables.append({
                'source_id': sid, 'group': group, 'view_id': vid,
                'name': group + '·' + out_name,
                'sort': sort, 'columns': columns, 'rows': rows,
                'filter_count': len(filters),
            })
    return tables


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    tables = build()
    print('tables', len(tables))
    for t in tables:
        print(f"  {t['sort']:2} {t['name']}  rows={len(t['rows'])} cols={len(t['columns'])} filters={t['filter_count']}")
    spec = {'project': PROJECT, 'tables': tables}
    (HERE / 'migration_final_spec.json').write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    if not args.apply:
        print('预检完成；加 --apply 才会重建。')
        return

    with conn() as c:
        cur = c.cursor()
        cur.execute("SELECT id,name FROM kb_table WHERE project=%s AND deleted=0 ORDER BY id", (PROJECT,))
        active = cur.fetchall()
        old_ids = set(range(204, 221))
        if {r['id'] for r in active} != old_ids:
            raise SystemExit('当前项目表不是上一版 17 表，停止：' + repr(sorted({r['id'] for r in active})))
        ids = [r['id'] for r in active]
        ph = ','.join(['%s'] * len(ids))
        try:
            cur.execute(f"DELETE FROM kb_row WHERE table_id IN ({ph})", tuple(ids))
            cur.execute(f"DELETE FROM kb_column WHERE table_id IN ({ph})", tuple(ids))
            cur.execute(f"DELETE FROM kb_audit WHERE table_id IN ({ph})", tuple(ids))
            cur.execute(f"DELETE FROM kb_sync WHERE target_tid IN ({ph}) OR source_tid IN ({ph})",
                        tuple(ids) + tuple(ids))
            cur.execute(f"DELETE FROM kb_table WHERE id IN ({ph})", tuple(ids))
            for t in tables:
                cur.execute("INSERT INTO kb_table(name,note,project,group_name,icon,sort) "
                            "VALUES(%s,%s,%s,%s,%s,%s)",
                            (t['name'], '', PROJECT, t['group'], '📄', t['sort']))
                tid = cur.lastrowid
                for col in t['columns']:
                    cur.execute(
                        "INSERT INTO kb_column(table_id,`key`,label,type,options,option_colors,"
                        "expr,width,sort,required,hidden) VALUES(%s,%s,%s,%s,%s,%s,NULL,%s,%s,0,%s)",
                        (tid, col['key'], col['label'], col['type'],
                         json.dumps(col['options'], ensure_ascii=False) if col['options'] is not None else None,
                         json.dumps(col['option_colors'], ensure_ascii=False) if col['option_colors'] else None,
                         col['width'], col['sort'], 1 if col['hidden'] else 0))
                params = [(tid, json.dumps(d, ensure_ascii=False, separators=(',', ':')), i + 1)
                          for i, d in enumerate(t['rows'])]
                for i in range(0, len(params), 200):
                    cur.executemany("INSERT INTO kb_row(table_id,data,sort) VALUES(%s,%s,%s)",
                                    params[i:i + 200])
                cur.execute("INSERT INTO kb_audit(table_id,row_id,action,actor,detail) "
                            "VALUES(%s,NULL,'import_new_table','migration',%s)",
                            (tid, json.dumps({'name': t['name'], 'view': t['view_id'],
                                              'rows': len(t['rows']), 'columns': len(t['columns']),
                                              'source': 'dingtalk-views'}, ensure_ascii=False)))
                print(f"  ok {t['name']} tid={tid} rows={len(t['rows'])}")
            c.commit()
        except Exception:
            c.rollback()
            raise
    print('done')


if __name__ == '__main__':
    main()
