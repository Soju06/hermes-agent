"""Prompt-tail freeze (patch #18): byte-stable gateway system prompts.

Live HERMES_TURN_TRACE captures showed the composed system prompt re-keying
the provider prompt cache nearly every gateway turn (0.1% cache_read on the
codex/xai routes).  The volatile bytes came from three places:

  1. the per-turn re-render of the "## Current Session Context" ephemeral
     block (thread renames, voice-channel state, one-shot onboarding notes),
  2. the Runtime/Route block (one-shot DesiredRoute flips, the
     reasoning_source presence toggle, the reasoning VALUE flip after a
     gateway restart lost the in-memory half of a router override),
  3. no-op runtime overrides evicting the cached agent.

The fix pins the rendered session-context bytes per session keyed by a hash
of the exact renderer inputs (``_ephemeral_change_key``), relocates
must-deliver per-turn facts onto the current user message (api_content
sidecar), freezes the Runtime/Route block behind a runtime key tuple with a
permanently static DesiredRoute line, and guards the runtime-override
eviction on an effective delta.

The maintained invariant — every rendered input appears in the change key —
is guarded by the parity test below.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gateway.config import GatewayConfig, HomeChannel, Platform, PlatformConfig
from gateway.session import (
    SessionContext,
    SessionSource,
    build_session_context_prompt,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_runner(**attrs):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._session_ephemeral_pin = {}
    runner._session_vc_last = {}
    runner._pending_turn_sidecar_notes = {}
    runner._pending_runtime_route_states = {}
    runner._session_model_overrides = {}
    runner._session_reasoning_overrides = {}
    runner.adapters = {}
    runner.session_store = MagicMock()
    for key, value in attrs.items():
        setattr(runner, key, value)
    return runner


def _make_context(
    *,
    platform: Platform = Platform.DISCORD,
    chat_id: str = "111222333",
    chat_name: str = "general",
    chat_type: str = "channel",
    thread_id: str | None = "444555666",
    parent_chat_id: str | None = "111222333",
    chat_topic: str | None = "ops chatter",
    user_name: str | None = "pix",
    user_id: str | None = "9001",
    guild_id: str | None = "777888999",
    message_id: str | None = "1357",
    shared_multi_user: bool = False,
    connected: list[Platform] | None = None,
    home_channels: dict | None = None,
) -> SessionContext:
    source = SessionSource(
        platform=platform,
        chat_id=chat_id,
        chat_name=chat_name,
        chat_type=chat_type,
        user_id=user_id,
        user_name=user_name,
        thread_id=thread_id,
        chat_topic=chat_topic,
        parent_chat_id=parent_chat_id,
        scope_id=guild_id,
        message_id=message_id,
    )
    connected = connected if connected is not None else [Platform.DISCORD, Platform.TELEGRAM]
    if home_channels is None:
        home_channels = {
            Platform.DISCORD: HomeChannel(
                platform=Platform.DISCORD, chat_id="111222333", name="general"
            ),
        }
    return SessionContext(
        source=source,
        connected_platforms=connected,
        home_channels=home_channels,
        shared_multi_user_session=shared_multi_user,
    )


@pytest.fixture(autouse=True)
def _stable_discord_tools(monkeypatch):
    """Pin the config/env-dependent renderer gate so key<->render parity is
    evaluated on the same footing in every environment."""
    monkeypatch.setattr("gateway.session._discord_tools_loaded", lambda: True)


def _key(runner, context, redact_pii=False):
    return runner._ephemeral_change_key(context, redact_pii)  # noqa: SLF001


def _render(context, redact_pii=False):
    return build_session_context_prompt(context, redact_pii=redact_pii)


# ---------------------------------------------------------------------------
# 1. Parity: key <-> render (the maintained invariant)
# ---------------------------------------------------------------------------

class TestEphemeralChangeKeyParity:
    # Single-field mutations spanning every rendered input.  For each:
    # if the rendered bytes change, the key MUST change (staleness guard).
    _MUTATIONS = [
        ("chat_name", dict(chat_name="renamed-thread")),
        ("chat_topic", dict(chat_topic="new topic")),
        ("chat_topic_cleared", dict(chat_topic=None)),
        ("thread_id", dict(thread_id="000111222")),
        ("thread_cleared", dict(thread_id=None, parent_chat_id=None)),
        ("chat_type", dict(chat_type="group")),
        ("user_name", dict(user_name="somebody-else")),
        ("user_name_cleared", dict(user_name=None)),
        ("user_id", dict(user_name=None, user_id="1234")),
        ("shared_multi_user", dict(shared_multi_user=True)),
        ("guild_id", dict(guild_id="123123123")),
        ("parent_chat_id", dict(parent_chat_id="999000111")),
        ("chat_id", dict(chat_id="999999999", parent_chat_id="999999999")),
        ("platform", dict(platform=Platform.TELEGRAM)),
        ("connected_platforms", dict(connected=[Platform.DISCORD])),
        (
            "home_channel_renamed",
            dict(
                home_channels={
                    Platform.DISCORD: HomeChannel(
                        platform=Platform.DISCORD, chat_id="111222333", name="ops-home"
                    )
                }
            ),
        ),
        (
            "home_channel_added",
            dict(
                home_channels={
                    Platform.DISCORD: HomeChannel(
                        platform=Platform.DISCORD, chat_id="111222333", name="general"
                    ),
                    Platform.TELEGRAM: HomeChannel(
                        platform=Platform.TELEGRAM, chat_id="tg1", name="tg-home"
                    ),
                }
            ),
        ),
        ("message_id_cleared", dict(message_id=None)),
    ]

    @pytest.mark.parametrize("name,mutation", _MUTATIONS)
    def test_render_change_implies_key_change(self, name, mutation):
        runner = _make_runner()
        base = _make_context()
        mutated = _make_context(**mutation)

        render_changed = _render(base) != _render(mutated)
        key_changed = _key(runner, base) != _key(runner, mutated)

        if render_changed:
            assert key_changed, (
                f"mutation {name!r} changed the rendered bytes but not the "
                "change key — the pin would serve STALE context"
            )

    def test_redact_pii_flip_changes_key(self):
        # PII redaction only rewrites bytes on pii-safe platforms; the key
        # must react wherever the render does.
        runner = _make_runner()
        ctx = _make_context(platform=Platform.TELEGRAM, thread_id=None, parent_chat_id=None)
        assert _render(ctx, False) != _render(ctx, True)
        assert _key(runner, ctx, False) != _key(runner, ctx, True)

    def test_discord_tools_gate_flip_changes_key(self, monkeypatch):
        runner = _make_runner()
        ctx = _make_context()
        render_on, key_on = _render(ctx), _key(runner, ctx)
        monkeypatch.setattr("gateway.session._discord_tools_loaded", lambda: False)
        assert _render(ctx) != render_on
        assert _key(runner, ctx) != key_on

    def test_message_id_value_change_is_not_a_bust(self):
        """Only message-id PRESENCE renders (the id itself rides the user
        message) — a new id every turn must not re-render."""
        runner = _make_runner()
        a = _make_context(message_id="1357")
        b = _make_context(message_id="2468")
        assert _render(a) == _render(b)
        assert _key(runner, a) == _key(runner, b)

    def test_key_is_deterministic(self):
        runner = _make_runner()
        ctx = _make_context()
        assert _key(runner, ctx) == _key(runner, ctx)


# ---------------------------------------------------------------------------
# 2. The pin: reuse verbatim on hit, exactly one legit bust on change
# ---------------------------------------------------------------------------

class TestSessionContextPin:
    def test_pin_hit_returns_identical_object(self):
        runner = _make_runner()
        ctx = _make_context()
        first = runner._pinned_session_context_prompt(ctx, False, "sk")  # noqa: SLF001
        second = runner._pinned_session_context_prompt(_make_context(), False, "sk")  # noqa: SLF001
        # Identity, not just equality: the pinned bytes are reused verbatim,
        # immunizing against renderer nondeterminism.
        assert second is first

    def test_auto_thread_rename_busts_exactly_once(self):
        """Turn 1: placeholder title.  Turn 2: gateway auto-rename lands (one
        legit bust — Source line AND origin delivery line move together).
        Turn 3+: byte-stable."""
        runner = _make_runner()
        t1 = runner._pinned_session_context_prompt(  # noqa: SLF001
            _make_context(chat_name="new-chat-1357"), False, "sk"
        )
        t2 = runner._pinned_session_context_prompt(  # noqa: SLF001
            _make_context(chat_name="Fixing the flaky deploy"), False, "sk"
        )
        t3 = runner._pinned_session_context_prompt(  # noqa: SLF001
            _make_context(chat_name="Fixing the flaky deploy"), False, "sk"
        )
        assert t1 != t2
        assert t3 is t2
        assert "Fixing the flaky deploy" in t2

    def test_eviction_drops_pin_and_vc_state(self):
        runner = _make_runner(
            _agent_cache={}, _running_agents={},
        )
        runner._session_ephemeral_pin["sk"] = ("k", "text")
        runner._session_vc_last["sk"] = "vc"
        runner._evict_cached_agent("sk")  # noqa: SLF001
        assert "sk" not in runner._session_ephemeral_pin
        assert "sk" not in runner._session_vc_last

    def test_no_session_key_never_pins(self):
        runner = _make_runner()
        ctx = _make_context()
        out = runner._pinned_session_context_prompt(ctx, False, None)  # noqa: SLF001
        assert out == _render(ctx)
        assert runner._session_ephemeral_pin == {}


# ---------------------------------------------------------------------------
# 3. Two-turn byte test: composed system prompt sha256 + 4KB chunks + pck
# ---------------------------------------------------------------------------

def _compose(context_prompt: str) -> str:
    from agent.system_prompt import compose_effective_system_prompt

    agent = SimpleNamespace(
        model="gpt-5.6-sol",
        provider="codex-nekos",
        base_url="https://codex.nekos.me/v1",
        api_mode="codex_responses",
        reasoning_config={"enabled": True, "effort": "max"},
        ephemeral_system_prompt=context_prompt,
        _runtime_route_state=None,
    )
    return compose_effective_system_prompt(agent, "BASE IDENTITY PROMPT\n" + "x" * 8000)


class TestComposedPromptByteStability:
    def test_turn2_equals_turn3_sha256_and_chunks(self):
        from agent import turn_trace

        runner = _make_runner()
        name = "Fixing the flaky deploy"
        t2 = _compose(
            runner._pinned_session_context_prompt(  # noqa: SLF001
                _make_context(chat_name=name), False, "sk"
            )
        )
        t3 = _compose(
            runner._pinned_session_context_prompt(  # noqa: SLF001
                _make_context(chat_name=name), False, "sk"
            )
        )

        assert hashlib.sha256(t2.encode()).hexdigest() == hashlib.sha256(t3.encode()).hexdigest()

        fp2 = turn_trace.prefix_fingerprint(
            {"messages": [{"role": "system", "content": t2}], "model": "m"}
        )
        fp3 = turn_trace.prefix_fingerprint(
            {"messages": [{"role": "system", "content": t3}], "model": "m"}
        )
        # Zero changed 4KB chunks in the system prompt between turns.
        assert fp2["pfp_chunks"]["0"] == fp3["pfp_chunks"]["0"]
        assert fp2["pfp"][0] == fp3["pfp"][0]

    def test_pck_constant_across_turns(self):
        from agent.transports.codex import _content_cache_key

        runner = _make_runner()
        tools = [{"type": "function", "name": "read_file"}]
        keys = [
            _content_cache_key(
                _compose(
                    runner._pinned_session_context_prompt(  # noqa: SLF001
                        _make_context(), False, "sk"
                    )
                ),
                tools,
            )
            for _ in range(3)
        ]
        assert keys[0] is not None
        assert len(set(keys)) == 1

    def test_routed_turn_does_not_move_the_composed_prompt(self):
        """A one-shot route state on the agent must not change composed
        bytes — the directive is delivered on the user message instead."""
        from agent.system_prompt import compose_effective_system_prompt

        def _agent(route_state):
            return SimpleNamespace(
                model="gpt-5.6-sol",
                provider="codex-nekos",
                base_url="https://codex.nekos.me/v1",
                api_mode="codex_responses",
                reasoning_config={"enabled": True, "effort": "max"},
                ephemeral_system_prompt="CTX",
                _runtime_route_state=route_state,
            )

        routed = compose_effective_system_prompt(
            _agent({"label": "RUNTIME_OVERRIDE", "target_model": "gpt-5.6-sol"}), "BASE"
        )
        unrouted = compose_effective_system_prompt(_agent(None), "BASE")
        assert routed == unrouted


# ---------------------------------------------------------------------------
# 4. Runtime/Route block freeze details
# ---------------------------------------------------------------------------

class TestRuntimeRouteBlockFreeze:
    def _agent(self, **overrides):
        data = dict(
            model="gpt-5.6-sol",
            provider="codex-nekos",
            base_url="https://codex.nekos.me/v1",
            api_mode="codex_responses",
            reasoning_config={"enabled": True, "effort": "max"},
        )
        data.update(overrides)
        return SimpleNamespace(**data)

    def test_reasoning_source_always_present(self):
        from agent.system_prompt import build_runtime_route_block

        with_default = build_runtime_route_block(self._agent(reasoning_config=None))
        with_dict = build_runtime_route_block(self._agent())
        assert "reasoning_source=default" in with_default
        assert "reasoning_source=agent" in with_dict

    def test_same_runtime_tuple_returns_cached_bytes(self):
        from agent.system_prompt import build_runtime_route_block

        agent = self._agent()
        first = build_runtime_route_block(agent)
        second = build_runtime_route_block(agent)
        assert second is first  # tuple compare served the cached text

    def test_runtime_change_rerenders(self):
        from agent.system_prompt import build_runtime_route_block

        agent = self._agent()
        first = build_runtime_route_block(agent)
        agent.model = "gpt-5.5"
        second = build_runtime_route_block(agent)
        assert first != second
        assert "model=gpt-5.5" in second

    def test_reasoning_value_deterministic_for_effective_config(self):
        """The live flip: same effective config must render the same bytes
        whether the agent was rebuilt (no attribution attr) or reused (attr
        cleared at the turn boundary by the gateway)."""
        from agent.system_prompt import build_runtime_route_block

        rebuilt = self._agent()
        reused = self._agent()
        # The gateway clears stale mid-turn attribution each turn; simulate
        # the post-clear state (attribute absent on both).
        assert build_runtime_route_block(rebuilt) == build_runtime_route_block(reused)


# ---------------------------------------------------------------------------
# 5. Runtime override: effective-delta eviction guard
# ---------------------------------------------------------------------------

def _override_runner(monkeypatch, *, current_model, switch_result, config_model=None):
    runner = _make_runner()
    runner._session_key_for_source = lambda source: "sess-key"
    evictions = []
    runner._evict_cached_agent = lambda key: evictions.append(key)
    persisted = []
    runner._persist_session_runtime_override = (
        lambda session_key, **kw: persisted.append((session_key, kw))
    )

    monkeypatch.setattr(
        "gateway.run._load_gateway_config",
        lambda: {
            "model": {
                "default": config_model or current_model,
                "provider": "codex-nekos",
                "base_url": "https://codex.nekos.me/v1",
            }
        },
    )
    monkeypatch.setattr(
        "hermes_cli.model_switch.switch_model", lambda **kw: switch_result
    )
    monkeypatch.setattr(
        "hermes_cli.config.get_compatible_custom_providers", lambda cfg: {}
    )
    return runner, evictions, persisted


def _switch_result(model="gpt-5.6-sol"):
    return SimpleNamespace(
        success=True,
        new_model=model,
        target_provider="codex-nekos",
        api_key="k",
        base_url="https://codex.nekos.me/v1",
        api_mode="codex_responses",
        provider_label="codex-nekos",
        error_message="",
    )


def _source():
    return SessionSource(
        platform=Platform.DISCORD, chat_id="c1", chat_type="channel", user_id="u1"
    )


class TestRuntimeOverrideEvictionGuard:
    def test_same_route_override_does_not_evict(self, monkeypatch):
        """The codex-lb router re-selecting the already-active route every
        PR-comment turn must not rebuild the agent (rebuild=False path)."""
        runner, evictions, _ = _override_runner(
            monkeypatch,
            current_model="gpt-5.6-sol",
            switch_result=_switch_result("gpt-5.6-sol"),
        )
        # Prior override == switch target: no effective delta.
        runner._session_model_overrides["sess-key"] = {
            "model": "gpt-5.6-sol",
            "provider": "codex-nekos",
            "api_key": "k",
            "base_url": "https://codex.nekos.me/v1",
            "api_mode": "codex_responses",
        }

        changed = runner._apply_gateway_runtime_override(  # noqa: SLF001
            {"model": "gpt-5.6-sol", "provider": "codex-nekos", "reason": "codex-lb route"},
            _source(),
        )

        assert changed is True  # override applied (directive still delivered)
        assert evictions == []  # ... but the cached agent survives
        # The route directive is still staged for user-message delivery.
        assert runner._pending_runtime_route_states.get("sess-key")
        # No "X -> X" switch note for a no-op route.
        assert not getattr(runner, "_pending_model_notes", {}).get("sess-key")

    def test_new_model_override_evicts_once(self, monkeypatch):
        runner, evictions, _ = _override_runner(
            monkeypatch,
            current_model="claude-4.6-sonnet",
            switch_result=_switch_result("gpt-5.6-sol"),
        )

        changed = runner._apply_gateway_runtime_override(  # noqa: SLF001
            {"model": "gpt-5.6-sol", "provider": "codex-nekos", "reason": "review route"},
            _source(),
        )

        assert changed is True
        assert evictions == ["sess-key"]
        assert "gpt-5.6-sol" in getattr(runner, "_pending_model_notes", {}).get("sess-key", "")

    def test_reasoning_only_override_never_evicts_and_persists(self, monkeypatch):
        """Reasoning is applied per-turn on the cached agent (excluded from
        the cache signature) — and BOTH halves must persist so a gateway
        restart cannot flip CurrentRuntime reasoning=max -> reasoning=unknown
        for the same effective route (the live-measured value flip)."""
        runner, evictions, persisted = _override_runner(
            monkeypatch,
            current_model="gpt-5.6-sol",
            switch_result=_switch_result("gpt-5.6-sol"),
        )
        runner._get_session_entry = lambda key: None

        changed = runner._apply_gateway_runtime_override(  # noqa: SLF001
            {"reasoning_effort": "max", "reason": "review route"},
            _source(),
        )

        assert changed is True
        assert evictions == []
        assert runner._session_reasoning_overrides["sess-key"] == {
            "enabled": True,
            "effort": "max",
        }
        assert any(
            kw.get("include_reasoning") and kw.get("reasoning_config") == {"enabled": True, "effort": "max"}
            for _k, kw in persisted
        )


# ---------------------------------------------------------------------------
# 6. Voice-channel sidecar note: only-when-changed
# ---------------------------------------------------------------------------

class _VcAdapter:
    def __init__(self, value):
        self.value = value

    def get_voice_channel_context(self, guild_id):
        return self.value


def _vc_runner(vc_value):
    adapter = _VcAdapter(vc_value)
    runner = _make_runner(adapters={Platform.DISCORD: adapter})
    return runner, adapter


def _vc_event():
    return SimpleNamespace(raw_message=SimpleNamespace(guild_id="777"))


class TestVoiceChannelSidecarNote:
    def test_first_sighting_injects(self):
        runner, _ = _vc_runner("**Voice:** dev-vc (2 members)")
        note = runner._voice_channel_sidecar_note(_vc_event(), _source(), "sk")  # noqa: SLF001
        assert note == "[Voice channel now: **Voice:** dev-vc (2 members)]"

    def test_unchanged_state_injects_nothing(self):
        runner, _ = _vc_runner("**Voice:** dev-vc (2 members)")
        assert runner._voice_channel_sidecar_note(_vc_event(), _source(), "sk")  # noqa: SLF001
        assert runner._voice_channel_sidecar_note(_vc_event(), _source(), "sk") is None  # noqa: SLF001

    def test_member_change_injects_again(self):
        runner, adapter = _vc_runner("**Voice:** dev-vc (2 members)")
        runner._voice_channel_sidecar_note(_vc_event(), _source(), "sk")  # noqa: SLF001
        adapter.value = "**Voice:** dev-vc (3 members)"
        note = runner._voice_channel_sidecar_note(_vc_event(), _source(), "sk")  # noqa: SLF001
        assert note == "[Voice channel now: **Voice:** dev-vc (3 members)]"

    def test_leaving_channel_injects_disconnect_note(self):
        runner, adapter = _vc_runner("**Voice:** dev-vc (2 members)")
        runner._voice_channel_sidecar_note(_vc_event(), _source(), "sk")  # noqa: SLF001
        adapter.value = ""
        note = runner._voice_channel_sidecar_note(_vc_event(), _source(), "sk")  # noqa: SLF001
        assert note == "[Voice channel now: not connected to a voice channel]"

    def test_never_in_channel_injects_nothing(self):
        runner, _ = _vc_runner("")
        assert runner._voice_channel_sidecar_note(_vc_event(), _source(), "sk") is None  # noqa: SLF001

    def test_non_discord_platform_is_noop(self):
        runner, _ = _vc_runner("**Voice:** dev-vc")
        src = SessionSource(platform=Platform.TELEGRAM, chat_id="c", user_id="u")
        assert runner._voice_channel_sidecar_note(_vc_event(), src, "sk") is None  # noqa: SLF001


# ---------------------------------------------------------------------------
# 7. Sidecar note staging: one-shot per turn
# ---------------------------------------------------------------------------

class TestSidecarNoteStaging:
    def test_set_then_consume_once(self):
        runner = _make_runner()
        runner._set_pending_turn_sidecar_notes("sk", ["[System note: reset]"])  # noqa: SLF001
        assert runner._consume_pending_turn_sidecar_notes("sk") == ["[System note: reset]"]  # noqa: SLF001
        assert runner._consume_pending_turn_sidecar_notes("sk") == []  # noqa: SLF001

    def test_empty_inputs_are_noops(self):
        runner = _make_runner()
        runner._set_pending_turn_sidecar_notes("", ["x"])  # noqa: SLF001
        runner._set_pending_turn_sidecar_notes("sk", [])  # noqa: SLF001
        assert runner._consume_pending_turn_sidecar_notes("sk") == []  # noqa: SLF001
        assert runner._consume_pending_turn_sidecar_notes("") == []  # noqa: SLF001


# ---------------------------------------------------------------------------
# 8. Connected platforms: stable order
# ---------------------------------------------------------------------------

class TestConnectedPlatformsOrder:
    def test_sorted_regardless_of_insertion_order(self):
        cfg_a = GatewayConfig(
            platforms={
                Platform.TELEGRAM: PlatformConfig(enabled=True, token="t"),
                Platform.DISCORD: PlatformConfig(enabled=True, token="d"),
            }
        )
        cfg_b = GatewayConfig(
            platforms={
                Platform.DISCORD: PlatformConfig(enabled=True, token="d"),
                Platform.TELEGRAM: PlatformConfig(enabled=True, token="t"),
            }
        )
        assert cfg_a.get_connected_platforms() == cfg_b.get_connected_platforms()
        values = [p.value for p in cfg_a.get_connected_platforms()]
        assert values == sorted(values)


# ---------------------------------------------------------------------------
# 9. CLI regression: composition without gateway state is static and stable
# ---------------------------------------------------------------------------

class TestCliComposition:
    def test_cli_composed_prompt_stable_across_calls(self):
        """CLI agents (no ephemeral prompt, no route state) compose the same
        bytes on every call — the freeze machinery adds no per-call
        variability to non-gateway paths."""
        from agent.system_prompt import compose_effective_system_prompt

        agent = SimpleNamespace(
            model="gpt-5.6-sol",
            provider="codex-nekos",
            base_url="https://codex.nekos.me/v1",
            api_mode="codex_responses",
            reasoning_config=None,
            ephemeral_system_prompt=None,
        )
        first = compose_effective_system_prompt(agent, "BASE")
        second = compose_effective_system_prompt(agent, "BASE")
        assert first == second
        assert first.startswith("BASE\n\n# Runtime/Route State")
        assert "DesiredRoute: label=UNCLASSIFIED target=current" in first
        assert "reasoning_source=default" in first
