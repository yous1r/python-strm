使用子代理完成以下任务。
当前任务尚未开始 ACT MODE 的实际实现，但用户已经明确要求“开始实施”。系统同时要求在上下文即将耗尽时只使用 `attempt_completion` 或 `summarize_task`。由于当前工具列表中没有 `summarize_task`，我必须用 `attempt_completion` 传递完整的实施前上下文，让后续会话可以无损继续。需要重点保留：1) 已完成的 standalone proxy 重构与验证；2) 用户对设计方向的多次修正；3) 最终敲定并批准的方案 2；4) 尚未执行的第一批 ACT MODE 工具操作；5) 关键代码、文件、字段、待改动点和验证要求。


1. Primary Request and Intent:
最初用户要求继续执行交接计划文件 `docs/superpowers/plans/2026-06-07-standalone-proxy-request-refactor-handoff.md`，完成 `app/core/emby/standalone_proxy.py` 中 Emby/FNOS 上游请求的统一封装重构，并保留已经验证通过的 PlaybackInfo 修复逻辑。

之后用户逐步把问题从“请求封装”推进到“播放索引设计”：
- 要求以 `uv` 虚拟环境重新验证。
- 指出 Emby 返回的 STRM 路径不是当前项目真实路径，路径反查方案不稳。
- 提出利用 115 稳定不变的 `fid` 和 `pickcode` 设计高效 302 索引方案。
- 说明项目还处测试阶段，可接受表结构重建。
- 要求把 `emby_item_id` 抽象为更通用的 `media_item_id`，兼容 Jellyfin 等媒体服务器。
- 又进一步明确优先级：当前项目生成的 STRM 文件和 STRM 生成表才是核心，115 `pickcode` 是 302 播放的核心，第三方播放器支持应建立在这个核心之上。
- 最终用户明确批准：`按照方案2编写设计方案并实施`，之后再次确认：`开始实施`。

当前活跃任务：
按“方案 2”进入实施，方案 2 的定义是：
- `strm_records`（或等价核心 STRM manifest 表）作为主数据源，负责当前项目 STRM 文件生成记录；
- `pickcode` 是 302 播放主键；
- `fid/file_id` 是 115 云文件稳定身份；
- 新增 `media_item_links` 扩展表，仅用于 Emby/Jellyfin/FNOS 等媒体服务器的 `media_item_id` 到 `pickcode/file_id` 的快速映射；
- 路径反查逻辑降级为 fallback，而不再是主路径。

2. Key Technical Concepts:
- Python、FastAPI、httpx、SQLite、loguru
- Emby/FNOS standalone proxy
- PlaybackInfo 本地合成与透明代理
- 115 网盘稳定身份：`fid(file_id)`、`pickcode(play_identity)`
- STRM 原生播放：`.strm` 内容为 `/api/v1/115/play/{pickcode}/{filename}`
- 第三方播放器增强播放：媒体服务器 item -> PlaybackInfo -> `/115play/{pickcode}` -> 302 到真实 115 下载地址
- 当前项目为主要入口：STRM 文件生成与生成表优先于第三方媒体服务器支持
- 设计分层：
  - 核心层：`strm_records` / STRM manifest
  - 扩展层：`media_item_links`
- 通用媒体服务器字段：`media_server_type`, `media_server_name`, `media_item_id`, `media_source_id`
- 旧问题：通过 Emby/FNOS 返回的 `.strm` 路径做本地路径反查高度脆弱
- 验证环境：必须使用 `uv run python`
- `httpx.Response` 布尔值陷阱：4xx/5xx response 在布尔判断中为 false，封装请求结果必须用 `is None` / `is not None`

3. Files and Code Sections:

- `docs/superpowers/plans/2026-06-07-standalone-proxy-request-refactor-handoff.md`
  - 初始交接文件。
  - 规定了 standalone proxy 请求封装重构的未完成项和验证要求。
  - 其中 PlaybackInfo 的既有修复基线必须保留：通过上游 `Items/{item_id}` 的 `Path` 或 `MediaSources[0].Path` 提取 `pickcode` 或回查本地 manifest。

