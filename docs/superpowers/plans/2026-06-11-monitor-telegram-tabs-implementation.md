# monitor.html Telegram Tabs 重构实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `app/web/templates/monitor.html` 从“单个 Telegram 大面板内嵌历史同步 / 启动编排小节”重构为“实时监听 / 历史同步 / 启动编排”三个独立 Tab，同时保持现有字段、保存逻辑和历史同步请求兼容。

**架构：** 在 `monitor.html` 内部复用 `organize.html` 的轻量 Tabs 模式，新增一个页面说明区、一个 Tabs 导航和三个 `tab-pane`。保留现有 DOM id、现有 JS 函数 `saveMonitorConfig()` / `testTgMonitor()` / `scrapeTgMonitor()`，只调整模板结构、补充最小量 Tab CSS/JS，并更新页面测试断言以覆盖新布局。

**技术栈：** Jinja2 模板、原生 HTML/CSS/JavaScript、FastAPI `TestClient`、pytest

---

## 文件结构

- 修改：`app/web/templates/monitor.html`
  - 职责：Telegram 监控页面模板；承载页面结构、Tab 布局、内联样式、Tab 切换脚本，以及原有保存/测试/历史同步前端逻辑。
- 修改：`tests/test_debug_api.py`
  - 职责：校验 `/monitor` 页面输出的关键 HTML 片段，确保新的 Tab 结构与关键字段仍可被渲染。
- 参考：`app/web/templates/organize.html`
  - 职责：提供现有 Tabs 样式与 `switchTab(tabId)` 交互模式，供 `monitor.html` 复用实现思路。
- 参考：`docs/superpowers/specs/2026-06-11-monitor-telegram-tabs-design.md`
  - 职责：本计划对应的已确认规格，定义 Tab 边界、字段归属和验收标准。

## 任务 1：先用页面测试锁定新的 Tab 信息架构

**文件：**
- 修改：`tests/test_debug_api.py:105-116`
- 参考：`docs/superpowers/specs/2026-06-11-monitor-telegram-tabs-design.md`

- [ ] **步骤 1：编写失败的测试**

将现有 `test_monitor_page_contains_telegram_scheduled_sync_fields` 替换为一个直接描述新结构的页面测试，断言 `/monitor` 输出包含三个 Tab 标识和新的关键字段归属。

```python
def test_monitor_page_contains_telegram_tabs_and_history_sync_fields():
    app = FastAPI()
    app.include_router(web_router)

    client = TestClient(app)
    response = client.get("/monitor")

    assert response.status_code == 200
    assert "实时监听" in response.text
    assert "历史同步" in response.text
    assert "启动编排" in response.text
    assert "tab-realtime" in response.text
    assert "tab-history" in response.text
    assert "tab-startup" in response.text
    assert "mon_tg_history_mode" in response.text
    assert "mon_tg_history_scheduled_enabled" in response.text
    assert "mon_startup_pipeline_enabled" in response.text
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_debug_api.py::test_monitor_page_contains_telegram_tabs_and_history_sync_fields -v`

预期：FAIL，因为当前 `monitor.html` 还没有 `tab-realtime` / `tab-history` / `tab-startup` 这些 Tab 结构标识。

- [ ] **步骤 3：Commit 测试基线变更**

```bash
git add tests/test_debug_api.py
git commit -m "test: define monitor tabs page expectations"
```

## 任务 2：重构 `monitor.html` 顶部骨架与 Tab 导航

**文件：**
- 修改：`app/web/templates/monitor.html:5-175`
- 参考：`app/web/templates/organize.html:6-40`

- [ ] **步骤 1：重写内容区骨架，加入页面说明区与 Tabs 导航**

把当前单个大 `glass-panel` 顶部标题区拆为：

1. 一个轻量页面说明区
2. 一个 Tabs 导航条
3. 一个包含三个 `tab-pane` 的统一 `monitorForm`

新增的 HTML 结构应接近下面的组织方式：

```html
<div class="glass-panel animate-fade-in delay-1" style="max-width: 800px; margin-bottom: 1.5rem;">
    <h2 style="margin-bottom: 0.75rem;"><i class="fa-solid fa-satellite-dish"></i> Telegram 监控与同步</h2>
    <div style="color: var(--text-secondary); font-size: 0.95rem;">
        在同一页中统一管理实时监听、历史同步与启动编排配置。
    </div>
</div>

<div class="tabs" style="max-width: 800px; margin-bottom: 1.5rem;">
    <button type="button" class="tab-btn active" onclick="switchMonitorTab(event, 'realtime')">
        <i class="fa-solid fa-tower-broadcast"></i> 实时监听
    </button>
    <button type="button" class="tab-btn" onclick="switchMonitorTab(event, 'history')">
        <i class="fa-solid fa-clock-rotate-left"></i> 历史同步
    </button>
    <button type="button" class="tab-btn" onclick="switchMonitorTab(event, 'startup')">
        <i class="fa-solid fa-rocket"></i> 启动编排
    </button>
</div>

<form id="monitorForm">
    <div id="tab-realtime" class="tab-pane active"></div>
    <div id="tab-history" class="tab-pane"></div>
    <div id="tab-startup" class="tab-pane"></div>
</form>
```

