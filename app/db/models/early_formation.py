from sqlalchemy import JSON, BigInteger, Boolean, Column, Index, String

from app.db.base import Base


class EarlyFormation(Base):
    __tablename__ = "early_formation_measurements"

    symbol = Column(String(64), primary_key=True)
    rule_hash = Column(String(64), primary_key=True)
    pattern = Column(String(64), primary_key=True)
    close_ms = Column(BigInteger, primary_key=True)
    observed_ms = Column(BigInteger, nullable=False)
    entry_ms = Column(BigInteger, nullable=False)
    snapshot = Column(JSON, nullable=False)
    outcomes = Column(JSON, nullable=False)
    complete = Column(Boolean, nullable=False, default=False)

    __table_args__ = (Index("ix_early_measure_pending", "symbol", "complete"),)
