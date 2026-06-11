# Telegram 历史同步设计

## 目标

Telegram 监控能力只保留两个能力域：

1. 实时监听：继续由 `app/core/monitor/telegram.py` 负责 `events.NewMessage` 消费、资源提取和事件投递。
2. 历史同步：统一收口为事件总线驱动的后台任务，覆盖启动触发、定时触发和手动触发。

本次设计不改变已经完成的“单消息单记录 + 完整资源字段”入库模型，也不回退现有 `tg_resources` 资源聚合语义。

## 现状问题

当前代码中存在三套历史链路：

1. 启动补抓：`startup_bootstrap_service.py` 直接调用 `sync_configured_channels(...)`
2. 定时后台同步：`telegram_background_service.py` 直接调用 `sync_configured_channels(...)`
3. 手动抓取：`app/api/system.py` 提供多组直连旧服务函数的接口

这些链路的问题是：

1. 编排入口重复，逻辑分散，参数模型不一致。
2. 同步范围仍围绕旧的 `latest / incremental / disabled` 设计，不适合“全部 / 最近跨度 / 日期区间”三类需求。
3. 历史同步缺少按频道、按时间窗口的分片执行与 checkpoint，长时间任务不可恢复。
4. 页面概念混杂，把实时监听、启动补抓、后台定时增量和手动抓历史混在一个区域。

## 新边界

### 实时监听

`app/core/monitor/telegram.py` 只保留：

1. Telethon 客户端生命周期。
2. 新消息正文与 `.torrent` 附件提取。
3. `telegram_monitor.ingest_message(...)` 入库。
4. 对新入库资源发出 `EVENT_MONITOR_NEW_LINK`。

实时监听不再承担历史补抓职责。

### 历史同步

新增 `app/services/telegram_history_sync_service.py`，统一负责：

1. 校验同步请求。
2. 解析同步范围。
3. 按频道分片。
4. 按时间窗口分片。
5. 执行 chunk 级抓取。
6. 维护 chunk checkpoint。
7. 产出任务结果与失败信息。

`app/services/telegram_service.py` 收缩为：

1. 监听器状态查询与重启。
2. Telegram 连通性验证。
3. 抓取消息入库的通用帮助函数，如 `_dispatch_scraped_message(...)`。

## 配置模型

`app/config.py` 为 `TelegramConfig` 增加嵌套的 `history_sync` 配置：

```yaml
monitor:
  telegram:
    history_sync:
      mode: relative_range
      relative_value: 6
      relative_unit: months
      date_start: ""
      date_end: ""
      chunk_days: 7
      emit_new_link_events: false
      scheduled_enabled: true
      scheduled_interval_minutes: 5
```

字段含义：

1. `mode`: `all | relative_range | date_range`
2. `relative_value`: 相对跨度数值，如 `6`
3. `relative_unit`: `days | weeks | months`
4. `date_start` / `date_end`: 日期区间模式下的起止日期，使用 `YYYY-MM-DD`
5. `chunk_days`: 每个时间窗口的天数
6. `emit_new_link_events`: 历史同步是否向转存链路发 `EVENT_MONITOR_NEW_LINK`
7. `scheduled_enabled` / `scheduled_interval_minutes`: 定时同步开关与周期

兼容策略：

1. 旧字段 `scheduled_sync_*`、`startup_sync` 保留在模型中，避免旧配置文件加载失败。
2. 新代码只读取 `history_sync`，旧字段视为兼容残留。

## 事件模型

新增事件常量：

1. `EVENT_TELEGRAM_HISTORY_SYNC_REQUESTED`
2. `EVENT_TELEGRAM_HISTORY_SYNC_CHUNK_REQUESTED`
3. `EVENT_TELEGRAM_HISTORY_SYNC_CHUNK_COMPLETED`
4. `EVENT_TELEGRAM_HISTORY_SYNC_CHANNEL_COMPLETED`
5. `EVENT_TELEGRAM_HISTORY_SYNC_COMPLETED`
6. `EVENT_TELEGRAM_HISTORY_SYNC_FAILED`

事件流：

1. 启动、定时、手动入口统一发布 `EVENT_TELEGRAM_HISTORY_SYNC_REQUESTED`
2. 请求 handler 创建 Telethon client，解析范围并生成 channel + chunk 执行计划
3. 每个 chunk 发布 `EVENT_TELEGRAM_HISTORY_SYNC_CHUNK_REQUESTED`
4. chunk handler 执行消息抓取、入库、checkpoint 更新
5. channel 全部 chunk 完成后发布 `EVENT_TELEGRAM_HISTORY_SYNC_CHANNEL_COMPLETED`
6. 全任务结束后发布 `EVENT_TELEGRAM_HISTORY_SYNC_COMPLETED`
7. 任一未恢复异常发布 `EVENT_TELEGRAM_HISTORY_SYNC_FAILED`

