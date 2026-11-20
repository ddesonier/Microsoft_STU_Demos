"""Public, read-only frontend for Azure AI Search Knowledge Base retrieval."""

import logging
import os
import threading
import time
from collections import defaultdict, deque

from flask import Flask, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

import answer_generator
import mcp_client

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_host=1, x_proto=1)

SNIPPET_CHARS = 600
MAX_CHAT_HISTORY_ITEMS = 12
CHAT_RATE_LIMIT_PER_MINUTE = int(
    os.environ.get("CHAT_RATE_LIMIT_PER_MINUTE", "10")
)
CHAT_GLOBAL_RATE_LIMIT_PER_MINUTE = int(
    os.environ.get("CHAT_GLOBAL_RATE_LIMIT_PER_MINUTE", "60")
)
_rate_lock = threading.Lock()
_client_requests = defaultdict(deque)
_global_requests = deque()


def plain_snippet(content: str, limit: int = SNIPPET_CHARS) -> str:
    content = (content or "").strip()
    if len(content) <= limit:
        return content
    return content[:limit].rstrip() + "..."


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    return render_template(
        "knowledge.html",
        knowledge_base_name=mcp_client.KNOWLEDGE_BASE_NAME,
        answer_configured=answer_generator.configured(),
    )


def valid_history(value) -> list:
    if not isinstance(value, list):
        return []
    history = []
    for item in value[-MAX_CHAT_HISTORY_ITEMS:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            history.append({"role": role, "content": content.strip()[:4000]})
    return history


def rate_limit_key() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    return (forwarded.split(",", 1)[0].strip() or request.remote_addr or "unknown")[:100]


def rate_limit_status(now: float | None = None) -> tuple[bool, int]:
    now = now if now is not None else time.monotonic()
    cutoff = now - 60
    key = rate_limit_key()
    with _rate_lock:
        while _global_requests and _global_requests[0] <= cutoff:
            _global_requests.popleft()
        client = _client_requests[key]
        while client and client[0] <= cutoff:
            client.popleft()

        if (
            len(_global_requests) >= CHAT_GLOBAL_RATE_LIMIT_PER_MINUTE
            or len(client) >= CHAT_RATE_LIMIT_PER_MINUTE
        ):
            waits = []
            if len(_global_requests) >= CHAT_GLOBAL_RATE_LIMIT_PER_MINUTE:
                waits.append(60 - (now - _global_requests[0]))
            if len(client) >= CHAT_RATE_LIMIT_PER_MINUTE:
                waits.append(60 - (now - client[0]))
            return False, max(1, round(max(waits)))

        _global_requests.append(now)
        client.append(now)
    return True, 0


@app.post("/api/chat")
def chat():
    if not request.is_json:
        return jsonify({"error": "A JSON request body is required."}), 415
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "The JSON request body must be an object."}), 400
    message = payload.get("message")
    if not isinstance(message, str):
        return jsonify({"error": "The message must be a string."}), 400
    query = message.strip()
    if not query:
        return jsonify({"error": "Enter a question."}), 400
    if len(query) > 1000:
        return jsonify({"error": "The question must be 1,000 characters or fewer."}), 400
    if not answer_generator.configured():
        return jsonify({"error": "Grounded answer generation is not configured."}), 503
    allowed, retry_after = rate_limit_status()
    if not allowed:
        response = jsonify(
            {"error": "Too many chat requests. Please wait and try again."}
        )
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        return response

    started = time.perf_counter()
    try:
        history = valid_history(payload.get("history"))
        hits = mcp_client.retrieve(query, history)
        answer, selected = answer_generator.generate_answer(
            query, hits, history
        )
    except Exception:
        app.logger.exception("Grounded chat request failed")
        return jsonify({"error": "The grounded answer request failed. Please try again."}), 502

    sources = [
        {
            "citation_index": hit.get("citation_index"),
            "title": (
                f"{hit.get('title')} — {hit.get('section_title')}"
                if hit.get("title") and hit.get("section_title")
                else hit.get("title") or hit.get("url") or "Untitled source"
            ),
            "url": hit.get("url"),
            "source": hit.get("source"),
            "snippet": plain_snippet(hit.get("content"), 400),
        }
        for hit in selected
    ]
    return jsonify(
        {
            "answer": answer,
            "sources": sources,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=False, port=port)
