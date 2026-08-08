"""Domain-aware compression for terminal output.

Replaces the dumb head+tail truncation with command-specific heuristics
that preserve the information the model actually needs:

- Test runners → failures + summary count
- git diff → hunk headers + added/removed lines (context trimmed)
- git log → one line per commit (hash + author + subject)
- ls/tree → grouped summary with file counts
- build/compile → errors + warning count

Full output is already saved to the artifact store before this runs,
so the compression is lossy but recoverable.

Each heuristic returns (compressed_text, was_applied).  When a heuristic
does not match (``was_applied=False``), the caller falls back to the
existing head+tail truncation.
"""

from __future__ import annotations

# Module-level counter for total chars saved across all compressions.
# Read by the gateway via get_terminal_compression_chars_saved().
_terminal_compression_chars_saved: int = 0


def get_terminal_compression_chars_saved() -> int:
    """Return total chars saved by terminal compression this session."""
    return _terminal_compression_chars_saved




import re
from typing import Tuple

_MAX_DIFF_CONTEXT_LINES = 1
_MAX_TEST_FAILURE_LINES = 60
_MAX_LS_ENTRIES = 40
_MAX_LOG_ENTRIES = 50


def compress_terminal_output(
    command: str,
    output: str,
    max_chars: int,
) -> Tuple[str, bool]:
    """Try domain-aware compression for *command* output.

    Returns ``(compressed, was_applied)``.  When ``was_applied`` is False
    the caller should fall back to head+tail truncation.
    Chars saved is accumulated in the module-level counter
    ``_terminal_compression_chars_saved`` (read via
    ``get_terminal_compression_chars_saved()``).
    """
    cmd_lower = command.strip().lower()
    global _terminal_compression_chars_saved
    original_len = len(output)

    def _track(compressed: str, applied: bool) -> Tuple[str, bool]:
        global _terminal_compression_chars_saved
        if applied:
            _terminal_compression_chars_saved += original_len - len(compressed)
        return compressed, applied

    # Order matters: most specific patterns first.
    if _is_test_command(cmd_lower):
        return _track(*_compress_test_output(output, max_chars))
    if _is_git_diff(cmd_lower):
        return _track(*_compress_git_diff(output, max_chars))
    if _is_git_log(cmd_lower):
        return _track(*_compress_git_log(output, max_chars))
    if _is_ls_command(cmd_lower):
        return _track(*_compress_ls_output(output, max_chars))
    if _is_build_command(cmd_lower):
        return _track(*_compress_build_output(output, max_chars))

    return output, False


# ── Test runners ───────────────────────────────────────────────────────

_TEST_PATTERNS = (
    "npm test", "npm run test", "yarn test", "pnpm test",
    "pytest", "python -m pytest", "python -m unittest",
    "cargo test", "go test", "dotnet test",
    "jest", "vitest", "mocha",
)


def _is_test_command(cmd: str) -> bool:
    return any(p in cmd for p in _TEST_PATTERNS)


