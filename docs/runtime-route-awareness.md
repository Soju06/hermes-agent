# Runtime route awareness

## Problem

Hermes currently has two separate truths that can drift:

1. The live runtime that will receive the next LLM call
2. The route that policy intended for the current turn

A user-message-only router is not enough. Many requests are ambiguous until the agent reads memory, files, session history, web results, or GitHub state. A route selected before context discovery can be wrong, and a route that escalates to a development model can also become stale when the next turn is ordinary chat. The agent must not infer its current model from an old session header or from memory.

`model_status` is still useful as a diagnostic tool, but it should not be the normal way for the model to discover which runtime is active. Runtime truth belongs in the system prompt for every LLM call.

## Principles

### CurrentRuntime and DesiredRoute are different contracts

`CurrentRuntime` is what this LLM call is actually using after provider resolution, session overrides, fallback activation, and reasoning-effort resolution.

`DesiredRoute` is what policy intended for this turn or call. It can be absent, automatic, user-strict, fallback-derived, or agent-requested.

The prompt must expose both, otherwise the agent cannot tell whether it is correctly routed or merely aware of a model name.

### Prompt-time truth beats cached/session truth

Runtime awareness must be built immediately before API dispatch, not when the cached session system prompt is built. The stable cached prompt can remain byte-stable, but the volatile runtime block must be appended at call time so fallback/model-switch/reasoning changes are visible without relying on stale headers.

### Routing is bidirectional

The router must support both escalation and downgrade:

- ordinary chat to development/reasoning model
- development model back to ordinary/research/writing route
- research to code and code back to research
- fallback target back to primary when recoverable

A route is not inherently safer because it uses a stronger model. Stale escalation is still a mismatch.

### User-strict overrides win

If the user explicitly chooses a model/provider/reasoning level, automatic routing must not override it. The route metadata must carry source and strictness so later layers can distinguish user intent from auto-policy guesses.

### Context discovery can change routing evidence

Phase 1 and 2 do not implement full post-tool rerouting, but the data model must leave room for it. The current implementation should avoid hard-coded ambiguous verb heuristics such as “분석해줘” or “확인해줘” because those collapse too many edge cases into a simplistic classifier. Likewise, Phase 1 and 2 should not add a separate NEED_CONTEXT scout mode yet.

## Prompt contract

The runtime block is appended to the effective system message at API-call time:

```text
# Runtime/Route State
CurrentRuntime: provider={provider_alias_or_type} model={model} reasoning={reasoning_effort} api={api_mode} endpoint={sanitized_base_url} source={runtime_source}
DesiredRoute: label={route_label} target={target_provider}/{target_model}/{target_reasoning} strictness={strictness} confidence={confidence} source={route_source} reason="{short_reason}"
Policy: This block is authoritative for this LLM call; do not infer current runtime from stale session headers, memory, or prior turns. model_status is diagnostic fallback only. Compare CurrentRuntime and DesiredRoute; if mismatched and not user_strict, treat as a routing anomaly before substantive work. Routing is bidirectional and may be re-evaluated after context discovery.
```

If no router supplied a route for this turn, `DesiredRoute` is explicit about that:

```text
DesiredRoute: label=UNCLASSIFIED target=current strictness=none confidence=unknown reason="no current-turn route decision supplied"
```

That makes the absence of a policy route visible without implying a mismatch.

## Phase 1 scope: CurrentRuntime block

Goal: make the active model/provider/reasoning visible in every LLM call without requiring `model_status`.

Included:

- Build a runtime block at API-call time, not cached prompt-build time
- Use the same sanitized state source as `model_status`
- Include model, provider, api mode, reasoning effort, and runtime source
- Keep secrets out of the block
- Apply to the main conversation loop and summary/compression call path
- Preserve cached stable prompt reuse
- Add tests that prove the runtime block is present and changes when the live agent runtime changes

Not included:

- learned routing
- post-tool rerouting
- mutating-tool boundary guard
- NEED_CONTEXT scout mode
- ambiguous verb route heuristics

## Phase 2 scope: DesiredRoute block

Goal: carry pre-dispatch route intent into the prompt for the first LLM call of a routed turn.

Included:

- Normalize `pre_gateway_dispatch` `runtime_override` metadata into pending per-session route state
- Carry route label, target provider/model/reasoning, source, strictness, confidence, and reason
- Consume pending route state once per gateway message so stale route intent does not leak into later turns
- Attach the route state to the live `AIAgent` before the conversation loop starts
- Render `DesiredRoute` beside `CurrentRuntime`
- Add tests for route-state normalization, one-shot consumption, and prompt rendering

Not included:

- automatic downgrade execution
- context-discovery reroute hook
- mutation boundary enforcement
- user-facing route correction prompts
- plugin classifier redesign

## Later phases

A later phase can add route re-evaluation after memory/file/web/GitHub inspection and before substantive writes. That work needs a real evidence model rather than a short ambiguous-verb list. It should handle edge cases such as mixed research/code tasks, user model pins, fallback recovery, streaming turns, retries, tool-only continuation, and stale cached agents.
