from sqlalchemy import JSON, BigInteger, Column, ForeignKey, Index, String, UniqueConstraint

from app.db.base import Base


class PortfolioSnapshot(Base):
    __tablename__ = "private_portfolio_snapshots"
    id = Column(String(36), primary_key=True)
    account_id = Column(String(36), ForeignKey("private_accounts.id"), nullable=False)
    symbol = Column(String(64), nullable=False)
    request_id = Column(String(36), nullable=False)
    observed_ms = Column(BigInteger, nullable=False)
    method = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    comparison = Column(JSON, nullable=False)
    __table_args__ = (
        UniqueConstraint("account_id", "request_id", name="uq_portfolio_snapshot_request"),
        Index("ix_portfolio_snapshot_time", "account_id", "symbol", "observed_ms"),
    )
