"""Prospective paper execution: exact closed bars, no exchange orders or downloads."""

from decimal import Decimal, localcontext

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select

from app.db.models.account import Account
from app.db.models.binance_spot import BinanceSpotCandle, BinanceSpotSymbol
from app.db.models.candidate_scan import CandidateScan
from app.db.models.simulation import Simulation
from app.services.formations import timestamp as candle_timestamp
from app.services.indicator_direction import VERSION, evaluate
from app.services.money_flow_context import report
from app.services.purchase_analysis import BAR, number, valid_candle

VERSION_SIM = "paper_100_usdt_v1"
BUDGET = Decimal("100")
FEE = Decimal("0.001")
SLIP = Decimal("0.0005")
LIMIT = 200


def timestamp(ms):
    return candle_timestamp(ms).isoformat() if ms is not None else None


def fail(message, code=422):
    raise HTTPException(status_code=code, detail=message)


def context(db, symbol, stamp):
    raw = report(db, symbol, stamp)
    if raw is None:
        return dict(symbol=symbol, checked_at=timestamp(stamp), horizons=[])
    horizons = []
    for h in raw["horizons"]:
        horizons.append(
            dict(
                interval=h["interval"],
                up=evaluate(h, "up"),
                down=evaluate(h, "down"),
                values=h.get("latest"),
                ichimoku={k: v for k, v in h.get("ichimoku", {}).items() if k != "series"},
                money_flow={k: v for k, v in h.get("money_flow", {}).items() if k != "series"},
                fibonacci=h.get("fibonacci"),
                patterns=h.get("confluence", {}).get("patterns", []),
            )
        )
    return jsonable_encoder(
        dict(symbol=symbol, checked_at=timestamp(stamp), version=VERSION, horizons=horizons)
    )


def price_at(db, symbol, close_ms):
    candle = db.scalar(
        select(BinanceSpotCandle).where(
            BinanceSpotCandle.symbol == symbol,
            BinanceSpotCandle.interval == "15m",
            BinanceSpotCandle.open_time == close_ms - BAR,
        )
    )
    return number(candle.close) if candle is not None and valid_candle(candle) else None


def owned(db, account_id, position_id):
    row = db.scalar(
        select(Simulation)
        .where(Simulation.account_id == account_id, Simulation.id == str(position_id))
        .with_for_update()
    )
    if row is None:
        fail("Sanal kayıt bulunamadı.", 404)
    return row


def fill_entry(row, price):
    with localcontext() as ctx:
        ctx.prec = 80
        execution = price * (1 + SLIP) if row.mode == "paper" else price
        row.entry_price = format(execution, "f")
        row.quantity = format(BUDGET / (execution * (1 + FEE)), "f")
    row.state = "open"


def settle(db, row, stamp):
    if row.state == "pending_entry" and row.entry_ms <= stamp:
        price = price_at(db, row.symbol, row.entry_ms)
        if price is not None:
            fill_entry(row, price)
            row.entry_context = context(db, row.symbol, row.entry_ms)
    if row.state == "pending_exit" and row.exit_ms <= stamp:
        price = price_at(db, row.symbol, row.exit_ms)
        if price is not None:
            row.exit_price = format(price * (1 - SLIP), "f")
            row.exit_context = context(db, row.symbol, row.exit_ms)
            row.state = "closed"


