#!/usr/bin/env python3

"""Healthcare Concierge — BeeAI orchestrator over A2A agents (DeepSeek).

Uses a BeeAI ``RequirementAgent`` with ``DeepseekChatModel`` to route queries
across three A2A sub-agents: Policy, Research, and Provider.

Modes:
    interactive (default) — send a prompt directly through the concierge
    serve                 — expose the concierge itself as an A2A server
    client                — connect to a running concierge A2A server

Prerequisites:
    - Policy Agent running   (e.g. ``python L4/l4.py`` or the DeepSeek variant)
    - Research Agent running (e.g. ``python L6/l6-deepseek.py``)
    - Provider Agent running (e.g. ``python L8/a2a_provider_agent.py``)

Usage:
    python L10/l10.py
    python L10/l10.py --prompt "What does my plan cover for therapy?"
    python L10/l10.py serve
    python L10/l10.py client --prompt "Find a cardiologist in Atlanta"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

from beeai_framework.adapters.a2a.agents import A2AAgent
from beeai_framework.adapters.a2a.serve.server import A2AServer, A2AServerConfig
from beeai_framework.adapters.deepseek import DeepseekChatModel
from beeai_framework.agents import AgentError
from beeai_framework.agents.requirement import RequirementAgent
from beeai_framework.backend import AnyMessage
from beeai_framework.agents.requirement.requirements.conditional import (
    ConditionalRequirement,
)
from beeai_framework.memory import UnconstrainedMemory
from beeai_framework.middleware.trajectory import EventMeta, GlobalTrajectoryMiddleware
from beeai_framework.serve.utils import LRUMemoryManager
from beeai_framework.tools.handoff import HandoffTool
from beeai_framework.tools.think import ThinkTool
from dotenv import load_dotenv


MAX_ITERATIONS = 36


class ConfiguredRequirementAgent(RequirementAgent):
    """RequirementAgent with a higher default max_iterations.

    The parent ``run()`` is wrapped by ``@runnable_entry`` and returns a ``Run``
    object (not a coroutine).  We must NOT be ``async`` here — just inject the
    default and hand back the same ``Run``.

    We also override ``clone()`` because the base implementation hard-codes
    ``RequirementAgent(...)`` — losing both the ``run()`` override and any
    middlewares injected at construction time would not take effect on the
    correct subclass.
    """

    def run(self, input: str | list[AnyMessage], /, **kwargs):
        kwargs.setdefault("max_iterations", MAX_ITERATIONS)
        return super().run(input, **kwargs)

    async def clone(self) -> "ConfiguredRequirementAgent":
        from beeai_framework.agents.tool_calling.utils import ToolCallChecker

        cloned = ConfiguredRequirementAgent(
            llm=await self._llm.clone(),
            memory=await self._memory.clone(),
            tools=self._tools.copy(),
            requirements=self._requirements.copy(),
            templates=self._templates.model_dump(),
            tool_call_checker=(
                self._tool_call_checker.config.model_copy()
                if isinstance(self._tool_call_checker, ToolCallChecker)
                else self._tool_call_checker
            ),
            save_intermediate_steps=self._save_intermediate_steps,
            final_answer_as_tool=self._final_answer_as_tool,
            name=self._meta.name,
            description=self._meta.description,
            middlewares=self.middlewares.copy(),
        )
        cloned.emitter = await self.emitter.clone()
        cloned.runner_cls = self.runner_cls
        return cloned


class CloneSafeConditionalRequirement(ConditionalRequirement):
    """Workaround for BeeAI bug: ConditionalRequirement retains a stale
    _source_tool reference across runs, causing 'More than one occurrence'
    on the 2nd request when the agent is cloned by A2AServer."""

    async def init(self, *, tools, ctx):
        self._source_tool = None
        await super().init(tools=tools, ctx=ctx)


class ConciseTrajectoryMiddleware(GlobalTrajectoryMiddleware):
    """Trajectory middleware that only logs event names, not payloads."""

    def _format_prefix(self, meta: EventMeta) -> str:
        return super()._format_prefix(meta).rstrip(": ")

    def _format_payload(self, value: Any) -> str:
        return ""


# ---------------------------------------------------------------------------
# Agent construction
# ---------------------------------------------------------------------------

def _init_sub_agent(host: str, port: str, label: str) -> A2AAgent:
    agent = A2AAgent(url=f"http://{host}:{port}", memory=UnconstrainedMemory())
    asyncio.run(agent.check_agent_exists())
    print(f"  + {agent.name} -> {host}:{port}  ({label})")
    return agent


_WEB_SEARCH_TOOL_DESC = (
    "General web search (DuckDuckGo) for any topic. "
    "PRIORITY: Use this agent FIRST when the question is NOT primarily about "
    "Red Hat (e.g. general news, other vendors, hobbies, science unrelated to RHEL). "
    "If the question IS about Red Hat products or support, prefer the Red Hat "
    "knowledge base handoff first instead."
)

_REDHAT_KCS_TOOL_DESC = (
    "Official Red Hat knowledge base (KCS) search via Access Red Hat API. "
    "PRIORITY: Use this agent FIRST when the question is about Red Hat: RHEL, "
    "OpenShift, Satellite, Ansible Automation Platform, subscriptions, RHSM, "
    "support cases, or other Red Hat product/documentation/support content. "
    "For clearly non-Red Hat topics, prefer the general web search handoff first."
)


def build_concierge(
    host: str,
    web_search_port: str,
    redhat_kcs_port: str,
    *,
    verbose: bool = False,
) -> RequirementAgent:
    """Connect to web search and Red Hat KCS sub-agents and assemble the orchestrator."""
    print("Initializing sub-agents:")
    web_agent = _init_sub_agent(host, web_search_port, "web_search")
    kcs_agent = _init_sub_agent(host, redhat_kcs_port, "red_hat_kcs")

    web_search_tool = HandoffTool(
        target=web_agent,
        name=web_agent.name,
        description=f"{web_agent.agent_card.description}\n\n{_WEB_SEARCH_TOOL_DESC}",
    )
    redhat_kcs_tool = HandoffTool(
        target=kcs_agent,
        name=kcs_agent.name,
        description=f"{kcs_agent.agent_card.description}\n\n{_REDHAT_KCS_TOOL_DESC}",
    )

    middlewares = [ConciseTrajectoryMiddleware()] if verbose else []

    concierge = ConfiguredRequirementAgent(
        name="WebSearch Agent",
        description=(
            "Concierge that routes to general web search or Red Hat KCS "
            "based on the topic, then synthesizes an answer with sources."
        ),
        llm=DeepseekChatModel(
            model_id=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            base_url="https://api.deepseek.com",
            allow_parallel_tool_calls=True,
        ),

        tools=[ThinkTool(), web_search_tool, redhat_kcs_tool],
        requirements=[
            CloneSafeConditionalRequirement(ThinkTool, force_at_step=1),
            CloneSafeConditionalRequirement(
                "final_answer",
                only_after=HandoffTool,
                consecutive_allowed=False,
            ),
        ],

        middlewares=middlewares,
        role="Research Concierge",
        instructions=(
            "You coordinate two specialist agents via handoff tools.\n\n"
            "Routing priority (mandatory):\n"
            "- If the user's question is primarily about Red Hat—products "
            "(RHEL, OpenShift, Satellite, Ansible Automation Platform, JBoss, "
            "Quay, etc.), subscriptions, RHSM, support, KCS articles, or "
            "official Red Hat documentation/support—hand off to the agent "
            "named like 'RedHatKCSAgent-DeepSeek' FIRST. Use general web search "
            "only if KCS results are insufficient.\n"
            "- For all other topics (general knowledge, other companies, news, "
            "non-Red Hat software), hand off to the agent named like "
            "'WebSearchAgent-DeepSeek' FIRST. Use the Red Hat KCS agent only "
            "if web search is insufficient or the topic shifts to Red Hat.\n\n"
            "Synthesize a clear answer, cite sources (URLs from the specialist), "
            "and state which agent provided the information."
        ),
    )
    print(f"  = {concierge.meta.name} ready\n")
    return concierge


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


async def run_interactive(concierge: RequirementAgent, prompt: str) -> None:
    """Send a single prompt through the concierge and print the response."""
    print(f"Prompt: {prompt!r}\n")
    try:
        response = await concierge.run(prompt)
        print("--- Concierge Response ---")
        print(response.last_message.text)
        print("--------------------------")
    except AgentError:
        print("--- Concierge Response ---")
        print("I'm sorry, I was unable to fully resolve your request. Please try rephrasing your question.")
        print("--------------------------")


def run_serve(concierge: RequirementAgent, host: str, port: int) -> None:
    """Expose the concierge as an A2A server."""
    print(f"Serving Healthcare Concierge on {host}:{port}")
    A2AServer(
        config=A2AServerConfig(port=port, protocol="jsonrpc", host=host),
        memory_manager=LRUMemoryManager(maxsize=100),
    ).register(concierge, send_trajectory=True).serve()


async def run_client(host: str, port: int, prompt: str) -> None:
    """Connect to a running concierge A2A server and send a prompt."""
    agent = A2AAgent(url=f"http://{host}:{port}", memory=UnconstrainedMemory())
    print(f"Connected to concierge at {host}:{port}")
    print(f"Prompt: {prompt!r}\n")
    response = await agent.run(prompt).middleware(ConciseTrajectoryMiddleware())
    print("--- Concierge Client Response ---")
    print(response.last_message.text)
    print("--------------------------")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Healthcare Concierge — BeeAI + DeepSeek orchestrator"
    )
    parser.add_argument("--host", default=os.environ.get("AGENT_HOST", "localhost"))
    parser.add_argument(
        "--web-search-port",
        default=os.environ.get("WEB_SEARCH_AGENT_PORT", "8080"),
        help="Port for web-search-agent-deepseek.py",
    )
    parser.add_argument(
        "--redhat-kcs-port",
        default=os.environ.get("REDHAT_KCS_AGENT_PORT", "8081"),
        help="Port for redhat-kcs-agent-deepseek.py",
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Print trajectory events to the console")
    parser.add_argument(
        "--prompt",
        #default="Please introduce Singpore O-level exam",
        default="I'm based in Austin, TX. How do I get mental health therapy near me and what does my insurance cover?",
    )

    sub = parser.add_subparsers(dest="mode")
    serve_p = sub.add_parser("serve", help="Run the concierge as an A2A server")
    serve_p.add_argument(
        "--concierge-port",
        type=int,
        default=int(os.environ.get("HEALTHCARE_AGENT_PORT", "9996")),
    )

    client_p = sub.add_parser("client", help="Query a running concierge A2A server")
    client_p.add_argument(
        "--concierge-port",
        type=int,
        default=int(os.environ.get("HEALTHCARE_AGENT_PORT", "9996")),
    )

    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()

    try:
        if args.mode == "client":
            asyncio.run(run_client(args.host, args.concierge_port, args.prompt))
        else:
            concierge = build_concierge(
                args.host,
                args.web_search_port,
                args.redhat_kcs_port,
                verbose=args.verbose,
            )
            if args.mode == "serve":
                run_serve(concierge, args.host, args.concierge_port)
            else:
                asyncio.run(run_interactive(concierge, args.prompt))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
