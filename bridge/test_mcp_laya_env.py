from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from laya_client import LayaClient


class MCPLayaEnvironmentTests(unittest.TestCase):
    def test_default_client_reads_only_the_process_environment(self):
        with patch.dict(os.environ, {"COMMERCE_BRAIN_LAYA_API_KEY": "fixture-key"}, clear=False):
            client = LayaClient(base_url="http://127.0.0.1:8765")
        self.assertEqual("fixture-key", client._api_key)

    def test_explicit_client_key_takes_precedence(self):
        with patch.dict(os.environ, {"COMMERCE_BRAIN_LAYA_API_KEY": "env-key"}, clear=False):
            client = LayaClient(base_url="http://127.0.0.1:8765", api_key="explicit-key")
        self.assertEqual("explicit-key", client._api_key)


if __name__ == "__main__":
    unittest.main()
