"""E2E gap fix — _parse_target_ref must recognize a2a as explicit free-form.

Found during E2E-A: AIAgent calling send_message(target='a2a:bot-b')
hit the channel_directory.resolve_channel_name fallback because
_parse_target_ref didn't have an a2a branch — bot-b was not numeric,
not a phone number, not a known platform pattern, so it returned
(None, None, False) and the caller errored out with "Could not
resolve 'bot-b' on a2a".

Fix: a2a branch returns (target_ref, None, True) so _handle_send
treats it as explicit and routes to A2AAdapter.send(chat_id=...),
where the actual peer-map / URL resolution lives.
"""

from tools.send_message_tool import _parse_target_ref


def test_a2a_target_ref_string_peer_id():
    """Sandbox-style: peer registered as 'bot-b' string."""
    chat_id, thread_id, is_explicit = _parse_target_ref("a2a", "bot-b")
    assert chat_id == "bot-b"
    assert thread_id is None
    assert is_explicit is True


def test_a2a_target_ref_numeric_discord_id():
    """Production-style: peer registered as their Discord user_id."""
    chat_id, thread_id, is_explicit = _parse_target_ref("a2a", "1234567890")
    assert chat_id == "1234567890"
    assert thread_id is None
    assert is_explicit is True


def test_a2a_target_ref_direct_url():
    """No peers entry — caller may pass a raw http URL; the adapter
    handles URL recognition itself, the parser just hands it back."""
    chat_id, thread_id, is_explicit = _parse_target_ref(
        "a2a", "http://10.0.0.1:8765/"
    )
    assert chat_id == "http://10.0.0.1:8765/"
    assert thread_id is None
    assert is_explicit is True


def test_a2a_target_ref_strips_whitespace():
    chat_id, thread_id, is_explicit = _parse_target_ref("a2a", "  bot-b  ")
    assert chat_id == "bot-b"
    assert thread_id is None
    assert is_explicit is True
