"""Read stored tracking evidence without inferring worker health or replaying history."""

from sqlalchemy import func, select

from app.db.models.candidate_observation import CandidateObservation
from app.db.models.candidate_outcome import CandidateOutcome
from app.services.formations import BAR, timestamp

HORIZONS = (4, 8, 16, 96)


def overview(db, scan, stamp):
    candidates = scan.payload["candidates"]
    entry = scan.payload.get("evaluation_entry_ms")
    end = entry + 96 * BAR if entry is not None else None
    ranked = (
        select(
            CandidateObservation.symbol,
            CandidateObservation.close_ms,
            func.row_number()
            .over(
                partition_by=CandidateObservation.symbol,
                order_by=CandidateObservation.close_ms.desc(),
            )
            .label("rank"),
        )
        .where(CandidateObservation.scan_id == scan.id, CandidateObservation.close_ms <= stamp)
        .subquery()
    )
    latest = db.scalars(
        select(CandidateObservation)
        .join(
            ranked,
            (CandidateObservation.symbol == ranked.c.symbol)
            & (CandidateObservation.close_ms == ranked.c.close_ms),
        )
        .where(CandidateObservation.scan_id == scan.id, ranked.c.rank == 1)
    ).all()
    history = db.scalars(
        select(CandidateObservation)
        .where(CandidateObservation.scan_id == scan.id, CandidateObservation.close_ms <= stamp)
        .order_by(CandidateObservation.close_ms)
    ).all()
    timelines = {}
    for observation in history:
        timelines.setdefault(observation.symbol, []).append(observation)
    by_symbol = {r.symbol: r for r in latest}
    stored = {
        (r.symbol, r.horizon_bars)
        for r in db.scalars(
            select(CandidateOutcome).where(CandidateOutcome.scan_id == scan.id)
        ).all()
        if r.payload.get("status") == "complete"
    }
    if entry is None:
        state, note = "legacy", "Eski taramada takip başlangıcı yok; otomatik gözlem beklenmez."
    elif not candidates:
        state, note = "no_candidates", "Tarama aday içermiyor; takip edilecek coin yok."
    elif stamp < entry:
        state, note = "before_entry", "Planlanan varsayımsal giriş zamanı henüz gelmedi."
    elif stamp < entry + BAR:
        state, note = (
            "first_close_pending",
            "Girişten sonraki ilk 15 dakikalık mum kapanışı bekleniyor.",
        )
    elif stamp > end:
        state, note = (
            "ended",
            "24 saatlik gözlem penceresi sona erdi; saklanmış kayıtlar gösteriliyor.",
        )
    else:
        state, note = (
            "window_open",
            "24 saatlik gözlem penceresi açık; saklanmış kayıtlar gösteriliyor.",
        )
    if state in ("window_open", "ended") and not latest:
        note += " Henüz otomatik gözlem kaydı yok. Geçmiş boşluklar sonradan doldurulmaz."
    expected = min(stamp // BAR * BAR, end) if entry is not None else None
    rows = []
    for candidate in candidates:
        symbol = candidate["symbol"]
        observation = by_symbol.get(symbol)
        payload = observation.payload if observation else {}
        qualified = payload.get("qualified")
        usable = payload.get("status") == "ready" and isinstance(qualified, bool)
        if usable:
            label = "Adaylık korunuyor" if qualified else "Son gözlemde aday koşulları yok"
        else:
            label = "Değerlendirilemedi" if observation else "Gözlem yok"
        timeline = timelines.get(symbol, [])
        previous = None
        changed = None
        regained = False
        for point in timeline:
            value = point.payload.get("qualified")
            if point.payload.get("status") != "ready" or not isinstance(value, bool):
                previous = None
                regained = False
                continue
            if previous is not None and value != previous:
                changed = point.close_ms
                regained = value
            previous = value
        group = "unassessed"
        if usable:
            group = "regained" if qualified and regained else "retained" if qualified else "absent"
        group_label = {
            "retained": "Son gözlemde aday",
            "regained": "Yeniden adaylık gözlendi",
            "absent": "Son gözlemde aday koşulları yok",
            "unassessed": "Değerlendirilemedi" if observation else "Gözlem yok",
        }[group]
        completed, pending, missing, legacy = 0, 0, 0, 0
        for bars in HORIZONS:
            if (symbol, bars) in stored:
                completed += 1
            elif entry is None:
                legacy += 1
            elif stamp < entry + bars * BAR:
                pending += 1
            else:
                missing += 1
        rows.append(
            dict(
                symbol=symbol,
                label=label,
                change_group=group,
                change_label=group_label,
                observation_count=len(timeline),
                first_close_time=timestamp(timeline[0].close_ms).isoformat() if timeline else None,
                last_change_close_time=timestamp(changed).isoformat() if changed else None,
                last_close_price=payload.get("close_price") if usable else None,
                qualified=qualified if usable else None,
                last_close_time=(
                    timestamp(observation.close_ms).isoformat() if observation else None
                ),
                observed_at=payload.get("observed_at"),
                reason=payload.get("reason", note),
                latest_expected_close_recorded=observation is not None
                and observation.close_ms == expected,
                stored_completed=completed,
                time_pending=pending,
                due_not_stored=missing,
                legacy_unavailable=legacy,
            )
        )
    last = max((r.close_ms for r in latest), default=None)
    return dict(
        scan_id=scan.id,
        as_of=timestamp(stamp).isoformat(),
        state=state,
        note=note,
        entry_time=timestamp(entry).isoformat() if entry is not None else None,
        window_end=timestamp(end).isoformat() if end is not None else None,
        last_observation_close=timestamp(last).isoformat() if last is not None else None,
        candidates=rows,
        change_counts={
            key: sum(r["change_group"] == key for r in rows)
            for key in ("retained", "absent", "regained", "unassessed")
        },
        totals={
            key: sum(r[key] for r in rows)
            for key in ("stored_completed", "time_pending", "due_not_stored", "legacy_unavailable")
        },
        limitation=(
            "Bu ekran yalnızca saklanmış kayıtları okur. Worker etkinliği ve sağlığı "
            "bu kayıtlarla kesin olarak doğrulanamaz. Süresi dolmuş ama saklanmamış sonuç, "
            "sıfır getiri veya kesin veri hatası değildir; sonuçları hesapla/yenile ile "
            "kontrol edilebilir."
        ),
    )
