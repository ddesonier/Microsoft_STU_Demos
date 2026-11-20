"""
On-demand re-crawl + re-index for specific Microsoft Learn URLs.

Backs the Admin tab in webapp.py: each submitted URL is re-fetched, and the
search index document is embedded + upserted only if its content actually
changed (compared via content_hash against what's already indexed). This
never rebuilds or replaces the index -- it's always a targeted upsert via
Azure AI Search's merge_or_upload_documents, same as the bulk crawler uses.
"""

import os
import time
import urllib.parse
from collections.abc import Callable

from bs4 import BeautifulSoup

import blob_store
import configuration
import search_index
import test_crawl

CONTENT_SOURCE_ORIGIN = configuration.value("CONTENT_SOURCE_ORIGIN").rstrip("/")
ALLOWED_HOST = urllib.parse.urlsplit(CONTENT_SOURCE_ORIGIN).netloc
DEFAULT_RECURSIVE_LIMIT = 25
MAX_RECURSIVE_LIMIT = 250
DEFAULT_SITEMAP_LIMIT = 25000
MAX_SITEMAP_LIMIT = 250000
DEFAULT_SITEMAP_CONCURRENCY = 12
MAX_SITEMAP_CONCURRENCY = 20
DEFAULT_SITEMAP_FILTER = (
    configuration.value("SITEMAP_NAME_FILTER") or "azure_en-us"
)
DEFAULT_SITEMAP_PREFIX = (
    f"{CONTENT_SOURCE_ORIGIN}/en-us/azure/" if CONTENT_SOURCE_ORIGIN else ""
)
DEFAULT_SITEMAP_INDEX = (
    configuration.value("SITEMAP_INDEX")
    or (
        f"{CONTENT_SOURCE_ORIGIN}/_sitemaps/sitemapindex.xml"
        if CONTENT_SOURCE_ORIGIN
        else ""
    )
)
_NON_PAGE_SUFFIXES = {
    ".7z", ".avi", ".csv", ".doc", ".docx", ".gif", ".gz", ".jpeg", ".jpg",
    ".json", ".mp3", ".mp4", ".pdf", ".png", ".ppt", ".pptx", ".svg", ".tar",
    ".webp", ".xls", ".xlsx", ".xml", ".zip",
}

# Shared across admin requests so repeated updates don't re-fetch robots.txt.
_robots_cache: dict = {}


def validate_url(url: str):
    """Return an error message if url isn't acceptable to crawl, else None."""
    if not CONTENT_SOURCE_ORIGIN or not ALLOWED_HOST:
        return "CONTENT_SOURCE_ORIGIN is not configured"
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return "not a valid URL"
    if parts.scheme != "https" or parts.netloc != ALLOWED_HOST:
        return f"only https://{ALLOWED_HOST}/... URLs are allowed"
    return None


def validate_sitemap_options(
    sitemap_index: str,
    sitemap_filter: str,
    allowed_prefixes: list[str],
) -> str | None:
    error = validate_url(sitemap_index)
    if error:
        return f"invalid sitemap index: {error}"
    if not sitemap_filter.strip():
        return "a sitemap filter is required to avoid crawling every language and product"
    if not allowed_prefixes:
        return "at least one allowed URL prefix is required"
    for prefix in allowed_prefixes:
        error = validate_url(prefix)
        if error:
            return f"invalid allowed prefix '{prefix}': {error}"
    return None


def update_url(url: str) -> dict:
    """Crawl one URL and update the index only if its content changed."""
    url = url.strip()
    if not url:
        return {"url": url, "status": "skipped", "detail": "empty line"}

    error = validate_url(url)
    if error:
        return {"url": url, "status": "rejected", "detail": error}

    try:
        name, title = test_crawl.crawl_url(url, None, _robots_cache)
    except Exception as e:
        return {"url": url, "status": "failed", "detail": f"crawl failed: {e}"}

    doc = blob_store.load_json(name)

    try:
        result = search_index.index_page_document(doc)
    except Exception as e:
        return {
            "url": url,
            "title": title,
            "status": "failed",
            "detail": f"chunked index update failed: {e}",
        }

    if not result["changed"]:
        return {
            "url": url,
            "title": title,
            "status": "unchanged",
            "detail": (
                f"content identical; {result['chunks']} existing chunk(s) retained"
            ),
        }

    status = "created" if result.get("created") else "updated"
    return {
        "url": url,
        "title": title,
        "status": status,
        "detail": (
            f"embedded and upserted {result['uploaded']} section chunk(s); "
            f"removed {result['deleted']} stale chunk(s)"
        ),
    }


