"""Regression tests for extracted GatewaySlashCommandsMixin ownership.

Slash-command handlers were lifted out of the large gateway/run.py module into
GatewaySlashCommandsMixin.  If a same-named handler remains directly on
GatewayRunner, Python's MRO resolves the stale GatewayRunner copy first and the
fixed mixin implementation is silently shadowed.  That exact failure broke
Discord /model when parse_model_flags gained the --session return value.
"""

from __future__ import annotations

import inspect

from gateway.slash_commands import GatewaySlashCommandsMixin

SLASH_COMMAND_METHODS = [
    "_handle_help_command",
    "_handle_commands_command",
    "_handle_model_command",
    "_handle_codex_runtime_command",
    "_handle_personality_command",
    "_handle_retry_command",
]


def test_mixin_defines_extracted_slash_command_methods():
    for method_name in SLASH_COMMAND_METHODS:
        assert hasattr(GatewaySlashCommandsMixin, method_name), (
            f"mixin missing {method_name}"
        )


def test_gateway_runner_resolves_extracted_slash_commands_to_mixin():
    # Import here so a heavy gateway import only happens if the first test passed.
    from gateway.run import GatewayRunner

    assert issubclass(GatewayRunner, GatewaySlashCommandsMixin)
    for method_name in SLASH_COMMAND_METHODS:
        owner = next(c for c in GatewayRunner.__mro__ if method_name in c.__dict__)
        assert owner is GatewaySlashCommandsMixin, (
            f"{method_name} resolved to {owner.__name__}, expected the mixin"
        )
        resolved = getattr(GatewayRunner, method_name)
        source_file = inspect.getsourcefile(resolved)
        assert source_file is not None
        assert source_file.endswith("gateway/slash_commands.py")
