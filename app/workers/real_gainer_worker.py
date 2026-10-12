"""Daily retrospective archival from stored data; no exchange requests or orders."""

import logging

from app.workers.scheduler import load_settings


def main():
    logging.basicConfig(level=logging.INFO)
    load_settings()
    from app.db.session import SessionLocal, engine
    from app.services.binance_collection import now_ms
    from app.services.real_gainers import refresh

    try:
        with SessionLocal() as db:
            saved = refresh(db, now_ms())
            db.commit()
        logging.info("real_gainer_study_complete new_revisions=%s", saved)
        return 0
    except Exception:
        logging.exception("real_gainer_study_failed")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
