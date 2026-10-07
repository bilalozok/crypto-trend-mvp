"""Higher-timeframe feed eligibility for active saved candidate windows."""

import os

from sqlalchemy import BigInteger, cast, select

from app.db.models.account import Account
from app.db.models.binance_spot import BinanceSpotSymbol
from app.db.models.candidate_scan import CandidateScan
from app.services.formations import BAR


def tracked_symbols(db, stamp):
    if os.getenv("CANDIDATE_TIMEFRAMES_ENABLED", "false").lower() != "true":
        return set()
    entry = cast(CandidateScan.payload["evaluation_entry_ms"].as_string(), BigInteger)
    scans = db.scalars(
        select(CandidateScan.payload)
        .join(Account, Account.id == CandidateScan.account_id)
        .where(Account.active.is_(True), entry <= stamp, entry + 96 * BAR >= stamp)
        .execution_options(yield_per=100)
    )
    symbols = {candidate["symbol"] for payload in scans for candidate in payload["candidates"]}
    if not symbols:
        return set()
    return set(
        db.scalars(
            select(BinanceSpotSymbol.symbol).where(
                BinanceSpotSymbol.active.is_(True), BinanceSpotSymbol.symbol.in_(symbols)
            )
        )
    )
