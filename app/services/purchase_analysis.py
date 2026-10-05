"""Private acquisition valuation using aligned, closed public market prices."""

from decimal import Decimal, InvalidOperation, localcontext
from functools import lru_cache

import requests
from sqlalchemy import select

from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.services.formations import timestamp

BAR = 900_000


def number(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() and 0 < result <= Decimal("1e12") else None


def money(value):
    return format(value, "f")


def percent(value):
    return format(value.quantize(Decimal("1e-8")), "f")


def valid_candle(row):
    prices = [number(getattr(row, field)) for field in ("open", "high", "low", "close")]
    if any(value is None for value in prices):
        return False
    opened, high, low, closed = prices
    return low <= min(opened, closed) <= max(opened, closed) <= high


@lru_cache(maxsize=8)
def try_rate(close_ms, retry_bucket):
    # Only public USDT/TRY and its time are sent; never account/purchase information.
    # Binance TR documents separate routes for symbol types 1 and 3.
    sources = (
        ("https://api.binance.me/api/v1/klines", "USDTTRY"),
        ("https://cloudme-tr.2meta.app/api/v1/klines", "USDT_TRY"),
    )
    for url, symbol in sources:
        try:
            response = requests.get(
                url,
                params=dict(
                    symbol=symbol,
                    interval="15m",
                    startTime=close_ms - BAR,
                    endTime=close_ms - 1,
                    limit=1,
                ),
                timeout=(3, 4),
                allow_redirects=False,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("code") != 0:
                continue
            rows = payload.get("data")
            if not isinstance(rows, list) or len(rows) != 1:
                continue
            row = rows[0]
            if not isinstance(row, list) or len(row) < 7:
                continue
            if int(row[0]) != close_ms - BAR or int(row[6]) != close_ms - 1:
                continue
            rate = number(row[4])
            opened, high, low = (number(row[i]) for i in (1, 2, 3))
            if (
                all(value is not None for value in (rate, opened, high, low))
                and low <= min(opened, rate) <= max(opened, rate) <= high
            ):
                return dict(
                    price=format(rate, "f"),
                    candle_close_time=timestamp(close_ms),
                    source="Binance TR USDT/TRY · kapanmış 15m mum",
                )
        except (requests.RequestException, ValueError, TypeError, IndexError, OverflowError):
            continue
    return None


def valuation(db, rows, stamp):
    close_ms = stamp // BAR * BAR
    groups = {}
    with localcontext() as ctx:
        ctx.prec = 80
        for row in rows:
            group = groups.setdefault(
                (row.symbol, row.currency),
                dict(quantity=Decimal(0), cost=Decimal(0), purchases=0, latest_purchase=0),
            )
            group["quantity"] += Decimal(row.quantity)
            group["cost"] += Decimal(row.quantity) * Decimal(row.unit_price) + Decimal(row.fee)
            group["purchases"] += 1
            group["latest_purchase"] = max(group["latest_purchase"], row.purchased_ms)
        rate = try_rate(close_ms, stamp // 30_000) if any(c == "TRY" for _, c in groups) else None
        symbols = {s for s, _ in groups}
        candles = {
            c.symbol: c
            for c in db.scalars(
                select(BinanceSpotCandle).where(
                    BinanceSpotCandle.symbol.in_(symbols),
                    BinanceSpotCandle.open_time == close_ms - BAR,
                    BinanceSpotCandle.interval == "15m",
                )
            ).all()
        }
        active = {
            s.symbol: s.active
            for s in db.scalars(
                select(BinanceSpotSymbol).where(BinanceSpotSymbol.symbol.in_(symbols))
            )
        }
        result = []
        for (symbol, currency), group in sorted(groups.items()):
            candle = candles.get(symbol)
            item = dict(
                symbol=symbol,
                currency=currency,
                purchases=group["purchases"],
                quantity=format(group["quantity"], "f"),
                total_cost=format(group["cost"], "f"),
                market_unit_price=None,
                market_value=None,
                unrealized_return=None,
                unrealized_return_pct=None,
                price_close_time=None,
                status="unavailable",
            )
            if not active.get(symbol) or candle is None or not valid_candle(candle):
                item["note"] = "Aktif parite için güncel kapanmış fiyat yok; değer hesaplanmadı."
            elif group["latest_purchase"] > close_ms:
                item["note"] = "Alış son fiyat zamanından sonra; yeni mum kapanışı bekleniyor."
            elif currency == "TRY" and rate is None:
                item["note"] = (
                    "Aynı zamana ait USDT/TL fiyatı doğrulanamadı; TL değer hesaplanmadı."
                )
            else:
                price = number(candle.close)
                if currency == "TRY":
                    price *= Decimal(rate["price"])
                value = price * group["quantity"]
                change = value - group["cost"]
                if change > 0:
                    note = "Olumlu: piyasa değeri komisyon dahil alış maliyetinin üzerinde."
                elif change < 0:
                    note = "Olumsuz: piyasa değeri komisyon dahil alış maliyetinin altında."
                else:
                    note = "Piyasa değeri alış maliyetine eşit."
                item.update(
                    status="ready",
                    market_unit_price=money(price),
                    market_value=money(value),
                    unrealized_return=money(change),
                    unrealized_return_pct=percent(change / group["cost"] * 100),
                    price_close_time=timestamp(close_ms),
                    note=note
                    + " Satış masrafları ve satışlar dahil değildir; gerçekleşmemiş farktır.",
                )
            result.append(item)
        totals = []
        for currency in sorted({c for _, c in groups}):
            subset = [r for r in result if r["currency"] == currency]
            complete = all(r["status"] == "ready" for r in subset)
            cost = sum((Decimal(r["total_cost"]) for r in subset), Decimal(0))
            value = (
                sum((Decimal(r["market_value"]) for r in subset), Decimal(0)) if complete else None
            )
            profit_pct = percent((value - cost) / cost * 100) if complete else None
            totals.append(
                dict(
                    currency=currency,
                    groups=len(subset),
                    unavailable_groups=sum(r["status"] != "ready" for r in subset),
                    total_cost=money(cost),
                    market_value=money(value) if complete else None,
                    unrealized_return=money(value - cost) if complete else None,
                    unrealized_return_pct=profit_pct,
                )
            )
    return dict(
        version="private_purchase_valuation_v1",
        as_of=timestamp(stamp),
        target_close_time=timestamp(close_ms),
        price_source="Binance Spot USDT · saklanan kapanmış 15m mum",
        try_rate=rate,
        groups=result,
        totals=totals,
        note=(
            "Seçilen alışlar elde tutuluyor varsayılır. Satışlar düşülmez; "
            "nakit ve portföy getirisi değildir. TL ve USDT toplamları ayrıdır. "
            "TL fiyatı iki piyasadan türetilir; banka USD/TL kuru veya işlem teklifi değildir."
        ),
    )


def price_range(db, symbol, begin, finish, stamp):
    # Include only whole closed bars; do not label a partial bar as a full-period price.
    first = (begin + BAR - 1) // BAR * BAR
    last = min(finish // BAR * BAR, stamp // BAR * BAR)
    expected = max(0, (last - first) // BAR)
    rows = db.scalars(
        select(BinanceSpotCandle)
        .where(
            BinanceSpotCandle.symbol == symbol,
            BinanceSpotCandle.interval == "15m",
            BinanceSpotCandle.open_time >= first,
            BinanceSpotCandle.open_time < last,
        )
        .order_by(BinanceSpotCandle.open_time)
        .limit(3001)
    ).all()
    valid = [r for r in rows if valid_candle(r) and r.open_time % BAR == 0]
    complete = expected >= 2 and len(valid) == expected
    result = dict(
        symbol=symbol,
        currency="USDT",
        requested_start=timestamp(begin),
        requested_end=timestamp(finish),
        effective_start=timestamp(first),
        effective_end=timestamp(last),
        expected_candles=expected,
        candles_used=len(valid),
        missing_candles=max(0, expected - len(valid)),
        status="ready" if complete else "incomplete",
        change_pct=None,
        first_open=None,
        last_close=None,
        lowest_price=None,
        highest_price=None,
        points=[
            dict(time=timestamp(r.open_time + BAR), price=money(number(r.close))) for r in valid
        ],
        note=(
            "Grafik USDT fiyatıdır; TL alış fiyatıyla doğrudan karşılaştırılmaz. "
            "Tam aralıktaki ilk açılıştan son kapanışa değişim; alış getirisi değildir. "
            "Yalnızca tamamen aralık içinde kapanmış 15m mumlar kullanılır."
        ),
    )
    if complete:
        with localcontext() as ctx:
            ctx.prec = 80
            opened, closed = number(valid[0].open), number(valid[-1].close)
            result.update(
                first_open=money(opened),
                last_close=money(closed),
                lowest_price=money(min(number(r.low) for r in valid)),
                highest_price=money(max(number(r.high) for r in valid)),
                change_pct=percent((closed / opened - 1) * 100),
            )
    else:
        result["note"] += (
            " Aralık eksik veya iki mumdan kısa; dönem değişimi hesaplanmadı "
            "ve kesintisiz grafik çizilmedi."
        )
    return result
