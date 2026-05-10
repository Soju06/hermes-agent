#!/usr/bin/env python3
"""ADR-007 v2 streaming mirror live verifier.

Drives Bot-A's AIAgent to send a long-reply request to Bot-B that:
  (a) takes long enough for streaming edits to be visible in Discord
  (b) is substantial (200+ words) so Bot-B's reply triggers many tokens

Runs inside the bot-a container via a2a-sdk client (matches a2a_driver.py
pattern; raw JSON-RPC POST with method='message/send' is wrong for v1.0.2).

Usage:
    docker exec -e BOT_A_URL=http://127.0.0.1:8800/ a2a-bot-a \
        /opt/hermes/.venv/bin/python /tmp/a2a_streaming_driver.py

Streaming UX itself is judged by a HUMAN looking at the configured
Discord mirror channel — this script just kicks the conversation off.
"""
from __future__ import annotations

import asyncio
import os
import sys
import textwrap
import uuid


PROMPT = textwrap.dedent(
    """\
    Hi Bot-A. Please send the following message to peer
    '1502705976233558126' via the send_message tool with platform='a2a'
    and target='1502705976233558126':

    'Write a Python implementation of the Fibonacci sequence with:
     1. A recursive version
     2. An iterative version
     3. A memoized version
     4. A brief explanation of time complexity for each

     Format the answer in markdown with headings and code blocks. Take
     your time — be thorough. The full answer should be at least 200
     words.'

    After you receive Bot-B's reply, quote the FIRST line of Bot-B's
    response verbatim.
    """
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

    print(f"→ Connecting to Bot-A at {BOT_A_URL}", flush=True)
    print(f"→ Prompt sent: {len(PROMPT)} chars (full prompt below)", flush=True)
    print(textwrap.indent(PROMPT, "    "), flush=True)
    print()

    async with httpx.AsyncClient(timeout=240.0) as http:
        resolver = A2ACardResolver(http, BOT_A_URL)
        card = await resolver.get_agent_card()
        print(f"✓ Got agent card: name={card.name!r}", flush=True)

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

        print("→ Sending SendMessageRequest, waiting up to 240s …", flush=True)
        reply_text_parts: list[str] = []
        async for ev in client.send_message(req):
            print(f"  · event: {type(ev).__name__}", flush=True)
            for attr in ("message", "result", "root"):
                obj = getattr(ev, attr, None)
                if obj is None:
                    continue
                parts = getattr(obj, "parts", None)
                if not parts:
                    continue
                for p in parts:
                    text = getattr(p, "text", None)
                    if text:
                        reply_text_parts.append(text)
                break

        full = "\n".join(reply_text_parts)
        print(f"\n→ Bot-A reply ({len(full)} chars):", flush=True)
        print(textwrap.indent(full, "    "), flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
