from sqlalchemy import JSON, BigInteger, Column, ForeignKey, Index, String, UniqueConstraint

from app.db.base import Base


class CandidateScan(Base):
    __tablename__ = "private_candidate_scans"
    id = Column(String(36), primary_key=True)
    account_id = Column(String(36), ForeignKey("private_accounts.id"), nullable=False)
    request_id = Column(String(36), nullable=False)
    created_ms = Column(BigInteger, nullable=False)
    rule_hash = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    __table_args__ = (
        UniqueConstraint("account_id", "request_id", name="uq_candidate_scan_request"),
        Index("ix_candidate_scan_owner_time", "account_id", "created_ms"),
    )
