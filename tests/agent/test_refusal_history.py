from agent.refusal_history import (
    RefusalSource,
    current_user_ordinal_from_tail,
    should_apply_clean_fork,
    user_anchor_from_tail,
)


def test_current_turn_anchor_is_bounded_and_ignores_synthetic_users():
    messages = [
        {"role": "system", "content": "stable"},
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "current"},
        {"role": "assistant", "content": "partial"},
        {"role": "user", "content": "synthetic nudge"},
    ]
    ordinal = current_user_ordinal_from_tail(
        messages, 3, keep_user_turns=2,
    )
    assert ordinal == 2
    assert user_anchor_from_tail(
        messages, ordinal, keep_user_turns=2,
    ) == 3
    assert current_user_ordinal_from_tail(
        messages, 3, keep_user_turns=1,
    ) is None


def test_clean_fork_policy_allows_only_router_soft_refusal(caplog):
    with caplog.at_level("INFO", logger="agent.refusal_history"):
        decisions = {
            source: should_apply_clean_fork(source)
            for source in RefusalSource
        }

    assert decisions == {
        RefusalSource.ROUTER_SOFT_REFUSAL: True,
        RefusalSource.API_STRUCTURED_REFUSAL: False,
        RefusalSource.PROVIDER_OUTPUT_FILTER: False,
        RefusalSource.STREAM_FILTER_ERROR: False,
        RefusalSource.EXCEPTION_CONTENT_POLICY: False,
    }
    for source in RefusalSource:
        assert f"source={source.value}" in caplog.text


def test_router_clean_fork_config_can_disable_context_removal(caplog):
    with caplog.at_level("INFO", logger="agent.refusal_history"):
        assert should_apply_clean_fork(
            RefusalSource.ROUTER_SOFT_REFUSAL,
            configured=False,
        ) is False

    assert "source=router_soft_refusal configured=False apply=False" in caplog.text
