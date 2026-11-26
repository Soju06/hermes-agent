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
base_commit:   8fc0374447313e276f6b9a2764bfc750190c966a
base_tag:      v0.23.4
base_describe: 8fc037444731
pinned_at:    2026-11-25
```

Bump `base_commit` only through the DECISIONS.md ADR-004 procedure (own-commit cherry-picks; see Operating Procedures). Each bump must rebuild all `soju/patches/*` topics on top of the new base and verify the production stack rebuilds clean.

## Patches (apply order = list order)


### 1. runtime-control
- **branch:** `soju/patches/runtime-control`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood runtime control)_
- **state:** `local-only`
- **rationale:** Agent-callable `model_status` / `model_switch` tools. Session-only scope (turn scope removed — LLMs omitted scope ~29%, causing silent reversion). Phase 3c (2026-07-23): the LLM-facing switch surface is route-only — `route` + `reason`; raw model/provider ids and reasoning effort are catalog (config SoT) decisions and get a teaching error at the tool boundary (`dispatch_model_switch`, the single dispatch entry point for BOTH executors — the Phase 3b sequential-executor `route` omission was exactly per-executor kwarg drift). With no declared routes the switch tool goes dormant via check_fn instead of degrading to free-form. `model_status` speaks route language (route block + model + reasoning; raw provider/endpoint/api-mode stay in telemetry). Gateway session callback plus durable SessionEntry runtime fields so session-scoped model/reasoning overrides survive gateway restart without storing secrets. Trusted pre-dispatch plugin runtime overrides can select the session route after auth but before the first LLM call. Plugin `prepend` directives accumulate text before the original event message.
- **fixes (2026-11-26, cleanup step 7):** `/reasoning reset` also clears the durable `runtime_reasoning_effort` (owner decision O-A6; test `tests/gateway/test_runtime_override_state_db_restart.py::test_reasoning_reset_drops_the_persisted_level_across_a_restart`), and the `runtime_state` hook has its row in `website/docs/developer-guide/plugins/index.md`.
- **decision (2026-11-26, O-A6):** `/model --global` needs no change: since 4fff840016 its `set_model_override(key, None)` already clears `runtime_model`/`runtime_provider` (pinned by `test_global_switch_drops_the_older_runtime_route`), so the planned extra clear was not carried. `/model reset`'s single-row write is accepted.
- **absorbed (2026-11-26 cleanup):** one independent topic for the runtime hook, the route block and the prepend guard (fewer re-stacks: a #1 fix no longer re-stacks #8, #14, #18). #77's commit deletes two stale tests that #1's own 985c8b83eb adds (net zero, never upstream). members in commit order: #1 runtime-control, #8 runtime-route-awareness, #14 runtime-override-rehydrate-credentials, #18 prompt-tail-freeze, #7 runtime-state-session-split, #28 hook-prepend-command-safety, #77 sync-20260819-test-seams. Old member tips: `backup/cleanup-1126/soju/patches/<name>` and the bundle.
  - #8 runtime-route-awareness: Inject an API-call-time Runtime/Route State block so the agent sees live CurrentRuntime plus current-turn DesiredRoute without calling `model_status`. Trusted pre-dispatch `runtime_override` metadata is normalized into one-shot route state for the routed gateway turn, preserving stale-route protection while leaving post-tool rerouting and NEED_CONTEXT scout mode for later phases.
  - #14 runtime-override-rehydrate-credentials: Upstream owns live `switch_model` endpoint rollback and provider-header reapplication; this patch is limited to gateway restart/persistence. A persisted runtime route is eligible only when both `runtime_model` and `runtime_provider` labels exist, then resolves once with `target_model` into one authoritative provider bundle (normalized provider, endpoint, API mode, keyed or keyless credential, command/args, and credential pool). Incomplete or unresolved labels stay durable but dormant while the turn falls back wholly to coherent defaults. Persisted reasoning rehydrates independently, and the latest `/model` or runtime-route writer clears the other durable model-route store without clearing reasoning. State DB coverage verifies DB-only restart and that no secret bundle fields are persisted.
  - #18 prompt-tail-freeze: Fork-only residue after the upstream merge of the original patch: (1) `agent/system_prompt.py` byte-stability for the Runtime/Route block — key-tuple cache, permanently static DesiredRoute line, always-emitted `reasoning_source=`, `format_routing_directive` one-shot delivery riding upstream's turn-sidecar channel; (2) `gateway/run.py` same-route re-selection eviction guard + `include_reasoning` persistence for runtime overrides; (3) fork-only test deltas rebased onto upstream's `test_prompt_tail_freeze.py`/`test_runtime_route_prompt.py`. The pinned session-context prompt, sidecar note staging/consume, VC note, multimodal fallback and sorted connected platforms are upstream now and are NOT re-added. Expiry-path inline sidecar-note pop dropped in favor of upstream's `_clear_conversation_scope` funnel.
  - #7 runtime-state-session-split: Runtime hard gates must see the active session's actual model/provider after context compression rotates the session id. Compression split republishes runtime state for the new session while retaining the old session as task/parent scope, preventing fallback to stale cross-session runtime state.
  - #28 hook-prepend-command-safety: A `pre_gateway_dispatch` `{"action": "prepend"}` rewrote `event.text` before slash dispatch, and `is_command()`/`get_command()` key off `text.startswith("/")` — so any plugin prepend on a command message demoted it into plain chat that fell through to the agent. Live incident 2026-07-15: the inbox-matter-coordinator advisory rewrite (plugin 9ed17c4) prepends an `[INBOX_MATTER ...]` marker on matter-linked threads; `/model` there was answered by the agent's `model_status` tool instead of the interactive picker, and every slash command in those threads broke the same way. Fix: drop prepends (debug log) when the event is a slash command — advisory context is agent-facing and command handlers can't consume it. Plain-chat prepend behavior unchanged.
  - #77 sync-20260819-test-seams: The 2026-08-19 upstream seam refactor moved pre-tool plugin blocking from `resolve_pre_tool_block` to `_dispatch_pre_tool_call_hooks`, leaving two runtime-control tests monkeypatching a bypassed seam. Upstream now carries equivalent integration coverage for both agent-level and registry-dispatched blocked tools, so this sync patch deletes the stale duplicates instead of re-anchoring redundant tests.
- **commits:** (own commits in order; `[#N]` names the absorbed topic each came from)
  - `985c8b83eb` feat(runtime): agent-callable model_switch / model_status (session-only)
  - `003ae6debb` fix(runtime): align runtime tool dispatch ownership
  - `4f71926902` feat(runtime): route-enum self model switching (ADR-003 Phase 3b)
  - `98b189ebcf` feat(runtime): route-only model switching (ADR-003 Phase 3c)
  - `118cb3b743` fix(runtime): rehydrate persisted runtime state after gateway refactor
  - `2a89cf9a6a` test(runtime): re-anchor runtime-control tests to the 2026.9.11 upstream seams
  - `cb906d2d24` fix(gateway): turn wiring tolerates bare runner doubles for fork hooks [#8]
  - `4d3aaf1f03` test(runtime): re-anchor runtime-control tests to the v0.22.5 upstream seams
  - `86e355281d` fix(runtime): re-resolve the persisted runtime route for its own model (2026-10-30 sync review)
  - `6b89637566` fix(runtime): commit a local session's agent model_switch through its policy (2026-10-30 sync review)
  - `301b39cd0c` fix(runtime): keep the durable runtime route across switch_session re-pins (2026-10-30 sync review)
  - `5ec897f18d` fix(runtime): apply pre-dispatch runtime overrides off the owner loop (2026-10-30 sync review)
  - `94eb26f5b2` fix(runtime): apply runtime_override directives on producer ingress (2026-10-30 sync review)
  - `156ff1dede` fix(runtime): keep an explicitly empty toolset selection tool-free (2026-10-30 sync review)
  - `4390c04d6c` test(runtime): make the pre-auth runtime_override test able to fail (2026-10-30 sync review)
  - `a349961037` test(runtime): tolerate the fork's runtime toolset on named picks in upstream resolver tests (2026-10-30 sync review)
  - `591267fb3e` fix(shared-metrics): list the fork's runtime toolset in the v4 schema (2026-10-30 sync regression)
  - `4d0ac88859` fix(runtime): keep a frozen create-time toolset pick exactly as picked (2026-10-30 sync regression)
  - `37c0cf9413` test(runtime): re-anchor runtime-control tests to the v0.23.4 upstream seams
  - `e8d6f3f988` fix(runtime): port runtime-control onto the v0.23.4 storage, ingress, /model reset and tool-catalog changes
  - `98288ec03b` fix(shared-metrics): list the fork's runtime tools in the v4 tool_unavailable enum
  - `806f97fe0f` test(runtime): cold /status reads the default runtime for the next-turn route and the window (2026-11-25 sync regression)
  - `8d161190d4` feat(runtime): inject runtime route awareness prompt [#8]
  - `7b2f4fa517` test(routing): expect the Runtime/Route block in post-compression system prompts [#8]
  - `ef7baec345` fix(gateway): turn wiring tolerates bare runner doubles for fork hooks [#8]
  - `b34cc3f3b6` test(routing): re-anchor runtime-route-awareness tests to the v0.22.5 upstream seams [#8]
  - `ddcab09185` fix(routing): pin the turn-wiring consume of the staged route state (2026-10-30 sync review) [#8]
  - `00e2c920e2` test(routing): re-anchor runtime-route-awareness tests to the v0.23.4 upstream seams [#8]
  - `712b71a184` test(routing): a refreshed session persists its prompt without the API-time Runtime/Route block (2026-11-25 sync regression) [#8]
  - `3f21d574f5` fix(gateway): re-resolve persisted runtime override bundle [#14]
  - `4fff840016` fix(gateway): a /model clear also drops the older persisted runtime route (2026-10-30 sync review) [#14]
  - `14afe25743` test(gateway): re-anchor runtime-override-rehydrate-credentials tests to the v0.23.4 upstream seams [#14]
  - `fe7d69116d` fix(gateway): a rehydrated runtime route on a managed OAuth host follows the auth store (v0.23.4 port) [#14]
  - `487f873455` feat(cache): prompt-tail freeze — byte-stable gateway system prompts (patch #18) [#18]
  - `31e8826749` feat(prompt): Runtime/Route identity speaks route + model (ADR-003 Phase 3c) [#18]
  - `88a0387a4c` fix(prompt): pin the turn-wiring routing directive and reasoning-source clear (2026-10-30 sync review) [#18]
  - `450c649c64` test(prompt): re-anchor prompt-tail-freeze tests to the v0.23.4 upstream seams [#18]
  - `135ff431ad` fix: keep runtime state scoped across compression splits [#7]
  - `5d36dc7d53` fix(gateway): drop pre_gateway_dispatch prepends on slash commands [#28]
  - `bfae4b28b6` test(runtime): re-anchor pre-tool block tests to _dispatch_pre_tool_call_hooks seam (2026-08-19 sync) [#77]
  - `2a4af15a88` fix(gateway): /reasoning reset clears the durable runtime reasoning level [#fix 2026-11-26]
  - `af6872f10d` docs(plugins): list the runtime_state hook in the fire-site table [#fix 2026-11-26]
- **touches:** `agent/agent_init.py`, `agent/agent_runtime_helpers.py`, `agent/chat_completion_helpers.py`, `agent/conversation_compression.py`, `agent/conversation_compression_runtime_state.py`, `agent/conversation_loop.py`, `agent/inline_tool_executors.py`, `agent/runtime_control.py`, `agent/system_prompt.py`, `agent/tool_dispatch_helpers.py`, `agent/turn_context.py`, `gateway/platforms/webhook_ingress.py`, `gateway/run.py`, `gateway/run_agent_cache.py`, `gateway/run_config_loaders.py`, `gateway/run_inbound.py`, `gateway/run_inbound_hooks.py`, `gateway/run_turn_prepare.py`, `gateway/run_turn_runner.py`, `gateway/session.py`, `gateway/session_policy.py`, `gateway/session_runtime_switch.py`, `gateway/slash_commands_model.py`, `gateway/slash_commands_session.py`, `hermes_cli/observability/schemas/hermes.shared_metrics.v4.schema.json`, `hermes_cli/plugins.py`, `hermes_cli/tools_config.py`, `model_tools.py`, `tools/runtime_control_tool.py`, `toolsets.py`, `website/docs/developer-guide/plugins/index.md`, `website/docs/developer-guide/runtime-route-awareness.md`, `tests/agent/test_413_compression.py`, `tests/agent/test_pre_tool_session_id.py`, `tests/agent/test_replay_cleanup.py`, `tests/agent/test_run_agent.py`, `tests/agent/test_runtime_control.py`, `tests/agent/test_runtime_control_dispatch.py`, `tests/agent/test_runtime_control_routes.py`, `tests/agent/test_runtime_route_prompt.py`, `tests/agent/test_runtime_state_session_split.py`, `tests/agent/test_session_prompt_refresh.py`, `tests/agent/test_session_prompt_refresh_storage_handle.py`, `tests/gateway/test_pre_gateway_dispatch.py`, `tests/gateway/test_prompt_tail_freeze.py`, `tests/gateway/test_runtime_override_global_model_switch.py`, `tests/gateway/test_runtime_override_restart_rehydration.py`, `tests/gateway/test_runtime_override_state_db_restart.py`, `tests/gateway/test_runtime_override_store_precedence.py`, `tests/gateway/test_runtime_route_turn_wiring.py`, `tests/gateway/test_runtime_switch_local_session.py`, `tests/gateway/test_session.py`, `tests/gateway/test_session_model_override_routing.py`, `tests/gateway/test_session_policy_coding_posture.py`, `tests/gateway/test_status_command.py`, `tests/gateway/test_switch_session_keeps_runtime_route.py`, `tests/hermes_cli/test_plugins.py`, `tests/hermes_cli/test_runtime_provider_resolution.py`, `tests/hermes_cli/test_tools_config.py`, `tests/tools/test_runtime_control_tool_schema.py`, `tests/tui_gateway/test_profiles_toolset_pin.py`, `tests/tui_gateway/test_tui_gateway_server.py`
- **member port reports (v0.23.4-sync):** `reports/runtime-route-awareness.md`, `reports/runtime-override-rehydrate-credentials.md`, `reports/prompt-tail-freeze.md`, `reports/runtime-state-session-split.md`, `reports/hook-prepend-command-safety.md`, `reports/sync-20260819-test-seams.md` under `~/work/hermes/upstream-sync-20261125/`.
- **v2026.8.3-sync:** rehydration moved to the modular gateway startup path; explicit reasoning still wins over upstream's per-model reasoning re-resolution on switch.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/runtime-control.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/runtime-control.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/runtime-control.md` for relocations/seams.


### 2. memory-write-reason-gate
- **branch:** `soju/patches/memory-write-reason-gate`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood memory hygiene)_
- **state:** `local-only`
- **rationale:** Memory `add`/`replace` tool calls require an explicit suitability reason explaining why USER/MEMORY is the right store rather than a skill, Graphiti, or session history. The reason is a guardrail only and is not persisted with the entry.
- **commits:**
  - `3ae65454b7` feat(memory): require write reason for memory updates
  - `c0d208b3ec` test(memory): pass write reason in upstream tests added since the reason gate
  - `747cee45a8` test(memory): pass write reason in upstream tests added since v2026.9.11
  - `b3d0780bbf` fix(memory): pass write reason in upstream e2e memory calls (2026-10-30 sync review)
  - `a3e0c10ad9` test(memory): pass write reason in upstream schema tests added since v0.22.5
- **touches:** `agent/inline_tool_executors.py`, `tools/memory_tool.py`, `tests/agent/test_background_review_memory_scope.py`, `tests/agent/test_builtin_memory_disabled_surface.py`, `tests/agent/test_inline_tool_executors_memory_args.py`, `tests/agent/test_memory_write_bridge.py`, `tests/agent/test_skip_memory_store.py`, `tests/e2e/core/cli_tmux/test_classic_cli_live_session.py`, `tests/e2e/core/providers/test_anthropic_discovery_and_aliases.py`, `tests/e2e/core/security/test_store_traversal.py`, `tests/hermes_cli/test_shared_metrics_loop.py`, `tests/tools/test_memory_patch_action.py`, `tests/tools/test_memory_target_contract.py`, `tests/tools/test_memory_tool.py`, `tests/tools/test_memory_tool_schema.py`, `tests/tools/test_write_approval.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/memory-write-reason-gate.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/memory-write-reason-gate.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/memory-write-reason-gate.md` for relocations/seams.


### 3. todo-progress-display
- **branch:** `soju/patches/todo-progress-display`
- **origin:** `local-author`
- **upstream_pr:** _(none — Discord/gateway dogfood UX)_
- **state:** `local-only`
- **rationale:** Gateway progress bubbles should show todo item statuses after the todo tool completes, not only a count like `planning N task(s)`. Also flush throttled progress edits when the final queued event has no following tool event.
- **commits:**
  - `a068874853` feat(gateway): show todo progress details
  - `ceb368451e` fix(gateway): gate the todo progress block on text tool progress (2026-10-30 sync review)
- **touches:** `agent/display.py`, `gateway/run_turn_progress.py`, `tests/agent/test_display.py`, `tests/gateway/test_run_progress_todo.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/todo-progress-display.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/todo-progress-display.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/todo-progress-display.md` for relocations/seams.


### 4. discord-table-codeblocks
- **branch:** `soju/patches/discord-table-codeblocks`
- **origin:** `local-author`
- **upstream_pr:** _(none — Discord/gateway dogfood UX)_
- **state:** `local-only`
- **rationale:** Discord does not render GitHub-flavored markdown pipe tables. Convert detected outbound pipe tables to fenced box-drawing ASCII tables so table responses remain readable, while preserving existing fenced code blocks, CJK wide-character alignment, and long-table chunk codeblock boundaries.
- **commits:**
  - `3cd76fc909` feat(discord): render markdown tables as codeblocks
  - `e2cb5f5af4` fix(discord): align vector-generator wording and tidy table sibling (2026-10-30 sync review)
  - `1f33b6d92c` fix(discord): split box-table rows with upstream's GFM splitter (2026-11-25 sync review)
- **touches:** `plugins/platforms/discord/adapter.py`, `plugins/platforms/discord/adapter_tables.py`, `scripts/generate_conformance_vectors.py`, `tests/gateway/test_discord_format.py`, `tests/gateway/test_discord_send.py`
- **v2026.7.20-sync:** upstream `test_table_converted_to_bullets` re-expected to the fenced-ASCII semantics (pre-existing gap — it was red on the old stack too).
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/discord-table-codeblocks.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/discord-table-codeblocks.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/discord-table-codeblocks.md` for relocations/seams.


### 6. background-review-guardrails
- **branch:** `soju/patches/background-review-guardrails`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood self-improvement guardrail)_
- **state:** `local-only`
- **rationale:** Background self-improvement review must be a calibrated participant in the memory/skill governance model instead of aggressively writing after most sessions. Memory review prompts now carry the structured always-injected memory rationale contract, and skill review prompts default to "Nothing to save" unless a concrete reusable signal exists.
- **commits:**
  - `e6452593f7` fix: calibrate background self-improvement review
  - `9438ed02c2` fix(background-review): gate memory(add|patch), not the removed replace (2026-10-30 sync review)
  - `132c4f1e9a` test(background-review): read the memory action enum from operations[] items (2026-11-25 sync seam)
- **touches:** `agent/background_review.py`, `tests/agent/test_background_review_calibration.py`
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/background-review-guardrails.md` for relocations/seams.
- **v0.22.5-sync:** DONE — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/background-review-guardrails.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/background-review-guardrails.md` for relocations/seams.


### 15. turn-waterfall-tracing
- **branch:** `soju/patches/turn-waterfall-tracing`
- **origin:** `local-author`
- **upstream_pr:** _(none — perf instrumentation for local bottleneck hunt)_
- **state:** `local-only`
- **rationale:** Per-turn waterfall tracing to attribute end-to-end turn latency (observed ~30% slower than a minimal agent on the same LLM). New `agent/turn_trace.py` collects wall-clock spans across the whole turn lifecycle — gateway ingest/session-resolve/transcript-load/agent-setup, prologue children (system-prompt restore, early persist, compression preflight, pre-LLM hook, memory prefetch), per-iteration context assembly/request setup/`llm.call` (TTFT + failed attempts) /accounting, tool batches incl. the inter-tool delay sleep, verify gates, finalize children, gateway persist, transport delivery — and emits one JSONL record per turn to `~/.hermes/logs/turn_traces.jsonl`. `agent/turn_trace_render.py` renders terminal/HTML waterfalls and cross-turn p50/p95 summaries with a model-time vs hermes-overhead split. Gated by `HERMES_TURN_TRACE=1` (default off = no-op); tracing failures can never break a turn.
- **commits:**
  - `425221d564` feat(telemetry): per-turn waterfall tracing spans
  - `549b3b6a4f` feat(telemetry): request prefix fingerprints for cache-break diffing
  - `7018ad43a4` fix(telemetry): fingerprint Responses-API payloads too
  - `14f159f5e3` fix(telemetry): carry turn trace across pre-dispatch event replacement
  - `698ef1335c` feat(telemetry): 4KB-chunk hashes locate in-message cache breaks
  - `1866e65c8c` feat(telemetry): attribute rest-field changes to specific keys
  - `006d8fe4ad` feat(telemetry): HERMES_TURN_TRACE_SYS_TAIL captures system-prompt tail
  - `a1885afc0f` fix(gateway): queued follow-up reads turn_trace_obj via getattr
  - `26948b53a4` fix(telemetry): hand the gateway turn trace across the durable admission drain
  - `04ba1f193a` fix(telemetry): carry rewritten-turn traces to the adapter and time busy follow-up delivery (2026-10-30 sync review)
  - `64c4f24d84` test(telemetry): pin tools.call spans from both tool executors (v0.23.4 port)
- **touches:** `agent/chat_completion_helpers.py`, `agent/client_lifecycle.py`, `agent/conversation_loop.py`, `agent/tool_executor.py`, `agent/turn_context.py`, `agent/turn_final_response.py`, `agent/turn_finalizer.py`, `agent/turn_response_check.py`, `agent/turn_trace.py`, `agent/turn_trace_loop.py`, `agent/turn_trace_render.py`, `gateway/platforms/base.py`, `gateway/run_startup.py`, `gateway/run_turn.py`, `gateway/run_turn_prepare.py`, `gateway/run_turn_runner.py`, `gateway/run_turn_setup_guard.py`, `gateway/session_ingress.py`, `gateway/turn_context.py`, `run_agent.py`, `tests/agent/test_turn_trace.py`, `tests/agent/test_turn_trace_loop.py`, `tests/gateway/test_turn_trace_admission.py`
- **v2026.7.20-sync:** tools.batch span adapted to upstream segmented dispatch (mode=`segmented` + `segments=<n>` tag); `prologue.persist_early` split into `persist_early` + new `prologue.persist_user_turn` following upstream's persist-site split (cross-version p50 continuity caveat).
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/turn-waterfall-tracing.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/turn-waterfall-tracing.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/turn-waterfall-tracing.md` for relocations/seams.


### 23. conn-error-fail-fast
- **branch:** `soju/patches/conn-error-fail-fast`
- **stacked-on:** assembled parallel lane (`cleanup1126/assembly` = v0.23.4 + the independent topics, octopus) — the chronological tip stack starts here (re-stacked 2026-11-26; was `sync1125/assembly`)
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate after burn-in)_
- **state:** `local-only`
- **rationale:** Consecutive sub-2s transport failures (connection refused/reset before any bytes) mean the endpoint is down, not congested; with no fallback available the retry loop burned a dozen attempts with growing backoff (observed 13 attempts / 170-250s of user-visible silence per turn against a briefly-down codex-lb). Track the instant-failure streak on TurnRetryState and end the turn with an actionable error after `HERMES_FAST_CONN_FAIL_LIMIT` (default 3, 0 disables) once the fallback chain has had its chance; slow timeouts keep the full retry budget.
- **commits:**
  - `6905c3572c` fix(agent): fail fast on instant transport-failure streaks
  - `155bdcc9b4` fix(telemetry): tag instant transport fail-fast exits
  - `d1c5ce3152` test(agent): fail-fast tests patch the backoff where upstream now computes it (2026-09-14 sync)
  - `dbf994be14` test(agent): fail-fast tests build the agent on upstream's patch targets (2026-10-30 sync)
  - `d29250d7c3` fix(agent): fail-fast measures instant failure per physical connect (2026-10-30 sync review)
  - `2897964e00` fix(agent): fail-fast result carries failure_retryable and a diagnostic status (2026-10-30 sync review)
  - `de452c98bc` fix(agent): fail-fast settles the transcript like retry exhaustion (2026-10-30 sync review)
  - `5e86ef8db7` test(agent): fail-fast transcript test reads the tail through the storage interface (2026-11-25 sync)
- **touches:** `agent/conversation_loop.py`, `agent/turn_api_error.py`, `agent/turn_recovery_fail_fast.py`, `agent/turn_retry_state.py`, `tests/agent/test_fast_transport_fail_fast.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/conn-error-fail-fast.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/conn-error-fail-fast.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/conn-error-fail-fast.md` for relocations/seams.


### 25. llm-activity-recap
- **branch:** `soju/patches/llm-activity-recap`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate after burn-in)_
- **state:** `local-only`
- **rationale:** `display.long_running_notifications` gains a `recap` mode: instead of the terse "⏳ Working — N min — iteration i/max" heartbeat, the gateway asks the auxiliary LLM (compression rail, 8s timeout, ~80 tokens) for a one-line present-tense recap of what the agent is doing — goal + recent tool calls + current wait — in the conversation's language (Claude Code-style). Context-hash caching regenerates only on activity change; failures fall back to the terse heartbeat; in recap mode the bubble is deleted-and-resent (adapters with delete support) so it stays at the thread bottom instead of buried. Complements #24 background-first-waits: waits that should end the turn do, and turns that legitimately run long narrate themselves.
- **commits:**
  - `5734ed23f0` feat(gateway): LLM activity recap for long-running notifications
  - `8470b45473` feat(gateway): tune activity recap context from live-session evaluation
  - `2c921f5cc6` feat(gateway): recap speaks in the agent's own voice
  - `441eb5528a` feat(gateway): persona-definition voice fallback for fresh sessions
  - `9452a85622` fix(gateway): recap passes the live main runtime, tolerant delete probe (2026-10-30 sync review)
- **touches:** `agent/activity_recap.py`, `gateway/display_config.py`, `gateway/run_turn.py`, `gateway/run_turn_activity_recap.py`, `run_agent.py`, `tests/gateway/test_llm_activity_recap.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/llm-activity-recap.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/llm-activity-recap.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/llm-activity-recap.md` for relocations/seams.


### 29. slow-tool-perf-advisor
- **branch:** `soju/patches/slow-tool-perf-advisor`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — candidate once soak data confirms hint uptake)_
- **state:** `local-only`
- **rationale:** Trace mining across 2,605 turns (2026-07-16) showed the dominant avoidable turn latency is agents routing heavy work through `terminal` in shapes with a cheap native equivalent: full-tree scans (`rglob`/`os.walk`/unpruned `find`/bare `grep -r` — 59+32+209 calls) over repos whose `node_modules` holds >1.7M files (observed 88s + 63s back-to-back scans of the same tree in one Discord turn), foreground `sleep` polling (364 calls), and multi-minute foreground jobs pinning session workers — while `search_files` (rg-backed, .gitignore-aware) answers in ~1s and background+`notify_on_complete` re-enters the session for free. Fix is a thin advisory-only shot mirroring the subdirectory-hints append: when a terminal call is slow AND matches an antipattern, append ONE `[perf-advisor]` line to the tool result at the moment the model just paid for the slow call. Kill switch `HERMES_PERF_ADVISOR=0`; thresholds `HERMES_PERF_ADVISOR_MIN_S` (10s), `HERMES_PERF_ADVISOR_FOREGROUND_S` (120s).
- **commits:**
  - `723360420d` feat(tools): slow-tool perf advisor — teach cheaper shapes on slow terminal results
  - `8c91c950f6` fix(tools): perf advisor teaches notify/process_manage, executor wiring tested (2026-10-30 sync review)
  - `28cbd75394` refactor(tools): the perf-advisory append lives in tools/perf_advisor.py (v0.23.4 sync)
- **touches:** `agent/tool_executor.py`, `tools/perf_advisor.py`, `tests/test_perf_advisor.py`
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/slow-tool-perf-advisor.md` for relocations/seams.
- **v0.22.5-sync:** DONE — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/slow-tool-perf-advisor.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/slow-tool-perf-advisor.md` for relocations/seams.


### 34. model-routing
- **branch:** `soju/patches/model-routing`
- **stacked-on:** `soju/patches/conn-error-fail-fast` (2026-11-25 chronological tip stack; #28 folded into #1 and #27 retired on 2026-11-26)
- **upstream_pr:** _(none — ADR-003 Phase 1; enum'd delegation aligns with upstream `delegation-model-routing` policy, natural round-2 candidate once Phases 2–3 prove it)_
- **origin:** `local-author`
- **state:** `local-only`
- **rationale:** ADR-003 Phases 1+2 — model routing SoT as a core subsystem. Phase 1: config-declared `model_routes:` catalog (per route: description, provider, model, reasoning_effort, accepted membership, ordered fallbacks), loader with startup cross-validation against `providers:`, and a health-aware resolver (`resolve_route`) that walks default→fallbacks with TTL-cached fail-open provider probes ported from skill-gate's runtime_catalog (401/403 healthy, credit-sniffed 400, 402/429/5xx unhealthy; config-first kill switch `model_routes.health.enabled` + `HERMES_MODEL_ROUTES_HEALTH` bridge). Phase 2: gateway dynamic router (`gateway/model_router.py` + `_model_router_stage`) — classifier core byte-identical to the skill-gate bench winner (parity-test-pinned; changing it requires the 230+120 bench rerun), `model_routes.router` config (mode off|shadow|enforce + `HERMES_MODEL_ROUTER_MODE` bridge, label_routes, chat_route, streak), static condition rules (short-circuit by design — the plugin's live last-wins is a bug not ported), decision log `~/.hermes/logs/model_router_decisions.jsonl` for shadow-soak comparison, enforce apply with /model parity (#48031 auto-reset survival, pending note, session-DB persist). Dormant with empty config. Phase 3 (`model_switch`/`delegate_task` route enums) builds on this; supersedes patch #5 when Phase 3 lands.
- **fix (2026-11-26, O-A5):** the router marks its own persisted /model override `router_applied: true`; re-promotion skips a session whose /model override lacks the mark (a typed /model or picker pick, also an agent `model_switch` or plugin `runtime_override` route), keeping ADR-003's escape hatch. Channel pins (config) and health-fallback members still re-promote. Deploy note: router overrides persisted before this fix carry no mark, so sessions parked on a non-primary member stop re-promoting until the next router switch (one-time).
- **absorbed (2026-11-26 cleanup):** the routing core and its two hardening topics are ported and reviewed as one unit. members in commit order: #34 model-routing, #46 route-repromote-hysteresis, #47 passive-provider-health. Old member tips: `backup/cleanup-1126/soju/patches/<name>` and the bundle.
  - #46 route-repromote-hysteresis: Route `accepted` membership was absorbing: once a session landed on a non-primary accepted member (DOCUMENT_WORK on claude-opus-5, SYSTEM_DEV on gpt-5.6-sol after a health failover), every turn returned `noop_satisfied` and the route primary (claude-fable-5) was never re-promoted. Adds `repromote_after_turns` (router default 3, per-route override, ≤0 disables): after N trusted noop turns on a non-primary member, the router resolves the route and emits `repromote_to_primary` — only onto the healthy true primary (`source=="default"`), `repromote_held` otherwise with the streak clamped for fast recovery. Classifier path trust-gated to `source=="llm"` (regex-fallback labels are inert), static path always trusted; CHAT gets the same mechanism at `noop_already_chat`; `normal_streak` untouched.
  - #47 passive-provider-health: The model-routes health probe was an active 1-token completion with a 2.5s default timeout; claude-lb's end-to-end completion floor is ~3s, so probes deterministically timed out, the primary was misjudged unhealthy, and SYSTEM_DEV sessions silently failed over to gpt-5.6-sol (2026-07-27/28 incident; 19 of 21 switches landed on the fallback). Health is now passive-first: real completion traffic files the verdicts (`record_provider_outcome` — unhealthy from `try_activate_fallback` on outage-shaped `FailoverReason`s with probe-parity classification, healthy-clear on live completion success gated to one `os.stat` via `has_unhealthy_verdicts`), `provider_key_for_runtime` maps the live agent identity (config key / display name / `custom:` slug / base_url) to the `providers:` config key with no-match→no-record, and `provider_health()` only probes as a *recovery check* on a stale unhealthy verdict — no verdict or a stale healthy one is assumed healthy with zero I/O, so steady state never probes and never burns completion tokens.
- **known limitation:** model-routed `delegate_task` children carry `request=None` and are refused on continue after a gateway restart (`tools/delegate_tool_continue.py:108-114`, fails closed); in-process continues work. A real fix needs `request={"route": name}` on claimed TaskRoutes and a continue path through `resolve_routed_tasks` (`~/work/hermes/upstream-sync-20261125/reports/model-routing.md:83-95`).
- **decision (2026-11-26, O-A7):** accepted as a known limitation; about one delegation a week, and the path fails closed. Revisit only if delegation use grows.
- **commits:** (own commits in order; `[#N]` names the absorbed topic each came from)
  - `a3fff31679` feat(routing): model_routes catalog + health-aware route resolver (ADR-003 Phase 1)
  - `502a92bb57` feat(routing): core dynamic model router with shadow mode (ADR-003 Phase 2)
  - `dc8674f8c7` feat(routing): route-enum delegation on delegate_task (ADR-003 Phase 3a)
  - `7073c788c6` fix(routing): classify the pre-hook message text in the router stage
  - `67c19244c5` fixup(model-routing): adapt to v2026.7.20 upstream
  - `766900cab4` test(routing): pin HERMES_MODEL_ROUTER_MODE=off in the hermetic fixture
  - `63dbdf75ef` fix(routing): adapt state and config validation seams
  - `fea377a9b9` test(routing): delegate route tests follow upstream schema/batch seams (2026-09-14 sync)
  - `36e4b55071` fix(gateway): model_router .env read passes encoding=utf-8
  - `345e01c049` test(routing): re-anchor route tests to the v0.22.5 seams (2026-10-30 sync)
  - `e444a89a6d` fix(gateway): router classifies the pre-hook text of durably admitted messages (2026-10-30 sync review)
  - `c5ee473c65` fix(gateway): model router never routes LOCAL policy-bound sessions (2026-10-30 sync review)
  - `b255de9f53` fix(gateway): router enforce apply commits like a typed /model switch (2026-10-30 sync review)
  - `28a70bc2b6` fix(gateway): router runtime snapshot resolves reasoning for the effective model (2026-10-30 sync review)
  - `a06697fa40` fix(delegation): default_route yields to a chosen delegation backend (2026-10-30 sync review)
  - `874c06590a` test(routing): pin the classifier core to a vendored bench snapshot (2026-10-30 sync review)
  - `7164122422` fix(gateway): model router env reads honour the profile secret scope (2026-10-30 sync review)
  - `624aecb1d2` fix(routing): re-anchor model routing to the v0.23.4 seams (2026-11-25 sync)
  - `925997116e` fix(routing): keep gateway/run.py under its line ratchet and import ConfigIssue from its owner (2026-11-25 sync review)
  - `12468576b8` feat(model-routes): add repromote_after_turns knob (router default + per-route override) [#46]
  - `90d053dbbb` feat(gateway): re-promote sessions from accepted members to the route primary [#46]
  - `117cc02deb` test(gateway): cover route re-promotion hysteresis [#46]
  - `9917d3ee1e` feat(routing): passive-first provider health — verdicts from real traffic [#47]
  - `b88442d340` fix(fallback): file the passive verdict when a reset-deferred switch ends the turn (2026-10-30 sync review) [#47]
  - `e885d16d42` fix(routing): decide passive health for every FailoverReason (2026-10-30 sync review) [#47]
  - `2a14a3376c` fix(agent): pin the five passive-health success hooks (2026-10-30 sync review) [#47]
  - `22cdfbd4e7` fix(routing): scope the passive burst guard to the health cache (2026-10-30 sync review) [#47]
  - `1b76acf08b` fix(routing): keep the per-completion passive-health gate cheap and logged (2026-10-30 sync review) [#47]
  - `55f9e548ec` fix(routing): see same-tick health-cache rewrites in the passive gate memo (2026-10-30 sync review) [#47]
  - `5694cc6e87` fix(docs): describe passive-first model_routes health in cli-config.yaml.example (2026-10-30 sync review) [#47]
  - `7ed6a50435` fix(routing): classify v0.23.4's guardrail_blocked for passive health (2026-11-25 sync) [#47]
  - `b95e31bed4` fix(routing): exempt a user's /model pick from route re-promotion (O-A5) [#fix 2026-11-26]
- **touches:** `agent/chat_completion_helpers.py`, `agent/chat_completion_nonstream.py`, `agent/passive_provider_health.py`, `agent/turn_recovery.py`, `cli-config.yaml.example`, `gateway/model_router.py`, `gateway/platforms/webhook_ingress.py`, `gateway/run.py`, `gateway/run_agent_cache.py`, `gateway/run_inbound.py`, `gateway/run_inbound_model_router.py`, `gateway/run_turn_prepare.py`, `gateway/session.py`, `gateway/session_authority.py`, `gateway/session_envelope.py`, `hermes_cli/config.py`, `hermes_cli/config_defaults.py`, `hermes_cli/model_routes.py`, `run_agent.py`, `tools/delegate_tool.py`, `tools/delegate_tool_model_routes.py`, `tests/_fixtures/env_filter.py`, `tests/agent/test_passive_provider_health.py`, `tests/conftest.py`, `tests/fixtures/skill_gate_classifier_core.json`, `tests/gateway/test_model_router.py`, `tests/gateway/test_model_router_admission.py`, `tests/hermes_cli/test_model_routes.py`, `tests/tools/test_delegate.py`, `tests/tools/test_delegate_tool_model_routes.py`
- **member port reports (v0.23.4-sync):** `reports/route-repromote-hysteresis.md`, `reports/passive-provider-health.md` under `~/work/hermes/upstream-sync-20261125/`.
- **v2026.8.3-sync:** route state followed the refactored gateway session helpers; config validation moved to `hermes_cli/config_validation.py`; Phase 3a delegation was re-expressed on the current `delegate_task` transport.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/model-routing.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/model-routing.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/model-routing.md` for relocations/seams.


### 35. audience-personas
- **branch:** `soju/patches/audience-personas`
- **stacked-on:** `soju/patches/model-routing` (2026-11-25 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — generic and config-off-by-default, upstream candidate once soaked)_
- **state:** `local-only`
- **rationale:** Audience-mode persona injection: when `HERMES_HOME/personas/modes.yaml` exists, a mode is selected per session as a pure deterministic function of session-constant inputs (platform/chat_type/chat_id/chat_name/user_id) — first-match rules (string-or-list exact match, chat_name case-insensitive, missing key = wildcard, AND), non-owner guard applied after rules (non-empty `user_id` not in `owners[platform]` forces `guards.non_owner_mode`; missing owners entry fails safe; empty user_id skips), `default_mode` fallback — and the mode's persona markdown is injected as stable-tier slot #2 right after the SOUL.md/DEFAULT_AGENT_IDENTITY identity block, through the same threat scan + truncation cap as SOUL.md. With no modes.yaml the build is byte-identical (strict no-op); all failure paths degrade to the no-op at DEBUG. Cache correctness: an `AudienceMode: <mode>` line joins the volatile tail only while active, and `_stored_prompt_matches_runtime` recomputes the expected mode via a cheap mode-only resolver (both-absent passes → pre-deploy stored prompts stay valid; mismatch/one-sided rebuilds exactly once per session). SOUL.md plumbing parity: `personas/` in profile clone + default-export include set, `hermes_config_mod` threat pattern extended to `.hermes/personas/` paths. The `artifact_register` section of modes.yaml is a tone-gate plugin contract, deliberately not consumed by core.
- **absorbed (2026-11-26 cleanup):** members in commit order: #35 audience-personas, #78 base-persona. Old member tips: `backup/cleanup-1126/soju/patches/<name>` and the bundle.
  - #78 base-persona: modes.yaml gains an optional top-level `base_persona: <file>` key: a personas-dir-relative markdown file prepended (`base + "\n\n" + mode`) to every selected mode persona inside `load_audience_persona`, so shared style/identity rules live in ONE file instead of being duplicated per mode (immediate consumer: fluent-korean Korean-output guideline shared across modes). Injection gating is untouched — the base never changes WHETHER injection happens, only content, so `resolve_audience_mode`/`_persona_stat_ok` (per-turn staleness resolver) stay byte-identical and loader/resolver agreement is preserved. Base file resolves through the same personas-dir containment as mode personas (factored into `_contained_persona_path`) and passes the same `_scan_context_content` + `_truncate_content` pipeline with its own label/read_path. Fail-open: missing/empty/escaping/unreadable base → DEBUG log, mode persona alone; base path == mode path → no self-duplication; key absent → byte-identical to pre-patch behavior.
- **commits:** (own commits in order; `[#N]` names the absorbed topic each came from)
  - `71c35ff1e4` feat(prompt): audience-mode persona injection from personas/modes.yaml
  - `94f29121af` fix(prompt): audience persona honours the hosted-scope and safe-worker gate of SOUL.md (2026-11-25 sync review)
  - `c69589298a` feat(prompt): optional shared base persona prefix for audience modes [#78]
- **touches:** `agent/audience_persona.py`, `agent/conversation_loop.py`, `agent/system_prompt.py`, `hermes_cli/profiles.py`, `tools/threat_patterns.py`, `tests/agent/test_audience_persona.py`, `tests/agent/test_system_prompt_switch_invalidation.py`
- **member port reports (v0.23.4-sync):** `reports/base-persona.md` under `~/work/hermes/upstream-sync-20261125/`.
- **2026-08-26-sync:** refreshed the declared model-routing ancestry to current tip `99aebe694`; the dependency correction restores the per-call delegation credential seam while leaving audience selection behavior unchanged.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/audience-personas.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/audience-personas.md` for relocations/seams.
- **v0.23.4-sync:** DONE — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/audience-personas.md` for relocations/seams.


### 37. memory-redesign
- **branch:** `soju/patches/memory-redesign`
- **stacked-on:** `soju/patches/audience-personas` (2026-11-25 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — ADR-004 dogfood; revisit after shadow soak)_
- **state:** `local-only`
- **rationale:** ADR-004 Phase 0 memory-redesign plumbing: `pending/` WAL (durable per-turn buffer for external memory sync), L0-mirror local evidence journal for outgoing memory payloads, `_memory_ingest_disabled` per-agent flag (read-only memory forks), MEMORY.md/USER.md char-cap defaults unified on the config SoT. Journal review fixes: atomic appends, marker-only boundary mirroring, per-directory startup scan, directories pinned at construction.
- **commit drops (2026-11-26):** #37's 0ac453015e (char-cap unification, owner decision O-A12: DEFAULT_CONFIG, code and docs match upstream's 2200/1375 again; both live profiles set the caps explicitly), #53's 92d496ca34 (current-turn quote deferral, owner decision O-A8; the grounding preview 7a7ad3e0ae stays) and #53's 51a897ef0e (stray root `IMPLEMENTATION-NOTES.md`).
- **fix (2026-11-26):** the `notes_write` description names no tool of another toolset (`tools/AGENTS.md`).
- **absorbed (2026-11-26 cleanup):** the memory-redesign ADR's phases 0 to 2 and the notes grounding as one topic ("ADR-004" inside these commits means the memory-redesign ADR, not DECISIONS.md ADR-004). members in commit order: #37 memory-phase0, #38 memory-phase1, #39 memory-phase2-taint, #40 memory-phase2-curator, #53 notes-recognition-grounding. Old member tips: `backup/cleanup-1126/soju/patches/<name>` and the bundle.
  - #38 memory-phase1: ADR-004 Phase 1 notes tier: `NotesStore` declarative notes under the citation contract, `notes_write`/`notes_read`/`memory_propose` tool family, two-step token contract with grounded admission and gated backfill seam. Review fixes close secret-scrub bypasses, add real quote grounding, non-destructive supersede, write-gate parity.
  - #39 memory-phase2-taint: ADR-004 §① Phase 2 origin-taint machinery: injected-span registry, WAL/mirror span tagging, quote-taint enforcement; spans registered at the prefetch and memory-tool result sites. Review fixes: registry singleton hygiene, WAL/mirror tag coverage, floor-not-round registration timestamps.
  - #40 memory-phase2-curator: ADR-004 Phase 2 ingest curator: fork recipe, verdict schema, shadow ledger, watermark; curator triggers + `curator_verdict` dispatch wiring. Review fixes: seam scrub+grounding, provenance validation, cross-lane taint interface. Runs shadow-only until the cutover gate.
  - #53 notes-recognition-grounding: Preview grounding evidence when proposing notes and allow admission to ground against the current user turn only at write time, while preserving mechanical taint rejection and the existing citation contract.
- **commits:** (own commits in order; `[#N]` names the absorbed topic each came from)
  - `3c33c0330a` feat(memory): _memory_ingest_disabled per-agent flag — read-only memory forks (ADR-004 Phase 0) [#37]
  - `813de457e5` feat(memory): pending/ WAL — durable per-turn buffer for external memory sync (ADR-004 Phase 0) [#37]
  - `3c3d58668d` feat(memory): L0-mirror — local evidence journal for outgoing memory payloads (ADR-004 Phase 0) [#37]
  - `770483d5af` fix(memory): pin journal directories at construction, not at write time [#37]
  - `86f2be0690` test(memory): exercise prefetch with a substantive query [#37]
  - `bd205949ff` test(memory): pin the Phase-0 gates against upstream's new teardown and stuck-call seams (2026-10-30 sync) [#37]
  - `6a39dbcc05` test(gateway): keep lazy-import allocations out of the spool recovery memory bound (2026-11-25 sync regression) [#37]
  - `dd4f766105` fix(memory): journal review fixes — atomic appends, marker-only boundary mirroring, per-directory startup scan [#38]
  - `29ea005304` feat(memory): NotesStore — declarative notes tier under the citation contract (ADR-004 Phase 1) [#38]
  - `461088c3e5` feat(memory): notes write pipeline — two-step token contract, grounded admission, gated backfill seam (ADR-004 §③) [#38]
  - `28cb8b3dbc` feat(memory): notes tool family — notes_write/notes_read/memory_propose wiring + prompt guidance (ADR-004 Phase 1) [#38]
  - `645b21204b` fix(memory): notes review fixes — close the secret-scrub bypasses, real quote grounding, non-destructive supersede, write gate parity (ADR-004 Phase 1) [#38]
  - `5c1b71bf8d` test(run_agent): list the notes tools in the post-hook ownership cases (2026-09-14 sync) [#38]
  - `4ccffefca1` fix(memory): port the notes tier onto v0.22.5 seams (2026-10-30 sync) [#38]
  - `63a275a289` fix(memory): fit the notes tool first sentences to the tool_search catalog line (2026-11-25 sync) [#38]
  - `d3b7996f34` fix(shared-metrics): list the notes tools in the v4 tool_unavailable enum (2026-11-25 sync regression) [#38]
  - `6238a00511` feat(memory): origin-taint machinery — injected-span registry, WAL/mirror span tagging, quote-taint enforcement (ADR-004 §① Phase 2) [#39]
  - `bf747b76db` feat(memory): register injected memory spans at the prefetch and memory-tool result sites (ADR-004 §① Phase 2) [#39]
  - `d65b35f9d2` fix(memory): origin-taint review fixes — registry singleton hygiene, WAL/mirror tag coverage, floor-not-round registration ts (ADR-004 Phase 2) [#39]
  - `92ba0df10b` fix(memory): keep one taint registry per profile home (2026-11-25 sync review) [#39]
  - `5f5cead491` feat(memory): ingest curator core — fork recipe, verdict schema, shadow ledger, watermark (ADR-004 Phase 2) [#40]
  - `75b9a03631` feat(memory): ingest curator triggers + curator_verdict dispatch wiring (ADR-004 Phase 2) [#40]
  - `490844a0c9` test(memory): ingest curator Phase-2 suite — shadow invariant, fork isolation, triggers, caps (ADR-004) [#40]
  - `84f75fddc1` fix(memory): ingest curator review fixes — seam scrub+grounding, provenance validation, cross-lane taint interface (ADR-004 Phase 2) [#40]
  - `9d6dd041fb` test(run_agent): list curator_verdict in the post-hook ownership cases (2026-09-14 sync) [#40]
  - `3b86b5a226` fix(memory): port the ingest curator onto v0.22.5 seams (2026-10-30 sync) [#40]
  - `110e19e622` fix(docs): document the memory ingest curator config keys (2026-10-30 sync regression) [#40]
  - `d358bf8406` fix(memory): give the ingest curator fork upstream's fork identity and hosted gate (2026-11-25 sync) [#40]
  - `316c691cb8` fix(memory): bind the curator idle trigger to its profile and keep the parent's provider pins on the fork (2026-11-25 sync review) [#40]
  - `9a3caf9510` feat(memory): add recognition grounding preview [#53]
  - `5cfdb8d88f` fix(notes): the notes_write description names no tool of another toolset [#fix 2026-11-26]
- **touches:** `IMPLEMENTATION-NOTES.md`, `agent/agent_init.py`, `agent/agent_runtime_helpers.py`, `agent/background_review.py`, `agent/codex_runtime.py`, `agent/conversation_compression.py`, `agent/ingest_curator.py`, `agent/inline_tool_executors.py`, `agent/memory_journal.py`, `agent/memory_manager.py`, `agent/memory_pipeline.py`, `agent/memory_taint.py`, `agent/notes_store.py`, `agent/prompt_builder.py`, `agent/system_prompt.py`, `agent/tool_executor.py`, `agent/turn_context.py`, `agent/turn_finalizer.py`, `cli-config.yaml.example`, `hermes_cli/config_defaults.py`, `hermes_cli/observability/schemas/hermes.shared_metrics.v4.schema.json`, `optional-skills/research/research-paper-writing/SKILL.md`, `run_agent.py`, `tools/delegate_tool_results.py`, `tools/delegate_tool_toolsets.py`, `tools/memory_tool.py`, `tools/memory_tool_store.py`, `tools/notes_tool.py`, `toolsets.py`, `website/docs/guides/tips.md`, `website/docs/user-guide/configuration.md`, `website/docs/user-guide/features/memory.md`, `website/docs/user-guide/skills/optional/research/research-research-paper-writing.md`, `website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/guides/tips.md`, `website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/user-guide/configuration.md`, `website/i18n/zh-Hans/docusaurus-plugin-content-docs/current/user-guide/features/memory.md`, `tests/agent/test_ingest_curator.py`, `tests/agent/test_memory_ingest_disabled.py`, `tests/agent/test_memory_l0_mirror.py`, `tests/agent/test_memory_pending_wal.py`, `tests/agent/test_memory_pipeline.py`, `tests/agent/test_memory_provider_caller_profile.py`, `tests/agent/test_memory_sync_redaction.py`, `tests/agent/test_memory_taint.py`, `tests/agent/test_notes_store.py`, `tests/agent/test_run_agent.py`, `tests/gateway/test_shutdown_spool_limits.py`, `tests/tools/test_notes_tool.py`, `tests/tools/test_write_approval.py`
- **member port reports (v0.23.4-sync):** `reports/memory-phase1.md`, `reports/memory-phase2-taint.md`, `reports/memory-phase2-curator.md`, `reports/notes-recognition-grounding.md` under `~/work/hermes/upstream-sync-20261125/`.
- **v2026.7.20-sync:** re-stacked on memory-write-reason-gate; turn_context gates re-expressed on upstream turn_context.py (7b3dcee92); conversation_compression gate adapted to upstream memory_context capture.
- **2026-08-26-sync:** replayed the phase-0 commits onto corrected durable-turns so the shared turn-context seam preserves both resume and memory-prefetch invariants.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/memory-phase0.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/memory-phase0.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/memory-phase0.md` for relocations/seams.


### 42. anthropic-picker-suppression
- **branch:** `soju/patches/anthropic-picker-suppression`
- **origin:** `local-author`
- **upstream_pr:** _(none)_
- **state:** `local-only`
- **rationale:** Codex CLI model picker offered Anthropic models even when Anthropic credentials are suppressed for the session; respect the suppression flag when building picker candidates. Recovered from the live deployment checkout (was deployed as an unmanifested commit on top of the old production assembly, 2026-07-22).
- **commits:**
  - `3419b610cd` fix(model-picker): respect Anthropic credential suppression
- **touches:** `hermes_cli/model_switch_providers.py`, `tests/hermes_cli/test_codex_cli_model_picker.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/anthropic-picker-suppression.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/anthropic-picker-suppression.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/anthropic-picker-suppression.md` for relocations/seams.


### 44. skill-manage-reason-schema
- **branch:** `soju/patches/skill-manage-reason-schema`
- **origin:** `local-author`
- **upstream_pr:** _(none — companion to the external skill-gate plugin's ADR-004 §② admission gate)_
- **state:** `local-only`
- **rationale:** The skill-gate plugin hard-requires a structured JSON `reason` on `skill_manage` create/edit/patch, but the tool schema never declared the field — schema-following models could not discover the contract and learned it only through blocked-call retry loops (a 2026-07-22 session burned dozens of retries). Adds an optional `reason` property documenting the rationale object (claim_kind / execution_evidence tier / evidence_pointer / why_not_note / target / neighbor_skills_checked) and the staged-create behavior. Deliberately NOT `required`: enforcement stays with the fail-closed gate. Harmless without the gate. Ledger counterpart (`skill-rationale-ledger.jsonl`, every gated verdict recorded scrubbed) lives in the skill-gate plugin repo, not this fork.
- **commits:**
  - `8d022132a8` feat(skills): advertise the structured write rationale in the skill_manage schema
  - `e68ae12379` feat(skills): type the skill_manage reason rationale as a real JSON-Schema object
  - `a908df2020` test(skills): allow the local `reason` property in the skill_manage schema diet pin
- **touches:** `tools/skill_manager_tool.py`, `tests/tools/test_skill_manage_schema_diet.py`, `tests/tools/test_skill_manager_tool.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/skill-manage-reason-schema.md` for relocations/seams.
- **v0.22.5-sync:** DONE — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/skill-manage-reason-schema.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/skill-manage-reason-schema.md` for relocations/seams.


### 45. refusal-fallback-reason
- **branch:** `soju/patches/refusal-fallback-reason`
- **stacked-on:** `soju/patches/runtime-control`
- **origin:** `local-author`
- **upstream_pr:** _(none yet — pairs with the local skill-gate plugin's refusal exemption; upstreamable on its own)_
- **state:** `local-only`
- **rationale:** `try_activate_fallback(agent, reason)` received the `FailoverReason` but discarded it, so plugins could not distinguish a safety-refusal fallback (deterministic, content-scoped, restore-next-turn) from rate-limit/5xx failovers. Records `agent._fallback_reason` at the single activation success point, mirrors it at every `_fallback_activated` reset/copy site, exposes `fallback_activated`/`fallback_reason` in `get_runtime_state()`, emits `runtime_state` hook events at activation (`event="fallback"`) and restore (`event="restore"`), and forwards the reason from the exception client-error branch (`classified.reason`) and the Codex soft-failure fallback; the HTTP-200 `content_filter` and mid-stream content-filter sites pass it upstream since 6ee6b7cb77. Enables the skill-gate refusal-fallback exemption so a refusal-driven claude-opus-5 fallback can perform dev edits for exactly that turn.
- **commits:**
  - `76981784d7` feat(fallback): record activation reason and publish runtime_state events
  - `0fb9ce0391` test(fallback): cover fallback reason lifecycle and runtime_state hook
  - `ce0d942256` test(fallback): behaviorally cover loop call-site reason forwarding and the transport-recovery reset
  - `9ed65229c5` fix(fallback): honor declared api_mode (chain entry or providers.<name>) over URL heuristics
  - `8dd30d4ee9` test(fallback): re-anchor refusal-fallback-reason tests to the v0.22.5 upstream seams
  - `e7683f2313` fix(fallback): forward the content-policy reason from the Codex soft-failure fallback (2026-10-30 sync review)
- **touches:** `agent/agent_init_fallback.py`, `agent/agent_runtime_helpers.py`, `agent/chat_completion_helpers.py`, `agent/route_binding.py`, `agent/runtime_control.py`, `agent/turn_api_error.py`, `agent/turn_response_check.py`, `agent/voice_turn_route.py`, `tests/agent/test_provider_fallback.py`, `tests/agent/test_refusal_fallback_reason.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/refusal-fallback-reason.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/refusal-fallback-reason.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/refusal-fallback-reason.md` for relocations/seams.


### 48. event-loop-db-isolation
- **branch:** `soju/patches/event-loop-db-isolation`
- **origin:** `local-author`
- **upstream_pr:** _(none — production outage hardening)_
- **state:** `local-only`
- **rationale:** Keep SessionDB reads and maintenance work off the gateway event loop and writer hot path, construct hygiene agents off-loop, add loop-stall diagnostics, and lower terminal-child CPU priority. Upstream now has its own liveness watchdog; the fork retains the richer stack/PSI diagnostic watchdog because it serves a distinct incident-analysis contract.
- **fix (2026-11-26):** the storage guide states that the WAL checkpoint and FTS merge run on the per-writer maintenance thread.
- **decision (2026-11-26):** `HERMES_TERMINAL_CHILD_NICE` (`tools/environments/local_child_nice.py`, an upstream HX002 new-env-var rule) is accepted as is; the fork does not run `scripts/check`. Move it to `terminal.child_nice` only if the topic is upstreamed.
- **commits:**
  - `a9c8663263` fix(gateway,state): evict SQLite from the event loop; stall watchdog; child niceness
  - `cad0f55d66` fix(gateway,state): track the hygiene build for shutdown, probe the DB generation before maintenance (2026-10-30 sync review)
  - `b53911ba94` fix(state): never fail or block the SessionDB open on its maintenance thread (2026-10-30 sync assembly)
  - `520a123352` fix(tests): stub the nice(1) prefix in the persist_on_release spawn test (2026-10-30 sync regression)
  - `d2496166dd` fix(state,gateway): start the holder's maintenance thread on the first write; meet the v0.23.4 code-health ratchet (2026-11-25 sync)
  - `09b899fb6f` fix(tests): plugin-engine hygiene rebind test drives the real off-loop build (2026-11-25 sync regression)
  - `670a157af6` docs(storage): the WAL checkpoint and FTS merge run on the per-writer maintenance thread
- **touches:** `gateway/loop_stall_watchdog.py`, `gateway/run_bootstrap.py`, `gateway/run_turn_hygiene.py`, `persistence/sqlite/_holder.py`, `persistence/sqlite/_maintenance_thread.py`, `tools/environments/local.py`, `tools/environments/local_child_nice.py`, `tools/process_registry.py`, `website/docs/developer-guide/session-storage.md`, `tests/gateway/test_gateway_plugin_engine_session_db.py`, `tests/gateway/test_hygiene_build_off_loop.py`, `tests/gateway/test_loop_stall_watchdog.py`, `tests/persistence_sqlite/core/test_fts_layout.py`, `tests/persistence_sqlite/core/test_wal_checkpoint_strategy.py`, `tests/persistence_sqlite/core/test_writer_conn_thread_safety.py`, `tests/tools/test_persist_on_release.py`, `tests/tools/test_process_registry.py`, `tests/tools/test_terminal_child_nice.py`
- **v2026.8.3-sync:** hygiene construction was adapted to the new gateway helpers without weakening upstream's compression commit fence; FTS maintenance follows upstream's `messages_fts_cjk` layout.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/event-loop-db-isolation.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/event-loop-db-isolation.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/event-loop-db-isolation.md` for relocations/seams.


### 50. per-tool-disable
- **branch:** `soju/patches/per-tool-disable`
- **stacked-on:** _(none — independent topic rooted on base v0.23.4; re-rooted 2026-11-26 from the tip stack, its own diff applies on bare base and 5bdd12e61b already made it work without the memory chain)_
- **origin:** `local-author`
- **upstream_pr:** _(none — deployment tool-surface control)_
- **state:** `local-only`
- **rationale:** Permit `agent.disabled_toolsets` to subtract individual registered tool names as well as whole toolsets, and propagate the denylist into the Codex hermes-tools MCP sidecar so disabled tools cannot remain visible or callable through that transport.
- **commits:**
  - `710f0ef1c4` fix(codex): honor disabled_toolsets in the hermes-tools MCP sidecar
  - `b7c516cb0f` feat(toolsets): let disabled_toolsets subtract individual tool names
  - `867e9a138b` fix(toolsets): sidecar reads disabled_toolsets like every surface; test seams for v0.22.5 (2026-10-30 sync)
  - `89ddbae7f5` fix(toolsets): pin per-tool disable and toolset precedence without the memory chain (2026-10-30 sync review)
  - `e306bacddf` fix(toolsets): per-tool disabled_toolsets entries follow the #134593 clear and hint logic (2026-11-25 sync review)
- **touches:** `agent/transports/hermes_tools_mcp_server.py`, `cli-config.yaml.example`, `hermes_cli/tools_config.py`, `model_tools.py`, `tests/agent/transports/test_hermes_tools_mcp_server.py`, `tests/hermes_cli/test_disabled_toolsets_per_tool.py`, `tests/tools/test_model_tools.py`
- **2026-08-26-sync:** rebuilt the integration merge on corrected durable-turns; its delta remains only the four per-tool-disable files and contains no worker-pool addition or reversion. Re-anchored the availability test from service-gated TTS to always-present core tools so it exercises per-tool subtraction rather than optional service configuration.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/per-tool-disable.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/per-tool-disable.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/per-tool-disable.md` for relocations/seams.


### 51. compaction-prompt-cc-upgrades
- **branch:** `soju/patches/compaction-prompt-cc-upgrades`
- **origin:** `local-author`
- **upstream_pr:** _(none — compaction fidelity dogfood)_
- **state:** `local-only`
- **rationale:** Improve compaction fidelity with a chronological analysis pre-pass, ordered preservation of user messages, and exact fenced snippets for interrupted work, while keeping stored summaries free of analysis tags and retaining upstream's reference-only safety contract.
- **commits:**
  - `b2cc137c74` feat(compression): port three /compact prompt techniques from Claude Code
  - `cb290165d2` fix(compression): strip <analysis> before the empty/refusal guards (2026-10-30 sync review)
- **touches:** `agent/context_compressor.py`, `agent/context_compressor_prompt_rules.py`, `tests/agent/test_compression_prompt_upgrades.py`
- **v2026.8.3-sync:** exact in-flight snippet guidance moved into Active State; the upstream-removed proactive Historical In-Progress heading was not reintroduced.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/compaction-prompt-cc-upgrades.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/compaction-prompt-cc-upgrades.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/compaction-prompt-cc-upgrades.md` for relocations/seams.


### 52. anthropic-signature-passthrough
- **branch:** `soju/patches/anthropic-signature-passthrough`
- **origin:** `local-author`
- **upstream_pr:** _(none — trusted proxy continuity)_
- **state:** `local-only`
- **rationale:** Allow explicitly configured Anthropic signature-passthrough proxies to retain signed thinking blocks across tool turns, while remaining fail-closed for all other providers and stripping both replay channels during reactive invalid-signature recovery.
- **commits:**
  - `71feda86c1` feat(anthropic): trust signature-passthrough proxies for thinking replay
  - `23f071ad32` fix(config): register anthropic_signature_passthrough as a known provider key
  - `4ff8bd1239` test(anthropic): re-point signature-passthrough tests at the thinking route classifier
  - `66bfd6a1ec` fix(anthropic): strip the ordered carrier in the off-native signature-400 repair (2026-10-30 sync review)
  - `ea86254249` fix(anthropic): read the signature-passthrough flag through the config loader (2026-11-25 sync)
- **touches:** `agent/anthropic_thinking_policy.py`, `agent/turn_recovery.py`, `hermes_cli/config_providers.py`, `tests/agent/test_anthropic_signature_passthrough.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/anthropic-signature-passthrough.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/anthropic-signature-passthrough.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/anthropic-signature-passthrough.md` for relocations/seams.


### 54. worker-cpu-hygiene
- **branch:** `soju/patches/worker-cpu-hygiene`
- **origin:** `local-author`
- **upstream_pr:** _(none — 2026-08-05 loop-stall follow-up)_
- **state:** `local-only`
- **rationale:** Stop worker threads from starving the gateway event loop of GIL time: the checkpoint size walk (`_dir_size_bytes`) and the pre-v2 mtime loop used Path.rglob (rewritten on os.scandir with identical early-stop/symlink semantics). The `_dir_file_count` half is upstream's since af70d77be2. Since 2026-11-26 the lifecycle_guard tokenize-once hunk of the original 0df0228fb6 lives in #66 lifecycle-guard-hardening (the commit was split by file, content unchanged).
- **commits:**
  - `0d4a75d106` perf(tools): walk checkpoint dirs with os.scandir instead of Path.rglob (split from 0df0228fb6)
  - `12ebb868d2` fix(tools): walk pre-v2 checkpoint mtimes with os.scandir (2026-10-30 sync review)
- **touches:** `agent/checkpoints/maintenance.py`, `agent/checkpoints/manager.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/worker-cpu-hygiene.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/worker-cpu-hygiene.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/worker-cpu-hygiene.md` for relocations/seams.


### 56. refusal-recovery
- **branch:** `soju/patches/refusal-recovery`
- **stacked-on:** `soju/patches/memory-redesign` (2026-11-25 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood refusal-risk preemptive routing)_
- **state:** `local-only`
- **rationale:** Gateway model router gains an orthogonal `refusal_risk` boolean (plus confidence) so hard frontier-refusal turns are preemptively switched to low-refusal routes before the primary model contaminates context. Label flow S1–S7 stays benched and byte-compatible; S0 only sets the orthogonal flag. Config `model_routes.router.refusal` (default disabled) maps SYSTEM_DEV/FRONTEND_DEV → PERMISSIVE_DEV (kimi-k3) and NORMAL/DOCUMENT_WORK → PERMISSIVE_CHAT (grok-4.5) when confidence ≥ threshold and source is LLM. Enforce-mode applied refusal switches emit an owner-visible platform notice via existing `_deliver_platform_notice` (config-gated). Follow-up tune: S0 explicitly false for draft-edit/tone-down of existing NSFW text and own-infra audits; true only for from-scratch NSFW authoring and third-party attack intents. Bench on 300 gold cases @0.85: precision 0.992 / recall 0.928; grok subset 1.0/1.0.
- **fixes (2026-11-26):** a PERMISSIVE route that resolves to a backend already in the refusal chain is skipped (the live `grok-4.6 -> grok-4.6` chain); the dead recall-quarantine plumbing and the never-written `last_label` lookup are removed (no behaviour change).
- **absorbed (2026-11-26 cleanup):** one refusal topic (the shared test files are resolved once; the #58/#59 shared branch is gone; #76 moved down from after #75, checked to apply). #59's cancelling pair e7b06f608b and its revert f0622e9ba8 (net diff empty) is no longer carried. members in commit order: #56 refusal-risk-routing, #57 refusal-api-fallback, #58 refusal-hop-clean-fork, #59 refusal-soft-turnend, #76 clean-fork-soft-refusal-only. Old member tips: `backup/cleanup-1126/soju/patches/<name>` and the bundle.
  - #57 refusal-api-fallback: When the primary model returns API-level safety refusal (`content_filter` / `FailoverReason.content_policy_blocked`), walk generic `fallback_providers` (opus) first, then resolved PERMISSIVE routes (dev→chat or chat→dev from primary runtime). Gated by `model_routes.router.refusal.enabled` + new `api_fallback` (default false). Preserves `_fallback_reason=content_policy_blocked` for skill-gate turn exemption. Non-refusal reasons unchanged.
  - #58 refusal-hop-clean-fork: Hard refusal: upstream retention (v0.23.4) removes the refused turn before the router runs; this topic only stages a one-shot PERMISSIVE hop and the notice, so the user's resend lands on the permissive route. Soft refusal: the classifier's `prior_refusal` masks the newest active assistant row (`active=0`, row preserved, `compacted` untouched) and one-shot routes the next turn to PERMISSIVE. History (pre-v0.23.4): the hard path also masked the refusal row. Replaces the preemptive `rewrite_transcript` that hard-deleted the whole session with no refusal evidence. Mid-turn clean-fork scope cut to the current turn (completed history preserved). `prior_refusal` classifier field costs zero extra API calls (router already receives `recent_turns`). Bench 30/30 at precision 1.0 / recall 1.0 after two prompt narrowings (sub-part inability and domain deferral no longer read as refusals). New `deactivate_messages`/`reactivate_messages` are reversible; no hard deletion anywhere.
  - #59 refusal-soft-turnend: Two gaps observed live on thread 1536678317166694441. (1) A soft refusal authored inside a turn (finish_reason=stop, no error) left no turn-end signal, so nothing masked it and no permissive hop was staged. Add `_handle_gateway_soft_refusal` probing the just-produced response through the existing `prior_refusal` classifier field, gated by length floor + config, with a `refusal_recovery_count` loop guard (`max_recovery_hops` default 2) that stops laundering repeat refusals through more models. (2) A refusal-recovery agent whose generic opus hop then died of `overloaded` never reached PERMISSIVE because the refusal walk only triggered on `content_policy_blocked`. Extend it so an active refusal recovery falls through to PERMISSIVE on ANY generic-exhaustion reason, and add logging to the previously silent `_build_refusal_fallback_chain`.
  - #76 clean-fork-soft-refusal-only: clean_fork (context truncation on refusal fallback) was firing on ALL refusal-shaped signals, including Anthropic structured `stop_reason=refusal` — a contaminated signal claude-fable-5 emits during normal work. Mid-task it cut tool progress from the fallback model's context, causing re-scoping + announce-and-stop early turn ends (2026-08-19 03:23 incident, session 20260813_031938_3171f671). Owner design verdict: clean_fork exists for ROUTER-CLASSIFIED soft refusals (natural-language decline text contaminating context) ONLY; API/provider/stream policy signals still fall back but must preserve full conversation and tool progress. Adds `RefusalSource` enum (5 sources) + `should_apply_clean_fork()` gate with per-decision INFO log; removes the dead auth-path content_policy branch (is_auth and content_policy are mutually exclusive in error_classifier). Incident-replay test asserts tool results survive into the fallback model's input.
- **decision (2026-11-26, O-A9):** accept upstream's v0.23.4 retention contract: a hard refusal removes the turn and the user resends. Keep only the one-shot PERMISSIVE hop and the notice; do not carry the refused request into the forced turn.
- **commits:** (own commits in order; `[#N]` names the absorbed topic each came from)
  - `d56e069e78` feat(gateway): refusal-risk preemptive routing with permissive routes [#56]
  - `4a046e327c` tune(gateway): narrow S0 refusal for draft-edit vs authoring [#56]
  - `57dc0c02dd` feat(fallback): content_policy_blocked prefers PERMISSIVE routes [#57]
  - `fe58e33a23` fix(fallback): content_policy walks generic before PERMISSIVE [#57]
  - `25e9a74ace` feat(refusal): clean-fork history on PERMISSIVE hop [#58]
  - `fb98d7a490` feat(refusal): mask refused turn and auto-switch instead of rewriting history [#58]
  - `16c0ed82d0` tune(router): narrow prior_refusal to whole-request refusals [#58]
  - `7657095847` fix(refusal): keep the failed-turn boundary when masking a hard refusal (2026-10-30 sync) [#58]
  - `531c5bb68c` fix(refusal): hard-refusal notice drops the always-zero hidden-message count (2026-11-25 sync review) [#58]
  - `63166bbc37` feat(refusal): detect soft refusals at turn end with recovery-hop guard [#59]
  - `7888e8b3f3` fix(fallback): log empty/built refusal chain reasons [#59]
  - `b4004facf6` fix(refusal): run the soft-refusal probe without the dead hard-mask gate (2026-11-25 sync review) [#59]
  - `8aa734ff45` fix(refusal): reserve clean fork for router soft refusals [#76]
  - `5e820e5914` fix(refusal): drop a PERMISSIVE route that resolves to a backend already in the chain [#fix 2026-11-26]
  - `af48ac748b` refactor(refusal): remove the dead recall-quarantine plumbing and last_label lookup [#fix 2026-11-26]
- **touches:** `agent/agent_init_fallback.py`, `agent/chat_completion_helpers.py`, `agent/refusal_fallback.py`, `agent/refusal_history.py`, `agent/turn_api_error.py`, `agent/turn_context.py`, `agent/turn_iteration_prep.py`, `agent/turn_response_check.py`, `agent/turn_truncation.py`, `gateway/model_router.py`, `gateway/run_agent_cache.py`, `gateway/run_inbound_model_router.py`, `gateway/run_turn_persistence.py`, `gateway/run_turn_runner.py`, `gateway/session_state.py`, `gateway/session_transcript.py`, `hermes_cli/model_routes.py`, `tests/agent/test_refusal_fallback_reason.py`, `tests/agent/test_refusal_history.py`, `tests/gateway/test_model_router.py`, `tests/gateway/test_model_router_failed_turn_boundary.py`, `tests/gateway/test_session_refusal_mask.py`, `tests/hermes_cli/test_model_routes.py`
- **member port reports (v0.23.4-sync):** `reports/refusal-api-fallback.md`, `reports/refusal-hop-clean-fork.md`, `reports/refusal-soft-turnend.md`, `reports/clean-fork-soft-refusal-only.md` under `~/work/hermes/upstream-sync-20261125/`.
- **2026-08-26-sync:** replayed the refusal-risk delta onto the corrected production prefix so it no longer carries stale durable/memory integration state. Refreshed the topic after the durable ledger correction so its assembled prefix no longer restores the redundant post-redelivery `resume_pending` clear.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/refusal-risk-routing.md` for relocations/seams.
- **v0.22.5-sync:** DONE — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/refusal-risk-routing.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/refusal-risk-routing.md` for relocations/seams.


### 61. gateway-hygiene-hardening
- **branch:** `soju/patches/gateway-hygiene-hardening`
- **stacked-on:** _(none — independent topic rooted on base v0.23.4)_
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate)_
- **state:** `local-only`
- **rationale:** Gateway session hygiene retried compression EVERY turn on sessions where the protected tail spans the whole transcript (1M-context model → tail_token_budget 190k; `hygiene_hard_message_limit: 400` keeps re-triggering; the compressor aborts in ~60ms with `failure_class=no_progress` and no cooldown was recorded — 106 warnings over 4 log rotations, one session hit 20 consecutive retries). The gateway then mis-attributed every no-op to "#21301 no session_db on the hygiene agent" even though session_db was bound. Fix: (1) record the durable DB-backed compression-failure cooldown row (600s, reason `no_progress: protected tail spans the whole transcript`) in the no_progress branch of `compress_context`, classified as a hygiene-only marker (`_HYGIENE_PREAGENT_ONLY_COOLDOWN_MARKERS`) so it gates only the gateway's pre-agent hygiene — the existing hygiene cooldown check (#74136) then stops the churn — and never the in-agent compressor, whose in-memory backoff upstream lifts once the transcript grows (2026-11-25 regression fix d58c750c9f); manual `/compress` (force=True) is unaffected; (2) split the #21301 warning into no_progress (INFO) / summary-abort (WARNING) / genuinely-missing-session_db (#21301 wording kept) / other (WARNING); (3) hygiene trigger log now attributes `trigger=hard_message_limit (N)` vs `token_threshold`.
- **fix (2026-11-26):** `compression.hygiene_threshold` is registered in DEFAULT_CONFIG (0.85) and documented; no behaviour change (the gateway reads the raw user config).
- **absorbed (2026-11-26 cleanup):** one independent hygiene topic (both edit `gateway/run_turn_hygiene.py` and its test; nothing stacks on #74). members in commit order: #61 hygiene-noprogress-cooldown, #74 hygiene-diag-and-lock-offload. Old member tips: `backup/cleanup-1126/soju/patches/<name>` and the bundle.
  - #74 hygiene-diag-and-lock-offload: Three hygiene-block fixes from the 2026-08-13 incident forensics. (1) `Session hygiene auto-compress failed: %s` logged empty for `asyncio.TimeoutError` (empty `str()`), which made the incident's 41 failure lines carry no cause — switch to `%r` + exception class name. (2) The cancelled-compression lock release ends in a synchronous SQLite DELETE that appeared in event-loop stall stack dumps — offload via `asyncio.to_thread` (cancellation path; nothing orders against the release, commit already fenced). (3) Expose the hardcoded 0.85 hygiene trigger as `compression.hygiene_threshold` (config.yaml per repo rubric, range-validated, default preserved) with a deduplicated warning-only layering check: `compression.threshold >= hygiene_threshold` means the safety net fires before the primary compressor — the exact inversion that amplified the 08-13 compress storm.
- **commits:** (own commits in order; `[#N]` names the absorbed topic each came from)
  - `b1445ed946` fix(compression): record cooldown on no_progress hygiene aborts + accurate trigger/abort diagnostics
  - `3cba075ef1` test(agent): stale-cooldown test allows the fork's durable no-progress cooldown
  - `179d72da98` fix(tests): hygiene restart test asserts the summary-abort diagnosis (2026-10-30 sync review)
  - `d58c750c9f` fix(compression): the no-progress cooldown gates gateway hygiene, not the in-agent compressor (2026-11-25 sync regression)
  - `50b0aa2af8` fix(gateway): hygiene failure diagnostics + off-loop lock release + hygiene_threshold config [#74]
  - `0348326a64` fix(gateway): defer hygiene cleanup when cancelled during off-loop lock release (2026-10-30 sync review) [#74]
  - `dd2eb2dac8` fix(config): register compression.hygiene_threshold in DEFAULT_CONFIG and document it [#fix 2026-11-26]
- **touches:** `agent/context_compressor.py`, `agent/conversation_compression.py`, `gateway/run_turn_hygiene.py`, `hermes_cli/config_defaults.py`, `website/docs/user-guide/configuration.md`, `tests/agent/test_compression_abort_state_reset.py`, `tests/gateway/test_session_hygiene.py`
- **member port reports (v0.23.4-sync):** `reports/hygiene-diag-and-lock-offload.md` under `~/work/hermes/upstream-sync-20261125/`.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/hygiene-noprogress-cooldown.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/hygiene-noprogress-cooldown.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/hygiene-noprogress-cooldown.md` for relocations/seams.


### 62. durable-bg-processes
- **branch:** `soju/patches/durable-bg-processes`
- **stacked-on:** `soju/patches/refusal-recovery` (2026-11-25 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none yet — upstream candidate)_
- **state:** `local-only`
- **rationale:** Background tool processes died on every gateway restart via three stacked kill mechanisms: (1) shutdown `process_registry.kill_all()` global sweep in `_stop_impl`, (2) systemd `KillMode=mixed` SIGKILL on the unit cgroup after `TimeoutStopSec`, (3) `ExecStopPost=gateway.cgroup_cleanup` per-PID SIGKILL of every cgroup survivor. Checkpoint recovery (`processes.json`) only ever adopted crash survivors, and adopted them `detached` with no output. Fix (config-gated, `terminal.durable_background: false` default off, config.yaml only — no tool-schema change): durable non-PTY local spawns run inside `systemd-run --user --scope --unit=hermes-bg-<sid>` (sibling cgroup — escapes 2 and 3; readiness marker + graceful fallback to plain spawn when scopes are unavailable), write output to `$HERMES_HOME/bg-logs/<sid>.log` (0600/0700) as source of truth so post-restart `read_log`/`poll`/notification tails work, and are excluded from the shutdown sweep via `kill_all(exclude_durable=True)`. Explicit `process(action=kill)` still terminates them; scope units are `reset-failed` on completion. Review fix vs executor output: cron `#60432` interrupted-marking made unconditional again — the cron agent is a ThreadPoolExecutor thread inside the gateway process and dies with it even when its durable subprocess survives; gating on `_killed>0` would let a truncated run report success (regression test added). E2E-verified on this host: scope cgroup escape, checkpoint re-adoption in a fresh registry, and full output readback after simulated restart.
- **decision (2026-11-26, O-A10):** the parked item "durable mode only outside per-turn worker processes" is closed as a documented scope limit (this host runs no `hermes-worker-*` units). Profile `mymel` stays without `terminal.durable_background`.
- **commits:**
  - `bf34feba3f` Add durable local background processes
  - `75833378c5` fix(process-registry): pass stdin=DEVNULL explicitly in the durable sibling (2026-10-30 sync regression)
  - `5458342fd6` fix(process-registry): fit durable background processes to v0.23.4 job ownership, profile scope and forced update sweep (2026-11-25 sync)
  - `6458f6c66d` fix(process-registry): clean up durable scopes over the launch's user bus and document terminal.durable_background (2026-11-25 sync review)
- **touches:** `gateway/run_shutdown.py`, `hermes_cli/config_defaults.py`, `hermes_cli/update_cmd_force_stop.py`, `tools/process_registry.py`, `tools/process_registry_checkpoint.py`, `tools/process_registry_durable.py`, `website/docs/user-guide/configuration.md`, `tests/gateway/test_cron_active_work_drain.py`, `tests/gateway/test_gateway_shutdown.py`, `tests/hermes_cli/test_update_force_stop.py`, `tests/tools/process_registry/__init__.py`, `tests/tools/process_registry/test_durable_scope_cleanup.py`, `tests/tools/test_process_registry.py`
- **2026-08-26-sync:** replayed the durable-background delta on the corrected prefix, preserving worker niceness and background-first wait escalation. Corrected the shared `Popen` kwargs so argv is passed exactly once and niceness is applied exactly once.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/durable-bg-processes.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/durable-bg-processes.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/durable-bg-processes.md` for relocations/seams.


### 63. route-aware-outage-fallback
- **branch:** `soju/patches/route-aware-outage-fallback`
- **stacked-on:** `soju/patches/durable-bg-processes` (2026-11-25 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — model_routes is fork-only (#34); upstream has no route subsystem to be aware of)_
- **state:** `local-only`
- **rationale:** Outage-shaped failures (`rate_limit`, `billing`, `upstream_rate_limit`, `overloaded`, `server_error`) walked only the global `fallback_providers:` chain, which has no route context and lands dev-routed sessions on a chat-tier model — silently demoting them and tripping skill-gate dev_edit capability gates until the quota window resets (2026-08-13: claude-lb model-quota-exhausted, 13h window, 90 fable→opus fallbacks in one day). Fix: `try_activate_fallback` resolves the CURRENT route via `runtime_satisfies_route` and walks that route's healthy `fallbacks:` (skipping unhealthy/self/unresolvable entries) BEFORE the global chain. Content-policy refusals unchanged (still generic-then-PERMISSIVE, patch #57). Non-routed runtimes and deployments without model_routes behave exactly as before.
- **commits:**
  - `9066607f8b` feat(fallback): route-aware outage fallback prefers current route fallbacks
  - `b7b329834f` fix(fallback): resolve the outage route from recorded intent, not model id
- **touches:** `agent/chat_completion_helpers.py`, `agent/outage_route_fallback.py`, `agent/runtime_control.py`, `tests/agent/test_fallback_helpers.py`, `tests/agent/test_runtime_control.py`
- **followup (2026-08-13):** the first cut was inert. `_build_outage_route_fallback_chain` identified the current route by scanning for the first route the live model satisfies, but membership is ambiguous — `claude-fable-5` is the DOCUMENT_WORK fallback AND the SYSTEM_DEV/FRONTEND_DEV primary, and DOCUMENT_WORK sorts first. A SYSTEM_DEV session resolved as DOCUMENT_WORK, whose only fallback is claude-fable-5 itself; that entry was skipped as self-targeting, the chain came back empty, and the global chain demoted the session to opus regardless. Fix: record the route the session actually selected (`_active_route_name`, set by `dispatch_model_switch` on the resolved/no-op/effort-only outcomes and carried across gateway pre-dispatch agent rebuilds) and prefer it while the live runtime still satisfies it; stale or absent intent falls back to the old scan. `model_status` shared the same ambiguity (reported DOCUMENT_WORK/CHAT for dev sessions) and now consults the same intent.
- **2026-08-26-sync:** replayed the route-aware fallback delta onto the corrected durable-background prefix; the corrected earlier topics already preserve pending runtime-route state, so the stale integration-repair commit was no longer needed. Refreshed the exact refusal-API dependency edge after its ledger correction.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/route-aware-outage-fallback.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/route-aware-outage-fallback.md` for relocations/seams.
- **v0.23.4-sync:** DONE — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/route-aware-outage-fallback.md` for relocations/seams.


### 66. lifecycle-guard-hardening
- **branch:** `soju/patches/lifecycle-guard-hardening`
- **stacked-on:** _(none — independent topic rooted on base v0.23.4; touches only upstream's `cron/lifecycle_guard.py` and two test files)_
- **origin:** `local-author`
- **upstream_pr:** _(none — #77131/#76762 third-generation follow-up; upstream candidate)_
- **state:** `local-only`
- **rationale:** The lifecycle guard split commands on newlines and shell-tokenized every line, so multiline `python3 -c "..."` / Python heredoc payloads were parsed as shell: a payload line like `sys.path.insert(0, os.path.expanduser('~/.hermes/hermes-agent'))` tokenized into a `/`-bearing token, got treated as a referenced-script path, resolved to a real directory, and `_read_referenced_script`'s non-regular fail-closed marked it unsafe — blocking benign terminal commands inside gateway sessions (29 blocked instances / 13 unique commands in request dumps; 9 of 13 were this false positive). Fix removes only syntactically proven non-shell regions before matching and tokenization: a complete quoted argument to a Python executable's `-c` (basename `python`/`pythonN`/`pythonN.M`, no `--` before payload), or the body of a complete Python heredoc (quoted/bare delimiter, `<<-` tab-strip honored). Unclosed quotes, missing heredoc terminators, shell `-c` payloads, here-strings, and non-Python interpreters stay visible (fail-closed). Preserved detections verified: `hermes gateway restart|stop`, `systemctl`/`launchctl submit|bootstrap`, `bash -c` payload recursion, nested `.sh` walking, binary/NUL skips, and lifecycle commands before/after sanitized payloads. Tokenize-once-per-level contract (patch #54) kept: sanitize once, share the segment list. Verified via TDD (3 RED → GREEN), 100-test file pass, 490-test cron+restart-loop pass, 12 adversarial cases, and historical replay (9/13 freed, 3 genuine lifecycle still blocked, 1 dump-truncation artifact correctly fail-closed).
- **absorbed (2026-11-26 cleanup):** one guard topic instead of a 4-branch stack (#54 guard hunk, #55, #66, #68); its first commit is the cron/lifecycle_guard.py half of #54's 0df0228fb6, split by file with content unchanged. members in commit order: #54 worker-cpu-hygiene (guard hunk only), #66 lifecycle-guard-multiline, #68 lifecycle-guard-stmt-boundary. #55 lifecycle-guard-nul-fallback (test-only) is no longer under it. Old member tips: `backup/cleanup-1126/soju/patches/<name>` and the bundle.
  - #54 worker-cpu-hygiene (guard hunk): lifecycle_guard tokenized each command three times per nesting level (launchctl scan, sh -c payload scan, referenced-script scan); tokenize once per level and share the segment list (the tokenize-once-per-level contract #66 builds on). The checkpoint half stays in #54.
  - #68 lifecycle-guard-stmt-boundary: `_GATEWAY_LIFECYCLE_PATTERN` Branch D (`p?kill…hermes…gateway`, both token orders) used `[^\n]*` token gaps, so only newlines bounded a match — `;`, `&&`, `|` passed through and stitched INDEPENDENT statements on one physical line into a single "kill the gateway" match. Live reproduction: `kill -USR1 <pid> && echo …; sleep 20; systemctl --user show hermes-gateway-mymel.service -p …` — the kill is the SANCTIONED SIGUSR1 self-restart path and hermes/gateway tokens come from a read-only `systemctl show` unit name three statements later. Counterfactuals verified: newline-separated same content passed, removing the kill passed, renaming the unit passed. Same design-flaw family as #66 (statement boundaries ignored), opposite direction: #66 over-split (newline = new shell statement inside Python payloads), this one under-split (`;&|` not a boundary at all). Fix narrows both Branch D gaps to `[^\n;&|]*`; `&&`/`||` break on their first char so compound operators are covered. Branches A/B/C untouched (their gaps sit between a verb and its own arguments within one statement). Signal whitelisting (-USR1 exemption) considered and rejected as guard-weakening. Real threats preserved: `pkill -f "hermes.*gateway"`, `kill $(pgrep -f …)`/backtick substitution (no `;&|` inside), line-continuation folding, Branch C after `;` prefix. Verified: TDD RED 4→GREEN, file 113 passed, tests/cron 390 passed, 15 adversarial cases.
- **commits:** (own commits in order; `[#N]` names the absorbed topic each came from)
  - `21f6a942f4` perf(cron): tokenize each lifecycle-guard command once per nesting level (split from 0df0228fb6) [#54 (split)]
  - `d8d9b5edfa` fix(cron): stop lifecycle guard from shell-parsing python payloads
  - `fcc5adc02c` fix(cron): mask python payloads only where a shell lexer proves them (2026-10-30 sync review)
  - `d57e9bb5d6` fix(cron): keep python payloads visible to the direct lifecycle scan (v0.23.4 #127698)
  - `5ed38da56e` fix(cron): keep python heredoc terminator lines in the walk mask (2026-11-25 sync review)
  - `4d8a9f1c93` fix(cron): stop lifecycle guard Branch D from spanning statement boundaries [#68]
  - `8a776709c5` fix(cron): keep Branch D matching kills fed by a substitution pipeline (2026-10-30 sync review) [#68]
- **touches:** `cron/lifecycle_guard.py`, `tests/cron/test_lifecycle_guard_python_payloads.py`, `tests/hermes_cli/test_gateway_restart_loop.py`
- **member port reports (v0.23.4-sync):** `reports/worker-cpu-hygiene.md`, `reports/lifecycle-guard-stmt-boundary.md` under `~/work/hermes/upstream-sync-20261125/`.
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/lifecycle-guard-multiline.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/lifecycle-guard-multiline.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/lifecycle-guard-multiline.md` for relocations/seams.


### 67. scoped-exhaustion-upstream-classify
- **branch:** `soju/patches/scoped-exhaustion-upstream-classify`
- **stacked-on:** _(none — independent topic rooted on base v0.23.4; re-rooted 2026-11-26, it touches only `agent/error_classifier.py` and its test, identical at base and at the old #66 tip)_
- **origin:** `local-author`
- **upstream_pr:** _(none — depends on claude-lb-specific message contract; fork-only)_
- **state:** `local-only`
- **rationale:** claude-lb returns HTTP 503 when ONE model's weekly scoped quota is exhausted across its pool while every other model it serves stays healthy (body carries the spec-locked `model-quota-exhausted` token; message shape pinned by claude-lb OpenSpec `account-load-balancing` since 2026-08-13, PR Soju06/claude-lb#127). The generic 503 branch classified this as `overloaded`, which (a) is in `_PASSIVE_UNHEALTHY_REASONS`, so one dead model marked the whole `claude-lb` provider unhealthy for fail_ttl (120s) in the model-routes passive health cache while claude-opus-5 on the same provider served fine, and (b) is transport-shaped in the retry loop, burning 2 retries against a condition whose only recovery is a scoped window reset days out. Fix classifies the token as `upstream_rate_limit` — the existing "one model at an aggregator, not the provider" reason (OpenRouter upstream-429 precedent): deliberately excluded from passive-unhealthy marking, `is_rate_limited`-eager fallback (no wasted transport retries), rotation forced off, route-aware fallback chain applies, and `error_context.upstream_provider` carries the model so the status line reads "Upstream claude-fable-5 rate-limited". Two return sites: the 503/529 status branch (after overflow guards, before the `overloaded` terminal) and `_classify_by_message` (BEFORE `_OVERLOADED_PATTERNS` and `_RATE_LIMIT_PATTERNS` — ordering pinned by test, else "Rate limit exceeded" text would enable credential rotation). 429 branch untouched (regression-pinned: scoped token on 429 stays `rate_limit`). Verified: 73-test classifier file + 134-test fallback/failover/route sweep pass; bare-503 → `overloaded` regression pin preserved verbatim (a delegate attempt to weaken it to an overload-texted message was caught in diff audit and reverted).
- **commits:**
  - `e3fa124353` fix(fallback): classify scoped model-exhaustion 503s as upstream_rate_limit
  - `f0847fe2be` test(fallback): pin scoped exhaustion ahead of long-quota-window wording (2026-11-25 sync review)
- **touches:** `agent/error_classifier.py`, `tests/agent/test_error_classifier_scoped_exhaustion.py`
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/scoped-exhaustion-upstream-classify.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/scoped-exhaustion-upstream-classify.md` for relocations/seams.
- **v0.23.4-sync:** DONE — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/scoped-exhaustion-upstream-classify.md` for relocations/seams.


### 71. delegated-child-marker-latch
- **branch:** `soju/patches/delegated-child-marker-latch`
- **stacked-on:** `soju/patches/route-aware-outage-fallback` (2026-11-25 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** _(none yet — proposal artifacts ready under `~/work/comm-improve/latch-bug/`; open after dogfood soak)_
- **state:** `local-only`
- **rationale:** A normal gateway session (never called `delegate_task`) got permanently classified as a delegate child — kanban mutations refused, `model_tools` tool-definition cache key poisoned. Primary carrier is the shared bash session snapshot (`/tmp/hermes-snap-<id>.sh`): `_resolve_container_task_id` collapses subagent task ids to `"default"` so parent/child share one snapshot, and `_wrap_command`'s trailing `export -p` write-through persisted the child's `HERMES_DELEGATED_CHILD_CONTEXT=1` into the parent's next turns (D1). Same mechanism defeated `scrub_kanban_env()` on the terminal path: sourcing the snapshot re-exported the dispatcher's `HERMES_KANBAN_*` over the scrubbed Popen env (D2). Secondary: detached daemons (ingest-curator, bg-review, async delegation) copy the delegated-child ContextVar and keep it for the process lifetime (D4); `KANBAN_ENV_KEYS` allowlist drifted to 7 of the dispatcher's 10 injected vars (D6). All four reproduced deterministically without a gateway (`~/work/comm-improve/latch-bug/repro*.py`). Fix: unset both families inside the snapshot dump subshell (same audited mechanism as `HERMES_SESSION_*`; no line-based filtering per #71296); scope-correct ContextVar propagation via keyword-only `inherit_delegated_child=True` with the three detached sites opting out (blanket clear measured to regress: in-turn workers lose the flag and a delegated child can mutate board state — fail-closed default kept); once-per-process latch fingerprint diagnostic. Snapshot fix covers all environment backends (`_wrap_command` lives on `BaseEnvironment`).
- **decision (2026-11-26, O-A11):** the D6 prefix-wide `HERMES_KANBAN_*` scrub is dropped; `scrub_kanban_env` strips exactly upstream's `KANBAN_ENV_KEYS` again (upstream b578261584), so non-identity Kanban tunables reach descendants. D2 (snapshot unset) and D4 (`inherit_delegated_child` opt-outs) and the diagnostic stay.
- **commits:**
  - `40c8fc966b` fix(delegation): stop delegated-child marker and kanban identity latching across turns
  - `933f71bdb2` fix(delegation): keep execute_code RPC loops out of the detached-daemon list and opt the curator idle timer out of the marker (2026-11-25 sync review)
  - `ccfda39df8` fix(delegation): drop the prefix-wide kanban scrub and strip only worker identity like upstream
- **touches:** `agent/delegation_context.py`, `agent/ingest_curator.py`, `run_agent.py`, `tools/async_delegation.py`, `tools/environments/base_session_env.py`, `tools/thread_context.py`, `tests/tools/test_delegated_child_marker_latch.py`
- **2026-08-26-sync:** replayed the latch fix onto corrected phase-2 curator to retain the declared edge and curator opt-out behavior.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/delegated-child-marker-latch.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/delegated-child-marker-latch.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/delegated-child-marker-latch.md` for relocations/seams.


### 75. turn-exit-reason-tagging
- **branch:** `soju/patches/turn-exit-reason-tagging`
- **stacked-on:** `soju/patches/delegated-child-marker-latch` (2026-11-25 chronological tip stack)
- **origin:** `local-author` (implemented by kanban goal worker t_9c855d47; last worker-implemented patch before the 2026-08-18 methodology rollback — see mistakes.db #1099)
- **upstream_pr:** _(none yet)_
- **state:** `local-only`
- **rationale:** Early conversation-loop exits (compression lock, 413, stream truncation, rate guard, ~16 paths) returned silently with no log line, making "the agent just stopped" incidents unattributable (comm-improve M5 diagnostics). Tag every early exit via a `_tag_exit()` helper; the pre-existing fallback is renamed `early_return(untagged)` so any future untagged site self-surfaces. Observability-only: no control-flow or return-shape change, tagging failures are swallowed.
- **commits:**
  - `389b768fd5` feat(telemetry): tag every early conversation-loop exit with its reason
  - `3675047ce3` fix(telemetry): tag upstream interpreter shutdown exit
- **touches:** `agent/conversation_loop.py`, `agent/turn_api_call.py`, `agent/turn_api_error.py`, `agent/turn_empty_response.py`, `agent/turn_final_response.py`, `agent/turn_overflow.py`, `agent/turn_preflight.py`, `agent/turn_recovery.py`, `agent/turn_recovery_autorecover.py`, `agent/turn_recovery_fail_fast.py`, `agent/turn_response_check.py`, `agent/turn_response_intake.py`, `agent/turn_tool_validation.py`, `agent/turn_trace_loop.py`, `agent/turn_truncation.py`, `tests/agent/test_turn_exit_reason_tagging.py`
- **2026-08-26-sync:** replayed exit tagging onto the corrected assembled conversation loop and retained the new upstream interpreter-shutdown exit tag.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/turn-exit-reason-tagging.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/turn-exit-reason-tagging.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/turn-exit-reason-tagging.md` for relocations/seams.


### 79. gemini-classifier-thinking-compat
- **branch:** `soju/patches/gemini-classifier-thinking-compat`
- **stacked-on:** `soju/patches/turn-exit-reason-tagging` (2026-11-25 chronological tip stack; #76 and #78 folded on 2026-11-26)
- **origin:** `local-author` (implemented by codex per /tmp/gemini-thinking-gateway-prompt.md)
- **upstream_pr:** _(none — `gateway/model_router.py` router is fork-only)_
- **state:** `local-only`
- **rationale:** `_call_gemini` hardcoded `thinkingConfig: {thinkingBudget: 0}`, which Gemini 3.5+ rejects with HTTP 400 INVALID_ARGUMENT (live smoke 2026-08-20), so any router model upgrade past 3.1 silently drops every turn to the regex fallback. New `_thinking_config(model)` parses the family from the model id: <3.5 keeps `thinkingBudget: 0`, 3.5+ (and unparseable ids) send `thinkingLevel: "low"` (thoughts=0 on 3.5/3.6; 3.7 cannot disable thinking at all and `minimal` is rejected — one reason 3.7 was not adopted). Unblocks the config-side router model change to `gemini-3.5-flash-lite` (gold-set rebench 2026-08-20, 350 cases: acc .983 vs .969, false switches 6→2, p50 -347ms, cost -36%; raw at /mnt/scratch/bench/gemini-router-upgrade/2026-08-20/). Prompts/schemas/regexes untouched — skill-gate byte-identity parity tests unaffected.
- **commits:**
  - `0632ffeb09` fix(gateway): model-family-aware thinkingConfig for Gemini classifier
- **touches:** `gateway/model_router.py`, `tests/gateway/test_model_router.py`
- **2026-08-26-sync:** refreshed the exact base-persona dependency edge; classifier behavior is unchanged.
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/gemini-classifier-thinking-compat.md` for relocations/seams.
- **v0.22.5-sync:** DONE — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/gemini-classifier-thinking-compat.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/gemini-classifier-thinking-compat.md` for relocations/seams.


### 80. reasoning-route-wire
- **branch:** `soju/patches/reasoning-route-wire`
- **stacked-on:** `soju/patches/gemini-classifier-thinking-compat` (2026-11-25 chronological tip stack)
- **origin:** `local-author` (implemented by codex per /tmp/reasoning-route-fix-prompt.md; diagnosis at /tmp/reasoning-route-wire-diagnosis.md)
- **upstream_pr:** _(none — router/session-override persistence seam is fork-only)_
- **state:** `local-only`
- **rationale:** Probe-verified diagnosis (2026-08-20) confirmed the routed reasoning happy path is correct but found three robustness gaps that silently drop routed `reasoning_effort`: (1) router-applied reasoning lived only in in-memory SessionState — a gateway restart rehydrated the routed model but not the effort, and `noop_satisfied` never re-stamps it; now persisted (sanitized, non-secret, nested in the existing session model-override record) and rehydrated unless a live explicit session override exists; (2) `try_activate_fallback()` re-resolved reasoning from global config only, overwriting a non-None session/routed config (None with global unset); now preserves the active value and resolves config only when absent — model-specific downgrade stays in the adapter; (3) cached prior-fallback agents let `restore_primary_runtime()` restore a stale non-None snapshot over the freshly assigned per-turn config; TurnRunner now syncs `_primary_runtime.reasoning_config` at assignment. RED reverse-verified: fixes reverted with tests kept → exactly 5 failures. 81 tests green across 8 suites at commit.
- **fix (2026-11-26, O-A6):** `/reasoning reset` drops the router's `reasoning_config` from the session's /model record (memory and store), so the reset survives a restart on a routed session; the `router_applied` mark and the route stay.
- **commits:**
  - `8ad6d0dd6e` fix(gateway): persist routed reasoning across restart and fallback
  - `789efd549f` fix(tests): router /model parity carries the routed reasoning record (2026-10-30 sync regression)
  - `8e814c37bd` fix(tests): reasoning-route-wire reads SessionState and takes the turn tier (2026-11-25 sync)
  - `84cb43fae1` fix(gateway): /reasoning reset drops the routed reasoning from the /model record
- **touches:** `agent/chat_completion_helpers.py`, `gateway/run_agent_cache.py`, `gateway/run_inbound_model_router.py`, `gateway/run_turn_prepare.py`, `gateway/run_turn_runner.py`, `gateway/session.py`, `gateway/slash_commands_model.py`, `tests/agent/test_fallback_reasoning_override.py`, `tests/gateway/test_model_router.py`, `tests/gateway/test_reasoning_primary_runtime_sync.py`, `tests/gateway/test_session_model_override_persistence.py`
- **2026-08-26-sync:** replayed reasoning persistence onto the corrected final prefix and combined it with active-route consumption and auto-reset route-state preservation. Refreshed the exact Gemini-classifier dependency edge after the ancestry repair, and removed the stale redundant post-redelivery `resume_pending` clear absorbed by this final assembled topic.
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/reasoning-route-wire.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/reasoning-route-wire.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/reasoning-route-wire.md` for relocations/seams.


### 83. todo-continuity-parity
- **branch:** `soju/patches/todo-continuity-parity`
- **stacked-on:** `soju/patches/reasoning-route-wire` (2026-11-25 chronological tip stack)
- **origin:** `local-author`
- **upstream_pr:** none — upstreamable (generic harness gap, no fork-only deps); file after live-session soak
- **state:** `local-only`
- **rationale:** Same-model (fable-5) sessions lose their todo plan mid-session while Claude Code drives its plan to completion. Root cause measured in CC v2.1.226: (1) a `todo_reminder` attachment re-surfaces the todo list every N turns (`TURNS_SINCE_WRITE: 10, TURNS_BETWEEN_REMINDERS: 10`) — Hermes re-injected todos only at compression boundaries, so plans silently aged out of attention (measured: 64% of recent gateway sessions never tracked todos; 14% of todo sessions ended with unfinished pending/in_progress items); (2) CC's TodoWrite result carries a continuation nudge ("Please proceed with the current tasks if applicable") — Hermes returned bare JSON; (3) CC's system prompt carries end-to-end approval-scope + last-paragraph completion guidance — TASK_COMPLETION_GUIDANCE had neither. Fix: periodic reminder through the cache-safe tool-tail channel (same as /steer + run-budget wrap-up; counters on the agent, zeroed by the todo handler in both dispatch paths), CC's verbatim continuation message in a new `message` field of write results (reads stay message-free; hydration json.loads contract preserved), CC TodoWrite description adapted onto the todo schema (+ optional `active_form`/`activeForm` item label), and CC's scope/completion paragraphs appended to TASK_COMPLETION_GUIDANCE verbatim.
- **commits:**
  - `7f0a0fd577` feat(agent): Claude Code todo-continuity parity — periodic reminder, continuation nudge, end-to-end scope guidance
  - `8c029d2a08` fix(agent): todo reminder only acts on integer loop counters
  - `7a3bfe5352` fix(todo): lead the todo_list description with its capability so the catalog line fits (2026-11-25 sync)
  - `bcddffc2a6` refactor(agent): import the todo reminder and its helpers at module top (2026-11-25 sync review)
- **touches:** `agent/agent_init.py`, `agent/inline_tool_executors.py`, `agent/prompt_builder.py`, `agent/tool_executor.py`, `agent/turn_iteration_prep.py`, `agent/turn_todo_reminder.py`, `tools/todo_tool.py`, `tests/agent/test_todo_reminder.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/todo-continuity-parity.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/todo-continuity-parity.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/todo-continuity-parity.md` for relocations/seams.


### 85. aux-anthropic-mode-max-tokens
- **branch:** `soju/patches/aux-anthropic-mode-max-tokens`
- **stacked-on:** _(none — independent topic rooted on base v0.23.4; re-rooted 2026-11-26 from the tip stack, its diff applies on bare base)_
- **origin:** `local-author`
- **upstream_pr:** none — upstreamable (generic gate mismatch, no fork-only deps). File after live soak; see rationale for the related open upstream issue and the four rejected blanket-forward PRs that bound how narrow the upstream PR must stay
- **state:** `local-only`
- **rationale:** Related upstream context: open issue 60388 (`max_tokens` settings silently dropped); four blanket "forward caps to all providers" PRs (60454/60513/60620/60645) were closed unmerged, so any upstream PR must honor the declared `api_mode` as one more anthropic-compat signal and nothing broader. Defect: `_build_call_kwargs()` forwarded an explicit auxiliary `max_tokens` only when the endpoint was detected as anthropic-compatible by provider name (`minimax*`) or a `/anthropic` URL path. A config-declared provider with `api_mode: anthropic_messages` and a plain-host `base_url` (our `claude-lb` at `http://10.8.0.3:2455`) matched neither, so the cap was dropped and `AnthropicAuxiliaryClient.create()` → `build_anthropic_kwargs()` substituted the model's default output ceiling (`_ANTHROPIC_DEFAULT_OUTPUT_LIMIT = 128_000`). Transport selection (`_maybe_wrap_anthropic`) already honored `api_mode == "anthropic_messages"`; cap forwarding used a narrower gate. Measured live 2026-09-04 via claude-lb `debug_artifacts`: compressor requested `max_tokens=1400` per digest, proxy captured `max_tokens: 128000` on the wire; 77/77 compression calls exceeded the cap (avg 6,844 out, max 22,863) and latency was linear in output tokens (13.17 ms/tok, R²=0.961) — ~80% of compaction wall time. Fix: `_build_call_kwargs(..., api_mode=None)` + `or api_mode == "anthropic_messages"` in the cap gate; all 8 call sites (sync/async primary, same-provider retry, fallback, refreshed fallback) thread the resolved/destination api_mode. `max_tokens=None` behavior unchanged; `_ANTHROPIC_COMPAT_PROVIDERS`/`_is_anthropic_compat_endpoint`/adapter default fallback untouched.
- **commits:**
  - `fe7e4d0857` fix(auxiliary): forward explicit max_tokens when api_mode is anthropic_messages
  - `c2e6ad4898` fix(fallback-check): thread the route api_mode into the live probe kwargs (2026-11-25 sync review)
- **touches:** `agent/auxiliary_client.py`, `hermes_cli/fallback_check.py`, `tests/agent/test_auxiliary_anthropic_mode_max_tokens.py`, `tests/hermes_cli/test_fallback_check.py`
- **v2026.9.11-sync:** DONE — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/aux-anthropic-mode-max-tokens.md` for relocations/seams.
- **v0.22.5-sync:** DONE — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/aux-anthropic-mode-max-tokens.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/aux-anthropic-mode-max-tokens.md` for relocations/seams.


### 86. session-store-lock-offload
- **branch:** `soju/patches/session-store-lock-offload`
- **stacked-on:** `soju/patches/todo-continuity-parity` (2026-11-25 chronological tip stack; #85 re-rooted on base 2026-11-26)
- **origin:** `local-author`
- **upstream_pr:** none — upstreamable in principle (upstream `gateway/session_persistence.py` still runs the UPSERT under `_lock` when `lock_held=True`; probed 2026-09-14 on upstream main: a 1000 ms persistence write blocked `get_model_override` for 855 ms), and since #27 durable-turns was retired (2026-11-26) the fork version no longer touches fork-only code, so it could be re-rooted on base and filed upstream.
- **state:** `local-only`
- **rationale:** Same defect class as the 2026-07-28/29 gateway outages (event loop stalled behind SQLite held under a store lock). `SessionStore._save_entry(lock_held=True)` and `_save()` ran the SQLite UPSERT / full `sessions.json` rewrite + fsync while the caller still held the store-wide non-reentrant `_lock`, so every reader on the event loop waited for the write. Fix: capture the serialized entry + revision under `_lock`, persist under `_save_lock` only (`_capture_entry_save_locked` / `_persist_entry_save_locked` / `_persist_routing_data_save_locked`); `mark_turn_active` takes `_save_lock` outermost, release `_lock` around the durable write and repair the row if the live entry was structurally replaced meanwhile; metadata/override/suspend writers use `_save_entry` after releasing the lock; `lock_held=True` on `_save_entry` is now a hard error; `_save(lock_held=True)` survives only for the startup stale-prune path (releases/re-acquires around I/O). Measured on the assembled stack: reader blocked 857 ms → 1 ms during a 1000 ms write. Provenance: written in the live production tree by another session on 2026-08-31 (preserved as `~/work/hermes-cc-parity/live-tree-wip-session-store-20260831.patch`), re-applied after the 2026-09-04 rebuild, running in both gateways since 2026-09-04 10:14 UTC; promoted from uncommitted WIP to this topic on 2026-09-14. `tests/gateway` on the topic tree: only pre-existing `test_health_detailed_returns_ok` (`degraded`, environment) fails; it fails identically on the WIP-free tree.
- **adjusted (2026-11-26, O-A1):** with #27 retired, the commit no longer rewrites #27's `begin_active_turn`/`mark_active_turn_interrupted`/`finish_active_turn`, no longer adds the `_update_entry_durable` helper only they used, and no longer extends `tests/gateway/test_durable_turn_records.py`.
- **commits:**
  - `7f9609c873` fix(gateway): never hold SessionStore._lock across routing persistence I/O
- **touches:** `gateway/session.py`, `gateway/session_lifecycle.py`, `gateway/session_persistence.py`, `gateway/session_prompt_pin.py`, `gateway/session_transcript.py`, `tests/gateway/test_active_turn_recovery.py`, `tests/gateway/test_delete_secondary_profile_multiplex.py`, `tests/gateway/test_routing_entry_origin_heal.py`, `tests/gateway/test_runtime_override_store_precedence.py`, `tests/gateway/test_secondary_route_fence_scope.py`, `tests/gateway/test_session_db_recovery.py`
- **v2026.9.11-sync:** DONE-WITH-NOTES — rebuilt 2026-09-14 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20260914/reports/session-store-lock-offload.md` for relocations/seams.
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/session-store-lock-offload.md` for relocations/seams.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/session-store-lock-offload.md` for relocations/seams.


### 90. slack-stale-reconnect-task
- **branch:** `soju/patches/slack-stale-reconnect-task`
- **stacked-on:** _(none — independent topic rooted on base v0.23.4; re-rooted 2026-11-26 from the tip stack, it touches only Slack plugin files)_
- **origin:** `local-author`
- **upstream_pr:** none
- **state:** `local-only`
- **rationale:** Slack's roughly 5-hourly `disconnect` frame makes slack_sdk 3.43 reconnect inside an untracked `asyncio.ensure_future(run_message_listeners(...))`. When the adapter watchdog restarted the handler in the middle of that refresh (2026-10-17 21:23), `_stop_socket_mode_handler` cancelled only `_socket_mode_task` and three client attributes, so the orphan survived and then looped on `Connector is closed.` / `Session is closed` every 10 s against the closed aiohttp session. Fix: `_track_socket_reconnects(client)` wraps the client's `connect_to_new_endpoint` and records entering tasks in a WeakSet; teardown marks the client superseded and cancels and awaits those tasks before closing the session. If the SDK renames the method the wrap silently no-ops (old behaviour). Deployed to live 2026-10-19 by cherry-pick (cdca4a874a, from d6e8992133). Details: `~/work/vooy/claw-migration-2026-10-05/observe/2026-10-19-hermes-followups.md` section 1.
- **commits:**
  - `8ffd024670` fix(slack): cancel the untracked disconnect-refresh reconnect on handler teardown
- **touches:** `plugins/platforms/slack/adapter.py`, `plugins/platforms/slack/adapter_socket_reconnect.py`, `tests/gateway/test_slack_socket_reconnect_heal.py`
- **v0.22.5-sync:** DONE-WITH-NOTES — rebuilt 2026-10-30 by cherry-pick onto the new base; see `~/work/hermes/upstream-sync-20261030/reports/slack-stale-reconnect-task.md` for relocations/seams.
- **v0.23.4-sync:** DONE — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/slack-stale-reconnect-task.md` for relocations/seams.


### 94. summary-empty-finish-reason
- **branch:** `soju/patches/summary-empty-finish-reason`
- **stacked-on:** _(none — independent topic rooted on base v0.23.4; re-rooted 2026-11-26 from the tip stack, its diff applies on bare base, also together with #51 and #85)_
- **origin:** `local-author`
- **upstream_pr:** none
- **state:** `local-only`
- **rationale:** 72 of 127 compression summary attempts between 2026-11-10 and 2026-11-14 failed with "Context compression LLM returned empty content", all on claude-fable-5-2, and nothing in the log told a structured refusal (`stop_reason=refusal`) from a reasoning-only turn. When the summary response is judged empty, `ContextCompressor._call_summary_llm` now logs one content-free WARNING (`finish_reason`, `stop_reason`, `output_tokens`, block-type counts, content/reasoning lengths, tool-call count, resolved aux provider/model) before raising the unchanged RuntimeError; the Anthropic aux shim (`_AnthropicCompletionsAdapter.create`) attaches a content-free `native_shape` (raw `stop_reason`, block-type counts) to the response it returns. The failure text, classifier and user warning are untouched. Deployed to the live v0.21.2 tree 2026-11-16 as 48ab645561 (fork-policy source commit 8b6c867785, branch `soju/hotfix-live/summary-empty-finish-reason`). Fork-only diagnostics, no upstream PR.
- **commits:**
  - `bccac62a7a` fix(agent): log why a compression summary came back empty
- **touches:** `agent/auxiliary_client.py`, `agent/context_compressor.py`, `tests/agent/test_summary_empty_content_diagnostics.py`
- **v0.22.5-sync:** DONE — added 2026-11-25 by `cherry-pick -x 8b6c867785` onto `soju/patches/sync-20261030-test-seams` (9676072162); applied clean, both hunks still land in the owning modules at v0.22.5 (`_call_summary_llm` in `agent/context_compressor.py`, `_AnthropicCompletionsAdapter` in `agent/auxiliary_client.py`); no prompt/tool-schema change, so #93's generated fixtures stay valid. Record: `~/work/hermes/update-20261125/PREP-REPORT.md` § 3.
- **v0.23.4-sync:** DONE-WITH-NOTES — rebuilt 2026-11-25 by cherry-pick onto v0.23.4; see `~/work/hermes/upstream-sync-20261125/reports/summary-empty-finish-reason.md` for relocations/seams.


### 95. sync-20261125-test-seams
- **branch:** `soju/patches/sync-20261125-test-seams`
- **stacked-on:** `soju/patches/review-fork-checkpoint-warning` (2026-11-25 chronological tip stack; tip of the whole production stack)
- **origin:** `local-author`
- **upstream_pr:** _(none — generated fixtures and test seams for fork gates)_
- **state:** `local-only`
- **rationale:** ADR-004 rule 3: generated test fixtures that fork tool-schema, prompt or classifier changes invalidate are re-rendered once, at the tip of the stack, in a dated tip topic. This topic does that for the v0.23.4 stack and supersedes #93 `sync-20261030-test-seams`. It re-renders the prompt-surface snapshot with `scripts/ci/prompt_surface.py render` (tools and guidance of #1, #2, #38, #44, #83), regenerates the Discord conformance vectors for #4's box tables (other platforms and the oracle stamp stay as committed), renders the #34 classifier-core pin from `gateway/model_router.py` (carrying the S0/S0b refusal fields of #56 and #58), and baselines the three fork DEFAULT_CONFIG keys that upstream's docs-drift ratchet flags and no topic documents (`agent.gateway_turn_resume`, `agent.turn_resume_max` from #27, `delegation.default_route` from #34). No runtime code. Re-render it (and amend) whenever a topic below changes the prompt surface, the Discord renderer or the classifier core.
- **2026-11-26 (cleanup step 7):** the prompt-surface fixtures are re-rendered once at the tip (notes_write after the #38 fix and the #53 deferral drop); vectors, classifier pin and docs-drift baseline needed no change.
- **2026-11-26 (cleanup):** `7b34c597c5` drops the two #27 durable-turns keys (`agent.gateway_turn_resume`, `agent.turn_resume_max`) from the docs-drift baseline; `delegation.default_route` (#34) stays.
- **commits:**
  - `c173d75207` test: re-render generated fixtures and re-seam upstream tests to fork gates (2026-11-25 sync)
  - `6880477e8a` test(ci): drop the durable-turns keys from the docs-drift baseline (#27 retired)
  - `872e536111` test: re-render the prompt-surface fixtures at the tip after the 2026-11-26 cleanup
- **touches:** `scripts/ci/docs_drift_baseline.json`, `tests/conformance/vectors/discord.json`, `tests/fixtures/prompt_surface/surfaces.json`, `tests/fixtures/prompt_surface/prompts/*.txt`, `tests/fixtures/prompt_surface/tools/*.json`, `tests/fixtures/skill_gate_classifier_core.json`
- **v0.23.4-sync:** DONE-WITH-NOTES — created 2026-11-25; see `~/work/hermes/upstream-sync-20261125/reports/sync-20261125-test-seams.md` (renderer for the classifier pin: `~/work/hermes/upstream-sync-20261125/tools/render_classifier_core.py`).
- **2026-11-26:** re-stacked onto #97 by `cherry-pick` of a9e3fe8fba (same patch-id, applied clean; backup `backup/topics-1126/soju/patches/sync-20261125-test-seams`). #96 and #97 change no prompt surface, Discord renderer, classifier core or config key, so no fixture was re-rendered (`~/work/hermes/hotfix-topics-20261126/REPORT.md`).


### 96. external-install-service-launcher
- **branch:** `soju/patches/external-install-service-launcher`
- **stacked-on:** _(none — independent topic rooted on base v0.23.4; touches no fork code)_
- **origin:** `local-author`
- **upstream_pr:** none
- **state:** `local-only`
- **rationale:** A tree stamped `install-stamp.json` `updateMechanism: external` (this host: the fork tree runs its own `venv/` built by `bin/hermes-venv-rebuild`) beside a PM store with no committed generation must never run PM's store Python. Since v0.22.5 every gateway boot (`refresh_systemd_unit_if_needed`) regenerated both user units to exec `<tree>/.hermes/bin/hermes` (a store launcher that exits 1, "no dependency environment is committed") and published that launcher; live was protected only by the `zz-venv-runtime.conf` drop-ins. Since v0.23.4 the boot step `install_default_plugins` (`default_plugins.spawn_install` → `runtime_command`) also started a detached store-Python child that exited 1 on every gateway/CLI start before it could record a backoff. Fix: one predicate `_launchers.external_runtime` plus `service_python` (the running interpreter for an external tree, else the store Python) and `service_venv`; `runtime_command` runs `service_python`, `installation_command` returns `<python> -m <module>` for an external tree beside a store (after upstream's macOS `sealed_service_launcher` and the Nix/developer `runtime_command` branch), `publishes_service_launcher` is False for it, and `generate_systemd_unit` puts the venv's `bin` first on PATH and writes `VIRTUAL_ENV`. Self-managed, Nix and macOS app trees are unchanged. Ported from hotfix `soju/hotfix-live/external-service-unit` (96eadabd76, v0.22.5 base; investigation `~/work/hermes/postcut-20261125/REPORT.md` § P2). After the first boot on this topic both live units are rewritten to the venv form; the drop-ins are then redundant and harmless.
- **commits:**
  - `e79156cc34` fix(launchers): an external tree's services and detached children run its own venv, never PM's store Python
- **touches:** `hermes_cli/_launchers.py`, `hermes_cli/gateway.py`, `tests/hermes_cli/test_external_service_unit.py`, `tests/hermes_cli/test_source_launcher_publication.py`
- **v0.23.4-sync:** DONE — created 2026-11-26 on v0.23.4 (`~/work/hermes/hotfix-topics-20261126/REPORT.md`).


### 97. review-fork-checkpoint-warning
- **branch:** `soju/patches/review-fork-checkpoint-warning`
- **stacked-on:** `soju/patches/session-store-lock-offload` (2026-11-25 chronological tip stack; #90 and #94 re-rooted on base 2026-11-26)
- **origin:** `local-author`
- **upstream_pr:** none
- **state:** `local-only`
- **rationale:** Since v0.22.5 every background-review fork logged "compression.checkpoint_required is enabled but the active memory provider (no active provider) does not implement checkpoint API v2" on construction (24 lines on 2026-11-25). Noise: the compress-time gate already exempts a persistence-detached fork with no memory manager and no session DB, but the fork was detached only after the constructor had warned. One predicate `conversation_compression_checkpoint.owns_no_durable_memory` now decides both the gate and the startup warning; `AIAgent(persist_disabled=...)` detaches a fork before init's startup checks, and the review fork and the ingest curator pass it. Every agent that keeps persistence still warns and still fails closed. Ported from hotfix `soju/hotfix-live/review-fork-checkpoint-warning` (b8cc962a22; `~/work/hermes/postcut-20261125/REPORT.md` § P3).
- **commits:**
  - `70b6fdc1bf` fix(agent): detached review/curator forks are persistence-detached at construction, so the checkpoint warning skips them
- **touches:** `agent/agent_init.py`, `agent/background_review.py`, `agent/conversation_compression.py`, `agent/conversation_compression_checkpoint.py`, `agent/ingest_curator.py`, `run_agent.py`, `tests/agent/test_pre_compress_checkpoint_contract.py`
- **v0.23.4-sync:** DONE — created 2026-11-26 on the v0.23.4 tip stack (`~/work/hermes/hotfix-topics-20261126/REPORT.md`).


## Retired entries (numbers are never reused)

Old tips: `/mnt/garbage/backups-claw-20261126/hermes-fork-refs.bundle` (branch `soju/patches/<name>`, or tag `archive/...` / `backup/sync-*/patches/<name>` as named). Full former sections: `git show backup/sync-1125/fork-policy:PATCHES.md`.

| # | name | retired | how | evidence |
|---|---|---|---|---|
| 5 | delegate-per-task-model | 2026-07-16 | superseded | #34 Phase 3a route-enum delegation `ffee9ea3c` (tip in the bundle) |
| 7 | runtime-state-session-split | 2026-11-26 | folded | into #1 runtime-control, own commits carried by cherry-pick; old tip `backup/cleanup-1126/soju/patches/runtime-state-session-split` |
| 8 | runtime-route-awareness | 2026-11-26 | folded | into #1 runtime-control, own commits carried by cherry-pick; old tip `backup/cleanup-1126/soju/patches/runtime-route-awareness` |
| 9 | lsp-idle-reaper | 2026-08-04 | merged upstream | PR #74058 (tip in the bundle) |
| 10 | aux-runtime-context | 2026-07-22 | merged upstream | upstream `fdc6c32d7`/`73057ed16`/`c201b72f3` ContextVar `_RUNTIME_MAIN_CONTEXT` with per-turn Token scoping, a superset of the `threading.local` fix (tip in the bundle) |
| 11 | gateway-max-iterations-config-authority | 2026-08-04 | merged upstream | v2026.8.3 reloads and re-bridges `agent.max_turns` per turn (tip in the bundle) |
| 12 | strict-chat-reasoning-details | 2026-11-26 | dropped (inert) | test-only since upstream 7bcafdbf03 "fix(agent): don't give custom providers local-engine reasoning carriers" (ancestor of base); the two glm-vooy guard tests are an optional upstream offer; old tip in the bundle and `backup/cleanup-1126/soju/patches/strict-chat-reasoning-details` |
| 13 | discord-home-autothread-fix | 2026-11-26 | retired (owner O-A13) | only its legacy flat `DISCORD_HOME_CHANNEL`/`TELEGRAM_HOME_CHANNEL` YAML bridge carried weight; both profiles move to upstream's `platforms.<p>.home_channel` (snippet and proof in `~/work/hermes/stack-cleanup-20261126/apply/REPORT.md`). Deploy a tree without #13 only after that config migration is live; old tip in the bundle and `backup/cleanup-1126/soju/patches/discord-home-autothread-fix` |
| 14 | runtime-override-rehydrate-credentials | 2026-11-26 | folded | into #1 runtime-control, own commits carried by cherry-pick; old tip `backup/cleanup-1126/soju/patches/runtime-override-rehydrate-credentials` |
| 16 | tool-delay-removal | 2026-08-04 | merged upstream | `ce9f6712f` via PR #64172 (tip in the bundle) |
| 17 | prompt-cache-stability | 2026-07-22 | merged upstream | upstreamed by the fork author as `7b3dcee92` (api_content sidecar), follow-ups `39efad89a`/`c7035ef25` (tip in the bundle) |
| 18 | prompt-tail-freeze | 2026-11-26 | folded | into #1 runtime-control, own commits carried by cherry-pick; old tip `backup/cleanup-1126/soju/patches/prompt-tail-freeze` |
| 19 | request-client-reuse | 2026-08-04 | merged upstream | `82e2c9ce4` via PR #73375 (tip in the bundle) |
| 20 | async-token-accounting | 2026-08-04 | merged upstream | PR #73359 (tip in the bundle) |
| 21 | gateway-persist-trim | 2026-08-04 | merged upstream | salvaged as `54eafee30` via PR #76916 (tip in the bundle) |
| 22 | gateway-worker-pool | 2026-10-30 | merged upstream | `24758cf4b8` + `fb992c84bc` (unbounded turn-body executor); re-adding a finite cap would weaken upstream's fix (tip in the bundle) |
| 24 | background-first-waits | 2026-10-30 | merged upstream | `33a25058b2` (background-first process waits, `terminal.process_wait_auto_notify`) (tip in the bundle) |
| 26 | config-knob-bridges | 2026-11-26 | dropped (inert) | the three bridged `agent.*` keys are set in no live config and the env vars still work; the recorded `process_wait_cap` bridge was already removed by 477ca39878; old tip in the bundle and `backup/cleanup-1126/soju/patches/config-knob-bridges` |
| 27 | durable-turns | 2026-11-26 | retired (owner O-A1) | upstream's canonical-admission restart policy owns restart recovery (its `holds_admission` guard runs first); same-turn resume fired only at the one-off 2026-11-25 cutover. #86's dependent hunks, the assembly-fix bullets, #95's two docs-drift keys and `upstream-pr/durable-turns` (local and origin) went with it; old tip in the bundle and `backup/cleanup-1126/soju/patches/durable-turns` |
| 28 | hook-prepend-command-safety | 2026-11-26 | folded | into #1 runtime-control, own commits carried by cherry-pick; old tip `backup/cleanup-1126/soju/patches/hook-prepend-command-safety` |
| 30 | session-db-read-path-split | 2026-08-04 | merged upstream | v2026.8.3 provides `_read_ctx()` and per-thread `mode=ro` connections (tip in the bundle) |
| 31 | fts5-cjk-bigram-index | 2026-08-04 | merged upstream | upstream ships `cjk_unicode61`, `native/fts5_cjk/` and `messages_fts_cjk` (tip in the bundle; the only fork CJK index implementation) |
| 32 | search-slow-query-log | 2026-08-04 | merged upstream | equivalent routing-path slow-query logging landed upstream as `8364576e33` (tip in the bundle) |
| 33 | fts-v2-config-authority | 2026-08-04 | superseded | fork `messages_fts_v2` never shipped upstream; v2026.8.3 owns `messages_fts_cjk` config; leftover v2 tables removed by #64 on 2026-08-13 (tip in the bundle) |
| 36 | model-switch-provider-dedupe | 2026-10-30 | merged upstream | `a02ca4a153` + `01d2e6c3d1` + `f7631c0ee7` (/model collapses compat views by identity); our alias logic fails 4 upstream tests (tip in the bundle) |
| 38 | memory-phase1 | 2026-11-26 | folded | into #37 memory-redesign, own commits carried by cherry-pick; old tip `backup/cleanup-1126/soju/patches/memory-phase1` |
| 39 | memory-phase2-taint | 2026-11-26 | folded | into #37 memory-redesign, own commits carried by cherry-pick; old tip `backup/cleanup-1126/soju/patches/memory-phase2-taint` |
| 40 | memory-phase2-curator | 2026-11-26 | folded | into #37 memory-redesign, own commits carried by cherry-pick; old tip `backup/cleanup-1126/soju/patches/memory-phase2-curator` |
| 41 | cron-secret-scope-env-fallback | 2026-08-04 | merged upstream | PR #69057 (tip in the bundle) |
| 43 | daemon-pool-py314-compat | 2026-10-30 | merged upstream | `c9fa191334` + `b48cc47a4c` (Python 3.14 daemon worker contexts); the cherry-pick came out empty (tip in the bundle) |
| 46 | route-repromote-hysteresis | 2026-11-26 | folded | into #34 model-routing, own commits carried by cherry-pick; old tip `backup/cleanup-1126/soju/patches/route-repromote-hysteresis` |
| 47 | passive-provider-health | 2026-11-26 | folded | into #34 model-routing, own commits carried by cherry-pick; old tip `backup/cleanup-1126/soju/patches/passive-provider-health` |
| 49 | token-accounting-schema-repair | 2026-08-04 | merged upstream | never rebased, registered as a tombstone; v2026.8.3 shape-gates `_heal_session_model_usage_pk()` and rebuilds malformed `session_model_usage` (tip in the bundle) |
| 53 | notes-recognition-grounding | 2026-11-26 | folded | into #37 memory-redesign, own commits carried by cherry-pick; old tip `backup/cleanup-1126/soju/patches/notes-recognition-grounding` |
| 55 | lifecycle-guard-nul-fallback | 2026-11-26 | dropped (inert) | test-only since 2026-09-14; upstream's `test_binary_from_remote_callback_never_false_positives` pins the same behaviour; old tip in the bundle and `backup/cleanup-1126/soju/patches/lifecycle-guard-nul-fallback` |
| 57 | refusal-api-fallback | 2026-11-26 | folded | into #56 refusal-recovery, own commits carried by cherry-pick; old tip `backup/cleanup-1126/soju/patches/refusal-api-fallback` |
| 58 | refusal-hop-clean-fork | 2026-11-26 | folded | into #56 refusal-recovery, own commits carried by cherry-pick; old tip `backup/cleanup-1126/soju/patches/refusal-hop-clean-fork` |
| 59 | refusal-soft-turnend | 2026-11-26 | folded | into #56 refusal-recovery, own commits carried by cherry-pick (the cancelling pair e7b06f608b/f0622e9ba8 dropped, net diff empty); old tip `backup/cleanup-1126/soju/patches/refusal-hop-clean-fork` |
| 60 | anthropic-structured-output | 2026-08-19 | merged upstream | same fix landed upstream as `f709bd8445`, in base 5dd15872a6 (2026-08-19 bump); kept as an intentional empty topic until 2026-10-30 (tip in the bundle) |
| 64 | fts-v2-orphan-hardening | 2026-11-26 | retired (owner O-A3) | no `messages_fts_v2` object in either live DB. Restore note: a state.db from before 2026-09-14 needs the v2 retirement from bundle branch `soju/patches/fts-v2-orphan-hardening` (`/mnt/garbage/backups-claw-20261126/hermes-fork-refs.bundle`) before v0.23.4 opens it. Fork PR Soju06/hermes-agent#2 and its origin head are left to the owner; old tip in the bundle and `backup/cleanup-1126/soju/patches/fts-v2-orphan-hardening` |
| 65 | read-file-binary-sniff | 2026-11-26 | dropped (inert) | upstream e40315d53a classifies binaries at the byte layer for the native read and the base64 probe; the fork hunk only served shells without `base64`, never used on this host (`terminal.backend: local`); old tip in the bundle and `backup/cleanup-1126/soju/patches/read-file-binary-sniff` |
| 68 | lifecycle-guard-stmt-boundary | 2026-11-26 | folded | into #66 lifecycle-guard-hardening, own commits carried by cherry-pick; old tip `backup/cleanup-1126/soju/patches/lifecycle-guard-stmt-boundary` |
| 69 | approval-grep-escaped-quote | 2026-10-30 | merged upstream | `e383c28d2f` + `9efa50071a` (quote-state parsing in `tools/approval_detection.py`) (tip in the bundle) |
| 70 | elision-marker-imitation | 2026-10-30 | merged upstream | `77b9ea0831` (#121548, non-imitable compression marker) + `262a6436fa`/`a7baa5f5eb` (tip in the bundle) |
| 72 | kanban-board-env-precedence | 2026-10-30 | merged upstream | `d11edc0fa2` + `53f1b970a5` + `387c78c8f7` (explicit board beats env pin, with upstream's worker-isolation fence) (tip in the bundle) |
| 73 | kanban-goal-judge-unavailable | 2026-10-30 | merged upstream | `242244bf68` (#91264, goal loop stops on worker/judge failures) + `c15af4055e` tests (tip in the bundle) |
| 74 | hygiene-diag-and-lock-offload | 2026-11-26 | folded | into #61 gateway-hygiene-hardening, own commits carried by cherry-pick; old tip `backup/cleanup-1126/soju/patches/hygiene-diag-and-lock-offload` |
| 76 | clean-fork-soft-refusal-only | 2026-11-26 | folded | into #56 refusal-recovery, own commits carried by cherry-pick; old tip `backup/cleanup-1126/soju/patches/clean-fork-soft-refusal-only` |
| 77 | sync-20260819-test-seams | 2026-11-26 | folded | into #1 runtime-control, own commits carried by cherry-pick; old tip `backup/cleanup-1126/soju/patches/sync-20260819-test-seams` |
| 78 | base-persona | 2026-11-26 | folded | into #35 audience-personas, own commits carried by cherry-pick; old tip `backup/cleanup-1126/soju/patches/base-persona` |
| 81 | precompress-checkpoint-signal | 2026-09-14 | merged upstream | upstream PR #97063; `git cherry` against v2026.9.11 shows '-' for `721c2db628` (tip in the bundle) |
| 82 | conversation-started-date-stable | 2026-10-30 | merged upstream | any base containing `11b98a1429` (#97930, two-line conversation clock) supersedes this patch; do not file a PR (tip in the bundle) |
| 84 | lean-single-aux-call | 2026-09-14 | merged upstream | cherry-pick of upstream `4f22543509` (PR 98628), an ancestor of v2026.9.11; residue was fork-base-only (tip in the bundle) |
| 87 | sync-20260914-test-seams | 2026-10-30 | folded | duplicate: seam commit `80bcb3a05d` already carried by the #2 (`8db5914255`) and #48 (`b5279de582`) ports (tip in the bundle) |
| 88 | sync-20260914-fixups | 2026-10-30 | folded | all 8 fixups folded into owner topics #1, #8, #15, #34, #61, #83; the #12 and #62 fold-ins moot (tip in the bundle) |
| 89 | sync-20260914-kanban-seams | 2026-10-30 | moot | `455cb2715d` moot after #72's drop; `21729888fb` moot under upstream's per-file subprocess runner (tip in the bundle) |
| 91 | startup-integrity-check-offload | 2026-10-30 | merged upstream | `ac90478e08` (unclean-exit state.db integrity scan deferred off the startup path) + `2fe81e7879` + `8f0367c8ec` (tip in the bundle) |
| 92 | cron-fire-fence-heartbeat | 2026-10-30 | merged upstream | `87b013b21e` (no fire fence held during heartbeat save_jobs) + `9a60a7f316` + `eafed27cf0` (tip in the bundle) |
| 93 | sync-20261030-test-seams | 2026-11-25 | superseded | re-rendered by #95 sync-20261125-test-seams, which also carries the three docs-drift keys (tip in the bundle) |

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
# parallel lane (independent topics on base v0.23.4; octopus-merged first)
soju/patches/runtime-control
soju/patches/memory-write-reason-gate
soju/patches/todo-progress-display
soju/patches/discord-table-codeblocks
soju/patches/background-review-guardrails
soju/patches/turn-waterfall-tracing
soju/patches/llm-activity-recap
soju/patches/slow-tool-perf-advisor
soju/patches/anthropic-picker-suppression
soju/patches/skill-manage-reason-schema
soju/patches/refusal-fallback-reason (stacked on runtime-control)
soju/patches/event-loop-db-isolation
soju/patches/per-tool-disable
soju/patches/compaction-prompt-cc-upgrades
soju/patches/anthropic-signature-passthrough
soju/patches/worker-cpu-hygiene
soju/patches/gateway-hygiene-hardening
soju/patches/lifecycle-guard-hardening
soju/patches/scoped-exhaustion-upstream-classify
soju/patches/aux-anthropic-mode-max-tokens
soju/patches/slack-stale-reconnect-task
soju/patches/summary-empty-finish-reason
soju/patches/external-install-service-launcher
cleanup1126/assembly                  ← octopus of the parallel lane on base; root of the tip stack
# sequential lane (chronological tip stack on cleanup1126/assembly, in this order; merge LAST, each contains the previous)
soju/patches/conn-error-fail-fast
soju/patches/model-routing
soju/patches/audience-personas
soju/patches/memory-redesign
soju/patches/refusal-recovery
soju/patches/durable-bg-processes
soju/patches/route-aware-outage-fallback
soju/patches/delegated-child-marker-latch
soju/patches/turn-exit-reason-tagging
soju/patches/gemini-classifier-thinking-compat
soju/patches/reasoning-route-wire
soju/patches/todo-continuity-parity
soju/patches/session-store-lock-offload
soju/patches/review-fork-checkpoint-warning
soju/patches/sync-20261125-test-seams (tip of the stack)
```

