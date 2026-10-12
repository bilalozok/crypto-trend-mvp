"""Retrospective observed-universe studies; never treat future values as prior evidence."""

import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timedelta
from itertools import combinations
from zoneinfo import ZoneInfo

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.db.models.portfolio_timeframe import PortfolioCandle
from app.db.models.real_gainer import RealGainerStudy
from app.services.binance_collection import insert_for
from app.services.binance_coverage import STABLECOIN_BASES
from app.services.indicator_direction import VERSION as DIRECTION_VERSION
from app.services.indicator_direction import evaluate, horizon
from app.services.technical_indicators import INTERVALS

VERSION = "daily_observed_gainers_v1"
TR = ZoneInfo("Europe/Istanbul")
DAY = 86_400_000
BAR = 900_000
NOTE = (
    "Türkiye takvim günü: 00:00–24:00. Son yedi tamamlanmış gün; bugün dahil değil. "
    "Sıralama, veritabanında katalog kaydı ve günün 96 geçerli 15m mumu bulunan USDT "
    "paritelerinin açılıştan kapanışa fiyat değişimidir. Stablecoinler dışlanır. "
    "Tam geçmiş borsa evreni değildir; eksik veri ve katalog kapsamı seçilim yanlılığı yaratır. "
    "Gün başlamadan önceki kapanmış mumlar ön koşuldur; gün sonu değerleri sonradan oluşmuş "
    "betimleyici bağlamdır. Ortaklık başarı olasılığı, nedensellik veya alım sinyali değildir. "
    "Örnekleme yalnız yükselenlerden oluşur; yükselmeyen kontrol grubuyla karşılaştırılmamıştır. "
    "SMA/EMA50, seçilen vadenin 50 mumudur; yalnız 1d görünümünde 50 gündür."
)


def periods(stamp, days=7):
    today = datetime.fromtimestamp(stamp / 1000, TR).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return [
        (
            int((today - timedelta(days=i)).timestamp() * 1000),
            int((today - timedelta(days=i - 1)).timestamp() * 1000),
        )
        for i in range(days, 0, -1)
    ]


def daily_ranking(db, begin, end):
    """Exact 96 valid unique 15m bars, never missing endpoints or current-day returns."""
    c = BinanceSpotCandle
    symbols = select(BinanceSpotSymbol.symbol).where(
        BinanceSpotSymbol.symbol.endswith("USDT"),
        BinanceSpotSymbol.base_asset.not_in(STABLECOIN_BASES | {"USDT"}),
    )
    universe = db.scalar(select(func.count()).select_from(symbols.subquery()))
    valid = [
        c.symbol.in_(symbols),
        c.interval == "15m",
        c.open_time >= begin,
        c.open_time < end,
        c.open_time % BAR == 0,
        c.volume >= 0,
        c.volume <= 1e30,
    ]
    for field in (c.open, c.high, c.low, c.close):
        valid.extend([field > 0, field <= 1e12])
    valid.extend([c.low <= c.open, c.low <= c.close, c.high >= c.open, c.high >= c.close])
    complete = (
        select(c.symbol.label("symbol"), func.sum(c.volume).label("volume"))
        .where(*valid)
        .group_by(c.symbol)
        .having(func.count() == 96)
        .subquery()
    )
    covered = db.scalar(select(func.count()).select_from(complete))
    first, last = aliased(c), aliased(c)
    gain = (last.close / first.open - 1) * 100
    query = (
        select(complete.c.symbol, first.open, last.close, gain.label("gain"), complete.c.volume)
        .select_from(complete)
        .join(
            first,
            (first.symbol == complete.c.symbol)
            & (first.interval == "15m")
            & (first.open_time == begin),
        )
        .join(
            last,
            (last.symbol == complete.c.symbol)
            & (last.interval == "15m")
            & (last.open_time == end - BAR),
        )
        .where(gain > 0)
        .order_by(gain.desc(), complete.c.symbol)
        .limit(5)
    )
    winners = [
        dict(symbol=s, open_price=o, close_price=c_, gain_pct=g, base_volume=v)
        for s, o, c_, g, v in db.execute(query)
    ]
    return dict(universe=universe, covered=covered, excluded=universe - covered, winners=winners)


