from sqlalchemy import JSON, Column, ForeignKey, Integer, String

from app.db.base import Base


class CandidateOutcome(Base):
    __tablename__ = "private_candidate_outcomes"
    scan_id = Column(
        String(36), ForeignKey("private_candidate_scans.id", ondelete="CASCADE"), primary_key=True
    )
    symbol = Column(String(64), primary_key=True)
    horizon_bars = Column(Integer, primary_key=True)
    payload = Column(JSON, nullable=False)
