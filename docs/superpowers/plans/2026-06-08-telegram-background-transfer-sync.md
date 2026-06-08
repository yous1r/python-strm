# Telegram 后台监控转存与 115 全链路联动实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 Telegram 频道监控改造成后台定时增量抓取链路：最新消息入库到 `tg_resources`，按当前监控规则自动转存 115 链接，并在本批次新消息处理完成后判断未来 20 分钟内是否已经排期 `Cloud115FullSync`；若即将执行则跳过，否则主动触发一次 115 全链路同步。

**架构：** 保留 `app/core/monitor/telegram.py` 作为消息解析与归档入口，但把“批量抓取最新消息 -> 收集新增资源 -> 顺序转存 -> 条件触发 115 全链路同步”抽到独立后台服务。Telegram 后台任务不再查询过去执行历史，而是直接检查 APScheduler 中 `db_sync` 任务的 `next_run_time`：如果未来 20 分钟内已经会执行 `Cloud115FullSync`，则跳过本次追加触发；否则再调用 `cloud115_full_sync_service.start_full_sync(source="telegram_monitor")`。同时把固定的 `Cloud115FullSync` 周期统一收敛为每 2 小时一次。

**技术栈：** Python、FastAPI、Telethon、APScheduler、SQLite、pytest、aiosqlite

---

## 文件结构

**修改文件：**
- `app/config.py`
  - 为 Telegram 后台定时抓取增加专属配置项。
- `app/database.py`
  - 增强 `tg_resources` 入库返回值，支持后续后台批量转存编排。
- `app/core/monitor/telegram.py`
  - 将消息归档返回值从“裸链接列表”升级为“已入库资源列表”，让后续链路能拿到 `db_id` 和状态字段。
- `app/core/monitor/handler.py`
  - 收敛为事件入口适配层，复用统一的 Telegram 资源转存服务，移除每条消息单独触发 STRM 刷新的职责。
- `app/services/telegram_service.py`
  - 扩展历史同步接口，支持返回本次新增资源明细，供后台定时任务复用。
- `app/services/cloud115_full_sync_service.py`
  - 统一 115 全链路同步的调度来源标识，兼容“固定调度”和“Telegram 追加触发”。
- `app/main.py`
  - 注册 Telegram 后台定时任务，并把 `Cloud115FullSync` 固定调度调整为每 2 小时一次。
- `app/services/system_service.py`
  - 让 Telegram 配置热更新时能重建后台定时任务。
- `app/utils/scheduler.py`
  - 暴露查询 job 的辅助函数，供 Telegram 后台任务判断未来 20 分钟内是否已排期全量同步。

**新增文件：**
- `app/services/telegram_resource_transfer_service.py`
  - 单条 `tg_resources` 资源的 115 转存执行器，统一状态更新与返回结构。
- `app/services/telegram_background_service.py`
  - 后台定时抓取编排器，负责抓取、转存、全链路同步去重触发。

**测试文件：**
- `tests/test_telegram_runtime.py`
  - Telegram 后台调度配置默认值测试。
- `tests/test_telegram_monitor_state.py`
  - `tg_resources` 入库返回值测试。
- `tests/test_telegram_monitor.py`
  - 归档返回资源明细的行为测试。
- `tests/test_telegram_service.py`
  - 增量抓取返回新增资源列表与状态推进测试。
- `tests/test_telegram_background_service.py`
  - 后台定时任务的抓取、转存、未来 20 分钟排期跳过逻辑测试。
- `tests/test_cloud115_full_sync_service.py`
  - 115 全链路同步来源标识与调度兼容性测试。

---

### 任务 1：扩展 Telegram 后台调度配置

**文件：**
- 修改：`app/config.py`
- 测试：`tests/test_telegram_runtime.py`

- [ ] **步骤 1：编写失败的配置测试**

```python
from app.config import TelegramConfig


def test_telegram_config_defaults_for_background_sync():
    cfg = TelegramConfig()

    assert cfg.scheduled_sync_enabled is True
    assert cfg.scheduled_sync_interval_minutes == 5
    assert cfg.scheduled_sync_limit == 20
    assert cfg.full_sync_skip_if_scheduled_within_minutes == 20
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_telegram_runtime.py::test_telegram_config_defaults_for_background_sync -v`
预期：`FAIL`，报错 `TelegramConfig` 缺少 `scheduled_sync_enabled` 或 `full_sync_skip_if_scheduled_within_minutes` 等新字段。

