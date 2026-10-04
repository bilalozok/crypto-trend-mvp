"""Experimental geometric rules, without calibrated probabilities."""

import math
from datetime import UTC, datetime

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol

BAR = 900_000
NAMES = {
    "double_bottom": "Çift dip",
    "double_top": "Çift tepe",
    "ascending_triangle": "Yükselen üçgen",
    "descending_triangle": "Alçalan üçgen",
}


def timestamp(ms):
    return datetime.fromtimestamp(ms / 1000, UTC)


def pivots(rows):
    highs, lows = [], []
    for i in range(3, len(rows) - 3):
        neighbours = rows[i - 3 : i] + rows[i + 1 : i + 4]
        if all(rows[i].high > r.high for r in neighbours):
            highs.append((i, rows[i].high))
        if all(rows[i].low < r.low for r in neighbours):
            lows.append((i, rows[i].low))
    return highs, lows


def evaluate(rows, anchor, up, level, invalidation, buffer):
    status, confirmed = "forming", None
    for row in rows[anchor + 1 :]:
        invalid = row.close < invalidation - buffer if up else (row.close > invalidation + buffer)
        crossed = row.close > level + buffer if up else row.close < level - buffer
        if invalid:
            status = "invalidated"
            break
        if crossed and confirmed is None:
            status, confirmed = "confirmed", timestamp(row.open_time + BAR)
    return status, confirmed


def detect(rows):
    highs, lows = pivots(rows)
    atr = (
        sum(
            max(r.high - r.low, abs(r.high - rows[i - 1].close), abs(r.low - rows[i - 1].close))
            for i, r in enumerate(rows[-14:], start=len(rows) - 14)
        )
        / 14
    )
    tolerance = max(rows[-1].close * 0.003, atr * 0.5)
    buffer = max(rows[-1].close * 0.001, atr * 0.2)
    results = []
    for kind, name in NAMES.items():
        up = kind in {"double_bottom", "ascending_triangle"}
        candidate = None
        if kind.startswith("double"):
            points = lows if up else highs
            for a, b in zip(points, points[1:], strict=False):
                if not 6 <= b[0] - a[0] <= 80 or len(rows) - b[0] > 40:
                    continue
                if abs(a[1] - b[1]) > tolerance:
                    continue
                middle = rows[a[0] + 1 : b[0]]
                level = max(r.high for r in middle) if up else min(r.low for r in middle)
                floor = min(a[1], b[1]) if up else max(a[1], b[1])
                if abs(level - floor) >= 2 * tolerance:
                    candidate = (a[0], b[0], level, floor)
        else:
            flat, slope = (highs[-3:], lows[-3:]) if up else (lows[-3:], highs[-3:])
            if len(flat) == len(slope) == 3:
                start = min(flat[0][0], slope[0][0])
                anchor = max(flat[-1][0], slope[-1][0])
                same = max(v for _, v in flat) - min(v for _, v in flat) <= tolerance
                advancing = all(
                    (b[1] - a[1] if up else a[1] - b[1]) >= atr * 0.2
                    for a, b in zip(slope, slope[1:], strict=False)
                )
                level, floor = sum(v for _, v in flat) / 3, slope[-1][1]
                interleaved = max(flat[0][0], slope[0][0]) < min(flat[-1][0], slope[-1][0])
                if (
                    same
                    and advancing
                    and interleaved
                    and 12 <= anchor - start <= 100
                    and len(rows) - anchor <= 40
                    and (level - floor if up else floor - level) > tolerance
                ):
                    candidate = (start, anchor, level, floor)
        result = {
            "pattern": kind,
            "name": name,
            "status": "not_detected",
            "direction": "up" if up else "down",
            "breakout_level": None,
            "invalidation_level": None,
            "confirmation_buffer": buffer,
            "confirmed_at": None,
            "start_time": None,
            "anchor_time": None,
            "reason": "Yakın tarihli yapı bu kuralları karşılamıyor.",
        }
        if candidate:
            start, anchor, level, floor = candidate
            status, confirmed = evaluate(rows, anchor, up, level, floor, buffer)
            reasons = {
                "forming": "Yapı bulundu; kapanışla kırılım henüz teyit edilmedi.",
                "confirmed": "Kapanış kırılım seviyesini teyit payıyla geçti.",
                "invalidated": "Kapanış yapının geçersizleşme sınırını geçti.",
            }
            result.update(
                status=status,
                breakout_level=level,
                invalidation_level=floor,
                confirmed_at=confirmed,
                reason=reasons[status],
                start_time=timestamp(rows[start].open_time),
                anchor_time=timestamp(rows[anchor].open_time),
            )
        results.append(result)
    return results


def analyze(db, symbol, stamp):
    active = db.get(BinanceSpotSymbol, symbol)
    if active is None or not active.active:
        return None
    rows = list(
        reversed(
            db.query(BinanceSpotCandle)
            .filter(
                BinanceSpotCandle.symbol == symbol,
                BinanceSpotCandle.open_time + BAR <= stamp,
            )
            .order_by(BinanceSpotCandle.open_time.desc())
            .limit(200)
            .all()
        )
    )
    status = "ready"
    if len(rows) < 200:
        status = "insufficient_data"
    elif any(
        r.open_time % BAR != 0
        or any(not math.isfinite(v) or v <= 0 for v in (r.open, r.high, r.low, r.close))
        or not math.isfinite(r.volume)
        or r.volume < 0
        or r.low > min(r.open, r.close)
        or r.high < max(r.open, r.close)
        for r in rows
    ):
        status = "invalid_data"
    elif any(b.open_time - a.open_time != BAR for a, b in zip(rows, rows[1:], strict=False)):
        status = "missing_data"
    elif rows[-1].open_time < (stamp // BAR) * BAR - BAR:
        status = "stale_data"
    return {
        "exchange": "binance",
        "market": "spot",
        "interval": "15m",
        "symbol": symbol,
        "method_version": "price_patterns_v1",
        "experimental": True,
        "as_of": timestamp(stamp),
        "status": status,
        "candles_used": len(rows),
        "candles_required": 200,
        "patterns": detect(rows) if status == "ready" else [],
    }
