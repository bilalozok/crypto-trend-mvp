from datetime import datetime


def _to_dt(v: str) -> datetime:
    # FastAPI çoğu zaman ISO döner. "Z" varsa +00:00'a çevir.
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    return datetime.fromisoformat(v)


def test_latest_supports_sort_asc_desc(client):
    r_desc = client.get("/candles/latest?symbol=BTCUSDT&interval=1m&limit=5&sort=desc")
    assert r_desc.status_code == 200
    data_desc = r_desc.json()
    assert isinstance(data_desc, list)

    if len(data_desc) >= 2:
        times = [_to_dt(item["open_time"]) for item in data_desc]
        assert times == sorted(times, reverse=True)

    r_asc = client.get("/candles/latest?symbol=BTCUSDT&interval=1m&limit=5&sort=asc")
    assert r_asc.status_code == 200
    data_asc = r_asc.json()
    assert isinstance(data_asc, list)

    if len(data_asc) >= 2:
        times = [_to_dt(item["open_time"]) for item in data_asc]
        assert times == sorted(times)


def test_latest_supports_limit_offset(client):
    r1 = client.get("/candles/latest?symbol=BTCUSDT&interval=1m&limit=2&offset=0&sort=asc")
    r2 = client.get("/candles/latest?symbol=BTCUSDT&interval=1m&limit=2&offset=2&sort=asc")

    assert r1.status_code == 200
    assert r2.status_code == 200

    d1 = r1.json()
    d2 = r2.json()

    assert isinstance(d1, list)
    assert isinstance(d2, list)
    assert len(d1) <= 2
    assert len(d2) <= 2

    if len(d1) == 2 and len(d2) > 0:
        assert d1 != d2
