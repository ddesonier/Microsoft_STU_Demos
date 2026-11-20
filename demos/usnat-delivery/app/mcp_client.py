"""
Demonstrates Azure AI Search's built-in MCP server: every knowledge base
exposes a native Model Context Protocol endpoint (one tool,
`knowledge_base_retrieve`) that any MCP-compatible client can call. This
module provisions the knowledge source/base backing that endpoint, and acts
as the MCP client that calls it -- a different code path from
search_index.py's direct SearchClient queries used by the main Search tab.

Provisioning (one-time, idempotent): run `python mcp_client.py`.

Auth: Azure AD only (DefaultAzureCredential), same as the rest of this app.

Notes on API versions (discovered empirically against this project's search
service -- the docs don't spell this out clearly):
  - The MCP endpoint itself currently requires a preview API version
    (2026-08-01-preview); the GA 2026-04-01 surface doesn't expose /mcp yet.
  - A knowledge base with no LLM model attached (our "simple, no extra LLM"
    choice) must have retrievalReasoningEffort explicitly set to "minimal",
    or every retrieve call fails with "A Knowledge Base model must be
    specified to use any reasoning effort other than 'Minimal'". The
    installed azure-search-documents SDK (12.0.0) doesn't yet model this
    field, so it's set via a raw REST call below.
"""

import asyncio
import json
import os
import re

import requests
from azure.identity import DefaultAzureCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    KnowledgeBase,
    KnowledgeSourceReference,
    SearchIndexFieldReference,
    SearchIndexKnowledgeSource,
    SearchIndexKnowledgeSourceParameters,
)
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

import configuration

SEARCH_ENDPOINT = configuration.value("SEARCH_ENDPOINT")
SEARCH_INDEX_NAME = os.environ.get("SEARCH_INDEX_NAME", "msdocs-content-v2")
SEMANTIC_CONFIG_NAME = os.environ.get("SEMANTIC_CONFIG_NAME", "default-semantic-config")
KNOWLEDGE_SOURCE_NAME = os.environ.get("KNOWLEDGE_SOURCE_NAME", "msdocs-content-v2-ks")
KNOWLEDGE_BASE_NAME = os.environ.get("KNOWLEDGE_BASE_NAME", "msdocs-content-v2-kb")
MCP_API_VERSION = configuration.value("MCP_API_VERSION")
SEARCH_TOKEN_SCOPE = os.environ.get(
    "SEARCH_TOKEN_SCOPE", ""
)
SEARCH_AUDIENCE = configuration.value("SEARCH_AUDIENCE")

_credential = DefaultAzureCredential()
MAX_RETRIEVAL_HITS = int(os.environ.get("MAX_RETRIEVAL_HITS", "8"))

_STOPWORDS = {
    "about", "after", "also", "and", "are", "can", "could", "does", "for",
    "from", "give", "have", "how", "into", "make", "more", "need", "of",
    "please", "run", "should", "show", "that", "the", "then", "this", "to",
    "using", "want", "what", "when", "where", "which", "with", "would", "you",
}
_EXPANSIONS = {
    "container": "Docker custom container image registry deployment",
    "containers": "Docker custom container image registry deployment",
    "iac": "infrastructure as code Bicep ARM Terraform",
    "identity": "managed identity authentication RBAC",
    "vscode": "Visual Studio Code",
}
_LANGUAGE_ALIASES = {
    "python": {"python"},
    "node": {"node", "nodejs", "javascript", "typescript"},
    "java": {"java"},
    "dotnet": {"dotnet", ".net", "csharp"},
    "go": {"golang"},
    "php": {"php"},
    "ruby": {"ruby"},
}


def mcp_endpoint() -> str:
    endpoint = configuration.require_https_url("SEARCH_ENDPOINT")
    configuration.require("KNOWLEDGE_BASE_NAME", "MCP_API_VERSION")
    return f"{endpoint}/knowledgebases/{KNOWLEDGE_BASE_NAME}/mcp?api-version={MCP_API_VERSION}"


