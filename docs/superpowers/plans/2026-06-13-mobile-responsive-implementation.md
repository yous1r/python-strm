# 移动端响应式体验优化 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为现有 FastAPI/Jinja 前端补齐移动端响应式基础，并专项优化资源图鉴、搜索、任务监控、频道监控、STRM、转存管道和 115 配置页。

**架构：** 保留现有 Jinja 模板和全局 CSS 架构，在 `base.html` 增加移动端导航壳，在 `style.css` 增加通用断点和可复用移动端工具类。重点页面只补充语义 class 和少量页面级 CSS，避免重写脚本和后端接口。

**技术栈：** FastAPI、Jinja2、原生 CSS、少量原生 JavaScript、pytest 内容结构测试。

---

## 文件结构

- 创建：`tests/test_mobile_responsive_ui.py`
  - 通过文件内容测试锁定移动端导航、全局断点和重点页面响应式 class，作为本次前端结构回归保护。
- 修改：`app/web/templates/base.html`
  - 增加移动端顶部栏、侧栏遮罩和导航打开/关闭 JS。
- 修改：`app/web/static/css/style.css`
  - 增加小屏断点、移动端抽屉导航、通用按钮/表单/网格/弹窗/Toast 规则和页面专项响应式规则。
- 修改：`app/web/templates/library.html`
  - 为顶部工具栏和图库容器增加 class，移动端搜索和操作区可换行。
- 修改：`app/web/templates/search.html`
  - 为搜索表单、过滤器、插件区、结果卡片链接行和转存弹窗增加 class，移动端链接行纵向展示。
- 修改：`app/web/templates/tasks.html`
  - 为任务总览头部、工具栏和表格外层增加 class，移动端收紧表格密度。
- 修改：`app/web/templates/monitor.html`
  - 为说明区、Tabs、标题操作区、开关行、保存区和 TMDB 弹窗控件增加 class。
- 修改：`app/web/templates/strm.html`
  - 为设置网格、扫描源按钮组、扫描源列表、历史弹窗表格增加 class。
- 修改：`app/web/templates/transfer.html`
  - 为转存卡片操作区、分类规则、覆盖输入区和回滚弹窗按钮区增加移动端 class。
- 修改：`app/web/templates/115.html`
  - 为 115 配置面板、select 行、按钮组和状态区增加移动端 class。

## 任务 1：全局移动端导航与响应式基础

**文件：**
- 创建：`tests/test_mobile_responsive_ui.py`
- 修改：`app/web/templates/base.html`
- 修改：`app/web/static/css/style.css`

- [ ] **步骤 1：编写失败的结构测试**

在 `tests/test_mobile_responsive_ui.py` 中新增：

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_base_template_defines_mobile_navigation_shell():
    html = read("app/web/templates/base.html")

    assert 'class="mobile-topbar"' in html
    assert 'id="mobileNavToggle"' in html
    assert 'class="sidebar-overlay"' in html
    assert "mobile-nav-open" in html
    assert "closeMobileNav" in html


def test_global_styles_define_mobile_breakpoint_and_drawer_nav():
    css = read("app/web/static/css/style.css")

    assert "@media (max-width: 768px)" in css
    assert ".mobile-topbar" in css
    assert "body.mobile-nav-open .sidebar" in css
    assert "body.mobile-nav-open .sidebar-overlay" in css
    assert ".main-content" in css
    assert "margin-left: 0" in css
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
PYTHONPATH=. uv run pytest -q tests/test_mobile_responsive_ui.py
```

预期：FAIL，失败原因为 `mobile-topbar`、`sidebar-overlay` 或 `@media (max-width: 768px)` 尚不存在。

- [ ] **步骤 3：实现移动端导航壳**

在 `app/web/templates/base.html` 的 `<body>` 内、`<aside class="sidebar">` 前增加：

```html
<div class="mobile-topbar">
    <button id="mobileNavToggle" class="mobile-nav-toggle" type="button" aria-label="打开导航菜单" aria-expanded="false">
        <i class="fa-solid fa-bars"></i>
    </button>
    <div class="mobile-brand">
        <i class="fa-solid fa-play"></i>
        <span>Python-STRM</span>
    </div>
    <button id="mobileThemeToggleBtn" class="mobile-theme-toggle" type="button" aria-label="切换主题">
        <i class="fa-solid fa-moon"></i>
    </button>
</div>
<div class="sidebar-overlay" id="sidebarOverlay"></div>
```

在主题脚本后增加导航控制：

```javascript
const mobileNavToggle = document.getElementById('mobileNavToggle');
const sidebarOverlay = document.getElementById('sidebarOverlay');
const mobileThemeToggleBtn = document.getElementById('mobileThemeToggleBtn');

