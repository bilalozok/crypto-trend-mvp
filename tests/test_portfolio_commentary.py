import pytest

from app.services.portfolio_commentary import describe, reviews


def pattern(**changes):
    p = dict(
        name="Çift dip",
        status="confirmed",
        direction="up",
        current_confirmation=True,
        report_volume_supported=True,
        confirmation_threshold=0.00000442,
        breakout_level=0.0000044,
        invalidation_level=0.0000041,
    )
    return {**p, **changes}


def horizon(assessment="bullish_setup", status="ready", patterns=None, name="Kısa"):
    return dict(
        name=name,
        interval="15m",
        assessment=assessment,
        status=status,
        patterns=[pattern()] if patterns is None else patterns,
    )


def test_up_volume_supported_and_exact_tiny_price_conditions():
    result = describe(horizon())
    assert len(result["positive_notes"]) == 2
    p = result["level_conditions"][0]
    assert p["threshold"] == "0.00000442"
    assert p["invalidation"] == "0.0000041"
    assert "üzerinde" in p["condition"]
    assert "geçersizliği" in p["risk_note"]


def test_bearish_volume_adds_risk_not_positive_evidence():
    p = pattern(direction="down", name="Çift tepe")
    result = describe(horizon("bearish_setup", patterns=[p]))
    assert result["positive_notes"] == [] and len(result["negative_notes"]) == 2
    assert "altında" in result["level_conditions"][0]["condition"]


@pytest.mark.parametrize("supported", [False, None])
def test_missing_or_weak_volume_is_explicit(supported):
    result = describe(horizon(patterns=[pattern(report_volume_supported=supported)]))
    assert len(result["positive_notes"]) == 1
    assert "hacim" in result["watch_notes"][0]


@pytest.mark.parametrize("state", ["invalidated", "confirmed"])
def test_inactive_signal_has_no_actionable_level(state):
    result = describe(
        horizon("waiting", patterns=[pattern(status=state, current_confirmation=False)])
    )
    assert result["positive_notes"] == [] and result["negative_notes"] == []
    assert result["level_conditions"] == []
    assert result["watch_notes"]


def test_forming_is_a_candidate_not_confirmed():
    result = describe(
        horizon("waiting", patterns=[pattern(status="forming", current_confirmation=False)])
    )
    assert result["positive_notes"] == []
    assert result["level_conditions"][0]["condition"].startswith("Teyit adayı")


def test_unavailable_horizon_cannot_contribute_direction():
    h = horizon("bullish_setup", status="stale_data")
    result = describe(h)
    assert result["positive_notes"] == [] and result["level_conditions"] == []
    assert "yapılmadı" in result["guidance"]
    result = reviews([h])
    assert "yapılamıyor" in result["new_purchase_review"]
    assert "Kısa" in result["holding_review"]


def test_opposing_horizons_explain_both_reviews_and_missing_data():
    result = reviews(
        [
            horizon(),
            horizon("bearish_setup", name="Orta"),
            horizon(status="insufficient_data", name="Uzun"),
        ]
    )
    assert "Orta" in result["new_purchase_review"]
    assert "çelişiyor" in result["new_purchase_review"]
    assert "Uzun" in result["new_purchase_review"] and "Uzun" in result["holding_review"]
    assert "risk planını" in result["holding_review"]
