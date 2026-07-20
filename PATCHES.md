# Hermes Agent Fork — Patch Manifest

Source of truth for every **runtime** modification this fork carries on top of upstream `NousResearch/hermes-agent`.

Management files (`PATCHES.md`, `DECISIONS.md`, `bin/hermes-patches`, `bin/hermes-venv-rebuild`) live on the separate `soju/fork-policy` branch/worktree only. They are intentionally **not** applied to `soju/production`.

Never edit history of `main` (mirrors upstream).
Never commit directly to `soju/production` (rebuilt by `bin/hermes-patches rebuild`).
Every runtime patch must live in a `soju/patches/<name>` topic branch and be listed below.

## Pinned Base

```
upstream: NousResearch/hermes-agent
base_ref: upstream/main
base_commit:   4281151ae859241351ba14d8c7682dc67ff4c126
base_tag:      (none; exact target is not tagged)
base_describe: v2026.7.7.2-329-g4281151ae
pinned_at:    2026-07-12
```

Bump `base_commit` only via `bin/hermes-patches sync <new-ref>`. Each bump must rebase all `soju/patches/*` topics on top of the new base and verify the production stack rebuilds clean.

## Patches (apply order = list order)


### 1. runtime-control
- **branch:** `soju/patches/runtime-control`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood runtime control)_
- **state:** `local-only`
- **rationale:** Agent-callable `model_status` / `model_switch` tools. Session-only scope (turn scope removed — LLMs omitted scope ~29%, causing silent reversion). Model targets constrained to config-declared providers/models. The `model_switch` schema exposes every supported reasoning level, including `max`. Gateway session callback plus durable SessionEntry runtime fields so session-scoped model/reasoning overrides survive gateway restart without storing secrets. Trusted pre-dispatch plugin runtime overrides can select the session route after auth but before the first LLM call. Plugin `prepend` directives accumulate text before the original event message.
- **commits:**
  - `f1e48ab77 feat(runtime): agent-callable model_switch / model_status (session-only)`
  - `52cb19a17 fix(runtime): align runtime tool dispatch ownership`
  - `0521ec35c feat(runtime): route-enum self model switching (ADR-003 Phase 3b — route enum from the #34 catalog when present, graceful free-form degradation when absent)`
- **touches:** `agent/agent_init.py`, `agent/agent_runtime_helpers.py`, `agent/runtime_control.py`, `agent/tool_dispatch_helpers.py`, `agent/tool_executor.py`, `gateway/run.py`, `gateway/session.py`, `hermes_cli/plugins.py`, `hermes_cli/runtime_provider.py`, `model_tools.py`, `tests/gateway/test_pre_gateway_dispatch.py`, `tests/gateway/test_session.py`, `tests/gateway/test_session_model_override_routing.py`, `tests/hermes_cli/test_plugins.py`, `tests/hermes_cli/test_runtime_provider_resolution.py`, `tests/run_agent/test_pre_tool_session_id.py`, `tests/run_agent/test_run_agent.py`, `tests/run_agent/test_runtime_control.py`, `tests/test_model_tools.py`, `tests/tools/test_runtime_control_tool_schema.py`, `tools/runtime_control_tool.py`, `toolsets.py`

### 2. memory-write-reason-gate
- **branch:** `soju/patches/memory-write-reason-gate`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood memory hygiene)_
- **state:** `local-only`
- **rationale:** Memory `add`/`replace` tool calls require an explicit suitability reason explaining why USER/MEMORY is the right store rather than a skill, Graphiti, or session history. The reason is a guardrail only and is not persisted with the entry.
- **commits:**
  - `deb240f9d feat(memory): require write reason for memory updates`
  - `11758d1c8 test(memory): pass write reason in null-target dispatcher test`
- **touches:** `agent/agent_runtime_helpers.py`, `agent/tool_executor.py`, `tools/memory_tool.py`, `tests/tools/test_memory_tool.py`, `tests/tools/test_memory_tool_schema.py`

### 3. todo-progress-display
- **branch:** `soju/patches/todo-progress-display`
- **origin:** `local-author`
- **upstream_pr:** _(none — Discord/gateway dogfood UX)_
- **state:** `local-only`
- **rationale:** Gateway progress bubbles should show todo item statuses after the todo tool completes, not only a count like `planning N task(s)`. Also flush throttled progress edits when the final queued event has no following tool event.
- **commit:** `8a5ec3bcd feat(gateway): show todo progress details`
- **touches:** `agent/display.py`, `gateway/run.py`, `tests/agent/test_display.py`, `tests/gateway/test_run_progress_topics.py`

### 4. discord-table-codeblocks
- **branch:** `soju/patches/discord-table-codeblocks`
- **origin:** `local-author`
- **upstream_pr:** _(none — Discord/gateway dogfood UX)_
- **state:** `local-only`
- **rationale:** Discord does not render GitHub-flavored markdown pipe tables. Convert detected outbound pipe tables to fenced box-drawing ASCII tables so table responses remain readable, while preserving existing fenced code blocks, CJK wide-character alignment, and long-table chunk codeblock boundaries.
- **commit:** `32edef91f feat(discord): render markdown tables as codeblocks`
- **touches:** `plugins/platforms/discord/adapter.py`, `tests/gateway/test_discord_send.py`


### 5. delegate-per-task-model — SUPERSEDED (2026-07-16)
Superseded by patch #34 Phase 3a (route-enum delegation, `ffee9ea3c`): raw
per-task model/provider overrides are replaced by config-declared route enums,
matching upstream's `delegation-model-routing` policy while keeping per-task
routing. Branch `soju/patches/delegate-per-task-model` retained for history;
no longer part of the production stack.

### 6. background-review-guardrails
- **branch:** `soju/patches/background-review-guardrails`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood self-improvement guardrail)_
- **state:** `local-only`
- **rationale:** Background self-improvement review must be a calibrated participant in the memory/skill governance model instead of aggressively writing after most sessions. Memory review prompts now carry the structured always-injected memory rationale contract, and skill review prompts default to "Nothing to save" unless a concrete reusable signal exists.
- **commit:** `7c051dd4c fix: calibrate background self-improvement review`
- **touches:** `agent/background_review.py`, `tests/run_agent/test_review_prompt_class_first.py`

### 7. runtime-state-session-split
- **branch:** `soju/patches/runtime-state-session-split`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood runtime gate correctness)_
- **state:** `local-only`
- **rationale:** Runtime hard gates must see the active session's actual model/provider after context compression rotates the session id. Compression split republishes runtime state for the new session while retaining the old session as task/parent scope, preventing fallback to stale cross-session runtime state.
- **commit:** `6650c9bd7 fix: keep runtime state scoped across compression splits`
- **touches:** `agent/conversation_compression.py`, `tests/agent/test_runtime_state_session_split.py`

### 8. runtime-route-awareness
- **branch:** `soju/patches/runtime-route-awareness`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood runtime routing correctness)_
- **state:** `local-only`
- **rationale:** Inject an API-call-time Runtime/Route State block so the agent sees live CurrentRuntime plus current-turn DesiredRoute without calling `model_status`. Trusted pre-dispatch `runtime_override` metadata is normalized into one-shot route state for the routed gateway turn, preserving stale-route protection while leaving post-tool rerouting and NEED_CONTEXT scout mode for later phases.
- **commit:** `45f1d39f9 feat(runtime): inject runtime route awareness prompt` (stacked on `soju/patches/runtime-control`; branch contains runtime-control commits plus this one)
- **touches:** `agent/chat_completion_helpers.py`, `agent/conversation_loop.py`, `agent/system_prompt.py`, `docs/runtime-route-awareness.md`, `gateway/run.py`, `tests/agent/test_runtime_route_prompt.py`, `tests/gateway/test_pre_gateway_dispatch.py`

### 9. lsp-idle-reaper
- **branch:** `soju/patches/lsp-idle-reaper`
- **origin:** `upstream-pr:36892`
- **upstream_pr:** `36892`
- **state:** `pending-upstream`
- **rationale:** Reap idle LSP clients after `lsp.idle_timeout` so long-running gateways do not keep TypeScript/pyright/gopls/rust-analyzer subprocesses alive for the full process lifetime. `idle_timeout <= 0` disables reaping; stale clients respawn on the next relevant file operation.
- **commit:** `99778c08c fix(lsp): reap idle language-server clients`
- **touches:** `agent/lsp/manager.py`, `tests/agent/lsp/test_service.py`, `website/docs/user-guide/features/lsp.md`

