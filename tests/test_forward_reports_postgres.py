import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.db.models.forward_report import ForwardReport
from app.db.models.forward_signal import ForwardSignal
from app.services import forward_reports
from app.services.formations import BAR
from tests.test_formation_history_postgres import sessions  # noqa: F401
from tests.test_forward_summary import add

pytestmark = pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="PostgreSQL CI only")


def test_postgres_snapshot_survives_worker_update(sessions, monkeypatch):  # noqa: F811
    monkeypatch.setattr(forward_reports, "rules_hash", lambda: "a" * 64)
    with sessions() as db:
        add(db, "a" * 64)
    original = forward_reports.summarize

    def interleaved(db, *args):
        summary = original(db, *args)
        with sessions() as writer:
            row = writer.get(ForwardSignal, ("BTCUSDT", "a" * 64, 190 * BAR))
            row.outcomes = {"4": dict(status="complete", result=dict(net_return_pct=2))}
            writer.commit()
        return summary

    monkeypatch.setattr(forward_reports, "summarize", interleaved)
    with sessions() as db:
        info = forward_reports.create_report(db, 200 * BAR)
        payload = db.get(ForwardReport, info["id"]).payload
        assert payload["summary"]["horizons"][0]["pending"] == 1
        assert payload["signals"][0]["outcomes"] == {}
        assert (
            db.get(ForwardSignal, ("BTCUSDT", "a" * 64, 190 * BAR)).outcomes["4"]["status"]
            == "complete"
        )


def test_concurrent_duplicate_report_request_persists_once(sessions, monkeypatch):  # noqa: F811
    monkeypatch.setattr(forward_reports, "rules_hash", lambda: "a" * 64)
    request_id = str(uuid4())

    def create(_):
        with sessions() as db:
            return forward_reports.create_report(db, 200 * BAR, request_id=request_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, [1, 2]))
    assert results[0] == results[1]
    with sessions() as db:
        assert db.query(ForwardReport).count() == 1
