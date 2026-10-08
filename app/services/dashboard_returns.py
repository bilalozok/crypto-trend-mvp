"""USDT acquisition P/L at exact closed prices; missing history stays missing."""

from decimal import Decimal, localcontext

from sqlalchemy import select

from app.db.models.account import Purchase
from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.services.formations import BAR, timestamp
from app.services.purchase_analysis import valid_candle

DAY = 86_400_000


def summary(db, owner, stamp):
    closed = stamp // BAR * BAR
    rows = db.scalars(
        select(Purchase)
        .where(Purchase.account_id == owner, Purchase.purchased_ms <= closed)
        .order_by(Purchase.symbol, Purchase.purchased_ms)
        .limit(10001)
    ).all()
    if len(rows) > 10000:
        raise ValueError("10.000 alış kaydı sınırı aşıldı.")
    excluded = sum(r.currency != "USDT" for r in rows)
    lots = {}
    for row in rows:
        if row.currency == "USDT":
            lots.setdefault(row.symbol, []).append(row)
    points = [closed - day * DAY for day in range(7, -1, -1)]
    prices = {}
    active = set(
        db.scalars(
            select(BinanceSpotSymbol.symbol).where(
                BinanceSpotSymbol.active.is_(True), BinanceSpotSymbol.symbol.in_(lots)
            )
        ).all()
    )
    if lots:
        candles = db.scalars(
            select(BinanceSpotCandle).where(
                BinanceSpotCandle.symbol.in_(lots),
                BinanceSpotCandle.open_time.in_([t - BAR for t in points]),
            )
        ).all()
        for candle in candles:
            if candle.symbol in active and valid_candle(candle):
                prices[candle.symbol, candle.open_time + BAR] = Decimal(str(candle.close))
    with localcontext() as ctx:
        ctx.prec = 80

        def values(symbol, at):
            held = [lot for lot in lots[symbol] if lot.purchased_ms <= at]
            quantity = sum((Decimal(lot.quantity) for lot in held), Decimal(0))
            cost = sum(
                (
                    Decimal(lot.quantity) * Decimal(lot.unit_price) + Decimal(lot.fee)
                    for lot in held
                ),
                Decimal(0),
            )
            if not held:
                return quantity, cost, Decimal(0), Decimal(0)
            price = prices.get((symbol, at))
            if price is None:
                return quantity, cost, None, None
            value = quantity * price
            return quantity, cost, value, value - cost

        def out(value):
            return format(value, "f") if value is not None else None

        coins = []
        for symbol in sorted(lots):
            qty, cost, value, profit = values(symbol, closed)
            old_day = values(symbol, closed - DAY)[3]
            old_week = values(symbol, closed - 7 * DAY)[3]
            day = profit - old_day if profit is not None and old_day is not None else None
            week = profit - old_week if profit is not None and old_week is not None else None
            coins.append(
                dict(
                    symbol=symbol,
                    quantity=out(qty),
                    cost=out(cost),
                    value=out(value),
                    profit=out(profit),
                    profit_pct=out(profit / cost * 100) if profit is not None and cost else None,
                    day_change=out(day),
                    week_change=out(week),
                    price=out(prices.get((symbol, closed))),
                    price_time=timestamp(closed) if value is not None else None,
                )
            )
        history = []
        for at in points:
            profits = [values(symbol, at)[3] for symbol in lots]
            complete = all(p is not None for p in profits)
            history.append(
                dict(
                    at=timestamp(at),
                    profit=out(sum(profits, Decimal(0))) if complete else None,
                    missing_coins=sum(p is None for p in profits),
                )
            )
        totals = {}
        for key in ("cost", "value", "profit", "day_change", "week_change"):
            complete = all(c[key] is not None for c in coins)
            totals[key] = (
                out(sum((Decimal(c[key]) for c in coins), Decimal(0))) if complete else None
            )
        return dict(
            checked_at=timestamp(stamp),
            price_close=timestamp(closed),
            coins=coins,
            totals=totals,
            history=history,
            excluded_try_purchases=excluded,
            missing_current=sum(c["value"] is None for c in coins),
        )
