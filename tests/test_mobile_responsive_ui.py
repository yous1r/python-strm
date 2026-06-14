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
        "app/web/templates/library_detail.html": [
            "transferDestinationModal",
            "loadTransferDestinations",
            "target_dir_id",
            "剧集资源类型",
            "目标网盘",
            "/api/v1/library/transfer_destinations",
        ],
        "app/web/templates/emby.html": [
            "emby-instances",
            "emby-instance-table",
            "emby-instance-card",
            "emby-instance-actions",
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
        ".transfer-destination-summary",
        ".emby-instances",
        ".emby-instance-card",
    ]:
        assert selector in css
