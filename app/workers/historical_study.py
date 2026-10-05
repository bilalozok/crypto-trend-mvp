"""Read-only dated study on existing PostgreSQL candles, with paired 1/2/4h outcomes."""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url

from app.workers.history_backfill import DEFAULT_SYMBOLS

BAR = 900_000


def date_ms(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Dates must include a timezone")
    ms = int(parsed.timestamp() * 1000)
    if ms % BAR:
        raise ValueError("Dates must align to a 15m boundary")
    return ms


def json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError("Unsupported report value")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-09-05T01:00:00Z")
    parser.add_argument("--split", default="2026-09-25T01:00:00Z")
    parser.add_argument("--end", default="2026-10-05T01:00:00Z")
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    parser.add_argument("--output", help="Optional new JSON report path (existing files rejected)")
    args = parser.parse_args()
    try:
        start, split, end = (date_ms(v) for v in (args.start, args.split, args.end))
        if start < 200 * BAR or not start < split < end:
            raise ValueError("Require start < split < end")
        if end > int(datetime.now(UTC).timestamp() * 1000) // BAR * BAR:
            raise ValueError("End includes unclosed candles")
        if end - start > 90 * 96 * BAR or min(split - start, end - split) <= 16 * BAR:
            raise ValueError("Periods must exceed 4h and total range cannot exceed 90 days")
        symbols = list(dict.fromkeys(s.strip().upper() for s in args.symbols.split(",")))
        if (
            not symbols
            or len(symbols) > 20
            or any(not s.isalnum() or not s.endswith("USDT") for s in symbols)
        ):
            raise ValueError("Provide 1 to 20 USDT symbols")
        if args.output and (Path(args.output).exists() or not Path(args.output).parent.is_dir()):
            raise ValueError("Output must be a new file in an existing directory")
        raw = os.environ.get("DATABASE_URL", "")
        if not raw or "${{" in raw:
            raise ValueError("DATABASE_URL missing or unresolved")
        if raw.startswith("postgres://"):
            raw = raw.replace("postgres://", "postgresql://", 1)
        url = make_url(raw)
        if url.get_backend_name() != "postgresql":
            raise ValueError("PostgreSQL required")
        if url.drivername == "postgresql":
            url = url.set(drivername="postgresql+psycopg")
        os.environ["DATABASE_URL"] = url.update_query_dict(
            {"connect_timeout": "10"}
        ).render_as_string(hide_password=False)
    except ValueError:
        parser.error("Check dates, symbols, output path and resolved PostgreSQL DATABASE_URL")

    from sqlalchemy import select

    from app.db.models.binance_spot import BinanceSpotCandle
    from app.db.session import SessionLocal, engine
    from app.services.formations import timestamp
    from app.services.historical_study import HORIZONS, aggregate, check_history, paired_period

    periods = (("first_period", start, split), ("second_period", split, end))
    collected = {name: {h: [] for h in HORIZONS} for name, _, _ in periods}
    boundary = {name: 0 for name, _, _ in periods}
    fields = ("open_time", "open", "high", "low", "close", "volume")
    snapshots = {}
    try:
        # Validate and materialize all histories before reporting any comparisons.
        with SessionLocal() as db:
            for symbol in symbols:
                rows = db.execute(
                    select(*[getattr(BinanceSpotCandle, f) for f in fields])
                    .where(
                        BinanceSpotCandle.symbol == symbol,
                        BinanceSpotCandle.open_time >= start - 200 * BAR,
                        BinanceSpotCandle.open_time < end,
                    )
                    .order_by(BinanceSpotCandle.open_time)
                ).all()
                print(f"Checking stored history: {symbol}", flush=True)
                check_history(rows, start, end)
                snapshots[symbol] = rows
                db.rollback()
        for number, symbol in enumerate(symbols, 1):
            print(f"[{number}/{len(symbols)}] {symbol} testing...", flush=True)
            for name, left, right in periods:
                rows = [r for r in snapshots[symbol] if left - 200 * BAR <= r.open_time < right]
                result = paired_period(rows, symbol, left, right)
                boundary[name] += result["excluded_boundary_signals"]
                for horizon in HORIZONS:
                    collected[name][horizon].extend(result["by_horizon"][horizon])
        report = dict(
            study_version="dated_paired_horizons_v1",
            symbols=symbols,
            start=timestamp(start).isoformat(),
            split=timestamp(split).isoformat(),
            end=timestamp(end).isoformat(),
            warmup_bars=200,
            fee_bps_per_side=10,
            slippage_bps_per_side=5,
            min_volume_ratio=1.5,
            periods={},
        )
        print("\nPERIOD | HOURS | SIGNALS | POSITIVE NET | MEAN NET | MEDIAN NET")
        for name, left, right in periods:
            report["periods"][name] = dict(
                start=timestamp(left).isoformat(),
                end=timestamp(right).isoformat(),
                excluded_boundary_signals=boundary[name],
                horizons={},
            )
            for horizon in HORIZONS:
                trades = collected[name][horizon]
                summary = aggregate(trades)
                report["periods"][name]["horizons"][str(horizon)] = dict(**summary, signals=trades)
                stats = summary["all"]

                def fmt(value):
                    return "—" if value is None else f"{value:+.3f}%"

                print(
                    f"{name} | {horizon // 4} | {stats['signals']} | "
                    f"{fmt(stats['positive_net_rate_pct'])} | "
                    f"{fmt(stats['mean_net_return_pct'])} | {fmt(stats['median_net_return_pct'])}"
                )
            print(f"Boundary signals excluded (same for all horizons): {boundary[name]}")
        report["note"] = (
            "Retrospective current-cohort comparison; periods are not unseen holdouts. "
            "Overlapping signals are not portfolio returns or independent trials. "
            "Each period excludes signals lacking its full 4h forward window; "
            "all horizons use the same signals."
        )
        print(report["note"])
        if args.output:
            with Path(args.output).open("x") as output:
                json.dump(report, output, ensure_ascii=False, indent=2, default=json_default)
            print(f"Report saved: {args.output}")
        return 0
    except Exception as exc:
        print(
            f"Study stopped: {type(exc).__name__}; check complete history and database connection."
        )
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
