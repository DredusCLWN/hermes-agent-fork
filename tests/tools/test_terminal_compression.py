"""Tests for domain-aware terminal output compression."""

from __future__ import annotations

from tools.terminal_compression import compress_terminal_output


# ── Test runner compression ─────────────────────────────────────────────

class TestTestCompression:
    def test_pytest_failures_extracted(self):
        output = (
            "test_a.py::test_one PASSED\n"
            "test_a.py::test_two PASSED\n"
            "test_b.py::test_three FAILED\n"
            "    assert 1 == 2\n"
            "    E  assert 1 == 2\n"
            "test_b.py::test_four PASSED\n"
            "test_c.py::test_five PASSED\n"
            "test_c.py::test_six PASSED\n"
            "\n"
            "========================= 1 failed, 5 passed in 0.5s =========================\n"
        )
        compressed, applied = compress_terminal_output("pytest", output, 100)
        assert applied
        assert "FAILED" in compressed
        assert "assert 1 == 2" in compressed
        assert "1 failed, 5 passed" in compressed
        assert "test_one" not in compressed  # passing test collapsed
        assert len(compressed) < len(output)

    def test_npm_test_failures_extracted(self):
        output = (
            "PASS  src/a.test.ts\n"
            "PASS  src/b.test.ts\n"
            "FAIL  src/c.test.ts\n"
            "  ● test_c › should work\n"
            "    Expected: 2\n"
            "    Received: 1\n"
            "PASS  src/d.test.ts\n"
            "\n"
            "Tests: 1 failed, 3 passed, 4 total\n"
        )
        compressed, applied = compress_terminal_output("npm test", output, 100)
        assert applied
        assert "FAIL" in compressed
        assert "Expected: 2" in compressed
        assert "1 failed, 3 passed" in compressed
        assert len(compressed) < len(output)

    def test_all_passing_collapsed(self):
        output = (
            "test_one PASSED\n"
            "test_two PASSED\n"
            "test_three PASSED\n"
            "3 passed in 0.1s\n"
        )
        compressed, applied = compress_terminal_output("pytest", output, 50)
        assert applied
        assert "3 passed" in compressed
        assert "passing tests collapsed" in compressed

    def test_no_match_returns_unapplied(self):
        output = "some random output\n" * 100
        compressed, applied = compress_terminal_output("echo hello", output, 50)
        assert not applied


# ── git diff compression ────────────────────────────────────────────────

class TestGitDiffCompression:
    def test_context_trimmed(self):
        output = (
            "diff --git a/foo.py b/foo.py\n"
            "index abc..def 100644\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -10,5 +10,5 @@\n"
            " unchanged line 1\n"
            " unchanged line 2\n"
            " unchanged line 3\n"
            "-old line\n"
            "+new line\n"
            " unchanged line 4\n"
            " unchanged line 5\n"
        )
        compressed, applied = compress_terminal_output("git diff", output, 50)
        assert applied
        assert "diff --git" in compressed
        assert "@@" in compressed
        assert "-old line" in compressed
        assert "+new line" in compressed
        # Context lines beyond _MAX_DIFF_CONTEXT_LINES should be trimmed
        assert "unchanged line 5" not in compressed
        assert len(compressed) < len(output)

    def test_small_diff_not_compressed(self):
        output = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,1 +1,1 @@\n-a\n+b\n"
        compressed, applied = compress_terminal_output("git diff", output, 1000)
        # Small output — compression shouldn't help much
        assert not applied or len(compressed) <= len(output)


# ── git log compression ─────────────────────────────────────────────────

class TestGitLogCompression:
    def test_compact_format(self):
        output = (
            "commit abc1234567890\n"
            "Author: Alice <alice@example.com>\n"
            "Date:   Mon Aug 5 10:00:00 2026\n"
            "\n"
            "    Fix bug in parser\n"
            "\n"
            "commit def6789012345\n"
            "Author: Bob <bob@example.com>\n"
            "Date:   Mon Aug 4 15:00:00 2026\n"
            "\n"
            "    Add feature X\n"
        )
        compressed, applied = compress_terminal_output("git log", output, 50)
        assert applied
        assert "abc1234567890" in compressed
        assert "Alice" in compressed
        assert "Fix bug in parser" in compressed
        assert "def6789012345" in compressed
        assert "Bob" in compressed
        assert "Add feature X" in compressed
        assert "Date:" not in compressed
        assert len(compressed) < len(output)


# ── ls compression ──────────────────────────────────────────────────────

class TestLsCompression:
    def test_grouped_by_extension(self):
        lines = []
        for i in range(100):
            lines.append(f"-rw-r--r--  1 user  staff  1234 Jan 01 file_{i}.py")
        for i in range(50):
            lines.append(f"-rw-r--r--  1 user  staff  5678 Jan 01 doc_{i}.md")
        for i in range(10):
            lines.append(f"drwxr-xr-x  3 user  staff    96 Jan 01 dir_{i}")
        output = "\n".join(lines)

        compressed, applied = compress_terminal_output("ls -la", output, 100)
        assert applied
        assert "160 entries total" in compressed
        assert ".py:" in compressed
        assert "100 files" in compressed
        assert ".md:" in compressed
        assert "50 files" in compressed
        assert len(compressed) < len(output)

    def test_small_ls_not_compressed(self):
        output = "file_a.py\nfile_b.py\nfile_c.py\n"
        compressed, applied = compress_terminal_output("ls", output, 1000)
        assert not applied


# ── build compression ───────────────────────────────────────────────────

class TestBuildCompression:
    def test_errors_kept_warnings_counted(self):
        lines = []
        for i in range(50):
            lines.append(f"   Compiling module_{i}.rs")
        lines.append("warning: unused variable: `x`")
        lines.append("warning: unused variable: `y`")
        lines.append("warning: unused variable: `z`")
        lines.append("warning: unused variable: `w`")
        lines.append("warning: unused variable: `a`")
        lines.append("warning: unused variable: `b`")
        lines.append("error[E0425]: cannot find value `foo` in scope")
        lines.append("  --> src/main.rs:10:5")
        output = "\n".join(lines)

        compressed, applied = compress_terminal_output("cargo build", output, 100)
        assert applied
        assert "error[E0425]" in compressed
        assert "cannot find value `foo`" in compressed
        assert "50 build/compile lines collapsed" in compressed
        assert "6 warnings" in compressed
        assert len(compressed) < len(output)

    def test_pip_install_collapsed(self):
        lines = []
        for i in range(30):
            lines.append(f"Collecting package_{i}")
            lines.append(f"  Downloading package_{i}-1.0.tar.gz")
        lines.append("Successfully installed package_0 package_1 package_2")
        output = "\n".join(lines)

        compressed, applied = compress_terminal_output("pip install -e .", output, 100)
        assert applied
        assert "build/compile lines collapsed" in compressed
        assert len(compressed) < len(output)


# ── Fallback ────────────────────────────────────────────────────────────

class TestFallback:
    def test_unknown_command_returns_unapplied(self):
        output = "line\n" * 1000
        compressed, applied = compress_terminal_output("some-unknown-cmd", output, 100)
        assert not applied

    def test_empty_output(self):
        compressed, applied = compress_terminal_output("pytest", "", 100)
        assert not applied
