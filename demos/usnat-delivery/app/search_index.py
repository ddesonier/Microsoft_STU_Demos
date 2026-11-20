"""
Index crawled Microsoft Learn pages as section-aware chunks (from Azure Blob Storage, see
blob_store.py) into Azure AI Search.

Creates/updates a hybrid (keyword + vector) search index, embeds each page's
content with an Azure OpenAI embedding deployment, and uploads the documents.

Auth: Azure AD only (no API keys) via DefaultAzureCredential. Run `az login`
(or `azd auth login`) first, and make sure the signed-in principal has the
"Search Service Contributor" + "Search Index Data Contributor" roles on the
search service, and "Cognitive Services OpenAI User" on the embeddings
resource.

Env vars (defaults match this project's provisioned resources):
  SEARCH_ENDPOINT               absolute Search endpoint for the target cloud
  SEARCH_INDEX_NAME             default: msdocs-content
  AOAI_ENDPOINT                 absolute AI Services endpoint for the target cloud
  AOAI_EMBEDDING_DEPLOYMENT     default: text-embedding-3-large
  AOAI_API_VERSION              default: 2024-10-21
"""

import hashlib
import os
import re
import urllib.parse
from collections import defaultdict
from collections.abc import Callable

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    VectorSearch,
    VectorSearchProfile,
)
from openai import AzureOpenAI

import blob_store
import configuration

# ---------- CONFIG ----------

SEARCH_ENDPOINT = configuration.value("SEARCH_ENDPOINT")
SEARCH_INDEX_NAME = os.environ.get("SEARCH_INDEX_NAME", "msdocs-content-v2")
SEARCH_AUDIENCE = configuration.value("SEARCH_AUDIENCE")
AOAI_ENDPOINT = configuration.value("AOAI_ENDPOINT")
AOAI_EMBEDDING_DEPLOYMENT = os.environ.get("AOAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
AOAI_API_VERSION = configuration.value("AOAI_API_VERSION")
COGNITIVE_SERVICES_TOKEN_SCOPE = os.environ.get(
    "COGNITIVE_SERVICES_TOKEN_SCOPE",
    "",
)

EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "1024"))
MAX_EMBEDDING_CHARS = 12000
EMBED_BATCH_SIZE = 16        # texts per embeddings API call (fewer round trips at scale)
UPLOAD_BATCH_SIZE = 100      # documents per search upload call
DELETE_BATCH_SIZE = 1000
SYNC_GROUP_CHUNKS = 128
SYNC_GROUP_PAGES = 25
CHUNK_TARGET_CHARS = int(os.environ.get("CHUNK_TARGET_CHARS", "3500"))
CHUNK_OVERLAP_CHARS = int(os.environ.get("CHUNK_OVERLAP_CHARS", "400"))
MIN_CHUNK_CHARS = int(os.environ.get("MIN_CHUNK_CHARS", "80"))
VECTOR_ALGORITHM = "default-hnsw"
VECTOR_SEARCH_PROFILE = "default-vector-profile"
SEMANTIC_CONFIG_NAME = "default-semantic-config"

# -----------------------------

_credential = DefaultAzureCredential()


def get_index_client() -> SearchIndexClient:
    endpoint = configuration.require_https_url("SEARCH_ENDPOINT")
    configuration.require("SEARCH_INDEX_NAME", "SEARCH_AUDIENCE")
    return SearchIndexClient(
        endpoint=endpoint,
        credential=_credential,
        audience=SEARCH_AUDIENCE,
    )


def get_search_client() -> SearchClient:
    endpoint = configuration.require_https_url("SEARCH_ENDPOINT")
    configuration.require("SEARCH_INDEX_NAME", "SEARCH_AUDIENCE")
    return SearchClient(
        endpoint=endpoint,
        index_name=SEARCH_INDEX_NAME,
        credential=_credential,
        audience=SEARCH_AUDIENCE,
    )


def get_embedding_client() -> AzureOpenAI:
    endpoint = configuration.require_https_url("AOAI_ENDPOINT")
    configuration.require(
        "AOAI_EMBEDDING_DEPLOYMENT",
        "AOAI_API_VERSION",
        "COGNITIVE_SERVICES_TOKEN_SCOPE",
    )
    token_provider = get_bearer_token_provider(
        _credential, COGNITIVE_SERVICES_TOKEN_SCOPE
    )
    return AzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token_provider=token_provider,
        api_version=AOAI_API_VERSION,
        max_retries=5,
    )


