## 115 全链路验证设计

### 背景

当前项目已经具备以下能力：

- `db_sync`：将 115 网盘目录树同步到本地 SQLite 缓存。
- `strm_records` / `media_item_links`：维护 STRM 生成记录和播放索引。
- `StrmGenerator115.batch_generate()`：依据 115 本地缓存与 manifest 刷新 `.strm` 文件，并清理失效记录。

但这些能力目前没有被清晰拆分成“可单独调用的步骤”，也没有一条可复用的事件链把“115 本地目录树更新 -> manifest 刷新 -> STRM 更新”组合成统一流程。`/debug` 页面也缺少一个能直接验证这条完整链路的入口。

本设计的目标是补上一条事件化的 115 全链路验证流程，并在 debug 页面提供单按钮触发能力，为后续后台定时增量同步复用同一条链路做准备。

### 目标

1. 将 115 全链路验证拆成三个可独立调用的步骤：
   - `db_sync`
   - `strm_records` 刷新
   - `strm` 更新
2. 使用事件总线按顺序编排这三个步骤，形成可复用的大流程。
3. 在 debug 页面增加“115 全链路验证”按钮，用于手动触发该流程。
4. 为后续后台定时增量同步复用同一条事件链，避免 debug 和生产链路分叉。
5. 返回可观察的阶段结果与统计信息，便于判断验证是否成功。

### 非目标

- 不修改 115 本地缓存数据库格式。
- 不改变现有 PlaybackInfo / `media_item_links` 设计。
- 不把三个步骤重新揉成一个难以复用的大函数。
- 不在本次改动中实现新的前端任务轮询中心；debug 页面只需要能触发并展示结果即可。

### 用户入口

在 `app/web/templates/debug.html` 中新增一个调试卡片：

- 按钮名称：`115 全链路验证`
- 触发方式：调用一个新的 debug API
- 展示内容：
  - 本次任务 `task_id`
  - 当前阶段
  - 每个目录的 `db_sync` 影响行数
  - `strm_records` 清理数量
  - 删除的 `.strm` 数量
  - 新生成或更新的 `.strm` 数量
  - 错误信息（如有）

### 架构拆分

#### 1. `db_sync` 步骤

职责：

- 对配置中的 115 目录执行本地目录树同步。
- 不再隐式包办全部后续逻辑。
- 输出每个目录的同步结果，作为后续步骤的输入。

输入：

- `cloud115.sync_dirs`
- `transfer.temp_dir_id`
- `transfer.archive_dir_id`

输出：

- 每个目录的 `dir_id`
- 目录名或逻辑名称
- 是否递归
- 影响行数 `count`
- 输出目录路径 `output_dir`

#### 2. `strm_records` 刷新步骤

职责：

- 基于 `db_sync` 刚更新的本地目录树，对对应目录执行 manifest 层刷新。
- 清理本地缓存中已不存在的 `file_id` 对应的 `strm_records`。
- 级联清理关联的 `media_item_links`。
- 产出结构化统计，为后续 `.strm` 更新提供上下文。

说明：

- 本项目当前的失效清理逻辑主要落在 `StrmGenerator115.batch_generate()` 内部。
- 为了满足“步骤分离”，需要把 manifest 清理统计从 STRM 生成中显式暴露出来，形成一个可单独调用的步骤，而不是只作为生成过程的副作用。

输出：

- `cleaned_records`
- `deleted_strm_files`
- 仍需刷新 `.strm` 的目录上下文

#### 3. `strm` 更新步骤

职责：

- 针对上一步输出的目录上下文执行 `.strm` 刷新。
- 复用现有 `StrmGenerator115.batch_generate()` 的生成能力。
- 返回生成数量和目录级统计。

输出：

- `generated_count`
- `generated_files`（可选，只在 debug 结果里保留裁剪后的样本）

### 事件设计

新增一组面向 115 全链路验证的事件，事件只表示阶段转换，不承载复杂业务逻辑：

- `cloud115.full_sync.requested`
  - 含义：请求启动 115 全链路验证。
- `cloud115.db_sync.finished`
  - 含义：本地目录树同步步骤完成。
- `cloud115.manifest_refresh.finished`
  - 含义：`strm_records` 刷新步骤完成。
- `cloud115.strm_refresh.finished`
  - 含义：`.strm` 更新步骤完成。
- `cloud115.full_sync.completed`
  - 含义：整条链路完成。

