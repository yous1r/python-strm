# monitor.html Telegram 监控页 Tabs 重构设计

## 背景

当前 `app/web/templates/monitor.html` 已经完成历史同步能力的数据字段接入：

- `立即历史同步` 按钮
- `history_sync` 三种模式：`all / relative_range / date_range`
- 最近跨度数值与单位
- 日期选择器 `date_start / date_end`
- 分片窗口天数 `chunk_days`
- 是否在历史同步时发出资源事件
- 后台定时同步开关和间隔
- 任务状态提示区

同时，后端接口和保存逻辑也已就绪：页面保存会把这些字段写入 `monitor.telegram.history_sync`，历史同步请求会走新的 `/api/v1/system/telegram/history-sync` 及兼容入口。

当前剩余问题不在功能字段，而在 UI 信息架构：页面仍然是一个大的“Telegram 监控节点设置”面板，内部嵌入“历史同步”和“启动编排”小节，尚未形成清晰的独立区域结构。

## 目标

把 `monitor.html` 从“单一大面板 + 内嵌小节”重构为**Tab 化的三段式结构**，让以下三个域在视觉与语义上都独立：

1. 实时监听
2. 历史同步
3. 启动编排

## 非目标

本次不处理以下内容：

- 不改后端接口
- 不改字段命名
- 不改保存 payload 结构
- 不改历史同步请求路径
- 不引入新的状态管理
- 不新增与本次布局重构无关的交互能力

## 用户确认的关键约束

### 1. 区域组织方式

页面不采用纵向三个卡片堆叠，而采用 **Tabs 切换**。

### 2. 启动编排归属

“启动编排”必须从 Telegram 区块中彻底拿出去，成为**独立 Tab**，而不是实时监听的附属配置。

### 3. `mon_tg_enabled` 的语义

`mon_tg_enabled` 只控制**实时监听**，不再代表整个 Telegram 能力总开关。历史同步与启动编排在视觉和语义上都独立。

### 4. 历史同步主操作归属

“立即历史同步”按钮必须移动到“历史同步”区域本身，作为该区域的主操作，而不是停留在页面总标题处。

## 现状分析

当前 `monitor.html` 主要问题：

1. 页面主标题承担了过多职责：页面名称、测试操作、历史同步操作、总开关都堆叠在一个标题行中。
2. “历史同步”虽然新增了完整字段，但仍以 Telegram 监控的下级小节出现，用户心智上仍像“附属配置”。
3. “启动编排”与 Telegram 实时监听混杂，弱化了它的全局启动编排属性。
4. 已有保存逻辑是统一 payload，这一点应保留，不需要因为布局变化而拆成多个保存接口。

## 设计方案

## 一、页面骨架

### 页面说明区

在页面顶部保留一个**轻量说明区**，只负责：

- 显示页面级标题
- 简要说明这是 Telegram 相关监听、历史同步与启动编排的统一配置页

该区域**不再承载操作按钮**，避免页面级标题与域内操作混合。

### Tabs 容器

说明区下方放置一个 Tabs 容器，固定三个标签：

1. `实时监听`
2. `历史同步`
3. `启动编排`

默认激活首个 Tab：`实时监听`。

### 全局保存区

页面底部继续保留一次统一的“保存并热加载”按钮。

含义保持不变：三个 Tab 共享同一份配置提交，用户切换 Tab 不触发自动保存。

## 二、Tab 边界设计

### Tab 1：实时监听

#### 标题区

标题文本：`实时监听`

标题行右侧保留：

- `测试连通性` 按钮
- `mon_tg_enabled` 开关

#### 内容范围

该 Tab 只包含实时监听相关字段：

- API ID
- API Hash
- Bot Token
- Proxy
- 监听频道列表
- 消息关键字过滤
- 资源匹配过滤规则
- TMDB 可视化配置正则入口
- Telegram 自动转存统一走媒体库归档目录的说明
- `auto_strm` 开关

#### 明确排除

该 Tab **不出现**：

- 任何历史同步字段
- 任何启动编排字段
- “立即历史同步”按钮

### Tab 2：历史同步

#### 标题区

标题文本：`历史同步`

标题行右侧保留：

- `立即历史同步` 按钮

该按钮继续复用现有 `scrapeTgMonitor()` 逻辑，只调整摆放位置。

#### 内容范围

该 Tab 只包含历史同步相关字段：

- `history_sync.mode`
- `history_sync.relative_value`
- `history_sync.relative_unit`
- `history_sync.date_start`
- `history_sync.date_end`
- `history_sync.chunk_days`
- `history_sync.emit_new_link_events`
- `history_sync.scheduled_enabled`
- `history_sync.scheduled_interval_minutes`
- 历史同步任务状态提示区

#### 语义说明

此 Tab 在产品语义上**独立于 `mon_tg_enabled`**。

