import pytest

from app.services.bullish_candidates import rank_match


def pattern(kind="double_bottom", direction="up", age=0, ratio=3, distance=1, holding=True):
    return dict(
        pattern=kind,
        status="confirmed",
        direction=direction,
        confirmation_age_bars=age,
        volume_ratio=ratio,
        distance_from_breakout_pct=distance,
        breakout_holding=holding,
    )


def row(patterns):
    return dict(symbol="BTCUSDT", quote_volume_24h=100, patterns=patterns)


def test_score_components_are_explicit():
    result, reason = rank_match(row([pattern()]))
    assert reason is None
    assert result["score_components"] == pytest.approx(
        dict(freshness=35, volume=35, breakout_proximity=20)
    )
    assert result["evidence_score"] == pytest.approx(90)


def test_multiple_correlated_structures_do_not_add_points():
    first = rank_match(row([pattern()]))[0]
    second = rank_match(row([pattern(), pattern("triple_bottom", age=1)]))[0]
    assert first["evidence_score"] == second["evidence_score"]
    assert second["primary_pattern"]["pattern"] == "double_bottom"


def test_conflict_excluded_or_explicitly_penalized():
    data = row([pattern(), pattern("double_top", direction="down", ratio=None)])
    assert rank_match(data) == (None, "opposing_confirmation")
    result, _ = rank_match(data, include_conflicting=True)
    assert result["conflict_penalty"] == 30
    assert result["evidence_score"] == pytest.approx(60)
    assert len(result["opposing_patterns"]) == 1


@pytest.mark.parametrize(
    "changes", [{"age": 5}, {"holding": False}, {"ratio": None}, {"ratio": 1.49}]
)
def test_old_unheld_or_unsupported_signal_is_not_candidate(changes):
    assert rank_match(row([pattern(**changes)]))[0] is None


def test_old_opposing_pattern_does_not_block_current_up():
    assert (
        rank_match(row([pattern(), pattern("double_top", direction="down", age=5)]))[0] is not None
    )


def test_farther_or_older_signal_has_lower_score():
    base = rank_match(row([pattern()]))[0]["evidence_score"]
    assert rank_match(row([pattern(distance=4)]))[0]["evidence_score"] < base
    assert rank_match(row([pattern(age=4)]))[0]["evidence_score"] < base
    assert rank_match(row([pattern(age=0)]), max_age=0)[0]["evidence_score"] == base


def test_endpoint_quality_and_parameters(client, monkeypatch):
    from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
    from app.db.session import SessionLocal
    from app.services.formations import BAR
    from tests.test_formation_freshness import structure

    monkeypatch.setattr("app.main.now_ms", lambda: 200 * BAR)
    rows = structure(ending=100)
    rows[-1].open = rows[-1].close = 102
    rows[-1].high, rows[-1].low, rows[-1].volume = 102.1, 101.9, 4
    with SessionLocal() as db:
        db.add(
            BinanceSpotSymbol(
                symbol="BTCUSDT",
                base_asset="BTC",
                active=True,
                quote_volume_24h=100,
                catalog_updated_ms=0,
            )
        )
        db.add_all([BinanceSpotCandle(symbol="BTCUSDT", interval="15m", **vars(r)) for r in rows])
        db.commit()
    path = "/analysis/binance/candidates"
    result = client.get(path).json()
    assert result["qualified_symbols"] == 1
    assert result["candidates"][0]["symbol"] == "BTCUSDT"
    assert result["candidates"][0]["primary_pattern"]["volume_ratio"] == 4
    assert result["next_offset"] is None
    assert client.get(path + "?min_volume_ratio=5").json()["qualified_symbols"] == 0
    for query in (
        "limit=101",
        "max_confirmation_age_bars=-1",
        "min_volume_ratio=nan",
        "include_conflicting=bad",
    ):
        assert client.get(path + "?" + query).status_code == 422
    monkeypatch.setattr("app.main.now_ms", lambda: 201 * BAR)
    result = client.get(path).json()
    assert result["candidates"] == []
    assert result["quality_counts"] == {"stale_data": 1}