- [ ] **步骤 3：编写最少实现代码**

```python
class TelegramConfig(BaseModel):
    enabled: bool = False
    api_id: str = ""
    api_hash: str = ""
    bot_token: str = ""
    channels: List[str] = []
    proxy: str = ""
    keywords: List[str] = []
    filter_rules: List[str] = []
    target_dir_id: str = "0"
    archive_dir_id: str = "0"
    auto_organize: bool = False
    auto_strm: bool = False
    mode: str = "auto"
    startup_sync: str = "latest"
    history_limit: int = 100
    reconnect_backoff: int = 5
    scheduled_sync_enabled: bool = True
    scheduled_sync_interval_minutes: int = 5
    scheduled_sync_limit: int = 20
    full_sync_skip_if_scheduled_within_minutes: int = 20
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_telegram_runtime.py::test_telegram_config_defaults_for_background_sync -v`
预期：`PASS`

- [ ] **步骤 5：Commit**

```bash
git add app/config.py tests/test_telegram_runtime.py
git commit -m "feat: add telegram background sync config"
```

### 任务 2：让 Telegram 消息归档返回可执行资源明细

**文件：**
- 修改：`app/database.py`
- 修改：`app/core/monitor/telegram.py`
- 测试：`tests/test_telegram_monitor_state.py`
- 测试：`tests/test_telegram_monitor.py`

- [ ] **步骤 1：编写失败的归档测试**

```python
import pytest


@pytest.mark.asyncio
async def test_ingest_message_returns_inserted_resource_payload(monkeypatch):
    from app.core.monitor.telegram import TelegramMonitor

    monitor = TelegramMonitor()

    async def fake_insert(db, resource):
        return {
            "id": 12,
            "message_id": resource["message_id"],
            "channel_id": resource["channel_id"],
            "link": resource["link"],
            "disk_type": resource["disk_type"],
            "status": resource["status"],
            "title": resource["title"],
        }

    monkeypatch.setattr("app.core.monitor.telegram.extract_title_from_text", lambda text: "示例标题")
    monkeypatch.setattr("app.core.monitor.telegram.insert_tg_resource", fake_insert)

    resources = await monitor.ingest_message(
        "示例标题 https://115.com/s/abc 密码 abcd",
        message_id=8,
        channel_id="-100123",
        msg_date="2026-06-08T09:00:00",
    )

    assert resources == [{
        "db_id": 12,
        "message_id": 8,
        "channel_id": "-100123",
        "link": "https://115.com/s/abc",
        "disk_type": "115",
        "status": "pending",
        "title": "示例标题",
    }]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_telegram_monitor.py::test_ingest_message_returns_inserted_resource_payload -v`
预期：`FAIL`，当前 `ingest_message()` 只返回链接列表，不包含 `db_id`、`status`、`title`。

- [ ] **步骤 3：编写最少实现代码**

```python
async def insert_tg_resource(db, resource: dict) -> dict | None:
    cursor = await db.execute(
        '''
        INSERT OR IGNORE INTO tg_resources
        (message_id, channel_id, title, raw_text, link, password, disk_type, msg_date, status, base_title, poster_url, overview, cast_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (...),
    )
    if cursor.rowcount <= 0:
        return None

    async with db.execute("SELECT * FROM tg_resources WHERE id = ?", (cursor.lastrowid,)) as read_cursor:
        row = await read_cursor.fetchone()
        return dict(row) if row else None


inserted = await insert_tg_resource(db, resource)
if inserted:
    new_links.append({
        "db_id": inserted["id"],
        "message_id": inserted["message_id"],
        "channel_id": inserted["channel_id"],
        "link": inserted["link"],
        "password": inserted["password"],
        "disk_type": inserted["disk_type"],
        "status": inserted["status"],
        "title": inserted["title"],
        "raw_text": inserted["raw_text"],
    })
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_telegram_monitor.py -v`
预期：`PASS`

- [ ] **步骤 5：Commit**

