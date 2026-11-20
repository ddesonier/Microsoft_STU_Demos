"""
Local web UI for testing the Microsoft Learn Azure AI Search index.

Run with:
    python app/webapp.py

Then open http://localhost:5000 in a browser (use "localhost", not
"127.0.0.1" -- it must match the redirect URI registered on the Entra app
for /admin sign-in to work). Requires `az login` (uses the same
DefaultAzureCredential-based clients as search_index.py).

This uses Flask's built-in development server -- fine for local testing, but
not for production. For a real deployment, use `python app/serve.py` instead,
which runs this same app behind waitress (a production-grade WSGI server).

The /admin tab requires Entra ID sign-in (see entra_auth.py); Search and MCP
stay open to anyone who can reach this app. Local dev secrets go in
.env.local (gitignored, see .env.local.example) and are auto-loaded below.
"""

import html
import os
import secrets
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from dotenv import load_dotenv

# Load .env.local (local-dev-only secrets) before importing entra_auth, whose
# module-level constants read these env vars at import time. Resolved
# relative to the repo root (this file's parent's parent) rather than the
# process's cwd, so it's found whether you run this as `python app/webapp.py`
# from the repo root or `python webapp.py` from app/.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_REPO_ROOT, ".env.local"))

from flask import Flask, abort, redirect, render_template, request, url_for  # noqa: E402
from markupsafe import Markup  # noqa: E402
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402

import entra_auth  # noqa: E402
import answer_generator  # noqa: E402
import blob_store  # noqa: E402
import mcp_client  # noqa: E402
import reindex  # noqa: E402
import search_index  # noqa: E402
from azure.search.documents.models import VectorizedQuery  # noqa: E402

app = Flask(__name__)


class _TrustAppServiceProto:
    """Azure App Service Linux terminates TLS at its front door and proxies
    plain HTTP to this container, signaling the original scheme via its own
    X-Appservice-Proto header -- not the more common X-Forwarded-Proto that
    werkzeug's ProxyFix looks for. Without translating it, url_for(
    _external=True) (used to build the Entra redirect_uri) always produces
    an http:// URL here, which doesn't match the https:// redirect URI
    registered on the Entra app, failing sign-in with AADSTS50011. Harmless
    locally/under other proxies -- this header is simply absent there.
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        proto = environ.get("HTTP_X_APPSERVICE_PROTO")
        if proto:
            environ["wsgi.url_scheme"] = proto
        return self.wsgi_app(environ, start_response)


app.wsgi_app = _TrustAppServiceProto(ProxyFix(app.wsgi_app, x_host=1))
# Falls back to a random per-process key if FLASK_SECRET_KEY isn't set, so
# Search/MCP (which don't touch the session) still work without Entra being
# configured yet -- but that also means sessions (and thus admin sign-in)
# won't survive a restart until a real FLASK_SECRET_KEY is set.
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)

MODES = ["hybrid", "semantic", "keyword", "vector"]
TOP_CHOICES = [5, 10, 20]
SNIPPET_CHARS = 300
ADMIN_JOB_HISTORY_LIMIT = 20

_admin_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="admin-index")
_admin_jobs: dict[str, dict] = {}
_admin_jobs_lock = Lock()

# Markers used for our own (non-semantic) highlight pass. Control characters
# are used so they never collide with real page content, and so escaping the
# raw text first (to neutralize any HTML in the crawled content) can't
# accidentally strip them.
HL_OPEN, HL_CLOSE = "\x01", "\x02"


@app.context_processor
def inject_nav_auth():
    """Make the signed-in admin user (if any) available to every template,
    so the navbar can show a Sign in/Sign out link without each route
    having to pass it in explicitly."""
    return {
        "nav_user": entra_auth.current_user(),
        "entra_configured": entra_auth.configured(),
    }


@app.get("/healthz")
def health():
    return {"status": "ok"}


def plain_snippet(content: str, limit: int = SNIPPET_CHARS) -> str:
    content = (content or "").strip()
    if len(content) <= limit:
        return content
    return content[:limit].rstrip() + "..."


def safe_markup(raw: str, open_marker: str, close_marker: str) -> Markup:
    """Escape raw text for safe HTML embedding, then turn our highlight
    markers into real <mark> tags. Escaping happens first so any HTML that
    was part of the crawled page content can't execute in the browser."""
    escaped = html.escape(raw)
    escaped = escaped.replace(html.escape(open_marker), "<mark>")
    escaped = escaped.replace(html.escape(close_marker), "</mark>")
    return Markup(escaped)


