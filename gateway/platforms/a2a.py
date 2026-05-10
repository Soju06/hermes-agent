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
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import a2a  # noqa: F401  - pure import probe
    A2A_AVAILABLE = True
except ImportError:
    A2A_AVAILABLE = False

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)


def check_a2a_requirements() -> bool:
    """Check if A2A SDK is available."""
    return A2A_AVAILABLE


class HermesA2AExecutor:
    """Bridges A2A inbound messages into Hermes MessageEvent dispatch.

    The executor implements the a2a-sdk `AgentExecutor` ABC. On each inbound
    request:
      1. Convert the A2A `Message` proto into a Hermes `MessageEvent`
      2. Register a per-message-id reply-capture callback on the adapter
      3. Dispatch through `BasePlatformAdapter.handle_message()`
      4. Wait (with timeout) for the agent to invoke the callback with the
         final reply text
      5. Emit the reply back to the A2A caller via `event_queue.enqueue_event`

    The capture-callback pattern is provisional. Task 7 hoists the registry
    onto BasePlatformAdapter and finds the gateway-side chokepoint that
    fires it. Until then, this adapter holds its own dict.
    """

    def __init__(self, adapter: "A2AAdapter") -> None:
        self._adapter = adapter

    async def execute(self, context, event_queue) -> None:  # type: ignore[no-untyped-def]
        # Local imports keep the adapter module importable without a2a-sdk.
        from a2a.types import Message as A2AMessage, Part as A2APart, Role

        incoming = context.message
        text_parts: list[str] = []
        if incoming and incoming.parts:
            for p in incoming.parts:
                if p.HasField("text") and p.text:
                    text_parts.append(p.text)
        text = "\n".join(text_parts)

        # Identify the peer. Phase 1: peer agent id is delivered either via
        # incoming.metadata["sender_agent_id"] (Hermes convention) or falls
        # back to "unknown" — the full peer-card lookup is out of scope here.
        peer_agent_id = "unknown"
        try:
            md = dict(incoming.metadata) if incoming and incoming.metadata else {}
            peer_agent_id = str(md.get("sender_agent_id") or peer_agent_id)
        except Exception:
            pass

        chat_id = (
            incoming.context_id
            if incoming and incoming.context_id
            else peer_agent_id
        ) or "a2a"

        message_id = (incoming.message_id if incoming else "") or f"a2a-in-{id(incoming)}"

        source = self._adapter.build_source(
            chat_id=chat_id,
            user_id=peer_agent_id,
            user_name=peer_agent_id,
            chat_type="a2a_peer",
            message_id=message_id,
        )
        event = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            message_id=message_id,
            raw_message=incoming,
        )

        # Register the capture callback BEFORE dispatching so the gateway can't
        # finalize a reply before we're listening.
        loop = asyncio.get_running_loop()
        reply_future: asyncio.Future = loop.create_future()

        async def _capture(reply_text: str) -> None:
            if not reply_future.done():
                reply_future.set_result(reply_text)

        self._adapter._post_response_callbacks[message_id] = _capture

        try:
            await self._adapter.handle_message(event)
            reply_text = await asyncio.wait_for(reply_future, timeout=120.0)
        except asyncio.TimeoutError:
            reply_text = "[A2A timeout: peer agent did not respond within 120s]"
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("[A2A] handle_message failed: %s", exc, exc_info=True)
            reply_text = f"[A2A error: {exc!s}]"
        finally:
            self._adapter._post_response_callbacks.pop(message_id, None)

        reply_msg = A2AMessage(
            role=Role.ROLE_AGENT,
            parts=[A2APart(text=reply_text)],
            message_id=f"reply-{message_id}",
            context_id=(incoming.context_id if incoming else "") or "",
        )
        await event_queue.enqueue_event(reply_msg)

    async def cancel(self, context, event_queue) -> None:  # type: ignore[no-untyped-def]
        return None


