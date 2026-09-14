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
base_commit:   939e45c91d751fadd94dcd1b873ac3cb44846213
base_tag:      v2026.9.11
base_describe: 939e45c91d75
pinned_at:    2026-09-14
```

Bump `base_commit` only via `bin/hermes-patches sync <new-ref>`. Each bump must rebase all `soju/patches/*` topics on top of the new base and verify the production stack rebuilds clean.

## Patches (apply order = list order)


### 1. runtime-control
- **branch:** `soju/patches/runtime-control`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood runtime control)_
- **state:** `local-only`
- **rationale:** Agent-callable `model_status` / `model_switch` tools. Session-only scope (turn scope removed — LLMs omitted scope ~29%, causing silent reversion). Phase 3c (2026-07-23): the LLM-facing switch surface is route-only — `route` + `reason`; raw model/provider ids and reasoning effort are catalog (config SoT) decisions and get a teaching error at the tool boundary (`dispatch_model_switch`, the single dispatch entry point for BOTH executors — the Phase 3b sequential-executor `route` omission was exactly per-executor kwarg drift). With no declared routes the switch tool goes dormant via check_fn instead of degrading to free-form. `model_status` speaks route language (route block + model + reasoning; raw provider/endpoint/api-mode stay in telemetry). Gateway session callback plus durable SessionEntry runtime fields so session-scoped model/reasoning overrides survive gateway restart without storing secrets. Trusted pre-dispatch plugin runtime overrides can select the session route after auth but before the first LLM call. Plugin `prepend` directives accumulate text before the original event message.
- **commits:**
  - `3df09e57f8` feat(runtime): agent-callable model_switch / model_status (session-only)
  - `2af487cb44` fix(runtime): align runtime tool dispatch ownership
  - `504d8a2ab4` feat(runtime): route-enum self model switching (ADR-003 Phase 3b)
  - `11da994120` feat(runtime): route-only model switching (ADR-003 Phase 3c)
  - `2a45cf7f3a` fix(runtime): rehydrate persisted runtime state after gateway refactor
  - `50b0f412be` test(runtime): re-anchor runtime-control tests to the 2026.9.11 upstream seams
- **touches:** `agent/agent_init.py`, `agent/agent_runtime_helpers.py`, `agent/runtime_control.py`, `agent/tool_dispatch_helpers.py`, `agent/tool_executor.py`, `gateway/run.py`, `gateway/session.py`, `hermes_cli/plugins.py`, `hermes_cli/runtime_provider.py`, `model_tools.py`, `tests/gateway/test_pre_gateway_dispatch.py`, `tests/gateway/test_session.py`, `tests/gateway/test_session_model_override_routing.py`, `tests/hermes_cli/test_plugins.py`, `tests/hermes_cli/test_runtime_provider_resolution.py`, `tests/run_agent/test_pre_tool_session_id.py`, `tests/run_agent/test_run_agent.py`, `tests/run_agent/test_runtime_control.py`, `tests/run_agent/test_runtime_control_dispatch.py`, `tests/test_model_tools.py`, `tests/tools/test_runtime_control_tool_schema.py`, `tools/runtime_control_tool.py`, `toolsets.py`
- **v2026.8.3-sync:** rehydration moved to the modular gateway startup path; explicit reasoning still wins over upstream's per-model reasoning re-resolution on switch.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/runtime-control.md` for relocations/seams.


### 2. memory-write-reason-gate
- **branch:** `soju/patches/memory-write-reason-gate`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood memory hygiene)_
- **state:** `local-only`
- **rationale:** Memory `add`/`replace` tool calls require an explicit suitability reason explaining why USER/MEMORY is the right store rather than a skill, Graphiti, or session history. The reason is a guardrail only and is not persisted with the entry.
- **commits:**
  - `a8ef02689f` feat(memory): require write reason for memory updates
  - `36902689fb` test(memory): pass write reason in null-target dispatcher test
  - `9380270517` test(memory): pass write reason in upstream tests added since the reason gate
- **touches:** `agent/agent_runtime_helpers.py`, `agent/tool_executor.py`, `tools/memory_tool.py`, `tests/tools/test_memory_tool.py`, `tests/tools/test_memory_tool_schema.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/memory-write-reason-gate.md` for relocations/seams.


### 3. todo-progress-display
- **branch:** `soju/patches/todo-progress-display`
- **origin:** `local-author`
- **upstream_pr:** _(none — Discord/gateway dogfood UX)_
- **state:** `local-only`
- **rationale:** Gateway progress bubbles should show todo item statuses after the todo tool completes, not only a count like `planning N task(s)`. Also flush throttled progress edits when the final queued event has no following tool event.
- **commits:**
  - `4eb478c29b` feat(gateway): show todo progress details
- **touches:** `agent/display.py`, `gateway/run.py`, `tests/agent/test_display.py`, `tests/gateway/test_run_progress_topics.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/todo-progress-display.md` for relocations/seams.


### 4. discord-table-codeblocks
- **branch:** `soju/patches/discord-table-codeblocks`
- **origin:** `local-author`
- **upstream_pr:** _(none — Discord/gateway dogfood UX)_
- **state:** `local-only`
- **rationale:** Discord does not render GitHub-flavored markdown pipe tables. Convert detected outbound pipe tables to fenced box-drawing ASCII tables so table responses remain readable, while preserving existing fenced code blocks, CJK wide-character alignment, and long-table chunk codeblock boundaries.
- **commits:**
  - `b2bc4b51a0` feat(discord): render markdown tables as codeblocks
- **touches:** `plugins/platforms/discord/adapter.py`, `tests/gateway/test_discord_send.py`
- **v2026.7.20-sync:** upstream `test_table_converted_to_bullets` re-expected to the fenced-ASCII semantics (pre-existing gap — it was red on the old stack too).
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/discord-table-codeblocks.md` for relocations/seams.


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
- **commits:**
  - `fb8f24658c` fix: calibrate background self-improvement review
- **touches:** `agent/background_review.py`, `tests/run_agent/test_review_prompt_class_first.py`
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/background-review-guardrails.md` for relocations/seams.


### 7. runtime-state-session-split
- **branch:** `soju/patches/runtime-state-session-split`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood runtime gate correctness)_
- **state:** `local-only`
- **rationale:** Runtime hard gates must see the active session's actual model/provider after context compression rotates the session id. Compression split republishes runtime state for the new session while retaining the old session as task/parent scope, preventing fallback to stale cross-session runtime state.
- **commits:**
  - `e0d21b4636` fix: keep runtime state scoped across compression splits
- **touches:** `agent/conversation_compression.py`, `tests/agent/test_runtime_state_session_split.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/runtime-state-session-split.md` for relocations/seams.


### 8. runtime-route-awareness
- **branch:** `soju/patches/runtime-route-awareness`
- **stacked-on:** `soju/patches/runtime-control`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood runtime routing correctness)_
- **state:** `local-only`
- **rationale:** Inject an API-call-time Runtime/Route State block so the agent sees live CurrentRuntime plus current-turn DesiredRoute without calling `model_status`. Trusted pre-dispatch `runtime_override` metadata is normalized into one-shot route state for the routed gateway turn, preserving stale-route protection while leaving post-tool rerouting and NEED_CONTEXT scout mode for later phases.
- **commits:**
  - `6477f191fe` feat(runtime): inject runtime route awareness prompt
  - `e857cc764b` test(routing): expect the Runtime/Route block in post-compression system prompts
- **touches:** `agent/chat_completion_helpers.py`, `agent/conversation_loop.py`, `agent/system_prompt.py`, `docs/runtime-route-awareness.md`, `gateway/run.py`, `tests/agent/test_runtime_route_prompt.py`, `tests/gateway/test_pre_gateway_dispatch.py`
- **v2026.7.20-sync:** `_pending_runtime_route_states` registered in upstream `_CONVERSATION_SCOPED_STATE` funnel; failover system message now composed via `compose_effective_system_prompt` so the Runtime/Route block refreshes on provider failover.
- **note (07-22):** pins fork semantics onto upstream `test_413_compression` exact-equality assertion (Runtime/Route block present in post-compression system prompts; was red on the old stack too).
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/runtime-route-awareness.md` for relocations/seams.


### 9. lsp-idle-reaper — MERGED UPSTREAM (2026-08-04)
Landed via PR #74058; old tip archived at `archive/pre-v20260804/lsp-idle-reaper`.

### 10. aux-runtime-context — MERGED UPSTREAM (2026-07-22)
Upstream `fdc6c32d7`/`73057ed16`/`c201b72f3` replaced process-global runtime-main state with a
`contextvars.ContextVar` (`_RUNTIME_MAIN_CONTEXT`) plus per-turn Token scoping — a strict superset of
this patch's `threading.local` isolation (also covers asyncio tasks). Dropped at the v2026.7.20 base
bump; old tip archived at `archive/pre-v20260722/aux-runtime-context`.

### 11. gateway-max-iterations-config-authority — MERGED UPSTREAM (2026-08-04)
v2026.8.3 reloads and re-bridges `agent.max_turns` per turn; old tip archived at `archive/pre-v20260804/gateway-max-iterations-config-authority`.

### 12. strict-chat-reasoning-details
- **branch:** `soju/patches/strict-chat-reasoning-details`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood strict OpenAI-compatible provider replay fix)_
- **state:** `local-only`
- **rationale:** Strict OpenAI-compatible Chat Completions providers such as GLM Vooy reject non-standard assistant message replay fields (`reasoning`, `reasoning_details`) with `Extra inputs are not permitted`. Preserve those fields in session history for provider continuity, but strip them from the outbound chat_completions wire payload so mixed-provider Discord sessions do not get stuck in repeat HTTP 400 retries.
- **commits:**
  - `2736a31797` fix(chat): strip reasoning replay fields for strict chat completions
- **touches:** `agent/transports/chat_completions.py`, `tests/run_agent/test_strict_api_validation.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/strict-chat-reasoning-details.md` for relocations/seams.


### 13. discord-home-autothread-fix
- **branch:** `soju/patches/discord-home-autothread-fix`
- **origin:** `local-author`
- **upstream_pr:** _(none — Discord home-channel dogfood routing fix)_
- **state:** `local-only`
- **rationale:** Discord home-channel messages should still auto-create thread conversations when channel controls disable broad channel auto-threading. Restore the home-channel path while keeping explicit channel-control disable behavior available for non-home channels.
- **commits:**
  - `9e2a8723a3` fix(discord): restore home channel auto-threading
- **touches:** `gateway/config.py`, `plugins/platforms/discord/adapter.py`, `tests/gateway/test_discord_channel_controls.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/discord-home-autothread-fix.md` for relocations/seams.


### 14. runtime-override-rehydrate-credentials
- **branch:** `soju/patches/runtime-override-rehydrate-credentials`
- **stacked-on:** `soju/patches/runtime-route-awareness`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood runtime-control follow-up fix)_
- **state:** `local-only`
- **rationale:** Upstream owns live `switch_model` endpoint rollback and provider-header reapplication; this patch is limited to gateway restart/persistence. A persisted runtime route is eligible only when both `runtime_model` and `runtime_provider` labels exist, then resolves once with `target_model` into one authoritative provider bundle (normalized provider, endpoint, API mode, keyed or keyless credential, command/args, and credential pool). Incomplete or unresolved labels stay durable but dormant while the turn falls back wholly to coherent defaults. Persisted reasoning rehydrates independently, and the latest `/model` or runtime-route writer clears the other durable model-route store without clearing reasoning. State DB coverage verifies DB-only restart and that no secret bundle fields are persisted.
- **commits:**
  - `cc434acd29` fix(gateway): re-resolve persisted runtime override bundle
- **touches:** `gateway/run.py`, `gateway/session.py`, `tests/gateway/test_runtime_override_restart_rehydration.py`, `tests/gateway/test_runtime_override_state_db_restart.py`, `tests/gateway/test_runtime_override_store_precedence.py`, `tests/gateway/test_session_model_override_routing.py`
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/runtime-override-rehydrate-credentials.md` for relocations/seams.


### 15. turn-waterfall-tracing
- **branch:** `soju/patches/turn-waterfall-tracing`
- **origin:** `local-author`
- **upstream_pr:** _(none — perf instrumentation for local bottleneck hunt)_
- **state:** `local-only`
- **rationale:** Per-turn waterfall tracing to attribute end-to-end turn latency (observed ~30% slower than a minimal agent on the same LLM). New `agent/turn_trace.py` collects wall-clock spans across the whole turn lifecycle — gateway ingest/session-resolve/transcript-load/agent-setup, prologue children (system-prompt restore, early persist, compression preflight, pre-LLM hook, memory prefetch), per-iteration context assembly/request setup/`llm.call` (TTFT + failed attempts) /accounting, tool batches incl. the inter-tool delay sleep, verify gates, finalize children, gateway persist, transport delivery — and emits one JSONL record per turn to `~/.hermes/logs/turn_traces.jsonl`. `agent/turn_trace_render.py` renders terminal/HTML waterfalls and cross-turn p50/p95 summaries with a model-time vs hermes-overhead split. Gated by `HERMES_TURN_TRACE=1` (default off = no-op); tracing failures can never break a turn.
- **commits:**
  - `7f96855135` feat(telemetry): per-turn waterfall tracing spans
  - `3ff6fa8ccd` feat(telemetry): request prefix fingerprints for cache-break diffing
  - `800505d4a5` fix(telemetry): fingerprint Responses-API payloads too
  - `df2dda8f63` fix(telemetry): carry turn trace across pre-dispatch event replacement
  - `e52b0b7ffa` feat(telemetry): 4KB-chunk hashes locate in-message cache breaks
  - `de55032152` feat(telemetry): attribute rest-field changes to specific keys
  - `1adb9f24b2` feat(telemetry): HERMES_TURN_TRACE_SYS_TAIL captures system-prompt tail
- **touches:** `agent/turn_trace.py` _(new)_, `agent/turn_trace_render.py` _(new)_, `tests/agent/test_turn_trace.py` _(new)_, `agent/chat_completion_helpers.py`, `agent/conversation_loop.py`, `agent/tool_executor.py`, `agent/turn_context.py`, `agent/turn_finalizer.py`, `gateway/platforms/base.py`, `gateway/run.py`, `plugins/platforms/telegram/adapter.py`, `run_agent.py`
- **v2026.7.20-sync:** tools.batch span adapted to upstream segmented dispatch (mode=`segmented` + `segments=<n>` tag); `prologue.persist_early` split into `persist_early` + new `prologue.persist_user_turn` following upstream's persist-site split (cross-version p50 continuity caveat).
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/turn-waterfall-tracing.md` for relocations/seams.


### 16. tool-delay-removal — MERGED UPSTREAM (2026-08-04)
Landed as `ce9f6712f` via PR #64172; old tip archived at `archive/pre-v20260804/tool-delay-removal`.

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
- **commits:**
  - `6fa3aa14d2` feat(cache): prompt-tail freeze — byte-stable gateway system prompts (patch #18)
  - `e80f68a3ef` feat(prompt): Runtime/Route identity speaks route + model (ADR-003 Phase 3c)
- **touches:** `agent/system_prompt.py`, `gateway/run.py`, `tests/gateway/test_prompt_tail_freeze.py`, `tests/agent/test_runtime_route_prompt.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/prompt-tail-freeze.md` for relocations/seams.


### 19. request-client-reuse — MERGED UPSTREAM (2026-08-04)
Landed as `82e2c9ce4` via PR #73375; old tip archived at `archive/pre-v20260804/request-client-reuse`.

### 20. async-token-accounting — MERGED UPSTREAM (2026-08-04)
Landed via PR #73359; old tip archived at `archive/pre-v20260804/async-token-accounting`.

### 21. gateway-persist-trim — MERGED UPSTREAM (2026-08-04)
Salvaged as `54eafee30` via PR #76916; old tip archived at `archive/pre-v20260804/gateway-persist-trim`.

### 22. gateway-worker-pool
- **branch:** `soju/patches/gateway-worker-pool`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate after burn-in)_
- **state:** `local-only`
- **rationale:** The gateway's shared agent-turn ThreadPoolExecutor was hardcoded to 10 workers; every agent turn holds one worker for its full duration, so a kanban batch of 8+ concurrent multi-hour marathon turns (300-iteration workers, blocking process waits) starved every other session for hours — observed 9.1h between a finished turn's finalize and its delivery, and users reporting sessions "quietly frozen". Default raised to 24 and exposed as config.yaml `gateway.max_workers` with `HERMES_GATEWAY_MAX_WORKERS` env fallback (clamped ≥ 2); workers are network-I/O-bound so the larger pool is cheap.
- **commits:**
  - `4ecc31d405` perf(gateway): size the agent-turn pool from config, default 24
- **touches:** `gateway/run.py`, `tests/gateway/test_gateway_max_workers.py` _(new)_
- **v2026.7.20-sync:** coexists with upstream per-session turn lease (`19527db73`): the lease serializes per-session, the pool sizes total concurrency.
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/gateway-worker-pool.md` for relocations/seams.


### 23. conn-error-fail-fast
- **branch:** `soju/patches/conn-error-fail-fast`
- **stacked-on:** assembled parallel lane (`sync0914/assembly` = v2026.9.11 + independent topics) — 2026-09-14 chronological tip stack starts here
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate after burn-in)_
- **state:** `local-only`
- **rationale:** Consecutive sub-2s transport failures (connection refused/reset before any bytes) mean the endpoint is down, not congested; with no fallback available the retry loop burned a dozen attempts with growing backoff (observed 13 attempts / 170-250s of user-visible silence per turn against a briefly-down codex-lb). Track the instant-failure streak on TurnRetryState and end the turn with an actionable error after `HERMES_FAST_CONN_FAIL_LIMIT` (default 3, 0 disables) once the fallback chain has had its chance; slow timeouts keep the full retry budget.
- **commits:**
  - `82cacc2b40` fix(agent): fail fast on instant transport-failure streaks
  - `4bc069e211` fix(telemetry): tag instant transport fail-fast exits
  - `11d4002d8f` test(agent): fail-fast tests patch the backoff where upstream now computes it (2026-09-14 sync)
- **touches:** `agent/conversation_loop.py`, `agent/turn_retry_state.py`, `tests/agent/test_fast_transport_fail_fast.py` _(new)_
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/conn-error-fail-fast.md` for relocations/seams.


### 24. background-first-waits
- **branch:** `soju/patches/background-first-waits`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate after burn-in)_
- **state:** `local-only`
- **rationale:** Models foreground-polled long jobs with chained `process wait` calls (observed 4-5 consecutive 180s waits per turn — total user-facing silence while holding a gateway worker). Background-first: the first full block-wait stays a normal flow (180s window unchanged per user request), but from the SECOND consecutive timed-out wait on the same still-running process, `notify_on_complete` is auto-armed and the tool result instructs the model to end its turn with a summary — the completion re-enters the session as an event and the model explains the result there (the natural LLM recap). `HERMES_PROCESS_WAIT_CAP` tunes the quiet-wait allowance (default 1, 0 disables for completion-bound sessions like kanban workers). The process tool schema and terminal guidance now state the one-wait-per-process contract (one-time tools-hash cache bust on deploy, legitimate).
- **commits:**
  - `07b54b39f8` feat(tools): background-first process waits — escalate chained blocking waits
- **touches:** `tools/process_registry.py`, `tools/terminal_tool.py`, `tests/tools/test_background_first_waits.py` _(new)_
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/background-first-waits.md` for relocations/seams.


### 25. llm-activity-recap
- **branch:** `soju/patches/llm-activity-recap`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate after burn-in)_
- **state:** `local-only`
- **rationale:** `display.long_running_notifications` gains a `recap` mode: instead of the terse "⏳ Working — N min — iteration i/max" heartbeat, the gateway asks the auxiliary LLM (compression rail, 8s timeout, ~80 tokens) for a one-line present-tense recap of what the agent is doing — goal + recent tool calls + current wait — in the conversation's language (Claude Code-style). Context-hash caching regenerates only on activity change; failures fall back to the terse heartbeat; in recap mode the bubble is deleted-and-resent (adapters with delete support) so it stays at the thread bottom instead of buried. Complements #24 background-first-waits: waits that should end the turn do, and turns that legitimately run long narrate themselves.
- **commits:**
  - `1891af2fc6` feat(gateway): LLM activity recap for long-running notifications
  - `39066cb674` feat(gateway): tune activity recap context from live-session evaluation
  - `603bd538e3` feat(gateway): recap speaks in the agent's own voice
  - `e8d3bb8336` feat(gateway): persona-definition voice fallback for fresh sessions
- **touches:** `gateway/run.py`, `run_agent.py`, `tests/gateway/test_llm_activity_recap.py` _(new)_
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/llm-activity-recap.md` for relocations/seams.


### 26. config-knob-bridges
- **branch:** `soju/patches/config-knob-bridges`
- **origin:** `local-author`
- **upstream_pr:** _(none — fork knob plumbing)_
- **state:** `local-only`
- **rationale:** Fork knobs were env-only, breaking the house convention (config.yaml is authoritative; env is the cross-process carrier/override — see upstream PR #64298). Bridge `agent.process_wait_cap` → `HERMES_PROCESS_WAIT_CAP` (#24) and `agent.fast_conn_fail_limit` → `HERMES_FAST_CONN_FAIL_LIMIT` (#23) in both the startup export block and the per-turn reload bridge. Recap interval already had the upstream `agent.gateway_notify_interval` bridge.
- **commits:**
  - `216c9df13b` feat(gateway): config.yaml bridges for fork knobs
- **touches:** `gateway/run.py`, `tests/gateway/test_fork_knob_config_bridges.py` _(new)_
- **v2026.8.3-sync:** restacked directly on the new base; fork knobs still read managed-scope-overlaid config.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/config-knob-bridges.md` for relocations/seams.


### 27. durable-turns
- **branch:** `soju/patches/durable-turns`
- **stacked-on:** `soju/patches/conn-error-fail-fast` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(pending — branch `upstream-pr/durable-turns` pushed to fork, PR body drafted, awaiting owner approval to open)_
- **state:** `local-only`
- **rationale:** Restart recovery was a paper-over: an interrupted turn was abandoned and a synthetic empty user turn told the model to "skip any unfinished work" — the banner's "I'll try to resume where you left off" was implemented by nothing (live incident 2026-07-15, discord thread 1526457680527622247 reduced to "응 오빠, 여기 있어"). Durable turns make the in-flight turn a first-class durable record (`SessionEntry.active_turn`: turn_id/status/boot_id/resume_count) and re-enter the SAME turn on its persisted transcript after a restart: `run_conversation(resume_turn=True)` appends no user row, drops synthetic "Operation interrupted…" closers, completes unanswered tool_calls via existing orphan recovery (side effects = UNKNOWN, never re-executed), and delivers an already-composed final without another model call. Poison cap `HERMES_TURN_RESUME_MAX`/`agent.turn_resume_max` (default 2) abandons repeat offenders with an honest notice. Kill switch `HERMES_GATEWAY_TURN_RESUME`/`agent.gateway_turn_resume` restores legacy behavior. Banner reworded to match reality. See `DECISIONS.md` ADR-002.
- **commits:**
  - `4eca722461` feat(gateway): durable turns — same-turn resume across gateway restarts
- **touches:** `agent/turn_resume.py` _(new)_, `agent/turn_context.py`, `agent/conversation_loop.py`, `agent/turn_finalizer.py`, `run_agent.py`, `gateway/run.py`, `gateway/session.py`, `hermes_cli/config.py`, `tests/agent/test_turn_resume.py` _(new)_, `tests/gateway/test_durable_turn_records.py` _(new)_, `tests/run_agent/test_resume_turn_loop.py` _(new)_, `tests/gateway/restart_test_helpers.py`, `tests/gateway/test_restart_resume_pending.py`
- **v2026.7.20-sync:** reconciled with upstream delivery-obligation ledger (`5854aad8b`): `_redeliver_pending_obligations` force-finishes `active_turn` records for claimed obligations, narrowing `_resume_composed_final` to the ledger-uncovered window; recovery-note bypass rewoven around module-scope `build_resume_recovery_note`.
- **2026-08-26-sync:** replayed the durable feature delta onto the assembled dependency tip and resolved the four overlapping seams semantically. This preserves the worker-pool helper/call, turn-trace setup, fail-fast retry path, route/runtime rehydration, and current gateway flow while adding only durable-turn behavior; it also removes the duplicate tracing wrapper introduced by the stale resolution. Refreshed the manifest to amended tip `018bd2284`, which preserves upstream's claim-time `resume_pending` clear without redundantly clearing it again after redelivery.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/durable-turns.md` for relocations/seams.


### 28. hook-prepend-command-safety
- **branch:** `soju/patches/hook-prepend-command-safety`
- **stacked-on:** `soju/patches/durable-turns` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — hardens the fork-only pre_gateway_dispatch prepend action from runtime-control)_
- **state:** `local-only`
- **rationale:** A `pre_gateway_dispatch` `{"action": "prepend"}` rewrote `event.text` before slash dispatch, and `is_command()`/`get_command()` key off `text.startswith("/")` — so any plugin prepend on a command message demoted it into plain chat that fell through to the agent. Live incident 2026-07-15: the inbox-matter-coordinator advisory rewrite (plugin 9ed17c4) prepends an `[INBOX_MATTER ...]` marker on matter-linked threads; `/model` there was answered by the agent's `model_status` tool instead of the interactive picker, and every slash command in those threads broke the same way. Fix: drop prepends (debug log) when the event is a slash command — advisory context is agent-facing and command handlers can't consume it. Plain-chat prepend behavior unchanged.
- **commits:**
  - `35c9da8f87` fix(gateway): drop pre_gateway_dispatch prepends on slash commands
- **touches:** `gateway/run.py`, `tests/gateway/test_pre_gateway_dispatch.py`
- **v2026.7.20-sync:** restacked — the guarded prepend action is fork-only (runtime-control), so the branch now merges runtime-control before the pick.
- **2026-08-26-sync:** replayed the hook change onto corrected durable tip `c82e26d88`, preserving the slash-command guard and restoring the declared current dependency edge.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/hook-prepend-command-safety.md` for relocations/seams.


### 29. slow-tool-perf-advisor
- **branch:** `soju/patches/slow-tool-perf-advisor`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — candidate once soak data confirms hint uptake)_
- **state:** `local-only`
- **rationale:** Trace mining across 2,605 turns (2026-07-16) showed the dominant avoidable turn latency is agents routing heavy work through `terminal` in shapes with a cheap native equivalent: full-tree scans (`rglob`/`os.walk`/unpruned `find`/bare `grep -r` — 59+32+209 calls) over repos whose `node_modules` holds >1.7M files (observed 88s + 63s back-to-back scans of the same tree in one Discord turn), foreground `sleep` polling (364 calls), and multi-minute foreground jobs pinning session workers — while `search_files` (rg-backed, .gitignore-aware) answers in ~1s and background+`notify_on_complete` re-enters the session for free. Fix is a thin advisory-only shot mirroring the subdirectory-hints append: when a terminal call is slow AND matches an antipattern, append ONE `[perf-advisor]` line to the tool result at the moment the model just paid for the slow call. Kill switch `HERMES_PERF_ADVISOR=0`; thresholds `HERMES_PERF_ADVISOR_MIN_S` (10s), `HERMES_PERF_ADVISOR_FOREGROUND_S` (120s).
- **commits:**
  - `8866a8d669` feat(tools): slow-tool perf advisor — teach cheaper shapes on slow terminal results
- **touches:** `tools/perf_advisor.py` (new), `agent/tool_executor.py`, `tests/test_perf_advisor.py` (new)
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/slow-tool-perf-advisor.md` for relocations/seams.


### 30. session-db-read-path-split — MERGED UPSTREAM (2026-08-04)
v2026.8.3 provides `_read_ctx()` and per-thread `mode=ro` connections; old tip archived at `archive/pre-v20260804/session-db-read-path-split`.

### 31. fts5-cjk-bigram-index — MERGED UPSTREAM (2026-08-04)
Upstream ships `cjk_unicode61`, `native/fts5_cjk/`, and `messages_fts_cjk`; old tip archived at `archive/pre-v20260804/fts5-cjk-bigram-index`.

### 32. search-slow-query-log — MERGED UPSTREAM (2026-08-04)
Equivalent routing-path slow-query logging landed upstream as `8364576e33`; old tip archived at `archive/pre-v20260804/search-slow-query-log`.

### 33. fts-v2-config-authority — MERGED UPSTREAM (2026-08-04)
Fork `messages_fts_v2` never shipped upstream; v2026.8.3 owns `messages_fts_cjk` configuration. Old tip archived at `archive/pre-v20260804/fts-v2-config-authority`; deployed fork-v2 tables require operator cleanup post-deploy.

### 34. model-routing
- **branch:** `soju/patches/model-routing`
- **stacked-on:** `soju/patches/hook-prepend-command-safety` (2026-09-14 chronological tip stack)
- **upstream_pr:** _(none — ADR-003 Phase 1; enum'd delegation aligns with upstream `delegation-model-routing` policy, natural round-2 candidate once Phases 2–3 prove it)_
- **origin:** `local-author`
- **state:** `local-only`
- **rationale:** ADR-003 Phases 1+2 — model routing SoT as a core subsystem. Phase 1: config-declared `model_routes:` catalog (per route: description, provider, model, reasoning_effort, accepted membership, ordered fallbacks), loader with startup cross-validation against `providers:`, and a health-aware resolver (`resolve_route`) that walks default→fallbacks with TTL-cached fail-open provider probes ported from skill-gate's runtime_catalog (401/403 healthy, credit-sniffed 400, 402/429/5xx unhealthy; config-first kill switch `model_routes.health.enabled` + `HERMES_MODEL_ROUTES_HEALTH` bridge). Phase 2: gateway dynamic router (`gateway/model_router.py` + `_model_router_stage`) — classifier core byte-identical to the skill-gate bench winner (parity-test-pinned; changing it requires the 230+120 bench rerun), `model_routes.router` config (mode off|shadow|enforce + `HERMES_MODEL_ROUTER_MODE` bridge, label_routes, chat_route, streak), static condition rules (short-circuit by design — the plugin's live last-wins is a bug not ported), decision log `~/.hermes/logs/model_router_decisions.jsonl` for shadow-soak comparison, enforce apply with /model parity (#48031 auto-reset survival, pending note, session-DB persist). Dormant with empty config. Phase 3 (`model_switch`/`delegate_task` route enums) builds on this; supersedes patch #5 when Phase 3 lands.
- **commits:**
  - `e481903e98` feat(routing): model_routes catalog + health-aware route resolver (ADR-003 Phase 1)
  - `722af239cf` feat(routing): core dynamic model router with shadow mode (ADR-003 Phase 2)
  - `019f9f81e2` feat(routing): route-enum delegation on delegate_task (ADR-003 Phase 3a)
  - `4293ca31fa` fix(routing): classify the pre-hook message text in the router stage
  - `15b3adf2de` fixup(model-routing): adapt to v2026.7.20 upstream
  - `0faf55a2c4` test(routing): pin HERMES_MODEL_ROUTER_MODE=off in the hermetic fixture
  - `06a3c0362d` fix(routing): adapt state and config validation seams
  - `336e1676ed` test(routing): delegate route tests follow upstream schema/batch seams (2026-09-14 sync)
- **touches:** `hermes_cli/model_routes.py` (new), `hermes_cli/config.py`, `gateway/model_router.py` (new), `gateway/run.py`, `gateway/slash_commands.py`, `tools/delegate_tool.py`, `run_agent.py`, `cli-config.yaml.example`, `tests/conftest.py`, `tests/hermes_cli/test_model_routes.py` (new), `tests/gateway/test_model_router.py` (new), `tests/tools/test_delegate.py`
- **v2026.8.3-sync:** route state followed the refactored gateway session helpers; config validation moved to `hermes_cli/config_validation.py`; Phase 3a delegation was re-expressed on the current `delegate_task` transport.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/model-routing.md` for relocations/seams.


### 35. audience-personas
- **branch:** `soju/patches/audience-personas`
- **stacked-on:** `soju/patches/model-routing` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — generic and config-off-by-default, upstream candidate once soaked)_
- **state:** `local-only`
- **rationale:** Audience-mode persona injection: when `HERMES_HOME/personas/modes.yaml` exists, a mode is selected per session as a pure deterministic function of session-constant inputs (platform/chat_type/chat_id/chat_name/user_id) — first-match rules (string-or-list exact match, chat_name case-insensitive, missing key = wildcard, AND), non-owner guard applied after rules (non-empty `user_id` not in `owners[platform]` forces `guards.non_owner_mode`; missing owners entry fails safe; empty user_id skips), `default_mode` fallback — and the mode's persona markdown is injected as stable-tier slot #2 right after the SOUL.md/DEFAULT_AGENT_IDENTITY identity block, through the same threat scan + truncation cap as SOUL.md. With no modes.yaml the build is byte-identical (strict no-op); all failure paths degrade to the no-op at DEBUG. Cache correctness: an `AudienceMode: <mode>` line joins the volatile tail only while active, and `_stored_prompt_matches_runtime` recomputes the expected mode via a cheap mode-only resolver (both-absent passes → pre-deploy stored prompts stay valid; mismatch/one-sided rebuilds exactly once per session). SOUL.md plumbing parity: `personas/` in profile clone + default-export include set, `hermes_config_mod` threat pattern extended to `.hermes/personas/` paths. The `artifact_register` section of modes.yaml is a tone-gate plugin contract, deliberately not consumed by core.
- **commits:**
  - `ecb315ade3` feat(prompt): audience-mode persona injection from personas/modes.yaml
- **touches:** `agent/audience_persona.py` (new), `agent/system_prompt.py`, `agent/conversation_loop.py`, `run_agent.py`, `hermes_cli/profiles.py`, `tools/threat_patterns.py`, `tests/agent/test_audience_persona.py` (new)
- **2026-08-26-sync:** refreshed the declared model-routing ancestry to current tip `99aebe694`; the dependency correction restores the per-call delegation credential seam while leaving audience selection behavior unchanged.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/audience-personas.md` for relocations/seams.


### 36. model-switch-provider-dedupe
- **branch:** `soju/patches/model-switch-provider-dedupe`
- **origin:** `local-author`
- **upstream_pr:** [#66128](https://github.com/NousResearch/hermes-agent/pull/66128)
- **state:** `pending-upstream`
- **rationale:** `/model <bare-model>` hard-fails with "declared by multiple configured providers (<slug>, custom:<display name>)" when the model is declared by exactly ONE `providers.<slug>` entry, because `_configured_provider_matches` counts the entry and its `get_compatible_custom_providers` legacy view separately. Fix canonicalizes match collection: a custom row is attributed to its originating providers slug via the `provider_key` stamp (or name+base_url identity). Genuine ambiguity — two different endpoints, including display-name collisions — still errors (regression-pinned). Found by the ADR-003 E2E harness (D1).
- **commits:**
  - `f491809bb2` fix(model_switch): dedupe self-duplicate provider views in typed-model routing
- **touches:** `hermes_cli/model_switch.py`, `tests/hermes_cli/test_model_switch_configured_provider_routing.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/model-switch-provider-dedupe.md` for relocations/seams.


### 37. memory-phase0
- **branch:** `soju/patches/memory-phase0`
- **stacked-on:** `soju/patches/audience-personas` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — ADR-004 dogfood; revisit after shadow soak)_
- **state:** `local-only`
- **rationale:** ADR-004 Phase 0 memory-redesign plumbing: `pending/` WAL (durable per-turn buffer for external memory sync), L0-mirror local evidence journal for outgoing memory payloads, `_memory_ingest_disabled` per-agent flag (read-only memory forks), MEMORY.md/USER.md char-cap defaults unified on the config SoT. Journal review fixes: atomic appends, marker-only boundary mirroring, per-directory startup scan, directories pinned at construction.
- **commits:**
  - `9098d13cb3` feat(memory): _memory_ingest_disabled per-agent flag — read-only memory forks (ADR-004 Phase 0)
  - `b1c9793ba7` feat(memory): pending/ WAL — durable per-turn buffer for external memory sync (ADR-004 Phase 0)
  - `217847b471` feat(memory): L0-mirror — local evidence journal for outgoing memory payloads (ADR-004 Phase 0)
  - `3971d5e139` fix(memory): unify MEMORY.md/USER.md char-cap defaults on config SoT (ADR-004 Phase 0)
  - `e28a3a4950` fix(memory): pin journal directories at construction, not at write time
  - `f4e5f99595` test(memory): exercise prefetch with a substantive query
- **touches:** `agent/memory_journal.py` (new), `agent/agent_init.py`, `agent/agent_runtime_helpers.py`, `agent/background_review.py`, `agent/conversation_compression.py`, `agent/memory_manager.py`, `agent/tool_executor.py`, `agent/turn_context.py`, `run_agent.py`, `tools/memory_tool.py`, `tools/delegate_tool.py`, `cli-config.yaml.example`, `tests/agent/test_memory_{ingest_disabled,l0_mirror,pending_wal}.py` (new), docs/website memory pages
- **v2026.7.20-sync:** re-stacked on memory-write-reason-gate; turn_context gates re-expressed on upstream turn_context.py (7b3dcee92); conversation_compression gate adapted to upstream memory_context capture.
- **2026-08-26-sync:** replayed the phase-0 commits onto corrected durable-turns so the shared turn-context seam preserves both resume and memory-prefetch invariants.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/memory-phase0.md` for relocations/seams.


### 38. memory-phase1
- **branch:** `soju/patches/memory-phase1`
- **stacked-on:** `soju/patches/memory-phase0` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — ADR-004 dogfood)_
- **state:** `local-only`
- **rationale:** ADR-004 Phase 1 notes tier: `NotesStore` declarative notes under the citation contract, `notes_write`/`notes_read`/`memory_propose` tool family, two-step token contract with grounded admission and gated backfill seam. Review fixes close secret-scrub bypasses, add real quote grounding, non-destructive supersede, write-gate parity.
- **commits:**
  - `8800111753` fix(memory): journal review fixes — atomic appends, marker-only boundary mirroring, per-directory startup scan
  - `d894667451` feat(memory): NotesStore — declarative notes tier under the citation contract (ADR-004 Phase 1)
  - `8c4975ad45` feat(memory): notes write pipeline — two-step token contract, grounded admission, gated backfill seam (ADR-004 §③)
  - `e2c45f24ea` feat(memory): notes tool family — notes_write/notes_read/memory_propose wiring + prompt guidance (ADR-004 Phase 1)
  - `a7c5b2bd49` fix(memory): notes review fixes — close the secret-scrub bypasses, real quote grounding, non-destructive supersede, write gate parity (ADR-004 Phase 1)
  - `eccdcbc13e` test(run_agent): list the notes tools in the post-hook ownership cases (2026-09-14 sync)
- **touches:** `agent/notes_store.py` (new), `agent/memory_pipeline.py` (new), `tools/notes_tool.py` (new), `agent/agent_runtime_helpers.py`, `agent/memory_journal.py`, `agent/memory_manager.py`, `agent/prompt_builder.py`, `agent/system_prompt.py`, `agent/tool_executor.py`, `tools/memory_tool.py`, `tools/delegate_tool.py`, `toolsets.py`, `tests/agent/test_{memory_pipeline,notes_store,managed_values_arity}.py` (new), `tests/tools/test_notes_tool.py` (new)
- **v2026.7.20-sync:** tool-name unions re-resolved against v2 parent chain (runtime-control tools union at assembly); _dispatch_notes_tool re-anchored above upstream's finalize= signature (271a9d8ec).
- **2026-08-26-sync:** replayed phase 1 onto the corrected phase-0/durable chain; no phase-1 behavior changed.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/memory-phase1.md` for relocations/seams.


### 39. memory-phase2-taint
- **branch:** `soju/patches/memory-phase2-taint`
- **stacked-on:** `soju/patches/memory-phase1` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — ADR-004 dogfood)_
- **state:** `local-only`
- **rationale:** ADR-004 §① Phase 2 origin-taint machinery: injected-span registry, WAL/mirror span tagging, quote-taint enforcement; spans registered at the prefetch and memory-tool result sites. Review fixes: registry singleton hygiene, WAL/mirror tag coverage, floor-not-round registration timestamps.
- **commits:**
  - `23b2a8ff96` feat(memory): origin-taint machinery — injected-span registry, WAL/mirror span tagging, quote-taint enforcement (ADR-004 §① Phase 2)
  - `57c3b87888` feat(memory): register injected memory spans at the prefetch and memory-tool result sites (ADR-004 §① Phase 2)
  - `6a92587060` fix(memory): origin-taint review fixes — registry singleton hygiene, WAL/mirror tag coverage, floor-not-round registration ts (ADR-004 Phase 2)
- **touches:** `agent/memory_taint.py` (new), `agent/agent_runtime_helpers.py`, `agent/memory_journal.py`, `agent/memory_manager.py`, `agent/memory_pipeline.py`, `agent/tool_executor.py`, `agent/turn_context.py`, `run_agent.py`, `tests/agent/test_memory_taint.py` (new), `tests/agent/test_memory_{ingest_disabled,pending_wal}.py`
- **v2026.7.20-sync:** prefetch taint registration combined at assembly with durable-turns' resume guard and waterfall span (see Assembly Integration Fixes).
- **2026-08-26-sync:** replayed phase-2 taint onto corrected phase 1 so the declared ancestry and combined prefetch seam remain current.
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/memory-phase2-taint.md` for relocations/seams.


### 40. memory-phase2-curator
- **branch:** `soju/patches/memory-phase2-curator`
- **stacked-on:** `soju/patches/memory-phase2-taint` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — ADR-004 dogfood; shadow-mode observation ongoing)_
- **state:** `local-only`
- **rationale:** ADR-004 Phase 2 ingest curator: fork recipe, verdict schema, shadow ledger, watermark; curator triggers + `curator_verdict` dispatch wiring. Review fixes: seam scrub+grounding, provenance validation, cross-lane taint interface. Runs shadow-only until the cutover gate.
- **commits:**
  - `1cbce24122` feat(memory): ingest curator core — fork recipe, verdict schema, shadow ledger, watermark (ADR-004 Phase 2)
  - `6f445ea29e` feat(memory): ingest curator triggers + curator_verdict dispatch wiring (ADR-004 Phase 2)
  - `ce5f1c959c` test(memory): ingest curator Phase-2 suite — shadow invariant, fork isolation, triggers, caps (ADR-004)
  - `a2003fd49f` fix(memory): ingest curator review fixes — seam scrub+grounding, provenance validation, cross-lane taint interface (ADR-004 Phase 2)
  - `038df67ce5` test(run_agent): list curator_verdict in the post-hook ownership cases (2026-09-14 sync)
- **touches:** `agent/ingest_curator.py` (new), `agent/agent_runtime_helpers.py`, `agent/background_review.py`, `agent/codex_runtime.py`, `agent/conversation_compression.py`, `agent/memory_journal.py`, `agent/memory_manager.py`, `agent/memory_pipeline.py`, `agent/tool_executor.py`, `agent/turn_finalizer.py`, `hermes_cli/config.py`, `run_agent.py`, `tests/agent/test_ingest_curator.py` (new)
- **2026-08-26-sync:** replayed phase-2 curator onto corrected phase 1 so the declared ancestry remains current; no curator behavior changed.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/memory-phase2-curator.md` for relocations/seams.


### 41. cron-secret-scope-env-fallback — MERGED UPSTREAM (2026-08-04)
Landed via PR #69057; old tip archived at `archive/pre-v20260804/cron-secret-scope-env-fallback`.


### 42. anthropic-picker-suppression
- **branch:** `soju/patches/anthropic-picker-suppression`
- **origin:** `local-author`
- **upstream_pr:** _(none)_
- **state:** `local-only`
- **rationale:** Codex CLI model picker offered Anthropic models even when Anthropic credentials are suppressed for the session; respect the suppression flag when building picker candidates. Recovered from the live deployment checkout (was deployed as an unmanifested commit on top of the old production assembly, 2026-07-22).
- **commits:**
  - `8105b94173` fix(model-picker): respect Anthropic credential suppression
- **touches:** `hermes_cli/model_switch.py`, `tests/hermes_cli/test_codex_cli_model_picker.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/anthropic-picker-suppression.md` for relocations/seams.


### 43. daemon-pool-py314-compat
- **branch:** `soju/patches/daemon-pool-py314-compat`
- **origin:** `local-author`
- **upstream_pr:** [#69209](https://github.com/NousResearch/hermes-agent/pull/69209)
- **state:** `pending-upstream`
- **rationale:** `DaemonThreadPoolExecutor` mirrors CPython 3.8–3.13 `ThreadPoolExecutor._adjust_thread_count` internals; CPython 3.14 moved per-worker state into `prepare_context()`/`WorkerContext` and changed `_worker`'s signature, so every `submit()` dies with `AttributeError: '_initializer'` (all concurrent tool batches + background memory sync). Branch on `hasattr(ThreadPoolExecutor, "prepare_context")` and pass matching worker args on both interpreter families. Hoisted from a memory-phase0 rebase collateral to a stack-root patch (2026-07-22).
- **commits:**
  - `f5438a68c2` fix(compat): support CPython 3.14 ThreadPoolExecutor internals in DaemonThreadPoolExecutor
- **touches:** `tools/daemon_pool.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/daemon-pool-py314-compat.md` for relocations/seams.


### 44. skill-manage-reason-schema
- **branch:** `soju/patches/skill-manage-reason-schema`
- **origin:** `local-author`
- **upstream_pr:** _(none — companion to the external skill-gate plugin's ADR-004 §② admission gate)_
- **state:** `local-only`
- **rationale:** The skill-gate plugin hard-requires a structured JSON `reason` on `skill_manage` create/edit/patch, but the tool schema never declared the field — schema-following models could not discover the contract and learned it only through blocked-call retry loops (a 2026-07-22 session burned dozens of retries). Adds an optional `reason` property documenting the rationale object (claim_kind / execution_evidence tier / evidence_pointer / why_not_note / target / neighbor_skills_checked) and the staged-create behavior. Deliberately NOT `required`: enforcement stays with the fail-closed gate. Harmless without the gate. Ledger counterpart (`skill-rationale-ledger.jsonl`, every gated verdict recorded scrubbed) lives in the skill-gate plugin repo, not this fork.
- **commits:**
  - `6bbe6bbfc7` feat(skills): advertise the structured write rationale in the skill_manage schema
  - `2c22d671f2` feat(skills): type the skill_manage reason rationale as a real JSON-Schema object
  - `6b672cc4ce` test(skills): allow the local `reason` property in the skill_manage schema diet pin
- **touches:** `tools/skill_manager_tool.py`, `tests/tools/test_skill_manager_tool.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/skill-manage-reason-schema.md` for relocations/seams.


### 45. refusal-fallback-reason
- **branch:** `soju/patches/refusal-fallback-reason`
- **stacked-on:** `soju/patches/runtime-control`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — pairs with the local skill-gate plugin's refusal exemption; upstreamable on its own)_
- **state:** `local-only`
- **rationale:** `try_activate_fallback(agent, reason)` received the `FailoverReason` but discarded it, so plugins could not distinguish a safety-refusal fallback (deterministic, content-scoped, restore-next-turn) from rate-limit/5xx failovers. Records `agent._fallback_reason` at the single activation success point, mirrors it at every `_fallback_activated` reset/copy site, exposes `fallback_activated`/`fallback_reason` in `get_runtime_state()`, emits `runtime_state` hook events at activation (`event="fallback"`) and restore (`event="restore"`), and passes the reason at the three conversation-loop refusal call sites (HTTP-200 `content_filter` branch, mid-stream content-filter stub, exception client-error branch → `classified.reason`). Enables the skill-gate refusal-fallback exemption so a refusal-driven claude-opus-5 fallback can perform dev edits for exactly that turn.
- **commits:**
  - `a38ce31658` feat(fallback): record activation reason and publish runtime_state events
  - `70314162b2` test(fallback): cover fallback reason lifecycle and runtime_state hook
  - `7e926ce198` test(fallback): behaviorally cover loop call-site reason forwarding and the transport-recovery reset
  - `48e85a91aa` fix(test): expect the content_policy_blocked reason in the codex content-filter fallback assertion
  - `c1eed405d1` fix(fallback): honor declared api_mode (chain entry or providers.<name>) over URL heuristics
- **touches:** `agent/chat_completion_helpers.py`, `agent/runtime_control.py`, `agent/agent_init.py`, `agent/agent_runtime_helpers.py`, `agent/conversation_loop.py`, `tests/run_agent/test_refusal_fallback_reason.py` (new), `tests/run_agent/test_run_agent.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/refusal-fallback-reason.md` for relocations/seams.


### 46. route-repromote-hysteresis
- **branch:** `soju/patches/route-repromote-hysteresis`
- **stacked-on:** `soju/patches/memory-phase2-curator` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — depends on fork-only model-routing patch #34)_
- **state:** `local-only`
- **rationale:** Route `accepted` membership was absorbing: once a session landed on a non-primary accepted member (DOCUMENT_WORK on claude-opus-5, SYSTEM_DEV on gpt-5.6-sol after a health failover), every turn returned `noop_satisfied` and the route primary (claude-fable-5) was never re-promoted. Adds `repromote_after_turns` (router default 3, per-route override, ≤0 disables): after N trusted noop turns on a non-primary member, the router resolves the route and emits `repromote_to_primary` — only onto the healthy true primary (`source=="default"`), `repromote_held` otherwise with the streak clamped for fast recovery. Classifier path trust-gated to `source=="llm"` (regex-fallback labels are inert), static path always trusted; CHAT gets the same mechanism at `noop_already_chat`; `normal_streak` untouched.
- **commits:**
  - `db6e19b582` feat(model-routes): add repromote_after_turns knob (router default + per-route override)
  - `866715a538` feat(gateway): re-promote sessions from accepted members to the route primary
  - `5ca09193bc` test(gateway): cover route re-promotion hysteresis
- **touches:** `hermes_cli/model_routes.py`, `gateway/model_router.py`, `gateway/run.py`, `cli-config.yaml.example`, `tests/gateway/test_model_router.py`, `tests/hermes_cli/test_model_routes.py`
- **2026-08-26-sync:** refreshed the declared model-routing ancestry to current tip `99aebe694`; re-promotion behavior is unchanged.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/route-repromote-hysteresis.md` for relocations/seams.


### 47. passive-provider-health
- **branch:** `soju/patches/passive-provider-health`
- **stacked-on:** `soju/patches/route-repromote-hysteresis` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — depends on fork-only model-routing patch #34)_
- **state:** `local-only`
- **rationale:** The model-routes health probe was an active 1-token completion with a 2.5s default timeout; claude-lb's end-to-end completion floor is ~3s, so probes deterministically timed out, the primary was misjudged unhealthy, and SYSTEM_DEV sessions silently failed over to gpt-5.6-sol (2026-07-27/28 incident; 19 of 21 switches landed on the fallback). Health is now passive-first: real completion traffic files the verdicts (`record_provider_outcome` — unhealthy from `try_activate_fallback` on outage-shaped `FailoverReason`s with probe-parity classification, healthy-clear on live completion success gated to one `os.stat` via `has_unhealthy_verdicts`), `provider_key_for_runtime` maps the live agent identity (config key / display name / `custom:` slug / base_url) to the `providers:` config key with no-match→no-record, and `provider_health()` only probes as a *recovery check* on a stale unhealthy verdict — no verdict or a stale healthy one is assumed healthy with zero I/O, so steady state never probes and never burns completion tokens.
- **commits:**
  - `e0a4b5c383` feat(routing): passive-first provider health — verdicts from real traffic
- **touches:** `hermes_cli/model_routes.py`, `agent/chat_completion_helpers.py`, `tests/hermes_cli/test_model_routes.py`, `tests/run_agent/test_passive_provider_health.py` (new)
- **2026-08-26-sync:** refreshed the declared model-routing ancestry to current tip `99aebe694`; passive-health behavior is unchanged.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/passive-provider-health.md` for relocations/seams.


### 48. event-loop-db-isolation
- **branch:** `soju/patches/event-loop-db-isolation`
- **origin:** `local-author`
- **upstream_pr:** _(none — production outage hardening)_
- **state:** `local-only`
- **rationale:** Keep SessionDB reads and maintenance work off the gateway event loop and writer hot path, construct hygiene agents off-loop, add loop-stall diagnostics, and lower terminal-child CPU priority. Upstream now has its own liveness watchdog; the fork retains the richer stack/PSI diagnostic watchdog because it serves a distinct incident-analysis contract.
- **commits:**
  - `ecf7f6e152` fix(gateway,state): evict SQLite from the event loop; stall watchdog; child niceness
- **touches:** `gateway/run.py`, `hermes_state.py`, `tools/environments/local.py`, `tools/process_registry.py`, `tests/gateway/test_loop_stall_watchdog.py`, `tests/test_hermes_state.py`, `tests/test_wal_checkpoint_strategy.py`, `tests/tools/test_terminal_child_nice.py`
- **v2026.8.3-sync:** hygiene construction was adapted to the new gateway helpers without weakening upstream's compression commit fence; FTS maintenance follows upstream's `messages_fts_cjk` layout.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/event-loop-db-isolation.md` for relocations/seams.


### 49. token-accounting-schema-repair — MERGED UPSTREAM (2026-08-04)
v2026.8.3 shape-gates `_heal_session_model_usage_pk()` on the actual primary-key layout and rebuilds malformed `session_model_usage`; old tip archived at `archive/pre-v20260804/token-accounting-schema-repair`.

### 50. per-tool-disable
- **branch:** `soju/patches/per-tool-disable`
- **stacked-on:** `soju/patches/passive-provider-health` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — deployment tool-surface control)_
- **state:** `local-only`
- **rationale:** Permit `agent.disabled_toolsets` to subtract individual registered tool names as well as whole toolsets, and propagate the denylist into the Codex hermes-tools MCP sidecar so disabled tools cannot remain visible or callable through that transport.
- **commits:**
  - `98beae553d` fix(codex): honor disabled_toolsets in the hermes-tools MCP sidecar
  - `382a178b4e` feat(toolsets): let disabled_toolsets subtract individual tool names
- **touches:** `model_tools.py`, `agent/transports/hermes_tools_mcp_server.py`, `cli-config.yaml.example`, `tests/test_model_tools.py`
- **2026-08-26-sync:** rebuilt the integration merge on corrected durable-turns; its delta remains only the four per-tool-disable files and contains no worker-pool addition or reversion. Re-anchored the availability test from service-gated TTS to always-present core tools so it exercises per-tool subtraction rather than optional service configuration.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/per-tool-disable.md` for relocations/seams.


### 51. compaction-prompt-cc-upgrades
- **branch:** `soju/patches/compaction-prompt-cc-upgrades`
- **origin:** `local-author`
- **upstream_pr:** _(none — compaction fidelity dogfood)_
- **state:** `local-only`
- **rationale:** Improve compaction fidelity with a chronological analysis pre-pass, ordered preservation of user messages, and exact fenced snippets for interrupted work, while keeping stored summaries free of analysis tags and retaining upstream's reference-only safety contract.
- **commits:**
  - `e21d8d1cac` feat(compression): port three /compact prompt techniques from Claude Code
- **touches:** `agent/context_compressor.py`, `tests/agent/test_compression_prompt_upgrades.py`
- **v2026.8.3-sync:** exact in-flight snippet guidance moved into Active State; the upstream-removed proactive Historical In-Progress heading was not reintroduced.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/compaction-prompt-cc-upgrades.md` for relocations/seams.


### 52. anthropic-signature-passthrough
- **branch:** `soju/patches/anthropic-signature-passthrough`
- **origin:** `local-author`
- **upstream_pr:** _(none — trusted proxy continuity)_
- **state:** `local-only`
- **rationale:** Allow explicitly configured Anthropic signature-passthrough proxies to retain signed thinking blocks across tool turns, while remaining fail-closed for all other providers and stripping both replay channels during reactive invalid-signature recovery.
- **commits:**
  - `1f2d87b240` feat(anthropic): trust signature-passthrough proxies for thinking replay
  - `ecb2313044` fix(config): register anthropic_signature_passthrough as a known provider key
- **touches:** `agent/anthropic_adapter.py`, `agent/conversation_loop.py`, `hermes_cli/config.py`, `tests/agent/test_anthropic_signature_passthrough.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/anthropic-signature-passthrough.md` for relocations/seams.


### 53. notes-recognition-grounding
- **branch:** `soju/patches/notes-recognition-grounding`
- **stacked-on:** `soju/patches/per-tool-disable` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — ADR-004 recognition-assist follow-up)_
- **state:** `local-only`
- **rationale:** Preview grounding evidence when proposing notes and allow admission to ground against the current user turn only at write time, while preserving mechanical taint rejection and the existing citation contract.
- **commits:**
  - `1ba43e2d35` feat(memory): add recognition grounding preview
  - `624f54d8a0` feat(memory): defer current-turn quote grounding
  - `8860ea742f` docs(memory): document grounding assist contract
- **touches:** `agent/memory_pipeline.py`, `agent/notes_store.py`, `tools/notes_tool.py`, `tests/agent/test_memory_pipeline.py`, `tests/tools/test_notes_tool.py`, `IMPLEMENTATION-NOTES.md`
- **2026-08-26-sync:** replayed recognition grounding onto corrected phase-2 taint to retain the declared edge.
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/notes-recognition-grounding.md` for relocations/seams.


### 54. worker-cpu-hygiene
- **branch:** `soju/patches/worker-cpu-hygiene`
- **origin:** `local-author`
- **upstream_pr:** _(none — 2026-08-05 loop-stall follow-up)_
- **state:** `local-only`
- **rationale:** Stop worker threads from starving the gateway event loop of GIL time. lifecycle_guard tokenized each command three times per nesting level (tokenize once, share the segment list); checkpoint_manager's dir walks used Path.rglob (rewritten on os.scandir with identical early-stop/symlink semantics).
- **commits:**
  - `eecbce7c5b` perf(cron,tools): stop worker threads from starving the event loop of GIL time
- **touches:** `cron/lifecycle_guard.py`, `tools/checkpoint_manager.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/worker-cpu-hygiene.md` for relocations/seams.


### 55. lifecycle-guard-nul-fallback
- **branch:** `soju/patches/lifecycle-guard-nul-fallback`
- **stacked-on:** `soju/patches/worker-cpu-hygiene`
- **origin:** `local-author`
- **upstream_pr:** _(none — #76762 follow-up on the fallback path)_
- **state:** `local-only`
- **rationale:** The terminal tool's `read_remote_script` callback re-read paths the local bounded reader had already skipped as binaries, so invoking any binary by path inside a gateway session fed decoded machine code back into the recursion and crashed `os.open` with `ValueError: embedded null byte`, killing the whole terminal call. Skip NUL-bearing callback content like the local binary skip, and tolerate `ValueError` in `_read_referenced_script` to match the existing `resolve()`-level tolerance.
- **commits:**
  - `e2371ac98d` fix(cron): stop lifecycle guard remote fallback from resurrecting binaries
- **touches:** `cron/lifecycle_guard.py`, `tests/hermes_cli/test_gateway_restart_loop.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/lifecycle-guard-nul-fallback.md` for relocations/seams.


### 56. refusal-risk-routing
- **branch:** `soju/patches/refusal-risk-routing`
- **stacked-on:** `soju/patches/notes-recognition-grounding` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood refusal-risk preemptive routing)_
- **state:** `local-only`
- **rationale:** Gateway model router gains an orthogonal `refusal_risk` boolean (plus confidence) so hard frontier-refusal turns are preemptively switched to low-refusal routes before the primary model contaminates context. Label flow S1–S7 stays benched and byte-compatible; S0 only sets the orthogonal flag. Config `model_routes.router.refusal` (default disabled) maps SYSTEM_DEV/FRONTEND_DEV → PERMISSIVE_DEV (kimi-k3) and NORMAL/DOCUMENT_WORK → PERMISSIVE_CHAT (grok-4.5) when confidence ≥ threshold and source is LLM. Enforce-mode applied refusal switches emit an owner-visible platform notice via existing `_deliver_platform_notice` (config-gated). Follow-up tune: S0 explicitly false for draft-edit/tone-down of existing NSFW text and own-infra audits; true only for from-scratch NSFW authoring and third-party attack intents. Bench on 300 gold cases @0.85: precision 0.992 / recall 0.928; grok subset 1.0/1.0.
- **commits:**
  - `1a90b98fb3` feat(gateway): refusal-risk preemptive routing with permissive routes
  - `765b65714f` tune(gateway): narrow S0 refusal for draft-edit vs authoring
- **touches:** `gateway/model_router.py`, `gateway/run.py`, `hermes_cli/model_routes.py`, `tests/gateway/test_model_router.py`, `tests/hermes_cli/test_model_routes.py`
- **2026-08-26-sync:** replayed the refusal-risk delta onto the corrected production prefix so it no longer carries stale durable/memory integration state. Refreshed the topic after the durable ledger correction so its assembled prefix no longer restores the redundant post-redelivery `resume_pending` clear.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/refusal-risk-routing.md` for relocations/seams.


### 57. refusal-api-fallback
- **branch:** `soju/patches/refusal-api-fallback`
- **stacked-on:** `soju/patches/refusal-risk-routing` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood reactive refusal recovery)_
- **state:** `local-only`
- **rationale:** When the primary model returns API-level safety refusal (`content_filter` / `FailoverReason.content_policy_blocked`), walk generic `fallback_providers` (opus) first, then resolved PERMISSIVE routes (dev→chat or chat→dev from primary runtime). Gated by `model_routes.router.refusal.enabled` + new `api_fallback` (default false). Preserves `_fallback_reason=content_policy_blocked` for skill-gate turn exemption. Non-refusal reasons unchanged.
- **commits:**
  - `901e800efd` feat(fallback): content_policy_blocked prefers PERMISSIVE routes
  - `20c768af46` fix(fallback): content_policy walks generic before PERMISSIVE
- **touches:** `agent/chat_completion_helpers.py`, `hermes_cli/model_routes.py`, `tests/hermes_cli/test_model_routes.py`, `tests/run_agent/test_refusal_fallback_reason.py`
- **2026-08-26-sync:** replayed the API-refusal fallback delta onto the corrected refusal-risk prefix, removing its stale stack integration. Refreshed the exact dependency edge to amended refusal-risk tip `418d95344`, retaining the single claim-time delivery-ledger clear.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/refusal-api-fallback.md` for relocations/seams.


### 58. refusal-hop-clean-fork
- **branch:** `soju/patches/refusal-hop-clean-fork`
- **stacked-on:** `soju/patches/refusal-api-fallback` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood refusal masking + auto-switch)_
- **state:** `local-only`
- **rationale:** When a refusal is detected (hard: `content_policy_blocked` at turn end; soft: classifier `prior_refusal` on the previous assistant turn), mask ONLY the refusal message row (`active=0`, row preserved, `compacted` untouched) and one-shot route the next turn to PERMISSIVE. Replaces the preemptive `rewrite_transcript` that hard-deleted the whole session with no refusal evidence. Mid-turn clean-fork scope cut to the current turn (completed history preserved). `prior_refusal` classifier field costs zero extra API calls (router already receives `recent_turns`). Bench 30/30 at precision 1.0 / recall 1.0 after two prompt narrowings (sub-part inability and domain deferral no longer read as refusals). New `deactivate_messages`/`reactivate_messages` are reversible; no hard deletion anywhere.
- **commits:**
  - `068de0af73` feat(refusal): clean-fork history on PERMISSIVE hop
  - `619180c738` feat(refusal): mask refused turn and auto-switch instead of rewriting history
  - `a5390218b6` tune(router): narrow prior_refusal to whole-request refusals
- **touches:** `agent/conversation_loop.py`, `agent/refusal_history.py`, `gateway/model_router.py`, `gateway/run.py`, `gateway/session.py`, `hermes_cli/model_routes.py`, `hermes_state.py`, `tests/`
- **2026-08-26-sync:** replayed the refusal-hop series onto the corrected refusal-API prefix and preserved the runtime-route/refusal-quarantine turn setup at the shared gateway seam. Refreshed the exact refusal-API dependency edge after its ledger correction.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/refusal-hop-clean-fork.md` for relocations/seams.


### 59. refusal-soft-turnend
- **branch:** `soju/patches/refusal-hop-clean-fork`
- **stacked-on:** `soju/patches/refusal-hop-clean-fork` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood soft-refusal detection + fallback hardening)_
- **state:** `local-only`
- **rationale:** Two gaps observed live on thread 1536678317166694441. (1) A soft refusal authored inside a turn (finish_reason=stop, no error) left no turn-end signal, so nothing masked it and no permissive hop was staged. Add `_handle_gateway_soft_refusal` probing the just-produced response through the existing `prior_refusal` classifier field, gated by length floor + config, with a `refusal_recovery_count` loop guard (`max_recovery_hops` default 2) that stops laundering repeat refusals through more models. (2) A refusal-recovery agent whose generic opus hop then died of `overloaded` never reached PERMISSIVE because the refusal walk only triggered on `content_policy_blocked`. Extend it so an active refusal recovery falls through to PERMISSIVE on ANY generic-exhaustion reason, and add logging to the previously silent `_build_refusal_fallback_chain`.
- **commits:**
  - `e9474f0ce1` feat(refusal): detect soft refusals at turn end with recovery-hop guard
  - `1f5a987654` fix(fallback): extend refusal PERMISSIVE chain to generic-exhaustion on any reason
  - `7b8ace7390` Revert "fix(fallback): extend refusal PERMISSIVE chain to generic-exhaustion on any reason"
  - `7695cf0ba6` fix(fallback): log empty/built refusal chain reasons
- **touches:** `gateway/model_router.py`, `gateway/run.py`, `hermes_cli/model_routes.py`, `agent/chat_completion_helpers.py`, `tests/gateway/test_model_router.py`, `tests/hermes_cli/test_model_routes.py`, `tests/run_agent/test_refusal_fallback_reason.py`
- **2026-08-26-sync:** replayed the soft-turn-end continuation on the corrected refusal-hop branch; the intentional generic-exhaustion revert and retained diagnostic logging are unchanged.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/refusal-soft-turnend.md` for relocations/seams.


### 60. anthropic-structured-output
- **branch:** `soju/patches/anthropic-structured-output`
- **stacked-on:** _(none — merged upstream)_
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate)_
- **state:** `merged-upstream`
- **rationale:** Plugin structured completions (`plugin_llm.complete_structured`) build an OpenAI Chat Completions `response_format` payload in extra_body; the anthropic_messages transport forwarded it verbatim and strict Anthropic gateways (claude-lb) reject it with HTTP 400 ("use output_config.format"). Observed live: every discord-thread-autotitle rename failed for 2+ days (1,600+ errors) once the main provider became claude-lb. Fix: `_translate_anthropic_response_format` converts `json_schema`→`output_config.format={"type":"json_schema","schema":S}` and `json_object`→permissive object schema (SDK 0.87.0 has no schema-less JSON mode), merging into any existing `output_config` (adaptive-thinking `effort` coexists) and excluding `response_format` from the raw extra_body passthrough alongside `reasoning`. Async path delegates to the sync adapter via `asyncio.to_thread`, covered by test. Non-Anthropic transports unchanged.
- **commits:**
  - `c67b6662b fix(aux): translate response_format to output_config.format for anthropic transport`
- **touches:** `agent/auxiliary_client.py`, `tests/agent/test_anthropic_structured_output.py`

### 61. hygiene-noprogress-cooldown
- **branch:** `soju/patches/hygiene-noprogress-cooldown`
- **stacked-on:** `soju/patches/anthropic-structured-output`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate)_
- **state:** `local-only`
- **rationale:** Gateway session hygiene retried compression EVERY turn on sessions where the protected tail spans the whole transcript (1M-context model → tail_token_budget 190k; `hygiene_hard_message_limit: 400` keeps re-triggering; the compressor aborts in ~60ms with `failure_class=no_progress` and no cooldown was recorded — 106 warnings over 4 log rotations, one session hit 20 consecutive retries). The gateway then mis-attributed every no-op to "#21301 no session_db on the hygiene agent" even though session_db was bound. Fix: (1) record the durable DB-backed compression-failure cooldown (600s, reason `no_progress: protected tail spans the whole transcript`) in the no_progress branch of `compress_context` — the existing hygiene cooldown check (#74136) then stops the churn; manual `/compress` (force=True) is unaffected; (2) split the #21301 warning into no_progress (INFO) / summary-abort (WARNING) / genuinely-missing-session_db (#21301 wording kept) / other (WARNING); (3) hygiene trigger log now attributes `trigger=hard_message_limit (N)` vs `token_threshold`.
- **commits:**
  - `edb05ff0ab` fix(compression): record cooldown on no_progress hygiene aborts + accurate trigger/abort diagnostics
- **touches:** `agent/conversation_compression.py`, `gateway/run.py`, `tests/gateway/test_session_hygiene.py`, `tests/run_agent/test_compression_abort_state_reset.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/hygiene-noprogress-cooldown.md` for relocations/seams.


### 62. durable-bg-processes
- **branch:** `soju/patches/durable-bg-processes`
- **stacked-on:** `soju/patches/refusal-hop-clean-fork` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate)_
- **state:** `local-only`
- **rationale:** Background tool processes died on every gateway restart via three stacked kill mechanisms: (1) shutdown `process_registry.kill_all()` global sweep in `_stop_impl`, (2) systemd `KillMode=mixed` SIGKILL on the unit cgroup after `TimeoutStopSec`, (3) `ExecStopPost=gateway.cgroup_cleanup` per-PID SIGKILL of every cgroup survivor. Checkpoint recovery (`processes.json`) only ever adopted crash survivors, and adopted them `detached` with no output. Fix (config-gated, `terminal.durable_background: false` default off, config.yaml only — no tool-schema change): durable non-PTY local spawns run inside `systemd-run --user --scope --unit=hermes-bg-<sid>` (sibling cgroup — escapes 2 and 3; readiness marker + graceful fallback to plain spawn when scopes are unavailable), write output to `$HERMES_HOME/bg-logs/<sid>.log` (0600/0700) as source of truth so post-restart `read_log`/`poll`/notification tails work, and are excluded from the shutdown sweep via `kill_all(exclude_durable=True)`. Explicit `process(action=kill)` still terminates them; scope units are `reset-failed` on completion. Review fix vs executor output: cron `#60432` interrupted-marking made unconditional again — the cron agent is a ThreadPoolExecutor thread inside the gateway process and dies with it even when its durable subprocess survives; gating on `_killed>0` would let a truncated run report success (regression test added). E2E-verified on this host: scope cgroup escape, checkpoint re-adoption in a fresh registry, and full output readback after simulated restart.
- **commits:**
  - `44e3d4212f` Add durable local background processes
- **touches:** `tools/process_registry.py`, `gateway/run.py`, `hermes_cli/config_defaults.py`, `tests/tools/test_process_registry.py`, `tests/gateway/test_gateway_shutdown.py`, `tests/gateway/test_cron_active_work_drain.py`
- **2026-08-26-sync:** replayed the durable-background delta on the corrected prefix, preserving worker niceness and background-first wait escalation. Corrected the shared `Popen` kwargs so argv is passed exactly once and niceness is applied exactly once.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/durable-bg-processes.md` for relocations/seams.


### 63. route-aware-outage-fallback
- **branch:** `soju/patches/route-aware-outage-fallback`
- **stacked-on:** `soju/patches/durable-bg-processes` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — model_routes is fork-only (#34); upstream has no route subsystem to be aware of)_
- **state:** `local-only`
- **rationale:** Outage-shaped failures (`rate_limit`, `billing`, `upstream_rate_limit`, `overloaded`, `server_error`) walked only the global `fallback_providers:` chain, which has no route context and lands dev-routed sessions on a chat-tier model — silently demoting them and tripping skill-gate dev_edit capability gates until the quota window resets (2026-08-13: claude-lb model-quota-exhausted, 13h window, 90 fable→opus fallbacks in one day). Fix: `try_activate_fallback` resolves the CURRENT route via `runtime_satisfies_route` and walks that route's healthy `fallbacks:` (skipping unhealthy/self/unresolvable entries) BEFORE the global chain. Content-policy refusals unchanged (still generic-then-PERMISSIVE, patch #57). Non-routed runtimes and deployments without model_routes behave exactly as before.
- **commits:**
  - `eb38fd058e` feat(fallback): route-aware outage fallback prefers current route fallbacks
  - `a93f95356b` fix(fallback): resolve the outage route from recorded intent, not model id
- **touches:** `agent/chat_completion_helpers.py`, `agent/runtime_control.py`, `gateway/run.py`, `tests/run_agent/test_fallback_helpers.py`, `tests/run_agent/test_runtime_control.py`
- **followup (2026-08-13):** the first cut was inert. `_build_outage_route_fallback_chain` identified the current route by scanning for the first route the live model satisfies, but membership is ambiguous — `claude-fable-5` is the DOCUMENT_WORK fallback AND the SYSTEM_DEV/FRONTEND_DEV primary, and DOCUMENT_WORK sorts first. A SYSTEM_DEV session resolved as DOCUMENT_WORK, whose only fallback is claude-fable-5 itself; that entry was skipped as self-targeting, the chain came back empty, and the global chain demoted the session to opus regardless. Fix: record the route the session actually selected (`_active_route_name`, set by `dispatch_model_switch` on the resolved/no-op/effort-only outcomes and carried across gateway pre-dispatch agent rebuilds) and prefer it while the live runtime still satisfies it; stale or absent intent falls back to the old scan. `model_status` shared the same ambiguity (reported DOCUMENT_WORK/CHAT for dev sessions) and now consults the same intent.
- **2026-08-26-sync:** replayed the route-aware fallback delta onto the corrected durable-background prefix; the corrected earlier topics already preserve pending runtime-route state, so the stale integration-repair commit was no longer needed. Refreshed the exact refusal-API dependency edge after its ledger correction.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/route-aware-outage-fallback.md` for relocations/seams.


### 64. fts-v2-orphan-hardening
- **branch:** `soju/patches/fts-v2-orphan-hardening`
- **stacked-on:** _(none — applies directly on base)_
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate; needs the v2 generation present to be observable)_
- **state:** `local-only`
- **rationale:** `hermes sessions optimize-storage` aborted with `error in trigger messages_fts_v2_delete after rename: vtable constructor failed: messages_fts_v2`, leaving the deployed DB stuck on the pre-v23 layout at 20.0 GB for 4 GB of messages (~14 GB of duplicate index copies) — this is the operator follow-up flagged in the 2026-08-04 sync, now fixed in code rather than by hand. `messages_fts_v2` (`tokenize='cjk_unicode61'`) was superseded by `messages_fts_cjk` (`f13f845116`) but a DB that stopped mid-transition keeps the v2 vtable, its three `AFTER INSERT/UPDATE/DELETE ON messages` triggers and `fts_v2_*` markers while no code references it. Two defects made that residue fatal: (1) `_demote_legacy_fts_to_trash` collected shadows with a `messages_fts_%` LIKE glob that also matches sibling generations' shadows — renaming them re-parses the v2 triggers and the vtable cannot be constructed without the loadable `cjk_unicode61` tokenizer, rolling back the migration; (2) nothing retired the orphan, so every re-run failed identically. Fix (single shared path, sync+async callers inherit): derive the shadow set from the vtables actually being demoted using FTS5's fixed shadow suffixes and match exactly; retire `messages_fts_v2` via a **named allowlist, not a prefix scan** (a future generation must not be droppable for merely looking similar), removing trigger/vtable definitions through `writable_schema` so no tokenizer is needed and handing shadows to the existing chunked teardown. Preflight is fail-closed: messages present, live base index covering every row, integrity check clean, rowcount parity — otherwise the orphan is left untouched. Note the orphan also breaks plain `PRAGMA integrity_check` (`no such tokenizer`), so the DB could not be verified with stock sqlite3 while it existed. Verified RED/GREEN by reverting `hermes_state_search.py` alone (3 fail → 27 pass on base) and live on the affected host: 20041.3 MB → 8164.3 MB (11.9 GB reclaimed), `integrity_check` ok, no v2/trash residue, `messages` 1,268,987 = `messages_fts` 1,268,987 with rows still arriving mid-run, gateway serving throughout.
- **commits:**
  - `ffd12e2cce` fix(state): retire orphaned FTS v2 during storage optimize
- **touches:** `hermes_state_search.py`, `tests/test_fts_v2_orphan_hardening.py`, `tests/test_state_db_malformed_repair.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/fts-v2-orphan-hardening.md` for relocations/seams.


### 65. read-file-binary-sniff
- **branch:** `soju/patches/read-file-binary-sniff`
- **stacked-on:** _(none — applies directly on base)_
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate; the sniff path is stock upstream code)_
- **state:** `pending-upstream`
- **rationale:** `read_file` refused valid UTF-8 Korean text with "Binary file - cannot display as text" — intermittently, and with no relation to file content type. Root cause is a two-stage mismatch in `tools/file_operations.py`: the binary sniff samples the first 1000 **bytes** (`head -c 1000`, ~L1182) while the terminal backend decodes stdout with `errors="replace"`, so a 3-byte Hangul or 4-byte emoji straddling byte 1000 leaves 1-3 trailing U+FFFD; `_is_likely_binary` (~L901) treats any U+FFFD in the sample as binary. That FFFD check is a deliberate fail-safe (it stops a read→edit→write roundtrip from overwriting undecodable bytes with mojibake) and is preserved — only the truncation artifact is excluded. Whether a given file trips this is pure byte-boundary luck, which is why it looked unreproducible: `personas/private.md` (2686 B) hits FFFD at cuts 1000/1024/2048, while `work.md` happens to land on a character boundary and reads fine; editing a file shifts the boundary and the symptom appears or vanishes. Fix: pass `sample_truncated=(file_size > 1000)` from the read path and, only in that case, strip a trailing run of at most 3 U+FFFD before the check. Untruncated samples keep byte-identical behavior, mid-sample FFFD still flags binary (genuine mojibake is not tail-only), and a trailing run of 4+ is not a boundary artifact so it still flags. Verified RED/GREEN by reverting `tools/file_operations.py` alone against the new tests (4 fail → 70 pass), then live: the reference doc describing this very bug was itself unreadable via `read_file` before the fix.
- **commits:**
  - `baf5aba930` fix(tools): don't flag UTF-8 text as binary when head -c splits a multibyte char
- **touches:** `tools/file_operations.py`, `tests/tools/test_file_operations.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/read-file-binary-sniff.md` for relocations/seams.


### 66. lifecycle-guard-multiline
- **branch:** `soju/patches/lifecycle-guard-multiline`
- **stacked-on:** `soju/patches/lifecycle-guard-nul-fallback`
- **origin:** `local-author`
- **upstream_pr:** _(none — #77131/#76762 third-generation follow-up; upstream candidate)_
- **state:** `local-only`
- **rationale:** The lifecycle guard split commands on newlines and shell-tokenized every line, so multiline `python3 -c "..."` / Python heredoc payloads were parsed as shell: a payload line like `sys.path.insert(0, os.path.expanduser('~/.hermes/hermes-agent'))` tokenized into a `/`-bearing token, got treated as a referenced-script path, resolved to a real directory, and `_read_referenced_script`'s non-regular fail-closed marked it unsafe — blocking benign terminal commands inside gateway sessions (29 blocked instances / 13 unique commands in request dumps; 9 of 13 were this false positive). Fix removes only syntactically proven non-shell regions before matching and tokenization: a complete quoted argument to a Python executable's `-c` (basename `python`/`pythonN`/`pythonN.M`, no `--` before payload), or the body of a complete Python heredoc (quoted/bare delimiter, `<<-` tab-strip honored). Unclosed quotes, missing heredoc terminators, shell `-c` payloads, here-strings, and non-Python interpreters stay visible (fail-closed). Preserved detections verified: `hermes gateway restart|stop`, `systemctl`/`launchctl submit|bootstrap`, `bash -c` payload recursion, nested `.sh` walking, binary/NUL skips, and lifecycle commands before/after sanitized payloads. Tokenize-once-per-level contract (patch #54) kept: sanitize once, share the segment list. Verified via TDD (3 RED → GREEN), 100-test file pass, 490-test cron+restart-loop pass, 12 adversarial cases, and historical replay (9/13 freed, 3 genuine lifecycle still blocked, 1 dump-truncation artifact correctly fail-closed).
- **commits:**
  - `a571793c11` fix(cron): stop lifecycle guard from shell-parsing python payloads
- **touches:** `cron/lifecycle_guard.py`, `tests/hermes_cli/test_gateway_restart_loop.py`
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/lifecycle-guard-multiline.md` for relocations/seams.


### 67. scoped-exhaustion-upstream-classify
- **branch:** `soju/patches/scoped-exhaustion-upstream-classify`
- **stacked-on:** `soju/patches/lifecycle-guard-multiline`
- **origin:** `local-author`
- **upstream_pr:** _(none — depends on claude-lb-specific message contract; fork-only)_
- **state:** `local-only`
- **rationale:** claude-lb returns HTTP 503 when ONE model's weekly scoped quota is exhausted across its pool while every other model it serves stays healthy (body carries the spec-locked `model-quota-exhausted` token; message shape pinned by claude-lb OpenSpec `account-load-balancing` since 2026-08-13, PR Soju06/claude-lb#127). The generic 503 branch classified this as `overloaded`, which (a) is in `_PASSIVE_UNHEALTHY_REASONS`, so one dead model marked the whole `claude-lb` provider unhealthy for fail_ttl (120s) in the model-routes passive health cache while claude-opus-5 on the same provider served fine, and (b) is transport-shaped in the retry loop, burning 2 retries against a condition whose only recovery is a scoped window reset days out. Fix classifies the token as `upstream_rate_limit` — the existing "one model at an aggregator, not the provider" reason (OpenRouter upstream-429 precedent): deliberately excluded from passive-unhealthy marking, `is_rate_limited`-eager fallback (no wasted transport retries), rotation forced off, route-aware fallback chain applies, and `error_context.upstream_provider` carries the model so the status line reads "Upstream claude-fable-5 rate-limited". Two return sites: the 503/529 status branch (after overflow guards, before the `overloaded` terminal) and `_classify_by_message` (BEFORE `_OVERLOADED_PATTERNS` and `_RATE_LIMIT_PATTERNS` — ordering pinned by test, else "Rate limit exceeded" text would enable credential rotation). 429 branch untouched (regression-pinned: scoped token on 429 stays `rate_limit`). Verified: 73-test classifier file + 134-test fallback/failover/route sweep pass; bare-503 → `overloaded` regression pin preserved verbatim (a delegate attempt to weaken it to an overload-texted message was caught in diff audit and reverted).
- **commits:**
  - `016fee2e3e` fix(fallback): classify scoped model-exhaustion 503s as upstream_rate_limit
- **touches:** `agent/error_classifier.py`, `tests/agent/test_error_classifier.py`
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/scoped-exhaustion-upstream-classify.md` for relocations/seams.


### 68. lifecycle-guard-stmt-boundary
- **branch:** `soju/patches/lifecycle-guard-stmt-boundary`
- **stacked-on:** `soju/patches/lifecycle-guard-multiline`
- **origin:** `local-author`
- **upstream_pr:** _(none — Branch D statement-boundary false positive; upstream candidate)_
- **state:** `local-only`
- **rationale:** `_GATEWAY_LIFECYCLE_PATTERN` Branch D (`p?kill…hermes…gateway`, both token orders) used `[^\n]*` token gaps, so only newlines bounded a match — `;`, `&&`, `|` passed through and stitched INDEPENDENT statements on one physical line into a single "kill the gateway" match. Live reproduction: `kill -USR1 <pid> && echo …; sleep 20; systemctl --user show hermes-gateway-mymel.service -p …` — the kill is the SANCTIONED SIGUSR1 self-restart path and hermes/gateway tokens come from a read-only `systemctl show` unit name three statements later. Counterfactuals verified: newline-separated same content passed, removing the kill passed, renaming the unit passed. Same design-flaw family as #66 (statement boundaries ignored), opposite direction: #66 over-split (newline = new shell statement inside Python payloads), this one under-split (`;&|` not a boundary at all). Fix narrows both Branch D gaps to `[^\n;&|]*`; `&&`/`||` break on their first char so compound operators are covered. Branches A/B/C untouched (their gaps sit between a verb and its own arguments within one statement). Signal whitelisting (-USR1 exemption) considered and rejected as guard-weakening. Real threats preserved: `pkill -f "hermes.*gateway"`, `kill $(pgrep -f …)`/backtick substitution (no `;&|` inside), line-continuation folding, Branch C after `;` prefix. Verified: TDD RED 4→GREEN, file 113 passed, tests/cron 390 passed, 15 adversarial cases.
- **commits:**
  - `fe2cb386a8` fix(cron): stop lifecycle guard Branch D from spanning statement boundaries
- **touches:** `cron/lifecycle_guard.py`, `tests/hermes_cli/test_gateway_restart_loop.py`
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/lifecycle-guard-stmt-boundary.md` for relocations/seams.


### 69. approval-grep-escaped-quote
- **branch:** `soju/patches/approval-grep-escaped-quote`
- **stacked-on:** _(none — applies directly on base)_
- **origin:** `local-author`
- **upstream_pr:** `85922`
- **state:** `pending-upstream`
- **rationale:** Started as a false positive — `grep -n "^from\|^__all__\|^    \"" file.py` was rejected as `hardline: command parser limit or malformed executable payload`, blocking a read-only grep in gateway sessions. Root cause: `detect_hardline_command` fed `_normalize_command_for_detection(command)` to the grep parser, but normalization strips backslash-escapes (`\"` → `"`), so the parser saw an odd number of quotes and reported the parse malformed, which fails closed. Investigating that exposed a far more serious defect in the same quote-state confusion: the hardline floor could be BYPASSED entirely. `_CMDPOS`-anchored hardline patterns do not match `; reboot` directly — they fire only after `_mark_command_starts` inserts a newline at each quote-aware command start. When an escaped quote is present, normalization leaves the quote open (`echo "a"b"; reboot`), the tokenizer sees one unterminated segment, no marker is inserted, and the floor never fires. Verified live on stock base and on the deployed tree: `cat "f\"n.txt"; rm -rf --no-preserve-root /` returned `approved=True` from `check_dangerous_command` — i.e. the yolo-proof floor, which is documented as unbypassable, was bypassable with one escaped quote in front of any command. Fix is three commits, additive only (variant generation is any-match, so adding variants can only tighten the floor): (1) validate grep syntax against faithful shell quote state, using `_mask_quoted_newlines(command)` rather than normalized text; (2) emit an ADDITIONAL detection variant whose command starts are marked BEFORE normalization, using a `" \n"` marker so a preceding backslash cannot swallow the inserted newline as a line continuation; (3) `_mark_command_starts` must not treat an offset inside a `${...}` parameter expansion as a command start — marking inside `${IFS}` produced `${ \nIFS}`, which defeated the IFS-collapse regex in normalization and left `echo "a\"b"; rm${IFS}-rf${IFS}--no-preserve-root${IFS}/` executable and unblocked (confirmed with the safe analogue `echo "a\"b"; echo${IFS}BYPASS_EXECUTED`, rc=0, second statement executed). A single-quote variant (`echo 'a\'b'; reboot`) was investigated and is NOT a bypass — bash rejects it with rc=2 (unterminated quote), so it cannot execute. Verified RED/GREEN per commit (1 → 5 → 4 failing tests), 266 passed across the four affected test files, mutation-killed both new code paths (reverting the param-expansion skip → 4 failures; reverting the faithful-marking variant → 88 failures), and 33 adversarial cases hand-checked against BASE to confirm zero regressions: every remaining difference from base is either fail-safe over-blocking that predates this patch or a non-executable string.
- **commits:**
  - `ceb813e1ac` fix(approval): parse raw grep quote state before hardline checks
  - `5a1c0aa77e` fix(approval): mark command starts from faithful quote state
  - `bdd6590fd9` fix(approval): don't mark command starts inside parameter expansions
- **touches:** `tools/approval.py`, `tests/tools/test_execution_flag_detection.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/approval-grep-escaped-quote.md` for relocations/seams.


### 70. elision-marker-imitation
- **branch:** `soju/patches/elision-marker-imitation`
- **stacked-on:** `soju/patches/route-aware-outage-fallback` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — pending dogfood soak before proposing)_
- **state:** `local-only`
- **rationale:** The compressor rewrote old tool_call arguments and oversized message bodies using the static literal `...[truncated]`. Because that literal is identical at every emission site and always terminates the truncated string, a long session's context accumulates dozens of `string value → marker → close JSON` examples and the model pattern-completes the same shape onto NEW tool calls — `write_file`, `patch`, `terminal`, `execute_code` payloads shipped silently cut off mid-word, producing broken files and unterminated here-docs. The failure is self-propagating: measured on live `state.db` (2026-07-01..08-14), 1,507 fresh imitated calls across 151 sessions (terminal 881, write_file 291, execute_code 217, patch 207, skill_manage 48), and **84% were preceded by the model's own earlier imitation** rather than a fresh compressor rewrite. Disproved the competing hypothesis that payloads are truncated at the tool boundary: 3,019 fresh `write_file` calls parsed with **0** malformed arguments and a 105,084-char `content` was delivered intact, so pre-truncating a payload is never correct. Root cause is shape, not policy — the marker is a constant a model can reproduce. Fix replaces it with `_elision_marker(n)` embedding the exact omitted-character count, which a model composing a fresh payload cannot know, making the marker unreproducible by pattern completion and self-identifying as a compression artifact. Converted every emission site reaching main-model context: `context_compressor` (pass-3 tool_call args shrink, summarizer serialization for tool/assistant/user bodies, deterministic fallback turn, active-task line), `skill_preprocessing` inline-shell output, `trajectory_compressor` summary input, `delegate_tool` goal preview. `hermes_cli/auth.py` deliberately untouched — it renders CLI error text for a human terminal, not model context. JSON-validity contract from upstream `3128d9fcd2` (`_truncate_tool_call_args_json`, shrink inside the parsed structure so strict providers do not 400) is preserved and re-asserted by tests. Verified: 12 new tests + 141 passed across the three affected suites, plus a loop simulation confirming markers differ by payload size.
- **commits:**
  - `213c1f93b6` fix(compression): make elision markers non-imitable by the model
- **touches:** `agent/context_compressor.py`, `agent/skill_preprocessing.py`, `trajectory_compressor.py`, `tools/delegate_tool.py`, `tests/agent/test_elision_marker_guard.py` (new), `tests/agent/test_context_compressor.py`, `tests/test_trajectory_compressor.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/elision-marker-imitation.md` for relocations/seams.


### 71. delegated-child-marker-latch
- **branch:** `soju/patches/delegated-child-marker-latch`
- **stacked-on:** `soju/patches/elision-marker-imitation` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none yet — proposal artifacts ready under `~/work/comm-improve/latch-bug/`; open after dogfood soak)_
- **state:** `local-only`
- **rationale:** A normal gateway session (never called `delegate_task`) got permanently classified as a delegate child — kanban mutations refused, `model_tools` tool-definition cache key poisoned. Primary carrier is the shared bash session snapshot (`/tmp/hermes-snap-<id>.sh`): `_resolve_container_task_id` collapses subagent task ids to `"default"` so parent/child share one snapshot, and `_wrap_command`'s trailing `export -p` write-through persisted the child's `HERMES_DELEGATED_CHILD_CONTEXT=1` into the parent's next turns (D1). Same mechanism defeated `scrub_kanban_env()` on the terminal path: sourcing the snapshot re-exported the dispatcher's `HERMES_KANBAN_*` over the scrubbed Popen env (D2). Secondary: detached daemons (ingest-curator, bg-review, async delegation) copy the delegated-child ContextVar and keep it for the process lifetime (D4); `KANBAN_ENV_KEYS` allowlist drifted to 7 of the dispatcher's 10 injected vars (D6). All four reproduced deterministically without a gateway (`~/work/comm-improve/latch-bug/repro*.py`). Fix: unset both families inside the snapshot dump subshell (same audited mechanism as `HERMES_SESSION_*`; no line-based filtering per #71296); scope-correct ContextVar propagation via keyword-only `inherit_delegated_child=True` with the three detached sites opting out (blanket clear measured to regress: in-turn workers lose the flag and a delegated child can mutate board state — fail-closed default kept); once-per-process latch fingerprint diagnostic; prefix-based kanban env scrub. Snapshot fix covers all environment backends (`_wrap_command` lives on `BaseEnvironment`).
- **commits:**
  - `8a7f4d64e6` fix(delegation): stop delegated-child marker and kanban identity latching across turns
- **touches:** `tools/environments/base.py`, `tools/thread_context.py`, `agent/delegation_context.py`, `agent/ingest_curator.py`, `run_agent.py`, `tools/async_delegation.py`, `tests/tools/test_delegated_child_marker_latch.py` (new)
- **2026-08-26-sync:** replayed the latch fix onto corrected phase-2 curator to retain the declared edge and curator opt-out behavior.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/delegated-child-marker-latch.md` for relocations/seams.


### 72. kanban-board-env-precedence
- **branch:** `soju/patches/kanban-board-env-precedence`
- **stacked-on:** _(none — applies directly on base; verified `git diff --stat base..production -- hermes_cli/kanban_db.py` empty before this patch)_
- **origin:** `local-author` (implementation delegated to codex; design proposal by kanban goal worker t_231c8f71)
- **upstream_pr:** _(none yet — upstream-eligible: file untouched by other fork patches)_
- **state:** `local-only`
- **rationale:** `HERMES_KANBAN_DB` env pin outranked an explicit `board=` argument / `--board` flag in `hermes_cli/kanban_db.py` path resolution, so any process inheriting a worker's env silently wrote to the wrong board's DB while the CLI printed success. Observed live 2026-08-16: `notify-subscribe --board comm-improve` stored its subscription in the default board DB → task-completion notifications never delivered → owner fell back to manual polling (the exact scheduling-crank pattern the comm-improve track exists to remove). Same defect class hit `create`. Diagnostic key: sibling env `HERMES_KANBAN_BOARD` was correctly overridden by `--board`; only `HERMES_KANBAN_DB` had inverted precedence. Fix reorders resolution to explicit-arg → env pin → current-board pointer, preserving the dispatcher→worker handoff contract (argument-less calls still follow the env pin, so workers keep their own board). Verified: isolated repro matrix FAIL→PASS on the patch and FAIL re-confirmed on base; 3 worker-selfboard contracts PASS; kanban suites green with pre-existing failures reproduced on base.
- **commits:**
  - `6699af13a6` fix(kanban): honor explicit board over env pins
- **touches:** `hermes_cli/kanban_db.py`, `tests/gateway/test_kanban_notifier.py`, `tests/gateway/test_kanban_notifier_apiserver_wake.py`, `tests/hermes_cli/test_kanban_boards.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/kanban-board-env-precedence.md` for relocations/seams.


### 73. kanban-goal-judge-unavailable
- **branch:** `soju/patches/kanban-goal-judge-unavailable`
- **stacked-on:** _(none — applies directly on base; `hermes_cli/goals.py` untouched by other fork patches, merge-base verified `3c27eb6234`)_
- **origin:** `local-author` (implemented by kanban goal worker t_126b9f1e per runtime-patches-scope §D)
- **upstream_pr:** _(none yet — upstream-eligible)_
- **state:** `local-only`
- **rationale:** `run_kanban_goal_loop` discarded the judge's `transport_failed` signal: with an unreachable/misconfigured judge the loop fed "continue" every turn until the goal-turn budget exhausted, then auto-blocked with a generic budget message. Observed live twice on 2026-08-17/18 (board comm-improve): 15/15 turns burned in 8 seconds, worker never ran a single tool call, block reason said "budget exhausted" instead of the actual cause (judge 401). The CLI `/goal` path already auto-pauses on judge transport failure — this patch brings the kanban goal path to parity: N consecutive judge transport failures (default 3, config `kanban.judge_failure_limit`) → `kanban_block` with typed reason `judge_unavailable`, preserving worker session state for resume instead of burning budget.
- **commits:**
  - `e390216241` fix(goals): block kanban goal-mode cards early when the judge is unreachable
- **touches:** `hermes_cli/goals.py`, `tests/hermes_cli/test_kanban_goal_mode.py` (new)
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/kanban-goal-judge-unavailable.md` for relocations/seams.


### 74. hygiene-diag-and-lock-offload
- **branch:** `soju/patches/hygiene-diag-and-lock-offload`
- **stacked-on:** `soju/patches/hygiene-noprogress-cooldown`
- **origin:** `local-author` (implemented by kanban goal worker t_a06bd1a3 per runtime-patches-scope §C)
- **upstream_pr:** _(none yet)_
- **state:** `local-only`
- **rationale:** Three hygiene-block fixes from the 2026-08-13 incident forensics. (1) `Session hygiene auto-compress failed: %s` logged empty for `asyncio.TimeoutError` (empty `str()`), which made the incident's 41 failure lines carry no cause — switch to `%r` + exception class name. (2) The cancelled-compression lock release ends in a synchronous SQLite DELETE that appeared in event-loop stall stack dumps — offload via `asyncio.to_thread` (cancellation path; nothing orders against the release, commit already fenced). (3) Expose the hardcoded 0.85 hygiene trigger as `compression.hygiene_threshold` (config.yaml per repo rubric, range-validated, default preserved) with a deduplicated warning-only layering check: `compression.threshold >= hygiene_threshold` means the safety net fires before the primary compressor — the exact inversion that amplified the 08-13 compress storm.
- **commits:**
  - `4eb9172307` fix(gateway): hygiene failure diagnostics + off-loop lock release + hygiene_threshold config
- **touches:** `gateway/run.py`, `tests/gateway/test_session_hygiene.py`
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/hygiene-diag-and-lock-offload.md` for relocations/seams.


### 75. turn-exit-reason-tagging
- **branch:** `soju/patches/turn-exit-reason-tagging`
- **stacked-on:** `soju/patches/delegated-child-marker-latch` (2026-09-14 chronological tip stack)
- **origin:** `local-author` (implemented by kanban goal worker t_9c855d47; last worker-implemented patch before the 2026-08-18 methodology rollback — see mistakes.db #1099)
- **upstream_pr:** _(none yet)_
- **state:** `local-only`
- **rationale:** Early conversation-loop exits (compression lock, 413, stream truncation, rate guard, ~16 paths) returned silently with no log line, making "the agent just stopped" incidents unattributable (comm-improve M5 diagnostics). Tag every early exit via a `_tag_exit()` helper; the pre-existing fallback is renamed `early_return(untagged)` so any future untagged site self-surfaces. Observability-only: no control-flow or return-shape change, tagging failures are swallowed.
- **commits:**
  - `df0e24aafa` feat(telemetry): tag every early conversation-loop exit with its reason
  - `9f8b465797` fix(telemetry): tag upstream interpreter shutdown exit
- **touches:** `agent/conversation_loop.py`, `tests/agent/test_turn_exit_reason_tagging.py`
- **2026-08-26-sync:** replayed exit tagging onto the corrected assembled conversation loop and retained the new upstream interpreter-shutdown exit tag.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/turn-exit-reason-tagging.md` for relocations/seams.


### 76. clean-fork-soft-refusal-only
- **branch:** `soju/patches/clean-fork-soft-refusal-only`
- **stacked-on:** `soju/patches/turn-exit-reason-tagging` (2026-09-14 chronological tip stack)
- **origin:** `local-author` (implemented by codex per /tmp/clean-fork-task.md; design verdict by owner 2026-08-19)
- **upstream_pr:** _(none yet)_
- **state:** `local-only`
- **rationale:** clean_fork (context truncation on refusal fallback) was firing on ALL refusal-shaped signals, including Anthropic structured `stop_reason=refusal` — a contaminated signal claude-fable-5 emits during normal work. Mid-task it cut tool progress from the fallback model's context, causing re-scoping + announce-and-stop early turn ends (2026-08-19 03:23 incident, session 20260813_031938_3171f671). Owner design verdict: clean_fork exists for ROUTER-CLASSIFIED soft refusals (natural-language decline text contaminating context) ONLY; API/provider/stream policy signals still fall back but must preserve full conversation and tool progress. Adds `RefusalSource` enum (5 sources) + `should_apply_clean_fork()` gate with per-decision INFO log; removes the dead auth-path content_policy branch (is_auth and content_policy are mutually exclusive in error_classifier). Incident-replay test asserts tool results survive into the fallback model's input.
- **commits:**
  - `1d57f60e07` fix(refusal): reserve clean fork for router soft refusals
- **touches:** `agent/conversation_loop.py`, `agent/refusal_history.py`, `agent/transports/anthropic.py`, `gateway/model_router.py`, `gateway/run.py`, `hermes_cli/model_routes.py`, `tests/agent/test_refusal_history.py`, `tests/gateway/test_model_router.py`, `tests/run_agent/test_refusal_fallback_reason.py`
- **2026-08-26-sync:** replayed the clean-fork policy delta onto the corrected exit-tagging prefix, preserving both behaviors at the conversation-loop seam.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/clean-fork-soft-refusal-only.md` for relocations/seams.


### 77. sync-20260819-test-seams
- **branch:** `soju/patches/sync-20260819-test-seams`
- **stacked-on:** `soju/patches/runtime-control`
- **origin:** `local-author`
- **upstream_pr:** _(none — sync-only test maintenance)_
- **state:** `local-only`
- **rationale:** The 2026-08-19 upstream seam refactor moved pre-tool plugin blocking from `resolve_pre_tool_block` to `_dispatch_pre_tool_call_hooks`, leaving two runtime-control tests monkeypatching a bypassed seam. Upstream now carries equivalent integration coverage for both agent-level and registry-dispatched blocked tools, so this sync patch deletes the stale duplicates instead of re-anchoring redundant tests.
- **commits:**
  - `cdca3a4e4c` test(runtime): re-anchor pre-tool block tests to _dispatch_pre_tool_call_hooks seam (2026-08-19 sync)
  - `7ae24b34ac` test(run_agent): prompt-cache static-prefix test tolerates the fork's Runtime/Route tail (2026-09-14 sync)
- **touches:** `tests/run_agent/test_run_agent.py`
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/sync-20260819-test-seams.md` for relocations/seams.


### 78. base-persona
- **branch:** `soju/patches/base-persona`
- **stacked-on:** `soju/patches/clean-fork-soft-refusal-only` (2026-09-14 chronological tip stack)
- **origin:** `local-author` (implemented by codex per /tmp/base-persona-prompt.md)
- **upstream_pr:** _(none — extends the not-yet-upstreamed #35; travels with it if #35 is ever proposed)_
- **state:** `local-only`
- **rationale:** modes.yaml gains an optional top-level `base_persona: <file>` key: a personas-dir-relative markdown file prepended (`base + "\n\n" + mode`) to every selected mode persona inside `load_audience_persona`, so shared style/identity rules live in ONE file instead of being duplicated per mode (immediate consumer: fluent-korean Korean-output guideline shared across modes). Injection gating is untouched — the base never changes WHETHER injection happens, only content, so `resolve_audience_mode`/`_persona_stat_ok` (per-turn staleness resolver) stay byte-identical and loader/resolver agreement is preserved. Base file resolves through the same personas-dir containment as mode personas (factored into `_contained_persona_path`) and passes the same `_scan_context_content` + `_truncate_content` pipeline with its own label/read_path. Fail-open: missing/empty/escaping/unreadable base → DEBUG log, mode persona alone; base path == mode path → no self-duplication; key absent → byte-identical to pre-patch behavior.
- **commits:**
  - `42c8db64ab` feat(prompt): optional shared base persona prefix for audience modes
- **touches:** `agent/audience_persona.py`, `tests/agent/test_audience_persona.py`
- **2026-08-26-sync:** refreshed the exact audience-personas dependency edge after its model-routing ancestry correction; base-persona behavior is unchanged.
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/base-persona.md` for relocations/seams.


### 79. gemini-classifier-thinking-compat
- **branch:** `soju/patches/gemini-classifier-thinking-compat`
- **stacked-on:** `soju/patches/base-persona` (2026-09-14 chronological tip stack)
- **origin:** `local-author` (implemented by codex per /tmp/gemini-thinking-gateway-prompt.md)
- **upstream_pr:** _(none — `gateway/model_router.py` router is fork-only)_
- **state:** `local-only`
- **rationale:** `_call_gemini` hardcoded `thinkingConfig: {thinkingBudget: 0}`, which Gemini 3.5+ rejects with HTTP 400 INVALID_ARGUMENT (live smoke 2026-08-20), so any router model upgrade past 3.1 silently drops every turn to the regex fallback. New `_thinking_config(model)` parses the family from the model id: <3.5 keeps `thinkingBudget: 0`, 3.5+ (and unparseable ids) send `thinkingLevel: "low"` (thoughts=0 on 3.5/3.6; 3.7 cannot disable thinking at all and `minimal` is rejected — one reason 3.7 was not adopted). Unblocks the config-side router model change to `gemini-3.5-flash-lite` (gold-set rebench 2026-08-20, 350 cases: acc .983 vs .969, false switches 6→2, p50 -347ms, cost -36%; raw at /mnt/scratch/bench/gemini-router-upgrade/2026-08-20/). Prompts/schemas/regexes untouched — skill-gate byte-identity parity tests unaffected.
- **commits:**
  - `5380588b32` fix(gateway): model-family-aware thinkingConfig for Gemini classifier
- **touches:** `gateway/model_router.py`, `tests/gateway/test_model_router.py`
- **2026-08-26-sync:** refreshed the exact base-persona dependency edge; classifier behavior is unchanged.
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/gemini-classifier-thinking-compat.md` for relocations/seams.


### 80. reasoning-route-wire
- **branch:** `soju/patches/reasoning-route-wire`
- **stacked-on:** `soju/patches/gemini-classifier-thinking-compat` (2026-09-14 chronological tip stack)
- **origin:** `local-author` (implemented by codex per /tmp/reasoning-route-fix-prompt.md; diagnosis at /tmp/reasoning-route-wire-diagnosis.md)
- **upstream_pr:** _(none — router/session-override persistence seam is fork-only)_
- **state:** `local-only`
- **rationale:** Probe-verified diagnosis (2026-08-20) confirmed the routed reasoning happy path is correct but found three robustness gaps that silently drop routed `reasoning_effort`: (1) router-applied reasoning lived only in in-memory SessionState — a gateway restart rehydrated the routed model but not the effort, and `noop_satisfied` never re-stamps it; now persisted (sanitized, non-secret, nested in the existing session model-override record) and rehydrated unless a live explicit session override exists; (2) `try_activate_fallback()` re-resolved reasoning from global config only, overwriting a non-None session/routed config (None with global unset); now preserves the active value and resolves config only when absent — model-specific downgrade stays in the adapter; (3) cached prior-fallback agents let `restore_primary_runtime()` restore a stale non-None snapshot over the freshly assigned per-turn config; TurnRunner now syncs `_primary_runtime.reasoning_config` at assignment. RED reverse-verified: fixes reverted with tests kept → exactly 5 failures. 81 tests green across 8 suites at commit.
- **commits:**
  - `d8559f1eec` fix(gateway): persist routed reasoning across restart and fallback
- **touches:** `gateway/run.py`, `gateway/session.py`, `agent/chat_completion_helpers.py`, `tests/gateway/test_model_router.py`, `tests/gateway/test_session_model_override_persistence.py`, `tests/gateway/test_reasoning_primary_runtime_sync.py`, `tests/run_agent/test_fallback_reasoning_override.py`
- **2026-08-26-sync:** replayed reasoning persistence onto the corrected final prefix and combined it with active-route consumption and auto-reset route-state preservation. Refreshed the exact Gemini-classifier dependency edge after the ancestry repair, and removed the stale redundant post-redelivery `resume_pending` clear absorbed by this final assembled topic.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/reasoning-route-wire.md` for relocations/seams.


### 81. precompress-checkpoint-signal
- **branch:** `soju/patches/precompress-checkpoint-signal`
- **stacked-on:** _(none — applies directly on base `b742be711a15aad543a871d2d3022277b70dc551`; fork changes in `MemoryManager.on_pre_compress` only add `_mirror_boundary(...)` 19 lines above the provider call, while the v2 detection, message selection, call, and exception policy retain upstream provenance)_
- **origin:** `local-author`
- **upstream_pr:** [NousResearch/hermes-agent#97063](https://github.com/NousResearch/hermes-agent/pull/97063)
- **state:** `merged-upstream`
- **rationale:** `MemoryManager.on_pre_compress()` detected checkpoint API v2 providers and selected normalized evidence correctly, but called them without `require_checkpoint`; providers therefore stayed in their default best-effort mode, swallowed durable-write failures, and let required compression proceed as a false success. Forward the flag only to providers advertising the requested checkpoint API version, preserving the strict one-argument `on_pre_compress(self, messages)` contract for bundled and third-party v1 providers. Regression probes cover required/best-effort v2 signaling, legacy signature compatibility, and required-mode failure propagation.
- **commits:**
  - `721c2db6286f7a6c9f0398bea34fd829f49498ff` fix(memory): forward checkpoint requirement to v2 providers`
- **touches:** `agent/memory_manager.py`, `tests/agent/test_pre_compress_checkpoint_contract.py`
- **v2026.9.11-sync:** DROPPED — merged upstream: git cherry TARGET shows '-' for 721c2db628; branch left at its pre-sync tip (`backup/sync-0914/patches/precompress-checkpoint-signal`), not part of the production stack.


### 82. conversation-started-date-stable
- **branch:** `soju/patches/conversation-started-date-stable`
- **stacked-on:** _(none — applies directly on base `b742be711a15aad543a871d2d3022277b70dc551`; `agent/system_prompt.py` is also touched by sibling patches (runtime-route prefix, base-persona) but in disjoint regions — new helpers land after `_append_tui_embedded_clarifier`, the render-site edit replaces only the `timestamp_line` f-string, and `invalidate_system_prompt` gains a capture block above its existing body)_
- **origin:** `local-author`
- **upstream_pr:** none — **do not file.** Upstream independently fixed this on 2026-08-29, one day before this patch: `11b98a1429` (#97930, two-line conversation clock salvaging #96224) derives the date deterministically from the session id's embedded `YYYYMMDD_HHMMSS` stamp via `_session_start_like`, walking to the lineage root (`get_conversation_root`) so compaction rotation keeps the original birth date; `514707ff3e` (#98426) pairs it with always-rebuild-at-commit-boundary. A PR would be closed `implemented_on_main`. (Discovered at PR-prep time, 2026-08-30 — grep upstream/main before filing, per hermes-fork-new-patch.md.)
- **state:** `local-only` — **drop at next base bump**: any base containing `11b98a1429` supersedes this patch (identical symptom, upstream's session-id-derived approach preferred over this patch's memo+stored-prompt recovery). Expect the rebase to surface a conflict in `agent/system_prompt.py`'s timestamp block; resolve by taking upstream wholesale and deleting this patch's helpers + test file.
- **rationale:** `build_system_prompt` re-rendered `Conversation started:` from `now()` on every rebuild (compaction boundary, restore without a stored prompt), so a long-lived session's prompt claimed it began today — live `state.db` showed a session started Aug 26 carrying `Conversation started: Sunday, August 30` after its Aug 30 compaction. Factually wrong for the model and a pure prefix-cache loss. Freeze the rendered value per session: session-scoped `(session_id, value)` memo on the agent, seeded by `invalidate_system_prompt` from the prompt it discards (compaction path) or recovered from stored prompt bytes on restore (last matching line, shape-validated so quoted lookalikes in project context/memory are never adopted); only a genuinely new session renders `now()`. No DB read on the build path (`test_fresh_build_when_no_history` pins that contract). Unit + full-build integration tests (real `AIAgent`, patched `hermes_time.now`).
- **commits:**
  - `aa826025cb87e4838bcb9de4c706398853d5f28b` fix(prompt): freeze Conversation started date to the session's first build
- **touches:** `agent/system_prompt.py`, `tests/agent/test_conversation_started_freeze.py` (new)

### 83. todo-continuity-parity
- **branch:** `soju/patches/todo-continuity-parity`
- **stacked-on:** `soju/patches/reasoning-route-wire` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** none — upstreamable (generic harness gap, no fork-only deps); file after live-session soak
- **state:** `pending-upstream`
- **rationale:** Same-model (fable-5) sessions lose their todo plan mid-session while Claude Code drives its plan to completion. Root cause measured in CC v2.1.226: (1) a `todo_reminder` attachment re-surfaces the todo list every N turns (`TURNS_SINCE_WRITE: 10, TURNS_BETWEEN_REMINDERS: 10`) — Hermes re-injected todos only at compression boundaries, so plans silently aged out of attention (measured: 64% of recent gateway sessions never tracked todos; 14% of todo sessions ended with unfinished pending/in_progress items); (2) CC's TodoWrite result carries a continuation nudge ("Please proceed with the current tasks if applicable") — Hermes returned bare JSON; (3) CC's system prompt carries end-to-end approval-scope + last-paragraph completion guidance — TASK_COMPLETION_GUIDANCE had neither. Fix: periodic reminder through the cache-safe tool-tail channel (same as /steer + run-budget wrap-up; counters on the agent, zeroed by the todo handler in both dispatch paths), CC's verbatim continuation message in a new `message` field of write results (reads stay message-free; hydration json.loads contract preserved), CC TodoWrite description adapted onto the todo schema (+ optional `active_form`/`activeForm` item label), and CC's scope/completion paragraphs appended to TASK_COMPLETION_GUIDANCE verbatim.
- **commits:**
  - `5953d7d1de` feat(agent): Claude Code todo-continuity parity — periodic reminder, continuation nudge, end-to-end scope guidance
- **touches:** `agent/conversation_loop.py`, `agent/agent_init.py`, `agent/tool_executor.py`, `agent/agent_runtime_helpers.py`, `agent/prompt_builder.py`, `tools/todo_tool.py`, `tests/agent/test_todo_reminder.py` (new)
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/todo-continuity-parity.md` for relocations/seams.


### 84. lean-single-aux-call
- **branch:** `soju/patches/lean-single-aux-call`
- **stacked-on:** `soju/patches/todo-continuity-parity` (chronological tip stack; file overlap with #51/#60/#70 in `agent/context_compressor.py` — verified their load-bearing symbols survive: `_LEAN_USER_MESSAGES_HEADING`, `_truncate_tool_call_args_json`)
- **origin:** `cherry-pick:4f22543509d1b91dc45bcb369447126c5eb14fb7`
- **upstream_pr:** 98628 (merged upstream 2026-09, ahead of our base) — **do not file.** Auto-drops at the next base bump: any base containing `4f22543509` supersedes this patch
- **state:** `merged-upstream`
- **rationale:** Lean-mode compaction's `_build_chunk_digests()` issued up to `_LEAN_DIGEST_MAX_CHUNKS=28` strictly sequential auxiliary LLM calls per compaction attempt (one per 72K-char chunk). Measured live 2026-09-04: 800k-token sessions took 956-1440s to compact and were fence-cancelled at the 600s ceiling (`commit_fence_cancelled`), then re-attempted every turn. Upstream hit the same symptom (#96603 "Summarizing Thread extremely slow", 7-11 min stalls) and fixed it by deleting the digest loop: the detailed identifier-preserving session log is folded into the ONE main summary request, oversized regions are evenly sampled with an exact-count elision marker. Cherry-pick resolution: kept the fork-base compressor around upstream-only summary-route pinning (not imported); docs retain this fork's `tail_mode: legacy` default while describing the single-call lean path; `tests/agent/test_compression_attempt_ownership.py` (upstream attempt-ownership subsystem absent at our base) replaced with a symbol-removal guard. `ed3562bbbc` (moot digest tests) not taken — its only path was fork-base-missing. Verified no regression: broad `-k "compress or compaction or lean"` selector fails the same 81 tests on base and on this tip (all `tools/daemon_pool.py` Python 3.14 `ThreadPoolExecutor._initializer` pre-existing + 1 LSP kill-guard), 0 new failures.
- **commits:**
  - `f5c64fefab` fix(compression): lean compaction makes exactly one auxiliary request per attempt
- **touches:** `agent/context_compressor.py`, `tests/agent/test_compression_attempt_ownership.py`, `tests/agent/test_lean_single_aux_call.py` (new), `evals/compaction/runner.py`, `evals/compaction/results/SCORECARD-2026-08-15.md`, `website/docs/developer-guide/context-compression-and-caching.md`, `website/docs/user-guide/configuration.md`
- **v2026.9.11-sync:** DROPPED — merged upstream: 4f22543509 (Upstream-PR 98628) is an ancestor of v2026.9.11; residue was fork-base-only (S3 report); branch left at its pre-sync tip (`backup/sync-0914/patches/lean-single-aux-call`), not part of the production stack.


### 85. aux-anthropic-mode-max-tokens
- **branch:** `soju/patches/aux-anthropic-mode-max-tokens`
- **stacked-on:** `soju/patches/todo-continuity-parity` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** none — upstreamable (generic gate mismatch, no fork-only deps). File after live soak; see rationale for the related open upstream issue and the four rejected blanket-forward PRs that bound how narrow the upstream PR must stay
- **state:** `pending-upstream`
- **rationale:** Related upstream context: open issue 60388 (`max_tokens` settings silently dropped); four blanket "forward caps to all providers" PRs (60454/60513/60620/60645) were closed unmerged, so any upstream PR must honor the declared `api_mode` as one more anthropic-compat signal and nothing broader. Defect: `_build_call_kwargs()` forwarded an explicit auxiliary `max_tokens` only when the endpoint was detected as anthropic-compatible by provider name (`minimax*`) or a `/anthropic` URL path. A config-declared provider with `api_mode: anthropic_messages` and a plain-host `base_url` (our `claude-lb` at `http://10.8.0.3:2455`) matched neither, so the cap was dropped and `AnthropicAuxiliaryClient.create()` → `build_anthropic_kwargs()` substituted the model's default output ceiling (`_ANTHROPIC_DEFAULT_OUTPUT_LIMIT = 128_000`). Transport selection (`_maybe_wrap_anthropic`) already honored `api_mode == "anthropic_messages"`; cap forwarding used a narrower gate. Measured live 2026-09-04 via claude-lb `debug_artifacts`: compressor requested `max_tokens=1400` per digest, proxy captured `max_tokens: 128000` on the wire; 77/77 compression calls exceeded the cap (avg 6,844 out, max 22,863) and latency was linear in output tokens (13.17 ms/tok, R²=0.961) — ~80% of compaction wall time. Fix: `_build_call_kwargs(..., api_mode=None)` + `or api_mode == "anthropic_messages"` in the cap gate; all 8 call sites (sync/async primary, same-provider retry, fallback, refreshed fallback) thread the resolved/destination api_mode. `max_tokens=None` behavior unchanged; `_ANTHROPIC_COMPAT_PROVIDERS`/`_is_anthropic_compat_endpoint`/adapter default fallback untouched.
- **commits:**
  - `86c8790140` fix(auxiliary): forward explicit max_tokens when api_mode is anthropic_messages
- **touches:** `agent/auxiliary_client.py`, `tests/agent/test_auxiliary_anthropic_mode_max_tokens.py` (new)
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/aux-anthropic-mode-max-tokens.md` for relocations/seams.


### 86. session-store-lock-offload
- **branch:** `soju/patches/session-store-lock-offload`
- **stacked-on:** `soju/patches/aux-anthropic-mode-max-tokens` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** none — upstreamable in principle (upstream `gateway/session_persistence.py` still runs the UPSERT under `_lock` when `lock_held=True`; probed 2026-09-14 on upstream main: a 1000 ms persistence write blocked `get_model_override` for 855 ms), but the fork version depends on `begin_active_turn` from #21. File after porting to the mixin split.
- **state:** `local-only`
- **rationale:** Same defect class as the 2026-07-28/29 gateway outages (event loop stalled behind SQLite held under a store lock). `SessionStore._save_entry(lock_held=True)` and `_save()` ran the SQLite UPSERT / full `sessions.json` rewrite + fsync while the caller still held the store-wide non-reentrant `_lock`, so every reader on the event loop waited for the write. Fix: capture the serialized entry + revision under `_lock`, persist under `_save_lock` only (`_capture_entry_save_locked` / `_persist_entry_save_locked` / `_persist_routing_data_save_locked`); `mark_turn_active`/`begin_active_turn` take `_save_lock` outermost, release `_lock` around the durable write and repair the row if the live entry was structurally replaced meanwhile; metadata/override/suspend writers use `_save_entry` after releasing the lock; `lock_held=True` on `_save_entry` is now a hard error; `_save(lock_held=True)` survives only for the startup stale-prune path (releases/re-acquires around I/O). Measured on the assembled stack: reader blocked 857 ms → 1 ms during a 1000 ms write. Provenance: written in the live production tree by another session on 2026-08-31 (preserved as `~/work/hermes-cc-parity/live-tree-wip-session-store-20260831.patch`), re-applied after the 2026-09-04 rebuild, running in both gateways since 2026-09-04 10:14 UTC; promoted from uncommitted WIP to this topic on 2026-09-14. `tests/gateway` on the topic tree: only pre-existing `test_health_detailed_returns_ok` (`degraded`, environment) fails; it fails identically on the WIP-free tree.
- **commits:**
  - `f445c74b31` fix(gateway): never hold SessionStore._lock across routing persistence I/O
- **touches:** `gateway/session.py`, `tests/gateway/test_active_turn_recovery.py`, `tests/gateway/test_clean_shutdown_marker.py`, `tests/gateway/test_durable_turn_records.py`, `tests/gateway/test_session_db_recovery.py`, `tests/gateway/test_session_reset_notify.py`, `tests/gateway/test_runtime_override_store_precedence.py` (test double gains a `_save_entry` stub — the only `tests/gateway` delta vs the WIP-free tree; full suite 2026-09-14: 23 pre-existing failures identical on both trees)
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/session-store-lock-offload.md` for relocations/seams.


### 87. sync-20260914-test-seams
- **branch:** `soju/patches/sync-20260914-test-seams`
- **stacked-on:** `soju/patches/session-store-lock-offload` (2026-09-14 chronological tip stack — this is the tip of the whole production stack)
- **origin:** `local-author`
- **upstream_pr:** none — test-only re-seaming of upstream tests to fork gates; nothing to upstream
- **state:** `local-only`
- **rationale:** Upstream v2026.9.11 tests that collide with fork behaviour on the assembled stack, adjusted without touching code under test: five upstream tests call `memory add/replace` without the fork's mandatory `reason` (#2 memory-write-reason-gate) — `test_background_review_memory_scope`, `test_builtin_memory_disabled_surface`, `test_skip_memory_store_65429`; `TestSystemdCgroupIsolation` in `test_process_registry.py` pins the exact spawn argv while stubbing `shutil.which` for every name, so the fork's nice(1) prefix (#48 event-loop-db-isolation) leaked into the expected argv — autouse fixture makes `_nice_argv` the identity inside that class. Companion to #77 (2026-08-19 seams) which gained the prompt-cache static-prefix re-seam in this sync.
- **commits:**
  - `80bcb3a05d7d` test: re-seam upstream tests to fork gates (2026-09-14 sync)
- **touches:** `tests/agent/test_background_review_memory_scope.py`, `tests/agent/test_builtin_memory_disabled_surface.py`, `tests/agent/test_skip_memory_store_65429.py`, `tests/tools/test_process_registry.py`
- **v2026.9.11-sync:** NEW — created 2026-09-14.

### 88. sync-20260914-fixups
- **branch:** `soju/patches/sync-20260914-fixups`
- **stacked-on:** `soju/patches/sync-20260914-test-seams` (2026-09-14 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** none
- **state:** `local-only`
- **rationale:** Post-sync regression fixups found by the full-suite comparison against pristine v2026.9.11 (66 tests failing only on the stack). Code fixes, each annotated with its OWNER TOPIC to be folded in at the next base bump: `_maybe_inject_todo_reminder` only acts on real int counters (#83); `gateway/model_router.py` read_text encoding (#34); display-wiring hooks `_consume_pending_runtime_route_state` / `_consume_refusal_recall_quarantine` read via getattr and `_make_runtime_update_callback` made a staticmethod so upstream's SimpleNamespace runner doubles work (#1/#8/#58); `turn_trace_obj` read via getattr in the queued follow-up path (#15); named `providers.<name>` entries restored to upstream's `provider="custom"` + `requested_provider=<slug>` identity contract — `hermes_cli/runtime_provider_custom.py` is byte-identical to upstream again (#1, drop the 7d690a provider_label hunk at the next bump). Test seams (fork behaviour intentional): auxiliary wire strips `reasoning` (#12); durable `no_progress` cooldown accepted by the rotation-state test (#61); shutdown-drain spy accepts `kill_all(exclude_durable=True)` (#62). Details: `~/work/hermes/upstream-sync-20260914/reports/sync-20260914-fixups.md`.
- **commits:**
  - `c18916d4cc` fix(agent): todo reminder only acts on integer loop counters
  - `bd24a67465` fix(gateway): model_router .env read passes encoding=utf-8
  - `6df65201c6` fix(gateway): turn wiring tolerates bare runner doubles for fork hooks
  - `20af1512b1` fix(gateway): queued follow-up reads turn_trace_obj via getattr
  - `69481b4788` test(agent): re-seam auxiliary wire test to the fork's reasoning strip
  - `22edad0a3f` test(agent): stale-cooldown test allows the fork's durable no-progress cooldown
  - `f5766a017b` test(gateway): settle-window kill_all spy accepts exclude_durable kwarg
  - `1963690e9f` fix(runtime): named custom providers resolve with provider='custom' again
- **v2026.9.11-sync:** NEW — created 2026-09-14.

### 89. sync-20260914-kanban-seams
- **branch:** `soju/patches/sync-20260914-kanban-seams`
- **stacked-on:** `soju/patches/sync-20260914-fixups` (2026-09-14 chronological tip stack — tip of the whole production stack)
- **origin:** `local-author`
- **upstream_pr:** none — test-only
- **state:** `local-only`
- **rationale:** (1) 15 upstream kanban notifier/wake tests isolated their DB purely via the `HERMES_KANBAN_DB` pin; with #72 (explicit board beats the pin) and upstream's notifier now opening boards explicitly they read the real default board. Shared helper `tests/gateway/_kanban_isolation.py::init_isolated_kanban` (same pattern as #72's own seam) applied to 16 pin sites in 5 files; no assertion weakened. Production sets no `HERMES_KANBAN_DB`. (2) ~35 `tests/hermes_cli/test_update_*` tests failed only under xdist on the stack: five upstream tests force fresh imports via `del sys.modules[...]` without restoring, so later tests patched stale module objects and `hermes update` ran un-mocked — **which is what fast-forwarded `main` to upstream and ran real `git checkout main` / `pull --ff-only upstream main` inside a scratch worktree on 2026-09-14 12:43 UTC**. Fixed with `tests/hermes_cli/conftest.py::evict_modules` (restores both the `sys.modules` entry and the parent-package attribute). Details: `~/work/hermes/upstream-sync-20260914/reports/sync-20260914-kanban-seams.md`.
- **commits:**
  - `455cb2715d` test(kanban): isolate notifier tests via HERMES_KANBAN_HOME, not the DB pin
  - `21729888fb` test(hermes_cli): restore evicted modules after fresh-import fixtures
- **v2026.9.11-sync:** NEW — created 2026-09-14.

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
# parallel lane (independent topics on base v2026.9.11; octopus-merged first)
soju/patches/runtime-control
soju/patches/memory-write-reason-gate
soju/patches/todo-progress-display
soju/patches/discord-table-codeblocks
soju/patches/background-review-guardrails
soju/patches/runtime-state-session-split
soju/patches/runtime-route-awareness (stacked on runtime-control)
soju/patches/strict-chat-reasoning-details
soju/patches/discord-home-autothread-fix
soju/patches/runtime-override-rehydrate-credentials (stacked on runtime-route-awareness)
soju/patches/turn-waterfall-tracing
soju/patches/prompt-tail-freeze (stacked on runtime-route-awareness)
soju/patches/gateway-worker-pool
soju/patches/background-first-waits
soju/patches/llm-activity-recap
soju/patches/config-knob-bridges
soju/patches/slow-tool-perf-advisor
soju/patches/model-switch-provider-dedupe
soju/patches/anthropic-picker-suppression
soju/patches/daemon-pool-py314-compat
soju/patches/skill-manage-reason-schema
soju/patches/refusal-fallback-reason (stacked on runtime-control)
soju/patches/event-loop-db-isolation
soju/patches/compaction-prompt-cc-upgrades
soju/patches/anthropic-signature-passthrough
soju/patches/worker-cpu-hygiene
soju/patches/lifecycle-guard-nul-fallback (stacked on worker-cpu-hygiene)
soju/patches/hygiene-noprogress-cooldown
soju/patches/fts-v2-orphan-hardening
soju/patches/read-file-binary-sniff
soju/patches/lifecycle-guard-multiline (stacked on lifecycle-guard-nul-fallback)
soju/patches/scoped-exhaustion-upstream-classify (stacked on lifecycle-guard-multiline)
soju/patches/lifecycle-guard-stmt-boundary (stacked on lifecycle-guard-multiline)
soju/patches/approval-grep-escaped-quote
soju/patches/kanban-board-env-precedence
soju/patches/kanban-goal-judge-unavailable
soju/patches/hygiene-diag-and-lock-offload (stacked on hygiene-noprogress-cooldown)
soju/patches/sync-20260819-test-seams (stacked on runtime-control)
# sequential lane (chronological tip stack on top of the assembled parallel lane, in this order; merge LAST, each contains the previous)
soju/patches/conn-error-fail-fast
soju/patches/durable-turns
soju/patches/hook-prepend-command-safety
soju/patches/model-routing
soju/patches/audience-personas
soju/patches/memory-phase0
soju/patches/memory-phase1
soju/patches/memory-phase2-taint
soju/patches/memory-phase2-curator
soju/patches/route-repromote-hysteresis
soju/patches/passive-provider-health
soju/patches/per-tool-disable
soju/patches/notes-recognition-grounding
soju/patches/refusal-risk-routing
soju/patches/refusal-api-fallback
soju/patches/refusal-hop-clean-fork
soju/patches/refusal-hop-clean-fork (same branch, continues)
soju/patches/durable-bg-processes
soju/patches/route-aware-outage-fallback
soju/patches/elision-marker-imitation
soju/patches/delegated-child-marker-latch
soju/patches/turn-exit-reason-tagging
soju/patches/clean-fork-soft-refusal-only
soju/patches/base-persona
soju/patches/gemini-classifier-thinking-compat
soju/patches/reasoning-route-wire
soju/patches/todo-continuity-parity
soju/patches/aux-anthropic-mode-max-tokens
soju/patches/session-store-lock-offload
soju/patches/sync-20260914-test-seams
soju/patches/sync-20260914-fixups
soju/patches/sync-20260914-kanban-seams (tip of the stack)
# dropped 2026-09-14: soju/patches/precompress-checkpoint-signal (merged upstream)
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

- `run_agent.py::_create_request_openai_client` — upstream's reusable-client body must remain INSIDE turn-waterfall-tracing's `llm.client_create` span.
- `agent/conversation_loop.py` — durable-turns' `resume_turn`/`turn_id` params must be threaded through turn-waterfall-tracing's `run_conversation` → `_run_conversation_impl` split, incl. both wrapper call sites (SILENT auto-merge drop; fixed by `3e84fb9f5`).
- `agent/turn_context.py::memory_prefetch` — durable-turns' `not resume_turn` guard combines with the tracing span wrapper (conflict, recorded).
- `gateway/session.py::from_dict` — runtime-control's three `runtime_*` kwargs must appear exactly once (positional-shuffle conflict, recorded).

- `agent/turn_context.py::on_turn_start/memory_prefetch` — memory-phase0's `memory_ingest_allowed` gate combines with durable-turns' `not resume_turn` guard AND turn-waterfall-tracing's span; memory-phase2-taint's `record_injected_text` block appends after the span-wrapped prefetch (conflicts, recorded).
- `agent/agent_runtime_helpers.py`/`toolsets.py` tool-name lists — union of runtime-control's model tools and memory-phase1's notes tools (conflicts, recorded).
- `gateway/run.py` hygiene compression — event-loop-db-isolation's off-loop construction/binding must preserve upstream's compression commit fence and durable-turn recovery context.
- `agent/memory_pipeline.py` proposal/admission — notes-recognition-grounding must retain memory-phase2-taint's mechanical injected-span rejection while adding preview and deferred current-turn grounding.

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

### 2026-08-04 — base bump 3ef6bbd20 → 3c27eb623 (v2026.8.3)
Full stack rebase and drop pass across the upstream July/August refactors.
- **Dropped with archived tips:** tool-delay-removal (`ce9f6712f`, PR #64172), request-client-reuse (`82e2c9ce4`, PR #73375), async-token-accounting (PR #73359), gateway-persist-trim (`54eafee30`, PR #76916), lsp-idle-reaper (PR #74058), cron-secret-scope-env-fallback (PR #69057), session-db-read-path-split (upstream `_read_ctx()`/per-thread `mode=ro`), fts5-cjk-bigram-index (upstream `cjk_unicode61` + `messages_fts_cjk`), search-slow-query-log (upstream equivalent `8364576e33`), fts-v2-config-authority (superseded by upstream's CJK layout), gateway-max-iterations-config-authority (equivalent per-turn config bridge), and the formerly unmanifested token-accounting-schema-repair (upstream shape-gated `_heal_session_model_usage_pk()`). Every old tip is tagged under `archive/pre-v20260804/`.
- **Reworked:** runtime-control/gateway state rehydration followed the modular gateway helpers; model-routing followed the new session/config-validation seams; durable-turns was expressed through `TurnContext`/`TurnRunner`; event-loop-db-isolation preserved the upstream compression commit fence and current FTS layout; compaction guidance was adapted without restoring the upstream-removed Historical In-Progress section; notes recognition grounding was stacked on memory-phase2-taint to preserve mechanical rejection.
- **Registered from live production:** event-loop-db-isolation, per-tool-disable, compaction-prompt-cc-upgrades, anthropic-signature-passthrough, and notes-recognition-grounding. token-accounting-schema-repair was registered as a merged-upstream tombstone instead of rebased.
- **Operator follow-up:** remove legacy fork `messages_fts_v2` tables/triggers from the deployed `state.db` after rollout; this repository change deliberately does not mutate live state. — **DONE 2026-08-13** via patch #64 `fts-v2-orphan-hardening` (fixed in code, not by hand: the orphan was blocking `optimize-storage` outright). Live DB 20041.3 MB → 8164.3 MB.
- **Unreferenced working branches left untouched:** `soju/patches/runtime-control-core` and `soju/patches/runtime-control-config-sot-guard`.

### 2026-08-26 — base bump 5dd15872a → b742be711 (v2026.8.25)
Restacked the 65 registered topics onto the 2026-08-25 upstream base and rebuilt
the production artifact. Non-mechanical resolutions are recorded here so a
future sync can distinguish intentional re-expression from accidental drift:

- **Absorbed upstream:** `anthropic-structured-output` is an intentional empty
  topic; upstream already contains the structured-output behavior.
- **Runtime/fallback union:** `agent_runtime_helpers.py` keeps the upstream
  tool-call ID pairing, route identity, credential rotation, and provider
  recovery notice, while retaining fork runtime-state restore hooks and the
  union of upstream and fork post-hook tool names.
- **Delegation credentials:** routed calls preserve lazy default credential
  resolution; `/review` calls with `credentials_cfg` resolve eagerly and turn a
  `ValueError` into `tool_error`.
- **Gateway durable turns/config:** upstream raw `max_turns` handling and
  claimed-obligation redelivery were retained; fork knob bridges, durable-turn
  finalization, abandonment notification, and executor-thread agent creation
  were reinserted at the new helper seams.
- **Memory/lifecycle:** memory write guards and upstream builtin-memory config
  hooks coexist with the fork's 2,750-character defaults. Lifecycle topics keep
  Python-payload stripping and statement-boundary kill matching; upstream
  hardening is retained around them.
- **Compression/tests:** dynamic elision markers were combined with upstream
  image-retirement logic and fix-point sizing. Audience-persona help guidance
  follows upstream skill gating; its fake-agent test seam is re-anchored to
  advertise `skill_view`.
