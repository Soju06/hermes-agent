#!/usr/bin/env python3
"""Phase 3a Task 29 — Concurrent A2A driver (ADR-008 + ADR-010 verify).

Drives Bot-A's AIAgent and Bot-C's AIAgent in parallel. Each receives a
prompt asking it to send a fibonacci implementation request to Bot-B via
the send_message tool. Bot-B receives 2 inbound A2A messages
near-simultaneously and streams responses back to its mirror channel
(per-peer routing under ADR-008; same-channel under Phase 3a sandbox C
mode).

Verifies:
- ADR-008: Bot-B routes Bot-A's reply to Bot-A's mirror_channels entry
  and Bot-C's reply to Bot-C's mirror_channels entry. (Phase 3a C mode:
  both entries point to the SAME channel, so we measure rate-limit
  pressure rather than visual separation.)
- ADR-010 Risk A: Discord per-channel rate limit (5 msg/5s) under
  concurrent stream consumer edits.

Uses a2a-sdk ClientFactory pattern (matches scripts/sandbox/a2a_driver.py
and a2a_streaming_driver.py — raw JSON-RPC POST does NOT work in v1.0.2).

Run inside `a2a-bot-a` container (or any container with the hermes venv):
    docker cp scripts/sandbox/a2a_concurrent_driver.py \\
              a2a-bot-a:/tmp/a2a_concurrent_driver.py
    docker exec a2a-bot-a /opt/hermes/.venv/bin/python \\
              /tmp/a2a_concurrent_driver.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import textwrap
import time
import uuid
from typing import Any


BOT_A_URL = os.environ.get("BOT_A_URL", "http://a2a-bot-a:8800/")
BOT_C_URL = os.environ.get("BOT_C_URL", "http://a2a-bot-c:8802/")
BOT_B_PEER_ID = "1502705976233558126"

PROMPT = textwrap.dedent(
    f"""\
    Hi! Please send the following message to peer
    '{BOT_B_PEER_ID}' via the send_message tool with platform='a2a'
    and target='{BOT_B_PEER_ID}':

    'Write a Python implementation of the Fibonacci sequence with:
     1. A recursive version
     2. An iterative version
     3. A memoized version
     4. A brief explanation of time complexity for each

     Format the answer in markdown with headings and code blocks. Take
     your time — be thorough. The full answer should be at least 200
     words.'

    After you receive the peer's reply, quote the FIRST line of their
    response verbatim.
    """
)


async def drive_one(label: str, peer_url: str) -> dict[str, Any]:
    """Open an A2AClient against `peer_url`, send PROMPT, collect reply text."""
    import httpx
    from a2a.client import A2ACardResolver, ClientConfig, ClientFactory
    from a2a.types import Message, Part, Role, SendMessageRequest

    t0 = time.time()
    reply_text_parts: list[str] = []
    error: str | None = None
    try:
        async with httpx.AsyncClient(timeout=240.0) as http:
            resolver = A2ACardResolver(http, peer_url)
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

            async for ev in client.send_message(req):
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
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    return {
        "label": label,
        "peer_url": peer_url,
        "elapsed_s": round(time.time() - t0, 2),
        "reply_text": "\n".join(reply_text_parts),
        "error": error,
    }


async def main() -> int:
    try:
        import httpx  # noqa: F401
        from a2a.client import A2ACardResolver  # noqa: F401
    except ImportError as exc:
        print(f"FATAL: a2a-sdk + httpx required. {exc}", file=sys.stderr)
        return 2

    print("=" * 70, flush=True)
    print("Phase 3a Task 29 — Concurrent A2A driver", flush=True)
    print("Bot-A + Bot-C send fibonacci request to Bot-B near-simultaneously.", flush=True)
    print("=" * 70, flush=True)
    print(f"  Bot-A URL: {BOT_A_URL}", flush=True)
    print(f"  Bot-C URL: {BOT_C_URL}", flush=True)
    print(f"  Bot-B peer ID: {BOT_B_PEER_ID}", flush=True)
    print(f"  Prompt: {len(PROMPT)} chars", flush=True)
    print(flush=True)

    print("→ Sending in parallel via asyncio.gather …", flush=True)
    t0 = time.time()
    results = await asyncio.gather(
        drive_one("from-A", BOT_A_URL),
        drive_one("from-C", BOT_C_URL),
        return_exceptions=False,
    )
    total = round(time.time() - t0, 2)

    print(f"→ Both done in {total}s.", flush=True)
    print(flush=True)
    for r in results:
        if r["error"]:
            print(f"  ✗ {r['label']} ({r['peer_url']}): {r['error']}", flush=True)
            continue
        full = r["reply_text"]
        print(
            f"  ✓ {r['label']} ({r['peer_url']}) "
            f"elapsed={r['elapsed_s']}s reply_chars={len(full)}",
            flush=True,
        )
        if full:
            preview = full[:300].replace("\n", "\n      ")
            print(f"      {preview}", flush=True)
            print(flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
