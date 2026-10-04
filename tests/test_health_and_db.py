def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, dict)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data == {"status": "ok"}


def test_debug_db_is_not_exposed(client):
    assert client.get("/debug/db").status_code == 404
