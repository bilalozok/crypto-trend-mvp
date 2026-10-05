from uuid import uuid4

import pytest
from sqlalchemy import inspect

from app.db.models.forward_report import ForwardReport
from app.db.models.forward_signal import ForwardSignal
from app.db.session import SessionLocal
from app.services import forward_reports
from app.services.formations import BAR
from tests.test_forward_summary import add


def test_saved_payload_preserves_pending_after_live_result_changes(monkeypatch):
    monkeypatch.setattr(forward_reports, "rules_hash", lambda: "a" * 64)
    with SessionLocal() as db:
        add(db, "a" * 64)
        info = forward_reports.create_report(db, 200 * BAR)
        report = db.get(ForwardReport, info["id"])
        assert report.payload["summary"]["total_signals"] == 1
        assert report.payload["signals"][0]["outcomes"] == {}
        row = db.get(ForwardSignal, ("BTCUSDT", "a" * 64, 190 * BAR))
        row.outcomes = {"4": dict(status="complete", result=dict(net_return_pct=2))}
        db.commit()
        db.expire_all()
        assert db.get(ForwardReport, info["id"]).payload["signals"][0]["outcomes"] == {}
        assert db.get(ForwardReport, info["id"]).payload["summary"]["horizons"][0]["pending"] == 1


def test_same_request_id_returns_original_and_rejects_changed_period(monkeypatch):
    monkeypatch.setattr(forward_reports, "rules_hash", lambda: "a" * 64)
    request_id = str(uuid4())
    with SessionLocal() as db:
        first = forward_reports.create_report(db, 200 * BAR, request_id=request_id)
        second = forward_reports.create_report(db, 201 * BAR, request_id=request_id)
        assert first == second
        assert db.query(ForwardReport).count() == 1
        db.rollback()
        with pytest.raises(ValueError):
            forward_reports.create_report(db, 202 * BAR, days=1, request_id=request_id)
        assert db.query(ForwardReport).count() == 1


def test_snapshot_filters_rule_and_observation_window(monkeypatch):
    monkeypatch.setattr(forward_reports, "rules_hash", lambda: "a" * 64)
    with SessionLocal() as db:
        add(db, "a" * 64)
        add(db, "b" * 64)
        add(db, "a" * 64, observed=10 * BAR)
        add(db, "a" * 64, observed=201 * BAR)
        report = forward_reports.create_report(db, 200 * BAR, days=1)
        row = db.get(ForwardReport, report["id"])
        assert row.signal_count == len(row.payload["signals"]) == 1
        assert row.payload["signals"][0]["rule_hash"] == "a" * 64


def test_report_failure_rolls_back_and_size_limit(monkeypatch):
    monkeypatch.setattr(forward_reports, "rules_hash", lambda: "a" * 64)
    monkeypatch.setattr(forward_reports, "MAX_SIGNALS", 0)
    with SessionLocal() as db:
        add(db, "a" * 64)
        with pytest.raises(ValueError):
            forward_reports.create_report(db, 200 * BAR)
        assert db.query(ForwardReport).count() == 0
        assert db.query(ForwardSignal).count() == 1


def test_list_is_paginated_without_loading_report_payload(monkeypatch):
    monkeypatch.setattr(forward_reports, "rules_hash", lambda: "a" * 64)
    with SessionLocal() as db:
        first = forward_reports.create_report(db, 200 * BAR)
        second = forward_reports.create_report(db, 201 * BAR)
        result = forward_reports.list_reports(db, limit=1)
        assert result["total"] == 2 and result["next_offset"] == 1
        assert result["reports"][0]["id"] == second["id"]
        assert "payload" not in result["reports"][0]
        last = forward_reports.list_reports(db, limit=1, offset=1)
        assert last["next_offset"] is None and last["reports"][0]["id"] == first["id"]


def test_api_create_open_download_and_validations(client, monkeypatch):
    monkeypatch.setattr("app.main.now_ms", lambda: 200 * BAR)
    monkeypatch.setattr(forward_reports, "rules_hash", lambda: "a" * 64)
    request_id = str(uuid4())
    response = client.post("/analysis/binance/forward/reports", params=dict(request_id=request_id))
    assert response.status_code == 201
    info = response.json()
    opened = client.get(info["report_path"])
    assert opened.json()["summary"]["total_signals"] == 0
    downloaded = client.get(info["download_path"])
    assert downloaded.json() == opened.json()
    assert "attachment;" in downloaded.headers["content-disposition"]
    assert client.get("/analysis/binance/forward/reports").json()["total"] == 1
    assert client.post("/analysis/binance/forward/reports?days=0").status_code == 422
    assert client.post("/analysis/binance/forward/reports?request_id=bad").status_code == 422
    assert client.get("/analysis/binance/forward/reports/" + str(uuid4())).status_code == 404
    html = client.get("/analysis/binance/dashboard").text
    assert 'id="saved-create"' in html and 'id="saved-table"' in html


def test_report_migration_preserves_existing_forward_signals(tmp_path, monkeypatch):
    import importlib

    from sqlalchemy import create_engine

    command = importlib.import_module("alembic.command")
    Config = importlib.import_module("alembic.config").Config
    url = "sqlite:///" + str(tmp_path / "reports.db")
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "9d15c2026c01")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO binance_forward_signals "
            "(symbol,rule_hash,signal_close_ms,observed_ms,entry_ms,snapshot,outcomes,complete) "
            "VALUES ('BTCUSDT','a',1,1,2,'{}','{}',0)"
        )
    command.upgrade(config, "head")
    assert "binance_forward_reports" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT count(*) FROM binance_forward_signals").scalar() == 1
        )
    engine.dispose()
