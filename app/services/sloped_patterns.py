"""Experimental converging-channel rules with per-candle boundary evaluation."""

import math

from app.services.formations import BAR, confirmation_volume, timestamp

KINDS = {"symmetrical_triangle", "rising_wedge", "falling_wedge", "broadening_triangle"}


def fit(points):
    mean_x = sum(i for i, _ in points) / len(points)
    mean_y = sum(v for _, v in points) / len(points)
    denominator = sum((i - mean_x) ** 2 for i, _ in points)
    if denominator == 0:
        return None
    slope = sum((i - mean_x) * (v - mean_y) for i, v in points) / denominator
    intercept = mean_y - slope * mean_x
    error = max(abs(v - (intercept + slope * i)) for i, v in points)
    return slope, intercept, error


def detect_sloped(rows, highs, lows, kind, name, atr, tolerance, buffer, expected_direction=None):
    expected = {
        "symmetrical_triangle": "neutral",
        "rising_wedge": "down",
        "falling_wedge": "up",
        "broadening_triangle": "neutral",
    }[kind]
    expanding = kind == "broadening_triangle"
    if expected_direction is not None:
        expected = expected_direction
    result = {
        "pattern": kind,
        "name": name,
        "status": "not_detected",
        "direction": expected,
        "breakout_level": None,
        "invalidation_level": None,
        "confirmation_buffer": buffer,
        "confirmed_at": None,
        "start_time": None,
        "anchor_time": None,
        "structure_available_at": None,
        "reason": "Yakın tarihli uygun eğimli kanal bulunamadı.",
        "last_close": rows[-1].close,
        "last_candle_close_time": timestamp(rows[-1].open_time + BAR),
        "confirmation_age_bars": None,
        "distance_from_breakout_pct": None,
        "breakout_position": None,
        "breakout_holding": None,
        "confirmation_threshold": None,
        "pivot_points": [],
        "boundary_lines": [],
        "apex_time": None,
    }

    def finish():
        result.update(confirmation_volume(rows, result["confirmed_at"]))
        return result

    upper_points, lower_points = highs[-3:], lows[-3:]
    if len(upper_points) != 3 or len(lower_points) != 3:
        return finish()
    start = min(upper_points[0][0], lower_points[0][0])
    anchor = max(upper_points[-1][0], lower_points[-1][0])
    if not (
        12 <= anchor - start <= 100
        and len(rows) - anchor <= 40
        and max(upper_points[0][0], lower_points[0][0])
        < min(upper_points[-1][0], lower_points[-1][0])
    ):
        return finish()
    upper_fit, lower_fit = fit(upper_points), fit(lower_points)
    if upper_fit is None or lower_fit is None:
        return finish()
    upper_slope, upper_intercept, upper_error = upper_fit
    lower_slope, lower_intercept, lower_error = lower_fit
    if max(upper_error, lower_error) > tolerance or (not expanding and upper_slope >= lower_slope):
        return finish()
    directions = {
        "symmetrical_triangle": upper_slope < 0 < lower_slope,
        "broadening_triangle": lower_slope < 0 < upper_slope,
        "rising_wedge": 0 < upper_slope < lower_slope,
        "falling_wedge": upper_slope < lower_slope < 0,
    }
    if not directions[kind]:
        return finish()
    span = anchor - start
    if min(abs(upper_slope), abs(lower_slope)) * span < max(tolerance, atr * 0.5):
        return finish()

    def upper(i):
        return upper_intercept + upper_slope * i

    def lower(i):
        return lower_intercept + lower_slope * i

    initial_width = upper(start) - lower(start)
    final_width = upper(anchor) - lower(anchor)
    if expanding:
        if not (initial_width > 2 * tolerance and final_width >= 1.25 * initial_width):
            return finish()
        # An expanding structure has no future apex; bound its unconfirmed lifetime.
        expiry = anchor + 60
        apex = None
    else:
        apex = (upper_intercept - lower_intercept) / (lower_slope - upper_slope)
        if not (
            initial_width > 0
            and 2 * tolerance < final_width <= 0.8 * initial_width
            and anchor + 3 < apex <= anchor + 100
        ):
            return finish()
        expiry = apex
    if any(
        r.high > upper(i) + tolerance or r.low < lower(i) - tolerance
        for i, r in enumerate(rows[start : anchor + 1], start=start)
    ):
        return finish()
    result.update(
        status="forming",
        start_time=timestamp(rows[start].open_time),
        anchor_time=timestamp(rows[anchor].open_time),
        structure_available_at=timestamp(rows[anchor + 3].open_time + BAR),
        apex_time=None if apex is None else timestamp(round(rows[0].open_time + apex * BAR)),
        reason="Eğimli sınırlar bulundu; kapanışla kırılım henüz teyit edilmedi.",
        pivot_points=[
            {"open_time": timestamp(rows[i].open_time), "price": v, "kind": label}
            for points, label in ((upper_points, "high"), (lower_points, "low"))
            for i, v in points
        ],
    )
    # Before confirmation levels follow their fitted lines; afterward they freeze
    # at the confirmation candle so the projected apex cannot invert invalidation.
    direction = expected
    level, opposite, confirmed_index = None, None, None
    for i in range(anchor + 3, len(rows)):
        close = rows[i].close
        if confirmed_index is None:
            if i >= expiry:
                result.update(
                    status="invalidated",
                    reason="Kırılım teyidi olmadan yapının değerlendirme süresi doldu.",
                )
                break
            above, below = close > upper(i) + buffer, close < lower(i) - buffer
            if expected == "up" and below or expected == "down" and above:
                result.update(
                    status="invalidated", reason="Beklenen yönün tersindeki sınır kapanışla aşıldı."
                )
                break
            if above and expected in {"up", "neutral"}:
                direction, level, opposite = "up", upper(i), lower(i)
            elif below and expected in {"down", "neutral"}:
                direction, level, opposite = "down", lower(i), upper(i)
            else:
                continue
            confirmed_index = i
            result.update(
                status="confirmed",
                direction=direction,
                confirmed_at=timestamp(rows[i].open_time + BAR),
                reason="Eğimli sınır kapanış fiyatıyla ve teyit payıyla aşıldı.",
            )
        else:
            invalid = close < opposite - buffer if direction == "up" else close > opposite + buffer
            if invalid:
                result.update(
                    status="invalidated", reason="Teyitte sabitlenen karşı sınır kapanışla aşıldı."
                )
                break

    end = min(len(rows) - 1, math.ceil(expiry) - 1)
    result["boundary_lines"] = [
        {
            "kind": label,
            "start_time": timestamp(rows[start].open_time),
            "start_price": line(start),
            "end_time": timestamp(rows[0].open_time + end * BAR),
            "end_price": line(end),
            "slope_per_bar": slope,
        }
        for label, line, slope in (("upper", upper, upper_slope), ("lower", lower, lower_slope))
    ]
    if confirmed_index is None and expected != "neutral":
        index = min(len(rows) - 1, math.ceil(expiry) - 1)
        level, opposite = (
            (upper(index), lower(index)) if expected == "up" else (lower(index), upper(index))
        )
    if level is not None:
        position = "within_buffer"
        if rows[-1].close > level + buffer:
            position = "above"
        elif rows[-1].close < level - buffer:
            position = "below"
        result.update(
            breakout_level=level,
            invalidation_level=opposite,
            confirmation_threshold=level + buffer if direction == "up" else level - buffer,
            distance_from_breakout_pct=(rows[-1].close / level - 1) * 100,
            breakout_position=position,
            breakout_holding=position == ("above" if direction == "up" else "below"),
        )
    if confirmed_index is not None:
        result["confirmation_age_bars"] = len(rows) - 1 - confirmed_index
    return finish()