def prior_context(db, symbol, stamp):
    result = []
    for interval, bar in INTERVALS.items():
        model = BinanceSpotCandle if interval == "15m" else PortfolioCandle
        rows = list(
            reversed(
                db.scalars(
                    select(model)
                    .where(
                        model.symbol == symbol,
                        model.interval == interval,
                        model.open_time + bar <= stamp,
                    )
                    .order_by(model.open_time.desc())
                    .limit(200)
                ).all()
            )
        )
        valid = all(
            math.isfinite(float(getattr(r, field)))
            for r in rows
            for field in ("open", "high", "low", "close", "volume")
        )
        valid = valid and all(
            0 < r.low <= min(r.open, r.close) <= max(r.open, r.close) <= r.high
            and r.high <= 1e12
            and 0 <= r.volume <= 1e30
            for r in rows
        )
        h = horizon(rows, symbol, interval, stamp) if valid else dict(status="invalid_data")
        up = evaluate(h, "up")
        result.append(
            dict(
                interval=interval,
                status=h["status"],
                checked_before=stamp,
                checks=up["checks"],
                latest=h.get("latest"),
                ichimoku=h.get("ichimoku", {}).get("latest"),
                money_flow=h.get("money_flow", {}).get("latest"),
                patterns=h.get("confluence", {}).get("patterns", []),
            )
        )
    return jsonable_encoder(result)


def analyze_day(db, begin, end):
    ranking = daily_ranking(db, begin, end)
    for winner in ranking["winners"]:
        winner["before"] = prior_context(db, winner["symbol"], begin)
        winner["after"] = prior_context(db, winner["symbol"], end)
    return dict(
        version=VERSION,
        indicator_version=DIRECTION_VERSION,
        day=datetime.fromtimestamp(begin / 1000, TR).date().isoformat(),
        begin=begin,
        end=end,
        **ranking,
    )


