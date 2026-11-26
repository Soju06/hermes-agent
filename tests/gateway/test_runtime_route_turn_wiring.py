"""Turn wiring hands a staged pre-dispatch route to the agent exactly once.

``GatewayAgentCacheMixin._apply_gateway_runtime_override`` stages a DesiredRoute for the
session; ``TurnRunner._wire_turn_agent_callbacks`` consumes it into
``agent._runtime_route_state``, which the API-time Runtime/Route State block reads. The
consume is a tolerant ``getattr`` (bare runner doubles have no hook), so a renamed hook or a
dropped line would put every gateway turn back on ``label=UNCLASSIFIED`` with no error. These
tests drive the wiring against the real mixin so either side of that seam is pinned.
"""

from __future__ import annotations

import types

from gateway.run_agent_cache import GatewayAgentCacheMixin
from gateway.run_turn_runner import TurnRunner

SESSION_KEY = "agent:main:telegram:dm:42"


class _Runner(GatewayAgentCacheMixin):
    """The real route-state staging/consume seam; nothing else of the gateway runner."""

    _service_tier = None

    def __init__(self) -> None:
        self._pending_runtime_route_states = {}

    @staticmethod
    def _consume_pending_turn_sidecar_notes(_session_key):
        return []


def _wire(runner, agent, session_key=SESSION_KEY):
    """Run ``_wire_turn_agent_callbacks`` for one turn of ``session_key``; return the agent."""
    ctx = types.SimpleNamespace(
        progress_callback=None,
        native_tool_start_callback=None,
        voice_ack_callback=None,
        _voice_ack_guild=[None],
        voice_turn=False,
        _native_slack_task_cards=False,
        native_tool_complete_callback=None,
        _step_callback_sync=None,
        _hooks_ref=types.SimpleNamespace(loaded_hooks=[]),
        _status_callback_sync=None,
        _event_callback_sync=None,
        _status_adapter=None,
        session_key=session_key,
        user_config={},
        source=types.SimpleNamespace(platform="telegram"),
        mute_notification_reply=False,
        _thinking_enabled=False,
        agent_holder=[None],
        tools_holder=[None],
        process_task_id=None,
        process_baseline=None,
        run_generation=0,
    )
    holder = types.SimpleNamespace(
        _ctx=ctx,
        _approval_owner=None,
        _runner=runner,
        _make_bg_review_callbacks=lambda: (lambda message: None, lambda: None),
        _merge_turn_request_overrides=TurnRunner._merge_turn_request_overrides,
        _clarify_callback_sync=lambda *a, **k: None,
        _notice_callback_sync=lambda *a, **k: None,
        _attach_session_title_callback=lambda agent, ctx: None,
    )
    TurnRunner._wire_turn_agent_callbacks(holder, agent, {}, None, None, None, False)
    return agent


def _staged_route(runner, session_key=SESSION_KEY):
    state = runner._build_pending_runtime_route_state(  # noqa: SLF001
        {"label": "SYSTEM_DEV", "source": "skill-gate/context-policy-router", "confidence": 0.91},
        target_model="gpt-5.5",
        target_provider="codex-nekos",
        target_reasoning_effort="high",
        reason="Hermes runtime work",
    )
    runner._set_pending_runtime_route_state(session_key, state)  # noqa: SLF001
    return state


def test_staged_route_reaches_the_turn_agent():
    runner = _Runner()
    state = _staged_route(runner)

    agent = _wire(runner, types.SimpleNamespace())

    assert agent._runtime_route_state == state
    assert agent._runtime_route_state["label"] == "SYSTEM_DEV"
    assert runner._consume_pending_runtime_route_state(SESSION_KEY) is None  # noqa: SLF001


def test_reused_agent_drops_the_route_on_the_next_turn():
    """The route is one-shot: a cached agent wired for the following turn carries none."""
    runner = _Runner()
    _staged_route(runner)
    agent = _wire(runner, types.SimpleNamespace())

    _wire(runner, agent)

    assert agent._runtime_route_state is None


def test_route_staged_for_another_session_stays_pending():
    runner = _Runner()
    other_key = "agent:main:telegram:dm:7"
    state = _staged_route(runner, other_key)

    agent = _wire(runner, types.SimpleNamespace())

    assert agent._runtime_route_state is None
    assert runner._consume_pending_runtime_route_state(other_key) == state  # noqa: SLF001