def build_index_definition() -> SearchIndex:
    fields = [
        SearchField(
            name="id",
            type=SearchFieldDataType.String,
            key=True,
            filterable=True,
            sortable=True,
        ),
        SearchField(name="page_id", type=SearchFieldDataType.String, filterable=True),
        SearchField(name="url", type=SearchFieldDataType.String, filterable=True),
        SearchField(name="canonical_url", type=SearchFieldDataType.String, filterable=True),
        SearchField(
            name="page_title",
            type=SearchFieldDataType.String,
            searchable=True,
            filterable=False,
            sortable=False,
            facetable=False,
        ),
        SearchField(
            name="title",
            type=SearchFieldDataType.String,
            searchable=True,
            filterable=False,
            sortable=False,
            facetable=False,
        ),
        SearchField(
            name="section_title",
            type=SearchFieldDataType.String,
            searchable=True,
            filterable=True,
            sortable=False,
            facetable=False,
        ),
        SearchField(
            name="headings",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            searchable=True,
            filterable=False,
            facetable=False,
        ),
        # filterable/sortable/facetable must be explicitly False here: Azure AI
        # Search otherwise defaults them to True for string fields, which
        # forces the whole (large) field value to be indexed as a single
        # non-analyzed term and fails once it exceeds Lucene's ~32KB term
        # limit -- which long docs here routinely do.
        SearchField(
            name="content",
            type=SearchFieldDataType.String,
            searchable=True,
            filterable=False,
            sortable=False,
            facetable=False,
        ),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=EMBEDDING_DIMENSIONS,
            vector_search_profile_name=VECTOR_SEARCH_PROFILE,
        ),
        SearchField(name="content_hash", type=SearchFieldDataType.String, filterable=True),
        SearchField(name="page_content_hash", type=SearchFieldDataType.String, filterable=True),
        SearchField(
            name="chunk_index",
            type=SearchFieldDataType.Int32,
            filterable=True,
            sortable=True,
        ),
        SearchField(
            name="section_index",
            type=SearchFieldDataType.Int32,
            filterable=True,
            sortable=True,
        ),
        SearchField(name="source_last_modified", type=SearchFieldDataType.String, filterable=True, sortable=True),
        SearchField(name="crawled_at", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
        SearchField(name="language", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchField(name="source", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SearchField(name="product", type=SearchFieldDataType.String, filterable=True, facetable=True),
    ]

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name=VECTOR_ALGORITHM)],
        profiles=[
            VectorSearchProfile(
                name=VECTOR_SEARCH_PROFILE,
                algorithm_configuration_name=VECTOR_ALGORITHM,
            )
        ],
    )

    semantic_search = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name=SEMANTIC_CONFIG_NAME,
                prioritized_fields=SemanticPrioritizedFields(
                    title_field=SemanticField(field_name="title"),
                    content_fields=[SemanticField(field_name="content")],
                    keywords_fields=[
                        SemanticField(field_name="section_title"),
                        SemanticField(field_name="headings"),
                    ],
                ),
            )
        ]
    )

    return SearchIndex(
        name=SEARCH_INDEX_NAME,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search,
    )


def ensure_index() -> None:
    """Create the index if missing, or update it in place (idempotent)."""
    get_index_client().create_or_update_index(build_index_definition())


def embed_texts(embedding_client: AzureOpenAI, texts: list) -> list:
    """Embed multiple texts in a single API call (cheaper and faster at scale)."""
    truncated = [t[:MAX_EMBEDDING_CHARS] for t in texts]
    response = embedding_client.embeddings.create(
        model=AOAI_EMBEDDING_DEPLOYMENT,
        input=truncated,
        dimensions=EMBEDDING_DIMENSIONS,
    )
    ordered = sorted(response.data, key=lambda d: d.index)
    return [d.embedding for d in ordered]


def embed_text(embedding_client: AzureOpenAI, text: str) -> list:
    return embed_texts(embedding_client, [text])[0]


def load_documents():
    yield from blob_store.iter_documents()


