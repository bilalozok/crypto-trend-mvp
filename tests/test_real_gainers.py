from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.db.models.real_gainer import RealGainerStudy
from app.db.session import SessionLocal
from app.services import real_gainers as study

STAMP = int(datetime(2026, 10, 12, 6, tzinfo=UTC).timestamp() * 1000)


def symbol(db, name, base=None):
    db.add(
        BinanceSpotSymbol(
            symbol=name,
            base_asset=base or name[:-4],
            active=True,
            quote_volume_24h=1,
            catalog_updated_ms=STAMP,
        )
    )


def bars(db, name, begin, count=96, opened=10, closed=20, skip=None):
    for i in range(count):
        if i == skip:
            continue
        db.add(
            BinanceSpotCandle(
                symbol=name,
                interval="15m",
                open_time=begin + i * study.BAR,
                open=opened,
                close=closed,
                high=max(opened, closed) + 1,
                low=min(opened, closed) - 1,
                volume=1,
            )
        )


def test_tr_completed_days_and_ranking_excludes_gaps_stables_invalid_ohlc():
    days = study.periods(STAMP)
    assert len(days) == 7
    begin, end = days[-1]
    assert datetime.fromtimestamp(begin / 1000, UTC).isoformat() == "2026-10-10T21:00:00+00:00"
    assert end - begin == study.DAY
    with SessionLocal() as db:
        for name in ["AAAUSDT", "BBBUSDT", "GAPUSDT", "BADUSDT", "USDCUSDT", "DOWNUSDT"]:
            symbol(db, name)
        bars(db, "AAAUSDT", begin, closed=20)
        bars(db, "BBBUSDT", begin, closed=20)
        bars(db, "GAPUSDT", begin, closed=100, skip=20)
        bars(db, "BADUSDT", begin, closed=200)
        # Autoflush disabled: flush before inspecting the inserted primary key.
        db.flush()
        bad = db.get(BinanceSpotCandle, ("BADUSDT", begin))
        bad.low = 300
        bars(db, "USDCUSDT", begin, closed=1000)
        bars(db, "DOWNUSDT", begin, closed=9)
        db.flush()
        result = study.daily_ranking(db, begin, end)
        assert result["universe"] == 5 and result["covered"] == 3 and result["excluded"] == 2
        assert [w["symbol"] for w in result["winners"]] == ["AAAUSDT", "BBBUSDT"]
        assert result["winners"][0]["gain_pct"] == pytest.approx(100)


def test_pre_event_values_exclude_future_and_missing_horizons():
    begin, end = study.periods(STAMP)[-1]
    with SessionLocal() as db:
        symbol(db, "AAAUSDT")
        bars(db, "AAAUSDT", begin - 200 * study.BAR, count=200, opened=10, closed=10)
        bars(db, "AAAUSDT", begin, closed=20)
        bars(db, "AAAUSDT", end, count=1, closed=999)
        db.flush()
        result = study.analyze_day(db, begin, end)
        assert len(result["winners"]) == 1
        winner = result["winners"][0]
        assert winner["before"][0]["latest"]["close"] == 10
        assert winner["after"][0]["latest"]["close"] == 20
        assert winner["before"][0]["latest"]["at"] == "2026-10-10T21:00:00+00:00"
        assert all(h["status"] == "insufficient_data" for h in winner["before"][1:])


def test_snapshot_idempotency_revisions_and_archive_no_write(client, monkeypatch):
    monkeypatch.setattr("app.main.now_ms", lambda: STAMP)
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP)
    assert client.get("/real-gainers").status_code == 200
    assert client.get("/analysis/binance/real-gainers").json()["saved_days"] == 0
    assert client.get("/analysis/binance/real-gainers?days=100").status_code == 422
    assert (
        client.post("/account/real-gainers/refresh", headers={"X-CSRF-Token": "bad"}).status_code
        == 403
    )
    # Account POST remains CSRF protected; catalogue studies contain no private account data.
    assert (
        client.post(
            "/account/real-gainers/refresh", headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RealGainerStudy)) == 0
        symbol(db, "AAAUSDT")
        begin, end = study.periods(STAMP)[-1]
        bars(db, "AAAUSDT", begin, skip=30)
        db.commit()
    first = client.post("/account/real-gainers/refresh")
    assert first.status_code == 200, first.text
    assert first.json()["saved"] == 7
    assert client.post("/account/real-gainers/refresh").json()["saved"] == 0
    with SessionLocal() as db:
        original_ids = set(db.scalars(select(RealGainerStudy.id)).all())
        bars(db, "AAAUSDT", begin + 30 * study.BAR, count=1)
        db.commit()
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP + 1)
    new = client.post("/account/real-gainers/refresh").json()
    assert new["saved"] == 1
    assert new["days"][-1]["winners"][0]["symbol"] == "AAAUSDT"
    with SessionLocal() as db:
        assert original_ids < set(db.scalars(select(RealGainerStudy.id)).all())
    archived = client.get("/analysis/binance/real-gainers?days=28").json()
    assert len(archived["days"]) == 28 and archived["saved_days"] == 7
    assert sum(d.get("not_archived", False) for d in archived["days"]) == 21

    # Reverting historical evidence is a new revision, not a return to stale output.
    with SessionLocal() as db:
        restored = db.get(BinanceSpotCandle, ("AAAUSDT", begin + 30 * study.BAR))
        db.delete(restored)
        db.commit()
    monkeypatch.setattr("app.api.account.now_ms", lambda: STAMP + 2)
    restored = client.post("/account/real-gainers/refresh").json()
    assert restored["saved"] == 1 and restored["days"][-1]["winners"] == []


def test_common_counts_do_not_treat_missing_values_as_matches_and_pattern_absence():
    days = []
    for day in ["2026-10-10", "2026-10-11"]:
        winners = []
        for i in range(5):
            h = dict(
                interval="15m",
                status="ready",
                checks=[
                    dict(
                        name="MACD",
                        state="supports" if i < 3 else "unavailable" if i == 4 else "not_met",
                    )
                ],
                patterns=(
                    [dict(name="Test", direction="up", stage="forming")]
                    if i < 3 and day == "2026-10-10"
                    else []
                ),
            )
            winners.append(dict(symbol="COIN" + str(i), before=[h], after=[h]))
        days.append(dict(day=day, winners=winners))
    result = study.trends(days, "15m", "before")
    rows = {r["name"]: r for r in result["rows"]}
    indicator = rows["Gösterge · MACD"]
    assert indicator["supports"] == 6 and indicator["available"] == 8
    assert indicator["observations"] == 10 and indicator["unique_coins"] == 3
    assert indicator["share_pct"] == 75 and indicator["common_days"] == 2
    pattern = rows["Formasyon · Test · Oluşuyor"]
    assert pattern["available"] == 10 and pattern["supports"] == 3 and pattern["common_days"] == 1
    assert result["per_day"][1]["available"]["Formasyon · Test · Oluşuyor"] == 5
    assert result["per_day"][1]["supports"].get("Formasyon · Test · Oluşuyor", 0) == 0

    pair = result["pairs"][0]
    assert pair["supports"] == 3 and pair["available"] == 8
    assert pair["share_pct"] == 37.5 and pair["unique_coins"] == 3
    assert pair["common_days"] == 1
