"""Tests for lightweight cleanup passes: _dedup_tool_results, _normalize_tool_whitespace, _compact_json_tool_output, _aggregate_repeated_lines."""

from agent.conversation_loop import (
    _aggregate_repeated_lines,
    _compact_json_table,
    _compact_json_tool_output,
    _dedup_tool_results,
    _normalize_tool_whitespace,
)


class TestDedupToolResults:
    def test_identical_tool_results_collapsed(self):
        msgs = [
            {"role": "user", "content": "check config"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "function": {"name": "read_file"}}]},
            {"role": "tool", "tool_call_id": "t1", "content": "config: value=1"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "t2", "function": {"name": "read_file"}}]},
            {"role": "tool", "tool_call_id": "t2", "content": "config: value=1"},
        ]
        _dedup_tool_results(msgs)
        assert msgs[2]["content"] == "config: value=1"
        assert msgs[4]["content"] == "[Same result as above]"

    def test_different_tool_results_preserved(self):
        msgs = [
            {"role": "tool", "tool_call_id": "t1", "content": "result A"},
            {"role": "tool", "tool_call_id": "t2", "content": "result B"},
        ]
        _dedup_tool_results(msgs)
        assert msgs[0]["content"] == "result A"
        assert msgs[1]["content"] == "result B"

    def test_non_tool_messages_not_affected(self):
        msgs = [
            {"role": "user", "content": "same text"},
            {"role": "assistant", "content": "same text"},
            {"role": "user", "content": "same text"},
        ]
        _dedup_tool_results(msgs)
        assert all(m["content"] == "same text" for m in msgs)

    def test_persisted_output_not_deduped(self):
        persisted = (
            "<persisted-output>\n"
            "Full output saved to: /tmp/x.txt\n"
            "Preview:\nsame content\n"
            "</persisted-output>"
        )
        msgs = [
            {"role": "tool", "tool_call_id": "t1", "content": persisted},
            {"role": "tool", "tool_call_id": "t2", "content": persisted},
        ]
        _dedup_tool_results(msgs)
        assert msgs[0]["content"] == persisted
        assert msgs[1]["content"] == persisted

    def test_empty_content_skipped(self):
        msgs = [
            {"role": "tool", "tool_call_id": "t1", "content": ""},
            {"role": "tool", "tool_call_id": "t2", "content": ""},
        ]
        _dedup_tool_results(msgs)
        assert msgs[0]["content"] == ""
        assert msgs[1]["content"] == ""

    def test_three_identical_only_first_kept(self):
        msgs = [
            {"role": "tool", "tool_call_id": "t1", "content": "same"},
            {"role": "tool", "tool_call_id": "t2", "content": "same"},
            {"role": "tool", "tool_call_id": "t3", "content": "same"},
        ]
        _dedup_tool_results(msgs)
        assert msgs[0]["content"] == "same"
        assert msgs[1]["content"] == "[Same result as above]"
        assert msgs[2]["content"] == "[Same result as above]"


class TestNormalizeToolWhitespace:
    def test_four_newlines_collapsed(self):
        content = "line1\n\n\n\nline2"
        msgs = [{"role": "tool", "content": content}]
        _normalize_tool_whitespace(msgs)
        assert msgs[0]["content"] == "line1\n\nline2"

    def test_six_newlines_collapsed(self):
        content = "line1\n\n\n\n\n\nline2"
        msgs = [{"role": "tool", "content": content}]
        _normalize_tool_whitespace(msgs)
        assert msgs[0]["content"] == "line1\n\nline2"

    def test_two_newlines_preserved(self):
        content = "line1\n\nline2"
        msgs = [{"role": "tool", "content": content}]
        _normalize_tool_whitespace(msgs)
        assert msgs[0]["content"] == "line1\n\nline2"

    def test_three_newlines_preserved(self):
        content = "line1\n\n\nline2"
        msgs = [{"role": "tool", "content": content}]
        _normalize_tool_whitespace(msgs)
        assert msgs[0]["content"] == "line1\n\n\nline2"

    def test_non_tool_not_affected(self):
        content = "line1\n\n\n\n\nline2"
        msgs = [{"role": "user", "content": content}]
        _normalize_tool_whitespace(msgs)
        assert msgs[0]["content"] == content

    def test_no_newlines_not_affected(self):
        content = "just one line"
        msgs = [{"role": "tool", "content": content}]
        _normalize_tool_whitespace(msgs)
        assert msgs[0]["content"] == "just one line"

    def test_multiple_blocks_normalized(self):
        content = "a\n\n\n\nb\n\n\n\nc"
        msgs = [{"role": "tool", "content": content}]
        _normalize_tool_whitespace(msgs)
        assert msgs[0]["content"] == "a\n\nb\n\nc"


