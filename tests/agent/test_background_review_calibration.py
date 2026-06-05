"""Behaviour contracts for the calibrated background review prompts.

The review prompts steer the background review agent toward calibrated updates: ordinary turns
and weak signals produce "Nothing to save." rather than synthetic skill churn, and memory writes
pass a structured always-injected gate before the USER.md / MEMORY.md routing applies.

These tests assert behavioural *instructions* are present; they do NOT snapshot prompt text.
"""

from run_agent import AIAgent


def _assert_skill_calibration(prompt: str, label: str) -> None:
    lower = prompt.lower()
    assert "be selective" in lower or "concrete reusable" in lower, f"{label}: must ask for selectivity"
    assert "do not create work" in lower, f"{label}: must forbid manufacturing updates"
    assert "nothing to save" in lower, f"{label}: must keep 'Nothing to save.' as an outcome"
    assert "missed learning opportunity" not in lower, f"{label}: must not frame inaction as a miss"


def _assert_memory_contract(prompt: str, label: str) -> None:
    assert "USER.md and MEMORY.md are always-injected" in prompt, f"{label}: must name the scarce context"
    assert "memory(add|replace)" in prompt, f"{label}: must gate memory writes"
    for field in ("declared_scope", "declared_category", "why_always_injected", "why_not_skill",
                  "why_not_graphiti", "applies_to"):
        assert field in prompt, f"{label}: structured reason must require {field}"
    assert "Graphiti/session history" in prompt, f"{label}: must redirect scoped facts"
    # The gate sits in front of the two-store routing; it never replaces it.
    assert "target='user'" in prompt and "target='memory'" in prompt, f"{label}: must keep store routing"


def test_skill_review_prompt_is_calibrated_not_eager():
    _assert_skill_calibration(AIAgent._SKILL_REVIEW_PROMPT, "_SKILL_REVIEW_PROMPT")


def test_combined_review_prompt_skills_are_calibrated_not_eager():
    prompt = AIAgent._COMBINED_REVIEW_PROMPT
    assert "**Skills**" in prompt
    _assert_skill_calibration(prompt, "_COMBINED_REVIEW_PROMPT")


def test_memory_review_prompt_requires_structured_reason_contract():
    prompt = AIAgent._MEMORY_REVIEW_PROMPT
    assert "ordinary_chat" in prompt
    _assert_memory_contract(prompt, "_MEMORY_REVIEW_PROMPT")


def test_combined_review_prompt_memory_half_carries_contract():
    prompt = AIAgent._COMBINED_REVIEW_PROMPT
    assert "**Memory**" in prompt
    _assert_memory_contract(prompt, "_COMBINED_REVIEW_PROMPT")
