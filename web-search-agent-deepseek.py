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
import concierge_logger as clog
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
web_search tool to find relevant, up-to-date information on the web. Call the
tool at most 3 times, using different queries only when the first result is
clearly insufficient. Once you have enough information — or after 3 searches —
stop searching and synthesize the findings into a clear, well-organized answer
and cite your sources with URLs."""

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


def web_search(query: str, max_results: int = 5, *, session: str = "", round_num: int = 0) -> str:
    """Run a DuckDuckGo search and return results as JSON.

    Returns an empty list JSON and a notice when DuckDuckGo finds no results,
    so the LLM tool-calling loop can retry with a different query.
    """
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if session:
            clog.log_web_search_result(session, query, results, round_num)
        return json.dumps(results, ensure_ascii=False)
    except DDGSException as exc:
        print(f"DDGS search failed for \'{query}\': {exc}")
        if session:
            clog.log_web_search_result(session, query, [], round_num)
        return json.dumps(
            {"error": str(exc), "query": query, "results": []},
            ensure_ascii=False,
        )


def _leaf_thinking_body(env_var: str, default: str = "disabled") -> dict:
    """Return the ``extra_body`` thinking toggle for DeepSeek V4 leaf agents.

    Reads *env_var* from the environment.  Any truthy value (``enabled``,
    ``1``, ``true``, ``yes``, ``on``) enables thinking; everything else
    (including the default ``disabled``) turns it off.

    .. warning::
        Enabling thinking on a tool-calling leaf agent causes the model to
        emit parallel tool calls (inflating search rounds 3-4×) and greatly
        increases context size.  Keep this ``disabled`` unless experimenting.
    """
    mode = (os.environ.get(env_var, default) or default).strip().lower()
    t = "enabled" if mode in ("1", "true", "on", "yes", "enabled") else "disabled"
    return {"thinking": {"type": t}}


class WebSearchAgent:
    """General-purpose web search agent powered by DeepSeek."""

    def __init__(self) -> None:
        load_dotenv()
        self.client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
        )
        # Thinking mode: env var WEB_SEARCH_DEEPSEEK_THINKING (default: disabled)
        self._thinking_extra = _leaf_thinking_body("WEB_SEARCH_DEEPSEEK_THINKING")
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


    def answer_query(self, prompt: str, *, session: str = "") -> str:
        session = session or clog.new_session()
        clog.log(clog.EV_WEB_QUERY_RAW, "web_search_agent", session, prompt=prompt)

        enhanced_query = self._enhance_query(prompt)
        clog.log(clog.EV_WEB_QUERY_ENHANCED, "web_search_agent", session,
                 raw=prompt, enhanced=enhanced_query)
        print(f"{'--' * 40}\nEnhanced Query:\n{enhanced_query}\n{'--' * 40}")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": enhanced_query},
        ]

        round_num = 0
        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=[SEARCH_TOOL],
                tool_choice="auto",
                parallel_tool_calls=False,
                extra_body=self._thinking_extra,
            )
            choice = response.choices[0]

            if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                messages.append(choice.message)
                for call in choice.message.tool_calls:
                    round_num += 1
                    q = json.loads(call.function.arguments)["query"]
                    clog.log_web_search_call(session, q, round_num,
                                             raw_query=prompt, enhanced_query=enhanced_query)
                    result = web_search(q, session=session, round_num=round_num)
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": result}
                    )
            else:
                answer = choice.message.content or ""
                clog.log_web_answer(session, prompt, enhanced_query, answer, round_num)
                return answer

        # MAX_TOOL_ROUNDS exhausted — force one final synthesis call with no tools
        # so the model must write a prose answer instead of returning raw search JSON.
        synth = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            extra_body=self._thinking_extra,
        )
        answer = synth.choices[0].message.content or ""
        clog.log_web_answer(session, prompt, enhanced_query, answer, round_num)
        return answer


class WebSearchAgentExecutor(AgentExecutor):
    """A2A executor wrapping the DeepSeek-based WebSearchAgent."""

    def __init__(self) -> None:
        self.agent = WebSearchAgent()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        prompt = context.get_user_input()
        # Try to reuse a session ID passed in task metadata; otherwise create one
        session = clog.new_session()
        response = self.agent.answer_query(prompt, session=session)
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
