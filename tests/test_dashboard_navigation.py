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
