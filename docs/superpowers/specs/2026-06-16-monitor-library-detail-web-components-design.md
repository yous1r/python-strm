# Monitor 与 Library Detail Web Components 迁移设计

## 背景

当前前端重构已经具备以下基础设施：

- 基于 `solid-js`、`typescript`、`vite` 的 Web Components 注册与构建链路
- 组件契约清单 `frontend/src/ui/manifest.ts`
- MCP 组件目录服务 `mcp/ui-components-server`
- 一批已经迁移完成的页面组件，如 `ps-search-page`、`ps-transfer-page`、`ps-notify-page`

但 `monitor`、`library-detail`、`debug` 仍未完成同等深度的迁移。其中 `ps-monitor-page` 和 `ps-library-detail-page` 目前仅存在页面契约，真实实现仍落在 Jinja 模板与内联脚本里。结果是：

- “所有页面已组件化” 这一目标尚未成立
- MCP 返回的页面蓝图对这两页仍然过于粗糙，AI 仍无法基于真实公共组件进行拼装
- 交互能力还没有沉淀为可复用积木

本轮子项目优先处理 `monitor` 与 `library-detail`，并采用“先抽公共交互组件，再回头拼页面”的顺序。

## 目标

1. 将 `monitor` 和 `library-detail` 的核心交互从 Jinja 模板与内联脚本迁移到真实的 Solid Web Components 页面。
2. 在迁移页面之前，先抽出这两页共享或可复用的公共交互组件。
3. 让 MCP 组件目录可以暴露这些新公共组件及其真实页面蓝图，供 AI 按契约组合页面。

## 非目标

- 本轮不处理 `debug` 页的真实迁移，只保留在下一波。
- 本轮不追求一次性抽出所有潜在公共组件，只提取 `monitor` 和 `library-detail` 明确依赖的第一批交互积木。
- 本轮不修改后端 API 协议，只在前端页面层重构交互与数据编排。

## 范围

### 页面范围

- `app/web/templates/monitor.html`
- `app/web/templates/library_detail.html`
- `frontend/src/elements/components.tsx`
- `frontend/src/elements/register-elements.test.ts`
- `frontend/src/ui/manifest.ts`
- `frontend/src/ui/manifest.test.ts`
- `mcp/ui-components-server/src/catalog-tools.test.ts`
- `mcp/ui-components-server/src/server.test.ts`
- `tests/test_web_component_page_shell.py`

### 公共组件范围

第一批新增公共交互组件：

- `ps-tab-group`
- `ps-bulk-action-bar`
- `ps-destination-dialog`
- `ps-infinite-list`

## 核心决策

### 1. 先抽交互骨架，再拼页面

页面业务不会先直接写成两个新的巨型 page component，而是先抽出交互骨架，再由页面组件负责 API 与业务状态。这样做的原因是：

- 后续 MCP 暴露的是可复用积木，而不是两个仅能整页使用的黑盒
- 公共组件边界会更稳定，后续再迁移 `debug` 或其他页面时能直接复用
- 避免把新的页面实现再次堆进 `components.tsx` 的超大文件中而没有清晰分层

### 2. 公共组件只处理交互，不处理业务

所有公共组件都必须满足以下原则：

- 只暴露 `props`、`slots`、`events`
- 不直接调用业务 API
- 不知道 Telegram、115、tg_resources 等业务名词
- 页面层负责请求、状态拼装与错误反馈

### 3. 模板最终退化为薄壳

迁移完成后：

- `monitor.html` 与 `library_detail.html` 不再保留核心内联脚本
- 模板只负责 `base.html` 扩展、标题/副标题和页面组件挂载
- 交互状态、事件绑定、数据加载全部进入 Web Component

## 公共组件设计

### `ps-tab-group`

**责任**

- 管理 tabs 激活态
- 渲染 tab 按钮语义
- 控制面板显隐
- 提供基本键盘可访问性

**输入**

- `tabs`: JSON，tab 列表
- `active-tab`: 当前激活 tab id