def break_long_tokens(text: str, max_len: int = 300) -> str:
    """Insert spaces into pathologically long unbroken runs of non-whitespace
    characters so Azure Search's tokenizer never sees a single term over
    Lucene's ~32KB term-size limit. Mirrors test_crawl.py's crawl-time fix,
    applied here too so pages crawled before that fix still index cleanly.
    """
    return re.sub(rf"(\S{{{max_len}}})(?=\S)", r"\1 ", text)


def _tail_overlap(text: str) -> str:
    tail = text[-CHUNK_OVERLAP_CHARS:]
    first_space = tail.find(" ")
    return tail[first_space + 1:] if first_space >= 0 else tail


def split_chunk_text(text: str) -> list[str]:
    """Split at paragraph/sentence boundaries with a small retrieval overlap."""
    paragraphs = [
        re.sub(r"\s+", " ", part).strip()
        for part in re.split(r"\n+", text)
        if part.strip()
    ]
    chunks = []
    current = ""

    def emit():
        nonlocal current
        value = current.strip()
        if value:
            chunks.append(value)
            current = _tail_overlap(value)

    for paragraph in paragraphs:
        pieces = [paragraph]
        if len(paragraph) > CHUNK_TARGET_CHARS:
            pieces = [
                piece.strip()
                for piece in re.split(r"(?<=[.!?])\s+", paragraph)
                if piece.strip()
            ]
        for piece in pieces:
            while len(piece) > CHUNK_TARGET_CHARS:
                available = max(1, CHUNK_TARGET_CHARS - len(current) - 1)
                current = f"{current} {piece[:available]}".strip()
                piece = piece[available:].lstrip()
                emit()
            candidate = f"{current}\n{piece}".strip()
            if current and len(candidate) > CHUNK_TARGET_CHARS:
                emit()
                candidate = f"{current}\n{piece}".strip()
            current = candidate
    if current.strip():
        chunks.append(current.strip())
    return [chunk for chunk in chunks if len(chunk) >= MIN_CHUNK_CHARS]


def _page_index_hash(page: dict) -> str:
    value = f"{page.get('extractor_version', 1)}:{page.get('content_hash', '')}"
    return hashlib.sha256(value.encode()).hexdigest()


def _product_from_url(url: str) -> str:
    parts = [part for part in urllib.parse.urlsplit(url).path.split("/") if part]
    return parts[1] if len(parts) > 1 and parts[0].lower() == "en-us" else ""


def chunk_document(page: dict) -> list[dict]:
    """Convert one persisted page into stable, section-aware Search documents."""
    page_id = page["id"]
    page_title = page.get("title") or page.get("canonical_url") or page.get("url")
    canonical_url = page.get("canonical_url") or page.get("url")
    sections = page.get("sections") or [
        {
            "heading": "Overview",
            "heading_path": [],
            "anchor": "",
            "content": page.get("content", ""),
        }
    ]
    page_hash = _page_index_hash(page)
    product = _product_from_url(canonical_url)
    chunks = []

    for section_index, section in enumerate(sections):
        section_title = (section.get("heading") or "Overview").strip()
        heading_path = [
            heading.strip()
            for heading in section.get("heading_path", [])
            if heading and heading.strip()
        ]
        body_chunks = split_chunk_text(section.get("content", ""))
        for section_chunk_index, body in enumerate(body_chunks):
            headings = list(dict.fromkeys([*heading_path, section_title]))
            header = "\n".join(
                value
                for value in [page_title, " > ".join(headings)]
                if value
            )
            content = f"{header}\n\n{body}".strip()
            chunk_seed = (
                f"{page_id}:{section_index}:{section_chunk_index}:"
                f"{section.get('anchor', '')}"
            )
            chunk_id = hashlib.sha256(chunk_seed.encode()).hexdigest()
            anchor = section.get("anchor", "").strip()
            url = page.get("url") or canonical_url
            if anchor:
                url = f"{url.split('#', 1)[0]}#{anchor}"
            title = (
                page_title
                if not section_title or section_title == page_title
                else f"{page_title} — {section_title}"
            )
            chunks.append(
                {
                    "id": chunk_id,
                    "page_id": page_id,
                    "url": url,
                    "canonical_url": canonical_url,
                    "page_title": page_title,
                    "title": title,
                    "section_title": section_title,
                    "headings": headings,
                    "content": break_long_tokens(content),
                    "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                    "page_content_hash": page_hash,
                    "chunk_index": len(chunks),
                    "section_index": section_index,
                    "source_last_modified": page.get("source_last_modified"),
                    "crawled_at": page.get("crawled_at"),
                    "language": page.get("language", "en-us"),
                    "source": page.get("source", "Microsoft Learn"),
                    "product": product,
                }
            )
    return chunks


