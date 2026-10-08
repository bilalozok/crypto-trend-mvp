import os
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.db.models.account import Account, AccountSession, Purchase
from app.db.models.binance_spot import BinanceSpotSymbol
from app.db.session import SessionLocal
from app.services import account_auth, portfolio_technical, purchase_analysis
from app.services.binance_collection import now_ms
from app.services.formations import timestamp

router = APIRouter(prefix="/account", tags=["Private purchases"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Hesap veritabanına erişilemiyor.") from exc
    finally:
        db.close()


Db = Annotated[object, Depends(get_db)]


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=128)


class PurchaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    symbol: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9]+$")
    purchased_at: datetime
    currency: Literal["TRY", "USDT"]
    unit_price: Decimal = Field(gt=0, le=Decimal("1e12"), max_digits=38, decimal_places=18)
    quantity: Decimal = Field(gt=0, le=Decimal("1e12"), max_digits=38, decimal_places=18)
    fee: Decimal = Field(
        default=Decimal(0), ge=0, le=Decimal("1e12"), max_digits=38, decimal_places=18
    )
    note: str = Field(default="", max_length=1000)

    @field_validator("purchased_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Saat dilimi içeren tarih gerekli.")
        return value


def same_origin(request):
    # JSON login plus strict cookies; reject explicit foreign origins for all mutations.
    origin = request.headers.get("origin")
    expected = os.getenv(
        "PRIVATE_APP_ORIGIN", "https://crypto-trend-mvp-production.up.railway.app"
    ).rstrip("/")
    if origin and origin != expected:
        raise HTTPException(status_code=403, detail="Bu kaynaktan işlem yapılamaz.")


def current(db, request, mutate=False):
    token = request.cookies.get(account_auth.COOKIE)
    account = account_auth.require_account(db, token, now_ms())
    if mutate:
        same_origin(request)
        account_auth.require_csrf(token, request.headers.get("x-csrf-token"))
    return account


