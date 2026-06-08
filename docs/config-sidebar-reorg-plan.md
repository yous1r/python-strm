# 配置项与侧边栏重组方案

## 1. 现状判断

当前项目的配置问题，不是“字段太多”这么简单，而是同一类目录配置被拆散到多个页面，且存在三类混用：

1. 账号接入配置：例如 `cloud115.cookie`、`cloud115.api_type`。
2. 业务流程配置：例如转存中转目录、归档根目录、STRM 输出目录。
3. 一次性执行参数：例如手动批量生成 STRM 时选择的目录。

这三类东西目前同时出现在 `115 网盘`、`STRM 管理`、`频道监控`、`刮削与整理`、`转存整理` 页面里，导致用户很难判断“这个目录是全局配置、模块配置，还是这次任务临时参数”。

## 2. 你点名的 5 个配置项，真实含义与实际用途

### 2.1 `115 网盘 -> 转存目标目录`

- 对应字段：`cloud115.target_dir_id`
- 前端位置：[app/web/templates/115.html](../app/web/templates/115.html)
- 实际用途：不是 115 账号配置本身，而是“转存落盘目录”的兜底值。

代码链路：

- Telegram 单条转存时，优先级为 `series_folder_id -> transfer.temp_dir_id -> monitor.telegram.target_dir_id -> cloud115.target_dir_id`
  见 [app/services/telegram_resource_transfer_service.py](../app/services/telegram_resource_transfer_service.py)
- 批量转存时，优先级为 `series_folder_id -> transfer.temp_dir_id -> monitor.telegram.target_dir_id -> cloud115.target_dir_id`
  见 [app/core/transfer/batch.py](../app/core/transfer/batch.py)

结论：

- 它不是“115 专属配置”，本质是历史遗留的全局兜底目录。
- 从信息架构上应移出 `115 网盘` 页面。

### 2.2 `STRM 管理 -> 115 网盘 自动化监控目录配置` 里的“目录”

- 对应字段：`cloud115.sync_dirs`
- 数据结构：`[{dir_id, name}]`
- 前端位置：[app/web/templates/strm.html](../app/web/templates/strm.html)

实际用途：

- 旧 `sync_engine` 会按这些目录做增量/全量 STRM 扫描
  见 [app/core/sync/engine.py](../app/core/sync/engine.py)
- `cloud115_full_sync_service` 会把这些目录纳入 115 全链路同步范围
  见 [app/services/cloud115_full_sync_service.py](../app/services/cloud115_full_sync_service.py)
- 115 本地目录缓存/数据库同步逻辑也会参考这类目录范围
  见 [app/core/cloud115/db_sync.py](../app/core/cloud115/db_sync.py)

结论：

- 这不是“STRM 页面里的普通配置项”，而是“115 内容源目录范围定义”。
- 它应该保留，但应该改名为“115 内容扫描目录”或“115 STRM 扫描源目录”。

### 2.3 `STRM 管理 -> 批量生成工具` 的“目标目录”

- 前端位置：[app/web/templates/strm.html](../app/web/templates/strm.html)
- 接口位置：[app/api/strm.py](../app/api/strm.py)
- 请求字段：`dir_id`

实际用途：

- 这是一次性执行参数，不会保存成配置。
- 用户点击“开始生成”时，把选中的目录作为本次任务的扫描起点传给 `generator_115.batch_generate(...)`。

结论：

- 它不是系统配置项，不应该和“全局 STRM 设置”放在同一认知层级。
- UI 上应明确标注为“本次任务目录”或“手动任务输入”。

### 2.4 `频道监控 -> Telegram 监控节点设置` 中：

#### `115 转存中转目录 ID`

- 对应字段：`monitor.telegram.target_dir_id`
- 前端位置：[app/web/templates/monitor.html](../app/web/templates/monitor.html)

实际用途：

- Telegram 自动转存时的落盘目录，但只在 `transfer.temp_dir_id` 未配置时才会生效。
- 见 [app/services/telegram_resource_transfer_service.py](../app/services/telegram_resource_transfer_service.py)
- 见 [app/core/transfer/batch.py](../app/core/transfer/batch.py)

结论：

- 它不是首选主配置，而是 Telegram 场景下的次级兜底/覆盖项。

#### `115 整理归档根目录 ID`

- 对应字段：`monitor.telegram.archive_dir_id`
- 前端位置：[app/web/templates/monitor.html](../app/web/templates/monitor.html)

实际用途：