def update_urls(urls: list, on_result: Callable[[dict], None] | None = None) -> list:
    results = []
    for i, url in enumerate(urls):
        result = update_url(url)
        results.append(result)
        if on_result:
            on_result(result)
        if i < len(urls) - 1:
            time.sleep(test_crawl.DELAY)
    return results


def _recursive_scope(seed_url: str) -> str:
    parts = urllib.parse.urlsplit(seed_url)
    path = parts.path.rstrip("/") + "/"
    return urllib.parse.urlunsplit(("https", ALLOWED_HOST, path, "", ""))


def _linked_child_urls(page_url: str, scope: str) -> list[str]:
    if not test_crawl.robots_ok(page_url, _robots_cache):
        return []

    response = test_crawl.session.get(page_url, timeout=test_crawl.TIMEOUT, allow_redirects=True)
    response.raise_for_status()
    scope_path = urllib.parse.urlsplit(scope).path
    discovered = set()
    for link in BeautifulSoup(response.text, "lxml").select("a[href]"):
        absolute = urllib.parse.urljoin(response.url, link["href"])
        parts = urllib.parse.urlsplit(absolute)
        path = parts.path.rstrip("/") or "/"
        suffix = path.rsplit("/", 1)[-1]
        suffix = f".{suffix.rsplit('.', 1)[-1].lower()}" if "." in suffix else ""
        if (
            parts.scheme == "https"
            and parts.netloc == ALLOWED_HOST
            and (path + "/").startswith(scope_path)
            and suffix not in _NON_PAGE_SUFFIXES
        ):
            discovered.add(urllib.parse.urlunsplit(("https", ALLOWED_HOST, path, "", "")))
    return sorted(discovered)


def update_urls_recursive(
    urls: list,
    limit: int = DEFAULT_RECURSIVE_LIMIT,
    on_result: Callable[[dict], None] | None = None,
) -> list:
    """Update seeds and linked descendant pages, bounded to avoid runaway crawls."""
    limit = max(1, min(int(limit), MAX_RECURSIVE_LIMIT))
    results = []
    seen = set()

    for seed in urls:
        scope = _recursive_scope(seed)
        queue = [seed]
        while queue and len(results) < limit:
            url = queue.pop(0)
            normalized = url.rstrip("/")
            if normalized in seen:
                continue
            seen.add(normalized)
            result = update_url(url)
            results.append(result)
            if on_result:
                on_result(result)
            if len(results) >= limit:
                break
            try:
                children = _linked_child_urls(url, scope)
            except Exception as exc:
                result = {
                    "url": url,
                    "status": "failed",
                    "detail": f"child-page discovery failed: {exc}",
                }
                results.append(result)
                if on_result:
                    on_result(result)
                continue
            queue.extend(child for child in children if child.rstrip("/") not in seen)
            if queue:
                time.sleep(test_crawl.DELAY)

    return results


def update_sitemap(
    sitemap_index: str,
    sitemap_filter: str,
    allowed_prefixes: list[str],
    max_pages: int = DEFAULT_SITEMAP_LIMIT,
    concurrency: int = DEFAULT_SITEMAP_CONCURRENCY,
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    """Crawl a filtered sitemap slice, then batch-index changed Blob documents."""
    sitemap_index = sitemap_index.strip()
    sitemap_filter = sitemap_filter.strip()
    allowed_prefixes = [prefix.strip() for prefix in allowed_prefixes if prefix.strip()]
    error = validate_sitemap_options(sitemap_index, sitemap_filter, allowed_prefixes)
    if error:
        raise ValueError(error)

    max_pages = max(1, min(int(max_pages), MAX_SITEMAP_LIMIT))
    concurrency = max(1, min(int(concurrency), MAX_SITEMAP_CONCURRENCY))
    crawl = test_crawl.crawl_sitemap(
        sitemap_index=sitemap_index,
        sitemap_filter=sitemap_filter,
        allowed_prefixes=allowed_prefixes,
        max_pages=max_pages,
        concurrency=concurrency,
        on_progress=on_progress,
    )
    index = search_index.index_crawled_pages(on_progress=on_progress)
    return {"crawl": crawl, "index": index}
