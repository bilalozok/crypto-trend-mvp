import re

from app.db.session import SessionLocal
from app.services.formation_scan import scan
from app.services.formations import BAR, NAMES
from tests.test_formation_scan import fake_patterns, seed


def test_single_formation_filter_does_not_change_quality_or_universe(monkeypatch):
    monkeypatch.setattr("app.services.formations.detect", fake_patterns)
    with SessionLocal() as db:
        seed(db, "BTCUSDT")
        original = scan(db, 200 * BAR, state="all")
        selected = scan(db, 200 * BAR, state="all", pattern="double_top")
        assert selected["pattern"] == "double_top"
        assert selected["quality_counts"] == original["quality_counts"] == {"ready": 1}
        assert selected["total_eligible_symbols"] == original["total_eligible_symbols"] == 1
        assert [p["pattern"] for p in selected["matches"][0]["patterns"]] == ["double_top"]
        assert scan(db, 200 * BAR, state="confirmed", pattern="double_top")["matches"] == []
        assert scan(db, 200 * BAR, state="all", pattern="bull_flag")["matches"] == []
        assert len(original["matches"][0]["patterns"]) == 2


def test_pattern_filter_api_and_legacy_scan(client, monkeypatch):
    monkeypatch.setattr("app.main.now_ms", lambda: 200 * BAR)
    monkeypatch.setattr("app.services.formations.detect", fake_patterns)
    with SessionLocal() as db:
        seed(db, "BTCUSDT")
    route = "/analysis/binance/formations/scan"
    data = client.get(route + "?pattern=double_top&state=forming").json()
    assert data["matches"][0]["patterns"][0]["pattern"] == "double_top"
    assert client.get(route + "?pattern=unknown").status_code == 422
    assert client.get(route + "?state=all").json()["pattern"] is None
    assert len(client.get(route + "?state=all").json()["matches"][0]["patterns"]) == 2


def test_dashboard_tabs_pattern_names_and_notes(client):
    html = client.get("/analysis/binance/dashboard").text
    assert len(re.findall('role="tab" ', html)) == 7
    for name in (
        "coin",
        "formations",
        "candidates",
        "results",
        "reports",
        "purchases",
        "portfolio",
    ):
        assert 'id="tab-' + name + '"' in html
        assert 'aria-controls="panel-' + name + '"' in html
        assert 'id="panel-' + name + '"' in html
    assert 'id="pattern-select"' in html and 'id="pattern-form"' in html
    for key, name in NAMES.items():
        assert '"' + key + '": "' + name + '"' in html
    assert "Not / İzlenecek adım" in html
    assert "function patternNote" in html and "function outcomeNote" in html
