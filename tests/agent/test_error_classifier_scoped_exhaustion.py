"""claude-lb reports model-scoped weekly quota exhaustion (``model-quota-exhausted``) as HTTP 503/529.

That is not provider overload: classifying it as ``overloaded`` marked the whole provider passively
unhealthy and burned transport retries against a days-long condition. It is ``upstream_rate_limit``
(fallback now, the key is healthy, no credential rotation); a 429 keeps the plain rate-limit path.
"""
from agent.error_classifier import FailoverReason, classify_api_error


class MockAPIError(Exception):
    """Simulates an OpenAI SDK APIStatusError."""
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def test_503_scoped_model_exhaustion_is_upstream_rate_limit():
    message = (
        "Rate limit exceeded. Try again in 3d 1h "
        "(at 2026-08-16T11:00:00Z; earliest reset: account 34a40fb6...; "
        "0 rate-limited, 0 quota-exceeded, 3 model-quota-exhausted)"
    )
    e = MockAPIError(message, status_code=503)
    result = classify_api_error(
        e,
        provider="claude-lb",
        model="claude-fable-5",
    )
    assert result.reason == FailoverReason.upstream_rate_limit
    assert result.retryable is True
    assert result.should_rotate_credential is False
    assert result.should_fallback is True
    assert result.error_context["upstream_provider"] == "claude-fable-5"


def test_529_scoped_model_exhaustion_is_upstream_rate_limit():
    e = MockAPIError(
        "Rate limit exceeded: 1 model-quota-exhausted",
        status_code=529,
    )
    result = classify_api_error(e, model="claude-fable-5")
    assert result.reason == FailoverReason.upstream_rate_limit


def test_message_only_scoped_model_exhaustion_is_upstream_rate_limit():
    e = MockAPIError(
        "Rate limit exceeded: provider overloaded; 3 MODEL-QUOTA-EXHAUSTED"
    )
    result = classify_api_error(e, provider="claude-lb")
    assert result.reason == FailoverReason.upstream_rate_limit
    assert result.should_rotate_credential is False


def test_429_scoped_model_exhaustion_stays_rate_limit():
    e = MockAPIError(
        "Rate limit exceeded: 3 model-quota-exhausted",
        status_code=429,
    )
    result = classify_api_error(e, provider="claude-lb")
    assert result.reason == FailoverReason.rate_limit
    assert result.should_rotate_credential is True
