-- 多维表格（开放版）DDL
-- 只建 mt_* 前缀的表。幂等：可重复执行。
-- 用法: mysql -h <主机> -u <用户> -p <库名> < backend/app/mtables/schema.sql
-- 与 8002 平台（eias）mt_* 表结构完全兼容：可直接把该库指过来沿用原数据。

CREATE TABLE IF NOT EXISTS `mt_table` (
  `id` INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  `name` VARCHAR(120) NOT NULL COMMENT '表名',
  `note` VARCHAR(500) NOT NULL DEFAULT '' COMMENT '说明',
  `created_by` VARCHAR(64) NOT NULL DEFAULT '' COMMENT '创建人',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `deleted` TINYINT NOT NULL DEFAULT 0 COMMENT '1=已删(软删)',
  KEY `idx_mt_table_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='多维表格-表定义';

CREATE TABLE IF NOT EXISTS `mt_column` (
  `id` INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  `table_id` INT UNSIGNED NOT NULL,
  `key` VARCHAR(64) NOT NULL COMMENT '字段键,行数据JSON的key',
  `label` VARCHAR(120) NOT NULL COMMENT '显示名',
  `type` VARCHAR(20) NOT NULL DEFAULT 'text' COMMENT 'text/number/date/select/multiselect/checkbox/user/image',
  `options` JSON NULL COMMENT '可选项(select/multiselect)',
  `required` TINYINT NOT NULL DEFAULT 0,
  `width` SMALLINT NOT NULL DEFAULT 0 COMMENT '列宽px,0=自适应',
  `sort` INT NOT NULL DEFAULT 0 COMMENT '列顺序',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY `uk_mt_col_key` (`table_id`, `key`),
  KEY `idx_mt_col_table` (`table_id`, `sort`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='多维表格-列定义';

CREATE TABLE IF NOT EXISTS `mt_row` (
  `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  `table_id` INT UNSIGNED NOT NULL,
  `data` JSON NOT NULL COMMENT '行数据 {列key: 值}',
  `data_text` TEXT GENERATED ALWAYS AS (json_unquote(`data`)) STORED COMMENT '生成列:JSON文本,供全文搜索',
  `ver` INT NOT NULL DEFAULT 1 COMMENT '乐观锁版本号',
  `sort` INT NOT NULL DEFAULT 0 COMMENT '行序',
  `created_by` VARCHAR(64) NOT NULL DEFAULT '',
  `updated_by` VARCHAR(64) NOT NULL DEFAULT '',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `deleted` TINYINT NOT NULL DEFAULT 0 COMMENT '1=已删(软删)',
  KEY `idx_mt_row_table` (`table_id`, `deleted`, `sort`),
  FULLTEXT KEY `ft_mt_row_data_text` (`data_text`) WITH PARSER ngram
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='多维表格-行数据';

CREATE TABLE IF NOT EXISTS `mt_audit` (
  `id` BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  `table_id` INT UNSIGNED NOT NULL COMMENT '0=系统级事件(用户管理等)',
  `row_id` BIGINT UNSIGNED NULL,
  `action` VARCHAR(20) NOT NULL COMMENT 'create_row/update_row/delete_row/create_table/create_column/update_column/delete_column/user_create/user_update/user_delete/import_rows/api_insert/...',
  `actor` VARCHAR(64) NOT NULL DEFAULT '' COMMENT '操作人',
  `detail` JSON NULL COMMENT '变更明细 {列key:{old,new}}',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY `idx_mt_audit_table` (`table_id`, `created_at`),
  KEY `idx_mt_audit_row` (`row_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='多维表格-变动历史(审计)';

CREATE TABLE IF NOT EXISTS `mt_user` (
  `id` INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  `username` VARCHAR(64) NOT NULL COMMENT '登录账号',
  `password_hash` VARCHAR(200) NOT NULL COMMENT 'scrypt/pbkdf2 格式串',
  `real_name` VARCHAR(64) NOT NULL DEFAULT '' COMMENT '姓名',
  `role` VARCHAR(20) NOT NULL DEFAULT 'staff' COMMENT 'admin=管理员 staff=可编辑用户',
  `status` TINYINT NOT NULL DEFAULT 1 COMMENT '1=启用 0=停用',
  `created_by` VARCHAR(64) NOT NULL DEFAULT '' COMMENT '创建人',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY `uk_mt_user` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='多维表格-用户(admin界面管理)';

CREATE TABLE IF NOT EXISTS `mt_apikey` (
  `id` INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  `table_id` INT UNSIGNED NOT NULL,
  `key_hash` CHAR(64) NOT NULL COMMENT 'sha256(明文key)，明文只在生成时显示一次',
  `key_prefix` CHAR(12) NOT NULL DEFAULT '' COMMENT '明文前缀(列表展示用)',
  `note` VARCHAR(120) NOT NULL DEFAULT '' COMMENT '用途备注(如：影刀到货机器人)',
  `created_by` VARCHAR(64) NOT NULL DEFAULT '',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `revoked` TINYINT NOT NULL DEFAULT 0 COMMENT '1=已作废',
  UNIQUE KEY `uk_mt_apikey_hash` (`key_hash`),
  KEY `idx_mt_apikey_table` (`table_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='多维表格-HTTP写入密钥(免登录插数据)';

-- 说明：开放版已去掉按表授权（原 mt_perm 不再使用）。若从旧库迁来存在 mt_perm 表，忽略即可。
