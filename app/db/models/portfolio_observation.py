from sqlalchemy import JSON, BigInteger, Column, ForeignKey, Index, String, UniqueConstraint

from app.db.base import Base


class PortfolioObservation(Base):
    __tablename__ = "private_portfolio_observations"
    id = Column(String(36), primary_key=True)
    account_id = Column(String(36), ForeignKey("private_accounts.id"), nullable=False)
    symbol = Column(String(64), nullable=False)
    local_day = Column(String(10), nullable=False)
    observed_ms = Column(BigInteger, nullable=False)
    method = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    __table_args__ = (
        UniqueConstraint("account_id", "symbol", "local_day", name="uq_portfolio_owner_day"),
        Index("ix_portfolio_owner_symbol_time", "account_id", "symbol", "observed_ms"),
    )
