"""ADR-011 v2.1 / Phase 4 Task 35b.2.b: outbound delivery tracking tests.

The broadcast call site relies on a generic outbound-delivery tracker in
``BasePlatformAdapter`` so post-delivery callbacks can read the
``surface_message_id`` of the last-delivered message without rebuilding it.
Both the non-streaming send path (``_process_message_background``) and the
streaming finalize path (``GatewayStreamConsumer._send_or_edit`` with
``finalize=True``, plus ``_send_fallback_final``) populate the same dict.

Tests cover the generic mechanism — no A2A-specific logic.  The A2A
broadcast registration in ``_handle_message_with_agent`` is a thin layer
that reads from this dict and fires ``_maybe_broadcast_a2a_reply``.  The
existing ``test_a2a_wiring.py`` already covers the broadcast helper itself
(35b.2.a); this file covers the tracker that feeds it (35b.2.b).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


# ===========================================================================
# Generic outbound-delivery tracker (base.py)
# ===========================================================================
class _StubAdapter:
    """Minimal stand-in that re-uses the base mixin behaviour."""

    def __init__(self) -> None:
        from gateway.platforms.base import BasePlatformAdapter

        # Re-bind the methods without instantiating the full ABC.
        self._last_outbound_per_chat = {}
        self._record_outbound_delivery = (
            BasePlatformAdapter._record_outbound_delivery.__get__(self)
        )
        self.get_last_outbound_delivery = (
            BasePlatformAdapter.get_last_outbound_delivery.__get__(self)
        )


def test_record_outbound_delivery_single_chunk():
    a = _StubAdapter()
    a._record_outbound_delivery("chat-1", "msg-42")

    entry = a.get_last_outbound_delivery("chat-1")
    assert entry is not None
    assert entry["message_id"] == "msg-42"
    assert entry["message_ids"] == ["msg-42"]
    assert "ts" in entry


def test_record_outbound_delivery_multi_chunk_uses_last():
    """Task 36 §3 chunking convention: the surface_message_id is the LAST chunk."""
    a = _StubAdapter()
    a._record_outbound_delivery(
        "chat-1",
        "msg-99",
        message_ids=["msg-97", "msg-98", "msg-99"],
    )

    entry = a.get_last_outbound_delivery("chat-1")
    assert entry["message_id"] == "msg-99"
    assert entry["message_ids"] == ["msg-97", "msg-98", "msg-99"]


def test_record_outbound_delivery_coerces_to_string():
    a = _StubAdapter()
    a._record_outbound_delivery(12345, 67890)

    entry = a.get_last_outbound_delivery("12345")
    assert entry["message_id"] == "67890"
    assert a.get_last_outbound_delivery(12345)["message_id"] == "67890"


def test_record_outbound_delivery_replaces_previous():
    a = _StubAdapter()
    a._record_outbound_delivery("chat-1", "msg-1")
    a._record_outbound_delivery("chat-1", "msg-2")

    assert a.get_last_outbound_delivery("chat-1")["message_id"] == "msg-2"


def test_record_outbound_delivery_empty_args_noop():
    a = _StubAdapter()
    a._record_outbound_delivery("", "msg-1")
    a._record_outbound_delivery("chat-1", "")
    a._record_outbound_delivery(None, "msg-1")
    a._record_outbound_delivery("chat-1", None)

    assert a.get_last_outbound_delivery("chat-1") is None


def test_get_last_outbound_delivery_unknown_chat_returns_none():
    a = _StubAdapter()
    a._record_outbound_delivery("chat-A", "msg-1")

    assert a.get_last_outbound_delivery("chat-B") is None
    assert a.get_last_outbound_delivery("") is None
    assert a.get_last_outbound_delivery(None) is None


# ===========================================================================
# stream_consumer finalize records outbound delivery
# ===========================================================================
@pytest.mark.asyncio
async def test_stream_consumer_records_on_finalize_edit():
    """When stream_consumer edits the message with finalize=True and it
    succeeds, the adapter's _record_outbound_delivery is called with the
    current self._message_id."""
    from gateway.stream_consumer import GatewayStreamConsumer

    adapter = MagicMock()
    adapter.MAX_MESSAGE_LENGTH = 4096
    adapter.REQUIRES_EDIT_FINALIZE = False
    # edit_message returns a success-like result.
    adapter.edit_message = AsyncMock(
        return_value=SimpleNamespace(success=True, message_id="msg-final")
    )
    adapter._record_outbound_delivery = MagicMock()

    consumer = GatewayStreamConsumer(adapter=adapter, chat_id="chat-7")
    # Simulate that the consumer already sent a first message.
    consumer._message_id = "msg-final"
    consumer._last_sent_text = "earlier text"

    ok = await consumer._send_or_edit("final text", finalize=True)

    assert ok is True
    adapter._record_outbound_delivery.assert_called_once_with(
        "chat-7", "msg-final"
    )


@pytest.mark.asyncio
async def test_stream_consumer_does_not_record_on_non_finalize_edit():
    """Mid-stream edits (finalize=False) must NOT poison the
    last-outbound slot — only the FINAL edit reflects what the user actually
    sees as the final reply."""
    from gateway.stream_consumer import GatewayStreamConsumer

    adapter = MagicMock()
    adapter.MAX_MESSAGE_LENGTH = 4096
    adapter.REQUIRES_EDIT_FINALIZE = False
    adapter.edit_message = AsyncMock(
        return_value=SimpleNamespace(success=True, message_id="msg-final")
    )
    adapter._record_outbound_delivery = MagicMock()

    consumer = GatewayStreamConsumer(adapter=adapter, chat_id="chat-7")
    consumer._message_id = "msg-final"
    consumer._last_sent_text = "earlier text"

    ok = await consumer._send_or_edit("intermediate text", finalize=False)

    assert ok is True
    adapter._record_outbound_delivery.assert_not_called()


@pytest.mark.asyncio
async def test_stream_consumer_records_on_fallback_final():
    """The _send_fallback_final path (taken when streaming edits fail and the
    consumer falls back to fresh sends) must also record the final
    message_id so broadcast sees the surface id."""
    from gateway.stream_consumer import GatewayStreamConsumer

    adapter = MagicMock()
    adapter.MAX_MESSAGE_LENGTH = 4096
    adapter.REQUIRES_EDIT_FINALIZE = False
    adapter.send = AsyncMock(
        return_value=SimpleNamespace(success=True, message_id="msg-fb-99")
    )
    adapter._record_outbound_delivery = MagicMock()

    consumer = GatewayStreamConsumer(adapter=adapter, chat_id="chat-7")

    # _send_fallback_final consumes _fallback_prefix / _last_sent_text to
    # compute continuation; with both empty + the message containing fresh
    # content, the full text gets sent as a single fallback chunk.
    consumer._last_sent_text = ""
    consumer._fallback_prefix = ""

    await consumer._send_fallback_final("Hello peers, this is the final.")

    adapter._record_outbound_delivery.assert_called_once_with(
        "chat-7", "msg-fb-99"
    )


@pytest.mark.asyncio
async def test_stream_consumer_finalize_record_failure_does_not_break_send():
    """Outbound tracking failures (e.g. adapter without the method) must
    never break the streaming send path. The send returns True even when
    _record_outbound_delivery is missing or raises."""
    from gateway.stream_consumer import GatewayStreamConsumer

    adapter = MagicMock()
    adapter.MAX_MESSAGE_LENGTH = 4096
    adapter.REQUIRES_EDIT_FINALIZE = False
    adapter.edit_message = AsyncMock(
        return_value=SimpleNamespace(success=True, message_id="msg-x")
    )
    # Simulate a partial adapter that doesn't ship the tracker (legacy /
    # third-party adapter).
    del adapter._record_outbound_delivery

    consumer = GatewayStreamConsumer(adapter=adapter, chat_id="chat-7")
    consumer._message_id = "msg-x"
    consumer._last_sent_text = "earlier"

    ok = await consumer._send_or_edit("final", finalize=True)
    assert ok is True


# ===========================================================================
# Discord adapter integration — raw_response["message_ids"][-1] convention
# ===========================================================================
def test_record_outbound_delivery_message_ids_filters_empty():
    """If the caller passes a list with empty/None entries, they are filtered
    out before storage — never persist falsy ids."""
    a = _StubAdapter()
    a._record_outbound_delivery(
        "chat-1",
        "msg-c",
        message_ids=["msg-a", "", None, "msg-c"],
    )
    entry = a.get_last_outbound_delivery("chat-1")
    assert entry["message_ids"] == ["msg-a", "msg-c"]
    assert entry["message_id"] == "msg-c"
