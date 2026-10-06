"""Conditional descriptions of each horizon; no calibrated odds or orders."""

from decimal import Decimal

QUALITY = {
    "insufficient_data": "200 kapanmış mum için geçmiş yetersiz.",
    "missing_data": "Mum dizisinde boşluk var.",
    "invalid_data": "Fiyat/hacim verisi doğrulanamadı.",
    "stale_data": "Son beklenen kapanmış mum henüz yok.",
    "inactive_symbol": "Parite aktif Binance Spot kataloğunda değil.",
}


def price(value):
    return format(Decimal(str(value)), "f") if value is not None else None


def describe(horizon):
    positives, negatives, watch, levels = [], [], [], []
    if horizon["status"] != "ready":
        return dict(
            positive_notes=[],
            negative_notes=[QUALITY.get(horizon["status"], "Veri hazır değil.")],
            watch_notes=[],
            level_conditions=[],
            guidance="Bu vadede yön veya alım/satım değerlendirmesi yapılmadı. Veriyi yenile.",
        )
    for p in horizon["patterns"]:
        name = p["name"]
        direction = p["direction"]
        if p["current_confirmation"]:
            text = (
                name
                + ": güncel "
                + ("yükseliş" if direction == "up" else "düşüş")
                + " teyidi korunuyor."
            )
            (positives if direction == "up" else negatives).append(text)
            supported = p.get("report_volume_supported")
            if supported is True:
                (positives if direction == "up" else negatives).append(
                    name + ": teyit hacmi referans ortalamasının en az 1,5 katı."
                )
            elif supported is False:
                watch.append(
                    name + ": teyit hacim oranı 1,5 kat eşiğinin altında; yön kanıtı daha sınırlı."
                )
            else:
                watch.append(name + ": hacim desteği bilinmiyor; güçlü teyit olarak yorumlanamaz.")
        elif p["status"] == "forming":
            watch.append(name + ": yapı oluşuyor, kapanış teyidi henüz yok.")
        elif p["status"] == "invalidated":
            watch.append(name + ": yapı geçersizleşmiş; güncel yön kanıtı olarak kullanılmaz.")
        elif p["status"] == "confirmed":
            watch.append(name + ": teyit eski veya kırılım korunmuyor; güncel sinyal sayılmadı.")
        if p["current_confirmation"] or p["status"] == "forming":
            threshold = price(p.get("confirmation_threshold"))
            invalidation = price(p.get("invalidation_level"))
            if direction in ("up", "down") and threshold is not None:
                relation = "üzerinde" if direction == "up" else "altında"
                condition = (
                    "Bu vadenin kapanışı "
                    + threshold
                    + " USDT "
                    + relation
                    + (
                        " kalırsa kırılım koşulu desteklenir; hacim ve yapı geçerliliği de "
                        "kontrol edilir."
                    )
                )
                if p["status"] == "forming":
                    condition = "Teyit adayı: " + condition
                levels.append(
                    dict(
                        pattern=name,
                        status=p["status"],
                        direction=direction,
                        breakout=price(p.get("breakout_level")),
                        threshold=threshold,
                        invalidation=invalidation,
                        condition=condition,
                        risk_note=(
                            "Formasyon geçersizliği seviyesi: " + invalidation + " USDT."
                            if invalidation
                            else "Geçersizlik seviyesi bilinmiyor."
                        ),
                    )
                )
            elif direction == "neutral":
                watch.append(name + ": yön henüz belirlenmedi; iki sınırdaki kapanışları izle.")
    assessment = horizon["assessment"]
    guidance = {
        "bullish_setup": (
            "Yükseliş teyidi var. Kırılımın korunmasını, hacmi ve formasyon "
            "geçersizliği seviyesini takip et."
        ),
        "bearish_setup": (
            "Düşüş baskısı var. Eldeki coin için risk planını ve düşüş "
            "yapısının geçersizleşeceği koşulları gözden geçir."
        ),
        "conflicting": (
            "Aynı vadede yön teyitleri çelişiyor. Hangi kırılımın korunduğunu "
            "ve hacim desteğini izle."
        ),
        "waiting": (
            "Güncel teyit yok. Oluşan formasyonları kesin yön gibi "
            "yorumlamadan kapanış koşullarını izle."
        ),
    }[assessment]
    return dict(
        positive_notes=positives,
        negative_notes=negatives,
        watch_notes=watch,
        level_conditions=levels,
        guidance=guidance,
    )


def reviews(horizons):
    ready = [h for h in horizons if h["status"] == "ready"]
    up = [h["name"] for h in ready if h["assessment"] in ("bullish_setup", "conflicting")]
    down = [h["name"] for h in ready if h["assessment"] in ("bearish_setup", "conflicting")]
    missing = [h["name"] for h in horizons if h["status"] != "ready"]
    if not ready:
        buy = "Veri hazır değil; yeni alım için teknik değerlendirme yapılamıyor."
        holding = (
            "Eldeki coin için teknik risk değerlendirmesi yapılamıyor; güncel ve tam veriyi bekle."
        )
    elif down:
        buy = (
            "Düşüş teyidi olan vadeler: "
            + ", ".join(down)
            + (
                ". Yeni alımı değerlendirirken düşüş baskısının azalmasını ve "
                "yükseliş kapanış teyidini izle."
            )
        )
        holding = (
            "Düşüş teyidi olan vadelerde risk planını gözden geçir. Tablodaki "
            "kırılım ve formasyon geçersizliği seviyelerini kendi risk "
            "sınırlarınla birlikte değerlendir."
        )
        if up:
            buy += " Yükseliş teyidi de var (" + ", ".join(up) + "); yön kanıtları çelişiyor."
            holding += " Bir vadede olumlu yapı diğer vadelerdeki düşüş riskini ortadan kaldırmaz."
    elif up:
        buy = (
            "Yükseliş teyidi olan vadeler: "
            + ", ".join(up)
            + (
                ". Yeni alımı değerlendirirken teyit hacmini, kırılımdan uzaklığı "
                "ve diğer vadeleri kontrol et."
            )
        )
        holding = (
            "Korunan yükseliş kırılımlarını takip et. Geçersizlik veya "
            "kırılımın kaybı değerlendirmeyi değiştirebilir; fiyat seviyeleri "
            "ilgili vadenin kapanışıyla izlenir."
        )
    else:
        buy = (
            "Hazır vadelerde güncel yön teyidi yok. Yeni alım değerlendirmesi "
            "için oluşan yapıların kapanış ve hacim teyidini izle."
        )
        holding = (
            "Teyit olmaması düşüş riski olmadığı anlamına gelmez. Kişisel risk "
            "planını koru ve yeni teyit/geçersizlik gelişmelerini takip et."
        )
    if missing:
        note = (
            " Eksik/eski verili vadeler: "
            + ", ".join(missing)
            + "; bu vadeler değerlendirmeye katılmadı."
        )
        buy += note
        holding += note
    return dict(
        new_purchase_review=buy,
        holding_review=holding,
        commentary_version="portfolio_conditional_notes_v1",
    )
