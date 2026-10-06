"""Daily real observations after 09:00 Turkey time; no backdated snapshots."""

import logging
import os
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from app.db.models.account import Account, Purchase
from app.db.models.portfolio_snapshot import PortfolioSnapshot
from app.services import portfolio_snapshots, portfolio_technical

logger = logging.getLogger(__name__)


def enabled():
    return os.getenv("PORTFOLIO_DAILY_ENABLED", "false").lower() == "true"


def schedule(stamp):
    local = datetime.fromtimestamp(stamp / 1000, UTC).astimezone(portfolio_technical.TR)
    planned = local.replace(hour=9, minute=0, second=0, microsecond=0)
    return local.date().isoformat(), planned, local >= planned


def request_id(owner, symbol, day):
    return str(
        uuid5(NAMESPACE_URL, "crypto-trend/portfolio-auto/" + owner + "/" + symbol + "/" + day)
    )


def status(db, owner, symbol, stamp, current=None):
    day, planned, due = schedule(stamp)
    row = db.scalar(
        select(PortfolioSnapshot).where(
            PortfolioSnapshot.account_id == owner,
            PortfolioSnapshot.request_id == request_id(owner, symbol, day),
        )
    )
    if row:
        note = "Bugünün otomatik gözlemi kaydedildi. Saatli kayıtlardan açabilirsin."
        state = "saved"
        missing = [h["name"] for h in row.payload.get("horizons", []) if h["status"] != "ready"]
        if missing:
            note += " Kayıtta verisi hazır olmayan vadeler: " + ", ".join(missing) + "."
    elif not enabled():
        note, state = "Otomatik günlük kayıt kapalı.", "disabled"
    elif not due:
        note, state = "Türkiye 09:00 sonrası worker çalışması bekleniyor.", "scheduled"
    elif current is not None and current["status"] != "ready":
        note = (
            "Kısa vade verisi hazır değil ("
            + current["status"]
            + "); sonraki worker çalışmasında tekrar denenecek."
        )
        state = "not_ready"
    else:
        note = "Bugün henüz kayıt yok; worker çalışması veya hazır kapanmış veri bekleniyor."
        state = "waiting"
    return dict(
        state=state,
        note=note,
        scheduled_for=planned.isoformat(),
        observed_at=portfolio_snapshots.output(row)["observed_at"] if row else None,
    )


def run_symbol(db, symbol, stamp):
    if not enabled():
        return []
    day, planned, due = schedule(stamp)
    if not due:
        return []
    owners = db.scalars(
        select(Purchase.account_id)
        .join(Account, Account.id == Purchase.account_id)
        .where(Purchase.symbol == symbol, Account.active.is_(True))
        .distinct()
    ).all()
    outcomes = []
    for owner in owners:
        try:
            result = portfolio_snapshots.save(
                db,
                owner,
                symbol,
                request_id(owner, symbol, day),
                stamp,
                metadata=dict(
                    source="automatic", scheduled_day=day, scheduled_for=planned.isoformat()
                ),
            )
            state = "saved" if result["created"] else result.get("reason", "already_saved")
            outcomes.append(state)
            logger.info("portfolio_auto_complete symbol=%s day=%s state=%s", symbol, day, state)
        except Exception as exc:
            db.rollback()
            outcomes.append("error")
            logger.error("portfolio_auto_failed symbol=%s error=%s", symbol, type(exc).__name__)
    return outcomes
