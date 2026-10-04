"""Explain existing geometric evidence without probabilities or trade orders."""


def build_report(analysis, max_age=4, min_volume_ratio=1.5):
    patterns = []
    current = []
    for p in analysis["patterns"] if analysis["status"] == "ready" else []:
        if p["status"] == "not_detected":
            continue
        reasons = []
        if p["status"] != "confirmed":
            reasons.append("not_confirmed")
        age = p["confirmation_age_bars"]
        if p["status"] == "confirmed":
            if age is None or age > max_age:
                reasons.append("confirmation_too_old_or_unknown")
            if p["breakout_holding"] is not True:
                reasons.append("breakout_not_holding")
            if p["direction"] not in {"up", "down"}:
                reasons.append("direction_unknown")
        qualifies = not reasons
        ratio = p.get("volume_ratio")
        supported = None if ratio is None else ratio >= min_volume_ratio
        enriched = dict(
            p,
            current_confirmation=qualifies,
            exclusion_reasons=reasons,
            report_volume_supported=supported,
        )
        patterns.append(enriched)
        if qualifies:
            current.append(enriched)
    up = [p for p in current if p["direction"] == "up"]
    down = [p for p in current if p["direction"] == "down"]
    assessment = "waiting"
    comment = "Güncel ve kırılımını koruyan teyit yok; oluşan yapılar izlenebilir."
    if analysis["status"] != "ready":
        assessment = "unavailable"
        comment = "Veri analize hazır değil; yön değerlendirmesi yapılmadı."
    elif up and down:
        assessment = "conflicting"
        comment = "Güncel yükseliş ve düşüş teyitleri birlikte var; yönler çelişiyor."
    elif up:
        assessment = "bullish_setup"
        comment = "Güncel yükseliş teyidi var; güncel düşüş teyidi bulunmuyor."
    elif down:
        assessment = "bearish_setup"
        comment = "Güncel düşüş teyidi var; güncel yükseliş teyidi bulunmuyor."
    supported = sum(p["report_volume_supported"] is True for p in current)
    if current:
        comment += (
            f" {len(current)} güncel teyidin {supported} tanesi seçilen hacim eşiğini karşılıyor."
        )
    return {
        **{k: v for k, v in analysis.items() if k != "patterns"},
        "report_version": "coin_report_v1",
        "assessment": assessment,
        "commentary": comment,
        "max_confirmation_age_bars": max_age,
        "min_volume_ratio": min_volume_ratio,
        "counts": {
            "patterns_evaluated": len(analysis["patterns"]) if analysis["status"] == "ready" else 0,
            "detected": len(patterns),
            "forming": sum(p["status"] == "forming" for p in patterns),
            "invalidated": sum(p["status"] == "invalidated" for p in patterns),
            "confirmed_total": sum(p["status"] == "confirmed" for p in patterns),
            "current_up": len(up),
            "current_down": len(down),
            "current_volume_supported": supported,
            "current_volume_unknown": sum(p["report_volume_supported"] is None for p in current),
        },
        "patterns": patterns,
        "chart_path": "/analysis/binance/chart?symbol=" + analysis["symbol"],
        "interpretation_note": (
            "Formasyonlar aynı fiyat verisinden türetilir; "
            "bağımsız kanıt veya başarı olasılığı değildir."
        ),
    }
