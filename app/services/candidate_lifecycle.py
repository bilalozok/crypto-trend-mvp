"""Observed scan transitions, never inferred changes between scans."""

from datetime import datetime
from math import isfinite

from sqlalchemy import select

from app.db.models.binance_spot import BinanceSpotCandle
from app.db.models.candidate_scan import CandidateScan
from app.services.formations import BAR


def enrich(db, history):
    events = sorted(history["events"], key=lambda e: (e["created_at"], e["scan_id"]))
    scans = {
        r.id: r
        for r in db.scalars(
            select(CandidateScan).where(CandidateScan.id.in_([e["scan_id"] for e in events]))
        )
    }
    previous, previous_rule, last_qualified, rows = None, None, None, []
    for event in events:
        state, rule = event["state"], event["rule_hash"]
        if rule != previous_rule:
            previous, last_qualified = None, None
        previous_rule = rule
        close_time = scans[event["scan_id"]].payload.get("candle_close_time")
        price = None
        if close_time:
            stamp = int(
                datetime.fromisoformat(close_time.replace("Z", "+00:00")).timestamp() * 1000
            )
            candle = db.scalar(
                select(BinanceSpotCandle).where(
                    BinanceSpotCandle.symbol == history["symbol"],
                    BinanceSpotCandle.interval == "15m",
                    BinanceSpotCandle.open_time == stamp - BAR,
                )
            )
            if candle and isfinite(candle.close) and candle.close > 0:
                price = format(candle.close, ".15g")
        prior_qualified = last_qualified
        if state is None:
            change = "Evren dışında; karşılaştırılmadı"
        elif state["status"] != "ready":
            change = "Veri hazır değil; kayıp sayılmadı"
        else:
            qualified = state["qualified"]
            if previous is None:
                change = "İlk uygun gözlem: aday" if qualified else "İlk uygun gözlem: aday değil"
            elif qualified and not previous:
                change = "Adaylığa yeniden giriş gözlendi"
            elif not qualified and previous:
                change = "Aday koşullarının kaybı gözlendi"
            else:
                change = "Adaylık korunuyor" if qualified else "Aday koşulları yok"
            previous = qualified
            if qualified:
                last_qualified = event["created_at"]
        rows.append(
            dict(
                observed_at=event["created_at"],
                candle_close_time=close_time,
                rule_hash=rule,
                change=change,
                last_qualified_at=prior_qualified,
                close_price=price,
                note=event["note"],
            )
        )
    history["lifecycle"] = list(reversed(rows))
    history["lifecycle_note"] = (
        "Son 100 taramada gözlenen değişimler. Kayıp zamanı iki uygun gözlem arasında olabilir; "
        "kesin kırılım kaybı veya satış zamanı değildir. Kural değişince karşılaştırma sıfırlanır. "
        "Fiyat, tarama veri kapanışına eşleşen mevcut tarihsel mumdan okunur; "
        "eski taramada saklanmış fiyat değildir."
    )
    return history