def fetch_existing_state(search_client: SearchClient) -> tuple[dict, dict, dict]:
    """Read all chunk/page hashes using stable key-range pagination."""
    page_hashes = defaultdict(set)
    chunk_hashes = {}
    ids_by_page = defaultdict(set)
    last_id = None
    while True:
        key_filter = None
        if last_id is not None:
            escaped = last_id.replace("'", "''")
            key_filter = f"id gt '{escaped}'"
        page = list(
            search_client.search(
                search_text="*",
                filter=key_filter,
                select=["id", "page_id", "content_hash", "page_content_hash"],
                order_by=["id asc"],
                top=1000,
            )
        )
        if not page:
            break
        for result in page:
            chunk_id = result["id"]
            page_id = result.get("page_id")
            chunk_hashes[chunk_id] = result.get("content_hash")
            if page_id:
                ids_by_page[page_id].add(chunk_id)
                page_hashes[page_id].add(result.get("page_content_hash"))
        last_id = page[-1]["id"]
        if len(page) < 1000:
            break
    return page_hashes, chunk_hashes, ids_by_page


def index_page_document(page: dict) -> dict:
    """Synchronize one page's chunks for targeted Admin updates."""
    ensure_index()
    search_client = get_search_client()
    existing = list(
        search_client.search(
            search_text="*",
            filter=f"page_id eq '{page['id']}'",
            select=["id", "content_hash", "page_content_hash"],
        )
    )
    page_hash = _page_index_hash(page)
    chunks = chunk_document(page)
    expected_ids = {document["id"] for document in chunks}
    existing_ids = {item["id"] for item in existing}
    if (
        existing_ids == expected_ids
        and all(item.get("page_content_hash") == page_hash for item in existing)
    ):
        return {
            "changed": False,
            "chunks": len(existing),
            "uploaded": 0,
            "deleted": 0,
        }

    embedding_client = get_embedding_client()
    for offset in range(0, len(chunks), EMBED_BATCH_SIZE):
        batch = chunks[offset:offset + EMBED_BATCH_SIZE]
        vectors = embed_texts(
            embedding_client,
            [document["content"] for document in batch],
        )
        for document, vector in zip(batch, vectors):
            document["content_vector"] = vector

    upload_results = search_client.merge_or_upload_documents(chunks)
    failed = [result for result in upload_results if not result.succeeded]
    if failed:
        raise RuntimeError(failed[0].error_message)

    stale = [{"id": item["id"]} for item in existing if item["id"] not in expected_ids]
    deleted = 0
    if stale:
        delete_results = search_client.delete_documents(stale)
        deleted = sum(1 for result in delete_results if result.succeeded)
    return {
        "changed": True,
        "chunks": len(chunks),
        "uploaded": len(chunks),
        "deleted": deleted,
        "created": not existing,
    }


