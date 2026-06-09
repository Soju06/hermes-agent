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
base_commit: 0bc616ecf9f16f48b7a3ec87497614b90e83254e
base_tag:   none (post-v2026.5.16 main)
pinned_at:  2026-06-01
```

Bump `base_commit` only via `bin/hermes-patches sync <new-ref>`. Each bump must rebase all `soju/patches/*` topics on top of the new base and verify the production stack rebuilds clean.

## Patches (apply order = list order)


### 1. runtime-control
- **branch:** `soju/patches/runtime-control`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood runtime control)_
- **state:** `local-only`
- **rationale:** Agent-callable `model_status` / `model_switch` tools. Session-only scope (turn scope removed — LLMs omitted scope ~29%, causing silent reversion). Model targets constrained to config-declared providers/models. Gateway session callback plus durable SessionEntry runtime fields so session-scoped model/reasoning overrides survive gateway restart without storing secrets. Trusted pre-dispatch plugin runtime overrides can select the session route after auth but before the first LLM call. Plugin `prepend` directives accumulate text before the original event message.
- **commit:** `b3e842047 feat(runtime): agent-callable model_switch / model_status (session-only)`
- **touches:** `agent/agent_init.py`, `agent/agent_runtime_helpers.py`, `agent/conversation_loop.py`, `agent/runtime_control.py`, `agent/tool_dispatch_helpers.py`, `agent/tool_executor.py`, `gateway/run.py`, `gateway/session.py`, `hermes_cli/plugins.py`, `model_tools.py`, `toolsets.py`, `tools/runtime_control_tool.py`, `tests/gateway/test_pre_gateway_dispatch.py`, `tests/gateway/test_session.py`, `tests/gateway/test_session_model_override_routing.py`, `tests/hermes_cli/test_plugins.py`, `tests/run_agent/test_pre_tool_session_id.py`, `tests/run_agent/test_run_agent.py`, `tests/run_agent/test_runtime_control.py`, `tests/test_model_tools.py`

### 2. memory-write-reason-gate
- **branch:** `soju/patches/memory-write-reason-gate`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood memory hygiene)_
- **state:** `local-only`
- **rationale:** Memory `add`/`replace` tool calls require an explicit suitability reason explaining why USER/MEMORY is the right store rather than a skill, Graphiti, or session history. The reason is a guardrail only and is not persisted with the entry.
- **commit:** `c818c4456 feat(memory): require write reason for memory updates`
- **touches:** `agent/agent_runtime_helpers.py`, `agent/tool_executor.py`, `tools/memory_tool.py`, `tests/tools/test_memory_tool.py`, `tests/tools/test_memory_tool_schema.py`

### 3. todo-progress-display
- **branch:** `soju/patches/todo-progress-display`
- **origin:** `local-author`
- **upstream_pr:** _(none — Discord/gateway dogfood UX)_
- **state:** `local-only`
- **rationale:** Gateway progress bubbles should show todo item statuses after the todo tool completes, not only a count like `planning N task(s)`. Also flush throttled progress edits when the final queued event has no following tool event.
- **commit:** `670274b3b feat(gateway): show todo progress details`
- **touches:** `agent/display.py`, `gateway/run.py`, `tests/agent/test_display.py`, `tests/gateway/test_run_progress_topics.py`

### 4. discord-table-codeblocks
- **branch:** `soju/patches/discord-table-codeblocks`
- **origin:** `local-author`
- **upstream_pr:** _(none — Discord/gateway dogfood UX)_
- **state:** `local-only`
- **rationale:** Discord does not render GitHub-flavored markdown pipe tables. Convert detected outbound pipe tables to fenced box-drawing ASCII tables so table responses remain readable, while preserving existing fenced code blocks, CJK wide-character alignment, and long-table chunk codeblock boundaries.
- **commit:** `10fdf4a43 feat(discord): render markdown tables as codeblocks`
- **touches:** `plugins/platforms/discord/adapter.py`, `tests/gateway/test_discord_send.py`


### 5. delegate-per-task-model
- **branch:** `soju/patches/delegate-per-task-model`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood subagent routing)_
- **state:** `local-only`
- **rationale:** `delegate_task` should support per-call and per-task model/provider overrides so the parent can route lightweight research or analysis children to a different configured provider (e.g. `grok-tokenmaxxing/grok-4.3`) without changing `delegation.model/provider` globally for every subagent.
- **commit:** `ccec568ae feat(delegate): restore per-call model provider override`
- **touches:** `gateway/run.py`, `run_agent.py`, `tools/delegate_tool.py`, `tests/gateway/test_run_progress_topics.py`, `tests/tools/test_delegate.py`

### 6. background-review-guardrails
- **branch:** `soju/patches/background-review-guardrails`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood self-improvement guardrail)_
- **state:** `local-only`
- **rationale:** Background self-improvement review must be a calibrated participant in the memory/skill governance model instead of aggressively writing after most sessions. Memory review prompts now carry the structured always-injected memory rationale contract, and skill review prompts default to "Nothing to save" unless a concrete reusable signal exists.
- **commit:** `4f4527126 fix: calibrate background self-improvement review`
- **touches:** `agent/background_review.py`, `tests/run_agent/test_review_prompt_class_first.py`

### 7. runtime-state-session-split
- **branch:** `soju/patches/runtime-state-session-split`
- **origin:** `local-author`
- **upstream_pr:** _(none — dogfood runtime gate correctness)_
- **state:** `local-only`
- **rationale:** Runtime hard gates must see the active session's actual model/provider after context compression rotates the session id. Compression split now republishes runtime state for the new session with the old session as task/parent scope, and tool hook prechecks consistently forward session_id/tool_call_id so plugins do not fall back to stale cross-session runtime state.
- **commit:** `39b5cfc42 fix: keep runtime state scoped across compression splits`
- **touches:** `agent/agent_runtime_helpers.py`, `agent/conversation_compression.py`, `agent/conversation_loop.py`, `agent/tool_executor.py`, `tests/agent/test_runtime_state_session_split.py`

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
soju/patches/runtime-control          ← runtime topic (squashed: core + config-sot-guard + session-only + SessionEntry persistence)
soju/patches/memory-write-reason-gate ← runtime topic, memory add/replace suitability reason guardrail
soju/patches/todo-progress-display    ← runtime topic, gateway todo progress status rendering
soju/patches/discord-table-codeblocks  ← runtime topic, Discord markdown tables as fenced ASCII codeblocks
soju/patches/delegate-per-task-model   ← runtime topic, per-call/per-task subagent model/provider routing
soju/patches/background-review-guardrails ← runtime topic, calibrated self-improvement review guardrails
soju/patches/runtime-state-session-split ← runtime topic, scoped runtime state across compression splits
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
