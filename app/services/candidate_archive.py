"""Full-universe immutable scans; missing data never means a failed candidate."""

import hashlib
from collections import Counter, defaultdict
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import aliased

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.db.models.candidate_scan import CandidateScan
from app.services import formations, portfolio_technical
from app.services.binance_coverage import STABLECOIN_BASES
from app.services.bullish_candidates import rank_match
from app.services.coin_report import build_report
from app.services.forward_tracking import rules_hash

REASONS = {
    "no_current_confirmation": (
        "Güncel yön teyidi yok; teyit eskimiş veya kırılım kaybolmuş olabilir."
    ),
    "no_volume_supported_up_confirmation": "Güncel hacim destekli yükseliş teyidi yok.",
    "opposing_confirmation": "Güncel karşıt düşüş teyidi var.",
    "missing_breakout_distance": "Kırılıma uzaklık hesaplanamadı.",
}


def archive_hash():
    digest = hashlib.sha256(rules_hash().encode())
    for name in (
        "candidate_archive.py",
        "portfolio_technical.py",
        "portfolio_timeframes.py",
        "portfolio_commentary.py",
        "coin_report.py",
        "binance_coverage.py",
    ):
        digest.update(Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


def detail(row):
    return dict(
        id=row.id,
        created_at=formations.timestamp(row.created_ms),
        rule_hash=row.rule_hash,
        scan=row.payload,
    )


def summary(row):
    return dict(
        id=row.id,
        created_at=formations.timestamp(row.created_ms),
        rule_hash=row.rule_hash,
        candle_close_time=row.payload["candle_close_time"],
        scanned_symbols=len(row.payload["universe"]),
        qualified_symbols=len(row.payload["candidates"]),
        quality_counts=row.payload["quality_counts"],
    )


def collect(db, stamp):
    symbols = db.scalars(
        select(BinanceSpotSymbol)
        .where(
            BinanceSpotSymbol.active.is_(True),
            BinanceSpotSymbol.base_asset.not_in(STABLECOIN_BASES),
            BinanceSpotSymbol.quote_volume_24h >= 0,
        )
        .order_by(BinanceSpotSymbol.symbol)
        .limit(1001)
    ).all()
    if len(symbols) > 1000:
        raise ValueError("Coin evreni 1000 sınırını aşıyor; kısmi tarama kaydedilmedi.")
    grouped = defaultdict(list)
    if symbols:
        ranked = (
            select(
                BinanceSpotCandle,
                func.row_number()
                .over(
                    partition_by=BinanceSpotCandle.symbol,
                    order_by=BinanceSpotCandle.open_time.desc(),
                )
                .label("position"),
            )
            .where(
                BinanceSpotCandle.symbol.in_([s.symbol for s in symbols]),
                BinanceSpotCandle.open_time + formations.BAR <= stamp,
            )
            .subquery()
        )
        candle = aliased(BinanceSpotCandle, ranked)
        for row in db.scalars(
            select(candle).where(ranked.c.position <= 200).order_by(candle.symbol, candle.open_time)
        ):
            grouped[row.symbol].append(row)
    candidates, universe, quality = [], [], Counter()
    for symbol in symbols:
        analysis = formations.analyze_rows(grouped[symbol.symbol], symbol.symbol, stamp)
        report = build_report(analysis)
        quality[analysis["status"]] += 1
        if analysis["status"] != "ready":
            item, reason = None, "data_unavailable"
        else:
            item, reason = rank_match(
                dict(
                    symbol=symbol.symbol,
                    quote_volume_24h=symbol.quote_volume_24h,
                    patterns=report["patterns"],
                )
            )
            if not any(p["current_confirmation"] for p in report["patterns"]):
                item, reason = None, "no_current_confirmation"
        note = (
            "Aday koşullarını karşılıyor."
            if item is not None
            else REASONS.get(reason, "Veri hazır değil; aday durumu değerlendirilemedi.")
        )
        universe.append(
            dict(
                symbol=symbol.symbol,
                status=analysis["status"],
                qualified=item is not None,
                reason=reason,
                assessment=report["assessment"],
                note=note,
            )
        )
        if item is not None:
            # Existing ranking is preserved. Other intervals are context, not score inputs.
            view = portfolio_technical.technical(db, symbol.symbol, stamp)
            item.update(
                horizons=view["horizons"],
                alignment=view["alignment"],
                new_purchase_review=view["new_purchase_review"],
                holding_review=view["holding_review"],
            )
            candidates.append(item)
    candidates.sort(key=lambda r: (-r["evidence_score"], -r["quote_volume_24h"], r["symbol"]))
    return portfolio_technical.json_ready(
        dict(
            version="saved_candidate_scan_v1",
            ranking_version="bullish_evidence_v1",
            as_of=formations.timestamp(stamp),
            candle_close_time=formations.timestamp(stamp // formations.BAR * formations.BAR),
            filters=dict(
                max_confirmation_age_bars=4,
                min_volume_ratio=1.5,
                min_quote_volume=0,
                include_conflicting=False,
                include_stablecoins=False,
            ),
            ranking_scope="saved_full_universe",
            universe=universe,
            candidates=candidates,
            quality_counts=dict(quality),
            note=(
                "Kaydedilmiş görünüm değişmez. Orta/uzun vadeler puanı değiştirmez; "
                "eksik veriler değerlendirilmez."
            ),
        )
    )


def create(db, owner, request_id, stamp):
    condition = (CandidateScan.account_id == owner, CandidateScan.request_id == request_id)
    existing = db.scalar(select(CandidateScan).where(*condition))
    if existing:
        return detail(existing)
    payload = collect(db, stamp)
    from app.services.binance_collection import now_ms

    completed = max(stamp, now_ms())
    payload["calculation_completed_at"] = formations.timestamp(completed).isoformat()
    payload["evaluation_entry_ms"] = (completed // formations.BAR + 2) * formations.BAR
    payload["evaluation_note"] = (
        "Varsayımsal giriş: hesaplama bitiminden sonra en az 15 dakika tamponlu "
        "ilk 15m mum açılışı. Tarama veri zamanı ve giriş zamanı farklıdır."
    )
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    db.execute(
        insert(CandidateScan)
        .values(
            id=str(uuid4()),
            account_id=owner,
            request_id=request_id,
            created_ms=completed,
            rule_hash=archive_hash(),
            payload=payload,
        )
        .on_conflict_do_nothing(index_elements=["account_id", "request_id"])
    )
    db.commit()
    return detail(db.scalar(select(CandidateScan).where(*condition)))


def history(db, owner, symbol):
    scans = db.scalars(
        select(CandidateScan)
        .where(CandidateScan.account_id == owner)
        .order_by(CandidateScan.created_ms.desc(), CandidateScan.id.desc())
        .limit(100)
    ).all()
    events = []
    for row in scans:
        state = next((r for r in row.payload["universe"] if r["symbol"] == symbol), None)
        candidate = next((r for r in row.payload["candidates"] if r["symbol"] == symbol), None)
        if state is None:
            note = "Bu taramanın evreninde yok."
        elif state["qualified"]:
            note = "Aday koşullarını karşılıyor."
        else:
            note = state["note"]
        events.append(
            dict(
                scan_id=row.id,
                created_at=formations.timestamp(row.created_ms),
                rule_hash=row.rule_hash,
                state=state,
                candidate=candidate,
                note=note,
            )
        )
    return dict(
        symbol=symbol,
        events=events,
        note=(
            "Son 100 kayıtlı taramadaki gözlemler; aradaki hareketler "
            "veya başarı olasılığı değildir."
        ),
    )
