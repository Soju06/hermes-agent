"""A2A peer registry resolver (Tier-1: static config).

Phase 1 PoC scope per ADR-001 §3:
  Tier-1 — static `[a2a].peers` mapping in user config (this module)
  Tier-2 — Discord "About me" parsing             (deferred to Phase 1.5)
  Tier-3 — central registry lookup                 (deferred to Phase 2)

The Tier-1 resolver is pure and config-driven — no I/O — so the gateway
can call it on every inbound Discord event without touching the network.
Discord IDs are coerced to ``str`` so int-typed IDs from raw API payloads
match the str-keyed config dict.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def resolve_a2a_peer_url(
    discord_user_id: str | int,
    a2a_config: Optional[Dict[str, Any]],
) -> Optional[str]:
    """Return the agent_card_url of the registered A2A peer, or None.

    Tier-1 only (PoC). Returns ``None`` when:
      - ``a2a_config`` is None / falsy
      - ``a2a_config["peers"]`` is missing or empty
      - ``discord_user_id`` is not a key in the peers map

    Args:
        discord_user_id: Author ID from a Discord message — accepted as
            ``str`` or ``int`` (Discord API hands out 64-bit ints).
        a2a_config: The ``[a2a]`` section of the gateway config dict, or
            ``None`` when A2A is disabled.

    Returns:
        Agent card URL (e.g. ``http://10.0.0.1:8765/``) if registered,
        else ``None``.
    """
    if not a2a_config:
        return None
    peers = a2a_config.get("peers") or {}
    return peers.get(str(discord_user_id))
