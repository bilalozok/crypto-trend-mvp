"""Owner-only paired statistics, with outcome-independent overlap exclusion."""

from collections import defaultdict

from sqlalchemy import select

from app.db.models.candidate_outcome import CandidateOutcome
from app.db.models.candidate_scan import CandidateScan
from app.services.candidate_outcomes import HORIZONS, statistics
from app.services.formations import BAR, timestamp


def report(db, owner, stamp, days):
    scans = db.scalars(
        select(CandidateScan)
        .where(
            CandidateScan.account_id == owner,
            CandidateScan.created_ms >= stamp - days * 86400000,
            CandidateScan.created_ms <= stamp,
        )
        .order_by(CandidateScan.created_ms, CandidateScan.id)
        .limit(1001)
    ).all()
    if len(scans) > 1000:
        raise ValueError("Dönemde 1000 taramadan fazlası var; daha kısa dönem seç.")
    stored = {
        (o.scan_id, o.symbol, o.horizon_bars): o.payload
        for o in db.scalars(
            select(CandidateOutcome).where(CandidateOutcome.scan_id.in_([s.id for s in scans]))
        )
    }
    records, raw, legacy = [], 0, 0
    for scan in scans:
        for c in scan.payload["candidates"]:
            raw += 1
            entry = scan.payload.get("evaluation_entry_ms")
            if entry is None:
                legacy += 1
                continue
            records.append((entry, scan.id, scan.rule_hash, c))
    records.sort(key=lambda r: (r[0], r[1], r[3]["symbol"]))
    indicator_records = []
    last, groups, overlap = {}, defaultdict(list), 0
    for entry, scan_id, rule, c in records:
        key = (rule, c["symbol"])
        if key in last and entry < last[key] + 96 * BAR:
            overlap += 1
            continue
        last[key] = entry
        outcomes = [stored.get((scan_id, c["symbol"], h)) for h in HORIZONS]
        groups[(rule, c["primary_pattern"]["name"])].append(outcomes)
        indicator_records.append((rule, c, outcomes))
    output = []
    for (rule, pattern), rows in sorted(groups.items()):
        paired = [r for r in rows if all(o and o.get("status") == "complete" for o in r)]
        horizons = []
        for i, bars in enumerate(HORIZONS):
            stats = statistics([r[i] for r in paired])
            avg, med = stats["mean_net_return_pct"], stats["median_net_return_pct"]
            if avg is None:
                comment = "Dört geçerli sonuç saklanmadan karşılaştırma yapılamaz."
            elif avg > 0 and med <= 0:
                comment = (
                    "Ortalama pozitif; medyan pozitif değil. Büyük kazançlar ortalamayı etkiliyor."
                )
            elif avg <= 0:
                comment = (
                    "Ortalama net pozitif değil; bu örneklem maliyet sonrası avantaj göstermiyor."
                )
            else:
                comment = "Ortalama ve medyan pozitif; bu örneklemde olumlu sonuç var."
            if paired:
                comment += f" Örneklem: {len(paired)}; yeni veriyle tekrar değerlendir."
                if len(paired) < 30:
                    comment += " Az sayıda sonuç var; güçlü bir genelleme yapılamaz."
            horizons.append(dict(hours=bars // 4, comment=comment, **stats))
        output.append(
            dict(
                rule_hash=rule,
                pattern=pattern,
                retained=len(rows),
                paired=len(paired),
                incomplete=len(rows) - len(paired),
                horizons=horizons,
            )
        )
    from app.services.candidate_indicator_study import study

    return dict(
        indicator_study=study(indicator_records),
        version="candidate_paired_study_v1",
        as_of=timestamp(stamp).isoformat(),
        days=days,
        scans=len(scans),
        raw_candidates=raw,
        legacy_excluded=legacy,
        overlap_excluded=overlap,
        groups=output,
        note="Saklanmış sonuçlar kullanılır; eksik sonuçlar sıfır getiri sayılmaz. "
        "Kural sürümleri ve formasyonlar ayrı değerlendirilir.",
    )
