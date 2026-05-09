from __future__ import annotations

"""
A2A (Agent2Agent) Protocol platform adapter.

Lets two Hermes bot instances exchange normalized A2A messages —
single-message turns with no chunking, no streaming-edit artifacts,
no inline tool-call bubbles.

Discovery (Phase 1): static config peers map (discord_user_id -> agent_card_url).
Mirror: sender bot posts to both A2A peer + Discord channel (handled by gateway).
Dedup: Discord adapter drops messages whose author is a registered A2A peer.

References:
- ADR-001 (~/projects/hermes-a2a/DECISIONS.md)
- a2a-sdk 1.0.x (https://github.com/a2aproject/a2a-python)
"""

import asyncio
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import a2a  # noqa: F401  - pure import probe
    A2A_AVAILABLE = True
except ImportError:
    A2A_AVAILABLE = False

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,  # noqa: F401  - re-exported for symmetry with sibling adapters
    MessageType,  # noqa: F401  - re-exported for symmetry with sibling adapters
    SendResult,
)


def check_a2a_requirements() -> bool:
    """Check if A2A SDK is available."""
    return A2A_AVAILABLE


class A2AAdapter(BasePlatformAdapter):
    """A2A Protocol platform adapter (Phase 1 PoC — HTTP+JSON only)."""

    MAX_MESSAGE_LENGTH = 0  # 0 = no platform-imposed limit; A2A messages are not chunked

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.A2A)
        self._listen_addr: str = config.extra.get("listen", "127.0.0.1:8765")
        self._peers: Dict[str, str] = config.extra.get("peers", {})  # discord_id -> agent_card_url
        self._self_card: Optional[Dict[str, Any]] = config.extra.get("agent_card", None)
        self._server_task: Optional[asyncio.Task] = None
        self._app = None
        # Per-message-id reply-capture callbacks. Task 7 hoists this onto
        # BasePlatformAdapter; until then it lives here so Task 6 has a place
        # to put `_capture` futures keyed by inbound message_id.
        self._post_response_callbacks: Dict[str, Any] = {}

    async def connect(self) -> bool:
        """Start the A2A server and establish peer client cache (Phase 1 stub)."""
        if not A2A_AVAILABLE:
            self._set_fatal_error("a2a-missing", "a2a-sdk not installed", retryable=False)
            return False
        logger.info("[A2A] connect() — skeleton, server not yet implemented")
        self._mark_connected()
        return True

    async def disconnect(self) -> None:
        logger.info("[A2A] disconnect()")
        if self._server_task and not self._server_task.done():
            self._server_task.cancel()
        self._mark_disconnected()

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        logger.info("[A2A] send() — skeleton, not yet implemented (chat_id=%s)", chat_id)
        return SendResult(success=False, error="not implemented")

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        return None  # A2A has no typing indicator

    async def send_image(self, chat_id, image_url, caption=None) -> SendResult:
        return SendResult(success=False, error="A2A image send not yet implemented")

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": f"a2a:{chat_id}", "type": "a2a_peer", "chat_id": chat_id}

    # ------------------------------------------------------------------
    # Agent Card construction
    # ------------------------------------------------------------------
    def _build_agent_card(self) -> "AgentCard":
        """Construct this Hermes instance's A2A AgentCard.

        Sources (priority: config.extra.agent_card > config.extra.listen):
        - name / description / version: caller-provided or sensible defaults
        - URL: built from `listen` (host:port) unless `agent_card.url` is set
        - Discord identity: attached as a typed AgentExtension on capabilities
          so peers can resolve our Discord bot user_id for dedup (ADR-001 §5)

        a2a-sdk 1.0.2 specifics (vs older docs):
        - AgentCard has no top-level `url`; URL lives in
          `supported_interfaces=[AgentInterface(url=..., protocol_binding="JSONRPC")]`
        - All proto fields are snake_case (default_input_modes, etc.)
        - AgentExtension.params is a google.protobuf.Struct
        """
        if not A2A_AVAILABLE:
            raise RuntimeError("a2a-sdk not installed; cannot build AgentCard")

        # Local imports keep the module importable when a2a-sdk is missing.
        from a2a.types import (
            AgentCapabilities,
            AgentCard,
            AgentExtension,
            AgentInterface,
            AgentSkill,
        )

        cfg = (self.config.extra.get("agent_card") or {}) if self.config.extra else {}
        base_url = (cfg.get("url") or f"http://{self._listen_addr}/").rstrip("/") + "/"
        rpc_url = base_url  # Phase 1: JSON-RPC at the root path

        capabilities = AgentCapabilities(streaming=False)

        # Discord identity → AgentExtension on capabilities.
        # Peer adapters use this to resolve the sender's Discord bot user_id
        # for the strict-dedup path (Discord echo from registered peer → drop).
        discord_bot_id = cfg.get("discord_bot_user_id")
        if discord_bot_id:
            from google.protobuf import struct_pb2

            params = struct_pb2.Struct()
            params.update(
                {
                    "bot_user_id": str(discord_bot_id),
                    "guild_ids": [str(g) for g in (cfg.get("discord_guild_ids") or [])],
                }
            )
            capabilities.extensions.append(
                AgentExtension(
                    uri="https://hermes.nous/extensions/discord-identity/v1",
                    description="Discord bot identity for A2A↔Discord dedup",
                    required=False,
                    params=params,
                )
            )

        return AgentCard(
            name=cfg.get("name", "Hermes Agent"),
            description=cfg.get("description", "Hermes Agent A2A endpoint"),
            version=cfg.get("version", "0.1.0"),
            capabilities=capabilities,
            skills=[
                AgentSkill(
                    id="chat",
                    name="General conversation",
                    description="Multi-turn dialog backed by Hermes Agent",
                    tags=["chat", "hermes"],
                    input_modes=["text/plain"],
                    output_modes=["text/plain"],
                ),
            ],
            default_input_modes=["text/plain"],
            default_output_modes=["text/plain"],
            supported_interfaces=[
                AgentInterface(url=rpc_url, protocol_binding="JSONRPC"),
            ],
        )
