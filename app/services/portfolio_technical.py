"""Private technical monitoring; no FX calls, calibrated odds or order execution."""

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.db.models.portfolio_observation import PortfolioObservation
from app.services import formations, portfolio_timeframes
from app.services.coin_report import build_report

VERSION = "portfolio_multiframe_v2"
LABELS = {
    "bullish_setup": "Yükseliş teyidi",
    "bearish_setup": "Düşüş baskısı",
    "conflicting": "Çelişkili teyitler",
    "waiting": "Teyit bekleniyor",
    "unavailable": "Veri hazır değil",
}
TR = timezone(timedelta(hours=3))


def json_ready(value):
    return json.loads(json.dumps(value, default=lambda item: item.isoformat()))


def short_technical(db, symbol, stamp):
    known = db.get(BinanceSpotSymbol, symbol)
    rows = list(
        reversed(
            db.scalars(
                select(BinanceSpotCandle)
                .where(
                    BinanceSpotCandle.symbol == symbol,
                    BinanceSpotCandle.interval == "15m",
                    BinanceSpotCandle.open_time + formations.BAR <= stamp,
                )
                .order_by(BinanceSpotCandle.open_time.desc())
                .limit(200)
            ).all()
        )
    )
    analysis = formations.analyze_rows(rows, symbol, stamp)
    if known is None or not known.active:
        analysis.update(status="inactive_symbol", patterns=[])
    report = build_report(analysis)
    positives, negatives, watch = [], [], []
    for p in report["patterns"]:
        if p["current_confirmation"]:
            direction = "yükseliş" if p["direction"] == "up" else "düşüş"
            evidence = p["name"] + ": güncel " + direction + " teyidi korunuyor."
            (positives if p["direction"] == "up" else negatives).append(evidence)
            if p["report_volume_supported"] is not True:
                negatives.append(p["name"] + ": teyit hacmi desteklenmiyor veya bilinmiyor.")
        elif p["status"] == "forming":
            watch.append(p["name"] + ": oluşuyor; kırılım teyidi bekleniyor.")
        elif p["status"] == "invalidated":
            negatives.append(p["name"] + ": yapı geçersizleşmiş; aktif sinyal değildir.")
    assessment = report["assessment"]
    guidance = {
        "bullish_setup": (
            "Yeni alımı değerlendirirken teyit hacmini ve kırılımın "
            "korunmasını kontrol et. Eldeki coin için geçersizlik seviyesini "
            "izle; diğer vadelerin durumunu da kontrol et."
        ),
        "bearish_setup": (
            "Eldeki coin için risk planını ve geçersizlik/kırılım seviyelerini "
            "gözden geçir. Yeni alım için düşüş baskısının azalmasını ve yeni "
            "yükseliş teyidini izle."
        ),
        "conflicting": (
            "Yön kanıtları çelişiyor. Yeni karar öncesinde çelişkinin "
            "çözülmesini ve hangi kırılımın korunduğunu izle."
        ),
        "waiting": (
            "Oluşan formasyon tek başına teyit değildir. Yeni alım veya elde "
            "tutma değerlendirmesini kapanış teyidi ve kişisel risk planıyla "
            "birlikte yap."
        ),
        "unavailable": (
            "Güncel ve tam veri olmadan teknik alım/satım değerlendirmesi "
            "üretilemez. Veri zamanını ve eksik geçmişi kontrol et."
        ),
    }[assessment]
    if analysis["status"] != "ready":
        negatives.append("Veri kalitesi: " + analysis["status"])
    return json_ready(
        dict(
            symbol=symbol,
            version=VERSION,
            as_of=formations.timestamp(stamp),
            status=analysis["status"],
            candles_used=len(rows),
            assessment=assessment,
            label=LABELS[assessment],
            candle_close_time=(
                formations.timestamp(rows[-1].open_time + formations.BAR) if rows else None
            ),
            last_close=(
                format(Decimal(str(rows[-1].close)), "f")
                if rows and analysis["status"] == "ready"
                else None
            ),
            positive_notes=positives,
            negative_notes=negatives,
            watch_notes=watch,
            guidance=guidance,
            patterns=report["patterns"],
            counts=report["counts"],
            note=(
                "Alış kaydı olan coinlerin teknik görünümüdür; satışlar düşülmez. "
                "Başarı olasılığı veya otomatik emir değildir."
            ),
        )
    )


