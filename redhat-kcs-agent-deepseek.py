#!/usr/bin/env python3

"""A2A Red Hat Knowledge Base Agent — DeepSeek + KCS V2 search.

Searches Red Hat official knowledge base articles (KCS) via the Access
Red Hat API using an offline token, then answers with synthesized text
and citations. Structure matches web-search-agent-deepseek.py.

Requires:
    - REDHAT_OFFLINE_TOKEN in environment (.env)
    - DEEPSEEK_API_KEY

Usage:
    python redhat-kcs-agent-deepseek.py
    python redhat-kcs-agent-deepseek.py --port 8081 --host 0.0.0.0
"""

from __future__ import annotations

import argparse
import json
import os
import time

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2a.utils import new_agent_text_message
from dotenv import load_dotenv
from openai import OpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_deepseek import ChatDeepSeek

from kcsv2 import get_red_hat_access_token, search_v2_kcs, strip_html

SYSTEM_PROMPT = """\
You are a helpful assistant for Red Hat products and support content. When the
user asks a question, use the red_hat_kcs_search tool to find relevant official
Red Hat knowledge base articles and documentation. You may call the tool
multiple times with different queries. Synthesize findings into a clear answer
and cite each source with its title and URL."""

KCS_TOOL = {
    "type": "function",
    "function": {
        "name": "red_hat_kcs_search",
        "description": (
            "Search Red Hat's official knowledge base (KCS articles, solutions, "
            "and related support documentation). Returns titles, URLs, summaries, "
            "and metadata for matching articles."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keywords or phrases for Red Hat content.",
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of articles to return (1–20, default 10).",
                },
            },
            "required": ["query"],
        },
    },
}

MAX_TOOL_ROUNDS = 5
TOKEN_TTL_SEC = 12 * 60  # refresh before 15-minute expiry
MAX_SNIPPET = 500


def _doc_to_entry(doc: dict) -> dict:
    title = doc.get("allTitle") or doc.get("documentTitle") or "No title"
    doc_id = doc.get("id", "")
    view_url = doc.get("view_uri") or (
        f"https://access.redhat.com/solutions/{doc_id}" if doc_id else ""
    )
    abstract = doc.get("abstract") or doc.get("allDescription") or ""
    if abstract:
        abstract = strip_html(abstract)
        if len(abstract) > MAX_SNIPPET:
            abstract = abstract[:MAX_SNIPPET] + "…"
    products = doc.get("product", [])
    if isinstance(products, list):
        products = products[:5]
    return {
        "title": title,
        "id": doc_id,
        "url": view_url,
        "type": doc.get("documentKind", ""),
        "summary": abstract or "",
        "products": products,
        "last_modified": doc.get("lastModifiedDate", ""),
    }


def format_kcs_results_for_llm(raw: object) -> str:
    """Turn KCS API response into compact JSON for the model."""
    if isinstance(raw, str):
        return json.dumps({"error": raw, "results": []}, ensure_ascii=False)
    response = raw.get("response", {}) if isinstance(raw, dict) else {}
    num_found = response.get("numFound", 0)
    docs = response.get("docs", [])
    entries = [_doc_to_entry(d) for d in docs]
    return json.dumps(
        {"total_found": num_found, "returned": len(entries), "articles": entries},
        ensure_ascii=False,
    )


class RedHatKCSAgent:
    """Agent that searches Red Hat KCS via DeepSeek tool calling."""

    def __init__(self) -> None:
        load_dotenv()
        self._offline_token = os.environ["REDHAT_OFFLINE_TOKEN"]
        self.client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
        )
        self.model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        self._access_token: str | None = None
        self._token_at: float = 0.0

        self.llm = ChatDeepSeek(
            model="deepseek-chat",
            temperature=0,
            max_tokens=None,
            timeout=None,
            max_retries=2,
        )
        self._query_rewrite_system = (
            "Rewrite the user's question into a short search query optimized for "
            "Red Hat knowledge base search. Use product names (e.g. RHEL, OpenShift), "
            "version numbers, and error strings when relevant. Output only the query text."
        )
        self._query_rewrite_chain = ChatPromptTemplate(
            [
                ("system", "{system_query_prompt}"),
                ("human", "{input}"),
            ]
        ) | self.llm

    def _ensure_token(self) -> str:
        now = time.time()
        if self._access_token is None or (now - self._token_at) > TOKEN_TTL_SEC:
            self._access_token = get_red_hat_access_token(self._offline_token)
            self._token_at = now
        return self._access_token

    def _kcs_search(self, query: str, num_results: int = 10) -> str:
        num_results = max(1, min(int(num_results), 20))
        token = self._ensure_token()
        raw = search_v2_kcs(token, query, num_results)
        if isinstance(raw, str) and "401" in raw:
            self._access_token = None
            token = self._ensure_token()
            raw = search_v2_kcs(token, query, num_results)
        return format_kcs_results_for_llm(raw)

    def _enhance_query(self, query: str) -> str:
        result = self._query_rewrite_chain.invoke(
            {"system_query_prompt": self._query_rewrite_system, "input": query}
        )
        return result.content

    def answer_query(self, prompt: str) -> str:
        enhanced_query = self._enhance_query(prompt)
        print(f"{'--' * 40}\nEnhanced KCS query:\n{enhanced_query}\n{'--' * 40}")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": enhanced_query},
        ]

        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=[KCS_TOOL],
                tool_choice="auto",
            )
            choice = response.choices[0]

            if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
                messages.append(choice.message)
                for call in choice.message.tool_calls:
                    args = json.loads(call.function.arguments)
                    q = args["query"]
                    n = args.get("num_results", 10)
                    result = self._kcs_search(q, num_results=n)
                    messages.append(
                        {"role": "tool", "tool_call_id": call.id, "content": result}
                    )
            else:
                return choice.message.content or ""

        return messages[-1].get("content", "") if isinstance(messages[-1], dict) else ""


class RedHatKCSAgentExecutor(AgentExecutor):
    def __init__(self) -> None:
        self.agent = RedHatKCSAgent()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        prompt = context.get_user_input()
        response = self.agent.answer_query(prompt)
        await event_queue.enqueue_event(new_agent_text_message(response))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="A2A Red Hat KCS Agent (DeepSeek)")
    parser.add_argument("--host", default=os.environ.get("AGENT_HOST", "localhost"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("REDHAT_KCS_AGENT_PORT", "8081")),
    )
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()

    skill = AgentSkill(
        id="red_hat_kcs",
        name="Red Hat knowledge base search",
        description=(
            "Searches official Red Hat KCS articles and support documentation "
            "and returns synthesized answers with source links."
        ),
        tags=["redhat", "kcs", "support", "documentation", "RHEL"],
        examples=[
            "RHEL 9 firewalld zone configuration",
            "OpenShift image pull backoff ImagePullBackOff",
            "Satellite 6.15 content view publish fails",
        ],
    )

    agent_card = AgentCard(
        name="RedHatKCSAgent-DeepSeek",
        description=(
            "Red Hat official knowledge base search using DeepSeek and the "
            "Access Red Hat KCS V2 API."
        ),
        url=f"http://{args.host}:{args.port}/",
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[skill],
    )

    request_handler = DefaultRequestHandler(
        agent_executor=RedHatKCSAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )

    print(f"Running Red Hat KCS Agent (DeepSeek) on {args.host}:{args.port}")
    uvicorn.run(server.build(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
