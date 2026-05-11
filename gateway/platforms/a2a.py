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
import time
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
            # ADR-011 v2.1 §8a / Phase 4 Task 37: route inbound side-effect
            # through the dispatch shim. Picks ADR-011 channel_broadcast
            # transcript-append path OR ADR-007 v3 mirror path OR disabled
            # no-op based on adapter._inbound_handler — mutually exclusive.
            # Best-effort, never blocks reply path.
            await self._adapter._dispatch_a2a_inbound(
                message=incoming, peer_agent_id=peer_agent_id, text=text
            )

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
        # ADR-003 dual-send mirror config. `mirror_channel_id` is wired from
        # `display.platforms.a2a.mirror_channel_id` by gateway/run.py at adapter
        # construction time (or here via extra for sandbox/test convenience).
        # ADR-007 v2 (Phase 2.5) replaced ADR-003's hand-rolled mirror with
        # `_status_adapter` swap + GatewayStreamConsumer reuse, so the
        # `_min_mirror_interval_s` / `_last_mirror_at` rate-limit state is
        # gone — stream_consumer's per-message edit_interval handles pacing.
        self._mirror_channel_id: Optional[str] = config.extra.get(
            "mirror_channel_id"
        )
        # ADR-008 (Phase 3a): per-peer mirror routing. Optional dict of
        # peer_user_id → channel_id. Lookup priority in
        # `_resolve_a2a_mirror_swap` (gateway/run.py):
        #   1. _mirror_channels[peer_user_id]    — peer match
        #   2. _mirror_channels["default"]       — default fallback
        #   3. _mirror_channel_id                — Phase 2.5 backwards-compat
        #   4. None → swap=False → force-off
        # Wired from `display.platforms.a2a.mirror_channels` by gateway/run.py
        # (or here via extra for sandbox/test convenience).
        self._mirror_channels: Dict[str, str] = dict(
            config.extra.get("mirror_channels") or {}
        )
        # ADR-011 v2.1 (Phase 4 Task 34): channel-bound peer group for
        # channel-broadcast A2A wire. Maps surface channel id (Discord
        # channel / Telegram chat) → list of bot_user_ids participating in
        # that channel via A2A. When a bot replies in a given channel,
        # Task 35's `_broadcast_to_channel_peers` iterates these IDs
        # (excluding self) and fires a fire-and-forget broadcast to each
        # peer that advertises the `hermes-channel-broadcast/v1` extension
        # (ADR-012). Phase 4 scope = static config only (mechanism (a)
        # in ADR-011 §2); Phase 5+ adds Activity-scan / slash-command /
        # channel-join hooks (b/c/d). Optional — empty default means
        # channel-broadcast is disabled for this adapter.
        self._channel_peers: Dict[str, List[str]] = {
            str(channel_id): [str(b) for b in bot_ids]
            for channel_id, bot_ids in (config.extra.get("channel_peers") or {}).items()
        }
        # ADR-012 (Phase 4 Task 34): cache of resolved peer AgentCards keyed
        # by bot_user_id. Populated by `_resolve_well_known_peers` (ADR-004)
        # after a successful fetch. Used by Task 40 to verify peer capability
        # via the `hermes-channel-broadcast/v1` extension before broadcasting
        # (R2 Tier 1 sender-side guard). Empty default — entries appear as
        # peers resolve at connect() time or lazily via send().
        self._peer_cards: Dict[str, Dict[str, Any]] = {}
        # ADR-006 multi-turn termination. Per-(peer_id, context_id) counter
        # capped at `max_turns_per_conversation` (default 5). When the cap is
        # hit, the inbound is dropped: real handler is NOT invoked, mirror is
        # NOT posted, but the A2A capture callback fires with a turn-limit
        # notice so the caller doesn't hang for 60s.
        self._turn_counters: Dict[tuple, int] = {}
        self._max_turns_per_conversation: int = int(
            config.extra.get("max_turns_per_conversation", 5)
        )
        self._self_card: Optional[Dict[str, Any]] = config.extra.get("agent_card", None)
        # ADR-011 v2.1 (Phase 4 Task 35): self bot_user_id, mirrored here from
        # `discord_bot_user_id` for the channel-broadcast self-skip path.
        # `_broadcast_to_channel_peers` excludes this id from broadcast targets
        # to prevent the bot from emitting an A2A copy of its own reply back
        # to itself (which would loop into the inbound handler). Default None
        # — when unset, broadcast still runs but the self-skip is a no-op
        # (callers should always configure `discord_bot_user_id` when using
        # channel_peers).
        _self_bot = config.extra.get("discord_bot_user_id")
        self._self_bot_user_id: Optional[str] = (
            str(_self_bot) if _self_bot is not None else None
        )
        # ADR-011 v2.1 §3 / Phase 4 Task 37: per-channel transcript ring buffer.
        # Inbound A2A messages arriving via the channel_broadcast handler are
        # appended here so a later reply-policy check (Task 38) can read the
        # last N entries as context for the LLM trigger. defaultdict(deque)
        # auto-creates a maxlen=100 deque per surface channel id — bounded so
        # a noisy peer channel can't grow memory unbounded.
        from collections import defaultdict, deque
        self._channel_transcripts: Dict[str, "deque[Dict[str, Any]]"] = (
            defaultdict(lambda: deque(maxlen=100))
        )
        # ADR-011 v2.1 §8a / Phase 4 Task 37: inbound handler resolution.
        # Modes:
        #   "channel_broadcast" — ADR-011 path: transcript append + immediate ack
        #   "mirror"            — ADR-007 v3 path: stream chunks to Discord mirror
        #   "disabled"          — neither side-effect (handle_message still runs
        #                         in the executor for compatibility with the
        #                         pre-Phase-3a roundtrip tests)
        # Resolution priority:
        #   1. Explicit `inbound_handler` config wins (operator decision)
        #   2. Auto: `channel_peers` set     → "channel_broadcast"
        #   3. Auto: `mirror_channels` set   → "mirror"
        #   4. Auto: neither                 → "disabled"
        # Mixed config (BOTH channel_peers AND mirror_channels) WITHOUT explicit
        # `inbound_handler` → fatal at __init__. Operator must commit to one
        # path. Explicit setting overrides the fatal — useful for gradual
        # migration where the operator wants both data structures loaded but
        # has picked the active path.
        _explicit_handler = config.extra.get("inbound_handler")
        _has_channel_peers = bool(self._channel_peers)
        _has_mirror_channels = bool(self._mirror_channels)
        if _explicit_handler:
            if _explicit_handler not in ("channel_broadcast", "mirror", "disabled"):
                raise ValueError(
                    f"[A2A] invalid inbound_handler={_explicit_handler!r}; "
                    "must be one of 'channel_broadcast' / 'mirror' / 'disabled'"
                )
            self._inbound_handler: str = _explicit_handler
        elif _has_channel_peers and _has_mirror_channels:
            raise ValueError(
                "[A2A] mixed config: BOTH `channel_peers` (ADR-011) and "
                "`mirror_channels` (ADR-007 v3) are set, but no explicit "
                "`inbound_handler` config was provided. Pick one path "
                "explicitly: `inbound_handler: channel_broadcast` or "
                "`inbound_handler: mirror` (or `disabled` to opt out)."
            )
        elif _has_channel_peers:
            self._inbound_handler = "channel_broadcast"
        elif _has_mirror_channels:
            self._inbound_handler = "mirror"
        else:
            self._inbound_handler = "disabled"
        # ADR-011 v2.1 §9 / Phase 4 Task 39: Loop / echo prevention caches.
        # Both are message-id → timestamp dicts with lazy on-access expiry.
        # - `_recently_seen` keys = A2A Message.message_id
        # - `_recently_seen_surface` keys = hermes.surface_message_id metadata
        #   (also populated by `_mark_surface_outbound` from the Discord reply
        #    hook in Task 35b — bot's own surface messages register themselves
        #    so an A2A wire echo of the same surface id is dedup'd as if it
        #    were a self-echo)
        # TTL default 300s (5 min) per plan §Task 39 + ADR-011 §9. Tunable
        # via `loop_prevention_ttl_seconds` extra for tests.
        self._recently_seen: Dict[str, float] = {}
        self._recently_seen_surface: Dict[str, float] = {}
        self._loop_prevention_ttl_seconds: int = int(
            config.extra.get("loop_prevention_ttl_seconds", 300)
        )
        # ADR-011 v2.1 §6 / Phase 4 Task 38: reply trigger policy.
        # Default mode='autonomous' per Soju Q3 directive. mention_only and
        # hybrid available via config. cost guards (max_consecutive_self_replies +
        # cooldown_after_silent_decision) apply to autonomous + hybrid; mention_only
        # ignores them because direct address always overrides.
        _raw_policy = dict(config.extra.get("reply_policy") or {})
        _mode = _raw_policy.get("mode", "autonomous")
        if _mode not in ("autonomous", "mention_only", "hybrid"):
            raise ValueError(
                f"[A2A] invalid reply_policy.mode={_mode!r}; must be one of "
                "'autonomous' / 'mention_only' / 'hybrid'"
            )
        self._reply_policy: Dict[str, Any] = {
            "mode": _mode,
            "max_consecutive_self_replies": int(
                _raw_policy.get("max_consecutive_self_replies", 3)
            ),
            "cooldown_after_silent_decision": int(
                _raw_policy.get("cooldown_after_silent_decision", 30)
            ),
            "channel_hints": list(_raw_policy.get("channel_hints") or []),
        }
        # Per-channel counters/timestamps for the cost guards.
        # `_consecutive_self_replies[channel_id]` increments on mark_self_reply,
        # resets on mark_other_inbound or any user message.
        # `_last_silent_decision[channel_id]` is the timestamp of the last
        # silent-decision marker; should_trigger_reply skips while
        # now < timestamp + cooldown_after_silent_decision (except mentions).
        self._consecutive_self_replies: Dict[str, int] = {}
        self._last_silent_decision: Dict[str, float] = {}
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
                    # ADR-012 (Phase 4 Task 34): cache the fetched card so
                    # Task 40 can verify the `hermes-channel-broadcast/v1`
                    # extension before broadcasting (R2 Tier 1 sender-side
                    # guard). Keyed by the same bot_user_id used in _peers
                    # so lookup mirrors the existing peer dedup key shape.
                    self._peer_cards[str(bot_id)] = card
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

    # ------------------------------------------------------------------
    # ADR-011 v2.1 §3 §8a / Phase 4 Task 37: Inbound handler split + transcript
    # ------------------------------------------------------------------
    @staticmethod
    def _decode_inbound_metadata(message) -> Dict[str, str]:
        """Pull ``hermes.*`` keys out of an inbound ``Message.metadata``
        protobuf Struct into a plain dict. Missing keys yield empty strings —
        the inbound handler treats "" as "unset".

        Defensive: also accepts a raw dict (test convenience) and a None
        metadata field (legacy peer that didn't set it).
        """
        if message is None:
            return {}
        meta = getattr(message, "metadata", None)
        if meta is None:
            return {}
        out: Dict[str, str] = {}
        # Real protobuf Struct: .fields is a map of name → Value
        fields = getattr(meta, "fields", None)
        if fields is not None:
            try:
                for k in fields:
                    v = fields[k]
                    if v.HasField("string_value"):
                        out[k] = v.string_value
                    elif v.HasField("number_value"):
                        out[k] = str(v.number_value)
                    elif v.HasField("bool_value"):
                        out[k] = "true" if v.bool_value else "false"
                    else:
                        out[k] = ""
                return out
            except Exception:
                pass
        # Fallback: dict-like (test fixture or future SDK change)
        if isinstance(meta, dict):
            return {str(k): str(v) for k, v in meta.items()}
        return {}

    def _prune_expired_seen(self, now: float) -> None:
        """ADR-011 §9 Task 39: lazy on-access TTL eviction.

        Called from the channel_broadcast inbound handler before each dedup
        check so cache size stays bounded without a background sweeper. O(N)
        in cache size per call — fine for the expected scale (sub-100 entries
        per ~5min window per channel). If load profile changes this can be
        amortized via a periodic sweep, but the simplest thing works.
        """
        ttl = self._loop_prevention_ttl_seconds
        cutoff = now - ttl
        # Snapshot keys before mutating to avoid RuntimeError mid-iteration
        for k in [k for k, ts in self._recently_seen.items() if ts < cutoff]:
            self._recently_seen.pop(k, None)
        for k in [k for k, ts in self._recently_seen_surface.items() if ts < cutoff]:
            self._recently_seen_surface.pop(k, None)

    def _mark_surface_outbound(self, surface_message_id: str) -> None:
        """Task 39 helper for Task 35b wiring.

        Discord/Telegram reply hook calls this when THIS bot sends a surface
        message so the same id is already in ``_recently_seen_surface`` by
        the time an A2A wire echo (carrying the same ``hermes.surface_message_id``)
        could arrive. Without this, the bot's own message would re-enter via
        the channel_broadcast handler and trigger a self-conversation loop
        through the dedup path (which only catches *seen-before* ids, not
        *just-sent* ones).
        """
        if not surface_message_id:
            return
        self._recently_seen_surface[str(surface_message_id)] = time.time()

    # ------------------------------------------------------------------
    # ADR-011 v2.1 §6 / Phase 4 Task 38: Reply trigger policy
    # ------------------------------------------------------------------
    def should_trigger_reply(
        self,
        channel_id: str,
        *,
        user_message: str,
        mentioned_user_ids: Optional[set] = None,
        is_user_message: bool = True,
    ) -> bool:
        """Decide whether to dispatch an LLM reply for this channel event.

        Decision tree per ADR-011 §6 Q3:

          mention_only:
            - is_user_message AND self._self_bot_user_id ∈ mentioned_user_ids
              → True
            - else → False
            - Cost guards do NOT apply — direct address always overrides.

          autonomous (default):
            - Cost guard A: consecutive self-replies cap. If
              `_consecutive_self_replies[channel_id]` ≥ max, return False
              UNLESS the caller is explicitly mentioned (mentions always win).
            - Cost guard B: cooldown after silent decision. If
              `now < _last_silent_decision[channel_id] + cooldown`, return
              False UNLESS the caller is explicitly mentioned.
            - Otherwise → True (every event becomes a reply candidate).

          hybrid:
            - Mention → True (same as mention_only).
            - Channel-hint regex match against user_message → True.
              Regex compiled lazily; invalid pattern logged + skipped.
            - is_user_message=False (peer A2A broadcast) without hint match
              → False. hybrid is mention-or-explicit-hint, not autonomous.

        `mentioned_user_ids` is a set of string user-ids extracted by the
        platform adapter (Discord/Telegram) BEFORE calling this. Empty set
        or None == nobody mentioned.
        """
        mode = self._reply_policy["mode"]
        mentions = mentioned_user_ids or set()
        is_self_mentioned = bool(
            self._self_bot_user_id and self._self_bot_user_id in mentions
        )

        if mode == "mention_only":
            # Direct address only. Peer broadcasts never trigger here.
            return bool(is_user_message and is_self_mentioned)

        # autonomous + hybrid share the cost guards (with mention bypass)
        if not is_self_mentioned:
            # Consecutive self-replies cap
            cap = int(self._reply_policy["max_consecutive_self_replies"])
            if self._consecutive_self_replies.get(str(channel_id), 0) >= cap:
                return False
            # Cooldown window
            cooldown = int(self._reply_policy["cooldown_after_silent_decision"])
            last_silent = self._last_silent_decision.get(str(channel_id))
            if last_silent is not None and time.time() < last_silent + cooldown:
                return False

        if mode == "autonomous":
            return True

        # hybrid:
        if is_self_mentioned:
            return True
        for pat in self._reply_policy["channel_hints"]:
            try:
                import re

                if re.search(pat, user_message or ""):
                    return True
            except Exception as e:
                logger.warning(
                    "[A2A] reply_policy hint regex %r failed; skipping: %s",
                    pat,
                    e,
                )
        return False

    def mark_self_reply(self, channel_id: str) -> None:
        """Increment the consecutive-self-replies counter for this channel.

        Task 35b wiring will call this whenever THIS bot emits a reply that
        broadcasts to channel_peers (the chain begins immediately after the
        reply lands on Discord/Telegram, before the next inbound).
        """
        cid = str(channel_id)
        self._consecutive_self_replies[cid] = (
            self._consecutive_self_replies.get(cid, 0) + 1
        )

    def mark_other_inbound(self, channel_id: str) -> None:
        """Reset the consecutive counter for this channel.

        Called whenever a DIFFERENT sender (other bot, user) lands a message
        in the channel — the chain of self-replies is broken so the cap
        starts fresh. Channel-broadcast handler (Task 37) and any
        user-message hook from gateway/run.py (Task 35b) call this.
        """
        self._consecutive_self_replies[str(channel_id)] = 0

    def mark_silent_decision(self, channel_id: str) -> None:
        """Start the cooldown window for this channel.

        Called by Task 35b's reply hook when the LLM evaluates the channel
        and decides not to reply ('silent'). For `cooldown_after_silent_decision`
        seconds afterwards, should_trigger_reply will return False for
        non-mention triggers — gives the channel a breather before the bot
        re-evaluates.
        """
        self._last_silent_decision[str(channel_id)] = time.time()

    def build_transcript_context(
        self, channel_id: str, max_entries: int = 20
    ) -> List[Dict[str, Any]]:
        """Return the last `max_entries` TranscriptEntry rows for this channel,
        augmented with an `is_self` flag derived from self._self_bot_user_id.

        Task 35b's gateway wiring will call this when it decides to dispatch
        an LLM reply (after `should_trigger_reply` returns True). The wiring
        is free to render the entries into the conversation_history slot it
        prefers — typically an assistant-role message for `is_self`, otherwise
        a system-or-user message labelled by `sender_bot_user_id`. This
        helper just returns the data; the splice site decides the layout.
        """
        deq = self._channel_transcripts.get(str(channel_id))
        if not deq:
            return []
        # Take last N (deque slicing isn't supported; convert + tail)
        recent = list(deq)[-int(max_entries):]
        out: List[Dict[str, Any]] = []
        for entry in recent:
            row = dict(entry)
            row["is_self"] = bool(
                self._self_bot_user_id
                and entry.get("sender_bot_user_id") == self._self_bot_user_id
            )
            out.append(row)
        return out

    async def _handle_a2a_inbound_channel_broadcast(
        self, message, peer_agent_id: str
    ) -> None:
        """ADR-011 channel_broadcast inbound handler.

        Appends a TranscriptEntry to ``_channel_transcripts[channel_id]`` and
        returns immediately. Does NOT trigger the LLM — that's Task 38's
        ``reply_policy``. Does NOT mirror to Discord — that's the legacy
        ADR-007 v3 ``_mirror_a2a_inbound_to_discord`` path, which is mutually
        exclusive with this one (handler split per ``_inbound_handler``).

        Entry shape (TranscriptEntry):
            {
                "sender_bot_user_id": str,    # from hermes.sender_bot_user_id
                "text":               str,    # parts[0].text
                "timestamp":          float,  # time.time()
                "message_id":         str,    # A2A Message.message_id
                "surface_message_id": str,    # from hermes.surface_message_id
                "surface_channel_id": str,    # from hermes.surface_channel_id
            }
        """
        meta = self._decode_inbound_metadata(message)
        channel_id = meta.get("hermes.surface_channel_id") or ""
        if not channel_id:
            # No surface channel id → can't bucket this transcript. Drop with
            # a debug log; a malformed legacy peer would land here.
            logger.debug(
                "[A2A] channel_broadcast inbound from %s has no "
                "hermes.surface_channel_id metadata; transcript drop",
                peer_agent_id,
            )
            return

        # ADR-011 v2.1 §9 Task 39 — loop / echo prevention 3-check.
        # Lazy TTL expiry first so a 5-min-old dedup entry doesn't keep blocking
        # legitimate resends.
        now = time.time()
        self._prune_expired_seen(now)

        # (1) Self-echo skip — sender == self never lands in own transcript.
        # We don't even bother caching this — repeated self-echoes are cheap to
        # re-check and caching them would pollute the dedup map.
        sender = meta.get("hermes.sender_bot_user_id", "")
        if sender and self._self_bot_user_id and sender == self._self_bot_user_id:
            logger.debug(
                "[A2A] channel_broadcast inbound self-echo skip (peer=%s)",
                peer_agent_id,
            )
            return

        # (2) Message-id dedup — same A2A Message.message_id within TTL.
        a2a_msg_id = getattr(message, "message_id", "") or ""
        if a2a_msg_id and a2a_msg_id in self._recently_seen:
            logger.debug(
                "[A2A] channel_broadcast inbound dedup skip msg_id=%s (peer=%s)",
                a2a_msg_id,
                peer_agent_id,
            )
            return

        # (3) Surface dedup — same hermes.surface_message_id within TTL.
        # Also catches Task 35b: when this bot sent the surface message, the
        # Discord reply hook called _mark_surface_outbound first, so the same
        # surface id arriving via A2A wire is dropped here.
        surface_id = meta.get("hermes.surface_message_id", "")
        if surface_id and surface_id in self._recently_seen_surface:
            logger.debug(
                "[A2A] channel_broadcast inbound dedup skip surface_id=%s (peer=%s)",
                surface_id,
                peer_agent_id,
            )
            return

        # Extract the text from parts[0] (Task 36 wire spec: single text part).
        text = ""
        try:
            for p in message.parts:
                if p.HasField("text"):
                    text = p.text
                    break
        except Exception:
            pass

        entry: Dict[str, Any] = {
            "sender_bot_user_id": meta.get("hermes.sender_bot_user_id", "")
            or peer_agent_id,
            "text": text,
            "timestamp": time.time(),
            "message_id": getattr(message, "message_id", "") or "",
            "surface_message_id": meta.get("hermes.surface_message_id", ""),
            "surface_channel_id": channel_id,
        }
        self._channel_transcripts[channel_id].append(entry)
        # Record dedup keys AFTER the append so a future inbound with the same
        # ids hits the dedup gate above. `now` was captured at the top of the
        # handler so the timestamp lines up with `_prune_expired_seen`'s view.
        if a2a_msg_id:
            self._recently_seen[a2a_msg_id] = now
        if surface_id:
            self._recently_seen_surface[surface_id] = now
        # ADR-011 §6 / Task 38: a non-self inbound breaks the consecutive
        # self-reply chain. The self-echo path already returned above, so
        # any inbound that lands here is by definition NOT us.
        self.mark_other_inbound(channel_id)

    async def _dispatch_a2a_inbound(
        self, message, peer_agent_id: str, text: str
    ) -> None:
        """Executor-side shim — routes A2A inbound to the configured handler.

        Called by ``HermesA2AExecutor.execute`` BEFORE the LLM dispatch loop
        runs. Picks one of:
          - ``_inbound_handler == "channel_broadcast"``:
              call ``_handle_a2a_inbound_channel_broadcast`` (ADR-011 path).
              Mirror skipped.
          - ``_inbound_handler == "mirror"``:
              call ``_mirror_a2a_inbound_to_discord`` (ADR-007 v3 path).
              Transcript skipped.
          - ``_inbound_handler == "disabled"``:
              no-op. Executor still continues to ``handle_message`` for the
              roundtrip path (pre-Phase-3a compatibility).

        Failures inside the handler are logged but never propagate — a broken
        handler must not block the inbound ack.
        """
        try:
            if self._inbound_handler == "channel_broadcast":
                await self._handle_a2a_inbound_channel_broadcast(
                    message=message, peer_agent_id=peer_agent_id
                )
            elif self._inbound_handler == "mirror":
                await self._mirror_a2a_inbound_to_discord(peer_agent_id, text)
            else:
                # "disabled" — no side-effect
                pass
        except Exception as e:
            logger.warning(
                "[A2A] inbound handler %r raised for peer %s (swallowed): %s",
                self._inbound_handler,
                peer_agent_id,
                e,
            )

    # ------------------------------------------------------------------
    # ADR-011 v2.1 / Phase 4 Task 35: Dual-delivery outbound broadcast
    # ------------------------------------------------------------------
    def _build_broadcast_payload(
        self,
        text: str,
        surface_channel_id: str,
        surface_message_id: str,
        surface_platform: str,
        context_id: str,
    ):
        """Construct the A2A ``Message`` for a channel-broadcast wire send.

        Follows ADR-011 v2.1 §3 (Q1 directive: text + minimal ``hermes.*``
        metadata):

            role     = ROLE_AGENT       (peer-to-peer, ADR-001)
            parts    = [text part]
            metadata = Struct{
                "hermes.sender_bot_user_id": self._self_bot_user_id,
                "hermes.surface_channel_id": surface_channel_id,
                "hermes.surface_message_id": surface_message_id,
                "hermes.surface_platform":   surface_platform,
                "hermes.context_id":         context_id,
            }
            message_id = fresh uuid4
            context_id = mirrored

        Plan §Task 36 §3 chunking convention: when a reply was streamed in N
        chunks on the human surface, the *caller* passes the LAST chunk's
        surface message id here. This helper just propagates whatever it's
        given — it doesn't track chunks itself.

        Spec-vs-SDK drift note: ADR-011 v2.1 §3 paper writes ``role="assistant"``
        as a conceptual dict-style spec. a2a-sdk 1.0.2's protobuf ``Role`` enum
        only has ``ROLE_USER`` and ``ROLE_AGENT`` — no "assistant" value. Since
        ADR-001 frames this as peer-to-peer agent communication, ``ROLE_AGENT``
        is the correct mapping. ADR-011 v2.2 minor amend should record this
        mapping so future readers don't trip on it.
        """
        from a2a.types import (
            Message as A2AMessage,
            Part as A2APart,
            Role,
        )
        from google.protobuf import struct_pb2
        import uuid

        # Pack hermes.* metadata into a protobuf Struct. struct_pb2.Struct
        # rejects None (NULL must be set explicitly), so we coerce None to ""
        # — channel-broadcast metadata fields are all conceptually strings
        # and "" carries the "unset" signal downstream.
        meta_struct = struct_pb2.Struct()
        meta_struct.update(
            {
                "hermes.sender_bot_user_id": self._self_bot_user_id or "",
                "hermes.surface_channel_id": str(surface_channel_id),
                "hermes.surface_message_id": str(surface_message_id),
                "hermes.surface_platform": str(surface_platform),
                "hermes.context_id": str(context_id),
            }
        )

        return A2AMessage(
            role=Role.ROLE_AGENT,
            parts=[A2APart(text=text)],
            message_id=str(uuid.uuid4()),
            context_id=str(context_id),
            metadata=meta_struct,
        )

    async def _broadcast_to_channel_peers(
        self,
        channel_id: str,
        content: str,
        surface_message_id: str,
        surface_platform: str,
        context_id: str,
    ) -> None:
        """Fire-and-forget broadcast the bot's reply to all channel peers.

        Iterates ``self._channel_peers[channel_id]`` (the bot user_ids subscribed
        to this surface channel via static config — mechanism (a) in
        ADR-011 §2). For each peer that is NOT ourselves, schedules a
        fire-and-forget ``_send_fire_and_forget`` task and returns immediately
        without awaiting peer replies.

        Scope notes (Phase 4):
        - Caller side guard for ``hermes-channel-broadcast/v1`` extension lands
          in Task 40 (R2 Tier 1). Phase 4 broadcasts to every peer in the
          channel regardless of capability; the extension check is added in
          Task 40 once `_has_channel_broadcast_ext` exists.
        - Peers not in ``self._peers`` (i.e. not resolved at startup) are
          skipped silently with a debug log — same lazy-retry semantics as
          ``send()`` will catch them on a future broadcast.
        - Self-skip uses ``self._self_bot_user_id``. If unset (None), no
          skip happens — operators using ``channel_peers`` must configure
          ``discord_bot_user_id`` or risk self-echo loops (handled defensively
          in Task 39 anyway).
        - Failures inside ``_send_fire_and_forget`` are logged but never
          propagate — a broken peer must not break the sender's reply flow.

        This method itself returns within a few ms once the peer tasks are
        scheduled — fire-and-forget semantics are preserved end-to-end.
        """
        peer_ids = self._channel_peers.get(str(channel_id))
        if not peer_ids:
            return

        metadata = {
            "hermes.sender_bot_user_id": self._self_bot_user_id,
            "hermes.surface_channel_id": str(channel_id),
            "hermes.surface_message_id": str(surface_message_id),
            "hermes.surface_platform": surface_platform,
            "hermes.context_id": str(context_id),
        }

        for peer_id in peer_ids:
            peer_id = str(peer_id)
            if (
                self._self_bot_user_id is not None
                and peer_id == self._self_bot_user_id
            ):
                # ADR-011 §6: never broadcast to self — would self-echo.
                continue
            peer_url = self._peers.get(peer_id)
            if peer_url is None:
                logger.debug(
                    "[A2A] broadcast: peer %s not in _peers; skipping (Phase 4 "
                    "scope — no lazy resolve)",
                    peer_id,
                )
                continue
            # Fire-and-forget — schedule the task and move on. The broadcast
            # method itself returns within ms regardless of peer latency.
            asyncio.create_task(
                self._send_fire_and_forget(
                    peer_id=peer_id,
                    peer_url=peer_url,
                    content=content,
                    metadata=metadata,
                )
            )

    async def _send_fire_and_forget(
        self,
        peer_id: str,
        peer_url: str,
        content: str,
        metadata: Dict[str, Any],
    ) -> None:
        """Send a single broadcast message to one peer with fire-and-forget
        semantics (a2a-sdk 1.0.2 `SendMessageConfiguration.return_immediately=True`).

        - Server-side: ``default_request_handler_v2.py`` breaks on the first
          event when ``return_immediately`` is set, so the peer agent's reply
          path runs in background and never blocks this caller's network round
          trip.
        - Client-side: we still consume the AsyncIterator but it closes
          immediately after the task acknowledgement, so the call returns in
          well under a second even when the peer is slow.
        - Exceptions are swallowed and logged — broadcast must not propagate
          peer failures to the sender's reply flow.
        """
        if not A2A_AVAILABLE:
            logger.warning(
                "[A2A] broadcast skipped: a2a-sdk not available (peer=%s)", peer_id
            )
            return

        try:
            from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
            from a2a.types import (
                SendMessageConfiguration,
                SendMessageRequest,
            )
            import httpx

            # Reconstruct the broadcast payload via the Task 36 helper so the
            # wire format stays in one place (DRY + spec-locked). Caller fed us
            # the flat `metadata` dict; unpack it back into the helper's named
            # kwargs. The helper re-builds the protobuf Struct from scratch —
            # we don't reuse the dict directly because the SDK Message field
            # wants a Struct, not a Python dict.
            payload = self._build_broadcast_payload(
                text=content,
                surface_channel_id=metadata.get("hermes.surface_channel_id", ""),
                surface_message_id=metadata.get("hermes.surface_message_id", ""),
                surface_platform=metadata.get("hermes.surface_platform", ""),
                context_id=metadata.get("hermes.context_id") or peer_id,
            )

            async with httpx.AsyncClient(timeout=10.0) as http:
                resolver = A2ACardResolver(http, peer_url)
                peer_card = await resolver.get_agent_card()
                client = ClientFactory(
                    ClientConfig(httpx_client=http, streaming=False)
                ).create(peer_card)

                req = SendMessageRequest(
                    message=payload,
                    configuration=SendMessageConfiguration(
                        return_immediately=True,
                    ),
                )

                # Consume the iterator; with return_immediately=True the server
                # closes the stream after the first ack event.
                async for _resp in client.send_message(req):
                    break

        except Exception as e:
            logger.warning(
                "[A2A] broadcast to %s failed (fire-and-forget, swallowed): %s",
                peer_id,
                e,
            )

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
    # ADR-006 multi-turn termination
    # ------------------------------------------------------------------
    def _reset_turn_counters_for_chat(self, chat_id: str) -> int:
        """Clear all turn counters whose context_id == chat_id.

        Called by Discord adapter when a non-bot message lands in the mirror
        channel — a human (re-)joining the conversation resets the cap so
        multi-turn peer chats can resume.

        Returns the number of (peer_id, chat_id) pairs cleared.
        """
        keys = [k for k in self._turn_counters if k[1] == chat_id]
        for k in keys:
            self._turn_counters.pop(k, None)
        if keys:
            logger.info(
                "[A2A] reset %d turn counter(s) on chat %s",
                len(keys),
                chat_id,
            )
        return len(keys)

    # ------------------------------------------------------------------
    # ADR-007 inbound mirror — surface peer message text in Discord too
    # ------------------------------------------------------------------
    async def _mirror_a2a_inbound_to_discord(
        self, peer_id: str, text: Optional[str]
    ) -> None:
        """Post the A2A INBOUND text to the configured Discord mirror channel.

        ADR-007: while replier-side streaming mirror (status_adapter swap)
        gives humans a live view of the *outgoing* reply, the inbound side
        of the conversation is invisible without this — the channel reads
        like a one-sided phone call. This method posts a single line per
        inbound message:

            📥 from {peer_id}: {text}

        Best-effort: missing mirror_channel_id, empty text, missing Discord
        adapter, or send failure all silently no-op so the inbound dispatch
        path stays alive (ADR-003 Risk D semantic, preserved by ADR-007).

        ADR-008 (Phase 3a): lookup priority for the target channel mirrors the
        swap helper's chain — `_mirror_channels[peer_id]` → `_mirror_channels["default"]`
        → `_mirror_channel_id` → no-op. Without this the inbound mirror is
        silently skipped whenever the config switches to per-peer routing.
        """
        target_chan = (
            self._mirror_channels.get(peer_id)
            or self._mirror_channels.get("default")
            or self._mirror_channel_id
        )
        if not target_chan:
            return
        if not text or not text.strip():
            return

        try:
            from gateway import run as _gw_run

            runner = _gw_run._gateway_runner_ref()
            if runner is None:
                return
            discord_adapter = runner.adapters.get(Platform.DISCORD)
            if discord_adapter is None:
                return
            await discord_adapter.send(
                chat_id=target_chan,
                content=f"📥 from {peer_id}: {text}",
            )
            logger.debug(
                "[A2A] inbound mirrored to discord channel %s (peer=%s, %d chars)",
                target_chan,
                peer_id,
                len(text),
            )
        except Exception as e:
            logger.warning("[A2A] inbound mirror to discord failed: %s", e)

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
            # ADR-006 turn-limit check — fast path, before handler/LLM call.
            # Key on (peer_id, context_id) per ADR-006 §1. Fail-safe: if
            # event.source is malformed, skip the check (don't break the
            # reply path).
            try:
                src = getattr(event, "source", None)
                peer_id = getattr(src, "user_id", None) if src else None
                ctx_id = getattr(src, "chat_id", None) if src else None
            except Exception:
                peer_id = ctx_id = None

            if peer_id and ctx_id:
                key = (peer_id, ctx_id)
                count = self._turn_counters.get(key, 0)
                if count >= self._max_turns_per_conversation:
                    logger.info(
                        "[A2A] turn limit %d reached for peer=%s ctx=%s — dropping",
                        self._max_turns_per_conversation,
                        peer_id,
                        ctx_id,
                    )
                    # A2A spec satisfaction: the caller is awaiting a reply
                    # via the executor's wait_for(120s). Fire the capture
                    # callback with a notice so they don't hang.
                    msg_id = getattr(event, "message_id", None)
                    if msg_id:
                        cb = self._post_response_callbacks.get(msg_id)
                        if cb is not None:
                            try:
                                await cb(
                                    f"[A2A turn limit reached after "
                                    f"{self._max_turns_per_conversation} turns]"
                                )
                            except Exception as e:
                                logger.error(
                                    "[A2A] turn-limit notice callback failed: %s",
                                    e,
                                )
                    # Silent on Discord side (no mirror), no handler call,
                    # no counter increment. Return None per base.py contract.
                    return None
                # Increment AFTER the gate so we don't double-count drops.
                self._turn_counters[key] = count + 1

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

            # ADR-003 replier-side mirror REMOVED — superseded by ADR-007 v2.
            # The reply text now reaches Discord via GatewayStreamConsumer
            # (point 5 in the comment block above): the gateway's
            # _status_adapter is swapped to the Discord mirror channel for
            # A2A inbound messages, so stream_delta_callback's edits and
            # the final text both land in Discord automatically.

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
