from sqlalchemy import JSON, BigInteger, Column, ForeignKey, String

from app.db.base import Base


class CandidateObservation(Base):
    __tablename__ = "private_candidate_observations"
    scan_id = Column(
        String(36), ForeignKey("private_candidate_scans.id", ondelete="CASCADE"), primary_key=True
    )
    symbol = Column(String(64), primary_key=True)
    close_ms = Column(BigInteger, primary_key=True)
    payload = Column(JSON, nullable=False)
