from collections import Counter
from html.parser import HTMLParser


class Layout(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.stack = []
        self.parents = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        key = attrs.get("id")
        if key:
            self.ids.append(key)
            self.parents[key] = tuple(self.stack)
        if tag not in ("input", "meta", "br", "hr", "img", "link"):
            self.stack.append(key)

    def handle_endtag(self, tag):
        if self.stack:
            self.stack.pop()


def test_single_login_and_private_workspace_structure(client):
    html = client.get("/analysis/binance/dashboard").text
    layout = Layout()
    layout.feed(html)
    assert all(count == 1 for count in Counter(layout.ids).values())
    assert "account-center" in layout.parents["account-login"]
    assert "workspace-private" in layout.parents["account-login"]
    assert "panel-purchases" not in layout.parents["account-login"]
    for name in ("portfolio", "purchases", "candidates"):
        assert "private-workspace-content" in layout.parents["panel-" + name]
    for name in ("coin", "formations", "results", "reports"):
        assert "workspace-public" in layout.parents["panel-" + name]
    for name in ("scan", "results", "history"):
        assert "panel-candidates" in layout.parents["candidate-pane-" + name]
    assert "candidate-pane-results" in layout.parents["candidate-study-table"]
    assert "candidate-pane-history" in layout.parents["candidate-lifecycle-table"]
    assert 'id="private-workspace-content" hidden' in html

    assert "workspace-admin" in layout.parents["admin-area"]
    assert "workspace-private" not in layout.parents["admin-area"]
    assert 'id="workspace-tab-admin"' in html
    assert 'id="account-center" class="account-center" hidden' in html


def test_formation_tools_are_separate_accessible_panes(client):
    html = client.get("/analysis/binance/dashboard").text
    layout = Layout()
    layout.feed(html)
    assert all(count == 1 for count in Counter(layout.ids).values())
    for key in ("early", "scan", "measure", "history", "health"):
        assert "panel-formations" in layout.parents["formation-pane-" + key]
        assert 'aria-controls="formation-pane-' + key + '"' in html
    for element, key in [
        ("early-table", "early"),
        ("pattern-form", "scan"),
        ("early-study-charts", "measure"),
        ("early-search-form", "history"),
        ("early-health-cards", "health"),
    ]:
        assert "formation-pane-" + key in layout.parents[element]
    assert (
        'id="formation-pane-early" role="tabpanel" aria-labelledby="formation-tab-early">' in html
    )
    for key in ("scan", "measure", "history", "health"):
        assert (
            'id="formation-pane-'
            + key
            + '" role="tabpanel" aria-labelledby="formation-tab-'
            + key
            + '" hidden'
            in html
        )


def test_task_views_cover_all_workspaces_without_duplicate_controls(client):
    html = client.get("/analysis/binance/dashboard").text
    layout = Layout()
    layout.feed(html)
    assert all(count == 1 for count in Counter(layout.ids).values())
    expected = {
        "indicator-summary": ("coin", "indicators"),
        "history-table": ("coin", "history"),
        "forward-patterns": ("market-results", "patterns"),
        "saved-table": ("reports", "list"),
        "saved-view": ("reports", "detail"),
        "dashboard-alerts": ("overview", "alerts"),
        "dashboard-return-table": ("overview", "returns"),
        "dashboard-matrix": ("overview", "portfolio"),
        "portfolio-table": ("portfolio", "list"),
        "portfolio-levels": ("portfolio", "detail"),
        "candidate-archive-list": ("candidate-scan", "archive"),
        "candidate-fresh-table": ("candidate-scan", "fresh"),
        "candidate-tracking-table": ("candidate-results", "tracking"),
        "candidate-outcomes-table": ("candidate-results", "outcomes"),
        "purchase-table": ("purchases", "records"),
        "admin-create-form": ("admin", "create"),
        "admin-users-table": ("admin", "users"),
    }
    for element, (group, key) in expected.items():
        assert "view-" + group + "-pane-" + key in layout.parents[element]
        assert 'aria-controls="view-' + group + "-pane-" + key + '"' in html


def test_reference_shell_and_theme_control_preserve_navigation(client):
    html = client.get("/analysis/binance/dashboard").text
    layout = Layout()
    layout.feed(html)
    assert all(count == 1 for count in Counter(layout.ids).values())
    assert "rail-public" in layout.parents["public-tabs"]
    assert "rail-private" in layout.parents["private-tabs"]
    assert 'id="theme-toggle"' in html
    assert 'aria-label="Ana gezinme"' in html
    assert "crypto-trend-theme" in html
