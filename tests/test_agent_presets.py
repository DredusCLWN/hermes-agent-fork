"""Tests for agent presets in DEFAULT_CONFIG.

Covers:
1. All 4 presets exist with required fields.
2. Each preset has a description.
3. Each preset has display settings.
4. Coder preset mentions graphify.
5. Presets have toolsets.
"""

from __future__ import annotations

from hermes_cli.config_defaults import DEFAULT_CONFIG


class TestAgentPresets:
    def test_all_four_presets_exist(self):
        presets = DEFAULT_CONFIG.get("agent_presets", {})
        assert set(presets.keys()) >= {"default", "coder", "researcher", "minimal"}

    def test_each_preset_has_description(self):
        presets = DEFAULT_CONFIG.get("agent_presets", {})
        for name, preset in presets.items():
            assert isinstance(preset, dict), f"preset {name} is not a dict"
            desc = preset.get("description", "")
            assert desc, f"preset {name} has no description"

    def test_each_preset_has_display_settings(self):
        presets = DEFAULT_CONFIG.get("agent_presets", {})
        for name, preset in presets.items():
            display = preset.get("display", {})
            assert isinstance(display, dict), f"preset {name} display is not a dict"
            assert "response_style" in display, f"preset {name} missing response_style"
            assert "caveman_mode" in display, f"preset {name} missing caveman_mode"

    def test_coder_preset_mentions_graphify(self):
        presets = DEFAULT_CONFIG.get("agent_presets", {})
        coder = presets.get("coder", {})
        desc = coder.get("description", "")
        assert "graphify" in desc.lower()

    def test_each_preset_has_toolsets(self):
        presets = DEFAULT_CONFIG.get("agent_presets", {})
        for name, preset in presets.items():
            toolsets = preset.get("toolsets", [])
            assert isinstance(toolsets, list), f"preset {name} toolsets is not a list"
            assert len(toolsets) > 0, f"preset {name} has empty toolsets"

    def test_minimal_preset_has_lower_compression_threshold(self):
        presets = DEFAULT_CONFIG.get("agent_presets", {})
        minimal = presets.get("minimal", {})
        compression = minimal.get("compression", {})
        assert compression.get("threshold") == 0.35

    def test_default_preset_has_standard_compression_threshold(self):
        presets = DEFAULT_CONFIG.get("agent_presets", {})
        default = presets.get("default", {})
        compression = default.get("compression", {})
        assert compression.get("threshold") == 0.50
