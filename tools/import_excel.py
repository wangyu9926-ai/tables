# -*- coding: utf-8 -*-
"""把 Excel（.xlsx）写进多维表：走应用的写入接口 `POST /api/in/{表ID}`（表级写入密钥）。

为什么不用直连库：走接口会按列类型校验、写变动历史、触发汇总同步、让缓存和实时刷新生效；
直连库这些都没有，还会因为进程内缓存导致界面显示旧数据。

一、先在界面拿两样东西
  1) 表 ID：地址/接口里那张表；或「写入密钥」弹窗里的 curl 命令里能看到
  2) 写入密钥：表页顶栏「写入密钥」→ 生成（只显示一次，形如 kbk_xxx）

二、用法
  # 只看会写什么，不真的写
  python tools/import_excel.py 数据.xlsx --tid 12 --token kbk_xxx --dry-run

  # 真写（本机自签证书加 --insecure；有域名正式证书就不需要）
  python tools/import_excel.py 数据.xlsx --tid 12 --token kbk_xxx --insecure

  # 常用参数
  --sheet 名称 / --sheet 0      指定工作表（默认第一个）
  --header-row 1               表头在第几行（默认 1）
  --only 名称,数量              只导这几列（其余忽略）
  --skip 备注                   跳过这几列
  --multi 标签,渠道             这几列按逗号/顿号/分号拆成多选数组
  --batch 200                  每批行数（接口上限 1000）
  --limit 50                   只导前 50 行（先试水的）
  --fail-out 失败.csv           失败的行写到这个文件
  --no-idem                    关闭幂等键（默认开：同一文件重跑不会重复插入）

三、注意
  - Excel 表头要和表里的**列名（显示名）完全一致**；对不上的列会被服务端忽略（这是接口行为），
    脚本会打印表头清单，导入后请在界面抽查一眼。也可以用 --columns-file 列出允许的列名，脚本会先警告。
  - 公式列、图片列不参与导入；必填列空了会被拒绝（只影响那一行，脚本会把它记进失败文件）。
  - 单选框的值必须在选项里；多选请用 --multi 指定列，或在单元格里写 JSON 数组：["A","B"]。
  - 数字统一按文本发送（服务端按列类型解析），18 位单号不会掉精度。
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time

try:
    import requests
except ImportError:
    sys.exit('缺少 requests：pip install -r requirements.txt')

try:
    import openpyxl
except ImportError:
    sys.exit('缺少 openpyxl：pip install -r requirements.txt')

MULTI_SEP = re.compile(r'[,，、;；]+')
DATE_SHAPE = re.compile(r'^(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?')


def parse_args():
    ap = argparse.ArgumentParser(description='把 Excel 写进多维表（走 /api/in 写入接口）',
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument('excel', help='.xlsx 文件路径')
    ap.add_argument('--tid', type=int, default=os.environ.get('KB_TID') and int(os.environ['KB_TID']),
                    help='目标表 ID')
    ap.add_argument('--token', default=os.environ.get('KB_TOKEN'), help='表级写入密钥 kbk_...')
    ap.add_argument('--base', default=os.environ.get('KB_BASE_URL', 'https://127.0.0.1:8614'),
                    help='应用地址（默认 https://127.0.0.1:8614）')
    ap.add_argument('--sheet', default='0', help='工作表名或序号（默认第一个）')
    ap.add_argument('--header-row', type=int, default=1, help='表头在第几行（默认 1）')
    ap.add_argument('--only', default='', help='只导这些列（逗号分隔）')
    ap.add_argument('--skip', default='', help='跳过这些列（逗号分隔）')
    ap.add_argument('--multi', default='', help='这些列按分隔符拆成多选数组（逗号分隔）')
    ap.add_argument('--dates', default='', help='这些列把 2026/11/3、2026.11.3、2026年11月3日 统一成 2026-11-03')
    ap.add_argument('--columns-file', default='', help='允许的列名清单（每行一个），用来先检查表头')
    ap.add_argument('--batch', type=int, default=200, help='每批行数（1~1000，默认 200）')
    ap.add_argument('--limit', type=int, default=0, help='最多导前 N 行（0=全部）')
    ap.add_argument('--dry-run', action='store_true', help='只打印将要写入的内容，不发请求')
    ap.add_argument('--no-idem', action='store_true', help='关闭幂等键')
    ap.add_argument('--fail-out', default='', help='失败行输出 CSV 路径')
    ap.add_argument('--insecure', action='store_true', help='跳过证书校验（本机自签 HTTPS 用）')
    a = ap.parse_args()
    if not a.tid:
        ap.error('需要 --tid（目标表 ID）')
    if not a.token:
        ap.error('需要 --token（表级写入密钥，界面「写入密钥」里生成）')
    a.batch = max(1, min(1000, a.batch))
    return a


def split_list(s):
    return [x.strip() for x in (s or '').split(',') if x.strip()]


def cell_string(v):
    """单元格 → 字符串/列表/bool/None。数字一律转文本，避免浮点把长单号弄丢精度。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        if isinstance(v, dt.datetime):
            if v.hour == 0 and v.minute == 0 and v.second == 0:
                return v.strftime('%Y-%m-%d')
            return v.strftime('%Y-%m-%d %H:%M:%S')
        if isinstance(v, dt.date):
            return v.strftime('%Y-%m-%d')
        return v.strftime('%H:%M:%S')
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return format(v, '.15g')
    s = str(v).strip()
    return s or None


