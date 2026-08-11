"""Tests for the central command registry and autocomplete."""

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from hermes_cli.commands import (
    COMMAND_REGISTRY,
    COMMANDS,
    COMMANDS_BY_CATEGORY,
    CommandDef,
    GATEWAY_KNOWN_COMMANDS,
    SUBCOMMANDS,
    SlashCommandAutoSuggest,
    SlashCommandCompleter,
    _CMD_NAME_LIMIT,
    _SLACK_RESERVED_COMMANDS,
    _SLACK_VIA_HERMES_ONLY,
    _TG_NAME_LIMIT,
    _clamp_command_names,
    _clamp_telegram_names,
    _sanitize_telegram_name,
    gateway_help_lines,
    resolve_command,
    slack_app_manifest,
    slack_native_slashes,
    slack_subcommand_map,
    telegram_bot_commands,
    telegram_menu_commands,
    telegram_menu_max_commands,
)

def _completions(completer: SlashCommandCompleter, text: str):
    return list(
        completer.get_completions(
            Document(text=text),
            CompleteEvent(completion_requested=True),
        )
    )

# ---------------------------------------------------------------------------
# CommandDef registry tests
# ---------------------------------------------------------------------------

class TestCommandRegistry:

    def test_no_duplicate_canonical_names(self):
        names = [cmd.name for cmd in COMMAND_REGISTRY]
        assert len(names) == len(set(names)), f"Duplicate names: {[n for n in names if names.count(n) > 1]}"

    def test_no_alias_collides_with_canonical_name(self):
        """An alias must not shadow another command's canonical name."""
        canonical_names = {cmd.name for cmd in COMMAND_REGISTRY}
        for cmd in COMMAND_REGISTRY:
            for alias in cmd.aliases:
                if alias in canonical_names:
                    # reset -> new is intentional (reset IS an alias for new)
                    target = next(c for c in COMMAND_REGISTRY if c.name == alias)
                    # This should only happen if the alias points to the same entry
                    assert resolve_command(alias).name == cmd.name or alias == cmd.name, \
                        f"Alias '{alias}' of '{cmd.name}' shadows canonical '{target.name}'"

# ---------------------------------------------------------------------------
# resolve_command tests
# ---------------------------------------------------------------------------

class TestResolveCommand:

    def test_topic_is_gateway_command(self):
        topic = resolve_command("topic")
        assert topic is not None
        assert topic.name == "topic"
        assert "topic" in GATEWAY_KNOWN_COMMANDS

    def test_context_command_registered_with_ctx_alias(self):
        ctx = resolve_command("context")
        assert ctx is not None
        assert ctx.name == "context"
        assert resolve_command("ctx").name == "context"
        assert "all" in (ctx.subcommands or ())
        # Available on both CLI and gateway surfaces
        assert not ctx.cli_only and not ctx.gateway_only
        assert "context" in GATEWAY_KNOWN_COMMANDS

# ---------------------------------------------------------------------------
# Derived dicts (backwards compat)
# ---------------------------------------------------------------------------

class TestDerivedDicts:

    def test_commands_dict_includes_aliases(self):
        assert "/bg" in COMMANDS
        assert "/reset" in COMMANDS
        assert "/q" in COMMANDS
        assert "/exit" in COMMANDS
        assert "/reload_mcp" in COMMANDS
        assert "/gateway" in COMMANDS

    def test_commands_by_category_covers_all_categories(self):
        registry_categories = {cmd.category for cmd in COMMAND_REGISTRY if not cmd.gateway_only}
        assert set(COMMANDS_BY_CATEGORY.keys()) == registry_categories

# ---------------------------------------------------------------------------
# Gateway helpers
# ---------------------------------------------------------------------------

class TestGatewayKnownCommands:

    def test_includes_config_gated_cli_only(self):
        """Commands with gateway_config_gate are always in GATEWAY_KNOWN_COMMANDS."""
        for cmd in COMMAND_REGISTRY:
            if cmd.gateway_config_gate:
                assert cmd.name in GATEWAY_KNOWN_COMMANDS, \
                    f"config-gated command '{cmd.name}' should be in GATEWAY_KNOWN_COMMANDS"

    def test_is_frozenset(self):
        assert isinstance(GATEWAY_KNOWN_COMMANDS, frozenset)

