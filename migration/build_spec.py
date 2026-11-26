# -*- coding: utf-8 -*-
"""把钉钉导出快照转换成 kb_* 迁移规格。

输入（同目录）：
  dingtalk_dump.json  六张表的字段定义 + 全部行（由浏览器端解码）
  dingtalk_views.json 视图配置（列顺序、宽度、隐藏列）

输出：
  migration_spec.json 给 apply_migration.py 直接执行
  migration_report.txt 供人工核对

原则：
  1. 表名、列名、列顺序、宽度、选项、选项颜色尽量保持钉钉原样。
  2. 钉钉有、我们引擎没有的列类型，降级成 text/number，值保持完整。
  3. 公式列不重算，直接落钉钉算好的值，保证迁移后看到的数字一致。
  4. 附件/关联列降级成文本：附件保留文件名，关联保留目标行主字段值。
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DUMP = json.loads((HERE / 'dingtalk_dump.json').read_text(encoding='utf-8'))
VIEWS = json.loads((HERE / 'dingtalk_views.json').read_text(encoding='utf-8'))

PROJECT = '牧马人商务合作看板'
GROUP = '牧马人商务合作看板'

# 钉钉视图里没有显式指定 sheet 的时候，用每张表最靠前的视图。
CHOSEN_VIEW = {
    'hERWDMS': 'hfeUc4R',   # 用户链接里带的 viewId
    'pfj3Sca': 'dkJtsR5',
    'Zi7wjuc': 'AzwfHsv',
    'MKoAGMh': 'dU3vTNA',
    'nJVlbIg': 'htKUukd',
    'roO3JWl': 'KcWP8bz',
}

# 钉钉 colorKey -> 本站 11 色盘下标
COLOR_MAP = {
    'blue_0': 0,
    'orange_0': 1,
    'purple_0': 2,
    'pink_0': 3,
    'pink_1': 3,
    'green_0': 4,
    'lime_0': 4,
    'lightblue_0': 6,
    'yellow_0': 7,
    'teal_0': 8,
    'red_0': 9,
    'grey_0': 10,
}


def slug(s):
    s = (s or '').strip()
    s = re.sub(r"[\s'\"`\\/]+", '_', s)
    return s[:120] or 'col'


def text(v):
    if v is None:
        return ''
    if isinstance(v, bool):
        return '是' if v else '否'
    return str(v)


def num(v):
    if v is None or v == '':
        return None
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip().replace(',', '').replace('，', '')
    m = re.fullmatch(r'(-?\d+(?:\.\d+)?)(亿|万|千|w|W|k|K)?%?', s)
    if not m:
        return None
    n = float(m.group(1))
    n *= {'亿': 1e8, '万': 1e4, 'w': 1e4, 'W': 1e4,
          '千': 1e3, 'k': 1e3, 'K': 1e3}.get(m.group(2) or '', 1)
    return int(n) if n.is_integer() else n


def formula_value(v):
    """钉钉公式结果 -> 可落库的标量。错误值保留成 #DIV/0! 之类。"""
    if isinstance(v, dict):
        ff = v.get('filterFactor')
        if isinstance(ff, list) and ff:
            return text(ff[0])
        if v.get('identifier') is not None:
            return text(v.get('identifier'))
        if v.get('data') not in (None, ''):
            return text(v.get('data'))
        return None
    if isinstance(v, list):
        if len(v) == 1:
            return formula_value(v[0])
        return '、'.join(text(x) for x in v)
    return v


def file_text(v):
    if not v:
        return ''
    if isinstance(v, str):
        return v
    # 钉钉 file 单元格：{data:'[{...}]', identifier:...}
    raw = v.get('data') if isinstance(v, dict) else None
    items = []
    if isinstance(raw, str):
        try:
            items = json.loads(raw)
        except Exception:
            items = []
    elif isinstance(raw, list):
        items = raw
    names = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict):
            names.append(it.get('name') or it.get('url') or it.get('resourceId') or '')
        elif it:
            names.append(text(it))
    if not names:
        ff = v.get('filterFactor') if isinstance(v, dict) else None
        if isinstance(ff, list):
            names = [text(x) for x in ff]
        elif ff:
            names = [text(ff)]
        elif isinstance(v, dict):
            names = [text(v.get('identifier') or '')]
    return '、'.join(x for x in names if x)


