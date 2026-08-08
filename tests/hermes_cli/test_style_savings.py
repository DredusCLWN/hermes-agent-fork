"""Tests for hermes_cli.style_savings.estimate_style_savings."""

from hermes_cli.style_savings import estimate_style_savings


def test_no_styles_no_savings():
    r = estimate_style_savings(1000)
    assert r == {"caveman_saved": 0, "ponytail_saved": 0, "mode_tag": ""}


def test_caveman_only():
    r = estimate_style_savings(1000, caveman_active=True)
    assert r["ponytail_saved"] == 0
    # 1000 tokens at 65% savings -> full ~2857 -> saved ~1857
    assert 1000 < r["caveman_saved"] < 2000


def test_ponytail_only():
    r = estimate_style_savings(1000, ponytail_active=True)
    assert r["caveman_saved"] == 0
    assert r["ponytail_saved"] > 1000


def test_combined_beats_either_alone():
    c = estimate_style_savings(1000, caveman_active=True)["caveman_saved"]
    p = estimate_style_savings(1000, ponytail_active=True)["ponytail_saved"]
    both = estimate_style_savings(1000, caveman_active=True, ponytail_active=True)
    total = both["caveman_saved"] + both["ponytail_saved"]
    # Both rates compound multiplicatively on the SAME full baseline, so the
    # combined estimate exceeds each single-style estimate (and even their
    # naive sum, which treats each rate as applying to a different baseline).
    assert total > max(c, p)
    # Attributed shares are non-empty and sum exactly to the combined savings.
    assert both["caveman_saved"] > 0 and both["ponytail_saved"] > 0
    # Upper bound: savings can never exceed the estimated full output.
    combined_rate = 1.0 - (1.0 - 0.65) * (1.0 - 0.54)
    assert total < 1000 / (1.0 - combined_rate)


def test_zero_output():
    r = estimate_style_savings(0, caveman_active=True)
    assert r["caveman_saved"] == 0
    assert r["ponytail_saved"] == 0


def test_mode_tag():
    assert estimate_style_savings(1000, caveman_active=True, caveman_mode="lite")["mode_tag"] == " (lite)"
    assert estimate_style_savings(1000, caveman_active=True, caveman_mode="auto")["mode_tag"] == ""
    assert estimate_style_savings(1000, caveman_active=True, caveman_mode="full")["mode_tag"] == ""
