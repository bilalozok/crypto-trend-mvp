"""Cost-adjusted hypothetical outcomes; each scan has its own cohort."""

from statistics import mean, median
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models.binance_spot import BinanceSpotCandle
from app.db.models.candidate_outcome import CandidateOutcome
from app.services.formations import BAR, timestamp
from app.services.portfolio_technical import json_ready
from app.services.signal_backtest import outcome

HORIZONS = (4, 8, 16, 96)
VERSION = "saved_candidate_outcomes_v1"


def statistics(rows):
    values = [r["net_return_pct"] for r in rows if r["status"] == "complete"]
    return dict(
        completed=len(values),
        pending=sum(r["status"] == "pending" for r in rows),
        unavailable=sum(r["status"] not in ("complete", "pending") for r in rows),
        positive_net_rate_pct=100 * sum(v > 0 for v in values) / len(values) if values else None,
        mean_net_return_pct=mean(values) if values else None,
        median_net_return_pct=median(values) if values else None,
    )


def results(db, scan, stamp, persist=False):
    entry = scan.payload.get("evaluation_entry_ms")
    candidates = scan.payload["candidates"]
    stored = {
        (r.symbol, r.horizon_bars): r.payload
        for r in db.scalars(select(CandidateOutcome).where(CandidateOutcome.scan_id == scan.id))
    }
    rows = []
    for candidate in candidates:
        symbol = candidate["symbol"]
        future = []
        if entry is not None:
            future = db.scalars(
                select(BinanceSpotCandle)
                .where(
                    BinanceSpotCandle.symbol == symbol,
                    BinanceSpotCandle.interval == "15m",
                    BinanceSpotCandle.open_time >= entry,
                    BinanceSpotCandle.open_time < entry + max(HORIZONS) * BAR,
                    BinanceSpotCandle.open_time + BAR <= stamp,
                )
                .order_by(BinanceSpotCandle.open_time)
            ).all()
        horizons = []
        for bars in HORIZONS:
            saved = stored.get((symbol, bars))
            if saved is not None:
                value = saved
            elif entry is None:
                value = dict(
                    status="legacy_unavailable",
                    reason="Eski taramada tamamlanma zamanı yok; ileriye dönük sonuç hesaplanmadı.",
                )
            elif stamp < entry + bars * BAR:
                value = dict(status="pending", reason="Tam ileri veri penceresi henüz kapanmadı.")
            else:
                # Reuse the checked contiguous-window and per-side trading cost formula.
                data, error = outcome(
                    [SimpleNamespace(open_time=entry - BAR), *future], 0, bars, 10, 5
                )
                if data is None:
                    value = dict(
                        status="missing_data",
                        reason=(
                            "İleri mum penceresi eksik veya geçersiz; "
                            "veri tamamlanırsa yeniden denenebilir."
                        ),
                    )
                else:
                    value = dict(
                        status="complete", **data, calculated_at=timestamp(stamp), version=VERSION
                    )
                    value = json_ready(value)
                    if persist:
                        insert = (
                            pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
                        )
                        db.execute(
                            insert(CandidateOutcome)
                            .values(
                                scan_id=scan.id, symbol=symbol, horizon_bars=bars, payload=value
                            )
                            .on_conflict_do_nothing(
                                index_elements=["scan_id", "symbol", "horizon_bars"]
                            )
                        )
                        db.flush()
                        value = db.get(CandidateOutcome, (scan.id, symbol, bars)).payload
            horizons.append(
                dict(
                    hours=bars // 4,
                    horizon_bars=bars,
                    stored=saved is not None or (persist and value["status"] == "complete"),
                    **value,
                )
            )
        rows.append(
            dict(
                symbol=symbol,
                pattern=candidate["primary_pattern"]["name"],
                evidence_score=candidate["evidence_score"],
                alignment=candidate.get("alignment"),
                outcomes=horizons,
            )
        )
    if persist:
        db.commit()
    summary = [
        dict(hours=bars // 4, **statistics([r["outcomes"][i] for r in rows]))
        for i, bars in enumerate(HORIZONS)
    ]
    paired = [r for r in rows if all(o["status"] == "complete" for o in r["outcomes"])]
    paired_summary = [
        dict(hours=bars // 4, **statistics([r["outcomes"][i] for r in paired]))
        for i, bars in enumerate(HORIZONS)
    ]
    return json_ready(
        dict(
            scan_id=scan.id,
            version=VERSION,
            as_of=timestamp(stamp),
            entry_time=timestamp(entry) if entry is not None else None,
            fee_bps_per_side=10,
            slippage_bps_per_side=5,
            summary=summary,
            paired_count=len(paired),
            paired_summary=paired_summary,
            candidates=rows,
            note=(
                "Bu taramanın varsayımsal aday sonuçlarıdır. Taramalar arası tekrarlar "
                "bağımsız işlem sayılmaz. Portföy getirisi veya başarı olasılığı değildir."
            ),
        )
    )
