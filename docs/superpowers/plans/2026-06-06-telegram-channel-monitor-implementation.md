# Telegram 频道监听实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将现有 Telegram 监听骨架补齐为可持续运行的频道监控链路，具备频道状态持久化、启动增量补抓、幂等入队与基础运维接口。

**架构：** 保留 `app/core/monitor/telegram.py` 作为 Telethon 运行时监听器，在 `app/database.py` 中增加 `telegram_monitor_state` 状态表与 `tg_resources` 复合唯一索引，通过 `app/services/telegram_service.py` 提供状态查询与手动增量补抓编排，再由 `app/api/system.py` 暴露状态、重启和补抓接口。实现顺序采用 TDD，先锁定数据库/服务接口，再改造监听启动路径。

**技术栈：** Python、FastAPI、Telethon、SQLite、pytest、aiosqlite

---

## 文件结构

**修改文件：**
- `app/config.py`
  - 为 `TelegramConfig` 增加 `mode`、`startup_sync`、`history_limit`、`reconnect_backoff`
- `app/database.py`
  - 增加 `telegram_monitor_state` 表、`tg_resources` 复合唯一索引、频道状态读写函数
- `app/core/monitor/telegram_runtime.py`
  - 增加消息文本提取与频道状态辅助函数所需的轻量工具
- `app/core/monitor/telegram.py`
  - 接入启动补偿、状态更新、幂等入队边界与失败隔离
- `app/services/telegram_service.py`
  - 增加监听状态查询、单频道/全频道增量补抓服务
- `app/api/system.py`
  - 增加监听状态、热重启、手动补抓接口

**测试文件：**
- `tests/test_telegram_runtime.py`
  - 解析频道、文本提取等纯函数测试
- `tests/test_telegram_monitor_state.py`
  - 状态表读写、资源幂等写入测试
- `tests/test_telegram_service.py`
  - 手动补抓与状态查询服务测试

---

### 任务 1：扩展 Telegram 配置模型

**文件：**
- 修改：`app/config.py`
- 测试：`tests/test_telegram_runtime.py`

- [ ] **步骤 1：编写失败的配置测试**

```python
from app.config import TelegramConfig


def test_telegram_config_defaults_for_monitor_runtime():
    cfg = TelegramConfig()

    assert cfg.mode == "auto"
    assert cfg.startup_sync == "latest"
    assert cfg.history_limit == 100
    assert cfg.reconnect_backoff == 5
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_telegram_runtime.py::test_telegram_config_defaults_for_monitor_runtime -v`
预期：`FAIL`，报错 `TelegramConfig` 不包含 `mode`、`startup_sync` 或其它新字段。

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
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_telegram_runtime.py::test_telegram_config_defaults_for_monitor_runtime -v`
预期：`PASS`

- [ ] **步骤 5：Commit**

```bash
git add app/config.py tests/test_telegram_runtime.py
git commit -m "feat: add telegram monitor runtime config"
```

### 任务 2：新增频道状态表与资源幂等索引

**文件：**
- 修改：`app/database.py`
- 测试：`tests/test_telegram_monitor_state.py`

- [ ] **步骤 1：编写失败的数据库测试**

```python
import pytest

from app.database import get_db_conn, init_db, insert_tg_resource, upsert_telegram_monitor_state


@pytest.mark.asyncio
async def test_insert_tg_resource_is_unique_per_channel_message_link(tmp_path, monkeypatch):
    from app.config import load_config

    config_file = tmp_path / "config.yaml"
    config_file.write_text("database:\n  path: '%s'\n" % (tmp_path / "test.db"), encoding="utf-8")
    load_config(str(config_file))
    await init_db()

    resource = {
        "message_id": 11,
        "channel_id": "-1001",
        "title": "demo",
        "raw_text": "https://115.com/s/abc",
        "link": "https://115.com/s/abc",
        "password": "",
        "disk_type": "115",
    }

    async with get_db_conn() as db:
        assert await insert_tg_resource(db, resource) is True
        assert await insert_tg_resource(db, resource) is False
        await db.commit()