### 10. aux-runtime-context
- **branch:** `soju/patches/aux-runtime-context`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood auxiliary runtime isolation)_
- **state:** `local-only`
- **rationale:** Auxiliary task routing must not use process-global main-runtime state in a concurrent gateway. Store the live provider/model/base_url/api_key/api_mode in thread-local state so one Discord thread's `codex-nekos/gpt-5.5` override cannot leak into another thread's `vision_analyze`, title generation, compression, or other auxiliary calls.
- **commit:** `309f3bf45 fix(auxiliary): isolate runtime routing per thread`
- **touches:** `agent/auxiliary_client.py`, `tests/agent/test_set_runtime_main_custom_provider.py`

### 11. gateway-max-iterations-config-authority
- **branch:** `soju/patches/gateway-max-iterations-config-authority`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood gateway budget config correctness)_
- **state:** `local-only`
- **rationale:** Gateway agent turns must resolve `max_iterations` from config.yaml `agent.max_turns` as the source of truth before falling back to `HERMES_MAX_ITERATIONS`. A stale `.env` value such as `HERMES_MAX_ITERATIONS=90` must not override `agent.max_turns: 300` or cause intermittent `Iteration budget exhausted (90/90)` in concurrent Discord sessions.
- **commit:** `b5d15809a fix(gateway): keep max iterations config authoritative`
- **touches:** `gateway/run.py`, `tests/gateway/test_runtime_env_reload_config_authority.py`

### 12. strict-chat-reasoning-details
- **branch:** `soju/patches/strict-chat-reasoning-details`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood strict OpenAI-compatible provider replay fix)_
- **state:** `local-only`
- **rationale:** Strict OpenAI-compatible Chat Completions providers such as GLM Vooy reject non-standard assistant message replay fields (`reasoning`, `reasoning_details`) with `Extra inputs are not permitted`. Preserve those fields in session history for provider continuity, but strip them from the outbound chat_completions wire payload so mixed-provider Discord sessions do not get stuck in repeat HTTP 400 retries.
- **commit:** `9f319ad87 fix(chat): strip reasoning replay fields for strict chat completions`
- **touches:** `agent/transports/chat_completions.py`, `tests/run_agent/test_strict_api_validation.py`

### 13. discord-home-autothread-fix
- **branch:** `soju/patches/discord-home-autothread-fix`
- **origin:** `local-author`
- **upstream_pr:** _(none — Discord home-channel dogfood routing fix)_
- **state:** `local-only`
- **rationale:** Discord home-channel messages should still auto-create thread conversations when channel controls disable broad channel auto-threading. Restore the home-channel path while keeping explicit channel-control disable behavior available for non-home channels.
- **commit:** `7851232d1 fix(discord): restore home channel auto-threading`
- **touches:** `gateway/config.py`, `plugins/platforms/discord/adapter.py`, `tests/gateway/test_discord_channel_controls.py`

### 14. runtime-override-rehydrate-credentials
- **branch:** `soju/patches/runtime-override-rehydrate-credentials`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood runtime-control follow-up fix)_
- **state:** `local-only`
- **rationale:** Upstream owns live `switch_model` endpoint rollback and provider-header reapplication; this patch is limited to gateway restart/persistence. A persisted runtime route is eligible only when both `runtime_model` and `runtime_provider` labels exist, then resolves once with `target_model` into one authoritative provider bundle (normalized provider, endpoint, API mode, keyed or keyless credential, command/args, and credential pool). Incomplete or unresolved labels stay durable but dormant while the turn falls back wholly to coherent defaults. Persisted reasoning rehydrates independently, and the latest `/model` or runtime-route writer clears the other durable model-route store without clearing reasoning. State DB coverage verifies DB-only restart and that no secret bundle fields are persisted.
- **commit:** `31e65b358 fix(gateway): re-resolve persisted runtime override bundle` (stacked on `soju/patches/runtime-control`; branch contains runtime-control commits plus this one)
- **touches:** `gateway/run.py`, `gateway/session.py`, `tests/gateway/test_runtime_override_restart_rehydration.py`, `tests/gateway/test_runtime_override_state_db_restart.py`, `tests/gateway/test_runtime_override_store_precedence.py`, `tests/gateway/test_session_model_override_routing.py`

### 15. turn-waterfall-tracing
- **branch:** `soju/patches/turn-waterfall-tracing`
- **origin:** `local-author`
- **upstream_pr:** _(none — perf instrumentation for local bottleneck hunt)_
- **state:** `local-only`
- **rationale:** Per-turn waterfall tracing to attribute end-to-end turn latency (observed ~30% slower than a minimal agent on the same LLM). New `agent/turn_trace.py` collects wall-clock spans across the whole turn lifecycle — gateway ingest/session-resolve/transcript-load/agent-setup, prologue children (system-prompt restore, early persist, compression preflight, pre-LLM hook, memory prefetch), per-iteration context assembly/request setup/`llm.call` (TTFT + failed attempts) /accounting, tool batches incl. the inter-tool delay sleep, verify gates, finalize children, gateway persist, transport delivery — and emits one JSONL record per turn to `~/.hermes/logs/turn_traces.jsonl`. `agent/turn_trace_render.py` renders terminal/HTML waterfalls and cross-turn p50/p95 summaries with a model-time vs hermes-overhead split. Gated by `HERMES_TURN_TRACE=1` (default off = no-op); tracing failures can never break a turn.
- **commits:**
  - `50b72568b feat(telemetry): per-turn waterfall tracing spans`
  - `437d22fa5 fix(telemetry): carry turn trace across pre-dispatch event replacement` _(pre-dispatch hooks may swap the event via dataclasses.replace; bind the trace to the surviving SessionSource so adapter finish sites can always reach it)_
  - `e59fe6ef9 feat(telemetry): request prefix fingerprints for cache-break diffing` _(HERMES_TURN_TRACE_PREFIX=1 adds wire-faithful per-message hashes to llm.call spans; `turn_trace_render --cache-diff` names the first divergent message across consecutive turns — for the first-call cache p50 13% hunt)_
- **touches:** `agent/turn_trace.py` _(new)_, `agent/turn_trace_render.py` _(new)_, `tests/agent/test_turn_trace.py` _(new)_, `agent/chat_completion_helpers.py`, `agent/conversation_loop.py`, `agent/tool_executor.py`, `agent/turn_context.py`, `agent/turn_finalizer.py`, `gateway/platforms/base.py`, `gateway/run.py`, `plugins/platforms/telegram/adapter.py`, `run_agent.py`

