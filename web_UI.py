#!/usr/bin/env python3
"""Flask web UI for the A2A Research Concierge.

Improvements over v1:
  1. Real-time status streaming via Server-Sent Events (SSE) — the user sees
     "Connecting …", "Agent is working …", etc. while waiting.
  2. Graceful error display — max-iteration failures, connection errors, and
     timeouts all produce readable messages in the UI instead of blank output.
  3. Markdown rendering — the concierge response is rendered as Markdown
     (including links and code blocks) in the browser.
  4. Conversation history — the last N exchanges are shown so the user has
     context without reloading.

Usage:
    uv run web_UI.py
    # or
    python web_UI.py
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import sys
import threading
import time
import uuid

from flask import Flask, Response, jsonify, render_template_string, request

from a2a_web_interface import run as a2a_run
import concierge_logger as clog

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONCIERGE_HOST = os.environ.get("AGENT_HOST", "localhost")
CONCIERGE_PORT = os.environ.get("HEALTHCARE_AGENT_PORT", "9996")
HISTORY_LIMIT = 20   # max conversation turns kept in memory

app = Flask(__name__)

# In-process session store: session_id -> list of {role, content}
_sessions: dict[str, list[dict]] = {}

# ---------------------------------------------------------------------------
# HTML template (single-file, no Jinja templates dir required)
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Research Concierge</title>
<!-- Marked.js for Markdown rendering -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/9.1.6/marked.min.js"></script>
<style>
  :root {
    --bg: #0f1117;
    --surface: #1a1d27;
    --surface2: #22263a;
    --accent: #4f8ef7;
    --accent2: #6ee7b7;
    --text: #e2e8f0;
    --text-muted: #8892a4;
    --error: #f87171;
    --warning: #fbbf24;
    --success: #34d399;
    --border: #2d3348;
    --radius: 12px;
    --font: 'Segoe UI', system-ui, sans-serif;
    --font-mono: 'Fira Code', 'Cascadia Code', 'Consolas', monospace;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font);
    font-size: 15px;
    line-height: 1.65;
    display: flex;
    flex-direction: column;
    height: 100vh;
    overflow: hidden;
  }

  /* ── Header ── */
  header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 14px 24px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  header .logo {
    width: 32px; height: 32px;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-weight: 800; font-size: 16px; color: #fff;
    flex-shrink: 0;
  }
  header h1 { font-size: 18px; font-weight: 600; }
  header .subtitle { font-size: 12px; color: var(--text-muted); margin-top: 1px; }
  .status-dot {
    margin-left: auto;
    display: flex; align-items: center; gap: 6px;
    font-size: 12px; color: var(--text-muted);
  }
  .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--text-muted);
    transition: background 0.3s;
  }
  .dot.ready   { background: var(--success); }
  .dot.working { background: var(--warning); animation: pulse 1s infinite; }
  .dot.error   { background: var(--error); }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.3; }
  }

  /* ── Chat window ── */
  #chat-window {
    flex: 1;
    overflow-y: auto;
    padding: 24px;
    display: flex;
    flex-direction: column;
    gap: 20px;
    scroll-behavior: smooth;
  }

  /* Messages */
  .msg-row {
    display: flex;
    gap: 12px;
    align-items: flex-start;
    animation: fadeIn 0.25s ease;
  }
  .msg-row.user  { flex-direction: row-reverse; }

  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(8px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  .avatar {
    width: 34px; height: 34px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 15px; flex-shrink: 0; font-weight: 700;
  }
  .avatar.bot  { background: linear-gradient(135deg, var(--accent), var(--accent2)); color: #fff; }
  .avatar.user { background: var(--surface2); color: var(--text-muted); }

  .bubble {
    max-width: min(680px, 80%);
    padding: 12px 16px;
    border-radius: var(--radius);
    line-height: 1.7;
  }
  .bubble.bot {
    background: var(--surface);
    border: 1px solid var(--border);
    border-top-left-radius: 4px;
  }
  .bubble.user {
    background: var(--accent);
    color: #fff;
    border-top-right-radius: 4px;
  }

  /* Markdown inside bot bubbles */
  .bubble.bot h1, .bubble.bot h2, .bubble.bot h3 {
    margin: 12px 0 6px; font-size: 1em; font-weight: 600;
    color: var(--accent2);
  }
  .bubble.bot p  { margin-bottom: 8px; }
  .bubble.bot ul, .bubble.bot ol { padding-left: 18px; margin-bottom: 8px; }
  .bubble.bot li { margin-bottom: 4px; }
  .bubble.bot a  { color: var(--accent); text-decoration: underline; word-break: break-all; }
  .bubble.bot a:hover { color: var(--accent2); }
  .bubble.bot code {
    background: var(--surface2); padding: 2px 5px; border-radius: 4px;
    font-family: var(--font-mono); font-size: 0.88em; color: var(--accent2);
  }
  .bubble.bot pre {
    background: var(--surface2); border: 1px solid var(--border);
    border-radius: 8px; padding: 12px; overflow-x: auto; margin: 8px 0;
  }
  .bubble.bot pre code { background: none; padding: 0; color: var(--text); }
  .bubble.bot hr { border: none; border-top: 1px solid var(--border); margin: 10px 0; }
  .bubble.bot blockquote {
    border-left: 3px solid var(--accent); padding-left: 10px;
    color: var(--text-muted); margin: 8px 0;
  }
  .bubble.bot strong { color: var(--accent2); }

  /* Error / warning bubbles */
  .bubble.bot.error   { border-color: var(--error);   background: #1f1010; }
  .bubble.bot.warning { border-color: var(--warning);  background: #1f1800; }

  /* Status strip inside a bot bubble */
  .status-strip {
    display: flex; align-items: center; gap: 8px;
    font-size: 13px; color: var(--text-muted);
    padding: 8px 0 4px;
  }
  .status-strip .spinner {
    width: 14px; height: 14px; border-radius: 50%;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    animation: spin 0.8s linear infinite;
    flex-shrink: 0;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── Input bar ── */
  #input-bar {
    flex-shrink: 0;
    padding: 16px 24px;
    background: var(--surface);
    border-top: 1px solid var(--border);
    display: flex; gap: 10px; align-items: flex-end;
  }
  #user-input {
    flex: 1;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--text);
    font-family: var(--font);
    font-size: 14px;
    padding: 10px 14px;
    resize: none;
    min-height: 42px;
    max-height: 180px;
    outline: none;
    transition: border-color 0.2s;
  }
  #user-input:focus  { border-color: var(--accent); }
  #user-input::placeholder { color: var(--text-muted); }

  #send-btn {
    background: var(--accent);
    border: none; border-radius: 10px;
    color: #fff; font-size: 14px; font-weight: 600;
    padding: 10px 22px; cursor: pointer;
    transition: background 0.2s, opacity 0.2s;
    white-space: nowrap;
    height: 42px;
  }
  #send-btn:hover:not(:disabled) { background: #3a76e0; }
  #send-btn:disabled { opacity: 0.45; cursor: default; }

  #clear-btn {
    background: none; border: 1px solid var(--border);
    border-radius: 10px; color: var(--text-muted);
    font-size: 13px; padding: 10px 14px; cursor: pointer;
    transition: border-color 0.2s, color 0.2s;
    height: 42px; white-space: nowrap;
  }
  #clear-btn:hover { border-color: var(--error); color: var(--error); }

  /* Empty state */
  #empty-state {
    flex: 1; display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 16px;
    text-align: center; color: var(--text-muted);
    padding-bottom: 60px;
  }
  #empty-state .big-icon { font-size: 48px; }
  #empty-state h2 { font-size: 20px; color: var(--text); }
  #empty-state p  { font-size: 14px; max-width: 380px; }
  #empty-state .chips { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-top: 8px; }
  .chip {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 20px; padding: 6px 14px; font-size: 13px;
    cursor: pointer; transition: border-color 0.2s, color 0.2s;
  }
  .chip:hover { border-color: var(--accent); color: var(--accent); }
</style>
</head>
<body>

<header>
  <div class="logo">R</div>
  <div>
    <h1>Research Concierge</h1>
    <div class="subtitle">Web search · Red Hat KCS · DeepSeek AI</div>
  </div>
  <div class="status-dot">
    <div class="dot ready" id="hdr-dot"></div>
    <span id="hdr-status">Ready</span>
  </div>
</header>

<div id="chat-window">
  <div id="empty-state">
    <div class="big-icon">🔍</div>
    <h2>What would you like to research?</h2>
    <p>Ask anything — the concierge will route your query to web search or the Red Hat knowledge base automatically.</p>
    <div class="chips">
      <div class="chip" onclick="fillAndSend(this)">RHEL 9 firewalld zone config</div>
      <div class="chip" onclick="fillAndSend(this)">OpenShift ImagePullBackOff error</div>
      <div class="chip" onclick="fillAndSend(this)">Latest AI research news</div>
      <div class="chip" onclick="fillAndSend(this)">Ansible AAP 2.5 containerized install RHEL 8</div>
    </div>
  </div>
</div>

<div id="input-bar">
  <textarea id="user-input" rows="1" placeholder="Ask anything …" autocomplete="off"></textarea>
  <button id="clear-btn" onclick="clearHistory()" title="Clear conversation">🗑 Clear</button>
  <button id="send-btn" onclick="sendMessage()">Send ↑</button>
</div>

<script>
const SESSION_KEY = 'concierge_session_' + Math.random().toString(36).slice(2);
const chatWindow  = document.getElementById('chat-window');
const emptyState  = document.getElementById('empty-state');
const inputEl     = document.getElementById('user-input');
const sendBtn     = document.getElementById('send-btn');
const hdrDot      = document.getElementById('hdr-dot');
const hdrStatus   = document.getElementById('hdr-status');

let busy = false;
let currentBotRow = null;
let currentStatusEl = null;

// ── Markdown renderer ──
marked.setOptions({ breaks: true, gfm: true });

function mdToHtml(text) {
  try { return marked.parse(text); }
  catch(e) { return text.replace(/\n/g, '<br>'); }
}

// ── Auto-resize textarea ──
inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 180) + 'px';
});
inputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

// ── Header dot ──
function setStatus(state, label) {
  hdrDot.className = 'dot ' + state;
  hdrStatus.textContent = label;
}

// ── Append a user bubble ──
function appendUser(text) {
  if (emptyState) emptyState.style.display = 'none';
  const row = document.createElement('div');
  row.className = 'msg-row user';
  row.innerHTML = `
    <div class="avatar user">You</div>
    <div class="bubble user">${escHtml(text)}</div>
  `;
  chatWindow.appendChild(row);
  scrollBottom();
  return row;
}

// ── Start a bot bubble (empty, will be filled) ──
function startBotBubble() {
  const row = document.createElement('div');
  row.className = 'msg-row';
  row.innerHTML = `
    <div class="avatar bot">R</div>
    <div class="bubble bot" id="active-bubble">
      <div class="status-strip" id="active-status">
        <div class="spinner"></div>
        <span id="active-status-text">Connecting …</span>
      </div>
    </div>
  `;
  chatWindow.appendChild(row);
  currentBotRow = row;
  currentStatusEl = row.querySelector('#active-status-text');
  scrollBottom();
  return row;
}

// ── Update the status text inside the active bot bubble ──
function updateStatusText(msg) {
  if (currentStatusEl) currentStatusEl.textContent = msg;
  scrollBottom();
}

// ── Finalise the bot bubble with the real answer ──
function finaliseBubble(html, isError) {
  const bubble = document.getElementById('active-bubble');
  if (!bubble) return;
  bubble.removeAttribute('id');
  if (isError) bubble.classList.add(isError);
  bubble.innerHTML = html;
  currentBotRow = null;
  currentStatusEl = null;
  scrollBottom();
}

function scrollBottom() {
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

function escHtml(str) {
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Detect error/warning prefix from server ──
function classifyResponse(text) {
  if (text.startsWith('⚠️') || text.startsWith('❌')) return 'error';
  return null;
}

// ── Chip shortcut ──
function fillAndSend(el) {
  inputEl.value = el.textContent.trim();
  sendMessage();
}

// ── Clear conversation ──
function clearHistory() {
  while (chatWindow.firstChild) chatWindow.removeChild(chatWindow.firstChild);
  chatWindow.appendChild(emptyState);
  emptyState.style.display = '';
  setStatus('ready', 'Ready');
}

// ── Main send ──
async function sendMessage() {
  if (busy) return;
  const text = inputEl.value.trim();
  if (!text) return;

  busy = true;
  sendBtn.disabled = true;
  setStatus('working', 'Working …');

  inputEl.value = '';
  inputEl.style.height = 'auto';

  appendUser(text);
  startBotBubble();

  // Use SSE endpoint for streaming status
  const params = new URLSearchParams({ q: encodeURIComponent(text) });
  const evtSrc = new EventSource(`/chat_stream?q=${encodeURIComponent(text)}`);

  evtSrc.addEventListener('status', e => {
    updateStatusText(e.data);
  });

  evtSrc.addEventListener('result', e => {
    evtSrc.close();
    const data = JSON.parse(e.data);
    const html = mdToHtml(data.response);
    const cls  = classifyResponse(data.response);
    finaliseBubble(html, cls);
    setStatus('ready', 'Ready');
    busy = false;
    sendBtn.disabled = false;
    inputEl.focus();
  });

  evtSrc.addEventListener('error', e => {
    evtSrc.close();
    const errHtml = mdToHtml(
      '⚠️ **Connection error**: could not reach the server.\n\n' +
      'Make sure `a2a_web_server.py` and the leaf agents are running.'
    );
    finaliseBubble(errHtml, 'error');
    setStatus('error', 'Error');
    busy = false;
    sendBtn.disabled = false;
  });
}
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def home():
    return render_template_string(HTML_TEMPLATE)


@app.route("/chat_stream")
def chat_stream():
    """SSE endpoint.

    Streams status events to the browser while the A2A call runs in a
    background thread, then emits a final `result` event.
    """
    query = request.args.get("q", "")
    if not query:
        def _empty():
            yield "event: result\ndata: {\"response\": \"⚠️ Empty query.\"}\n\n"
        return Response(_empty(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    q: queue.Queue = queue.Queue()

    def _status_cb(msg: str) -> None:
        # Strip leading emoji for the compact header; keep for bubble
        q.put(("status", msg))

    session = clog.new_session()
    clog.log_query_received(session, query, source_ip=request.remote_addr or "")
    _t0 = time.time()

    def _worker():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(
                a2a_run(
                    CONCIERGE_HOST,
                    CONCIERGE_PORT,
                    query,
                    status_cb=_status_cb,
                    session=session,
                )
            )
            loop.close()
            elapsed = time.time() - _t0
            if result.startswith("⚠️") or result.startswith("❌"):
                clog.log_query_error(session, query, result, elapsed,
                                     error_type="agent_error")
            else:
                clog.log_query_complete(session, query, result, elapsed)
        except Exception as exc:
            elapsed = time.time() - _t0
            result = (
                f"⚠️ Unexpected server-side error: {type(exc).__name__}: {exc}\n\n"
                "Please check that all agents are running and try again."
            )
            clog.log_query_error(session, query, result, elapsed,
                                 error_type=type(exc).__name__)
        q.put(("result", result))

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()

    def _generate():
        while True:
            try:
                kind, payload = q.get(timeout=290)
            except queue.Empty:
                # Timeout — emit a timeout result
                timeout_msg = (
                    "⚠️ **Request timed out** (290 s).\n\n"
                    "The concierge or a leaf agent is not responding. "
                    "Check that `a2a_web_server.py`, `web-search-agent-deepseek.py`, "
                    "and `redhat-kcs-agent-deepseek.py` are all running."
                )
                yield f"event: result\ndata: {json.dumps({'response': timeout_msg})}\n\n"
                break

            if kind == "status":
                # Sanitise for SSE (no newlines in data field)
                safe = payload.replace("\n", " ").replace("\r", "")
                yield f"event: status\ndata: {safe}\n\n"
            elif kind == "result":
                yield f"event: result\ndata: {json.dumps({'response': payload})}\n\n"
                break

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering if present
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    host = os.environ.get("UI_HOST", "0.0.0.0")
    port = int(os.environ.get("UI_PORT", "5002"))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() in ("1", "true", "yes")
    print(f"Research Concierge UI → http://{host}:{port}")
    print(f"  Concierge backend : http://{CONCIERGE_HOST}:{CONCIERGE_PORT}")
    app.run(host=host, port=port, debug=debug, threaded=True)
