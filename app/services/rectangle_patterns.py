"""Experimental horizontal continuation rectangles."""

from app.services.formations import BAR, confirmation_volume, evaluate, timestamp

KINDS = {"bull_rectangle", "bear_rectangle"}


def detect_rectangle(rows, highs, lows, kind, name, tolerance, buffer):
    up = kind == "bull_rectangle"
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
        "reason": "Yatay kanal ve aynı yönde ön hareket bulunamadı.",
        "last_close": rows[-1].close,
        "last_candle_close_time": timestamp(rows[-1].open_time + BAR),
        "confirmation_age_bars": None,
        "distance_from_breakout_pct": None,
        "breakout_position": None,
        "breakout_holding": None,
        "confirmation_threshold": None,
        "pivot_points": [],
    }
    candidate = None
    touches = sorted([(i, v, "high") for i, v in highs] + [(i, v, "low") for i, v in lows])
    for end in range(6, len(touches) + 1):
        selected = touches[end - 6 : end]
        if len(selected) != 6:
            continue
        indices = [p[0] for p in selected]
        start, anchor = indices[0], indices[-1]
        upper = [v for _, v, k in selected if k == "high"]
        lower = [v for _, v, k in selected if k == "low"]
        if not (
            len(upper) == len(lower) == 3
            and all(x[2] != y[2] for x, y in zip(selected, selected[1:], strict=False))
            and all(3 <= y - x <= 25 for x, y in zip(indices, indices[1:], strict=False))
            and 15 <= anchor - start <= 100
            and len(rows) - anchor <= 40
            and start >= 12
            and max(upper) - min(upper) <= tolerance
            and max(lower) - min(lower) <= tolerance
        ):
            continue
        ceiling, support = max(upper), min(lower)
        if min(upper) - max(lower) < 4 * tolerance:
            continue
        # Require a preceding move in the continuation direction.
        move = rows[start].close - rows[start - 12].close
        if (move if up else -move) < ceiling - support:
            continue
        if any(
            r.high > ceiling + tolerance or r.low < support - tolerance
            for r in rows[start : anchor + 1]
        ):
            continue
        level, floor = (ceiling, support) if up else (support, ceiling)
        candidate = (start, anchor, selected, level, floor)
    if candidate is not None:
        start, anchor, selected, level, floor = candidate
        status, confirmed = evaluate(rows, anchor, up, level, floor, buffer)
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
            start_time=timestamp(rows[start].open_time),
            anchor_time=timestamp(rows[anchor].open_time),
            structure_available_at=timestamp(rows[anchor + 3].open_time + BAR),
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
                "forming": "Yatay kanal bulundu; kapanışla kırılım bekleniyor.",
                "confirmed": "Dikdörtgen sınırı kapanışla ve teyit payıyla aşıldı.",
                "invalidated": "Karşı kanal sınırı kapanışla aşıldı.",
            }[status],
            pivot_points=[
                {
                    "open_time": timestamp(rows[i].open_time),
                    "price": v,
                    "kind": k,
                    "label": "Direnç" if k == "high" else "Destek",
                }
                for i, v, k in selected
            ],
        )
    result.update(confirmation_volume(rows, result["confirmed_at"]))
    return result
