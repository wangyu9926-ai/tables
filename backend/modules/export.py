# -*- coding: utf-8 -*-
"""导出：CSV / xlsx"""
from backend.deps import *          # noqa: F401,F403
from fastapi import APIRouter       # noqa: F401

router = APIRouter()

@router.get("/api/tables/{tid}/export")
def export_table(tid: int, fmt: str = ''):
    """导出：默认 CSV；fmt=xlsx 导出 Excel（表头加粗冻结、按列宽设定、公式导出算好的值）。"""
    t = _need(tid)
    need_table(tid, t.get('project') or '')
    cols = columns(tid)
    project = t.get('project') or ''
    if fmt == 'xlsx':
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, Alignment, PatternFill
            from openpyxl.utils import get_column_letter
        except ImportError:
            raise HTTPException(503, '服务器缺少 openpyxl，Excel 导出不可用（pip install -r requirements.txt）')
        wb = Workbook()
        ws = wb.active
        ws.title = ((t['name'].split('·')[1] if '·' in t['name'] else t['name']) or 'Sheet1')[:31]
        head_font = Font(bold=True)
        head_fill = PatternFill('solid', fgColor='F2F4F7')
        for j, c in enumerate(cols, 1):
            cell = ws.cell(row=1, column=j, value=c['label'])
            cell.font = head_font
            cell.fill = head_fill
            cell.alignment = Alignment(vertical='center')
            w = int(c.get('width') or 0)
            ws.column_dimensions[get_column_letter(j)].width = max(8, min(40, round(w / 7) if w else 14))
        ws.freeze_panes = 'A2'
        rw = 1
        for r in q("SELECT data FROM kb_row WHERE table_id=%s AND deleted=0 ORDER BY sort,id", (tid,)):
            rw += 1
            d = compute_formulas(jload(r['data']), cols, project)
            for j, c in enumerate(cols, 1):
                v = d.get(c['key'])
                if isinstance(v, list):
                    v = ','.join(str(x) for x in v)
                elif isinstance(v, bool):
                    v = '是' if v else (None if c['type'] == 'checkbox' else v)
                elif c['type'] == 'number' and v is not None:
                    try:
                        v = float(v) if '.' in str(v) else int(v)
                    except Exception:
                        pass
                elif v is not None and not isinstance(v, (int, float, str)):
                    v = str(v)
                ws.cell(row=rw, column=j, value=v)
        out = io.BytesIO()
        wb.save(out)
        fname = quote(f"{t['name']}.xlsx")
        return StreamingResponse(iter([out.getvalue()]),
                                 media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                                 headers={'Content-Disposition': f"attachment; filename*=UTF-8''{fname}"})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c['label'] for c in cols])
    for r in q("SELECT data FROM kb_row WHERE table_id=%s AND deleted=0 ORDER BY sort,id", (tid,)):
        d = compute_formulas(jload(r['data']), cols, project)
        w.writerow([_csv_cell(d.get(c['key'])) for c in cols])
    fname = quote(f"{t['name']}.csv")
    return StreamingResponse(iter([buf.getvalue()]), media_type='text/csv; charset=utf-8',
                             headers={'Content-Disposition': f"attachment; filename*=UTF-8''{fname}"})
