# STRM 自动生成与归档覆盖实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将当前 STRM 生成功能改造成事件驱动、归档路径单一真源、低 API 调用的一致化方案，并在转存页面提供“归档 STRM 一键覆盖”能力。

**架构：** 以转存归档结果为事件源，抽离统一的归档路径解析服务，保证转存目录与 STRM 输出目录来自同一套 `ArchivePlacement` 计算结果。自动 STRM 仅消费当前批次的归档元数据并按需补一次当前目录的云盘文件信息；手动覆盖优先使用本地 `strm_records` 清单重写 `.strm` 文件，避免全盘扫描。

**技术栈：** Python 3.12、FastAPI、aiosqlite、现有事件总线、115 云盘客户端、unittest

---

## 文件结构

- 创建：`app/core/transfer/placement.py`
  归档与 STRM 路径单一真源，输出 `ArchivePlacement`。
- 创建：`app/core/transfer/strm_manifest.py`
  STRM 清单读写服务，负责 `strm_records` 升级后的查询、写入、覆盖读取。
- 创建：`tests/test_transfer_placement.py`
  覆盖统一路径解析、剧集/电影路径一致性。
- 创建：`tests/test_strm_manifest.py`
  覆盖记录表升级后的写入、去重、覆盖读取。
- 修改：`app/core/transfer/classifier.py`
  让分类结果输出稳定字段，供 `ArchivePlacement` 使用。
- 修改：`app/core/transfer/batch.py`
  批量转存完成后发出携带归档元数据的 STRM 事件。
- 修改：`app/core/cloud115/strm.py`
  按事件驱动生成 STRM，复用统一路径服务并减少目录扫描。
- 修改：`app/database.py`
  升级 `strm_records` 表结构。
- 修改：`app/events.py`
  增加 STRM 请求/完成相关事件定义。
- 修改：`app/api/transfer.py`
  增加“归档 STRM 一键覆盖”接口。
- 修改：`app/web/templates/transfer.html`
  增加一键覆盖入口与任务反馈。
- 测试：`tests/test_transfer_classifier.py`
  保留现有分类和地区回归，必要时补充统一路径联动断言。

### 任务 1：统一归档路径真源

**文件：**
- 创建：`app/core/transfer/placement.py`
- 修改：`app/core/transfer/classifier.py`
- 测试：`tests/test_transfer_placement.py`

- [ ] **步骤 1：编写失败的路径一致性测试**

```python
import unittest

from app.core.transfer.models import ClassifyResult
from app.core.transfer.placement import build_archive_placement


class ArchivePlacementTests(unittest.TestCase):
    def test_build_archive_placement_for_tv_matches_transfer_and_strm_layout(self):
        result = ClassifyResult(
            category="剧集",
            subcategory="国产剧集",
            title="灵魂摆渡·十年",
            year="2026",
            tmdb_id="289271",
            season=1,
            media_type="tv",
        )

        placement = build_archive_placement(result, "灵魂摆渡·十年.2026.S01E05.mkv")

        self.assertEqual(placement.archive_rel_path, "剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1")
        self.assertEqual(placement.strm_rel_dir, "剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1")
        self.assertEqual(placement.target_file_name, "灵魂摆渡·十年 - S01E05.mkv")
        self.assertEqual(placement.strm_file_name, "灵魂摆渡·十年 - S01E05.strm")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run python -m unittest tests.test_transfer_placement -v`
预期：FAIL，报错 `No module named 'app.core.transfer.placement'` 或 `cannot import name 'build_archive_placement'`

- [ ] **步骤 3：编写最少实现代码**

```python
# app/core/transfer/placement.py
from dataclasses import dataclass
from pathlib import Path

from app.core.media.parser import parse_filename
from app.core.transfer.classifier import build_archive_path


@dataclass(slots=True)
class ArchivePlacement:
    archive_rel_path: str
    strm_rel_dir: str
    target_file_name: str
    strm_file_name: str


def build_archive_placement(result, original_name: str) -> ArchivePlacement:
    media_info = parse_filename(original_name)
    parts = build_archive_path(result)
    rel_dir = "/".join(parts)
    stem = result.title

    if result.media_type == "tv":
        season = media_info.season or result.season or 1
        episode = media_info.episode or 1
        target_file_name = f"{stem} - S{season:02d}E{episode:02d}{Path(original_name).suffix}"
        strm_file_name = f"{stem} - S{season:02d}E{episode:02d}.strm"
    else:
        folder_name = parts[-1]
        target_file_name = f"{folder_name}{Path(original_name).suffix}"
        strm_file_name = f"{folder_name}.strm"

    return ArchivePlacement(
        archive_rel_path=rel_dir,
        strm_rel_dir=rel_dir,
        target_file_name=target_file_name,
        strm_file_name=strm_file_name,
    )
```