```bash
git add app/database.py app/core/monitor/telegram.py tests/test_telegram_monitor.py tests/test_telegram_monitor_state.py
git commit -m "feat: return archived telegram resource payloads"
```

### 任务 3：抽取可复用的 Telegram 资源转存执行器

**文件：**
- 新增：`app/services/telegram_resource_transfer_service.py`
- 修改：`app/core/monitor/handler.py`
- 测试：`tests/test_telegram_background_service.py`

- [ ] **步骤 1：编写失败的转存服务测试**

```python
import pytest


@pytest.mark.asyncio
async def test_process_resource_updates_tg_status_and_returns_success(monkeypatch):
    from app.services.telegram_resource_transfer_service import process_tg_resource

    resource = {
        "db_id": 5,
        "link": "https://115.com/s/abc",
        "password": "abcd",
        "disk_type": "115",
        "title": "示例标题",
        "raw_text": "示例标题 https://115.com/s/abc",
    }

    monkeypatch.setattr(
        "app.services.telegram_resource_transfer_service.client_115.share_receive",
        pytest.AsyncMock(return_value={"state": True}),
    )
    updated = []
    monkeypatch.setattr(
        "app.services.telegram_resource_transfer_service._update_tg_status",
        pytest.AsyncMock(side_effect=lambda db_id, status: updated.append((db_id, status))),
    )

    result = await process_tg_resource(resource)

    assert result["status"] == "success"
    assert updated[-1] == (5, "success")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_telegram_background_service.py::test_process_resource_updates_tg_status_and_returns_success -v`
预期：`FAIL`，缺少 `process_tg_resource()` 服务。

- [ ] **步骤 3：编写最少实现代码**

```python
async def process_tg_resource(resource: dict[str, object]) -> dict[str, object]:
    if resource.get("disk_type") != "115":
        return {"status": "skipped", "reason": "non-115"}

    await _update_tg_status(resource.get("db_id"), "queued")
    transfer_res = await client_115.share_receive(
        resource["link"],
        resource.get("password", ""),
        _resolve_target_dir(resource),
        filter_rules=_resolve_filter_rules(resource),
    )
    if not transfer_res.get("state"):
        await _update_tg_status(resource.get("db_id"), "failed")
        return {"status": "failed", "error": transfer_res.get("error", "transfer failed")}

    await _update_tg_status(resource.get("db_id"), "success")
    return {"status": "success", "db_id": resource.get("db_id"), "link": resource["link"]}


async def handle_new_link(link_data: dict, source: str, **kwargs):
    await process_tg_resource(link_data)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_telegram_background_service.py::test_process_resource_updates_tg_status_and_returns_success -v`
预期：`PASS`

- [ ] **步骤 5：Commit**

```bash
git add app/services/telegram_resource_transfer_service.py app/core/monitor/handler.py tests/test_telegram_background_service.py
git commit -m "feat: extract telegram resource transfer service"
```

### 任务 4：实现 Telegram 后台定时抓取与批量转存编排

**文件：**
- 新增：`app/services/telegram_background_service.py`
- 修改：`app/services/telegram_service.py`
- 测试：`tests/test_telegram_service.py`
- 测试：`tests/test_telegram_background_service.py`

- [ ] **步骤 1：编写失败的后台抓取测试**

```python
import pytest


@pytest.mark.asyncio
async def test_run_scheduled_sync_fetches_resources_and_transfers_them(monkeypatch):
    from app.services.telegram_background_service import telegram_background_service

    channel_results = [
        {
            "channel_ref": "@demo",
            "processed": 2,
            "inserted": 2,
            "resources": [
                {"db_id": 1, "disk_type": "115", "link": "https://115.com/s/abc", "password": "abcd"},
                {"db_id": 2, "disk_type": "123", "link": "https://www.123pan.com/s/a-b.html", "password": "efgh"},
            ],
        }
    ]

    monkeypatch.setattr(
        "app.services.telegram_background_service.sync_configured_channels",
        pytest.AsyncMock(return_value={"status": "success", "channels": channel_results}),
    )
    processed = []
    monkeypatch.setattr(
        "app.services.telegram_background_service.process_tg_resource",
        pytest.AsyncMock(side_effect=lambda resource: processed.append(resource) or {"status": "success", "db_id": resource["db_id"]}),
    )
    monkeypatch.setattr(
        "app.services.telegram_background_service._trigger_full_sync_if_needed",
        pytest.AsyncMock(return_value={"status": "skipped", "reason": "cooldown"}),
    )

    result = await telegram_background_service.run_scheduled_sync()

    assert result["channels"][0]["inserted"] == 2
    assert [item["db_id"] for item in processed] == [1]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_telegram_background_service.py::test_run_scheduled_sync_fetches_resources_and_transfers_them -v`
