from types import SimpleNamespace

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.db.session import SessionLocal
from app.services.early_data_health import data_health, measurement_health
from app.services.formations import BAR


def test_measurement_boundaries_and_invalid_are_separate():
    record = SimpleNamespace(
        entry_ms=0, symbol="A", snapshot={"name": "Dip"}, rule_hash="r", outcomes={}
    )
    assert measurement_health([record], 4 * BAR - 1)["waiting"] == 4
    assert measurement_health([record], 4 * BAR)["awaiting_collection"] == 1
    assert measurement_health([record], 8 * BAR)["overdue"] == 1
    record.outcomes = {"1": {"status": "invalid_data"}, "2": {"status": "complete"}}
    result = measurement_health([record], 8 * BAR)
    assert result["invalid"] == 1 and result["complete"] == 1 and result["overdue"] == 0


def test_last_closed_candle_health_ignores_open_bar_and_is_read_only():
    stamp = 20 * BAR
    with SessionLocal() as db:
        for symbol in ["A", "B", "C"]:
            db.add(
                BinanceSpotSymbol(
                    symbol=symbol,
                    base_asset=symbol,
                    quote_volume_24h=1,
                    active=True,
                    catalog_updated_ms=stamp,
                    last_success_ms=stamp - 1,
                )
            )
        for symbol, opened in [("A", stamp - BAR), ("A", stamp), ("B", stamp - 2 * BAR)]:
            db.add(
                BinanceSpotCandle(
                    symbol=symbol, open_time=opened, open=1, high=1, low=1, close=1, volume=1
                )
            )
        db.commit()
        result = data_health(db, stamp, 7)
        assert (result["current"], result["stale"], result["missing"]) == (1, 1, 1)
        assert result["measurement_records"] == 0
        assert not db.dirty and not db.new and not db.deleted
