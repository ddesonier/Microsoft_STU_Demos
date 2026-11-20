import os
import sys
import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

os.environ.setdefault("CONTENT_SOURCE_ORIGIN", "https://learn.microsoft.com")
os.environ.setdefault("CONTENT_SOURCE_HOST", "learn.microsoft.com")
os.environ.setdefault(
    "SITEMAP_INDEX",
    "https://learn.microsoft.com/_sitemaps/sitemapindex.xml",
)
os.environ.setdefault(
    "ALLOWED_PREFIXES",
    "https://learn.microsoft.com/en-us/azure/",
)

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, APP_DIR)

import mcp_client
import search_index
import test_crawl


class ExtractionTests(unittest.TestCase):
    def test_extract_page_preserves_heading_sections_and_anchors(self):
        document = test_crawl.extract_page(
            "https://learn.microsoft.com/en-us/azure/app-service/example",
            """
            <html><head><title>Python containers</title></head><body><main>
              <h1 id="overview">Deploy Python</h1>
              <p>Overview text for the deployment.</p>
              <h2 id="commands">Run commands</h2>
              <pre>az webapp config container set --name example</pre>
            </main></body></html>
            """,
        )

        self.assertEqual(document["extractor_version"], 2)
        self.assertEqual(len(document["sections"]), 2)
        self.assertEqual(document["sections"][1]["heading"], "Run commands")
        self.assertEqual(document["sections"][1]["anchor"], "commands")
        self.assertIn("az webapp", document["sections"][1]["content"])


class ChunkingTests(unittest.TestCase):
    def test_section_chunks_include_metadata_and_anchor(self):
        page = {
            "id": "page-id",
            "url": "https://learn.microsoft.com/en-us/azure/app-service/example",
            "canonical_url": "https://learn.microsoft.com/en-us/azure/app-service/example",
            "title": "Deploy Python containers",
            "content_hash": "page-hash",
            "extractor_version": 2,
            "crawled_at": "2026-11-19T00:00:00+00:00",
            "sections": [
                {
                    "heading": "Build and push",
                    "heading_path": ["Deploy Python containers", "Build and push"],
                    "anchor": "build",
                    "content": "Use Docker and Azure Container Registry. " * 20,
                }
            ],
        }

        chunks = search_index.chunk_document(page)

        self.assertTrue(chunks)
        self.assertEqual(chunks[0]["page_id"], "page-id")
        self.assertEqual(chunks[0]["product"], "azure")
        self.assertEqual(chunks[0]["section_title"], "Build and push")
        self.assertTrue(chunks[0]["url"].endswith("#build"))
        self.assertIn("Deploy Python containers", chunks[0]["content"])

    @patch.object(search_index, "CHUNK_TARGET_CHARS", 120)
    @patch.object(search_index, "CHUNK_OVERLAP_CHARS", 20)
    @patch.object(search_index, "MIN_CHUNK_CHARS", 20)
    def test_long_section_is_split_with_overlap(self):
        chunks = search_index.split_chunk_text(
            "First sentence about Python containers. " * 20
        )

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 140 for chunk in chunks))

    def test_existing_state_uses_key_range_pagination(self):
        client = Mock()
        first_page = [
            {
                "id": f"{index:064x}",
                "page_id": "page",
                "content_hash": str(index),
                "page_content_hash": "page-hash",
            }
            for index in range(1000)
        ]
        second_page = [
            {
                "id": f"{1000:064x}",
                "page_id": "page",
                "content_hash": "1000",
                "page_content_hash": "page-hash",
            }
        ]
        client.search.side_effect = [first_page, second_page]

        page_hashes, chunk_hashes, ids_by_page = (
            search_index.fetch_existing_state(client)
        )

        self.assertEqual(len(chunk_hashes), 1001)
        self.assertEqual(page_hashes["page"], {"page-hash"})
        self.assertEqual(len(ids_by_page["page"]), 1001)
        self.assertIsNone(client.search.call_args_list[0].kwargs["filter"])
        self.assertIn(
            first_page[-1]["id"],
            client.search.call_args_list[1].kwargs["filter"],
        )
        self.assertEqual(
            client.search.call_args_list[0].kwargs["order_by"],
            ["id asc"],
        )

    @patch.object(search_index, "ensure_index")
    @patch.object(search_index.blob_store, "count_documents", return_value=1)
    @patch.object(search_index, "load_documents")
    @patch.object(search_index, "fetch_existing_state")
    @patch.object(search_index, "get_embedding_client")
    @patch.object(search_index, "get_search_client")
    @patch.object(search_index, "embed_texts", side_effect=RuntimeError("quota"))
    def test_failed_replacements_do_not_delete_stale_chunks(
        self,
        _embed,
        get_search_client,
        _get_embedding_client,
        fetch_state,
        load_documents,
        _count,
        _ensure,
    ):
        page = {
            "id": "page-id",
            "url": "https://learn.microsoft.com/en-us/azure/example",
            "canonical_url": "https://learn.microsoft.com/en-us/azure/example",
            "title": "Example",
            "content": "New content about Azure containers. " * 20,
            "content_hash": "new-page-hash",
            "extractor_version": 2,
            "crawled_at": "2026-11-19T00:00:00+00:00",
        }
        load_documents.return_value = iter([page])
        fetch_state.return_value = (
            {"page-id": {"old-page-hash"}},
            {"stale-id": "old-chunk-hash"},
            {"page-id": {"stale-id"}},
        )
        search_client = get_search_client.return_value

        result = search_index.index_crawled_pages()

        self.assertGreater(result["failed"], 0)
        search_client.delete_documents.assert_not_called()


class RetrievalQualityTests(unittest.TestCase):
    def test_follow_up_query_uses_recent_user_context(self):
        query = mcp_client.contextual_query(
            "Give me the commands to run as well",
            [
                {
                    "role": "user",
                    "content": "Deploy a Python container from VS Code to App Service",
                },
                {"role": "assistant", "content": "Here is an overview."},
            ],
        )

        self.assertIn("Python container", query)
        self.assertIn("commands", query)

    def test_language_mismatch_is_rejected(self):
        hits = [
            {
                "title": "Configure a Node.js app",
                "section_title": "Set Node version",
                "content": "Use npm and NODE_DEFAULT_VERSION for Node.js.",
            },
            {
                "title": "Deploy a Python container",
                "section_title": "Azure CLI commands",
                "content": "Build a Docker image for Python and deploy it to App Service.",
            },
            {
                "title": "Azure App Configuration",
                "section_title": "Overview",
                "content": "Centralize application settings and feature flags.",
            },
        ]

        filtered = mcp_client.filter_hits(
            "Deploy a Python container and give Azure CLI commands",
            hits,
        )

        self.assertEqual(len(filtered), 1)
        self.assertIn("Python", filtered[0]["title"])

    def test_query_variants_expand_container_terms(self):
        variants = mcp_client.build_query_variants(
            "Deploy a Python container to App Service"
        )

        self.assertGreaterEqual(len(variants), 2)
        self.assertTrue(any("Docker" in variant for variant in variants))


if __name__ == "__main__":
    unittest.main()