- [ ] **步骤 2：把原实时监听字段迁移到 `tab-realtime`**

保留以下 DOM id 不变，仅迁移布局位置：

```html
<input type="checkbox" id="mon_tg_enabled">
<input type="text" id="mon_tg_api_id" class="form-control">
<input type="password" id="mon_tg_api_hash" class="form-control">
<input type="password" id="mon_tg_bot_token" class="form-control">
<input type="text" id="mon_tg_proxy" class="form-control">
<textarea id="mon_tg_channels" class="form-control"></textarea>
<textarea id="mon_tg_keywords" class="form-control"></textarea>
<textarea id="mon_tg_filter_rules" class="form-control"></textarea>
<input type="checkbox" id="mon_tg_auto_strm">
```

实时监听卡片标题行应包含：

```html
<h2 style="margin-bottom: 1.5rem; display: flex; align-items: center; justify-content: space-between; gap: 1rem;">
    <span><i class="fa-solid fa-tower-broadcast"></i> 实时监听</span>
    <span style="display: flex; align-items: center; gap: 1rem;">
        <button type="button" class="btn btn-secondary" onclick="testTgMonitor(this)">
            <i class="fa-solid fa-plug-circle-check"></i> 测试连通性
        </button>
        <label class="switch">
            <input type="checkbox" id="mon_tg_enabled">
            <span class="slider round"></span>
        </label>
    </span>
</h2>
```

- [ ] **步骤 3：运行单测，确认此时仍失败但错误前移到其他缺失 Tab 内容**

运行：`pytest tests/test_debug_api.py::test_monitor_page_contains_telegram_tabs_and_history_sync_fields -v`

预期：仍 FAIL，但不再缺少 `tab-realtime`，而会继续报告 `tab-history` / `tab-startup` 或对应字段缺失。

- [ ] **步骤 4：Commit 实时监听 Tab 骨架**

```bash
git add app/web/templates/monitor.html
git commit -m "refactor: split monitor realtime settings into tab layout"
```

## 任务 3：迁移历史同步与启动编排到独立 Tab

**文件：**
- 修改：`app/web/templates/monitor.html:66-166`

- [ ] **步骤 1：把历史同步字段迁移到 `tab-history` 并移动主操作按钮**

为历史同步创建独立卡片，并把“立即历史同步”移动到标题行右侧。

标题区结构：

```html
<div id="tab-history" class="tab-pane">
    <div class="glass-panel animate-fade-in" style="max-width: 800px; margin-bottom: 2rem;">
        <h2 style="margin-bottom: 1.5rem; display: flex; align-items: center; justify-content: space-between; gap: 1rem;">
            <span><i class="fa-solid fa-clock-rotate-left"></i> 历史同步</span>
            <button type="button" class="btn btn-secondary" style="color: var(--accent); border-color: var(--accent);" onclick="scrapeTgMonitor(this)">
                <i class="fa-solid fa-clock-rotate-left"></i> 立即历史同步
            </button>
        </h2>
    </div>
</div>
```

必须继续保留这些字段 id：

```html
<select id="mon_tg_history_mode" class="form-control"></select>
<input type="number" id="mon_tg_history_relative_value" class="form-control">
<select id="mon_tg_history_relative_unit" class="form-control"></select>
<input type="date" id="mon_tg_history_date_start" class="form-control">
<input type="date" id="mon_tg_history_date_end" class="form-control">
<input type="number" id="mon_tg_history_chunk_days" class="form-control">
<input type="checkbox" id="mon_tg_history_emit_events">
<input type="checkbox" id="mon_tg_history_scheduled_enabled">
<input type="number" id="mon_tg_history_scheduled_interval_minutes" class="form-control">
<div id="tgHistoryTaskHint"></div>
```

- [ ] **步骤 2：把启动编排字段迁移到 `tab-startup`**

创建独立的“启动编排”卡片，说明它属于服务启动时的全局编排，并保留这些字段 id：

```html
<input type="checkbox" id="mon_startup_pipeline_enabled">
<input type="checkbox" id="mon_startup_run_db_sync">
<input type="checkbox" id="mon_startup_run_telegram_sync">
<input type="checkbox" id="mon_startup_run_strm_sync">
```

建议标题和说明：

