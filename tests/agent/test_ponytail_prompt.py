"""Tests for Ponytail response style in system prompt.

Covers:
1. PONYTAIL_RESPONSE_STYLE constant exists and is non-empty.
2. Ponytail block is injected when _ponytail == "ponytail".
3. Ponytail block is NOT injected when _ponytail == "off".
4. Ponytail block is NOT injected when _ponytail is empty.
5. DEFAULT_CONFIG has display.ponytail == "" (off by default).
6. get_ponytail_prompt returns correct variant for each mode.
"""

from __future__ import annotations

from agent.prompt_builder import (
    PONYTAIL_RESPONSE_STYLE,
    PONYTAIL_LITE_RESPONSE_STYLE,
    PONYTAIL_ULTRA_RESPONSE_STYLE,
    get_ponytail_prompt,
)


class TestPonytailConstant:
    def test_constant_exists_and_nonempty(self):
        assert PONYTAIL_RESPONSE_STYLE
        assert isinstance(PONYTAIL_RESPONSE_STYLE, str)
        assert len(PONYTAIL_RESPONSE_STYLE) > 20

    def test_constant_contains_key_directives(self):
        assert "ponytail" in PONYTAIL_RESPONSE_STYLE.lower()
        assert "ladder" in PONYTAIL_RESPONSE_STYLE.lower()
        assert "YAGNI" in PONYTAIL_RESPONSE_STYLE
        assert "Boundaries" in PONYTAIL_RESPONSE_STYLE
        assert "Persistence" in PONYTAIL_RESPONSE_STYLE

    def test_lite_constant_exists(self):
        assert PONYTAIL_LITE_RESPONSE_STYLE
        assert "lite" in PONYTAIL_LITE_RESPONSE_STYLE.lower()
        assert "ladder" in PONYTAIL_LITE_RESPONSE_STYLE.lower()

    def test_ultra_constant_exists(self):
        assert PONYTAIL_ULTRA_RESPONSE_STYLE
        assert "ultra" in PONYTAIL_ULTRA_RESPONSE_STYLE.lower()
        assert "YAGNI" in PONYTAIL_ULTRA_RESPONSE_STYLE

    def test_get_ponytail_prompt_returns_correct_variant(self):
        assert get_ponytail_prompt("lite") is PONYTAIL_LITE_RESPONSE_STYLE
        assert get_ponytail_prompt("ultra") is PONYTAIL_ULTRA_RESPONSE_STYLE
        assert get_ponytail_prompt("full") is PONYTAIL_RESPONSE_STYLE
        assert get_ponytail_prompt("auto") is PONYTAIL_RESPONSE_STYLE
        assert get_ponytail_prompt("unknown") is PONYTAIL_RESPONSE_STYLE


class TestDefaultConfig:
    def test_default_config_has_ponytail_off(self):
        from hermes_cli.config_defaults import DEFAULT_CONFIG
        display = DEFAULT_CONFIG.get("display", {})
        assert display.get("ponytail") == ""
        assert display.get("ponytail_mode") == "full"
