from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, Float, String

from app.db.base import Base


class BinanceSpotSymbol(Base):
    __tablename__ = "binance_spot_symbols"

    symbol = Column(String(64), primary_key=True)
    base_asset = Column(String(64), nullable=False)
    quote_volume_24h = Column(Float, nullable=False)
    active = Column(Boolean, nullable=False)
    catalog_updated_ms = Column(BigInteger, nullable=False)
    last_attempt_ms = Column(BigInteger, nullable=True)
    last_success_ms = Column(BigInteger, nullable=True)
    last_error = Column(String(100), nullable=True)


class BinanceSpotCandle(Base):
    __tablename__ = "binance_spot_candles"

    symbol = Column(String(64), primary_key=True)
    open_time = Column(BigInteger, primary_key=True)
    interval = Column(String(3), nullable=False, default="15m")
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)

    __table_args__ = (CheckConstraint("interval = '15m'", name="ck_binance_spot_15m"),)