def link_text(v):
    """钉钉 link 单元格 -> URL 文本。

    钉钉的 link 值常见两层 JSON：
      {sequence, identifier, data:'{"url":"...","text":"..."}', filterFactor}
    真正可点的 URL 在 data.url 里；没有 url 时退回 sequence/identifier。
    """
    def walk(x, depth=0):
        if x is None or depth > 3:
            return ''
        if isinstance(x, str):
            s = x.strip()
            if s.startswith('{') or s.startswith('['):
                try:
                    return walk(json.loads(s), depth + 1)
                except Exception:
                    return s
            return s
        if isinstance(x, dict):
            for k in ('url', 'text', 'identifier', 'sequence', 'filterFactor'):
                if x.get(k) not in (None, ''):
                    got = walk(x.get(k), depth + 1)
                    if got:
                        return got
            return ''
        if isinstance(x, list):
            for it in x:
                got = walk(it, depth + 1)
                if got:
                    return got
        return text(x)

    return walk(v)


def field_payload(f):
    """返回给前端/引擎的列定义（不含 key/sort/width/hidden）。"""
    t = f['type']
    dt = f['dataType']
    if t in ('text', 'number', 'date', 'select', 'multiSelect', 'checkbox'):
        typ = {'multiSelect': 'multiselect'}.get(t, t)
    elif t == 'formula':
        typ = 'text'  # 下面按值再决定 number
    elif t == 'filter-up':
        typ = 'text'  # 下面按值再决定 number
    elif t == 'link':
        typ = 'text'
    elif t == 'file':
        typ = 'text'
    elif t == 'association':
        typ = 'text'
    else:
        typ = 'text'
    return {'type': typ, 'source_type': t, 'dataType': dt}


def options_for(f, used):
    enums = (((f.get('config') or {}).get('renderFieldConfig') or {})
             .get('props', {}).get('enums') or [])
    out, seen, colors = [], set(), {}
    for e in enums:
        label = e.get('label')
        if label is None or label == '':
            label = e.get('value')
        if label is None or label == '':
            continue
        label = str(label)
        # 过滤掉明显是误导入进来的长文本选项（未被任何单元格使用的）
        if label not in used and len(label) > 80:
            continue
        if label in seen:
            continue
        seen.add(label)
        out.append(label)
        ci = COLOR_MAP.get(e.get('colorKey'))
        if ci is not None:
            colors[label] = ci
    for label in used:
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out, colors