- 仅在 Telegram 自动转存完成后，且 `monitor.telegram.auto_organize = true` 时，用来作为自动整理的归档根目录。
- 见 [app/services/telegram_resource_transfer_service.py](../app/services/telegram_resource_transfer_service.py)

结论：

- 它和 `transfer.archive_dir_id` 语义高度重合。
- 如果不支持“Telegram 单独走另一套归档根目录”，就应该被收敛掉。

### 2.5 `刮削与整理 -> 整理分类规则映射`

前端当前保存的是：

- `organize.categories`
- `organize.regions`

前端位置：

- [app/web/templates/organize.html](../app/web/templates/organize.html)

但实际分类逻辑读取的是：

- `transfer.categories`
- 见 [app/core/transfer/classifier.py](../app/core/transfer/classifier.py)

而 `MediaOrganizer` 内部虽然读取了 `get_config().organize`，但当前 `categories/regions` 并未参与 `determine_category_and_region()` 或路径构建逻辑：

- 见 [app/core/media/organizer.py](../app/core/media/organizer.py)

结论：

- 目前页面上的“整理分类规则映射”并没有真正控制主分类结果。
- 正常业务上，本来应该按规则配置来分类；但当前代码实际是“内置分类逻辑 + TMDB 区域判断”。
- 这属于典型的“前端可配，后端未真正消费”的伪配置项。

## 3. 当前应认定为重复、无效或误导的配置

### 3.1 应收敛为单一来源的目录配置

建议统一只保留一套主配置：

- `transfer.temp_dir_id`：统一转存中转目录
- `transfer.archive_dir_id`：统一归档根目录

应降级为兼容/覆盖逻辑，而不应继续作为主页面配置展示的字段：

- `cloud115.target_dir_id`
- `monitor.telegram.target_dir_id`
- `monitor.telegram.archive_dir_id`

### 3.2 当前基本无效或未接入主链路的配置

- `organize.categories`
- `organize.regions`

这两个字段现在不控制主分类结果，继续暴露会误导用户。

### 3.3 当前疑似未生效的开关

全局搜索结果显示，以下字段定义存在，但没有看到实际消费逻辑：

- `transfer.auto_strm`
- `monitor.telegram.auto_strm`

如果后续确认确实没有消费代码，建议直接移除或暂时隐藏，避免“开关存在但不生效”。

### 3.4 仅适合作为手动工具参数，不应伪装成配置

- `STRM 管理 -> 批量生成工具 -> 目标目录`

它本质是任务输入，不应纳入“配置整理”的讨论范围。

## 4. 建议的新侧边栏结构

建议按“底层驱动 -> 媒体资产处理 -> 自动化流程”重组侧边栏。这个结构比单纯按功能名分组更符合当前系统真实依赖：云盘能力是底座，STRM/刮削/归档是媒体资产处理层，Telegram 和通知只是自动化触发与反馈层。

### 4.1 推荐侧边栏：4 大核心模块

#### 运行面板 Dashboard

定位：只放运行状态、任务态、日志态，不承载业务配置。

建议收纳：

- `仪表盘`：服务状态、最近同步、关键指标。
- `任务监控`：转存任务、整理任务、STRM 任务、失败重试。
- `日志查看`：运行日志、错误日志、调试输出。

当前可归并页面：

- `/`
- `/tasks`
- `/debug` 中偏“观测”的部分

#### 存储与云盘 Storage & Cloud

定位：底层驱动配置，只解决“系统如何连接云盘、如何访问文件、哪些目录作为云盘内容源”。

建议收纳：

- `115 网盘`：账号、Cookie、扫码登录、API 类型、播放 UA、STRM 播放模式。
- `123 网盘`：账号 Token、基础能力配置。
- `云盘目录源`：115/123 的扫描源目录，例如 `cloud115.sync_dirs`。
- `本地缓存`：115 目录树同步、本地数据库缓存刷新。

不建议放在这里：

- 转存中转目录。
- 整理归档根目录。
- Telegram 专属目录。

原因：这些是媒体入库管道的业务目录，不是云盘驱动配置。

#### 媒体库引擎 Media Library

定位：处理媒体资产本身，包括转存落点、归档规则、刮削、STRM 输出、媒体库联动。

建议收纳：

- `转存管道`：`transfer.temp_dir_id`、`transfer.archive_dir_id`、`transfer.inbox_dir_id`、`transfer.auto_organize`。
- `分类规则`：真实生效的 `transfer.categories`。
- `刮削设置`：TMDB API Key、语言、代理、洗版偏好。
- `STRM 设置`：`strm.output_dir`、`strm.base_url`、`strm.sync_metadata`、`strm.clean_invalid`。
- `手动工具`：手动整理、手动 STRM 生成、归档 STRM 覆盖。
- `媒体库联动`：Emby/Jellyfin 代理、路径映射、刷新策略。

