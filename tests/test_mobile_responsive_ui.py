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
