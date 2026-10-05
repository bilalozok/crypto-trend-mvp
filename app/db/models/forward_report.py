from sqlalchemy import JSON, BigInteger, Column, Index, Integer, String

from app.db.base import Base


class ForwardReport(Base):
    __tablename__ = "binance_forward_reports"

    id = Column(String(36), primary_key=True)
    created_ms = Column(BigInteger, nullable=False)
    days = Column(Integer, nullable=False)
    rule_hash = Column(String(64), nullable=False)
    signal_count = Column(Integer, nullable=False)
    payload = Column(JSON, nullable=False)

    __table_args__ = (Index("ix_forward_reports_created", "created_ms"),)