## Operating Procedures

See `DECISIONS.md` ADR-001 for the full policy. Short forms:

```bash
# Add a new patch
bin/hermes-patches add <name> [--origin local-author|cherry-pick:SHA|upstream-pr:N]

# Rebuild production from base + patches. `rebuild` runs `git checkout` in its own checkout,
# so run it from a dedicated worktree (git worktree add --detach .worktree/<x> soju/fork-policy), never in REPO's main worktree.
bin/hermes-patches rebuild

# Base bump: plain `git rebase` (bin/hermes-patches sync) is NOT sufficient for tip-stack topics.
# Follow DECISIONS.md ADR-004 and its consequence notes; reuse ~/work/hermes/upstream-sync-20261125/tools/
# (build-venv.sh, run-suite-chunked.py, final_restack.py).

# Show patch states (open PR? merged? conflicts on rebase?)
bin/hermes-patches status

# Roll the live host back: follow the newest Sync History entry's rollback tags and the procedure in
# ~/work/hermes/upstream-sync-20261125/SYNC-REPORT.md § Rollback refs (external install stamp, venv swap).
```

## Assembly Integration Fixes (re-apply when reassembling production)

Sibling patches that only interact at production-assembly time. The rebuild's
sequential merge either conflicts here (resolution recorded in the merge
commit) or auto-merges WRONG — re-verify these after every rebuild:

