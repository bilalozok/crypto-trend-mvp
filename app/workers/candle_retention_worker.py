"""Read-only size/retention report by default. --apply explicitly enables raw-candle pruning."""

import argparse
import json
import logging
import os

from app.workers.scheduler import load_settings


def settings_from_env():
    from app.services.candle_retention import DEFAULTS, Settings

    return Settings(
        {
            k: int(os.getenv("CANDLE_KEEP_" + k.upper() + "_DAYS", str(v)))
            for k, v in DEFAULTS.items()
        },
        batch_size=int(os.getenv("CANDLE_PRUNE_BATCH", "2000")),
        max_rows=int(os.getenv("CANDLE_PRUNE_MAX_ROWS", "100000")),
        seconds=int(os.getenv("CANDLE_PRUNE_SECONDS", "120")),
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--vacuum", action="store_true", help="Apply sonrası standart VACUUM ANALYZE"
    )
    parser.add_argument("--sizes-only", action="store_true")
    args = parser.parse_args(argv)
    if args.vacuum and not args.apply or args.sizes_only and args.apply:
        parser.error("--vacuum yalnız --apply ile kullanılabilir; --sizes-only salt okunurdur.")
    logging.basicConfig(level=logging.INFO)
    load_settings()
    from sqlalchemy import text

    from app.db.session import SessionLocal, engine
    from app.services.binance_collection import now_ms
    from app.services.candle_retention import MODELS, apply, plan, sizes, vacuum

    settings = settings_from_env()
    stamp = now_ms()
    try:
        with engine.connect() as lock:
            acquired = False
            try:
                if args.apply and engine.dialect.name == "postgresql":
                    acquired = lock.scalar(text("SELECT pg_try_advisory_lock(1910202601)"))
                    if not acquired:
                        logging.info("candle_retention_already_running")
                        return 0
                with SessionLocal() as db:
                    if engine.dialect.name == "postgresql":
                        db.execute(text("SET TRANSACTION READ ONLY"))
                        db.execute(text("SET LOCAL statement_timeout = '120s'"))
                    report = dict(
                        mode="apply" if args.apply else "read_only", sizes=sizes(db.connection())
                    )
                    if not args.sizes_only:
                        report["retention_plan"] = plan(db, stamp, settings)
                print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
                if args.apply:
                    result = apply(SessionLocal, stamp, settings)
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                    if args.vacuum:
                        vacuum(
                            engine,
                            [
                                MODELS[i].__tablename__
                                for i, n in result["by_interval"].items()
                                if n
                            ],
                        )
                    logging.info(
                        "candle_retention_complete deleted=%s limited=%s",
                        result["deleted_rows"],
                        result["limit_reached"],
                    )
                return 0
            finally:
                if acquired:
                    lock.execute(text("SELECT pg_advisory_unlock(1910202601)"))
    except Exception as exc:
        # Do not print credentials or SQL parameters containing personal data.
        origin = getattr(exc, "orig", None)
        logging.error(
            "candle_retention_failed type=%s sqlstate=%s",
            type(exc).__name__,
            getattr(origin, "sqlstate", None),
        )
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
