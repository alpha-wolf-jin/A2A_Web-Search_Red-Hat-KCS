#!/usr/bin/env python3

from __future__ import annotations

import json
from flask import Flask, render_template, request, jsonify
import asyncio
import os
import sys

from a2a_web_interface import run

app = Flask(__name__)

def web_search(query: str) -> str:
    """Search DuckDuckGo and return results as a JSON string."""
    try:
        result = asyncio.run(run('localhost', '9996', query))
    except KeyboardInterrupt:
        sys.exit(130)
    except httpx.ConnectError as exc:
        print(f"Error: could not connect to concierge at {args.host}:{args.port} — {exc}", file=sys.stderr)
        sys.exit(1)

    return result

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    user_input = request.form["user_input"]
    response = web_search(user_input)
    return jsonify({"response": response})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=True)
