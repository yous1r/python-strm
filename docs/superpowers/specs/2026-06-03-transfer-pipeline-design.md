# 115 网盘转存整理管道设计规范

## 概述

将 115 网盘转存整理功能重构为事件驱动管道，解决四个核心问题：
1. 115 share_receive API 不支持自定义目标目录 → 两步走：转存后移动
2. 临时目录管理 → 自动刮削分类归档
3. 变更范围限制 → 操作前校验文件所在目录
4. 操作可追溯可还原 → 操作日志 + 逆序 rollback

技术方案：事件驱动管道（发布-订阅），为后续全部模块接入事件总线铺路。

## 一、事件总线扩展

在现有 `app/events.py` 中新增事件常量：

| 事件 | 触发时机 | 载荷 |
|---|---|---|
| `EVENT_TRANSFER_RECEIVED` | share_receive 完成 | `share_url, inbox_dir_id, files[]` |
| `EVENT_TRANSFER_MOVED` | 文件从收件箱移到临时目录 | `task_id, temp_dir_id, files[]` |
| `EVENT_ORGANIZE_START` | 开始逐文件整理 | `task_id, file_count` |
| `EVENT_ORGANIZE_FILE_DONE` | 单文件整理完成 | `task_id, file_cid, op_log` |
| `EVENT_ORGANIZE_COMPLETE` | 整批整理完毕（已有，复用） | `task_id, success_count` |
| `EVENT_ROLLBACK_START` | 开始还原 | `task_id` |
| `EVENT_ROLLBACK_FILE_DONE` | 单文件还原完成 | `task_id, file_cid, seq` |
| `EVENT_ROLLBACK_COMPLETE` | 整批还原完毕 | `task_id` |

### 处理链

```
share_receive → TRANSFER_RECEIVED → [Mover: 收件箱→临时目录] → TRANSFER_MOVED
  → [Organizer: TMDB刮削→建目录→移动+重命名] → ORGANIZE_FILE_DONE × N → ORGANIZE_COMPLETE
  → [StrmGen: 生成 STRM]

ROLLBACK 请求 → ROLLBACK_START → [Rollback: 逆序执行] → ROLLBACK_FILE_DONE × N → ROLLBACK_COMPLETE
```

## 二、模块结构

新增 `app/core/transfer/`：

| 文件 | 职责 | 订阅的事件 |
|---|---|---|
| `__init__.py` | 注册所有处理器，暴露 `init_transfer_pipeline()` | — |
| `mover.py` | 列出收件箱文件 → move_files 到 temp_dir → 记录操作 | `EVENT_TRANSFER_RECEIVED` |
| `organizer.py` | 获取文件列表 → TMDB 刮削 → 建目录 → 移动+重命名 → 写日志 | `EVENT_TRANSFER_MOVED` |
| `rollback.py` | 按 task_id 逆序执行操作日志，恢复文件位置和名称 | `EVENT_ROLLBACK_START` |
| `classifier.py` | 分类规则引擎：区分电影/剧集/动漫/纪录片/综艺 + 地区分类 | — |
| `catalog.py` | 目录树构建器：在 115 中逐级创建/查找分类目录路径 | — |
| `scope.py` | 安全边界校验：文件 cid 必须在 allowed_dirs 范围内 | — |
| `models.py` | Pydantic 模型：OperationLog, TransferTask, RollbackResult | — |

**约束规则**：mover 和 organizer 操作前调用 `scope.validate()`，确保文件的当前目录和目标目录都在 `allowed_dirs` 内，否则拒绝操作。

## 三、数据库模型

### transfer_tasks（整理任务表）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `task_id` | TEXT UNIQUE | UUID |
| `status` | TEXT | pending / running / done / rolled_back / failed |
| `source_dir_id` | TEXT | 临时转存目录 cid |
| `archive_dir_id` | TEXT | 归档目录 cid |
| `file_count` | INTEGER | 涉及文件总数 |
| `success_count` | INTEGER | 成功整理数 |
| `error_detail` | TEXT | 失败时记录原因 |
| `created_at` | DATETIME | 创建时间 |
| `completed_at` | DATETIME | 完成时间 |

### operation_logs（操作明细表）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `task_id` | TEXT FK | 关联 transfer_tasks |
| `seq` | INTEGER | 操作序号（还原时倒序执行） |
| `file_cid` | TEXT | 文件/目录的 115 cid |
| `file_name` | TEXT | 操作前原文件名 |
| `new_name` | TEXT | 操作后新文件名 |
| `source_cid` | TEXT | 操作前所在目录 cid |
| `target_cid` | TEXT | 操作后所在目录 cid |
| `op_type` | TEXT | move / rename / create_dir |
| `status` | TEXT | done / rolled_back |
| `created_at` | DATETIME | 操作时间 |

### 还原逻辑

对指定 `task_id`，按 `seq DESC` 读取 `status='done'` 的记录：

- `op_type=rename` → 将文件从 `new_name` 改回 `file_name`
- `op_type=move` → 将文件从 `target_cid` 移回 `source_cid`
- `op_type=create_dir` → 跳过