def refresh(db, stamp):
    """Archive seven days. Same evidence is idempotent; changes create a new revision."""
    saved = 0
    for begin, end in periods(stamp):
        payload = analyze_day(db, begin, end)
        previous = db.scalar(
            select(RealGainerStudy)
            .where(RealGainerStudy.day == payload["day"], RealGainerStudy.version == VERSION)
            .order_by(RealGainerStudy.computed_ms.desc(), RealGainerStudy.id.desc())
            .limit(1)
        )
        if previous is not None and previous.payload == payload:
            continue
        evidence = json.dumps(payload, sort_keys=True, allow_nan=False)
        token = hashlib.sha256((evidence + "|" + str(stamp)).encode()).hexdigest()
        statement = (
            insert_for(db, RealGainerStudy)
            .values(
                id=token, day=payload["day"], version=VERSION, computed_ms=stamp, payload=payload
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )
        result = db.execute(statement)
        saved += result.rowcount
    return saved


def trends(days, interval, phase):
    counters = {}
    total = sum(len(d.get("winners", [])) for d in days)
    per_day = []
    for day in days:
        count, available, symbols = Counter(), Counter(), {}
        for winner in day.get("winners", []):
            h = next((h for h in winner[phase] if h["interval"] == interval), None)
            if not h or h["status"] != "ready":
                continue
            for check in h["checks"]:
                key = "Gösterge · " + check["name"]
                if check["state"] != "unavailable":
                    available[key] += 1
                if check["state"] == "supports":
                    count[key] += 1
                    symbols.setdefault(key, set()).add(winner["symbol"])
            # A pattern counts once per winner, separate forming/confirmed categories.
            names = {
                p["name"] + (" · Teyitli" if p["stage"] == "confirmed" else " · Oluşuyor")
                for p in h["patterns"]
                if p["direction"] == "up"
            }
            for name in names:
                key = "Formasyon · " + name
                count[key] += 1
                symbols.setdefault(key, set()).add(winner["symbol"])
            for key in names:
                available["Formasyon · " + key] = sum(
                    next(
                        (h["status"] == "ready" for h in w[phase] if h["interval"] == interval),
                        False,
                    )
                    for w in day.get("winners", [])
                )
        ready_count = sum(
            h["status"] == "ready"
            for w in day.get("winners", [])
            for h in w[phase]
            if h["interval"] == interval
        )
        per_day.append(
            dict(
                day=day["day"],
                ready=ready_count,
                winners=len(day.get("winners", [])),
                supports=dict(count),
                available=dict(available),
            )
        )
        for key in count.keys() | available.keys():
            value = counters.setdefault(
                key, dict(supports=0, available=0, coins=set(), common_days=0)
            )
            value["supports"] += count[key]
            value["available"] += available[key]
            value["coins"].update(symbols.get(key, set()))
            if count[key] >= max(3, math.ceil(len(day.get("winners", [])) * 0.6)):
                value["common_days"] += 1
    # Pattern absence on another ready day is still a measured non-match.
    for key, value in counters.items():
        if key.startswith("Formasyon · "):
            for item in per_day:
                item["available"][key] = item["ready"]
            value["available"] = sum(
                h["status"] == "ready"
                for d in days
                for w in d.get("winners", [])
                for h in w[phase]
                if h["interval"] == interval
            )
    return dict(
        total=total,
        per_day=per_day,
        pairs=cooccurrence(days, interval, phase),
        rows=[
            dict(
                name=key,
                supports=v["supports"],
                available=v["available"],
                observations=total,
                unique_coins=len(v["coins"]),
                common_days=v["common_days"],
                share_pct=v["supports"] / v["available"] * 100 if v["available"] else None,
            )
            for key, v in sorted(
                counters.items(), key=lambda kv: (-kv[1]["common_days"], -kv[1]["supports"], kv[0])
            )
        ],
    )


def cooccurrence(days, interval, phase):
    observations = []
    patterns = set()
    for day in days:
        for winner in day.get("winners", []):
            h = next((h for h in winner[phase] if h["interval"] == interval), None)
            if not h or h["status"] != "ready":
                continue
            available = {
                "Gösterge · " + c["name"] for c in h["checks"] if c["state"] != "unavailable"
            }
            supported = {"Gösterge · " + c["name"] for c in h["checks"] if c["state"] == "supports"}
            found = {
                "Formasyon · "
                + p["name"]
                + (" · Teyitli" if p["stage"] == "confirmed" else " · Oluşuyor")
                for p in h["patterns"]
                if p["direction"] == "up"
            }
            patterns.update(found)
            observations.append(
                dict(
                    day=day["day"],
                    symbol=winner["symbol"],
                    available=available,
                    supported=supported | found,
                )
            )
    matches = {}
    for observation in observations:
        observation["available"].update(patterns)
        for pair in combinations(sorted(observation["supported"]), 2):
            matches.setdefault(pair, []).append(observation)
    out = []
    day_sizes = {d["day"]: len(d.get("winners", [])) for d in days}
    for pair, matched in matches.items():
        if len(matched) < 3:
            continue
        available = sum(all(p in o["available"] for p in pair) for o in observations)
        by_day = Counter(o["day"] for o in matched)
        out.append(
            dict(
                left=pair[0],
                right=pair[1],
                supports=len(matched),
                available=available,
                unique_coins=len({o["symbol"] for o in matched}),
                share_pct=len(matched) / available * 100,
                common_days=sum(
                    n >= max(3, math.ceil(day_sizes[d] * 0.6)) for d, n in by_day.items()
                ),
            )
        )
    return sorted(out, key=lambda r: (-r["common_days"], -r["supports"], r["left"], r["right"]))[
        :12
    ]


def archive(db, stamp, days=7):
    periods_ = periods(stamp, days)
    dates = [datetime.fromtimestamp(a / 1000, TR).date().isoformat() for a, _ in periods_]
    snapshots = db.scalars(
        select(RealGainerStudy)
        .where(RealGainerStudy.day.in_(dates), RealGainerStudy.version == VERSION)
        .order_by(RealGainerStudy.computed_ms.desc(), RealGainerStudy.id.desc())
    ).all()
    selected = {}
    for snapshot in snapshots:
        if snapshot.day not in selected:
            selected[snapshot.day] = dict(
                snapshot.payload, recorded_at=snapshot.computed_ms, snapshot_id=snapshot.id
            )
    studies = [selected.get(day, dict(day=day, winners=[], not_archived=True)) for day in dates]
    return dict(
        version=VERSION,
        note=NOTE,
        days=studies,
        saved_days=len(selected),
        trends={
            interval: {phase: trends(studies, interval, phase) for phase in ("before", "after")}
            for interval in INTERVALS
        },
    )
