"""Experimental countertrend flags with an explicit preceding pole."""

from app.services.formations import BAR, confirmation_volume, timestamp
from app.services.sloped_patterns import fit

KINDS = {"bull_flag", "bear_flag"}


def detect_flag(rows, highs, lows, kind, name, atr, tolerance, buffer):
    up = kind == "bull_flag"
    direction = "up" if up else "down"
    result = {
        "pattern": kind,
        "name": name,
        "status": "not_detected",
        "direction": direction,
        "breakout_level": None,
        "invalidation_level": None,
        "confirmation_buffer": buffer,
        "confirmed_at": None,
        "start_time": None,
        "anchor_time": None,
        "structure_available_at": None,
        "reason": "Direk ve karşı yönlü bayrak kanalı bulunamadı.",
        "last_close": rows[-1].close,
        "last_candle_close_time": timestamp(rows[-1].open_time + BAR),
        "confirmation_age_bars": None,
        "distance_from_breakout_pct": None,
        "breakout_position": None,
        "breakout_holding": None,
        "confirmation_threshold": None,
        "pivot_points": [],
        "boundary_lines": [],
        "pole_points": [],
        "pole_move_pct": None,
        "flag_retracement_ratio": None,
    }

    def finish():
        result.update(confirmation_volume(rows, result["confirmed_at"]))
        return result

    hp, lp = highs[-3:], lows[-3:]
    if len(hp) != 3 or len(lp) != 3:
        return finish()
    start, anchor = min(hp[0][0], lp[0][0]), max(hp[-1][0], lp[-1][0])
    if not (
        start >= 30
        and 12 <= anchor - start <= 48
        and len(rows) - anchor <= 30
        and max(hp[0][0], lp[0][0]) < min(hp[-1][0], lp[-1][0])
        and (hp[0][0] < lp[0][0] if up else lp[0][0] < hp[0][0])
    ):
        return finish()
    uf, lf = fit(hp), fit(lp)
    if uf is None or lf is None:
        return finish()
    us, ub, ue = uf
    ls, lb, le = lf
    span = anchor - start
    if (
        max(ue, le) > tolerance
        or (not us < 0 or not ls < 0 if up else not us > 0 or not ls > 0)
        or abs(us - ls) * span > tolerance
        or min(abs(us), abs(ls)) * span < tolerance
    ):
        return finish()

    def upper(i):
        return ub + us * i

    def lower(i):
        return lb + ls * i

    width = upper(start) - lower(start)
    final_width = upper(anchor) - lower(anchor)
    if not (width > 2 * tolerance and 0.8 * width <= final_width <= 1.2 * width):
        return finish()
    if any(
        r.high > upper(i) + tolerance or r.low < lower(i) - tolerance
        for i, r in enumerate(rows[start : anchor + 1], start=start)
    ):
        return finish()
    reference = range(start - 30, start - 7)
    pole_index = (
        min(reference, key=lambda i: rows[i].close)
        if up
        else (max(reference, key=lambda i: rows[i].close))
    )
    pole_start = rows[pole_index].close
    pole_end = rows[start].high if up else rows[start].low
    move = pole_end - pole_start if up else pole_start - pole_end
    retreat = (
        pole_end - min(r.low for r in rows[start : anchor + 1])
        if up
        else (max(r.high for r in rows[start : anchor + 1]) - pole_end)
    )
    if move < max(3 * atr, 3 * width) or not 0 <= retreat <= 0.6 * move:
        return finish()
    result.update(
        status="forming",
        reason="Belirgin direk ve sınırlı geri çekilmeli paralel kanal bulundu.",
        start_time=timestamp(rows[start].open_time),
        anchor_time=timestamp(rows[anchor].open_time),
        structure_available_at=timestamp(rows[anchor + 3].open_time + BAR),
        pole_move_pct=(pole_end / pole_start - 1) * 100,
        flag_retracement_ratio=retreat / move,
        pole_points=[
            {
                "open_time": timestamp(rows[pole_index].open_time),
                "price": pole_start,
                "label": "Direk başlangıcı",
            },
            {
                "open_time": timestamp(rows[start].open_time),
                "price": pole_end,
                "label": "Direk sonu",
            },
        ],
        pivot_points=[
            {"open_time": timestamp(rows[i].open_time), "price": v, "kind": label}
            for points, label in ((hp, "high"), (lp, "low"))
            for i, v in points
        ],
    )
    confirmed_index, level, opposite = None, None, None
    for i in range(anchor + 3, len(rows)):
        close = rows[i].close
        if confirmed_index is None:
            if i - start > 60 or upper(i) <= lower(i):
                result.update(
                    status="invalidated",
                    reason="Teyit olmadan bayrak süresi veya kanal geçerliliği doldu.",
                )
                break
            invalid = close < lower(i) - buffer if up else close > upper(i) + buffer
            crossed = close > upper(i) + buffer if up else close < lower(i) - buffer
            if invalid:
                result.update(
                    status="invalidated", reason="Bayrağın karşı sınırı kapanışla aşıldı."
                )
                break
            if not crossed:
                continue
            confirmed_index = i
            level, opposite = (upper(i), lower(i)) if up else (lower(i), upper(i))
            result.update(
                status="confirmed",
                confirmed_at=timestamp(rows[i].open_time + BAR),
                reason="Direk yönünde bayrak kırılımı kapanışla teyit edildi.",
            )
        elif close < opposite - buffer if up else close > opposite + buffer:
            result.update(
                status="invalidated", reason="Teyitte sabitlenen karşı sınır kapanışla aşıldı."
            )
            break
    end = min(len(rows) - 1, start + 60)
    result["boundary_lines"] = [
        {
            "kind": label,
            "start_time": timestamp(rows[start].open_time),
            "start_price": line(start),
            "end_time": timestamp(rows[end].open_time),
            "end_price": line(end),
            "slope_per_bar": slope,
        }
        for label, line, slope in (("upper", upper, us), ("lower", lower, ls))
    ]
    if confirmed_index is None:
        level, opposite = (upper(end), lower(end)) if up else (lower(end), upper(end))
    position = "within_buffer"
    if rows[-1].close > level + buffer:
        position = "above"
    elif rows[-1].close < level - buffer:
        position = "below"
    result.update(
        breakout_level=level,
        invalidation_level=opposite,
        confirmation_threshold=level + buffer if up else level - buffer,
        distance_from_breakout_pct=(rows[-1].close / level - 1) * 100,
        breakout_position=position,
        breakout_holding=position == ("above" if up else "below"),
        confirmation_age_bars=None if confirmed_index is None else len(rows) - 1 - confirmed_index,
    )
    return finish()