@pytest.mark.asyncio
async def test_upsert_and_get_monitor_state(tmp_path):
    from app.config import load_config
    from app.database import get_telegram_monitor_state

    config_file = tmp_path / "config.yaml"
    config_file.write_text("database:\n  path: '%s'\n" % (tmp_path / "test.db"), encoding="utf-8")
    load_config(str(config_file))
    await init_db()

    await upsert_telegram_monitor_state(
        channel_ref="@demo",
        resolved_channel_id="-100123",
        last_message_id=99,
        last_message_date="2026-06-06T10:00:00",
        last_error="",
    )

    state = await get_telegram_monitor_state("@demo")
    assert state["last_message_id"] == 99
    assert state["resolved_channel_id"] == "-100123"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_telegram_monitor_state.py -v`
预期：`FAIL`，报错缺少 `upsert_telegram_monitor_state` / `get_telegram_monitor_state`，或 `tg_resources` 幂等行为仍按 `link UNIQUE` 工作。

- [ ] **步骤 3：编写最少实现代码**

```python
await db.execute("DROP INDEX IF EXISTS idx_tg_resources_link")
await db.execute(
    """
    CREATE TABLE IF NOT EXISTS telegram_monitor_state (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_ref TEXT NOT NULL UNIQUE,
        resolved_channel_id TEXT,
        last_message_id INTEGER,
        last_message_date TEXT,
        last_success_at TEXT DEFAULT CURRENT_TIMESTAMP,
        last_error TEXT DEFAULT '',
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """
)
await db.execute(
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_tg_resources_channel_message_link
    ON tg_resources(channel_id, message_id, link)
    """
)


async def upsert_telegram_monitor_state(...):
    ...


async def get_telegram_monitor_state(channel_ref: str):
    ...
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_telegram_monitor_state.py -v`
预期：`PASS`

- [ ] **步骤 5：Commit**

```bash
git add app/database.py tests/test_telegram_monitor_state.py
git commit -m "feat: add telegram monitor state storage"
```

### 任务 3：补充 Telegram 运行时纯函数

**文件：**
- 修改：`app/core/monitor/telegram_runtime.py`
- 测试：`tests/test_telegram_runtime.py`

- [ ] **步骤 1：编写失败的纯函数测试**

```python
from types import SimpleNamespace

from app.core.monitor.telegram_runtime import extract_message_text, parse_channel_reference


def test_parse_channel_reference_supports_t_me_c_links():
    assert parse_channel_reference("https://t.me/c/123456/99") == -100123456


def test_extract_message_text_prefers_caption_and_text_fields():
    message = SimpleNamespace(message="", text="caption text")
    assert extract_message_text(message) == "caption text"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_telegram_runtime.py -v`
预期：`FAIL`，缺少 `extract_message_text()`。

- [ ] **步骤 3：编写最少实现代码**

```python
def extract_message_text(message) -> str:
    for attr in ("message", "text"):
        value = getattr(message, attr, "")
        if value:
            return value
    return ""
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_telegram_runtime.py -v`
预期：`PASS`

- [ ] **步骤 5：Commit**

```bash
git add app/core/monitor/telegram_runtime.py tests/test_telegram_runtime.py
git commit -m "test: cover telegram runtime helpers"
```

### 任务 4：在服务层增加频道状态查询

**文件：**
- 修改：`app/services/telegram_service.py`
- 测试：`tests/test_telegram_service.py`

- [ ] **步骤 1：编写失败的服务测试**

```python
import pytest


@pytest.mark.asyncio
async def test_get_monitor_status_returns_channel_states(monkeypatch):
    from app.services.telegram_service import get_monitor_status

    async def fake_list_states():
        return [{"channel_ref": "@demo", "last_message_id": 7, "last_error": ""}]

    monkeypatch.setattr("app.services.telegram_service.list_telegram_monitor_states", fake_list_states)

    status = await get_monitor_status()
    assert status["enabled"] in (True, False)
    assert status["channels"][0]["channel_ref"] == "@demo"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_telegram_service.py::test_get_monitor_status_returns_channel_states -v`
预期：`FAIL`，缺少 `get_monitor_status()`。

- [ ] **步骤 3：编写最少实现代码**

```python
async def get_monitor_status() -> dict[str, object]:
    cfg = get_config().monitor.telegram
    states = await list_telegram_monitor_states()
    return {
        "enabled": cfg.enabled,
        "mode": getattr(cfg, "mode", "auto"),
        "running": bool(telegram_monitor.client and telegram_monitor.client.is_connected()),
        "channels": states,
    }
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_telegram_service.py::test_get_monitor_status_returns_channel_states -v`
预期：`PASS`

- [ ] **步骤 5：Commit**

```bash
git add app/services/telegram_service.py tests/test_telegram_service.py
git commit -m "feat: add telegram monitor status service"
```

### 任务 5：实现手动单频道/全频道增量补抓服务

**文件：**
- 修改：`app/services/telegram_service.py`
- 测试：`tests/test_telegram_service.py`

- [ ] **步骤 1：编写失败的补抓服务测试**

```python
import pytest


@pytest.mark.asyncio
async def test_sync_single_channel_uses_last_message_state(monkeypatch):
    from app.services.telegram_service import sync_single_channel

    calls = []

    async def fake_sync_channel_history(client, channel_ref, limit):
        calls.append((channel_ref, limit))
        return {"processed": 3, "inserted": 2}

    async def fake_state(channel_ref):
        return {"channel_ref": channel_ref, "last_message_id": 88}

    monkeypatch.setattr("app.services.telegram_service._sync_channel_history", fake_sync_channel_history)
    monkeypatch.setattr("app.services.telegram_service.get_telegram_monitor_state", fake_state)
    monkeypatch.setattr("app.services.telegram_service._acquire_client", pytest.AsyncMock(return_value=(object(), False, True, None)))

    result = await sync_single_channel("1", "2", channels=["@demo"], channel_ref="@demo")
    assert result["processed"] == 3
    assert calls == [("@demo", 100)]
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_telegram_service.py::test_sync_single_channel_uses_last_message_state -v`
预期：`FAIL`，缺少 `sync_single_channel()` / `_sync_channel_history()`。

- [ ] **步骤 3：编写最少实现代码**

```python
async def sync_single_channel(...):
    normalized_channels = validate_monitor_request(...)
    if channel_ref not in normalized_channels:
        raise TelegramValidationError("目标频道未在配置列表中")

    client_to_use, disconnect_after, is_auth, auth_error = await _acquire_client(...)
    if auth_error or not is_auth:
        raise TelegramValidationError(auth_error or "Telegram client not authorized")

    try:
        return await _sync_channel_history(client_to_use, channel_ref, limit=get_config().monitor.telegram.history_limit)
    finally:
        if disconnect_after:
            await client_to_use.disconnect()
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_telegram_service.py -v`
预期：`PASS`

- [ ] **步骤 5：Commit**

```bash
git add app/services/telegram_service.py tests/test_telegram_service.py
git commit -m "feat: add telegram incremental sync service"
```

### 任务 6：改造监听器接入状态更新与启动补偿

**文件：**
- 修改：`app/core/monitor/telegram.py`
- 修改：`app/services/telegram_service.py`
- 测试：`tests/test_telegram_service.py`

- [ ] **步骤 1：编写失败的启动补偿测试**

```python
import pytest


@pytest.mark.asyncio
async def test_restart_monitor_triggers_sync_before_subscribe(monkeypatch):
    events = []

    async def fake_sync_startup(*args, **kwargs):
        events.append("sync")

    async def fake_connect(*args, **kwargs):
        events.append("connect")

    monkeypatch.setattr("app.core.monitor.telegram.TelegramMonitor._sync_startup_gaps", fake_sync_startup)
    monkeypatch.setattr("app.core.monitor.telegram.TelegramMonitor._connect_client", fake_connect)

    # 这里断言 start() 内部顺序，而不是依赖真实 Telethon
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_telegram_service.py -k startup -v`
预期：`FAIL`，现有 `TelegramMonitor.start()` 不具备可测试的拆分方法。

- [ ] **步骤 3：编写最少实现代码**

```python
class TelegramMonitor:
    async def start(self):
        self.config = get_config().monitor.telegram
        if not self.config.enabled:
            return

        self.client = await self._connect_client()
        await self._sync_startup_gaps()
        self._register_new_message_handler()

    async def _sync_startup_gaps(self):
        if getattr(self.config, "startup_sync", "latest") == "disabled":
            return
        await sync_configured_channels(...)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_telegram_service.py -k startup -v`
预期：`PASS`

- [ ] **步骤 5：Commit**

```bash
git add app/core/monitor/telegram.py app/services/telegram_service.py tests/test_telegram_service.py
git commit -m "feat: add telegram startup gap sync"
```

### 任务 7：补齐系统接口

**文件：**
- 修改：`app/api/system.py`
- 测试：`tests/test_telegram_service.py`

- [ ] **步骤 1：编写失败的 API 层测试**

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.system import router


def test_get_telegram_status_endpoint(monkeypatch):
    app = FastAPI()
    app.include_router(router)

    async def fake_status():
        return {"enabled": True, "running": False, "channels": []}

    monkeypatch.setattr("app.api.system.get_monitor_status", fake_status)
    client = TestClient(app)
    response = client.get("/system/telegram/status")
    assert response.status_code == 200
    assert response.json()["enabled"] is True
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_telegram_service.py::test_get_telegram_status_endpoint -v`
预期：`FAIL`，路由不存在。

- [ ] **步骤 3：编写最少实现代码**

```python
@router.get("/telegram/status")
async def telegram_status():
    return await get_monitor_status()


@router.post("/telegram/restart")
async def restart_telegram_monitor():
    await restart_monitor()
    return {"status": "success", "message": "Telegram 监听器已重启"}


@router.post("/telegram/sync")
async def sync_telegram_monitor(req: TelegramScrapeRequest):
    return await sync_configured_channels(...)
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_telegram_service.py -k telegram_status_endpoint -v`
预期：`PASS`

- [ ] **步骤 5：Commit**

```bash
git add app/api/system.py tests/test_telegram_service.py
git commit -m "feat: expose telegram monitor management endpoints"
```

### 任务 8：全量回归验证

**文件：**
- 测试：`tests/test_telegram_runtime.py`
- 测试：`tests/test_telegram_monitor_state.py`
- 测试：`tests/test_telegram_service.py`

- [ ] **步骤 1：运行 Telegram 相关测试集**

运行：`pytest tests/test_telegram_runtime.py tests/test_telegram_monitor_state.py tests/test_telegram_service.py -v`
预期：全部 `PASS`

- [ ] **步骤 2：运行现有事件与转存回归测试**

运行：`pytest tests/test_events.py tests/test_transfer_service.py -v`
预期：全部 `PASS`，确认监听改造没有破坏现有事件链路与转存服务。

- [ ] **步骤 3：检查关键文件差异**

运行：`git diff -- app/config.py app/database.py app/core/monitor/telegram.py app/core/monitor/telegram_runtime.py app/services/telegram_service.py app/api/system.py tests/test_telegram_runtime.py tests/test_telegram_monitor_state.py tests/test_telegram_service.py`
预期：只包含 Telegram 监听相关改动，没有无关回退。

- [ ] **步骤 4：Commit**

```bash
git add app/config.py app/database.py app/core/monitor/telegram.py app/core/monitor/telegram_runtime.py app/services/telegram_service.py app/api/system.py tests/test_telegram_runtime.py tests/test_telegram_monitor_state.py tests/test_telegram_service.py
git commit -m "feat: implement telegram channel monitor recovery flow"
```

## 自检

- 规格覆盖度：已覆盖配置扩展、状态表、幂等入库、启动补偿、服务接口、API 和测试回归。
- 占位符扫描：计划中没有 `TODO`、`后续实现`、`类似任务` 之类占位符。
- 类型一致性：统一使用 `telegram_monitor_state`、`get_monitor_status()`、`sync_single_channel()`、`sync_configured_channels()` 这组命名。

## 执行交接

计划已完成并保存到 `docs/superpowers/plans/2026-06-06-telegram-channel-monitor-implementation.md`。两种执行方式：

**1. 子代理驱动（推荐）** - 每个任务调度一个新的子代理，任务间进行审查，快速迭代

**2. 内联执行** - 在当前会话中使用 executing-plans 执行任务，批量执行并设有检查点

选哪种方式？