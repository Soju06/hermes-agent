"""Gateway per-turn reasoning must outrank a cached primary snapshot."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from gateway.config import Platform
from gateway.run import GatewayRunner, TurnRunner
from gateway.session import SessionSource
from gateway.turn_context import TurnContext


class _Compressor:
    last_prompt_tokens = 0
    context_length = 200_000

    def update_model(self, **kwargs):
        return None


class _CachedFallbackAgent:
    """Cached agent left in fallback-active state by the preceding turn."""

    def __init__(self):
        self.model = "fallback-model"
        self.provider = "fallback-provider"
        self.requested_provider = "fallback-provider"
        self.base_url = "https://fallback.example/v1"
        self.api_mode = "chat_completions"
        self.api_key = "fallback-key"
        self.client = MagicMock()
        self._client_kwargs = {}
        self._use_prompt_caching = False
        self._use_native_cache_layout = False
        self._transport_cache = {}
        self._fallback_activated = True
        self._fallback_index = 1
        self._fallback_chain = []
        self._rate_limited_until = 0
        self._rate_limit_backoff_count = 0
        self._credential_pool = None
        self._credential_pool_entry_id = None
        self._cached_system_prompt = None
        self._primary_runtime = {
            "model": "primary-model",
            "provider": "primary-provider",
            "requested_provider": "primary-provider",
            "base_url": "https://primary.example/v1",
            "api_mode": "chat_completions",
            "api_key": "primary-key",
            "client_kwargs": {},
            "use_prompt_caching": False,
            "use_native_cache_layout": False,
            # Deliberately stale from an older switch_model call.
            "reasoning_config": {"enabled": True, "effort": "low"},
            "compressor_model": "primary-model",
            "compressor_context_length": 200_000,
            "compressor_base_url": "https://primary.example/v1",
            "compressor_api_key": "primary-key",
            "compressor_provider": "primary-provider",
            "compressor_api_mode": "chat_completions",
        }
        self.reasoning_config = {"enabled": True, "effort": "low"}
        self.context_compressor = _Compressor()
        self.session_id = "sid"
        self.tools = []
        self._session_messages = []
        self.observed_reasoning = None

    def _create_openai_client(self, *args, **kwargs):
        return MagicMock()

    def run_conversation(self, *args, **kwargs):
        from agent.agent_runtime_helpers import restore_primary_runtime

        with patch("agent.credential_pool.load_pool", return_value=None):
            assert restore_primary_runtime(self) is True
        # This is the value the real conversation loop uses to build provider
        # kwargs immediately after its restore-primary prologue.
        self.observed_reasoning = self.reasoning_config
        return {"final_response": "ok", "messages": [], "api_calls": 1}


def _runner_with_cached_agent(agent):
    runner = object.__new__(GatewayRunner)
    runner.config = SimpleNamespace(
        streaming=SimpleNamespace(enabled=False, transport="off")
    )
    runner.adapters = {}
    runner.session_store = SimpleNamespace(_entries={})
    runner._session_db = None
    runner._provider_routing = {}
    runner._prefill_messages = []
    runner._ephemeral_system_prompt = ""
    runner._service_tier = None
    runner._reasoning_config = None
    runner._agent_cache = {"session-key": (agent, "sig", None, "sid")}
    runner._agent_cache_lock = threading.Lock()
    runner._sessions = {}
    runner._resolve_session_agent_runtime = lambda **kwargs: (
        "primary-model",
        {
            "provider": "primary-provider",
            "api_key": "primary-key",
            "base_url": "https://primary.example/v1",
            "api_mode": "chat_completions",
        },
    )
    runner._resolve_session_reasoning_config = lambda **kwargs: {
        "enabled": True,
        "effort": "xhigh",
    }
    runner._refresh_fallback_model = lambda: None
    runner._apply_fallback_chain_to_agent = lambda *args, **kwargs: None
    runner._init_cached_agent_for_turn = lambda *args, **kwargs: None
    runner._resolve_session_service_tier = lambda **kwargs: None
    runner._get_system_prompt_for_channel = lambda *args, **kwargs: ""
    runner._resolve_turn_agent_config = lambda message, model, runtime: {
        "model": model,
        "runtime": runtime,
        "request_overrides": {},
    }
    runner._agent_config_signature = lambda *args, **kwargs: "sig"
    runner._extract_cache_busting_config = lambda cfg: {}
    runner._consume_pending_turn_sidecar_notes = lambda key: []
    runner._adapter_for_source = lambda source: None
    runner._is_discord_auto_thread_lane = lambda source: False
    runner._is_relay_discord_channel_lane = lambda source: False
    runner._is_telegram_topic_lane = lambda source: False
    runner._thread_metadata_for_source = lambda *args, **kwargs: {}
    runner._thread_metadata_for_target = lambda *args, **kwargs: {}
    return runner


def test_cached_prior_fallback_cannot_restore_stale_reasoning_over_turn_value():
    agent = _CachedFallbackAgent()
    runner = _runner_with_cached_agent(agent)
    source = SessionSource(
        platform=Platform.DISCORD,
        user_id="u",
        chat_id="c",
        chat_type="dm",
        thread_id="t",
    )
    ctx = TurnContext(
        source=source,
        message="hello",
        history=[],
        context_prompt="",
        session_id="sid",
        session_key="session-key",
        user_config={},
        enabled_toolsets=[],
        disabled_toolsets=None,
        AIAgent=MagicMock,
        resolve_display_setting=lambda *args, **kwargs: None,
        streaming_tts_consumer_holder=[None],
        _run_still_current=lambda: True,
        _hooks_ref=SimpleNamespace(loaded_hooks=[]),
        _status_callback_sync=lambda *args, **kwargs: None,
        _event_callback_sync=lambda *args, **kwargs: None,
    )
    ctx._status_adapter = None
    ctx._status_chat_id = "c"
    ctx._status_thread_metadata = {}
    ctx._step_callback_sync = lambda *args, **kwargs: None
    ctx._native_slack_task_cards = False
    ctx._voice_ack_guild = [None]
    ctx.progress_callback = None
    ctx.voice_ack_callback = None
    ctx.native_tool_start_callback = None
    ctx.native_tool_complete_callback = None

    result = TurnRunner(runner, ctx).run_sync()

    assert result["final_response"] == "ok"
    assert agent.observed_reasoning == {"enabled": True, "effort": "xhigh"}

