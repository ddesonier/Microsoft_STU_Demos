"""
Azure Blob Storage-backed persistence for crawled page JSON artifacts.

Replaces local-disk storage of app/out/*.json so the crawler can run safely
inside an Azure App Service instance. App Service local disk is either
ephemeral per-instance temp storage (wiped on restart/scale-out/redeploy) or
the quota-limited %HOME% SMB share -- neither is a good fit for tens of
thousands of small, frequently-rewritten JSON files. Blob Storage gives every
instance a shared, durable view of crawled pages that survives restarts and
scale-out, and is what crawl resume (SKIP_EXISTING) and reindexing read from.

Auth: Azure AD only (no account keys) via DefaultAzureCredential, matching
search_index.py. The app's managed identity needs "Storage Blob Data
Contributor" on the storage account.

Networking: the storage account (msdocscrawlstore, in rg-msdocs-search-app)
has public network access disabled per this tenant's governance policy, and
is only reachable via the private endpoint pe-msdocscrawlstore-blob in
vnet-msdocs-search-app/snet-private-endpoints (DNS: privatelink.blob.core
.windows.net, zone-linked to that VNet). Anything importing this module --
the crawler, the admin reindex path, or a one-off migration script -- must
run from inside that VNet (e.g. an App Service with VNet integration into
snet-appservice-integration, or a VM/Cloud Shell connected to it).

Env vars:
  BLOB_ACCOUNT_URL     absolute Blob endpoint for the target cloud
  BLOB_CONTAINER_NAME  default: crawled-pages
"""

import json
import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import ContainerClient

import configuration

BLOB_ACCOUNT_URL = configuration.value("BLOB_ACCOUNT_URL")
BLOB_CONTAINER_NAME = os.environ.get("BLOB_CONTAINER_NAME", "crawled-pages")
BLOB_READ_CONCURRENCY = int(os.environ.get("BLOB_READ_CONCURRENCY", "12"))
STORAGE_AUDIENCE = configuration.value("STORAGE_AUDIENCE")

_container_client: ContainerClient = None


def get_container_client() -> ContainerClient:
    """Return a cached ContainerClient, creating the container on first use."""
    global _container_client
    if _container_client is None:
        account_url = configuration.require_https_url("BLOB_ACCOUNT_URL")
        configuration.require("STORAGE_AUDIENCE")
        client = ContainerClient(
            account_url=account_url,
            container_name=BLOB_CONTAINER_NAME,
            credential=DefaultAzureCredential(),
            audience=STORAGE_AUDIENCE,
        )
        try:
            client.create_container()
        except ResourceExistsError:
            pass
        _container_client = client
    return _container_client


def _blob_name(doc_id: str) -> str:
    return f"{doc_id}.json"


def list_ids(min_extractor_version: int | None = None) -> set:
    """Return the set of doc ids currently stored, for crawl-resume checks.

    Fetched once per crawl run (not per-URL) so resuming a large crawl costs
    one listing call instead of one existence check per candidate URL.
    """
    container = get_container_client()
    ids = set()
    include = ["metadata"] if min_extractor_version is not None else None
    for blob in container.list_blobs(include=include):
        if not blob.name.endswith(".json"):
            continue
        if min_extractor_version is not None:
            metadata = blob.metadata or {}
            try:
                version = int(metadata.get("extractor_version", "0"))
            except ValueError:
                version = 0
            if version < min_extractor_version:
                continue
        ids.add(blob.name[: -len(".json")])
    return ids


def save_json(doc: dict) -> str:
    """Upload doc as pretty-printed JSON, keyed by its id. Returns the blob name."""
    name = _blob_name(doc["id"])
    data = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
    metadata = {
        "extractor_version": str(doc.get("extractor_version", 1)),
        "content_hash": doc.get("content_hash", ""),
    }
    get_container_client().upload_blob(
        name=name,
        data=data,
        metadata=metadata,
        overwrite=True,
    )
    return name


def load_json(name: str) -> dict:
    data = get_container_client().download_blob(name).readall()
    return json.loads(data)


def iter_documents(concurrency: int = BLOB_READ_CONCURRENCY):
    """Yield persisted pages with bounded parallel Blob downloads."""
    container = get_container_client()
    names = (
        blob.name
        for blob in container.list_blobs()
        if blob.name.endswith(".json")
    )
    with ThreadPoolExecutor(
        max_workers=concurrency,
        thread_name_prefix="blob-read",
    ) as executor:
        pending = {}
        for _ in range(concurrency * 2):
            try:
                name = next(names)
            except StopIteration:
                break
            pending[executor.submit(load_json, name)] = name

        while pending:
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                pending.pop(future)
                yield future.result()
                try:
                    name = next(names)
                except StopIteration:
                    continue
                pending[executor.submit(load_json, name)] = name


def count_documents() -> int:
    return sum(
        1
        for blob in get_container_client().list_blobs()
        if blob.name.endswith(".json")
    )