class TestGatewayHelpLines:

    def test_excludes_cli_only_commands_without_config_gate(self):
        import re
        lines = gateway_help_lines()
        joined = "\n".join(lines)
        for cmd in COMMAND_REGISTRY:
            if cmd.cli_only and not cmd.gateway_config_gate:
                # Word-boundary match so `/reload` doesn't match `/reload-mcp`
                pattern = rf'`/{re.escape(cmd.name)}(?![-_\w])'
                assert not re.search(pattern, joined), \
                    f"cli_only command /{cmd.name} should not be in gateway help"

    def test_includes_alias_note_for_bg(self):
        lines = gateway_help_lines()
        bg_line = [l for l in lines if "/background" in l]
        assert len(bg_line) == 1
        assert "/bg" in bg_line[0]

class TestTelegramBotCommands:
    def test_returns_list_of_tuples(self):
        cmds = telegram_bot_commands()
        assert len(cmds) > 10
        for name, desc in cmds:
            assert isinstance(name, str)
            assert isinstance(desc, str)

    def test_no_hyphens_in_command_names(self):
        """Telegram does not support hyphens in command names."""
        for name, _ in telegram_bot_commands():
            assert "-" not in name, f"Telegram command '{name}' contains a hyphen"

    def test_includes_builtin_commands_with_required_args(self):
        """Built-in arg-taking commands (e.g. /queue, /steer, /background)
        are now included because their handlers return usage text when
        invoked without arguments — issue #24312."""
        names = {name for name, _ in telegram_bot_commands()}
        assert "background" in names
        assert "queue" in names
        assert "steer" in names

class TestSlackSubcommandMap:
    def test_returns_dict(self):
        mapping = slack_subcommand_map()
        assert isinstance(mapping, dict)
        assert len(mapping) > 10

    def test_values_are_slash_prefixed(self):
        for key, val in slack_subcommand_map().items():
            assert val.startswith("/"), f"Slack mapping for '{key}' should start with /"

    def test_excludes_cli_only_without_config_gate(self):
        mapping = slack_subcommand_map()
        for cmd in COMMAND_REGISTRY:
            if cmd.cli_only and not cmd.gateway_config_gate:
                assert cmd.name not in mapping

class TestSlackNativeSlashes:
    """Slack native slash command generation — used to register every
    COMMAND_REGISTRY entry as a first-class Slack slash, matching Discord
    and Telegram."""

    def test_names_respect_slack_limits(self):
        for name, _desc, _hint in slack_native_slashes():
            # Slack: lowercase a-z, 0-9, hyphens, underscores; max 32 chars
            assert len(name) <= 32, f"slash {name!r} exceeds 32 chars"
            assert name == name.lower()
            for ch in name:
                assert ch.isalnum() or ch in "-_", f"invalid char {ch!r} in {name!r}"

    def test_telegram_parity(self):
        """Every Telegram bot command must be registerable on Slack too.

        This catches the old behavior where Slack users couldn't invoke
        commands like /btw natively. If a future command surfaces on
        Telegram but not Slack (because of Slack's 50-slash cap), this
        test fails loudly so we can curate the list rather than silently
        dropping parity.

        Slack-reserved built-in commands (e.g. /status) are excluded
        from parity checks since they cannot be registered on Slack.
        """
        slack_names = {n for n, _d, _h in slack_native_slashes()}
        tg_names = {n for n, _d in telegram_bot_commands()}
        # Some Telegram names have underscores where Slack uses hyphens
        # (e.g. set_home vs sethome). Normalize both sides for comparison.
        def _norm(s: str) -> str:
            return s.replace("-", "_").replace("__", "_").strip("_")

        slack_norm = {_norm(n) for n in slack_names}
        tg_norm = {_norm(n) for n in tg_names}
        reserved_norm = {_norm(n) for n in _SLACK_RESERVED_COMMANDS}
        # Commands deliberately routed through /hermes <command> on Slack only
        # (Slack's 50-slash cap) are expected to be absent from native slashes.
        via_hermes_norm = {_norm(n) for n in _SLACK_VIA_HERMES_ONLY}
        missing = (tg_norm - slack_norm) - reserved_norm - via_hermes_norm
        assert not missing, (
            f"commands on Telegram but missing from Slack native slashes: {sorted(missing)}"
        )