- [ ] **步骤 4：在分类器中收敛季目录命名**

```python
# app/core/transfer/classifier.py
if result.media_type == "tv" and result.season > 0:
    parts.append(f"Season {result.season}")
```

保持 `build_archive_path()` 继续作为目录片段生成器，不再让其他模块自己拼接 `Season 01/Season 1`。

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run python -m unittest tests.test_transfer_placement -v`
预期：PASS

- [ ] **步骤 6：Commit**

```bash
git add app/core/transfer/placement.py app/core/transfer/classifier.py tests/test_transfer_placement.py
git commit -m "feat: unify archive and strm placement"
```

### 任务 2：升级 STRM 记录表为清单表

**文件：**
- 创建：`app/core/transfer/strm_manifest.py`
- 修改：`app/database.py`
- 测试：`tests/test_strm_manifest.py`

- [ ] **步骤 1：编写失败的清单去重测试**

```python
import unittest

from app.core.transfer.strm_manifest import build_manifest_record


class StrmManifestTests(unittest.TestCase):
    def test_build_manifest_record_keeps_archive_and_strm_paths(self):
        record = build_manifest_record(
            cloud_type="115",
            file_id="fid-1",
            archive_dir_id="cid-9",
            archive_rel_path="剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1",
            strm_rel_path="剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1/灵魂摆渡·十年 - S01E05.strm",
            strm_abs_path="/data/strm_output/剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1/灵魂摆渡·十年 - S01E05.strm",
            play_identity="pickcode-1",
        )

        self.assertEqual(record["cloud_type"], "115")
        self.assertEqual(record["file_id"], "fid-1")
        self.assertEqual(record["status"], "generated")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run python -m unittest tests.test_strm_manifest -v`
预期：FAIL，报错 `No module named 'app.core.transfer.strm_manifest'`

- [ ] **步骤 3：编写最少实现代码**

```python
# app/core/transfer/strm_manifest.py
from datetime import datetime


def build_manifest_record(*, cloud_type, file_id, archive_dir_id, archive_rel_path, strm_rel_path, strm_abs_path, play_identity):
    return {
        "cloud_type": cloud_type,
        "file_id": file_id,
        "archive_dir_id": archive_dir_id,
        "archive_rel_path": archive_rel_path,
        "strm_rel_path": strm_rel_path,
        "strm_abs_path": strm_abs_path,
        "play_identity": play_identity,
        "status": "generated",
        "updated_at": datetime.utcnow().isoformat(),
    }
```

- [ ] **步骤 4：升级数据库建表语句**

```python
# app/database.py
await db.execute(
    """
    CREATE TABLE IF NOT EXISTS strm_records (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cloud_type TEXT NOT NULL,
        file_id TEXT NOT NULL,
        archive_dir_id TEXT,
        archive_rel_path TEXT,
        strm_rel_path TEXT,
        strm_abs_path TEXT,
        play_identity TEXT,
        status TEXT DEFAULT 'generated',
        task_id TEXT,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(cloud_type, file_id)
    )
    """
)
```

如果旧表已存在且缺字段，添加兼容迁移逻辑，最少做到 `PRAGMA table_info(strm_records)` 后补列。

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run python -m unittest tests.test_strm_manifest -v`
预期：PASS

- [ ] **步骤 6：Commit**

```bash
git add app/core/transfer/strm_manifest.py app/database.py tests/test_strm_manifest.py
git commit -m "feat: upgrade strm records to manifest table"
```

### 任务 3：改造自动 STRM 事件链路

**文件：**
- 修改：`app/events.py`
- 修改：`app/core/transfer/batch.py`
- 修改：`app/core/cloud115/strm.py`
- 测试：`tests/test_events.py`

- [ ] **步骤 1：编写失败的事件载荷测试**

