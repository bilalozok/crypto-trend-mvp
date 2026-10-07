"""Owner-scoped archive search with database filtering before pagination."""

from sqlalchemy import JSON, BigInteger, cast, exists, func, literal, select

from app.db.models.candidate_scan import CandidateScan
from app.services import candidate_archive
from app.services.formations import BAR


def listing(db, owner, stamp, offset=0, symbol=None, kind="all", start=None, end=None):
    entry = cast(CandidateScan.payload["evaluation_entry_ms"].as_string(), BigInteger)
    query = select(CandidateScan).where(CandidateScan.account_id == owner)
    if start is not None:
        query = query.where(CandidateScan.created_ms >= start)
    if end is not None:
        query = query.where(CandidateScan.created_ms < end)
    if kind == "legacy":
        query = query.where(entry.is_(None))
    elif kind == "tracked":
        query = query.where(entry.is_not(None))
    elif kind == "open":
        query = query.where(entry <= stamp, entry + 96 * BAR >= stamp)
    elif kind == "ended":
        query = query.where(entry + 96 * BAR < stamp)
    if symbol:
        if db.bind.dialect.name == "postgresql":
            items = func.json_array_elements(CandidateScan.payload["candidates"]).table_valued(
                "value"
            )
            match = cast(items.c.value, JSON)["symbol"].as_string() == symbol.upper()
        else:
            items = func.json_each(CandidateScan.payload, "$.candidates").table_valued("value")
            match = func.json_extract(items.c.value, "$.symbol") == symbol.upper()
        query = query.where(exists(select(literal(1)).select_from(items).where(match)))
    rows = db.scalars(
        query.order_by(CandidateScan.created_ms.desc(), CandidateScan.id.desc())
        .offset(offset)
        .limit(21)
    ).all()
    summaries = []
    for row in rows[:20]:
        item = candidate_archive.summary(row)
        beginning = row.payload.get("evaluation_entry_ms")
        if beginning is None:
            state = "legacy"
        elif stamp < beginning:
            state = "before_entry"
        elif stamp <= beginning + 96 * BAR:
            state = "open"
        else:
            state = "ended"
        item["tracking_state"] = state
        item["tracking_label"] = {
            "legacy": "Eski · Takip başlangıcı yok",
            "before_entry": "Giriş zamanı bekleniyor",
            "open": "24 saatlik takip penceresi açık",
            "ended": "Takip penceresi sona erdi",
        }[state]
        summaries.append(item)
    return dict(scans=summaries, next_offset=offset + 20 if len(rows) > 20 else None)
