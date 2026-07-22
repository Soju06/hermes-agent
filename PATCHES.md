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
base_commit:   3ef6bbd201263d354fd83ec55b3c306ded2eb72a
base_tag:      v2026.7.20
base_describe: v2026.7.20
pinned_at:    2026-07-22
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
  - `c8b4f7877 feat(runtime): agent-callable model_switch / model_status (session-only)`
  - `bdae1b651 fix(runtime): align runtime tool dispatch ownership`
  - `39bd91af5 feat(runtime): route-enum self model switching (ADR-003 Phase 3b)`
- **touches:** `agent/agent_init.py`, `agent/agent_runtime_helpers.py`, `agent/runtime_control.py`, `agent/tool_dispatch_helpers.py`, `agent/tool_executor.py`, `gateway/run.py`, `gateway/session.py`, `hermes_cli/plugins.py`, `hermes_cli/runtime_provider.py`, `model_tools.py`, `tests/gateway/test_pre_gateway_dispatch.py`, `tests/gateway/test_session.py`, `tests/gateway/test_session_model_override_routing.py`, `tests/hermes_cli/test_plugins.py`, `tests/hermes_cli/test_runtime_provider_resolution.py`, `tests/run_agent/test_pre_tool_session_id.py`, `tests/run_agent/test_run_agent.py`, `tests/run_agent/test_runtime_control.py`, `tests/test_model_tools.py`, `tests/tools/test_runtime_control_tool_schema.py`, `tools/runtime_control_tool.py`, `toolsets.py`
- **v2026.7.20-sync:** non-dict guard in `_rehydrate_session_model_override`; `provider_label` assertions aligned in upstream tests; explicit reasoning still wins over upstream's per-model reasoning re-resolution on switch.

### 2. memory-write-reason-gate
- **branch:** `soju/patches/memory-write-reason-gate`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood memory hygiene)_
- **state:** `local-only`
- **rationale:** Memory `add`/`replace` tool calls require an explicit suitability reason explaining why USER/MEMORY is the right store rather than a skill, Graphiti, or session history. The reason is a guardrail only and is not persisted with the entry.
- **commits:**
  - `694e8447c feat(memory): require write reason for memory updates`
  - `3b8e4b5a8 test(memory): pass write reason in null-target dispatcher test`
- **touches:** `agent/agent_runtime_helpers.py`, `agent/tool_executor.py`, `tools/memory_tool.py`, `tests/tools/test_memory_tool.py`, `tests/tools/test_memory_tool_schema.py`

### 3. todo-progress-display
- **branch:** `soju/patches/todo-progress-display`
- **origin:** `local-author`
- **upstream_pr:** _(none — Discord/gateway dogfood UX)_
- **state:** `local-only`
- **rationale:** Gateway progress bubbles should show todo item statuses after the todo tool completes, not only a count like `planning N task(s)`. Also flush throttled progress edits when the final queued event has no following tool event.
- **commit:** `ccc667f8a feat(gateway): show todo progress details`
- **touches:** `agent/display.py`, `gateway/run.py`, `tests/agent/test_display.py`, `tests/gateway/test_run_progress_topics.py`

### 4. discord-table-codeblocks
- **branch:** `soju/patches/discord-table-codeblocks`
- **origin:** `local-author`
- **upstream_pr:** _(none — Discord/gateway dogfood UX)_
- **state:** `local-only`
- **rationale:** Discord does not render GitHub-flavored markdown pipe tables. Convert detected outbound pipe tables to fenced box-drawing ASCII tables so table responses remain readable, while preserving existing fenced code blocks, CJK wide-character alignment, and long-table chunk codeblock boundaries.
- **commit:** `18f445b05 feat(discord): render markdown tables as codeblocks`
- **touches:** `plugins/platforms/discord/adapter.py`, `tests/gateway/test_discord_send.py`
- **v2026.7.20-sync:** upstream `test_table_converted_to_bullets` re-expected to the fenced-ASCII semantics (pre-existing gap — it was red on the old stack too).

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
- **commit:** `d6c1a0483 fix: calibrate background self-improvement review`
- **touches:** `agent/background_review.py`, `tests/run_agent/test_review_prompt_class_first.py`

### 7. runtime-state-session-split
- **branch:** `soju/patches/runtime-state-session-split`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood runtime gate correctness)_
- **state:** `local-only`
- **rationale:** Runtime hard gates must see the active session's actual model/provider after context compression rotates the session id. Compression split republishes runtime state for the new session while retaining the old session as task/parent scope, preventing fallback to stale cross-session runtime state.
- **commit:** `88d5fc8b5 fix: keep runtime state scoped across compression splits`
- **touches:** `agent/conversation_compression.py`, `tests/agent/test_runtime_state_session_split.py`

### 8. runtime-route-awareness
- **branch:** `soju/patches/runtime-route-awareness`
- **stacked-on:** `soju/patches/runtime-control`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood runtime routing correctness)_
- **state:** `local-only`
- **rationale:** Inject an API-call-time Runtime/Route State block so the agent sees live CurrentRuntime plus current-turn DesiredRoute without calling `model_status`. Trusted pre-dispatch `runtime_override` metadata is normalized into one-shot route state for the routed gateway turn, preserving stale-route protection while leaving post-tool rerouting and NEED_CONTEXT scout mode for later phases.
- **commits:**
  - `27dd6aebc feat(runtime): inject runtime route awareness prompt`
  - `7931c4188 test(routing): expect the Runtime/Route block in post-compression system prompts`
- **touches:** `agent/chat_completion_helpers.py`, `agent/conversation_loop.py`, `agent/system_prompt.py`, `docs/runtime-route-awareness.md`, `gateway/run.py`, `tests/agent/test_runtime_route_prompt.py`, `tests/gateway/test_pre_gateway_dispatch.py`
- **v2026.7.20-sync:** `_pending_runtime_route_states` registered in upstream `_CONVERSATION_SCOPED_STATE` funnel; failover system message now composed via `compose_effective_system_prompt` so the Runtime/Route block refreshes on provider failover.
- **note (07-22):** pins fork semantics onto upstream `test_413_compression` exact-equality assertion (Runtime/Route block present in post-compression system prompts; was red on the old stack too).

### 9. lsp-idle-reaper
- **branch:** `soju/patches/lsp-idle-reaper`
- **origin:** `upstream-pr:36892`
- **upstream_pr:** `36892`
- **state:** `pending-upstream`
- **rationale:** Reap idle LSP clients after `lsp.idle_timeout` so long-running gateways do not keep TypeScript/pyright/gopls/rust-analyzer subprocesses alive for the full process lifetime. `idle_timeout <= 0` disables reaping; stale clients respawn on the next relevant file operation.
- **commit:** `942dfd581 fix(lsp): reap idle language-server clients`
- **touches:** `agent/lsp/manager.py`, `tests/agent/lsp/test_service.py`, `website/docs/user-guide/features/lsp.md`

### 10. aux-runtime-context — MERGED UPSTREAM (2026-07-22)
Upstream `fdc6c32d7`/`73057ed16`/`c201b72f3` replaced process-global runtime-main state with a
`contextvars.ContextVar` (`_RUNTIME_MAIN_CONTEXT`) plus per-turn Token scoping — a strict superset of
this patch's `threading.local` isolation (also covers asyncio tasks). Dropped at the v2026.7.20 base
bump; old tip archived at `archive/pre-v20260722/aux-runtime-context`.

