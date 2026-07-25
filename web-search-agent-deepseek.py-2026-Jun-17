#!/usr/bin/env python3

"""A2A Web Search Agent — DeepSeek + DuckDuckGo via function calling.

A general-purpose web search agent powered by DeepSeek that uses
function calling to search the web via DuckDuckGo, then wraps
the agent with the A2A SDK.

Requires:
    pip install ddgs

Usage:
    python L6/web-search-agent-deepseek.py
    python L6/web-search-agent-deepseek.py --port 8080 --host 0.0.0.0
"""

from __future__ import annotations

import argparse
import json
import os

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2a.utils import new_agent_text_message
from ddgs import DDGS
from ddgs.exceptions import DDGSException
from dotenv import load_dotenv
from openai import OpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_deepseek import ChatDeepSeek

SYSTEM_PROMPT = """\
You are a helpful web research agent. When the user asks a question, use the
web_search tool to find relevant, up-to-date information on the web. You may
call the tool multiple times with different queries to gather comprehensive
results. Synthesize the findings into a clear, well-organized answer and
cite your sources with URLs."""

SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for any topic. Returns a list of results with title, URL, and snippet.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to look up.",
                },
            },
            "required": ["query"],
        },
    },
}

MAX_TOOL_ROUNDS = 5


def web_search(query: str, max_results: int = 5) -> str:
    """Run a DuckDuckGo search and return results as JSON.

    Returns an empty list JSON and a notice when DuckDuckGo finds no results,
    so the LLM tool-calling loop can retry with a different query.
    """
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return json.dumps(results, ensure_ascii=False)
    except DDGSException as exc:
        print(f"DDGS search failed for '{query}': {exc}")
        return json.dumps(
            {"error": str(exc), "query": query, "results": []},
            ensure_ascii=False,
        )


class WebSearchAgent:
    """General-purpose web search agent powered by DeepSeek."""

    def __init__(self) -> None:
        load_dotenv()
        self.client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
        )
        self.model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

        self.llm = ChatDeepSeek(
            model=self.model,
            temperature=0,
            max_tokens=None,
            timeout=None,
            max_retries=2,
        )
 
        # Prompt that rewrites a user query into a search-optimized form
        self._query_rewrite_system = (
            "Rewrite the prompt to optimize it for internet searching by doing the following.\n\n"
            "- Clarify ambiguous phrases,\n"
            "- use terminology where applicable,\n"
            "- add synonyms that increase the odds of finding matching documents,\n"
            "- remove unnecessary or distracting information,\n"
            "- provide the rephrased query without additional information.\n\n"
            "Example Input:\n"
            "Can I use REHL8 VM for the Ansible Automation Platform 2.5 containerized installation?\n\n"
            "Example Response:\n"
            "Can Red Hat Enterprise Linux 8 (RHEL 8) virtual machine (VM) be used for "
            "containerized installation of Ansible Automation Platform 2.5?"
        )
        self._query_rewrite_chain = ChatPromptTemplate(
            [
                ("system", "{system_query_prompt}"),
                ("human", "{input}"),
            ]
        ) | self.llm

    def _enhance_query(self, query: str) -> str:
        """Use the LLM to rewrite a user query for better web search results."""
        result = self._query_rewrite_chain.invoke(
            {"system_query_prompt": self._query_rewrite_system, "input": query}
        )
        return result.content


    def answer_query(self, prompt: str) -> str:
        enhanced_query = self._enhance_query(prompt)
        print(f"{'--' * 40}\nEnhanced Query:\n{enhanced_query}\n{'--' * 40}")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": enhanced_query},
        ]

        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=[SEARCH_TOOL],
                tool_choice="auto",
                extra_body={"thinking": {"type": "enabled"}}
            )
            choice = response.choices[0]

            if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                messages.append(choice.message)
                for call in choice.message.tool_calls:
                    result = web_search(json.loads(call.function.arguments)["query"])
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": result}
                    )
            else:
                return choice.message.content or ""

        return messages[-1].get("content", "") if isinstance(messages[-1], dict) else ""


class WebSearchAgentExecutor(AgentExecutor):
    """A2A executor wrapping the DeepSeek-based WebSearchAgent."""

    def __init__(self) -> None:
        self.agent = WebSearchAgent()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        prompt = context.get_user_input()
        response = self.agent.answer_query(prompt)
        await event_queue.enqueue_event(new_agent_text_message(response))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="A2A Web Search Agent (DeepSeek)")
    parser.add_argument("--host", default=os.environ.get("AGENT_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WEB_SEARCH_AGENT_PORT", "8080")))
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()


    skill = AgentSkill(
        id="web_search",
        name="Web search",
        description="Searches the web for information on any topic and returns a synthesized answer with sources.",
        tags=["search", "web", "research"],
        examples=[
            "What is the latest news about AI?",
            "How does photosynthesis work?",
            "Best restaurants in Tokyo",
        ],
    )

    agent_card = AgentCard(
        name="WebSearchAgent-DeepSeek",
        description="General-purpose web search agent powered by DeepSeek and DuckDuckGo.",
        url=f"http://{args.host}:{args.port}/",
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[skill],
    )

    request_handler = DefaultRequestHandler(
        agent_executor=WebSearchAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )

    print(f"Running Web Search Agent (DeepSeek) on {args.host}:{args.port}")
    uvicorn.run(server.build(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
