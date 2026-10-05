from app.db.session import SessionLocal
from app.services.binance_coverage import coverage
from app.services.formation_scan import scan
from app.services.formations import BAR
from tests.test_formation_scan import seed


def test_usd1_marked_and_excluded_from_default_scan():
    with SessionLocal() as db:
        seed(db, "USD1USDT", base="USD1", volume=100, count=0)
        seed(db, "BTCUSDT", base="BTC", volume=50, count=0)
        result = coverage(db, limit=10, offset=0, required=200, stamp=200 * BAR)
        flags = {row["symbol"]: row["stablecoin_candidate"] for row in result["symbols"]}
        assert flags == {"USD1USDT": True, "BTCUSDT": False}
        filtered = scan(db, 200 * BAR)
        assert filtered["total_eligible_symbols"] == 1
        assert filtered["scanned_symbols"] == 1
        included = scan(db, 200 * BAR, include_stablecoins=True)
        assert included["total_eligible_symbols"] == 2
        assert included["scanned_symbols"] == 2
