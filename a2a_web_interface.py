#!/usr/bin/env python3
"""A2A client for the Research Concierge server.

Connects to the running A2A server and sends a prompt.
Returns the response text (or a descriptive error string).

Usage (standalone):
    python a2a_web_interface.py
    python a2a_web_interface.py --prompt "RHEL 9 firewalld zone config"
    python a2a_web_interface.py --port 9996
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Callable

import httpx
from a2a.client import Client, ClientConfig, ClientFactory, create_text_message_object
from a2a.types import AgentCard, Message, Task
from a2a.utils.message import get_message_text
from dotenv import load_dotenv
import concierge_logger as clog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _best_text_from_task(task: Task) -> str:
    """Extract the most complete text from a Task's artifacts + history."""
    candidate = ""
    if task.artifacts:
        for artifact in task.artifacts:
            t = get_message_text(artifact)
            if t and len(t) > len(candidate):
                candidate = t
    if task.history:
        for msg in task.history:
            t = get_message_text(msg)
            if t and len(t) > len(candidate):
                candidate = t
    return candidate


# ---------------------------------------------------------------------------
# Core async runner
# ---------------------------------------------------------------------------

async def run(
    host: str,
    port: str,
    prompt: str,
    *,
    status_cb: Callable[[str], None] | None = None,
    session: str = "",
) -> str:
    """Connect to the Research Concierge and return the response text.

    Args:
        host:      Hostname of the A2A server.
        port:      Port of the A2A server.
        prompt:    User question / query.
        status_cb: Optional callback invoked with plain-text status updates
                   as the request progresses.  Use this to stream progress
                   messages to the UI.

    Returns:
        The response text, or an error message string (never raises for
        connectivity / timeout / agent-level errors).
    """

    session = session or clog.new_session()

    def _emit(msg: str) -> None:
        if status_cb:
            status_cb(msg)
        clog.log_status(session, "a2a_interface", msg)

    url = f"http://{host}:{port}"
    _emit(f"Connecting to concierge at {url} …")

    try:
        async with httpx.AsyncClient(timeout=300.0) as httpx_client:
            try:
                client: Client = await ClientFactory.connect(
                    url,
                    client_config=ClientConfig(httpx_client=httpx_client),
                )
            except Exception as exc:
                msg = (
                    f"⚠️ Could not connect to the concierge server at {url}.\n"
                    f"Make sure `a2a_web_server.py` is running on port {port}.\n"
                    f"Detail: {exc}"
                )
                _emit(msg)
                return msg

            try:
                agent_card: AgentCard = await client.get_card()
                _emit(f"Connected to **{agent_card.name}** — sending your query …")
            except Exception as exc:
                _emit(f"⚠️ Connected but could not fetch agent card: {exc}")

            message = create_text_message_object(content=prompt)

            text_content = ""
            task_failed = False
            failure_reason = ""

            try:
                async for response in client.send_message(message):
                    if isinstance(response, Message):
                        t = get_message_text(response) or ""
                        if t:
                            if t != text_content:
                                _emit("📨 Received partial response from concierge …")
                            text_content = t

                    elif isinstance(response, tuple):
                        task: Task = response[0]

                        # Surface task status for the UI
                        if hasattr(task, "status") and task.status:
                            state = getattr(task.status, "state", None)
                            if state:
                                state_str = str(state).lower()
                                if "working" in state_str or "submitted" in state_str:
                                    _emit("⚙️  Agent is working …")
                                elif "failed" in state_str or "error" in state_str:
                                    task_failed = True
                                    # Try to pull a message from status
                                    status_msg = getattr(task.status, "message", None)
                                    if status_msg:
                                        t = get_message_text(status_msg)
                                        if t:
                                            failure_reason = t
                                elif "completed" in state_str:
                                    _emit("✅ Agent completed — retrieving answer …")

                        best = _best_text_from_task(task)
                        if best:
                            text_content = best

            except Exception as exc:
                # Stream / iteration error — treat as a soft failure
                err_str = str(exc)
                _emit(f"⚠️ Error while receiving response: {err_str}")
                if not text_content:
                    task_failed = True
                    failure_reason = err_str

            # ----------------------------------------------------------------
            # Build the final answer
            # ----------------------------------------------------------------
            if text_content:
                _emit("✅ Response received.")
                clog.log_concierge_answer(session, text_content, 0)
                return text_content

            if task_failed:
                # Distinguish max-iterations from other errors
                if "max" in failure_reason.lower() and "iter" in failure_reason.lower():
                    fallback = (
                        "⚠️ The agent reached its maximum number of reasoning steps "
                        "without producing a final answer. This can happen with very "
                        "complex or ambiguous queries.\n\n"
                        "**Suggestions:**\n"
                        "- Try rephrasing your question more specifically.\n"
                        "- Break it into smaller sub-questions.\n"
                        "- Add product names / version numbers if this is a Red Hat query.\n\n"
                        f"Technical detail: {failure_reason}"
                    )
                else:
                    fallback = (
                        f"⚠️ The agent reported a failure: {failure_reason or 'unknown error'}.\n\n"
                        "Please try again or rephrase your question."
                    )
                _emit("❌ Agent could not produce a final answer.")
                return fallback

            # No text and no explicit failure — probably empty task
            no_resp = (
                "⚠️ No response was received from the concierge.\n\n"
                "The agents may still be starting up, or the query may have "
                "been routed to a specialist that is not currently running. "
                "Check that all leaf agents (ports 8080 and 8081) are up."
            )
            _emit("❌ No response received.")
            return no_resp

    except httpx.ConnectError as exc:
        msg = (
            f"⚠️ Connection refused at {url}.\n"
            f"Make sure `a2a_web_server.py --concierge-port {port}` is running.\n"
            f"Detail: {exc}"
        )
        _emit(msg)
        return msg

    except httpx.TimeoutException as exc:
        msg = (
            "⚠️ The request timed out (300 s). The concierge or a leaf agent "
            "may be overloaded or unresponsive.\n"
            f"Detail: {exc}"
        )
        _emit(msg)
        return msg

    except Exception as exc:
        msg = f"⚠️ Unexpected error: {type(exc).__name__}: {exc}"
        _emit(msg)
        return msg


# ---------------------------------------------------------------------------
# CLI entry point (unchanged behaviour)
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="A2A client for Research Concierge")
    parser.add_argument("--host", default=os.environ.get("AGENT_HOST", "localhost"))
    parser.add_argument("--port", default=os.environ.get("HEALTHCARE_AGENT_PORT", "9996"))
    parser.add_argument(
        "--prompt",
        default="I'm based in Austin, TX. How do I get mental health therapy near me?",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    try:
        result = asyncio.run(run(args.host, args.port, args.prompt))
        print("\n--- Concierge Response ---")
        print(result)
        print("--------------------------")
    except KeyboardInterrupt:
        sys.exit(130)
