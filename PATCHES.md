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
base_commit: 6122a79aab45041d8b7c8d775f95be3ac6ce579f
base_tag:   none (post-v2026.5.7 main)
pinned_at:  2026-05-14
```

Bump `base_commit` only via `bin/hermes-patches sync <new-ref>`. Each bump must rebase all `soju/patches/*` topics on top of the new base and verify the production stack rebuilds clean.

## Patches (apply order = list order)


### 1. redact-pii-optout
- **branch:** `soju/patches/redact-pii-optout`
- **origin:** `local-author` (Soju)
- **upstream_pr:** _(none — personal preference)_
- **state:** `local-only`
- **rationale:** Nachoneko/Mymel hosts disable PII redaction by default; gate behind `HERMES_REDACT_PII=1`. Required so memory/Graphiti ingest sees raw user text.
- **commit:** `5eb14655c feat(redact): gate PII redaction behind HERMES_REDACT_PII (default off)`
- **touches:** `agent/redact.py`, `tests/conftest.py`, `tests/gateway/test_signal.py`

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
soju/patches/redact-pii-optout       ← runtime topic, rebased on base_commit
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
