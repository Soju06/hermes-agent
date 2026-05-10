#!/usr/bin/env python3
"""Sandbox A2A E2E driver — drive Bot-A on localhost:9800 with an
external A2A request and observe the reply path.

Usage (from host, with sandbox compose up):
    cd ~/projects/hermes-agent-a2a
    .venv/bin/python scripts/sandbox/a2a_driver.py

Expected behavior:
    1. POST a SendMessageRequest to Bot-A asking it to ping Bot-B.
    2. Bot-A's AIAgent calls send_message(platform=a2a, target=bot-b).
    3. A2AAdapter.send() routes to a2a-bot-b:8801 inside the sandbox network.
    4. Bot-B's HermesA2AExecutor wakes its AIAgent.
    5. Bot-B replies; capture wrapper round-trips through a2a-sdk reply channel.
    6. Bot-A's send_message receives raw_response = Bot-B's reply text.
    7. Bot-A composes its final answer to the driver and we see Bot-B's
       text quoted inside it.

This validates the full reply path that 2026-05-09 E2E-A could not finish.
"""
from __future__ import annotations

import asyncio
import os
import sys
import textwrap
import uuid

PROMPT = (
    "Hi Bot-A. Please send the following message to peer "
    "'1502705976233558126' via the send_message tool with platform='a2a' and "
    "target='1502705976233558126': "
    "'Bot-B, please reply with exactly: HELLO_FROM_BOT_B (a unique token "
    "we use to verify the reply path).' "
    "After you receive Bot-B's reply, quote it back to me verbatim "
    "in your response."
)

BOT_A_URL = os.environ.get("BOT_A_URL", "http://127.0.0.1:9800/")
SENTINEL = "HELLO_FROM_BOT_B"


async def main() -> int:
    try:
        import httpx
        from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
        from a2a.types import Message, Part, Role, SendMessageRequest
    except ImportError as exc:
        print(f"FATAL: a2a-sdk + httpx required. Install in venv. {exc}",
              file=sys.stderr)
        return 2

    print(f"→ Connecting to Bot-A at {BOT_A_URL}")
    print(f"→ Prompt: {textwrap.shorten(PROMPT, 100)}")
    print()

    async with httpx.AsyncClient(timeout=180.0) as http:
        resolver = A2ACardResolver(http, BOT_A_URL)
        card = await resolver.get_agent_card()
        print(f"✓ Got agent card: name={card.name!r}")

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

        print("→ Sending SendMessageRequest …")
        reply_text_parts: list[str] = []
        async for ev in client.send_message(req):
            # ev is a tuple of (Task, UpdateEvent) per a2a-sdk 1.0.x;
            # for non-streaming the iterator yields once with the final reply.
            print(f"  · event: {type(ev).__name__}")
            # Try common shapes:
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
            # Tuple shape (Task, UpdateEvent)
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

    print()
    full_reply = "\n---\n".join(reply_text_parts) if reply_text_parts \
                 else "(no text parts extracted from reply)"
    print("=" * 60)
    print("BOT-A REPLY:")
    print("=" * 60)
    print(full_reply)
    print("=" * 60)
    print()

    # Verdict: does Bot-A's reply contain the sentinel that only Bot-B
    # could have produced?
    if SENTINEL in full_reply:
        print(f"✅ PASS — sentinel {SENTINEL!r} present in Bot-A's reply.")
        print("   Reply path Bot-A → Bot-B → Bot-A is live-verified.")
        return 0
    else:
        print(f"❌ FAIL — sentinel {SENTINEL!r} NOT in Bot-A's reply.")
        print("   Either Bot-B never replied, the capture wrapper didn't")
        print("   fire, the reply didn't propagate, or Bot-A's AIAgent")
        print("   chose not to quote it. Check container logs:")
        print("     docker logs a2a-bot-a --tail 50")
        print("     docker logs a2a-bot-b --tail 50")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