def run_search(query: str, mode: str, top: int) -> list:
    search_client = search_index.get_search_client()

    vector_queries = None
    search_text = query
    kwargs = {
        "select": [
            "id", "page_title", "title", "section_title", "url", "source",
            "product", "content",
        ],
        "top": top,
    }

    if mode in ("vector", "hybrid", "semantic"):
        embedding_client = search_index.get_embedding_client()
        vector = search_index.embed_text(embedding_client, query)
        vector_queries = [
            VectorizedQuery(vector=vector, k_nearest_neighbors=top, fields="content_vector")
        ]

    if mode == "vector":
        search_text = None  # vector-only: no keyword/BM25 component

    if mode == "semantic":
        kwargs["query_type"] = "semantic"
        kwargs["semantic_configuration_name"] = search_index.SEMANTIC_CONFIG_NAME
        kwargs["query_caption"] = "extractive"
        kwargs["query_caption_highlight_enabled"] = True
    elif search_text:
        kwargs["highlight_fields"] = "content"
        kwargs["highlight_pre_tag"] = HL_OPEN
        kwargs["highlight_post_tag"] = HL_CLOSE

    results = search_client.search(search_text=search_text, vector_queries=vector_queries, **kwargs)

    hits = []
    for r in results:
        snippet_html = None

        captions = r.get("@search.captions")
        if captions:
            raw = captions[0].highlights or captions[0].text
            if raw:
                snippet_html = safe_markup(raw, "<em>", "</em>")

        if snippet_html is None:
            highlights = (r.get("@search.highlights") or {}).get("content")
            if highlights:
                snippet_html = safe_markup(" … ".join(highlights), HL_OPEN, HL_CLOSE)

        if snippet_html is None:
            snippet_html = Markup(html.escape(plain_snippet(r.get("content"))))

        hits.append({
            "title": r.get("page_title") or r.get("title") or r.get("url"),
            "section_title": r.get("section_title"),
            "url": r.get("url"),
            "source": r.get("source"),
            "product": r.get("product"),
            "score": r.get("@search.score"),
            "reranker_score": r.get("@search.reranker_score"),
            "snippet": snippet_html,
            "content": r.get("content") or "",
        })
    return hits


def get_doc_count():
    try:
        return search_index.get_search_client().get_document_count()
    except Exception:
        return None


@app.route("/", methods=["GET"])
def index():
    query = request.args.get("q", "").strip()
    mode = request.args.get("mode", "hybrid")
    if mode not in MODES:
        mode = "hybrid"
    top = request.args.get("top", "10")
    top = int(top) if top.isdigit() and int(top) in TOP_CHOICES else 10

    results = None
    answer = None
    answer_error = None
    error = None
    elapsed_ms = None
    if query:
        started = time.perf_counter()
        try:
            results = run_search(query, mode, top)
            if results and answer_generator.configured():
                try:
                    answer, results = answer_generator.generate_answer(query, results)
                except Exception:
                    app.logger.exception("Grounded answer generation failed")
                    answer_error = (
                        "Search results were retrieved, but answer generation failed."
                    )
        except Exception:
            app.logger.exception("Search request failed")
            error = "The search request failed. Please try again."
        elapsed_ms = (time.perf_counter() - started) * 1000

    return render_template(
        "index.html",
        active_tab="search",
        query=query,
        mode=mode,
        top=top,
        modes=MODES,
        top_choices=TOP_CHOICES,
        index_name=search_index.SEARCH_INDEX_NAME,
        doc_count=get_doc_count(),
        results=results,
        answer=answer,
        answer_error=answer_error,
        error=error,
        elapsed_ms=elapsed_ms,
    )