这里应成为目录主配置的唯一入口。用户只需要在这个模块里理解 3 个核心目录：

1. 转存中转目录
2. 归档根目录
3. STRM 扫描源目录

其中前两个属于 `媒体库引擎`，第三个目录源也可以在 `存储与云盘` 中维护，并在媒体库引擎里只读展示或快捷跳转。

#### 自动化与通知 Auto & Notify

定位：自动触发器和消息反馈，不再重复定义媒体目录主配置。

建议收纳：

- `Telegram 频道监控`：API ID、API Hash、Bot Token、代理、频道、关键词、过滤规则、历史同步策略。
- `自动化编排`：启动编排、定时同步、全链路同步触发策略。
- `通知推送`：企业微信、Telegram Bot 通知、Bark。

目录配置处理原则：

- Telegram 自动转存默认使用 `媒体库引擎 -> 转存管道` 的中转目录和归档目录。
- 如果保留 Telegram 独立目录，只能作为“高级覆盖项”，文案必须明确是覆盖默认目录。
- `monitor.telegram.target_dir_id` 和 `monitor.telegram.archive_dir_id` 不应再作为普通主配置展示。

### 4.2 当前页面的归并建议

#### `115 网盘` -> 归入 `存储与云盘`

保留：

- `enabled`
- `cookie`
- `api_type`
- `strm_type`
- `play_ua`

移出：

- `cloud115.target_dir_id`

原因：这是媒体入库业务目录，不是云盘底层驱动配置。

#### `STRM 管理` -> 拆入 `媒体库引擎` 与 `存储与云盘`

拆成两块：

1. `STRM 全局设置`
2. `手动生成工具`

保留：

- `strm.output_dir`
- `strm.base_url`
- `strm.sync_metadata`
- `strm.clean_invalid`
- `cloud115.sync_dirs`

改名：

- “115 网盘 自动化监控目录配置” -> “115 STRM 扫描源目录”

说明：`cloud115.sync_dirs` 本质是云盘内容源范围，建议配置入口放到 `存储与云盘 -> 云盘目录源`，在 `媒体库引擎 -> STRM 设置` 中提供只读摘要或快捷跳转。

#### `频道监控` -> 归入 `自动化与通知`

保留 Telegram 接入本身：

- `api_id`
- `api_hash`
- `bot_token`
- `proxy`
- `channels`
- `keywords`
- `filter_rules`
- `startup_sync`
- 定时同步相关开关

目录配置改造：

- 默认不再单独展示 `monitor.telegram.target_dir_id`
- 默认不再单独展示 `monitor.telegram.archive_dir_id`
- 改为只显示一句说明：
  “Telegram 转存默认沿用转存管道中的中转目录和归档目录”

如果确实需要 Telegram 专属目录：

- 折叠到“高级覆盖配置”中
- 文案改成“覆盖默认中转目录（可选）”“覆盖默认归档根目录（可选）”

#### `刮削与整理` -> 归入 `媒体库引擎`

保留真正有意义的内容：

- TMDB API Key / 语言 / 代理
- 洗版偏好
- 手动 115 多对一整理工具

移除或重构：

- `organize.categories`
- `organize.regions`

如果要保留“分类规则配置”，必须改成真实驱动 `transfer.categories` 的统一配置页，而不是继续挂在 `organize.*` 下。

#### `转存整理` -> 归入 `媒体库引擎`

应成为目录主配置的唯一主入口。

保留为主配置：

- `transfer.enabled`
- `transfer.inbox_dir_id`
- `transfer.temp_dir_id`
- `transfer.archive_dir_id`
- `transfer.auto_organize`

建议新增到这里：

- `transfer.categories` 的可视化配置

原因：真实分类逻辑已经走 `transfer.categories`，应该把配置入口放回真实消费它的业务页。

## 5. 推荐的配置收敛方案

### 方案 A：最稳妥，先做 UI 收敛，不动后端字段

适合先快速止血。

做法：

- UI 只把 `transfer.temp_dir_id` 展示为“统一转存中转目录”
- UI 只把 `transfer.archive_dir_id` 展示为“统一归档根目录”
- `cloud115.target_dir_id`、`monitor.telegram.target_dir_id`、`monitor.telegram.archive_dir_id` 隐藏为兼容字段
- `organize.categories/regions` 从页面移除
- `transfer.categories` 增加一个新的可视化编辑入口

