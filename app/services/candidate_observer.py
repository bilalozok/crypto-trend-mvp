"""Live-only immutable observations; no replay masquerading as live tracking."""

import os

from sqlalchemy import BigInteger, cast, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models.account import Account
from app.db.models.binance_spot import BinanceSpotCandle
from app.db.models.candidate_observation import CandidateObservation
from app.db.models.candidate_scan import CandidateScan
from app.services import candidate_archive, formations
from app.services.bullish_candidates import rank_match
from app.services.coin_report import build_report


def observe_symbol(db, symbol, stamp):
    if os.getenv("CANDIDATE_OBSERVATIONS_ENABLED", "false").lower() != "true":
        return 0
    close_ms = stamp // formations.BAR * formations.BAR
    entry = cast(CandidateScan.payload["evaluation_entry_ms"].as_string(), BigInteger)
    scans = db.scalars(
        select(CandidateScan)
        .join(Account, Account.id == CandidateScan.account_id)
        .where(
            Account.active.is_(True),
            entry + formations.BAR <= close_ms,
            entry + 96 * formations.BAR >= close_ms,
        )
    ).all()
    scans = [s for s in scans if any(c["symbol"] == symbol for c in s.payload["candidates"])]
    if not scans:
        return 0
    rows = list(
        reversed(
            db.scalars(
                select(BinanceSpotCandle)
                .where(
                    BinanceSpotCandle.symbol == symbol,
                    BinanceSpotCandle.interval == "15m",
                    BinanceSpotCandle.open_time + formations.BAR <= close_ms,
                )
                .order_by(BinanceSpotCandle.open_time.desc())
                .limit(200)
            ).all()
        )
    )
    analysis = formations.analyze_rows(rows, symbol, stamp)
    current_hash = candidate_archive.archive_hash()
    base = dict(
        symbol=symbol,
        observed_at=formations.timestamp(stamp).isoformat(),
        candle_close_time=formations.timestamp(close_ms).isoformat(),
        status=analysis["status"],
        qualified=None,
        close_price=None,
        reason="Veri hazır değil; aday durumu değerlendirilemedi.",
        analysis_rule_hash=current_hash,
    )
    if analysis["status"] == "ready":
        report = build_report(analysis)
        item, reason = rank_match(
            dict(symbol=symbol, quote_volume_24h=0, patterns=report["patterns"])
        )
        if not any(p["current_confirmation"] for p in report["patterns"]):
            item, reason = None, "no_current_confirmation"
        base.update(
            qualified=item is not None,
            close_price=format(rows[-1].close, ".15g"),
            reason=(
                "Aday koşulları korunuyor."
                if item is not None
                else candidate_archive.REASONS.get(reason, "Aday koşulları yok.")
            ),
        )
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    recorded = 0
    for scan in scans:
        payload = dict(base)
        if scan.rule_hash != current_hash:
            payload.update(
                status="rule_changed",
                qualified=None,
                reason="Kural sürümü farklı; aday durumu karşılaştırılmadı.",
            )
        result = db.execute(
            insert(CandidateObservation)
            .values(
                scan_id=scan.id,
                symbol=symbol,
                close_ms=close_ms,
                payload=payload,
            )
            .on_conflict_do_nothing(index_elements=["scan_id", "symbol", "close_ms"])
            .returning(CandidateObservation.scan_id)
        ).scalar_one_or_none()
        recorded += result is not None
    db.commit()
    return recorded


def history(db, scan):
    condition = CandidateObservation.scan_id == scan.id
    rows = db.scalars(
        select(CandidateObservation)
        .where(condition)
        .order_by(CandidateObservation.close_ms.desc(), CandidateObservation.symbol)
        .limit(200)
    ).all()
    return dict(
        scan_id=scan.id,
        total=db.scalar(select(func.count()).select_from(CandidateObservation).where(condition)),
        observations=[r.payload for r in rows],
        note="Son 200 otomatik gözlem gösterilir. Yeni taramaların adayları girişten itibaren "
        "24 saat izlenir; geçmiş boşluklar doldurulmaz. Veri veya kural uyumsuzluğu "
        "aday kaybı değildir. Worker ilk gözlemi saklar; kesin değişim anı bilinmez.",
    )
