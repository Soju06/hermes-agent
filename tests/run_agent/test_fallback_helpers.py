"""Behavior tests for route-aware failure-driven fallback ordering."""

from unittest.mock import MagicMock, patch

from agent.chat_completion_helpers import _build_outage_route_fallback_chain
from agent.error_classifier import FailoverReason
from run_agent import AIAgent


def _make_agent(*, fallback_model, model="claude-fable-5"):
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key",
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            fallback_model=fallback_model,
        )
    agent.client = MagicMock()
    agent.provider = "openrouter"
    agent.model = model
    agent.base_url = "https://openrouter.ai/api/v1"
    agent._primary_runtime = {
        "provider": agent.provider,
        "model": agent.model,
        "base_url": agent.base_url,
    }
    return agent


def _mock_client(provider):
    client = MagicMock()
    client.base_url = f"https://{provider}.example/v1"
    client.api_key = "fallback-key"
    return client


def _routes_config(*, accepted=("claude-fable-5",), fallbacks=None):
    if fallbacks is None:
        fallbacks = [{"provider": "openai", "model": "gpt-5.6"}]
    providers = {
        "openrouter": {
            "base_url": "https://openrouter.ai/api/v1",
            "models": {"anthropic/claude-fable-5": {}},
        },
    }
    for fallback in fallbacks:
        providers[fallback["provider"]] = {
            "base_url": f"https://{fallback['provider']}.example/v1",
            "models": {fallback["model"]: {}},
        }
    return {
        "providers": providers,
        "model_routes": {
            "health": {"enabled": False},
            "routes": {
                "DEV": {
                    "description": "development runtime",
                    "provider": "openrouter",
                    "model": "anthropic/claude-fable-5",
                    "accepted": list(accepted),
                    "fallbacks": fallbacks,
                }
            },
        },
    }


def _ambiguous_routes_config():
    return {
        "providers": {
            "claude-lb": {
                "base_url": "https://claude-lb.example/v1",
                "models": {
                    "claude-opus-5": {},
                    "claude-fable-5": {},
                },
            },
            "codex-nekos": {
                "base_url": "https://codex-nekos.example/v1",
                "models": {"gpt-5.6-sol": {}},
            },
            "glm-vooy": {
                "base_url": "https://glm-vooy.example/v1",
                "models": {"kimi-k3": {}, "grok-4.5": {}},
            },
        },
        "model_routes": {
            "health": {"enabled": False},
            "routes": {
                "DOCUMENT_WORK": {
                    "description": "document work",
                    "provider": "claude-lb",
                    "model": "claude-opus-5",
                    "accepted": ["claude-opus-5", "claude-fable-5"],
                    "fallbacks": [
                        {"provider": "claude-lb", "model": "claude-fable-5"},
                    ],
                },
                "SYSTEM_DEV": {
                    "description": "system development",
                    "provider": "claude-lb",
                    "model": "claude-fable-5",
                    "accepted": [
                        "claude-fable-5",
                        "gpt-5.6-sol",
                        "kimi-k3",
                        "grok-4.5",
                    ],
                    "fallbacks": [
                        {"provider": "codex-nekos", "model": "gpt-5.6-sol"},
                        {"provider": "glm-vooy", "model": "kimi-k3"},
                        {"provider": "glm-vooy", "model": "grok-4.5"},
                    ],
                },
            },
        },
    }


def _route_fallback_pairs(agent):
    with patch(
        "hermes_cli.config.load_config", return_value=_ambiguous_routes_config()
    ):
        route_name, chain = _build_outage_route_fallback_chain(agent)
    return route_name, [(entry["provider"], entry["model"]) for entry in chain]


def test_outage_fallback_prefers_recorded_route_under_ambiguous_membership():
    agent = _make_agent(fallback_model=[])
    agent.provider = "claude-lb"
    agent.model = "claude-fable-5"
    agent.base_url = "https://claude-lb.example/v1"
    agent._active_route_name = "SYSTEM_DEV"

    assert _route_fallback_pairs(agent) == (
        "SYSTEM_DEV",
        [
            ("codex-nekos", "gpt-5.6-sol"),
            ("glm-vooy", "kimi-k3"),
            ("glm-vooy", "grok-4.5"),
        ],
    )


