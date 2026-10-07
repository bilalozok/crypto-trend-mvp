"""Private accounts: slow password hashes and revocable HTTPS cookie sessions."""

import hashlib
import hmac
import secrets
from threading import BoundedSemaphore
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, select

from app.db.models.account import Account, AccountSession, LoginLimit
from app.services.binance_collection import insert_for

COOKIE = "__Host-crypto_session"
SESSION_MS = 12 * 60 * 60 * 1000
SCRYPT_N = 2**17
HASH_SLOTS = BoundedSemaphore(1)
DUMMY_HASH = "scrypt$" + "00" * 16 + "$" + "00" * 32


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def password_hash(password):
    salt = secrets.token_bytes(16)
    result = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=8, p=1, dklen=32, maxmem=256 * 1024 * 1024
    )
    return "scrypt$" + salt.hex() + "$" + result.hex()


def verify_password(password, encoded):
    try:
        method, salt, expected = encoded.split("$")
        if method != "scrypt" or len(salt) != 32 or len(expected) != 64:
            return False
        result = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=SCRYPT_N,
            r=8,
            p=1,
            dklen=32,
            maxmem=256 * 1024 * 1024,
        )
        return hmac.compare_digest(result.hex(), expected)
    except (ValueError, TypeError):
        return False


def create_account(db, username, password, stamp, reset=False):
    account = db.scalar(select(Account).where(Account.username == username))
    if account and not reset:
        raise ValueError("Kullanıcı zaten var; yeni hesap oluşturulmadı.")
    if account:
        account.password_hash = password_hash(password)
        db.execute(delete(AccountSession).where(AccountSession.account_id == account.id))
    else:
        account = Account(
            id=str(uuid4()),
            username=username,
            password_hash=password_hash(password),
            active=True,
            created_ms=stamp,
        )
        db.add(account)
    db.commit()


def reserve_login(db, username, stamp):
    # Database limits work across web processes; unknown usernames share the global limit.
    for key, maximum in ((digest("global-login"), 30), (digest("user:" + username), 6)):
        statement = insert_for(db, LoginLimit).values(key=key, window_ms=stamp, attempts=0)
        db.execute(statement.on_conflict_do_nothing(index_elements=["key"]))
        row = db.scalar(select(LoginLimit).where(LoginLimit.key == key).with_for_update())
        if stamp - row.window_ms >= 15 * 60 * 1000:
            row.window_ms, row.attempts = stamp, 0
        if row.attempts >= maximum:
            db.rollback()
            raise HTTPException(
                status_code=429, detail="Çok fazla giriş denemesi; 15 dakika sonra dene."
            )
        row.attempts += 1
    db.commit()


def login(db, username, password, stamp):
    reserve_login(db, username, stamp)
    # Serialize password verification/session creation with administrator resets.
    account = db.scalar(select(Account).where(Account.username == username).with_for_update())
    if not HASH_SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="Giriş işlemi meşgul; biraz sonra tekrar dene.")
    try:
        valid = verify_password(password, account.password_hash if account else DUMMY_HASH)
    finally:
        HASH_SLOTS.release()
    if not valid or account is None or not account.active:
        raise HTTPException(status_code=401, detail="Kullanıcı adı veya parola yanlış.")
    # Remove expired sessions without touching another user's current sessions.
    db.execute(delete(AccountSession).where(AccountSession.expires_ms <= stamp))
    token = secrets.token_urlsafe(32)
    db.add(
        AccountSession(
            token_hash=digest(token), account_id=account.id, expires_ms=stamp + SESSION_MS
        )
    )
    db.commit()
    return token


def require_account(db, token, stamp):
    if not token or len(token) > 100:
        raise HTTPException(status_code=401, detail="Önce giriş yap.")
    session = db.get(AccountSession, digest(token))
    if session is None or session.expires_ms <= stamp:
        raise HTTPException(status_code=401, detail="Oturum sona ermiş; tekrar giriş yap.")
    account = db.get(Account, session.account_id)
    if account is None or not account.active:
        raise HTTPException(status_code=401, detail="Önce giriş yap.")
    return account


def csrf_token(token):
    return digest("csrf:" + token)


def require_csrf(token, provided):
    if not provided or not token or not hmac.compare_digest(csrf_token(token), provided):
        raise HTTPException(status_code=403, detail="İstek doğrulanamadı; sayfayı yenile.")
