from sqlalchemy import JSON, BigInteger, Column, Index, String

from app.db.base import Base


class RealGainerStudy(Base):
    __tablename__ = "real_gainer_studies"
    id = Column(String(64), primary_key=True)
    day = Column(String(10), nullable=False)
    version = Column(String(64), nullable=False)
    computed_ms = Column(BigInteger, nullable=False)
    payload = Column(JSON, nullable=False)
    __table_args__ = (Index("ix_real_gainer_day_version", "day", "version", "computed_ms"),)
