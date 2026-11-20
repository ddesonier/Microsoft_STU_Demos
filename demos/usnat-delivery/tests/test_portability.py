import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
sys.path.insert(0, str(APP_DIR))

import configuration


class PortabilityTests(unittest.TestCase):
    def test_runtime_and_infrastructure_have_no_commercial_cloud_hosts(self):
        forbidden = re.compile(
            r"(?:"
            r"login\.microsoftonline\.com|"
            r"graph\.microsoft\.com|"
            r"management\.azure\.com|"
            r"cognitiveservices\.azure\.com|"
            r"search\.windows\.net|"
            r"core\.windows\.net|"
            r"learn\.microsoft\.com|"
            r"azurewebsites\.net|"
            r"\beastus2?\b"
            r")",
            re.IGNORECASE,
        )
        violations = []
        for directory in (ROOT / "app", ROOT / "infra"):
            for path in directory.rglob("*"):
                if path.suffix.lower() not in {".py", ".ps1", ".bicep"}:
                    continue
                if forbidden.search(path.read_text(encoding="utf-8")):
                    violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [])

    def test_required_url_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "SEARCH_ENDPOINT"):
                configuration.require_https_url("SEARCH_ENDPOINT")

    def test_required_url_rejects_non_https(self):
        with patch.dict(
            os.environ,
            {"SEARCH_ENDPOINT": "http://search.internal"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "absolute HTTPS"):
                configuration.require_https_url("SEARCH_ENDPOINT")

    def test_example_environment_contains_only_placeholders(self):
        content = (ROOT / ".env.local.example").read_text(encoding="utf-8")
        self.assertNotRegex(
            content,
            re.compile(
                r"(?:"
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|"
                r"gh[opusr]_[A-Za-z0-9]{20,}|"
                r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
                r")",
                re.IGNORECASE,
            ),
        )


if __name__ == "__main__":
    unittest.main()