def technical(db, symbol, stamp):
    result = short_technical(db, symbol, stamp)
    horizons = [
        dict(
            name="Kısa",
            interval="15m",
            status=result["status"],
            label=result["label"],
            assessment=result["assessment"],
            candle_close_time=result["candle_close_time"],
            counts=result["counts"],
            patterns=result["patterns"],
            candles_required=200,
        )
    ]
    for name, interval in (("Orta", "4h"), ("Uzun", "1d")):
        analysis = portfolio_timeframes.analyze(db, symbol, stamp, interval)
        if result["status"] == "inactive_symbol":
            analysis.update(status="inactive_symbol", patterns=[])
        report = build_report(analysis)
        horizons.append(
            dict(
                name=name,
                interval=interval,
                status=analysis["status"],
                label=LABELS[report["assessment"]],
                assessment=report["assessment"],
                candle_close_time=analysis["candle_close_time"],
                counts=report["counts"],
                patterns=report["patterns"],
                candles_used=analysis["candles_used"],
                candles_required=200,
            )
        )
    result["horizons"] = horizons
    ready = [h for h in horizons if h["status"] == "ready"]
    up = any(h["assessment"] in ("bullish_setup", "conflicting") for h in ready)
    down = any(h["assessment"] in ("bearish_setup", "conflicting") for h in ready)
    if up and down:
        result["alignment"] = "Vade/yön çelişkisi"
        result["alignment_note"] = (
            "Hazır vadelerde yükseliş ve düşüş teyitleri birlikte bulunuyor. "
            "Kısa vadeli olumlu yapı orta/uzun vadeli riski ortadan kaldırmaz."
        )
    elif up:
        result["alignment"] = "Hazır vadelerde yükseliş teyidi"
        result["alignment_note"] = (
            "Yükseliş teyidi bulunan vadeleri ve hacim desteğini ayrı incele. "
            "Diğer vadelerde teyit bekleniyor olabilir."
        )
    elif down:
        result["alignment"] = "Hazır vadelerde düşüş baskısı"
        result["alignment_note"] = (
            "Düşüş teyidi bulunan vadelerde risk seviyelerini kontrol et. Kısa "
            "vadeli hareket ana yapının değiştiği anlamına gelmez."
        )
    else:
        result["alignment"] = "Yön teyidi yok" if ready else "Veri hazır değil"
        result["alignment_note"] = "Oluşan yapılar teyit değildir; kapanış ve hacim desteğini izle."
    missing = [h["name"] for h in horizons if h["status"] != "ready"]
    if missing:
        result["alignment_note"] += " Değerlendirilemeyen vadeler: " + ", ".join(missing) + "."
    result["guidance"] = result["alignment_note"] + " " + result["guidance"]
    return json_ready(result)


def save_daily(db, account_id, symbol, stamp):
    day = datetime.fromtimestamp(stamp / 1000, UTC).astimezone(TR).date().isoformat()
    condition = (
        PortfolioObservation.account_id == account_id,
        PortfolioObservation.symbol == symbol,
        PortfolioObservation.local_day == day,
    )
    existing = db.scalar(select(PortfolioObservation).where(*condition))
    if existing:
        return dict(created=False, observation=observation_out(existing))
    payload = technical(db, symbol, stamp)
    if payload["status"] != "ready":
        return dict(created=False, reason="not_ready", current=payload)
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    inserted_id = db.execute(
        insert(PortfolioObservation)
        .values(
            id=str(uuid4()),
            account_id=account_id,
            symbol=symbol,
            local_day=day,
            observed_ms=stamp,
            method=VERSION,
            payload=payload,
        )
        .on_conflict_do_nothing(index_elements=["account_id", "symbol", "local_day"])
        .returning(PortfolioObservation.id)
    ).scalar_one_or_none()
    db.commit()
    row = db.scalar(select(PortfolioObservation).where(*condition))
    return dict(created=inserted_id is not None, observation=observation_out(row))


def observation_out(row):
    return dict(
        id=row.id,
        symbol=row.symbol,
        day=row.local_day,
        observed_at=formations.timestamp(row.observed_ms),
        method=row.method,
        technical=row.payload,
    )


def previous_day(db, account_id, symbol, stamp):
    day = datetime.fromtimestamp(stamp / 1000, UTC).astimezone(TR).date().isoformat()
    row = db.scalar(
        select(PortfolioObservation)
        .where(
            PortfolioObservation.account_id == account_id,
            PortfolioObservation.symbol == symbol,
            PortfolioObservation.local_day < day,
        )
        .order_by(PortfolioObservation.local_day.desc())
        .limit(1)
    )
    return observation_out(row) if row else None