@app.route("/mcp", methods=["GET"])
def mcp():
    """Demonstrates Azure AI Search's built-in MCP server: calls the
    knowledge base's native MCP endpoint (knowledge_base_retrieve tool) via
    mcp_client.py, instead of querying the index directly with SearchClient
    like the Search tab does."""
    query = request.args.get("q", "").strip()

    results = None
    answer = None
    answer_error = None
    error = None
    elapsed_ms = None
    if query:
        started = time.perf_counter()
        try:
            hits = mcp_client.retrieve(query)
            if hits and answer_generator.configured():
                try:
                    answer, hits = answer_generator.generate_answer(query, hits)
                except Exception:
                    app.logger.exception("Grounded answer generation failed")
                    answer_error = (
                        "References were retrieved, but answer generation failed."
                    )
            results = [
                {
                    "title": h.get("title") or h.get("url"),
                    "url": h.get("url"),
                    "source": h.get("source"),
                    "section_title": h.get("section_title"),
                    "snippet": Markup(html.escape(plain_snippet(h.get("content")))),
                    "citation_index": h.get("citation_index"),
                }
                for h in hits
            ]
        except Exception:
            app.logger.exception("MCP retrieval failed")
            error = "The Knowledge Base request failed. Please try again."
        elapsed_ms = (time.perf_counter() - started) * 1000

    return render_template(
        "mcp.html",
        active_tab="mcp",
        query=query,
        knowledge_base_name=mcp_client.KNOWLEDGE_BASE_NAME,
        mcp_endpoint=mcp_client.mcp_endpoint(),
        doc_count=get_doc_count(),
        results=results,
        answer=answer,
        answer_error=answer_error,
        error=error,
        elapsed_ms=elapsed_ms,
    )


@app.route("/admin/login")
def admin_login():
    if not entra_auth.configured():
        return (
            "Entra auth isn't configured -- set ENTRA_TENANT_ID, ENTRA_CLIENT_ID, "
            "and ENTRA_CLIENT_SECRET (see .env.local.example).",
            500,
        )
    return redirect(entra_auth.build_auth_url())


@app.route("/admin/auth/callback")
def auth_callback():
    error = entra_auth.handle_callback(request.args)
    if error:
        return f"Login failed: {error}", 401
    return redirect(entra_auth.pop_post_login_redirect() or url_for("admin"))


@app.route("/admin/logout")
def admin_logout():
    return redirect(entra_auth.logout())


