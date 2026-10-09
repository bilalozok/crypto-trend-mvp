"""Explain directional agreement without weights, probability, or trade decisions."""

VERSION = "formation_indicator_context_v1"


def relation(direction, sign):
    if sign == 0:
        return "neutral"
    return "supports" if (sign > 0) == (direction == "up") else "conflicts"


def summarize(horizon, patterns):
    result = dict(version=VERSION, status="unavailable", patterns=[])
    if horizon["status"] != "ready":
        return result
    latest, previous = horizon["latest"], horizon["series"][-2]
    eligible = [
        p
        for p in patterns
        if p["direction"] in {"up", "down"}
        and (p["status"] == "forming" or p.get("current_confirmation") is True)
    ]
    result["status"] = "ready"
    for p in eligible:
        direction = p["direction"]
        price_sign = (latest["close"] > latest["ema50"]) - (latest["close"] < latest["ema50"])
        slope_sign = (latest["ema50"] > previous["ema50"]) - (latest["ema50"] < previous["ema50"])
        trend = "mixed" if price_sign != slope_sign else relation(direction, price_sign)
        rsi_sign = (latest["rsi"] > 50) - (latest["rsi"] < 50)
        macd_sign = (latest["histogram"] > 0) - (latest["histogram"] < 0)
        momentum = "mixed" if rsi_sign != macd_sign else relation(direction, rsi_sign)
        volume = "neutral"
        if p.get("current_confirmation"):
            if p.get("report_volume_supported") is True:
                volume = "supports"
            elif p.get("report_volume_supported") is False:
                volume = "limited"
        groups = [
            dict(
                name="Trend · EMA50",
                state=trend,
                reason="Fiyatın EMA50 konumu ve EMA50 eğimi birlikte incelendi.",
            ),
            dict(
                name="Momentum · RSI / MACD",
                state=momentum,
                reason="RSI 50 orta noktası ve MACD histogram yönü tek grupta incelendi.",
            ),
            dict(
                name="Teyit hacmi",
                state=volume,
                reason=(
                    "Teyit hacmi 1,5x eşiğiyle karşılaştırılır; "
                    "oluşan yapıda henüz teyit hacmi yok."
                ),
            ),
        ]
        if latest["rsi"] >= 70 or latest["rsi"] <= 30:
            groups.append(
                dict(
                    name="RSI aşırılığı",
                    state="caution",
                    reason="RSI 30/70 sınırı dışında; tek başına dönüş kanıtı değildir.",
                )
            )
        groups.append(
            dict(
                name="Bollinger",
                state="neutral",
                reason="Oynaklık bağlamıdır; bant konumu yön teyidine ek oy sayılmadı.",
            )
        )
        fib = horizon.get("fibonacci", {})
        if fib.get("status") == "ready":
            nearest = min(
                fib["levels"], key=lambda level: abs(latest["close"] / level["price"] - 1)
            )
            groups.append(
                dict(
                    name="Fibonacci",
                    state="neutral",
                    reason=(
                        f"En yakın düzeltme %{nearest['ratio'] * 100:g}; "
                        "fiyat seviyesidir, bağımsız yön teyidi değildir."
                    ),
                )
            )
        result["patterns"].append(
            dict(
                name=p["name"],
                direction=direction,
                stage="confirmed" if p.get("current_confirmation") else "forming",
                groups=groups,
            )
        )
    return result
