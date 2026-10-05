from sqlalchemy import JSON, BigInteger, Column, String

from app.db.base import Base


class FormationState(Base):
    __tablename__ = "binance_formation_states"
    symbol = Column(String(64), primary_key=True)
    pattern = Column(String(64), primary_key=True)
    candle_close_ms = Column(BigInteger, nullable=False)
    signature = Column(JSON, nullable=False)


class FormationEvent(Base):
    __tablename__ = "binance_formation_events"
    symbol = Column(String(64), primary_key=True)
    pattern = Column(String(64), primary_key=True)
    candle_close_ms = Column(BigInteger, primary_key=True)
    observed_ms = Column(BigInteger, nullable=False)
    event_type = Column(String(24), nullable=False)
    previous = Column(JSON, nullable=True)
    current = Column(JSON, nullable=False)
    method_version = Column(String(64), nullable=False)
