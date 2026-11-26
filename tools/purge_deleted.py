# -*- coding: utf-8 -*-
"""软删数据清理：把删了很久的行 / 变动历史 / 回收站图片物理删掉，避免库只增不减。

默认只统计不动手（先看清楚再删）：

    python tools/purge_deleted.py                 # 看看有多少（默认 90 天前）
    python tools/purge_deleted.py --days 30       # 30 天前
    python tools/purge_deleted.py --days 90 --yes # 真的删

删除顺序：镜像行的关联（kb_sync_link）→ 软删行 → 变动历史 → 回收站图片。
在项目目录里跑（用 backend/local.secrets.json 里的连接信息）；动手前先 `python tools/backup_db.py`。
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=90, help='保留最近几天的软删数据（默认 90）')
    ap.add_argument('--yes', action='store_true', help='真的执行删除（不加只统计）')
    a = ap.parse_args()
    if a.days < 1:
        print('--days 至少 1')
        sys.exit(2)
    from backend.engine import ex, q

    cut = f"NOW() - INTERVAL {int(a.days)} DAY"
    rows_n = q(f"SELECT COUNT(*) n FROM kb_row WHERE deleted=1 AND updated_at < {cut}", one=True)['n']
    links_n = q(f"SELECT COUNT(*) n FROM kb_sync_link l JOIN kb_row r ON r.id=l.source_rid "
                f"WHERE r.deleted=1 AND r.updated_at < {cut}", one=True)['n']
    audit_n = q(f"SELECT COUNT(*) n FROM kb_audit WHERE at < {cut}", one=True)['n']
    img_n = q(f"SELECT COUNT(*) n FROM kb_image WHERE deleted=1 AND deleted_at IS NOT NULL "
              f"AND deleted_at < {cut}", one=True)['n']
    img_b = q(f"SELECT COALESCE(SUM(size),0) s FROM kb_image WHERE deleted=1 AND deleted_at IS NOT NULL "
              f"AND deleted_at < {cut}", one=True)['s']
    print(f'早于 {a.days} 天前、可以物理清理的数据：')
    print(f'  软删的行       {int(rows_n):>8}')
    print(f'  这些行的镜像链接 {int(links_n):>8}')
    print(f'  变动历史       {int(audit_n):>8}')
    print(f'  回收站图片     {int(img_n):>8}（{int(img_b) / 1048576:.1f} MB）')
    if not a.yes:
        print('\n只统计，没有删除。确认无误后加 --yes 执行（先做一次备份）。')
        return
    if not (int(rows_n) or int(audit_n) or int(img_n)):
        print('\n没有需要清理的。')
        return
    ex(f"DELETE l FROM kb_sync_link l JOIN kb_row r ON r.id=l.source_rid "
       f"WHERE r.deleted=1 AND r.updated_at < {cut}")
    ex(f"DELETE FROM kb_row WHERE deleted=1 AND updated_at < {cut}")
    ex(f"DELETE FROM kb_audit WHERE at < {cut}")
    ex(f"DELETE FROM kb_image WHERE deleted=1 AND deleted_at IS NOT NULL AND deleted_at < {cut}")
    print('\n清理完成。建议再跑一次不带 --yes 的命令确认已归零。')


if __name__ == '__main__':
    main()
