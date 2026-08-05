"""``hermes agent`` subcommand parser.

Agent preset management — list, select, and show behavioral presets.
Presets deep-merge into config.yaml (user values win).
"""

from __future__ import annotations

from typing import Callable


def build_agent_parser(subparsers, *, cmd_agent: Callable) -> None:
    """Attach the ``agent`` subcommand to ``subparsers``."""
    agent_parser = subparsers.add_parser(
        "agent",
        help="List, select, or show agent presets",
        description="Manage agent presets — behavioral templates that configure "
        "toolsets, display style, compression, and more. "
        "Run `hermes agent list` to see available presets, "
        "`hermes agent select <name>` to apply one.",
    )
    agent_sub = agent_parser.add_subparsers(dest="agent_action", required=True)

    # agent list
    agent_sub.add_parser("list", help="List available agent presets")

    # agent select
    select_parser = agent_sub.add_parser("select", help="Select an agent preset")
    select_parser.add_argument(
        "name",
        help="Preset name (e.g. default, coder, researcher, minimal)",
    )

    # agent show
    show_parser = agent_sub.add_parser("show", help="Show details of an agent preset")
    show_parser.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Preset name (defaults to the active preset)",
    )

    agent_parser.set_defaults(func=cmd_agent)