预期：`FAIL`，缺少后台编排服务，且当前 `sync_configured_channels()` 不返回 `resources`。

- [ ] **步骤 3：编写最少实现代码**

```python
async def _dispatch_scraped_message(channel: int | str, message, *, emit_events: bool = True) -> list[dict]:
    resources = await telegram_monitor.ingest_message(
        extract_message_text(message),
        message_id=message.id,
        channel_id=str(getattr(message, "chat_id", channel)),
        msg_date=str(message.date),
    )
    if emit_events:
        for resource in resources:
            event_bus.emit_background(EVENT_MONITOR_NEW_LINK, link_data=resource, source="telegram")
    return resources


async def _sync_channel_history(..., emit_events: bool = True, limit: int = 100) -> dict[str, object]:
    inserted_resources: list[dict] = []
    ...
    new_resources = await _dispatch_scraped_message(parsed_channel, message, emit_events=emit_events)
    inserted_resources.extend(new_resources)
    inserted += len(new_resources)
    ...
    return {
        "channel_ref": channel_ref,
        "processed": processed,
        "inserted": inserted,
        "resources": inserted_resources,
        "last_message_id": highest_id,
    }


class TelegramBackgroundService:
    async def run_scheduled_sync(self) -> dict[str, object]:
        cfg = get_config().monitor.telegram
        sync_result = await sync_configured_channels(
            cfg.api_id,
            cfg.api_hash,
            bot_token=cfg.bot_token,
            proxy=cfg.proxy,
            channels=cfg.channels,
            keywords=cfg.keywords,
            emit_events=False,
            startup_mode="incremental",
        )
        transfer_results = []
        for channel_result in sync_result.get("channels", []):
            for resource in channel_result.get("resources", []):
                if resource.get("disk_type") != "115":
                    continue
                transfer_results.append(await process_tg_resource(resource))
        followup = await self._trigger_full_sync_if_needed(transfer_results)
        return {"status": "success", "channels": sync_result.get("channels", []), "transfers": transfer_results, "followup": followup}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_telegram_service.py tests/test_telegram_background_service.py -v`
预期：`PASS`

- [ ] **步骤 5：Commit**

```bash
git add app/services/telegram_service.py app/services/telegram_background_service.py tests/test_telegram_service.py tests/test_telegram_background_service.py
git commit -m "feat: add telegram scheduled sync pipeline"
```

### 任务 5：为 Telegram 追加同步增加未来 20 分钟排期避让

**文件：**
- 修改：`app/main.py`
- 修改：`app/services/telegram_background_service.py`
- 修改：`app/utils/scheduler.py`
- 测试：`tests/test_cloud115_full_sync_service.py`
- 测试：`tests/test_telegram_background_service.py`

- [ ] **步骤 1：编写失败的排期避让测试**

```python
import pytest
from datetime import datetime, timedelta, timezone


@pytest.mark.asyncio
async def test_trigger_full_sync_skips_when_job_scheduled_within_20_minutes(monkeypatch):
    from app.services.telegram_background_service import telegram_background_service

    next_run_time = datetime.now(timezone.utc) + timedelta(minutes=15)
    monkeypatch.setattr(
        "app.services.telegram_background_service.get_job",
        lambda job_id: type("Job", (), {"next_run_time": next_run_time})() if job_id == "db_sync" else None,
    )
    start_calls = []
    monkeypatch.setattr(
        "app.services.telegram_background_service.cloud115_full_sync_service.start_full_sync",
        pytest.AsyncMock(side_effect=lambda source: start_calls.append(source) or {"status": "running"}),
    )

    result = await telegram_background_service._trigger_full_sync_if_needed([
        {"status": "success", "db_id": 1}
    ])

    assert result["status"] == "skipped"
    assert result["reason"] == "full sync already scheduled soon"
    assert start_calls == []
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_telegram_background_service.py::test_trigger_full_sync_skips_when_job_scheduled_within_20_minutes -v`
预期：`FAIL`，当前实现尚未检查 APScheduler 中 `db_sync` 的未来执行时间。

