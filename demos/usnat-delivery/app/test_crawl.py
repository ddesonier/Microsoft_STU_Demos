"""
Local test crawler for Microsoft Learn.

Two modes:
  1. If TEST_URLS is non-empty, crawl just those URLs (no sitemap needed).
  2. Otherwise, walk the real Microsoft Learn sitemap index, discover every
     URL under ALLOWED_PREFIXES, and crawl CRAWL_FRACTION of them (capped by
     MAX_PAGES) using a small thread pool.

Writes extracted pages to Azure Blob Storage (see blob_store.py) rather than
local disk, so a crawl can run inside an App Service instance and survive
restarts, scale-out, and redeploys.
"""

import concurrent.futures
import gzip
import hashlib
import math
import os
import re
import time
import urllib.parse
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from urllib3.util.retry import Retry

import blob_store
import configuration

# ---------- CONFIG (edit these) ----------

# Mode 1: direct URL smoke test. Leave non-empty to crawl just these URLs
# instead of running the sitemap-driven mode below.
TEST_URLS = []

# Mode 2: sitemap discovery.
# The approved content source's root sitemap may differ from its public root.
# Configure SITEMAP_INDEX explicitly. Microsoft Learn's index fans out into
# thousands of per-product,
# per-language sub-sitemaps, so only sub-sitemaps whose filename contains
# SITEMAP_NAME_FILTER are fetched (avoids needlessly walking the whole site).
CONTENT_SOURCE_ORIGIN = configuration.value("CONTENT_SOURCE_ORIGIN").rstrip("/")
SITEMAP_INDEX = configuration.value("SITEMAP_INDEX")
SITEMAP_NAME_FILTER = os.environ.get("SITEMAP_NAME_FILTER", "azure_en-us")

# Only URLs starting with one of these prefixes will be crawled in sitemap mode.
ALLOWED_PREFIXES = [
    prefix.strip()
    for prefix in os.environ.get(
        "ALLOWED_PREFIXES",
        f"{CONTENT_SOURCE_ORIGIN}/en-us/azure/" if CONTENT_SOURCE_ORIGIN else "",
    ).split(",")
    if prefix.strip()
]

# Crawl this fraction of all discovered allowed URLs (~82k Azure en-us pages
# as of this writing, so ~20.5k pages), hard-capped by MAX_PAGES regardless.
CRAWL_FRACTION = 0.25
MAX_PAGES = 25000

CONCURRENCY = 6                                         # concurrent page fetches
DELAY     = 0.5                                         # seconds each worker sleeps between its own requests
UA        = "msdocs-test-crawler/0.1 (+your-email@example.com)"
CONNECT_TIMEOUT = int(os.environ.get("CRAWL_CONNECT_TIMEOUT_SECONDS", "10"))
READ_TIMEOUT = int(os.environ.get("CRAWL_READ_TIMEOUT_SECONDS", "120"))
TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)

# Skip URLs already saved to blob storage, so an interrupted large crawl can
# be resumed by simply rerunning the script instead of starting over.
SKIP_EXISTING = True

# After crawling, embed + upload every changed page in blob storage to Azure
# AI Search. See search_index.py for the index schema and Azure resource
# configuration.
INDEX_TO_SEARCH = True

PROGRESS_EVERY = 100                                    # print a progress line every N successful pages
EXTRACTOR_VERSION = 2

# -----------------------------------------

session = requests.Session()
session.headers.update({
    "User-Agent": UA,
    "Accept": "application/xml,text/html,text/xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
})
_retry = Retry(
    total=4,
    connect=4,
    read=4,
    backoff_factor=1,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset({"GET"}),
    respect_retry_after_header=True,
)
_adapter = requests.adapters.HTTPAdapter(
    max_retries=_retry,
    pool_connections=CONCURRENCY,
    pool_maxsize=CONCURRENCY,
)
session.mount("https://", _adapter)
session.mount("http://", _adapter)


def allowed(url: str, prefixes: list[str] | None = None) -> bool:
    return any(url.startswith(p) for p in (prefixes or ALLOWED_PREFIXES))


