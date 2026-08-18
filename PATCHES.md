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
base_commit:   3c27eb6234bf91b8ceee9e9071591b31e9b148cb
base_tag:      v2026.8.3
base_describe: v2026.8.3
pinned_at:    2026-08-04
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
  - `d120ef780 feat(runtime): agent-callable model_switch / model_status (session-only)`
  - `c12e34b67 fix(runtime): align runtime tool dispatch ownership`
  - `9af5c332b feat(runtime): route-enum self model switching (ADR-003 Phase 3b)`
  - `ec0805074 feat(runtime): route-only model switching (ADR-003 Phase 3c)`
  - `9a2a2dde0 fix(runtime): rehydrate persisted runtime state after gateway refactor`
- **touches:** `agent/agent_init.py`, `agent/agent_runtime_helpers.py`, `agent/runtime_control.py`, `agent/tool_dispatch_helpers.py`, `agent/tool_executor.py`, `gateway/run.py`, `gateway/session.py`, `hermes_cli/plugins.py`, `hermes_cli/runtime_provider.py`, `model_tools.py`, `tests/gateway/test_pre_gateway_dispatch.py`, `tests/gateway/test_session.py`, `tests/gateway/test_session_model_override_routing.py`, `tests/hermes_cli/test_plugins.py`, `tests/hermes_cli/test_runtime_provider_resolution.py`, `tests/run_agent/test_pre_tool_session_id.py`, `tests/run_agent/test_run_agent.py`, `tests/run_agent/test_runtime_control.py`, `tests/run_agent/test_runtime_control_dispatch.py`, `tests/test_model_tools.py`, `tests/tools/test_runtime_control_tool_schema.py`, `tools/runtime_control_tool.py`, `toolsets.py`
- **v2026.8.3-sync:** rehydration moved to the modular gateway startup path; explicit reasoning still wins over upstream's per-model reasoning re-resolution on switch.

### 2. memory-write-reason-gate
- **branch:** `soju/patches/memory-write-reason-gate`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood memory hygiene)_
- **state:** `local-only`
- **rationale:** Memory `add`/`replace` tool calls require an explicit suitability reason explaining why USER/MEMORY is the right store rather than a skill, Graphiti, or session history. The reason is a guardrail only and is not persisted with the entry.
- **commits:**
  - `723eefcb9 feat(memory): require write reason for memory updates`
  - `c3fb1dced test(memory): pass write reason in null-target dispatcher test`
- **touches:** `agent/agent_runtime_helpers.py`, `agent/tool_executor.py`, `tools/memory_tool.py`, `tests/tools/test_memory_tool.py`, `tests/tools/test_memory_tool_schema.py`

### 3. todo-progress-display
- **branch:** `soju/patches/todo-progress-display`
- **origin:** `local-author`
- **upstream_pr:** _(none — Discord/gateway dogfood UX)_
- **state:** `local-only`
- **rationale:** Gateway progress bubbles should show todo item statuses after the todo tool completes, not only a count like `planning N task(s)`. Also flush throttled progress edits when the final queued event has no following tool event.
- **commit:** `65b068b25 feat(gateway): show todo progress details`
- **touches:** `agent/display.py`, `gateway/run.py`, `tests/agent/test_display.py`, `tests/gateway/test_run_progress_topics.py`

### 4. discord-table-codeblocks
- **branch:** `soju/patches/discord-table-codeblocks`
- **origin:** `local-author`
- **upstream_pr:** _(none — Discord/gateway dogfood UX)_
- **state:** `local-only`
- **rationale:** Discord does not render GitHub-flavored markdown pipe tables. Convert detected outbound pipe tables to fenced box-drawing ASCII tables so table responses remain readable, while preserving existing fenced code blocks, CJK wide-character alignment, and long-table chunk codeblock boundaries.
- **commit:** `71795dde2 feat(discord): render markdown tables as codeblocks`
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
- **commit:** `4f0072841 fix: calibrate background self-improvement review`
- **touches:** `agent/background_review.py`, `tests/run_agent/test_review_prompt_class_first.py`

### 7. runtime-state-session-split
- **branch:** `soju/patches/runtime-state-session-split`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood runtime gate correctness)_
- **state:** `local-only`
- **rationale:** Runtime hard gates must see the active session's actual model/provider after context compression rotates the session id. Compression split republishes runtime state for the new session while retaining the old session as task/parent scope, preventing fallback to stale cross-session runtime state.
- **commit:** `3c58b4f5d fix: keep runtime state scoped across compression splits`
- **touches:** `agent/conversation_compression.py`, `tests/agent/test_runtime_state_session_split.py`

### 8. runtime-route-awareness
- **branch:** `soju/patches/runtime-route-awareness`
- **stacked-on:** `soju/patches/runtime-control`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood runtime routing correctness)_
- **state:** `local-only`
- **rationale:** Inject an API-call-time Runtime/Route State block so the agent sees live CurrentRuntime plus current-turn DesiredRoute without calling `model_status`. Trusted pre-dispatch `runtime_override` metadata is normalized into one-shot route state for the routed gateway turn, preserving stale-route protection while leaving post-tool rerouting and NEED_CONTEXT scout mode for later phases.
- **commits:**
  - `456c89cc9 feat(runtime): inject runtime route awareness prompt`
  - `6ca6378f7 test(routing): expect the Runtime/Route block in post-compression system prompts`
- **touches:** `agent/chat_completion_helpers.py`, `agent/conversation_loop.py`, `agent/system_prompt.py`, `docs/runtime-route-awareness.md`, `gateway/run.py`, `tests/agent/test_runtime_route_prompt.py`, `tests/gateway/test_pre_gateway_dispatch.py`
- **v2026.7.20-sync:** `_pending_runtime_route_states` registered in upstream `_CONVERSATION_SCOPED_STATE` funnel; failover system message now composed via `compose_effective_system_prompt` so the Runtime/Route block refreshes on provider failover.
- **note (07-22):** pins fork semantics onto upstream `test_413_compression` exact-equality assertion (Runtime/Route block present in post-compression system prompts; was red on the old stack too).

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
- **commit:** `bb63a5fe2 fix(chat): strip reasoning replay fields for strict chat completions`
- **touches:** `agent/transports/chat_completions.py`, `tests/run_agent/test_strict_api_validation.py`

### 13. discord-home-autothread-fix
- **branch:** `soju/patches/discord-home-autothread-fix`
- **origin:** `local-author`
- **upstream_pr:** _(none — Discord home-channel dogfood routing fix)_
- **state:** `local-only`
- **rationale:** Discord home-channel messages should still auto-create thread conversations when channel controls disable broad channel auto-threading. Restore the home-channel path while keeping explicit channel-control disable behavior available for non-home channels.
- **commit:** `38914e9a6 fix(discord): restore home channel auto-threading`
- **touches:** `gateway/config.py`, `plugins/platforms/discord/adapter.py`, `tests/gateway/test_discord_channel_controls.py`

### 14. runtime-override-rehydrate-credentials
- **branch:** `soju/patches/runtime-override-rehydrate-credentials`
- **stacked-on:** `soju/patches/runtime-route-awareness`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood runtime-control follow-up fix)_
- **state:** `local-only`
- **rationale:** Upstream owns live `switch_model` endpoint rollback and provider-header reapplication; this patch is limited to gateway restart/persistence. A persisted runtime route is eligible only when both `runtime_model` and `runtime_provider` labels exist, then resolves once with `target_model` into one authoritative provider bundle (normalized provider, endpoint, API mode, keyed or keyless credential, command/args, and credential pool). Incomplete or unresolved labels stay durable but dormant while the turn falls back wholly to coherent defaults. Persisted reasoning rehydrates independently, and the latest `/model` or runtime-route writer clears the other durable model-route store without clearing reasoning. State DB coverage verifies DB-only restart and that no secret bundle fields are persisted.
- **commit:** `2756fd512 fix(gateway): re-resolve persisted runtime override bundle`
- **touches:** `gateway/run.py`, `gateway/session.py`, `tests/gateway/test_runtime_override_restart_rehydration.py`, `tests/gateway/test_runtime_override_state_db_restart.py`, `tests/gateway/test_runtime_override_store_precedence.py`, `tests/gateway/test_session_model_override_routing.py`

### 15. turn-waterfall-tracing
- **branch:** `soju/patches/turn-waterfall-tracing`
- **origin:** `local-author`
- **upstream_pr:** _(none — perf instrumentation for local bottleneck hunt)_
- **state:** `local-only`
- **rationale:** Per-turn waterfall tracing to attribute end-to-end turn latency (observed ~30% slower than a minimal agent on the same LLM). New `agent/turn_trace.py` collects wall-clock spans across the whole turn lifecycle — gateway ingest/session-resolve/transcript-load/agent-setup, prologue children (system-prompt restore, early persist, compression preflight, pre-LLM hook, memory prefetch), per-iteration context assembly/request setup/`llm.call` (TTFT + failed attempts) /accounting, tool batches incl. the inter-tool delay sleep, verify gates, finalize children, gateway persist, transport delivery — and emits one JSONL record per turn to `~/.hermes/logs/turn_traces.jsonl`. `agent/turn_trace_render.py` renders terminal/HTML waterfalls and cross-turn p50/p95 summaries with a model-time vs hermes-overhead split. Gated by `HERMES_TURN_TRACE=1` (default off = no-op); tracing failures can never break a turn.
- **commits:**
  - `c45fb6cdd feat(telemetry): per-turn waterfall tracing spans`
  - `02a302dd0 fix(telemetry): carry turn trace across pre-dispatch event replacement`
  - `49b1dbaf9 feat(telemetry): request prefix fingerprints for cache-break diffing`
  - `5ada37e44 fix(telemetry): fingerprint Responses-API payloads too`
  - `df13bf18a feat(telemetry): 4KB-chunk hashes locate in-message cache breaks`
  - `9fff44f89 feat(telemetry): attribute rest-field changes to specific keys`
  - `29d6d1b22 feat(telemetry): HERMES_TURN_TRACE_SYS_TAIL captures system-prompt tail`