### 11. gateway-max-iterations-config-authority
- **branch:** `soju/patches/gateway-max-iterations-config-authority`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood gateway budget config correctness)_
- **state:** `local-only`
- **rationale:** Gateway agent turns must resolve `max_iterations` from config.yaml `agent.max_turns` as the source of truth before falling back to `HERMES_MAX_ITERATIONS`. A stale `.env` value such as `HERMES_MAX_ITERATIONS=90` must not override `agent.max_turns: 300` or cause intermittent `Iteration budget exhausted (90/90)` in concurrent Discord sessions.
- **commit:** `25a72881c fix(gateway): keep max iterations config authoritative`
- **touches:** `gateway/run.py`, `tests/gateway/test_runtime_env_reload_config_authority.py`

### 12. strict-chat-reasoning-details
- **branch:** `soju/patches/strict-chat-reasoning-details`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood strict OpenAI-compatible provider replay fix)_
- **state:** `local-only`
- **rationale:** Strict OpenAI-compatible Chat Completions providers such as GLM Vooy reject non-standard assistant message replay fields (`reasoning`, `reasoning_details`) with `Extra inputs are not permitted`. Preserve those fields in session history for provider continuity, but strip them from the outbound chat_completions wire payload so mixed-provider Discord sessions do not get stuck in repeat HTTP 400 retries.
- **commit:** `dbe818ac4 fix(chat): strip reasoning replay fields for strict chat completions`
- **touches:** `agent/transports/chat_completions.py`, `tests/run_agent/test_strict_api_validation.py`

### 13. discord-home-autothread-fix
- **branch:** `soju/patches/discord-home-autothread-fix`
- **origin:** `local-author`
- **upstream_pr:** _(none — Discord home-channel dogfood routing fix)_
- **state:** `local-only`
- **rationale:** Discord home-channel messages should still auto-create thread conversations when channel controls disable broad channel auto-threading. Restore the home-channel path while keeping explicit channel-control disable behavior available for non-home channels.
- **commit:** `1e6940e4b fix(discord): restore home channel auto-threading`
- **touches:** `gateway/config.py`, `plugins/platforms/discord/adapter.py`, `tests/gateway/test_discord_channel_controls.py`

### 14. runtime-override-rehydrate-credentials
- **branch:** `soju/patches/runtime-override-rehydrate-credentials`
- **stacked-on:** `soju/patches/runtime-route-awareness`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood runtime-control follow-up fix)_
- **state:** `local-only`
- **rationale:** Upstream owns live `switch_model` endpoint rollback and provider-header reapplication; this patch is limited to gateway restart/persistence. A persisted runtime route is eligible only when both `runtime_model` and `runtime_provider` labels exist, then resolves once with `target_model` into one authoritative provider bundle (normalized provider, endpoint, API mode, keyed or keyless credential, command/args, and credential pool). Incomplete or unresolved labels stay durable but dormant while the turn falls back wholly to coherent defaults. Persisted reasoning rehydrates independently, and the latest `/model` or runtime-route writer clears the other durable model-route store without clearing reasoning. State DB coverage verifies DB-only restart and that no secret bundle fields are persisted.
- **commit:** `925582fae fix(gateway): re-resolve persisted runtime override bundle`
- **touches:** `gateway/run.py`, `gateway/session.py`, `tests/gateway/test_runtime_override_restart_rehydration.py`, `tests/gateway/test_runtime_override_state_db_restart.py`, `tests/gateway/test_runtime_override_store_precedence.py`, `tests/gateway/test_session_model_override_routing.py`

### 15. turn-waterfall-tracing
- **branch:** `soju/patches/turn-waterfall-tracing`
- **origin:** `local-author`
- **upstream_pr:** _(none — perf instrumentation for local bottleneck hunt)_
- **state:** `local-only`
- **rationale:** Per-turn waterfall tracing to attribute end-to-end turn latency (observed ~30% slower than a minimal agent on the same LLM). New `agent/turn_trace.py` collects wall-clock spans across the whole turn lifecycle — gateway ingest/session-resolve/transcript-load/agent-setup, prologue children (system-prompt restore, early persist, compression preflight, pre-LLM hook, memory prefetch), per-iteration context assembly/request setup/`llm.call` (TTFT + failed attempts) /accounting, tool batches incl. the inter-tool delay sleep, verify gates, finalize children, gateway persist, transport delivery — and emits one JSONL record per turn to `~/.hermes/logs/turn_traces.jsonl`. `agent/turn_trace_render.py` renders terminal/HTML waterfalls and cross-turn p50/p95 summaries with a model-time vs hermes-overhead split. Gated by `HERMES_TURN_TRACE=1` (default off = no-op); tracing failures can never break a turn.
- **commits:**
  - `804def15b feat(telemetry): per-turn waterfall tracing spans`
  - `3cac2e002 fix(telemetry): carry turn trace across pre-dispatch event replacement`
  - `db2643a2c feat(telemetry): request prefix fingerprints for cache-break diffing`
  - `66dd32d70 fix(telemetry): fingerprint Responses-API payloads too`
  - `e8f34b85c feat(telemetry): 4KB-chunk hashes locate in-message cache breaks`
  - `3544d7b8a feat(telemetry): attribute rest-field changes to specific keys`
  - `08672771f feat(telemetry): HERMES_TURN_TRACE_SYS_TAIL captures system-prompt tail`
- **touches:** `agent/turn_trace.py` _(new)_, `agent/turn_trace_render.py` _(new)_, `tests/agent/test_turn_trace.py` _(new)_, `agent/chat_completion_helpers.py`, `agent/conversation_loop.py`, `agent/tool_executor.py`, `agent/turn_context.py`, `agent/turn_finalizer.py`, `gateway/platforms/base.py`, `gateway/run.py`, `plugins/platforms/telegram/adapter.py`, `run_agent.py`
- **v2026.7.20-sync:** tools.batch span adapted to upstream segmented dispatch (mode=`segmented` + `segments=<n>` tag); `prologue.persist_early` split into `persist_early` + new `prologue.persist_user_turn` following upstream's persist-site split (cross-version p50 continuity caveat).

