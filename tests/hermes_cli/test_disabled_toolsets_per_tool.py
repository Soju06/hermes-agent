"""Per-tool ``agent.disabled_toolsets`` entries (fork: per-tool-disable) against the #134593 clear and hint logic.

A per-tool entry (``web_search``, ``text_to_speech``) is a deliberate cut inside a toolset: `hermes tools`, doctor
and every save keep it while the owning toolset stays on, and say how to undo it. When the entry switches a toolset
off entirely, the picker shows that toolset unchecked, so checking it again clears the entry (#49995)."""
from unittest.mock import patch

from hermes_cli.tools_config import (
    _disabled_entry_tools,
    _get_platform_tools,
    _save_platform_tools,
    toolsets_partially_stripped,
)


def _enabled(config):
    return _get_platform_tools(config, "cli", include_default_mcp_servers=False)


def _save(config, enabled):
    with patch("hermes_cli.tools_config.save_config"):
        _save_platform_tools(config, "cli", set(enabled))


def test_per_tool_entry_removes_just_that_tool():
    assert _disabled_entry_tools("web_search") == {"web_search"}
    assert _disabled_entry_tools("search") == {"web_search"}  # the subset toolset, unchanged


def test_per_tool_entry_inside_an_enabled_toolset_is_reported_and_kept():
    config = {"platform_toolsets": {"cli": ["file", "terminal", "web"]},
              "agent": {"disabled_toolsets": ["web_search", "browser"]}}
    enabled = _enabled(config)
    assert "web" in enabled
    report = {"web": (["web_search"], ["web_search"], "remove web_search from agent.disabled_toolsets to restore")}
    assert toolsets_partially_stripped(config, enabled) == report

    _save(config, enabled)  # any save, including `hermes tools enable web`
    assert config["agent"]["disabled_toolsets"] == ["web_search", "browser"]
    _save(config, enabled | {"web"})
    assert config["agent"]["disabled_toolsets"] == ["web_search", "browser"]
    assert toolsets_partially_stripped(config, _enabled(config)) == report


def test_mixed_entries_never_promise_enable_clears_a_per_tool_entry():
    config = {"platform_toolsets": {"cli": ["file", "terminal", "browser"]},
              "agent": {"disabled_toolsets": ["browser-cdp", "browser_exec"]}}
    stripped, entries, hint = toolsets_partially_stripped(config, _enabled(config))["browser"]
    assert (stripped, entries) == (["browser_cdp", "browser_dialog", "browser_exec"], ["browser-cdp", "browser_exec"])
    assert hint == "remove browser-cdp, browser_exec from agent.disabled_toolsets to restore"


def test_checking_a_toolset_a_per_tool_entry_switched_off_clears_the_entry():
    config = {"platform_toolsets": {"cli": ["file", "terminal", "tts"]},
              "agent": {"disabled_toolsets": ["text_to_speech", "browser"]}}
    enabled = _enabled(config)
    assert "tts" not in enabled
    _save(config, enabled)  # a save that leaves tts unchecked keeps the cut
    assert config["agent"]["disabled_toolsets"] == ["text_to_speech", "browser"]

    _save(config, _enabled(config) | {"tts"})
    assert config["agent"]["disabled_toolsets"] == ["browser"]
    assert "tts" in _enabled(config)


def test_reopening_clears_only_the_per_tool_entries_that_keep_the_toolset_off():
    # `search` is covered by checking web (upstream #134593); then web_extract alone no longer switches web off,
    # so the deliberate web_extract cut stays and web comes back with web_search.
    config = {"platform_toolsets": {"cli": ["file", "terminal"]},
              "agent": {"disabled_toolsets": ["search", "web_extract"]}}
    _save(config, _enabled(config) | {"web"})
    assert config["agent"]["disabled_toolsets"] == ["web_extract"]
    assert "web" in _enabled(config)

    # A remaining toolset entry (not covered by the save) still switches web off: clearing the per-tool entry
    # would lose it without turning web on.
    config = {"platform_toolsets": {"cli": ["file", "terminal"]},
              "agent": {"disabled_toolsets": ["web_search", "safe"]}}
    _save(config, _enabled(config) | {"web"})
    assert config["agent"]["disabled_toolsets"] == ["web_search", "safe"]
