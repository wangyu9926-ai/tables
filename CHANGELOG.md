# 更新记录

按日期倒序，只记功能和结构变化；更早的逐条历史看 `git log`。

## 2026-11-26 · 批量操作、导出与上线加固

### 新增

- **区域选择**：拖选 / Shift+单击 / Shift+方向键扩展、方向键移动、Delete 清空选中格、Ctrl+C 复制成 TSV；
  选区右下角**填充柄**按住往下拖，按选区第一行重复（整片一次请求、可撤销）。
- **粘贴自动建行**：粘贴超出当前页的行数时在表尾新建，一次批量请求；超过 500 行界面自动分批。
  只复制了一个值、却选了一片时铺满选区。
- **导出 Excel**：顶栏「导出」下拉支持 CSV 和 `.xlsx`（表头加粗冻结、列宽沿用表设置、公式列给算好的值）。
- **团队视图**：视图可保存到服务端，本表所有人可见；同名覆盖，只有保存者和管理员能删。
- **HTTP 写入幂等键**：`X-Idempotency-Key`，7 天窗口内同键重试直接返回上次结果、不重复插入。
- **图片按表配额**：每张表图片总量默认 512MB（`KB_IMAGE_TABLE_MB` 可调），到顶回 413。
- **软删数据清理**：`tools/purge_deleted.py`，默认只统计，`--days` / `--yes` 控制范围与执行。
- **库结构版本**：`kb_setting.schema_version` 在启动时写入，升级时可直接看出库是哪一版结构。
- **分析页大表提示**：项目里有单表超过 5 万行时，页头直接提示整表加载会慢。

### 改进

- 批量行接口（`POST /api/tables/{tid}/rows/bulk`）：单次 1~500 行，逐行返回更新 / 新建 / 版本冲突 / 报错，
  一行失败不影响其它行；畸形的 `id` / `ver` 逐条报错，不再 500。
- 上线前回归扩到 **363 项断言**：批量行、xlsx、服务端视图、写入幂等、图片配额、清理工具只统计模式。

## 2026-11-27 · 后端按功能拆模块 + LICENSE

- **后端**：`backend/app.py` 从 3300 行拆成 ≈100 行的装配层（FastAPI 实例、异常处理器、中间件、include_router、
  静态目录、首页）；共享函数/缓存/常量进 `backend/deps.py`；52 个路由按功能搬到 `backend/modules/` 下 16 个文件
  （meta / projects / tables / columns / rows / events / comments / misc / export / images / apikeys / ingest /
  imports / sync_api / views / dashboards）。路由函数体原样搬移，注册顺序与原文件一致。
- 修拆分暴露的一个缓存 bug：`_META_GEN`/`_META_CNT` 是裸标量，`from deps import *` 后各模块拿到的是副本，
  元信息缓存永不失效 → 改成 `_META_VERS = {'gen','cnt'}` 容器共享（回归里的「组名同步」抓到）。
- **LICENSE**：新增，私有 / 保留所有权利（仅限内部使用，不对外授权）。
- 上线前回归 **365 项断言**（拆分前在临时实例上跑通；路由集合与线上 openapi 逐一对比一致）。

## 2026-11-26 · 前端拆分 + 后端模块化样板

- **前端**：`index.html` 里 6800 行的内联脚本按功能拆成 `frontend/js/` 下 12 个文件（core / shell / dialogs /
  sync-fields / grid-head / rows-live / cells / paste-panel / filters-tools / auth-menu / analysis / import-select），
  加载顺序即执行顺序；`index.html` 只剩页面骨架和样式。脚本走 `/static/js/…`，`?v=` 由服务端按文件 mtime 自动加
  （改 js 不用重启，浏览器也会重新拉）。
- **后端**：`backend/modules/` 放 APIRouter 模块，「保存的视图」先迁过去做样板；老路由仍在 `app.py`（路径顺序互相咬合，
  要逐个模块拆、每拆一个跑一轮回归）。
- 内容安全策略：主页面没有内联脚本了，`script-src` 只剩 `'self'`；回归改为检查 12 个外部脚本都能取到、都带版本号。
- 上线前回归 **365 项断言**。

## 2026-11-26 · 上线前加固

- 长整数（订单号 / 商品 ID）按整数解析并以字符串返回，不丢精度。
- 畸形输入统一 4xx（筛选、名称长度、列定义、导入配置、登录与账号接口、`NaN` / `Infinity` 请求体）。
- 首次部署向导支持无人值守（`GATE_SETUP_STDIN=1`），数据库与管理员账号在启动向导里配置。
- 服务器 + 域名部署：Nginx 终结 HTTPS、HSTS、时区、SSE 代理参数；`tools/backup_db.py` 备份与恢复演练。
- 实时刷新：SSE 推送 + 轮询兜底，只重画变更行；服务重启后序号回退自动发 `reset`。
