# A2A Research Concierge

A multi-agent research assistant that routes questions to either a general web search agent or the Red Hat official knowledge base (KCS), then synthesises a single answer. Built with [BeeAI Framework](https://github.com/i-am-bee/beeai-framework), the [A2A SDK](https://github.com/google/a2a-sdk), and DeepSeek.

---

## Architecture

```
Browser
  │
  ▼
web_UI.py  (Flask, port 5002)
  │  SSE status stream
  ▼
a2a_web_interface.py  (A2A client)
  │
  ▼
a2a_web_server.py  ←  web_util.py
  (Concierge, port 9996)
  │ BeeAI RequirementAgent
  ├──────────────────────────────────┐
  ▼                                  ▼
web-search-agent-deepseek.py    redhat-kcs-agent-deepseek.py
  (port 8080)                     (port 8081)
  DuckDuckGo search               Red Hat KCS V2 API
```

All components write structured logs through `concierge_logger.py` to `logs/YYYY-MM-DD/concierge.jsonl`.

---

## Files

| File | Role |
|---|---|
| `web_UI.py` | Flask web UI with real-time SSE status streaming |
| `a2a_web_interface.py` | A2A client; connects to concierge, surfaces errors, streams status callbacks |
| `a2a_web_server.py` | A2A server wrapping the BeeAI concierge orchestrator |
| `web_util.py` | Concierge agent construction: routing logic, handoff tools, BeeAI wiring |
| `web-search-agent-deepseek.py` | Leaf agent: DuckDuckGo web search via DeepSeek tool calling |
| `redhat-kcs-agent-deepseek.py` | Leaf agent: Red Hat KCS V2 API search via DeepSeek tool calling |
| `kcsv2.py` | Red Hat KCS V2 API client (token refresh, search, result formatting) |
| `a2a_web_client.py` | Standalone CLI client for testing the concierge A2A endpoint |
| `concierge_logger.py` | Shared structured JSONL logger + CLI analysis tool |

---

## Prerequisites

- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv) (recommended) or `pip`
- A [DeepSeek API key](https://platform.deepseek.com/)
- A Red Hat offline token (only needed for the KCS agent) — obtain from [https://access.redhat.com/management/api](https://access.redhat.com/management/api)

### Install dependencies

```bash
uv pip install \
  flask \
  httpx \
  python-dotenv \
  openai \
  langchain-core \
  langchain-deepseek \
  ddgs \
  uvicorn \
  a2a-sdk \
  beeai-framework \
  requests
```

---

## Configuration

Create a `.env` file in the project root:

```dotenv
# Required for all agents
DEEPSEEK_API_KEY=sk-...

# Required only for the Red Hat KCS agent
REDHAT_OFFLINE_TOKEN=eyJ...

# Optional overrides (defaults shown)
DEEPSEEK_MODEL=deepseek-v4-flash          # model used by leaf agents
CONCIERGE_DEEPSEEK_MODEL=deepseek-v4-pro  # model used by concierge orchestrator
CONCIERGE_DEEPSEEK_THINKING=disabled      # enabled/disabled (thinking mode for concierge)

# Port configuration
WEB_SEARCH_AGENT_PORT=8080
REDHAT_KCS_AGENT_PORT=8081
Concierge_AGENT_PORT=9996
AGENT_HOST=localhost

# Logging
CONCIERGE_LOG_DIR=logs    # base directory for JSONL log files

# Web UI
UI_HOST=0.0.0.0
UI_PORT=5002
FLASK_DEBUG=false
```

> **Note:** `deepseek-reasoner` is not supported for the concierge orchestrator (it conflicts with forced `tool_choice`). Use `deepseek-v4-flash` or `deepseek-v4-pro` for the concierge.

---

## Running

Start each component in a separate terminal, in this order:

### 1. Web Search Agent

```bash
python web-search-agent-deepseek.py
# or with explicit options:
python web-search-agent-deepseek.py --port 8080 --host localhost
```

### 2. Red Hat KCS Agent

```bash
python redhat-kcs-agent-deepseek.py
# or:
python redhat-kcs-agent-deepseek.py --port 8081 --host localhost
```

> The KCS agent can be omitted if you only need general web search. The concierge will detect it is unreachable and fall back to web search only.

### 3. Concierge A2A Server

```bash
python a2a_web_server.py -v
# or with explicit ports:
python a2a_web_server.py --concierge-port 9996 --web-search-port 8080 --redhat-kcs-port 8081 -v
```

The `-v` flag prints BeeAI trajectory events (Think → HandOff → FinalAnswer steps) to the terminal, which is useful for debugging routing decisions.

To require both leaf agents to be reachable (fail fast if either is down):

```bash
python a2a_web_server.py --require-all-agents
```

### 4. Web UI

```bash
uv run web_UI.py
# or:
python web_UI.py
```

Open [http://localhost:5002](http://localhost:5002) in your browser.

---

## CLI Client (no browser)

Send a prompt directly to the concierge and print the response:

```bash
python a2a_web_client.py --prompt "RHEL 9 firewalld zone configuration"
python a2a_web_client.py --prompt "OpenShift ImagePullBackOff error" --port 9996
```

---

## Routing Logic

The concierge (`web_util.py`) routes based on topic:

| Query topic | Routed to first |
|---|---|
| Red Hat products (RHEL, OpenShift, Satellite, AAP, RHSM, KCS) | `redhat-kcs-agent-deepseek.py` |
| All other topics (general news, other vendors, science, etc.) | `web-search-agent-deepseek.py` |

If the first agent's results are insufficient, the concierge may call the second agent. Each agent can be called at most **twice** per query (controlled by the handoff budget in `web_util.py`).

### Query enhancement

Both leaf agents rewrite the user's raw query with a second LLM call before searching. This expands abbreviations, adds synonyms, and adds product/version context. The original and enhanced queries are both captured in the logs.

---

## Logging

All components write to a single rotating JSONL file:

```
logs/
└── YYYY-MM-DD/
    └── concierge.jsonl   # one JSON object per line
```

Every log record contains at minimum:

| Field | Description |
|---|---|
| `ts` | ISO-8601 UTC timestamp |
| `session` | 12-character hex ID tying all records for one query together |
| `agent` | Which component wrote the record (`web_ui`, `web_search_agent`, `kcs_agent`, `a2a_interface`, `concierge`) |
| `event` | What happened (see event types below) |

### Event types

| Event | Agent | Key extra fields |
|---|---|---|
| `query_received` | web_ui | `prompt`, `source_ip` |
| `query_complete` | web_ui | `prompt`, `answer`, `elapsed_sec`, `answer_len` |
| `query_error` | web_ui | `prompt`, `error_msg`, `error_type`, `elapsed_sec` |
| `web_query_raw` | web_search_agent | `prompt` |
| `web_query_enhanced` | web_search_agent | `raw`, `enhanced` |
| `web_search_call` | web_search_agent | `query`, `round_num` |
| `web_search_result` | web_search_agent | `query`, `round_num`, `num_results`, `results[]` (title, url, snippet) |
| `web_answer` | web_search_agent | `raw_query`, `enhanced_query`, `answer`, `rounds_used` |
| `kcs_query_raw` | kcs_agent | `prompt` |
| `kcs_query_enhanced` | kcs_agent | `raw`, `enhanced` |
| `kcs_search_call` | kcs_agent | `query`, `num_results`, `round_num` |
| `kcs_search_result` | kcs_agent | `query`, `round_num`, `total_found`, `num_returned`, `articles[]` (id, title, url, products, summary) |
| `kcs_answer` | kcs_agent | `raw_query`, `enhanced_query`, `answer`, `rounds_used` |
| `concierge_answer` | concierge | `answer`, `answer_len`, `elapsed_sec` |
| `status_update` | a2a_interface | `message` |

### Log analysis CLI

`concierge_logger.py` can be run directly for log analysis:

```bash
# Live tail of the current day's log
python concierge_logger.py tail

# Per-session summary table: query, status, elapsed time, URL count
python concierge_logger.py summary logs/2025-01-15/concierge.jsonl

# Full drill-down on one session (all events in order)
python concierge_logger.py session abc123def456 logs/2025-01-15/concierge.jsonl
```

### Diagnosing poor synthesis quality

The logs are structured to answer specific questions about why an answer was poor:

| Symptom | What to check in the log |
|---|---|
| Answer is irrelevant to the query | Compare `web_query_raw` vs `web_query_enhanced` — the rewriter may have changed the intent |
| Answer ignores the best URLs | Check `web_search_result.results[].url` — were the right pages actually returned? |
| URLs were good but answer is thin | The synthesis prompt is the problem; the raw material was available but not used well |
| `rounds_used: 1` on a complex query | The agent stopped searching too quickly |
| KCS answer is weak | Check `kcs_search_result.total_found` vs `num_returned` — it may be working with too few articles |
| Concierge answer is shorter than leaf answer | The orchestrator truncated or discarded the leaf's output; compare `kcs_answer.answer` vs `concierge_answer.answer` |
| `query_error` with max-iteration detail | The concierge ran out of steps; try a more specific or shorter query |

### Example: grep for all URLs seen in a session

```bash
# All URLs returned by web search for session abc123
grep '"session": "abc123"' logs/2025-01-15/concierge.jsonl \
  | python -c "
import sys, json
for line in sys.stdin:
    r = json.loads(line)
    for res in r.get('results', []) + r.get('articles', []):
        u = res.get('url') or res.get('href', '')
        if u: print(u)
"
```

---

## Troubleshooting

**Concierge fails to start with "No sub-agents are reachable"**
At least one leaf agent must be running before starting `a2a_web_server.py`. Start `web-search-agent-deepseek.py` first.

**KCS agent returns 401 errors**
Your `REDHAT_OFFLINE_TOKEN` has expired or is invalid. Generate a new one at [https://access.redhat.com/management/api](https://access.redhat.com/management/api).

**Web UI shows a blank response**
Check the concierge terminal for `AgentError` or max-iteration messages. The UI now shows a descriptive error message for these cases. If the bubble is still blank, check that `a2a_web_server.py` is running on port 9996.

**`deepseek-reasoner does not support this tool_choice` error**
Set `CONCIERGE_DEEPSEEK_MODEL=deepseek-v4-pro` or `deepseek-v4-flash` in your `.env`. The `deepseek-reasoner` model is incompatible with the concierge's tool-calling orchestration.

**Very slow responses**
The concierge can make up to 12 reasoning steps (`MAX_ITERATIONS` in `web_util.py`), each of which may call a leaf agent that in turn makes multiple LLM calls. For faster responses, reduce `MAX_ITERATIONS` or `MAX_TOOL_ROUNDS` in the leaf agents (default: 5).

---

## Development Notes

### Adding a new leaf agent

1. Create a new A2A agent following the pattern in `web-search-agent-deepseek.py`.
2. Add `import concierge_logger as clog` and instrument it with the appropriate `clog.log_*` calls.
3. In `web_util.py`, add a new `_init_sub_agent()` call in `build_concierge()` and add a corresponding `HandoffTool` with routing instructions.
4. Add the new port to `a2a_web_server.py` CLI args and `.env`.

### Changing the concierge model

```dotenv
CONCIERGE_DEEPSEEK_MODEL=deepseek-v4-flash   # faster, cheaper
CONCIERGE_DEEPSEEK_MODEL=deepseek-v4-pro     # default, better reasoning
```

Do not use `deepseek-reasoner` for the concierge (tool-choice incompatibility).

### Log rotation

Logs rotate at 50 MB per file with 14 backups kept (`concierge_logger.py`, `_build_handler()`). Daily directories are created automatically. Old directories are not auto-deleted; prune them manually or with a cron job if disk space is a concern.
