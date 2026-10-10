"""Explicit, uncalibrated same-timeframe indicator agreement; read-only."""

from collections import Counter, defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from app.db.models.account import Purchase
from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.db.models.portfolio_timeframe import PortfolioCandle
from app.services import formations, portfolio_timeframes
from app.services.coin_report import build_report
from app.services.ichimoku_context import calculate as cloud
from app.services.money_flow_context import calculate as money_flow
from app.services.technical_indicators import INTERVALS, analyze_rows

VERSION = "direction_checklist_v1"


def evaluate(h, direction):
    result = dict(direction=direction, status=h["status"], all_match=False, checks=[])
    if h["status"] != "ready":
        return result
    p = h["latest"]
    previous = h["series"][-2]
    up = direction == "up"

    def sign(a, b):
        return a > b if up else a < b

    def add(name, matches, reason, available=True):
        result["checks"].append(
            dict(
                name=name,
                state="unavailable" if not available else "supports" if matches else "not_met",
                reason=reason,
            )
        )

    patterns = h.get("confluence", {}).get("patterns", [])
    confirmed = [x for x in patterns if x["stage"] == "confirmed"]
    supported = any(
        x["direction"] == direction
        and any(g["name"] == "Teyit hacmi" and g["state"] == "supports" for g in x["groups"])
        for x in confirmed
    )
    opposing = any(x["direction"] != direction for x in confirmed)
    add(
        "Formasyon / hacim",
        supported and not opposing,
        "Güncel, hacim destekli aynı yönlü teyit; karşıt güncel teyit yok.",
    )
    add(
        "RSI14",
        50 < p["rsi"] < 70 if up else 30 < p["rsi"] < 50,
        "Yükseliş: 50<RSI<70; düşüş: 30<RSI<50. Aşırılık dahil edilmez.",
    )
    add("MACD", sign(p["histogram"], 0), "MACD histogramı yönle aynı işarette.")
    add(
        "SMA50 / EMA50",
        sign(p["close"], p["sma50"])
        and sign(p["close"], p["ema50"])
        and sign(p["ema50"], previous["ema50"]),
        "Fiyat her iki ortalamanın aynı yönünde; EMA50 eğimi de aynı yönde.",
    )
    add(
        "Bollinger20",
        sign(p["close"], p["bb_middle"]) and p["bb_lower"] <= p["close"] <= p["bb_upper"],
        "Fiyat orta bandın aynı yönünde ve dış bantlar içinde.",
    )
    c = h.get("ichimoku", {})
    ready = c.get("status") == "ready"
    q = c.get("latest", {})
    add(
        "Ichimoku",
        ready and c["position"] == ("above" if up else "below") and sign(q["tenkan"], q["kijun"]),
        "Fiyat mevcut bulutun aynı yönünde; Tenkan/Kijun ilişkisi aynı yönde.",
        ready,
    )
    f = h.get("money_flow", {})
    ready = f.get("status") == "ready"
    q = f.get("latest", {})
    add(
        "CMF21 / MFI14",
        ready and sign(q["cmf"], 0) and (50 < q["mfi"] < 80 if up else 20 < q["mfi"] < 50),
        "CMF işareti ve MFI aynı yönü destekler; MFI 20/80 aşırılığı dışlanır. Ortak gruptur.",
        ready,
    )
    fib = h.get("fibonacci", {})
    ready = fib.get("status") == "ready"
    level = next((x["price"] for x in fib.get("levels", []) if x["ratio"] == 0.618), None)
    matches = (
        ready
        and level is not None
        and fib["direction"] == direction
        and (p["close"] >= level if up else p["close"] <= level)
    )
    add(
        "Fibonacci referansı",
        matches,
        (
            "Son doğrulanmış hareket aynı yönde; fiyat %61,8 düzeltmesini yönün tersine "
            "aşmamış. Deneysel seviye filtresidir; bağımsız yön teyidi değildir."
        ),
        ready and level is not None,
    )
    result["all_match"] = all(c["state"] == "supports" for c in result["checks"])
    result["candle_close_time"] = p["at"]
    return result


def horizon(rows, symbol, interval, stamp):
    from app.services.indicator_confluence import summarize

    h = analyze_rows(rows, interval, stamp)
    if h["status"] != "ready":
        return h
    analysis = (
        formations.analyze_rows(rows, symbol, stamp)
        if interval == "15m"
        else portfolio_timeframes.analyze_rows(rows, symbol, stamp, interval)
    )
    h["confluence"] = summarize(h, build_report(analysis)["patterns"])
    h["ichimoku"] = cloud(rows, INTERVALS[interval])
    h["money_flow"] = money_flow(rows, INTERVALS[interval])
    return h


def coin(db, symbol, stamp):
    from app.services.money_flow_context import report

    result = report(db, symbol, stamp)
    if result is None:
        return None
    return dict(
        symbol=symbol,
        checked_at=formations.timestamp(stamp),
        version=VERSION,
        horizons=[
            dict(interval=h["interval"], up=evaluate(h, "up"), down=evaluate(h, "down"))
            for h in result["horizons"]
        ],
    )


def screen(db, account_id, scope, interval, stamp, offset=0, limit=100):
    direction = "up" if scope == "market" else "down"
    filters = [BinanceSpotSymbol.active.is_(True)]
    if scope == "held":
        filters.append(
            BinanceSpotSymbol.symbol.in_(
                select(Purchase.symbol).where(Purchase.account_id == account_id)
            )
        )
    total = db.scalar(select(func.count()).select_from(BinanceSpotSymbol).where(*filters))
    symbols = db.scalars(
        select(BinanceSpotSymbol.symbol)
        .where(*filters)
        .order_by(BinanceSpotSymbol.symbol)
        .offset(offset)
        .limit(limit)
    ).all()
    bar = INTERVALS[interval]
    model = BinanceSpotCandle if interval == "15m" else PortfolioCandle
    grouped = defaultdict(list)
    if symbols:
        ranked = (
            select(
                model,
                func.row_number()
                .over(partition_by=model.symbol, order_by=model.open_time.desc())
                .label("position"),
            )
            .where(
                model.symbol.in_(symbols),
                model.interval == interval,
                model.open_time + bar <= stamp,
            )
            .subquery()
        )
        candle = aliased(model, ranked)
        for row in db.scalars(
            select(candle).where(ranked.c.position <= 200).order_by(candle.symbol, candle.open_time)
        ):
            grouped[row.symbol].append(row)
    matches = []
    quality = Counter()
    not_met = Counter()
    for symbol in symbols:
        h = horizon(grouped[symbol], symbol, interval, stamp)
        result = evaluate(h, direction)
        quality[h["status"]] += 1
        for check in result["checks"]:
            if check["state"] != "supports":
                not_met[check["name"]] += 1
        if result["all_match"]:
            matches.append(dict(symbol=symbol, close=h["latest"]["close"], **result))
    return dict(
        version=VERSION,
        scope=scope,
        direction=direction,
        interval=interval,
        as_of=formations.timestamp(stamp),
        total_symbols=total,
        scanned_symbols=len(symbols),
        offset=offset,
        next_offset=offset + len(symbols) if offset + len(symbols) < total else None,
        quality_counts=dict(quality),
        not_met_counts=dict(not_met),
        matches=matches,
        note=(
            "Aynı vadenin kapanmış mumları; deneysel koşul eşleşmesi, işlem emri veya "
            "başarı olasılığı değildir. Sayımlar bu sayfaya aittir; bir coin birden fazla "
            "koşuldan elenebilir. Kişisel liste alış kayıtlarını kullanır; satışlar düşülmez."
        ),
    )
