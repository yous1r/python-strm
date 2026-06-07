# STRM 播放索引方案 2 设计

## 背景

当前项目生成的 STRM 文件内容已经直接使用 115 `pickcode`：

```text
{base_url}/api/v1/115/play/{pickcode}/{filename}
```

因此 302 播放链路的稳定核心不是 Emby/FNOS/Jellyfin 返回的媒体库路径，而是本项目生成 STRM 时记录下来的云盘文件身份：

- `file_id`：115 文件稳定身份，对应 115 `fid`。
- `play_identity`：播放稳定身份，对应 115 `pickcode`。
- `strm_records`：本项目 STRM 生成 manifest，是主数据源。

第三方媒体服务器可能返回自己的容器挂载路径，例如 `/mnt/strm-self/archive/.../*.strm`。这类路径只适合做兼容 fallback，不应作为高效播放索引主键。

## 目标

1. 保持 `strm_records` 作为 STRM 生成记录主表，优先保证本项目 STRM 文件生成、重写和清单查询正常。
2. 新增 `media_item_links` 扩展表，用于缓存第三方媒体服务器 `media_item_id` 到 `file_id` / `pickcode` 的映射。
3. PlaybackInfo 本地合成优先通过 `media_item_links` 命中，再从 STRM URL 直接提取 `pickcode`，最后才走旧路径反查。
4. 字段命名保持媒体服务器无关，兼容 Emby、Jellyfin、FNOS 等接入方。

## 数据模型

### `strm_records`

主表职责不变：记录当前项目生成的 STRM manifest。

关键字段：

- `cloud_type`：云盘类型，当前 115 固定为 `115`。
- `file_id`：云盘文件 ID，115 场景为 `fid`。
- `play_identity`：播放身份，115 场景为 `pickcode`。
- `archive_rel_path` / `strm_rel_path` / `strm_abs_path`：STRM 生成与重写所需路径。
- `status` / `task_id`：生成状态与任务追踪。

### `media_item_links`

扩展表职责：缓存媒体服务器 item 到云盘播放身份的链接。

关键字段：

- `media_server_type`：媒体服务器类型，例如 `emby`、`jellyfin`、`fnos`。
- `media_server_name`：实例名，避免多个实例 item id 冲突。
- `media_item_id`：媒体服务器 item id。
- `media_source_id`：媒体源 id，默认空字符串。
- `cloud_type`：云盘类型。
- `file_id`：云盘文件 ID。
- `play_identity`：播放身份。
- `strm_record_id`：可选关联 `strm_records.id`。
- `source_path`：发现映射时的媒体服务器路径，仅用于排查和 fallback 追踪。

唯一约束：`media_server_type + media_server_name + media_item_id + media_source_id`。

## PlaybackInfo 解析顺序

1. 用 `media_server_type/media_server_name/media_item_id/media_source_id` 查询 `media_item_links`。
2. 若上游 item `Path` 或 `MediaSources[0].Path` 已包含 `/api/v1/115/play/{pickcode}`，直接提取 `pickcode` 并回填 `media_item_links`。
3. 若前两步失败，再使用配置路径映射和旧 `strm_output` 规则反查 `strm_records`，命中后回填 `media_item_links`。
4. 若仍未找到 `play_identity`，返回 404，避免给播放器返回无效播放源。

视频流直连请求 `/Videos/{item_id}/stream` 复用同一条解析链路：

1. 先查 `media_item_links`。
2. 再读取上游 item 的 `Path/MediaSources`。
3. 优先从项目 STRM URL 中直接提取 `pickcode`。
4. 最后 fallback 到本地 manifest 的路径反查。

## 兼容策略

- 保留 `_resolve_local_strm_path()` 的配置映射和旧 `strm_output` fallback，现有测试继续有效。
- `strm_records` 的旧字段兼容迁移继续保留，新增索引只增强查询性能。
- `media_item_links` 可通过运行期 `CREATE TABLE IF NOT EXISTS` 初始化，无需破坏已有测试数据库。
- `EmbyProxyInstanceConfig` 增加可选 `media_server_type` 字段，默认 `emby`，为 Jellyfin/FNOS 等兼容接入预留统一身份字段。

## 失效清理

`strm.clean_invalid=true` 时，115 本地缓存同步后触发的 STRM 刷新会顺带执行方案 B 清理：

1. 以当前同步目录对应的 manifest 子树为作用域。
2. 用 115 本地缓存中的存活 `file_id` 集合作为真值来源。
3. 删除缓存中已不存在的 `strm_records` 记录。
4. 同时删除对应磁盘 `.strm` 文件。
5. 级联删除关联的 `media_item_links`，避免播放器命中过期索引。

## 验证要求

- `uv run python -m py_compile app/database.py app/core/transfer/strm_manifest.py app/core/emby/standalone_proxy.py`
- `uv run python -m unittest tests.test_strm_manifest tests.test_emby_standalone_proxy`