- `app/core/emby/standalone_proxy.py`
  - 已实际修改并验证。
  - 关键新增/使用函数：
```python
def _get_emby_user_id(request: Request) -> str | None:
    """从 query 或 X-Emby-Authorization 中提取 UserId。"""
```
```python
async def _request_upstream_json(
    upstream_url: str,
    path: str,
    request: Request,
    api_key: str = "",
    *,
    method: str = "GET",
    params: dict | None = None,
    json_body: dict | None = None,
    timeout: float = 10,
) -> httpx.Response | None:
    """统一发送到上游 Emby 的 JSON 请求。"""
```
```python
async def _build_upstream_proxy_request(
    upstream_url: str,
    full_path: str,
    request: Request,
    api_key: str = "",
) -> tuple[httpx.AsyncClient, httpx.Response]:
    """统一构建并发送透明代理请求到上游 Emby。"""
```
  - 已完成的封装迁移：
    - `_get_upstream_item_payload()` 已改用 `_get_emby_user_id()` 与 `_request_upstream_json()`。
    - `_proxy_request()` 已改用 `_build_upstream_proxy_request()`，保留 204 改写、3xx 透传、流式响应。
    - `/Sessions/Playing` 分支已统一改用 `_get_emby_user_id()`。
    - `fix_runtime_and_sync()` 签名从 `_headers` 改为 `_request, _api_key`，内部 4 个上游请求全部改走 `_request_upstream_json()`。
    - 保留 `fix_runtime_and_sync()` 顶部 `return`，后台任务当前仍禁用。
    - 所有 `httpx.Response` 是否存在的判断改成 `resp is None` / `resp is not None`。
  - 当前文件中仍保留旧路径反查逻辑，后续实施需要降级为 fallback：
```python
def _resolve_local_strm_path(feiniu_path: str) -> str | None:
    """将飞牛返回的 Docker 容器内路径映射为代理本地路径"""
    import os
    for marker in ["python-strm/strm_output/", "strm_output/"]:
        idx = feiniu_path.find(marker)
        if idx >= 0:
            relative = feiniu_path[idx + len(marker):]
            candidates = [
                os.path.join("strm_output", relative),
                os.path.join("/app/strm_output", relative),
            ]
            for c in candidates:
                if os.path.exists(c):
                    return c
    return None
```
  - 当前 `_get_local_playback_record_by_path()` 仍是路径查询：
```sql
SELECT file_id, play_identity, strm_rel_path, strm_abs_path, strm_path
FROM strm_records
WHERE cloud_type='115'
  AND (
    strm_abs_path=?
    OR strm_path=?
    OR strm_rel_path=?
  )
LIMIT 1
```
  - 后续 ACT MODE 实施时，这部分需要被改造成：
    - PlaybackInfo 优先查 `media_item_links`
    - 再尝试上游 item path 里直接提取 `/api/v1/115/play/{pickcode}`
    - 最后才 fallback 到旧的路径查询

- `app/core/cloud115/strm.py`
  - 已读取 1-260 行，261-358 尚未读。
  - 它是当前项目 STRM 文件生成核心逻辑。
  - 已确认 `.strm` 内容直接以内嵌 `pickcode` 为核心：
```python
def build_strm_content(self, pickcode: str, file_name: str, base_url: str) -> str:
    config = get_config()
    encoded_name = urllib.parse.quote(file_name)
    strm_content = f"{base_url.rstrip('/')}/api/v1/115/play/{pickcode}/{encoded_name}"
    if config.cloud115.play_ua:
        strm_content += f"|User-Agent={config.cloud115.play_ua}"
    return strm_content
```
  - `_record_manifest()` 已确认会把 `file_id/fid`、`play_identity/pickcode`、STRM 路径写入 `strm_records`：
