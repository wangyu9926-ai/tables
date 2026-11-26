# -*- coding: utf-8 -*-
"""数据库备份：用 mysqldump 导出到 backups/，只保留最近 N 份；导完做一次完整性检查。

用法（在项目目录里，先用向导或环境变量配好数据库连接）：

    python tools/backup_db.py                 # 备一次，保留最近 7 份
    python tools/backup_db.py --keep 14       # 保留 14 份
    python tools/backup_db.py --out D:/baks   # 放到别的目录

口令通过环境变量 MYSQL_PWD 传给 mysqldump，不出现在命令行、也不打印。
恢复步骤见 README「备份与恢复」；`python tools/backup_db.py --check 某份.gz` 可以只检查文件是否完整。
"""
import argparse
import datetime
import gzip
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def find_mysqldump():
    """找 mysqldump：环境变量 MYSQLDUMP > PATH > 常见安装位置（Windows / Linux / macOS）。"""
    cands = [os.environ.get('MYSQLDUMP') or '', 'mysqldump']
    cands += [r'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe',
              r'C:\Program Files\MySQL\MySQL Server 5.7\bin\mysqldump.exe',
              '/usr/bin/mysqldump', '/usr/local/bin/mysqldump', '/opt/homebrew/bin/mysqldump',
              '/usr/local/mysql/bin/mysqldump']
    for c in cands:
        if not c:
            continue
        exe = shutil.which(c) or (c if os.path.exists(c) else None)
        if exe:
            return exe
    return None


def tail_ok(path: Path) -> bool:
    """mysqldump 正常结束时会写一行『-- Dump completed』；gz 末尾没这行说明导出中途断了。"""
    try:
        with gzip.open(path, 'rt', encoding='utf-8', errors='replace') as f:
            tail = f.read()[-4000:]
        return 'Dump completed' in tail
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--keep', type=int, default=7, help='保留最近几份（默认 7）')
    ap.add_argument('--out', default=str(ROOT / 'backups'), help='备份目录（默认项目下的 backups/）')
    ap.add_argument('--check', default='', help='只检查一份已有备份是否完整')
    a = ap.parse_args()

    if a.check:
        p = Path(a.check)
        ok = p.exists() and tail_ok(p)
        print(('OK   ' if ok else '损坏 ') + str(p))
        sys.exit(0 if ok else 1)

    from backend import db as kbdb
    cfg = kbdb.config()
    dump = find_mysqldump()
    if not dump:
        print('找不到 mysqldump：装 MySQL 客户端，或设置环境变量 MYSQLDUMP 指向它')
        sys.exit(2)
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    raw = out_dir / f"{cfg['database']}-{stamp}.sql"
    gz = out_dir / f"{cfg['database']}-{stamp}.sql.gz"
    cmd = [dump, '--single-transaction', '--quick', '--no-tablespaces',
           '--default-character-set=utf8mb4', '--set-gtid-purged=OFF',
           '-h', str(cfg['host']), '-P', str(cfg['port']), '-u', cfg['user'], cfg['database']]
    env = dict(os.environ, MYSQL_PWD=str(cfg['password'] or ''))
    print(f'正在备份 {cfg["user"]}@{cfg["host"]}:{cfg["port"]}/{cfg["database"]} …')
    with open(raw, 'wb') as f:
        r = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, env=env)
    if r.returncode != 0:
        err = r.stderr.decode('utf-8', 'replace')
        # --set-gtid-purged 在老版本 / 部分云库上不认：退一步再试一次
        if 'set-gtid-purged' in err or 'unknown variable' in err.lower():
            cmd = [c for c in cmd if not c.startswith('--set-gtid-purged')]
            with open(raw, 'wb') as f:
                r = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, env=env)
        if r.returncode != 0:
            raw.unlink(missing_ok=True)
            print('备份失败：' + err.strip()[:400])
            sys.exit(1)
    with open(raw, 'rb') as f, gzip.open(gz, 'wb', compresslevel=6) as g:
        shutil.copyfileobj(f, g)
    raw.unlink(missing_ok=True)
    if not tail_ok(gz):
        print(f'备份文件不完整（没有 Dump completed 结尾）：{gz}')
        sys.exit(1)
    size = gz.stat().st_size
    print(f'完成：{gz}（{size / 1048576:.1f} MB）')

    olds = sorted(out_dir.glob('*.sql.gz'), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in olds[a.keep:]:
        p.unlink(missing_ok=True)
        print(f'  清理旧备份：{p.name}')
    print(f'当前保留 {min(len(olds), a.keep)} 份')


if __name__ == '__main__':
    main()