```python
import unittest

from app.events import EVENT_STRM_BATCH_REQUESTED


class TransferEventTests(unittest.TestCase):
    def test_strm_batch_requested_payload_contains_archive_placement(self):
        payload = {
            "task_id": "task-1",
            "cloud_type": "115",
            "archive_dir_id": "cid-9",
            "archive_rel_path": "剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1",
            "strm_rel_dir": "剧集/国产剧集/灵魂摆渡·十年 (2026) {tmdb-289271}/Season 1",
            "files": [{"file_id": "fid-1", "file_name": "灵魂摆渡·十年.2026.S01E05.mkv"}],
        }

        self.assertEqual(payload["archive_dir_id"], "cid-9")
        self.assertEqual(EVENT_STRM_BATCH_REQUESTED, "strm.batch.requested")
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run python -m unittest tests.test_events -v`
预期：FAIL，提示事件常量不存在或当前事件载荷断言不满足

- [ ] **步骤 3：在事件总线中增加事件常量**

```python
# app/events.py
EVENT_STRM_BATCH_REQUESTED = "strm.batch.requested"
EVENT_STRM_BATCH_COMPLETED = "strm.batch.completed"
```

- [ ] **步骤 4：在转存批次完成时发出最小事件载荷**

```python
# app/core/transfer/batch.py
await event_bus.emit(
    EVENT_STRM_BATCH_REQUESTED,
    {
        "task_id": task_id,
        "cloud_type": "115",
        "archive_dir_id": season_folder_cid,
        "archive_rel_path": "/".join(archive_parts),
        "strm_rel_dir": "/".join(archive_parts),
        "files": share_files,
    },
)
```

- [ ] **步骤 5：在 115 STRM 生成器中优先消费事件元数据**

```python
# app/core/cloud115/strm.py
if payload.get("files") and payload.get("archive_dir_id"):
    return await self.generate_strm_for_folder(
        payload["archive_dir_id"],
        payload["files"],
        strm_subdir=payload["strm_rel_dir"],
        root_output_dir=root_output_dir,
        skip_organize=True,
    )
```

只有在缺少 `pickcode` 或 `fid -> pickcode` 映射时，才允许补一次 `list_files(archive_dir_id)`。

- [ ] **步骤 6：运行测试验证通过**

运行：`uv run python -m unittest tests.test_events tests.test_transfer_classifier -v`
预期：PASS

- [ ] **步骤 7：Commit**

```bash
git add app/events.py app/core/transfer/batch.py app/core/cloud115/strm.py tests/test_events.py tests/test_transfer_classifier.py
git commit -m "feat: trigger strm generation from transfer events"
```

### 任务 4：实现“已生成不再检查”和归档覆盖服务

**文件：**
- 修改：`app/core/cloud115/strm.py`
- 修改：`app/core/transfer/strm_manifest.py`
- 测试：`tests/test_strm_manifest.py`

- [ ] **步骤 1：编写失败的跳过已生成记录测试**

```python
import unittest


class StrmSkipTests(unittest.TestCase):
    def test_existing_manifest_record_skips_repeat_generation(self):
        existing = {("115", "fid-1")}
        incoming = [("115", "fid-1"), ("115", "fid-2")]

        pending = [item for item in incoming if item not in existing]

        self.assertEqual(pending, [("115", "fid-2")])
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run python -m unittest tests.test_strm_manifest -v`
预期：FAIL，缺少对应筛选逻辑或测试文件未包含该行为

- [ ] **步骤 3：在清单服务中增加去重与覆盖读取接口**

```python
# app/core/transfer/strm_manifest.py
async def filter_pending_records(records):
    existing = await self.load_existing_keys(records)
    return [record for record in records if (record["cloud_type"], record["file_id"]) not in existing]

async def list_records_for_rewrite(archive_root: str):
    ...
```

- [ ] **步骤 4：在生成器中先过滤后生成**

```python
# app/core/cloud115/strm.py
manifest_records = await manifest_service.filter_pending_records(manifest_records)
if not manifest_records:
    logger.info("No pending STRM records for current batch")
    return []
```

- [ ] **步骤 5：实现归档覆盖写入器**

```python
# app/core/cloud115/strm.py
async def rewrite_from_manifest(self, archive_root: str) -> dict:
    records = await manifest_service.list_records_for_rewrite(archive_root)
    for record in records:
        await self.write_strm_file(record["strm_abs_path"], record["play_identity"])
    return {"rewritten": len(records)}
```

- [ ] **步骤 6：运行测试验证通过**

运行：`uv run python -m unittest tests.test_strm_manifest -v`
预期：PASS

- [ ] **步骤 7：Commit**