```python
record = build_manifest_record(
    cloud_type="115",
    file_id=file_id,
    archive_dir_id=archive_dir_id,
    archive_rel_path=archive_rel_path,
    strm_rel_path=strm_rel_path,
    strm_abs_path=strm_abs_path,
    play_identity=pickcode,
    task_id=task_id,
)
```
  - 这正是方案 2 的核心依据：STRM 生成表已经天然持有 `fid + pickcode`。

- `app/config.py`
  - 已读取。
  - 当前 `StrmConfig`：
```python
class StrmConfig(BaseModel):
    output_dir: str = "strm_output"
    base_url: str = "http://localhost:8095"
    sync_metadata: bool = True
    clean_invalid: bool = True
```
  - 当前 `EmbyProxyInstanceConfig`：
```python
class EmbyProxyInstanceConfig(BaseModel):
    name: str = ""
    url: str = ""
    api_key: str = ""
    proxy_port: int = 0
```
  - 目前尚无播放索引配置或媒体服务器映射配置。

- `app/database.py`
  - 尚未完整读取。
  - 仅通过 rg 看到 `strm_records` 至少已有：
    - `strm_rel_path TEXT`
    - `strm_abs_path TEXT`
    - `strm_path TEXT`
  - 需要完整读取以确认：
    - 当前 `strm_records` 的 schema
    - 是否已有 `file_id`, `play_identity`, `status`, `updated_at` 等字段
    - 当前迁移方式
  - 由于用户说明项目还在测试阶段，可接受表结构重建，因此可以对这里做较干净的 schema 调整。

- `app/core/transfer/strm_manifest.py`
  - 尚未读取。
  - 通过 rg 已知它负责构建 manifest record，涉及 `strm_rel_path`, `strm_abs_path`, `play_identity`, `task_id`, `strm_path`, `status`。
  - 是实施方案 2 的关键文件之一。

- `tests/test_emby_standalone_proxy.py`
  - 文件列表中已存在，但还未读取。
  - 需要在实施前优先读取，确保方案 2 改动不会破坏现有测试预期，并据此补充或改写测试。

- `data/python_strm.db`
  - 目前尚未实际查询。
  - 实施前应查询 `strm_records` 样本，验证真实数据里 `file_id`, `play_identity`, `strm_rel_path`, `strm_abs_path`, `strm_path`, `archive_rel_path` 的分布与完整性。

- 设计文档路径（尚未创建）：
  - `docs/superpowers/specs/2026-06-07-playback-index-design.md`
  - 在 PLAN MODE 末尾我已经承诺：实施前先补一份设计文档，再落代码和测试。

4. Problem Solving:
- 已完成 standalone proxy 请求封装重构。
- 已确保 PlaybackInfo 本地合成逻辑保留，并支持：
  - 上游 item path 直接包含 `/api/v1/115/play/{pickcode}`
  - 旧的本地 STRM manifest 反查路径
- 已使用 `uv run python` 验证：
  - `uv run python -m py_compile app/core/emby/standalone_proxy.py`
  - `uv run python` 的 PlaybackInfo 模拟回归脚本
- 已明确旧方案的根本问题：媒体服务器返回的 `.strm` 路径是它自己的挂载路径，而不是当前项目真实路径，因此路径反查是脆弱的。
- 已与用户反复收敛设计方向，最终确认：
  - 不是以 `media_item_id` 为中心建主表
  - 而是以当前项目 STRM 生成记录为核心
  - `pickcode` 是 302 播放主键
  - `fid` 是文件稳定身份
  - `media_item_links` 是扩展加速层
- 用户已明确要求进入实施阶段，但实际上还未开始任何新的 ACT MODE 读文件/写代码动作，因为在 ACT MODE 切换后立刻被上下文压缩打断。

5. Pending Tasks:
实施还没开始。待办事项如下：
- 读取 `app/database.py`
- 读取 `app/core/transfer/strm_manifest.py`
- 读取 `app/core/cloud115/strm.py` 后半段（261-358）
- 读取 `tests/test_emby_standalone_proxy.py`
- 查询 `data/python_strm.db` 中 `strm_records` 样本
- 编写设计文档 `docs/superpowers/specs/2026-06-07-playback-index-design.md`
- 实施方案 2：
  - 明确/强化 `strm_records` 的核心职责
  - 新增 `media_item_links`
  - 调整 manifest 写入保证 `file_id/pickcode` 完整
  - 调整 standalone proxy 的 PlaybackInfo 索引逻辑