class TestSlackAppManifest:
    """Generated Slack app manifest (used by `hermes slack manifest`)."""

    def test_each_slash_has_required_fields(self):
        m = slack_app_manifest()
        for entry in m["features"]["slash_commands"]:
            assert entry["command"].startswith("/")
            assert "description" in entry
            assert "url" in entry
            # should_escape must be present (Slack defaults to True which
            # HTML-escapes args — we want the raw text)
            assert "should_escape" in entry

    def test_btw_is_in_manifest(self):
        """Regression: /btw must be a native Slack slash, not just a
        /hermes subcommand."""
        m = slack_app_manifest()
        commands = [c["command"] for c in m["features"]["slash_commands"]]
        assert "/btw" in commands

# ---------------------------------------------------------------------------
# Config-gated gateway commands
# ---------------------------------------------------------------------------

class TestGatewayConfigGate:
    """Tests for the gateway_config_gate mechanism on CommandDef."""

    def test_verbose_in_gateway_known_commands(self):
        """Config-gated commands are always recognized by the gateway."""
        assert "verbose" in GATEWAY_KNOWN_COMMANDS

    def test_config_gate_excluded_from_help_when_off(self, tmp_path, monkeypatch):
        """When the config gate is falsy, the command should not appear in help."""
        # Write a config with the gate off (default)
        config_file = tmp_path / "config.yaml"
        config_file.write_text("display:\n  tool_progress_command: false\n")
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

        lines = gateway_help_lines()
        joined = "\n".join(lines)
        assert "`/verbose" not in joined

    def test_config_gate_included_in_slack_when_on(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("display:\n  tool_progress_command: true\n")
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

        mapping = slack_subcommand_map()
        assert "verbose" in mapping

# ---------------------------------------------------------------------------
# Autocomplete (SlashCommandCompleter)
# ---------------------------------------------------------------------------

class TestSlashCommandCompleter:
    # -- basic prefix completion -----------------------------------------

    # -- exact-match trailing space --------------------------------------

    # -- non-slash input returns nothing ---------------------------------

    # -- skill commands via provider ------------------------------------

    def test_skill_commands_are_completed_from_provider(self):
        completer = SlashCommandCompleter(
            skill_commands_provider=lambda: {
                "/gif-search": {"description": "Search for GIFs across providers"},
            }
        )

        completions = _completions(completer, "/gif")

        assert len(completions) == 1
        assert completions[0].text == "gif-search"
        assert completions[0].display_text == "/gif-search"
        assert completions[0].display_meta_text == "⚡ Search for GIFs across providers"

    def test_skill_provider_exception_is_swallowed(self):
        """A broken provider should not crash autocomplete."""
        completer = SlashCommandCompleter(
            skill_commands_provider=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        # Should return builtin matches only, no crash
        completions = _completions(completer, "/he")
        texts = {item.text for item in completions}
        assert "help" in texts

# ── Stacked slash-skill completion ──────────────────────────────────────

def _stacked_completer(**extra_skills):
    skills = {
        "/skill-a": {"description": "Skill A"},
        "/skill-b": {"description": "Skill B"},
        "/skill-c": {"description": "Skill C"},
        **extra_skills,
    }
    return SlashCommandCompleter(skill_commands_provider=lambda: skills)

class TestStackedSkillCompletion:
    """Second+ leading skill tokens keep getting completions (stacked
    slash-skill invocations, Claude Code v2.1.199 port follow-up)."""

    def test_no_completions_for_instruction_text(self):
        assert _completions(_stacked_completer(), "/skill-a do the") == []
        assert _completions(_stacked_completer(), "/skill-a ") == []

    def test_cap_stops_completions(self):
        skills = {f"/stk-{i}": {"description": f"S{i}"} for i in range(8)}
        completer = SlashCommandCompleter(skill_commands_provider=lambda: skills)
        text = " ".join(f"/stk-{i}" for i in range(5)) + " /stk-"
        assert _completions(completer, text) == []

# ── SUBCOMMANDS extraction ──────────────────────────────────────────────

class TestSubcommands:
    def test_explicit_subcommands_extracted(self):
        """Commands with explicit subcommands on CommandDef are extracted."""
        assert "/skills" in SUBCOMMANDS
        assert "install" in SUBCOMMANDS["/skills"]

    def test_commands_without_subcommands_not_in_dict(self):
        """Plain commands should not appear in SUBCOMMANDS."""
        assert "/help" not in SUBCOMMANDS
        assert "/quit" not in SUBCOMMANDS
        assert "/clear" not in SUBCOMMANDS

# ── Subcommand tab completion ───────────────────────────────────────────

class TestSubcommandCompletion:

    def test_tools_enable_skips_already_listed(self, monkeypatch):
        """If the user already typed a name, don't suggest it again."""
        monkeypatch.setattr(
            "hermes_cli.tools_config._get_platform_tools",
            lambda *_a, **_k: set(),
        )
        monkeypatch.setattr("hermes_cli.config.load_config", lambda: {})
        monkeypatch.setattr(
            "hermes_cli.tools_config._get_plugin_toolset_keys",
            lambda: set(),
        )

        completions = _completions(SlashCommandCompleter(), "/tools enable spotify ")
        texts = {c.text for c in completions}
        assert "spotify" not in texts

    def _fake_gateway(self, monkeypatch, platforms):
        """Patch load_gateway_config with a fake whose connected platforms are
        the keys of `platforms` (name -> home as None or a (chat_id, name) tuple).
        """
        from types import SimpleNamespace

        enums = {name: SimpleNamespace(value=name) for name in platforms}
        homes = {
            name: (None if home is None else SimpleNamespace(chat_id=home[0], name=home[1]))
            for name, home in platforms.items()
        }
        fake = SimpleNamespace(
            get_connected_platforms=lambda: list(enums.values()),
            get_home_channel=lambda p: homes[p.value],
        )
        monkeypatch.setattr("gateway.config.load_gateway_config", lambda: fake)

    def test_handoff_completes_connected_platforms(self, monkeypatch):
        """`/handoff ` offers connected platforms, with or without a home channel."""
        self._fake_gateway(
            monkeypatch,
            {
                "telegram": ("123", "Me"),
                "discord": None,  # no home channel yet -> still listed
            },
        )

        texts = {c.text for c in _completions(SlashCommandCompleter(), "/handoff ")}
        assert texts == {"telegram", "discord"}

# ── Ghost text (SlashCommandAutoSuggest) ────────────────────────────────

def _suggestion(text: str, completer=None) -> str | None:
    """Get ghost text suggestion for given input."""
    suggest = SlashCommandAutoSuggest(completer=completer)
    doc = Document(text=text)

    class FakeBuffer:
        pass

    result = suggest.get_suggestion(FakeBuffer(), doc)
    return result.text if result else None

class TestGhostText:
    def test_command_name_suggestion(self):
        """/he → 'lp'"""
        assert _suggestion("/he") == "lp"

    # -- stacked slash-skill ghost text -----------------------------------

    def test_stacked_skill_ghost_text_skips_used(self):
        completer = SlashCommandCompleter(
            skill_commands_provider=lambda: {
                "/alpha": {"description": "A"},
                "/beta": {"description": "B"},
            }
        )
        assert _suggestion("/alpha /a", completer=completer) is None
        assert _suggestion("/alpha /b", completer=completer) == "eta"

# ---------------------------------------------------------------------------
# Telegram command name sanitization
# ---------------------------------------------------------------------------

class TestSanitizeTelegramName:
    """Tests for _sanitize_telegram_name() — Telegram requires [a-z0-9_] only."""

    def test_hyphens_replaced_with_underscores(self):
        assert _sanitize_telegram_name("my-skill-name") == "my_skill_name"

    def test_consecutive_underscores_collapsed(self):
        assert _sanitize_telegram_name("a---b") == "a_b"
        assert _sanitize_telegram_name("a-+-b") == "a_b"

    def test_leading_trailing_underscores_stripped(self):
        assert _sanitize_telegram_name("-leading") == "leading"
        assert _sanitize_telegram_name("trailing-") == "trailing"
        assert _sanitize_telegram_name("-both-") == "both"

# ---------------------------------------------------------------------------
# Telegram command name clamping (32-char limit)
# ---------------------------------------------------------------------------

class TestClampTelegramNames:
    """Tests for _clamp_telegram_names() — 32-char enforcement + collision."""

    def test_collision_between_entries_gets_incrementing_digits(self):
        # Two long names that truncate to the same 32-char prefix
        base = "y" * 40
        entries = [(base + "_alpha", "d1"), (base + "_beta", "d2")]
        result = _clamp_telegram_names(entries, set())
        assert len(result) == 2
        assert result[0][0] == "y" * _TG_NAME_LIMIT
        assert result[1][0] == "y" * (_TG_NAME_LIMIT - 1) + "0"

    def test_all_digits_exhausted_drops_entry(self):
        prefix = "w" * _TG_NAME_LIMIT
        # Reserve the plain truncation + all 10 digit slots
        reserved = {prefix} | {"w" * (_TG_NAME_LIMIT - 1) + str(d) for d in range(10)}
        long_name = "w" * 50
        result = _clamp_telegram_names([(long_name, "d")], reserved)
        assert result == []

class TestClampCommandNamesTriples:
    """Tests for _clamp_command_names with 3-tuples (name, desc, cmd_key).

    Skill entries pass through _clamp_command_names as 3-tuples so the
    original cmd_key survives name truncation.  Before the fix in PR #18951,
    the code stripped cmd_key into a side-dict keyed by the *original*
    (name, desc) pair — after truncation the lookup key no longer matched,
    silently losing the cmd_key.
    """

    def test_long_name_preserves_cmd_key(self):
        long = "a" * 50
        cmd_key = f"/{long}"
        result = _clamp_command_names([(long, "desc", cmd_key)], set())
        assert len(result) == 1
        name, desc, key = result[0]
        assert len(name) == _CMD_NAME_LIMIT
        assert key == cmd_key, "cmd_key must survive name clamping"

    def test_collision_preserves_cmd_key(self):
        prefix = "x" * _CMD_NAME_LIMIT
        long = "x" * 50
        result = _clamp_command_names(
            [(long, "desc", "/long-skill")], reserved={prefix},
        )
        assert len(result) == 1
        name, _desc, key = result[0]
        assert name == "x" * (_CMD_NAME_LIMIT - 1) + "0"
        assert key == "/long-skill"

class TestBackwardCompatAliases:
    """The renamed constants/functions still exist under the old names."""

    def test_tg_name_limit_alias(self):
        assert _TG_NAME_LIMIT == _CMD_NAME_LIMIT == 32

    def test_clamp_telegram_names_is_clamp_command_names(self):
        assert _clamp_telegram_names is _clamp_command_names

# ---------------------------------------------------------------------------
# Discord skill command registration
# ---------------------------------------------------------------------------

class TestPluginCommandEnumeration:
    """Plugin commands registered via ctx.register_command() must be surfaced
    by every gateway enumerator (Telegram menu, Slack subcommand map, etc.).
    """

    def _patch_plugin_commands(self, monkeypatch, commands):
        """Monkeypatch hermes_cli.plugins.get_plugin_commands() to a fixed dict."""
        from hermes_cli import plugins as _plugins_mod

        monkeypatch.setattr(
            _plugins_mod, "get_plugin_commands", lambda: dict(commands)
        )

    def test_plugin_command_with_hyphens_sanitized_for_telegram(self, monkeypatch):
        """Plugin names containing hyphens must be underscore-normalized for Telegram."""
        self._patch_plugin_commands(monkeypatch, {
            "my-plugin-cmd": {
                "handler": lambda _a: "ok",
                "description": "desc",
                "args_hint": "",
                "plugin": "p",
            }
        })
        names = {name for name, _desc in telegram_bot_commands()}
        assert "my_plugin_cmd" in names
        assert "my-plugin-cmd" not in names

    def test_plugin_enumerator_handles_missing_plugin_manager(self, monkeypatch):
        """Enumerators must never raise when plugin discovery raises."""
        from hermes_cli import plugins as _plugins_mod

        def _boom():
            raise RuntimeError("plugin system down")

        monkeypatch.setattr(_plugins_mod, "get_plugin_commands", _boom)

        # Both calls should succeed and just return the built-in set.
        tg_names = {name for name, _desc in telegram_bot_commands()}
        slack_names = set(slack_subcommand_map())
        assert "status" in tg_names
        assert "status" in slack_names
