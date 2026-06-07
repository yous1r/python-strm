# standalone_proxy 请求封装重构交接计划

> **面向 AI 代理的工作者：** 建议先完成本计划中的“交接落盘”确认，再继续修改 `app/core/emby/standalone_proxy.py`。当前状态不是损坏态，但属于“基础工具函数已加入、业务调用点尚未全部迁移”的半重构状态。

**目标：** 完成 `app/core/emby/standalone_proxy.py` 中上游 Emby/FNOS 请求的统一封装，把零散的 `httpx` 调用收敛到公共工具函数，并保留已验证通过的 `PlaybackInfo` 路径映射修复逻辑。

**背景：** 用户最初要求“熟悉当前项目”，后续实际工作聚焦到 Emby 独立代理。上一阶段已经修复 `PlaybackInfo` 的错误映射逻辑：不再把 Emby `item_id` 当作本地 `strm_records.file_id` 使用，而是统一通过上游 `Items/{item_id}` 的 `Path/MediaSources` 映射 `pickcode` 或本地 STRM manifest。该修复已经验证通过。本计划对应的剩余工作，是继续完成“请求封装统一化”的结构重构，并保留上下文给新会话直接续接。

**技术栈：** Python、FastAPI、httpx、SQLite、loguru

---

## 相关文件

**核心文件：**
- `app/core/emby/standalone_proxy.py`

**交接文件：**
- `docs/superpowers/plans/2026-06-07-standalone-proxy-request-refactor-handoff.md`

---

## 当前已完成状态

### 1. `PlaybackInfo` 逻辑修复已完成

已修复的问题：
- 不再用 Emby `item_id` 直接查询本地 `strm_records.file_id`
- `PlaybackInfo` 统一改为：
  - 先请求上游 `Items/{item_id}`
  - 读取 `Path` 或 `MediaSources[0].Path`
  - 如果路径包含 `/api/v1/115/play/{pickcode}`，直接提取 `pickcode`
  - 否则通过 STRM 路径反查本地 manifest

已验证结果：
- `STATUS 200`
- `HAS_PICKCODE True`
- `HAS_ITEMID True`
- `HAS_FILENAME True`

### 2. 公共工具函数已加入

以下工具函数已经存在于 `app/core/emby/standalone_proxy.py`：

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

这些函数已经写入文件，但调用点迁移还没有收尾。

---

## 当前未完成状态

以下旧逻辑仍然存在，说明重构处于半完成状态：

- `_get_upstream_item_payload(...)` 仍在内部手工构造 headers，并直接 `httpx.AsyncClient(...).get(...)`
- `_proxy_request(...)` 仍手工拼装 `params/headers/client/build_request/send`
- `handle_proxy()` 的 `/Sessions/Playing` 分支仍手工正则提取 `UserId`
- `fix_runtime_and_sync(...)` 仍接收 `_headers`，并直接发起多次 `client.get()` / `client.post()`

换句话说：
- 公共工具函数已经可用
- 业务调用点还没有全部切过去
- 当前最合理的续做方式，是先完成迁移，再做语法和行为验证

---

## 建议实施顺序

### 任务 1：收敛 Item 查询逻辑

**文件：**
- 修改：`app/core/emby/standalone_proxy.py`

- [ ] 把 `_get_upstream_item_payload(...)` 中的 `UserId` 解析改为 `_get_emby_user_id(request)`
- [ ] 把 `_get_upstream_item_payload(...)` 中的 GET 请求改为 `_request_upstream_json(...)`
- [ ] 保留现有 `status_code != 200` 和 JSON decode 异常日志

目标形态：

```python
async def _get_upstream_item_payload(upstream_url: str, api_key: str, item_id: str, request: Request) -> dict | None:
    user_id = _get_emby_user_id(request)
    api_path = f"/Users/{user_id}/Items/{item_id}" if user_id else f"/Items/{item_id}"

    res = await _request_upstream_json(
        upstream_url,
        api_path,
        request,
        api_key,
        params={"Fields": "Path,MediaSources"},
    )
    if not res:
        return None
    if res.status_code != 200:
        logger.warning(f"[PROXY] Upstream item lookup failed for item_id={item_id}, status={res.status_code}")
        return None

    try:
        return res.json()
    except Exception as e:
        logger.error(f"[PROXY] Failed to decode upstream item payload for item_id={item_id}: {repr(e)}")
        return None
```

