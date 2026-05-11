"""ADR-011 v2.1 §3 / Phase 4 Task 36: A2A wire payload structure (Q1 directive).

Tests that:
  - `_build_broadcast_payload(text, surface_channel_id, surface_message_id,
    surface_platform, context_id)` returns an A2A Message with the Q1 spec
    structure.
  - `parts[0]` is a text part carrying the final reply text.
  - `metadata` is a protobuf Struct containing all five `hermes.*` keys exactly:
    sender_bot_user_id, surface_channel_id, surface_message_id,
    surface_platform, context_id.
  - `role == ROLE_AGENT` — SDK reality. ADR-011 v2.1 §3 paper says
    `role="assistant"` (conceptual dict spec), but a2a-sdk 1.0.2 protobuf
    Role enum has no "assistant" value — only ROLE_USER/ROLE_AGENT. Peer-to-peer
    semantic (ADR-001) → ROLE_AGENT. ADR-011 v2.2 minor amend will record this.
  - `message_id` is a fresh UUID per call (two calls → two different ids).
  - `context_id` is mirrored into both `Message.context_id` and
    `metadata["hermes.context_id"]`.
  - Caller-provided `surface_message_id` is passed through verbatim — Phase 4
    convention is for the caller to feed the LAST chunk's surface message id
    when a reply was streamed in N chunks (plan §Task 36 §3). The helper
    itself doesn't enforce this; it just propagates whatever it's given.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.a2a import A2AAdapter


def _make_adapter(self_bot_user_id: str = "bot_self") -> A2AAdapter:
    config = PlatformConfig(
        enabled=True,
        token="",
        extra={
            "listen": "127.0.0.1:9999",
            "discord_bot_user_id": self_bot_user_id,
        },
    )
    return A2AAdapter(config)


def _struct_to_dict(struct) -> dict:
    """Decode google.protobuf.Struct → plain dict."""
    out: dict[str, Any] = {}
    for k, v in struct.fields.items():
        if v.HasField("string_value"):
            out[k] = v.string_value
        elif v.HasField("number_value"):
            out[k] = v.number_value
        elif v.HasField("bool_value"):
            out[k] = v.bool_value
        elif v.HasField("null_value"):
            out[k] = None
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# 1. parts[0] carries the reply text verbatim
# ---------------------------------------------------------------------------
def test_payload_parts_carry_text():
    adapter = _make_adapter()
    msg = adapter._build_broadcast_payload(
        text="hello peers",
        surface_channel_id="chan_1",
        surface_message_id="surf_msg_42",
        surface_platform="discord",
        context_id="ctx_a",
    )
    assert len(msg.parts) == 1
    p = msg.parts[0]
    assert p.HasField("text"), "broadcast payload part must be a text part"
    assert p.text == "hello peers"


# ---------------------------------------------------------------------------
# 2. metadata carries all 5 hermes.* keys exactly
# ---------------------------------------------------------------------------
def test_payload_metadata_has_all_hermes_keys():
    adapter = _make_adapter(self_bot_user_id="bot_self_xyz")
    msg = adapter._build_broadcast_payload(
        text="payload check",
        surface_channel_id="chan_55",
        surface_message_id="surf_msg_99",
        surface_platform="telegram",
        context_id="ctx_42",
    )
    meta = _struct_to_dict(msg.metadata)
    assert meta == {
        "hermes.sender_bot_user_id": "bot_self_xyz",
        "hermes.surface_channel_id": "chan_55",
        "hermes.surface_message_id": "surf_msg_99",
        "hermes.surface_platform": "telegram",
        "hermes.context_id": "ctx_42",
    }


# ---------------------------------------------------------------------------
# 3. role == ROLE_AGENT (SDK reality, peer-to-peer semantic)
# ---------------------------------------------------------------------------
def test_payload_role_is_agent():
    from a2a.types import Role

    adapter = _make_adapter()
    msg = adapter._build_broadcast_payload(
        text="hi",
        surface_channel_id="chan_1",
        surface_message_id="surf_msg_1",
        surface_platform="discord",
        context_id="ctx_1",
    )
    assert msg.role == Role.ROLE_AGENT


# ---------------------------------------------------------------------------
# 4. message_id is fresh per call (uuid4)
# ---------------------------------------------------------------------------
def test_payload_message_id_unique_per_call():
    adapter = _make_adapter()
    msg_a = adapter._build_broadcast_payload(
        text="a",
        surface_channel_id="chan_1",
        surface_message_id="s1",
        surface_platform="discord",
        context_id="ctx_1",
    )
    msg_b = adapter._build_broadcast_payload(
        text="b",
        surface_channel_id="chan_1",
        surface_message_id="s2",
        surface_platform="discord",
        context_id="ctx_1",
    )
    assert msg_a.message_id != msg_b.message_id
    assert len(msg_a.message_id) > 0


# ---------------------------------------------------------------------------
# 5. context_id mirrored into Message.context_id AND metadata
# ---------------------------------------------------------------------------
def test_payload_context_id_mirrored():
    adapter = _make_adapter()
    msg = adapter._build_broadcast_payload(
        text="ctx test",
        surface_channel_id="chan_1",
        surface_message_id="s1",
        surface_platform="discord",
        context_id="ctx_mirror_999",
    )
    assert msg.context_id == "ctx_mirror_999"
    meta = _struct_to_dict(msg.metadata)
    assert meta["hermes.context_id"] == "ctx_mirror_999"


# ---------------------------------------------------------------------------
# 6. surface_message_id is passed through verbatim (caller decides chunking)
# ---------------------------------------------------------------------------
def test_payload_passes_through_surface_message_id():
    """Plan §Task 36 §3: when a reply was streamed in N chunks, the caller
    feeds the LAST chunk's surface message id. The helper itself doesn't
    enforce or transform this — it just propagates what it's given. This test
    pins that contract so a future refactor doesn't accidentally start
    rewriting the id."""
    adapter = _make_adapter()
    msg = adapter._build_broadcast_payload(
        text="chunked reply tail",
        surface_channel_id="chan_1",
        surface_message_id="LAST_CHUNK_MSG_ID_777",
        surface_platform="discord",
        context_id="ctx_1",
    )
    meta = _struct_to_dict(msg.metadata)
    assert meta["hermes.surface_message_id"] == "LAST_CHUNK_MSG_ID_777"


# ---------------------------------------------------------------------------
# 7. _send_fire_and_forget uses _build_broadcast_payload (refactor verify)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_send_fire_and_forget_uses_build_payload_helper():
    """After Task 36 refactor, `_send_fire_and_forget` constructs its A2A
    message via `_build_broadcast_payload` rather than inlining the Message
    constructor. Verified by patching the helper and checking it was called
    with the expected kwargs."""
    from unittest.mock import AsyncMock, patch

    adapter = _make_adapter(self_bot_user_id="bot_self")

    # Build a real payload once to use as the patched return value
    real_payload = adapter._build_broadcast_payload(
        text="x",
        surface_channel_id="chan_1",
        surface_message_id="surf_1",
        surface_platform="discord",
        context_id="ctx_1",
    )

    fake_resolver = MagicMock()
    fake_resolver.get_agent_card = AsyncMock(return_value=MagicMock())

    async def _send_message_stub(req):
        if False:
            yield None
        return

    fake_client = MagicMock()
    fake_client.send_message = _send_message_stub
    fake_factory = MagicMock()
    fake_factory.create.return_value = fake_client

    metadata = {
        "hermes.sender_bot_user_id": "bot_self",
        "hermes.surface_channel_id": "chan_1",
        "hermes.surface_message_id": "surf_1",
        "hermes.surface_platform": "discord",
        "hermes.context_id": "ctx_1",
    }

    with (
        patch.object(
            adapter,
            "_build_broadcast_payload",
            wraps=adapter._build_broadcast_payload,
        ) as spy,
        patch("a2a.client.A2ACardResolver", return_value=fake_resolver),
        patch("a2a.client.ClientFactory", return_value=fake_factory),
    ):
        await adapter._send_fire_and_forget(
            peer_id="bot_a",
            peer_url="http://a.example/",
            content="hello",
            metadata=metadata,
        )

    assert spy.called, "_send_fire_and_forget must delegate to _build_broadcast_payload"
    kwargs = spy.call_args.kwargs
    assert kwargs.get("text") == "hello"
    assert kwargs.get("surface_channel_id") == "chan_1"
    assert kwargs.get("surface_message_id") == "surf_1"
    assert kwargs.get("surface_platform") == "discord"
    assert kwargs.get("context_id") == "ctx_1"
