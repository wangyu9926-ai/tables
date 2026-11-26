# -*- coding: utf-8 -*-
"""把第一张总表的 5 个「看后搜」聚合列改成真正的 SUMIF 汇总公式。

钉钉字段配置：
  目标表：抖音看后搜数据源（Zi7wjuc）
  匹配：子表 视频ID = 总表 视频ID
  SUM：评论数 / 点赞数 / 视频观看次数 / 看后搜 / 收藏数
对应总表列：评论 / 点赞 / 视频观看次数 / 看后搜 / 收藏数
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
MASTER_GROUP = '达人数据看板-抖音'
CHILD = '抖音看后搜数据源·表格'
FIELDS = {
    '评论': '评论数',
    '点赞': '点赞数',
    '视频观看次数': '视频观看次数',
    '看后搜': '看后搜',
    '收藏数': '收藏数',
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    with conn() as c:
        cur = c.cursor()
        cur.execute("SELECT id,name FROM kb_table WHERE project=%s AND group_name=%s AND deleted=0 ORDER BY sort,id",
                    (PROJECT, MASTER_GROUP))
        masters = cur.fetchall()
        plan = []
        for t in masters:
            for label, child_field in FIELDS.items():
                cur.execute("SELECT id,type,options FROM kb_column WHERE table_id=%s AND label=%s AND deleted=0",
                            (t['id'], label))
                col = cur.fetchone()
                if not col:
                    print('missing', t['name'], label); continue
                expr = f'SUMIF("{CHILD}","视频ID",{{视频ID}},"{child_field}")'
                plan.append((t['id'], t['name'], col['id'], label, col['type'], expr))
        for tid, tname, cid, label, old, expr in plan:
            print(f'  {tname} :: {label} ({old} -> formula) {expr}')
        if not args.apply:
            print('预检完成；加 --apply 才写入。')
            return
        try:
            for tid, tname, cid, label, _old, expr in plan:
                cur.execute("SELECT options FROM kb_column WHERE id=%s", (cid,))
                r = cur.fetchone()
                opts = r['options'] if r else None
                if isinstance(opts, str):
                    try: opts = json.loads(opts)
                    except Exception: opts = None
                if not isinstance(opts, dict):
                    opts = {}
                opts['display_format'] = {'kind': 'number', 'precision': 0,
                                          'thousand': False, 'percent': False}
                cur.execute("UPDATE kb_column SET type='formula', expr=%s, options=%s WHERE id=%s",
                            (expr, json.dumps(opts, ensure_ascii=False), cid))
                cur.execute("INSERT INTO kb_audit(table_id,row_id,action,actor,detail) VALUES(%s,NULL,'update_column','migration',%s)",
                            (tid, json.dumps({'label': label, 'type': 'formula', 'expr': expr,
                                              'source': 'dingtalk filter-up'}, ensure_ascii=False)))
            c.commit()
        except Exception:
            c.rollback(); raise
        print('updated', len(plan))


if __name__ == '__main__':
    main()
