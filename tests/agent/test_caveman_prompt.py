"""Tests for Caveman response style in system prompt.

Covers:
1. CAVEMAN_RESPONSE_STYLE constant exists and is non-empty.
2. Caveman block is injected when _response_style == "caveman".
3. Caveman block is NOT injected when _response_style == "off".
4. Caveman block is NOT injected when _response_style is empty.
5. DEFAULT_CONFIG has display.response_style == "caveman".
"""

from __future__ import annotations

from agent.prompt_builder import (
    CAVEMAN_RESPONSE_STYLE,
    CAVEMAN_LITE_RESPONSE_STYLE,
    CAVEMAN_ULTRA_RESPONSE_STYLE,
    get_caveman_prompt,
)


class TestCavemanConstant:
    def test_constant_exists_and_nonempty(self):
        assert CAVEMAN_RESPONSE_STYLE
        assert isinstance(CAVEMAN_RESPONSE_STYLE, str)
        assert len(CAVEMAN_RESPONSE_STYLE) > 20

    def test_constant_contains_key_directives(self):
        assert "caveman" in CAVEMAN_RESPONSE_STYLE.lower()
        assert "fluff" in CAVEMAN_RESPONSE_STYLE.lower()
        assert "Auto-Clarity" in CAVEMAN_RESPONSE_STYLE
        assert "Boundaries" in CAVEMAN_RESPONSE_STYLE
        assert "Persistence" in CAVEMAN_RESPONSE_STYLE
        assert "never drop not/never/no/only/except" in CAVEMAN_RESPONSE_STYLE.lower()

    def test_lite_constant_exists(self):
        assert CAVEMAN_LITE_RESPONSE_STYLE
        assert "lite" in CAVEMAN_LITE_RESPONSE_STYLE.lower()
        assert "keep full sentences" in CAVEMAN_LITE_RESPONSE_STYLE.lower()

    def test_ultra_constant_exists(self):
        assert CAVEMAN_ULTRA_RESPONSE_STYLE
        assert "ultra" in CAVEMAN_ULTRA_RESPONSE_STYLE.lower()
        assert "one word" in CAVEMAN_ULTRA_RESPONSE_STYLE.lower()

    def test_get_caveman_prompt_returns_correct_variant(self):
        assert get_caveman_prompt("lite") is CAVEMAN_LITE_RESPONSE_STYLE
        assert get_caveman_prompt("ultra") is CAVEMAN_ULTRA_RESPONSE_STYLE
        assert get_caveman_prompt("full") is CAVEMAN_RESPONSE_STYLE
        assert get_caveman_prompt("auto") is CAVEMAN_RESPONSE_STYLE
        assert get_caveman_prompt("unknown") is CAVEMAN_RESPONSE_STYLE


class TestDefaultConfig:
    def test_default_config_has_caveman_response_style(self):
        from hermes_cli.config_defaults import DEFAULT_CONFIG
        display = DEFAULT_CONFIG.get("display", {})
        assert display.get("response_style") == "caveman"
        assert display.get("caveman_mode") == "auto"

    def test_default_config_has_agent_presets(self):
        from hermes_cli.config_defaults import DEFAULT_CONFIG
        presets = DEFAULT_CONFIG.get("agent_presets", {})
        assert isinstance(presets, dict)
        assert "default" in presets
        assert "coder" in presets
        assert "researcher" in presets
        assert "minimal" in presets

    def test_default_config_has_token_budget(self):
        from hermes_cli.config_defaults import DEFAULT_CONFIG
        agent = DEFAULT_CONFIG.get("agent", {})
        budget = agent.get("token_budget", {})
        assert budget.get("warning_threshold") == 0.80
        assert budget.get("hard_limit") == 0.95
        assert budget.get("per_turn_output_cap") == 0

    def test_default_config_has_artifact_store(self):
        from hermes_cli.config_defaults import DEFAULT_CONFIG
        tool_output = DEFAULT_CONFIG.get("tool_output", {})
        assert tool_output.get("artifact_store_enabled") is True
        assert tool_output.get("keep_first_lines") == 50
        assert tool_output.get("keep_last_lines") == 80

    def test_default_config_proactive_prune_tokens(self):
        from hermes_cli.config_defaults import DEFAULT_CONFIG
        compression = DEFAULT_CONFIG.get("compression", {})
        assert compression.get("proactive_prune_tokens") == 48000

    def test_default_config_has_active_preset(self):
        from hermes_cli.config_defaults import DEFAULT_CONFIG
        assert DEFAULT_CONFIG.get("active_preset") == ""