- **touches:** `agent/turn_trace.py` _(new)_, `agent/turn_trace_render.py` _(new)_, `tests/agent/test_turn_trace.py` _(new)_, `agent/chat_completion_helpers.py`, `agent/conversation_loop.py`, `agent/tool_executor.py`, `agent/turn_context.py`, `agent/turn_finalizer.py`, `gateway/platforms/base.py`, `gateway/run.py`, `plugins/platforms/telegram/adapter.py`, `run_agent.py`
- **v2026.7.20-sync:** tools.batch span adapted to upstream segmented dispatch (mode=`segmented` + `segments=<n>` tag); `prologue.persist_early` split into `persist_early` + new `prologue.persist_user_turn` following upstream's persist-site split (cross-version p50 continuity caveat).

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
  - `f0cbc2965 feat(cache): prompt-tail freeze — byte-stable gateway system prompts (patch #18)`
  - `a5111e40b feat(prompt): Runtime/Route identity speaks route + model (ADR-003 Phase 3c)` — CurrentRuntime drops raw provider/endpoint/api-mode from the rendered text (they stay in the cache key); renders `route=<NAME|off-catalog>` when a catalog is declared.
- **touches:** `agent/system_prompt.py`, `gateway/run.py`, `tests/gateway/test_prompt_tail_freeze.py`, `tests/agent/test_runtime_route_prompt.py`

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
- **commit:** `dc8f3da46 perf(gateway): size the agent-turn pool from config, default 24`
- **touches:** `gateway/run.py`, `tests/gateway/test_gateway_max_workers.py` _(new)_
- **v2026.7.20-sync:** coexists with upstream per-session turn lease (`19527db73`): the lease serializes per-session, the pool sizes total concurrency.

### 23. conn-error-fail-fast
- **branch:** `soju/patches/conn-error-fail-fast`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate after burn-in)_
- **state:** `local-only`
- **rationale:** Consecutive sub-2s transport failures (connection refused/reset before any bytes) mean the endpoint is down, not congested; with no fallback available the retry loop burned a dozen attempts with growing backoff (observed 13 attempts / 170-250s of user-visible silence per turn against a briefly-down codex-lb). Track the instant-failure streak on TurnRetryState and end the turn with an actionable error after `HERMES_FAST_CONN_FAIL_LIMIT` (default 3, 0 disables) once the fallback chain has had its chance; slow timeouts keep the full retry budget.
- **commit:** `c412e9ad1 fix(agent): fail fast on instant transport-failure streaks`
- **touches:** `agent/conversation_loop.py`, `agent/turn_retry_state.py`, `tests/agent/test_fast_transport_fail_fast.py` _(new)_

### 24. background-first-waits
- **branch:** `soju/patches/background-first-waits`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate after burn-in)_
- **state:** `local-only`
- **rationale:** Models foreground-polled long jobs with chained `process wait` calls (observed 4-5 consecutive 180s waits per turn — total user-facing silence while holding a gateway worker). Background-first: the first full block-wait stays a normal flow (180s window unchanged per user request), but from the SECOND consecutive timed-out wait on the same still-running process, `notify_on_complete` is auto-armed and the tool result instructs the model to end its turn with a summary — the completion re-enters the session as an event and the model explains the result there (the natural LLM recap). `HERMES_PROCESS_WAIT_CAP` tunes the quiet-wait allowance (default 1, 0 disables for completion-bound sessions like kanban workers). The process tool schema and terminal guidance now state the one-wait-per-process contract (one-time tools-hash cache bust on deploy, legitimate).
- **commit:** `7bbc315b4 feat(tools): background-first process waits — escalate chained blocking waits`
- **touches:** `tools/process_registry.py`, `tools/terminal_tool.py`, `tests/tools/test_background_first_waits.py` _(new)_

### 25. llm-activity-recap
- **branch:** `soju/patches/llm-activity-recap`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate after burn-in)_
- **state:** `local-only`
- **rationale:** `display.long_running_notifications` gains a `recap` mode: instead of the terse "⏳ Working — N min — iteration i/max" heartbeat, the gateway asks the auxiliary LLM (compression rail, 8s timeout, ~80 tokens) for a one-line present-tense recap of what the agent is doing — goal + recent tool calls + current wait — in the conversation's language (Claude Code-style). Context-hash caching regenerates only on activity change; failures fall back to the terse heartbeat; in recap mode the bubble is deleted-and-resent (adapters with delete support) so it stays at the thread bottom instead of buried. Complements #24 background-first-waits: waits that should end the turn do, and turns that legitimately run long narrate themselves.
- **commits:**
  - `b6c28429e feat(gateway): LLM activity recap for long-running notifications`
  - `6e19034cf feat(gateway): tune activity recap context from live-session evaluation`
  - `19cd72c95 feat(gateway): recap speaks in the agent's own voice`
  - `79aead6f5 feat(gateway): persona-definition voice fallback for fresh sessions`
- **touches:** `gateway/run.py`, `run_agent.py`, `tests/gateway/test_llm_activity_recap.py` _(new)_

