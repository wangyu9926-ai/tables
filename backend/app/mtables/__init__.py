# -*- coding: utf-8 -*-
"""多维表格模块（开放版）—— 独立模块包。

权限模型（2026-11 用户确认）：
  1. 走 .env 指定 MySQL 的 MT_DB_NAME 库，只用 mt_* 表。
  2. 权限控制已简化（去掉按表授权 mt_perm）：
       游客（未登录）—— 所有表只读（能看不能改）；
       登录用户 —— admin 在「用户管理」里添加的账号，所有表可增改删行、可看变动历史；
       管理员 —— 首次启动由 .env ADMIN_USERNAME/ADMIN_PASSWORD 引导创建，
                 额外可建表/删表/管列/导入 Excel/管用户/HTTP密钥/DB视图。
  3. 一切写操作落 mt_audit（变动历史）：谁、何时、哪张表哪行、哪个字段 旧值→新值。

表结构（6 张，DDL 见 schema.sql）：
  mt_table  —— 表定义
  mt_column —— 列定义（text/number/date/select/multiselect/checkbox/user/image）
  mt_row    —— 行数据（data JSON + ver 乐观锁）
  mt_user   —— 用户（admin/staff；admin 界面管理）
  mt_audit  —— 变动历史/审计日志
  mt_apikey —— HTTP 写入密钥（影刀/脚本免登录插数据）

注意：router 不在本 __init__ 里导入（auth_sessions → database → 本包 → router → deps
会成环）；main.py 直接 from .mtables.router import router。
"""