```html
<h2 style="margin-bottom: 1rem;"><i class="fa-solid fa-rocket"></i> 启动编排</h2>
<div style="color: var(--text-secondary); margin-bottom: 1rem; font-size: 0.95rem;">
    控制服务启动后是否自动执行全局初始化流水，包括 115 本地缓存同步、Telegram 历史同步与 STRM 增量同步。
</div>
```

- [ ] **步骤 3：运行页面测试，确认结构断言通过**

运行：`pytest tests/test_debug_api.py::test_monitor_page_contains_telegram_tabs_and_history_sync_fields -v`

预期：PASS，说明页面已具备三个独立 Tab 与新字段归属。

- [ ] **步骤 4：Commit Tab 内容迁移**

```bash
git add app/web/templates/monitor.html tests/test_debug_api.py
git commit -m "refactor: move monitor history and startup settings into tabs"
```

## 任务 4：补齐 Tab 样式与切换脚本，并验证现有逻辑未被破坏

**文件：**
- 修改：`app/web/templates/monitor.html:177-223`
- 修改：`app/web/templates/monitor.html:252-552`

- [ ] **步骤 1：补充最小量 Tab CSS**

在现有 `<style>` 中加入与 `organize.html` 一致的 Tabs 样式，最少包含以下片段：

```css
.tabs {
  display: flex;
  border-bottom: 1px solid rgba(255,255,255,0.1);
  margin-bottom: 1.5rem;
}

.tab-btn {
  background: none;
  border: none;
  color: var(--text-secondary);
  padding: 0.75rem 1.25rem;
  font-size: 1rem;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: all 0.3s ease;
}

.tab-btn.active {
  color: var(--primary);
  border-bottom-color: var(--primary);
}

.tab-pane { display: none; }
.tab-pane.active { display: block; }
```

- [ ] **步骤 2：新增 `switchMonitorTab(event, tabId)`，不要依赖隐式全局 `event`**

在 `<script>` 顶部附近加入明确接收事件对象的函数，避免照抄 `organize.html` 中对 `event.currentTarget` 的隐式依赖：

```javascript
function switchMonitorTab(event, tabId) {
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));

    event.currentTarget.classList.add('active');
    document.getElementById('tab-' + tabId).classList.add('active');
}
```

- [ ] **步骤 3：确认 `saveMonitorConfig()` / `testTgMonitor()` / `scrapeTgMonitor()` 不需要字段 id 改动**

逐项检查下列读取逻辑仍然有效；如果 DOM id 没变，就不要改动业务代码：

```javascript
document.getElementById('mon_tg_api_id').value
document.getElementById('mon_tg_history_mode').value
document.getElementById('mon_startup_pipeline_enabled').checked
```

此步骤的原则是 **只在布局需要的地方改代码，不做无关重写**。

- [ ] **步骤 4：运行目标测试确认最终通过**

运行：`pytest tests/test_debug_api.py::test_monitor_page_contains_telegram_tabs_and_history_sync_fields -v`

预期：PASS。

- [ ] **步骤 5：Commit Tab 样式与交互**

```bash
git add app/web/templates/monitor.html tests/test_debug_api.py
git commit -m "feat: add monitor page tabs for telegram settings"
```

## 任务 5：执行回归验证并整理交付

**文件：**
- 修改：`app/web/templates/monitor.html`
- 修改：`tests/test_debug_api.py`

- [ ] **步骤 1：运行 `/monitor` 页面相关测试**

运行：`pytest tests/test_debug_api.py -v`

预期：与 `/debug`、`/monitor` 页面相关的现有测试全部 PASS。

- [ ] **步骤 2：运行更聚焦的 HTML 关键字检查（可选命令行验证）**

运行：`python - <<'PY'
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.web import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)
html = client.get('/monitor').text
for token in ['tab-realtime', 'tab-history', 'tab-startup', 'mon_tg_history_mode', 'mon_startup_pipeline_enabled']:
    print(token, token in html)
PY`

预期：输出的每个 token 都是 `True`。

- [ ] **步骤 3：检查工作区差异**

运行：`git status --short`

预期：只出现本计划涉及的实现文件改动；没有意外文件变更。

- [ ] **步骤 4：Commit 最终整理**

```bash
git add app/web/templates/monitor.html tests/test_debug_api.py
git commit -m "test: verify monitor tabs page layout"
```

## 自检结论

- **规格覆盖度：** 已覆盖页面骨架、三个 Tab 的字段边界、`mon_tg_enabled` 语义隔离、“立即历史同步”按钮迁移、统一保存、最小 JS/CSS 变更，以及页面测试验证。
- **占位符扫描：** 本计划未使用“TODO / 待定 / 后续实现 / 类似任务 N”等占位符。
- **类型一致性：** 计划中统一使用 `tab-realtime` / `tab-history` / `tab-startup`，并与按钮 `switchMonitorTab(event, '<tab>')` 保持一致；所有字段 id 都与现有模板和 JS 读取逻辑一致。
