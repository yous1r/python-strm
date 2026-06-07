# Telegram 频道监听与自动转存设计规范

## 概述

在现有 `Telethon + tg_resources + EVENT_MONITOR_NEW_LINK` 骨架基础上，补齐可长期运行的 Telegram 频道监听链路，解决四个核心问题：

1. 监听能力只具备基础骨架，缺少生产可用的状态管理与恢复机制
2. 服务重启或断线后可能漏消息，缺少启动补偿抓取能力
3. 消息去重与事件幂等边界不够明确，容易重复入队或重复转存
4. 监听入口与现有 transfer pipeline 存在双轨逻辑，需要逐步收口到统一管道

技术方案：保留 `Telethon` 实时订阅，叠加频道状态表与启动补偿抓取，形成“实时监听 + 增量补抓 + 幂等入队”的稳定链路。

## 一、目标与范围

### 目标

- 稳定监听指定 Telegram 频道的新消息
- 从消息中提取 115 / 123 云盘链接并写入 `tg_resources`
- 仅对首次入库的新资源投递 `EVENT_MONITOR_NEW_LINK`
- 服务重启、网络闪断后可从上次消费位置继续抓取未处理消息
- 与现有自动转存、STRM、通知链路保持兼容

### 非目标

- 不在第一阶段重构整个媒体整理逻辑
- 不在第一阶段支持大量新网盘类型
- 不在第一阶段实现复杂的多账号负载均衡
- 不在第一阶段对历史海量消息做全量回扫

## 二、现状分析

当前代码已具备以下能力：

- `app/core/monitor/telegram.py`
  - 已有 `events.NewMessage(chats=parsed_channels)` 实时监听骨架
  - 已有关键词过滤、链接提取、消息入库、事件投递
- `app/services/telegram_service.py`
  - 已有连接测试、历史抓取、热重启封装
- `app/core/monitor/handler.py`
  - 已能消费 `EVENT_MONITOR_NEW_LINK` 并执行自动转存
- `app/config.py`
  - 已有 Telegram API、Bot Token、频道列表、关键词、过滤规则等配置项

主要缺口：

1. 监听运行状态没有独立持久化
2. 启动后没有按频道补抓增量消息
3. 去重策略依赖资源表隐式约束，缺少明确唯一性定义
4. 监听链路和 transfer pipeline 的边界尚未完全统一
5. 缺少面向前端的监听状态与手动补抓接口

## 三、接入模式

推荐支持三种模式：

| 模式 | 含义 | 适用场景 | 风险 |
|---|---|---|---|
| `user` | 仅使用用户 session | 用户已加入目标频道，权限最完整 | 需要先运行 `login_tg.py` 登录 |
| `bot` | 仅使用 Bot Token | 频道明确允许 bot 读消息 | 很多频道权限不稳定 |
| `auto` | 优先用户 session，失败时回退 bot | 默认推荐模式 | 实现稍复杂 |

默认建议：`auto`

原因：很多频道不保证 bot 拥有稳定读取权限，而用户 session 对私有频道、已加入频道的可用性更高。

## 四、运行架构

### 模块职责

| 模块 | 职责 |
|---|---|
| `app/core/monitor/telegram.py` | 运行时监听器，负责连接、订阅、接收消息、投递事件 |
| `app/core/monitor/telegram_runtime.py` | 构造 Telethon client、代理解析、频道标识解析 |
| `app/services/telegram_service.py` | 监听编排层，负责测试连接、热重启、启动补偿抓取、状态查询 |
| `app/core/monitor/handler.py` | 消费 `EVENT_MONITOR_NEW_LINK`，进入自动转存链路 |
| `app/database.py` | 维护资源表与监听状态表 |

### 逻辑分层

监听链路拆成四层：

1. **连接层**：根据配置创建 client，选择 `user / bot / auto` 模式
2. **消费层**：接收 `NewMessage`，读取文本或 caption
3. **提取层**：关键词过滤、链接提取、标题提纯、资源入库
4. **投递层**：仅对新资源发出 `EVENT_MONITOR_NEW_LINK`

### 事件流

```text
Telegram 新消息
  -> TelegramMonitor.handle_event()
  -> 文本提取 / 关键词过滤 / 链接提取
  -> ingest_message()
  -> tg_resources 去重入库
  -> emit EVENT_MONITOR_NEW_LINK
  -> monitor.handler.handle_new_link()
  -> 自动转存 / STRM / 通知
```

### 启动流

```text
start()
  -> 读取配置并建立连接
  -> 恢复频道状态 telegram_monitor_state
  -> 对每个频道执行增量补抓
  -> 注册 NewMessage 实时监听
  -> 进入常驻运行
```

## 五、数据库模型