**输出事件**

- `ps-tab-change { tabId }`

**边界**

- 不承载业务字段
- 每个面板内容仍由外部页面提供

### `ps-bulk-action-bar`

**责任**

- 展示总数、已选数、全选状态
- 渲染一组批量操作入口
- 统一批量操作栏的布局与按钮触发

**输入**

- `selected-count`
- `total-count`
- `all-selected`
- `disabled`
- `actions`: JSON，批量操作定义

**输出事件**

- `ps-toggle-all`
- `ps-bulk-action { action }`

**首个使用页面**

- `ps-library-detail-page`

### `ps-destination-dialog`

**责任**

- 展示目标云盘与资源类型
- 呈现目的地列表
- 承载确认与取消动作

**输入**

- `open`
- `cloud-type`
- `resource-type`
- `target-label`
- `destinations`: JSON
- `loading`

**输出事件**

- `ps-close`
- `ps-confirm { dirId, cloudType }`

**边界**

- 不直接发起转存 API
- 页面收到确认事件后自行调用业务接口

### `ps-infinite-list`

**责任**

- 对给定 `items` 做分批渲染
- 执行触底续渲染
- 统一空状态与列表容器行为

**输入**

- `items`: JSON
- `batch-size`
- `empty-text`

**输出事件**

- 可选 `ps-list-exhausted`

**边界**

- 不理解列表项业务结构
- 列表行内容由外层页面注入

## 页面设计

### `ps-monitor-page`

**保留在页面层的业务能力**

- 读取 `/api/v1/system/config`
- 组装并提交 `/api/v1/system/config PATCH`
- 调用 `/api/v1/system/test-monitor/telegram`
- 调用 `/api/v1/system/scrape-monitor/telegram`
- 调用 `/api/v1/search/tmdb`
- 将 TMDB 搜索结果转换为 regex 并回填过滤规则

**页面结构**

- 顶部说明区
- `ps-tab-group` 承载三个分区：
  - 实时监听
  - 历史同步
  - 启动编排
- TMDB 搜索弹窗使用 `ps-modal-dialog`
- 保存操作保留在页面底部固定操作区

**迁移后期望**

- 移除模板内 tab 切换逻辑
- 移除模板内配置加载/保存脚本
- 由页面组件统一管理本地表单状态与通知

### `ps-library-detail-page`

**保留在页面层的业务能力**

- 从 URL 解析 `base_title`
- 调用 `/api/v1/library/tg_resources/episodes`
- 维护剧集选中状态
- 调用 `/api/v1/library/transfer_destinations`
- 调用 `/api/v1/library/transfer_selected`
- 转存成功后刷新详情列表

**页面结构**

- 返回入口与标题区
- 剧集概览区
- `ps-bulk-action-bar` 承载全选、已选统计与转存入口
- `ps-infinite-list` 承载剧集列表懒渲染
- `ps-destination-dialog` 承载统一目的地确认流程

**迁移后期望**

- 移除模板内批量选择逻辑
- 移除模板内懒加载脚本
- 移除模板内目的地弹窗提交逻辑

## 数据流设计

### Monitor

1. 页面挂载
2. `GET /api/v1/system/config`
3. 将 `monitor.telegram` 与 `monitor.startup_pipeline` 映射到本地状态
4. `ps-tab-group` 仅切换视图，不触发业务请求
5. 保存时将页面状态组装为 `PATCH /api/v1/system/config` 的 payload
6. 测试连通性时使用当前未保存表单快照调用 `/api/v1/system/test-monitor/telegram`
7. 历史同步时使用当前未保存表单快照调用 `/api/v1/system/scrape-monitor/telegram`
8. TMDB 搜索结果点击后转换为 regex 并回填过滤规则字段

### Library Detail