def main():
    all_tables = []
    report = []
    # 先建全量行索引，供关联列解析主字段
    row_label = {}
    for sid, sheet in DUMP['sheets'].items():
        fields = sheet['fields']
        primary = next((f for f in fields if f.get('isPrimary')), None)
        if not primary:
            continue
        m = {}
        for r in sheet['rows']:
            v = r['cells'].get(primary['name'])
            if isinstance(v, list):
                v = '、'.join(text(x) for x in v)
            m[r['id']] = text(v)
        row_label[sid] = m

    for idx, (sid, sheet) in enumerate(DUMP['sheets'].items()):
        view_id = CHOSEN_VIEW.get(sid)
        view = (VIEWS.get(sid) or {}).get('views', {}).get(view_id) or {}
        order = view.get('columns') or []
        hidden_map = ((view.get('custom') or {}).get('hiddenFields') or {})
        width_map = ((view.get('custom') or {}).get('widthMap') or {})
        by_id = {f['id']: f for f in sheet['fields']}
        # 视图列在前，没进视图的字段补在最后（隐藏），保证数据不丢
        ids = [x for x in order if x in by_id]
        ids += [f['id'] for f in sheet['fields'] if f['id'] not in ids]

        # 先算每列的原始值
        raw_cols = []
        for fid in ids:
            f = by_id[fid]
            vals = []
            for r in sheet['rows']:
                vals.append((r, r['cells'].get(f['name'])))
            raw_cols.append((f, vals, fid in order, bool(hidden_map.get(fid))))

        columns, keys = [], []
        used_keys = set()
        for sort_i, (f, vals, in_view, hidden) in enumerate(raw_cols):
            label = f['name']
            base = slug(label)
            key, n = base, 2
            while key in used_keys:
                key = f'{base}_{n}'
                n += 1
            used_keys.add(key)
            payload = field_payload(f)
            typ = payload['type']
            options, colors = None, None

            if typ in ('select', 'multiselect'):
                used = set()
                for _r, v in vals:
                    if isinstance(v, list):
                        used.update(text(x) for x in v if x not in (None, ''))
                    elif v not in (None, '', {}):
                        used.add(text(v))
                options, colors = options_for(f, used)
                if not options:
                    typ = 'text'
            elif payload['source_type'] in ('formula', 'filter-up'):
                # 数值型公式/引用型字段用 number；带错误值或文本的用 text
                nonempty = [formula_value(v) for _r, v in vals if v not in (None, '', {})]
                nums = [num(v) for v in nonempty]
                if nonempty and all(x is not None for x in nums):
                    typ = 'number'
                else:
                    typ = 'text'
            elif typ == 'number':
                nonempty = [v for _r, v in vals if v not in (None, '', {})]
                if nonempty and not all(num(v) is not None for v in nonempty):
                    typ = 'text'

            width = width_map.get(f['id'])
            try:
                width = int(round(float(width))) if width is not None else 0
            except Exception:
                width = 0
            if width < 0 or width > 800:
                width = 0
            columns.append({
                'key': key,
                'label': label,
                'type': typ,
                'source_type': payload['source_type'],
                'source_id': f['id'],
                'dataType': payload['dataType'],
                'options': options,
                'option_colors': colors,
                'width': width,
                'hidden': bool(hidden or not in_view),
                'sort': sort_i,
            })
            keys.append(key)
            report.append(
                f"{sheet['name']}\t{label}\t{payload['source_type']}->{typ}"
                f"\tview={'Y' if in_view else 'N'}\thidden={'Y' if (hidden or not in_view) else 'N'}"
                f"\tw={width}\topts={len(options or [])}"
            )

        # 落行
        rows = []
        for r in sheet['rows']:
            d = {}
            for col in columns:
                f = by_id[col['source_id']]
                v = r['cells'].get(f['name'])
                if v in (None, '', []):
                    continue
                t = col['type']
                st = col['source_type']
                if st == 'file':
                    v = file_text(v)
                elif st == 'link':
                    v = link_text(v)
                elif st == 'association':
                    ids2 = v if isinstance(v, list) else [v]
                    vals2 = []
                    for rid in ids2:
                        target = (((f.get('config') or {}).get('renderFieldConfig') or {})
                                  .get('props', {}).get('sheetId'))
                        vals2.append(row_label.get(target, {}).get(rid) or text(rid))
                    v = '、'.join(x for x in vals2 if x)
                elif st == 'formula':
                    v = formula_value(v)
                if t == 'number':
                    v = num(v)
                    if v is None:
                        continue
                elif t == 'multiselect':
                    if not isinstance(v, list):
                        v = [text(v)]
                    else:
                        v = [text(x) for x in v if x not in (None, '')]
                    if not v:
                        continue
                elif t == 'date':
                    v = text(v)[:10]
                    if not v:
                        continue
                elif t == 'checkbox':
                    if isinstance(v, bool):
                        pass
                    else:
                        v = str(v).lower() in ('1', 'true', 'yes', 'on', '是')
                else:
                    v = text(v)
                    if v == '':
                        continue
                if v not in (None, '', []):
                    d[col['key']] = v
            rows.append(d)
        all_tables.append({
            'source_id': sid,
            'name': sheet['name'],
            'view_id': view_id,
            'columns': columns,
            'rows': rows,
        })
        report.append(f"== {sheet['name']} rows={len(rows)} cols={len(columns)} view={view_id}")

    # 表名/列名重复检查
    names = [t['name'] for t in all_tables]
    assert len(names) == len(set(names)), '表名重复'
    for t in all_tables:
        labs = [c['label'] for c in t['columns']]
        assert len(labs) == len(set(labs)), f"{t['name']} 列名重复"

    spec = {'project': PROJECT, 'group': GROUP, 'tables': all_tables}
    (HERE / 'migration_spec.json').write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    (HERE / 'migration_report.txt').write_text('\n'.join(report) + '\n', encoding='utf-8')
    print('spec tables:', [(t['name'], len(t['columns']), len(t['rows'])) for t in all_tables])
    print('report:', HERE / 'migration_report.txt')


if __name__ == '__main__':
    main()