def robots_ok(url: str, cache: dict) -> bool:
    p = urllib.parse.urlsplit(url)
    origin = f"{p.scheme}://{p.netloc}"
    if origin not in cache:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(origin + "/robots.txt")
        try:
            rp.read()
            cache[origin] = rp
        except Exception:
            # If robots.txt can't be fetched, be conservative and skip.
            return False
    return cache[origin].can_fetch(UA, url)


def fetch_bytes(url: str) -> bytes:
    r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    data = r.content
    if url.endswith(".gz"):
        data = gzip.decompress(data)
    return data


def sitemap_urls(seed: str, seen=None, prefixes: list[str] | None = None):
    """Yield (url, lastmod) for every <url> entry under a sitemap or sitemap index."""
    seen = seen or set()
    if seed in seen:
        return
    seen.add(seed)
    try:
        root = ET.fromstring(fetch_bytes(seed))
    except Exception as e:
        print(f"[sitemap-fail] {seed} -> {e}")
        return

    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    if root.tag == ns + "sitemapindex":
        for loc in root.findall(f".//{ns}loc"):
            if loc.text:
                yield from sitemap_urls(loc.text.strip(), seen, prefixes)
    else:
        for u in root.findall(f".//{ns}url"):
            loc = u.find(ns + "loc")
            lastmod = u.find(ns + "lastmod")
            if loc is not None and loc.text and allowed(loc.text.strip(), prefixes):
                yield loc.text.strip(), (lastmod.text.strip() if lastmod is not None and lastmod.text else None)


def discover_sitemap_seeds(index_url: str, name_filter: str) -> list:
    """Return sub-sitemap URLs from a sitemap index whose filename contains name_filter.

    Avoids recursively walking every sub-sitemap in a huge multi-product,
    multi-language index when we only care about a specific slice of it.
    """
    try:
        root = ET.fromstring(fetch_bytes(index_url))
    except Exception as e:
        print(f"[sitemap-index-fail] {index_url} -> {e}")
        return []
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    if root.tag != ns + "sitemapindex":
        return [index_url]
    return [
        loc.text.strip()
        for loc in root.findall(f".//{ns}loc")
        if loc.text and name_filter in loc.text
    ]


def sitemap_urls_multi(seeds, prefixes: list[str] | None = None):
    seen: set = set()
    for seed in seeds:
        yield from sitemap_urls(seed, seen, prefixes)


def already_crawled(url: str, existing_ids: set) -> bool:
    """Approximate resume check: skip URLs already saved from a prior run.

    Keyed on the sitemap URL itself (not the page's canonical URL, which is
    only known after fetching), so it's a best-effort dedup, not exact.
    existing_ids is fetched once per run via blob_store.list_ids() rather
    than checked per-URL, since that's a single listing call instead of one
    blob-existence round trip per candidate URL.
    """
    candidate_id = hashlib.sha256(url.encode()).hexdigest()
    return candidate_id in existing_ids


def break_long_tokens(text: str, max_len: int = 300) -> str:
    """Insert spaces into pathologically long unbroken runs of non-whitespace
    characters (minified code, encoded blobs, etc.) so Azure Search's
    tokenizer never sees a single term over Lucene's ~32KB term-size limit.
    """
    return re.sub(rf"(\S{{{max_len}}})(?=\S)", r"\1 ", text)


def extract_page(url: str, html: str, source_lastmod=None) -> dict:
    soup = BeautifulSoup(html, "lxml")
    can = soup.find("link", rel="canonical")
    canonical = can.get("href", url) if can else url
    title = soup.title.string.strip() if soup.title and soup.title.string else canonical
    main = soup.find("main") or soup.find("article") or soup.body
    if main is None:
        raise ValueError("No main/article/body found")
    for tag in main.select("script,style,nav,footer,form,noscript,svg,aside"):
        tag.decompose()
    text = "\n".join(l.strip() for l in main.get_text("\n").splitlines() if l.strip())
    if not text:
        raise ValueError("No text content")
    text = break_long_tokens(text)
    headings = [
        h.get_text(" ", strip=True)
        for h in main.find_all(["h1", "h2", "h3", "h4"])
    ]
    sections = extract_sections(main, title)
    return {
        "id": hashlib.sha256(canonical.encode()).hexdigest(),
        "url": url,
        "canonical_url": canonical,
        "title": title,
        "headings": headings,
        "sections": sections,
        "content": text,
        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
        "source_last_modified": source_lastmod,
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        "language": "en-us",
        "source": "Microsoft Learn",
        "extractor_version": EXTRACTOR_VERSION,
    }


