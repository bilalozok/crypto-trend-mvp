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
    "head_and_shoulders": "Omuz-baş-omuz",
    "inverse_head_and_shoulders": "Ters omuz-baş-omuz",
    "symmetrical_triangle": "Simetrik üçgen",
    "rising_wedge": "Yükselen takoz",
    "falling_wedge": "Alçalan takoz",
    "bull_flag": "Boğa bayrağı",
    "bear_flag": "Ayı bayrağı",
    "bull_pennant": "Boğa flaması",
    "bear_pennant": "Ayı flaması",
    "triple_bottom": "Üçlü dip",
    "triple_top": "Üçlü tepe",
    "bull_rectangle": "Yükseliş dikdörtgeni",
    "bear_rectangle": "Düşüş dikdörtgeni",
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
    # The final pivot is observable only after three right-hand candles close.
    for row in rows[anchor + 3 :]:
        invalid = row.close < invalidation - buffer if up else (row.close > invalidation + buffer)
        crossed = row.close > level + buffer if up else row.close < level - buffer
        if invalid:
            status = "invalidated"
            break
        if crossed and confirmed is None:
            status, confirmed = "confirmed", timestamp(row.open_time + BAR)
    return status, confirmed


def confirmation_volume(rows, confirmed):
    result = {
        "confirmation_volume": None,
        "prior_volume_average": None,
        "volume_ratio": None,
        "volume_reference_bars": 20,
        "volume_supported": None,
        "volume_support_threshold": 1.5,
    }
    if confirmed is None:
        return result
    opened = round(confirmed.timestamp() * 1000) - BAR
    index = next((i for i, r in enumerate(rows) if r.open_time == opened), None)
    if index is None:
        return result
    result["confirmation_volume"] = rows[index].volume
    if index < 20:
        return result
    average = sum(r.volume for r in rows[index - 20 : index]) / 20
    result["prior_volume_average"] = average
    if average > 0:
        ratio = rows[index].volume / average
        if math.isfinite(ratio):
            result["volume_ratio"] = ratio
            result["volume_supported"] = ratio >= 1.5
    return result


def detect(rows):
    from app.services.flag_patterns import KINDS as FLAG_KINDS
    from app.services.flag_patterns import detect_flag
    from app.services.pennant_patterns import KINDS as PENNANT_KINDS
    from app.services.pennant_patterns import detect_pennant
    from app.services.rectangle_patterns import KINDS as RECTANGLE_KINDS
    from app.services.rectangle_patterns import detect_rectangle
    from app.services.sloped_patterns import KINDS, detect_sloped
    from app.services.triple_patterns import KINDS as TRIPLE_KINDS
    from app.services.triple_patterns import detect_triple

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
        if kind in RECTANGLE_KINDS:
            results.append(detect_rectangle(rows, highs, lows, kind, name, tolerance, buffer))
            continue
        if kind in TRIPLE_KINDS:
            results.append(detect_triple(rows, highs, lows, kind, name, tolerance, buffer))
            continue
        if kind in PENNANT_KINDS:
            results.append(detect_pennant(rows, highs, lows, kind, name, atr, tolerance, buffer))
            continue
        if kind in FLAG_KINDS:
            results.append(detect_flag(rows, highs, lows, kind, name, atr, tolerance, buffer))
            continue
        if kind in KINDS:
            results.append(detect_sloped(rows, highs, lows, kind, name, atr, tolerance, buffer))
            continue
        up = kind in {"double_bottom", "ascending_triangle", "inverse_head_and_shoulders"}
        candidate = None
        selected_points = []
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
                    selected_points = [
                        {
                            "open_time": timestamp(rows[index].open_time),
                            "price": value,
                            "kind": "low" if up else "high",
                        }
                        for index, value in (a, b)
                    ]
        elif kind.endswith("triangle"):
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
                    selected_points = [
                        {
                            "open_time": timestamp(rows[index].open_time),
                            "price": value,
                            "kind": label,
                        }
                        for points, label in (
                            (flat, "high" if up else "low"),
                            (slope, "low" if up else "high"),
                        )
                        for index, value in points
                    ]
        else:
            # This version deliberately requires an approximately horizontal neckline.
            points = lows if up else highs
            for a, head, b in zip(points, points[1:], points[2:], strict=False):
                left_span, right_span = head[0] - a[0], b[0] - head[0]
                if (
                    not 6 <= left_span <= 60
                    or not 6 <= right_span <= 60
                    or not 0.5 <= left_span / right_span <= 2
                    or b[0] - a[0] > 100
                    or len(rows) - b[0] > 40
                    or abs(a[1] - b[1]) > tolerance
                ):
                    continue
                prominence = min(a[1], b[1]) - head[1] if up else (head[1] - max(a[1], b[1]))
                if prominence < 2 * tolerance:
                    continue
                if up:
                    neck1 = max(range(a[0] + 1, head[0]), key=lambda i: rows[i].high)
                    neck2 = max(range(head[0] + 1, b[0]), key=lambda i: rows[i].high)
                    v1, v2 = rows[neck1].high, rows[neck2].high
                else:
                    neck1 = min(range(a[0] + 1, head[0]), key=lambda i: rows[i].low)
                    neck2 = min(range(head[0] + 1, b[0]), key=lambda i: rows[i].low)
                    v1, v2 = rows[neck1].low, rows[neck2].low
                if abs(v1 - v2) > tolerance:
                    continue
                level = (v1 + v2) / 2
                shoulder_depth = level - max(a[1], b[1]) if up else (min(a[1], b[1]) - level)
                if shoulder_depth < 2 * tolerance:
                    continue
                candidate = (a[0], b[0], level, b[1])
                selected_points = [
                    {
                        "open_time": timestamp(rows[index].open_time),
                        "price": price,
                        "kind": "low" if up else "high",
                        "label": label,
                    }
                    for (index, price), label in ((a, "Sol omuz"), (head, "Baş"), (b, "Sağ omuz"))
                ] + [
                    {
                        "open_time": timestamp(rows[index].open_time),
                        "price": price,
                        "kind": "high" if up else "low",
                        "label": "Boyun",
                    }
                    for index, price in ((neck1, v1), (neck2, v2))
                ]
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
            "structure_available_at": None,
            "reason": "Yakın tarihli yapı bu kuralları karşılamıyor.",
            "last_close": rows[-1].close,
            "last_candle_close_time": timestamp(rows[-1].open_time + BAR),
            "confirmation_age_bars": None,
            "distance_from_breakout_pct": None,
            "breakout_position": None,
            "breakout_holding": None,
            "confirmation_threshold": None,
            "pivot_points": selected_points,
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
                structure_available_at=timestamp(rows[anchor + 3].open_time + BAR),
            )
        if candidate:
            threshold = level + buffer if up else level - buffer
            position = "within_buffer"
            if rows[-1].close > level + buffer:
                position = "above"
            elif rows[-1].close < level - buffer:
                position = "below"
            age = None
            if confirmed is not None:
                age = (rows[-1].open_time + BAR - round(confirmed.timestamp() * 1000)) // BAR
            result.update(
                confirmation_age_bars=age,
                distance_from_breakout_pct=(rows[-1].close / level - 1) * 100,
                breakout_position=position,
                breakout_holding=(position == ("above" if up else "below")),
                confirmation_threshold=threshold,
            )
        result.update(confirmation_volume(rows, result["confirmed_at"]))
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
    return analyze_rows(rows, symbol, stamp)


def analyze_rows(rows, symbol, stamp):
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
