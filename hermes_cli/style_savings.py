"""Shared estimation of output-token savings from response styles.

The same multiplicative combination (caveman + ponytail rates) used to be
inlined in gateway/runtime_footer.py, tui_gateway/server.py and
gateway/platforms/api_server.py. Display-only accounting — keep this
module dependency-free.
"""

from __future__ import annotations

# Measured benchmark rates (history: JuliusBrussee/caveman,
# DietrichGebert/ponytail agentic benchmark).
CAVEMAN_SAVINGS_PCT = 65
PONYTAIL_SAVINGS_PCT = 54


def estimate_style_savings(
    output_tokens: int,
    caveman_active: bool = False,
    ponytail_active: bool = False,
    caveman_mode: str = "auto",
) -> dict:
    """Estimate tokens saved by the caveman/ponytail response styles.

    When both styles are active the combined reduction is multiplicative
    (1 - (1-caveman) * (1-ponytail)): both rates compress the SAME full
    baseline, so savings compound. Each style's attributed share is
    proportional to its rate and the shares sum to the combined savings.
    The result is an estimate for display only — not billing data.

    Returns ``{"caveman_saved": int, "ponytail_saved": int, "mode_tag": str}``
    where ``mode_tag`` is the caveman intensity suffix for display (e.g.
    ``" (lite)"``) when a non-default intensity is active.
    """
    caveman_saved = 0
    ponytail_saved = 0
    mode_tag = ""
    if output_tokens <= 0:
        return {"caveman_saved": 0, "ponytail_saved": 0, "mode_tag": ""}
    caveman_rate = CAVEMAN_SAVINGS_PCT / 100 if caveman_active else 0.0
    ponytail_rate = PONYTAIL_SAVINGS_PCT / 100 if ponytail_active else 0.0
    if not caveman_rate and not ponytail_rate:
        return {"caveman_saved": 0, "ponytail_saved": 0, "mode_tag": ""}
    combined = 1.0 - (1.0 - caveman_rate) * (1.0 - ponytail_rate)
    estimated_full = int(output_tokens / (1.0 - combined))
    total_style_saved = estimated_full - output_tokens
    if caveman_rate and ponytail_rate:
        share = caveman_rate / (caveman_rate + ponytail_rate)
        caveman_saved = int(total_style_saved * share)
        ponytail_saved = total_style_saved - caveman_saved
    elif caveman_rate:
        caveman_saved = total_style_saved
    else:
        ponytail_saved = total_style_saved
    if caveman_active and caveman_mode not in ("auto", "full"):
        mode_tag = f" ({caveman_mode})"
    return {
        "caveman_saved": caveman_saved,
        "ponytail_saved": ponytail_saved,
        "mode_tag": mode_tag,
    }
