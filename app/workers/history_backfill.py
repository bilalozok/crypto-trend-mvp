"""Manual, finite 30-day backfill with a fixed cohort and cutoff."""

import argparse
import json
import os
from datetime import UTC, datetime

from sqlalchemy.engine import make_url

DEFAULT_SYMBOLS = (
    "BTCUSDT,ETHUSDT,SOLUSDT,ZECUSDT,XRPUSDT,SUIUSDT,NEARUSDT,BNBUSDT,"
    "DOGEUSDT,ADAUSDT,QNTUSDT,PUMPUSDT,FETUSDT,WLDUSDT,ENAUSDT,SANDUSDT,"
    "MUBARAKUSDT,STRKUSDT,LTCUSDT,BEAMXUSDT"
)
BAR = 900_000


def plan(symbols, days, end_text, stamp):
    symbols = list(dict.fromkeys(s.strip().upper() for s in symbols.split(",")))
    if (
        not symbols
        or len(symbols) > 20
        or any(not s.isalnum() or not s.endswith("USDT") or len(s) > 30 for s in symbols)
    ):
        raise ValueError("Provide 1 to 20 USDT symbols")
    if not 1 <= days <= 90:
        raise ValueError("Days must be between 1 and 90")
    if end_text:
        parsed = datetime.fromisoformat(end_text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("End time must include a timezone")
        end = int(parsed.timestamp() * 1000)
    else:
        end = stamp // BAR * BAR
    if end % BAR or end > stamp // BAR * BAR:
        raise ValueError("End must be a closed 15m boundary")
    evaluation_start = end - days * 96 * BAR
    fetch_start = evaluation_start - 200 * BAR
    if fetch_start < 0:
        raise ValueError("Invalid historical start")
    return symbols, fetch_start, evaluation_start, end


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--end", help="Exclusive UTC cutoff, e.g. 2026-10-05T01:00:00Z")
    parser.add_argument("--execute", action="store_true", help="Write candles to PostgreSQL")
    args = parser.parse_args()
    stamp = int(datetime.now(UTC).timestamp() * 1000)
    try:
        symbols, start, evaluation_start, end = plan(args.symbols, args.days, args.end, stamp)
    except ValueError as exc:
        parser.error(str(exc))

    def iso(ms):
        return datetime.fromtimestamp(ms / 1000, UTC).isoformat()

    print(
        json.dumps(
            dict(
                symbols=symbols,
                fetch_start=iso(start),
                evaluation_start=iso(evaluation_start),
                end=iso(end),
                warmup_bars=200,
                candles_per_symbol=(end - start) // BAR,
                execute=args.execute,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    if not args.execute:
        return 0
    raw = os.environ.get("DATABASE_URL", "")
    try:
        if not raw or "${{" in raw:
            raise ValueError
        url = make_url(raw)
        if url.get_backend_name() != "postgresql":
            raise ValueError
        if url.drivername in ("postgresql", "postgres"):
            url = url.set(drivername="postgresql+psycopg")
        url = url.update_query_dict({"connect_timeout": "10"})
        os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    except Exception:
        print("DATABASE_URL must be a resolved PostgreSQL URL.")
        return 2
    from app.db.models.binance_spot import BinanceSpotSymbol
    from app.db.session import SessionLocal, engine
    from app.services.binance_backfill import collect_history
    from app.services.binance_coverage import STABLECOIN_BASES

    def progress(symbol, page, count):
        print(f"{symbol} page={page} candles={count}", flush=True)

    results = []
    try:
        # Check the entire cohort before the first write.
        with SessionLocal() as db:
            for symbol in symbols:
                target = db.get(BinanceSpotSymbol, symbol)
                if target is None or not target.active or target.base_asset in STABLECOIN_BASES:
                    print(f"Invalid catalogue symbol: {symbol}; no candles written.")
                    return 2
        for symbol in symbols:
            try:
                with SessionLocal() as db:
                    result = collect_history(db, symbol, start, end, stamp, progress=progress)
                results.append(result)
                print(json.dumps(result), flush=True)
            except Exception as exc:
                # Keep credentials and raw database exceptions out of terminal output.
                print(f"Backfill stopped: symbol={symbol} error={type(exc).__name__}")
                print("Committed pages are preserved; rerun with the same --end to retry.")
                return 1
        complete = sum(r["status"] == "complete" for r in results)
        print(f"Complete symbols: {complete}/{len(symbols)}", flush=True)
        return 0 if complete == len(symbols) else 1
    except Exception as exc:
        print(f"Database preflight failed: error={type(exc).__name__}")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
