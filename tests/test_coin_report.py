import pytest

from app.services.coin_report import build_report


def pattern(direction="up", age=1, holding=True, ratio=2, status="confirmed"):
    return dict(
        pattern="test",
        name="Örnek",
        status=status,
        direction=direction,
        confirmation_age_bars=age,
        breakout_holding=holding,
        volume_ratio=ratio,
    )


def report(patterns, status="ready", **kwargs):
    return build_report(dict(symbol="BTCUSDT", status=status, patterns=patterns), **kwargs)


@pytest.mark.parametrize(
    "direction,assessment", [("up", "bullish_setup"), ("down", "bearish_setup")]
)
def test_current_direction_with_volume(direction, assessment):
    data = report([pattern(direction)])
    assert data["assessment"] == assessment
    assert data["counts"]["current_volume_supported"] == 1


def test_opposing_evidence_never_resolved_by_majority():
    assert report([pattern(), pattern(), pattern("down")])["assessment"] == "conflicting"


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"age": 5}, "confirmation_too_old_or_unknown"),
        ({"holding": False}, "breakout_not_holding"),
        ({"status": "invalidated"}, "not_confirmed"),
    ],
)
def test_old_or_failed_confirmation_cannot_drive_direction(changes, reason):
    data = report([pattern(**changes)])
    assert data["assessment"] == "waiting"
    assert reason in data["patterns"][0]["exclusion_reasons"]


def test_age_boundary_and_configurable_volume_threshold():
    p = pattern(age=4, ratio=1.5)
    assert report([p])["counts"]["current_volume_supported"] == 1
    assert report([p], max_age=3)["assessment"] == "waiting"
    assert report([p], min_volume_ratio=2)["counts"]["current_volume_supported"] == 0


def test_missing_volume_is_unknown_not_supported():
    data = report([pattern(ratio=None)])
    assert data["assessment"] == "bullish_setup"
    assert data["counts"]["current_volume_unknown"] == 1
    assert data["counts"]["current_volume_supported"] == 0


@pytest.mark.parametrize(
    "status", ["stale_data", "missing_data", "insufficient_data", "invalid_data"]
)
def test_unavailable_data_suppresses_all_evidence(status):
    data = report([pattern()], status)
    assert data["assessment"] == "unavailable"
    assert data["patterns"] == []
    assert data["counts"]["patterns_evaluated"] == 0


def test_undetected_filtered_without_mutating_input():
    p = pattern(status="not_detected")
    assert report([p])["patterns"] == []
    assert "current_confirmation" not in p


def test_endpoint_and_existing_analysis_agree(client, monkeypatch):
    from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
    from app.db.session import SessionLocal
    from app.services.formations import BAR
    from tests.test_formation_freshness import structure

    monkeypatch.setattr("app.main.now_ms", lambda: 200 * BAR)
    with SessionLocal() as db:
        db.add(
            BinanceSpotSymbol(
                symbol="BTCUSDT",
                base_asset="BTC",
                active=True,
                quote_volume_24h=1,
                catalog_updated_ms=0,
            )
        )
        db.add_all(
            [BinanceSpotCandle(symbol="BTCUSDT", interval="15m", **vars(r)) for r in structure()]
        )
        db.commit()
    response = client.get("/analysis/binance/report?symbol=btcusdt")
    assert response.status_code == 200
    data = response.json()
    original = client.get("/analysis/binance/formations?symbol=BTCUSDT").json()
    expected = build_report(original)
    for key in ("assessment", "counts", "patterns"):
        assert data[key] == expected[key]
    assert data["counts"]["patterns_evaluated"] == 20
    assert client.get("/analysis/binance/report?symbol=UNKNOWNUSDT").status_code == 404
    for query in (
        "max_confirmation_age_bars=-1",
        "max_confirmation_age_bars=201",
        "min_volume_ratio=nan",
        "min_volume_ratio=-1",
    ):
        assert client.get("/analysis/binance/report?symbol=BTCUSDT&" + query).status_code == 422