### 16. tool-delay-removal
- **branch:** `soju/patches/tool-delay-removal`
- **stacked-on:** `soju/patches/turn-waterfall-tracing`
- **origin:** `local-author`
- **upstream_pr:** [#64172](https://github.com/NousResearch/hermes-agent/pull/64172) _(being rewritten from env-knob to full removal)_
- **state:** `local-only`
- **rationale:** Remove the inter-tool 1.0s sleep entirely (supersedes the earlier env-knob patch). The delay has been present verbatim since upstream's initial commit with no documented rationale; it sleeps between LOCAL tool executions (the next LLM request only goes out after the whole batch), so it rate-limits nothing — pure (N-1)s dead time per multi-tool turn. Also removes the `tool_delay` parameter plumbing, dead `agent.tool_delay = 0` test remnants, and the now-dead `tools.delay` trace span from the renderer.
- **commit:** `3855c76ed refactor(agent): remove the inter-tool delay entirely`
- **touches:** `agent/tool_executor.py`, `agent/agent_init.py`, `run_agent.py`, `agent/turn_trace_render.py`, `tests/` _(dead assignments removed)_

### 17. prompt-cache-stability — MERGED UPSTREAM (2026-07-22)
Upstreamed by the fork author as `7b3dcee92` (*api_content sidecar*, follow-ups `39efad89a`/`c7035ef25`)
— landed as `agent/turn_context.py` + hooks; changed-line hashes identical for 11/13 files. Dropped at
the v2026.7.20 base bump; old tip archived at `archive/pre-v20260722/prompt-cache-stability`.

### 18. prompt-tail-freeze
- **branch:** `soju/patches/prompt-tail-freeze`
- **stacked-on:** `soju/patches/runtime-route-awareness`
- **origin:** `local-author`
- **upstream_pr:** _(~70% merged upstream as `c0c76a471` byte-stable session context prompts, 2026-07-14)_
- **state:** `local-only`
- **rationale:** Fork-only residue after the upstream merge of the original patch: (1) `agent/system_prompt.py` byte-stability for the Runtime/Route block — key-tuple cache, permanently static DesiredRoute line, always-emitted `reasoning_source=`, `format_routing_directive` one-shot delivery riding upstream's turn-sidecar channel; (2) `gateway/run.py` same-route re-selection eviction guard + `include_reasoning` persistence for runtime overrides; (3) fork-only test deltas rebased onto upstream's `test_prompt_tail_freeze.py`/`test_runtime_route_prompt.py`. The pinned session-context prompt, sidecar note staging/consume, VC note, multimodal fallback and sorted connected platforms are upstream now and are NOT re-added. Expiry-path inline sidecar-note pop dropped in favor of upstream's `_clear_conversation_scope` funnel.
- **commit:** `80534418d feat(cache): prompt-tail freeze — byte-stable gateway system prompts (patch #18)`
- **touches:** `agent/system_prompt.py`, `gateway/run.py`, `tests/gateway/test_prompt_tail_freeze.py`, `tests/agent/test_runtime_route_prompt.py`

### 19. request-client-reuse
- **branch:** `soju/patches/request-client-reuse`
- **origin:** `local-author`
- **upstream_pr:** [#64170](https://github.com/NousResearch/hermes-agent/pull/64170)
- **state:** `local-only`
- **rationale:** A fresh OpenAI wire client (new httpx pool, TCP+TLS handshake) was built and torn down for EVERY LLM call (`llm.client_create` p50 19.2ms / p95 35.5ms, ~5 calls/turn, 13.5%% of pooled overhead self-time). Now one reusable client is cached per agent keyed by the effective request kwargs (incl. resolved headers): reuse on identical kwargs, rebuild on credential rotation/failover/vision-header variant, poison-on-abort so a stranger-thread socket shutdown (#29507) can never hand a dead client to the next call, real close on kwargs change/agent teardown/gateway eviction. Interrupt-break SSE leak plugged (stream closed on early break); holder-read+abort made atomic at all three abort sites (streaming, non-streaming, cron inline); codex stream close-failure poisons the slot.
- **commits:**
  - `de14f0fe6 perf(llm): reuse the per-request OpenAI wire client across sequential calls`
  - `da5ae8189 fix(llm): plug interrupt-break connection leak; make stranger abort atomic`
  - `ea750fd19 fix(llm): extend atomic holder-abort to cron inline path; poison on codex stream close failure`
  - `5d63d98a8 fixup(request-client-reuse): adapt to v2026.7.20 upstream`
- **touches:** `agent/chat_completion_helpers.py`, `agent/codex_runtime.py`, `run_agent.py`, `tests/agent/test_request_client_reuse.py`, `tests/run_agent/test_openai_client_lifecycle.py`, `tests/run_agent/test_request_client_reuse_abort_races.py`
- **v2026.7.20-sync:** atomic holder-abort extended to upstream's request-local anthropic client kind (read `request_client_kind` under `request_client_lock`); reuse cache stays OpenAI-wire-only; superseded-stream break from the single-writer fence gets the same close+poison treatment.

### 20. async-token-accounting
- **branch:** `soju/patches/async-token-accounting`
- **origin:** `local-author`
- **upstream_pr:** [#64171](https://github.com/NousResearch/hermes-agent/pull/64171)
- **state:** `local-only`
- **rationale:** `update_token_counts` ran a synchronous sqlite UPDATE on the turn thread after every API call (`llm.accounting` p50 3.3ms / p95 70ms, historically 299ms into the cold 6.8GB state.db). Deltas now enqueue to a single-writer daemon thread that applies them in order with backlog coalescing; sync model/billing-route writers flush the queue first (happens-before preserved); drains at turn finalize, close(), and atexit; enqueue-after-close applies inline instead of dropping; readers needing exact values call flush() (cheap when empty).
- **commits:**
  - `914767afb perf(accounting): async token accounting — per-call deltas off the turn thread (patch #19)`
  - `118cde197 fix(accounting): close review gaps in the async token writer (patch #19 review)`
  - `37e34d74f fix(accounting): enqueue-after-close applies inline instead of dropping`
- **touches:** `agent/codex_runtime.py`, `agent/conversation_loop.py`, `agent/insights.py`, `hermes_state.py`, `run_agent.py`, `tests/agent/test_async_token_accounting.py`, `tests/run_agent/test_token_persistence_non_cli.py`
- **v2026.7.20-sync:** flush drain ordered before `note_turn_persisted` in both persist branches; upstream's synchronous `record_aux_model_usage` (eb6aa0360) left as-is — optional queue follow-up.

### 21. gateway-persist-trim
- **branch:** `soju/patches/gateway-persist-trim`
- **origin:** `local-author`
- **upstream_pr:** [#64169](https://github.com/NousResearch/hermes-agent/pull/64169)
- **state:** `local-only`
- **rationale:** The steady-state gateway turn bumps `updated_at`/`last_prompt_tokens` on ONE routing entry but paid the full index rewrite twice per turn — every entry re-serialized, DELETE+INSERT of every `gateway_routing` row, and a multi-MB sessions.json dump+fsync (~50ms p50 at ~1100 keys, inside the ~175ms/turn session_resolve+persist spans). Metadata-only saves now UPSERT the single row (<1ms) with a routing-generation guard against regressing a racing full snapshot; structural transitions (create/recover/reset/switch/prune, compression-tip heals) keep the full rewrite incl. the sessions.json mirror; peer fields snapshot under `_lock` (no torn rows); DB-less installs fall back to the full rewrite.
- **commit:** `415e453ed perf(gateway): single-row routing UPSERT fast path for metadata-only saves`
- **touches:** `gateway/session.py`, `tests/gateway/test_routing_save_fast_path.py`

### 22. gateway-worker-pool
- **branch:** `soju/patches/gateway-worker-pool`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate after burn-in)_
- **state:** `local-only`
- **rationale:** The gateway's shared agent-turn ThreadPoolExecutor was hardcoded to 10 workers; every agent turn holds one worker for its full duration, so a kanban batch of 8+ concurrent multi-hour marathon turns (300-iteration workers, blocking process waits) starved every other session for hours — observed 9.1h between a finished turn's finalize and its delivery, and users reporting sessions "quietly frozen". Default raised to 24 and exposed as config.yaml `gateway.max_workers` with `HERMES_GATEWAY_MAX_WORKERS` env fallback (clamped ≥ 2); workers are network-I/O-bound so the larger pool is cheap.
- **commit:** `c37eb2fa4 perf(gateway): size the agent-turn pool from config, default 24`
- **touches:** `gateway/run.py`, `tests/gateway/test_gateway_max_workers.py` _(new)_
- **v2026.7.20-sync:** coexists with upstream per-session turn lease (`19527db73`): the lease serializes per-session, the pool sizes total concurrency.

### 23. conn-error-fail-fast
- **branch:** `soju/patches/conn-error-fail-fast`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate after burn-in)_
- **state:** `local-only`
- **rationale:** Consecutive sub-2s transport failures (connection refused/reset before any bytes) mean the endpoint is down, not congested; with no fallback available the retry loop burned a dozen attempts with growing backoff (observed 13 attempts / 170-250s of user-visible silence per turn against a briefly-down codex-lb). Track the instant-failure streak on TurnRetryState and end the turn with an actionable error after `HERMES_FAST_CONN_FAIL_LIMIT` (default 3, 0 disables) once the fallback chain has had its chance; slow timeouts keep the full retry budget.
- **commit:** `429527fe7 fix(agent): fail fast on instant transport-failure streaks`
- **touches:** `agent/conversation_loop.py`, `agent/turn_retry_state.py`, `tests/agent/test_fast_transport_fail_fast.py` _(new)_

### 24. background-first-waits
- **branch:** `soju/patches/background-first-waits`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate after burn-in)_
- **state:** `local-only`
- **rationale:** Models foreground-polled long jobs with chained `process wait` calls (observed 4-5 consecutive 180s waits per turn — total user-facing silence while holding a gateway worker). Background-first: the first full block-wait stays a normal flow (180s window unchanged per user request), but from the SECOND consecutive timed-out wait on the same still-running process, `notify_on_complete` is auto-armed and the tool result instructs the model to end its turn with a summary — the completion re-enters the session as an event and the model explains the result there (the natural LLM recap). `HERMES_PROCESS_WAIT_CAP` tunes the quiet-wait allowance (default 1, 0 disables for completion-bound sessions like kanban workers). The process tool schema and terminal guidance now state the one-wait-per-process contract (one-time tools-hash cache bust on deploy, legitimate).
- **commit:** `dafa1ebab feat(tools): background-first process waits — escalate chained blocking waits`
- **touches:** `tools/process_registry.py`, `tools/terminal_tool.py`, `tests/tools/test_background_first_waits.py` _(new)_

### 25. llm-activity-recap
- **branch:** `soju/patches/llm-activity-recap`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate after burn-in)_
- **state:** `local-only`
- **rationale:** `display.long_running_notifications` gains a `recap` mode: instead of the terse "⏳ Working — N min — iteration i/max" heartbeat, the gateway asks the auxiliary LLM (compression rail, 8s timeout, ~80 tokens) for a one-line present-tense recap of what the agent is doing — goal + recent tool calls + current wait — in the conversation's language (Claude Code-style). Context-hash caching regenerates only on activity change; failures fall back to the terse heartbeat; in recap mode the bubble is deleted-and-resent (adapters with delete support) so it stays at the thread bottom instead of buried. Complements #24 background-first-waits: waits that should end the turn do, and turns that legitimately run long narrate themselves.
- **commits:**
  - `f4eba0d6e feat(gateway): LLM activity recap for long-running notifications`
  - `7b3bc7ab9 feat(gateway): tune activity recap context from live-session evaluation`
  - `bf8b8414f feat(gateway): recap speaks in the agent's own voice`
  - `a9d7b75fe feat(gateway): persona-definition voice fallback for fresh sessions`
- **touches:** `gateway/run.py`, `run_agent.py`, `tests/gateway/test_llm_activity_recap.py` _(new)_

### 26. config-knob-bridges
- **branch:** `soju/patches/config-knob-bridges`
- **stacked-on:** `soju/patches/gateway-max-iterations-config-authority`
- **origin:** `local-author`
- **upstream_pr:** _(none — fork knob plumbing)_
- **state:** `local-only`
- **rationale:** Fork knobs were env-only, breaking the house convention (config.yaml is authoritative; env is the cross-process carrier/override — see upstream PR #64298). Bridge `agent.process_wait_cap` → `HERMES_PROCESS_WAIT_CAP` (#24) and `agent.fast_conn_fail_limit` → `HERMES_FAST_CONN_FAIL_LIMIT` (#23) in both the startup export block and the per-turn reload bridge. Recap interval already had the upstream `agent.gateway_notify_interval` bridge.
- **commit:** `3ec9e55d7 feat(gateway): config.yaml bridges for fork knobs`
- **touches:** `gateway/run.py`, `tests/gateway/test_fork_knob_config_bridges.py` _(new)_
- **v2026.7.20-sync:** restacked on gateway-max-iterations-config-authority; fork knobs read the managed-scope-overlaid config, keeping admin-pinned values authoritative per turn.

### 27. durable-turns
- **branch:** `soju/patches/durable-turns`
- **stacked-on:** `soju/patches/llm-activity-recap`, `soju/patches/config-knob-bridges`
- **origin:** `local-author`
- **upstream_pr:** _(pending — branch `upstream-pr/durable-turns` pushed to fork, PR body drafted, awaiting owner approval to open)_
- **state:** `local-only`
- **rationale:** Restart recovery was a paper-over: an interrupted turn was abandoned and a synthetic empty user turn told the model to "skip any unfinished work" — the banner's "I'll try to resume where you left off" was implemented by nothing (live incident 2026-07-15, discord thread 1526457680527622247 reduced to "응 오빠, 여기 있어"). Durable turns make the in-flight turn a first-class durable record (`SessionEntry.active_turn`: turn_id/status/boot_id/resume_count) and re-enter the SAME turn on its persisted transcript after a restart: `run_conversation(resume_turn=True)` appends no user row, drops synthetic "Operation interrupted…" closers, completes unanswered tool_calls via existing orphan recovery (side effects = UNKNOWN, never re-executed), and delivers an already-composed final without another model call. Poison cap `HERMES_TURN_RESUME_MAX`/`agent.turn_resume_max` (default 2) abandons repeat offenders with an honest notice. Kill switch `HERMES_GATEWAY_TURN_RESUME`/`agent.gateway_turn_resume` restores legacy behavior. Banner reworded to match reality. See `DECISIONS.md` ADR-002.
- **commit:** `d84853741 feat(gateway): durable turns — same-turn resume across gateway restarts`
- **touches:** `agent/turn_resume.py` _(new)_, `agent/turn_context.py`, `agent/conversation_loop.py`, `agent/turn_finalizer.py`, `run_agent.py`, `gateway/run.py`, `gateway/session.py`, `hermes_cli/config.py`, `tests/agent/test_turn_resume.py` _(new)_, `tests/gateway/test_durable_turn_records.py` _(new)_, `tests/run_agent/test_resume_turn_loop.py` _(new)_, `tests/gateway/restart_test_helpers.py`, `tests/gateway/test_restart_resume_pending.py`
- **v2026.7.20-sync:** reconciled with upstream delivery-obligation ledger (`5854aad8b`): `_redeliver_pending_obligations` force-finishes `active_turn` records for claimed obligations, narrowing `_resume_composed_final` to the ledger-uncovered window; recovery-note bypass rewoven around module-scope `build_resume_recovery_note`.

### 28. hook-prepend-command-safety
- **branch:** `soju/patches/hook-prepend-command-safety`
- **stacked-on:** `soju/patches/durable-turns`, `soju/patches/runtime-control`
- **origin:** `local-author`
- **upstream_pr:** _(none — hardens the fork-only pre_gateway_dispatch prepend action from runtime-control)_
- **state:** `local-only`
- **rationale:** A `pre_gateway_dispatch` `{"action": "prepend"}` rewrote `event.text` before slash dispatch, and `is_command()`/`get_command()` key off `text.startswith("/")` — so any plugin prepend on a command message demoted it into plain chat that fell through to the agent. Live incident 2026-07-15: the inbox-matter-coordinator advisory rewrite (plugin 9ed17c4) prepends an `[INBOX_MATTER ...]` marker on matter-linked threads; `/model` there was answered by the agent's `model_status` tool instead of the interactive picker, and every slash command in those threads broke the same way. Fix: drop prepends (debug log) when the event is a slash command — advisory context is agent-facing and command handlers can't consume it. Plain-chat prepend behavior unchanged.
- **commit:** `4ee5607b3 fix(gateway): drop pre_gateway_dispatch prepends on slash commands`
- **touches:** `gateway/run.py`, `tests/gateway/test_pre_gateway_dispatch.py`
- **v2026.7.20-sync:** restacked — the guarded prepend action is fork-only (runtime-control), so the branch now merges runtime-control before the pick.

### 29. slow-tool-perf-advisor
- **branch:** `soju/patches/slow-tool-perf-advisor`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — candidate once soak data confirms hint uptake)_
- **state:** `local-only`
- **rationale:** Trace mining across 2,605 turns (2026-07-16) showed the dominant avoidable turn latency is agents routing heavy work through `terminal` in shapes with a cheap native equivalent: full-tree scans (`rglob`/`os.walk`/unpruned `find`/bare `grep -r` — 59+32+209 calls) over repos whose `node_modules` holds >1.7M files (observed 88s + 63s back-to-back scans of the same tree in one Discord turn), foreground `sleep` polling (364 calls), and multi-minute foreground jobs pinning session workers — while `search_files` (rg-backed, .gitignore-aware) answers in ~1s and background+`notify_on_complete` re-enters the session for free. Fix is a thin advisory-only shot mirroring the subdirectory-hints append: when a terminal call is slow AND matches an antipattern, append ONE `[perf-advisor]` line to the tool result at the moment the model just paid for the slow call. Kill switch `HERMES_PERF_ADVISOR=0`; thresholds `HERMES_PERF_ADVISOR_MIN_S` (10s), `HERMES_PERF_ADVISOR_FOREGROUND_S` (120s).
- **commit:** `3e2dcd963 feat(tools): slow-tool perf advisor — teach cheaper shapes on slow terminal results`
- **touches:** `tools/perf_advisor.py` (new), `agent/tool_executor.py`, `tests/test_perf_advisor.py` (new)

### 30. session-db-read-path-split
- **branch:** `soju/patches/session-db-read-path-split`
- **stacked-on:** `soju/patches/hook-prepend-command-safety`, `soju/patches/async-token-accounting`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — good candidate after soak; the shared-SessionDB convoy exists upstream too)_
- **state:** `local-only`
- **rationale:** The gateway hands ONE SessionDB to every agent (gateway/run.py `session_db=...`), so all recall/browse reads queued behind all writer flushes on `self._lock` — one Python lock in front of a WAL DB that natively supports concurrent readers. Measured production convoy (2026-07-16 trace analysis): a 0.23s FTS query stretched to 112s, a browse flush to 137s, while 6-8 concurrent turns flushed hundreds of tool results; session_search averaged 12.4s in-gateway vs 0.26s standalone. Fix: under WAL the seven read-only recall methods run on per-thread `mode=ro` connections via `_read_ctx()` with no lock; fresh read transactions per statement preserve read-your-committed-writes. Non-WAL (NFS DELETE fallback) or read-conn open failure falls back to the legacy locked path.
- **commit:** `68aea8f0d perf(state): read-path split — per-thread read-only connections for recall reads`
- **touches:** `hermes_state.py`, `tests/test_session_db_read_path_split.py` (new)
- **v2026.7.20-sync:** upstream-new `get_resume_conversations`/`export_session*` read paths deliberately left on `self._lock` (optional follow-up).

