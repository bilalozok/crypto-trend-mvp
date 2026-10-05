"""Aligned historical similarity, with explicit component availability."""

import math
from collections import Counter, defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.db.models.formation_history import FormationEvent
from app.services.binance_coverage import STABLECOIN_BASES
from app.services.formations import BAR, analyze_rows, timestamp


def returns(rows, lookback):
    closes = [r.close for r in rows[-lookback - 1 :]]
    return [math.log(b / a) for a, b in zip(closes, closes[1:], strict=False)]


def correlation(a, b):
    if len(a) != len(b) or len(a) < 2:
        return None
    mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
    x = [v - mean_a for v in a]
    y = [v - mean_b for v in b]
    xx, yy = sum(v * v for v in x), sum(v * v for v in y)
    if min(xx, yy) < 1e-16:
        return None
    return max(-1.0, min(1.0, sum(v * w for v, w in zip(x, y, strict=True)) / math.sqrt(xx * yy)))


def features(analysis):
    return {
        (p["pattern"], p["status"], p["direction"])
        for p in analysis["patterns"]
        if p["status"] == "forming"
        or (
            p["status"] == "confirmed"
            and p.get("confirmation_age_bars") is not None
            and p["confirmation_age_bars"] <= 4
            and p.get("breakout_holding") is True
        )
    }


def overlap(a, b):
    return None if not (a | b) else len(a & b) / len(a | b)


def components(corr, reference, candidate, ref_changes, candidate_changes):
    values = dict(
        price_correlation=max(0, corr),
        formation_overlap=overlap(reference, candidate),
        change_overlap=(
            overlap(ref_changes, candidate_changes) if ref_changes and candidate_changes else None
        ),
    )
    weights = dict(price_correlation=0.7, formation_overlap=0.2, change_overlap=0.1)
    total = sum(weights[k] for k, v in values.items() if v is not None)
    used = {k: weights[k] / total for k, v in values.items() if v is not None}
    score = 100 * sum(values[k] * w for k, w in used.items())
    return dict(
        similarity_score=score,
        components=values,
        weights_used=used,
        shared_formations=[
            dict(pattern=p, status=s, direction=d) for p, s, d in sorted(reference & candidate)
        ],
        shared_changes=[
            dict(pattern=p, previous_status=a, current_status=b, direction=d)
            for p, a, b, d in sorted(ref_changes & candidate_changes)
        ],
    )


def similar(
    db,
    symbol,
    stamp,
    lookback=48,
    candidate_limit=100,
    offset=0,
    limit=10,
    min_volume=0,
    min_correlation=0.3,
):
    target = db.get(BinanceSpotSymbol, symbol)
    if target is None or not target.active:
        return None
    filters = [
        BinanceSpotSymbol.active.is_(True),
        BinanceSpotSymbol.symbol != symbol,
        BinanceSpotSymbol.base_asset.not_in(STABLECOIN_BASES),
        BinanceSpotSymbol.quote_volume_24h >= min_volume,
    ]
    total = db.scalar(select(func.count()).select_from(BinanceSpotSymbol).where(*filters))
    candidates = db.scalars(
        select(BinanceSpotSymbol)
        .where(*filters)
        .order_by(BinanceSpotSymbol.quote_volume_24h.desc(), BinanceSpotSymbol.symbol)
        .offset(offset)
        .limit(candidate_limit)
    ).all()
    symbols = [symbol] + [s.symbol for s in candidates]
    ranked = (
        select(
            BinanceSpotCandle,
            func.row_number()
            .over(
                partition_by=BinanceSpotCandle.symbol, order_by=BinanceSpotCandle.open_time.desc()
            )
            .label("position"),
        )
        .where(BinanceSpotCandle.symbol.in_(symbols), BinanceSpotCandle.open_time + BAR <= stamp)
        .subquery()
    )
    candle = aliased(BinanceSpotCandle, ranked)
    grouped = defaultdict(list)
    for row in db.scalars(
        select(candle).where(ranked.c.position <= 200).order_by(candle.symbol, candle.open_time)
    ):
        grouped[row.symbol].append(row)
    reference = analyze_rows(grouped[symbol], symbol, stamp)
    response = dict(
        exchange="binance",
        market="spot",
        interval="15m",
        symbol=symbol,
        similarity_version="aligned_similarity_v1",
        method_version=reference["method_version"],
        experimental=True,
        as_of=timestamp(stamp),
        status=reference["status"],
        lookback_bars=lookback,
        candidate_limit=candidate_limit,
        offset=offset,
        limit=limit,
        min_quote_volume=min_volume,
        min_correlation=min_correlation,
        total_eligible_symbols=total,
        scanned_symbols=0,
        quality_counts={},
        next_offset=None,
        ranking_scope="candidate_page",
        matches=[],
        matched_symbols=0,
        note=(
            "Geçmiş benzerliği gelecekte aynı hareketin olasılığı değildir; "
            "eksik bileşenlerin ağırlıkları yeniden normalize edilir."
        ),
    )
    if reference["status"] != "ready":
        return response
    ref_returns = returns(grouped[symbol], lookback)
    if correlation(ref_returns, ref_returns) is None:
        response["status"] = "reference_no_variation"
        return response
    close_ms = grouped[symbol][-1].open_time + BAR
    response["reference_candle_close_time"] = timestamp(close_ms)
    changes = defaultdict(set)
    events = db.scalars(
        select(FormationEvent).where(
            FormationEvent.symbol.in_(symbols),
            FormationEvent.candle_close_ms > close_ms - lookback * BAR,
            FormationEvent.candle_close_ms <= close_ms,
            FormationEvent.event_type != "initial_observation",
        )
    )
    for e in events:
        if e.previous is not None:
            changes[e.symbol].add(
                (e.pattern, e.previous["status"], e.current["status"], e.current["direction"])
            )
    ref_features = features(reference)
    response["reference_formations"] = [
        dict(pattern=p, status=s, direction=d) for p, s, d in sorted(ref_features)
    ]
    response["reference_change_count"] = len(changes[symbol])
    response["lookback_start_close_time"] = timestamp(close_ms - lookback * BAR)
    quality = Counter()
    matches = []
    for candidate in candidates:
        rows = grouped[candidate.symbol]
        analysis = analyze_rows(rows, candidate.symbol, stamp)
        if analysis["status"] != "ready":
            quality[analysis["status"]] += 1
            continue
        if rows[-1].open_time + BAR != close_ms:
            quality["unaligned_data"] += 1
            continue
        corr = correlation(ref_returns, returns(rows, lookback))
        if corr is None:
            quality["no_variation"] += 1
            continue
        quality["ready"] += 1
        if corr < min_correlation:
            continue
        item = components(
            corr, ref_features, features(analysis), changes[symbol], changes[candidate.symbol]
        )
        matches.append(
            dict(
                symbol=candidate.symbol,
                quote_volume_24h=candidate.quote_volume_24h,
                return_correlation=corr,
                chart_path="/analysis/binance/chart?symbol=" + candidate.symbol,
                **item,
            )
        )
    matches.sort(key=lambda r: (-r["similarity_score"], -r["return_correlation"], r["symbol"]))
    next_offset = offset + len(candidates)
    response.update(
        scanned_symbols=len(candidates),
        quality_counts=dict(quality),
        next_offset=next_offset if next_offset < total else None,
        matched_symbols=len(matches),
        matches=matches[:limit],
    )
    return response
