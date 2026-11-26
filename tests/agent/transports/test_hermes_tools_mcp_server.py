"""Tests for the hermes-tools-as-MCP server module surface.

We don't run a live MCP session in unit tests — that requires the codex
subprocess + client + an event loop. These tests pin the static
contract: the module imports, the EXPOSED_TOOLS list is sane, and the
build helper assembles a server when the SDK is present.
"""

from __future__ import annotations

import inspect

import pytest

from agent.transports.hermes_tools_mcp_server import (
    _signature_from_schema,
)


class TestSignatureFromSchema:
    """Test the JSON Schema -> Python signature conversion."""

    def test_simple_required_string_param(self):
        """A required string param becomes str with no default."""
        schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        sig, annots = _signature_from_schema(schema)

        assert len(sig.parameters) == 1
        param = sig.parameters["query"]
        assert param.name == "query"
        assert param.kind == inspect.Parameter.KEYWORD_ONLY
        assert annots["query"] == str
        assert param.default is inspect.Parameter.empty


    def test_skip_private_params(self):
        """Params starting with '_' are excluded from the signature."""
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "_internal": {"type": "string"},
            },
            "required": ["query", "_internal"],
        }
        sig, annots = _signature_from_schema(schema)

        assert "_internal" not in sig.parameters
        assert "_internal" not in annots
        assert "query" in sig.parameters

    def test_all_json_types(self):
        """All JSON schema types map to correct Python types."""
        schema = {
            "type": "object",
            "properties": {
                "s": {"type": "string"},
                "i": {"type": "integer"},
                "n": {"type": "number"},
                "b": {"type": "boolean"},
                "a": {"type": "array"},
                "o": {"type": "object"},
            },
            "required": ["s", "i", "n", "b", "a", "o"],
        }
        _sig, annots = _signature_from_schema(schema)

        assert annots["s"] == str
        assert annots["i"] == int
        assert annots["n"] == float
        assert annots["b"] == bool
        assert annots["a"] == list
        assert annots["o"] == dict


class TestModuleSurface:

    def test_exposed_tools_are_safe_subset(self):
        """We MUST NOT expose tools codex already has, because codex'
        own builtins are better-integrated with its sandbox + approvals.
        Specifically: no terminal/shell, no read_file/write_file, no
        patch — those are codex's built-in tools."""
        from agent.transports.hermes_tools_mcp_server import EXPOSED_TOOLS
        forbidden = {
            "terminal", "shell", "read_file", "write_file", "patch",
            "search_files", "process",
        }
        leaked = forbidden & set(EXPOSED_TOOLS)
        assert not leaked, (
            f"these tools must NOT be exposed via the codex callback "
            f"because codex has built-in equivalents: {leaked}"
        )


    def test_every_exposed_tool_is_a_core_registered_tool(self):
        """Each exposed name must dispatch through handle_function_call in a plain Hermes process:
        a name only a plugin registers (the camofox plugin's browser_navigate & co.) is not core."""
        import model_tools
        from agent.transports.hermes_tools_mcp_server import EXPOSED_TOOLS
        missing = set(EXPOSED_TOOLS) - set(model_tools.get_all_tool_names())
        assert not missing, f"EXPOSED_TOOLS names no core tool registers: {sorted(missing)}"


class TestMain:
    def test_main_returns_2_when_mcp_unavailable(self, monkeypatch):
        """When the mcp package isn't installed, main() should exit
        cleanly with code 2 and an install hint, not crash."""
        import agent.transports.hermes_tools_mcp_server as m

        def boom_build(*a, **kw):
            raise ImportError("mcp not installed")

        monkeypatch.setattr(m, "_build_server", boom_build)
        rc = m.main(["--verbose"])
        assert rc == 2

    def test_main_handles_keyboard_interrupt(self, monkeypatch):
        import agent.transports.hermes_tools_mcp_server as m

        class FakeServer:
            def run(self):
                raise KeyboardInterrupt()

        monkeypatch.setattr(m, "_build_server", lambda: FakeServer())
        rc = m.main([])
        assert rc == 0

    def test_main_returns_1_on_runtime_error(self, monkeypatch):
        import agent.transports.hermes_tools_mcp_server as m

        class CrashingServer:
            def run(self):
                raise RuntimeError("boom")

        monkeypatch.setattr(m, "_build_server", lambda: CrashingServer())
        rc = m.main([])
        assert rc == 1


class TestDisabledToolsetsHonored:
    """agent.disabled_toolsets reaches the sidecar's exposed surface (per-tool-disable patch).

    Real build against the temp HERMES_HOME config: a tool turned off by name must not be
    re-exposed on Codex-routed turns, in every config form the other surfaces accept.
    """

    @staticmethod
    def _exposed_names():
        import asyncio

        pytest.importorskip("mcp.server")
        from agent.transports.hermes_tools_mcp_server import _build_server

        return {tool.name for tool in asyncio.run(_build_server().list_tools())}

    @pytest.mark.parametrize(
        "value",
        ['["skill_view"]', "skill_view", "\"['skill_view']\""],
        ids=["list", "bare-name", "json-array-string"],
    )
    def test_disabled_tool_is_not_exposed(self, value):
        from hermes_constants import get_hermes_home

        assert "skill_view" in self._exposed_names(), "precondition: skill_view exposed by default"
        (get_hermes_home() / "config.yaml").write_text(
            f"agent:\n  disabled_toolsets: {value}\n", encoding="utf-8"
        )
        exposed = self._exposed_names()
        assert "skill_view" not in exposed
        assert "skills_list" in exposed, "only the disabled tool leaves the surface"
