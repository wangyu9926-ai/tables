# -*- coding: utf-8 -*-
"""扫描 xlsx 的条件格式规则 / 数据验证（钉钉胶囊可能以条件格式落盘）。"""
import sys, os
import openpyxl

root = sys.argv[1]
for fn in sorted(os.listdir(root)):
    if not fn.endswith('.xlsx'):
        continue
    wb = openpyxl.load_workbook(os.path.join(root, fn))
    print('=' * 70); print(fn)
    for ws in wb.worksheets:
        cf = ws.conditional_formatting
        n = 0
        for rng in cf:
            for rule in rng.rules:
                n += 1
                dxf = rule.dxf
                fill = None
                if dxf is not None and dxf.fill is not None:
                    f = dxf.fill
                    try:
                        fill = (f.fgColor.rgb if f.fgColor and f.fgColor.type=='rgb' else str(f.fgColor.value if f.fgColor else None),
                                f.bgColor.rgb if f.bgColor and f.bgColor.type=='rgb' else str(f.bgColor.value if f.bgColor else None))
                    except Exception as e:
                        fill = 'err'
                fontc = None
                if dxf is not None and dxf.font is not None and dxf.font.color is not None:
                    fontc = getattr(dxf.font.color, 'rgb', None) or str(dxf.font.color.value)
                if n <= 40:
                    print(f'  [cf] {ws.title} {rng.sqref} type={rule.type} formula={rule.formula} fill={fill} font={fontc}')
        dv = list(ws.data_validations.dataValidation) if ws.data_validations else []
        for v in dv[:20]:
            print(f'  [dv] {ws.title} {v.sqref} type={v.type} opts={v.formula1!r}')
