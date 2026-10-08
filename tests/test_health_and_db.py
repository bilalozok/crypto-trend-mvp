def test_root(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/analysis/binance/dashboard?tab=overview"


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data == {"status": "ok"}


def test_debug_db_is_not_exposed(client):
    assert client.get("/debug/db").status_code == 404
