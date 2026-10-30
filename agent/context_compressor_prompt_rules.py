"""Rule text and helpers for the compaction summarizer prompt.

Epistemic stance: compression sheds qualifier words first ("probably", "I suspect", "unconfirmed"),
which either hardens an untested hypothesis into a fact the resumed agent acts on, or drops it and
loses the investigative thread. Asking for stance as an explicit labelled field rather than an inline aside
keeps it through compression (arXiv:2608.06953). Both strings are interpolated into the summarizer
prompt only (never the system prompt or the persisted handoff prefix), so the cached prefix is
untouched; f-string interpolation does not re-parse them, so braces in them are safe.

Chronological ``<analysis>`` pre-pass: a cheap/fast summarizer that fills the template in one shot
tends to fabricate or drop detail; an explicit chronological read-through first (Claude Code's
/compact design) improves faithfulness. The block is working material: ``_call_summary_llm`` strips it
right after extracting the reply, before the empty-content and refusal guards judge the body, so a
reply that is only the block (or the block followed by a refusal) takes the failure path, and the
block never reaches the stored summary or compounds through the iterative-update feedback loop.
"""

import re

UNVERIFIED_SECTION_HEADING = "## Unverified / Working Hypotheses"

EPISTEMIC_STANCE_RULE = (
    "Never restate an uncertain claim (suspicion, hypothesis, unverified inference) as fact: list "
    f'each under {UNVERIFIED_SECTION_HEADING} as "UNVERIFIED: <claim> — <basis>". '
)

UNVERIFIED_HYPOTHESES_SECTION = f"""

{UNVERIFIED_SECTION_HEADING}
[UNVERIFIED: <claim> — <basis so far>, one per line. If none, write "None."]"""

# Wording is kept plain for the same content-filter reason as the summarizer preamble.
ANALYSIS_PASS_RULE = (
    "First, inside <analysis></analysis> tags, work through the turns "
    "chronologically as preparation: note each user request and piece "
    "of feedback, the actions taken and their outcomes, exact file "
    "paths / errors / values worth preserving, and what was in flight "
    "at the end. This preparation block is discarded automatically — "
    "it is the one exception to the no-preamble rule. After the "
    "closing </analysis> tag, write the summary body only."
)


def strip_analysis_block(text: str) -> str:
    """Remove the ``<analysis>...</analysis>`` preparation block from a summary.

    Stored or replayed, the block would bloat every subsequent iterative-update prompt, exactly
    like ``<think>`` traces. An unclosed tag drops everything from ``<analysis>`` up to the first
    markdown heading after it (or to end-of-text when no heading follows).
    """
    stripped = re.sub(
        r"<analysis>.*?</analysis>", "", text, flags=re.DOTALL | re.IGNORECASE
    ).strip()
    match = re.search(r"<analysis>", stripped, flags=re.IGNORECASE)
    if match:
        rest = stripped[match.start():]
        heading = re.search(r"^#{1,3} ", rest, flags=re.MULTILINE)
        if heading:
            stripped = (stripped[: match.start()] + rest[heading.start():]).strip()
        else:
            stripped = stripped[: match.start()].strip()
    return stripped


def _today_for_prompt() -> str:
    """Date-only (user tz) for temporal anchoring; "" when the clock fails. Cache-safe: the summary is outside the prefix."""
    try:
        # Date-only granularity matches system_prompt.py:337 (PR #20451) and the user's configured timezone
        # via hermes_time.now(). The compaction summary is a mid-conversation message that is NOT part of
        # the cached prefix, so a date here never affects prompt-cache stability. Resolved defensively — a
        # clock failure must never block compaction.
        from hermes_time import now as _hermes_now
        return _hermes_now().strftime("%Y-%m-%d")
    except Exception:  # pragma: no cover - clock resolution is best-effort
        return ""