- `agent/client_lifecycle.py::_create_request_openai_client` (was `run_agent.py`) — upstream's reusable-client body must remain INSIDE turn-waterfall-tracing's `llm.client_create` span.
- `gateway/session.py::from_dict` — runtime-control's three `runtime_*` kwargs must appear exactly once (positional-shuffle conflict, recorded).

- `agent/turn_context.py::on_turn_start/memory_prefetch` — memory-redesign's `memory_ingest_allowed` gate combines with turn-waterfall-tracing's span, and its `record_injected_text` block appends after the span-wrapped prefetch (conflicts, recorded).
- `agent/agent_runtime_helpers.py`/`toolsets.py` tool-name lists — union of runtime-control's model tools and memory-redesign's notes tools (conflicts, recorded).
- `gateway/run_turn_hygiene.py` hygiene compression (was `gateway/run.py`) — event-loop-db-isolation's off-loop construction/binding must preserve upstream's compression commit fence.

## Sync History

### 2026-11-26 (later) — stack cleanup: merged topics, re-roots, retirements and owner fixes (base unchanged)
- **Method:** plan `~/work/hermes/stack-cleanup-20261126/CLEANUP-PLAN.md`, record `apply/REPORT.md`. Every merge and re-root cherry-picks the members' own commits (never `git rebase`); every moved branch was tagged `backup/cleanup-1126/<name>` first. Gates: the restructure's production tree equals 08aca724ee byte for byte (`backup/cleanup-1126/step5-production`); the retirements touch only the retired topics' files (`backup/cleanup-1126/step6-production`).
- **Merged:** `runtime-control` (#1 + #7 #8 #14 #18 #28 #77), `model-routing` (#34 + #46 #47), `audience-personas` (#35 + #78), `memory-redesign` (#37 #38 #39 #40 #53), `refusal-recovery` (#56 #57 #58 #59 #76), `lifecycle-guard-hardening` (#54's guard half + #66 #68), `gateway-hygiene-hardening` (#61 #74). **Re-rooted on base:** #50, #67, #85, #90, #94. The tip stack now roots on `cleanup1126/assembly` (octopus of the independent topics).
- **Retired:** #12, #26, #55, #65 (inert); #27 (O-A1), #64 (O-A3), #13 (O-A13, deploy only after the `platforms.<p>.home_channel` config migration). Commits dropped inside topics: #37 0ac453015e (O-A12), #53 92d496ca34 (O-A8) and 51a897ef0e, #59's cancelling pair e7b06f608b/f0622e9ba8.
- **Fixed (owner topic commits):** `runtime-control` (`/reasoning reset` clears the durable runtime level, O-A6; `runtime_state` docs row), `model-routing` (user `/model` picks exempt from re-promotion, O-A5), `memory-redesign` (`notes_write` names no foreign tool), `refusal-recovery` (PERMISSIVE chain dedupe; dead recall-quarantine code removed), #71 (D6 prefix scrub dropped, O-A11), #80 (`/reasoning reset` drops the persisted route `reasoning_config`, O-A6), `gateway-hygiene-hardening` (`compression.hygiene_threshold` registered), #48 (storage docs). `/model --global` already cleared the durable route (4fff840016), so no commit there. #95 re-rendered the prompt surface once at the tip.
- **Entries:** 64 active → 38 (tip stack 32 → 15, parallel lane 32 → 23).
- **Rollback:** tags `backup/cleanup-1126/soju/production` (08aca724ee), `backup/cleanup-1126/soju/fork-policy` (1e5b84c7da), `backup/cleanup-1126/soju/patches/*` (all 63 pre-cleanup topic tips); removed refs in `/mnt/garbage/backups-claw-20261126/hermes-fork-refs.bundle`.

### 2026-11-26 — two post-cutover hotfixes carried as topics on the v0.23.4 stack (base unchanged)
- **New:** #96 external-install-service-launcher (independent, on base), #97 review-fork-checkpoint-warning (tip stack, on #94). #95 re-stacked onto #97 by cherry-pick (same patch-id, no fixture re-render). Production diff against 194763e0de = #96 + #97 only.
- **Not carried:** hotfix `soju/hotfix-live/watch-route-identity` (c593e93eb1): superseded by upstream 91171b7f0b "fix(gateway): admit automation wakes on route identity, not display fields", an ancestor of base 8fc0374447 (`git merge-base --is-ancestor`); the hotfix was never a manifest entry.
- **Rollback:** tags `backup/topics-1126/soju/production` (194763e0de), `backup/topics-1126/soju/fork-policy` (c5d460f6e9), `backup/topics-1126/soju/patches/sync-20261125-test-seams` (a9e3fe8fba), `backup/topics-1126/soju/patches/summary-empty-finish-reason` (3fe74a66e1, unchanged). Record: `~/work/hermes/hotfix-topics-20261126/REPORT.md`.

### 2026-11-25 — base bump bf0203a794 → 8fc0374447 (v0.23.4, 18,455 commits, 14,328 files; old session store removed for the `persistence/` package, folder-facade layout rule)
- **Method:** ADR-004 with the 2026-11-25 consequence note: preflight (`am --3way`, 50/205 clean on bare TARGET) + relocation map; 12 parallel worktree groups → octopus `sync1125/assembly` (v1 f8cc6fd329; v2 adeca72a8c after the interim-suite regression fixes); the chronological tip stack ported in parallel lanes (validated at the OLD base) and re-stacked linearly once in manifest order (28 of 29 entries clean; one test-file conflict at #76). Every port adversarially reviewed by an independent read-only reviewer; blocker/major findings fixed in the owner topic (#4, #35, #50, #64, #66; plus minors), regression fixes from two full suites in the owner topics (#1, #8, #37, #38, #48 test seams; #61 real fix).
- **Dropped:** #93 sync-20261030-test-seams (DROPPED — superseded by the new tip topic #95, which re-renders its artifacts). One commit dropped as merged upstream: #45 `f7ff6ad98a` (upstream 6ee6b7cb77 "fix(agent): name the cause of a refusal fallback and log every buffered fallback trigger").
- **New:** #95 sync-20261125-test-seams (generated fixtures re-rendered at the tip).
- **Environment:** Python 3.14 still required; `pm.build_env` unchanged; `_config_version` 51 → 54; state.db SCHEMA 32 → 33 (no FTS realign; upstream 0a0dab0686 retires the CJK FTS index); new boot step `install_default_plugins`. Full facts and deploy readiness: `~/work/hermes/upstream-sync-20261125/SYNC-REPORT.md`.
- **Rollback:** tags `backup/sync-1125/production` (9948b74486), `backup/sync-1125/live-production` (9948b74486), `backup/sync-1125/fork-policy` (0f451951f2), `backup/sync-1125/patches/*`.


### 2026-10-30 — base bump 939e45c91d → bf0203a794 (v0.22.5, 24,081 commits, 17,198 files; Sep-2026 facade + siblings decomposition, compat layer removed)
- **Method:** ADR-004 (own-commit cherry-picks; 11 parallel worktree groups → octopus `sync1030/assembly` 8aba582d03; chronological tip stack #23 … #90 + new tip topic #93 rebuilt in chunks and re-stacked linearly once at the end so every chain topic sits on the previous topic's fixed tip). Every port was adversarially reviewed by an independent read-only reviewer (parallel lane, S1, S2); blocker/major findings were fixed in the owner topic (`notes/fix/`, each report's "Review fixes" section) — 2026-10-30 review-fix commits are now part of the topics' own commits.
- **Dropped — merged upstream:** #22 gateway-worker-pool (24758cf4b8/fb992c84bc), #24 background-first-waits (33a25058b2), #36 model-switch-provider-dedupe (a02ca4a153/01d2e6c3d1/f7631c0ee7), #43 daemon-pool-py314-compat (c9fa191334/b48cc47a4c), #69 approval-grep-escaped-quote (e383c28d2f/9efa50071a), #70 elision-marker-imitation (77b9ea0831/262a6436fa), #72 kanban-board-env-precedence (d11edc0fa2/53f1b970a5/387c78c8f7), #73 kanban-goal-judge-unavailable (242244bf68/c15af4055e), #82 conversation-started-date-stable (11b98a1429), #91 startup-integrity-check-offload (ac90478e08/2fe81e7879/8f0367c8ec), #92 cron-fire-fence-heartbeat (87b013b21e/9a60a7f316/eafed27cf0).
- **Dropped — folded/moot:** #87 sync-20260914-test-seams (duplicate of the #2/#48 ports), #88 sync-20260914-fixups (all 8 folded into #1, #8, #15, #34, #61, #83; #12 and #62 fold-ins moot), #89 sync-20260914-kanban-seams (moot after #72's drop and upstream's per-file test runner).
- **New:** #93 sync-20261030-test-seams (generated fixtures re-rendered at the tip).
- **Environment:** TARGET requires Python 3.14 for every runtime dependency; `dev`/`test` are dependency-groups; `pyyaml` left core; `FTS_STORAGE_VERSION` 2 → 3 realigns `messages_fts` on first writable open; `_config_version` 42 → 51. Full facts: `~/work/hermes/upstream-sync-20261030/SYNC-REPORT.md`.
- **Rollback:** tags `backup/sync-1030/production` (547716fcc4), `backup/sync-1030/live-production` (b9de701076), `backup/sync-1030/fork-policy` (ac958cf362), `backup/sync-1030/patches/*`. (tag deleted 2026-11-26, in `hermes-fork-refs.bundle`)


### 2026-07-22 — base bump 4281151ae → 3ef6bbd20 (v2026.7.20, 1,358 commits)
Full stack rebuild (35 → 33 active patches), multi-agent triage + per-patch rebase with targeted tests.
- **Dropped (merged upstream):** prompt-cache-stability (`7b3dcee92`), aux-runtime-context (`fdc6c32d7`+`73057ed16`+`c201b72f3`).
- **Reduced:** prompt-tail-freeze → fork-only residue (~30%) on runtime-route-awareness (upstream `c0c76a471`).
- **Reworked for upstream landings:** turn-waterfall-tracing (segmented dispatch), request-client-reuse (request-local anthropic clients #67142 + single-writer fence #65991), durable-turns (delivery ledger `5854aad8b` + recovery-note refactor `9fc0074ba`), model-routing (conversation-scope funnel `19527db73`).
- **Retired branches deleted (tips tagged `archive/pre-v20260722/*`):** gateway-executor-capacity, process-wait-visibility (aliases of old prompt-tail-freeze tip), long-wait-heartbeat, slash-command-mixin-shadow, tool-delay-env-default, delegate-per-task-model. (tag deleted 2026-11-26, in `hermes-fork-refs.bundle`)
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
- **Dropped with archived tips:** tool-delay-removal (`ce9f6712f`, PR #64172), request-client-reuse (`82e2c9ce4`, PR #73375), async-token-accounting (PR #73359), gateway-persist-trim (`54eafee30`, PR #76916), lsp-idle-reaper (PR #74058), cron-secret-scope-env-fallback (PR #69057), session-db-read-path-split (upstream `_read_ctx()`/per-thread `mode=ro`), fts5-cjk-bigram-index (upstream `cjk_unicode61` + `messages_fts_cjk`), search-slow-query-log (upstream equivalent `8364576e33`), fts-v2-config-authority (superseded by upstream's CJK layout), gateway-max-iterations-config-authority (equivalent per-turn config bridge), and the formerly unmanifested token-accounting-schema-repair (upstream shape-gated `_heal_session_model_usage_pk()`). Every old tip is tagged under `archive/pre-v20260804/`. (tag deleted 2026-11-26, in `hermes-fork-refs.bundle`)
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