class A2AAdapter(BasePlatformAdapter):
    """A2A Protocol platform adapter (Phase 1 PoC — HTTP+JSON only)."""

    MAX_MESSAGE_LENGTH = 0  # 0 = no platform-imposed limit; A2A messages are not chunked

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.A2A)
        self._listen_addr: str = config.extra.get("listen", "127.0.0.1:8765")
        # Tier-1.5 well-known peer list (ADR-004). Two config forms accepted:
        #   peers: {"<discord_bot_user_id>": "<agent_card_url>", ...}  (dict)
        #   peers: ["<agent_card_url>", ...]  (list — auto-resolve at connect)
        # Mixing forms is rejected to avoid mode confusion.
        peers_raw = config.extra.get("peers", {})
        if isinstance(peers_raw, list):
            self._peers_to_resolve: List[str] = list(peers_raw)
            self._peers: Dict[str, str] = {}
        elif isinstance(peers_raw, dict):
            self._peers_to_resolve = []
            self._peers = dict(peers_raw)
        else:
            raise ValueError(
                f"a2a.peers must be list[url] or dict[id, url], got {type(peers_raw).__name__}"
            )
        # Peers that failed to resolve at connect() time — retried on first send().
        self._unresolved_peer_urls: set[str] = set()
        self._self_card: Optional[Dict[str, Any]] = config.extra.get("agent_card", None)
        self._server_task: Optional[asyncio.Task] = None
        self._app = None
        # Per-message-id reply-capture callbacks. Task 7 hoists this onto
        # BasePlatformAdapter; until then it lives here so Task 6 has a place
        # to put `_capture` futures keyed by inbound message_id.
        self._post_response_callbacks: Dict[str, Any] = {}

    async def connect(self) -> bool:
        """Start the A2A server.

        Spins up a Starlette+uvicorn server with two route groups:
        - GET  /.well-known/agent-card.json (agent card discovery)
        - POST / (JSON-RPC dispatcher for SendMessage / GetTask / etc.)

        Inbound A2A requests route through HermesA2AExecutor → handle_message().
        """
        if not A2A_AVAILABLE:
            self._set_fatal_error("a2a-missing", "a2a-sdk not installed", retryable=False)
            return False
        try:
            # Local imports keep the module importable without a2a-sdk.
            from a2a.server.request_handlers import DefaultRequestHandler
            from a2a.server.routes import (
                create_agent_card_routes,
                create_jsonrpc_routes,
            )
            from a2a.server.tasks import InMemoryTaskStore
            from starlette.applications import Starlette
            import uvicorn

            card = self._build_agent_card()
            executor = HermesA2AExecutor(self)
            handler = DefaultRequestHandler(
                agent_executor=executor,
                task_store=InMemoryTaskStore(),
                agent_card=card,
            )

            rpc_path = "/"
            routes = []
            routes += create_agent_card_routes(card)
            routes += create_jsonrpc_routes(handler, rpc_url=rpc_path)
            self._app = Starlette(routes=routes)

            host, port_s = self._listen_addr.rsplit(":", 1)
            port = int(port_s)
            uconfig = uvicorn.Config(
                self._app,
                host=host,
                port=port,
                log_level="warning",
            )
            self._server = uvicorn.Server(uconfig)
            self._server_task = asyncio.create_task(self._server.serve())

            # Wait for the server to actually start (up to ~2s).
            for _ in range(40):
                if getattr(self._server, "started", False):
                    break
                await asyncio.sleep(0.05)
            if not getattr(self._server, "started", False):
                logger.warning("[A2A] server did not report started within 2s")

            logger.info("[A2A] server listening on %s", self._listen_addr)
            self._mark_connected()
            # ADR-004: resolve list-form peers via AgentCard fetch.
            # Awaited inline so that connect() doesn't return success before peer
            # resolution gets a fair shot — but resolution failures are NEVER
            # treated as connect failures (the local server is up and ready
            # regardless of remote peer availability).
            if self._peers_to_resolve:
                try:
                    await self._resolve_well_known_peers()
                except Exception as e:
                    logger.warning(
                        "[A2A] well-known peer resolution raised; ignored: %s", e
                    )
            return True
        except Exception as e:
            logger.error("[A2A] connect failed: %s", e, exc_info=True)
            self._set_fatal_error("connect-failed", str(e), retryable=True)
            return False

    async def _resolve_well_known_peers(self) -> None:
        """Resolve list-form peers via AgentCard fetch (ADR-004).

        For each URL in ``self._peers_to_resolve``, GET
        ``<url>/.well-known/agent-card.json``, parse, find the
        ``discord-identity`` extension, and register the peer keyed by
        ``bot_user_id``.

        Chicken-egg defense: backoff schedule [1.0s, 3.0s, 7.0s] (~11s budget)
        per peer. Any peer that still fails is queued in
        ``self._unresolved_peer_urls`` for lazy retry on first ``send()``.

        Failure semantics:
          - Network/HTTP errors → retry with backoff
          - Card has no ``discord-identity`` extension → permanent skip
          - Card has extension but no ``bot_user_id`` → permanent skip
          - All retries exhausted → queue for lazy retry
        """
        import httpx

        backoffs = [1.0, 3.0, 7.0]
        for url in self._peers_to_resolve:
            base = url.rstrip("/")
            card_url = f"{base}/.well-known/agent-card.json"
            resolved = False
            for attempt, delay in enumerate(backoffs):
                try:
                    async with httpx.AsyncClient(timeout=5.0) as cli:
                        r = await cli.get(card_url)
                        r.raise_for_status()
                        card = r.json()
                    extensions = (card.get("capabilities", {}) or {}).get(
                        "extensions", []
                    ) or []
                    ext = next(
                        (
                            e
                            for e in extensions
                            if str(e.get("uri", "")).endswith("/discord-identity/v1")
                        ),
                        None,
                    )
                    if not ext:
                        logger.warning(
                            "[A2A] peer %s has no discord-identity extension; skipping",
                            url,
                        )
                        # Permanent skip — don't retry, don't queue for lazy retry.
                        resolved = True  # treat as "handled, move on"
                        break
                    bot_id = (ext.get("params") or {}).get("bot_user_id")
                    if not bot_id:
                        logger.warning(
                            "[A2A] peer %s discord-identity has no bot_user_id; skipping",
                            url,
                        )
                        resolved = True
                        break
                    self._peers[str(bot_id)] = url
                    self._unresolved_peer_urls.discard(url)
                    logger.info(
                        "[A2A] resolved peer %s → bot_user_id=%s (attempt %d)",
                        url,
                        bot_id,
                        attempt + 1,
                    )
                    resolved = True
                    break
                except Exception as e:
                    if attempt < len(backoffs) - 1:
                        logger.debug(
                            "[A2A] resolve %s failed (attempt %d/%d): %s; retrying in %.1fs",
                            url,
                            attempt + 1,
                            len(backoffs),
                            e,
                            delay,
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.warning(
                            "[A2A] failed to resolve peer %s after %d attempts: %s",
                            url,
                            len(backoffs),
                            e,
                        )
            if not resolved:
                # Queue for lazy retry — send() will try once more if it can't
                # find the peer in self._peers.
                self._unresolved_peer_urls.add(url)

    async def disconnect(self) -> None:
        logger.info("[A2A] disconnect()")
        # Tell uvicorn to exit gracefully; cancel the serve() task as a fallback
        server = getattr(self, "_server", None)
        if server is not None:
            server.should_exit = True
        if self._server_task and not self._server_task.done():
            try:
                await asyncio.wait_for(self._server_task, timeout=2.0)
            except asyncio.TimeoutError:
                self._server_task.cancel()
                try:
                    await self._server_task
                except (asyncio.CancelledError, Exception):
                    pass
        self._server = None
        self._server_task = None
        self._app = None
        self._mark_disconnected()

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send an A2A message to a peer and capture its terminal reply.

        ``chat_id`` is interpreted as either:
          - a discord_user_id (looked up in self._peers → agent_card_url), or
          - a direct agent_card_url (must start with http:// or https://).

        Returns a SendResult whose ``raw_response`` carries the peer's terminal
        reply text (per the ADR-001 §3 non-streaming PoC). Failures set
        ``retryable=True`` so the gateway's retry policy can decide whether to
        re-attempt — A2A failures are typically transient (network, peer
        restart), not permanent.
        """
        if not A2A_AVAILABLE:
            return SendResult(success=False, error="a2a-sdk not available")

        peer_url = self._peers.get(chat_id)
        if peer_url is None and self._unresolved_peer_urls:
            # ADR-004 lazy retry — peer may have come up after connect().
            logger.info(
                "[A2A] peer %s not in registry; retrying well-known resolve",
                chat_id,
            )
            try:
                await self._resolve_well_known_peers()
            except Exception as e:
                logger.warning("[A2A] lazy resolve raised; ignored: %s", e)
            peer_url = self._peers.get(chat_id)
        if peer_url is None:
            # Fall back to treating chat_id as a direct URL (legacy path).
            peer_url = chat_id
        if not (peer_url.startswith("http://") or peer_url.startswith("https://")):
            return SendResult(success=False, error=f"unknown peer: {chat_id}")

        try:
            # Local imports keep the module importable when a2a-sdk is missing.
            from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
            from a2a.types import (
                Message as A2AMessage,
                Part as A2APart,
                Role,
                SendMessageRequest,
            )
            import httpx
            import uuid

            message_id = str(uuid.uuid4())
            ctx_id = (metadata or {}).get("context_id") or chat_id

            async with httpx.AsyncClient(timeout=60.0) as http:
                resolver = A2ACardResolver(http, peer_url)
                peer_card = await resolver.get_agent_card()
                client = ClientFactory(
                    ClientConfig(httpx_client=http, streaming=False)
                ).create(peer_card)

                req = SendMessageRequest(
                    message=A2AMessage(
                        # Peer-to-peer: we ARE an agent talking to another agent.
                        # ROLE_USER would also work (HermesA2AExecutor doesn't
                        # branch on role), but ROLE_AGENT matches ADR-001's
                        # peer-to-peer semantic.
                        role=Role.ROLE_AGENT,
                        parts=[A2APart(text=content)],
                        message_id=message_id,
                        context_id=str(ctx_id),
                    )
                )

                reply_text: Optional[str] = None
                # send_message ALWAYS returns AsyncIterator[StreamResponse]
                # in a2a-sdk 1.0.2 — even with streaming=False. The streaming
                # flag only controls whether interim events are emitted; the
                # terminal {message,task} oneof always closes the iterator.
                async for resp in client.send_message(req):
                    # StreamResponse is a proto with oneof {task, message, ...}.
                    if resp.HasField("message"):
                        for p in resp.message.parts:
                            if p.HasField("text"):
                                reply_text = p.text
                                break
                        if reply_text is not None:
                            break
                    elif resp.HasField("task"):
                        # Scan the task history for the agent's terminal reply.
                        task = resp.task
                        for m in task.history:
                            if m.role == Role.ROLE_AGENT and m.parts:
                                for p in m.parts:
                                    if p.HasField("text"):
                                        reply_text = p.text
                                        break
                            if reply_text is not None:
                                break
                        if reply_text is not None:
                            break

            return SendResult(
                success=True, message_id=message_id, raw_response=reply_text
            )

        except Exception as e:
            logger.error("[A2A] send failed: %s", e, exc_info=True)
            return SendResult(success=False, error=str(e), retryable=True)

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        return None  # A2A has no typing indicator

    async def send_image(self, chat_id, image_url, caption=None) -> SendResult:
        return SendResult(success=False, error="A2A image send not yet implemented")

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": f"a2a:{chat_id}", "type": "a2a_peer", "chat_id": chat_id}

    # ------------------------------------------------------------------
    # Message handler — capture-via-wrapping
    # ------------------------------------------------------------------
    def set_message_handler(self, handler):  # type: ignore[override]
        """Wrap the gateway's message handler so we can intercept the agent's
        final reply text and route it back to the A2A caller via the
        per-message-id capture callback registry.

        Why a wrapper instead of a base-class hook (Task 7 reality-check):
        - Hermes' MessageHandler contract already says the handler may
          return a string (final text), an EphemeralReply, or None
          (already-delivered, e.g. streaming). See gateway/platforms/base.py
          line 1115.
        - For A2A, we deliver the reply through HermesA2AExecutor →
          event_queue.enqueue_event(reply_msg) inside the executor. We do
          NOT want base.py's _process_message_background to also call
          self._send_with_retry (which would hit the SendResult(success=False,
          error="not implemented") path and log a spurious failure).
        - Returning None from the wrapped handler engages the existing
          base-class contract: `if response: ... if text_content: ...
          _send_with_retry()` is gated, so no outbound send fires for
          A2A turns.
        - Streaming is disabled per-platform via
          `display.platforms.a2a.streaming: false` in user config (gateway/
          run.py line ~13738), so no StreamConsumer.adapter.send hits this
          adapter either.

        Net effect: A2A inbound → handler → final string captured →
        executor enqueues reply Message → caller sees the response. base.py
        sees None and skips its own send path entirely.
        """

        async def _wrapped(event):
            # Delegate to the gateway's real handler — runs the agent,
            # returns the final string (or None when streaming already sent).
            try:
                result = await handler(event)
            except Exception:
                # Surface the failure to the A2A caller as a reply rather
                # than letting the executor's wait_for time out at 120s.
                cb = self._post_response_callbacks.get(event.message_id) if event.message_id else None
                if cb is not None:
                    try:
                        await cb("[A2A error: handler raised an exception]")
                    except Exception:  # pragma: no cover - defensive
                        pass
                raise

            text, _ttl = self._unwrap_ephemeral(result)  # TTL is dropped — A2A doesn't need ephemeral TTLs

            if text and event.message_id:
                cb = self._post_response_callbacks.get(event.message_id)
                if cb is not None:
                    try:
                        await cb(text)
                    except Exception as e:
                        logger.error(
                            "[A2A] post-response capture callback failed: %s",
                            e,
                            exc_info=True,
                        )

            # Always return None so base.py's _process_message_background
            # skips its own _send_with_retry (the `if text_content:` gate).
            # The A2A executor has either captured the text (above) or
            # the handler streamed/already-delivered (text was None).
            return None

        super().set_message_handler(_wrapped)

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
