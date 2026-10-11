from sqlalchemy import JSON, BigInteger, Column, Float, ForeignKey, Index, String

from app.db.base import Base


class Simulation(Base):
    __tablename__ = "private_simulations"
    id = Column(String(36), primary_key=True)
    account_id = Column(String(36), ForeignKey("private_accounts.id"), nullable=False)
    symbol = Column(String(64), nullable=False)
    created_ms = Column(BigInteger, nullable=False)
    state = Column(String(20), nullable=False)
    source = Column(String(24), nullable=False)
    mode = Column(String(24), nullable=False)
    entry_interval = Column(String(3), nullable=False, default="15m", server_default="15m")
    exit_interval = Column(String(3), nullable=False, default="15m", server_default="15m")
    entry_ms = Column(BigInteger, nullable=False)
    exit_ms = Column(BigInteger(), nullable=True)
    entry_price = Column(String(128), nullable=True)
    exit_price = Column(String(128), nullable=True)
    quantity = Column(String(128), nullable=True)
    budget = Column(String(128), nullable=False, default="100", server_default="100")
    purchase_id = Column(String(36), nullable=True)
    request = Column(JSON, nullable=False)
    selection = Column(JSON, nullable=False)
    entry_context = Column(JSON, nullable=True)
    sale_context = Column(JSON, nullable=True)
    exit_context = Column(JSON, nullable=True)
    observations = Column(JSON, nullable=False)
    __table_args__ = (Index("ix_simulation_owner_time", "account_id", "created_ms", "id"),)


class SimulationCandle(Base):
    __tablename__ = "simulation_candles_5m"
    symbol = Column(String(64), primary_key=True)
    open_time = Column(BigInteger, primary_key=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)
