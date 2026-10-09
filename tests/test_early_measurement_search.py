from app.db.models.early_formation import EarlyFormation
from app.db.session import SessionLocal
from app.services.early_measurement_search import search_records


def test_filters_stable_pages_and_inclusive_start_exclusive_end():
    stamp = 1800000000000
    with SessionLocal() as db:
        for i in range(25):
            db.add(
                EarlyFormation(
                    symbol="BTCUSDT",
                    rule_hash="old" if i % 2 else "new",
                    pattern="dip",
                    close_ms=stamp + i,
                    observed_ms=stamp + i,
                    entry_ms=stamp + i + 1000,
                    snapshot=dict(
                        name="Çift dip",
                        direction="up",
                        indicator_status="ready",
                        latest=dict(rsi=i),
                    ),
                    outcomes={},
                    complete=False,
                )
            )
        db.commit()
        first = search_records(db, stamp + 100, symbol=" btcusdt ", name="Çift dip", direction="up")
        assert len(first["rows"]) == 20 and first["next_offset"] == 20
        db.add(
            EarlyFormation(
                symbol="BTCUSDT",
                rule_hash="new",
                pattern="dip",
                close_ms=stamp + 200,
                observed_ms=stamp + 200,
                entry_ms=stamp + 1000,
                snapshot=dict(name="Çift dip", direction="up", indicator_status="ready"),
                outcomes={},
                complete=False,
            )
        )
        db.commit()
        second = search_records(db, stamp + 300, as_of=first["as_of"], offset=20)
        assert len(second["rows"]) == 5 and second["next_offset"] is None
        assert (
            search_records(db, stamp + 300, start_ms=stamp + 2, end_ms=stamp + 3)["rows"][0][
                "saved_context"
            ]["latest"]["rsi"]
            == 2
        )
        assert not search_records(db, stamp + 300, direction="down")["rows"]
        assert not db.dirty and not db.new and not db.deleted


def test_endpoint_requires_login_and_valid_dates(client):
    url = "/analysis/binance/early-study/records"
    assert client.get(url).status_code == 200
    assert client.get(url, params={"start": "2026-10-10T00:00:00"}).status_code == 422
    assert (
        client.get(
            url, params={"start": "2026-10-11T00:00:00+03:00", "end": "2026-10-10T00:00:00+03:00"}
        ).status_code
        == 422
    )
    assert client.get(url, params={"direction": "bad"}).status_code == 422
    client.cookies.clear()
    assert client.get(url).status_code == 401
