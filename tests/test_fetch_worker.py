import pytest

from app.db.models.candle import Candle
from app.db.session import SessionLocal
from app.workers.scheduler import FetchSettings, load_settings, main, run_once
from app.workers.tasks import refresh_candles


def test_worker_settings_defaults(monkeypatch):
    for name in ("FETCH_SYMBOLS", "FETCH_INTERVALS", "FETCH_LIMIT"):
        monkeypatch.delenv(name, raising=False)
    assert load_settings() == FetchSettings(("BTCUSDT",), ("1h",), 100)


def test_worker_settings_normalize_and_deduplicate(monkeypatch):
    monkeypatch.setenv("FETCH_SYMBOLS", " btcusdt,ETHUSDT,BTCUSDT ")
    monkeypatch.setenv("FETCH_INTERVALS", "1h,1h")
    assert load_settings().symbols == ("BTCUSDT", "ETHUSDT")
    assert load_settings().intervals == ("1h",)


@pytest.mark.parametrize(
    "name,value",
    [
        ("DATABASE_URL", ""),
        ("DATABASE_URL", "postgres://${{PGUSER}}"),
        ("DATABASE_URL", "invalid"),
        ("FETCH_SYMBOLS", ""),
        ("FETCH_SYMBOLS", "BTC/USDT"),
        ("FETCH_INTERVALS", "1M"),
        ("FETCH_LIMIT", "0"),
        ("FETCH_LIMIT", "101"),
        ("FETCH_LIMIT", "invalid"),
        ("FETCH_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,ADAUSDT,XRPUSDT,DOGEUSDT"),
    ],
)
def test_invalid_settings_fail_before_fetch(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    assert main() == 2


def test_postgres_url_uses_psycopg_and_connect_timeout(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://user:secret@db:5432/railway")
    load_settings()
    import os

    from sqlalchemy.engine import make_url

    url = make_url(os.environ["DATABASE_URL"])
    assert url.drivername == "postgresql+psycopg"
    assert url.query["connect_timeout"] == "10"
    assert url.password == "secret"


def test_task_refreshes_and_updates_candles(monkeypatch):
    rows = [[1700000000000, "100", "110", "90", "105", "1000"]]
    monkeypatch.setattr("app.workers.tasks.fetch_klines", lambda **kwargs: rows)
    assert refresh_candles("BTCUSDT", "1h", 100) == 1
    rows[0][4] = "108"
    assert refresh_candles("BTCUSDT", "1h", 100) == 1
    with SessionLocal() as db:
        assert db.query(Candle).count() == 1
        assert db.query(Candle).first().close == 108


def test_worker_reports_failure_and_continues(monkeypatch, caplog):
    calls = []

    def refresh(symbol, interval, limit):
        calls.append((symbol, interval, limit))
        if symbol == "BTCUSDT":
            raise RuntimeError("sensitive secret")
        return 100

    monkeypatch.setattr("app.workers.tasks.refresh_candles", refresh)
    result = run_once(FetchSettings(("BTCUSDT", "ETHUSDT"), ("1h",), 100))
    assert result == 1
    assert len(calls) == 2
    assert "sensitive secret" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_worker_returns_success_and_closes_engines(monkeypatch):
    calls = []
    monkeypatch.setattr("app.workers.tasks.refresh_candles", lambda *args: 100)
    monkeypatch.setattr("app.db.session.engine.dispose", lambda: calls.append("session"))
    monkeypatch.setattr("app.db.base.engine.dispose", lambda: calls.append("base"))
    assert run_once(FetchSettings(("BTCUSDT",), ("1h",), 100)) == 0
    assert calls == ["session", "base"]
