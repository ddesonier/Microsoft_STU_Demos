import os
import sys
import unittest
from unittest.mock import patch

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, APP_DIR)

import knowledge_webapp


class KnowledgeWebAppTests(unittest.TestCase):
    def setUp(self):
        knowledge_webapp.app.config["TESTING"] = True
        knowledge_webapp._client_requests.clear()
        knowledge_webapp._global_requests.clear()
        self.client = knowledge_webapp.app.test_client()

    def test_health(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    @patch("knowledge_webapp.answer_generator.configured", return_value=True)
    def test_home_renders_chat(self, _configured):
        response = self.client.get("/")
        body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="chat-form"', body)
        self.assertIn("knowledge-chat.js", body)

    @patch("knowledge_webapp.answer_generator.configured", return_value=True)
    @patch("knowledge_webapp.answer_generator.generate_answer")
    @patch("knowledge_webapp.mcp_client.retrieve")
    def test_chat_returns_grounded_answer(self, retrieve, generate_answer, _configured):
        hit = {
            "title": "Managed identity",
            "url": "https://learn.microsoft.com/example",
            "source": "Microsoft Learn",
            "content": "Use the Identity settings page.",
            "citation_index": 1,
        }
        retrieve.return_value = [hit]
        generate_answer.return_value = ("Enable the identity [1].", [hit])

        response = self.client.post(
            "/api/chat",
            json={"message": "How do I enable managed identity?", "history": []},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["answer"], "Enable the identity [1].")
        self.assertEqual(payload["sources"][0]["title"], "Managed identity")
        retrieve.assert_called_once_with("How do I enable managed identity?", [])

    @patch("knowledge_webapp.answer_generator.configured", return_value=True)
    @patch(
        "knowledge_webapp.mcp_client.retrieve",
        side_effect=RuntimeError("private detail"),
    )
    def test_chat_does_not_expose_backend_errors(self, _retrieve, _configured):
        response = self.client.post("/api/chat", json={"message": "failure"})

        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private detail", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