class TestCompactJsonToolOutput:
    def test_pretty_json_compacted(self):
        import json as _json
        data = {"status": "ok", "items": [1, 2, 3], "meta": {"page": 1}}
        pretty = _json.dumps(data, indent=2)
        msgs = [{"role": "tool", "content": pretty}]
        _compact_json_tool_output(msgs)
        assert msgs[0]["content"] == _json.dumps(data, separators=(",", ":"))
        assert len(msgs[0]["content"]) < len(pretty)

    def test_already_compact_not_changed(self):
        compact = '{"a":1,"b":2}'
        msgs = [{"role": "tool", "content": compact}]
        _compact_json_tool_output(msgs)
        assert msgs[0]["content"] == compact

    def test_non_json_not_affected(self):
        content = "Error: file not found\nCheck path: /tmp/test"
        msgs = [{"role": "tool", "content": content}]
        _compact_json_tool_output(msgs)
        assert msgs[0]["content"] == content

    def test_short_json_skipped(self):
        content = '{"a":1}'
        msgs = [{"role": "tool", "content": content}]
        _compact_json_tool_output(msgs)
        assert msgs[0]["content"] == '{"a":1}'

    def test_protected_tool_skipped(self):
        import json as _json
        data = {"result": "ok", "values": [1, 2, 3]}
        pretty = _json.dumps(data, indent=2)
        msgs = [{"role": "tool", "name": "execute_code", "content": pretty}]
        _compact_json_tool_output(msgs)
        assert msgs[0]["content"] == pretty

    def test_unprotected_tool_compacted(self):
        # Regression: the guard used to read the nonexistent ``tool_name``
        # key, so NOTHING was ever protected. With ``name`` it must work.
        import json as _json
        data = {"result": "ok", "values": [1, 2, 3]}
        pretty = _json.dumps(data, indent=2)
        msgs = [{"role": "tool", "name": "search_files", "content": pretty}]
        _compact_json_tool_output(msgs)
        assert msgs[0]["content"] == _json.dumps(data, separators=(",", ":"))

    def test_json_array_compacted(self):
        import json as _json
        data = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        pretty = _json.dumps(data, indent=2)
        msgs = [{"role": "tool", "content": pretty}]
        _compact_json_tool_output(msgs)
        assert msgs[0]["content"] == _json.dumps(data, separators=(",", ":"))

    def test_invalid_json_not_affected(self):
        content = '{not valid json'
        msgs = [{"role": "tool", "content": content}]
        _compact_json_tool_output(msgs)
        assert msgs[0]["content"] == content

    def test_non_tool_not_affected(self):
        import json as _json
        data = {"a": 1}
        pretty = _json.dumps(data, indent=2)
        msgs = [{"role": "user", "content": pretty}]
        _compact_json_tool_output(msgs)
        assert msgs[0]["content"] == pretty


class TestAggregateRepeatedLines:
    def test_three_identical_lines_collapsed(self):
        line = "warning: unused import in module /path/to/very/long/module/name.py"
        content = "\n".join([line] * 3 + ["done"])
        msgs = [{"role": "tool", "content": content}]
        _aggregate_repeated_lines(msgs)
        assert "[3\u00d7 " + line + "]" in msgs[0]["content"]
        assert "done" in msgs[0]["content"]

    def test_two_identical_not_collapsed(self):
        line = "this is a sufficiently long line to pass the threshold check"
        content = line + "\n" + line + "\nend of output here"
        msgs = [{"role": "tool", "content": content}]
        _aggregate_repeated_lines(msgs)
        assert msgs[0]["content"] == content

    def test_five_identical_collapsed(self):
        line = "error: timeout while connecting to remote server at 10.0.0.1:8080"
        content = "\n".join([line] * 5 + ["exit code: 1"])
        msgs = [{"role": "tool", "content": content}]
        _aggregate_repeated_lines(msgs)
        assert "[5\u00d7 " + line + "]" in msgs[0]["content"]
        assert "exit code: 1" in msgs[0]["content"]

    def test_short_content_skipped(self):
        content = "a\na\na\na\na\na"
        msgs = [{"role": "tool", "content": content}]
        _aggregate_repeated_lines(msgs)
        assert msgs[0]["content"] == content

    def test_protected_tool_skipped(self):
        line = "this is a sufficiently long line to pass the threshold check for sure"
        content = "\n".join([line] * 10 + ["end"])
        msgs = [{"role": "tool", "name": "memory", "content": content}]
        _aggregate_repeated_lines(msgs)
        assert msgs[0]["content"] == content

    def test_unprotected_tool_collapsed(self):
        # Regression: ``tool_name`` was never set on messages, so the
        # protected-tool guard silently matched nothing.
        line = "this is a sufficiently long line to pass the threshold check for sure"
        content = "\n".join([line] * 10 + ["end"])
        msgs = [{"role": "tool", "name": "search_files", "content": content}]
        _aggregate_repeated_lines(msgs)
        assert "[10\u00d7 " + line + "]" in msgs[0]["content"]

    def test_non_tool_not_affected(self):
        line = "this is a sufficiently long line to pass the threshold check for sure"
        content = "\n".join([line] * 5 + ["end"])
        msgs = [{"role": "user", "content": content}]
        _aggregate_repeated_lines(msgs)
        assert msgs[0]["content"] == content

    def test_mixed_repeated_and_unique(self):
        warn = "warning: deprecation in module /path/to/some/module/file.py"
        info = "info: task completed successfully with output verified"
        content = "start\n" + "\n".join([warn] * 4) + "\nend\n" + "\n".join([info] * 3)
        msgs = [{"role": "tool", "content": content}]
        _aggregate_repeated_lines(msgs)
        result = msgs[0]["content"]
        assert "start" in result
        assert "[4\u00d7 " + warn + "]" in result
        assert "end" in result
        assert "[3\u00d7 " + info + "]" in result

    def test_blank_lines_not_collapsed(self):
        content = "\n" * 10
        msgs = [{"role": "tool", "content": content}]
        _aggregate_repeated_lines(msgs)
        assert msgs[0]["content"] == content