### 16. tool-delay-removal
- **branch:** `soju/patches/tool-delay-removal`
- **origin:** `local-author`
- **upstream_pr:** [#64172](https://github.com/NousResearch/hermes-agent/pull/64172) _(being rewritten from env-knob to full removal)_
- **state:** `local-only`
- **rationale:** Remove the inter-tool 1.0s sleep entirely (supersedes the earlier env-knob patch). The delay has been present verbatim since upstream's initial commit with no documented rationale; it sleeps between LOCAL tool executions (the next LLM request only goes out after the whole batch), so it rate-limits nothing — pure (N-1)s dead time per multi-tool turn. Also removes the `tool_delay` parameter plumbing, dead `agent.tool_delay = 0` test remnants, and the now-dead `tools.delay` trace span from the renderer.
- **commits:** (stacked on `soju/patches/turn-waterfall-tracing` — the removal deletes the span-wrapped sleep the tracing patch instrumented)
  - `6f5dffad5 refactor(agent): remove the inter-tool delay entirely`
- **touches:** `agent/tool_executor.py`, `agent/agent_init.py`, `run_agent.py`, `agent/turn_trace_render.py`, `tests/` _(dead assignments removed)_

### 17. prompt-cache-stability
- **branch:** `soju/patches/prompt-cache-stability`
- **origin:** `local-author`
- **upstream_pr:** _(none — fork latency work, candidate for upstreaming after burn-in)_
- **state:** `local-only`
- **rationale:** Turn-boundary prompt-cache stabilization ("persist what you send"). Live tracing proved the first LLM call of every gateway turn gets ~0% provider prompt cache (in-turn calls 97–99%) because the bytes sent differ from the bytes replayed from the transcript next turn: API-call-time injections into the current user message (memory prefetch, plugin pre_llm_call context) were never persisted, the #48677 persist override stores cleaned content, and `get_messages_as_conversation` sanitizes on load. Fix: nullable `api_content` sidecar column on `messages` stores the exact wire bytes when they differ from clean content; the api_messages build replays the sidecar verbatim for historical user/assistant rows and pops the field from every outgoing copy. Prologue stamps the sidecar (persist_early moved after memory prefetch so the row is written once, complete); flush captures override/sanitize divergences; gateway replay, append_to_transcript, /branch copies, max-iterations summary, compression/repair rewrite sites and MoA all audited to carry or safely drop it. Expected effect: first-call cache from p50 13% toward 90%+ once the system-prompt tail churn (next patch) is also fixed.
- **commits:** (stacked on `soju/patches/turn-waterfall-tracing`; branch contains the tracing commits plus these)
  - `2a476737d feat(cache): api_content sidecar — persist the exact bytes sent`
  - `67ab2dce0 fix(cache): close review gaps in the api_content sidecar`
- **touches:** `hermes_state.py`, `agent/turn_context.py`, `agent/conversation_loop.py`, `agent/chat_completion_helpers.py`, `agent/context_compressor.py`, `agent/replay_cleanup.py`, `agent/agent_runtime_helpers.py`, `agent/transports/chat_completions.py`, `run_agent.py`, `gateway/run.py`, `gateway/session.py`, `gateway/slash_commands.py`, `hermes_cli/cli_commands_mixin.py`, `tests/agent/test_api_content_sidecar.py` _(new)_, `tests/gateway/test_replay_entry_fields.py` _(new)_

### 18. prompt-tail-freeze
- **branch:** `soju/patches/prompt-tail-freeze`
- **origin:** `local-author`
- **upstream_pr:** _(none — fork latency work, candidate for upstreaming after burn-in)_
- **state:** `local-only`
- **rationale:** Byte-stable gateway system prompts (the second half of the turn-boundary prompt-cache fix; pairs with #17). Live sys-tail diffs proved the composed system prompt tail changes between consecutive turns — DesiredRoute one-shot flips, CurrentRuntime reasoning value/suffix instability, per-turn ephemeral recomposition (auto-thread rename, reset notes, VC state) — which kills the provider prefix at the head and re-keys the content-addressed `prompt_cache_key` on codex/xai routes (observed 0.1% cache despite a 14.5%-matching head). Fix: pin the session-context prompt per session keyed by a hash of exactly the fields it renders (re-render only on legitimate changes: renames, /sethome, redact_pii, toolset flips); freeze the runtime/route block behind a runtime key-tuple cache with `reasoning_source=` always emitted and a permanently static DesiredRoute; deliver one-shot facts (routing directives, VC changes, auto-reset notes, first-contact intro) on the current user message via the #17 api_content sidecar (multimodal turns get a durable appended text part); guard `_apply_gateway_runtime_override` so same-route re-selections stop evicting the cached agent; persist the reasoning half of runtime overrides (fixes the post-restart `reasoning=max`↔`unknown` byte flip); sort `get_connected_platforms`.
- **commits:** (stacked on `soju/patches/prompt-cache-stability` + tips of `runtime-route-awareness`/`turn-waterfall-tracing`; branch contains those commits plus these)
  - `39d47f64b merge: stack prompt-tail-freeze on runtime-route + waterfall tips`
  - `66849a10f feat(cache): prompt-tail freeze — byte-stable gateway system prompts (patch #18)`
- **touches:** `gateway/run.py`, `gateway/session.py`, `gateway/config.py`, `agent/system_prompt.py`, `agent/turn_context.py`, `tests/gateway/test_prompt_tail_freeze.py` _(new)_, `tests/agent/test_gateway_turn_sidecar.py` _(new)_, `tests/agent/test_runtime_route_prompt.py`

### 19. request-client-reuse
- **branch:** `soju/patches/request-client-reuse`
- **origin:** `local-author`
- **upstream_pr:** [#64170](https://github.com/NousResearch/hermes-agent/pull/64170)
- **state:** `local-only`
- **rationale:** A fresh OpenAI wire client (new httpx pool, TCP+TLS handshake) was built and torn down for EVERY LLM call (`llm.client_create` p50 19.2ms / p95 35.5ms, ~5 calls/turn, 13.5%% of pooled overhead self-time). Now one reusable client is cached per agent keyed by the effective request kwargs (incl. resolved headers): reuse on identical kwargs, rebuild on credential rotation/failover/vision-header variant, poison-on-abort so a stranger-thread socket shutdown (#29507) can never hand a dead client to the next call, real close on kwargs change/agent teardown/gateway eviction. Interrupt-break SSE leak plugged (stream closed on early break); holder-read+abort made atomic at all three abort sites (streaming, non-streaming, cron inline); codex stream close-failure poisons the slot.
- **commits:** (stacked on `soju/patches/prompt-tail-freeze`)
  - `b5f8a9b4c perf(llm): reuse the per-request OpenAI wire client across sequential calls`
  - `e53c73c51 fix(llm): plug interrupt-break connection leak; make stranger abort atomic`
  - `cdbc057fc fix(llm): extend atomic holder-abort to cron inline path; poison on codex stream close failure`
- **touches:** `agent/chat_completion_helpers.py`, `agent/codex_runtime.py`, `run_agent.py`, `tests/agent/test_request_client_reuse.py`, `tests/run_agent/test_openai_client_lifecycle.py`, `tests/run_agent/test_request_client_reuse_abort_races.py`

### 20. async-token-accounting
- **branch:** `soju/patches/async-token-accounting`
- **origin:** `local-author`
- **upstream_pr:** [#64171](https://github.com/NousResearch/hermes-agent/pull/64171)
- **state:** `local-only`
- **rationale:** `update_token_counts` ran a synchronous sqlite UPDATE on the turn thread after every API call (`llm.accounting` p50 3.3ms / p95 70ms, historically 299ms into the cold 6.8GB state.db). Deltas now enqueue to a single-writer daemon thread that applies them in order with backlog coalescing; sync model/billing-route writers flush the queue first (happens-before preserved); drains at turn finalize, close(), and atexit; enqueue-after-close applies inline instead of dropping; readers needing exact values call flush() (cheap when empty).
- **commits:** (stacked on `soju/patches/prompt-tail-freeze`)
  - `7fb0d5330 perf(accounting): async token accounting — per-call deltas off the turn thread (patch #19)`
  - `3a74edb97 fix(accounting): close review gaps in the async token writer (patch #19 review)`
  - `20cde884f fix(accounting): enqueue-after-close applies inline instead of dropping`
- **touches:** `agent/codex_runtime.py`, `agent/conversation_loop.py`, `agent/insights.py`, `hermes_state.py`, `run_agent.py`, `tests/agent/test_async_token_accounting.py`, `tests/run_agent/test_token_persistence_non_cli.py`

### 21. gateway-persist-trim
- **branch:** `soju/patches/gateway-persist-trim`
- **origin:** `local-author`
- **upstream_pr:** [#64169](https://github.com/NousResearch/hermes-agent/pull/64169)
- **state:** `local-only`
- **rationale:** The steady-state gateway turn bumps `updated_at`/`last_prompt_tokens` on ONE routing entry but paid the full index rewrite twice per turn — every entry re-serialized, DELETE+INSERT of every `gateway_routing` row, and a multi-MB sessions.json dump+fsync (~50ms p50 at ~1100 keys, inside the ~175ms/turn session_resolve+persist spans). Metadata-only saves now UPSERT the single row (<1ms) with a routing-generation guard against regressing a racing full snapshot; structural transitions (create/recover/reset/switch/prune, compression-tip heals) keep the full rewrite incl. the sessions.json mirror; peer fields snapshot under `_lock` (no torn rows); DB-less installs fall back to the full rewrite.
- **commits:** (stacked on `soju/patches/prompt-tail-freeze`)
  - `4e28ace06 perf(gateway): single-row routing UPSERT fast path for metadata-only saves`
- **touches:** `gateway/session.py`, `tests/gateway/test_routing_save_fast_path.py`

### 19. request-client-reuse
- **branch:** `soju/patches/request-client-reuse`
- **origin:** `local-author`
- **upstream_pr:** _(none — fork latency tuning)_
- **state:** `local-only`
- **rationale:** A fresh OpenAI wire client (new httpx pool, TCP+TLS handshake) was built and torn down for EVERY LLM call (`llm.client_create` p50 19.2ms / p95 35.5ms x ~5 calls/turn = 13.5%% of pooled hermes self-time). Now cached per agent keyed by the effective request kwargs (incl. max_retries=0 and the copilot vision default_headers variant) and reused across sequential calls: success paths keep the client; request/stream errors, cross-thread aborts (poison), credential/base_url rotation (key change), and agent teardown rebuild or close it. Preserves the #29507 stranger-thread abort discipline (abort sockets + detach; owner closes) and extends atomic holder-abort to the cron inline path. Warm reuse measured 0.008-0.039ms vs ~19ms build — ~75-80ms saved per typical turn.
- **commits:** (stacked on `soju/patches/prompt-tail-freeze`)
  - `b5f8a9b4c perf(llm): reuse the per-request OpenAI wire client across sequential calls`
  - `e53c73c51 fix(llm): plug interrupt-break connection leak; make stranger abort atomic`
  - `cdbc057fc fix(llm): extend atomic holder-abort to cron inline path; poison on codex stream close failure`
- **touches:** `run_agent.py`, `agent/chat_completion_helpers.py`, `agent/codex_runtime.py`, `tests/agent/test_request_client_reuse.py` _(new)_, `tests/run_agent/test_openai_client_lifecycle.py`

### 20. async-token-accounting
- **branch:** `soju/patches/async-token-accounting`
- **origin:** `local-author`
- **upstream_pr:** _(none — fork latency tuning)_
- **state:** `local-only`
- **rationale:** Per-call token/cost accounting (`update_token_counts`) ran synchronously on the turn thread after every API call (`llm.accounting` p50 3.3ms / p95 70.4ms, historically 299ms into the cold 6.8GB state.db). Deltas now enqueue to a single-writer background thread (ordered, coalescing consecutive same-session deltas), with flush barriers where readers need exactness (model-switch route updates flush first so a queued pre-switch delta cannot resurrect the old route), drain on turn finalize/close/atexit (unregistered on close), busy-claim protocol so concurrent drains cannot interleave, and enqueue-after-close applying inline instead of dropping. The `llm.accounting` span now measures the enqueue.
- **commits:** (stacked on `soju/patches/prompt-tail-freeze`)
  - `7fb0d5330 perf(accounting): async token accounting — per-call deltas off the turn thread (patch #19)`
  - `3a74edb97 fix(accounting): close review gaps in the async token writer (patch #19 review)`
  - `20cde884f fix(accounting): enqueue-after-close applies inline instead of dropping`
- **touches:** `hermes_state.py`, `agent/conversation_loop.py`, `agent/codex_runtime.py`, `tests/agent/test_async_token_accounting.py` _(new)_

### 21. gateway-persist-trim
- **branch:** `soju/patches/gateway-persist-trim`
- **origin:** `local-author`
- **upstream_pr:** _(none — fork latency tuning)_
- **state:** `local-only`
- **rationale:** Profiled `gateway.session_resolve`/`gateway.persist` (~175ms per gateway turn): the steady-state turn bumps updated_at/last_prompt_tokens on ONE routing entry but paid the full index rewrite twice — every entry re-serialized, DELETE+INSERT of every gateway_routing row, and a multi-MB sessions.json dump+fsync (~50ms p50 at ~1100 keys). Metadata-only saves now UPSERT the single row (<1ms) via the pre-existing `save_gateway_routing_entry`; structural transitions (create/recover/reset/switch/prune, compression-tip heals) keep the full rewrite which also refreshes the legacy sessions.json mirror; a generation guard prevents a racing full snapshot from being regressed by a stale single-row write; no-DB installs keep the full rewrite (sessions.json is their primary store).
- **commits:** (stacked on `soju/patches/prompt-tail-freeze`)
  - `4e28ace06 perf(gateway): single-row routing UPSERT fast path for metadata-only saves`
- **touches:** `gateway/session.py`, `tests/gateway/test_routing_save_fast_path.py` _(new)_

### 22. gateway-worker-pool
- **branch:** `soju/patches/gateway-worker-pool`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate after burn-in)_
- **state:** `local-only`
- **rationale:** The gateway's shared agent-turn ThreadPoolExecutor was hardcoded to 10 workers; every agent turn holds one worker for its full duration, so a kanban batch of 8+ concurrent multi-hour marathon turns (300-iteration workers, blocking process waits) starved every other session for hours — observed 9.1h between a finished turn's finalize and its delivery, and users reporting sessions "quietly frozen". Default raised to 24 and exposed as config.yaml `gateway.max_workers` with `HERMES_GATEWAY_MAX_WORKERS` env fallback (clamped ≥ 2); workers are network-I/O-bound so the larger pool is cheap.
- **commits:** (stacked on `soju/patches/prompt-tail-freeze`)
  - `183085fff perf(gateway): size the agent-turn pool from config, default 24`
- **touches:** `gateway/run.py`, `tests/gateway/test_gateway_max_workers.py` _(new)_

### 23. conn-error-fail-fast
- **branch:** `soju/patches/conn-error-fail-fast`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate after burn-in)_
- **state:** `local-only`
- **rationale:** Consecutive sub-2s transport failures (connection refused/reset before any bytes) mean the endpoint is down, not congested; with no fallback available the retry loop burned a dozen attempts with growing backoff (observed 13 attempts / 170-250s of user-visible silence per turn against a briefly-down codex-lb). Track the instant-failure streak on TurnRetryState and end the turn with an actionable error after `HERMES_FAST_CONN_FAIL_LIMIT` (default 3, 0 disables) once the fallback chain has had its chance; slow timeouts keep the full retry budget.
- **commits:** (stacked on `soju/patches/prompt-tail-freeze`)
  - `c6040f7c6 fix(agent): fail fast on instant transport-failure streaks`
- **touches:** `agent/conversation_loop.py`, `agent/turn_retry_state.py`, `tests/agent/test_fast_transport_fail_fast.py` _(new)_

### 24. background-first-waits
- **branch:** `soju/patches/background-first-waits`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate after burn-in)_
- **state:** `local-only`
- **rationale:** Models foreground-polled long jobs with chained `process wait` calls (observed 4-5 consecutive 180s waits per turn — total user-facing silence while holding a gateway worker). Background-first: the first full block-wait stays a normal flow (180s window unchanged per user request), but from the SECOND consecutive timed-out wait on the same still-running process, `notify_on_complete` is auto-armed and the tool result instructs the model to end its turn with a summary — the completion re-enters the session as an event and the model explains the result there (the natural LLM recap). `HERMES_PROCESS_WAIT_CAP` tunes the quiet-wait allowance (default 1, 0 disables for completion-bound sessions like kanban workers). The process tool schema and terminal guidance now state the one-wait-per-process contract (one-time tools-hash cache bust on deploy, legitimate).
- **commits:** (stacked on `soju/patches/prompt-tail-freeze`)
  - `2b9be80da feat(tools): background-first process waits — escalate chained blocking waits`
- **touches:** `tools/process_registry.py`, `tools/terminal_tool.py`, `tests/tools/test_background_first_waits.py` _(new)_

### 25. llm-activity-recap
- **branch:** `soju/patches/llm-activity-recap`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate after burn-in)_
- **state:** `local-only`
- **rationale:** `display.long_running_notifications` gains a `recap` mode: instead of the terse "⏳ Working — N min — iteration i/max" heartbeat, the gateway asks the auxiliary LLM (compression rail, 8s timeout, ~80 tokens) for a one-line present-tense recap of what the agent is doing — goal + recent tool calls + current wait — in the conversation's language (Claude Code-style). Context-hash caching regenerates only on activity change; failures fall back to the terse heartbeat; in recap mode the bubble is deleted-and-resent (adapters with delete support) so it stays at the thread bottom instead of buried. Complements #24 background-first-waits: waits that should end the turn do, and turns that legitimately run long narrate themselves.
- **commits:** (stacked on `soju/patches/prompt-tail-freeze`)
  - `67bbd6f87 feat(gateway): LLM activity recap for long-running notifications`
  - `4bd93f530 feat(gateway): tune activity recap context from live-session evaluation` _(real-session eval: bracket-note goal stripping + fallback, user-message language detection, agent-last-words + last-tool-result in context, per-tool semantic labels)_
  - `feat(gateway): recap speaks in the agent's own voice` _(voice samples from the agent's own recent utterances replace the language heuristic — language/tone/persona mirroring)_
  - `feat(gateway): persona-definition voice fallback for fresh sessions` _(SOUL identity+style head injected as the voice when no utterance exists yet — first-turn recaps keep the persona register; chosen over prior-session DB lookup)_
- **touches:** `gateway/run.py`, `run_agent.py`, `tests/gateway/test_llm_activity_recap.py` _(new)_

### 26. config-knob-bridges
- **branch:** `soju/patches/config-knob-bridges`
- **origin:** `local-author`
- **upstream_pr:** _(none — fork knob plumbing)_
- **state:** `local-only`
- **rationale:** Fork knobs were env-only, breaking the house convention (config.yaml is authoritative; env is the cross-process carrier/override — see upstream PR #64298). Bridge `agent.process_wait_cap` → `HERMES_PROCESS_WAIT_CAP` (#24) and `agent.fast_conn_fail_limit` → `HERMES_FAST_CONN_FAIL_LIMIT` (#23) in both the startup export block and the per-turn reload bridge. Recap interval already had the upstream `agent.gateway_notify_interval` bridge.
- **commits:** (stacked on `soju/patches/prompt-tail-freeze` + `soju/patches/gateway-max-iterations-config-authority` tip — shares the bridge function)
  - `feat(gateway): config.yaml bridges for fork knobs`
- **touches:** `gateway/run.py`, `tests/gateway/test_fork_knob_config_bridges.py` _(new)_

### 27. durable-turns
- **branch:** `soju/patches/durable-turns`
- **origin:** `local-author`
- **upstream_pr:** _(pending — branch `upstream-pr/durable-turns` pushed to fork, PR body drafted, awaiting owner approval to open)_
- **state:** `local-only`
- **rationale:** Restart recovery was a paper-over: an interrupted turn was abandoned and a synthetic empty user turn told the model to "skip any unfinished work" — the banner's "I'll try to resume where you left off" was implemented by nothing (live incident 2026-07-15, discord thread 1526457680527622247 reduced to "응 오빠, 여기 있어"). Durable turns make the in-flight turn a first-class durable record (`SessionEntry.active_turn`: turn_id/status/boot_id/resume_count) and re-enter the SAME turn on its persisted transcript after a restart: `run_conversation(resume_turn=True)` appends no user row, drops synthetic "Operation interrupted…" closers, completes unanswered tool_calls via existing orphan recovery (side effects = UNKNOWN, never re-executed), and delivers an already-composed final without another model call. Poison cap `HERMES_TURN_RESUME_MAX`/`agent.turn_resume_max` (default 2) abandons repeat offenders with an honest notice. Kill switch `HERMES_GATEWAY_TURN_RESUME`/`agent.gateway_turn_resume` restores legacy behavior. Banner reworded to match reality. See `DECISIONS.md` ADR-002.
- **commits:** (stacked on `soju/patches/llm-activity-recap` + `soju/patches/config-knob-bridges` tips — shares run_conversation wrapper, api_content sidecar prologue, and the config-bridge block)
  - `656256d53 feat(gateway): durable turns — same-turn resume across gateway restarts`
  - `e5dd8e1c8 merge: stack durable-turns on llm-activity-recap tip`
  - `56aecda05 merge: stack durable-turns on config-knob-bridges tip`
- **touches:** `agent/turn_resume.py` _(new)_, `agent/turn_context.py`, `agent/conversation_loop.py`, `agent/turn_finalizer.py`, `run_agent.py`, `gateway/run.py`, `gateway/session.py`, `hermes_cli/config.py`, `tests/agent/test_turn_resume.py` _(new)_, `tests/gateway/test_durable_turn_records.py` _(new)_, `tests/run_agent/test_resume_turn_loop.py` _(new)_, `tests/gateway/restart_test_helpers.py`, `tests/gateway/test_restart_resume_pending.py`

### 28. hook-prepend-command-safety
- **branch:** `soju/patches/hook-prepend-command-safety`
- **origin:** `local-author`
- **upstream_pr:** _(none — hardens the fork-only pre_gateway_dispatch prepend action from runtime-control)_
- **state:** `local-only`
- **rationale:** A `pre_gateway_dispatch` `{"action": "prepend"}` rewrote `event.text` before slash dispatch, and `is_command()`/`get_command()` key off `text.startswith("/")` — so any plugin prepend on a command message demoted it into plain chat that fell through to the agent. Live incident 2026-07-15: the inbox-matter-coordinator advisory rewrite (plugin 9ed17c4) prepends an `[INBOX_MATTER ...]` marker on matter-linked threads; `/model` there was answered by the agent's `model_status` tool instead of the interactive picker, and every slash command in those threads broke the same way. Fix: drop prepends (debug log) when the event is a slash command — advisory context is agent-facing and command handlers can't consume it. Plain-chat prepend behavior unchanged.
- **commits:** (stacked on `soju/patches/durable-turns` tip)
  - `28e7b2b5a fix(gateway): drop pre_gateway_dispatch prepends on slash commands`
- **touches:** `gateway/run.py`, `tests/gateway/test_pre_gateway_dispatch.py`

### 29. slow-tool-perf-advisor
- **branch:** `soju/patches/slow-tool-perf-advisor`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — candidate once soak data confirms hint uptake)_
- **state:** `local-only`
- **rationale:** Trace mining across 2,605 turns (2026-07-16) showed the dominant avoidable turn latency is agents routing heavy work through `terminal` in shapes with a cheap native equivalent: full-tree scans (`rglob`/`os.walk`/unpruned `find`/bare `grep -r` — 59+32+209 calls) over repos whose `node_modules` holds >1.7M files (observed 88s + 63s back-to-back scans of the same tree in one Discord turn), foreground `sleep` polling (364 calls), and multi-minute foreground jobs pinning session workers — while `search_files` (rg-backed, .gitignore-aware) answers in ~1s and background+`notify_on_complete` re-enters the session for free. Fix is a thin advisory-only shot mirroring the subdirectory-hints append: when a terminal call is slow AND matches an antipattern, append ONE `[perf-advisor]` line to the tool result at the moment the model just paid for the slow call. Kill switch `HERMES_PERF_ADVISOR=0`; thresholds `HERMES_PERF_ADVISOR_MIN_S` (10s), `HERMES_PERF_ADVISOR_FOREGROUND_S` (120s).
- **commits:**
  - `261253849 feat(tools): slow-tool perf advisor — teach cheaper shapes on slow terminal results`
- **touches:** `tools/perf_advisor.py` (new), `agent/tool_executor.py`, `tests/test_perf_advisor.py` (new)

### 30. session-db-read-path-split
- **branch:** `soju/patches/session-db-read-path-split`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — good candidate after soak; the shared-SessionDB convoy exists upstream too)_
- **state:** `local-only`
- **rationale:** The gateway hands ONE SessionDB to every agent (gateway/run.py `session_db=...`), so all recall/browse reads queued behind all writer flushes on `self._lock` — one Python lock in front of a WAL DB that natively supports concurrent readers. Measured production convoy (2026-07-16 trace analysis): a 0.23s FTS query stretched to 112s, a browse flush to 137s, while 6-8 concurrent turns flushed hundreds of tool results; session_search averaged 12.4s in-gateway vs 0.26s standalone. Fix: under WAL the seven read-only recall methods run on per-thread `mode=ro` connections via `_read_ctx()` with no lock; fresh read transactions per statement preserve read-your-committed-writes. Non-WAL (NFS DELETE fallback) or read-conn open failure falls back to the legacy locked path.
- **commits:** (stacked on `soju/patches/hook-prepend-command-safety` tip; merges `soju/patches/async-token-accounting` to co-resolve the SessionDB __init__/close/get_session hunks)
  - `90a6ac9a6 perf(state): read-path split — per-thread read-only connections for recall reads`
  - `2972cb535 Merge branch 'soju/patches/async-token-accounting' (conflict resolution: flush_token_counts before read_ctx in get_session)`
- **touches:** `hermes_state.py`, `tests/test_session_db_read_path_split.py` (new)

### 31. fts5-cjk-bigram-index
- **branch:** `soju/patches/fts5-cjk-bigram-index`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — strong candidate; CJK search brokenness is upstream-wide)_
- **state:** `local-only`
- **rationale:** unicode61 indexes a CJK run as ONE token, so 2-char Korean terms could never match; hermes routed any query containing one to a 3-column LIKE full-table scan (3-6.4s CPU on the 6.8GB state.db) — the #1 base cost behind session_search's 12.4s production average, and the LIKE scans were also the convoy source amplified by the shared-lock issue fixed in #30. Adds a loadable FTS5 tokenizer `cjk_unicode61` (native/fts5_cjk, unicode61 wrapper emitting CJK character bigrams) + standalone 3-column `messages_fts_v2` whose single MATCH path replaces the unicode61/trigram/LIKE routing (prototype: 3.7s→83ms). Online migration via `scripts/fts_v2_migrate.py` (idempotent batched backfill, resume in state_meta); read cutover behind `HERMES_FTS_V2_READ=1`; self-heal drops v2 triggers when the tokenizer can't load so writes never fail. Update triggers scoped `AFTER UPDATE OF` content columns (v1 re-tokenized whole messages on flag flips).
- **commits:** (stacked on `soju/patches/session-db-read-path-split` tip)
  - `3425751c1 feat(state): messages_fts_v2 — cjk_unicode61 bigram index replaces trigram+LIKE routing`
- **touches:** `hermes_state.py`, `native/fts5_cjk/*` (new), `scripts/fts_v2_migrate.py` (new), `tests/test_fts_v2_cjk.py` (new)

### 32. search-slow-query-log
- **branch:** `soju/patches/search-slow-query-log`
- **origin:** `local-author`
- **upstream_pr:** _(none yet)_
- **state:** `local-only`
- **rationale:** The session_search latency investigation (2026-07-16) required turn-trace archaeology + workload replay to attribute 12.4s searches to LIKE full scans. One INFO line per slow search (threshold `HERMES_SEARCH_SLOW_MS`, default 1000ms) naming the routing path (fts_v2/fts5/trigram/like_scan), elapsed, rows, and query makes the next routing regression a journalctl grep. Wrapper around `_search_messages_impl`; zero behavior change.
- **commits:** (stacked on `soju/patches/fts5-cjk-bigram-index` tip)
  - `d6633adcb feat(state): slow-query log for session search with routing-path attribution`
- **touches:** `hermes_state.py`, `tests/test_search_slow_query_log.py` (new)

### 33. fts-v2-config-authority
- **branch:** `soju/patches/fts-v2-config-authority`
- **origin:** `local-author`
- **upstream_pr:** _(folds into the #31 upstream submission — sweeper requires the config.yaml surface)_
- **state:** `local-only`
- **rationale:** Config SoT for the FTS v2 knobs (`agent.fts_v2_read`, `agent.search_slow_ms` bridged at both gateway bridge sites, house pattern), read default promoted to ON behind a `state_meta fts_v2_ready` backfill-completion marker (a triggers-only/partial index is never served; migrate script sets the marker), fresh DBs with a loadable tokenizer are v2-native (no v1/trigram tables), and `scripts/fts_v1_drop.py` retires the six v1 triggers + two tables behind an integrity/rowcount preflight (~5.6GB logical). After the drop the off-flag is ignored — no fallback exists to select.
- **commits:** (stacked on `soju/patches/search-slow-query-log` tip)
  - `a7109d33e feat(state): FTS v2 config authority, default-on reads, v1 retirement path`
- **touches:** `hermes_state.py`, `gateway/run.py`, `scripts/fts_v2_migrate.py`, `scripts/fts_v1_drop.py` (new), `tests/gateway/test_fts_v2_config_bridge.py` (new), `tests/test_fts_v2_cjk.py`, `tests/test_search_slow_query_log.py`

### 34. model-routing
- **branch:** `soju/patches/model-routing`
- **upstream_pr:** _(none — ADR-003 Phase 1; enum'd delegation aligns with upstream `delegation-model-routing` policy, natural round-2 candidate once Phases 2–3 prove it)_
- **origin:** `local-author`
- **state:** `local-only`
- **rationale:** ADR-003 Phases 1+2 — model routing SoT as a core subsystem. Phase 1: config-declared `model_routes:` catalog (per route: description, provider, model, reasoning_effort, accepted membership, ordered fallbacks), loader with startup cross-validation against `providers:`, and a health-aware resolver (`resolve_route`) that walks default→fallbacks with TTL-cached fail-open provider probes ported from skill-gate's runtime_catalog (401/403 healthy, credit-sniffed 400, 402/429/5xx unhealthy; config-first kill switch `model_routes.health.enabled` + `HERMES_MODEL_ROUTES_HEALTH` bridge). Phase 2: gateway dynamic router (`gateway/model_router.py` + `_model_router_stage`) — classifier core byte-identical to the skill-gate bench winner (parity-test-pinned; changing it requires the 230+120 bench rerun), `model_routes.router` config (mode off|shadow|enforce + `HERMES_MODEL_ROUTER_MODE` bridge, label_routes, chat_route, streak), static condition rules (short-circuit by design — the plugin's live last-wins is a bug not ported), decision log `~/.hermes/logs/model_router_decisions.jsonl` for shadow-soak comparison, enforce apply with /model parity (#48031 auto-reset survival, pending note, session-DB persist). Dormant with empty config. Phase 3 (`model_switch`/`delegate_task` route enums) builds on this; supersedes patch #5 when Phase 3 lands.
- **commits:**
  - `792162537 feat(routing): model_routes catalog + health-aware route resolver (ADR-003 Phase 1)`
  - `27fd5fffd feat(routing): core dynamic model router with shadow mode (ADR-003 Phase 2)`
  - `ffee9ea3c feat(routing): route-enum delegation on delegate_task (ADR-003 Phase 3a — supersedes and drops patch #5)`
- **touches:** `hermes_cli/model_routes.py` (new), `hermes_cli/config.py`, `gateway/model_router.py` (new), `gateway/run.py`, `gateway/slash_commands.py`, `tools/delegate_tool.py`, `run_agent.py`, `cli-config.yaml.example`, `tests/conftest.py`, `tests/hermes_cli/test_model_routes.py` (new), `tests/gateway/test_model_router.py` (new), `tests/tools/test_delegate.py`

### 35. audience-personas
- **branch:** `soju/patches/audience-personas`
- **origin:** `local-author`
- **upstream_pr:** _(none — generic and config-off-by-default, upstream candidate once soaked)_
- **state:** `local-only`
- **rationale:** Audience-mode persona injection: when `HERMES_HOME/personas/modes.yaml` exists, a mode is selected per session as a pure deterministic function of session-constant inputs (platform/chat_type/chat_id/chat_name/user_id) — first-match rules (string-or-list exact match, chat_name case-insensitive, missing key = wildcard, AND), non-owner guard applied after rules (non-empty `user_id` not in `owners[platform]` forces `guards.non_owner_mode`; missing owners entry fails safe; empty user_id skips), `default_mode` fallback — and the mode's persona markdown is injected as stable-tier slot #2 right after the SOUL.md/DEFAULT_AGENT_IDENTITY identity block, through the same threat scan + truncation cap as SOUL.md. With no modes.yaml the build is byte-identical (strict no-op); all failure paths degrade to the no-op at DEBUG. Cache correctness: an `AudienceMode: <mode>` line joins the volatile tail only while active, and `_stored_prompt_matches_runtime` recomputes the expected mode via a cheap mode-only resolver (both-absent passes → pre-deploy stored prompts stay valid; mismatch/one-sided rebuilds exactly once per session). SOUL.md plumbing parity: `personas/` in profile clone + default-export include set, `hermes_config_mod` threat pattern extended to `.hermes/personas/` paths. The `artifact_register` section of modes.yaml is a tone-gate plugin contract, deliberately not consumed by core.
- **commits:** (stacked on `soju/patches/model-routing` tip)
  - `acc7edd43 feat(prompt): audience-mode persona injection from personas/modes.yaml`
- **touches:** `agent/audience_persona.py` (new), `agent/system_prompt.py`, `agent/conversation_loop.py`, `run_agent.py`, `hermes_cli/profiles.py`, `tools/threat_patterns.py`, `tests/agent/test_audience_persona.py` (new)

### 36. model-switch-provider-dedupe
- **branch:** `soju/patches/model-switch-provider-dedupe`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate; D1 finding from ADR-003 routing rollout)_
- **state:** `local-only`
- **rationale:** `/model` typed-model routing built duplicate provider views for the same configured provider, so switching by bare model name could land on a stale self-duplicate entry. Dedupe the view list before selection.
- **commit:** `c4c4398c0 fix(model_switch): dedupe self-duplicate provider views in typed-model routing`
- **touches:** `hermes_cli/model_switch.py`, `tests/hermes_cli/test_model_switch_configured_provider_routing.py`

### 37. memory-phase0
- **branch:** `soju/patches/memory-phase0`
- **origin:** `local-author`
- **upstream_pr:** _(none — ADR-004 dogfood; revisit after shadow soak)_
- **state:** `local-only`
- **rationale:** ADR-004 Phase 0 memory-redesign plumbing: `pending/` WAL (durable per-turn buffer for external memory sync), L0-mirror local evidence journal for outgoing memory payloads, `_memory_ingest_disabled` per-agent flag (read-only memory forks), MEMORY.md/USER.md char-cap defaults unified on the config SoT. Journal review fixes: atomic appends, marker-only boundary mirroring, per-directory startup scan, directories pinned at construction.
- **commits:** (stacked on `soju/patches/model-switch-provider-dedupe` era stack tip)
  - `4632195d7 feat(memory): _memory_ingest_disabled per-agent flag — read-only memory forks (ADR-004 Phase 0)`
  - `3d7c28aec feat(memory): pending/ WAL — durable per-turn buffer for external memory sync (ADR-004 Phase 0)`
  - `daa2c8a0d feat(memory): L0-mirror — local evidence journal for outgoing memory payloads (ADR-004 Phase 0)`
  - `e37571712 fix(memory): unify MEMORY.md/USER.md char-cap defaults on config SoT (ADR-004 Phase 0)`
  - `622fa9813 fix(memory): pin journal directories at construction, not at write time`
  - `df56c9249 fix(memory): journal review fixes — atomic appends, marker-only boundary mirroring, per-directory startup scan`
- **touches:** `agent/memory_journal.py` (new), `agent/agent_init.py`, `agent/agent_runtime_helpers.py`, `agent/background_review.py`, `agent/conversation_compression.py`, `agent/memory_manager.py`, `agent/tool_executor.py`, `agent/turn_context.py`, `run_agent.py`, `tools/memory_tool.py`, `tools/delegate_tool.py`, `cli-config.yaml.example`, `tests/agent/test_memory_{ingest_disabled,l0_mirror,pending_wal}.py` (new), docs/website memory pages

### 38. memory-phase1
- **branch:** `soju/patches/memory-phase1`
- **origin:** `local-author`
- **upstream_pr:** _(none — ADR-004 dogfood)_
- **state:** `local-only`
- **rationale:** ADR-004 Phase 1 notes tier: `NotesStore` declarative notes under the citation contract, `notes_write`/`notes_read`/`memory_propose` tool family, two-step token contract with grounded admission and gated backfill seam. Review fixes close secret-scrub bypasses, add real quote grounding, non-destructive supersede, write-gate parity.
- **commits:** (stacked on `soju/patches/memory-phase0`)
  - `5dd082dd8 feat(memory): NotesStore — declarative notes tier under the citation contract (ADR-004 Phase 1)`
  - `37ffc440a feat(memory): notes write pipeline — two-step token contract, grounded admission, gated backfill seam (ADR-004 §③)`
  - `54aa639fb feat(memory): notes tool family — notes_write/notes_read/memory_propose wiring + prompt guidance (ADR-004 Phase 1)`
  - `bc8b28c9e fix(memory): notes review fixes — close the secret-scrub bypasses, real quote grounding, non-destructive supersede, write gate parity (ADR-004 Phase 1)`
- **touches:** `agent/notes_store.py` (new), `agent/memory_pipeline.py` (new), `tools/notes_tool.py` (new), `agent/agent_runtime_helpers.py`, `agent/memory_journal.py`, `agent/memory_manager.py`, `agent/prompt_builder.py`, `agent/system_prompt.py`, `agent/tool_executor.py`, `tools/memory_tool.py`, `tools/delegate_tool.py`, `toolsets.py`, `tests/agent/test_{memory_pipeline,notes_store}.py` (new), `tests/tools/test_notes_tool.py` (new)

### 39. memory-phase2-taint
- **branch:** `soju/patches/memory-phase2-taint`
- **origin:** `local-author`
- **upstream_pr:** _(none — ADR-004 dogfood)_
- **state:** `local-only`
- **rationale:** ADR-004 §① Phase 2 origin-taint machinery: injected-span registry, WAL/mirror span tagging, quote-taint enforcement; spans registered at the prefetch and memory-tool result sites. Review fixes: registry singleton hygiene, WAL/mirror tag coverage, floor-not-round registration timestamps.
- **commits:** (stacked on `soju/patches/memory-phase1`)
  - `27c798a19 feat(memory): origin-taint machinery — injected-span registry, WAL/mirror span tagging, quote-taint enforcement (ADR-004 §① Phase 2)`
  - `d8ce068be feat(memory): register injected memory spans at the prefetch and memory-tool result sites (ADR-004 §① Phase 2)`
  - `923d20399 fix(memory): origin-taint review fixes — registry singleton hygiene, WAL/mirror tag coverage, floor-not-round registration ts (ADR-004 Phase 2)`
- **touches:** `agent/memory_taint.py` (new), `agent/agent_runtime_helpers.py`, `agent/memory_journal.py`, `agent/memory_manager.py`, `agent/memory_pipeline.py`, `agent/tool_executor.py`, `agent/turn_context.py`, `run_agent.py`, `tests/agent/test_memory_taint.py` (new), `tests/agent/test_memory_{ingest_disabled,pending_wal}.py`

### 40. memory-phase2-curator
- **branch:** `soju/patches/memory-phase2-curator`
- **origin:** `local-author`
- **upstream_pr:** _(none — ADR-004 dogfood; shadow-mode observation ongoing)_
- **state:** `local-only`
- **rationale:** ADR-004 Phase 2 ingest curator: fork recipe, verdict schema, shadow ledger, watermark; curator triggers + `curator_verdict` dispatch wiring. Review fixes: seam scrub+grounding, provenance validation, cross-lane taint interface. Runs shadow-only until the cutover gate.
- **commits:** (stacked on `soju/patches/memory-phase1`)
  - `ceef15701 feat(memory): ingest curator core — fork recipe, verdict schema, shadow ledger, watermark (ADR-004 Phase 2)`
  - `6a60d22a2 feat(memory): ingest curator triggers + curator_verdict dispatch wiring (ADR-004 Phase 2)`
  - `ab9b51bac test(memory): ingest curator Phase-2 suite — shadow invariant, fork isolation, triggers, caps (ADR-004)`
  - `d596a0b07 fix(memory): ingest curator review fixes — seam scrub+grounding, provenance validation, cross-lane taint interface (ADR-004 Phase 2)`
- **touches:** `agent/ingest_curator.py` (new), `agent/agent_runtime_helpers.py`, `agent/background_review.py`, `agent/codex_runtime.py`, `agent/conversation_compression.py`, `agent/memory_journal.py`, `agent/memory_manager.py`, `agent/memory_pipeline.py`, `agent/tool_executor.py`, `agent/turn_finalizer.py`, `hermes_cli/config.py`, `run_agent.py`, `tests/agent/test_ingest_curator.py` (new)

### 41. cron-secret-scope-env-fallback
- **branch:** `soju/patches/cron-secret-scope-env-fallback`
- **origin:** `local-author`
- **upstream_pr:** _(pending — being opened; upstream regression fdab380a1 × Workstream A)_
- **state:** `pending-upstream`
- **rationale:** Upstream fdab380a1 wraps every cron job in a `<home>/.env` secret scope regardless of deployment mode, and `get_secret()` treats any installed scope as authoritative. In single-profile deployments where provider keys live only in the process environment (systemd `Environment=`, `pass-cli run`/`op run` wrappers, shell exports) every cron credential read resolved empty → OpenAI client built with the `no-key-required` placeholder → every scheduled agent job 401s while interactive turns keep working (claw: vooy 모닝 브리핑 broke daily since 2026-07-07). Scope-miss reads now fall through to `os.environ` when multiplexing is OFF; multiplexed scopes stay authoritative (fail-closed semantics unchanged).
- **commit:** `6d2e87ad8 fix(secrets): fall back to os.environ on scope miss when multiplexing is off`
- **touches:** `agent/secret_scope.py`, `tests/agent/test_secret_scope.py`

## State Vocabulary

| state | meaning | when |
|-------|---------|------|
| `local-only` | Authored by us, not upstreamed. | Personal preferences, secrets handling, host-specific tweaks. |
| `pending-upstream` | Cherry-picked from an open upstream PR. | Use upstream's fix before merge lands. Auto-dropped on merge. |
| `merged-upstream` | Equivalent fix is in current base. Should be deleted from manifest. | Transient state — cleanup pass after sync. |
| `vendored` | We diverged from the upstream version; cannot auto-drop. | Rare. Documented per-case. |

## Commit Trailers (mandatory on every patch commit)

```
Origin: local-author | cherry-pick:<sha> | upstream-pr:<N>
Upstream-PR: <number or "none">
Patch-State: local-only | pending-upstream | vendored
```

`bin/hermes-patches status` parses these to auto-update PATCHES.md against actual git history.

## Branch Layout

```
main                                  ← mirror of upstream/main, fast-forward only
soju/fork-policy                    ← management branch with manifest + scripts (not applied)
soju/patches/runtime-control          ← runtime topic, agent runtime tools + durable session labels (two commits)
soju/patches/memory-write-reason-gate ← runtime topic, memory add/replace suitability reason guardrail
soju/patches/todo-progress-display    ← runtime topic, gateway todo progress status rendering
soju/patches/discord-table-codeblocks  ← runtime topic, Discord markdown tables as fenced ASCII codeblocks
soju/patches/delegate-per-task-model   ← runtime topic, per-call/per-task subagent model/provider routing
soju/patches/background-review-guardrails ← runtime topic, calibrated self-improvement review guardrails
soju/patches/runtime-state-session-split ← runtime topic, scoped runtime state across compression splits
soju/patches/runtime-route-awareness   ← runtime-control child, CurrentRuntime + DesiredRoute prompt block
soju/patches/lsp-idle-reaper        ← runtime topic, pending upstream PR #36892 LSP idle reaper
soju/patches/aux-runtime-context    ← runtime topic, thread-local auxiliary runtime routing state
soju/patches/gateway-max-iterations-config-authority ← runtime topic, config.yaml max_turns beats stale .env HERMES_MAX_ITERATIONS
soju/patches/strict-chat-reasoning-details ← runtime topic, strip provider replay fields from strict chat_completions wire payloads
soju/patches/discord-home-autothread-fix ← runtime topic, restore Discord home-channel auto-thread creation
soju/patches/runtime-override-rehydrate-credentials ← runtime-control child, atomic restart rehydration for persisted runtime routes
soju/patches/turn-waterfall-tracing ← runtime topic, HERMES_TURN_TRACE per-turn waterfall spans + JSONL sink + renderer
soju/patches/tool-delay-removal ← runtime topic (stacked on turn-waterfall-tracing), inter-tool sleep removed entirely
soju/patches/prompt-cache-stability ← runtime topic (stacked on turn-waterfall-tracing), api_content sidecar for byte-stable prompt-cache replay
soju/patches/prompt-tail-freeze ← runtime topic (stacked on prompt-cache-stability + route-awareness tips), byte-stable gateway system prompts
soju/patches/request-client-reuse ← runtime topic (stacked on prompt-tail-freeze), reuse OpenAI wire client across sequential calls
soju/patches/async-token-accounting ← runtime topic (stacked on prompt-tail-freeze), token accounting off the turn thread
soju/patches/gateway-persist-trim ← runtime topic (stacked on prompt-tail-freeze), single-row routing UPSERT fast path
soju/patches/gateway-worker-pool ← runtime topic (stacked on prompt-tail-freeze), agent-turn pool 10→24, config gateway.max_workers
soju/patches/conn-error-fail-fast ← runtime topic (stacked on prompt-tail-freeze), instant transport-failure streak fail-fast
soju/patches/background-first-waits ← runtime topic (stacked on prompt-tail-freeze), chained process waits escalate to notify_on_complete + end-turn
soju/patches/llm-activity-recap ← runtime topic (stacked on prompt-tail-freeze), aux-LLM one-line recap heartbeat (display.long_running_notifications: recap)
soju/patches/config-knob-bridges ← runtime topic (stacked on prompt-tail-freeze), config.yaml authority for fork knobs (process_wait_cap, fast_conn_fail_limit)
soju/patches/request-client-reuse ← runtime topic (stacked on prompt-tail-freeze), per-request wire client reuse
soju/patches/async-token-accounting ← runtime topic (stacked on prompt-tail-freeze), token accounting off the turn thread
soju/patches/gateway-persist-trim ← runtime topic (stacked on prompt-tail-freeze), single-row routing UPSERT fast path
soju/patches/gateway-worker-pool ← runtime topic (stacked on prompt-tail-freeze), agent-turn pool 10→24, config gateway.max_workers
soju/patches/conn-error-fail-fast ← runtime topic (stacked on prompt-tail-freeze), instant transport-failure streak fail-fast
soju/patches/background-first-waits ← runtime topic (stacked on prompt-tail-freeze), chained process waits escalate to notify_on_complete + end-turn
soju/patches/llm-activity-recap ← runtime topic (stacked on prompt-tail-freeze), aux-LLM one-line recap heartbeat (display.long_running_notifications: recap)
soju/patches/config-knob-bridges ← runtime topic (stacked on prompt-tail-freeze), config.yaml authority for fork knobs (process_wait_cap, fast_conn_fail_limit)
soju/patches/hook-prepend-command-safety ← runtime topic (stacked on durable-turns), pre_gateway_dispatch prepends dropped on slash commands
soju/patches/slow-tool-perf-advisor ← runtime topic, advisory [perf-advisor] line appended to slow antipattern terminal results
soju/patches/session-db-read-path-split ← runtime topic, per-thread read-only connections for recall reads (convoy fix)
soju/patches/fts5-cjk-bigram-index ← runtime topic (stacked on read-path-split), cjk_unicode61 bigram FTS5 index replaces trigram+LIKE
soju/patches/search-slow-query-log ← runtime topic (stacked on fts5-cjk-bigram-index), slow session-search log with path attribution
soju/patches/fts-v2-config-authority ← runtime topic (stacked on search-slow-query-log), config.yaml authority + default-on + v1 retirement
soju/patches/model-routing ← runtime topic, ADR-003 model routing core (catalog + health-aware resolver + dynamic router + route-enum delegation)
soju/patches/audience-personas ← runtime topic (stacked on model-routing), audience-mode persona injection from personas/modes.yaml
soju/production                       ← rebuilt: base_commit + runtime patches only
                                         NEVER hand-edit. Always run `bin/hermes-patches rebuild` from `soju/fork-policy`.
```

## Operating Procedures

See `DECISIONS.md` ADR-001 for the full policy. Short forms:

```bash
# Add a new patch
bin/hermes-patches add <name> [--origin local-author|cherry-pick:SHA|upstream-pr:N]

# Rebuild production from base + patches
bin/hermes-patches rebuild

# Pull latest upstream and rebase all patches on new base
bin/hermes-patches sync upstream/main

# Show patch states (open PR? merged? conflicts on rebase?)
bin/hermes-patches status

# Roll the live host venv back to pure upstream (rollback)
git -C ~/.hermes/hermes-agent checkout main && bin/hermes-venv-rebuild
```