def _compress_test_output(output: str, max_chars: int) -> Tuple[str, bool]:
    """Keep failures + final summary; collapse passing tests to a count."""
    lines = output.splitlines()
    if not lines:
        return output, False

    failure_lines: list[str] = []
    summary_lines: list[str] = []
    passed_count = 0
    in_failure = False

    for line in lines:
        stripped = line.strip()

        # Detect failure blocks (pytest, jest, mocha, cargo, go, npm)
        if re.search(
            r"(FAILED|FAIL|ERROR|PANIC|AssertionError|Expected|Received|"
            r"--- FAIL|✕|✗|×|❌)",
            stripped,
            re.IGNORECASE,
        ):
            in_failure = True
            failure_lines.append(line)
            continue

        if in_failure:
            # End of failure block: blank line after failure or new test start
            if not stripped or re.search(
                r"(PASS|✓|✓|✔|--- PASS|Running|ok\s|test\s)",
                stripped,
            ):
                in_failure = False
                if not stripped:
                    failure_lines.append("")
            else:
                failure_lines.append(line)
                if len(failure_lines) > _MAX_TEST_FAILURE_LINES:
                    failure_lines.append("... [failure output truncated] ...")
                    in_failure = False
            continue

        # Detect summary lines
        if re.search(
            r"(\d+ (passed|failed|skipped|error)|"
            r"Tests:\s+\d+|"
            r"Test Suite:|"
            r"test result:|"
            r"SUMMARY|"
            r"passed!,\s*\d+|"
            r"FAIL\s|"
            r"ok\.\s|"
            r"running \d+ test)",
            stripped,
            re.IGNORECASE,
        ):
            summary_lines.append(line)
            continue

        # Count passing tests
        if re.search(r"(PASS|✓|✔|--- PASS|ok\s+)", stripped):
            passed_count += 1

    if not failure_lines and not summary_lines and passed_count == 0:
        return output, False

    parts: list[str] = []
    if passed_count > 0:
        parts.append(f"[{passed_count} passing tests collapsed]")
    if failure_lines:
        parts.append("\n".join(failure_lines))
    if summary_lines:
        parts.append("\n".join(summary_lines))

    compressed = "\n".join(parts)
    saved = len(output) - len(compressed)
    if saved > 60:
        compressed += f"\n... [{saved} chars saved by domain-aware compression] ..."
        return compressed, True
    if saved > 0:
        return compressed, True

    return output, False


# ── git diff ───────────────────────────────────────────────────────────

def _is_git_diff(cmd: str) -> bool:
    return "git diff" in cmd


def _compress_git_diff(output: str, max_chars: int) -> Tuple[str, bool]:
    """Keep hunk headers + added/removed lines; trim unchanged context."""
    lines = output.splitlines()
    if not lines:
        return output, False

    result: list[str] = []
    context_streak = 0

    for line in lines:
        if line.startswith(("diff --git", "---", "+++", "@@", "index ")):
            result.append(line)
            context_streak = 0
        elif line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            result.append(line)
            context_streak = 0
        else:
            # Context line — keep only _MAX_DIFF_CONTEXT_LINES per hunk
            if context_streak < _MAX_DIFF_CONTEXT_LINES:
                result.append(line)
            context_streak += 1

    compressed = "\n".join(result)
    saved = len(output) - len(compressed)
    if saved > 60:
        compressed += f"\n... [{saved} chars saved by domain-aware compression] ..."
        return compressed, True
    if saved > 0:
        return compressed, True

    return output, False


# ── git log ────────────────────────────────────────────────────────────

def _is_git_log(cmd: str) -> bool:
    return "git log" in cmd


def _compress_git_log(output: str, max_chars: int) -> Tuple[str, bool]:
    """One line per commit: hash + author + subject."""
    lines = output.splitlines()
    if not lines:
        return output, False

    result: list[str] = []
    count = 0
    for line in lines:
        if line.startswith("commit "):
            count += 1
            if count > _MAX_LOG_ENTRIES:
                result.append(f"... [{len(lines) - len(result)} more commits omitted] ...")
                break
            result.append(line)
        elif line.startswith("Author:"):
            if result:
                # Append author to previous commit line
                result[-1] = result[-1] + f"  {line.strip()}"
        elif line.startswith("    ") and result:
            # Commit subject (indented)
            subject = line.strip()
            if subject:
                result[-1] = result[-1] + f"  {subject}"
        # Skip Date:, blank lines, and everything else

    if not result:
        return output, False

    compressed = "\n".join(result)
    saved = len(output) - len(compressed)
    if saved > 60:
        compressed += f"\n... [{saved} chars saved by domain-aware compression] ..."
        return compressed, True
    if saved > 0:
        return compressed, True

    return output, False


# ── ls / tree ──────────────────────────────────────────────────────────

def _is_ls_command(cmd: str) -> bool:
    return bool(re.match(r"(?:^|\s)(?:ls|tree|dir|Get-ChildItem|gci)\b", cmd))


