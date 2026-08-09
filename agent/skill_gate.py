"""Skill validation gate — quality control for background review.

Inspired by SkillOpt (Microsoft): validation gate, learning-rate budget,
rejected-edit buffer, and held-out evaluation for skill edits.

Four layers of defense against skill bloat and garbage:

1. **Learning-rate budget** — limit how much a single review pass can
   change a skill file.  If the delta exceeds ``_MAX_SKILL_EDIT_TOKENS``,
   the edit is rejected and reverted.

2. **Dedup / similarity gate** — if a skill's content is >80% similar
   to another existing skill (word-overlap Jaccard), reject it.

3. **Rejected-edit buffer** — rejected edits are stored in a JSON file
   so future review passes can check if a similar edit was already
   rejected and skip it, or re-evaluate if enough context has changed.

4. **Held-out validation gate** — for modified skills, extract test cases
   from past sessions in state.db.  Evaluate old vs new skill content on
   those test cases via a single auxiliary LLM call each.  Accept the
   edit only if the new skill scores equal or higher than the old one.
   This prevents edits that look plausible but actually degrade the
   skill's usefulness on real tasks.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

# Maximum tokens a single review pass may add to a skill file.
# ~4 chars/token → 500 tokens ≈ 2000 chars.  Enough for a new section
# or pitfall, not enough to double a compact skill.
_MAX_SKILL_EDIT_TOKENS = 500
_MAX_SKILL_EDIT_CHARS = _MAX_SKILL_EDIT_TOKENS * 4

# Maximum total size of a SKILL.md in tokens.  Skills above this are
# flagged as bloated and the review is told to trim, not add.
_MAX_SKILL_SIZE_TOKENS = 2000
_MAX_SKILL_SIZE_CHARS = _MAX_SKILL_SIZE_TOKENS * 4

# Jaccard similarity threshold for dedup.  Above this → reject as duplicate.
_DEDUP_THRESHOLD = 0.80

# How many rejected edits to keep per skill.
_MAX_REJECTED_PER_SKILL = 5

# Rejected-edit buffer entries older than this (seconds) are pruned on save.
_BUFFER_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 days

# Rejected-edit buffer file path (lazy-computed).
_BUFFER_PATH: Optional[Path] = None


def _buffer_path() -> Path:
    global _BUFFER_PATH
    if _BUFFER_PATH is None:
        from hermes_constants import get_hermes_home
        _BUFFER_PATH = get_hermes_home() / "skill_rejected_edits.json"
    return _BUFFER_PATH


# ── Token estimation ───────────────────────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, len(text) // 4)


def _estimate_chars(tokens: int) -> int:
    return tokens * 4


# ── Skill snapshot ─────────────────────────────────────────────────────────

def _get_curator_skills_dir() -> Path:
    """Return the curator-managed skills directory."""
    from tools.skill_manager_tool import _skills_dir
    return _skills_dir()


def _is_protected_skill(skill_md_path: Path) -> bool:
    """Check if a skill is protected (not curator-managed).

    Protected = bundled, hub-installed, external, pinned, or user-owned.
    Only curator-managed skills (created_by: agent) are eligible for gate.
    """
    name = skill_md_path.parent.name
    try:
        from tools.skill_usage import is_curator_managed
        return not is_curator_managed(name)
    except Exception:
        return False


def _list_curator_skills() -> List[Path]:
    """List all curator-managed SKILL.md paths."""
    skills_dir = _get_curator_skills_dir()
    if not skills_dir.exists():
        return []
    result = []
    for skill_md in skills_dir.rglob("SKILL.md"):
        if _is_protected_skill(skill_md):
            continue
        result.append(skill_md)
    return result


def snapshot_skills() -> Dict[str, Dict[str, Any]]:
    """Take a snapshot of all curator-managed skills before review.

    Returns ``{skill_name: {"path": Path, "content": str, "tokens": int}}``.
    """
    snapshot: Dict[str, Dict[str, Any]] = {}
    for skill_md in _list_curator_skills():
        try:
            content = skill_md.read_text(encoding="utf-8", errors="replace")
            name = skill_md.parent.name
            snapshot[name] = {
                "path": skill_md,
                "content": content,
                "tokens": _estimate_tokens(content),
            }
        except Exception as exc:
            logger.debug("skill_gate: failed to snapshot %s: %s", skill_md, exc)
    return snapshot


# ── Similarity / dedup ─────────────────────────────────────────────────────

def _tokenize(text: str) -> Set[str]:
    """Simple word tokenizer for Jaccard similarity."""
    return set(re.findall(r"\b\w{3,}\b", text.lower()))


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = a & b
    union = a | b
    return len(intersection) / len(union) if union else 0.0


def _find_duplicate(
    skill_name: str,
    content: str,
    snapshot: Dict[str, Dict[str, Any]],
) -> Optional[str]:
    """Check if ``content`` is too similar to any other existing skill.

    Returns the name of the duplicate skill, or None.
    """
    tokens = _tokenize(content)
    if not tokens:
        return None
    for name, info in snapshot.items():
        if name == skill_name:
            continue
        other_tokens = _tokenize(info["content"])
        sim = _jaccard(tokens, other_tokens)
        if sim >= _DEDUP_THRESHOLD:
            return name
    return None


# ── Validation ─────────────────────────────────────────────────────────────

def validate_skill_edits(
    before: Dict[str, Dict[str, Any]],
    after: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Validate skill edits between before/after snapshots.

    Returns a list of validation results:
    ``{"skill": name, "action": "revert"|"keep", "reason": str, ...}``
    """
    results: List[Dict[str, Any]] = []

    # Build path → name index for rename detection.
    # If review renames a skill (delete old + create new at same path),
    # we detect it by matching paths, not names.
    before_paths: Dict[str, str] = {
        str(info["path"]): name for name, info in before.items()
    }
    after_paths: Dict[str, str] = {
        str(info["path"]): name for name, info in after.items()
    }

    # Check deleted skills (in before, missing from after by name AND path).
    # If the path still exists in after under a different name, it's a rename —
    # handled below as a modified skill, not a deletion.
    for name, info in before.items():
        if name in after:
            continue  # still exists by name
        skill_path = str(info["path"])
        if skill_path in after_paths:
            continue  # path exists under new name → rename, not delete
        results.append({
            "skill": name,
            "action": "revert",
            "reason": "deleted_by_review",
            "path": skill_path,
            "old_content": info["content"],
        })

    # Check new skills (in after but not before by name).
    # If the path existed in before under a different name, it's a rename —
    # treat as a modified skill (compare old content at that path vs new).
    for name, info in after.items():
        if name in before:
            continue
        skill_path = str(info["path"])
        old_name = before_paths.get(skill_path)
        if old_name and old_name in before:
            # Rename: compare old content (at same path) vs new content
            old_info = before[old_name]
            old_content = old_info["content"]
            new_content = info["content"]
            if old_content == new_content:
                continue  # only name changed, content same — allow
            old_tokens = old_info["tokens"]
            new_tokens = info["tokens"]
            delta = new_tokens - old_tokens
            if delta > _MAX_SKILL_EDIT_TOKENS:
                results.append({
                    "skill": name,
                    "action": "revert",
                    "reason": f"lr_budget_exceeded:rename:delta={delta}t>budget={_MAX_SKILL_EDIT_TOKENS}t",
                    "path": skill_path,
                    "old_content": old_content,
                    "new_content": new_content,
                    "delta_tokens": delta,
                })
                continue
            results.append({
                "skill": name,
                "action": "keep",
                "reason": f"rename_ok:from={old_name}",
                "delta_tokens": delta,
            })
            continue
        # Genuinely new skill (no path match)
        content = info["content"]
        tokens = info["tokens"]

        # Check size
        if tokens > _MAX_SKILL_SIZE_TOKENS:
            results.append({
                "skill": name,
                "action": "revert",
                "reason": f"size_overflow:{tokens}t>{_MAX_SKILL_SIZE_TOKENS}t",
                "path": str(info["path"]),
                "content": content,
            })
            continue

        # Check dedup
        dup = _find_duplicate(name, content, before)
        if dup:
            results.append({
                "skill": name,
                "action": "revert",
                "reason": f"duplicate_of:{dup}",
                "path": str(info["path"]),
                "content": content,
            })
            continue

        results.append({
            "skill": name,
            "action": "keep",
            "reason": "new_skill_ok",
            "tokens": tokens,
        })

    # Check modified skills (in both, content changed)
    for name, info in after.items():
        if name not in before:
            continue
        old_info = before[name]
        old_content = old_info["content"]
        new_content = info["content"]
        if old_content == new_content:
            continue  # No change

        old_tokens = old_info["tokens"]
        new_tokens = info["tokens"]
        delta = new_tokens - old_tokens

        # Learning-rate budget: reject if growth exceeds budget
        if delta > _MAX_SKILL_EDIT_TOKENS:
            results.append({
                "skill": name,
                "action": "revert",
                "reason": f"lr_budget_exceeded:delta={delta}t>budget={_MAX_SKILL_EDIT_TOKENS}t",
                "path": str(info["path"]),
                "old_content": old_content,
                "new_content": new_content,
                "delta_tokens": delta,
            })
            continue

        # Absolute size cap — only reject if the edit pushed the skill
        # OVER the cap. If it was already over before (e.g. large design
        # skill), allow small targeted edits as long as they don't grow it.
        if new_tokens > _MAX_SKILL_SIZE_TOKENS and old_tokens <= _MAX_SKILL_SIZE_TOKENS:
            results.append({
                "skill": name,
                "action": "revert",
                "reason": f"size_overflow:{new_tokens}t>{_MAX_SKILL_SIZE_TOKENS}t",
                "path": str(info["path"]),
                "old_content": old_content,
                "new_content": new_content,
            })
            continue

        results.append({
            "skill": name,
            "action": "keep",
            "reason": "edit_ok",
            "delta_tokens": delta,
            "old_tokens": old_tokens,
            "new_tokens": new_tokens,
        })

    return results


