"""Experimental three-touch horizontal reversal structures."""

from app.services.formations import BAR, confirmation_volume, evaluate, timestamp

KINDS = {"triple_bottom", "triple_top"}


def detect_triple(rows, highs, lows, kind, name, tolerance, buffer):
    up = kind == "triple_bottom"
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
        "reason": "Üç benzer temas ve belirgin ara hareket bulunamadı.",
        "last_close": rows[-1].close,
        "last_candle_close_time": timestamp(rows[-1].open_time + BAR),
        "confirmation_age_bars": None,
        "distance_from_breakout_pct": None,
        "breakout_position": None,
        "breakout_holding": None,
        "confirmation_threshold": None,
        "pivot_points": [],
    }
    points = lows if up else highs
    candidate = None
    for a, b, c in zip(points, points[1:], points[2:], strict=False):
        first, second = b[0] - a[0], c[0] - b[0]
        if not (
            6 <= first <= 50
            and 6 <= second <= 50
            and 0.5 <= first / second <= 2
            and c[0] - a[0] <= 100
            and len(rows) - c[0] <= 40
            and max(a[1], b[1], c[1]) - min(a[1], b[1], c[1]) <= tolerance
        ):
            continue
        ranges = (range(a[0] + 1, b[0]), range(b[0] + 1, c[0]))
        if up:
            middle = [max(indices, key=lambda i: rows[i].high) for indices in ranges]
            prices = [rows[i].high for i in middle]
            if min(prices) - max(a[1], b[1], c[1]) < 2 * tolerance:
                continue
            level, floor = max(prices), min(a[1], b[1], c[1])
        else:
            middle = [min(indices, key=lambda i: rows[i].low) for indices in ranges]
            prices = [rows[i].low for i in middle]
            if min(a[1], b[1], c[1]) - max(prices) < 2 * tolerance:
                continue
            level, floor = min(prices), max(a[1], b[1], c[1])
        candidate = (a, b, c, middle, prices, level, floor)
    if candidate is not None:
        a, b, c, middle, prices, level, floor = candidate
        status, confirmed = evaluate(rows, c[0], up, level, floor, buffer)
        position = "within_buffer"
        if rows[-1].close > level + buffer:
            position = "above"
        elif rows[-1].close < level - buffer:
            position = "below"
        result.update(
            status=status,
            breakout_level=level,
            invalidation_level=floor,
            confirmed_at=confirmed,
            start_time=timestamp(rows[a[0]].open_time),
            anchor_time=timestamp(rows[c[0]].open_time),
            structure_available_at=timestamp(rows[c[0] + 3].open_time + BAR),
            confirmation_age_bars=(
                None
                if confirmed is None
                else (rows[-1].open_time + BAR - round(confirmed.timestamp() * 1000)) // BAR
            ),
            distance_from_breakout_pct=(rows[-1].close / level - 1) * 100,
            breakout_position=position,
            breakout_holding=position == ("above" if up else "below"),
            confirmation_threshold=level + buffer if up else level - buffer,
            reason={
                "forming": "Üç temas bulundu; kapanışla kırılım henüz teyit edilmedi.",
                "confirmed": "Her iki ara hareketin sınırı kapanışla ve teyit payıyla aşıldı.",
                "invalidated": "Üçlü yapının uç sınırı kapanışla aşıldı.",
            }[status],
            pivot_points=[
                {
                    "open_time": timestamp(rows[i].open_time),
                    "price": v,
                    "kind": "low" if up else "high",
                    "label": ("Dip " if up else "Tepe ") + str(n),
                }
                for n, (i, v) in enumerate((a, b, c), start=1)
            ]
            + [
                {
                    "open_time": timestamp(rows[i].open_time),
                    "price": v,
                    "kind": "high" if up else "low",
                    "label": "Ara dönüş",
                }
                for i, v in zip(middle, prices, strict=True)
            ],
        )
    result.update(confirmation_volume(rows, result["confirmed_at"]))
    return result
