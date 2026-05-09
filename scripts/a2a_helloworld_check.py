"""One-shot smoke test for a2a-sdk 1.0.2:
spin up a Starlette/uvicorn server with create_jsonrpc_routes +
create_agent_card_routes, hit it with the high-level Client, exit.

API drift notes vs Phase 1 plan (a2a-sdk 1.0.2):
  - No A2AFastAPIApplication. Use Starlette + create_jsonrpc_routes /
    create_agent_card_routes directly.
  - AgentCard has no `url` field; URL lives in `supported_interfaces[].url`.
  - Proto Message uses snake_case (`context_id`, `message_id`).
  - Agent card well-known path is `/.well-known/agent-card.json`.
  - DefaultRequestHandler now requires `agent_card=` (it's the V2 handler).
  - Client.send_message takes a SendMessageRequest, returns AsyncIterator[StreamResponse].
  - Use `a2a.client.minimal_agent_card(url)` to bootstrap the client side.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import threading
import time
import uuid

import httpx
import uvicorn
from starlette.applications import Starlette

from a2a.client import (
    A2ACardResolver,
    Client,
    ClientConfig,
    ClientFactory,
    minimal_agent_card,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Message,
    Part,
    Role,
    SendMessageRequest,
)


HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}/"
RPC_PATH = "/"  # JSON-RPC dispatcher


class EchoExecutor(AgentExecutor):
    """Trivial executor that emits `echo: <input text>` and returns."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        msg = context.message
        text = ""
        if msg is not None and msg.parts:
            for p in msg.parts:
                if p.HasField("text"):
                    text = p.text
                    break

        reply = Message(
            role=Role.ROLE_AGENT,
            parts=[Part(text=f"echo: {text}")],
            message_id=f"echo-{msg.message_id if msg else uuid.uuid4().hex}",
            context_id=msg.context_id if msg else "",
        )
        await event_queue.enqueue_event(reply)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return None


def build_agent_card() -> AgentCard:
    """Construct a minimal valid AgentCard for an HTTP+JSONRPC echo agent."""
    return AgentCard(
        name="EchoBot",
        description="Smoke-test echo agent (a2a-sdk 1.0.2)",
        version="0.0.1",
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="echo",
                name="Echo",
                description="Echoes input text",
                tags=["test"],
                input_modes=["text/plain"],
                output_modes=["text/plain"],
            )
        ],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        supported_interfaces=[
            AgentInterface(
                url=URL.rstrip("/") + RPC_PATH,
                protocol_binding="JSONRPC",
            ),
        ],
    )


def build_app() -> Starlette:
    card = build_agent_card()
    handler = DefaultRequestHandler(
        agent_executor=EchoExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    routes = []
    routes += create_agent_card_routes(card)
    routes += create_jsonrpc_routes(handler, rpc_url=RPC_PATH)
    return Starlette(routes=routes)


async def roundtrip() -> str:
    async with httpx.AsyncClient(timeout=15.0) as http:
        # Resolve the public agent card from the well-known endpoint
        resolver = A2ACardResolver(http, URL)
        card = await resolver.get_agent_card()

        factory = ClientFactory(ClientConfig(httpx_client=http, streaming=False))
        client: Client = factory.create(card)

        request = SendMessageRequest(
            message=Message(
                role=Role.ROLE_USER,
                parts=[Part(text="hello")],
                message_id=uuid.uuid4().hex,
            )
        )

        async for resp in client.send_message(request):
            # StreamResponse is a protobuf with oneof {task, message, ...}
            if resp.HasField("message"):
                msg = resp.message
                for p in msg.parts:
                    if p.HasField("text"):
                        return p.text
            elif resp.HasField("task"):
                task = resp.task
                # Scan task history for the agent reply
                for m in task.history:
                    if m.role == Role.ROLE_AGENT and m.parts:
                        for p in m.parts:
                            if p.HasField("text"):
                                return p.text
        return "<no reply>"


def main() -> int:
    app = build_app()
    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)

    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    # Wait for startup
    deadline = time.time() + 5.0
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        print("ERROR: server did not start", file=sys.stderr)
        return 2

    try:
        reply = asyncio.run(roundtrip())
    except Exception as e:
        print(f"ERROR: roundtrip raised {e!r}", file=sys.stderr)
        with contextlib.suppress(Exception):
            server.should_exit = True
        return 3

    print(f"Reply: {reply}")

    # Clean shutdown
    server.should_exit = True
    t.join(timeout=2.0)

    return 0 if reply == "echo: hello" else 1


if __name__ == "__main__":
    sys.exit(main())
