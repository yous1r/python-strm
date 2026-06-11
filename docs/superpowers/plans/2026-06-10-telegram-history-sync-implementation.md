# Telegram 历史同步实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 Telegram 历史抓取改造成事件总线驱动的后台同步任务，并把界面与配置收口为“实时监听 + 历史同步”两块。

**架构：** 保留 `telegram_monitor` 负责实时 `NewMessage` 监听，引入新的 `telegram_history_sync_service` 负责范围解析、分片、checkpoint 和事件编排。启动、定时与手动入口统一发布历史同步请求事件，页面通过统一接口投递任务并复用任务观测接口展示状态。

**技术栈：** Python 3.12、FastAPI、Telethon、Pydantic、SQLite、Jinja2、APScheduler、pytest

---

### 任务 1：扩展事件与配置模型

**文件：**
- 修改：`app/events.py`
- 修改：`app/config.py`
- 测试：`tests/test_events.py`

- [ ] 添加 Telegram 历史同步事件常量到 `app/events.py`
- [ ] 在 `app/config.py` 新增 `TelegramHistorySyncConfig`，并挂到 `TelegramConfig.history_sync`
- [ ] 保留旧字段兼容，避免已有 YAML 加载失败
- [ ] 在 `tests/test_events.py` 增加事件常量稳定性断言

### 任务 2：增加 checkpoint 数据结构

**文件：**
- 修改：`app/database.py`
- 测试：`tests/test_telegram_monitor_state.py`

- [ ] 为 `init_db()` 增加 `telegram_history_sync_checkpoint` 表创建逻辑
- [ ] 增加 checkpoint 的 upsert / query / list helpers
- [ ] 为 checkpoint helper 增加单元测试，覆盖 completed 与 failed 状态写入

### 任务 3：实现历史同步编排服务

**文件：**
- 创建：`app/services/telegram_history_sync_service.py`
- 修改：`app/services/telegram_service.py`
- 测试：`tests/test_telegram_history_sync_service.py`

- [ ] 定义历史同步请求模型、chunk 模型和范围解析 helpers
- [ ] 实现 `all / relative_range / date_range` 时间窗口解析
- [ ] 实现按频道、按 chunk 的执行计划生成
- [ ] 实现 chunk 执行、checkpoint 更新和 resume 逻辑
- [ ] 实现事件订阅初始化函数与 `EVENT_TELEGRAM_HISTORY_SYNC_*` handler
- [ ] 收缩 `telegram_service.py`，让旧历史接口成为新服务的兼容包装或彻底不再被主流程使用

### 任务 4：切换启动链路与定时链路

**文件：**
- 修改：`app/services/startup_bootstrap_service.py`
- 修改：`app/services/telegram_background_service.py`
- 修改：`app/services/system_service.py`
- 修改：`app/main.py`
- 测试：`tests/test_startup_bootstrap_unittest.py`
- 测试：`tests/test_telegram_background_service.py`

- [ ] 启动流水改为投递历史同步请求事件，而不是直接调用旧抓取函数
- [ ] 定时服务改为注册 job 并投递历史同步请求事件
- [ ] 配置热加载后重新配置定时任务
- [ ] 在应用启动时初始化 Telegram 历史同步事件订阅器，并重新启用定时注册和启动流水

### 任务 5：重构系统 API

**文件：**
- 修改：`app/api/system.py`
- 测试：`tests/test_telegram_service.py`

- [ ] 新增统一请求模型 `TelegramHistorySyncRequest`
- [ ] 新增 `POST /system/telegram/history-sync`
- [ ] 让 `/system/scrape-monitor/telegram` 与 `/system/telegram/sync` 兼容转发到统一入口
- [ ] 保留状态/重启/连通性接口不变

### 任务 6：更新监控页面

**文件：**
- 修改：`app/web/templates/monitor.html`
- 测试：`tests/test_debug_api.py`

- [ ] 将页面拆成“实时监听设置”和“历史同步设置”两个视觉区块
- [ ] 增加历史同步模式选择、最近跨度输入、日期选择器、chunk_days、立即同步按钮
- [ ] 调整配置读写逻辑，改为使用 `monitor.telegram.history_sync`
- [ ] 增加任务状态区并轮询 `/api/v1/system/tasks`
- [ ] 更新页面测试断言到新字段 ID

### 任务 7：回归测试与收尾

**文件：**
- 修改：`tests/test_events.py`
- 修改：`tests/test_startup_bootstrap_unittest.py`
- 修改：`tests/test_telegram_background_service.py`
- 修改：`tests/test_telegram_service.py`
- 创建：`tests/test_telegram_history_sync_service.py`

- [ ] 运行 Telegram 相关测试，优先执行新服务、启动链路、API 和页面相关用例
- [ ] 运行更完整的 pytest 回归，确认实时监听现有测试不回归
- [ ] 检查日志、事件名、响应结构和配置默认值是否与规格一致