def create(db, account_id, data, stamp):
    # Serialize each owner's creation budget and repeated request IDs.
    db.scalar(select(Account).where(Account.id == account_id).with_for_update())
    request = data.model_dump(mode="json")
    old = db.get(Simulation, str(data.id))
    if old is not None:
        if old.account_id != account_id or old.request != request:
            fail("Bu işlem kimliği farklı bir kayıt için kullanılmış.", 409)
        return old
    count = db.scalar(
        select(func.count()).select_from(Simulation).where(Simulation.account_id == account_id)
    )
    if count >= LIMIT:
        fail("En fazla 200 sanal kayıt saklanır; önce bir kaydı sil.")
    symbol = data.symbol.upper()
    active = db.scalar(
        select(BinanceSpotSymbol).where(
            BinanceSpotSymbol.symbol == symbol, BinanceSpotSymbol.active.is_(True)
        )
    )
    if active is None or not symbol.endswith("USDT"):
        fail("Aktif USDT paritesi gerekli.")
    decision = context(db, symbol, stamp)
    selection = dict(
        version=VERSION_SIM,
        indicator_version=VERSION,
        selected_at=timestamp(stamp),
        context=decision,
        note=data.note,
    )
    if data.source == "candidate_scan":
        scan = db.scalar(
            select(CandidateScan).where(
                CandidateScan.id == str(data.scan_id), CandidateScan.account_id == account_id
            )
        )
        if scan is None:
            fail("Kendi kayıtlı taramanı seç.", 404)
        candidate = next(
            (c for c in scan.payload.get("candidates", []) if c["symbol"] == symbol), None
        )
        if candidate is None:
            fail("Coin bu taramanın adayları arasında değil.")
        selection.update(
            scan_id=scan.id,
            scan_time=timestamp(scan.created_ms),
            scan_rule_hash=scan.rule_hash,
            candidate=candidate,
        )
    elif data.scan_id is not None:
        fail("Tarama kimliği yalnızca kayıtlı aday kaynağında kullanılır.")
    if data.source == "formation_scan":
        from app.services.formations import NAMES

        if data.scan_filters is None or data.scan_filters.pattern not in NAMES:
            fail("Formasyon taramasının filtrelerini belirt.")
        selection["displayed_scan_filters"] = request["scan_filters"]
        selection["filter_note"] = (
            "Kullanıcının seçtiği tarama filtreleri; güncel bağlam ayrı saklanır."
        )
    elif data.scan_filters is not None:
        fail("Formasyon filtreleri bu kaynak için kullanılamaz.")
    if data.source == "indicator_filter":
        h = next((h for h in decision["horizons"] if h["interval"] == data.interval), None)
        if not h or not h["up"]["all_match"]:
            fail("Seçilen vadede yükseliş filtresi artık tam eşleşmiyor; listeyi yenile.", 409)
        selection["filter"] = dict(interval=data.interval, direction="up", criteria=h["up"])
    if data.mode == "actual_reference":
        if data.source != "manual" or data.price is None or data.purchased_at is None:
            fail("Gerçek alış referansı için elle fiyat ve alış zamanı gir.")
        entry_ms = int(data.purchased_at.timestamp() * 1000)
        if entry_ms < 0 or entry_ms > stamp:
            fail("Alış zamanı geçmişte olmalı.")
    else:
        if data.price is not None or data.purchased_at is not None:
            fail("Sanal alış fiyatı veya geçmiş işlem zamanı elle atanamaz.")
        entry_ms = (stamp // BAR + 1) * BAR
    row = Simulation(
        id=str(data.id),
        account_id=account_id,
        symbol=symbol,
        created_ms=stamp,
        state="pending_entry",
        mode=data.mode,
        source=data.source,
        entry_ms=entry_ms,
        request=request,
        selection=selection,
        observations=[],
    )
    if data.mode == "actual_reference":
        fill_entry(row, data.price)
        row.entry_context = dict(
            note="Geçmiş gerçek alış referansı sonradan girildi; o andaki seçim kanıtı değildir.",
            reported_at=timestamp(stamp),
            historical_context=context(db, symbol, entry_ms),
        )
    db.add(row)
    return row


def sell(db, row, stamp, data):
    settle(db, row, stamp)
    if row.state in ("pending_exit", "closed"):
        return row  # Retried sale never changes its price/time/context.
    if row.state != "open":
        fail("Alış kapanışı kaydedilmeden satış yapılamaz.", 409)
    row.sale_context = dict(
        decided_at=timestamp(stamp), note=data.note, context=context(db, row.symbol, stamp)
    )
    if data.price is not None or data.sold_at is not None:
        if row.mode != "actual_reference" or data.price is None or data.sold_at is None:
            fail("Elle satış yalnızca gerçek alış referansında fiyat ve zamanla yapılır.")
        exit_ms = int(data.sold_at.timestamp() * 1000)
        if not row.entry_ms < exit_ms <= stamp:
            fail("Satış alıştan sonra ve geçmişte olmalı.")
        row.exit_ms = exit_ms
        row.exit_price = format(data.price, "f")
        row.exit_context = dict(
            note="Elle girilmiş satış referansı.",
            historical_context=context(db, row.symbol, exit_ms),
        )
        row.state = "closed"
    else:
        row.exit_ms = (stamp // BAR + 1) * BAR
        row.state = "pending_exit"
    return row


def out(db, row, stamp, detail=False):
    with localcontext() as ctx:
        ctx.prec = 80
        mark_time = stamp // BAR * BAR
        mark = (
            price_at(db, row.symbol, mark_time) if row.quantity and row.state != "closed" else None
        )
        execution = (
            Decimal(row.exit_price)
            if row.state == "closed"
            else (mark * (1 - SLIP) if mark is not None else None)
        )
        net = Decimal(row.quantity) * execution * (1 - FEE) if execution is not None else None
        profit = net - BUDGET if net is not None else None

        def fmt(value):
            return format(value, ".8f") if value is not None else None

        result = dict(
            id=row.id,
            symbol=row.symbol,
            state=row.state,
            mode=row.mode,
            source=row.source,
            selected_at=timestamp(row.created_ms),
            entry_at=timestamp(row.entry_ms),
            exit_at=timestamp(row.exit_ms),
            budget="100",
            entry_price=row.entry_price,
            exit_price=row.exit_price,
            quantity=row.quantity,
            current_price=fmt(mark),
            price_at=timestamp(mark_time) if mark is not None else None,
            net_value=fmt(net),
            profit=fmt(profit),
            return_pct=fmt(profit / BUDGET * 100) if profit is not None else None,
            note=row.selection.get("note", ""),
        )
    last = (row.observations or [])[-1] if row.observations else None
    if row.state == "closed":
        last_context = row.exit_context or {}
    elif last:
        last_context = last["context"]
    else:
        last_context = row.entry_context or {}
    actual_context = last_context.get("historical_context", last_context)
    short = next((h for h in actual_context.get("horizons", []) if h["interval"] == "15m"), None)
    result["last_observed"] = dict(
        at=actual_context.get("checked_at"),
        status=short["up"]["status"] if short else "unavailable",
        up=sum(c["state"] == "supports" for c in short["up"]["checks"]) if short else None,
        down=sum(c["state"] == "supports" for c in short["down"]["checks"]) if short else None,
        total=len(short["up"]["checks"]) if short else None,
    )
    if detail:
        latest = context(db, row.symbol, stamp) if row.state in ("open", "pending_exit") else None
        if latest is not None:
            previous = row.observations or []
            key = dict(
                at=timestamp(mark_time),
                price=result["current_price"],
                profit=result["profit"],
                context=latest,
            )
            if not previous or previous[-1]["at"] != key["at"]:
                row.observations = (previous + [key])[-192:]
        result.update(
            selection=row.selection,
            entry_context=row.entry_context,
            sale_context=row.sale_context,
            exit_context=row.exit_context,
            current_context=latest,
            observations=row.observations,
            history_note="İnceleme/yenileme anları saklanır; kesintisiz geçmiş değildir. "
            "Son 192 gözlem korunur, alış/satış bağlamları ayrı saklanır.",
        )
    return result


def listing(db, account_id, stamp, offset, kind="all"):
    rows = db.scalars(
        select(Simulation)
        .where(Simulation.account_id == account_id)
        .order_by(Simulation.id)
        .with_for_update()
    ).all()
    for row in rows:
        settle(db, row, stamp)
    ordered = sorted(rows, key=lambda r: (r.created_ms, r.id), reverse=True)
    totals = [out(db, r, stamp) for r in ordered]
    closed = [r for r in totals if r["state"] == "closed"]
    open_rows = [r for r in totals if r["state"] in ("open", "pending_exit")]
    realized = sum((Decimal(r["profit"]) for r in closed), Decimal(0))
    ready = all(r["profit"] is not None for r in open_rows)
    unrealized = sum((Decimal(r["profit"]) for r in open_rows), Decimal(0)) if ready else None
    source_groups = []
    for source, mode in sorted({(r["source"], r["mode"]) for r in totals}):
        subset = [r for r in closed if r["source"] == source and r["mode"] == mode]
        profit = sum((Decimal(r["profit"]) for r in subset), Decimal(0))
        source_groups.append(
            dict(
                source=source,
                mode=mode,
                closed=len(subset),
                profit=format(profit, ".8f"),
                win_rate_pct=(
                    format(
                        Decimal(sum(Decimal(r["profit"]) > 0 for r in subset)) / len(subset) * 100,
                        ".2f",
                    )
                    if subset
                    else None
                ),
            )
        )
    filtered = [
        r for r in totals if kind == "all" or (r["state"] == "closed") == (kind == "closed")
    ]
    return dict(
        version=VERSION_SIM,
        as_of=timestamp(stamp),
        source_groups=source_groups,
        positions=filtered[offset : offset + 20],
        total=len(filtered),
        next_offset=offset + 20 if offset + 20 < len(filtered) else None,
        summary=dict(
            closed=len(closed),
            open=len(open_rows),
            pending_entry=sum(r["state"] == "pending_entry" for r in totals),
            realized_profit=format(realized, ".8f"),
            unrealized_profit=format(unrealized, ".8f") if ready else None,
            win_rate_pct=(
                format(
                    Decimal(sum(Decimal(r["profit"]) > 0 for r in closed)) / len(closed) * 100,
                    ".2f",
                )
                if closed
                else None
            ),
            net_return_pct=(
                format(realized / (BUDGET * len(closed)) * 100, ".8f") if closed else None
            ),
        ),
        note="100 USDT alış bütçesi komisyon dahildir. Her yönde %0,10 komisyon; "
        "sanal kapanış fiyatında alış +%0,05, satış -%0,05 kayma varsayılır. "
        "Gerçek referans fiyatında kayma yoktur; komisyon yine varsayımsaldır. "
        "Yeni kararın sonraki 15m kapanışı beklenir; eksik mum başka fiyatla doldurulmaz. "
        "Silinen işlemler istatistiklerden çıkar. Emir gönderilmez.",
    )
