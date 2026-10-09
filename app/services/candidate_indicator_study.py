"""Frozen 15m context and descriptive outcome subgroups; no backfill or score changes."""

import hashlib
from collections import defaultdict
from pathlib import Path

from app.services.candidate_outcomes import HORIZONS, statistics
from app.services.formations import timestamp
from app.services.indicator_confluence import summarize
from app.services.technical_indicators import analyze_rows


def version():
    digest = hashlib.sha256()
    for name in (
        "technical_indicators.py",
        "fibonacci_context.py",
        "indicator_confluence.py",
        "candidate_indicator_study.py",
    ):
        digest.update(Path(__file__).with_name(name).read_bytes())
    return "candidate_indicators15m_v1:" + digest.hexdigest()


def snapshot(rows, stamp, patterns, primary, fingerprint):
    horizon = analyze_rows(rows, "15m", stamp)
    result = dict(
        version=fingerprint, interval="15m", observed_at=timestamp(stamp), status=horizon["status"]
    )
    if horizon["status"] != "ready":
        return result
    agreement = summarize(horizon, patterns)
    chosen = next((p for p in agreement["patterns"] if p["name"] == primary["name"]), None)
    result.update(
        latest=horizon["latest"],
        previous=horizon["series"][-2],
        fibonacci=horizon["fibonacci"],
        primary_context=chosen,
        confluence_version=agreement["version"],
    )
    return result


def study(records):
    """Records already have the baseline outcome-independent overlap filter."""
    buckets = defaultdict(list)
    excluded = 0
    for rule, candidate, outcomes in records:
        saved = candidate.get("indicator_snapshot")
        if not saved or saved.get("status") != "ready" or not saved.get("primary_context"):
            excluded += 1
            continue
        key = (rule, candidate["primary_pattern"]["name"], saved["version"])
        states = {g["name"]: g["state"] for g in saved["primary_context"]["groups"]}
        buckets[key].append((states, outcomes))
    output = []
    for (rule, pattern, fingerprint), rows in sorted(buckets.items()):
        paired = [
            outcomes
            for _, outcomes in rows
            if all(o and o.get("status") == "complete" for o in outcomes)
        ]
        labels = [("Tüm gösterge kayıtlı adaylar", rows)]
        for name in ("Trend · EMA50", "Momentum · RSI / MACD"):
            for state in ("supports", "conflicts", "mixed", "neutral"):
                selected = [r for r in rows if r[0].get(name) == state]
                if selected:
                    labels.append((name + " · " + state, selected))
        for label, selected in labels:
            complete = [
                outcomes
                for _, outcomes in selected
                if all(o and o.get("status") == "complete" for o in outcomes)
            ]
            horizons = []
            for i, bars in enumerate(HORIZONS):
                baseline = statistics([o[i] for o in paired])
                stats = statistics([o[i] for o in complete])
                mean, base = stats["mean_net_return_pct"], baseline["mean_net_return_pct"]
                delta = None if mean is None or base is None else mean - base
                horizons.append(dict(hours=bars // 4, mean_difference_pp=delta, **stats))
            output.append(
                dict(
                    rule_hash=rule,
                    indicator_version=fingerprint,
                    pattern=pattern,
                    condition=label,
                    retained=len(selected),
                    paired=len(complete),
                    incomplete=len(selected) - len(complete),
                    horizons=horizons,
                )
            )
    return dict(
        groups=output,
        excluded_without_snapshot=excluded,
        note="Yalnızca tarama anında saklanan 15m koşulları ve dört tamamlanmış sonuç kullanılır. "
        "Alt gruplar aynı sürüm/formasyonun gösterge kayıtlı temel grubuyla karşılaştırılır. "
        "Gruplar örtüşebilir; fark nedensel katkı veya başarı olasılığı değildir. "
        "Eski kayıtlar sonradan doldurulmaz; küçük örneklemle kural seçilmez.",
    )