function closeMobileNav() {
    document.body.classList.remove('mobile-nav-open');
    if (mobileNavToggle) mobileNavToggle.setAttribute('aria-expanded', 'false');
}

if (mobileNavToggle) {
    mobileNavToggle.addEventListener('click', () => {
        const isOpen = document.body.classList.toggle('mobile-nav-open');
        mobileNavToggle.setAttribute('aria-expanded', String(isOpen));
    });
}
if (sidebarOverlay) sidebarOverlay.addEventListener('click', closeMobileNav);
document.querySelectorAll('.sidebar .nav-link').forEach(link => {
    link.addEventListener('click', closeMobileNav);
});
if (mobileThemeToggleBtn && themeToggleBtn) {
    mobileThemeToggleBtn.addEventListener('click', () => themeToggleBtn.click());
}
```

- [ ] **步骤 4：实现全局响应式 CSS**

在 `app/web/static/css/style.css` 中补充：

```css
.mobile-topbar,
.sidebar-overlay {
    display: none;
}

.content-wrapper,
.glass-panel,
.grid-2,
.grid-3 {
    min-width: 0;
}

@media (max-width: 768px) {
    body {
        display: block;
        overflow-x: hidden;
        padding-top: 64px;
    }

    .mobile-topbar {
        position: fixed;
        inset: 0 0 auto 0;
        z-index: 250;
        height: 64px;
        display: grid;
        grid-template-columns: 44px minmax(0, 1fr) 44px;
        align-items: center;
        gap: 0.75rem;
        padding: 0.75rem 1rem;
        background: var(--bg-secondary);
        border-bottom: 1px solid var(--border);
    }

    .mobile-nav-toggle,
    .mobile-theme-toggle {
        width: 44px;
        height: 44px;
        border: 1px solid var(--border);
        border-radius: 0.75rem;
        background: rgba(255,255,255,0.05);
        color: var(--text-primary);
    }

    .mobile-brand {
        min-width: 0;
        display: flex;
        justify-content: center;
        align-items: center;
        gap: 0.5rem;
        font-weight: 700;
    }

    .sidebar {
        width: min(82vw, 280px);
        transform: translateX(-105%);
        transition: transform 0.25s ease;
        box-shadow: 18px 0 40px rgba(0,0,0,0.35);
    }

    body.mobile-nav-open .sidebar {
        transform: translateX(0);
    }

    .sidebar-overlay {
        position: fixed;
        inset: 0;
        z-index: 90;
        background: rgba(0,0,0,0.55);
    }

    body.mobile-nav-open .sidebar-overlay {
        display: block;
    }

    .main-content {
        margin-left: 0;
        width: 100%;
        max-width: none;
        padding: 1rem;
    }

    .page-header {
        margin-bottom: 1.25rem;
    }

    .page-title {
        font-size: 1.8rem;
    }

    .glass-panel {
        padding: 1rem;
        border-radius: 0.85rem;
    }

    .glass-panel:hover,
    .btn-primary:hover,
    .nav-link:hover {
        transform: none;
    }

    .grid-2,
    .grid-3 {
        grid-template-columns: 1fr;
        gap: 1rem;
    }

    .btn {
        width: 100%;
        min-height: 44px;
        padding: 0.75rem 1rem;
        white-space: normal;
    }

    .form-control {
        min-width: 0;
    }

    .custom-toast-container {
        left: 1rem;
        right: 1rem;
        bottom: max(1rem, env(safe-area-inset-bottom));
    }

    .custom-toast {
        min-width: 0;
        width: 100%;
    }

    .custom-modal-dialog {
        max-width: calc(100vw - 2rem);
        max-height: calc(100dvh - 2rem);
        overflow-y: auto;
    }
}
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```bash
PYTHONPATH=. uv run pytest -q tests/test_mobile_responsive_ui.py
```

预期：PASS。

- [ ] **步骤 6：提交任务 1**

运行：

```bash
git add tests/test_mobile_responsive_ui.py app/web/templates/base.html app/web/static/css/style.css
git commit -m "feat: add mobile responsive shell (task 1/3)"
```

## 任务 2：重点页面移动端布局收敛

**文件：**
- 修改：`tests/test_mobile_responsive_ui.py`
- 修改：`app/web/templates/library.html`
- 修改：`app/web/templates/search.html`
- 修改：`app/web/templates/tasks.html`
- 修改：`app/web/templates/monitor.html`
- 修改：`app/web/templates/strm.html`
- 修改：`app/web/templates/transfer.html`
- 修改：`app/web/templates/115.html`
- 修改：`app/web/static/css/style.css`

