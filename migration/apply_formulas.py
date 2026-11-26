# -*- coding: utf-8 -*-
"""把 6 个数据表的公式字段落成真正的 formula 列。

用户给的公式（字段名对应）：
  达人数据看板-抖音 / 红书：
    CPM = 合作金额*1000/视频观看次数（红书用阅读量）
    CPE = 合作金额/总互动
    总互动 = 点赞+评论（红书再加收藏）
    合作金额 = 商品成本+佣金+平台服务费
    视频ID = REGEXEXTRACT(发布链接, 数字/链接)
  抖音看后搜数据源：
    观看搜索率 = 看后搜商品曝光次数/视频观看次数
    曝光点击率 = 看后搜/看后搜商品曝光次数
  拍单状态跟进：
    订单号_重复计数 = COUNTIF(全表, 订单号, 当前订单号)
    达人昵称_重复计数 = COUNTIF(全表, 达人昵称, 当前昵称)
除法统一用 IFERROR(..., "#DIV/0!") 保留钉钉错误值。
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / 'backend'))
from db import conn  # noqa: E402

PROJECT = '牧马人商务合作看板'

FORMULAS = {
    '达人数据看板-抖音': {
        'CPM': 'IFERROR({合作金额}*1000/{视频观看次数}, "#DIV/0!")',
        'CPE': 'IFERROR({合作金额}/{总互动}, "#DIV/0!")',
        '总互动': '{点赞}+{评论}',
        '合作金额': '{商品成本}+{佣金}+{平台服务费}',
        '视频ID': 'IFERROR(REGEXEXTRACT({发布链接}, "\\\\d{15,}"), "#N/A")',
    },
    '达人数据看板-红书': {
        'CPM': 'IFERROR({合作金额}*1000/{阅读量}, "#DIV/0!")',
        'CPE': 'IFERROR({合作金额}/{总互动}, "#DIV/0!")',
        '总互动': '{点赞}+{评论}+{收藏}',
        '合作金额': '{商品成本}+{佣金}+{平台服务费}',
        '视频ID': 'IFERROR(REGEXEXTRACT({发布链接}, "https://[^\\\\s]+"), "#N/A")',
    },
    '抖音看后搜数据源': {
        '观看搜索率': 'IFERROR({看后搜商品曝光次数}/{视频观看次数}, "#DIV/0!")',
        '曝光点击率': 'IFERROR({看后搜}/{看后搜商品曝光次数}, "#DIV/0!")',
    },
    '拍单状态跟进': {
        '订单号_重复计数': 'IF(ISBLANK({订单号}),0,COUNTIF("拍单状态跟进·表格","订单号",{订单号}))',
        '达人昵称_重复计数': 'IF(ISBLANK({达人昵称}),0,COUNTIF("拍单状态跟进·表格","达人昵称",{达人昵称}))',
    },
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    with conn() as c:
        cur = c.cursor()
        cur.execute("SELECT id,name,group_name FROM kb_table "
                    "WHERE project=%s AND deleted=0 ORDER BY sort,id", (PROJECT,))
        tables = cur.fetchall()
        print('active tables:', len(tables))
        plan = []
        for t in tables:
            fs = FORMULAS.get(t['group_name'])
            if not fs:
                continue
            for label, expr in fs.items():
                cur.execute("SELECT id,type FROM kb_column WHERE table_id=%s AND label=%s AND deleted=0",
                            (t['id'], label))
                col = cur.fetchone()
                if not col:
                    print('  missing', t['name'], label)
                    continue
                plan.append((t['id'], t['name'], col['id'], label, col['type'], expr))
        for tid, tname, cid, label, old, expr in plan:
            print(f"  {tname} :: {label} ({old} -> formula)")
        if not args.apply:
            print('预检完成；加 --apply 才会改列定义。')
            return
        try:
            for tid, tname, cid, label, _old, expr in plan:
                cur.execute("UPDATE kb_column SET type='formula', expr=%s WHERE id=%s AND table_id=%s",
                            (expr, cid, tid))
                cur.execute("INSERT INTO kb_audit(table_id,row_id,action,actor,detail) "
                            "VALUES(%s,NULL,'update_column','migration',%s)",
                            (tid, json.dumps({'label': label, 'type': 'formula', 'expr': expr},
                                             ensure_ascii=False)))
            c.commit()
        except Exception:
            c.rollback()
            raise
        print('updated formulas:', len(plan))


if __name__ == '__main__':
    main()