def extract_sections(main, page_title: str) -> list[dict]:
    """Preserve heading hierarchy and anchors for section-aware indexing."""
    sections = []
    heading_stack: list[str] = []
    current = {
        "heading": "Overview",
        "heading_path": [page_title],
        "anchor": "",
        "content_parts": [],
    }

    def flush():
        content = "\n".join(current["content_parts"]).strip()
        if content:
            sections.append(
                {
                    "heading": current["heading"],
                    "heading_path": list(current["heading_path"]),
                    "anchor": current["anchor"],
                    "content": break_long_tokens(content),
                }
            )

    block_names = {"p", "pre", "li", "table"}
    for node in main.find_all(["h1", "h2", "h3", "h4", *block_names]):
        if node.name in {"h1", "h2", "h3", "h4"}:
            flush()
            level = int(node.name[1])
            heading = node.get_text(" ", strip=True)
            if not heading:
                continue
            heading_stack = heading_stack[: level - 1]
            heading_stack.append(heading)
            current = {
                "heading": heading,
                "heading_path": list(heading_stack),
                "anchor": node.get("id", ""),
                "content_parts": [],
            }
            continue

        if node.find_parent(list(block_names)) is not None:
            continue
        value = node.get_text("\n" if node.name in {"pre", "table"} else " ", strip=True)
        value = "\n".join(line.strip() for line in value.splitlines() if line.strip())
        if value:
            current["content_parts"].append(value)

    flush()
    return sections


def save_page(url: str, html: str, lastmod) -> str:
    doc = extract_page(url, html, lastmod)
    name = blob_store.save_json(doc)
    return name, doc["title"]


def crawl_url(url: str, lastmod, robots_cache: dict) -> str:
    if not robots_ok(url, robots_cache):
        raise RuntimeError("blocked by robots.txt")
    r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    name, title = save_page(url, r.text, lastmod)
    return name, title


def crawl_one(url: str, lastmod, robots_cache: dict):
    """Thread-pool worker: crawl one page, then pace this worker's next request."""
    result = crawl_url(url, lastmod, robots_cache)
    time.sleep(DELAY)
    return result


def crawl_sitemap(
    sitemap_index: str,
    sitemap_filter: str,
    allowed_prefixes: list[str],
    max_pages: int,
    concurrency: int,
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    """Crawl a bounded sitemap slice concurrently and persist pages to Blob."""
    seeds = discover_sitemap_seeds(sitemap_index, sitemap_filter)
    if not seeds:
        raise ValueError(
            f"No sub-sitemaps matched filter '{sitemap_filter}' under {sitemap_index}."
        )

    if on_progress:
        on_progress({"phase": "discovering", "processed": 0, "total": 0})
    all_urls = list(sitemap_urls_multi(seeds, allowed_prefixes))
    discovered = len(all_urls)
    selected = all_urls[:max_pages]
    existing_ids = blob_store.list_ids(min_extractor_version=EXTRACTOR_VERSION)
    to_crawl = [
        (url, lastmod)
        for url, lastmod in selected
        if not already_crawled(url, existing_ids)
    ]
    skipped = len(selected) - len(to_crawl)
    summary = {
        "discovered": discovered,
        "selected": len(selected),
        "skipped": skipped,
        "processed": 0,
        "crawled": 0,
        "failed": 0,
        "failures": [],
    }
    if on_progress:
        on_progress(
            {
                "phase": "crawling",
                "processed": 0,
                "total": len(to_crawl),
                **summary,
            }
        )
    if not to_crawl:
        return summary

    robots_cache: dict = {}
    robots_ok(to_crawl[0][0], robots_cache)
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(crawl_one, url, lastmod, robots_cache): url
            for url, lastmod in to_crawl
        }
        for future in concurrent.futures.as_completed(futures):
            url = futures[future]
            summary["processed"] += 1
            try:
                future.result()
                summary["crawled"] += 1
            except Exception as exc:
                summary["failed"] += 1
                if len(summary["failures"]) < 100:
                    summary["failures"].append(
                        {
                            "url": url,
                            "status": "failed",
                            "detail": f"crawl failed: {exc}",
                        }
                    )
            if on_progress:
                on_progress(
                    {
                        "phase": "crawling",
                        "total": len(to_crawl),
                        **summary,
                    }
                )
    return summary


