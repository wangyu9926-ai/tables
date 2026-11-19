# -*- coding: utf-8 -*-
"""多维表格（开放版）—— 配置。全部从 backend/.env 读取，无外部依赖。"""
import os
from pathlib import Path

from dotenv import load_dotenv

_BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(_BASE_DIR / '.env')

APP_ENV = os.getenv('APP_ENV', 'dev').strip().lower()
IS_PRODUCTION = APP_ENV in ('prod', 'production')

PORT = int(os.getenv('PORT', '8002'))

# JWT：生产必须换掉默认密钥（至少 32 字符）
JWT_SECRET = os.getenv('JWT_SECRET', 'mtables-dev-secret-please-change-32')
JWT_ALG = 'HS256'
JWT_EXPIRE_HOURS = int(os.getenv('JWT_EXPIRE_HOURS', '8'))
if IS_PRODUCTION and (len(JWT_SECRET) < 32 or 'please-change' in JWT_SECRET):
    raise RuntimeError('生产环境(APP_ENV=prod)必须设置至少 32 字符的 JWT_SECRET')

# 管理员引导账号：首次启动时写入 mt_user（role=admin）。
# 之后改密码请在界面「用户管理」里改，重启不会覆盖密码。
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin').strip() or 'admin'
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123')

# 业务数据库（MySQL 5.7+ / 8.x / PolarDB，需 JSON 支持）
MT_DB_CONFIG = {
    'host': os.getenv('MT_DB_HOST', '127.0.0.1'),
    'port': int(os.getenv('MT_DB_PORT', '3306')),
    'user': os.getenv('MT_DB_USER', 'root'),
    'password': os.getenv('MT_DB_PASSWORD', ''),
}
MT_DB_NAME = os.getenv('MT_DB_NAME', 'mtables')
