#!/usr/bin/env python3
"""Task 19a — induction driver for ADR-006 turn-cap verification.

Hits Bot-A with a prompt that asks it to ping Bot-B 5 times via send_message.
With cap=2 (configured in docker/sandbox-bot-{a,b}-config.yaml), Bot-B's
_wrapped should drop the 3rd inbound and capture-cb fire with the
'turn limit reached' notice.

Expected post-conditions:
  - Bot-B logs: '[A2A] turn limit 2 reached for peer=...' (≥1 line)
  - Bot-B's _post_response_callbacks fires limit notice for the 3rd inbound
  - Bot-A receives the limit notice as a reply (not a real Bot-B reply)
  - Discord channel sees ≤ 2 mirror messages from Bot-B (3rd inbound = no mirror)
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

PROMPT = (
    "Hi Bot-A. Please send 5 separate messages to peer "
    "'1502705976233558126' via the send_message tool with platform='a2a' and "
    "target='1502705976233558126'. Each message should be a short greeting "
    "like 'hello #1', 'hello #2', etc. After all 5 sends complete, summarize "
    "for me: how many of Bot-B's replies looked like real replies vs how many "
    "were turn-limit notices."
)

BOT_A_URL = os.environ.get("BOT_A_URL", "http://127.0.0.1:8800/")


async def main() -> int:
    try:
        import httpx
        from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
        from a2a.types import Message, Part, Role, SendMessageRequest
    except ImportError as exc:
        print(f"FATAL: a2a-sdk + httpx required. {exc}", file=sys.stderr)
        return 2

    print(f"→ BOT_A_URL={BOT_A_URL}")
    print(f"→ Prompt asks Bot-A to ping Bot-B 5 times (cap=2 will trigger).")
    print()

    async with httpx.AsyncClient(timeout=600.0) as http:
        resolver = A2ACardResolver(http, BOT_A_URL)
        card = await resolver.get_agent_card()
        client = ClientFactory(
            ClientConfig(httpx_client=http, streaming=False)
        ).create(card)

        req = SendMessageRequest(
            message=Message(
                role=Role.ROLE_USER,
                parts=[Part(text=PROMPT)],
                message_id=str(uuid.uuid4()),
            )
        )

        print("→ Sending induction request …")
        reply_text_parts: list[str] = []
        async for ev in client.send_message(req):
            for attr in ("message", "result", "root"):
                obj = getattr(ev, attr, None)
                if obj is None:
                    continue
                parts = getattr(obj, "parts", None)
                if parts:
                    for p in parts:
                        root = getattr(p, "root", p)
                        text = getattr(root, "text", None)
                        if text:
                            reply_text_parts.append(text)
            if isinstance(ev, tuple):
                for item in ev:
                    if item is None:
                        continue
                    parts = getattr(item, "parts", None) or \
                            getattr(getattr(item, "status", None), "message", None)
                    if hasattr(parts, "parts"):
                        parts = parts.parts
                    if parts:
                        for p in parts:
                            root = getattr(p, "root", p)
                            text = getattr(root, "text", None)
                            if text:
                                reply_text_parts.append(text)

    full = "\n---\n".join(reply_text_parts) if reply_text_parts else "(no reply)"
    print()
    print("=" * 60)
    print("BOT-A SUMMARY:")
    print("=" * 60)
    print(full)
    print("=" * 60)

    # Sanity: just check the driver got *some* reply. Cap verification
    # happens via post-run log grep, not here.
    if not reply_text_parts:
        print("\n❌ FAIL — no reply at all (driver couldn't reach Bot-A?)")
        return 1
    print("\n✓ Driver completed. Now check Bot-B logs for cap activity:")
    print("    docker exec a2a-bot-b sh -c 'grep \"turn limit\" /opt/data/logs/gateway.log'")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