- [ ] **步骤 3：编写最少实现代码**

```python
def get_job(job_id: str):
    return _scheduler.get_job(job_id)


def _has_upcoming_full_sync(window_minutes: int) -> bool:
    job = get_job("db_sync")
    if not job or not getattr(job, "next_run_time", None):
        return False

    now = datetime.now(job.next_run_time.tzinfo or timezone.utc)
    return job.next_run_time <= now + timedelta(minutes=window_minutes)


if _has_upcoming_full_sync(cfg.full_sync_skip_if_scheduled_within_minutes):
    return {"status": "skipped", "reason": "full sync already scheduled soon"}
return await cloud115_full_sync_service.start_full_sync(source="telegram_monitor")
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_cloud115_full_sync_service.py tests/test_telegram_background_service.py -v`
预期：`PASS`

- [ ] **步骤 5：Commit**

```bash
git add app/main.py app/utils/scheduler.py app/services/telegram_background_service.py tests/test_cloud115_full_sync_service.py tests/test_telegram_background_service.py
git commit -m "feat: skip telegram followup sync when full sync is scheduled soon"
```

### 任务 6：接入 APScheduler 与配置热更新

**文件：**
- 修改：`app/main.py`
- 修改：`app/services/system_service.py`
- 测试：`tests/test_startup_bootstrap_unittest.py`
- 测试：`tests/test_telegram_background_service.py`

- [ ] **步骤 1：编写失败的调度注册测试**

```python
def test_app_registers_telegram_background_job(monkeypatch):
    from app.main import lifespan

    added_jobs = []

    monkeypatch.setattr("app.main.add_job", lambda func, trigger, **kwargs: added_jobs.append((func, trigger, kwargs)))
    monkeypatch.setattr(
        "app.main.get_config",
        lambda: type(
            "Cfg",
            (),
            {
                "monitor": type(
                    "Monitor",
                    (),
                    {
                        "poll_interval": 60,
                        "startup_pipeline": type("Startup", (), {"enabled": False})(),
                        "telegram": type(
                            "Telegram",
                            (),
                            {
                                "enabled": True,
                                "scheduled_sync_enabled": True,
                                "scheduled_sync_interval_minutes": 5,
                            },
                        )(),
                    },
                )(),
            },
        )(),
    )

    assert any(job[2]["id"] == "telegram_background_sync" for job in added_jobs)
    assert any(job[2]["id"] == "db_sync" and job[2].get("hours") == 2 for job in added_jobs)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_startup_bootstrap_unittest.py tests/test_telegram_background_service.py -v`
预期：`FAIL`，当前应用未注册 Telegram 后台抓取任务，且 `db_sync` 仍然是每 6 小时一次。

- [ ] **步骤 3：编写最少实现代码**

```python
add_job(
    telegram_background_service.run_scheduled_sync,
    "interval",
    minutes=max(1, config.monitor.telegram.scheduled_sync_interval_minutes),
    id="telegram_background_sync",
    replace_existing=True,
)

add_job(
    cloud115_full_sync_service.run_scheduled_full_sync,
    "interval",
    hours=2,
    id="db_sync",
    replace_existing=True,
)


def _telegram_monitor_changed(changed_data: dict, old_config, new_config) -> bool:
    if "monitor" not in changed_data or "telegram" not in changed_data["monitor"]:
        return False
    return old_config.monitor.telegram.model_dump() != new_config.monitor.telegram.model_dump()


if _telegram_monitor_changed(changed_data, old_config, new_config):
    from app.utils.scheduler import remove_job, add_job
    from app.services.telegram_background_service import telegram_background_service

    remove_job("telegram_background_sync")
    if new_config.monitor.telegram.enabled and new_config.monitor.telegram.scheduled_sync_enabled:
        add_job(
            telegram_background_service.run_scheduled_sync,
            "interval",
            minutes=max(1, new_config.monitor.telegram.scheduled_sync_interval_minutes),
            id="telegram_background_sync",
            replace_existing=True,
        )
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_startup_bootstrap_unittest.py tests/test_telegram_background_service.py tests/test_telegram_service.py tests/test_cloud115_full_sync_service.py -v`
预期：`PASS`

