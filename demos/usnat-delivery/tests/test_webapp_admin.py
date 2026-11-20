import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("CONTENT_SOURCE_ORIGIN", "https://learn.microsoft.com")
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

import webapp


class AdminJobTests(unittest.TestCase):
    def setUp(self):
        webapp.app.config.update(TESTING=True, SECRET_KEY="test")
        with webapp._admin_jobs_lock:
            webapp._admin_jobs.clear()
        self.client = webapp.app.test_client()
        with self.client.session_transaction() as session:
            session["user"] = {"name": "Test Admin"}

    @patch.object(webapp._admin_executor, "submit")
    def test_sitemap_form_queues_background_job(self, submit):
        response = self.client.post(
            "/admin",
            data={
                "crawl_mode": "sitemap",
                "sitemap_index": webapp.reindex.DEFAULT_SITEMAP_INDEX,
                "sitemap_filter": "azure_en-us",
                "sitemap_prefixes": webapp.reindex.DEFAULT_SITEMAP_PREFIX,
                "sitemap_limit": "25000",
                "sitemap_concurrency": "12",
            },
        )

        self.assertEqual(response.status_code, 303)
        self.assertIn("/admin/jobs/", response.headers["Location"])
        submit.assert_called_once()
        with webapp._admin_jobs_lock:
            job = next(iter(webapp._admin_jobs.values()))
        self.assertEqual(job["crawl_mode"], "sitemap")
        self.assertEqual(job["sitemap_limit"], 25000)

    @patch.object(webapp.blob_store, "count_documents", return_value=123)
    @patch.object(webapp._admin_executor, "submit")
    def test_stored_pages_form_queues_chunking_job(self, submit, _count):
        response = self.client.post(
            "/admin",
            data={"crawl_mode": "stored"},
        )

        self.assertEqual(response.status_code, 303)
        submit.assert_called_once()
        with webapp._admin_jobs_lock:
            job = next(iter(webapp._admin_jobs.values()))
        self.assertEqual(job["crawl_mode"], "stored")
        self.assertEqual(job["total"], 123)


if __name__ == "__main__":
    unittest.main()