class TestCompactJsonTable:
    def _make_homogeneous(self, n: int = 6, keys: int = 4):
        import json as _json
        key_names = [f"field_{i}" for i in range(keys)]
        data = [
            {k: f"value_{i}_{j}" for j, k in enumerate(key_names)}
            for i in range(n)
        ]
        return _json.dumps(data, indent=2), data, key_names

    def test_homogeneous_array_transformed(self):
        import json as _json
        pretty, data, keys = self._make_homogeneous()
        msgs = [{"role": "tool", "content": pretty}]
        _compact_json_table(msgs)
        result = _json.loads(msgs[0]["content"])
        assert result["_table"] is True
        assert result["schema"] == keys
        assert len(result["rows"]) == len(data)
        for i, row in enumerate(result["rows"]):
            assert row == [data[i][k] for k in keys]
        assert len(msgs[0]["content"]) < len(pretty)

    def test_heterogeneous_keys_not_transformed(self):
        import json as _json
        data = [
            {"a": 1, "b": 2, "c": 3, "d": 4},
            {"a": 1, "b": 2, "c": 3, "e": 4},
            {"a": 1, "b": 2, "c": 3, "d": 4},
            {"a": 1, "b": 2, "c": 3, "d": 4},
            {"a": 1, "b": 2, "c": 3, "d": 4},
        ]
        content = _json.dumps(data, indent=2)
        msgs = [{"role": "tool", "content": content}]
        _compact_json_table(msgs)
        assert msgs[0]["content"] == content

    def test_too_few_items_not_transformed(self):
        import json as _json
        data = [{"a": 1, "b": 2, "c": 3, "d": 4}] * 3
        content = _json.dumps(data, indent=2)
        msgs = [{"role": "tool", "content": content}]
        _compact_json_table(msgs)
        assert msgs[0]["content"] == content

    def test_too_few_keys_not_transformed(self):
        import json as _json
        data = [{"a": 1, "b": 2}] * 10
        content = _json.dumps(data, indent=2)
        msgs = [{"role": "tool", "content": content}]
        _compact_json_table(msgs)
        assert msgs[0]["content"] == content

    def test_protected_tool_skipped(self):
        pretty, _, _ = self._make_homogeneous()
        msgs = [{"role": "tool", "name": "execute_code", "content": pretty}]
        _compact_json_table(msgs)
        assert msgs[0]["content"] == pretty

    def test_unprotected_tool_transformed(self):
        # Regression: the guard read ``tool_name`` (never present), so
        # protected tools were being transformed like any other.
        import json as _json
        pretty, data, keys = self._make_homogeneous()
        msgs = [{"role": "tool", "name": "search_files", "content": pretty}]
        _compact_json_table(msgs)
        result = _json.loads(msgs[0]["content"])
        assert result["_table"] is True
        assert result["schema"] == keys

    def test_non_array_json_not_transformed(self):
        import json as _json
        data = {"a": 1, "b": 2, "c": 3, "d": 4}
        content = _json.dumps(data, indent=2)
        msgs = [{"role": "tool", "content": content}]
        _compact_json_table(msgs)
        assert msgs[0]["content"] == content

    def test_lossless_reconstruction(self):
        import json as _json
        pretty, data, keys = self._make_homogeneous(n=10, keys=5)
        msgs = [{"role": "tool", "content": pretty}]
        _compact_json_table(msgs)
        result = _json.loads(msgs[0]["content"])
        reconstructed = [
            dict(zip(result["schema"], row)) for row in result["rows"]
        ]
        assert reconstructed == data

    def test_non_homogeneous_types_not_transformed(self):
        import json as _json
        data = [{"a": 1, "b": 2, "c": 3, "d": 4}] * 4 + ["not_a_dict"] + [{"a": 1, "b": 2, "c": 3, "d": 4}]
        content = _json.dumps(data, indent=2)
        msgs = [{"role": "tool", "content": content}]
        _compact_json_table(msgs)
        assert msgs[0]["content"] == content

    def test_non_tool_not_affected(self):
        pretty, _, _ = self._make_homogeneous()
        msgs = [{"role": "user", "content": pretty}]
        _compact_json_table(msgs)
        assert msgs[0]["content"] == pretty