- [ ] **步骤 5：Commit**

```bash
git add app/main.py app/services/system_service.py tests/test_startup_bootstrap_unittest.py tests/test_telegram_background_service.py tests/test_telegram_service.py tests/test_cloud115_full_sync_service.py
git commit -m "feat: schedule telegram background sync job"
```

### 任务 7：执行最终回归验证

**文件：**
- 修改：无
- 测试：`tests/test_telegram_runtime.py`
- 测试：`tests/test_telegram_monitor_state.py`
- 测试：`tests/test_telegram_monitor.py`
- 测试：`tests/test_telegram_service.py`
- 测试：`tests/test_telegram_background_service.py`
- 测试：`tests/test_cloud115_full_sync_service.py`
- 测试：`tests/test_startup_bootstrap_unittest.py`

- [ ] **步骤 1：运行 Telegram/115 相关定向测试**

运行：

```bash
pytest \
  tests/test_telegram_runtime.py \
  tests/test_telegram_monitor_state.py \
  tests/test_telegram_monitor.py \
  tests/test_telegram_service.py \
  tests/test_telegram_background_service.py \
  tests/test_cloud115_full_sync_service.py \
  tests/test_startup_bootstrap_unittest.py -v
```

预期：全部 `PASS`，没有新增失败。

- [ ] **步骤 2：运行 Python 语法校验**

运行：

```bash
python -m py_compile \
  app/config.py \
  app/database.py \
  app/core/monitor/telegram.py \
  app/core/monitor/handler.py \
  app/services/telegram_service.py \
  app/services/telegram_resource_transfer_service.py \
  app/services/telegram_background_service.py \
  app/services/cloud115_full_sync_service.py \
  app/main.py \
  app/services/system_service.py
```

预期：命令退出码为 `0`。

- [ ] **步骤 3：整理变更并提交**

```bash
git add app/config.py app/database.py app/core/monitor/telegram.py app/core/monitor/handler.py app/services/telegram_service.py app/services/telegram_resource_transfer_service.py app/services/telegram_background_service.py app/services/cloud115_full_sync_service.py app/main.py app/services/system_service.py tests/test_telegram_runtime.py tests/test_telegram_monitor_state.py tests/test_telegram_monitor.py tests/test_telegram_service.py tests/test_telegram_background_service.py tests/test_cloud115_full_sync_service.py tests/test_startup_bootstrap_unittest.py
git commit -m "feat: automate telegram monitoring transfer and full sync followup"
```

---

## 自检结果

- 已覆盖需求 1：通过 `telegram_background_service` + APScheduler 实现后台定时抓取最新消息。
- 已覆盖需求 2：通过 `ingest_message()` 与 `insert_tg_resource()` 返回入库资源明细，确保最新消息归档到 `tg_resources`。
- 已覆盖需求 3：通过 `telegram_resource_transfer_service.py` 统一按当前监控规则执行 115 转存。
- 已覆盖需求 4：通过 APScheduler 的 `db_sync.next_run_time` 判断未来 20 分钟内是否即将执行 `Cloud115FullSync`，若没有才触发 `cloud115_full_sync_service.start_full_sync(source="telegram_monitor")`。
- 已覆盖新增要求：把固定 `Cloud115FullSync` 调度统一调整为每 2 小时一次。
- 计划内没有使用 `TODO`、`待补充`、`后续实现` 等占位符，所有任务都给出了具体文件、代码片段、测试命令和提交命令。

## 执行交接

计划已完成并保存到 `docs/superpowers/plans/2026-06-08-telegram-background-transfer-sync.md`。两种执行方式：

**1. 子代理驱动（推荐）** - 每个任务调度一个新的子代理，任务间进行审查，快速迭代

**2. 内联执行** - 在当前会话中使用 executing-plans 执行任务，批量执行并设有检查点

选哪种方式？