from types import SimpleNamespace

import pytest

from app.services.formations import BAR, timestamp
from app.services.signal_backtest import outcome, replay


def rows(count=208):
    return [
        SimpleNamespace(open_time=i * BAR, open=100, close=110, high=112, low=98, volume=1)
        for i in range(count)
    ]


def test_entry_is_next_open_and_costs_apply_both_sides():
    result, reason = outcome(rows(), 199, 8, 10, 5)
    assert reason is None
    assert result["entry_time"] == timestamp(200 * BAR)
    assert result["exit_time"] == timestamp(208 * BAR)
    assert result["gross_return_pct"] == pytest.approx(10)
    expected = (110 * 0.9995 * 0.999 / (100 * 1.0005 * 1.001) - 1) * 100
    assert result["net_return_pct"] == pytest.approx(expected)
    assert result["best_excursion_pct"] == pytest.approx(12)
    assert result["worst_excursion_pct"] == pytest.approx(-2)


def test_incomplete_future_is_pending_but_gaps_are_invalid():
    assert outcome(rows(207), 199, 8, 0, 0)[1] == "pending_forward_data"
    data = rows()
    data[202].open_time += BAR
    assert outcome(data, 199, 8, 0, 0)[1] == "invalid_forward_data"


def stub(window, symbol, stamp):
    index = window[-1].open_time // BAR
    p = dict(
        pattern="double_bottom",
        name="Çift dip",
        status="confirmed",
        direction="up",
        confirmation_age_bars=0,
        breakout_holding=True,
        volume_ratio=3,
        distance_from_breakout_pct=0.5,
        breakout_level=99,
        confirmed_at=timestamp(stamp if index == 199 else 200 * BAR),
    )
    return dict(status="ready", patterns=[p, dict(p, pattern="triple_bottom")])


def test_replay_never_passes_future_to_detector_and_deduplicates(monkeypatch):
    seen = []

    def spy(window, symbol, stamp):
        assert len(window) == 200
        assert all(r.open_time + BAR <= stamp for r in window)
        seen.append(stamp)
        return stub(window, symbol, stamp)

    monkeypatch.setattr("app.services.signal_backtest.analyze_rows", spy)
    result = replay(rows(), "BTCUSDT", 208 * BAR, fee_bps=0, slippage_bps=0)
    assert len(seen) == 9
    assert result["summary"]["completed_signals"] == 1
    assert result["signals"][0]["signal_time"] == timestamp(200 * BAR)
    assert result["summary"]["positive_net_rate_pct"] == 100


def test_future_price_changes_do_not_change_selected_signal(monkeypatch):
    monkeypatch.setattr("app.services.signal_backtest.analyze_rows", stub)
    original = replay(rows(), "BTCUSDT", 208 * BAR)
    changed = rows()
    changed[-1].close = 90
    changed[-1].low = 88
    result = replay(changed, "BTCUSDT", 208 * BAR)
    assert result["signals"][0]["signal_time"] == original["signals"][0]["signal_time"]
    assert result["signals"][0]["pattern"] == original["signals"][0]["pattern"]
    assert result["signals"][0]["net_return_pct"] < 0


def test_zero_completed_signals_has_no_fake_success_rate(monkeypatch):
    monkeypatch.setattr("app.services.signal_backtest.analyze_rows", stub)
    result = replay(rows(200), "BTCUSDT", 200 * BAR)
    assert result["summary"]["pending_signals"] == 1
    assert result["summary"]["positive_net_rate_pct"] is None
    assert result["summary"]["mean_net_return_pct"] is None


def test_real_detector_and_api_exclude_unclosed_data(client, monkeypatch):
    from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
    from app.db.session import SessionLocal
    from tests.test_formation_freshness import structure

    data = structure(ending=100)
    data[-1].open = data[-1].close = 102
    data[-1].high, data[-1].low, data[-1].volume = 102.1, 101.9, 4
    for i in range(200, 209):
        data.append(
            SimpleNamespace(open_time=i * BAR, open=102, high=105, low=101, close=104, volume=1)
        )
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
        db.add_all([BinanceSpotCandle(symbol="BTCUSDT", interval="15m", **vars(r)) for r in data])
        db.commit()
    monkeypatch.setattr("app.main.now_ms", lambda: 208 * BAR)
    path = "/analysis/binance/backtest?symbol=btcusdt"
    result = client.get(path).json()
    assert result["stored_candles_used"] == 208
    assert result["summary"]["completed_signals"] >= 1
    assert result["signals"][0]["entry_open"] == 102
    assert client.get("/analysis/binance/backtest?symbol=UNKNOWNUSDT").status_code == 404
    for q in ("history_limit=200", "horizon_bars=0", "fee_bps=nan", "slippage_bps=-1"):
        assert client.get(path + "&" + q).status_code == 422
