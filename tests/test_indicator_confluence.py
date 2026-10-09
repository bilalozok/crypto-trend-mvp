from app.services.indicator_confluence import summarize


def horizon():
    return dict(
        status="ready",
        latest=dict(close=110, ema50=100, rsi=60, histogram=1),
        series=[dict(ema50=99), dict(ema50=100)],
    )


def pattern(direction="up", stage="forming", current=False):
    return dict(
        name="Test",
        direction=direction,
        status=stage,
        current_confirmation=current,
        report_volume_supported=True,
    )


def test_same_evidence_supports_up_and_conflicts_down_without_score():
    up = summarize(horizon(), [pattern()])["patterns"][0]
    down = summarize(horizon(), [pattern("down")])["patterns"][0]
    assert [g["state"] for g in up["groups"][:2]] == ["supports", "supports"]
    assert [g["state"] for g in down["groups"][:2]] == ["conflicts", "conflicts"]
    assert up["stage"] == "forming"
    assert up["groups"][2]["state"] == "neutral"
    assert "score" not in up


def test_mixed_momentum_no_double_vote_and_stale_confirmations_excluded():
    h = horizon()
    h["latest"]["histogram"] = -1
    p = summarize(h, [pattern(), pattern(stage="confirmed"), pattern(stage="invalidated")])
    assert len(p["patterns"]) == 1
    assert p["patterns"][0]["groups"][1]["state"] == "mixed"
    assert len([g for g in p["patterns"][0]["groups"] if "Momentum" in g["name"]]) == 1
    h["status"] = "stale_data"
    assert summarize(h, [pattern()])["patterns"] == []


def test_current_volume_and_rsi_caution():
    h = horizon()
    h["latest"]["rsi"] = 75
    p = pattern(stage="confirmed", current=True)
    p["report_volume_supported"] = False
    result = summarize(h, [p])["patterns"][0]
    assert result["stage"] == "confirmed"
    assert result["groups"][2]["state"] == "limited"
    assert result["groups"][3]["state"] == "caution"
