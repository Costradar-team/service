from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "collect"
    / "collect_kca.py"
)
SPEC = importlib.util.spec_from_file_location("collect_kca", SCRIPT_PATH)
assert SPEC is not None
collect = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collect
assert SPEC.loader is not None
SPEC.loader.exec_module(collect)


class KcaCollectTests(unittest.TestCase):
    def test_service_key_prefers_odcloud_service_key(self) -> None:
        with patch.dict(
            os.environ,
            {"ODCLOUD_SERVICE_KEY": "odcloud-key", "KCA_API_KEY": "legacy-key"},
            clear=True,
        ):
            self.assertEqual(collect.service_key_from_env(), "odcloud-key")

    def test_service_key_falls_back_to_kca_api_key_alias(self) -> None:
        with patch.dict(os.environ, {"KCA_API_KEY": "legacy-key"}, clear=True):
            self.assertEqual(collect.service_key_from_env(), "legacy-key")

    def test_service_key_requires_supported_env_name(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "ODCLOUD_SERVICE_KEY or KCA_API_KEY"):
                collect.service_key_from_env()


if __name__ == "__main__":
    unittest.main()
