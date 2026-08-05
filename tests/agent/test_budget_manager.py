"""Tests for agent.budget_manager.

Covers:
1. check_budget returns OK when usage is low.
2. check_budget returns WARNING at warning_threshold.
3. check_budget returns CRITICAL at hard_limit.
4. check_budget returns OK when model_context_length is 0.
5. Config overrides are respected.
6. Shortcut getters return expected values.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.budget_manager import (
    check_budget,
    BudgetStatus,
    get_warning_threshold,
    get_hard_limit,
    get_per_turn_output_cap,
    _reset_budget_config_cache,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    _reset_budget_config_cache()
    yield
    _reset_budget_config_cache()


class TestCheckBudget:
    def test_ok_when_usage_low(self):
        with patch("hermes_cli.config.load_config", return_value={}):
            status = check_budget(context_tokens=10000, model_context_length=200000)
        assert status == BudgetStatus.OK

    def test_warning_at_threshold(self):
        cfg = {"agent": {"token_budget": {"warning_threshold": 0.80, "hard_limit": 0.95}}}
        with patch("hermes_cli.config.load_config", return_value=cfg):
            status = check_budget(context_tokens=160000, model_context_length=200000)
        assert status == BudgetStatus.WARNING

    def test_critical_at_hard_limit(self):
        cfg = {"agent": {"token_budget": {"warning_threshold": 0.80, "hard_limit": 0.95}}}
        with patch("hermes_cli.config.load_config", return_value=cfg):
            status = check_budget(context_tokens=190000, model_context_length=200000)
        assert status == BudgetStatus.CRITICAL

    def test_ok_when_context_length_zero(self):
        with patch("hermes_cli.config.load_config", return_value={}):
            status = check_budget(context_tokens=10000, model_context_length=0)
        assert status == BudgetStatus.OK

    def test_ok_when_context_tokens_zero(self):
        with patch("hermes_cli.config.load_config", return_value={}):
            status = check_budget(context_tokens=0, model_context_length=200000)
        assert status == BudgetStatus.OK


class TestConfigOverrides:
    def test_custom_thresholds_respected(self):
        cfg = {"agent": {"token_budget": {"warning_threshold": 0.50, "hard_limit": 0.90}}}
        with patch("hermes_cli.config.load_config", return_value=cfg):
            status = check_budget(context_tokens=110000, model_context_length=200000)
        assert status == BudgetStatus.WARNING  # 55% > 50% warning

    def test_defaults_when_no_config(self):
        with patch("hermes_cli.config.load_config", return_value={}):
            assert get_warning_threshold() == 0.80
            assert get_hard_limit() == 0.95
            assert get_per_turn_output_cap() == 0


class TestInvalidConfig:
    def test_invalid_threshold_falls_back(self):
        cfg = {"agent": {"token_budget": {"warning_threshold": "bad", "hard_limit": None}}}
        with patch("hermes_cli.config.load_config", return_value=cfg):
            assert get_warning_threshold() == 0.80
            assert get_hard_limit() == 0.95