# ── Revert ─────────────────────────────────────────────────────────────────

def revert_skill(
    skill_path: Path,
    old_content: Optional[str] = None,
    delete_if_new: bool = True,
) -> bool:
    """Revert a skill to its previous content, or delete if it was newly created.

    Returns True on success.
    """
    try:
        if old_content is not None:
            # Parent directory may not exist if the skill was deleted
            # (review agent removed the whole skill directory).
            skill_path.parent.mkdir(parents=True, exist_ok=True)
            skill_path.write_text(old_content, encoding="utf-8")
            logger.info("skill_gate: reverted %s to previous content", skill_path)
        elif delete_if_new:
            # New skill that was rejected — delete the skill directory
            skill_dir = skill_path.parent
            import shutil
            shutil.rmtree(skill_dir, ignore_errors=True)
            logger.info("skill_gate: deleted rejected new skill %s", skill_dir)
        return True
    except Exception as exc:
        logger.warning("skill_gate: failed to revert %s: %s", skill_path, exc)
        return False


# ── Rejected-edit buffer ───────────────────────────────────────────────────

def _load_buffer() -> Dict[str, List[Dict[str, Any]]]:
    """Load the rejected-edit buffer from disk."""
    path = _buffer_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.debug("skill_gate: failed to load rejected buffer: %s", exc)
    return {}


