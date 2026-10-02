from sqlalchemy import BigInteger, Column, Float, Integer, String, UniqueConstraint

from app.db.base import Base


class Candle(Base):
    __tablename__ = "candles"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String, index=True, nullable=False)
    interval = Column(String, index=True, nullable=False)
    open_time = Column(BigInteger, index=True, nullable=False)  # ms epoch
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "interval",
            "open_time",
            name="uq_candles_symbol_interval_open_time",
        ),
    )