优点：

- 不会破坏旧配置文件兼容性
- 前后端改动小
- 能立即解决“用户不知道该填哪里”的问题

### 方案 B：中期优化，字段语义彻底统一

做法：

- 停止新写入 `cloud115.target_dir_id`
- 停止新写入 `monitor.telegram.target_dir_id`
- 停止新写入 `monitor.telegram.archive_dir_id`
- Telegram 转存和批量转存统一只依赖 `transfer.temp_dir_id`
- Telegram 自动整理统一只依赖 `transfer.archive_dir_id`
- 保留旧字段读取兼容，但启动时自动迁移并给出日志提示

优点：

- 代码语义更干净
- 文档、页面、后端三者更一致

缺点：

- 需要做迁移兼容和回归测试

### 方案 C：长期形态，按“流程域”重建配置模型

例如：

- `sources.cloud115.*`
- `sources.telegram.*`
- `pipeline.transfer.*`
- `pipeline.classify.*`
- `output.strm.*`

这个方向最干净，但需要的改造面最大，不建议作为第一步。

## 6. 建议的实施顺序

### 第一阶段落地状态

已按“方案 A：先做 UI 收敛，不动后端字段”完成第一轮改造：

- [x] 侧边栏已重组为 `运行面板`、`存储与云盘`、`媒体库引擎`、`自动化与通知` 四大模块。
- [x] `115 网盘` 页面已移除 `cloud115.target_dir_id` 配置入口，并停止从该页写入该字段。
- [x] `STRM 管理` 页面已将 `cloud115.sync_dirs` 文案改为 `115 STRM 扫描源目录`，并明确它是扫描源范围。
- [x] `STRM 管理 -> 批量生成工具` 已将目标目录文案改为本次任务的扫描目录，避免误认为系统配置。
- [x] `频道监控` 页面已把 `monitor.telegram.target_dir_id/archive_dir_id` 收进“高级覆盖配置”，并说明默认沿用转存管道目录。
- [x] `刮削与整理` 页面已移除 `organize.categories/regions` 的伪配置入口。
- [x] `转存管道` 页面已新增真实写入 `transfer.categories` 的分类规则编辑入口。

本阶段刻意保留后端字段和旧读取链路，以保持旧配置文件兼容；后端字段迁移、旧字段清理和 `auto_strm` 消费确认放到第二阶段。

### 第一阶段：先把用户认知理顺

1. 将侧边栏重组为 `运行面板`、`存储与云盘`、`媒体库引擎`、`自动化与通知` 四大模块。
2. 把 `cloud115.target_dir_id` 从 `115 网盘` 页面移除，目录主配置迁到 `媒体库引擎 -> 转存管道`。
3. 把 `cloud115.sync_dirs` 改名为“云盘目录源 / 115 STRM 扫描源目录”，入口放到 `存储与云盘`。
4. 把 `monitor.telegram.target_dir_id/archive_dir_id` 改成“高级覆盖项”或直接隐藏。
5. 把 `organize.categories/regions` 从页面移除。
6. 在 `媒体库引擎 -> 分类规则` 增加配置入口，直接绑定 `transfer.categories`。

### 第二阶段：再清理后端歧义

1. 统一 Telegram 转存目录读取顺序。
2. 统一自动整理归档目录来源。
3. 确认 `auto_strm` 是否真正有消费者，没有就删掉。
4. 为旧字段做兼容迁移。

## 7. 最终建议

如果只给一个结论，我建议按下面原则收敛：

- `存储与云盘` 只管底层驱动：115/123 账号、API、播放模式、云盘扫描源、本地缓存。
- `媒体库引擎` 只管媒体资产处理：转存中转、归档根目录、分类规则、刮削、STRM、Emby/Jellyfin 联动。
- `自动化与通知` 只管流程触发和反馈：Telegram 监听、历史同步、启动编排、定时任务、通知推送。
- 目录主配置只放一处：`transfer.temp_dir_id`、`transfer.archive_dir_id`。
- 分类规则只保留一套，且必须绑定真实生效的 `transfer.categories`。
- `organize.categories/regions` 这类假配置应尽快下线。

这样改完后，用户只需要记住 3 个核心目录概念：

1. 转存中转目录
2. 归档根目录
3. STRM 扫描源目录

其余页面不再重复定义这几个目录，配置认知会清晰很多。