def ensure_knowledge_base() -> None:
    """Create/update the knowledge source + knowledge base backing the MCP
    endpoint. Idempotent -- safe to call every time before retrieving."""
    endpoint = configuration.require_https_url("SEARCH_ENDPOINT")
    configuration.require(
        "SEARCH_INDEX_NAME",
        "SEMANTIC_CONFIG_NAME",
        "KNOWLEDGE_SOURCE_NAME",
        "KNOWLEDGE_BASE_NAME",
        "MCP_API_VERSION",
        "SEARCH_TOKEN_SCOPE",
        "SEARCH_AUDIENCE",
    )
    index_client = SearchIndexClient(
        endpoint=endpoint,
        credential=_credential,
        audience=SEARCH_AUDIENCE,
    )

    knowledge_source = SearchIndexKnowledgeSource(
        name=KNOWLEDGE_SOURCE_NAME,
        search_index_parameters=SearchIndexKnowledgeSourceParameters(
            search_index_name=SEARCH_INDEX_NAME,
            semantic_configuration_name=SEMANTIC_CONFIG_NAME,
            # Default field selection omits everything except id/title/
            # headings/content -- request url/source explicitly so results
            # can link back to the original Microsoft Learn page.
            source_data_fields=[
                SearchIndexFieldReference(name="url"),
                SearchIndexFieldReference(name="title"),
                SearchIndexFieldReference(name="page_title"),
                SearchIndexFieldReference(name="section_title"),
                SearchIndexFieldReference(name="content"),
                SearchIndexFieldReference(name="headings"),
                SearchIndexFieldReference(name="source"),
                SearchIndexFieldReference(name="product"),
            ],
        ),
    )
    index_client.create_or_update_knowledge_source(knowledge_source=knowledge_source)

    knowledge_base = KnowledgeBase(
        name=KNOWLEDGE_BASE_NAME,
        knowledge_sources=[KnowledgeSourceReference(name=KNOWLEDGE_SOURCE_NAME)],
        # No `models` -- this knowledge base intentionally has no LLM
        # attached (query planning / answer synthesis stay out of scope).
    )
    index_client.create_or_update_knowledge_base(knowledge_base=knowledge_base)

    _set_minimal_reasoning_effort()


def _set_minimal_reasoning_effort() -> None:
    """Set retrievalReasoningEffort=minimal via raw REST. Required because
    (a) a model-less knowledge base otherwise rejects every retrieve call,
    and (b) the installed SDK doesn't yet model this preview-only field."""
    token = _credential.get_token(SEARCH_TOKEN_SCOPE).token
    endpoint = configuration.require_https_url("SEARCH_ENDPOINT")
    url = f"{endpoint}/knowledgebases/{KNOWLEDGE_BASE_NAME}?api-version={MCP_API_VERSION}"
    body = {
        "name": KNOWLEDGE_BASE_NAME,
        "knowledgeSources": [{"name": KNOWLEDGE_SOURCE_NAME}],
        "retrievalReasoningEffort": {"kind": "minimal"},
    }
    r = requests.put(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    r.raise_for_status()


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9.+#-]*", text.lower())
        if len(token) > 2 and token not in _STOPWORDS
    }


def contextual_query(query: str, history: list | None = None) -> str:
    prior_questions = [
        item.get("content", "").strip()
        for item in (history or [])[-6:]
        if item.get("role") == "user" and item.get("content", "").strip()
    ]
    return "\n".join([*prior_questions[-2:], query]).strip()[:1000]


def build_query_variants(query: str) -> list[str]:
    variants = [query.strip()]
    terms = _tokens(query)
    if terms:
        variants.append(" ".join(sorted(terms)))
    expanded = [
        expansion
        for term, expansion in _EXPANSIONS.items()
        if term in terms
    ]
    if expanded:
        variants.append(f"{query} {' '.join(expanded)}")
    clauses = [
        clause.strip()
        for clause in re.split(r"\b(?:and then|then|also)\b|[;]", query, flags=re.I)
        if len(_tokens(clause)) >= 2
    ]
    variants.extend(clauses[:2])
    return list(dict.fromkeys(value[:400] for value in variants if value))[:5]