def looks_like_json_list(s):
    if not (isinstance(s, str) and s.startswith('[') and s.endswith(']')):
        return None
    try:
        arr = json.loads(s)
    except Exception:
        return None
    return [str(x) for x in arr] if isinstance(arr, list) else None


def load_rows(a):
    wb = openpyxl.load_workbook(a.excel, data_only=True, read_only=True)
    if a.sheet.isdigit():
        ws = wb.worksheets[int(a.sheet)]
    else:
        if a.sheet not in wb.sheetnames:
            sys.exit('工作表不存在：%s（有：%s）' % (a.sheet, ', '.join(wb.sheetnames)))
        ws = wb[a.sheet]

    rows = ws.iter_rows(min_row=a.header_row, values_only=True)
    try:
        header = next(rows)
    except StopIteration:
        sys.exit('工作表是空的')

    headers = []
    seen = {}
    for i, h in enumerate(header):
        name = str(h).strip() if h is not None else ''
        if not name:
            continue
        if name in seen:                       # 重名列：只取第一列
            print('  ! 表头重复，忽略后面的「%s」' % name)
            continue
        seen[name] = i
        headers.append((name, i))

    only, skip, multi = split_list(a.only), split_list(a.skip), split_list(a.multi)
    dates = split_list(a.dates)
    if only:
        missing = [n for n in only if n not in seen]
        if missing:
            sys.exit('--only 里的列在 Excel 里找不到：%s' % ', '.join(missing))
        headers = [(n, i) for n, i in headers if n in only]
    if skip:
        headers = [(n, i) for n, i in headers if n not in skip]

    if a.columns_file:
        allowed = {x.strip() for x in open(a.columns_file, encoding='utf-8') if x.strip()}
        unknown = [n for n, _ in headers if n not in allowed]
        if unknown:
            print('  ! 这些表头不在 --columns-file 清单里（建议核对，服务端会忽略未知列）：%s' % ', '.join(unknown))

    data = []
    for rownum, row in enumerate(rows, start=a.header_row + 1):
        item = {}
        for name, idx in headers:
            v = cell_string(row[idx]) if idx < len(row) else None
            if v is None:
                continue                        # 空单元格不发送：交给服务端的必填校验/默认值
            if isinstance(v, str):
                jl = looks_like_json_list(v)      # 单元格里写了 ["A","B"] 就按数组发
                if jl is not None:
                    v = jl
                elif name in dates:               # 文本形式的日期统一成 YYYY-MM-DD
                    m = DATE_SHAPE.match(v)
                    v = '%04d-%02d-%02d' % tuple(int(x) for x in m.groups()) if m else v
                    if v is None:
                        continue
                if isinstance(v, str) and name in multi:
                    parts = [x.strip() for x in MULTI_SEP.split(v) if x.strip()]
                    if not parts:
                        continue
                    v = parts
            item[name] = v
        if not item:                            # 整行都空：跳过
            continue
        data.append((rownum, item))
        if a.limit and len(data) >= a.limit:
            break
    wb.close()
    return headers, data


def post_batch(session, a, rows, idem_key):
    headers = {'X-KB-Token': a.token}
    if idem_key:
        headers['X-Idempotency-Key'] = idem_key
    url = a.base.rstrip('/') + '/api/in/%d' % a.tid
    last = None
    for attempt in range(4):
        try:
            r = session.post(url, json={'rows': rows}, headers=headers, timeout=60)
        except requests.RequestException as e:
            last = '网络错误：%s' % e
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 200:
            return 'ok', r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            last = 'HTTP %d：%s' % (r.status_code, r.text[:160])
            time.sleep(1.5 * (attempt + 1))
            continue
        return 'bad', (r.status_code, r.text[:300])
    return 'bad', (0, last)


