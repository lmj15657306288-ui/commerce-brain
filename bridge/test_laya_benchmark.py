import json
import importlib.util
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_laya.py"
SPEC = importlib.util.spec_from_file_location("benchmark_laya_script", SCRIPT_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
FIXTURE_DIR = MODULE.FIXTURE_DIR
load_fixtures = MODULE.load_fixtures
percentile = MODULE.percentile


class LayaBenchmarkTests(unittest.TestCase):
    def test_required_fixtures_are_present_and_scalar_safe(self):
        fixtures = load_fixtures()
        self.assertGreaterEqual(len(fixtures), 9)
        self.assertEqual(
            {
                "stable_live",
                "traffic_up_ctr_down",
                "roi_falling",
                "high_cvr_low_traffic",
                "missing_metrics",
                "stale_state",
                "extreme_values",
                "mixed_cn_en",
                "ambiguous",
                "inventory_risk",
            },
            {item["name"] for item in fixtures},
        )
        for fixture in fixtures:
            self.assertIsInstance(fixture["state"], dict)
            encoded = json.dumps(fixture, ensure_ascii=False, allow_nan=False)
            self.assertNotIn("cookie", encoded.lower())
            self.assertNotIn("token", encoded.lower())
            self.assertNotIn("api_key", encoded.lower())

    def test_percentiles_are_deterministic(self):
        self.assertEqual(1, percentile([1, 2, 3], 0.0))
        self.assertEqual(2, percentile([1, 2, 3], 0.5))
        self.assertEqual(3, percentile([1, 2, 3], 1.0))
        self.assertIsNone(percentile([], 0.5))


if __name__ == "__main__":
    unittest.main()
