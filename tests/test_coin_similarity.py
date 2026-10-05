import math

import pytest

from app.services.coin_similarity import components, correlation, overlap


def test_return_correlation_scale_and_opposite_direction():
    a = [0.01, -0.02, 0.03, -0.01]
    assert correlation(a, [v * 3 for v in a]) == pytest.approx(1)
    assert correlation(a, [-v for v in a]) == pytest.approx(-1)
    assert correlation(a, [0] * 4) is None
    assert correlation(a, a[:2]) is None


def test_empty_features_never_count_as_matching_evidence():
    assert overlap(set(), set()) is None
    assert overlap({("p", "forming", "up")}, set()) == 0
    result = components(0.8, set(), set(), set(), set())
    assert result["similarity_score"] == pytest.approx(80)
    assert result["weights_used"] == {"price_correlation": 1}


def test_shared_patterns_and_transitions_are_explained():
    p = {("double_bottom", "confirmed", "up")}
    change = {("double_bottom", "forming", "confirmed", "up")}
    result = components(0.5, p, p, change, change)
    assert result["similarity_score"] == pytest.approx(65)
    assert result["shared_formations"][0]["pattern"] == "double_bottom"
    assert result["shared_changes"][0]["current_status"] == "confirmed"


def seed(symbol, scale=1, opposite=False, flat=False, bars=200, volume=1):
    from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
    from app.db.session import SessionLocal
    from app.services.formations import BAR

    with SessionLocal() as db:
        db.add(
            BinanceSpotSymbol(
                symbol=symbol,
                base_asset=symbol.removesuffix("USDT"),
                active=True,
                quote_volume_24h=volume,
                catalog_updated_ms=0,
            )
        )
        rows = []
        price = 100 * scale
        for i in range(bars):
            move = 0 if flat else math.sin(i * 0.7) * 0.005
            price *= math.exp(-move if opposite else move)
            rows.append(
                BinanceSpotCandle(
                    symbol=symbol,
                    interval="15m",
                    open_time=i * BAR,
                    open=price,
                    close=price,
                    high=price * 1.001,
                    low=price * 0.999,
                    volume=10,
                )
            )
        db.add_all(rows)
        db.commit()


def test_endpoint_aligned_ranking_filtering_and_pagination(client, monkeypatch):
    from app.services.formations import BAR

    monkeypatch.setattr("app.main.now_ms", lambda: 200 * BAR)
    seed("BTCUSDT")
    seed("ETHUSDT", scale=2, volume=10)
    seed("SOLUSDT", opposite=True, volume=9)
    seed("FLATUSDT", flat=True, volume=8)
    seed("STALEUSDT", bars=199, volume=7)
    seed("USDCUSDT", volume=100)
    url = "/analysis/binance/similar?symbol=btcusdt&candidate_limit=2"
    result = client.get(url).json()
    assert result["status"] == "ready"
    assert result["total_eligible_symbols"] == 4
    assert result["scanned_symbols"] == 2
    assert result["next_offset"] == 2
    assert [r["symbol"] for r in result["matches"]] == ["ETHUSDT"]
    assert result["matches"][0]["return_correlation"] == pytest.approx(1)
    second = client.get(url + "&offset=2").json()
    assert second["next_offset"] is None
    assert second["matches"] == []
    assert second["quality_counts"] == {"no_variation": 1, "insufficient_data": 1}
    assert client.get("/analysis/binance/similar?symbol=UNKNOWNUSDT").status_code == 404
    for q in ("lookback_bars=200", "candidate_limit=101", "offset=-1", "min_correlation=nan"):
        assert client.get(url + "&" + q).status_code == 422


def test_reference_quality_and_zero_variation_block_ranking(client, monkeypatch):
    from app.services.formations import BAR

    monkeypatch.setattr("app.main.now_ms", lambda: 200 * BAR)
    seed("BTCUSDT", flat=True)
    assert (
        client.get("/analysis/binance/similar?symbol=BTCUSDT").json()["status"]
        == "reference_no_variation"
    )
    monkeypatch.setattr("app.main.now_ms", lambda: 201 * BAR)
    result = client.get("/analysis/binance/similar?symbol=BTCUSDT").json()
    assert result["status"] == "stale_data"
    assert result["matches"] == []