def main():
    a = parse_args()
    if not os.path.exists(a.excel):
        sys.exit('文件不存在：%s' % a.excel)
    if not a.token.startswith('kbk_'):
        print('  ! 密钥看起来不是 kbk_ 开头，确认没拿错（用「写入密钥」里那个）')

    headers, data = load_rows(a)
    print('文件：%s' % a.excel)
    print('表头（%d 列）：%s' % (len(headers), ', '.join(n for n, _ in headers)))
    print('数据行：%d 行（第 %d 行起）' % (len(data), a.header_row + 1))
    if data:
        print('首行预览：%s' % json.dumps(data[0][1], ensure_ascii=False)[:300])
        print('末行预览：%s' % json.dumps(data[-1][1], ensure_ascii=False)[:300])

    if a.dry_run or not data:
        print('\n--dry-run：没有发任何请求。确认无误后去掉 --dry-run 再跑。')
        return 0

    session = requests.Session()
    session.verify = not a.insecure
    if a.insecure:
        try:
            import urllib3
            urllib3.disable_warnings()
        except Exception:
            pass

    idem_prefix = ''
    if not a.no_idem:
        import hashlib
        h = hashlib.sha256(open(a.excel, 'rb').read()).hexdigest()[:12]
        idem_prefix = 'xlsx:%s:%s' % (h, a.sheet)

    ok_rows, fail_rows, replayed = 0, [], 0
    total = len(data)
    t0 = time.time()
    for start in range(0, total, a.batch):
        chunk = data[start:start + a.batch]
        payload = [item for _, item in chunk]
        idem = ('%s:b%d' % (idem_prefix, start // a.batch)) if idem_prefix else ''
        status, res = post_batch(session, a, payload, idem)

        if status == 'ok':
            if res.get('replayed'):
                replayed += len(chunk)
                print('  第 %d~%d 行：已存在（幂等键命中，跳过）' % (chunk[0][0], chunk[-1][0]))
            else:
                ok_rows += len(chunk)
                print('  第 %d~%d 行：写入 %s 行  [%d/%d, %.0f 行/秒]'
                      % (chunk[0][0], chunk[-1][0], res.get('inserted'), ok_rows + replayed, total,
                         (ok_rows + replayed) / max(0.001, time.time() - t0)))
            continue

        code, text = res
        if code in (401, 403):
            sys.exit('写入被拒绝（HTTP %d）：检查密钥是否作废、是不是这张表的密钥、表 ID 对不对。\n  %s' % (code, text))
        if code == 400 and len(chunk) > 1:
            # 整批被拒：拆成单行重试，把坏行挑出来，别让一行毁掉一批
            print('  第 %d~%d 行整批被拒，逐行重试定位问题…' % (chunk[0][0], chunk[-1][0]))
            for rownum, item in chunk:
                st2, res2 = post_batch(session, a, [item], ('%s:r%d' % (idem_prefix, rownum)) if idem_prefix else '')
                if st2 == 'ok':
                    if res2.get('replayed'):
                        replayed += 1
                    else:
                        ok_rows += 1
                else:
                    c2, t2 = res2
                    fail_rows.append((rownum, t2 if c2 == 0 else 'HTTP %d：%s' % (c2, t2), item))
                    print('    ! 第 %d 行失败：%s' % (rownum, (t2 if c2 == 0 else 'HTTP %d：%s' % (c2, t2))[:120]))
            continue
        fail_rows.append((chunk[0][0], text if code == 0 else 'HTTP %d：%s' % (code, text), payload[0] if payload else {}))
        print('  ! 第 %d 行起这一批失败：%s' % (chunk[0][0], text if code == 0 else 'HTTP %d：%s' % (code, text)))

    print('\n完成：写入 %d 行，幂等跳过 %d 行，失败 %d 行，用时 %.1f 秒'
          % (ok_rows, replayed, len(fail_rows), time.time() - t0))
    if fail_rows and a.fail_out:
        with open(a.fail_out, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(['Excel 行号', '错误', '数据'])
            for rownum, err, item in fail_rows:
                w.writerow([rownum, err, json.dumps(item, ensure_ascii=False)])
        print('失败明细已写到：%s' % a.fail_out)
    elif fail_rows:
        print('（失败行没有输出文件；加 --fail-out 失败.csv 可以把它们导出来改）')
    print('请在界面确认行数/抽查几行；公式列由服务端自动算。')
    return 1 if fail_rows else 0


if __name__ == '__main__':
    sys.exit(main())