def test_outage_fallback_without_recorded_route_keeps_first_match_behavior():
    agent = _make_agent(fallback_model=[])
    agent.provider = "claude-lb"
    agent.model = "claude-fable-5"
    agent.base_url = "https://claude-lb.example/v1"

    assert _route_fallback_pairs(agent) == ("DOCUMENT_WORK", [])


def test_outage_fallback_ignores_recorded_route_when_runtime_no_longer_satisfies_it():
    agent = _make_agent(fallback_model=[], model="claude-opus-5")
    agent.provider = "claude-lb"
    agent.model = "claude-opus-5"
    agent.base_url = "https://claude-lb.example/v1"
    agent._active_route_name = "SYSTEM_DEV"

    assert _route_fallback_pairs(agent) == (
        "DOCUMENT_WORK",
        [("claude-lb", "claude-fable-5")],
    )


def _activate(agent, reason, cfg, resolver):
    with (
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("agent.chat_completion_helpers._record_passive_provider_outcome"),
        patch(
            "hermes_cli.model_normalize.normalize_model_for_provider",
            side_effect=lambda model, provider: model,
        ),
        patch(
            "agent.model_metadata.get_model_context_length",
            return_value=256_000,
        ),
        patch(
            "agent.auxiliary_client.resolve_provider_client",
            side_effect=resolver,
        ),
    ):
        return agent._try_activate_fallback(reason=reason)


def test_rate_limit_prefers_current_route_fallback_before_global_chain():
    global_fallback = {"provider": "zai", "model": "global-chat"}
    agent = _make_agent(fallback_model=[global_fallback])
    calls = []

    def resolve(provider, model=None, **kwargs):
        calls.append((provider, model))
        return _mock_client(provider), model

    assert (
        _activate(
            agent,
            FailoverReason.rate_limit,
            _routes_config(),
            resolve,
        )
        is True
    )

    assert calls == [("openai", "gpt-5.6")]
    assert (agent.provider, agent.model) == ("openai", "gpt-5.6")
    assert agent._fallback_index == 0


def test_unmatched_runtime_uses_global_chain_without_reordering():
    global_fallback = {"provider": "zai", "model": "global-chat"}
    agent = _make_agent(fallback_model=[global_fallback])
    calls = []

    def resolve(provider, model=None, **kwargs):
        calls.append((provider, model))
        return _mock_client(provider), model

    assert (
        _activate(
            agent,
            FailoverReason.rate_limit,
            _routes_config(accepted=("different-model",)),
            resolve,
        )
        is True
    )

    assert calls == [("zai", "global-chat")]
    assert agent._fallback_index == 1


def test_unhealthy_and_unresolvable_route_fallbacks_reach_global_chain():
    global_fallback = {"provider": "zai", "model": "global-chat"}
    route_fallbacks = [
        {"provider": "openai", "model": "route-unhealthy"},
        {"provider": "anthropic", "model": "route-unresolvable"},
    ]
    agent = _make_agent(fallback_model=[global_fallback])
    calls = []

    def health(provider, model, **kwargs):
        if model in {"anthropic/claude-fable-5", "route-unhealthy"}:
            return False, "quota exhausted"
        return True, "healthy"

    def resolve(provider, model=None, **kwargs):
        calls.append((provider, model))
        if model == "route-unresolvable":
            return None, None
        return _mock_client(provider), model

    with patch("hermes_cli.model_routes.provider_health", side_effect=health):
        assert (
            _activate(
                agent,
                FailoverReason.server_error,
                _routes_config(fallbacks=route_fallbacks),
                resolve,
            )
            is True
        )

    assert calls == [
        ("anthropic", "route-unresolvable"),
        ("zai", "global-chat"),
    ]
    assert (agent.provider, agent.model) == ("zai", "global-chat")
    assert agent._fallback_index == 1


def test_content_policy_still_uses_global_chain_before_refusal_tail():
    global_fallback = {"provider": "zai", "model": "global-chat"}
    agent = _make_agent(fallback_model=[global_fallback])
    calls = []

    def resolve(provider, model=None, **kwargs):
        calls.append((provider, model))
        return _mock_client(provider), model

    assert (
        _activate(
            agent,
            FailoverReason.content_policy_blocked,
            _routes_config(),
            resolve,
        )
        is True
    )

    assert calls == [("zai", "global-chat")]
    assert agent._fallback_index == 1
