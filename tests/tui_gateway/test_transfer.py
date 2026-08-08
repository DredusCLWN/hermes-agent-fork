"""Tests for the "Transfer to new session"` context-carry helpers."""

import asyncio

from tui_gateway.transfer import (
    assemble_seed,
    build_summary_prompt,
    split_user_turns,
    summarize_head_async,
)


def _turns(roles):
    return [{"role": r, "text": f"{r}:{i}"} for i, r in enumerate(roles)]


class TestSplitUserTurns:
    def test_fewer_than_n_user_turns_returns_zero(self):
        assert split_user_turns(_turns(["user", "assistant"]), 10) == 0

    def test_keeps_last_n_user_turns(self):
        turns = _turns(["user", "assistant", "user", "assistant", "user", "assistant", "user"])
        assert split_user_turns(turns, 2) == 4
        tail = turns[4:]
        assert [t["role"] for t in tail] == ["user", "assistant", "user"]

    def test_empty(self):
        assert split_user_turns([], 10) == 0
        assert split_user_turns(None, 10) == 0

    def test_tail_zero(self):
        assert split_user_turns(_turns(["user", "assistant", "user"]), 0) == 0


class TestAssembleSeed:
    def test_summary_prepended_as_assistant(self):
        seed = assemble_seed("carry", _turns(["user", "assistant"]))
        assert seed[0] == {"role": "assistant", "content": "carry"}
        assert [m["role"] for m in seed] == ["assistant", "user", "assistant"]

    def test_no_summary_tail_only(self):
        seed = assemble_seed(None, _turns(["user", "assistant"]))
        assert [m["role"] for m in seed] == ["user", "assistant"]

    def test_drops_blank_and_unknown_roles(self):
        tail = [{"role": "user", "text": "  "}, {"role": "system", "text": "x"}, {"role": "tool", "text": "y"}]
        assert assemble_seed(None, tail) == [{"role": "system", "content": "x"}]


class TestBuildSummaryPrompt:
    def test_marks_roles(self):
        prompt = build_summary_prompt([{"role": "user", "text": "hello"}])
        assert "USER: hello" in prompt
        assert "conversation to summarize" in prompt

    def test_caps_long_turn(self):
        prompt = build_summary_prompt([{"role": "user", "text": "x" * 20000}])
        assert "USER: " + "x" * 6000 in prompt
        assert "x" * 6001 not in prompt


class TestSummarizeHead:
    def test_empty_head_returns_empty(self):
        assert asyncio.run(summarize_head_async([])) == ""

    def test_no_aux_provider_returns_empty(self, monkeypatch):
        async def _none(*a, **kwargs):
            return (None, None)

        monkeypatch.setattr("agent.auxiliary_client.get_async_text_auxiliary_client", _none)
        assert asyncio.run(summarize_head_async([{"role": "user", "text": "hi"}])) == ""

    def test_client_misbehaves_falls_back_empty(self, monkeypatch):
        class _Broken:
            chat = None  # accessing .chat.completions raises AttributeError

        async def _broken_client(*a, **kwargs):
            return (_Broken(), "m")

        monkeypatch.setattr("agent.auxiliary_client.get_async_text_auxiliary_client", _broken_client)
        assert asyncio.run(summarize_head_async([{"role": "user", "text": "hi"}])) == ""


def test_module_imports():
    import tui_gateway.transfer as t
    assert t.DEFAULT_TAIL_USER_TURNS == 10