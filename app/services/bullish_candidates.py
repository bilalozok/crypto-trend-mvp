"""Heuristic evidence ranking; scores are not calibrated probabilities."""

from collections import Counter

from app.services.formation_scan import scan
from app.services.formations import BAR, timestamp


def rank_match(row, max_age=4, min_ratio=1.5, include_conflicting=False):
    current = [
        p
        for p in row["patterns"]
        if p["status"] == "confirmed"
        and p.get("confirmation_age_bars") is not None
        and 0 <= p["confirmation_age_bars"] <= max_age
        and p.get("breakout_holding") is True
    ]
    up = [
        p
        for p in current
        if p["direction"] == "up"
        and p.get("volume_ratio") is not None
        and p["volume_ratio"] >= min_ratio
    ]
    down = [p for p in current if p["direction"] == "down"]
    if not up:
        return None, "no_volume_supported_up_confirmation"
    if down and not include_conflicting:
        return None, "opposing_confirmation"
    ranked = []
    for p in up:
        distance = p.get("distance_from_breakout_pct")
        if distance is None:
            continue
        parts = dict(
            freshness=35 * (1 - p["confirmation_age_bars"] / (max_age + 1)),
            volume=35 * min(p["volume_ratio"] / max(3, 2 * min_ratio), 1),
            breakout_proximity=30 * max(0, 1 - abs(distance) / 3),
        )
        ranked.append((sum(parts.values()), p, parts))
    if not ranked:
        return None, "missing_breakout_distance"
    ranked.sort(key=lambda item: (-item[0], item[1]["pattern"]))
    raw, primary, parts = ranked[0]
    penalty = 30 if down else 0
    context = row.get("ranking_indicator_context")
    result = dict(
        symbol=row["symbol"],
        quote_volume_24h=row["quote_volume_24h"],
        evidence_score=max(0, raw - penalty),
        score_components=parts,
        conflict_penalty=penalty,
        primary_pattern=primary,
        supporting_patterns=up,
        opposing_patterns=down,
        commentary=(
            "Güncel düşüş teyidi de var; çelişki cezası uygulandı."
            if down
            else "Güncel, hacim destekli yükseliş teyidi var; güncel düşüş teyidi yok."
        ),
        chart_path="/analysis/binance/chart?symbol=" + row["symbol"],
    )
    if context is not None:
        apply_indicator_ranking(result, context)
    return result, None


def apply_indicator_ranking(item, context):
    base = item["evidence_score"]
    adjustment = context.get("adjustment", 0) if context.get("status") == "ready" else 0
    item.update(
        base_evidence_score=base,
        indicator_adjustment=adjustment,
        ranking_indicator_context=context,
        evidence_score=min(100, max(0, base + adjustment)),
    )
    return item


def candidates(
    db,
    stamp,
    candidate_limit=100,
    offset=0,
    limit=10,
    max_age=4,
    min_ratio=1.5,
    min_volume=0,
    include_conflicting=False,
):
    page = scan(
        db,
        stamp,
        limit=candidate_limit,
        offset=offset,
        direction="all",
        state="confirmed",
        min_volume=min_volume,
        max_confirmation_age_bars=max_age,
        breakout_holding=True,
    )
    ranked, excluded = [], Counter()
    excluded["no_current_confirmation"] = (
        page["quality_counts"].get("ready", 0) - page["matched_symbols"]
    )
    for row in page["matches"]:
        item, reason = rank_match(row, max_age, min_ratio, include_conflicting)
        if item is None:
            excluded[reason] += 1
        else:
            ranked.append(item)
    from app.services.money_flow_context import batch_context

    contexts = batch_context(db, [r["symbol"] for r in ranked], stamp)
    for item in ranked:
        apply_indicator_ranking(item, contexts[item["symbol"]])
    ranked.sort(key=lambda r: (-r["evidence_score"], -r["quote_volume_24h"], r["symbol"]))
    return dict(
        exchange="binance",
        market="spot",
        interval="15m",
        experimental=True,
        method_version=page["method_version"],
        ranking_version="bullish_evidence_moneyflow_v2",
        as_of=page["as_of"],
        candle_close_time=timestamp(stamp // BAR * BAR),
        candidate_limit=candidate_limit,
        offset=offset,
        limit=limit,
        max_confirmation_age_bars=max_age,
        min_volume_ratio=min_ratio,
        min_quote_volume=min_volume,
        include_conflicting=include_conflicting,
        total_eligible_symbols=page["total_eligible_symbols"],
        scanned_symbols=page["scanned_symbols"],
        quality_counts=page["quality_counts"],
        next_offset=page["next_offset"],
        ranking_scope="candidate_page",
        current_confirmation_symbols=page["matched_symbols"],
        exclusion_counts=dict(excluded),
        qualified_symbols=len(ranked),
        candidates=ranked[:limit],
        note="Puan deneysel kanıt sıralamasıdır; yükseliş veya başarı olasılığı değildir.",
    )
