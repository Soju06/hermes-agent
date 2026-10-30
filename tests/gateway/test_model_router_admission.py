"""The dynamic model router on upstream's durable admission path (2026-10-30 sync review).

Every gateway boot installs a session authority, so an ordinary message is admitted to the durable
FIFO at ingress and executed by the drain (``gateway/session_ingress.py::execute_admission``) on an
event restored from the committed row, re-entering ``_handle_message``. Local CLI/TUI/Desktop turns
take the same drain. Real GatewayRunner, adapters, SessionAuthority and drain over a real SessionDB;
only the classifier, the model switch and the agent leg are stand-ins.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.waiting import async_wait_until

ADVISORY = "[Learning reminder: consider the deploy skill]"
MESSAGE = "gateway 버그 고쳐줘"


def _classifier(seen):
    """Stand-in for ``gateway.model_router.classifier_decision``: records the text it classified and
    answers a switch to the ``dev`` route."""
    def classify(*, event, **_kwargs):
        seen.append(event.text)
        return SimpleNamespace(directive={"route": "dev", "model": "model-a", "provider": "p1"},
                               outcome="switch", record={}, label="SYSTEM_DEV")
    return classify


def _router(monkeypatch, tmp_path, mode):
    import gateway.model_router as mr_mod
    from hermes_cli.model_switch import ModelSwitchResult

    monkeypatch.setenv("HERMES_MODEL_ROUTER_MODE", mode)
    monkeypatch.setenv("HERMES_MODEL_ROUTER_DECISION_LOG", str(tmp_path / "decisions.jsonl"))
    classified = []
    monkeypatch.setattr(mr_mod, "classifier_decision", _classifier(classified))
    switch = MagicMock(return_value=ModelSwitchResult(
        success=True, new_model="model-a", target_provider="p1", base_url="https://p1.example/v1"))
    monkeypatch.setattr("hermes_cli.model_switch.switch_model", switch)
    return classified, switch


@pytest.mark.asyncio
async def test_an_admitted_message_is_classified_on_its_pre_hook_text(tmp_path, monkeypatch):
    """A ``pre_gateway_dispatch`` prepend runs once, at ingress, and the post-hook text is what the
    FIFO commits; the hook never runs again when the drain executes the row. The router (running at
    execution) must still classify the message the user sent, never the advisory (4293ca31fa). Red
    before the fix: the classifier saw the prepended text. An unhooked message keeps upstream's exact
    envelope, so its retry fingerprint is unchanged."""
    from gateway.config import GatewayConfig, Platform, PlatformConfig
    from gateway.platforms.base import SendResult
    from gateway.platforms.event import MessageEvent
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority
    from gateway.session_envelope import restore_native
    from hermes_state_runtime import list_session_admissions
    from plugins.platforms.discord.adapter import DiscordAdapter

    monkeypatch.setenv("DISCORD_ALLOWED_USERS", "42")
    classified, _switch = _router(monkeypatch, tmp_path, "shadow")

    async def hook(name, **kwargs):
        if name == "pre_gateway_dispatch" and "버그" in kwargs["event"].text:
            return [{"action": "prepend", "text": ADVISORY}]
        return []
    monkeypatch.setattr("hermes_cli.lifecycle.ainvoke_hook", hook)

    runner = GatewayRunner(GatewayConfig())
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="fixture-token", typing_indicator=False))

    async def send(chat_id, content, reply_to=None, metadata=None):
        return SendResult(success=True, message_id="sent")
    monkeypatch.setattr(adapter, "send", send)
    runner.adapters = {Platform.DISCORD: adapter}
    authority = await initialize_session_authority(runner, profile_id="default", instance_id="owner")
    runner._wire_adapter_handlers(adapter)
    agent_saw = []

    async def turn(event, source, quick_key, run_generation):
        agent_saw.append(event.text)
        return "ok"
    monkeypatch.setattr(runner, "_handle_message_with_agent", turn)
    source = adapter.build_source(chat_id="42", chat_type="dm", user_id="42")

    await adapter.handle_message(MessageEvent(text=MESSAGE, source=source, message_id="hooked"))
    await async_wait_until(lambda: len(agent_saw) == 1, timeout=10, what="the hooked turn")
    await adapter.handle_message(MessageEvent(text="plain hello", source=source, message_id="plain"))
    await async_wait_until(lambda: len(agent_saw) == 2, timeout=10, what="the plain turn")

    assert agent_saw == [f"{ADVISORY}\n\n{MESSAGE}", "plain hello"]  # the prepend reached the agent
    assert classified == [MESSAGE, "plain hello"]
    owner = runner.session_store.peek_session_id(runner.session_store._generate_session_key(source))
    rows = {row["request_id"]: row for row in list_session_admissions(authority.db, session_id=owner,
                                                                          pending_only=False)}
    # Committed with the row, so a replay after an owner restart still classifies the sent text.
    assert restore_native(rows["hooked"]["payload"], runner)._hermes_router_pre_hook_text == MESSAGE
    assert "router_pre_hook_text" not in rows["plain"]["payload"]["native_text_v1"]


@pytest.mark.asyncio
async def test_a_hooked_webhook_delivery_is_classified_as_received(tmp_path, monkeypatch):
    """Receipt-only producer ingress (webhook/relay ``admit_producer``) runs the hook before its commit
    and never at execution, like the messaging path: the router still classifies the delivery's own
    text. Red before the fix: the classifier saw the prepended advisory."""
    from aiohttp.test_utils import TestClient, TestServer

    from hermes_cli.plugins import get_plugin_manager
    from tests.gateway.test_pre_gateway_dispatch import _run_webhook_delivery, _webhook_app

    classified, _switch = _router(monkeypatch, tmp_path, "shadow")
    manager = get_plugin_manager()
    manager._discovered = True
    manager._hooks.setdefault("pre_gateway_dispatch", []).append(
        lambda event, **_kwargs: {"action": "prepend", "text": ADVISORY})
    adapter, app = _webhook_app()
    agent_saw = []

    async def turn(event, source, quick_key, run_generation):
        agent_saw.append(event.text)
        return "agent-ok"

    async with TestClient(TestServer(app)) as client:
        await _run_webhook_delivery(client, adapter, MESSAGE, "d-hooked", capture=turn)

    assert agent_saw == [f"{ADVISORY}\n\n{MESSAGE}"]
    assert classified == [MESSAGE]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["shadow", "enforce"])
async def test_a_local_session_turn_is_never_routed(tmp_path, monkeypatch, mode):
    """A LOCAL session (CLI, TUI, Desktop served by the gateway) runs its frozen launch policy:
    ``_resolve_session_agent_runtime`` answers from it before any messaging override is read
    (gateway/session_runtime_switch.py). An enforce apply into the messaging override slot would be
    a no-op logged ``applied: true`` with a false "model was just switched" note and an agent
    eviction on every routed turn, and shadow would pay a classifier call per local turn. Red before
    the fix: the drained local turn was classified and, in enforce, "applied"."""
    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority
    from gateway.session_contract import Principal, Submission
    from gateway.session_local import create_local_session
    from gateway.session_policy import policy_for_source
    from hermes_state import SessionDB

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    classified, switch = _router(monkeypatch, tmp_path, mode)
    runner = GatewayRunner(GatewayConfig(sessions_dir=tmp_path / "sessions"))
    db = SessionDB(tmp_path / "state.db")
    runner.session_store._db = runner._session_db = db
    authority = await initialize_session_authority(runner, profile_id="default", instance_id="owner", db=db)
    ran = []

    async def turn(event, source, quick_key, run_generation):
        ran.append(event.text)
        return "ok"
    monkeypatch.setattr(runner, "_handle_message_with_agent", turn)
    try:
        viewer = Principal("test-owner", "default", frozenset(
            {"session:create", "session:submit", "session:read"}), "viewer")
        ref = create_local_session(authority, viewer, dict(request_id="cli", source="cli", model="fixture",
                                                          toolsets=[]))
        live = authority.sessions[ref.session_id]
        assert policy_for_source(runner, live.source) is not None  # its runtime is the frozen policy's
        await authority.attach(viewer, ref)
        await authority.submit(viewer, Submission("typed", ref, {"text": MESSAGE}, "queue"))
        await async_wait_until(lambda: ran, timeout=10, what="the local turn")
    finally:
        db.close()

    assert ran == [MESSAGE]
    assert classified == []
    switch.assert_not_called()
    assert runner._session_model_overrides.get(live.route) is None
    assert live.route not in (getattr(runner, "_pending_model_notes", None) or {})