### 任务 2：收敛透明代理请求逻辑

**文件：**
- 修改：`app/core/emby/standalone_proxy.py`

- [ ] 把 `_proxy_request(...)` 中手工构造请求的逻辑改为调用 `_build_upstream_proxy_request(...)`
- [ ] 保留响应头过滤、`/Sessions/Playing` 失败重写 204、3xx 透传、流式返回逻辑

关键替换点：

```python
client, resp = await _build_upstream_proxy_request(upstream_url, full_path, request, api_key)
```

### 任务 3：统一 `UserId` 提取

**文件：**
- 修改：`app/core/emby/standalone_proxy.py`

- [ ] 在 `handle_proxy()` 的 `/Sessions/Playing` 分支中，用 `_get_emby_user_id(request)` 替代当前手工正则
- [ ] 保留现有事件分类和日志输出
- [ ] `has_token` 继续通过 `_get_emby_headers(request, api_key)` 判断

### 任务 4：收敛后台同步请求

**文件：**
- 修改：`app/core/emby/standalone_proxy.py`

- [ ] 把 `fix_runtime_and_sync(...)` 的签名从 `_headers` 改成 `_request, _api_key`
- [ ] 鉴权判断改成：`"X-Emby-Token" in _get_emby_headers(_request, _api_key)`
- [ ] 以下四个请求全部改走 `_request_upstream_json(...)`

需要收敛的上游调用：
- `GET /Items/{i_id}`
- `POST /Items/{i_id}`
- `GET /Users/{u_id}/Items/{i_id}`
- `POST /Users/{u_id}/Items/{i_id}/UserData`

建议目标签名：

```python
async def fix_runtime_and_sync(u_id, i_id, pos_ticks, rt_ticks, evt_type, _upstream_url, _request, _api_key):
```

### 任务 5：完成验证

**文件：**
- 验证：`app/core/emby/standalone_proxy.py`

- [ ] 运行 `python -m py_compile app/core/emby/standalone_proxy.py`
- [ ] 复跑 `PlaybackInfo` 模拟验证脚本，确认无回归
- [ ] 如有条件，再补充一次代理路径透传的基础验证

---

## 先前验证记录

`PlaybackInfo` 修复已经通过模拟脚本验证。脚本关键手法：

```python
sp._get_upstream_item_payload = fake_upstream
sp._get_local_playback_record_by_path = fake_path_lookup
```

调用方式：

```python
sp._intercept_playback_info('http://upstream', 'cfg', '/Items/16068/PlaybackInfo', req)
```

关键通过信号：

```text
STATUS 200
HAS_PICKCODE True
HAS_ITEMID True
HAS_FILENAME True
```

这部分是当前重构必须保留的行为基线。

---

## 风险与注意事项

- 当前 `fix_runtime_and_sync(...)` 函数体顶部存在 `return`，说明后台任务逻辑处于临时禁用状态。重构时不要误以为它当前已在生产执行。
- 不要回退已完成的 `PlaybackInfo` 修复逻辑。
- `_request_upstream_json(...)` 当前要求能从请求头、query 或配置中拿到 `X-Emby-Token`，否则会提前返回 `None`。迁移调用点时要保留这种失败语义。
- `_build_upstream_proxy_request(...)` 负责透明代理的请求拼装，但响应清理、重定向透传和 streaming 生命周期仍然应该保留在 `_proxy_request(...)`。

---

## 建议执行顺序

1. 先改 `_get_upstream_item_payload()`
2. 再改 `_proxy_request()`
3. 再改 `/Sessions/Playing` 分支里的 `UserId` 提取
4. 最后改 `fix_runtime_and_sync()`
5. 完成后运行 `py_compile` 和 PlaybackInfo 回归验证

这样可以先完成低风险结构收敛，再处理后台任务里的多请求同步逻辑。

---

## 自检

- 当前状态描述：已明确区分“已完成修复”和“未完成迁移”
- 交接信息完整性：包含目标、现状、未完成项、建议改法、验证步骤和风险
- 新会话可执行性：新会话可以先读本文件，再直接进入 `standalone_proxy.py` 继续收尾

## 执行交接

本计划已写入 `docs/superpowers/plans/2026-06-07-standalone-proxy-request-refactor-handoff.md`。

建议新会话直接按“建议执行顺序”继续实现，并在完成后补一次最小验证闭环：`py_compile` + PlaybackInfo 模拟验证。