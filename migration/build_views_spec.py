# -*- coding: utf-8 -*-
"""按用户确认的 17 个视图生成迁移规格。

分组 / 视图（与用户给的矩阵一致）：
  达人数据看板-抖音：表格1 表格2 表格3 表格4   （不含旧的“表格”）
  达人建联表-抖音：  表格 表格1 表格2 表格3
  达人数据看板-红书：表格 表格2
  达人建联表-红书：  表格 表格1
  抖音看后搜数据源： 表格视图 表格
  拍单状态跟进：     表格 表格1 表格2
每个视图在 kb_table 里是一张 Sheet，name = 分组名·视图名。
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_spec as B  # noqa: E402

HERE = Path(__file__).resolve().parent
PROJECT = B.PROJECT
GROUP_ORDER = [
    '达人数据看板-抖音',
    '达人建联表-抖音',
    '达人数据看板-红书',
    '达人建联表-红书',
    '抖音看后搜数据源',
    '拍单状态跟进',
]
VIEW_PLAN = {
    '达人数据看板-抖音': ['kgib3Jy', 'hfeUc4R', '1maIyvt', 'D7BOk2a'],
    '达人建联表-抖音': ['dkJtsR5', 'Y9xpwrL', 'X4RmcKU', 'YlMHkOK'],
    '达人数据看板-红书': ['htKUukd', 'TfGqq4t'],
    '达人建联表-红书': ['KcWP8bz', 'cweR38W'],
    '抖音看后搜数据源': ['AzwfHsv', 'Lrh1fza'],
    '拍单状态跟进': ['dU3vTNA', 'jwNVdDu', '2Z15910'],
}


def clean_view_name(s):
    # 钉钉里是“表格 1”，用户矩阵写“表格1”。这里保留钉钉原名，避免和源数据对不上。
    return (s or '').strip()


def field_meta_for_sheet(sheet):
    """字段 id -> 列定义（类型/选项/颜色），与具体视图无关。"""
    out = {}
    for f in sheet['fields']:
        vals = [r['cells'].get(f['name']) for r in sheet['rows']]
        payload = B.field_payload(f)
        typ = payload['type']
        options = colors = None
        if typ in ('select', 'multiselect'):
            used = set()
            for v in vals:
                if isinstance(v, list):
                    used.update(B.text(x) for x in v if x not in (None, ''))
                elif v not in (None, '', {}):
                    used.add(B.text(v))
            options, colors = B.options_for(f, used)
            if not options:
                typ = 'text'
        elif payload['source_type'] in ('formula', 'filter-up'):
            nonempty = [B.formula_value(v) for v in vals if v not in (None, '', {})]
            nums = [B.num(v) for v in nonempty]
            typ = 'number' if (nonempty and all(x is not None for x in nums)) else 'text'
        elif typ == 'number':
            nonempty = [v for v in vals if v not in (None, '', {})]
            if nonempty and not all(B.num(v) is not None for v in nonempty):
                typ = 'text'
        out[f['id']] = {
            'label': f['name'],
            'type': typ,
            'source_type': payload['source_type'],
            'source_id': f['id'],
            'dataType': payload['dataType'],
            'options': options,
            'option_colors': colors,
            'config': f.get('config') or {},
        }
    return out


def value_for(col, f, v, row_label):
    """把一个钉钉单元格值转成本站落库值。返回 None 表示空。"""
    if v in (None, '', []):
        return None
    st = col['source_type']
    t = col['type']
    if st == 'file':
        v = B.file_text(v)
    elif st == 'link':
        v = B.link_text(v)
    elif st == 'association':
        ids = v if isinstance(v, list) else [v]
        names = []
        target = (((f.get('config') or {}).get('renderFieldConfig') or {})
                  .get('props', {}).get('sheetId'))
        for rid in ids:
            names.append(row_label.get(target, {}).get(rid) or B.text(rid))
        v = '、'.join(x for x in names if x)
    elif st == 'formula':
        v = B.formula_value(v)

    if t == 'number':
        v = B.num(v)
        if v is None:
            return None
    elif t == 'multiselect':
        if not isinstance(v, list):
            v = [B.text(v)]
        else:
            v = [B.text(x) for x in v if x not in (None, '')]
        if not v:
            return None
    elif t == 'date':
        v = B.text(v)[:10]
        if not v:
            return None
    elif t == 'checkbox':
        if not isinstance(v, bool):
            v = str(v).lower() in ('1', 'true', 'yes', 'on', '是')
    else:
        v = B.text(v)
        if v == '':
            return None
    if v in (None, '', []):
        return None
    return v


def main():
    tables = []
    sort = 0
    for group in GROUP_ORDER:
        sid = next(k for k, v in B.DUMP['sheets'].items() if v['name'] == group)
        sheet = B.DUMP['sheets'][sid]
        by_id = {f['id']: f for f in sheet['fields']}
        metas = field_meta_for_sheet(sheet)
        # 关联列解析用：目标 sheetId -> rowId -> 主字段显示值
        row_label = {}
        for s2id, sh2 in B.DUMP['sheets'].items():
            primary = next((f for f in sh2['fields'] if f.get('isPrimary')), None)
            if not primary:
                continue
            m = {}
            for r in sh2['rows']:
                v = r['cells'].get(primary['name'])
                if isinstance(v, list):
                    v = '、'.join(B.text(x) for x in v)
                m[r['id']] = B.text(v)
            row_label[s2id] = m
        # 每个视图一张表
        for view_id in VIEW_PLAN[group]:
            view = ((B.VIEWS.get(sid) or {}).get('views', {}) or {}).get(view_id)
            if not view:
                raise SystemExit(f'找不到视图 {group} {view_id}')
            order = view.get('columns') or []
            hidden_map = ((view.get('custom') or {}).get('hiddenFields') or {})
            width_map = ((view.get('custom') or {}).get('widthMap') or {})
            ids = [x for x in order if x in by_id]
            ids += [f['id'] for f in sheet['fields'] if f['id'] not in ids]
            columns = []
            used_keys = set()
            for si, fid in enumerate(ids):
                meta = metas[fid]
                base = B.slug(meta['label'])
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
                columns.append({
                    'key': key, 'label': meta['label'], 'type': meta['type'],
                    'source_type': meta['source_type'], 'source_id': fid,
                    'dataType': meta['dataType'], 'options': meta['options'],
                    'option_colors': meta['option_colors'], 'width': width,
                    'hidden': bool(hidden_map.get(fid) or fid not in order),
                    'sort': si,
                })
            # 行：所有视图共用同一份源数据
            rows = []
            for r in sheet['rows']:
                d = {}
                for col in columns:
                    f = by_id[col['source_id']]
                    v = value_for(col, f, r['cells'].get(f['name']), row_label)
                    if v is not None:
                        d[col['key']] = v
                rows.append(d)
            name = group + '·' + clean_view_name(view.get('name') or view_id)
            sort += 1
            tables.append({
                'source_id': sid, 'source_name': group, 'view_id': view_id,
                'view_name': view.get('name') or view_id,
                'name': name, 'group': group, 'sort': sort,
                'columns': columns, 'rows': rows,
            })
    # 名称唯一性检查
    names = [t['name'] for t in tables]
    assert len(names) == len(set(names)), '表名重复'
    for t in tables:
        labs = [c['label'] for c in t['columns']]
        assert len(labs) == len(set(labs)), f"{t['name']} 列名重复"
    spec = {'project': PROJECT, 'tables': tables}
    (HERE / 'migration_views_spec.json').write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    print('tables:', len(tables))
    for t in tables:
        print(f"  {t['sort']:2} {t['name']} cols={len(t['columns'])} rows={len(t['rows'])}")
    print('file:', HERE / 'migration_views_spec.json')


if __name__ == '__main__':
    main()
