#!/usr/bin/env python3
"""concierge_logger.py — Structured JSONL logging for the A2A research stack.

Every agent (web-search, redhat-kcs, concierge, web_UI) imports this module
and calls the appropriate log_* function.  All records go to the same rotating
log file so a single grep or analysis script covers the whole request lifecycle.

Log file location (in priority order):
  1. $CONCIERGE_LOG_DIR/<date>/concierge.jsonl
  2. ./logs/<date>/concierge.jsonl

Each record is one JSON object per line (JSONL) with at minimum:
  ts        – ISO-8601 UTC timestamp
  session   – opaque request/session ID (correlates all records for one query)
  agent     – which component wrote the record
  event     – what happened (see EVENT_* constants below)

Run this file directly to tail or analyse an existing log:
  python concierge_logger.py --tail
  python concierge_logger.py --summary logs/2025-01-15/concierge.jsonl
  python concierge_logger.py --session <id> logs/2025-01-15/concierge.jsonl
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Event type constants  (use these as the `event` field value)
# ---------------------------------------------------------------------------

# Concierge / UI layer
EV_QUERY_RECEIVED   = "query_received"     # user hit Send
EV_QUERY_COMPLETE   = "query_complete"     # final answer delivered to user
EV_QUERY_ERROR      = "query_error"        # error path (timeout, max-iter, etc.)
EV_STATUS_UPDATE    = "status_update"      # intermediate status message

# Leaf agent: web search
EV_WEB_QUERY_RAW    = "web_query_raw"      # original user query
EV_WEB_QUERY_ENHANCED = "web_query_enhanced"  # after LLM rewrite
EV_WEB_SEARCH_CALL  = "web_search_call"    # one DuckDuckGo call
EV_WEB_SEARCH_RESULT = "web_search_result" # URLs + snippets returned
EV_WEB_ANSWER       = "web_answer"         # final synthesised answer

# Leaf agent: Red Hat KCS
EV_KCS_QUERY_RAW    = "kcs_query_raw"
EV_KCS_QUERY_ENHANCED = "kcs_query_enhanced"
EV_KCS_SEARCH_CALL  = "kcs_search_call"
EV_KCS_SEARCH_RESULT = "kcs_search_result"
EV_KCS_ANSWER       = "kcs_answer"

# Concierge orchestrator
EV_CONCIERGE_HANDOFF = "concierge_handoff"  # which leaf agent was called
EV_CONCIERGE_ANSWER  = "concierge_answer"   # final text before sending to UI


# ---------------------------------------------------------------------------
# Internal setup
# ---------------------------------------------------------------------------

def _log_dir() -> Path:
    base = os.environ.get("CONCIERGE_LOG_DIR") or "logs"
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    d = Path(base) / date_str
    d.mkdir(parents=True, exist_ok=True)
    return d


def _build_handler() -> RotatingFileHandler:
    log_path = _log_dir() / "concierge.jsonl"
    h = RotatingFileHandler(
        log_path,
        maxBytes=50 * 1024 * 1024,   # 50 MB per file
        backupCount=14,               # keep ~2 weeks at that size
        encoding="utf-8",
    )
    h.setFormatter(logging.Formatter("%(message)s"))
    return h


_logger = logging.getLogger("concierge")
_logger.setLevel(logging.DEBUG)
if not _logger.handlers:
    _logger.addHandler(_build_handler())
    # Also echo to stderr at INFO+ so the terminal isn't silent
    _stderr = logging.StreamHandler(sys.stderr)
    _stderr.setLevel(logging.INFO)
    _stderr.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
    _logger.addHandler(_stderr)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def new_session() -> str:
    """Generate a fresh session/request ID."""
    return uuid.uuid4().hex[:12]


def log(
    event: str,
    agent: str,
    session: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Write one structured JSONL record."""
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session": session,
        "agent": agent,
        "event": event,
    }
    record.update(fields)
    _logger.log(level, json.dumps(record, ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------
# Convenience wrappers (keep call sites tidy)
# ---------------------------------------------------------------------------

def log_query_received(session: str, prompt: str, source_ip: str = "") -> None:
    log(EV_QUERY_RECEIVED, "web_ui", session, prompt=prompt, source_ip=source_ip)


def log_query_complete(
    session: str,
    prompt: str,
    answer: str,
    elapsed_sec: float,
    *,
    answer_len: int | None = None,
) -> None:
    log(
        EV_QUERY_COMPLETE, "web_ui", session,
        prompt=prompt,
        answer=answer,
        answer_len=answer_len if answer_len is not None else len(answer),
        elapsed_sec=round(elapsed_sec, 2),
    )


def log_query_error(
    session: str,
    prompt: str,
    error_msg: str,
    elapsed_sec: float,
    error_type: str = "",
) -> None:
    log(
        EV_QUERY_ERROR, "web_ui", session,
        prompt=prompt,
        error_msg=error_msg,
        error_type=error_type,
        elapsed_sec=round(elapsed_sec, 2),
        level=logging.ERROR,
    )


def log_status(session: str, agent: str, message: str) -> None:
    log(EV_STATUS_UPDATE, agent, session, message=message, level=logging.DEBUG)


def log_web_search_call(
    session: str,
    query: str,
    round_num: int,
    raw_query: str = "",
    enhanced_query: str = "",
) -> None:
    log(
        EV_WEB_SEARCH_CALL, "web_search_agent", session,
        query=query,
        round_num=round_num,
        raw_query=raw_query,
        enhanced_query=enhanced_query,
    )


def log_web_search_result(
    session: str,
    query: str,
    results: list[dict],
    round_num: int,
) -> None:
    # Capture title + URL only (snippets can be huge; keep them short)
    slim = [
        {
            "title": r.get("title", ""),
            "url": r.get("href", r.get("url", "")),
            "snippet": (r.get("body", "") or "")[:200],
        }
        for r in results
    ]
    log(
        EV_WEB_SEARCH_RESULT, "web_search_agent", session,
        query=query,
        round_num=round_num,
        num_results=len(slim),
        results=slim,
    )


def log_web_answer(
    session: str,
    raw_query: str,
    enhanced_query: str,
    answer: str,
    rounds_used: int,
) -> None:
    log(
        EV_WEB_ANSWER, "web_search_agent", session,
        raw_query=raw_query,
        enhanced_query=enhanced_query,
        answer=answer,
        answer_len=len(answer),
        rounds_used=rounds_used,
    )


def log_kcs_search_call(
    session: str,
    query: str,
    num_results: int,
    round_num: int,
    raw_query: str = "",
    enhanced_query: str = "",
) -> None:
    log(
        EV_KCS_SEARCH_CALL, "kcs_agent", session,
        query=query,
        num_results=num_results,
        round_num=round_num,
        raw_query=raw_query,
        enhanced_query=enhanced_query,
    )


def log_kcs_search_result(
    session: str,
    query: str,
    round_num: int,
    total_found: int,
    articles: list[dict],
) -> None:
    slim = [
        {
            "id": a.get("id", ""),
            "title": a.get("title", ""),
            "url": a.get("url", ""),
            "type": a.get("type", ""),
            "products": a.get("products", []),
            "summary": (a.get("summary", "") or "")[:200],
            "last_modified": a.get("last_modified", ""),
        }
        for a in articles
    ]
    log(
        EV_KCS_SEARCH_RESULT, "kcs_agent", session,
        query=query,
        round_num=round_num,
        total_found=total_found,
        num_returned=len(slim),
        articles=slim,
    )


def log_kcs_answer(
    session: str,
    raw_query: str,
    enhanced_query: str,
    answer: str,
    rounds_used: int,
) -> None:
    log(
        EV_KCS_ANSWER, "kcs_agent", session,
        raw_query=raw_query,
        enhanced_query=enhanced_query,
        answer=answer,
        answer_len=len(answer),
        rounds_used=rounds_used,
    )


def log_concierge_handoff(session: str, target_agent: str, prompt: str) -> None:
    log(EV_CONCIERGE_HANDOFF, "concierge", session,
        target_agent=target_agent, prompt=prompt)


def log_concierge_answer(session: str, answer: str, elapsed_sec: float) -> None:
    log(EV_CONCIERGE_ANSWER, "concierge", session,
        answer=answer,
        answer_len=len(answer),
        elapsed_sec=round(elapsed_sec, 2))


# ---------------------------------------------------------------------------
# Analysis helpers (used by the CLI below)
# ---------------------------------------------------------------------------

def _load_records(path: str) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def _sessions_from_records(records: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in records:
        sid = r.get("session", "unknown")
        out.setdefault(sid, []).append(r)
    return out


def cmd_summary(path: str) -> None:
    """Print a per-session summary table."""
    records = _load_records(path)
    sessions = _sessions_from_records(records)
    print(f"\n{'SESSION':<14} {'QUERY':<55} {'STATUS':<12} {'SEC':>6} {'URLS':>5}")
    print("-" * 100)
    for sid, recs in sessions.items():
        prompt = next((r.get("prompt", "") for r in recs if r.get("event") == EV_QUERY_RECEIVED), "")
        complete = next((r for r in recs if r.get("event") == EV_QUERY_COMPLETE), None)
        error    = next((r for r in recs if r.get("event") == EV_QUERY_ERROR), None)
        status   = "ok" if complete else ("error" if error else "partial")
        elapsed  = (complete or error or {}).get("elapsed_sec", "?")
        # count distinct URLs across all search results
        urls: set[str] = set()
        for r in recs:
            for res in r.get("results", []):
                u = res.get("url", "")
                if u:
                    urls.add(u)
            for art in r.get("articles", []):
                u = art.get("url", "")
                if u:
                    urls.add(u)
        q_short = (prompt[:52] + "…") if len(prompt) > 52 else prompt
        print(f"{sid:<14} {q_short:<55} {status:<12} {str(elapsed):>6} {len(urls):>5}")
    print()


def cmd_session(session_id: str, path: str) -> None:
    """Dump all records for one session in readable form."""
    records = _load_records(path)
    matched = [r for r in records if r.get("session") == session_id]
    if not matched:
        print(f"No records found for session {session_id!r}")
        return
    print(f"\n{'='*80}")
    print(f"Session: {session_id}  ({len(matched)} records)")
    print(f"{'='*80}\n")
    for r in matched:
        ts    = r.get("ts", "")[-8:-1]   # HH:MM:SS
        event = r.get("event", "")
        agent = r.get("agent", "")
        print(f"[{ts}] {agent:20s} {event}")

        if event == EV_QUERY_RECEIVED:
            print(f"  QUERY : {r.get('prompt', '')}")

        elif event in (EV_WEB_SEARCH_CALL, EV_KCS_SEARCH_CALL):
            print(f"  SEARCH: {r.get('query', '')}  (round {r.get('round_num')})")
            if r.get("enhanced_query"):
                print(f"  ENHANC: {r.get('enhanced_query', '')}")

        elif event in (EV_WEB_SEARCH_RESULT, EV_KCS_SEARCH_RESULT):
            results = r.get("results") or r.get("articles") or []
            print(f"  FOUND : {r.get('num_results', r.get('num_returned', 0))} / "
                  f"{r.get('total_found', '?')} total")
            for i, res in enumerate(results, 1):
                title = res.get("title", "")[:60]
                url   = res.get("url", "")
                print(f"    [{i}] {title}")
                print(f"        {url}")

        elif event in (EV_WEB_ANSWER, EV_KCS_ANSWER, EV_CONCIERGE_ANSWER):
            answer = r.get("answer", "")
            print(f"  LEN   : {r.get('answer_len', len(answer))} chars, "
                  f"{r.get('rounds_used', '?')} rounds")
            print(f"  ANSWER: {answer[:400]}{'…' if len(answer) > 400 else ''}")

        elif event in (EV_QUERY_COMPLETE, EV_QUERY_ERROR):
            print(f"  ELAPSED: {r.get('elapsed_sec')}s")
            answer = r.get("answer") or r.get("error_msg", "")
            print(f"  FINAL  : {answer[:300]}{'…' if len(answer) > 300 else ''}")

        elif event == EV_CONCIERGE_HANDOFF:
            print(f"  TARGET : {r.get('target_agent')}  prompt={r.get('prompt','')[:80]}")

        print()


def cmd_tail(log_dir: str | None = None) -> None:
    """Find the most recent log file and tail it."""
    import subprocess
    base = log_dir or os.environ.get("CONCIERGE_LOG_DIR") or "logs"
    # find most recent date dir
    dirs = sorted(Path(base).glob("????-??-??"), reverse=True)
    if not dirs:
        print(f"No log dirs found under {base}/")
        return
    log_path = dirs[0] / "concierge.jsonl"
    if not log_path.exists():
        print(f"No log file at {log_path}")
        return
    print(f"Tailing {log_path} …  (Ctrl-C to stop)\n")
    try:
        subprocess.run(["tail", "-f", str(log_path)])
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Analyse concierge log files")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("tail", help="Tail the current log file")

    p_sum = sub.add_parser("summary", help="Per-session summary table")
    p_sum.add_argument("logfile", help="Path to a .jsonl log file")

    p_ses = sub.add_parser("session", help="Dump all records for one session")
    p_ses.add_argument("session_id")
    p_ses.add_argument("logfile")

    args = parser.parse_args()
    if args.cmd == "tail":
        cmd_tail()
    elif args.cmd == "summary":
        cmd_summary(args.logfile)
    elif args.cmd == "session":
        cmd_session(args.session_id, args.logfile)
    else:
        parser.print_help()
