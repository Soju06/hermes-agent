# Hermes Fork — Decision Log

Append-only. Never edit a past ADR; supersede it with a new one.

---

## ADR-001 — Fork patch management policy (base + topic stack + manifest, 2026-05-14)

**Status:** Decided
**Type:** Repo structure + Operational
**Resolves:** Tribal-knowledge "how do we keep fork sane" gap that produced (a) production running on `feat/personal-mode-pii-optout`, (b) fork 550 commits behind upstream, (c) no documented rollback procedure.

### Context

The host runs three 24/7 Hermes gateways (`nachoneko`, `mymel`, plus a profile `default`) all importing `/home/ubuntu/.hermes/hermes-agent/` as an editable install. That tree was on `feat/personal-mode-pii-optout` (1 local commit, parent `faa13e49f`), while upstream `origin/main` had moved 550 commits ahead to `6122a79aab`. There was no manifest of carried patches, no rollback recipe, no upstream-sync recipe, and no way to tell whether a given patch was authored locally or cherry-picked from a pending upstream PR.

Trigger: Soju asked for per-task model routing on subagents. Existing in-flight upstream fix exists (PR #23769) but had nowhere to land cleanly. Fixing it ad-hoc would have left a second uncategorized patch on the live tree.

### Decision (5 핵심 결정)

| # | 결정 | 핵심 |
|---|---|---|
| D1 | **Base + topic-stack + manifest** model (vs. `feat/*`-as-production or quilt) | Single-commit upstream sync, per-patch state tracking, conflict isolation |
| D2 | **`soju/production` 브랜치 = base + octopus-merge of all `soju/patches/*` topics**; NEVER hand-edit | Rebuild from manifest is the canonical operation; production is deterministic |
| D3 | **`PATCHES.md` manifest is source of truth** for which patches exist + their origin + state | `bin/hermes-patches` reads/writes this; humans can review/approve at one place |
| D4 | **Commit-trailer convention** (`Origin:`, `Upstream-PR:`, `Patch-State:`) | `git log` is queryable; `status` command auto-syncs manifest with reality |
| D5 | **Weekly sync cadence** triggered by `git fetch upstream` + new release tag | Bounded conflict-recovery window; auto-drop merged PRs |

### Why this model over alternatives?

| Option | Verdict | 핵심 이유 |
|---|---|---|
| **Base + topic-stack + manifest** (chosen) | 🥇 | Standard pattern (kernel, Debian, dotfiles). Rebases trivially per-patch. `git patch-id` auto-detects upstream merges. Manifest gives human-readable inventory. |
| `feat/*`-as-production (status quo) | ❌ | Single branch holding N unrelated changes can't be partially reverted; upstream rebase = N-way conflict at once. **What we had been doing; what produced the mess.** |
| jj (Jujutsu) first-class patch stack | 🥈 | Conceptually cleaner (conflicts as 1st-class objects). Rejected because Soju + tooling all assume git. Future migration possible without breaking the manifest. |
| Quilt patch-series files | ❌ | Forces working with text patches instead of git commits; loses git's blame/log/cherry-pick. Overkill for a 2-patch stack. |
| Vendor everything by force-pushing main | ❌ | Burns all upstream provenance; impossible to ever sync again. |

### Branch convention

```
main                              fast-forward mirror of upstream/main. Touch ONLY via `git fetch && git merge --ff-only`.
soju/patches/<short-name>         single-purpose topic. Rebased on base_commit. Lives forever or until merged-upstream.
soju/production                   rebuilt by tooling. Never hand-edit.
<personal>/<experiment>           short-lived, never deployed. Delete when done.
```

### Manifest format (PATCHES.md)

YAML-in-markdown. Each patch entry MUST have: `branch`, `origin`, `upstream_pr` (or `none`), `state`, `rationale`, `commits` (SHAs), `touches` (files). `state ∈ {local-only, pending-upstream, merged-upstream, vendored}`.

### Commit trailer convention

Every commit on a `soju/patches/*` branch MUST include:
```
Origin: local-author | cherry-pick:<sha> | upstream-pr:<N>
Upstream-PR: <number or "none">
Patch-State: local-only | pending-upstream | vendored
```
Enforced by `bin/hermes-patches add` (auto-injects) and validated by `bin/hermes-patches status`.

### Sync workflow (weekly OR new tag)

```bash
bin/hermes-patches sync upstream/main
```
For each `soju/patches/*` topic:
1. `git patch-id` compare each patch commit against new base. If contained → mark `merged-upstream` and propose drop.
2. Otherwise rebase the topic onto new base. Conflicts reported per-patch, never as a single mass.
3. Rebuild `soju/production` and run `tests/tools/test_delegate.py` (+ targeted tests for touched files) as smoke gate.

### Rollback workflow

```bash
git -C ~/.hermes/hermes-agent checkout main
bin/hermes-venv-rebuild
hermes gateway --replace
```
1 minute. Live host returns to pure upstream `main` at `base_commit`. No patches active. `PATCHES.md` unchanged so we can re-apply later.

### Anti-goals

| Anti-goal | 이유 |
|---|---|
| Hand-edit `soju/production` | Breaks the rebuild guarantee. Untraceable; conflicts on next sync. |
| Skip the manifest entry | Patches without manifest entries are invisible to `sync` and `status`; will silently get lost on rebase. |
| Cherry-pick whole upstream PR diffs (with vendored deps) | Bloats stack; impossible to attribute. Always pull the PR's own commits via `git fetch upstream pull/N/head`. |
| Multiple unrelated changes in one topic branch | Can't be partially reverted or upstreamed. One topic = one concern. |
| Maintaining `feat/*` branches as long-lived production | Pre-ADR-001 status quo. The reason this ADR exists. |

### Pitfalls

1. **Editable install caches `.pth`**: `~/.hermes/hermes-agent/venv/lib/python*/site-packages/__editable__.hermes_agent-*.pth` points at the live tree. Switching git branches in the live tree affects all live gateways' next import. **Run `bin/hermes-venv-rebuild` after every checkout that changes Python files.**
2. **Live gateways won't pick up changes until restart**: `hermes_cli.main gateway run --replace` does an in-place restart and is the safe path. Never `kill -9` a gateway — checkpoints might be mid-flush.
3. **Octopus merge fails on real conflicts**: if two patches touch the same lines, `bin/hermes-patches rebuild` falls back to sequential merges and reports the offending pair. Resolve in the topic branches, not in production.
4. **`git patch-id` is content-based, not author-based**: an upstream-merged variant of our cherry-pick will be detected even if signature differs. Good. But intentional vendored divergence MUST be marked `state: vendored` so sync doesn't auto-drop it.
5. **Profile config drift**: `~/.hermes/profiles/<name>/config.yaml` may reference flags/keys added by a local patch. If the patch gets dropped, that profile crashes on next startup. Always grep profiles before marking a patch `merged-upstream` and dropping.
6. **Three running gateways share the same venv**: rebuilding venv mid-work will affect all three on next restart. Schedule restarts; never restart all three at once in case the new build has issues — restart `nachoneko` first as canary, watch logs 60s, then `mymel` + `default`.

### Verification

```bash
# manifest matches git
bin/hermes-patches status        # reports state of each patch vs upstream + base

# production rebuilds clean
bin/hermes-patches rebuild       # exits 0; soju/production fast-forwards or merges cleanly

# tests pass on production stack
cd ~/projects/hermes-agent-fork-policy && source .venv/bin/activate
python -m pytest tests/tools/test_delegate.py -q
# expect: 134 passed (current state as of 2026-05-14)

# live host can roll back to pure upstream
git -C ~/.hermes/hermes-agent checkout main && bin/hermes-venv-rebuild
hermes gateway --replace
# nachoneko gateway boots with zero patches active; verify in logs
```

### When this might break

- Upstream changes editable install layout (`pyproject.toml`-based dynamic version → static, or moves to a workspace setup) — `bin/hermes-venv-rebuild` would need updating.
- Upstream adopts uv-managed lockfile that conflicts with `uv pip install -e .[dev]` semantics — re-check the bootstrap path.
- More than ~10 simultaneous patches — octopus merge starts becoming unmanageable. At that point either upstream more aggressively, or switch to jj.
- A patch lands in upstream with a behavior tweak we don't want — `state: vendored` with a clear divergence note in the manifest.

### References

- Patch manifest: `PATCHES.md`
- Scripts: `bin/hermes-patches`, `bin/hermes-venv-rebuild`
- Skill (procedural): `~/.hermes/skills/hermes-fork-patch-management/SKILL.md` (added 2026-05-14)
- Related skill: `hermes-live-system-modification` (separation playbook BEFORE patches enter PATCHES.md)
- External: Linux kernel `Documentation/process/applying-patches.rst`, Debian "source format 3.0 (quilt)" docs
- Live host paths: `~/.hermes/hermes-agent/` (live tree, editable install root), `~/projects/hermes-agent-fork-policy/` (this work tree)

---

## ADR-002 — Durable turns: same-turn resume across gateway restarts (2026-07-15)

**Status:** Decided
**Type:** Architecture (gateway + agent loop)
**Resolves:** Restart recovery is a paper-over: an interrupted turn is abandoned and a NEW synthetic empty user turn is spawned with a system note that explicitly tells the model to "skip any unfinished work" and ask what to do next. The restart banner promises "I'll try to resume where you left off" — nothing implements that promise. Live incident 2026-07-15 (discord thread 1526457680527622247): two deploy restarts each reduced an in-flight PR-workflow turn to "응 오빠, 여기 있어".

### Context

The transcript layer is already durable and resume-ready upstream: incremental SQLite flush with an idempotent cursor (`_last_flushed_db_idx`), assistant tool_calls persisted BEFORE tool execution (intent journaling), per-tool progress flushes (`_flush_session_db_after_tool_progress`), `effect_disposition` stamping, and replay hygiene that fills dangling side-effecting calls with UNKNOWN orphan-recovery results instead of erasing them (`agent/replay_cleanup.py`). What is missing is purely the resume semantics:

1. No first-class notion of an in-flight turn. Recovery signals are session-level heuristics (`resume_pending` + `updated_at` freshness windows, 120s unclean-boot window).
2. No entry point that re-enters the conversation loop on an interrupted transcript tail. The only entry is `run_conversation(user_message=...)`, so recovery must fabricate a user turn.
3. The fabricated turn's guidance is anti-resume by design (stale-task-revival scars #4493/#16802), and its successful completion consumes `resume_pending`, so even the user's next real message gets no recovery context.

### Decision

1. **Turn record (SessionEntry.active_turn).** Each gateway chat turn writes a durable record at dispatch — `{turn_id, started_at, boot_id, resume_count, status}` — cleared on normal finalize, marked `interrupted` on drain-timeout. A record with a stale `boot_id` (SIGKILL/crash) or `interrupted` status is an orphaned turn. Per-session concurrency slot ⇒ at most one record per session; it lives on SessionEntry, no new table.
2. **Same-turn re-entry.** `run_conversation` gains a resume mode (no new user row appended; reuses recorded `turn_id`). Pre-entry tail normalization: strip synthetic interrupt-closer assistant rows ("Operation interrupted…"), fill unanswered tool_calls via existing `strip_dangling_tool_call_tail` orphan recovery, and if the tail is a genuine final assistant text (turn finished but delivery was cut), deliver it directly instead of re-calling the model. The next LLM call then continues the SAME turn — LLM statelessness makes this a true resume.
3. **Boot resume replaces the synthetic empty turn.** `_schedule_resume_pending_sessions` selects orphaned turn records (legacy `resume_pending`-only sessions fall back to the same-turn path with a fresh turn_id) and dispatches resume events through the existing startup-restore gate/queue. The anti-resume "report restored / skip unfinished work" wording is retired for resumed turns; it remains only for genuinely stale/abandoned tails.
4. **Guards.** `resume_count` cap (default 2, `HERMES_TURN_RESUME_MAX`, poison-turn protection; exceeded ⇒ status `abandoned` + honest notice to the thread) on top of existing freshness window, restart-loop guard, allowlist and suspension checks.
5. **Honest banner.** Restart notification says work resumes automatically after restart; no more "send any message" instruction.

Kill switch: `agent.gateway_turn_resume: false` (config) / `HERMES_GATEWAY_TURN_RESUME=0` restores legacy behavior.

### Anti-goals

- Resuming CLI/TUI turns (gateway-origin turns only; CLI has a human at the keyboard).
- Replaying tool side effects. Resume NEVER re-executes persisted tool calls; unanswered calls surface as UNKNOWN-effect results (existing replay_cleanup semantics).
- Preserving in-flight LLM generation. A response cut mid-stream is simply re-requested; prompt cache keeps the cost low.

### Patch

`soju/patches/durable-turns` (PATCHES.md entry TBD in same change).

---

## ADR-003 — Model routing as a first-class core subsystem; skill-gate relinquishes model switching (2026-07-16)

**Status:** Decided
**Type:** Architecture (gateway + agent tools + skill-gate plugin boundary)
**Resolves:** Model-switching ownership fragmentation. The *application* layer is already unified — `/model` (gateway/slash_commands.py:1402), the agent's `model_switch` tool (agent/runtime_control.py:459, patch #1), and skill-gate's `runtime_override` directive (gateway/run.py:16824) all resolve through `hermes_cli.model_switch.switch_model` and write the same `_session_model_overrides[session_key]` slot. But the *decision* layer is split across three actors with three different bodies of knowledge:

1. **skill-gate accidentally owns the routing brain.** The route catalog (`runtime_catalog.yaml`: per-route default/accepted/fallbacks), provider health probing (`select_healthy_spec`, fail-open semantics), the LLM classifier (dev_routing S1–S7 decision tree, benched at 95.0% holdout), CHAT-downgrade hysteresis, and the decision log all live in a plugin whose original job was skill/rule gating. Route labels are hardcoded (`RuntimeLabel` Literal, policy_router.py:56); adding a category means editing plugin code + rules.yaml + runtime_catalog.yaml.
2. **`delegate_task` gives the model unbounded freedom.** `model`/`provider` are free-form strings — no enum, no runtime validation of `model` (arbitrary strings flow raw into the child agent; delegate_tool.py:3042-3182). Upstream deliberately rejects this (`delegation-model-routing` sweeper policy); our patch #5 carries it locally. Meanwhile the config-driven alias system (`model_aliases:`, hermes_cli/model_switch.py:236-314) is never consulted on the delegation path.
3. **The agent's self-switch tool is blind.** `model_switch` is constrained to config-declared providers/models (patch #1) but knows nothing about purposes, route defaults, or health fallbacks — the agent picks raw model ids while skill-gate picks health-checked routes, last-writer-wins on the same session slot.

### Context

The end state we actually operate is "purpose → runtime" routing: dev sessions on claude-fable-5@claude-lb xhigh, chat/document/security on grok-4.5@glm-vooy, with health-probed fallback chains. That knowledge should be one declarative catalog consumed by every decider, and switching should be a core subsystem — skill-gate returns to being a rules engine.

### Decision

1. **`model_routes:` in config.yaml is the SoT.** Each route: `description` (purpose guidance shown to models and the classifier), `provider`, `model`, `reasoning_effort`, `accepted:` (membership for no-op suppression), `fallbacks:` (ordered health-fallback chain). Startup validation: every route's provider/model must exist under `providers:`. Static condition routes (e.g. non-owner sender → security route, codex-lb PR channel → dev route) are declared here too, replacing skill-gate's static runtime_override rules.
2. **Core route resolver.** One module resolves route → concrete runtime, walking `default → fallbacks` with provider health probing ported verbatim from skill-gate (fail-open semantics preserved: 401/403 = healthy, unhealthy = credit-exhaustion body sniff + 402/429/5xx/connection errors; cached probes; kill switch).
3. **Core dynamic router.** The LLM classifier moves into the gateway as a pre-dispatch stage (post-auth, pre-first-LLM-call): router model/timeout/downgrade-streak configured in config.yaml, label set + purpose descriptions generated from `model_routes`, asymmetric hysteresis and the decision log (JSONL) carried over. The benched prompt/model/JSON-context configuration is lifted as-is; behavior change target is zero.
4. **Agent surfaces become route enums.** `model_switch` (patch #1 evolution) and `delegate_task` (supersedes patch #5) drop free-form `model`/`provider` in favor of a `route` parameter whose enum + per-route descriptions are injected at schema-build time (delegate_tool.py already rebuilds schemas per `get_definitions()`). `delegation.model/provider` config becomes `delegation.default_route`. Session-only scope for `model_switch` is unchanged.
5. **skill-gate shrinks.** Removed from the plugin: `runtime_catalog.yaml`, policy_router dev_routing mode, the health prober, and all `runtime_override` rule outputs. It keeps prepends, notifications, advisory rules — the rules engine proper. The gateway's generic plugin `runtime_override` directive interface (patch #1) stays for now as a plugin capability; skill-gate simply stops using it.
6. **Rollout gate.** Core router first runs in shadow mode (log-only) against live skill-gate decisions; the dev-routing bench (230-case main + 120-case session-split holdout, `skill-gate/bench/`) MUST be re-run against the core implementation before the flip — lift-and-shift still changes context assembly. Kill switch restores skill-gate routing until the plugin-side removal lands.

### Anti-goals

- Changing `/model` — the human command keeps free-form model/provider selection as the owner escape hatch.
- Touching the request-error fallback system (`get_fallback_chain` / `agent._fallback_chain`) — it remains the post-hoc outage failover, distinct from switch-time route fallbacks.
- Re-litigating routing quality — classifier prompt/model changes are out of scope; this is an ownership/interface change gated on behavior parity.
- Upstreaming in round one. The enum'd delegation design aligns with upstream's `delegation-model-routing` policy and gets a config.yaml surface from day one, so it is a natural later candidate — but the fork ships first.

### Patch

`soju/patches/model-routing` (new topic: catalog, resolver, dynamic router, delegate_task enum — supersedes and drops `soju/patches/delegate-per-task-model`); `model_switch` route-enum lands as an evolution of `soju/patches/runtime-control`. skill-gate removal is a plugin-repo change (Soju06/skill-gate), sequenced after the bench gate passes.
