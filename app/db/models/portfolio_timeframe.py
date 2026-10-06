from sqlalchemy import BigInteger, CheckConstraint, Column, Float, String

from app.db.base import Base


class PortfolioCandle(Base):
    __tablename__ = "portfolio_market_candles"
    symbol = Column(String(64), primary_key=True)
    interval = Column(String(3), primary_key=True)
    open_time = Column(BigInteger, primary_key=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
    __table_args__ = (CheckConstraint("interval IN ('4h', '1d')", name="ck_portfolio_interval"),)


class PortfolioFeed(Base):
    __tablename__ = "portfolio_market_feeds"
    symbol = Column(String(64), primary_key=True)
    interval = Column(String(3), primary_key=True)
    last_attempt_ms = Column(BigInteger, nullable=False)
    last_success_ms = Column(BigInteger, nullable=True)
    last_error = Column(String(100), nullable=True)
    __table_args__ = (
        CheckConstraint("interval IN ('4h', '1d')", name="ck_portfolio_feed_interval"),
    )