- [ ] **步骤 1：编写失败的页面结构测试**

在 `tests/test_mobile_responsive_ui.py` 中追加：

```python
def test_high_frequency_pages_expose_responsive_hooks():
    expected = {
        "app/web/templates/library.html": [
            "library-toolbar",
            "library-actions",
            "library-grid",
        ],
        "app/web/templates/search.html": [
            "search-form",
            "search-filters",
            "search-result-link",
            "transfer-modal-actions",
        ],
        "app/web/templates/tasks.html": [
            "tasks-header",
            "tasks-toolbar",
            "tasks-table-wrap",
        ],
        "app/web/templates/monitor.html": [
            "monitor-intro",
            "monitor-tabs",
            "monitor-section-heading",
            "monitor-savebar",
            "tmdb-modal-filters",
        ],
        "app/web/templates/strm.html": [
            "strm-settings-grid",
            "sync-dir-actions",
            "sync-history-table-wrap",
        ],
        "app/web/templates/transfer.html": [
            "transfer-card-actions",
            "rewrite-archive-controls",
            "rollback-modal-actions",
        ],
        "app/web/templates/115.html": [
            "cloud115-panel",
            "cloud115-select-row",
            "cloud115-actions",
        ],
    }

    for template, hooks in expected.items():
        html = read(template)
        for hook in hooks:
            assert hook in html, f"{template} missing {hook}"


def test_mobile_styles_cover_high_frequency_page_hooks():
    css = read("app/web/static/css/style.css")

    for selector in [
        ".library-toolbar",
        ".library-grid",
        ".search-result-link",
        ".tasks-table-wrap",
        ".monitor-tabs",
        ".strm-settings-grid",
        ".category-rule-row",
        ".cloud115-select-row",
    ]:
        assert selector in css
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
PYTHONPATH=. uv run pytest -q tests/test_mobile_responsive_ui.py
```

预期：FAIL，失败原因为页面 class hook 尚未添加。

- [ ] **步骤 3：为重点页面添加 class hook**

按以下方式修改模板：

```html
<!-- library.html -->
<div class="library-toolbar" ...>
<div class="library-actions" ...>
<div id="libraryContainer" class="library-grid" ...>

<!-- search.html -->
<form id="searchForm" class="search-form" ...>
<div class="search-filters" ...>
<div class="search-result-link" ...>
<div class="transfer-modal-actions" ...>

<!-- tasks.html -->
<div class="tasks-header" ...>
<div class="tasks-toolbar" ...>
<div class="tasks-table-wrap" style="overflow-x: auto;">

<!-- monitor.html -->
<div class="glass-panel monitor-intro" ...>
<div class="tabs monitor-tabs" ...>
<h2 class="monitor-section-heading" ...>
<div class="monitor-savebar" ...>
<div class="tmdb-modal-filters" ...>

<!-- strm.html -->
<div class="strm-settings-grid" ...>
<div class="sync-dir-actions" ...>
<div class="sync-history-table-wrap" ...>

<!-- transfer.html -->
<div class="transfer-card-actions" ...>
<div class="rewrite-archive-controls" ...>
<div class="rollback-modal-actions" ...>

<!-- 115.html -->
<div class="glass-panel cloud115-panel" ...>
<div class="cloud115-select-row" ...>
<div class="cloud115-actions" ...>
```

对于 JavaScript 生成的搜索结果链接行，生成字符串中使用 `class="search-result-link"`，并给转存按钮增加 `search-result-action`。

- [ ] **步骤 4：为重点页面添加移动端 CSS**

在 `style.css` 中追加页面专项规则：