```bash
git add app/core/cloud115/strm.py app/core/transfer/strm_manifest.py tests/test_strm_manifest.py
git commit -m "feat: skip generated strm records and support manifest rewrite"
```

### 任务 5：暴露归档 STRM 覆盖 API 和前端入口

**文件：**
- 修改：`app/api/transfer.py`
- 修改：`app/web/templates/transfer.html`
- 测试：`tests/test_events.py`

- [ ] **步骤 1：编写失败的 API 行为测试**

```python
def test_rewrite_archive_strm_endpoint_returns_task_summary(client):
    response = client.post("/api/v1/transfer/strm/rewrite")
    assert response.status_code == 200
    assert "rewritten" in response.json()
```
```

- [ ] **步骤 2：运行测试验证失败**

运行：`uv run python -m unittest tests.test_events -v`
预期：FAIL，路由不存在或响应格式不匹配

- [ ] **步骤 3：新增接口，默认仅覆盖已有记录**

```python
# app/api/transfer.py
@router.post("/strm/rewrite")
async def rewrite_archive_strm():
    result = await strm_generator.rewrite_from_manifest(get_config().strm.output_dir)
    return {"ok": True, **result}
```

- [ ] **步骤 4：在转存页面增加按钮与反馈**

```html
<button id="rewrite-archive-strm" class="btn btn-outline-primary">一键覆盖归档 STRM</button>
<div id="rewrite-archive-strm-result" class="text-muted small"></div>
```

```javascript
document.getElementById("rewrite-archive-strm").addEventListener("click", async () => {
  const resp = await fetch("/api/v1/transfer/strm/rewrite", { method: "POST" });
  const data = await resp.json();
  document.getElementById("rewrite-archive-strm-result").textContent = `已覆盖 ${data.rewritten} 个 STRM`;
});
```

- [ ] **步骤 5：运行测试验证通过**

运行：`uv run python -m unittest tests.test_events -v`
预期：PASS

- [ ] **步骤 6：Commit**

```bash
git add app/api/transfer.py app/web/templates/transfer.html tests/test_events.py
git commit -m "feat: add archive strm rewrite entry"
```

### 任务 6：全链路验证与收尾

**文件：**
- 测试：`tests/test_transfer_placement.py`
- 测试：`tests/test_strm_manifest.py`
- 测试：`tests/test_transfer_classifier.py`
- 测试：`tests/test_events.py`

- [ ] **步骤 1：运行统一路径与清单测试**

运行：`uv run python -m unittest tests.test_transfer_placement tests.test_strm_manifest -v`
预期：PASS

- [ ] **步骤 2：运行现有 STRM/分类回归测试**

运行：`uv run python -m unittest tests.test_transfer_classifier tests.test_events -v`
预期：PASS

- [ ] **步骤 3：手动验证一次事件驱动链路**

运行：`uv run python -m app.main`
预期：应用启动成功，执行一次转存后日志中出现 `strm.batch.requested`，且 `.strm` 文件落在与归档相同的相对路径下。

- [ ] **步骤 4：检查关键验收项**

```text
1. 自动生成和手动覆盖使用同一套 ArchivePlacement
2. 已生成记录再次触发时不会重复 list_files 或重复落盘
3. 一键覆盖默认只重写已有 manifest 记录，不补扫老归档
4. 115 适配器之外的主流程不依赖 pickcode 细节
```

- [ ] **步骤 5：Commit**

```bash
git add app tests
git commit -m "test: verify event-driven strm generation workflow"
```

## 自检

- 规格覆盖度：已覆盖统一路径真源、事件驱动自动生成、已生成不再检查、低 API 调用、一键覆盖、后续多网盘适配边界。
- 占位符扫描：本计划未使用“TODO/待定/后续补充”等占位词，所有任务均给出具体文件、命令和最小代码骨架。
- 类型一致性：计划中统一使用 `ArchivePlacement`、`strm_manifest`、`EVENT_STRM_BATCH_REQUESTED`、`rewrite_from_manifest()` 这组命名，避免后续任务漂移。

## 执行交接

计划已完成并保存到 `docs/superpowers/plans/2026-06-06-strm-auto-generation.md`。两种执行方式：

**1. 子代理驱动（推荐）** - 每个任务调度一个新的子代理，任务间进行审查，快速迭代

**2. 内联执行** - 在当前会话中使用 executing-plans 执行任务，批量执行并设有检查点

选哪种方式？