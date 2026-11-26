# -*- coding: utf-8 -*-
"""扫描钉钉导出 xlsx 的单元格填充色，找出哪些列/值在钉钉里是彩色胶囊。"""
import sys, os, collections
import openpyxl

root = sys.argv[1]
for fn in sorted(os.listdir(root)):
    if not fn.endswith('.xlsx'):
        continue
    path = os.path.join(root, fn)
    wb = openpyxl.load_workbook(path, data_only=True)
    print('=' * 70)
    print(fn)
    for ws in wb.worksheets:
        header = [c.value for c in ws[1]]
        style = collections.Counter()
        bycol = collections.defaultdict(collections.Counter)
        for row in ws.iter_rows(min_row=2):
            for i, cell in enumerate(row):
                f = cell.fill
                if f is None or f.patternType != 'solid':
                    continue
                rgb = None
                try:
                    rgb = f.fgColor.rgb if f.fgColor.type == 'rgb' else None
                except Exception:
                    pass
                if not rgb or rgb in ('00000000', 'FFFFFFFF'):
                    continue
                hdr = str(header[i]) if i < len(header) else f'col{i}'
                style[rgb] += 1
                bycol[hdr][rgb] += 1
        if not style:
            continue
        print(f'  [sheet] {ws.title}  rows={ws.max_row-1}')
        for hdr, cnt in bycol.items():
            print(f'    {hdr}: ' + ', '.join(f'{c}×{n}' for c, n in cnt.most_common()))