def index_crawled_pages(
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    """Chunk, embed and incrementally synchronize persisted pages into Search."""
    ensure_index()
    embedding_client = get_embedding_client()
    search_client = get_search_client()
    page_hashes, chunk_hashes, ids_by_page = fetch_existing_state(search_client)
    total_pages = blob_store.count_documents()
    if on_progress:
        on_progress(
            {
                "phase": "indexing",
                "processed": 0,
                "total": total_pages,
                "uploaded": 0,
                "failed": 0,
                "skipped": 0,
            }
        )

    pages_processed = pages_skipped = uploaded = failed = stale_deleted = 0
    metadata_updated = total_chunks = skipped_chunks = 0
    sync_group = []
    sync_group_chunks = 0

    def batches(values, size):
        for offset in range(0, len(values), size):
            yield values[offset:offset + size]

    def apply_results(results, successful_ids: set, operation: str):
        nonlocal failed
        for result in results:
            if result.succeeded:
                successful_ids.add(result.key)
            else:
                failed += 1
                print(f"[{operation}-fail] {result.key}  ({result.error_message})")

    def process_group():
        nonlocal sync_group, sync_group_chunks
        nonlocal uploaded, metadata_updated, failed, stale_deleted
        if not sync_group:
            return

        to_embed = []
        to_merge = []
        for work in sync_group:
            for chunk in work["chunks"]:
                if chunk_hashes.get(chunk["id"]) == chunk["content_hash"]:
                    to_merge.append(dict(chunk))
                else:
                    to_embed.append(chunk)

        embedded = []
        for batch in batches(to_embed, EMBED_BATCH_SIZE):
            try:
                vectors = embed_texts(
                    embedding_client,
                    [document["content"] for document in batch],
                )
            except Exception as exc:
                failed += len(batch)
                print(f"[embed-fail] {len(batch)} chunk(s) ({exc})")
                continue
            for document, vector in zip(batch, vectors):
                document["content_vector"] = vector
                embedded.append(document)

        successful_ids = set()
        for batch in batches(embedded, UPLOAD_BATCH_SIZE):
            results = search_client.merge_or_upload_documents(batch)
            before = len(successful_ids)
            apply_results(results, successful_ids, "upload")
            uploaded += len(successful_ids) - before

        for document in to_merge:
            document.pop("content_vector", None)
        for batch in batches(to_merge, UPLOAD_BATCH_SIZE):
            results = search_client.merge_documents(batch)
            before = len(successful_ids)
            apply_results(results, successful_ids, "merge")
            metadata_updated += len(successful_ids) - before

        safe_deletes = []
        for work in sync_group:
            if work["expected_ids"].issubset(successful_ids):
                safe_deletes.extend({"id": chunk_id} for chunk_id in work["stale_ids"])
            else:
                missing = work["expected_ids"] - successful_ids
                print(
                    f"[page-incomplete] {work['page_id']} "
                    f"({len(missing)} replacement chunk(s) missing)"
                )

        for batch in batches(safe_deletes, DELETE_BATCH_SIZE):
            results = search_client.delete_documents(batch)
            stale_deleted += sum(1 for result in results if result.succeeded)
            failed += sum(1 for result in results if not result.succeeded)

        sync_group = []
        sync_group_chunks = 0

    for page in load_documents():
        pages_processed += 1
        page_id = page["id"]
        page_hash = _page_index_hash(page)
        existing_ids = ids_by_page.get(page_id, set())
        chunks = chunk_document(page)
        expected_ids = {chunk["id"] for chunk in chunks}
        total_chunks += len(chunks)
        if (
            existing_ids == expected_ids
            and page_hashes.get(page_id, set()) == {page_hash}
        ):
            pages_skipped += 1
            skipped_chunks += len(existing_ids)
        else:
            if not chunks:
                failed += 1
                print(f"[page-empty] {page_id} produced no indexable chunks")
            else:
                sync_group.append(
                    {
                        "page_id": page_id,
                        "chunks": chunks,
                        "expected_ids": expected_ids,
                        "stale_ids": existing_ids - expected_ids,
                    }
                )
                sync_group_chunks += len(chunks)
                if (
                    sync_group_chunks >= SYNC_GROUP_CHUNKS
                    or len(sync_group) >= SYNC_GROUP_PAGES
                ):
                    process_group()

        if on_progress and (
            pages_processed % 25 == 0 or pages_processed == total_pages
        ):
            on_progress(
                {
                    "phase": "indexing",
                    "processed": pages_processed,
                    "total": total_pages,
                    "uploaded": uploaded,
                    "failed": failed,
                    "skipped": skipped_chunks,
                }
            )

    process_group()

    return {
        "total": total_chunks,
        "uploaded": uploaded,
        "failed": failed,
        "skipped": skipped_chunks,
        "pages": pages_processed,
        "pages_skipped": pages_skipped,
        "metadata_updated": metadata_updated,
        "stale_deleted": stale_deleted,
    }


if __name__ == "__main__":
    stats = index_crawled_pages()
    print(
        f"indexed {stats['uploaded']}/{stats['total']} document(s) "
        f"into '{SEARCH_INDEX_NAME}' ({stats['failed']} failed, {stats['skipped']} unchanged/skipped)"
    )