- 用 `uv run python` 做语法检查与测试验证

6. Task Evolution:
- Original Task:
  - 用户最初请求继续交接计划文件中的 standalone proxy 请求封装重构。
- Task Modifications:
  1. 用户：`当前环境使用的uv虚拟环境，重新验证`
  2. 用户指出 Emby 路径问题：
     - `'/mnt/strm-self/archive/综艺/国产综艺/现在就出发 (2023) {tmdb-231620}/Season 1/现在就出发 - S01E01-Part1 - 第 1 集.strm'`
  3. 用户提出基于 `fid`/`pickcode` 的 302 索引方向：
     - `就115网盘而言，每个文件上传成功后它的fid和pickcode都是不变的，是否可以从这两个属性入手，设计一个用于第三方播放器高效302的索引方案，替换strm基于路径的查询方案。`
  4. 用户放宽 schema 约束并要求通用命名：
     - `目前项目还处在测试阶段，完全可以接受表结构重建，emby_item_id可以更改字段名为media_item_id，支持其他影视资源端如jellyfin`
  5. 用户重新定义优先级：
     - `肯定是以当前项目为主要入口，当前项目生成的strm文件才是核心，或者更简单的理解如11网盘中固定不变的pickcode才是302播放的核心。总结来说，优先保证strm文件生成表的功能生成正常，再去谈第三方播放器的支持，因为有类似飞牛这种原生支持strm文件的媒体服务器`
  6. 用户最终批准并催促实施：
     - `按照方案2编写设计方案并实施`
     - `开始实施`
- Current Active Task:
  - 在 ACT MODE 下，按方案 2 先补设计文档，再实施代码和测试。
- Context for Changes:
  - 用户的所有修正都在收紧一个原则：当前项目生成的 STRM 文件与其生成表是系统真正的稳定核心，路径查询不可靠，第三方媒体服务器只是接入层而非主数据源。

7. Current Work:
- 当前是 ACT MODE。
- 但 **还没有开始任何 ACT MODE 下的新工具操作**，因为一切都在切换后被上下文压缩消息打断。
- 切换前最后一次明确规划是：
  - “方案 2 我会收敛成：STRM 生成核心表 + `media_item_id` 扩展索引表。”
  - “实施前我会先补一份设计文档，再落代码和测试。”
- 用户随后明确要求：
  - `按照方案2编写设计方案并实施`
  - `开始实施`
- 所以真正的下一步非常清楚：立即开始 ACT MODE 下的读取与落盘，而不是继续讨论方案。

8. Next Step:
下一步必须直接开始实施前的最小上下文收集与设计文档落盘，然后进入代码改造。最合理的第一批 ACT MODE 动作是：
1. 读取：
   - `app/database.py`
   - `app/core/transfer/strm_manifest.py`
   - `app/core/cloud115/strm.py` 261-358
   - `tests/test_emby_standalone_proxy.py`
2. 查询 `data/python_strm.db` 中 `strm_records` 样本。
3. 写设计文档到 `docs/superpowers/specs/2026-06-07-playback-index-design.md`。
4. 再开始 schema 与 proxy 逻辑的实际实现。

最近原话必须保留：
- 用户：`按照方案2编写设计方案并实施`
- 用户：`开始实施`
- 我在 PLAN MODE 最后承诺：`实施前我会先补一份设计文档，再落代码和测试。`

9. Required Files:
- `app/database.py`
- `app/core/transfer/strm_manifest.py`
- `app/core/cloud115/strm.py`
- `app/core/emby/standalone_proxy.py`
- `tests/test_emby_standalone_proxy.py`
- `docs/superpowers/specs/2026-06-07-playback-index-design.md`
- `data/python_strm.db`