def print_summary(processed: int, fetched: int, failed: int) -> None:
    print("\n--- summary ---")
    print(f"processed: {processed}")
    print(f"saved:     {fetched}")
    print(f"failed:    {failed}")
    print(f"output:    blob container '{blob_store.BLOB_CONTAINER_NAME}' ({blob_store.BLOB_ACCOUNT_URL})")

    if not INDEX_TO_SEARCH:
        return
    print("\n[index] embedding + uploading changed pages to Azure AI Search...")
    try:
        import search_index
        stats = search_index.index_crawled_pages()
        print(
            f"[index] uploaded {stats['uploaded']}/{stats['total']} document(s) "
            f"into '{search_index.SEARCH_INDEX_NAME}' "
            f"({stats['failed']} failed, {stats['skipped']} unchanged/skipped)"
        )
    except Exception as e:
        print(f"[index-fail] {e}")


def main():
    robots_cache: dict = {}
    processed = fetched = failed = 0

    # ---------- Mode 1: direct URL smoke test ----------
    if TEST_URLS:
        print(f"[mode] direct URL list ({len(TEST_URLS)} url(s))")
        for url in TEST_URLS[:MAX_PAGES]:
            processed += 1
            try:
                name, title = crawl_url(url, None, robots_cache)
                fetched += 1
                print(f"[ok] {title[:80]}  ->  {name}")
            except Exception as e:
                failed += 1
                print(f"[fail] {url}  ({e})")
            time.sleep(DELAY)

        print_summary(processed, fetched, failed)
        return

    # ---------- Mode 2: sitemap discovery ----------
    seeds = discover_sitemap_seeds(SITEMAP_INDEX, SITEMAP_NAME_FILTER)
    if not seeds:
        print(f"No sub-sitemaps matched filter '{SITEMAP_NAME_FILTER}' under {SITEMAP_INDEX}.")
        return
    print(f"[sitemap] {len(seeds)} matching sub-sitemap(s)")

    all_urls = list(sitemap_urls_multi(seeds))
    discovered = len(all_urls)
    target = min(MAX_PAGES, math.ceil(discovered * CRAWL_FRACTION))
    print(
        f"[scope] discovered {discovered} allowed URL(s) under {ALLOWED_PREFIXES}; "
        f"crawling {target} ({CRAWL_FRACTION:.0%}, capped at {MAX_PAGES})"
    )

    to_crawl = all_urls[:target]
    if SKIP_EXISTING:
        before = len(to_crawl)
        existing_ids = blob_store.list_ids()
        to_crawl = [(u, lm) for (u, lm) in to_crawl if not already_crawled(u, existing_ids)]
        skipped = before - len(to_crawl)
        if skipped:
            print(f"[resume] skipping {skipped} already-crawled page(s)")

    if not to_crawl:
        print("Nothing left to crawl.")
        print_summary(0, 0, 0)
        return

    # Warm the robots.txt cache once up front; it's read-only afterwards, so
    # safe to share across worker threads.
    robots_ok(to_crawl[0][0], robots_cache)

    print(f"[mode] sitemap crawl, {len(to_crawl)} page(s), concurrency={CONCURRENCY}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = {
            pool.submit(crawl_one, url, lastmod, robots_cache): url
            for url, lastmod in to_crawl
        }
        for future in concurrent.futures.as_completed(futures):
            url = futures[future]
            processed += 1
            try:
                future.result()
                fetched += 1
                if fetched % PROGRESS_EVERY == 0 or fetched == len(to_crawl):
                    print(f"[progress] {fetched}/{len(to_crawl)} crawled (failed: {failed})")
            except Exception as e:
                failed += 1
                print(f"[fail] {url}  ({e})")

    print_summary(processed, fetched, failed)


if __name__ == "__main__":
    main()