### 31. fts5-cjk-bigram-index
- **branch:** `soju/patches/fts5-cjk-bigram-index`
- **stacked-on:** `soju/patches/session-db-read-path-split`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — strong candidate; CJK search brokenness is upstream-wide)_
- **state:** `local-only`
- **rationale:** unicode61 indexes a CJK run as ONE token, so 2-char Korean terms could never match; hermes routed any query containing one to a 3-column LIKE full-table scan (3-6.4s CPU on the 6.8GB state.db) — the #1 base cost behind session_search's 12.4s production average, and the LIKE scans were also the convoy source amplified by the shared-lock issue fixed in #30. Adds a loadable FTS5 tokenizer `cjk_unicode61` (native/fts5_cjk, unicode61 wrapper emitting CJK character bigrams) + standalone 3-column `messages_fts_v2` whose single MATCH path replaces the unicode61/trigram/LIKE routing (prototype: 3.7s→83ms). Online migration via `scripts/fts_v2_migrate.py` (idempotent batched backfill, resume in state_meta); read cutover behind `HERMES_FTS_V2_READ=1`; self-heal drops v2 triggers when the tokenizer can't load so writes never fail. Update triggers scoped `AFTER UPDATE OF` content columns (v1 re-tokenized whole messages on flag flips).
- **commit:** `cb924069c feat(state): messages_fts_v2 — cjk_unicode61 bigram index replaces trigram+LIKE routing`
- **touches:** `hermes_state.py`, `native/fts5_cjk/*` (new), `scripts/fts_v2_migrate.py` (new), `tests/test_fts_v2_cjk.py` (new)