### 1. tg_resources 唯一性策略

继续保留 `tg_resources` 作为资源表，但要明确幂等规则：

- 唯一粒度推荐为：`(channel_id, message_id, link)`
- 含义：
  - 同一频道同一消息中的同一链接只入库一次
  - 同一消息中的多个不同链接允许分别入库
  - 不同频道转发的相同链接允许保留来源记录

这样既能避免重复触发，又不丢失来源信息。

### 2. telegram_monitor_state（新增）

新增频道监听状态表：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `channel_ref` | TEXT UNIQUE | 原始频道配置，如 `@abc` / `t.me/c/...` |
| `resolved_channel_id` | TEXT | 解析后的频道 ID |
| `last_message_id` | INTEGER | 最近成功处理的消息 ID |
| `last_message_date` | TEXT | 最近成功处理的消息时间 |
| `last_success_at` | TEXT | 最近一次成功抓取时间 |
| `last_error` | TEXT | 最近一次错误信息 |
| `updated_at` | TEXT | 状态更新时间 |

### 作用

- 服务重启后从 `last_message_id` 继续补抓
- 能区分“监听中但暂无新消息”和“监听失败”
- 为前端提供频道级监控状态展示

## 六、消息处理策略

### 1. 文本提取

消息内容统一按以下优先级提取：

1. `event.message.message`
2. `event.message.text`
3. 媒体 caption

要求：不能只处理纯文本消息，必须兼容图文消息中的 caption，否则会漏掉大量资源分享。

### 2. 过滤顺序

过滤顺序固定为：

1. 频道过滤
2. 文本存在性校验
3. 关键词匹配
4. 链接提取
5. 标题提纯与落库

关键词只负责“是否处理这条消息”，不负责链接级筛选。

### 3. 链接提取

短期继续复用 `TelegramMonitor.extract_links()`：

- 115 分享链接
- 123Pan 分享链接
- 密码从 URL 参数或正文中提取

中期建议把链接提取拆到独立 parser 模块，便于后续扩展更多网盘规则。

### 4. 幂等投递

`ingest_message()` 的行为应严格定义为：

- 解析消息中的所有链接
- 逐条写入 `tg_resources`
- 只返回“本次首次成功入库”的链接集合
- 只有返回集合中的链接才会触发 `EVENT_MONITOR_NEW_LINK`

这样重复消息、重复补抓、断线重连后的重复消费都不会重复转存。

## 七、启动补偿抓取

### 原则

实时监听不能单独作为可靠来源，因为服务重启、网络断开、Telegram 重连期间都可能漏消息。

因此启动时必须有一段“补偿抓取”流程。

### 推荐策略

对每个已配置频道：

1. 查询 `telegram_monitor_state.last_message_id`
2. 若存在，则抓取 `message_id > last_message_id` 的增量消息
3. 若不存在，则按启动策略处理：
   - `latest`：仅记录当前最新消息，不做历史回扫
   - `incremental`：回扫最近 N 条或最近 N 天
   - `disabled`：完全跳过补抓
4. 补抓完成后更新状态表
5. 再注册实时 `NewMessage` 监听

### 默认策略

默认使用：`latest`

原因：

- 首次部署时更安全，不会立即触发大规模历史转存
- 风险可控，适合先验证链路稳定性
- 真正需要历史补抓时，走手动接口更合适

## 八、故障恢复与稳定性

### 1. 连接失败

- 启动时如果 `api_id / api_hash` 缺失，直接拒绝启动
- 若用户 session 未登录且没有 bot token，写清晰错误日志并停止监听
- 若代理配置错误，记录具体代理解析错误

### 2. 自动重连

- 依赖 Telethon 的基础重连能力
- 外层补充监控日志与状态写入
- 每次重连成功后记录 `last_success_at`

### 3. 单条消息失败隔离

- 单条消息处理失败不能导致整个监听循环退出
- 失败时记录频道、消息 ID、错误内容
- 将错误写入 `telegram_monitor_state.last_error`
- 后续消息继续消费

### 4. 转存链路隔离

- 监听回调只做入库和投递事件
- 真正的转存必须继续后台执行，避免 115 API 调用阻塞消息消费线程
- 延续现有 `event_bus.emit_background(...)` 模式

## 九、与转存管道的衔接策略

### 短期方案

保持当前兼容链路：

- Telegram 监听器发出 `EVENT_MONITOR_NEW_LINK`
- `app/core/monitor/handler.py` 接收事件并执行自动转存

这样改动范围小，可以先把监听稳定性补齐。

### 中期收口方案

逐步把 `handle_new_link()` 改成统一入口：