也就是说：

- `mon_tg_enabled` 只表示实时监听开关
- 历史同步 Tab 的显示与配置不依赖该开关的开/关状态

### Tab 3：启动编排

#### 标题区

标题文本：`启动编排`

不需要额外的标题行主操作按钮。

#### 内容范围

该 Tab 只包含启动编排字段：

- `monitor.startup_pipeline.enabled`
- `monitor.startup_pipeline.run_db_sync`
- `monitor.startup_pipeline.run_telegram_sync`
- `monitor.startup_pipeline.run_strm_sync`

#### 说明文案

需要明确说明：

这是**服务启动后的全局编排行为**，不是 Telegram 实时监听的下级选项。

这样用户在认知上能把它与历史同步、实时监听区分开。

## 三、视觉与交互规则

### 复用现有 Tabs 风格

优先参考 `app/web/templates/organize.html` 已存在的 Tabs 实现，包括：

- `.tabs`
- `.tab-btn`
- `.tab-pane`
- 简单的 `switchTab(tabId)` 交互

这样做的原因：

1. 与项目现有页面风格一致
2. 避免引入新的交互模型
3. 只需最小量 CSS/JS 变更即可落地

### Tab 切换行为

切换 Tab 时只切换显示状态：

- 不重新加载配置
- 不触发保存
- 不触发额外网络请求

### 表单组织

虽然视觉上是三个 Tab，但 DOM 仍可继续位于同一个 `<form id="monitorForm">` 中。

这意味着：

- `saveMonitorConfig()` 可以继续读取现有 DOM id
- 保存 payload 结构保持不变
- 这次主要修改布局结构，不需要重写保存逻辑

### 动作函数复用

以下函数应直接复用，不改职责：

- `saveMonitorConfig()`
- `testTgMonitor()`
- `scrapeTgMonitor()`

需要调整的只有按钮位置和可能的少量选择器上下文，但不重写业务流程。

## 四、实现建议

### 模板层

在 `app/web/templates/monitor.html` 中完成以下重组：

1. 把当前单个大 `glass-panel` 拆成：
   - 页面说明区
   - Tabs 导航
   - 三个 `tab-pane`
2. 将现有表单字段按职责迁移到对应 Tab Pane
3. 将“测试连通性”迁移到实时监听标题行
4. 将“立即历史同步”迁移到历史同步标题行
5. 将“启动编排”从 Telegram 大面板中抽离为独立 Tab 内容

### 样式层

补充最小量 CSS：

- Tabs 容器样式
- Tab 按钮激活态
- Tab Pane 显隐
- 与当前玻璃卡片样式兼容的间距

如无必要，不新建单独静态文件，直接沿用当前模板内联样式模式。

### 脚本层

新增或复用简单 Tab 切换函数：

- 默认激活 `实时监听`
- 点击按钮时切换 `.active`

除此之外，不新增复杂脚本状态。

## 五、错误处理与兼容性

### 配置加载

配置加载逻辑保持现状。因为 DOM id 不变，`DOMContentLoaded` 时的字段回填逻辑可基本不改。

### 保存兼容性

因为字段 id 和 payload 结构不变，`PATCH /api/v1/system/config` 的兼容性保持不变。

### 历史同步兼容性

因为 `scrapeTgMonitor()` 不改业务逻辑，所以仍然兼容：

- `/api/v1/system/telegram/history-sync`
- 兼容旧入口

## 六、测试关注点

本次重构完成后，至少需要验证以下场景：

1. 页面初次加载后，三个 Tab 均能正确显示各自字段
2. 默认 Tab 为“实时监听”
3. 切换 Tab 不丢失当前已输入但未保存的表单内容
4. 点击“测试连通性”仍调用原测试逻辑
5. 点击“立即历史同步”仍调用原历史同步逻辑
6. 保存后：
   - `monitor.telegram.*` 正常写入
   - `monitor.telegram.history_sync.*` 正常写入
   - `monitor.startup_pipeline.*` 正常写入
7. `mon_tg_enabled` 的存在不会误导为历史同步总开关

## 七、验收标准

当满足以下条件时，本次设计视为实现完成：

1. `monitor.html` 不再呈现为一个包含多个内嵌小节的大 Telegram 面板
2. 页面主体以三个独立 Tab 展示：实时监听、历史同步、启动编排
3. “立即历史同步”位于历史同步 Tab 内部标题区
4. `mon_tg_enabled` 只在实时监听 Tab 中出现，并只表达实时监听开关语义
5. 启动编排不再视觉上挂在 Telegram 监听之下
6. 所有既有字段、保存逻辑、测试逻辑、历史同步逻辑保持兼容

## 八、范围边界确认

本规格覆盖一次**单页面 UI 信息架构重构**，范围适中，可以由单一实现计划承接，无需继续拆分子项目。