@router.post("/login")
def login(data: LoginInput, request: Request, response: Response, db: Db):
    same_origin(request)
    old_token = request.cookies.get(account_auth.COOKIE)
    token = account_auth.login(db, data.username.lower(), data.password, now_ms())
    if old_token:
        db.execute(
            delete(AccountSession).where(
                AccountSession.token_hash == account_auth.digest(old_token)
            )
        )
        db.commit()
    response.set_cookie(
        account_auth.COOKIE,
        token,
        max_age=account_auth.SESSION_MS // 1000,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return dict(authenticated=True)


@router.get("/session")
def session(request: Request, db: Db):
    account = current(db, request)
    return dict(
        username=account.username,
        can_manage_users=account.username == "bilalozok",
        csrf_token=account_auth.csrf_token(request.cookies[account_auth.COOKIE]),
    )


@router.post("/logout")
def logout(request: Request, response: Response, db: Db):
    current(db, request, mutate=True)
    db.execute(
        delete(AccountSession).where(
            AccountSession.token_hash == account_auth.digest(request.cookies[account_auth.COOKIE])
        )
    )
    db.commit()
    response.delete_cookie(
        account_auth.COOKIE, secure=True, httponly=True, samesite="strict", path="/"
    )
    return dict(authenticated=False)


def purchase_out(row):
    with localcontext() as ctx:
        ctx.prec = 80
        cost = Decimal(row.unit_price) * Decimal(row.quantity) + Decimal(row.fee)
    return dict(
        id=row.id,
        symbol=row.symbol,
        purchased_at=timestamp(row.purchased_ms),
        currency=row.currency,
        unit_price=row.unit_price,
        quantity=row.quantity,
        fee=row.fee,
        total_cost=format(cost, "f"),
        note=row.note,
    )


@router.post("/purchases", status_code=201)
def add_purchase(data: PurchaseInput, request: Request, db: Db):
    account = current(db, request, mutate=True)
    stamp = now_ms()
    purchased_ms = round(data.purchased_at.timestamp() * 1000)
    if purchased_ms > stamp or purchased_ms < 0:
        raise HTTPException(
            status_code=422, detail="Alış zamanı gelecekte veya 1970 öncesinde olamaz."
        )
    symbol = data.symbol.upper()
    known = db.get(BinanceSpotSymbol, symbol)
    if known is None:
        raise HTTPException(
            status_code=422, detail="Katalogda bulunan Binance USDT paritesini seç."
        )
    values = dict(
        symbol=symbol,
        purchased_ms=purchased_ms,
        currency=data.currency,
        unit_price=format(data.unit_price, "f"),
        quantity=format(data.quantity, "f"),
        fee=format(data.fee, "f"),
        note=data.note,
    )
    existing = db.get(Purchase, str(data.id))
    if existing:
        if existing.account_id != account.id:
            raise HTTPException(
                status_code=409, detail="Kayıt kimliği kullanılamıyor; yeniden dene."
            )
        if any(getattr(existing, key) != value for key, value in values.items()):
            raise HTTPException(
                status_code=409, detail="Bu kayıt kimliği farklı bilgilerle kullanılmış."
            )
        return purchase_out(existing)
    row = Purchase(id=str(data.id), account_id=account.id, created_ms=stamp, **values)
    db.add(row)
    result = purchase_out(row)
    db.commit()
    return result


def date_ms(value):
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(status_code=422, detail="Saat dilimi içeren tarih gerekli.")
    return round(value.timestamp() * 1000)


@router.get("/purchases")
def purchases(
    request: Request,
    db: Db,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    account = current(db, request)
    begin, finish = date_ms(start), date_ms(end)
    if begin is not None and finish is not None and begin >= finish:
        raise HTTPException(status_code=422, detail="Başlangıç bitişten önce olmalı.")
    conditions = [Purchase.account_id == account.id]
    if begin is not None:
        conditions.append(Purchase.purchased_ms >= begin)
    if finish is not None:
        conditions.append(Purchase.purchased_ms < finish)
    rows = db.scalars(
        select(Purchase)
        .where(*conditions)
        .order_by(Purchase.purchased_ms.desc(), Purchase.id)
        .offset(offset)
        .limit(limit + 1)
    ).all()
    shown = rows[:limit]
    return dict(
        purchases=[purchase_out(row) for row in shown],
        next_offset=offset + len(shown) if len(rows) > limit else None,
    )


@router.get("/purchases/summary")
def purchase_summary(
    request: Request, db: Db, start: datetime | None = None, end: datetime | None = None
):
    # Bounded selection for exact decimal arithmetic, never mixes currencies.
    account = current(db, request)
    begin, finish = date_ms(start), date_ms(end)
    if begin is not None and finish is not None and begin >= finish:
        raise HTTPException(status_code=422, detail="Başlangıç bitişten önce olmalı.")
    query = select(Purchase).where(Purchase.account_id == account.id)
    if begin is not None:
        query = query.where(Purchase.purchased_ms >= begin)
    if finish is not None:
        query = query.where(Purchase.purchased_ms < finish)
    rows = db.scalars(query.limit(10_001)).all()
    if len(rows) > 10_000:
        raise HTTPException(
            status_code=422, detail="10.000 kayıt sınırı aşıldı; tarih aralığını daralt."
        )
    groups = {}
    with localcontext() as ctx:
        ctx.prec = 80
        for row in rows:
            key = (row.symbol, row.currency)
            group = groups.setdefault(key, dict(quantity=Decimal(0), cost=Decimal(0), count=0))
            group["quantity"] += Decimal(row.quantity)
            group["cost"] += Decimal(row.unit_price) * Decimal(row.quantity) + Decimal(row.fee)
            group["count"] += 1
        result = [
            dict(
                symbol=symbol,
                currency=currency,
                purchases=g["count"],
                quantity=format(g["quantity"], "f"),
                total_cost=format(g["cost"], "f"),
                average_cost=format((g["cost"] / g["quantity"]).quantize(Decimal("1e-18")), "f"),
            )
            for (symbol, currency), g in sorted(groups.items())
        ]
    return dict(groups=result, note="Komisyon dahil alış maliyeti; satış ve güncel değer içermez.")


@router.get("/purchases/valuation")
def purchase_valuation(
    request: Request, db: Db, start: datetime | None = None, end: datetime | None = None
):
    account = current(db, request)
    begin, finish = date_ms(start), date_ms(end)
    if begin is not None and finish is not None and begin >= finish:
        raise HTTPException(status_code=422, detail="Başlangıç bitişten önce olmalı.")
    query = select(Purchase).where(Purchase.account_id == account.id)
    if begin is not None:
        query = query.where(Purchase.purchased_ms >= begin)
    if finish is not None:
        query = query.where(Purchase.purchased_ms < finish)
    rows = db.scalars(query.limit(10_001)).all()
    if len(rows) > 10_000:
        raise HTTPException(
            status_code=422, detail="10.000 kayıt sınırı aşıldı; tarih aralığını daralt."
        )
    return purchase_analysis.valuation(db, rows, now_ms())


@router.get("/purchases/price-range")
def purchase_price_range(
    request: Request,
    db: Db,
    symbol: str = Query(pattern=r"^[A-Za-z0-9]{1,64}$"),
    start: datetime | None = None,
    end: datetime | None = None,
):
    account = current(db, request)
    symbol = symbol.upper()
    if (
        db.scalar(
            select(Purchase.id)
            .where(Purchase.account_id == account.id, Purchase.symbol == symbol)
            .limit(1)
        )
        is None
    ):
        raise HTTPException(status_code=404, detail="Bu coin için özel alış kaydı bulunamadı.")
    stamp = now_ms()
    finish = date_ms(end) if end is not None else stamp
    begin = date_ms(start) if start is not None else finish - 2 * 86_400_000
    if begin < 0 or begin >= finish or finish - begin > 31 * 86_400_000:
        raise HTTPException(
            status_code=422,
            detail="Başlangıç bitişten önce olmalı; aralık en fazla 31 gün olabilir.",
        )
    if finish > stamp:
        raise HTTPException(status_code=422, detail="Bitiş zamanı gelecekte olamaz.")
    result = purchase_analysis.price_range(db, symbol, begin, finish, stamp)
    markers = db.scalars(
        select(Purchase)
        .where(
            Purchase.account_id == account.id,
            Purchase.symbol == symbol,
            Purchase.purchased_ms >= begin,
            Purchase.purchased_ms < finish,
        )
        .order_by(Purchase.purchased_ms, Purchase.id)
        .limit(1001)
    ).all()
    if len(markers) > 1000:
        raise HTTPException(
            status_code=422, detail="1.000 alış işareti sınırı aşıldı; aralığı daralt."
        )
    result["purchases"] = [purchase_out(row) for row in markers]
    return result


@router.delete("/purchases/{purchase_id}")
def remove_purchase(purchase_id: UUID, request: Request, db: Db):
    account = current(db, request, mutate=True)
    row = db.scalar(
        select(Purchase).where(Purchase.id == str(purchase_id), Purchase.account_id == account.id)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Alış kaydı bulunamadı.")
    db.delete(row)
    db.commit()
    return dict(deleted=True)


class PortfolioObservationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(pattern=r"^[A-Za-z0-9]{1,64}$")


def require_owned_symbol(db, account_id, symbol):
    if (
        db.scalar(
            select(Purchase.id)
            .where(Purchase.account_id == account_id, Purchase.symbol == symbol)
            .limit(1)
        )
        is None
    ):
        raise HTTPException(status_code=404, detail="Bu coin için özel alış kaydı bulunamadı.")


@router.get("/portfolio")
def portfolio(request: Request, db: Db, offset: int = Query(0, ge=0)):
    account = current(db, request)
    stamp = now_ms()
    symbols = db.scalars(
        select(Purchase.symbol)
        .where(Purchase.account_id == account.id)
        .distinct()
        .order_by(Purchase.symbol)
        .offset(offset)
        .limit(11)
    ).all()
    result = []
    for symbol in symbols[:10]:
        item = portfolio_technical.technical(db, symbol, stamp)
        previous = portfolio_technical.previous_day(db, account.id, symbol, stamp)
        from app.services import portfolio_auto

        item["automatic_observation"] = portfolio_auto.status(db, account.id, symbol, stamp, item)
        item["previous_observation"] = previous
        if previous is None:
            item["daily_change"] = "Önceki güne ait kayıt yok."
        elif previous["method"] != portfolio_technical.VERSION or item["status"] != "ready":
            item["daily_change"] = "Kural/veri farklı; günlük yön karşılaştırması yapılmadı."
        else:
            prior = {h["interval"]: h for h in previous["technical"]["horizons"]}
            changes = []
            for horizon in item["horizons"]:
                old = prior.get(horizon["interval"])
                if old is None or old["status"] != "ready" or horizon["status"] != "ready":
                    changes.append(horizon["name"] + ": veri karşılaştırılamıyor")
                elif old["assessment"] != horizon["assessment"]:
                    changes.append(horizon["name"] + ": " + old["label"] + " → " + horizon["label"])
                else:
                    changes.append(horizon["name"] + ": yön aynı; seviyeler değişebilir")
            item["daily_change"] = previous["day"] + " kaydına göre · " + " · ".join(changes)
        result.append(item)
    return dict(
        version=portfolio_technical.VERSION,
        as_of=timestamp(stamp),
        coins=result,
        next_offset=offset + 10 if len(symbols) > 10 else None,
    )


@router.post("/portfolio/observations")
def save_portfolio_observation(data: PortfolioObservationInput, request: Request, db: Db):
    account = current(db, request, mutate=True)
    symbol = data.symbol.upper()
    require_owned_symbol(db, account.id, symbol)
    return portfolio_technical.save_daily(db, account.id, symbol, now_ms())


@router.get("/portfolio/history")
def portfolio_history(
    request: Request, db: Db, symbol: str = Query(pattern=r"^[A-Za-z0-9]{1,64}$")
):
    from app.db.models.portfolio_observation import PortfolioObservation

    account = current(db, request)
    symbol = symbol.upper()
    require_owned_symbol(db, account.id, symbol)
    rows = db.scalars(
        select(PortfolioObservation)
        .where(PortfolioObservation.account_id == account.id, PortfolioObservation.symbol == symbol)
        .order_by(PortfolioObservation.local_day.desc())
        .limit(30)
    ).all()
    return dict(
        symbol=symbol,
        observations=[portfolio_technical.observation_out(row) for row in rows],
        note=(
            "Türkiye takvim gününde ilk kullanıcı gözlemi saklanır; otomatik "
            "günlük kayıt veya geçmişe dönük üretim yapılmaz."
        ),
    )


@router.post("/portfolio/refresh")
def refresh_portfolio_timeframes(data: PortfolioObservationInput, request: Request, db: Db):
    from app.services import portfolio_feeds
    from app.services.binance_market import BinanceMarketError

    account = current(db, request, mutate=True)
    symbol = data.symbol.upper()
    require_owned_symbol(db, account.id, symbol)
    known = db.get(BinanceSpotSymbol, symbol)
    if known is None or not known.active:
        raise HTTPException(status_code=422, detail="Aktif Binance Spot paritesi gerekli.")
    try:
        result = portfolio_feeds.refresh_symbol(db, symbol, now_ms())
    except BinanceMarketError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail="Orta/uzun vade verisi alınamadı; daha sonra yeniden dene.",
        ) from exc
    return dict(symbol=symbol, feeds=result)


class PortfolioSnapshotInput(PortfolioObservationInput):
    request_id: UUID


@router.post("/portfolio/snapshots")
def save_portfolio_snapshot(data: PortfolioSnapshotInput, request: Request, db: Db):
    from app.services import portfolio_snapshots

    account = current(db, request, mutate=True)
    symbol = data.symbol.upper()
    require_owned_symbol(db, account.id, symbol)
    return portfolio_snapshots.save(db, account.id, symbol, str(data.request_id), now_ms())


@router.get("/portfolio/snapshots")
def portfolio_snapshot_history(
    request: Request, db: Db, symbol: str = Query(pattern=r"^[A-Za-z0-9]{1,64}$")
):
    from app.db.models.portfolio_snapshot import PortfolioSnapshot
    from app.services import portfolio_snapshots

    account = current(db, request)
    symbol = symbol.upper()
    require_owned_symbol(db, account.id, symbol)
    rows = db.scalars(
        select(PortfolioSnapshot)
        .where(
            PortfolioSnapshot.account_id == account.id,
            PortfolioSnapshot.symbol == symbol,
        )
        .order_by(PortfolioSnapshot.observed_ms.desc(), PortfolioSnapshot.id.desc())
        .limit(30)
    ).all()
    return dict(symbol=symbol, snapshots=[portfolio_snapshots.output(row) for row in rows])


class CandidateScanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


@router.post("/candidate-scans", status_code=201)
def create_candidate_scan(data: CandidateScanInput, request: Request, db: Db):
    from app.services import candidate_archive

    owner = current(db, request, mutate=True).id
    try:
        with SessionLocal() as scan_db:
            if scan_db.bind.dialect.name == "postgresql":
                scan_db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            return candidate_archive.create(scan_db, owner, str(data.request_id), now_ms())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503, detail="Tarama kaydedilemedi; aynı isteği yeniden deneyebilirsin."
        ) from exc


@router.get("/candidate-scans")
def list_candidate_scans(
    request: Request,
    db: Db,
    offset: int = Query(0, ge=0),
    symbol: str | None = Query(None, pattern=r"^[A-Za-z0-9]{1,64}$"),
    kind: Literal["all", "legacy", "tracked", "open", "ended"] = "all",
    start: datetime | None = None,
    end: datetime | None = None,
):
    from app.services.candidate_scan_list import listing

    owner = current(db, request).id
    if any(value is not None and value.utcoffset() is None for value in (start, end)):
        raise HTTPException(status_code=422, detail="Tarih aralığında saat dilimi gerekli.")
    if start is not None and end is not None and end <= start:
        raise HTTPException(status_code=422, detail="Bitiş, başlangıçtan sonra olmalı.")
    return listing(
        db,
        owner,
        now_ms(),
        offset,
        symbol,
        kind,
        int(start.timestamp() * 1000) if start is not None else None,
        int(end.timestamp() * 1000) if end is not None else None,
    )


@router.get("/candidate-scans/history")
def candidate_symbol_history(
    request: Request, db: Db, symbol: str = Query(pattern=r"^[A-Za-z0-9]{1,64}$")
):
    from app.services import candidate_archive
    from app.services.candidate_lifecycle import enrich

    return enrich(db, candidate_archive.history(db, current(db, request).id, symbol.upper()))


@router.get("/candidate-scans/study")
def candidate_study(request: Request, db: Db, days: int = Query(7, ge=1, le=30)):
    from app.services import candidate_study as study

    try:
        return study.report(db, current(db, request).id, now_ms(), days)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/candidate-scans/{scan_id}")
def get_candidate_scan(scan_id: UUID, request: Request, db: Db):
    from app.db.models.candidate_scan import CandidateScan
    from app.services import candidate_archive

    owner = current(db, request).id
    row = db.scalar(
        select(CandidateScan).where(
            CandidateScan.id == str(scan_id), CandidateScan.account_id == owner
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Kayıtlı tarama bulunamadı.")
    return candidate_archive.detail(row)


@router.delete("/candidate-scans/{scan_id}")
def delete_candidate_scan(scan_id: UUID, request: Request, db: Db):
    from app.db.models.candidate_scan import CandidateScan

    owner = current(db, request, mutate=True).id
    from app.db.models.candidate_outcome import CandidateOutcome

    owned = db.scalar(
        select(CandidateScan.id).where(
            CandidateScan.id == str(scan_id),
            CandidateScan.account_id == owner,
        )
    )
    if owned is None:
        raise HTTPException(status_code=404, detail="Kayıtlı tarama bulunamadı.")
    from app.db.models.candidate_observation import CandidateObservation

    db.execute(delete(CandidateObservation).where(CandidateObservation.scan_id == owned))
    db.execute(delete(CandidateOutcome).where(CandidateOutcome.scan_id == owned))
    removed = db.execute(
        delete(CandidateScan)
        .where(
            CandidateScan.id == str(scan_id),
            CandidateScan.account_id == owner,
        )
        .returning(CandidateScan.id)
    ).scalar_one_or_none()
    if removed is None:
        raise HTTPException(status_code=404, detail="Kayıtlı tarama bulunamadı.")
    db.commit()
    return dict(deleted=True, id=removed)


def require_candidate_scan(db, owner, scan_id):
    from app.db.models.candidate_scan import CandidateScan

    row = db.scalar(
        select(CandidateScan).where(
            CandidateScan.id == str(scan_id),
            CandidateScan.account_id == owner,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Kayıtlı tarama bulunamadı.")
    return row


@router.get("/candidate-scans/{scan_id}/outcomes")
def get_candidate_outcomes(scan_id: UUID, request: Request, db: Db):
    from app.services import candidate_outcomes

    row = require_candidate_scan(db, current(db, request).id, scan_id)
    return candidate_outcomes.results(db, row, now_ms())


@router.post("/candidate-scans/{scan_id}/outcomes")
def update_candidate_outcomes(scan_id: UUID, request: Request, db: Db):
    from app.services import candidate_outcomes

    row = require_candidate_scan(db, current(db, request, mutate=True).id, scan_id)
    return candidate_outcomes.results(db, row, now_ms(), persist=True)


@router.get("/candidate-scans/{scan_id}/observations")
def candidate_observations(scan_id: UUID, request: Request, db: Db):
    from app.services.candidate_observer import history

    row = require_candidate_scan(db, current(db, request).id, scan_id)
    return history(db, row)


class NewAccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=15, max_length=128)


def administrator(db, request, mutate=False):
    account = current(db, request, mutate=mutate)
    if account.username != "bilalozok":
        raise HTTPException(status_code=403, detail="Kullanıcı yönetimi için yetkin yok.")
    return account


@router.get("/admin/users")
def list_accounts(request: Request, db: Db):
    administrator(db, request)
    rows = db.scalars(select(Account).order_by(Account.created_ms, Account.username)).all()
    return dict(
        users=[
            dict(username=r.username, active=r.active, created_at=timestamp(r.created_ms))
            for r in rows
        ]
    )


@router.post("/admin/users", status_code=201)
def add_account(data: NewAccountInput, request: Request, db: Db):
    administrator(db, request, mutate=True)
    username = data.username.lower()
    if db.scalar(select(Account.id).where(Account.username == username)):
        raise HTTPException(status_code=409, detail="Bu kullanıcı adı zaten kayıtlı.")
    if not account_auth.HASH_SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="Hesap işlemi meşgul; biraz sonra dene.")
    try:
        account_auth.create_account(db, username, data.password, now_ms())
    except (ValueError, IntegrityError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Bu kullanıcı adı zaten kayıtlı.") from exc
    finally:
        account_auth.HASH_SLOTS.release()
    return dict(username=username, active=True)


@router.post("/admin/users/password")
def reset_account_password(data: NewAccountInput, request: Request, db: Db):
    admin = administrator(db, request, mutate=True)
    target = db.scalar(
        select(Account).where(Account.username == data.username.lower()).with_for_update()
    )
    if target is None:
        raise HTTPException(
            status_code=404, detail="Kullanıcı bulunamadı; yeni hesap oluşturulmadı."
        )
    if not account_auth.HASH_SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="Hesap işlemi meşgul; biraz sonra dene.")
    try:
        target.password_hash = account_auth.password_hash(data.password)
        db.execute(delete(AccountSession).where(AccountSession.account_id == target.id))
        changed_self = target.id == admin.id
        username = target.username
        db.commit()
    finally:
        account_auth.HASH_SLOTS.release()
    return dict(username=username, password_changed=True, reauthenticate=changed_self)


@router.get("/candidate-scans/{scan_id}/tracking")
def candidate_tracking_status(scan_id: UUID, request: Request, db: Db):
    from app.services.candidate_tracking import overview

    row = require_candidate_scan(db, current(db, request).id, scan_id)
    return overview(db, row, now_ms())


@router.post("/candidate-scans/{scan_id}/refresh")
def refresh_candidate_timeframes(
    scan_id: UUID, data: PortfolioObservationInput, request: Request, db: Db
):
    from app.services import portfolio_feeds, portfolio_technical
    from app.services.binance_market import BinanceMarketError

    row = require_candidate_scan(db, current(db, request, mutate=True).id, scan_id)
    symbol = data.symbol.upper()
    if symbol not in {c["symbol"] for c in row.payload["candidates"]}:
        raise HTTPException(status_code=404, detail="Bu taramada aday coin bulunamadı.")
    known = db.get(BinanceSpotSymbol, symbol)
    if known is None or not known.active:
        raise HTTPException(status_code=422, detail="Aktif Binance Spot paritesi gerekli.")
    try:
        feeds = portfolio_feeds.refresh_symbol(db, symbol, now_ms())
    except BinanceMarketError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail="Orta/uzun vade verisi alınamadı; daha sonra yeniden dene.",
        ) from exc
    stamp = now_ms()
    return dict(
        symbol=symbol,
        feeds=feeds,
        as_of=timestamp(stamp).isoformat(),
        technical=portfolio_technical.technical(db, symbol, stamp),
        note="Yeni değerlendirme; kayıtlı tarama ve sonuçlar değiştirilmedi.",
    )


@router.get("/dashboard")
def personal_dashboard(request: Request, db: Db, offset: int = Query(0, ge=0)):
    from app.services.personal_dashboard import summary

    account = current(db, request)
    return summary(db, account.id, now_ms(), offset)


@router.get("/dashboard/early")
def personal_early_dashboard(request: Request, db: Db):
    from app.services.personal_dashboard import early_summary

    return early_summary(db, current(db, request).id, now_ms())


@router.get("/dashboard/returns")
def dashboard_returns(request: Request, db: Db):
    from app.services.dashboard_returns import summary

    account = current(db, request)
    try:
        return summary(db, account.id, now_ms())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
