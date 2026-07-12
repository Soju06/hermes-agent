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
- **touches:** `agent/agent_init.py`, `agent/agent_runtime_helpers.py`, `agent/runtime_control.py`, `agent/tool_dispatch_helpers.py`, `agent/tool_executor.py`, `gateway/run.py`, `gateway/session.py`, `hermes_cli/plugins.py`, `hermes_cli/runtime_provider.py`, `model_tools.py`, `tests/gateway/test_pre_gateway_dispatch.py`, `tests/gateway/test_session.py`, `tests/gateway/test_session_model_override_routing.py`, `tests/hermes_cli/test_plugins.py`, `tests/hermes_cli/test_runtime_provider_resolution.py`, `tests/run_agent/test_pre_tool_session_id.py`, `tests/run_agent/test_run_agent.py`, `tests/run_agent/test_runtime_control.py`, `tests/test_model_tools.py`, `tests/tools/test_runtime_control_tool_schema.py`, `tools/runtime_control_tool.py`, `toolsets.py`

### 2. memory-write-reason-gate
- **branch:** `soju/patches/memory-write-reason-gate`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood memory hygiene)_
- **state:** `local-only`
- **rationale:** Memory `add`/`replace` tool calls require an explicit suitability reason explaining why USER/MEMORY is the right store rather than a skill, Graphiti, or session history. The reason is a guardrail only and is not persisted with the entry.
- **commit:** `deb240f9d feat(memory): require write reason for memory updates`
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


### 5. delegate-per-task-model
- **branch:** `soju/patches/delegate-per-task-model`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood subagent routing)_
- **state:** `local-only`
- **rationale:** `delegate_task` should support per-call and per-task model/provider overrides so the parent can route lightweight research or analysis children to a different configured provider (e.g. `grok-tokenmaxxing/grok-4.3`) without changing `delegation.model/provider` globally for every subagent.
- **commit:** `b52946a22 feat(delegate): restore per-call model provider override`
- **touches:** `gateway/run.py`, `run_agent.py`, `tools/delegate_tool.py`, `tests/gateway/test_run_progress_topics.py`, `tests/tools/test_delegate.py`

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