每条还原成功后更新 `status='rolled_back'`，全部完成后更新 `transfer_tasks.status='rolled_back'`。

### 迁移策略

- 删除旧表 `organize_history`
- 新建 `transfer_tasks` 和 `operation_logs`
- 在 `app/database.py` 的 `init_db()` 中新增建表语句

## 四、配置模型

`config.yaml` 新增 `transfer` 段落：

```yaml
transfer:
  enabled: true
  inbox_dir_id: "0"           # 115 share_receive 默认落点（最近接收）
  temp_dir_id: ""             # 临时目录，转存后移动到此处待整理
  archive_dir_id: ""          # 归档根目录，整理后最终落点
  auto_organize: true          # 移动完成后自动触发整理
  auto_strm: true              # 整理后自动生成 STRM
  categories:                  # 分类结构
    电影:
      - 国产电影
      - 欧美电影
      - 日韩电影
      - 其他
    剧集:
      - 国产剧集
      - 欧美剧集
      - 日韩剧集
      - 其他
    动漫:
      - 国产动漫
      - 日韩动漫
      - 欧美动漫
      - 其他
    纪录片: []
    综艺: []
```

Pydantic 模型在 `app/config.py` 中新增 `TransferConfig` 和 `CategoryConfig`。

`allowed_dirs` 由系统运行时自动维护：`temp_dir_id` + `archive_dir_id` 及其子孙目录 cid。

## 五、目录结构规范

```
归档根目录/
├── 电影/
│   └── 电影名 (年份) {tmdb-id}.ext
├── 剧集/
│   ├── 国产剧集/
│   │   └── 翘楚 (2026) {tmdb-289271}/
│   │       └── Season 1/
│   │           └── 翘楚 (2026) - S01E01.mkv
│   ├── 欧美剧集/
│   ├── 日韩剧集/
│   └── 其他/
├── 动漫/                         # 结构同剧集
├── 纪录片/                        # 无二级分类，平铺
└── 综艺/
```

规则：
- 电影：平铺在 `一级分类/` 下，文件名 `作品名 (年份) {tmdb-id}.ext`
- 剧集/动漫：`一级/二级/作品名 (年份) {tmdb-id}/Season N/作品名 (年份) - S01E01.ext`
- 一级和二级分类均可配置，空列表表示无二级分类
- 归档目录不存在时自动创建

## 六、API 端点

路由 `app/api/transfer.py`，前缀 `/api/v1/transfer`：

### 转存

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/receive` | 接收 115 分享链接，后台执行两步走，返回 task_id |
| `GET` | `/tasks` | 列出所有任务，支持 status/task_id 筛选 |
| `GET` | `/tasks/{task_id}` | 单任务详情 |

### 整理

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/organize/run` | 手动触发整理，传 temp_dir_id |
| `GET` | `/organize/tasks` | 列出整理任务 |
| `GET` | `/organize/tasks/{task_id}` | 任务详情，含 operation_logs 明细 |

### 还原

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/rollback/{task_id}` | 一键还原 |
| `GET` | `/rollback/{task_id}/preview` | 预览影响范围，不执行 |

### 分类

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/categories` | 返回当前分类配置 |

## 七、监控处理器集成

修改 `app/core/monitor/handler.py`：`handle_new_link` 不再直接调用 `_auto_organize`，改为 emit `EVENT_TRANSFER_RECEIVED`，由 mover 和 organizer 通过事件总线异步处理。

## 八、变更范围

### 新增文件

| 文件 | 说明 |
|---|---|
| `app/core/transfer/__init__.py` | 管道初始化，注册事件处理器 |
| `app/core/transfer/mover.py` | 收件箱→临时目录移动器 |
| `app/core/transfer/organizer.py` | 刮削+分类+文件整理器 |
| `app/core/transfer/rollback.py` | 还原执行器 |
| `app/core/transfer/classifier.py` | 分类规则引擎 |
| `app/core/transfer/catalog.py` | 目录树构建器 |
| `app/core/transfer/scope.py` | 安全边界校验 |
| `app/core/transfer/models.py` | 数据模型 |
| `app/api/transfer.py` | REST API |

### 修改文件

| 文件 | 变更 |
|---|---|
| `app/events.py` | 新增 7 个事件常量 |
| `app/database.py` | 删除 organize_history，新增 transfer_tasks + operation_logs |
| `app/config.py` | 新增 TransferConfig、CategoryConfig |
| `config.yaml` | 新增 transfer 段落 |
| `app/core/monitor/handler.py` | 改为 emit 事件，移除直接调用 _auto_organize |
| `app/main.py` | 注册 transfer router，调用 init_transfer_pipeline() |
| `app/core/cloud115/client.py` | share_receive 返回结构增加 received_files 信息，支持后续移动 |

### 保留不动的文件

| 文件 | 说明 |
|---|---|
| `app/core/media/organizer.py` | 现有 organizer 暂保留，后续可迁移到事件总线 |
| `app/core/sync/engine.py` | 不变，仍通过 ORGANIZE_COMPLETE 触发 |