```css
.library-toolbar,
.tasks-header,
.monitor-section-heading {
    min-width: 0;
}

.search-result-link {
    display: flex;
    align-items: center;
    gap: 0.75rem;
}

.sync-history-table-wrap,
.tasks-table-wrap {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
}

@media (max-width: 768px) {
    .library-toolbar,
    .tasks-header,
    .monitor-section-heading {
        flex-direction: column;
        align-items: stretch !important;
        gap: 1rem;
    }

    .library-actions,
    .tasks-toolbar,
    .sync-dir-actions,
    .transfer-card-actions,
    .rewrite-archive-controls,
    .cloud115-actions,
    .transfer-modal-actions,
    .rollback-modal-actions {
        width: 100%;
        display: flex;
        flex-wrap: wrap;
        gap: 0.75rem;
    }

    .library-actions > *,
    .sync-dir-actions > *,
    .transfer-card-actions > *,
    .rewrite-archive-controls > *,
    .cloud115-actions > * {
        flex: 1 1 100%;
        width: 100% !important;
        min-width: 0 !important;
    }

    .library-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        gap: 0.75rem !important;
    }

    .search-form {
        flex-direction: column;
    }

    .search-form > * {
        width: 100%;
        min-width: 0 !important;
    }

    .search-filters {
        gap: 0.75rem !important;
    }

    .search-result-link {
        flex-direction: column;
        align-items: stretch !important;
    }

    .search-result-link > span,
    .search-result-link > button {
        margin-left: 0 !important;
        margin-right: 0 !important;
        width: 100%;
    }

    .tasks-table-wrap table {
        min-width: 640px;
        font-size: 0.78rem;
    }

    .tasks-table-wrap th,
    .tasks-table-wrap td {
        padding: 0.45rem 0.5rem;
    }

    .monitor-tabs {
        overflow-x: auto;
        white-space: nowrap;
    }

    .monitor-tabs .tab-btn {
        flex: 0 0 auto;
    }

    .tmdb-modal-filters {
        flex-direction: column;
    }

    .tmdb-modal-filters > * {
        width: 100% !important;
    }

    .strm-settings-grid {
        grid-template-columns: 1fr !important;
    }

    .category-rule-row {
        grid-template-columns: 1fr !important;
    }

    .cloud115-select-row {
        flex-direction: column;
    }

    .cloud115-select-row > * {
        width: 100%;
    }
}

@media (max-width: 420px) {
    .library-grid {
        grid-template-columns: 1fr !important;
    }
}
```

- [ ] **步骤 5：运行测试验证通过**

运行：

```bash
PYTHONPATH=. uv run pytest -q tests/test_mobile_responsive_ui.py
```

预期：PASS。

- [ ] **步骤 6：提交任务 2**

运行：

```bash
git add tests/test_mobile_responsive_ui.py app/web/static/css/style.css app/web/templates/library.html app/web/templates/search.html app/web/templates/tasks.html app/web/templates/monitor.html app/web/templates/strm.html app/web/templates/transfer.html app/web/templates/115.html
git commit -m "feat: optimize key pages for mobile (task 2/3)"
```

## 任务 3：验证、修正和收尾

**文件：**
- 修改：按验证结果修正任务 1/2 中涉及的模板或 CSS。

- [ ] **步骤 1：运行移动端结构测试**

运行：

```bash
PYTHONPATH=. uv run pytest -q tests/test_mobile_responsive_ui.py
```

预期：PASS。

- [ ] **步骤 2：运行全量测试**

运行：

```bash
PYTHONPATH=. uv run pytest -q
```

预期：PASS。若出现与本次前端模板无关的失败，先和当前工作区基线对比，再决定是否修复或报告为既有问题。

- [ ] **步骤 3：启动本地服务做页面检查**

运行：

```bash
PYTHONPATH=. uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

检查：

1. `http://127.0.0.1:8000/`
2. `http://127.0.0.1:8000/library`
3. `http://127.0.0.1:8000/search`
4. `http://127.0.0.1:8000/tasks`
5. `http://127.0.0.1:8000/monitor`
6. `http://127.0.0.1:8000/strm`
7. `http://127.0.0.1:8000/transfer`
8. `http://127.0.0.1:8000/115`

375px 和 768px 宽度下确认：

1. 侧栏抽屉可打开和关闭。
2. 主内容不被固定侧栏挤压。
3. 高频页面按钮、输入框、弹窗不出现不可操作的横向溢出。
4. 表格横向滚动只限制在表格容器内部。

- [ ] **步骤 4：提交最终修正**

若步骤 2 或步骤 3 产生修正，运行：

```bash
git add app/web/static/css/style.css app/web/templates/base.html app/web/templates/library.html app/web/templates/search.html app/web/templates/tasks.html app/web/templates/monitor.html app/web/templates/strm.html app/web/templates/transfer.html app/web/templates/115.html tests/test_mobile_responsive_ui.py
git commit -m "fix: polish mobile responsive behavior (task 3/3)"
```

若无修正，不创建空提交。

## 执行注意

1. 当前主工作区有后台任务相关未提交改动。前端实现必须只编辑本计划列出的前端文件、测试文件和计划文件。
2. 曾尝试创建 `.worktrees/mobile-responsive`，但隔离 worktree 缺少当前主工作区的未提交后台任务修复，基线全量测试失败。因此本计划以当前主工作区为执行环境。
3. 每次提交必须明确指定文件，避免把后台任务改动混入前端提交。
4. `uv.lock` 存在，所有 pytest 命令使用 `PYTHONPATH=. uv run pytest ...`。