def _save_buffer(buffer: Dict[str, List[Dict[str, Any]]]) -> None:
    """Save the rejected-edit buffer to disk.

    Prunes entries older than ``_BUFFER_MAX_AGE_SECONDS`` to prevent
    unbounded growth.
    """
    path = _buffer_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        cutoff = now - _BUFFER_MAX_AGE_SECONDS
        pruned: Dict[str, List[Dict[str, Any]]] = {}
        for skill_name, entries in buffer.items():
            fresh = [e for e in entries if e.get("timestamp", 0) >= cutoff]
            if fresh:
                pruned[skill_name] = fresh[-_MAX_REJECTED_PER_SKILL:]
        path.write_text(
            json.dumps(pruned, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("skill_gate: failed to save rejected buffer: %s", exc)


def add_to_rejected_buffer(
    skill_name: str,
    reason: str,
    content: str,
    old_content: Optional[str] = None,
) -> None:
    """Add a rejected edit to the buffer."""
    buffer = _load_buffer()
    entries = buffer.setdefault(skill_name, [])
    entries.append({
        "reason": reason,
        "content_preview": content[:200],
        "content_tokens": _estimate_tokens(content),
        "old_content_preview": (old_content or "")[:200] if old_content else None,
        "timestamp": time.time(),
    })
    # Trim to max per skill
    if len(entries) > _MAX_REJECTED_PER_SKILL:
        buffer[skill_name] = entries[-_MAX_REJECTED_PER_SKILL:]
    _save_buffer(buffer)


def get_rejected_summary(skill_name: Optional[str] = None) -> str:
    """Return a human-readable summary of rejected edits."""
    buffer = _load_buffer()
    if skill_name:
        entries = buffer.get(skill_name, [])
    else:
        entries = []
        for name, ents in buffer.items():
            for e in ents:
                entries.append({**e, "skill": name})

    if not entries:
        return "(no rejected edits)"

    lines = []
    for e in entries[-10:]:
        skill = e.get("skill", skill_name or "?")
        reason = e.get("reason", "?")
        ts = time.strftime("%Y-%m-%d %H:%M", time.gmtime(e.get("timestamp", 0)))
        lines.append(f"  {skill}: {reason} ({ts})")
    return "\n".join(lines)


# ── Mining recurring tasks ─────────────────────────────────────────────────

def mine_recurring_tasks(
    session_db: Any,
    session_id: str,
    lookback_hours: int = 168,
    min_repeats: int = 3,
) -> List[Dict[str, Any]]:
    """Mine recurring task patterns from recent sessions in state.db.

    Looks at user messages across recent sessions and finds repeated
    task patterns (by tool usage similarity and user message keywords).

    Returns a list of ``{"pattern": str, "count": int, "sessions": list}``.
    """
    patterns: Dict[str, List[str]] = {}
    try:
        # Get recent sessions
        cutoff = time.time() - (lookback_hours * 3600)
        rows = session_db._execute_read_all(
            "SELECT id, started_at FROM sessions "
            "WHERE started_at > ? AND parent_session_id IS NULL "
            "ORDER BY started_at DESC LIMIT 50",
            (cutoff,),
        )
        if not rows:
            return []

        for row in rows:
            sid = row["id"] if isinstance(row, dict) else row[0]
            # Get user messages for this session
            msgs = session_db._execute_read_all(
                "SELECT content FROM messages "
                "WHERE session_id = ? AND role = 'user' AND active = 1 "
                "ORDER BY id LIMIT 5",
                (sid,),
            )
            if not msgs:
                continue
            # Extract first user message as task signature
            first_msg = msgs[0]
            content = first_msg["content"] if isinstance(first_msg, dict) else first_msg[0]
            if not content or not isinstance(content, str):
                continue
            # Normalize: lowercase, strip, take first 100 chars
            signature = content.strip().lower()[:100]
            # Simple pattern: first 5 words
            words = re.findall(r"\b\w+\b", signature)
            pattern_key = " ".join(words[:5]) if len(words) >= 3 else None
            if pattern_key:
                patterns.setdefault(pattern_key, []).append(sid)

    except Exception as exc:
        logger.debug("skill_gate: mining recurring tasks failed: %s", exc)
        return []

    # Filter to patterns that repeat enough
    result = []
    for pattern, sids in patterns.items():
        if len(sids) >= min_repeats:
            result.append({
                "pattern": pattern,
                "count": len(sids),
                "sessions": sids[:5],
            })
    result.sort(key=lambda x: x["count"], reverse=True)
    return result[:5]


# ── Held-out validation gate ───────────────────────────────────────────────

# Number of test cases to extract from past sessions for evaluation.
# More = better signal but more API cost. 2 is a good balance.
_HELD_OUT_TEST_COUNT = 2

# Minimum length for a user message to be considered a valid test case.
_MIN_TEST_CASE_CHARS = 30

# Maximum length for a test case (truncated to keep API cost low).
_MAX_TEST_CASE_CHARS = 500

# Timeout for the evaluation LLM call.
_EVAL_TIMEOUT = 30.0

# Degradation threshold: revert only if new_avg < old_avg - threshold.
# Below 1.0 is noise (temp=0 still has sampling variance on ±1 score).
_DEGRADATION_THRESHOLD = 1.0

# Throttle: minimum seconds between held-out validation runs.
# Prevents +30-60s latency on every turn during rapid conversations.
_HELD_OUT_THROTTLE_SECONDS = 600  # 10 minutes

# Stop-words: user messages starting with these are not real tasks.
_STOP_WORD_PREFIXES = (
    "спасибо", "благодар", "thank", "thanks", "ок", "окей", "ok",
    "продолж", "contin", "давай", "хорошо", "good", "great",
    "да", "нет", "yes", "no", "ага", "угу", "хм", "hm",
    "/", "!", "?",  # commands, exclamations, questions
)

# Last held-out run timestamp (module-level throttle state).
_last_held_out_ts: float = 0.0


def _is_stop_word_msg(content: str) -> bool:
    """Check if a user message is a greeting/thanks/command — not a real task."""
    lower = content.strip().lower()
    if len(lower) < _MIN_TEST_CASE_CHARS:
        return True
    for prefix in _STOP_WORD_PREFIXES:
        if lower.startswith(prefix):
            return True
    return False


def extract_test_cases(
    session_db: Any,
    current_session_id: str,
    n: int = _HELD_OUT_TEST_COUNT,
    skill_keywords: Optional[List[str]] = None,
) -> List[str]:
    """Extract held-out test cases from past sessions in state.db.

    Picks representative user messages from sessions OTHER than the
    current one — these are "held-out" because the review agent never
    saw them during the current turn.

    If ``skill_keywords`` is provided, filters candidates to messages
    containing at least one keyword (case-insensitive). This ensures
    test cases are relevant to the skill being evaluated — evaluating
    a git-workflow skill on "make me a landing page" is noise.

    Returns a list of user message strings (truncated to _MAX_TEST_CASE_CHARS).
    """
    if session_db is None or not current_session_id:
        return []
    try:
        cutoff = time.time() - (168 * 3600)  # 7 days
        rows = session_db._execute_read_all(
            "SELECT id FROM sessions "
            "WHERE started_at > ? AND parent_session_id IS NULL "
            "AND id != ? "
            "ORDER BY started_at DESC LIMIT 20",
            (cutoff, current_session_id),
        )
        if not rows:
            return []

        candidates: List[str] = []
        for row in rows:
            sid = row["id"] if isinstance(row, dict) else row[0]
            if sid == current_session_id:
                continue
            msgs = session_db._execute_read_all(
                "SELECT content FROM messages "
                "WHERE session_id = ? AND role = 'user' AND active = 1 "
                "ORDER BY id LIMIT 5",
                (sid,),
            )
            if not msgs:
                continue
            for m in msgs:
                content = m["content"] if isinstance(m, dict) else m[0]
                if not isinstance(content, str):
                    continue
                content = content.strip()
                if _is_stop_word_msg(content):
                    continue
                # Keyword filter: if keywords provided, require at least one match
                if skill_keywords:
                    lower = content.lower()
                    if not any(kw.lower() in lower for kw in skill_keywords):
                        continue
                candidates.append(content[:_MAX_TEST_CASE_CHARS])

        if not candidates:
            return []

        # Pick diverse test cases: spread across the candidate list
        # rather than taking the first N (which might all be from one session).
        step = max(1, len(candidates) // n)
        selected = candidates[::step][:n]
        return selected

    except Exception as exc:
        logger.debug("skill_gate: extract_test_cases failed: %s", exc)
        return []


def _evaluate_skill_on_task(
    skill_name: str,
    skill_content: str,
    task: str,
    main_runtime: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Evaluate how well a skill covers a task. Returns 1-10 score or None.

    Uses a single auxiliary LLM call with a compact prompt. The LLM is
    asked to rate the skill's relevance and usefulness for the given task.
    """
    try:
        from agent.auxiliary_client import call_llm

        prompt = (
            f"You are evaluating a skill document for an AI agent.\n\n"
            f"SKILL NAME: {skill_name}\n"
            f"SKILL CONTENT:\n{skill_content[:2000]}\n\n"
            f"TASK (from a real user session):\n{task}\n\n"
            f"Rate how well this skill helps the agent handle this task.\n"
            f"Consider: relevance, completeness, actionability.\n"
            f"Respond with ONLY a single integer 1-10. No explanation."
        )

        response = call_llm(
            task="skill_validation",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0.0,
            timeout=_EVAL_TIMEOUT,
            main_runtime=main_runtime,
        )

        text = ""
        if hasattr(response, "choices") and response.choices:
            text = response.choices[0].message.content or ""
        elif isinstance(response, dict):
            text = response.get("content", "") or response.get("text", "")
        elif isinstance(response, str):
            text = response

        text = text.strip()
        match = re.search(r"\b(\d{1,2})\b", text)
        if match:
            score = int(match.group(1))
            return max(1, min(10, score))
        return None

    except Exception as exc:
        logger.debug("skill_gate: evaluate_skill failed: %s", exc)
        return None


def _extract_skill_keywords(skill_name: str, skill_content: str) -> List[str]:
    """Extract keywords from skill name and content for test-case filtering.

    Uses the skill name (split on hyphens/underscores) plus the first
    line of the skill content (usually a title/description).
    """
    keywords: List[str] = []
    # Split skill name: "git-workflow" → ["git", "workflow"]
    for part in re.split(r"[-_\s]+", skill_name.lower()):
        if len(part) >= 3:
            keywords.append(part)
    # First non-empty line of content often has the skill's domain
    for line in skill_content.split("\n"):
        line = line.strip().strip("#*").strip()
        if len(line) >= 5:
            for word in re.findall(r"\b\w{3,}\b", line.lower()):
                if word not in keywords:
                    keywords.append(word)
            break  # only first meaningful line
    return keywords[:10]  # cap to avoid over-filtering


def validate_with_held_out(
    before: Dict[str, Dict[str, Any]],
    after: Dict[str, Dict[str, Any]],
    test_cases: List[str],
    main_runtime: Optional[Dict[str, Any]] = None,
    session_db: Any = None,
    current_session_id: str = "",
) -> List[Dict[str, Any]]:
    """Run held-out validation on modified skills.

    For each skill that was modified (not new, not deleted), evaluates
    old vs new content on test cases relevant to that skill. Returns a
    list of results:
    ``{"skill": name, "action": "revert"|"keep", "reason": str, scores}``

    Test cases are filtered by keywords extracted from the skill name/content.
    If no relevant test cases are found for a skill, the edit is kept
    (fail-open — no signal → don't block).

    If evaluation fails (API error, parse error), the edit is kept (fail-open).
    Revert only triggers if ``new_avg < old_avg - _DEGRADATION_THRESHOLD``
    (default 1.0) — below the noise floor of ±1 score variance.
    """
    if not test_cases and not (session_db and current_session_id):
        return []

    # Find modified skills that passed the static gate
    modified: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
    for name, after_info in after.items():
        if name not in before:
            continue
        before_info = before[name]
        if before_info["content"] == after_info["content"]:
            continue
        modified.append((name, before_info, after_info))

    if not modified:
        return []

    results: List[Dict[str, Any]] = []

    for name, old_info, new_info in modified:
        old_content = old_info["content"]
        new_content = new_info["content"]

        # Extract skill-specific test cases if we have DB access
        skill_test_cases = test_cases
        if session_db and current_session_id:
            keywords = _extract_skill_keywords(name, old_content)
            if keywords:
                skill_test_cases = extract_test_cases(
                    session_db, current_session_id,
                    skill_keywords=keywords,
                )
                if not skill_test_cases:
                    # No relevant test cases → fail-open (keep)
                    results.append({
                        "skill": name,
                        "action": "keep",
                        "reason": "held_out_no_relevant_cases:fail_open",
                    })
                    continue

        if not skill_test_cases:
            results.append({
                "skill": name,
                "action": "keep",
                "reason": "held_out_no_cases:fail_open",
            })
            continue

        old_scores: List[Optional[int]] = []
        new_scores: List[Optional[int]] = []

        for task in skill_test_cases:
            old_score = _evaluate_skill_on_task(name, old_content, task, main_runtime)
            new_score = _evaluate_skill_on_task(name, new_content, task, main_runtime)
            old_scores.append(old_score)
            new_scores.append(new_score)

        # Calculate averages (ignoring None = failed evaluations)
        old_valid = [s for s in old_scores if s is not None]
        new_valid = [s for s in new_scores if s is not None]

        # Fail-open: if we couldn't get any valid scores for new, keep the edit
        if not new_valid:
            results.append({
                "skill": name,
                "action": "keep",
                "reason": "held_out_eval_failed:fail_open",
                "old_scores": old_scores,
                "new_scores": new_scores,
            })
            continue

        old_avg = sum(old_valid) / len(old_valid) if old_valid else 0
        new_avg = sum(new_valid) / len(new_valid)

        # Revert only if degradation exceeds threshold (noise floor ±1)
        if new_avg < old_avg - _DEGRADATION_THRESHOLD:
            results.append({
                "skill": name,
                "action": "revert",
                "reason": f"held_out_degraded:old={old_avg:.1f}>new={new_avg:.1f}",
                "path": str(new_info["path"]),
                "old_content": old_content,
                "new_content": new_content,
                "old_scores": old_scores,
                "new_scores": new_scores,
                "old_avg": old_avg,
                "new_avg": new_avg,
            })
        else:
            results.append({
                "skill": name,
                "action": "keep",
                "reason": f"held_out_ok:old={old_avg:.1f}>=new={new_avg:.1f}-{_DEGRADATION_THRESHOLD}",
                "old_scores": old_scores,
                "new_scores": new_scores,
                "old_avg": old_avg,
                "new_avg": new_avg,
            })

    return results


# ── Full gate pipeline ─────────────────────────────────────────────────────

def run_skill_gate(
    before: Dict[str, Dict[str, Any]],
    agent: Any = None,
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """Run the full validation gate after a background review.

    Takes the ``before`` snapshot (from ``snapshot_skills()``) and:
    1. Takes an ``after`` snapshot
    2. Validates all edits (static gate: size, dedup, budget, deletions)
    3. Reverts rejected edits from static gate
    4. If agent provided: runs held-out validation on surviving edits
    5. Reverts edits that degraded held-out scores
    6. Adds all rejected edits to the buffer

    Returns ``(after_snapshot, validation_results)``.
    """
    after = snapshot_skills()
    results = validate_skill_edits(before, after)

    # Phase 1: static gate reverts
    for r in results:
        if r["action"] != "revert":
            continue
        skill_name = r["skill"]
        skill_path = Path(r["path"])
        old_content = r.get("old_content")
        new_content = r.get("new_content") or r.get("content", "")

        revert_skill(skill_path, old_content=old_content, delete_if_new=(old_content is None))
        add_to_rejected_buffer(skill_name, r["reason"], new_content, old_content)
        logger.info("skill_gate: REJECTED edit to '%s' — %s", skill_name, r["reason"])

    # Re-snapshot after static reverts
    if any(r["action"] == "revert" for r in results):
        after = snapshot_skills()

    # Phase 2: held-out validation gate (only if agent provided + not throttled)
    if agent is not None:
        session_db = getattr(agent, "_session_db", None)
        session_id = getattr(agent, "session_id", "")
        if session_db and session_id:
            # Throttle: skip held-out if last run was < _HELD_OUT_THROTTLE_SECONDS ago
            global _last_held_out_ts
            now = time.time()
            if now - _last_held_out_ts < _HELD_OUT_THROTTLE_SECONDS:
                logger.debug(
                    "skill_gate: held-out validation throttled (%.0fs since last run)",
                    now - _last_held_out_ts,
                )
            else:
                _last_held_out_ts = now
                main_runtime = agent._current_main_runtime() if hasattr(agent, "_current_main_runtime") else None
                held_out_results = validate_with_held_out(
                    before, after, [], main_runtime,
                    session_db=session_db,
                    current_session_id=session_id,
                )

                for r in held_out_results:
                    results.append(r)
                    if r["action"] != "revert":
                        continue
                    skill_name = r["skill"]
                    skill_path = Path(r["path"])
                    old_content = r.get("old_content")
                    new_content = r.get("new_content", "")

                    revert_skill(skill_path, old_content=old_content, delete_if_new=False)
                    add_to_rejected_buffer(skill_name, r["reason"], new_content, old_content)
                    logger.info(
                        "skill_gate: HELD-OUT REJECTED edit to '%s' — %s",
                        skill_name, r["reason"],
                    )

                if any(r["action"] == "revert" for r in held_out_results):
                    after = snapshot_skills()

    return after, results


def format_gate_report(results: List[Dict[str, Any]]) -> str:
    """Format validation results as a human-readable report."""
    if not results:
        return ""
    reverted = [r for r in results if r["action"] == "revert"]
    if not reverted:
        return ""
    lines = ["  🚫 Skill gate rejected:"]
    for r in reverted:
        reason = r["reason"]
        if "held_out" in reason and "old_avg" in r:
            lines.append(
                f"    • {r['skill']}: {reason} "
                f"(old {r['old_avg']:.1f} → new {r['new_avg']:.1f})"
            )
        else:
            lines.append(f"    • {r['skill']}: {reason}")
    return "\n".join(lines)


# ── Prompt augmentation ────────────────────────────────────────────────────

def build_mining_context(agent: Any) -> str:
    """Build additional context for the review prompt from mining.

    Queries state.db for recurring task patterns and formats them
    as additional context for the review agent.
    """
    session_db = getattr(agent, "_session_db", None)
    if session_db is None:
        return ""
    session_id = getattr(agent, "session_id", "")
    if not session_id:
        return ""

    try:
        patterns = mine_recurring_tasks(session_db, session_id)
    except Exception:
        return ""

    if not patterns:
        return ""

    lines = ["\n\nRECURRING TASK PATTERNS (from recent sessions):"]
    for p in patterns:
        lines.append(f"  • '{p['pattern']}' — seen {p['count']} times")
    lines.append(
        "Consider whether existing skills cover these patterns well, "
        "or if a skill update would improve handling of recurring work."
    )
    return "\n".join(lines)


def build_budget_instruction() -> str:
    """Return instruction text about the learning-rate budget for the review prompt."""
    return (
        f"\n\nSKILL EDIT BUDGET: You may add at most {_MAX_SKILL_EDIT_TOKENS} tokens "
        f"(~{_MAX_SKILL_EDIT_CHARS} chars) to any single skill file in this pass. "
        f"If a skill needs more changes, spread them across multiple review passes. "
        f"Target skill size: 300-{_MAX_SKILL_SIZE_TOKENS} tokens. "
        f"Do NOT trim or restructure skills that are already larger than this target — "
        f"they may be intentionally large (design systems, reference banks). "
        f"Only apply small, targeted additions or fixes to them."
    )


__all__ = [
    "snapshot_skills",
    "run_skill_gate",
    "format_gate_report",
    "build_mining_context",
    "build_budget_instruction",
    "get_rejected_summary",
    "mine_recurring_tasks",
    "extract_test_cases",
    "validate_with_held_out",
]
