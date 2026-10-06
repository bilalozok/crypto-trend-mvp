"""Bounded rotating settlement batches after market collection."""

import logging
import os

from sqlalchemy import func, select

from app.db.models.account import Account
from app.db.models.candidate_outcome import CandidateOutcome
from app.db.models.candidate_scan import CandidateScan
from app.services import candidate_outcomes
from app.services.formations import BAR

logger = logging.getLogger(__name__)
BATCH = 10


def settle_due(db, stamp):
    if os.getenv("CANDIDATE_OUTCOMES_ENABLED", "false").lower() != "true":
        return 0
    entry = CandidateScan.payload["evaluation_entry_ms"].as_integer()
    length = func.json_array_length(CandidateScan.payload["candidates"])
    completed = (
        select(func.count())
        .select_from(CandidateOutcome)
        .where(CandidateOutcome.scan_id == CandidateScan.id)
        .correlate(CandidateScan)
        .scalar_subquery()
    )
    query = (
        select(CandidateScan.id)
        .join(Account, Account.id == CandidateScan.account_id)
        .where(Account.active.is_(True), entry + 4 * BAR <= stamp, completed < length * 4)
        .order_by(CandidateScan.created_ms, CandidateScan.id)
    )
    count = db.scalar(select(func.count()).select_from(query.subquery()))
    if not count:
        return 0
    offset = (stamp // BAR * BATCH) % count
    ids = list(db.scalars(query.offset(offset).limit(BATCH)))
    if len(ids) < min(count, BATCH):
        ids.extend(db.scalars(query.limit(min(count, BATCH) - len(ids))))
    processed = 0
    for scan_id in ids:
        try:
            scan = db.get(CandidateScan, scan_id)
            if scan is None:
                continue
            candidate_outcomes.results(db, scan, stamp, persist=True)
            processed += 1
        except Exception as exc:
            db.rollback()
            logger.error("candidate_auto_scan_failed error=%s", type(exc).__name__)
    logger.info("candidate_auto_complete eligible=%s processed=%s", count, processed)
    return processed
