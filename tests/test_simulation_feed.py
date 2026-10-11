import pytest
from test_private_purchases import STAMP, auth
from test_simulation import order
from test_simulation import private_client as setup

from app.db.models.simulation import SimulationCandle
from app.db.session import SessionLocal
from app.workers import simulation_worker as worker


@pytest.fixture
def private_client(monkeypatch):
    yield from setup.__wrapped__(monkeypatch)


def bars(stamp):
    opened = stamp // worker.BAR * worker.BAR - worker.BAR
    return [[opened, "10", "11", "9", "10", "100", opened + worker.BAR - 1]]


def test_parse_excludes_open_and_rejects_invalid_or_stale():
    payload = bars(STAMP)
    opened = STAMP // worker.BAR * worker.BAR
    assert (
        len(
            worker.parse(
                payload + [[opened, 10, 11, 9, 10, 100, opened + worker.BAR - 1]], "BTCUSDT", STAMP
            )
        )
        == 1
    )
    for bad in (
        [],
        payload * 2,
        [[opened - 2 * worker.BAR, 10, 11, 9, 10, 100, opened - worker.BAR - 1]],
        [[payload[0][0], 10, 11, 9, "nan", 100, payload[0][6]]],
    ):
        with pytest.raises(ValueError):
            worker.parse(bad, "BTCUSDT", STAMP)


def test_worker_only_live_symbols_and_caches_closed_bar(private_client, monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "now_ms", lambda: STAMP)
    monkeypatch.setattr(worker, "_get", lambda path, params: calls.append(params) or bars(STAMP))
    assert worker.collect(SessionLocal) == 0 and calls == []
    h = auth(private_client)
    private_client.post("/account/simulations", headers=h, json=order())
    private_client.post("/account/simulations", headers=h, json=order())
    assert worker.collect(SessionLocal) == 0
    assert len(calls) == 1 and calls[0]["interval"] == "5m"
    assert worker.collect(SessionLocal) == 0 and len(calls) == 1
    with SessionLocal() as db:
        assert db.get(SimulationCandle, ("BTCUSDT", STAMP - worker.BAR)) is not None
