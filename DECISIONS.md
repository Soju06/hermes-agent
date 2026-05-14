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
