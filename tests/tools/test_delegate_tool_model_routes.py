"""``delegation.default_route`` (fork model routes) next to upstream's delegation backends.

``default_route`` is the fork's answer for an unrouted task on the DEFAULT delegation backend. The
backend a session's ``delegate_task`` was built from (``agent._delegation_backend``, tools/AGENTS.md
§ Delegation) and a configured non-default ``delegation.backend`` are explicit backend choices, so the
chosen backend answers unrouted tasks. A task that names its route keeps it. Real plugin discovery and
real ``delegate_task``; only child construction/run, provider health and credential resolution are
stand-ins (2026-10-30 sync review; the session's backend follows upstream 552f735368 since 2026-11-25).
"""
import json

import pytest

from tests.tools.test_delegate import _ROUTES_FULL_CFG, _fake_runtime_resolver, _health_for
from tests.tools.test_delegation_backends import OPTIONS, _home, _parent, recorded  # noqa: F401 (fixture)


@pytest.fixture
def routes(monkeypatch):
    monkeypatch.setattr("tools.delegate_tool_model_routes._load_full_config", lambda: _ROUTES_FULL_CFG)
    monkeypatch.setattr("hermes_cli.model_routes.provider_health", _health_for({"p1", "p2"}))
    monkeypatch.setattr("hermes_cli.runtime_provider.resolve_runtime_provider", _fake_runtime_resolver)


def _delegate(session_id, *tasks, built_for=None):
    from tools.delegate_tool import delegate_task
    parent = _parent(session_id)
    if built_for is not None:  # the backend this session's delegate_task was built from
        parent._delegation_backend = built_for
    out = json.loads(delegate_task(tasks=list(tasks), parent_agent=parent))
    assert "error" not in out, out
    assert [r["status"] for r in out["results"]] == ["completed"] * len(tasks), out


PICKED = {"goal": "Summarize the quarterly report for the finance channel", "option": "fast"}
NAMED = {"goal": "Refactor the gateway retry loop and add regression tests", "route": "dev"}


def test_default_route_steps_aside_for_the_sessions_built_backend(tmp_path, monkeypatch, recorded, routes):
    """Red before the fix: ``default_route`` claimed the task of a session built for another backend before
    that backend was asked."""
    _, manager = _home(tmp_path, monkeypatch, {"default_route": "chat",
                                               "backends": {"probe-options": {"options": OPTIONS}}})
    try:
        _delegate("chat-a", {"goal": "Draft the release notes for the gateway changes"})
        _delegate("chat-b", PICKED, NAMED, built_for="probe-options")
    finally:
        manager.unload()

    on_default, picked, named = recorded
    assert on_default["model"] == "model-c"  # the default backend: default_route answers
    assert picked["model"] == "fast-model"  # the session's backend answers, not default_route
    assert named["model"] == "model-a"  # a task naming its route keeps it


def test_default_route_steps_aside_for_a_configured_backend(tmp_path, monkeypatch, recorded, routes):
    _, manager = _home(tmp_path, monkeypatch, {"backend": "probe-options", "default_route": "chat",
                                               "backends": {"probe-options": {"options": OPTIONS}}})
    try:
        _delegate("chat-a", PICKED, NAMED)
    finally:
        manager.unload()

    picked, named = recorded
    assert (picked["model"], named["model"]) == ("fast-model", "model-a")
