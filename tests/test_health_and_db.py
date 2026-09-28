def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, dict)


def test_debug_db_initial(client):
    r = client.get("/debug/db")
    assert r.status_code == 200
    data = r.json()
    assert "total" in data
    assert "last" in data