def _compress_ls_output(output: str, max_chars: int) -> Tuple[str, bool]:
    """Group by extension/type with counts; list up to _MAX_LS_ENTRIES."""
    lines = [l for l in output.splitlines() if l.strip()]
    if not lines:
        return output, False

    # If output is small enough, don't compress
    if len(output) <= max_chars:
        return output, False

    entries: list[str] = []
    for line in lines:
        # Strip common ls -l prefixes (permissions, size, date)
        parts = line.split()
        if len(parts) >= 1:
            name = parts[-1]
            entries.append(name)

    if len(entries) <= _MAX_LS_ENTRIES:
        return output, False

    # Group by extension
    groups: dict[str, list[str]] = {}
    no_ext: list[str] = []
    for name in entries:
        if "." in name and not name.startswith("."):
            ext = name.rsplit(".", 1)[-1].lower()
            groups.setdefault(ext, []).append(name)
        else:
            no_ext.append(name)

    result: list[str] = [f"[{len(entries)} entries total]"]
    for ext, names in sorted(groups.items()):
        if len(names) <= 3:
            result.append(f"  .{ext}: {', '.join(names)}")
        else:
            result.append(f"  .{ext}: {len(names)} files (e.g. {', '.join(names[:3])})")
    if no_ext:
        if len(no_ext) <= 5:
            result.append(f"  (no ext): {', '.join(no_ext)}")
        else:
            result.append(f"  (no ext): {len(no_ext)} items (e.g. {', '.join(no_ext[:3])})")

    compressed = "\n".join(result)
    saved = len(output) - len(compressed)
    if saved > 60:
        compressed += f"\n... [{saved} chars saved by domain-aware compression] ..."
        return compressed, True
    if saved > 0:
        return compressed, True

    return output, False


# ── build / compile ────────────────────────────────────────────────────

_BUILD_PATTERNS = (
    "cargo build", "cargo check", "cargo clippy",
    "npm run build", "yarn build", "pnpm build",
    "tsc", "webpack", "vite build", "rollup",
    "make", "cmake", "gcc ", "g++ ", "clang ", "rustc",
    "dotnet build", "msbuild", "go build",
    "pip install", "uv pip install", "pip install -e",
)


def _is_build_command(cmd: str) -> bool:
    return any(p in cmd for p in _BUILD_PATTERNS)


def _compress_build_output(output: str, max_chars: int) -> Tuple[str, bool]:
    """Keep errors + warnings count; collapse successful compilation lines."""
    lines = output.splitlines()
    if not lines:
        return output, False

    error_lines: list[str] = []
    warning_count = 0
    success_count = 0

    for line in lines:
        stripped = line.strip()
        if re.match(
            r"(error|ERROR|Error\[|fatal|FATAL|undefined|cannot find|"
            r"failed to|linking failed|unresolved)",
            stripped,
            re.IGNORECASE,
        ):
            error_lines.append(line)
        elif re.match(r"(warning|WARN|caution)", stripped, re.IGNORECASE):
            warning_count += 1
            # Keep first few warnings
            if warning_count <= 5:
                error_lines.append(line)
        elif re.match(
            r"(Compiling|Building|Finished|Done|success|✓|✔|"
            r"Installing|Collecting|Downloading)",
            stripped,
            re.IGNORECASE,
        ):
            success_count += 1

    if not error_lines and warning_count == 0 and success_count == 0:
        return output, False

    parts: list[str] = []
    if success_count > 0:
        parts.append(f"[{success_count} build/compile lines collapsed]")
    if warning_count > 5:
        parts.append(f"[{warning_count} warnings total — first 5 shown]")
    elif warning_count > 0:
        parts.append(f"[{warning_count} warnings]")
    if error_lines:
        parts.append("\n".join(error_lines))

    compressed = "\n".join(parts)
    saved = len(output) - len(compressed)
    if saved > 60 and len(compressed) + len(f"\n... [{saved} chars saved by domain-aware compression] ...") < len(output):
        compressed += f"\n... [{saved} chars saved by domain-aware compression] ..."
        return compressed, True
    if saved > 0:
        return compressed, True

    return output, False

# ── Test runner
