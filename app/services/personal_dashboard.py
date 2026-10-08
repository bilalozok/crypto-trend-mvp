"""Read-only, owner-scoped summary; no market fetches or observation writes."""

from sqlalchemy import select

from app.db.models.account import Purchase
from app.db.models.candidate_scan import CandidateScan
from app.services import candidate_tracking, formation_early, portfolio_technical
from app.services.formations import timestamp


def summary(db, account_id, stamp, offset=0):
    symbols = db.scalars(
        select(Purchase.symbol)
        .where(Purchase.account_id == account_id)
        .distinct()
        .order_by(Purchase.symbol)
    ).all()
    selected = symbols[offset : offset + 10]
    coins = [portfolio_technical.technical(db, symbol, stamp) for symbol in selected]
    scan = db.scalar(
        select(CandidateScan)
        .where(CandidateScan.account_id == account_id, CandidateScan.created_ms <= stamp)
        .order_by(CandidateScan.created_ms.desc(), CandidateScan.id.desc())
        .limit(1)
    )
    tracking = candidate_tracking.overview(db, scan, stamp) if scan else None
    alerts = early_summary(db, account_id, stamp)["alerts"]
    attention = []
    for coin in coins:
        for horizon in coin["horizons"]:
            if horizon["status"] != "ready":
                reason = "Veri hazır değil"
            elif horizon["assessment"] in ("bearish_setup", "conflicting"):
                reason = horizon["label"]
            else:
                continue
            attention.append(dict(symbol=coin["symbol"], horizon=horizon["name"], reason=reason))
    return dict(
        checked_at=timestamp(stamp),
        portfolio_total=len(symbols),
        portfolio=coins,
        attention=attention,
        offset=offset,
        next_offset=offset + 10 if offset + 10 < len(symbols) else None,
        scan_id=scan.id if scan else None,
        scan_created_at=timestamp(scan.created_ms) if scan else None,
        tracking=tracking,
        alerts=alerts,
        alert_scope="Alış kaydı bulunan coinler ve son kayıtlı taramanın adayları.",
    )


def early_summary(db, account_id, stamp):
    symbols = set(
        db.scalars(select(Purchase.symbol).where(Purchase.account_id == account_id)).all()
    )
    scan = db.scalar(
        select(CandidateScan)
        .where(CandidateScan.account_id == account_id, CandidateScan.created_ms <= stamp)
        .order_by(CandidateScan.created_ms.desc(), CandidateScan.id.desc())
        .limit(1)
    )
    if scan:
        symbols.update(c["symbol"] for c in scan.payload["candidates"])
    early = formation_early.listing(db, stamp)
    return dict(
        checked_at=early["checked_at"],
        alerts=[row for row in early["alerts"] if row["symbol"] in symbols],
    )
