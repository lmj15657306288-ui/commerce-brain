import unittest

from live_state import FixtureLiveStateAdapter, SUPPORTED_WINDOWS, build_live_state_snapshot


class LiveStateTests(unittest.TestCase):
    def adapter(self):
        return FixtureLiveStateAdapter([
            {
                "online_users": 100,
                "enter_rate": 0.20,
                "leave_rate": 0.10,
                "product_click_rate": 0.08,
                "orders": 2,
                "gmv": 200,
                "ad_spend": 100,
                "roi": 2.0,
            },
            {
                "online_users": 120,
                "enter_rate": 0.22,
                "leave_rate": 0.12,
                "product_click_rate": 0.10,
                "orders": None,
                "gmv": 220,
                "ad_spend": 110,
                "roi": 2.0,
            },
        ])

    def test_all_required_windows_are_supported(self):
        for window in SUPPORTED_WINDOWS:
            snapshot = build_live_state_snapshot(
                self.adapter(),
                window_seconds=window,
                captured_at="2026-09-26T12:00:00+00:00",
            )
            self.assertEqual(window, snapshot.window_seconds)
            self.assertEqual("demo_fixture", snapshot.source)
            self.assertFalse(snapshot.evidence_hash == "")

    def test_missing_metric_is_not_coerced_to_zero(self):
        snapshot = build_live_state_snapshot(
            self.adapter(),
            window_seconds=30,
            captured_at="2026-09-26T12:00:00+00:00",
        )
        self.assertEqual(2.0, snapshot.metrics["orders"])
        sparse = FixtureLiveStateAdapter([{"orders": None}])
        sparse_snapshot = build_live_state_snapshot(sparse, window_seconds=30)
        self.assertIsNone(sparse_snapshot.metrics["orders"])

    def test_real_or_unmarked_adapter_is_rejected(self):
        class Unsafe:
            def read(self):
                return {"source": "platform", "samples": [{}]}

        with self.assertRaisesRegex(ValueError, "synthetic"):
            build_live_state_snapshot(Unsafe(), window_seconds=30)


if __name__ == "__main__":
    unittest.main()