### 32. search-slow-query-log
- **branch:** `soju/patches/search-slow-query-log`
- **stacked-on:** `soju/patches/fts5-cjk-bigram-index`
- **origin:** `local-author`
- **upstream_pr:** _(none yet)_
- **state:** `local-only`
- **rationale:** The session_search latency investigation (2026-07-16) required turn-trace archaeology + workload replay to attribute 12.4s searches to LIKE full scans. One INFO line per slow search (threshold `HERMES_SEARCH_SLOW_MS`, default 1000ms) naming the routing path (fts_v2/fts5/trigram/like_scan), elapsed, rows, and query makes the next routing regression a journalctl grep. Wrapper around `_search_messages_impl`; zero behavior change.
- **commit:** `54d42af89 feat(state): slow-query log for session search with routing-path attribution`
- **touches:** `hermes_state.py`, `tests/test_search_slow_query_log.py` (new)

### 33. fts-v2-config-authority
- **branch:** `soju/patches/fts-v2-config-authority`
- **stacked-on:** `soju/patches/search-slow-query-log`
- **origin:** `local-author`
- **upstream_pr:** _(folds into the #31 upstream submission — sweeper requires the config.yaml surface)_
- **state:** `local-only`
- **rationale:** Config SoT for the FTS v2 knobs (`agent.fts_v2_read`, `agent.search_slow_ms` bridged at both gateway bridge sites, house pattern), read default promoted to ON behind a `state_meta fts_v2_ready` backfill-completion marker (a triggers-only/partial index is never served; migrate script sets the marker), fresh DBs with a loadable tokenizer are v2-native (no v1/trigram tables), and `scripts/fts_v1_drop.py` retires the six v1 triggers + two tables behind an integrity/rowcount preflight (~5.6GB logical). After the drop the off-flag is ignored — no fallback exists to select.
- **commit:** `4aaaf5009 feat(state): FTS v2 config authority, default-on reads, v1 retirement path`
- **touches:** `hermes_state.py`, `gateway/run.py`, `scripts/fts_v2_migrate.py`, `scripts/fts_v1_drop.py` (new), `tests/gateway/test_fts_v2_config_bridge.py` (new), `tests/test_fts_v2_cjk.py`, `tests/test_search_slow_query_log.py`
- **v2026.7.20-sync:** integrated with upstream write-path FTS self-heal (`9e1b1d753`): new `_fts_maintenance_tables()` extends `_FTS_TABLES` with `messages_fts_v2` when the tokenizer is loaded; optimize/rebuild loops cover v2; +2 pinning tests.

### 34. model-routing
- **branch:** `soju/patches/model-routing`
- **upstream_pr:** _(none — ADR-003 Phase 1; enum'd delegation aligns with upstream `delegation-model-routing` policy, natural round-2 candidate once Phases 2–3 prove it)_
- **origin:** `local-author`
- **state:** `local-only`
- **rationale:** ADR-003 Phases 1+2 — model routing SoT as a core subsystem. Phase 1: config-declared `model_routes:` catalog (per route: description, provider, model, reasoning_effort, accepted membership, ordered fallbacks), loader with startup cross-validation against `providers:`, and a health-aware resolver (`resolve_route`) that walks default→fallbacks with TTL-cached fail-open provider probes ported from skill-gate's runtime_catalog (401/403 healthy, credit-sniffed 400, 402/429/5xx unhealthy; config-first kill switch `model_routes.health.enabled` + `HERMES_MODEL_ROUTES_HEALTH` bridge). Phase 2: gateway dynamic router (`gateway/model_router.py` + `_model_router_stage`) — classifier core byte-identical to the skill-gate bench winner (parity-test-pinned; changing it requires the 230+120 bench rerun), `model_routes.router` config (mode off|shadow|enforce + `HERMES_MODEL_ROUTER_MODE` bridge, label_routes, chat_route, streak), static condition rules (short-circuit by design — the plugin's live last-wins is a bug not ported), decision log `~/.hermes/logs/model_router_decisions.jsonl` for shadow-soak comparison, enforce apply with /model parity (#48031 auto-reset survival, pending note, session-DB persist). Dormant with empty config. Phase 3 (`model_switch`/`delegate_task` route enums) builds on this; supersedes patch #5 when Phase 3 lands.
- **commits:**
  - `fc34f882a feat(routing): model_routes catalog + health-aware route resolver (ADR-003 Phase 1)`
  - `1de282bc3 feat(routing): core dynamic model router with shadow mode (ADR-003 Phase 2)`
  - `3bf05fc33 feat(routing): route-enum delegation on delegate_task (ADR-003 Phase 3a)`
  - `fc5ebe08e fix(routing): classify the pre-hook message text in the router stage`
  - `30561b53e fixup(model-routing): adapt to v2026.7.20 upstream`
- **touches:** `hermes_cli/model_routes.py` (new), `hermes_cli/config.py`, `gateway/model_router.py` (new), `gateway/run.py`, `gateway/slash_commands.py`, `tools/delegate_tool.py`, `run_agent.py`, `cli-config.yaml.example`, `tests/conftest.py`, `tests/hermes_cli/test_model_routes.py` (new), `tests/gateway/test_model_router.py` (new), `tests/tools/test_delegate.py`
  - `e9a83296b test(routing): pin HERMES_MODEL_ROUTER_MODE=off in the hermetic fixture`
- **v2026.7.20-sync:** router state adopted upstream conversation-scope funnel (`_model_router_state`/`_model_router_fresh_applies` in `_CONVERSATION_SCOPED_STATE`, fresh-applies set→dict); #48031 fresh-apply survival reimplemented around the funnel; `_KNOWN_ROOT_KEYS` edit dropped (derived from `DEFAULT_CONFIG` upstream); Phase 3a delegate hunks rebuilt on the reworked `delegate_task`.

### 35. audience-personas
- **branch:** `soju/patches/audience-personas`
- **stacked-on:** `soju/patches/model-routing`
- **origin:** `local-author`
- **upstream_pr:** _(none — generic and config-off-by-default, upstream candidate once soaked)_
- **state:** `local-only`
- **rationale:** Audience-mode persona injection: when `HERMES_HOME/personas/modes.yaml` exists, a mode is selected per session as a pure deterministic function of session-constant inputs (platform/chat_type/chat_id/chat_name/user_id) — first-match rules (string-or-list exact match, chat_name case-insensitive, missing key = wildcard, AND), non-owner guard applied after rules (non-empty `user_id` not in `owners[platform]` forces `guards.non_owner_mode`; missing owners entry fails safe; empty user_id skips), `default_mode` fallback — and the mode's persona markdown is injected as stable-tier slot #2 right after the SOUL.md/DEFAULT_AGENT_IDENTITY identity block, through the same threat scan + truncation cap as SOUL.md. With no modes.yaml the build is byte-identical (strict no-op); all failure paths degrade to the no-op at DEBUG. Cache correctness: an `AudienceMode: <mode>` line joins the volatile tail only while active, and `_stored_prompt_matches_runtime` recomputes the expected mode via a cheap mode-only resolver (both-absent passes → pre-deploy stored prompts stay valid; mismatch/one-sided rebuilds exactly once per session). SOUL.md plumbing parity: `personas/` in profile clone + default-export include set, `hermes_config_mod` threat pattern extended to `.hermes/personas/` paths. The `artifact_register` section of modes.yaml is a tone-gate plugin contract, deliberately not consumed by core.
- **commit:** `852dbdbd3 feat(prompt): audience-mode persona injection from personas/modes.yaml`
- **touches:** `agent/audience_persona.py` (new), `agent/system_prompt.py`, `agent/conversation_loop.py`, `run_agent.py`, `hermes_cli/profiles.py`, `tools/threat_patterns.py`, `tests/agent/test_audience_persona.py` (new)

### 36. model-switch-provider-dedupe
- **branch:** `soju/patches/model-switch-provider-dedupe`
- **origin:** `local-author`
- **upstream_pr:** [#66128](https://github.com/NousResearch/hermes-agent/pull/66128)
- **state:** `pending-upstream`
- **rationale:** `/model <bare-model>` hard-fails with "declared by multiple configured providers (<slug>, custom:<display name>)" when the model is declared by exactly ONE `providers.<slug>` entry, because `_configured_provider_matches` counts the entry and its `get_compatible_custom_providers` legacy view separately. Fix canonicalizes match collection: a custom row is attributed to its originating providers slug via the `provider_key` stamp (or name+base_url identity). Genuine ambiguity — two different endpoints, including display-name collisions — still errors (regression-pinned). Found by the ADR-003 E2E harness (D1).
- **commit:** `48608a7d5 fix(model_switch): dedupe self-duplicate provider views in typed-model routing`
- **touches:** `hermes_cli/model_switch.py`, `tests/hermes_cli/test_model_switch_configured_provider_routing.py`

### 37. memory-phase0
- **branch:** `soju/patches/memory-phase0`
- **stacked-on:** `soju/patches/memory-write-reason-gate`
- **origin:** `local-author`
- **upstream_pr:** _(none — ADR-004 dogfood; revisit after shadow soak)_
- **state:** `local-only`
- **rationale:** ADR-004 Phase 0 memory-redesign plumbing: `pending/` WAL (durable per-turn buffer for external memory sync), L0-mirror local evidence journal for outgoing memory payloads, `_memory_ingest_disabled` per-agent flag (read-only memory forks), MEMORY.md/USER.md char-cap defaults unified on the config SoT. Journal review fixes: atomic appends, marker-only boundary mirroring, per-directory startup scan, directories pinned at construction.
- **commits:**
  - `6e0ddde44 feat(memory): _memory_ingest_disabled per-agent flag — read-only memory forks (ADR-004 Phase 0)`
  - `94f6d2380 feat(memory): pending/ WAL — durable per-turn buffer for external memory sync (ADR-004 Phase 0)`
  - `77c471b2a feat(memory): L0-mirror — local evidence journal for outgoing memory payloads (ADR-004 Phase 0)`
  - `683bdc935 fix(memory): unify MEMORY.md/USER.md char-cap defaults on config SoT (ADR-004 Phase 0)`
  - `11391d73a fix(memory): pin journal directories at construction, not at write time`
- **touches:** `agent/memory_journal.py` (new), `agent/agent_init.py`, `agent/agent_runtime_helpers.py`, `agent/background_review.py`, `agent/conversation_compression.py`, `agent/memory_manager.py`, `agent/tool_executor.py`, `agent/turn_context.py`, `run_agent.py`, `tools/memory_tool.py`, `tools/delegate_tool.py`, `cli-config.yaml.example`, `tests/agent/test_memory_{ingest_disabled,l0_mirror,pending_wal}.py` (new), docs/website memory pages
- **v2026.7.20-sync:** re-stacked on memory-write-reason-gate; turn_context gates re-expressed on upstream turn_context.py (7b3dcee92); conversation_compression gate adapted to upstream memory_context capture.


### 38. memory-phase1
- **branch:** `soju/patches/memory-phase1`
- **stacked-on:** `soju/patches/memory-phase0`
- **origin:** `local-author`
- **upstream_pr:** _(none — ADR-004 dogfood)_
- **state:** `local-only`
- **rationale:** ADR-004 Phase 1 notes tier: `NotesStore` declarative notes under the citation contract, `notes_write`/`notes_read`/`memory_propose` tool family, two-step token contract with grounded admission and gated backfill seam. Review fixes close secret-scrub bypasses, add real quote grounding, non-destructive supersede, write-gate parity.
- **commits:**
  - `ea75b3b42 fix(memory): journal review fixes — atomic appends, marker-only boundary mirroring, per-directory startup scan`
  - `7b6f2ddba feat(memory): NotesStore — declarative notes tier under the citation contract (ADR-004 Phase 1)`
  - `f7b511306 feat(memory): notes write pipeline — two-step token contract, grounded admission, gated backfill seam (ADR-004 §③)`
  - `d48950f18 feat(memory): notes tool family — notes_write/notes_read/memory_propose wiring + prompt guidance (ADR-004 Phase 1)`
  - `e38597533 fix(memory): notes review fixes — close the secret-scrub bypasses, real quote grounding, non-destructive supersede, write gate parity (ADR-004 Phase 1)`
- **touches:** `agent/notes_store.py` (new), `agent/memory_pipeline.py` (new), `tools/notes_tool.py` (new), `agent/agent_runtime_helpers.py`, `agent/memory_journal.py`, `agent/memory_manager.py`, `agent/prompt_builder.py`, `agent/system_prompt.py`, `agent/tool_executor.py`, `tools/memory_tool.py`, `tools/delegate_tool.py`, `toolsets.py`, `tests/agent/test_{memory_pipeline,notes_store}.py` (new), `tests/tools/test_notes_tool.py` (new)
- **v2026.7.20-sync:** tool-name unions re-resolved against v2 parent chain (runtime-control tools union at assembly); _dispatch_notes_tool re-anchored above upstream's finalize= signature (271a9d8ec).


### 39. memory-phase2-taint
- **branch:** `soju/patches/memory-phase2-taint`
- **stacked-on:** `soju/patches/memory-phase1`
- **origin:** `local-author`
- **upstream_pr:** _(none — ADR-004 dogfood)_
- **state:** `local-only`
- **rationale:** ADR-004 §① Phase 2 origin-taint machinery: injected-span registry, WAL/mirror span tagging, quote-taint enforcement; spans registered at the prefetch and memory-tool result sites. Review fixes: registry singleton hygiene, WAL/mirror tag coverage, floor-not-round registration timestamps.
- **commits:**
  - `a94e5088b feat(memory): origin-taint machinery — injected-span registry, WAL/mirror span tagging, quote-taint enforcement (ADR-004 §① Phase 2)`
  - `03ebd88dc feat(memory): register injected memory spans at the prefetch and memory-tool result sites (ADR-004 §① Phase 2)`
  - `e30f99c52 fix(memory): origin-taint review fixes — registry singleton hygiene, WAL/mirror tag coverage, floor-not-round registration ts (ADR-004 Phase 2)`
- **touches:** `agent/memory_taint.py` (new), `agent/agent_runtime_helpers.py`, `agent/memory_journal.py`, `agent/memory_manager.py`, `agent/memory_pipeline.py`, `agent/tool_executor.py`, `agent/turn_context.py`, `run_agent.py`, `tests/agent/test_memory_taint.py` (new), `tests/agent/test_memory_{ingest_disabled,pending_wal}.py`
- **v2026.7.20-sync:** prefetch taint registration combined at assembly with durable-turns' resume guard and waterfall span (see Assembly Integration Fixes).


### 40. memory-phase2-curator
- **branch:** `soju/patches/memory-phase2-curator`
- **stacked-on:** `soju/patches/memory-phase1`
- **origin:** `local-author`
- **upstream_pr:** _(none — ADR-004 dogfood; shadow-mode observation ongoing)_
- **state:** `local-only`
- **rationale:** ADR-004 Phase 2 ingest curator: fork recipe, verdict schema, shadow ledger, watermark; curator triggers + `curator_verdict` dispatch wiring. Review fixes: seam scrub+grounding, provenance validation, cross-lane taint interface. Runs shadow-only until the cutover gate.
- **commits:**
  - `7e0472dd1 feat(memory): ingest curator core — fork recipe, verdict schema, shadow ledger, watermark (ADR-004 Phase 2)`
  - `b4623d592 feat(memory): ingest curator triggers + curator_verdict dispatch wiring (ADR-004 Phase 2)`
  - `80bf2978b test(memory): ingest curator Phase-2 suite — shadow invariant, fork isolation, triggers, caps (ADR-004)`
  - `7e0bfcad3 fix(memory): ingest curator review fixes — seam scrub+grounding, provenance validation, cross-lane taint interface (ADR-004 Phase 2)`
- **touches:** `agent/ingest_curator.py` (new), `agent/agent_runtime_helpers.py`, `agent/background_review.py`, `agent/codex_runtime.py`, `agent/conversation_compression.py`, `agent/memory_journal.py`, `agent/memory_manager.py`, `agent/memory_pipeline.py`, `agent/tool_executor.py`, `agent/turn_finalizer.py`, `hermes_cli/config.py`, `run_agent.py`, `tests/agent/test_ingest_curator.py` (new)


### 41. cron-secret-scope-env-fallback
- **branch:** `soju/patches/cron-secret-scope-env-fallback`
- **origin:** `local-author`
- **upstream_pr:** `67827`
- **state:** `pending-upstream`
- **rationale:** Upstream fdab380a1 wraps every cron job in a `<home>/.env` secret scope regardless of deployment mode, and `get_secret()` treats any installed scope as authoritative. In single-profile deployments where provider keys live only in the process environment (systemd `Environment=`, `pass-cli run`/`op run` wrappers, shell exports) every cron credential read resolved empty → OpenAI client built with the `no-key-required` placeholder → every scheduled agent job 401s while interactive turns keep working (claw: vooy 모닝 브리핑 broke daily since 2026-07-07). Scope-miss reads now fall through to `os.environ` when multiplexing is OFF; multiplexed scopes stay authoritative (fail-closed semantics unchanged).
- **commit:** `b5429a52c fix(secrets): fall back to os.environ on scope miss when multiplexing is off`
- **touches:** `agent/secret_scope.py`, `tests/agent/test_secret_scope.py`
- **v2026.7.20-sync:** verified NOT merged upstream at v2026.7.20 (agent/secret_scope.py byte-identical BASE→TARGET; scheduler still installs unconditional secret scope); clean pick.


### 42. anthropic-picker-suppression
- **branch:** `soju/patches/anthropic-picker-suppression`
- **origin:** `local-author`
- **upstream_pr:** _(none)_
- **state:** `local-only`
- **rationale:** Codex CLI model picker offered Anthropic models even when Anthropic credentials are suppressed for the session; respect the suppression flag when building picker candidates. Recovered from the live deployment checkout (was deployed as an unmanifested commit on top of the old production assembly, 2026-07-22).
- **commit:** `38695e2e2 fix(model-picker): respect Anthropic credential suppression`
- **touches:** `hermes_cli/model_switch.py`, `tests/hermes_cli/test_codex_cli_model_picker.py`

### 43. daemon-pool-py314-compat
- **branch:** `soju/patches/daemon-pool-py314-compat`
- **origin:** `local-author`
- **upstream_pr:** [#69209](https://github.com/NousResearch/hermes-agent/pull/69209)
- **state:** `pending-upstream`
- **rationale:** `DaemonThreadPoolExecutor` mirrors CPython 3.8–3.13 `ThreadPoolExecutor._adjust_thread_count` internals; CPython 3.14 moved per-worker state into `prepare_context()`/`WorkerContext` and changed `_worker`'s signature, so every `submit()` dies with `AttributeError: '_initializer'` (all concurrent tool batches + background memory sync). Branch on `hasattr(ThreadPoolExecutor, "prepare_context")` and pass matching worker args on both interpreter families. Hoisted from a memory-phase0 rebase collateral to a stack-root patch (2026-07-22).
- **commit:** `e3f90d800 fix(compat): support CPython 3.14 ThreadPoolExecutor internals in DaemonThreadPoolExecutor`
- **touches:** `tools/daemon_pool.py`

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
soju/fork-policy                      ← management branch with manifest + scripts (not applied)
soju/patches/runtime-control
soju/patches/memory-write-reason-gate
soju/patches/todo-progress-display
soju/patches/discord-table-codeblocks
soju/patches/background-review-guardrails
soju/patches/runtime-state-session-split
soju/patches/runtime-route-awareness (stacked on runtime-control)
soju/patches/lsp-idle-reaper
soju/patches/gateway-max-iterations-config-authority
soju/patches/strict-chat-reasoning-details
soju/patches/discord-home-autothread-fix
soju/patches/runtime-override-rehydrate-credentials (stacked on runtime-route-awareness)
soju/patches/turn-waterfall-tracing
soju/patches/tool-delay-removal (stacked on turn-waterfall-tracing)
soju/patches/prompt-tail-freeze (stacked on runtime-route-awareness)
soju/patches/request-client-reuse
soju/patches/async-token-accounting
soju/patches/gateway-persist-trim
soju/patches/gateway-worker-pool
soju/patches/conn-error-fail-fast
soju/patches/background-first-waits
soju/patches/llm-activity-recap
soju/patches/config-knob-bridges (stacked on gateway-max-iterations-config-authority)
soju/patches/durable-turns (stacked on llm-activity-recap + config-knob-bridges)
soju/patches/hook-prepend-command-safety (stacked on durable-turns + runtime-control)
soju/patches/slow-tool-perf-advisor
soju/patches/session-db-read-path-split (stacked on hook-prepend-command-safety + async-token-accounting)
soju/patches/fts5-cjk-bigram-index (stacked on session-db-read-path-split)
soju/patches/search-slow-query-log (stacked on fts5-cjk-bigram-index)
soju/patches/fts-v2-config-authority (stacked on search-slow-query-log)
soju/patches/model-routing
soju/patches/audience-personas (stacked on model-routing)
soju/patches/model-switch-provider-dedupe
soju/patches/memory-phase0 (stacked on memory-write-reason-gate)
soju/patches/memory-phase1 (stacked on memory-phase0)
soju/patches/memory-phase2-taint (stacked on memory-phase1)
soju/patches/memory-phase2-curator (stacked on memory-phase1)
soju/patches/cron-secret-scope-env-fallback
soju/patches/anthropic-picker-suppression
soju/patches/daemon-pool-py314-compat
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

## Assembly Integration Fixes (re-apply when reassembling production)

Sibling patches that only interact at production-assembly time. The rebuild's
sequential merge either conflicts here (resolution recorded in the merge
commit) or auto-merges WRONG — re-verify these after every rebuild:

- `run_agent.py::_create_request_openai_client` — request-client-reuse body must sit INSIDE turn-waterfall-tracing's `llm.client_create` span (conflict, recorded).
- `agent/conversation_loop.py` — durable-turns' `resume_turn`/`turn_id` params must be threaded through turn-waterfall-tracing's `run_conversation` → `_run_conversation_impl` split, incl. both wrapper call sites (SILENT auto-merge drop; fixed by `3e84fb9f5`).
- `agent/turn_context.py::memory_prefetch` — durable-turns' `not resume_turn` guard combines with the tracing span wrapper (conflict, recorded).
- `gateway/session.py::from_dict` — runtime-control's three `runtime_*` kwargs must appear exactly once (positional-shuffle conflict, recorded).

- `agent/turn_context.py::on_turn_start/memory_prefetch` — memory-phase0's `memory_ingest_allowed` gate combines with durable-turns' `not resume_turn` guard AND turn-waterfall-tracing's span; memory-phase2-taint's `record_injected_text` block appends after the span-wrapped prefetch (conflicts, recorded).
- `agent/agent_runtime_helpers.py`/`toolsets.py` tool-name lists — union of runtime-control's model tools and memory-phase1's notes tools (conflicts, recorded).

## Sync History

### 2026-07-22 — base bump 4281151ae → 3ef6bbd20 (v2026.7.20, 1,358 commits)
Full stack rebuild (35 → 33 active patches), multi-agent triage + per-patch rebase with targeted tests.
- **Dropped (merged upstream):** prompt-cache-stability (`7b3dcee92`), aux-runtime-context (`fdc6c32d7`+`73057ed16`+`c201b72f3`).
- **Reduced:** prompt-tail-freeze → fork-only residue (~30%) on runtime-route-awareness (upstream `c0c76a471`).
- **Reworked for upstream landings:** turn-waterfall-tracing (segmented dispatch), request-client-reuse (request-local anthropic clients #67142 + single-writer fence #65991), durable-turns (delivery ledger `5854aad8b` + recovery-note refactor `9fc0074ba`), model-routing (conversation-scope funnel `19527db73`).
- **Retired branches deleted (tips tagged `archive/pre-v20260722/*`):** gateway-executor-capacity, process-wait-visibility (aliases of old prompt-tail-freeze tip), long-wait-heartbeat, slash-command-mixin-shadow, tool-delay-env-default, delegate-per-task-model.
- **Manifest fixes:** duplicate §19–21 removed; `stacked-on` field introduced.
- **Known environment issue (pre-existing at TARGET, not fork-caused):** upstream `tools/daemon_pool.py` mirrors CPython ≤3.13 ThreadPoolExecutor internals; under Python 3.14 (`.venv` = 3.14.6) every concurrent tool-batch test fails with `AttributeError: _initializer`. Verify the deployment venv Python before rollout; upstream-report candidate.

### 2026-07-22 (later) — origin-only patches recovered
The 07-22 rebuild was cut from a stale local fork-policy: origin carried #37–#41
(ADR-004 memory phases, cron-secret-scope-env-fallback) that were live in the old
production but absent locally, plus an unmanifested deployed commit
(anthropic-picker-suppression) found only in the live checkout. All six re-stacked
onto v2026.7.20 and merged; production force-push briefly lacked them (~2h window,
no live redeploy occurred). Lesson: `hermes-patches sync` must diff origin/production
vs manifest-built production BEFORE force-pushing.

### 2026-07-22 (later-2) — daemon-pool hoist + 413 pin
daemon_pool CPython-3.14 fix hoisted out of memory-phase0 into stack-root patch #43
(memory chain restacked without it; production content unchanged, `cf80b8765` still
valid — assembly predates the branch restructure but is content-identical, verified).
runtime-route-awareness gains the test_413_compression expectation pin; production
re-merged (`merge: apply patch runtime-route-awareness (413-compression expectation)`).
