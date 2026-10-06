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
from app.services import formations
from app.services.coin_report import build_report

VERSION = "portfolio_technical_15m_v1"
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


def technical(db, symbol, stamp):
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
            "izle; diğer vadeler henüz değerlendirilmedi."
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
            horizons=[
                dict(
                    name="Kısa", interval="15m", status=analysis["status"], label=LABELS[assessment]
                ),
                dict(
                    name="Orta",
                    interval="4h",
                    status="not_supported",
                    label="4 saatlik motor ve geçmiş henüz hazır değil",
                ),
                dict(
                    name="Uzun",
                    interval="1d",
                    status="not_supported",
                    label="Günlük motor ve geçmiş henüz hazır değil",
                ),
            ],
            note=(
                "Alış kaydı olan coinlerin teknik görünümüdür; satışlar düşülmez. "
                "Başarı olasılığı veya otomatik emir değildir."
            ),
        )
    )


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
    result = db.execute(
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
    )
    db.commit()
    row = db.scalar(select(PortfolioObservation).where(*condition))
    return dict(created=result.rowcount == 1, observation=observation_out(row))


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
