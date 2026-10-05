def test_dashboard_served_and_existing_chart_kept(client):
    response = client.get("/analysis/binance/dashboard?symbol=PEOPLEUSDT")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Analiz merkezi" in response.text
    assert "https://cdn" not in response.text
    assert client.get("/analysis/binance/chart").status_code == 200
