"""Build a public-to-USNat URL map from the Microsoft Learn sitemap tree."""

import argparse
import csv
import gzip
import os
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_SITEMAP_INDEX = os.environ.get("SITEMAP_INDEX", "")
SITEMAP_NAMESPACE = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
SOURCE_HOST = os.environ.get("CONTENT_SOURCE_HOST", "").strip().lower()


@dataclass(frozen=True)
class SitemapEntry:
    source_url: str
    last_modified: str
    sitemap_url: str


def normalize_source_url(url: str) -> str:
    value = url.strip()
    while value.lower().startswith("https://https://"):
        value = value[len("https://") :]
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid HTTP(S) URL: {url}")
    return value


def normalize_target_origin(origin: str) -> str:
    parsed = urlsplit(origin.strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("USNat target origin must be an HTTPS origin")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("USNat target origin cannot contain a path, query, or fragment")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def convert_url(source_url: str, target_origin: str) -> str:
    if not SOURCE_HOST:
        raise RuntimeError("CONTENT_SOURCE_HOST is required for URL conversion")
    source = urlsplit(source_url)
    if source.hostname and source.hostname.lower() != SOURCE_HOST:
        return source_url
    target = urlsplit(target_origin)
    return urlunsplit(
        (target.scheme, target.netloc, source.path, source.query, source.fragment)
    )


def build_session() -> requests.Session:
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "msdocs-usnat-url-converter/1.0",
            "Accept": "application/xml,text/xml,*/*;q=0.8",
        }
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def fetch_sitemap(session: requests.Session, url: str, timeout: int) -> ET.Element:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.content
    if payload.startswith(b"\x1f\x8b"):
        payload = gzip.decompress(payload)
    return ET.fromstring(payload)


def iter_sitemap_entries(
    session: requests.Session,
    sitemap_index: str,
    timeout: int = 30,
    sitemap_filter: str = "",
) -> Iterator[SitemapEntry]:
    pending = [normalize_source_url(sitemap_index)]
    seen_sitemaps: set[str] = set()

    while pending:
        sitemap_url = pending.pop()
        if sitemap_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sitemap_url)
        root = fetch_sitemap(session, sitemap_url, timeout)

        if root.tag == SITEMAP_NAMESPACE + "sitemapindex":
            children = [
                loc.text.strip()
                for loc in root.findall(f".//{SITEMAP_NAMESPACE}loc")
                if loc.text and (not sitemap_filter or sitemap_filter in loc.text)
            ]
            pending.extend(reversed(children))
            continue

        if root.tag != SITEMAP_NAMESPACE + "urlset":
            raise ValueError(f"Unsupported sitemap document at {sitemap_url}: {root.tag}")

        for node in root.findall(f".//{SITEMAP_NAMESPACE}url"):
            loc = node.find(SITEMAP_NAMESPACE + "loc")
            if loc is None or not loc.text:
                continue
            lastmod = node.find(SITEMAP_NAMESPACE + "lastmod")
            yield SitemapEntry(
                source_url=loc.text.strip(),
                last_modified=(
                    lastmod.text.strip()
                    if lastmod is not None and lastmod.text
                    else ""
                ),
                sitemap_url=sitemap_url,
            )


def write_url_map(
    entries: Iterator[SitemapEntry],
    target_origin: str,
    output_path: str,
    include_prefixes: tuple[str, ...] = (),
    max_urls: int = 0,
) -> int:
    count = 0
    seen_urls: set[str] = set()
    with open(output_path, "w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(
            ["source_url", "usnat_url", "last_modified", "source_sitemap"]
        )
        for entry in entries:
            if include_prefixes and not entry.source_url.startswith(include_prefixes):
                continue
            if entry.source_url in seen_urls:
                continue
            seen_urls.add(entry.source_url)
            writer.writerow(
                [
                    entry.source_url,
                    convert_url(entry.source_url, target_origin),
                    entry.last_modified,
                    entry.sitemap_url,
                ]
            )
            count += 1
            if max_urls and count >= max_urls:
                break
    return count


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recursively walk the Microsoft Learn sitemap tree and produce a "
            "CSV mapping every public URL to its USNat equivalent."
        )
    )
    parser.add_argument(
        "--sitemap-index",
        default=DEFAULT_SITEMAP_INDEX,
        required=not bool(DEFAULT_SITEMAP_INDEX),
        help="Root sitemap index (or set SITEMAP_INDEX)",
    )
    parser.add_argument(
        "--target-origin",
        default=os.environ.get("USNAT_LEARN_ORIGIN"),
        help="USNat Learn HTTPS origin, or set USNAT_LEARN_ORIGIN",
    )
    parser.add_argument(
        "--output",
        default="usnat-url-map.csv",
        help="Destination CSV path (default: usnat-url-map.csv)",
    )
    parser.add_argument(
        "--sitemap-filter",
        default="",
        help="Only recurse into child sitemap URLs containing this text",
    )
    parser.add_argument(
        "--include-prefix",
        action="append",
        default=[],
        help="Only include page URLs with this prefix; repeat for multiple prefixes",
    )
    parser.add_argument(
        "--max-urls",
        type=int,
        default=0,
        help="Stop after this many unique URLs; 0 means all URLs",
    )
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args(argv)
    if not args.target_origin:
        parser.error("--target-origin or USNAT_LEARN_ORIGIN is required")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    target_origin = normalize_target_origin(args.target_origin)
    entries = iter_sitemap_entries(
        build_session(),
        args.sitemap_index,
        timeout=args.timeout,
        sitemap_filter=args.sitemap_filter,
    )
    count = write_url_map(
        entries,
        target_origin,
        args.output,
        include_prefixes=tuple(args.include_prefix),
        max_urls=args.max_urls,
    )
    print(f"Wrote {count} URL mappings to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
