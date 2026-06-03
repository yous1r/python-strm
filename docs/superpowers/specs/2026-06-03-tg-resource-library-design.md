# Telegram 资源库与历史导入功能设计

## 1. 目标
支持导入 Telegram 导出的 JSON 历史记录文件，并将包含 115/123pan 分享链接的影视资源统一收录到本地数据库中。同时建立一个独立的“资源库”页面，支持名称检索和一键异步转存功能。

## 2. 数据库设计 (tg_resources)
在 `app/database.py` 中新增 `tg_resources` 表，字段包括：
- `id`: INTEGER PRIMARY KEY AUTOINCREMENT
- `title`: TEXT NOT NULL (提取提纯后的资源名称)
- `raw_text`: TEXT (原始消息内容备查)
- `link`: TEXT NOT NULL UNIQUE (**分享链接，设置唯一索引，天然去重**)
- `password`: TEXT (提取码)
- `disk_type`: TEXT (网盘类型，如 '115', '123')
- `channel_name`: TEXT (来源频道名称，可选)
- `msg_date`: DATETIME (消息原始发布时间)
- `status`: TEXT DEFAULT 'pending' (状态：'pending' 待转存, 'queued' 排队中, 'success' 已转存, 'failed' 失败)
- `created_at`: DATETIME DEFAULT CURRENT_TIMESTAMP

**去重逻辑**：
利用 `link` 字段的 `UNIQUE` 属性。在执行 `INSERT OR IGNORE` 时，任何已存在于库中的链接都将被直接忽略。同一个影视剧可以有不同的链接（多条数据），但相同的链接绝对不会重复。

## 3. 核心入库逻辑与清洗规则
- **标题智能提纯**：
  使用正则表达式去掉常见冗余词汇，如“复制这段内容”、“访问码”、“提取码”、“115生活APP”等。截取剩余有效文本的第一行或最长有意义字符串作为 `title`。
- **数据通道统一**：
  1. **离线 JSON 导入**：解析 JSON 中的 `messages` 数组，对每条消息调用 `extract_links` 和提纯函数，执行入库（状态设为 `pending`）。
  2. **实时监控/API全量抓取**：拦截到的每条新消息，首先执行提纯与入库（状态初始为 `pending`）。如果系统开启了自动转存，立刻通过 `event_bus` 触发转存队列，并将数据库状态同步更新为 `queued`。如果失败变更为 `failed`，成功变更为 `success`。

## 4. API 接口层设计
- `POST /api/v1/library/upload_json`: 接收 `multipart/form-data` 格式的 JSON 文件。后台通过异步读取文件并解析，返回开始解析的响应。
- `GET /api/v1/library/tg_resources`: 获取资源列表，支持分页 (limit, offset) 和标题关键字模糊搜索 (?search=xxx)。
- `POST /api/v1/library/transfer/{id}`: 一键转存接口。将指定 ID 的资源推入全局 3 秒间隔的安全队列中，并将数据库状态标记为 `queued`。

## 5. 独立前端页面 (`app/web/templates/library.html`)
- **布局**：与现有毛玻璃风格保持一致的全新页面。
- **功能区**：
  - 左侧：搜索框，支持实时输入检索。
  - 右侧：【导入 TG JSON 历史记录】按钮。
- **数据展示区**：
  - 列表/网格形式呈现资源库。
  - 字段展示：资源名称、原始出处、日期、状态标识。
  - **交互**：针对 `pending` 或 `failed` 的条目提供高亮的“一键转存”按钮；点击后通过 Ajax 请求后台并立刻将 UI 更新为“正在排队”，利用现成的后台锁防止 115 封控。

## 6. 测试与安全要求
- 确保超大型 JSON 文件 (100MB+) 解析时不会造成内存 OOM，优先采用增量或异步阻塞切片。
- 转存请求必须通过系统底层的 `transfer_semaphore` 进行节流控制。