@app.route("/admin", methods=["GET", "POST"])
@entra_auth.login_required
def admin():
    submitted_urls = ""
    recursive = False
    recursive_limit = reindex.DEFAULT_RECURSIVE_LIMIT
    crawl_mode = "urls"
    sitemap_index = reindex.DEFAULT_SITEMAP_INDEX
    sitemap_filter = reindex.DEFAULT_SITEMAP_FILTER
    sitemap_prefixes = reindex.DEFAULT_SITEMAP_PREFIX
    sitemap_limit = reindex.DEFAULT_SITEMAP_LIMIT
    sitemap_concurrency = reindex.DEFAULT_SITEMAP_CONCURRENCY
    if request.method == "POST":
        crawl_mode = request.form.get("crawl_mode", "urls")
        submitted_urls = request.form.get("urls", "")
        urls = [u.strip() for u in submitted_urls.splitlines() if u.strip()]
        recursive = request.form.get("recursive") == "on"
        requested_limit = request.form.get("recursive_limit", "")
        if requested_limit.isdigit():
            recursive_limit = max(1, min(int(requested_limit), reindex.MAX_RECURSIVE_LIMIT))
        sitemap_index = request.form.get("sitemap_index", sitemap_index).strip()
        sitemap_filter = request.form.get("sitemap_filter", sitemap_filter).strip()
        sitemap_prefixes = request.form.get("sitemap_prefixes", sitemap_prefixes).strip()
        prefixes = [
            prefix.strip()
            for prefix in sitemap_prefixes.replace(",", "\n").splitlines()
            if prefix.strip()
        ]
        requested_sitemap_limit = request.form.get("sitemap_limit", "")
        if requested_sitemap_limit.isdigit():
            sitemap_limit = max(
                1, min(int(requested_sitemap_limit), reindex.MAX_SITEMAP_LIMIT)
            )
        requested_concurrency = request.form.get("sitemap_concurrency", "")
        if requested_concurrency.isdigit():
            sitemap_concurrency = max(
                1,
                min(
                    int(requested_concurrency),
                    reindex.MAX_SITEMAP_CONCURRENCY,
                ),
            )

        if crawl_mode == "sitemap":
            validation_error = reindex.validate_sitemap_options(
                sitemap_index, sitemap_filter, prefixes
            )
            if validation_error:
                return _render_admin(
                    submitted_urls=submitted_urls,
                    recursive=recursive,
                    recursive_limit=recursive_limit,
                    results=None,
                    crawl_mode=crawl_mode,
                    sitemap_index=sitemap_index,
                    sitemap_filter=sitemap_filter,
                    sitemap_prefixes=sitemap_prefixes,
                    sitemap_limit=sitemap_limit,
                    sitemap_concurrency=sitemap_concurrency,
                    form_error=validation_error,
                )
        elif crawl_mode == "stored":
            urls = []
        elif not urls:
            return _render_admin(
                submitted_urls=submitted_urls,
                recursive=recursive,
                recursive_limit=recursive_limit,
                results=[],
            )

        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "status": "queued",
            "crawl_mode": crawl_mode,
            "submitted_urls": submitted_urls,
            "urls": urls,
            "recursive": recursive,
            "recursive_limit": recursive_limit,
            "sitemap_index": sitemap_index,
            "sitemap_filter": sitemap_filter,
            "sitemap_prefixes": prefixes,
            "sitemap_limit": sitemap_limit,
            "sitemap_concurrency": sitemap_concurrency,
            "total": (
                sitemap_limit
                if crawl_mode == "sitemap"
                else blob_store.count_documents() if crawl_mode == "stored"
                else recursive_limit if recursive else len(urls)
            ),
            "progress": {"phase": "queued", "processed": 0, "total": 0},
            "results": [],
            "summary": None,
            "error": None,
        }
        with _admin_jobs_lock:
            completed_ids = [
                existing_id
                for existing_id, existing in _admin_jobs.items()
                if existing["status"] in {"completed", "failed"}
            ]
            while len(_admin_jobs) >= ADMIN_JOB_HISTORY_LIMIT and completed_ids:
                del _admin_jobs[completed_ids.pop(0)]
            _admin_jobs[job_id] = job
        _admin_executor.submit(_run_admin_job, job_id)
        return redirect(url_for("admin_job", job_id=job_id), code=303)

    return _render_admin(
        submitted_urls=submitted_urls,
        recursive=recursive,
        recursive_limit=recursive_limit,
        results=None,
        crawl_mode=crawl_mode,
        sitemap_index=sitemap_index,
        sitemap_filter=sitemap_filter,
        sitemap_prefixes=sitemap_prefixes,
        sitemap_limit=sitemap_limit,
        sitemap_concurrency=sitemap_concurrency,
    )


