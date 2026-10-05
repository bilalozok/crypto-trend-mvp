from sqlalchemy import BigInteger, Boolean, Column, ForeignKey, Index, Integer, String

from app.db.base import Base


class Account(Base):
    __tablename__ = "private_accounts"
    id = Column(String(36), primary_key=True)
    username = Column(String(32), nullable=False, unique=True)
    password_hash = Column(String(192), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    created_ms = Column(BigInteger, nullable=False)


class AccountSession(Base):
    __tablename__ = "private_account_sessions"
    token_hash = Column(String(64), primary_key=True)
    account_id = Column(String(36), ForeignKey("private_accounts.id"), nullable=False, index=True)
    expires_ms = Column(BigInteger, nullable=False)


class LoginLimit(Base):
    __tablename__ = "private_login_limits"
    key = Column(String(64), primary_key=True)
    window_ms = Column(BigInteger, nullable=False)
    attempts = Column(Integer, nullable=False)


class Purchase(Base):
    __tablename__ = "private_purchases"
    id = Column(String(36), primary_key=True)
    account_id = Column(String(36), ForeignKey("private_accounts.id"), nullable=False)
    symbol = Column(String(64), nullable=False)
    purchased_ms = Column(BigInteger, nullable=False)
    currency = Column(String(4), nullable=False)
    unit_price = Column(String(64), nullable=False)
    quantity = Column(String(64), nullable=False)
    fee = Column(String(64), nullable=False)
    note = Column(String(1000), nullable=False)
    created_ms = Column(BigInteger, nullable=False)
    __table_args__ = (Index("ix_private_purchases_owner_time", "account_id", "purchased_ms"),)
