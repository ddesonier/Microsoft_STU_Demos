import gzip
import io
import os
import sys
import tempfile
import unittest

os.environ.setdefault("CONTENT_SOURCE_HOST", "learn.microsoft.com")
os.environ.setdefault(
    "SITEMAP_INDEX",
    "https://learn.microsoft.com/_sitemaps/sitemapindex.xml",
)

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, APP_DIR)

import usnat_url_converter


class FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, responses):
        self.responses = responses

    def get(self, url, timeout):
        return FakeResponse(self.responses[url])


class UsnatUrlConverterTests(unittest.TestCase):
    def test_normalizes_duplicate_scheme_and_converts_page_url(self):
        source = "https://https://learn.microsoft.com/_sitemaps/sitemapindex.xml"
        self.assertEqual(
            usnat_url_converter.normalize_source_url(source),
            "https://learn.microsoft.com/_sitemaps/sitemapindex.xml",
        )
        self.assertEqual(
            usnat_url_converter.convert_url(
                "https://learn.microsoft.com/en-us/azure/search/?view=rest#examples",
                "https://learn.example.us",
            ),
            "https://learn.example.us/en-us/azure/search/?view=rest#examples",
        )

    def test_recurses_through_nested_and_gzipped_sitemaps(self):
        root_url = "https://learn.microsoft.com/_sitemaps/sitemapindex.xml"
        child_url = "https://learn.microsoft.com/_sitemaps/azure.xml.gz"
        root = b"""<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://learn.microsoft.com/_sitemaps/azure.xml.gz</loc></sitemap>
</sitemapindex>"""
        child = b"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://learn.microsoft.com/en-us/azure/search/</loc>
    <lastmod>2026-11-13</lastmod>
  </url>
  <url><loc>https://learn.microsoft.com/en-us/azure/app-service/</loc></url>
</urlset>"""
        session = FakeSession({root_url: root, child_url: gzip.compress(child)})

        entries = list(
            usnat_url_converter.iter_sitemap_entries(session, root_url)
        )

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].last_modified, "2026-11-13")
        self.assertEqual(entries[1].sitemap_url, child_url)

    def test_writes_filtered_unique_url_map(self):
        entries = iter(
            [
                usnat_url_converter.SitemapEntry(
                    "https://learn.microsoft.com/en-us/azure/search/",
                    "2026-11-13",
                    "source.xml",
                ),
                usnat_url_converter.SitemapEntry(
                    "https://learn.microsoft.com/en-us/azure/search/",
                    "2026-11-13",
                    "source.xml",
                ),
                usnat_url_converter.SitemapEntry(
                    "https://learn.microsoft.com/en-us/windows/",
                    "",
                    "source.xml",
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "map.csv")
            count = usnat_url_converter.write_url_map(
                entries,
                "https://learn.example.us",
                output,
                include_prefixes=(
                    "https://learn.microsoft.com/en-us/azure/",
                ),
            )
            with io.open(output, encoding="utf-8") as csv_file:
                content = csv_file.read()

        self.assertEqual(count, 1)
        self.assertIn(
            "https://learn.example.us/en-us/azure/search/",
            content,
        )
        self.assertNotIn("/en-us/windows/", content)


if __name__ == "__main__":
    unittest.main()
