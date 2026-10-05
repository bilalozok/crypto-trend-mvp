"""Provision private accounts directly, never through a public signup endpoint."""

import argparse
import getpass
import re

from app.db.session import SessionLocal, engine
from app.services.account_auth import create_account
from app.services.binance_collection import now_ms


def main():
    parser = argparse.ArgumentParser(description="Özel alış hesabı oluştur veya parolasını yenile.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--reset-password", action="store_true")
    args = parser.parse_args()
    username = args.username.lower()
    if not re.fullmatch(r"[a-z0-9_.-]{1,32}", username):
        raise SystemExit("Kullanıcı adı: en fazla 32 harf/rakam veya _ . -")
    if engine.dialect.name != "postgresql":
        raise SystemExit("Üretim hesabı için PostgreSQL bağlantısı gerekli.")
    password = getpass.getpass("Parola (en az 15 karakter): ")
    if not 15 <= len(password) <= 128:
        raise SystemExit("Parola 15–128 karakter olmalı.")
    if password != getpass.getpass("Parolayı tekrar gir: "):
        raise SystemExit("Parolalar eşleşmiyor.")
    try:
        with SessionLocal() as db:
            create_account(db, username, password, now_ms(), reset=args.reset_password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    except Exception as exc:
        raise SystemExit("Hesap işlemi tamamlanamadı: " + type(exc).__name__) from None
    print("Hesap hazır:", username)
    if args.reset_password:
        print("Eski oturumlar sonlandırıldı.")


if __name__ == "__main__":
    main()
