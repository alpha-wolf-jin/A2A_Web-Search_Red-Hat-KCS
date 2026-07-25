#!/usr/bin/env python3
"""A2A client for the Healthcare Concierge server.

Connects to the running Healthcare Concierge A2A server and sends a prompt.

Prerequisites:
    - a2a_healthcare_agent.py running (e.g. ``python L10/a2a_healthcare_agent.py``)

Usage:
    python L10/a2a_healthcare_client.py
    python L10/a2a_healthcare_client.py --prompt "Find a cardiologist in Atlanta, GA"
    python L10/a2a_healthcare_client.py --port 9996
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx
from a2a.client import Client, ClientConfig, ClientFactory, create_text_message_object
from a2a.types import AgentCard, Artifact, Message, Task
from a2a.utils.message import get_message_text
from dotenv import load_dotenv


def display_agent_card(card: AgentCard) -> None:
    """Pretty-print an AgentCard to the terminal."""
    print("\n--- Agent Card ---")
    print(f"  Name:             {card.name}")
    print(f"  Description:      {card.description}")
    print(f"  Version:          {card.version}")
    print(f"  URL:              {card.url}")
    print(f"  Protocol Version: {card.protocol_version}")
    if card.skills:
        print("\n  Skills:")
        for skill in card.skills:
            print(f"    - {skill.name}: {skill.description}")
    print("------------------\n")


async def run(host: str, port: str, prompt: str) -> None:
    """Connect to the Healthcare Concierge and print the response."""
    url = f"http://{host}:{port}"

    async with httpx.AsyncClient(timeout=300.0) as httpx_client:
        client: Client = await ClientFactory.connect(
            url,
            client_config=ClientConfig(httpx_client=httpx_client),
        )

        agent_card = await client.get_card()
        display_agent_card(agent_card)

        message = create_text_message_object(content=prompt)
        print(f"Sending prompt: {prompt!r}\n")

        text_content = ""
        async for response in client.send_message(message):
            if isinstance(response, Message):
                text_content = get_message_text(response) or text_content
            elif isinstance(response, tuple):
                task: Task = response[0]
                if task.artifacts:
                    for artifact in task.artifacts:
                        content = get_message_text(artifact)
                        if content:
                            text_content = content
                if task.history:
                    for msg in task.history:
                        content = get_message_text(msg)
                        if content:
                            text_content = content

        print("--- Concierge Interface Response ---")
        if text_content:
            print(text_content)
        else:
            print("No response received from the concierge.")
            text_content = "No response received from the concierge."
        print("--------------------------")

    return text_content

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="A2A client for Healthcare Concierge")
    parser.add_argument("--host", default=os.environ.get("AGENT_HOST", "localhost"))
    parser.add_argument("--port", default=os.environ.get("HEALTHCARE_AGENT_PORT", "9996"))
    parser.add_argument(
        "--prompt",
        default="I'm based in Austin, TX. How do I get mental health therapy near me and what does my insurance cover?",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    try:
        asyncio.run(run(args.host, args.port, args.prompt))
    except KeyboardInterrupt:
        sys.exit(130)
    except httpx.ConnectError as exc:
        print(f"Error: could not connect to concierge at {args.host}:{args.port} — {exc}", file=sys.stderr)
        sys.exit(1)