def _render_admin(
    submitted_urls: str,
    recursive: bool,
    recursive_limit: int,
    results: list | None,
    job: dict | None = None,
    crawl_mode: str = "urls",
    sitemap_index: str = reindex.DEFAULT_SITEMAP_INDEX,
    sitemap_filter: str = reindex.DEFAULT_SITEMAP_FILTER,
    sitemap_prefixes: str = reindex.DEFAULT_SITEMAP_PREFIX,
    sitemap_limit: int = reindex.DEFAULT_SITEMAP_LIMIT,
    sitemap_concurrency: int = reindex.DEFAULT_SITEMAP_CONCURRENCY,
    form_error: str | None = None,
):
    return render_template(
        "admin.html",
        active_tab="admin",
        index_name=search_index.SEARCH_INDEX_NAME,
        doc_count=get_doc_count(),
        submitted_urls=submitted_urls,
        recursive=recursive,
        recursive_limit=recursive_limit,
        max_recursive_limit=reindex.MAX_RECURSIVE_LIMIT,
        crawl_mode=crawl_mode,
        sitemap_index=sitemap_index,
        sitemap_filter=sitemap_filter,
        sitemap_prefixes=sitemap_prefixes,
        sitemap_limit=sitemap_limit,
        max_sitemap_limit=reindex.MAX_SITEMAP_LIMIT,
        sitemap_concurrency=sitemap_concurrency,
        max_sitemap_concurrency=reindex.MAX_SITEMAP_CONCURRENCY,
        content_source_origin=reindex.CONTENT_SOURCE_ORIGIN,
        results=results,
        job=job,
        form_error=form_error,
    )


def _run_admin_job(job_id: str) -> None:
    with _admin_jobs_lock:
        job = _admin_jobs[job_id]
        job["status"] = "running"
        crawl_mode = job["crawl_mode"]
        urls = list(job["urls"])
        recursive = job["recursive"]
        recursive_limit = job["recursive_limit"]

    def record_result(result: dict) -> None:
        with _admin_jobs_lock:
            stored = _admin_jobs[job_id]
            stored["results"].append(result)
            stored["progress"] = {
                "phase": "indexing",
                "processed": len(stored["results"]),
                "total": stored["total"],
            }

    def record_progress(progress: dict) -> None:
        with _admin_jobs_lock:
            _admin_jobs[job_id]["progress"] = dict(progress)

    try:
        if crawl_mode == "sitemap":
            summary = reindex.update_sitemap(
                sitemap_index=job["sitemap_index"],
                sitemap_filter=job["sitemap_filter"],
                allowed_prefixes=job["sitemap_prefixes"],
                max_pages=job["sitemap_limit"],
                concurrency=job["sitemap_concurrency"],
                on_progress=record_progress,
            )
            mcp_client.ensure_knowledge_base()
            results = summary["crawl"]["failures"]
        elif crawl_mode == "stored":
            index_summary = search_index.index_crawled_pages(
                on_progress=record_progress
            )
            mcp_client.ensure_knowledge_base()
            summary = {"index": index_summary}
            results = []
        elif recursive:
            summary = None
            results = reindex.update_urls_recursive(urls, recursive_limit, record_result)
        else:
            summary = None
            results = reindex.update_urls(urls, record_result)
    except Exception as exc:
        app.logger.exception("Admin indexing job %s failed", job_id)
        with _admin_jobs_lock:
            job = _admin_jobs[job_id]
            job["status"] = "failed"
            job["error"] = str(exc)
    else:
        with _admin_jobs_lock:
            job = _admin_jobs[job_id]
            job["results"] = results
            job["summary"] = summary
            job["status"] = "completed"


@app.get("/admin/jobs/<job_id>")
@entra_auth.login_required
def admin_job(job_id: str):
    with _admin_jobs_lock:
        stored_job = _admin_jobs.get(job_id)
        job = dict(stored_job) if stored_job else None
        if job:
            job["results"] = list(stored_job["results"])
            job["progress"] = dict(stored_job["progress"])
    if job is None:
        abort(404)
    return _render_admin(
        submitted_urls=job["submitted_urls"],
        recursive=job["recursive"],
        recursive_limit=job["recursive_limit"],
        results=job["results"],
        job=job,
        crawl_mode=job["crawl_mode"],
        sitemap_index=job["sitemap_index"],
        sitemap_filter=job["sitemap_filter"],
        sitemap_prefixes="\n".join(job["sitemap_prefixes"]),
        sitemap_limit=job["sitemap_limit"],
        sitemap_concurrency=job["sitemap_concurrency"],
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=True, port=port)