1. 页面挂载
2. 从 `window.location.search` 读取 `base_title`
3. `GET /api/v1/library/tg_resources/episodes`
4. 将详情数据和剧集数组写入本地状态
5. `ps-infinite-list` 根据数组分批渲染
6. `ps-bulk-action-bar` 读取已选数量、总数、全选状态
7. 用户发起单集/选中/整剧转存时，页面打开 `ps-destination-dialog`
8. 用户在弹窗中确认目标目录后，页面调用 `/api/v1/library/transfer_selected`
9. 成功后提示并刷新详情

## Manifest 与 MCP 变更

本轮必须同步更新以下契约层：

- `componentManifest` 中新增 4 个公共组件定义
- `ps-monitor-page` 与 `ps-library-detail-page` 的事件定义完善
- `pageBlueprint("monitor", ...)` 从默认占位升级为真实 `regions` 与 `dataFlows`
- `pageBlueprint("library-detail", ...)` 从默认占位升级为真实 `regions` 与 `dataFlows`

MCP 层必须能够：

- 通过 `list_components` 发现新公共组件
- 通过 `get_component_contract` 返回新组件契约
- 通过 `compose_page_blueprint` 返回 `monitor` 和 `library-detail` 的真实积木结构

## 实施顺序

1. 新增公共组件契约与渲染实现
2. 为公共组件补充单元测试
3. 迁移 `ps-monitor-page`
4. 为 `ps-monitor-page` 补充页面交互测试
5. 迁移 `ps-library-detail-page`
6. 为 `ps-library-detail-page` 补充页面交互测试
7. 将 `monitor.html` 与 `library_detail.html` 收缩为薄模板
8. 更新 MCP 蓝图、资源与 prompt 测试
9. 运行前端构建、类型检查与相关 Python 模板测试

## 测试策略

### 前端组件测试

- `ps-tab-group`：切换、激活态、事件派发
- `ps-bulk-action-bar`：统计显示、全选事件、动作事件
- `ps-destination-dialog`：显隐、目的地选择、确认事件
- `ps-infinite-list`：初始批次、继续渲染、空状态

### 页面测试

- `ps-monitor-page`
  - 配置加载
  - tab 切换
  - 保存 payload 组装
  - 测试连通性请求
  - 历史同步请求
  - TMDB 搜索与 regex 回填
- `ps-library-detail-page`
  - 详情加载
  - 懒渲染批次推进
  - 全选/反选与计数
  - 单集/选中/整剧转存
  - 目的地选择确认
  - 成功后刷新

### MCP 与契约测试

- `manifest.test.ts`
- `catalog-tools.test.ts`
- `server.test.ts`

### Python 模板回归测试

- `tests/test_web_component_page_shell.py`

迁移完成后，`monitor.html` 与 `library_detail.html` 不应继续保留核心交互脚本与业务 DOM 结构。

## 风险与约束

### 风险 1：`components.tsx` 继续膨胀

如果新增公共组件和新页面仍全部堆在同一文件，后续维护成本会继续上升。

**应对**

本轮实现中允许把新增组件与页面逻辑拆出页面私有模块或组件模块，不强制继续堆入单文件。

### 风险 2：懒渲染组件过度抽象

`ps-infinite-list` 如果试图一次解决所有列表场景，容易过度设计。

**应对**

只实现 `library-detail` 当前所需的最小能力：批次渲染、继续加载、空状态。

### 风险 3：页面与公共组件职责混淆

如果 `ps-destination-dialog` 直接开始调用业务 API，后续复用会失败。

**应对**

严格保持“组件只发事件，页面负责 API”。

## 完成判定

当以下条件同时成立时，本子项目才算完成：

1. `ps-monitor-page` 与 `ps-library-detail-page` 具备真实 Solid 实现，不再依赖模板内核心脚本。
2. `ps-tab-group`、`ps-bulk-action-bar`、`ps-destination-dialog`、`ps-infinite-list` 已作为公共组件进入契约清单。
3. `monitor` 与 `library-detail` 的 MCP 页面蓝图已升级为真实 `regions` 与 `dataFlows`。
4. 前端测试、类型检查、构建，以及相关 Python 模板测试通过。
5. `debug` 页仍可保留到下一波，不作为本子项目阻塞项。
