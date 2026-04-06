#!/usr/bin/env python3
"""A2A server for the research concierge (DeepSeek).

Exposes a BeeAI orchestrator that hands off to:
  - web-search-agent-deepseek.py (general web search)
  - redhat-kcs-agent-deepseek.py (Red Hat KCS / official KB)

Prerequisites (run separately):
    - Web search agent: WEB_SEARCH_AGENT_PORT (default 8080)
    - Red Hat KCS agent: REDHAT_KCS_AGENT_PORT (default 8081)

Usage:
    python a2a_web_server.py
    python a2a_web_server.py --concierge-port 9996 --web-search-port 8080 --redhat-kcs-port 8081
"""

from __future__ import annotations

import argparse
import os
import sys

from beeai_framework.adapters.a2a.serve.server import A2AServer, A2AServerConfig
from beeai_framework.serve.utils import LRUMemoryManager
from dotenv import load_dotenv

from web_util import build_concierge


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="A2A research concierge (web search + Red Hat KCS)")
    parser.add_argument("--host", default=os.environ.get("AGENT_HOST", "localhost"))
    parser.add_argument(
        "--web-search-port",
        default=os.environ.get("WEB_SEARCH_AGENT_PORT", "8080"),
        help="Port where web-search-agent-deepseek.py is listening",
    )
    parser.add_argument(
        "--redhat-kcs-port",
        default=os.environ.get("REDHAT_KCS_AGENT_PORT", "8081"),
        help="Port where redhat-kcs-agent-deepseek.py is listening",
    )
    parser.add_argument(
        "--concierge-port",
        type=int,
        default=int(os.environ.get("Concierge_AGENT_PORT", "9996")),
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Print trajectory events to the console")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()

    concierge = build_concierge(
        args.host,
        args.web_search_port,
        args.redhat_kcs_port,
        verbose=args.verbose,
    )

    print(
        f"Serving research concierge A2A on {args.host}:{args.concierge_port} "
        f"(web search -> {args.host}:{args.web_search_port}, "
        f"Red Hat KCS -> {args.host}:{args.redhat_kcs_port})"
    )
    try:
        A2AServer(
            config=A2AServerConfig(
                port=args.concierge_port,
                protocol="jsonrpc",
                host=args.host,
            ),
            memory_manager=LRUMemoryManager(maxsize=100),
        ).register(concierge, send_trajectory=True).serve()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
