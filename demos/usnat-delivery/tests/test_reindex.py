import os
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("CONTENT_SOURCE_ORIGIN", "https://learn.microsoft.com")
os.environ.setdefault(
    "SITEMAP_INDEX",
    "https://learn.microsoft.com/_sitemaps/sitemapindex.xml",
)
os.environ.setdefault(
    "ALLOWED_PREFIXES",
    "https://learn.microsoft.com/en-us/azure/",
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import reindex


class RecursiveIndexTests(unittest.TestCase):
    @patch.object(reindex.test_crawl, "robots_ok", return_value=True)
    @patch.object(reindex.test_crawl.session, "get")
    def test_link_discovery_stays_beneath_seed_path(self, get, _robots_ok):
        response = Mock()
        response.url = "https://learn.microsoft.com/en-us/azure/ai-services/"
        response.text = """
            <a href="search/overview">Child</a>
            <a href="/en-us/azure/other">Outside</a>
            <a href="guide.pdf">Download</a>
            <a href="search/overview#section">Duplicate</a>
        """
        response.raise_for_status.return_value = None
        get.return_value = response

        urls = reindex._linked_child_urls(response.url, response.url)

        self.assertEqual(
            urls,
            ["https://learn.microsoft.com/en-us/azure/ai-services/search/overview"],
        )

    @patch.object(reindex, "_linked_child_urls")
    @patch.object(reindex, "update_url")
    def test_recursive_update_is_bounded_and_deduplicated(self, update_url, child_urls):
        update_url.side_effect = lambda url: {"url": url, "status": "updated"}
        child_urls.side_effect = [
            [
                "https://learn.microsoft.com/en-us/azure/root/child",
                "https://learn.microsoft.com/en-us/azure/root/child",
            ],
            [],
        ]

        results = reindex.update_urls_recursive(
            ["https://learn.microsoft.com/en-us/azure/root/"],
            limit=2,
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(update_url.call_count, 2)

    @patch.object(reindex, "_linked_child_urls", return_value=[])
    @patch.object(reindex, "update_url")
    def test_recursive_update_reports_progress(self, update_url, _child_urls):
        result = {"url": "https://learn.microsoft.com/en-us/azure/root/", "status": "updated"}
        update_url.return_value = result
        progress = []

        reindex.update_urls_recursive(
            ["https://learn.microsoft.com/en-us/azure/root/"],
            on_result=progress.append,
        )

        self.assertEqual(progress, [result])

    @patch.object(reindex.search_index, "index_crawled_pages")
    @patch.object(reindex.test_crawl, "crawl_sitemap")
    def test_sitemap_update_crawls_then_batch_indexes(self, crawl_sitemap, index_pages):
        crawl_sitemap.return_value = {
            "discovered": 100,
            "selected": 25,
            "skipped": 5,
            "processed": 20,
            "crawled": 20,
            "failed": 0,
            "failures": [],
        }
        index_pages.return_value = {
            "total": 25,
            "uploaded": 20,
            "failed": 0,
            "skipped": 5,
        }
        progress = []

        result = reindex.update_sitemap(
            reindex.DEFAULT_SITEMAP_INDEX,
            "azure_en-us",
            [reindex.DEFAULT_SITEMAP_PREFIX],
            max_pages=25,
            concurrency=12,
            on_progress=progress.append,
        )

        self.assertEqual(result["crawl"]["crawled"], 20)
        self.assertEqual(result["index"]["uploaded"], 20)
        crawl_sitemap.assert_called_once()
        index_pages.assert_called_once_with(on_progress=progress.append)

    def test_sitemap_update_requires_a_filter(self):
        with self.assertRaisesRegex(ValueError, "sitemap filter"):
            reindex.update_sitemap(
                reindex.DEFAULT_SITEMAP_INDEX,
                "",
                [reindex.DEFAULT_SITEMAP_PREFIX],
            )


if __name__ == "__main__":
    unittest.main()
