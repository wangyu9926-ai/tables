# -*- coding: utf-8 -*-
"""清理旧版/废弃数据。

保留：
  - 项目「牧马人商务合作看板」及其 17 张未删除表（221~237）
  - 仪表盘分析页 id=33
  - 账号 kb_user / 授权 kb_perm

删除：
  - 旧版 muma_* 六张表（已备份）
  - kb_table.deleted=1 的旧表及其行/列/审计/图片/密钥/关联
  - 空项目「111」及其空表 252
  - 已删除项目「牧马人商务看板」「测试01」
  - 默认空看板 id=3 和测试项目残留看板
  - 指向已删表的 kb_relation / kb_perm 等残留
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / 'backend'))
from db import conn  # noqa: E402

ACTIVE_PROJECT = '牧马人商务合作看板'
ACTIVE_TABLE_IDS = set(range(221, 238))
KEEP_DASHBOARD_IDS = {33}
BACKUP = 'migration/backup_before_cleanup_20261124.sql.gz'


def chunks(xs, n=500):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    with conn() as c:
        cur = c.cursor()
        cur.execute("SHOW TABLES LIKE 'muma%%'")
        muma = [list(r.values())[0] for r in cur.fetchall()]

        cur.execute("SELECT id,name,project,deleted FROM kb_table ORDER BY id")
        tables = cur.fetchall()
        old_tids = [int(t['id']) for t in tables
                    if t['id'] not in ACTIVE_TABLE_IDS
                    and (t['deleted'] or t['project'] not in (ACTIVE_PROJECT,))]
        old_names = {int(t['id']): t['name'] for t in tables if int(t['id']) in old_tids}

        cur.execute("SELECT id,name,deleted FROM kb_project ORDER BY id")
        projects = cur.fetchall()
        old_projects = [p['name'] for p in projects
                        if p['name'] != ACTIVE_PROJECT
                        and (p['deleted'] or p['name'] in ('111',))]

        cur.execute("SELECT id,name,project,deleted FROM kb_dashboard ORDER BY id")
        dashboards = cur.fetchall()
        old_dash = [int(d['id']) for d in dashboards
                    if int(d['id']) not in KEEP_DASHBOARD_IDS
                    and (d['deleted'] or d['project'] != ACTIVE_PROJECT or int(d['id']) == 3)]

        print('muma tables:', muma)
        print('old kb tables:', len(old_tids), sorted(old_tids))
        print('old projects:', old_projects)
        print('old dashboards:', old_dash)
        if not args.apply:
            print('预检完成；加 --apply 才会删除。备份：', BACKUP)
            return

        manifest = {'backup': BACKUP, 'muma': muma, 'old_tables': old_names,
                    'old_projects': old_projects, 'old_dashboards': old_dash}
        try:
            # 1) muma_* 旧版
            for t in muma:
                cur.execute(f"DROP TABLE IF EXISTS `{t}`")

            # 2) 旧 kb 表的子表
            for group in chunks(old_tids):
                ph = ','.join(['%s'] * len(group))
                cur.execute(f"DELETE FROM kb_row WHERE table_id IN ({ph})", tuple(group))
                cur.execute(f"DELETE FROM kb_column WHERE table_id IN ({ph})", tuple(group))
                cur.execute(f"DELETE FROM kb_audit WHERE table_id IN ({ph})", tuple(group))
                cur.execute(f"DELETE FROM kb_image WHERE table_id IN ({ph})", tuple(group))
                cur.execute(f"DELETE FROM kb_apikey WHERE table_id IN ({ph})", tuple(group))
                cur.execute(f"DELETE FROM kb_relation WHERE from_tid IN ({ph}) OR to_tid IN ({ph})",
                            tuple(group) + tuple(group))
                cur.execute(f"DELETE FROM kb_sync_link WHERE sync_id IN "
                            f"(SELECT id FROM kb_sync WHERE target_tid IN ({ph}) OR source_tid IN ({ph}))",
                            tuple(group) + tuple(group))
                cur.execute(f"DELETE FROM kb_sync WHERE target_tid IN ({ph}) OR source_tid IN ({ph})",
                            tuple(group) + tuple(group))
                cur.execute(f"DELETE FROM kb_table WHERE id IN ({ph})", tuple(group))

            # 3) 已删项目 / 空测试项目
            for p in old_projects:
                cur.execute("DELETE FROM kb_project WHERE name=%s", (p,))

            # 4) 看板：只留 id=33
            for d in old_dash:
                cur.execute("DELETE FROM kb_dashboard WHERE id=%s", (d,))

            # 5) 其它残留
            active_list = tuple(sorted(ACTIVE_TABLE_IDS))
            ph = ','.join(['%s'] * len(active_list))
            cur.execute(f"DELETE FROM kb_relation WHERE from_tid NOT IN ({ph}) OR to_tid NOT IN ({ph})",
                        active_list + active_list)
            cur.execute(f"DELETE FROM kb_image WHERE table_id NOT IN ({ph})", active_list)
            cur.execute(f"DELETE FROM kb_apikey WHERE table_id NOT IN ({ph})", active_list)
            cur.execute("DELETE FROM kb_perm WHERE scope_type='project' AND scope_key<>%s",
                        (ACTIVE_PROJECT,))
            # 表级授权只保留活动表
            cur.execute("SELECT id,scope_key FROM kb_perm WHERE scope_type='table'")
            for r in cur.fetchall():
                try:
                    if int(r['scope_key']) not in ACTIVE_TABLE_IDS:
                        cur.execute("DELETE FROM kb_perm WHERE id=%s", (r['id'],))
                except Exception:
                    cur.execute("DELETE FROM kb_perm WHERE id=%s", (r['id'],))
            c.commit()
        except Exception:
            c.rollback()
            raise

        (HERE / 'cleanup_manifest_20261124.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        print('cleanup done; manifest:', HERE / 'cleanup_manifest_20261124.json')


if __name__ == '__main__':
    main()