编排原则：

- debug API 只负责发布 `cloud115.full_sync.requested`。
- 编排服务订阅起始事件，执行 `db_sync`，完成后发布 `cloud115.db_sync.finished`。
- manifest 刷新处理器订阅 `cloud115.db_sync.finished`。
- STRM 刷新处理器订阅 `cloud115.manifest_refresh.finished`。
- 汇总处理器订阅 `cloud115.strm_refresh.finished` 并发布 `cloud115.full_sync.completed`。

这样可以保证每一步都能单独复用，也方便以后把某一步替换成不同实现。

### 编排服务

新增一个专用服务模块，负责：

- 组织 115 全链路验证上下文。
- 将步骤输出累计到统一结果对象中。
- 为 debug 页面和后续后台任务提供统一返回结构。

上下文建议字段：

- `task_id`
- `status`
- `current_stage`
- `started_at`
- `finished_at`
- `dirs`
- `db_sync_results`
- `manifest_results`
- `strm_results`
- `error`

统计字段建议：

- `db_sync_rows`
- `cleaned_records`
- `deleted_strm_files`
- `generated_strm_files`

该上下文需要支持短期内存查询，便于 debug 页面在触发后立即查看结果。实现上可以使用一个轻量的进程内字典或挂接到现有任务追踪结构，当前阶段优先选择简单可维护的方式。

### debug API 设计

新增两个 API：

1. `POST /api/v1/debug/cloud115/full-sync`
   - 作用：发布全链路验证起始事件。
   - 返回：`task_id`、初始阶段、提示信息。

2. `GET /api/v1/debug/cloud115/full-sync/{task_id}`
   - 作用：查询全链路验证结果。
   - 返回：当前阶段、是否完成、统计信息、错误信息。

这样前端不需要长时间阻塞等待单次 HTTP 请求结束，可以通过轮询方式显示进度。

### debug 页面设计

在现有调试页新增一个独立卡片：

- 标题：`9. 115 全链路验证`
- 描述：验证 `db_sync -> strm_records 刷新 -> strm 更新` 整条链路。
- 控件：
  - 一个触发按钮
  - 一个结果面板

交互流程：

1. 用户点击按钮。
2. 前端调用 `POST /api/v1/debug/cloud115/full-sync`。
3. 取得 `task_id` 后开始轮询 `GET /api/v1/debug/cloud115/full-sync/{task_id}`。
4. 页面持续展示当前阶段和累计统计。
5. 完成后停止轮询并显示最终结果。

### 与定时任务的关系

后续后台定时增量同步应优先复用这条事件链，而不是继续分别调用 `db_sync` 和 `batch_generate()`。

目标状态：

- debug 页面触发的是生产同源链路。
- 定时任务触发的是同一条生产链路。
- 两者只是在“谁发出起始事件”上不同。

### 错误处理

- 任一步骤失败时，记录 `current_stage` 和 `error`。
- 默认中止后续步骤，不继续发布下一阶段事件。
- `GET` 查询接口必须能返回失败状态，便于前端直接显示。
- 失败时保留已经完成阶段的统计，方便排错。

### 测试策略

需要补充以下测试：

1. 编排测试
   - 验证事件处理器按顺序推进。
   - 验证上一步失败时后续步骤不会执行。

2. debug API 测试
   - `POST` 接口能返回 `task_id`。
   - `GET` 接口能返回阶段信息和统计结果。

3. 步骤测试
   - `db_sync` 步骤输出目录结果。
   - `strm_records` 刷新步骤返回清理统计。
   - `strm` 更新步骤返回生成统计。

### 实施顺序

1. 定义新事件常量。
2. 抽出 115 全链路验证编排服务。
3. 将 `db_sync`、manifest 刷新、`strm` 更新拆成独立步骤函数。
4. 注册事件订阅关系。
5. 新增 debug API。
6. 新增 debug 页面按钮与轮询展示。
7. 补测试并验证。

### 成功标准

- debug 页面可一键触发 115 全链路验证。
- 页面能看到从 `db_sync` 到 `.strm` 更新的阶段推进。
- 结果中能看到 `db_sync_rows`、`cleaned_records`、`deleted_strm_files`、`generated_strm_files`。
- 任一步骤失败时可以在页面看到失败阶段和错误信息。
- 后续定时任务可复用同一条事件链，不再维护分叉逻辑。