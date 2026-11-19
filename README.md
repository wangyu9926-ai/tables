# 多维表格（开放版）

从 8002 平台（eias-data-platform）拉出来的独立多维表格应用，权限模型按 2026-11 需求重做：

> **游客（未登录）能看不能改；只有 admin 能加用户；admin 加的用户可改表；表格变动历史全都有。**

技术栈：FastAPI + MySQL(JSON) + Vue3（本地文件，无需 npm 构建）。

## 当前部署要点

- **端口**：8555（`backend/.env` 的 PORT，可改）
- **数据库**：MySQL 5.7+/8.x/PolarDB 一个库（需 JSON 类型支持），连接信息配在 `backend/.env`（**该文件不入库**）。
  可以与旧平台共用同一个多维表库：mt_* 表结构完全兼容，把 `MT_DB_*` 指过去即可，原表/行/变动历史直接沿用。
- **管理员**：首次启动由 `.env` 的 ADMIN_USERNAME / ADMIN_PASSWORD 自动创建，登录后到「用户管理」改密码。

## 权限模型（本版核心）

| 身份 | 怎么来 | 能做什么 |
|---|---|---|
| 游客 | 打开网页即是 | 看所有表（列表/数据/搜索/图片），**不能改** |
| 可编辑用户 | **仅 admin** 在「用户管理」里添加 | 所有表 增/改/删行、单元格双击编辑、看变动历史 |
| 管理员（admin） | 首次启动由 `.env` 的 ADMIN_USERNAME/ADMIN_PASSWORD 自动创建 | 以上全部 + 建表/删表/管列/导入 Excel/管用户/HTTP 写入密钥/DB视图 |

- 原「按表授权（mt_perm）」体系已去掉：所有人（含游客）可见全部表。
- 改密码/停用账号 → 该用户会话立刻失效（下线）。
- 一切写操作（含影刀 API 写入）都落 **变动历史**：谁、何时、哪张表哪行、哪个字段 旧值→新值。

## 目录结构

```
├── README.md / requirements.txt / .env.example
├── start.bat / restart.bat        Windows 一键启动 / 重启
├── backend/
│   ├── run.py                     启动入口（PORT 读 .env，默认 8555）
│   └── app/
│       ├── config.py              全部配置（读 backend/.env）
│       ├── auth.py / deps.py / auth_sessions.py / database.py
│       │                          多用户登录（JWT + sqlite 会话 + 实时账号核验）
│       ├── main.py                入口（无全局登录墙：读接口游客可用）
│       └── mtables/               多维表格模块（router / store / schema.sql）
└── frontend/                      纯静态（index.html + css + js，无需构建）
    └── js/pages/mtables.js        页面本体（Vue 组件）
```

## 部署步骤（Windows）

1. **装 Python 3.10+**（勾选 Add to PATH），命令行验证：`python --version`
2. **装依赖**（在本目录执行）：
   ```
   pip install -r requirements.txt
   ```
3. **建数据库**：在 MySQL 里建一个库（如 `mtables`），导入表结构：
   ```
   mysql -h <主机> -u <用户> -p <库名> < backend/app/mtables/schema.sql
   ```
   要求 MySQL 5.7+ / 8.x / PolarDB（需要 JSON 类型；8.x 全文搜索用 ngram）。
   账号需要该库的 CREATE / ALTER / DROP 权限（DB 视图功能用）。
4. **配置**：复制 `.env.example` 为 `backend/.env`，改四处：
   - `MT_DB_*`：数据库地址账号
   - `ADMIN_USERNAME` / `ADMIN_PASSWORD`：**管理员引导账号**（仅首次启动创建；之后改密码在界面「用户管理」里改，重启不会覆盖）
   - `JWT_SECRET`：换成一个至少 32 字符的随机串
   - `PORT`：默认 8555
5. **启动**：双击 `start.bat`（自动打开浏览器 `http://127.0.0.1:8555`）。
   改了 `.py` 或 `.env` 后运行 `restart.bat` 生效。

## 日常使用

- **看表**：打开网页即可（游客免登录，全部表只读）。
- **编辑**：右上角「登录」→ 输入账号（没有账号找管理员开通）→ 即可双击单元格编辑、新增/删除行。
- **管理员加人**：登录 admin → 列表页「用户管理」→ 填账号/密码/姓名 → 添加。新用户立刻可以登录编辑全部表。
  可随时停用（立刻下线）、重置密码、删除。
- **变动历史**：打开任意表 →「变动历史」按钮（登录后可见）：最近 200 条，含旧值→新值明细。
- **Excel 导入建表**（admin）：列表页「导入 Excel」→ 选 .xlsx（第一行当列名，自动识别数字/日期/勾选/文本），上限 1 万行 / 10MB。
- **单元格编辑**：双击任意数据格直接改；勾选列单击切换；回车保存 / Esc 取消。多人同时改同一行有乐观锁保护。
- **图片列**：编辑行时可点「上传图片」（jpg/png/webp/gif ≤10MB），也可直接贴 URL。
- **HTTP 写入（影刀/脚本）**（admin）：打开表 →「写入密钥」→ 生成密钥（只显示一次）→
  ```
  curl -X POST http://127.0.0.1:8555/api/mtables/in/<表ID> \
    -H "X-MT-Token: mtk_..." -H "Content-Type: application/json" \
    -d '{"data": {"列名": "值"}}'
  ```
  批量：`{"rows": [{...}, ...]}`（≤1000 行/次）。密钥可随时作废。
- **SQL 直接查**（admin）：打开表 →「DB视图」→ 生成视图，然后 `SELECT * FROM mtv_t<表ID>;`（实时同步，加列自动重建）。

## 从 8002 平台迁移旧数据（可选）

新应用与 eias 平台的 `mt_*` 表结构完全兼容。两种方式：

1. **共用库（推荐）**：把 `backend/.env` 的 `MT_DB_*` 指到 8002 平台用的那个库即可，
   原有表/列/行/审计/密钥直接沿用；旧的 `mt_perm` 按表授权记录会被忽略（本版不需要）。
   注意两个应用同时写同一个库时数据是共享的。
2. **导出再导入**：`mysqldump` 原 库的 `mt_table mt_column mt_row mt_audit mt_apikey` 五张表，
   导入新库；再导入本项目的 `schema.sql` 补建 `mt_user`。

## 接口权限速查

| 接口 | 游客 | 登录用户 | admin |
|---|---|---|---|
| GET tables / rows / meta / 图片 | ✔ | ✔ | ✔ |
| 行 增/改/删、图片上传 | ✘ 401 | ✔ | ✔ |
| 变动历史 audit | ✘ 401 | ✔ | ✔ |
| 建表/删表/管列/导入Excel | ✘ | ✘ 403 | ✔ |
| 用户管理 /api/mtables/users | ✘ | ✘ 403 | ✔ |
| 写入密钥 / DB视图 | ✘ | ✘ 403 | ✔ |
| HTTP 写入 /in/<表ID> | X-MT-Token 密钥即可（免登录） | 同左 | 同左 |

## 已知边界

- 行数据全部存 `mt_row`（JSON），单表几十万行内无压力；行数过 10 万界面亮黄灯提醒做实体表迁移。
- 改后端代码必须重启（run.py 未开热重载）。
- 后端黑窗口最小化没关系，**别关**；关了服务就停，双击 start.bat 可再起。
- 变动历史默认展示最近 200 条（接口上限 500），完整记录都在 `mt_audit` 表里可 SQL 查。