### 26. config-knob-bridges
- **branch:** `soju/patches/config-knob-bridges`
- **origin:** `local-author`
- **upstream_pr:** _(none — fork knob plumbing)_
- **state:** `local-only`
- **rationale:** Fork knobs were env-only, breaking the house convention (config.yaml is authoritative; env is the cross-process carrier/override — see upstream PR #64298). Bridge `agent.process_wait_cap` → `HERMES_PROCESS_WAIT_CAP` (#24) and `agent.fast_conn_fail_limit` → `HERMES_FAST_CONN_FAIL_LIMIT` (#23) in both the startup export block and the per-turn reload bridge. Recap interval already had the upstream `agent.gateway_notify_interval` bridge.
- **commit:** `e20e89607 feat(gateway): config.yaml bridges for fork knobs`
- **touches:** `gateway/run.py`, `tests/gateway/test_fork_knob_config_bridges.py` _(new)_
- **v2026.8.3-sync:** restacked directly on the new base; fork knobs still read managed-scope-overlaid config.

### 27. durable-turns
- **branch:** `soju/patches/durable-turns`
- **stacked-on:** `soju/patches/llm-activity-recap`, `soju/patches/config-knob-bridges`
- **origin:** `local-author`
- **upstream_pr:** _(pending — branch `upstream-pr/durable-turns` pushed to fork, PR body drafted, awaiting owner approval to open)_
- **state:** `local-only`
- **rationale:** Restart recovery was a paper-over: an interrupted turn was abandoned and a synthetic empty user turn told the model to "skip any unfinished work" — the banner's "I'll try to resume where you left off" was implemented by nothing (live incident 2026-07-15, discord thread 1526457680527622247 reduced to "응 오빠, 여기 있어"). Durable turns make the in-flight turn a first-class durable record (`SessionEntry.active_turn`: turn_id/status/boot_id/resume_count) and re-enter the SAME turn on its persisted transcript after a restart: `run_conversation(resume_turn=True)` appends no user row, drops synthetic "Operation interrupted…" closers, completes unanswered tool_calls via existing orphan recovery (side effects = UNKNOWN, never re-executed), and delivers an already-composed final without another model call. Poison cap `HERMES_TURN_RESUME_MAX`/`agent.turn_resume_max` (default 2) abandons repeat offenders with an honest notice. Kill switch `HERMES_GATEWAY_TURN_RESUME`/`agent.gateway_turn_resume` restores legacy behavior. Banner reworded to match reality. See `DECISIONS.md` ADR-002.
- **commit:** `1261a018d feat(gateway): durable turns — same-turn resume across gateway restarts`
- **touches:** `agent/turn_resume.py` _(new)_, `agent/turn_context.py`, `agent/conversation_loop.py`, `agent/turn_finalizer.py`, `run_agent.py`, `gateway/run.py`, `gateway/session.py`, `hermes_cli/config.py`, `tests/agent/test_turn_resume.py` _(new)_, `tests/gateway/test_durable_turn_records.py` _(new)_, `tests/run_agent/test_resume_turn_loop.py` _(new)_, `tests/gateway/restart_test_helpers.py`, `tests/gateway/test_restart_resume_pending.py`
- **v2026.7.20-sync:** reconciled with upstream delivery-obligation ledger (`5854aad8b`): `_redeliver_pending_obligations` force-finishes `active_turn` records for claimed obligations, narrowing `_resume_composed_final` to the ledger-uncovered window; recovery-note bypass rewoven around module-scope `build_resume_recovery_note`.

### 28. hook-prepend-command-safety
- **branch:** `soju/patches/hook-prepend-command-safety`
- **stacked-on:** `soju/patches/durable-turns`, `soju/patches/runtime-control`
- **origin:** `local-author`
- **upstream_pr:** _(none — hardens the fork-only pre_gateway_dispatch prepend action from runtime-control)_
- **state:** `local-only`
- **rationale:** A `pre_gateway_dispatch` `{"action": "prepend"}` rewrote `event.text` before slash dispatch, and `is_command()`/`get_command()` key off `text.startswith("/")` — so any plugin prepend on a command message demoted it into plain chat that fell through to the agent. Live incident 2026-07-15: the inbox-matter-coordinator advisory rewrite (plugin 9ed17c4) prepends an `[INBOX_MATTER ...]` marker on matter-linked threads; `/model` there was answered by the agent's `model_status` tool instead of the interactive picker, and every slash command in those threads broke the same way. Fix: drop prepends (debug log) when the event is a slash command — advisory context is agent-facing and command handlers can't consume it. Plain-chat prepend behavior unchanged.
- **commit:** `7e5a02c82 fix(gateway): drop pre_gateway_dispatch prepends on slash commands`
- **touches:** `gateway/run.py`, `tests/gateway/test_pre_gateway_dispatch.py`
- **v2026.7.20-sync:** restacked — the guarded prepend action is fork-only (runtime-control), so the branch now merges runtime-control before the pick.

### 29. slow-tool-perf-advisor
- **branch:** `soju/patches/slow-tool-perf-advisor`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — candidate once soak data confirms hint uptake)_
- **state:** `local-only`
- **rationale:** Trace mining across 2,605 turns (2026-07-16) showed the dominant avoidable turn latency is agents routing heavy work through `terminal` in shapes with a cheap native equivalent: full-tree scans (`rglob`/`os.walk`/unpruned `find`/bare `grep -r` — 59+32+209 calls) over repos whose `node_modules` holds >1.7M files (observed 88s + 63s back-to-back scans of the same tree in one Discord turn), foreground `sleep` polling (364 calls), and multi-minute foreground jobs pinning session workers — while `search_files` (rg-backed, .gitignore-aware) answers in ~1s and background+`notify_on_complete` re-enters the session for free. Fix is a thin advisory-only shot mirroring the subdirectory-hints append: when a terminal call is slow AND matches an antipattern, append ONE `[perf-advisor]` line to the tool result at the moment the model just paid for the slow call. Kill switch `HERMES_PERF_ADVISOR=0`; thresholds `HERMES_PERF_ADVISOR_MIN_S` (10s), `HERMES_PERF_ADVISOR_FOREGROUND_S` (120s).
- **commit:** `6a92a4226 feat(tools): slow-tool perf advisor — teach cheaper shapes on slow terminal results`
- **touches:** `tools/perf_advisor.py` (new), `agent/tool_executor.py`, `tests/test_perf_advisor.py` (new)

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
- **upstream_pr:** _(none — ADR-003 Phase 1; enum'd delegation aligns with upstream `delegation-model-routing` policy, natural round-2 candidate once Phases 2–3 prove it)_
- **origin:** `local-author`
- **state:** `local-only`
- **rationale:** ADR-003 Phases 1+2 — model routing SoT as a core subsystem. Phase 1: config-declared `model_routes:` catalog (per route: description, provider, model, reasoning_effort, accepted membership, ordered fallbacks), loader with startup cross-validation against `providers:`, and a health-aware resolver (`resolve_route`) that walks default→fallbacks with TTL-cached fail-open provider probes ported from skill-gate's runtime_catalog (401/403 healthy, credit-sniffed 400, 402/429/5xx unhealthy; config-first kill switch `model_routes.health.enabled` + `HERMES_MODEL_ROUTES_HEALTH` bridge). Phase 2: gateway dynamic router (`gateway/model_router.py` + `_model_router_stage`) — classifier core byte-identical to the skill-gate bench winner (parity-test-pinned; changing it requires the 230+120 bench rerun), `model_routes.router` config (mode off|shadow|enforce + `HERMES_MODEL_ROUTER_MODE` bridge, label_routes, chat_route, streak), static condition rules (short-circuit by design — the plugin's live last-wins is a bug not ported), decision log `~/.hermes/logs/model_router_decisions.jsonl` for shadow-soak comparison, enforce apply with /model parity (#48031 auto-reset survival, pending note, session-DB persist). Dormant with empty config. Phase 3 (`model_switch`/`delegate_task` route enums) builds on this; supersedes patch #5 when Phase 3 lands.
- **commits:**
  - `7194cf967 feat(routing): model_routes catalog + health-aware route resolver (ADR-003 Phase 1)`
  - `c599d6388 feat(routing): core dynamic model router with shadow mode (ADR-003 Phase 2)`
  - `9bdf5d2f8 feat(routing): route-enum delegation on delegate_task (ADR-003 Phase 3a)`
  - `3caf5a62c fix(routing): classify the pre-hook message text in the router stage`
  - `6eacb0b3e fixup(model-routing): adapt to v2026.7.20 upstream`
  - `c24949033 test(routing): pin HERMES_MODEL_ROUTER_MODE=off in the hermetic fixture`
  - `3f1cd2a4f fix(routing): adapt state and config validation seams`
- **touches:** `hermes_cli/model_routes.py` (new), `hermes_cli/config.py`, `gateway/model_router.py` (new), `gateway/run.py`, `gateway/slash_commands.py`, `tools/delegate_tool.py`, `run_agent.py`, `cli-config.yaml.example`, `tests/conftest.py`, `tests/hermes_cli/test_model_routes.py` (new), `tests/gateway/test_model_router.py` (new), `tests/tools/test_delegate.py`
- **v2026.8.3-sync:** route state followed the refactored gateway session helpers; config validation moved to `hermes_cli/config_validation.py`; Phase 3a delegation was re-expressed on the current `delegate_task` transport.

### 35. audience-personas
- **branch:** `soju/patches/audience-personas`
- **stacked-on:** `soju/patches/model-routing`
- **origin:** `local-author`
- **upstream_pr:** _(none — generic and config-off-by-default, upstream candidate once soaked)_
- **state:** `local-only`
- **rationale:** Audience-mode persona injection: when `HERMES_HOME/personas/modes.yaml` exists, a mode is selected per session as a pure deterministic function of session-constant inputs (platform/chat_type/chat_id/chat_name/user_id) — first-match rules (string-or-list exact match, chat_name case-insensitive, missing key = wildcard, AND), non-owner guard applied after rules (non-empty `user_id` not in `owners[platform]` forces `guards.non_owner_mode`; missing owners entry fails safe; empty user_id skips), `default_mode` fallback — and the mode's persona markdown is injected as stable-tier slot #2 right after the SOUL.md/DEFAULT_AGENT_IDENTITY identity block, through the same threat scan + truncation cap as SOUL.md. With no modes.yaml the build is byte-identical (strict no-op); all failure paths degrade to the no-op at DEBUG. Cache correctness: an `AudienceMode: <mode>` line joins the volatile tail only while active, and `_stored_prompt_matches_runtime` recomputes the expected mode via a cheap mode-only resolver (both-absent passes → pre-deploy stored prompts stay valid; mismatch/one-sided rebuilds exactly once per session). SOUL.md plumbing parity: `personas/` in profile clone + default-export include set, `hermes_config_mod` threat pattern extended to `.hermes/personas/` paths. The `artifact_register` section of modes.yaml is a tone-gate plugin contract, deliberately not consumed by core.
- **commit:** `fb3e8b288 feat(prompt): audience-mode persona injection from personas/modes.yaml`
- **touches:** `agent/audience_persona.py` (new), `agent/system_prompt.py`, `agent/conversation_loop.py`, `run_agent.py`, `hermes_cli/profiles.py`, `tools/threat_patterns.py`, `tests/agent/test_audience_persona.py` (new)

### 36. model-switch-provider-dedupe
- **branch:** `soju/patches/model-switch-provider-dedupe`
- **origin:** `local-author`
- **upstream_pr:** [#66128](https://github.com/NousResearch/hermes-agent/pull/66128)
- **state:** `pending-upstream`
- **rationale:** `/model <bare-model>` hard-fails with "declared by multiple configured providers (<slug>, custom:<display name>)" when the model is declared by exactly ONE `providers.<slug>` entry, because `_configured_provider_matches` counts the entry and its `get_compatible_custom_providers` legacy view separately. Fix canonicalizes match collection: a custom row is attributed to its originating providers slug via the `provider_key` stamp (or name+base_url identity). Genuine ambiguity — two different endpoints, including display-name collisions — still errors (regression-pinned). Found by the ADR-003 E2E harness (D1).
- **commit:** `c444f2d2b fix(model_switch): dedupe self-duplicate provider views in typed-model routing`
- **touches:** `hermes_cli/model_switch.py`, `tests/hermes_cli/test_model_switch_configured_provider_routing.py`

### 37. memory-phase0
- **branch:** `soju/patches/memory-phase0`
- **stacked-on:** `soju/patches/memory-write-reason-gate`
- **origin:** `local-author`
- **upstream_pr:** _(none — ADR-004 dogfood; revisit after shadow soak)_
- **state:** `local-only`
- **rationale:** ADR-004 Phase 0 memory-redesign plumbing: `pending/` WAL (durable per-turn buffer for external memory sync), L0-mirror local evidence journal for outgoing memory payloads, `_memory_ingest_disabled` per-agent flag (read-only memory forks), MEMORY.md/USER.md char-cap defaults unified on the config SoT. Journal review fixes: atomic appends, marker-only boundary mirroring, per-directory startup scan, directories pinned at construction.
- **commits:**
  - `707459023 feat(memory): _memory_ingest_disabled per-agent flag — read-only memory forks (ADR-004 Phase 0)`
  - `d5c89b66e feat(memory): pending/ WAL — durable per-turn buffer for external memory sync (ADR-004 Phase 0)`
  - `d0739c4a3 feat(memory): L0-mirror — local evidence journal for outgoing memory payloads (ADR-004 Phase 0)`
  - `3117e93fc fix(memory): unify MEMORY.md/USER.md char-cap defaults on config SoT (ADR-004 Phase 0)`
  - `7d8ae47ff fix(memory): pin journal directories at construction, not at write time`
  - `189c30a52 test(memory): exercise prefetch with a substantive query`
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
  - `41b419dba fix(memory): journal review fixes — atomic appends, marker-only boundary mirroring, per-directory startup scan`
  - `8ca2ff409 feat(memory): NotesStore — declarative notes tier under the citation contract (ADR-004 Phase 1)`
  - `569477393 feat(memory): notes write pipeline — two-step token contract, grounded admission, gated backfill seam (ADR-004 §③)`
  - `ecb661a82 feat(memory): notes tool family — notes_write/notes_read/memory_propose wiring + prompt guidance (ADR-004 Phase 1)`
  - `c9d31e1eb fix(memory): notes review fixes — close the secret-scrub bypasses, real quote grounding, non-destructive supersede, write gate parity (ADR-004 Phase 1)`
  - `fa3e886848 fix(memory): unwrap _ManagedToolResult at notes/memory_propose middleware sites` — v2026.8.3 NeMo Relay refactor changed the middleware return type; the fork call sites kept the old 2-tuple unpack and crashed the outer loop on every notes_write/notes_read/memory_propose call
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
  - `24e6b2d72 feat(memory): origin-taint machinery — injected-span registry, WAL/mirror span tagging, quote-taint enforcement (ADR-004 §① Phase 2)`
  - `58081dadc feat(memory): register injected memory spans at the prefetch and memory-tool result sites (ADR-004 §① Phase 2)`
  - `099db7a95 fix(memory): origin-taint review fixes — registry singleton hygiene, WAL/mirror tag coverage, floor-not-round registration ts (ADR-004 Phase 2)`
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
  - `b4ed59777 feat(memory): ingest curator core — fork recipe, verdict schema, shadow ledger, watermark (ADR-004 Phase 2)`
  - `65a1e2863 feat(memory): ingest curator triggers + curator_verdict dispatch wiring (ADR-004 Phase 2)`
  - `3a7a16670 test(memory): ingest curator Phase-2 suite — shadow invariant, fork isolation, triggers, caps (ADR-004)`
  - `7aea03d82 fix(memory): ingest curator review fixes — seam scrub+grounding, provenance validation, cross-lane taint interface (ADR-004 Phase 2)`
  - `9993e1723e fix(memory): unwrap _ManagedToolResult at curator_verdict middleware site` — same class as the memory-phase1 unpack fix (v2026.8.3 Relay refactor)
- **touches:** `agent/ingest_curator.py` (new), `agent/agent_runtime_helpers.py`, `agent/background_review.py`, `agent/codex_runtime.py`, `agent/conversation_compression.py`, `agent/memory_journal.py`, `agent/memory_manager.py`, `agent/memory_pipeline.py`, `agent/tool_executor.py`, `agent/turn_finalizer.py`, `hermes_cli/config.py`, `run_agent.py`, `tests/agent/test_ingest_curator.py` (new)


### 41. cron-secret-scope-env-fallback — MERGED UPSTREAM (2026-08-04)
Landed via PR #69057; old tip archived at `archive/pre-v20260804/cron-secret-scope-env-fallback`.


### 42. anthropic-picker-suppression
- **branch:** `soju/patches/anthropic-picker-suppression`
- **origin:** `local-author`
- **upstream_pr:** _(none)_
- **state:** `local-only`
- **rationale:** Codex CLI model picker offered Anthropic models even when Anthropic credentials are suppressed for the session; respect the suppression flag when building picker candidates. Recovered from the live deployment checkout (was deployed as an unmanifested commit on top of the old production assembly, 2026-07-22).
- **commit:** `249c9348b fix(model-picker): respect Anthropic credential suppression`
- **touches:** `hermes_cli/model_switch.py`, `tests/hermes_cli/test_codex_cli_model_picker.py`

### 43. daemon-pool-py314-compat
- **branch:** `soju/patches/daemon-pool-py314-compat`
- **origin:** `local-author`
- **upstream_pr:** [#69209](https://github.com/NousResearch/hermes-agent/pull/69209)
- **state:** `pending-upstream`
- **rationale:** `DaemonThreadPoolExecutor` mirrors CPython 3.8–3.13 `ThreadPoolExecutor._adjust_thread_count` internals; CPython 3.14 moved per-worker state into `prepare_context()`/`WorkerContext` and changed `_worker`'s signature, so every `submit()` dies with `AttributeError: '_initializer'` (all concurrent tool batches + background memory sync). Branch on `hasattr(ThreadPoolExecutor, "prepare_context")` and pass matching worker args on both interpreter families. Hoisted from a memory-phase0 rebase collateral to a stack-root patch (2026-07-22).
- **commit:** `2b821d7e7 fix(compat): support CPython 3.14 ThreadPoolExecutor internals in DaemonThreadPoolExecutor`
- **touches:** `tools/daemon_pool.py`

### 44. skill-manage-reason-schema
- **branch:** `soju/patches/skill-manage-reason-schema`
- **origin:** `local-author`
- **upstream_pr:** _(none — companion to the external skill-gate plugin's ADR-004 §② admission gate)_
- **state:** `local-only`
- **rationale:** The skill-gate plugin hard-requires a structured JSON `reason` on `skill_manage` create/edit/patch, but the tool schema never declared the field — schema-following models could not discover the contract and learned it only through blocked-call retry loops (a 2026-07-22 session burned dozens of retries). Adds an optional `reason` property documenting the rationale object (claim_kind / execution_evidence tier / evidence_pointer / why_not_note / target / neighbor_skills_checked) and the staged-create behavior. Deliberately NOT `required`: enforcement stays with the fail-closed gate. Harmless without the gate. Ledger counterpart (`skill-rationale-ledger.jsonl`, every gated verdict recorded scrubbed) lives in the skill-gate plugin repo, not this fork.
- **commits:**
  - `5d6d5a29e feat(skills): advertise the structured write rationale in the skill_manage schema`
  - `a28dda4dd feat(skills): type the skill_manage reason rationale as a real JSON-Schema object`
- **touches:** `tools/skill_manager_tool.py`, `tests/tools/test_skill_manager_tool.py`

### 45. refusal-fallback-reason
- **branch:** `soju/patches/refusal-fallback-reason`
- **stacked-on:** `soju/patches/runtime-control`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — pairs with the local skill-gate plugin's refusal exemption; upstreamable on its own)_
- **state:** `local-only`
- **rationale:** `try_activate_fallback(agent, reason)` received the `FailoverReason` but discarded it, so plugins could not distinguish a safety-refusal fallback (deterministic, content-scoped, restore-next-turn) from rate-limit/5xx failovers. Records `agent._fallback_reason` at the single activation success point, mirrors it at every `_fallback_activated` reset/copy site, exposes `fallback_activated`/`fallback_reason` in `get_runtime_state()`, emits `runtime_state` hook events at activation (`event="fallback"`) and restore (`event="restore"`), and passes the reason at the three conversation-loop refusal call sites (HTTP-200 `content_filter` branch, mid-stream content-filter stub, exception client-error branch → `classified.reason`). Enables the skill-gate refusal-fallback exemption so a refusal-driven claude-opus-5 fallback can perform dev edits for exactly that turn.
- **commits:**
  - `ff63b8ba4 feat(fallback): record activation reason and publish runtime_state events`
  - `65abb2cb3 test(fallback): cover fallback reason lifecycle and runtime_state hook`
  - `a3cf311ed test(fallback): behaviorally cover loop call-site reason forwarding and the transport-recovery reset`
  - `b72f76d85 fix(test): expect the content_policy_blocked reason in the codex content-filter fallback assertion`
  - `568526a08 fix(fallback): honor declared api_mode (chain entry or providers.<name>) over URL heuristics`
- **touches:** `agent/chat_completion_helpers.py`, `agent/runtime_control.py`, `agent/agent_init.py`, `agent/agent_runtime_helpers.py`, `agent/conversation_loop.py`, `tests/run_agent/test_refusal_fallback_reason.py` (new), `tests/run_agent/test_run_agent.py`

### 46. route-repromote-hysteresis
- **branch:** `soju/patches/route-repromote-hysteresis`
- **stacked-on:** `soju/patches/model-routing`
- **origin:** `local-author`
- **upstream_pr:** _(none — depends on fork-only model-routing patch #34)_
- **state:** `local-only`
- **rationale:** Route `accepted` membership was absorbing: once a session landed on a non-primary accepted member (DOCUMENT_WORK on claude-opus-5, SYSTEM_DEV on gpt-5.6-sol after a health failover), every turn returned `noop_satisfied` and the route primary (claude-fable-5) was never re-promoted. Adds `repromote_after_turns` (router default 3, per-route override, ≤0 disables): after N trusted noop turns on a non-primary member, the router resolves the route and emits `repromote_to_primary` — only onto the healthy true primary (`source=="default"`), `repromote_held` otherwise with the streak clamped for fast recovery. Classifier path trust-gated to `source=="llm"` (regex-fallback labels are inert), static path always trusted; CHAT gets the same mechanism at `noop_already_chat`; `normal_streak` untouched.
- **commits:**
  - `daa6b80bb feat(model-routes): add repromote_after_turns knob (router default + per-route override)`
  - `e99a334b9 feat(gateway): re-promote sessions from accepted members to the route primary`
  - `da35cdd59 test(gateway): cover route re-promotion hysteresis`
- **touches:** `hermes_cli/model_routes.py`, `gateway/model_router.py`, `gateway/run.py`, `cli-config.yaml.example`, `tests/gateway/test_model_router.py`, `tests/hermes_cli/test_model_routes.py`

### 47. passive-provider-health
- **branch:** `soju/patches/passive-provider-health`
- **stacked-on:** `soju/patches/model-routing`
- **origin:** `local-author`
- **upstream_pr:** _(none — depends on fork-only model-routing patch #34)_
- **state:** `local-only`
- **rationale:** The model-routes health probe was an active 1-token completion with a 2.5s default timeout; claude-lb's end-to-end completion floor is ~3s, so probes deterministically timed out, the primary was misjudged unhealthy, and SYSTEM_DEV sessions silently failed over to gpt-5.6-sol (2026-07-27/28 incident; 19 of 21 switches landed on the fallback). Health is now passive-first: real completion traffic files the verdicts (`record_provider_outcome` — unhealthy from `try_activate_fallback` on outage-shaped `FailoverReason`s with probe-parity classification, healthy-clear on live completion success gated to one `os.stat` via `has_unhealthy_verdicts`), `provider_key_for_runtime` maps the live agent identity (config key / display name / `custom:` slug / base_url) to the `providers:` config key with no-match→no-record, and `provider_health()` only probes as a *recovery check* on a stale unhealthy verdict — no verdict or a stale healthy one is assumed healthy with zero I/O, so steady state never probes and never burns completion tokens.
- **commits:**
  - `bdcac17d5 feat(routing): passive-first provider health — verdicts from real traffic`
- **touches:** `hermes_cli/model_routes.py`, `agent/chat_completion_helpers.py`, `tests/hermes_cli/test_model_routes.py`, `tests/run_agent/test_passive_provider_health.py` (new)

### 48. event-loop-db-isolation
- **branch:** `soju/patches/event-loop-db-isolation`
- **origin:** `local-author`
- **upstream_pr:** _(none — production outage hardening)_
- **state:** `local-only`
- **rationale:** Keep SessionDB reads and maintenance work off the gateway event loop and writer hot path, construct hygiene agents off-loop, add loop-stall diagnostics, and lower terminal-child CPU priority. Upstream now has its own liveness watchdog; the fork retains the richer stack/PSI diagnostic watchdog because it serves a distinct incident-analysis contract.
- **commit:** `16a16c7f4 fix(gateway,state): evict SQLite from the event loop; stall watchdog; child niceness`
- **touches:** `gateway/run.py`, `hermes_state.py`, `tools/environments/local.py`, `tools/process_registry.py`, `tests/gateway/test_loop_stall_watchdog.py`, `tests/test_hermes_state.py`, `tests/test_wal_checkpoint_strategy.py`, `tests/tools/test_terminal_child_nice.py`
- **v2026.8.3-sync:** hygiene construction was adapted to the new gateway helpers without weakening upstream's compression commit fence; FTS maintenance follows upstream's `messages_fts_cjk` layout.

### 49. token-accounting-schema-repair — MERGED UPSTREAM (2026-08-04)
v2026.8.3 shape-gates `_heal_session_model_usage_pk()` on the actual primary-key layout and rebuilds malformed `session_model_usage`; old tip archived at `archive/pre-v20260804/token-accounting-schema-repair`.

### 50. per-tool-disable
- **branch:** `soju/patches/per-tool-disable`
- **origin:** `local-author`
- **upstream_pr:** _(none — deployment tool-surface control)_
- **state:** `local-only`
- **rationale:** Permit `agent.disabled_toolsets` to subtract individual registered tool names as well as whole toolsets, and propagate the denylist into the Codex hermes-tools MCP sidecar so disabled tools cannot remain visible or callable through that transport.
- **commits:**
  - `8742eea6f feat(toolsets): let disabled_toolsets subtract individual tool names`
  - `64ac10926 fix(codex): honor disabled_toolsets in the hermes-tools MCP sidecar`
- **touches:** `model_tools.py`, `agent/transports/hermes_tools_mcp_server.py`, `cli-config.yaml.example`, `tests/test_model_tools.py`

### 51. compaction-prompt-cc-upgrades
- **branch:** `soju/patches/compaction-prompt-cc-upgrades`
- **origin:** `local-author`
- **upstream_pr:** _(none — compaction fidelity dogfood)_
- **state:** `local-only`
- **rationale:** Improve compaction fidelity with a chronological analysis pre-pass, ordered preservation of user messages, and exact fenced snippets for interrupted work, while keeping stored summaries free of analysis tags and retaining upstream's reference-only safety contract.
- **commit:** `277dea254 feat(compression): port three /compact prompt techniques from Claude Code`
- **touches:** `agent/context_compressor.py`, `tests/agent/test_compression_prompt_upgrades.py`
- **v2026.8.3-sync:** exact in-flight snippet guidance moved into Active State; the upstream-removed proactive Historical In-Progress heading was not reintroduced.

### 52. anthropic-signature-passthrough
- **branch:** `soju/patches/anthropic-signature-passthrough`
- **origin:** `local-author`
- **upstream_pr:** _(none — trusted proxy continuity)_
- **state:** `local-only`
- **rationale:** Allow explicitly configured Anthropic signature-passthrough proxies to retain signed thinking blocks across tool turns, while remaining fail-closed for all other providers and stripping both replay channels during reactive invalid-signature recovery.
- **commits:**
  - `0eddba57b feat(anthropic): trust signature-passthrough proxies for thinking replay`
  - `325a155b6 fix(config): register anthropic_signature_passthrough as a known provider key`
- **touches:** `agent/anthropic_adapter.py`, `agent/conversation_loop.py`, `hermes_cli/config.py`, `tests/agent/test_anthropic_signature_passthrough.py`

### 53. notes-recognition-grounding
- **branch:** `soju/patches/notes-recognition-grounding`
- **stacked-on:** `soju/patches/memory-phase2-taint`
- **origin:** `local-author`
- **upstream_pr:** _(none — ADR-004 recognition-assist follow-up)_
- **state:** `local-only`
- **rationale:** Preview grounding evidence when proposing notes and allow admission to ground against the current user turn only at write time, while preserving mechanical taint rejection and the existing citation contract.
- **commits:**
  - `dbd51250e feat(memory): add recognition grounding preview`
  - `12926e627 feat(memory): defer current-turn quote grounding`
  - `02b148dd4 docs(memory): document grounding assist contract`
- **touches:** `agent/memory_pipeline.py`, `agent/notes_store.py`, `tools/notes_tool.py`, `tests/agent/test_memory_pipeline.py`, `tests/tools/test_notes_tool.py`, `IMPLEMENTATION-NOTES.md`

### 54. worker-cpu-hygiene
- **branch:** `soju/patches/worker-cpu-hygiene`
- **origin:** `local-author`
- **upstream_pr:** _(none — 2026-08-05 loop-stall follow-up)_
- **state:** `local-only`
- **rationale:** Stop worker threads from starving the gateway event loop of GIL time. lifecycle_guard tokenized each command three times per nesting level (tokenize once, share the segment list); checkpoint_manager's dir walks used Path.rglob (rewritten on os.scandir with identical early-stop/symlink semantics).
- **commits:**
  - `d3ed0b344 perf(cron,tools): stop worker threads from starving the event loop of GIL time`
- **touches:** `cron/lifecycle_guard.py`, `tools/checkpoint_manager.py`

### 55. lifecycle-guard-nul-fallback
- **branch:** `soju/patches/lifecycle-guard-nul-fallback`
- **stacked-on:** `soju/patches/worker-cpu-hygiene`
- **origin:** `local-author`
- **upstream_pr:** _(none — #76762 follow-up on the fallback path)_
- **state:** `local-only`
- **rationale:** The terminal tool's `read_remote_script` callback re-read paths the local bounded reader had already skipped as binaries, so invoking any binary by path inside a gateway session fed decoded machine code back into the recursion and crashed `os.open` with `ValueError: embedded null byte`, killing the whole terminal call. Skip NUL-bearing callback content like the local binary skip, and tolerate `ValueError` in `_read_referenced_script` to match the existing `resolve()`-level tolerance.
- **commits:**
  - `f4d6e542a fix(cron): stop lifecycle guard remote fallback from resurrecting binaries`
- **touches:** `cron/lifecycle_guard.py`, `tests/hermes_cli/test_gateway_restart_loop.py`

### 56. refusal-risk-routing
- **branch:** `soju/patches/refusal-risk-routing`
- **stacked-on:** `soju/patches/lifecycle-guard-nul-fallback`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood refusal-risk preemptive routing)_
- **state:** `local-only`
- **rationale:** Gateway model router gains an orthogonal `refusal_risk` boolean (plus confidence) so hard frontier-refusal turns are preemptively switched to low-refusal routes before the primary model contaminates context. Label flow S1–S7 stays benched and byte-compatible; S0 only sets the orthogonal flag. Config `model_routes.router.refusal` (default disabled) maps SYSTEM_DEV/FRONTEND_DEV → PERMISSIVE_DEV (kimi-k3) and NORMAL/DOCUMENT_WORK → PERMISSIVE_CHAT (grok-4.5) when confidence ≥ threshold and source is LLM. Enforce-mode applied refusal switches emit an owner-visible platform notice via existing `_deliver_platform_notice` (config-gated). Follow-up tune: S0 explicitly false for draft-edit/tone-down of existing NSFW text and own-infra audits; true only for from-scratch NSFW authoring and third-party attack intents. Bench on 300 gold cases @0.85: precision 0.992 / recall 0.928; grok subset 1.0/1.0.
- **commits:**
  - `9e1df96aa feat(gateway): refusal-risk preemptive routing with permissive routes`
  - `a03a15a71 tune(gateway): narrow S0 refusal for draft-edit vs authoring`
- **touches:** `gateway/model_router.py`, `gateway/run.py`, `hermes_cli/model_routes.py`, `tests/gateway/test_model_router.py`, `tests/hermes_cli/test_model_routes.py`


### 57. refusal-api-fallback
- **branch:** `soju/patches/refusal-api-fallback`
- **stacked-on:** `soju/patches/refusal-risk-routing`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood reactive refusal recovery)_
- **state:** `local-only`
- **rationale:** When the primary model returns API-level safety refusal (`content_filter` / `FailoverReason.content_policy_blocked`), walk generic `fallback_providers` (opus) first, then resolved PERMISSIVE routes (dev→chat or chat→dev from primary runtime). Gated by `model_routes.router.refusal.enabled` + new `api_fallback` (default false). Preserves `_fallback_reason=content_policy_blocked` for skill-gate turn exemption. Non-refusal reasons unchanged.
- **commits:**
  - `87792122f feat(fallback): content_policy_blocked prefers PERMISSIVE routes`
  - `904bb7c2c fix(fallback): content_policy walks generic before PERMISSIVE`
- **touches:** `agent/chat_completion_helpers.py`, `hermes_cli/model_routes.py`, `tests/hermes_cli/test_model_routes.py`, `tests/run_agent/test_refusal_fallback_reason.py`


### 58. refusal-hop-clean-fork
- **branch:** `soju/patches/refusal-hop-clean-fork`
- **stacked-on:** `soju/patches/refusal-api-fallback`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood refusal masking + auto-switch)_
- **state:** `local-only`
- **rationale:** When a refusal is detected (hard: `content_policy_blocked` at turn end; soft: classifier `prior_refusal` on the previous assistant turn), mask ONLY the refusal message row (`active=0`, row preserved, `compacted` untouched) and one-shot route the next turn to PERMISSIVE. Replaces the preemptive `rewrite_transcript` that hard-deleted the whole session with no refusal evidence. Mid-turn clean-fork scope cut to the current turn (completed history preserved). `prior_refusal` classifier field costs zero extra API calls (router already receives `recent_turns`). Bench 30/30 at precision 1.0 / recall 1.0 after two prompt narrowings (sub-part inability and domain deferral no longer read as refusals). New `deactivate_messages`/`reactivate_messages` are reversible; no hard deletion anywhere.
- **commits:**
  - `e18d67b29 feat(refusal): clean-fork history on PERMISSIVE hop`
  - `2c3f0496d feat(refusal): mask refused turn and auto-switch instead of rewriting history`
  - `92e05b919 tune(router): narrow prior_refusal to whole-request refusals`
- **touches:** `agent/conversation_loop.py`, `agent/refusal_history.py`, `gateway/model_router.py`, `gateway/run.py`, `gateway/session.py`, `hermes_cli/model_routes.py`, `hermes_state.py`, `tests/`


### 59. refusal-soft-turnend
- **branch:** `soju/patches/refusal-hop-clean-fork`
- **stacked-on:** `soju/patches/refusal-hop-clean-fork` (same branch, two more commits)
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood soft-refusal detection + fallback hardening)_
- **state:** `local-only`
- **rationale:** Two gaps observed live on thread 1536678317166694441. (1) A soft refusal authored inside a turn (finish_reason=stop, no error) left no turn-end signal, so nothing masked it and no permissive hop was staged. Add `_handle_gateway_soft_refusal` probing the just-produced response through the existing `prior_refusal` classifier field, gated by length floor + config, with a `refusal_recovery_count` loop guard (`max_recovery_hops` default 2) that stops laundering repeat refusals through more models. (2) A refusal-recovery agent whose generic opus hop then died of `overloaded` never reached PERMISSIVE because the refusal walk only triggered on `content_policy_blocked`. Extend it so an active refusal recovery falls through to PERMISSIVE on ANY generic-exhaustion reason, and add logging to the previously silent `_build_refusal_fallback_chain`.
- **commits:**
  - `7a310d6ae feat(refusal): detect soft refusals at turn end with recovery-hop guard`
  - `780717641 fix(fallback): extend refusal PERMISSIVE chain to generic-exhaustion on any reason`
  - `2eb134996 Revert "fix(fallback): extend refusal PERMISSIVE chain to generic-exhaustion on any reason"` — owner decided an overload on the generic hop should STOP (fail the turn), not downgrade to k3; the any-reason extension is removed, restoring content_policy_blocked-only
  - `926bd58ba fix(fallback): log empty/built refusal chain reasons` — the previously silent `_build_refusal_fallback_chain` now logs why PERMISSIVE did/did not attach (kept from the reverted commit)
- **touches:** `gateway/model_router.py`, `gateway/run.py`, `hermes_cli/model_routes.py`, `agent/chat_completion_helpers.py`, `tests/gateway/test_model_router.py`, `tests/hermes_cli/test_model_routes.py`, `tests/run_agent/test_refusal_fallback_reason.py`

### 60. anthropic-structured-output
- **branch:** `soju/patches/anthropic-structured-output`
- **stacked-on:** `soju/patches/refusal-hop-clean-fork`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate)_
- **state:** `local-only`
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
  - `a17d9ec4f fix(compression): record cooldown on no_progress hygiene aborts + accurate trigger/abort diagnostics`
- **touches:** `agent/conversation_compression.py`, `gateway/run.py`, `tests/gateway/test_session_hygiene.py`, `tests/run_agent/test_compression_abort_state_reset.py`

### 62. durable-bg-processes
- **branch:** `soju/patches/durable-bg-processes`
- **stacked-on:** `soju/patches/hygiene-noprogress-cooldown`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate)_
- **state:** `local-only`
- **rationale:** Background tool processes died on every gateway restart via three stacked kill mechanisms: (1) shutdown `process_registry.kill_all()` global sweep in `_stop_impl`, (2) systemd `KillMode=mixed` SIGKILL on the unit cgroup after `TimeoutStopSec`, (3) `ExecStopPost=gateway.cgroup_cleanup` per-PID SIGKILL of every cgroup survivor. Checkpoint recovery (`processes.json`) only ever adopted crash survivors, and adopted them `detached` with no output. Fix (config-gated, `terminal.durable_background: false` default off, config.yaml only — no tool-schema change): durable non-PTY local spawns run inside `systemd-run --user --scope --unit=hermes-bg-<sid>` (sibling cgroup — escapes 2 and 3; readiness marker + graceful fallback to plain spawn when scopes are unavailable), write output to `$HERMES_HOME/bg-logs/<sid>.log` (0600/0700) as source of truth so post-restart `read_log`/`poll`/notification tails work, and are excluded from the shutdown sweep via `kill_all(exclude_durable=True)`. Explicit `process(action=kill)` still terminates them; scope units are `reset-failed` on completion. Review fix vs executor output: cron `#60432` interrupted-marking made unconditional again — the cron agent is a ThreadPoolExecutor thread inside the gateway process and dies with it even when its durable subprocess survives; gating on `_killed>0` would let a truncated run report success (regression test added). E2E-verified on this host: scope cgroup escape, checkpoint re-adoption in a fresh registry, and full output readback after simulated restart.
- **commits:**
  - `5848ae3da Add durable local background processes`
- **touches:** `tools/process_registry.py`, `gateway/run.py`, `hermes_cli/config_defaults.py`, `tests/tools/test_process_registry.py`, `tests/gateway/test_gateway_shutdown.py`, `tests/gateway/test_cron_active_work_drain.py`

### 63. route-aware-outage-fallback
- **branch:** `soju/patches/route-aware-outage-fallback`
- **stacked-on:** `soju/patches/refusal-api-fallback`
- **origin:** `local-author`
- **upstream_pr:** _(none — model_routes is fork-only (#34); upstream has no route subsystem to be aware of)_
- **state:** `local-only`
- **rationale:** Outage-shaped failures (`rate_limit`, `billing`, `upstream_rate_limit`, `overloaded`, `server_error`) walked only the global `fallback_providers:` chain, which has no route context and lands dev-routed sessions on a chat-tier model — silently demoting them and tripping skill-gate dev_edit capability gates until the quota window resets (2026-08-13: claude-lb model-quota-exhausted, 13h window, 90 fable→opus fallbacks in one day). Fix: `try_activate_fallback` resolves the CURRENT route via `runtime_satisfies_route` and walks that route's healthy `fallbacks:` (skipping unhealthy/self/unresolvable entries) BEFORE the global chain. Content-policy refusals unchanged (still generic-then-PERMISSIVE, patch #57). Non-routed runtimes and deployments without model_routes behave exactly as before.
- **commits:**
  - `0ff914006 feat(fallback): route-aware outage fallback prefers current route fallbacks`
  - `998f8d8b6 fix(fallback): resolve the outage route from recorded intent, not model id`
- **touches:** `agent/chat_completion_helpers.py`, `agent/runtime_control.py`, `gateway/run.py`, `tests/run_agent/test_fallback_helpers.py`, `tests/run_agent/test_runtime_control.py`
- **followup (2026-08-13):** the first cut was inert. `_build_outage_route_fallback_chain` identified the current route by scanning for the first route the live model satisfies, but membership is ambiguous — `claude-fable-5` is the DOCUMENT_WORK fallback AND the SYSTEM_DEV/FRONTEND_DEV primary, and DOCUMENT_WORK sorts first. A SYSTEM_DEV session resolved as DOCUMENT_WORK, whose only fallback is claude-fable-5 itself; that entry was skipped as self-targeting, the chain came back empty, and the global chain demoted the session to opus regardless. Fix: record the route the session actually selected (`_active_route_name`, set by `dispatch_model_switch` on the resolved/no-op/effort-only outcomes and carried across gateway pre-dispatch agent rebuilds) and prefer it while the live runtime still satisfies it; stale or absent intent falls back to the old scan. `model_status` shared the same ambiguity (reported DOCUMENT_WORK/CHAT for dev sessions) and now consults the same intent.

### 64. fts-v2-orphan-hardening
- **branch:** `soju/patches/fts-v2-orphan-hardening`
- **stacked-on:** _(none — applies directly on base)_
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate; needs the v2 generation present to be observable)_
- **state:** `local-only`
- **rationale:** `hermes sessions optimize-storage` aborted with `error in trigger messages_fts_v2_delete after rename: vtable constructor failed: messages_fts_v2`, leaving the deployed DB stuck on the pre-v23 layout at 20.0 GB for 4 GB of messages (~14 GB of duplicate index copies) — this is the operator follow-up flagged in the 2026-08-04 sync, now fixed in code rather than by hand. `messages_fts_v2` (`tokenize='cjk_unicode61'`) was superseded by `messages_fts_cjk` (`f13f845116`) but a DB that stopped mid-transition keeps the v2 vtable, its three `AFTER INSERT/UPDATE/DELETE ON messages` triggers and `fts_v2_*` markers while no code references it. Two defects made that residue fatal: (1) `_demote_legacy_fts_to_trash` collected shadows with a `messages_fts_%` LIKE glob that also matches sibling generations' shadows — renaming them re-parses the v2 triggers and the vtable cannot be constructed without the loadable `cjk_unicode61` tokenizer, rolling back the migration; (2) nothing retired the orphan, so every re-run failed identically. Fix (single shared path, sync+async callers inherit): derive the shadow set from the vtables actually being demoted using FTS5's fixed shadow suffixes and match exactly; retire `messages_fts_v2` via a **named allowlist, not a prefix scan** (a future generation must not be droppable for merely looking similar), removing trigger/vtable definitions through `writable_schema` so no tokenizer is needed and handing shadows to the existing chunked teardown. Preflight is fail-closed: messages present, live base index covering every row, integrity check clean, rowcount parity — otherwise the orphan is left untouched. Note the orphan also breaks plain `PRAGMA integrity_check` (`no such tokenizer`), so the DB could not be verified with stock sqlite3 while it existed. Verified RED/GREEN by reverting `hermes_state_search.py` alone (3 fail → 27 pass on base) and live on the affected host: 20041.3 MB → 8164.3 MB (11.9 GB reclaimed), `integrity_check` ok, no v2/trash residue, `messages` 1,268,987 = `messages_fts` 1,268,987 with rows still arriving mid-run, gateway serving throughout.
- **commits:**
  - `32f562255 fix(state): retire orphaned FTS v2 during storage optimize`
- **touches:** `hermes_state_search.py`, `tests/test_fts_v2_orphan_hardening.py`, `tests/test_state_db_malformed_repair.py`

### 65. read-file-binary-sniff
- **branch:** `soju/patches/read-file-binary-sniff`
- **stacked-on:** _(none — applies directly on base)_
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate; the sniff path is stock upstream code)_
- **state:** `pending-upstream`
- **rationale:** `read_file` refused valid UTF-8 Korean text with "Binary file - cannot display as text" — intermittently, and with no relation to file content type. Root cause is a two-stage mismatch in `tools/file_operations.py`: the binary sniff samples the first 1000 **bytes** (`head -c 1000`, ~L1182) while the terminal backend decodes stdout with `errors="replace"`, so a 3-byte Hangul or 4-byte emoji straddling byte 1000 leaves 1-3 trailing U+FFFD; `_is_likely_binary` (~L901) treats any U+FFFD in the sample as binary. That FFFD check is a deliberate fail-safe (it stops a read→edit→write roundtrip from overwriting undecodable bytes with mojibake) and is preserved — only the truncation artifact is excluded. Whether a given file trips this is pure byte-boundary luck, which is why it looked unreproducible: `personas/private.md` (2686 B) hits FFFD at cuts 1000/1024/2048, while `work.md` happens to land on a character boundary and reads fine; editing a file shifts the boundary and the symptom appears or vanishes. Fix: pass `sample_truncated=(file_size > 1000)` from the read path and, only in that case, strip a trailing run of at most 3 U+FFFD before the check. Untruncated samples keep byte-identical behavior, mid-sample FFFD still flags binary (genuine mojibake is not tail-only), and a trailing run of 4+ is not a boundary artifact so it still flags. Verified RED/GREEN by reverting `tools/file_operations.py` alone against the new tests (4 fail → 70 pass), then live: the reference doc describing this very bug was itself unreadable via `read_file` before the fix.
- **commits:**
  - `170c8ac07 fix(tools): don't flag UTF-8 text as binary when head -c splits a multibyte char`
- **touches:** `tools/file_operations.py`, `tests/tools/test_file_operations.py`

### 66. lifecycle-guard-multiline
- **branch:** `soju/patches/lifecycle-guard-multiline`
- **stacked-on:** `soju/patches/lifecycle-guard-nul-fallback`
- **origin:** `local-author`
- **upstream_pr:** _(none — #77131/#76762 third-generation follow-up; upstream candidate)_
- **state:** `local-only`
- **rationale:** The lifecycle guard split commands on newlines and shell-tokenized every line, so multiline `python3 -c "..."` / Python heredoc payloads were parsed as shell: a payload line like `sys.path.insert(0, os.path.expanduser('~/.hermes/hermes-agent'))` tokenized into a `/`-bearing token, got treated as a referenced-script path, resolved to a real directory, and `_read_referenced_script`'s non-regular fail-closed marked it unsafe — blocking benign terminal commands inside gateway sessions (29 blocked instances / 13 unique commands in request dumps; 9 of 13 were this false positive). Fix removes only syntactically proven non-shell regions before matching and tokenization: a complete quoted argument to a Python executable's `-c` (basename `python`/`pythonN`/`pythonN.M`, no `--` before payload), or the body of a complete Python heredoc (quoted/bare delimiter, `<<-` tab-strip honored). Unclosed quotes, missing heredoc terminators, shell `-c` payloads, here-strings, and non-Python interpreters stay visible (fail-closed). Preserved detections verified: `hermes gateway restart|stop`, `systemctl`/`launchctl submit|bootstrap`, `bash -c` payload recursion, nested `.sh` walking, binary/NUL skips, and lifecycle commands before/after sanitized payloads. Tokenize-once-per-level contract (patch #54) kept: sanitize once, share the segment list. Verified via TDD (3 RED → GREEN), 100-test file pass, 490-test cron+restart-loop pass, 12 adversarial cases, and historical replay (9/13 freed, 3 genuine lifecycle still blocked, 1 dump-truncation artifact correctly fail-closed).
- **commits:**
  - `96cd2aa2f fix(cron): stop lifecycle guard from shell-parsing python payloads`
- **touches:** `cron/lifecycle_guard.py`, `tests/hermes_cli/test_gateway_restart_loop.py`

### 67. scoped-exhaustion-upstream-classify
- **branch:** `soju/patches/scoped-exhaustion-upstream-classify`
- **stacked-on:** `soju/patches/lifecycle-guard-multiline`
- **origin:** `local-author`
- **upstream_pr:** _(none — depends on claude-lb-specific message contract; fork-only)_
- **state:** `local-only`
- **rationale:** claude-lb returns HTTP 503 when ONE model's weekly scoped quota is exhausted across its pool while every other model it serves stays healthy (body carries the spec-locked `model-quota-exhausted` token; message shape pinned by claude-lb OpenSpec `account-load-balancing` since 2026-08-13, PR Soju06/claude-lb#127). The generic 503 branch classified this as `overloaded`, which (a) is in `_PASSIVE_UNHEALTHY_REASONS`, so one dead model marked the whole `claude-lb` provider unhealthy for fail_ttl (120s) in the model-routes passive health cache while claude-opus-5 on the same provider served fine, and (b) is transport-shaped in the retry loop, burning 2 retries against a condition whose only recovery is a scoped window reset days out. Fix classifies the token as `upstream_rate_limit` — the existing "one model at an aggregator, not the provider" reason (OpenRouter upstream-429 precedent): deliberately excluded from passive-unhealthy marking, `is_rate_limited`-eager fallback (no wasted transport retries), rotation forced off, route-aware fallback chain applies, and `error_context.upstream_provider` carries the model so the status line reads "Upstream claude-fable-5 rate-limited". Two return sites: the 503/529 status branch (after overflow guards, before the `overloaded` terminal) and `_classify_by_message` (BEFORE `_OVERLOADED_PATTERNS` and `_RATE_LIMIT_PATTERNS` — ordering pinned by test, else "Rate limit exceeded" text would enable credential rotation). 429 branch untouched (regression-pinned: scoped token on 429 stays `rate_limit`). Verified: 73-test classifier file + 134-test fallback/failover/route sweep pass; bare-503 → `overloaded` regression pin preserved verbatim (a delegate attempt to weaken it to an overload-texted message was caught in diff audit and reverted).
- **commits:**
  - `53bdecd55 fix(fallback): classify scoped model-exhaustion 503s as upstream_rate_limit`
- **touches:** `agent/error_classifier.py`, `tests/agent/test_error_classifier.py`

### 68. lifecycle-guard-stmt-boundary
- **branch:** `soju/patches/lifecycle-guard-stmt-boundary`
- **stacked-on:** `soju/patches/lifecycle-guard-multiline`
- **origin:** `local-author`
- **upstream_pr:** _(none — Branch D statement-boundary false positive; upstream candidate)_
- **state:** `local-only`
- **rationale:** `_GATEWAY_LIFECYCLE_PATTERN` Branch D (`p?kill…hermes…gateway`, both token orders) used `[^\n]*` token gaps, so only newlines bounded a match — `;`, `&&`, `|` passed through and stitched INDEPENDENT statements on one physical line into a single "kill the gateway" match. Live reproduction: `kill -USR1 <pid> && echo …; sleep 20; systemctl --user show hermes-gateway-mymel.service -p …` — the kill is the SANCTIONED SIGUSR1 self-restart path and hermes/gateway tokens come from a read-only `systemctl show` unit name three statements later. Counterfactuals verified: newline-separated same content passed, removing the kill passed, renaming the unit passed. Same design-flaw family as #66 (statement boundaries ignored), opposite direction: #66 over-split (newline = new shell statement inside Python payloads), this one under-split (`;&|` not a boundary at all). Fix narrows both Branch D gaps to `[^\n;&|]*`; `&&`/`||` break on their first char so compound operators are covered. Branches A/B/C untouched (their gaps sit between a verb and its own arguments within one statement). Signal whitelisting (-USR1 exemption) considered and rejected as guard-weakening. Real threats preserved: `pkill -f "hermes.*gateway"`, `kill $(pgrep -f …)`/backtick substitution (no `;&|` inside), line-continuation folding, Branch C after `;` prefix. Verified: TDD RED 4→GREEN, file 113 passed, tests/cron 390 passed, 15 adversarial cases.
- **commits:**
  - `e59aa3953 fix(cron): stop lifecycle guard Branch D from spanning statement boundaries`
- **touches:** `cron/lifecycle_guard.py`, `tests/hermes_cli/test_gateway_restart_loop.py`

### 69. approval-grep-escaped-quote
- **branch:** `soju/patches/approval-grep-escaped-quote`
- **stacked-on:** _(none — applies directly on base)_
- **origin:** `local-author`
- **upstream_pr:** `85922`
- **state:** `pending-upstream`
- **rationale:** Started as a false positive — `grep -n "^from\|^__all__\|^    \"" file.py` was rejected as `hardline: command parser limit or malformed executable payload`, blocking a read-only grep in gateway sessions. Root cause: `detect_hardline_command` fed `_normalize_command_for_detection(command)` to the grep parser, but normalization strips backslash-escapes (`\"` → `"`), so the parser saw an odd number of quotes and reported the parse malformed, which fails closed. Investigating that exposed a far more serious defect in the same quote-state confusion: the hardline floor could be BYPASSED entirely. `_CMDPOS`-anchored hardline patterns do not match `; reboot` directly — they fire only after `_mark_command_starts` inserts a newline at each quote-aware command start. When an escaped quote is present, normalization leaves the quote open (`echo "a"b"; reboot`), the tokenizer sees one unterminated segment, no marker is inserted, and the floor never fires. Verified live on stock base and on the deployed tree: `cat "f\"n.txt"; rm -rf --no-preserve-root /` returned `approved=True` from `check_dangerous_command` — i.e. the yolo-proof floor, which is documented as unbypassable, was bypassable with one escaped quote in front of any command. Fix is three commits, additive only (variant generation is any-match, so adding variants can only tighten the floor): (1) validate grep syntax against faithful shell quote state, using `_mask_quoted_newlines(command)` rather than normalized text; (2) emit an ADDITIONAL detection variant whose command starts are marked BEFORE normalization, using a `" \n"` marker so a preceding backslash cannot swallow the inserted newline as a line continuation; (3) `_mark_command_starts` must not treat an offset inside a `${...}` parameter expansion as a command start — marking inside `${IFS}` produced `${ \nIFS}`, which defeated the IFS-collapse regex in normalization and left `echo "a\"b"; rm${IFS}-rf${IFS}--no-preserve-root${IFS}/` executable and unblocked (confirmed with the safe analogue `echo "a\"b"; echo${IFS}BYPASS_EXECUTED`, rc=0, second statement executed). A single-quote variant (`echo 'a\'b'; reboot`) was investigated and is NOT a bypass — bash rejects it with rc=2 (unterminated quote), so it cannot execute. Verified RED/GREEN per commit (1 → 5 → 4 failing tests), 266 passed across the four affected test files, mutation-killed both new code paths (reverting the param-expansion skip → 4 failures; reverting the faithful-marking variant → 88 failures), and 33 adversarial cases hand-checked against BASE to confirm zero regressions: every remaining difference from base is either fail-safe over-blocking that predates this patch or a non-executable string.
- **commits:**
  - `428fe1d51 fix(approval): parse raw grep quote state before hardline checks`
  - `7855422f0 fix(approval): mark command starts from faithful quote state`
  - `e8bbdbeb4 fix(approval): don't mark command starts inside parameter expansions`
- **touches:** `tools/approval.py`, `tests/tools/test_execution_flag_detection.py`

### 70. elision-marker-imitation
- **branch:** `soju/patches/elision-marker-imitation`
- **stacked-on:** `soju/patches/approval-grep-escaped-quote`
- **origin:** `local-author`
- **upstream_pr:** _(none — pending dogfood soak before proposing)_
- **state:** `local-only`
- **rationale:** The compressor rewrote old tool_call arguments and oversized message bodies using the static literal `...[truncated]`. Because that literal is identical at every emission site and always terminates the truncated string, a long session's context accumulates dozens of `string value → marker → close JSON` examples and the model pattern-completes the same shape onto NEW tool calls — `write_file`, `patch`, `terminal`, `execute_code` payloads shipped silently cut off mid-word, producing broken files and unterminated here-docs. The failure is self-propagating: measured on live `state.db` (2026-07-01..08-14), 1,507 fresh imitated calls across 151 sessions (terminal 881, write_file 291, execute_code 217, patch 207, skill_manage 48), and **84% were preceded by the model's own earlier imitation** rather than a fresh compressor rewrite. Disproved the competing hypothesis that payloads are truncated at the tool boundary: 3,019 fresh `write_file` calls parsed with **0** malformed arguments and a 105,084-char `content` was delivered intact, so pre-truncating a payload is never correct. Root cause is shape, not policy — the marker is a constant a model can reproduce. Fix replaces it with `_elision_marker(n)` embedding the exact omitted-character count, which a model composing a fresh payload cannot know, making the marker unreproducible by pattern completion and self-identifying as a compression artifact. Converted every emission site reaching main-model context: `context_compressor` (pass-3 tool_call args shrink, summarizer serialization for tool/assistant/user bodies, deterministic fallback turn, active-task line), `skill_preprocessing` inline-shell output, `trajectory_compressor` summary input, `delegate_tool` goal preview. `hermes_cli/auth.py` deliberately untouched — it renders CLI error text for a human terminal, not model context. JSON-validity contract from upstream `3128d9fcd2` (`_truncate_tool_call_args_json`, shrink inside the parsed structure so strict providers do not 400) is preserved and re-asserted by tests. Verified: 12 new tests + 141 passed across the three affected suites, plus a loop simulation confirming markers differ by payload size.
- **commits:**
  - `58fb6b920 fix(compression): make elision markers non-imitable by the model`
- **touches:** `agent/context_compressor.py`, `agent/skill_preprocessing.py`, `trajectory_compressor.py`, `tools/delegate_tool.py`, `tests/agent/test_elision_marker_guard.py` (new), `tests/agent/test_context_compressor.py`, `tests/test_trajectory_compressor.py`

### 71. delegated-child-marker-latch
- **branch:** `soju/patches/delegated-child-marker-latch`
- **stacked-on:** `soju/patches/memory-phase2-curator` (patch B opts ingest-curator's detached spawn out of delegated-flag inheritance, so it edits `agent/ingest_curator.py` owned by that patch)
- **origin:** `local-author`
- **upstream_pr:** _(none yet — proposal artifacts ready under `~/work/comm-improve/latch-bug/`; open after dogfood soak)_
- **state:** `local-only`
- **rationale:** A normal gateway session (never called `delegate_task`) got permanently classified as a delegate child — kanban mutations refused, `model_tools` tool-definition cache key poisoned. Primary carrier is the shared bash session snapshot (`/tmp/hermes-snap-<id>.sh`): `_resolve_container_task_id` collapses subagent task ids to `"default"` so parent/child share one snapshot, and `_wrap_command`'s trailing `export -p` write-through persisted the child's `HERMES_DELEGATED_CHILD_CONTEXT=1` into the parent's next turns (D1). Same mechanism defeated `scrub_kanban_env()` on the terminal path: sourcing the snapshot re-exported the dispatcher's `HERMES_KANBAN_*` over the scrubbed Popen env (D2). Secondary: detached daemons (ingest-curator, bg-review, async delegation) copy the delegated-child ContextVar and keep it for the process lifetime (D4); `KANBAN_ENV_KEYS` allowlist drifted to 7 of the dispatcher's 10 injected vars (D6). All four reproduced deterministically without a gateway (`~/work/comm-improve/latch-bug/repro*.py`). Fix: unset both families inside the snapshot dump subshell (same audited mechanism as `HERMES_SESSION_*`; no line-based filtering per #71296); scope-correct ContextVar propagation via keyword-only `inherit_delegated_child=True` with the three detached sites opting out (blanket clear measured to regress: in-turn workers lose the flag and a delegated child can mutate board state — fail-closed default kept); once-per-process latch fingerprint diagnostic; prefix-based kanban env scrub. Snapshot fix covers all environment backends (`_wrap_command` lives on `BaseEnvironment`).
- **commits:**
  - `84cf492bb fix(delegation): stop delegated-child marker and kanban identity latching across turns`
- **touches:** `tools/environments/base.py`, `tools/thread_context.py`, `agent/delegation_context.py`, `agent/ingest_curator.py`, `run_agent.py`, `tools/async_delegation.py`, `tests/tools/test_delegated_child_marker_latch.py` (new)

### 72. kanban-board-env-precedence
- **branch:** `soju/patches/kanban-board-env-precedence`
- **stacked-on:** _(none — applies directly on base; verified `git diff --stat base..production -- hermes_cli/kanban_db.py` empty before this patch)_
- **origin:** `local-author` (implementation delegated to codex; design proposal by kanban goal worker t_231c8f71)
- **upstream_pr:** _(none yet — upstream-eligible: file untouched by other fork patches)_
- **state:** `local-only`
- **rationale:** `HERMES_KANBAN_DB` env pin outranked an explicit `board=` argument / `--board` flag in `hermes_cli/kanban_db.py` path resolution, so any process inheriting a worker's env silently wrote to the wrong board's DB while the CLI printed success. Observed live 2026-08-16: `notify-subscribe --board comm-improve` stored its subscription in the default board DB → task-completion notifications never delivered → owner fell back to manual polling (the exact scheduling-crank pattern the comm-improve track exists to remove). Same defect class hit `create`. Diagnostic key: sibling env `HERMES_KANBAN_BOARD` was correctly overridden by `--board`; only `HERMES_KANBAN_DB` had inverted precedence. Fix reorders resolution to explicit-arg → env pin → current-board pointer, preserving the dispatcher→worker handoff contract (argument-less calls still follow the env pin, so workers keep their own board). Verified: isolated repro matrix FAIL→PASS on the patch and FAIL re-confirmed on base; 3 worker-selfboard contracts PASS; kanban suites green with pre-existing failures reproduced on base.
- **commits:**
  - `0b48702d5 fix(kanban): honor explicit board over env pins`
- **touches:** `hermes_cli/kanban_db.py`, `tests/gateway/test_kanban_notifier.py`, `tests/gateway/test_kanban_notifier_apiserver_wake.py`, `tests/hermes_cli/test_kanban_boards.py`

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
soju/patches/strict-chat-reasoning-details
soju/patches/discord-home-autothread-fix
soju/patches/runtime-override-rehydrate-credentials (stacked on runtime-route-awareness)
soju/patches/turn-waterfall-tracing
soju/patches/prompt-tail-freeze (stacked on runtime-route-awareness)
soju/patches/gateway-worker-pool
soju/patches/conn-error-fail-fast
soju/patches/background-first-waits
soju/patches/llm-activity-recap
soju/patches/config-knob-bridges
soju/patches/durable-turns (stacked on llm-activity-recap + config-knob-bridges)
soju/patches/hook-prepend-command-safety (stacked on durable-turns + runtime-control)
soju/patches/slow-tool-perf-advisor
soju/patches/model-routing
soju/patches/audience-personas (stacked on model-routing)
soju/patches/model-switch-provider-dedupe
soju/patches/memory-phase0 (stacked on memory-write-reason-gate)
soju/patches/memory-phase1 (stacked on memory-phase0)
soju/patches/memory-phase2-taint (stacked on memory-phase1)
soju/patches/memory-phase2-curator (stacked on memory-phase1)
soju/patches/anthropic-picker-suppression
soju/patches/daemon-pool-py314-compat
soju/patches/skill-manage-reason-schema
soju/patches/refusal-fallback-reason (stacked on runtime-control)
soju/patches/route-repromote-hysteresis (stacked on model-routing)
soju/patches/passive-provider-health (stacked on model-routing)
soju/patches/event-loop-db-isolation
soju/patches/per-tool-disable
soju/patches/compaction-prompt-cc-upgrades
soju/patches/anthropic-signature-passthrough
soju/patches/notes-recognition-grounding (stacked on memory-phase2-taint)
soju/patches/worker-cpu-hygiene
soju/patches/lifecycle-guard-nul-fallback (stacked on worker-cpu-hygiene)
soju/patches/lifecycle-guard-multiline (stacked on lifecycle-guard-nul-fallback)
soju/patches/lifecycle-guard-stmt-boundary (stacked on lifecycle-guard-multiline)
soju/patches/anthropic-structured-output (stacked on refusal-hop-clean-fork)
soju/patches/hygiene-noprogress-cooldown (stacked on anthropic-structured-output)
soju/patches/fts-v2-orphan-hardening
soju/patches/read-file-binary-sniff
soju/patches/approval-grep-escaped-quote
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