def _query_languages(tokens: set[str]) -> set[str]:
    return {
        language
        for language, aliases in _LANGUAGE_ALIASES.items()
        if tokens & aliases
    }


def filter_hits(query: str, hits: list) -> list:
    """Reject obvious technology mismatches and weak lexical grounding."""
    query_tokens = _tokens(query)
    query_languages = _query_languages(query_tokens)
    query_lower = query.lower()
    requires_app_service = "app service" in query_lower
    requires_container = bool(query_tokens & {"container", "containers", "docker"})
    ranked = []
    for position, hit in enumerate(hits):
        title = f"{hit.get('title', '')} {hit.get('section_title', '')}"
        text = f"{title} {hit.get('content', '')}"
        text_lower = text.lower()
        if requires_app_service and "app service" not in text_lower:
            continue
        if requires_container and not any(
            term in text_lower for term in ("container", "docker")
        ):
            continue
        hit_tokens = _tokens(text)
        hit_languages = _query_languages(hit_tokens)
        if query_languages and hit_languages and not (query_languages & hit_languages):
            continue
        overlap = query_tokens & hit_tokens
        title_overlap = query_tokens & _tokens(title)
        required = 1 if len(query_tokens) <= 4 else 2
        if len(overlap) < required and not title_overlap:
            continue
        score = len(overlap) + (2 * len(title_overlap)) - (position * 0.01)
        ranked.append((score, hit))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [hit for _, hit in ranked[:MAX_RETRIEVAL_HITS]]


async def _retrieve_async(query: str, history: list | None = None) -> list:
    retrieval_query = contextual_query(query, history)
    configuration.require_https_url("SEARCH_ENDPOINT")
    configuration.require(
        "SEARCH_TOKEN_SCOPE",
        "SEARCH_AUDIENCE",
        "MCP_API_VERSION",
        "KNOWLEDGE_BASE_NAME",
    )
    token = _credential.get_token(SEARCH_TOKEN_SCOPE).token
    headers = {"Authorization": f"Bearer {token}"}

    async with streamablehttp_client(mcp_endpoint(), headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "knowledge_base_retrieve",
                arguments={"query_variants": build_query_variants(retrieval_query)},
            )

    if result.isError:
        detail = result.content[0].text if result.content else "unknown error"
        raise RuntimeError(detail)

    # content[0] is a JSON array of {ref_id, title, terms, content} summaries,
    # in relevance order. Remaining blocks are {kind: "reference", ref_id,
    # sourceData: {...}} -- one per ref_id, carrying the full source fields
    # (including "url", which the summary block doesn't have).
    summaries = json.loads(result.content[0].text) if result.content else []

    urls_by_ref = {}
    sources_by_ref = {}
    source_data_by_ref = {}
    for block in result.content[1:]:
        try:
            parsed = json.loads(block.text)
        except (ValueError, AttributeError):
            continue
        if parsed.get("kind") == "reference":
            source_data = parsed.get("sourceData") or {}
            urls_by_ref[parsed.get("ref_id")] = source_data.get("url")
            sources_by_ref[parsed.get("ref_id")] = source_data.get("source")
            source_data_by_ref[parsed.get("ref_id")] = source_data

    hits = []
    for item in summaries:
        ref_id = item.get("ref_id")
        source_data = source_data_by_ref.get(ref_id, {})
        hits.append({
            "title": source_data.get("page_title") or item.get("title"),
            "url": urls_by_ref.get(ref_id),
            "source": sources_by_ref.get(ref_id),
            "section_title": source_data.get("section_title"),
            "product": source_data.get("product"),
            "content": item.get("content", ""),
        })
    return filter_hits(retrieval_query, hits)


def retrieve(query: str, history: list | None = None) -> list:
    """Synchronous wrapper around the MCP tool call, for use from Flask."""
    return asyncio.run(_retrieve_async(query, history))


if __name__ == "__main__":
    ensure_knowledge_base()
    print(f"knowledge base '{KNOWLEDGE_BASE_NAME}' ready")
    print(f"MCP endpoint: {mcp_endpoint()}")