## 分片与范围规则

### 分片层次

1. 第一层：按频道分片
2. 第二层：按时间窗口分片

### 范围模式

1. `all`
   先读取频道最早消息时间和最新消息时间，再按 `chunk_days` 倒序切窗。
2. `relative_range`
   使用当前时间倒推，例如最近 6 个月，得到 `[now-6months, now]` 后切窗。
3. `date_range`
   使用用户提供的 `[date_start, date_end]`，校验后切窗。

### 切窗规则

1. 所有 chunk 都按“右闭左开近似区间”倒序执行，减少重复扫描。
2. 每个 chunk 至少覆盖 1 天。
3. chunk 的消息迭代按消息时间边界截断，不依赖单纯的 `limit`。

## Checkpoint / Resume

新增数据库表 `telegram_history_sync_checkpoint`，用于记录 chunk 级进度。

最小字段：

1. `checkpoint_key`
2. `channel_ref`
3. `range_start`
4. `range_end`
5. `status`
6. `last_message_id`
7. `last_message_date`
8. `processed_count`
9. `inserted_count`
10. `last_error`
11. `updated_at`

规则：

1. 同一 `channel_ref + range_start + range_end` 视为同一个 checkpoint。
2. chunk 完成后标记为 `completed`，再次执行同一范围时默认跳过。
3. chunk 失败时记录 `last_message_id` 与 `last_error`，重试时跳过已处理的较新消息，继续抓剩余区间。

## API 调整

保留测试与状态接口，历史同步接口统一为一个能力：

1. `POST /system/telegram/history-sync`
2. 为兼容前端和旧调用，`/system/scrape-monitor/telegram` 与 `/system/telegram/sync` 改为同义入口

请求体统一支持：

1. 账号参数：`api_id`、`api_hash`、`bot_token`、`proxy`
2. 频道参数：`channels`、`keywords`
3. 范围参数：`mode`、`relative_value`、`relative_unit`、`date_start`、`date_end`、`chunk_days`
4. 策略参数：`emit_events`

接口行为：

1. API 不直接执行长任务，而是通过 `event_bus.emit_background(...)` 投递后台任务。
2. 响应返回 `task_id`，前端通过 `/system/tasks` 观察执行状态。

## 启动与定时接入

### 启动

`startup_bootstrap_service.py` 不再直接调用 `sync_configured_channels(...)`，而是：

1. 根据保存配置组装历史同步请求
2. 发布 `EVENT_TELEGRAM_HISTORY_SYNC_REQUESTED`
3. 在启动流水结果中返回 `queued` + `task_id`

### 定时

`telegram_background_service.py` 不再自己抓消息与转存，而只负责：

1. 注册 APScheduler job
2. 由 job 发布 `EVENT_TELEGRAM_HISTORY_SYNC_REQUESTED`
3. 返回排队结果

## 页面调整

`app/web/templates/monitor.html` 拆成两个区域：

1. 实时监听设置
   包含启停、账号、代理、频道、关键字、过滤规则、自动 STRM
2. 历史同步设置
   包含范围模式、最近跨度、日期选择器、chunk_days、定时开关、定时间隔、立即同步按钮、任务状态区

前端逻辑：

1. 保存配置时写入 `monitor.telegram.history_sync`
2. 点击“立即同步”时调用统一历史同步接口
3. 页面轮询 `/api/v1/system/tasks` 展示最近的后台任务

## 验证计划

后端测试至少覆盖：

1. 范围解析：`all / relative_range / date_range`
2. chunk 生成
3. checkpoint 的创建、完成和失败恢复
4. 启动流程通过事件总线触发历史同步
5. 定时服务通过事件总线触发历史同步
6. 手动 API 正确投递后台任务
7. 实时监听流程不受影响

验证命令遵循当前仓库习惯，优先使用：

```bash
PYTHONPATH=. uv run pytest -q
```

## 实施边界

本次实施只解决 Telegram 监控体系收口，不额外做以下内容：

1. 不新增新的资源转存策略中心。
2. 不重构 Telethon runtime 基础工具。
3. 不改动已经稳定的 `tg_resources` 消息级入库结构。