- 由监听事件触发 `transfer_service.receive_share_task()` 或 transfer pipeline 入口
- 不再在 `monitor.handler` 内长期维护一套平行的旧整理逻辑

原因：

- 当前项目已经在建设统一 transfer pipeline
- 监控入口不应继续保留独立的整理分支
- 否则后续目录归档、STRM 覆盖、回滚等能力会再次分叉

## 十、配置模型调整

在 `TelegramConfig` 中新增建议字段：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `mode` | `str` | `auto` | 连接模式：`user / bot / auto` |
| `startup_sync` | `str` | `latest` | 启动补抓策略：`latest / incremental / disabled` |
| `history_limit` | `int` | `100` | 首次增量补抓上限 |
| `reconnect_backoff` | `int` | `5` | 重连退避秒数 |

现有字段继续保留：

- `api_id`
- `api_hash`
- `bot_token`
- `channels`
- `proxy`
- `keywords`
- `filter_rules`
- `target_dir_id`
- `archive_dir_id`
- `auto_organize`
- `auto_strm`

## 十一、API 设计

建议在 `app/api/system.py` 增加或补强以下接口：

| 接口 | 作用 |
|---|---|
| `GET /system/telegram/status` | 获取监听总状态与各频道状态 |
| `POST /system/telegram/restart` | 热重启监听器 |
| `POST /system/telegram/sync` | 对全部频道执行一次手动增量补抓 |
| `POST /system/telegram/sync-channel` | 对单个频道执行补抓 |
| `POST /system/telegram/test-channel` | 测试频道是否可访问 |

前端应将“配置保存”和“监听运行状态”分开展示，避免只靠测试连通性判断服务是否正常。

## 十二、测试策略

### 单元测试

- `parse_channel_reference()`
- 代理解析 `parse_telegram_proxy()`
- 关键词过滤逻辑
- 文本 / caption 提取逻辑
- `extract_links()` 的密码提取与去重行为
- `ingest_message()` 的幂等行为
- 状态表读写逻辑

### 集成测试

- 启动补偿只抓取 `last_message_id` 之后的消息
- 重复消息不会重复写入资源表
- 新消息首次入库后只触发一次 `EVENT_MONITOR_NEW_LINK`
- 监听器收到消息后能进入现有转存与通知链路

### 验收标准

第一阶段验收以以下四项为准：

1. 能稳定监听指定频道新消息
2. 服务重启后不会漏掉未处理消息
3. 重复消息不会重复转存
4. 新消息能够稳定进入现有“转存 -> STRM -> 通知”流程

## 十三、实施顺序

### 第一阶段：补齐监听基础设施

1. 在 `app/database.py` 新增 `telegram_monitor_state` 表
2. 明确 `tg_resources` 的唯一性策略并补索引
3. 封装频道状态读写接口

### 第二阶段：改造监听启动流程

1. 在 `TelegramMonitor.start()` 中接入连接模式选择
2. 增加启动补偿抓取逻辑
3. 补充消息失败隔离与状态更新

### 第三阶段：补强服务与接口

1. 在 `telegram_service.py` 中补监听状态查询与手动补抓服务
2. 在 `app/api/system.py` 中暴露状态、重启、补抓接口

### 第四阶段：与 transfer pipeline 收口

1. 评估 `monitor.handler` 与 `transfer_service` 的职责边界
2. 将自动转存入口逐步统一到 transfer pipeline

### 第五阶段：验证

1. 单测覆盖解析、过滤、幂等、状态恢复
2. 真实频道联调验证
3. 校验日志、数据库状态、转存结果、通知结果

## 十四、风险与取舍

### 风险 1：频道权限差异

部分频道只允许用户账号访问，不允许 bot 正常读消息。

应对：默认 `auto` 模式，优先用户 session。

### 风险 2：首次部署误扫历史消息

如果首次接入时直接全量回扫，可能造成大量转存任务堆积。

应对：默认 `startup_sync=latest`，仅在手动触发时做历史补抓。

### 风险 3：旧链路与新管道并存时间过长

如果 `monitor.handler` 长期保留独立整理逻辑，后续会继续出现两套行为不一致的问题。

应对：将监听稳定性建设和 transfer pipeline 收口拆成两个阶段，但明确第二阶段必须执行。

## 十五、结论

推荐采用“`Telethon` 实时监听 + 频道状态表 + 启动增量补抓 + 幂等事件投递”的方案。

该方案的优势：

- 对现有代码改动集中，能复用已有监听与事件基础设施
- 能解决生产环境最关键的漏消息与重复处理问题
- 能与当前转存链路兼容，并为后续统一到 transfer pipeline 留出清晰演进路径

按本规范实施后，Telegram 频道监听将从“可用骨架”提升为“可持续运行的自动入队入口”。